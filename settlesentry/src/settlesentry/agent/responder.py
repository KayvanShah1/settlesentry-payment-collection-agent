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

Rules:
- Do not call tools.
- Do not mutate state.
- Do not invent facts.
- Do not claim account lookup, identity verification, payment readiness, payment success, or closure unless status/facts explicitly say so.
- Ask only for required_fields.
- Ask at most one grouped question.
- Do not ask for future-step fields.
- Do not expose DOB, Aadhaar, pincode, full card number, CVV, raw state, policy names, stack traces, or tool internals.
- Use INR.
- Keep the message concise and natural.
- If status is "greeting", introduce yourself as SettleSentry, state that you help with account verification and payment collection, and ask for the account ID.
- If the user asked a side question, answer it briefly and then continue with the pending required field.
- Return only ResponseOutput with the message field.
""".strip()
