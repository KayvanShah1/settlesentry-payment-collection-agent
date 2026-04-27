from __future__ import annotations

from settlesentry.agent.parsers.base import InputParser, ParserContext
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.core import get_logger, settings

logger = get_logger("InputParser")


class CombinedInputParser:
    """LLM-first parser with deterministic fallback."""

    def __init__(
        self,
        *,
        primary: InputParser | None = None,
        fallback: InputParser | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or DeterministicInputParser()

    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput:
        if self.primary is not None:
            try:
                primary_output = self.primary.extract(user_input, context)

                if isinstance(primary_output, ExtractedUserInput):
                    return primary_output

                return ExtractedUserInput.model_validate(primary_output)
            except Exception as exc:
                logger.info(
                    "llm_parser_fallback",
                    extra={"error_type": type(exc).__name__},
                )

        return self.fallback.extract(user_input, context)


def build_input_parser() -> InputParser:
    """
    Build the parser used by the agent service.

    LLM parsing is enabled only when both OPENROUTER_ENABLED=true and an API key
    are configured. Otherwise the deterministic parser is used directly.
    """

    fallback = DeterministicInputParser()

    if settings.llm.enabled and settings.llm.api_key:
        try:
            from settlesentry.agent.parsers.llm import PydanticAIInputParser

            return CombinedInputParser(
                primary=PydanticAIInputParser(),
                fallback=fallback,
            )
        except Exception as exc:
            logger.info(
                "llm_parser_disabled_fallback",
                extra={"error_type": type(exc).__name__},
            )

    return fallback
