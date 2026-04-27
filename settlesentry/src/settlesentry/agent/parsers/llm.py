from __future__ import annotations

import os

from settlesentry.agent.parsers.base import ParserContext
from settlesentry.agent.parsers.prompts import build_parser_instructions, build_parser_user_prompt
from settlesentry.agent.state import ConversationState, ExtractedUserInput
from settlesentry.core import settings


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

        # PydanticAI's OpenRouter provider reads OPENROUTER_API_KEY from the
        # process environment. settings.py may load it from .env without
        # exporting it, so set a process-local value here.
        os.environ.setdefault("OPENROUTER_API_KEY", api_key)

        from pydantic_ai import Agent

        self.agent = Agent(
            self._model_name(),
            output_type=ExtractedUserInput,
            instructions=build_parser_instructions(),
        )

    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput:
        if context is None:
            context = self._empty_context()

        result = self.agent.run_sync(
            build_parser_user_prompt(
                user_input=user_input,
                context=context,
            )
        )

        output = getattr(result, "output", None)

        # Compatibility fallback for older PydanticAI result objects.
        if output is None:
            output = getattr(result, "data", None)

        if isinstance(output, ExtractedUserInput):
            return output

        return ExtractedUserInput.model_validate(output)

    @staticmethod
    def _model_name() -> str:
        if settings.llm.model.startswith("openrouter:"):
            return settings.llm.model

        return f"openrouter:{settings.llm.model}"

    @staticmethod
    def _empty_context() -> ParserContext:
        state = ConversationState()
        return ParserContext.from_state(state)
