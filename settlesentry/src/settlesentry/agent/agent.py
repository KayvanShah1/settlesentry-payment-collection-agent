from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
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


class AgentResponse(BaseModel):
    """
    Required public response shape.

    Agent.next() returns this as a dict.
    """

    message: str = Field(min_length=1, max_length=700)


AGENT_INSTRUCTIONS = """
You are SettleSentry, a payment collection agent.

Use tools to control the workflow. Do not change state directly.

Tool rules:
- Always call submit_user_input first for each user message.
- If any tool returns recommended_tool, call that tool before responding.
- If a tool returns required_fields and no recommended_tool, ask only for the first missing field and end the turn.
- greet_user should introduce SettleSentry, explain that you will help the user make a payment step by step, and ask only for account ID.
- Never ask for future-step fields.
- Never claim account lookup, verification, payment readiness, payment success, or closure unless a tool result says so.
- Never reveal balance before identity_verified.
- Never ask for payment amount before identity_verified.
- Never ask for card details before payment_amount is captured and identity is verified.
- Never process payment before explicit user confirmation.
- If submit_user_input returns confirmation_received, call confirm_payment with confirmed=true.
- If process_payment_if_allowed returns payment_success, call recap_and_close before final response.
- If any tool returns recommended_tool=recap_and_close, call recap_and_close before final response.
- If the user cancels or refuses, call cancel_payment_flow.

Safety rules:
- Do not expose tool internals, policy names, stack traces, raw state, DOB, Aadhaar, pincode, full card number, or CVV.
- Use INR, never $.
- Ask for DOB only in YYYY-MM-DD format.
- Ask for the full card number when card_number is required.
- Only mention card last 4 digits during confirmation or recap.
- Never compute or claim an updated remaining balance after payment.
- Use transaction_id exactly as provided by the tool/state. Do not rewrite, shorten, or guess it.

Response rules:
- Return an AgentResponse object with only the message field.
- The message must be concise, user-facing, and contain only one question.
- Do not repeat the same question in different words.
- If required_fields is present, ask only for the first required field.
- If the conversation is closed, say it is closed and do not continue the flow.
""".strip()


def build_collection_agent() -> PydanticAgent[AgentDeps, AgentResponse]:
    return PydanticAgent(
        model=_build_openrouter_model(),
        deps_type=AgentDeps,
        output_type=AgentResponse,
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
        pydantic_agent: PydanticAgent[AgentDeps, AgentResponse] | None = None,
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
            return AgentResponse(message=self._closed_response()).model_dump()

        if self.state.step == ConversationStep.PAYMENT_SUCCESS:
            return AgentResponse(message=self._finalize_success_response()).model_dump()

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
            response = self._extract_response(result)

            if self.state.step == ConversationStep.PAYMENT_SUCCESS and not self.state.completed:
                response = AgentResponse(message=self._finalize_success_response())

        except UsageLimitExceeded:
            logger.warning(
                "agent_turn_usage_limited",
                extra=operation.completed_extra(
                    session_id=self.session_id,
                    step_before=step_before.value,
                    step_after=self.state.step.value,
                ),
            )
            response = AgentResponse(message=self._state_fallback_response())

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
            response = AgentResponse(message=self._state_fallback_response())

        logger.info(
            "agent_turn_completed",
            extra=operation.completed_extra(
                session_id=self.session_id,
                step_before=step_before.value,
                step_after=self.state.step.value,
                completed=self.state.completed,
            ),
        )

        return response.model_dump()

    def _finalize_success_response(self) -> str:
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

        if self.state.step == ConversationStep.START:
            return "Hello, I’m SettleSentry, your payment collection assistant. I will help you make a payment step by step."

        if self.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID:
            return "Please share your account ID in ACC<digits> format, for example ACC1001."

        if self.state.step == ConversationStep.WAITING_FOR_FULL_NAME:
            return "Please share your full name exactly as registered on the account."

        if self.state.step == ConversationStep.WAITING_FOR_SECONDARY_FACTOR:
            return "Please share one verification factor: DOB in YYYY-MM-DD format, Aadhaar last 4 digits, or pincode."

        if self.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT:
            return "Please share the payment amount in INR."

        if self.state.step == ConversationStep.WAITING_FOR_CARDHOLDER_NAME:
            return "Please share the cardholder name."

        if self.state.step == ConversationStep.WAITING_FOR_CARD_NUMBER:
            return "Please share the full card number."

        if self.state.step == ConversationStep.WAITING_FOR_CVV:
            return "Please share the CVV."

        if self.state.step == ConversationStep.WAITING_FOR_EXPIRY:
            return "Please share the card expiry in MM/YYYY format."

        if self.state.step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION:
            return f"Please confirm the payment of {self._format_amount()} by replying yes or no."

        if self.state.step == ConversationStep.PAYMENT_SUCCESS:
            return self._finalize_success_response()

        return "Please provide the requested detail to continue."

    @staticmethod
    def _extract_response(result: object) -> AgentResponse:
        output = getattr(result, "output", None)

        if output is None:
            output = getattr(result, "data", None)

        if isinstance(output, AgentResponse):
            return output

        if isinstance(output, str):
            text = output.strip()

            if not text:
                raise ValueError("Agent output message is empty.")

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return AgentResponse(message=text)

            if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
                return AgentResponse(message=parsed["message"])

            return AgentResponse(message=text)

        return AgentResponse.model_validate(output)

    @staticmethod
    def _extract_message_history(result: object) -> list[Any]:
        all_messages = getattr(result, "all_messages", None)

        if callable(all_messages):
            return list(all_messages())

        return []
