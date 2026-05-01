from __future__ import annotations

from typing import Any

from settlesentry.agent.contracts import MessageResponse
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.parsing.base import InputParser
from settlesentry.agent.response.messages import ResponseContext, build_fallback_response, format_amount
from settlesentry.agent.response.writer import ResponseWriter
from settlesentry.agent.state import ConversationState, ConversationStep
from settlesentry.agent.workflow.graph import build_payment_graph
from settlesentry.core import OperationLogContext, get_logger
from settlesentry.integrations.payments.client import PaymentsClient

logger = get_logger("Agent")


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
        responder: ResponseWriter | None = None,
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
        # Terminal conversations are short-circuited before graph execution so closed
        # sessions cannot mutate state.
        if self.state.completed:
            return MessageResponse(message=self._closed_response()).model_dump()

        # Payment success is finalized on the next turn to return a stable final recap
        # and then close the session.
        if self.state.step == ConversationStep.PAYMENT_SUCCESS:
            return MessageResponse(message=self._finalize_success_response()).model_dump()

        operation = OperationLogContext(operation="agent_turn")
        step_before = self.state.step

        try:
            # One user message equals one graph invocation. All state changes happen
            # through deps.state inside graph nodes.
            result = self._graph.invoke(
                {
                    "deps": self.deps,
                    "user_input": user_input,
                    "last_result": None,
                    "final_response": "",
                }
            )

            message = result.get("final_response") or self._fallback_response()
            response = MessageResponse(message=message)

        except Exception as exc:
            # Last-resort safety fallback: never expose stack traces or internal tool
            # failures to the user.
            logger.exception(
                "agent_turn_failed",
                extra=operation.completed_extra(
                    session_id=self.session_id,
                    step_before=step_before.value,
                    step_after=self.state.step.value,
                    error_type=type(exc).__name__,
                ),
            )
            response = MessageResponse(message=self._fallback_response())

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
