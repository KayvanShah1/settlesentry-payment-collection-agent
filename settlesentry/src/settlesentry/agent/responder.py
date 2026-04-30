from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from settlesentry.agent.messages import (
    DETERMINISTIC_STATUSES,
    ResponseContext,
    build_fallback_response,
)
from settlesentry.core import OperationLogContext, get_logger, settings

logger = get_logger("ResponseGenerator")


class ResponseOutput(BaseModel):
    message: str = Field(min_length=1, max_length=700)


class ResponseGenerator(Protocol):
    def generate(self, context: ResponseContext) -> str: ...


class DeterministicResponseGenerator:
    def generate(self, context: ResponseContext) -> str:
        return build_fallback_response(context)


class PydanticAIResponseGenerator:
    """
    LLM response writer.

    This does not call tools, mutate state, verify identity, reveal sensitive fields,
    or authorize payment. It only phrases a user-facing message from safe facts.
    """

    # LLM response generation is optional; deterministic statuses still use fixed
    # messages.

    def __init__(self) -> None:
        api_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None

        if not api_key:
            raise RuntimeError("Response generator requires OPENROUTER_API_KEY")

        self.agent = Agent(
            model=OpenRouterModel(
                model_name=settings.llm.model,
                provider=OpenRouterProvider(api_key=api_key),
                settings=OpenRouterModelSettings(
                    temperature=settings.llm.temperature,
                    max_tokens=settings.llm.max_tokens,
                    timeout=settings.llm.timeout_seconds,
                ),
            ),
            output_type=ResponseOutput,
            instructions=RESPONSE_INSTRUCTIONS,
            name="SettleSentryResponseAgent",
            retries=settings.llm.retries,
        )

    def generate(self, context: ResponseContext) -> str:
        # Hard-stop for critical responses so LLM mode cannot soften/omit required
        # safety language.
        if context.status in DETERMINISTIC_STATUSES:
            return build_fallback_response(context)

        operation = OperationLogContext(operation="llm_response")

        try:
            result = self.agent.run_sync(context.model_dump_json(indent=2))
            output = getattr(result, "output", None) or getattr(result, "data", None)

            if isinstance(output, ResponseOutput):
                message = output.message
            else:
                message = ResponseOutput.model_validate(output).message

            return message.strip()

        except Exception as exc:
            logger.warning(
                "llm_response_fallback",
                extra=operation.completed_extra(
                    status=context.status,
                    error_type=type(exc).__name__,
                ),
            )
            return build_fallback_response(context)


class CombinedResponseGenerator:
    def __init__(
        self,
        *,
        primary: ResponseGenerator | None = None,
        fallback: ResponseGenerator | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or DeterministicResponseGenerator()

    def generate(self, context: ResponseContext) -> str:
        # Response fallback protects the public interface from LLM/provider
        # failures.
        if self.primary is None:
            return self.fallback.generate(context)

        try:
            return self.primary.generate(context)
        except Exception:
            return self.fallback.generate(context)


def build_response_generator() -> ResponseGenerator:
    fallback = DeterministicResponseGenerator()

    if settings.llm.enabled and settings.llm.api_key:
        try:
            return CombinedResponseGenerator(
                primary=PydanticAIResponseGenerator(),
                fallback=fallback,
            )
        except Exception as exc:
            logger.warning(
                "llm_responder_disabled_fallback",
                extra={"error_type": type(exc).__name__},
            )

    return fallback


RESPONSE_INSTRUCTIONS = """
You are SettleSentry's response writer.

You only write the next user-facing message from the provided ResponseContext.

Tone and language:
- Use customer-facing payment language. The user is making a payment, not collecting one.
- Be formal but friendly, like a helpful bank representative or payment app support agent.
- Keep the message concise, natural, and direct.
- Use INR for money.

Hard rules:
- Do not call tools.
- Do not mutate state.
- Do not invent facts.
- Do not claim account lookup, identity verification, balance availability, payment readiness, payment success, or closure unless status/facts explicitly say so.
- Do not expose DOB, Aadhaar, pincode, full card number, CVV, raw state, policy names, stack traces, or tool internals.
- Do not reveal outstanding balance unless identity is verified and balance is present in facts or safe context.
- Do not say payment has been processed unless status is payment_success or conversation_closed with transaction_id.
- Do not ask for card details before payment_amount is collected.
- Do not ask for confirmation before all payment details are collected.

Fact handling:
- Never omit required factual values present in facts when they are safe to show.
- Show balance after successful verification when balance is present.
- Show transaction_id after successful payment when transaction_id is present.
- Show card_last4 during payment confirmation when card_last4 is present.
- Never expose unsafe raw values even if present.

Question framing:
- Ask only for required_fields.
- Ask for the next missing field only.
- Ask at most one grouped question.
- Do not ask for future-step fields.
- Do not re-ask for fields already present in safe_state.
- Do not combine verification and payment questions in the same response.
- Do not combine payment amount and card collection in the same response.

Required field wording:
- account_id: ask for the account ID.
- full_name: ask for the full name exactly as registered on the account.
- dob_or_aadhaar_last4_or_pincode: ask for one verification factor: DOB in YYYY-MM-DD format, Aadhaar last 4 digits, or pincode.
- payment_amount: ask for the payment amount in INR.
- cardholder_name: ask for the cardholder name.
- card_number: ask for the full card number.
- expiry: ask for the expiry in MM/YYYY format.
- cvv: ask for the CVV.
- confirmation: ask the user to reply yes to confirm or no to cancel.

Status-specific behavior:
- If status is "greeting", introduce yourself as SettleSentry, say you help with account verification and payment, then ask for the account ID.
- If status is "account_loaded", ask for the full name exactly as registered on the account.
- If status is "identity_verified" and balance is present, say identity is verified, show the outstanding balance, then ask for the payment amount in INR.
- If status is "ask_current_status", summarize only the safe current progress and then continue with the pending required field.
- If status is "ask_agent_identity", answer briefly and then continue with the pending required field.
- If status is "ask_agent_capability", answer briefly and then continue with the pending required field.
- If status is "ask_to_repeat", repeat only the pending question.
- If status is "payment_ready_for_confirmation", summarize amount and card last 4, then ask for yes/no confirmation.
- If status is "payment_success", include transaction ID and say the conversation is closed.
- If status is "cancelled" or "conversation_closed", do not ask follow-up payment questions.

Return only ResponseOutput with the message field.
""".strip()
