import pytest
from pydantic import SecretStr
from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parser import CombinedInputParser, build_input_parser
from settlesentry.agent.parsers.base import ParserContext
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.state import ExtractedUserInput
from settlesentry.core import settings


class _FixedParser:
    def __init__(self, result: ExtractedUserInput) -> None:
        self._result = result

    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput:
        return self._result


class _FailingParser:
    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput:
        raise RuntimeError("primary parser failed")


class _MalformedShapeParser:
    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> object:
        return object()


def test_combined_parser_uses_primary_when_it_succeeds():
    expected = ExtractedUserInput(
        intent=UserIntent.LOOKUP_ACCOUNT,
        proposed_action=ProposedAction.LOOKUP_ACCOUNT,
        account_id="ACC1001",
    )
    parser = CombinedInputParser(
        primary=_FixedParser(expected),
        fallback=DeterministicInputParser(),
    )

    result = parser.extract("any text")

    assert result == expected


def test_combined_parser_falls_back_when_primary_raises():
    fallback = _FixedParser(
        ExtractedUserInput(
            intent=UserIntent.LOOKUP_ACCOUNT,
            proposed_action=ProposedAction.LOOKUP_ACCOUNT,
            account_id="ACC1002",
        )
    )
    parser = CombinedInputParser(primary=_FailingParser(), fallback=fallback)

    result = parser.extract("whatever")

    assert result.account_id == "ACC1002"


def test_combined_parser_falls_back_when_primary_returns_invalid_shape():
    fallback = _FixedParser(
        ExtractedUserInput(
            intent=UserIntent.LOOKUP_ACCOUNT,
            proposed_action=ProposedAction.LOOKUP_ACCOUNT,
            account_id="ACC1003",
        )
    )
    parser = CombinedInputParser(primary=_MalformedShapeParser(), fallback=fallback)

    result = parser.extract("whatever")

    assert result.account_id == "ACC1003"


def test_build_input_parser_returns_deterministic_when_llm_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.llm, "enabled", False)
    monkeypatch.setattr(settings.llm, "api_key", None)

    parser = build_input_parser()

    assert isinstance(parser, DeterministicInputParser)


def test_build_input_parser_returns_deterministic_when_llm_enabled_but_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.llm, "enabled", True)
    monkeypatch.setattr(settings.llm, "api_key", None)

    parser = build_input_parser()

    assert isinstance(parser, DeterministicInputParser)


def test_build_input_parser_returns_combined_when_llm_available(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.llm, "enabled", True)
    monkeypatch.setattr(settings.llm, "api_key", SecretStr("test-key"))

    import settlesentry.agent.parsers.llm as llm_module

    class DummyLLMParser:
        def extract(
            self,
            user_input: str,
            context: ParserContext | None = None,
        ) -> ExtractedUserInput:
            return ExtractedUserInput(
                intent=UserIntent.LOOKUP_ACCOUNT,
                proposed_action=ProposedAction.LOOKUP_ACCOUNT,
                account_id="ACC7777",
            )

    monkeypatch.setattr(llm_module, "PydanticAIInputParser", DummyLLMParser)

    parser = build_input_parser()

    assert isinstance(parser, CombinedInputParser)
    result = parser.extract("account id ACC1001")
    assert result.account_id == "ACC7777"


def test_build_input_parser_falls_back_when_llm_init_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.llm, "enabled", True)
    monkeypatch.setattr(settings.llm, "api_key", SecretStr("test-key"))

    import settlesentry.agent.parsers.llm as llm_module

    class FailingLLMParser:
        def __init__(self) -> None:
            raise RuntimeError("simulated LLM init failure")

    monkeypatch.setattr(llm_module, "PydanticAIInputParser", FailingLLMParser)

    parser = build_input_parser()

    assert isinstance(parser, DeterministicInputParser)


def test_combined_parser_falls_back_when_llm_result_has_no_output_shape():
    from settlesentry.agent.parsers.llm import PydanticAIInputParser

    class ResultWithoutOutputOrData:
        pass

    class AgentReturningMalformedResult:
        def run_sync(self, prompt: str) -> object:
            return ResultWithoutOutputOrData()

    llm_parser = PydanticAIInputParser.__new__(PydanticAIInputParser)
    llm_parser.agent = AgentReturningMalformedResult()

    fallback = _FixedParser(
        ExtractedUserInput(
            intent=UserIntent.LOOKUP_ACCOUNT,
            proposed_action=ProposedAction.LOOKUP_ACCOUNT,
            account_id="ACC1004",
        )
    )
    parser = CombinedInputParser(primary=llm_parser, fallback=fallback)

    result = parser.extract("anything")

    assert result.account_id == "ACC1004"


def test_combined_parser_logs_fallback_when_primary_raises(monkeypatch: pytest.MonkeyPatch):
    emitted: list[tuple[str, dict]] = []

    def fake_warning(message, *args, **kwargs):
        emitted.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr("settlesentry.agent.parser.logger.warning", fake_warning)

    fallback = _FixedParser(
        ExtractedUserInput(
            intent=UserIntent.LOOKUP_ACCOUNT,
            proposed_action=ProposedAction.LOOKUP_ACCOUNT,
            account_id="ACC1002",
        )
    )
    parser = CombinedInputParser(primary=_FailingParser(), fallback=fallback)

    result = parser.extract("whatever")

    assert result.account_id == "ACC1002"

    fallback_logs = [extra for message, extra in emitted if message == "llm_parser_fallback"]
    assert len(fallback_logs) == 1
    assert fallback_logs[0]["error_type"] == "RuntimeError"
