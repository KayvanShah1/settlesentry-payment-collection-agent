from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from settlesentry.agent.contracts import MessageResponse
from settlesentry.agent.response.messages import (
    DETERMINISTIC_STATUSES,
    ResponseContext,
    build_fallback_response,
)
from settlesentry.agent.response.prompts import RESPONSE_INSTRUCTIONS
from settlesentry.core import OperationLogContext, get_logger, settings

logger = get_logger("ResponseWriter")

ResponseWriter = Callable[[ResponseContext], str]


class PydanticAIResponseWriter:
    """
    LLM response writer over safe response context.

    This writer never calls tools, mutates state, verifies identity, or authorizes
    payment.
    """

    def __init__(self) -> None:
        api_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None

        if not api_key:
            raise RuntimeError("Response writer requires OPENROUTER_API_KEY")

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
            output_type=MessageResponse,
            instructions=RESPONSE_INSTRUCTIONS,
            name="SettleSentryResponseWriter",
            retries=settings.llm.retries,
        )

    def __call__(self, context: ResponseContext) -> str:
        return self.generate(context)

    def generate(self, context: ResponseContext) -> str:
        # Deterministic safety statuses always bypass LLM phrasing.
        if context.status in DETERMINISTIC_STATUSES:
            return build_fallback_response(context)

        operation = OperationLogContext(operation="llm_response")

        try:
            result = self.agent.run_sync(context.model_dump_json(indent=2))
            output = getattr(result, "output", None) or getattr(result, "data", None)

            if isinstance(output, MessageResponse):
                message = output.message
            else:
                message = MessageResponse.model_validate(output).message

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


def build_response_writer() -> ResponseWriter:
    """
    Build response writer with deterministic fallback.
    """
    if settings.llm.enabled and settings.llm.api_key:
        try:
            return PydanticAIResponseWriter()
        except Exception as exc:
            logger.warning(
                "llm_response_writer_disabled_fallback",
                extra={"error_type": type(exc).__name__},
            )

    return build_fallback_response
