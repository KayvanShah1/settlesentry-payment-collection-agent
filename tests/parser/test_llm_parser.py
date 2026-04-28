import pytest

import settlesentry.agent.parsers.llm as llm_module
from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.base import ParserContext
from settlesentry.agent.parsers.llm import PydanticAIInputParser
from settlesentry.agent.state import ConversationState, ExtractedUserInput


class AgentResult:
    def __init__(self, output: object) -> None:
        self.output = output


class AgentReturningOutput:
    def __init__(self, output: object) -> None:
        self._output = output

    def run_sync(self, prompt: str) -> AgentResult:
        return AgentResult(self._output)


class AgentFailing:
    def run_sync(self, prompt: str) -> object:
        raise RuntimeError("llm failed")


def context() -> ParserContext:
    return ParserContext.from_state(
        ConversationState(),
        expected_fields=("account_id",),
    )


def test_llm_parser_returns_structured_output_and_logs_completion(
    monkeypatch: pytest.MonkeyPatch,
):
    emitted: list[tuple[str, dict]] = []

    def fake_debug(message, *args, **kwargs):
        emitted.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr(llm_module.logger, "debug", fake_debug)

    parser = PydanticAIInputParser.__new__(PydanticAIInputParser)
    parser.agent = AgentReturningOutput(
        ExtractedUserInput(
            intent=UserIntent.LOOKUP_ACCOUNT,
            proposed_action=ProposedAction.LOOKUP_ACCOUNT,
            account_id="ACC1001",
        )
    )

    result = parser.extract("ACC1001", context=context())

    assert result.account_id == "ACC1001"

    logs = [extra for message, extra in emitted if message == "llm_parser_completed"]

    assert len(logs) == 1
    assert logs[0]["operation"] == "llm_parse"
    assert logs[0]["intent"] == UserIntent.LOOKUP_ACCOUNT.value
    assert logs[0]["proposed_action"] == ProposedAction.LOOKUP_ACCOUNT.value
    assert isinstance(logs[0]["duration_ms"], int)


def test_llm_parser_logs_failure_and_reraises(monkeypatch: pytest.MonkeyPatch):
    emitted: list[tuple[str, dict]] = []

    def fake_debug(message, *args, **kwargs):
        emitted.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr(llm_module.logger, "debug", fake_debug)

    parser = PydanticAIInputParser.__new__(PydanticAIInputParser)
    parser.agent = AgentFailing()

    with pytest.raises(RuntimeError):
        parser.extract("ACC1001", context=context())

    logs = [extra for message, extra in emitted if message == "llm_parser_failed"]

    assert len(logs) == 1
    assert logs[0]["operation"] == "llm_parse"
    assert logs[0]["error_type"] == "RuntimeError"
    assert isinstance(logs[0]["duration_ms"], int)
