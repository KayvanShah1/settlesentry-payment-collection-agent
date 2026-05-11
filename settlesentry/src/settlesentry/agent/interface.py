from __future__ import annotations

from typing import Any

from settlesentry.agent.contracts import MessageResponse
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.parsing.base import InputParser
from settlesentry.agent.response.messages import ResponseContext, build_fallback_response
from settlesentry.agent.response.writer import ResponseWriter
from settlesentry.agent.state import ConversationState
from settlesentry.agent.workflow.graph import build_payment_graph
from settlesentry.core import OperationLogContext, get_logger
from settlesentry.integrations.payments.client import PaymentsClient

logger = get_logger("Agent")


class Agent:
    """
    Multi-turn agent-customer interface.
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
        Process one user turn and return {"message": str}.
        """
        # Closed sessions are immutable.
        if self.state.completed:
            return MessageResponse(message=self._response_for_status("conversation_closed")).model_dump()

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

            message = result.get("final_response") or self._response_for_status("unknown")
            response = MessageResponse(message=message)

        except Exception as exc:
            # Last-resort fallback avoids surfacing internal failures.
            logger.exception(
                "agent_turn_failed",
                extra=operation.completed_extra(
                    session_id=self.session_id,
                    step_before=step_before.value,
                    step_after=self.state.step.value,
                    error_type=type(exc).__name__,
                ),
            )
            response = MessageResponse(message=self._response_for_status("unknown"))

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

    def _response_for_status(self, status: str) -> str:
        context = ResponseContext(
            status=status,
            required_fields=(),
            facts={
                "amount": self.state.payment_amount,
                "payment_amount": self.state.payment_amount,
                "transaction_id": self.state.transaction_id,
            },
            safe_state=self.state.safe_view(session_id=self.session_id),
        )

        return build_fallback_response(context)
