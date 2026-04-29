from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.graph import build_payment_graph
from settlesentry.agent.messages import ResponseContext, build_fallback_response, format_amount
from settlesentry.agent.parsers.base import InputParser
from settlesentry.agent.responder import ResponseGenerator
from settlesentry.agent.state import ConversationState, ConversationStep
from settlesentry.core import OperationLogContext, get_logger
from settlesentry.integrations.payments.client import PaymentsClient

logger = get_logger("Agent")


class AgentResponse(BaseModel):
    """
    Required public response shape.

    Agent.next() returns this as a dict.
    """

    message: str = Field(min_length=1, max_length=700)


class Agent:
    """
    Required assignment-facing interface.

    One Agent instance represents one conversation/session.
    """

    def __init__(
        self,
        *,
        payments_client: PaymentsClient | None = None,
        parser: InputParser | None = None,
        responder: ResponseGenerator | None = None,
        grouped_card_collection: bool = False,
        graph: Any | None = None,
    ) -> None:
        deps_kwargs: dict[str, Any] = {
            "payments_client": payments_client or PaymentsClient(),
            "grouped_card_collection": grouped_card_collection,
        }

        if parser is not None:
            deps_kwargs["parser"] = parser

        if responder is not None:
            deps_kwargs["responder"] = responder

        self.deps = AgentDeps(**deps_kwargs)
        self._graph = graph or build_payment_graph()

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
            result = self._graph.invoke(
                {
                    "deps": self.deps,
                    "user_input": user_input,
                    "last_result": None,
                    "final_response": "",
                }
            )

            message = result.get("final_response") or self._fallback_response()
            response = AgentResponse(message=message)

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
            response = AgentResponse(message=self._fallback_response())

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
        amount = format_amount(self.state.payment_amount)
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
                f"Payment of {format_amount(self.state.payment_amount)} was processed successfully. "
                f"Transaction ID: {self.state.transaction_id}."
            )

        return "This conversation is already closed. No payment has been processed."

    def _fallback_response(self) -> str:
        context = ResponseContext(
            status="unknown",
            required_fields=(),
            facts={},
            safe_state=self.state.safe_view(session_id=self.session_id),
        )
        return build_fallback_response(context)
