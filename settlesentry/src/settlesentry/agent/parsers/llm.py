from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from settlesentry.agent.parsers.base import ParserContext
from settlesentry.agent.parsers.prompts import build_parser_instructions, build_parser_user_prompt
from settlesentry.agent.state import ConversationState, ExtractedUserInput
from settlesentry.core import OperationLogContext, get_logger, settings

logger = get_logger("PydanticAIInputParser")


class PydanticAIInputParser:
    """
    LLM-first parser using PydanticAI and OpenRouter.

    The LLM only extracts fields and proposes an action. It must not verify
    identity, reveal balance, or authorize payment.
    """

    def __init__(self) -> None:
        api_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None

        if not api_key:
            raise RuntimeError("PydanticAI parser requires OPENROUTER_API_KEY")

        self.agent = Agent(
            model=OpenRouterModel(
                model_name=settings.llm.model,
                provider=OpenRouterProvider(
                    api_key=api_key,
                ),
                settings=OpenRouterModelSettings(
                    temperature=settings.llm.temperature,
                    max_tokens=settings.llm.max_tokens,
                    timeout=settings.llm.timeout_seconds,
                ),
            ),
            output_type=ExtractedUserInput,
            instructions=build_parser_instructions(),
            name="ParserAgent",
            retries=settings.llm.retries,
        )

    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput:
        if context is None:
            context = self._empty_context()

        operation = OperationLogContext(operation="llm_parse")

        try:
            result = self.agent.run_sync(
                build_parser_user_prompt(
                    user_input=user_input,
                    context=context,
                )
            )

            output = self._extract_result_output(result)

            if isinstance(output, ExtractedUserInput):
                parsed = output
            else:
                parsed = ExtractedUserInput.model_validate(output)

        except Exception as exc:
            logger.debug(
                "llm_parser_failed",
                extra=operation.completed_extra(
                    current_step=context.current_step,
                    expected_fields=self._format_expected_fields(context),
                    error_type=type(exc).__name__,
                ),
            )
            raise

        logger.debug(
            "llm_parser_completed",
            extra=operation.completed_extra(
                current_step=context.current_step,
                expected_fields=self._format_expected_fields(context),
                intent=parsed.intent.value,
                proposed_action=parsed.proposed_action.value,
            ),
        )

        return parsed

    @staticmethod
    def _extract_result_output(result: object) -> object:
        output = getattr(result, "output", None)

        # Compatibility fallback for older PydanticAI result objects.
        if output is None:
            output = getattr(result, "data", None)

        return output

    @staticmethod
    def _format_expected_fields(context: ParserContext) -> str | None:
        if not context.expected_fields:
            return None

        return ",".join(context.expected_fields)

    @staticmethod
    def _empty_context() -> ParserContext:
        state = ConversationState()
        return ParserContext.from_state(state)
