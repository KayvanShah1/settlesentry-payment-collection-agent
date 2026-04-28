from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.state import ConversationState, ConversationStep
from settlesentry.agent.tools import payment_collection_toolset
from settlesentry.core import OperationLogContext, get_logger, settings
from settlesentry.integrations.payments.client import PaymentsClient

logger = get_logger("Agent")
RUN_USAGE_LIMITS = UsageLimits(
    request_limit=8,
    tool_calls_limit=6,
)


AGENT_INSTRUCTIONS = """
You are SettleSentry, a payment collection agent.

You must use tools for state changes, verification, account lookup, payment readiness, confirmation, payment processing, and final recap.

Rules:
- Always call submit_user_input first for each new user message.
- If submit_user_input returns recommended_tool=greet_user, call greet_user before responding.
- If the conversation is at START and the user only greets or starts the chat, submit_user_input should lead to greet_user.
- greet_user should introduce SettleSentry and ask only for account ID.
- When greet_user returns required_fields=account_id, ask only for account ID. Do not ask for any other information until after account lookup.
- Use required_fields to ask only for missing information. Do not ask for future-step fields.
- After any tool result with required_fields and no recommended_tool, ask only for those required_fields and end the turn.
- Use recommended_tool when provided.
- Never claim account lookup, verification, payment readiness, payment success, or closure unless a tool result says so.
- Never reveal balance before identity_verified.
- Do not ask for payment_amount until status=identity_verified or safe_state.verified=true.
- Do not ask for card details until payment_amount is captured and safe_state.verified=true.
- If status=identity_verification_failed, ask for full_name again and then one secondary factor in a new user turn. Do not call verify_identity_if_ready again in the same turn.
- When asking for DOB, always request YYYY-MM-DD format.
- When collecting card_number, ask for the full card number. Do not say last 4 digits are enough.
- Only mention card last 4 digits during confirmation or recap.
- Never process payment unless the user explicitly confirms and process_payment_if_allowed succeeds.
- If submit_user_input returns confirmation_received, call confirm_payment with confirmed=true.
- If process_payment_if_allowed returns payment_success, call recap_and_close before final response.
- If any tool returns recommended_tool=recap_and_close, call recap_and_close before final response.
- If the user cancels or refuses, call cancel_payment_flow.
- Do not expose tool internals, policy names, stack traces, raw state, DOB, Aadhaar, pincode, full card number, or CVV.
- Use INR, never $.
- Never compute or claim an updated remaining balance after payment.
- After payment_success, include the exact transaction_id from tool facts or state. Do not rewrite, shorten, or guess it.
- Final recap must be concise and safe. It should mention whether payment was completed, transaction_id if present, amount if present, and that the conversation is closed.
- If completed=true, do not continue collecting payment details.
- Return only plain user-facing text. Do not return JSON, markdown fences, or a {"message": "..."} object.

Expected flow:
0. greet user and introduce yourself as SettleSentry, a payment collection assistant
1. collect account ID
2. look up account
3. collect full name
4. collect one secondary factor
5. verify identity
6. collect payment amount
7. collect cardholder name, full card number, CVV, and expiry
8. prepare payment and ask for explicit confirmation
9. after explicit yes, confirm payment and process payment
10. recap and close
""".strip()


def build_collection_agent() -> PydanticAgent[AgentDeps, str]:
    return PydanticAgent(
        model=_build_openrouter_model(),
        deps_type=AgentDeps,
        output_type=str,
        instructions=AGENT_INSTRUCTIONS,
        name="SettleSentryAgent",
        retries=settings.llm.retries,
        toolsets=[payment_collection_toolset],
    )


def _build_openrouter_model() -> OpenRouterModel:
    api_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None

    if not api_key:
        raise RuntimeError("SettleSentry Agent requires OPENROUTER_API_KEY.")

    return OpenRouterModel(
        model_name=settings.llm.model,
        provider=OpenRouterProvider(api_key=api_key),
        settings=OpenRouterModelSettings(
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
            timeout=settings.llm.timeout_seconds,
        ),
    )


