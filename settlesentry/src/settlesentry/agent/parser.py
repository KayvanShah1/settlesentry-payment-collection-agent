from __future__ import annotations

from settlesentry.agent.parsers.base import InputParser, ParserContext
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.core import get_logger, settings

logger = get_logger("InputParser")


EXPECTED_FIELD_OUTPUT_KEYS: dict[str, tuple[str, ...]] = {
    "account_id": ("account_id",),
    "full_name": ("full_name",),
    "dob": ("dob",),
    "aadhaar_last4": ("aadhaar_last4",),
    "pincode": ("pincode",),
    "payment_amount": ("payment_amount",),
    "cardholder_name": ("cardholder_name",),
    "card_number": ("card_number",),
    "cvv": ("cvv",),
    "expiry": ("expiry_month", "expiry_year"),
    "confirmation": ("confirmation",),
}


class CombinedInputParser:
    """
    LLM-first parser with deterministic repair/fallback.

    The LLM parser is allowed to handle flexible language, but if it misses an
    expected slot and the deterministic parser can extract that slot, we merge
    the deterministic extraction back in. This keeps LLM mode robust on simple
    recovery turns like bare names, DOBs, amounts, card numbers, CVV, and expiry.
    """

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
        fallback_output = self.fallback.extract(user_input, context)

        if self.primary is None:
            return fallback_output

        try:
            primary_output = self.primary.extract(user_input, context)

            if not isinstance(primary_output, ExtractedUserInput):
                primary_output = ExtractedUserInput.model_validate(primary_output)

            return self._merge_missing_expected_fields(
                primary_output=primary_output,
                fallback_output=fallback_output,
                context=context,
            )

        except Exception as exc:
            expected_fields = None
            current_step = None

            if context is not None:
                current_step = context.current_step
                if context.expected_fields:
                    expected_fields = ",".join(context.expected_fields)

            logger.warning(
                "llm_parser_fallback",
                extra={
                    "error_type": type(exc).__name__,
                    "current_step": current_step,
                    "expected_fields": expected_fields,
                },
            )

            return fallback_output

    @staticmethod
    def _merge_missing_expected_fields(
        *,
        primary_output: ExtractedUserInput,
        fallback_output: ExtractedUserInput,
        context: ParserContext | None,
    ) -> ExtractedUserInput:
        if context is None or not context.expected_fields:
            return primary_output

        updates: dict[str, object] = {}

        for field in context.expected_fields:
            output_keys = EXPECTED_FIELD_OUTPUT_KEYS.get(field, ())

            primary_has_field = any(getattr(primary_output, key) is not None for key in output_keys)
            fallback_has_field = any(getattr(fallback_output, key) is not None for key in output_keys)

            if primary_has_field or not fallback_has_field:
                continue

            for key in output_keys:
                value = getattr(fallback_output, key)
                if value is not None:
                    updates[key] = value

        if not updates:
            return primary_output

        if primary_output.intent is None and fallback_output.intent is not None:
            updates["intent"] = fallback_output.intent

        if primary_output.proposed_action is None and fallback_output.proposed_action is not None:
            updates["proposed_action"] = fallback_output.proposed_action

        return primary_output.model_copy(update=updates)


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
            logger.warning(
                "llm_parser_disabled_fallback",
                extra={
                    "error_type": type(exc).__name__,
                },
            )

    return fallback
