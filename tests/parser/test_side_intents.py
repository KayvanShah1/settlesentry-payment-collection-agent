from __future__ import annotations

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.deterministic import DeterministicInputParser


def test_parser_detects_agent_identity_question():
    parsed = DeterministicInputParser().extract("who are you?")

    assert parsed.intent == UserIntent.ASK_AGENT_IDENTITY


def test_parser_detects_agent_capability_question():
    parsed = DeterministicInputParser().extract("what will you do?")

    assert parsed.intent == UserIntent.ASK_AGENT_CAPABILITY


def test_parser_detects_repeat_question():
    parsed = DeterministicInputParser().extract("can you repeat that?")

    assert parsed.intent == UserIntent.ASK_TO_REPEAT


def test_parser_detects_correction_with_field():
    parsed = DeterministicInputParser().extract("actually DOB is 1990-05-14")

    assert parsed.intent == UserIntent.CORRECT_PREVIOUS_DETAIL
    assert parsed.proposed_action == ProposedAction.HANDLE_CORRECTION
    assert parsed.dob == "1990-05-14"


def test_parser_detects_correction_without_field():
    parsed = DeterministicInputParser().extract("I want to correct my details")

    assert parsed.intent == UserIntent.CORRECT_PREVIOUS_DETAIL
    assert parsed.proposed_action == ProposedAction.HANDLE_CORRECTION