class Agent:
    """
    Required assignment-facing interface.

    One Agent instance represents one conversation/session.
    """

    def __init__(
        self,
        *,
        payments_client: PaymentsClient | None = None,
        pydantic_agent: PydanticAgent[AgentDeps, str] | None = None,
    ) -> None:
        self.deps = AgentDeps(
            payments_client=payments_client or PaymentsClient(),
        )
        self._agent = pydantic_agent or build_collection_agent()
        self._message_history: list[Any] = []

    @property
    def state(self) -> ConversationState:
        return self.deps.state

    @property
    def session_id(self) -> str:
        return self.deps.session_id

    def next(self, user_input: str) -> dict[str, str]:
        """
        Process one turn of the conversation.

        Returns:
            {"message": str}
        """
        if self.state.completed:
            return {"message": self._closed_response()}

        if self.state.step == ConversationStep.PAYMENT_SUCCESS:
            return {"message": self._finalize_success_response()}

        operation = OperationLogContext(operation="agent_turn")
        step_before = self.state.step

        try:
            result = self._agent.run_sync(
                user_input,
                deps=self.deps,
                message_history=self._message_history,
                usage_limits=RUN_USAGE_LIMITS,
            )
            self._message_history = self._extract_message_history(result)
            output = self._extract_output(result)

            if self.state.step == ConversationStep.PAYMENT_SUCCESS and not self.state.completed:
                output = self._finalize_success_response()

            if not output.strip():
                output = self._state_fallback_response()

            if self.state.completed and not output.strip():
                output = self._closed_response()

        except UsageLimitExceeded:
            logger.warning(
                "agent_turn_usage_limited",
                extra=operation.completed_extra(
                    session_id=self.session_id,
                    step_before=step_before.value,
                    step_after=self.state.step.value,
                ),
            )
            output = self._state_fallback_response()

        except Exception as exc:
            logger.exception(
                "agent_turn_failed",
                extra=operation.completed_extra(
                    session_id=self.session_id,
                    step_before=step_before.value,
                    step_after=self.state.step.value,
                    error_type=type(exc).__name__,
                ),
            )
            raise

        logger.info(
            "agent_turn_completed",
            extra=operation.completed_extra(
                session_id=self.session_id,
                step_before=step_before.value,
                step_after=self.state.step.value,
                completed=self.state.completed,
            ),
        )

        return {"message": output}

    def _finalize_success_response(self) -> str:
        """
        Deterministic fallback close for payment success.

        This prevents the LLM from skipping recap_and_close, mutating the
        transaction_id, claiming updated balance, or keeping the session open.
        """
        amount = self._format_amount()
        transaction_id = self.state.transaction_id or "not available"

        self.state.mark_closed()

        return (
            f"Payment of {amount} was processed successfully. "
            f"Transaction ID: {transaction_id}. "
            "This conversation is now closed."
        )

    def _closed_response(self) -> str:
        if self.state.transaction_id:
            return (
                "This conversation is already closed. "
                f"Payment of {self._format_amount()} was processed successfully. "
                f"Transaction ID: {self.state.transaction_id}."
            )

        return "This conversation is already closed. No payment has been processed."

    def _format_amount(self) -> str:
        if self.state.payment_amount is None:
            return "the selected amount"

        return f"INR {self.state.payment_amount:.2f}"

    def _state_fallback_response(self) -> str:
        if self.state.completed:
            return self._closed_response()
        if self.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID:
            return "Please provide your account ID."
        if self.state.step == ConversationStep.WAITING_FOR_FULL_NAME:
            return "I couldn't verify your details yet. Please share your full name exactly as registered on the account."
        if self.state.step == ConversationStep.WAITING_FOR_SECONDARY_FACTOR:
            return (
                "Please provide one secondary factor for verification: "
                "DOB in YYYY-MM-DD, Aadhaar last 4 digits, or pincode."
            )
        if self.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT:
            return "Please share the payment amount in INR."
        if self.state.step == ConversationStep.WAITING_FOR_CARDHOLDER_NAME:
            return "Please provide the cardholder name."
        if self.state.step == ConversationStep.WAITING_FOR_CARD_NUMBER:
            return "Please provide the full card number."
        if self.state.step == ConversationStep.WAITING_FOR_CVV:
            return "Please provide the CVV."
        if self.state.step == ConversationStep.WAITING_FOR_EXPIRY:
            return "Please provide the card expiry in MM/YY format."
        if self.state.step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION:
            return f"Please confirm the payment of {self._format_amount()} by replying yes or no."
        if self.state.step == ConversationStep.PAYMENT_SUCCESS:
            return self._finalize_success_response()

        return "Please provide the requested details to continue."

    @staticmethod
    def _extract_output(result: object) -> str:
        output = getattr(result, "output", None)

        if output is None:
            output = getattr(result, "data", None)

        if output is None:
            return ""

        if not isinstance(output, str):
            return str(output)

        text = output.strip()

        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text

        if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
            return parsed["message"]

        return text

    @staticmethod
    def _extract_message_history(result: object) -> list[Any]:
        all_messages = getattr(result, "all_messages", None)

        if callable(all_messages):
            return list(all_messages())

        return []
