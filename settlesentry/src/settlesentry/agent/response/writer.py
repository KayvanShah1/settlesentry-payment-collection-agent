from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from settlesentry.agent.response.messages import (
    DETERMINISTIC_STATUSES,
    ResponseContext,
    build_fallback_response,
)
from settlesentry.agent.response.prompts import RESPONSE_INSTRUCTIONS
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


