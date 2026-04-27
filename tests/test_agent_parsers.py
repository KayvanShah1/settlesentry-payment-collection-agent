import json
from decimal import Decimal

import pytest
from pydantic import SecretStr

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parser import CombinedInputParser, build_input_parser
from settlesentry.agent.parsers.base import ConversationTurn, ParserContext
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.parsers.prompts import build_parser_instructions, build_parser_user_prompt
from settlesentry.agent.state import ConversationState, ExtractedUserInput
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


def _context(
    *,
    expected_fields: tuple = (),
) -> ParserContext:
    state = ConversationState(
        account_id="ACC1001",
        card_number="4532-0151-1283-0366",
        payment_amount=Decimal("500.00"),
    )
    return ParserContext.from_state(
        state,
        expected_fields=expected_fields,
        last_assistant_message="Please share the requested fields.",
        recent_turns=(ConversationTurn(role="assistant", content="Share details"),),
    )


def test_parser_context_from_state_is_privacy_aware():
    context = _context(expected_fields=("cvv",))

    assert context.state_summary.card_last4 == "0366"
    assert context.state_summary.payment_amount == "500.00"
    dumped = context.model_dump()
    assert "card_number" not in dumped["state_summary"]
    assert "cvv" not in dumped["state_summary"]
    assert "dob" not in dumped["state_summary"]
    assert "aadhaar_last4" not in dumped["state_summary"]
    assert "pincode" not in dumped["state_summary"]


def test_deterministic_parser_extracts_bare_dob_when_context_expects_it():
    parser = DeterministicInputParser()
    result = parser.extract("1990-05-14", _context(expected_fields=("dob",)))

    assert result.dob == "1990-05-14"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


@pytest.mark.parametrize(
    ("expected_field", "user_input", "expected_values"),
    [
        ("full_name", "Nithin Jain", {"full_name": "Nithin Jain"}),
        ("aadhaar_last4", "4321", {"aadhaar_last4": "4321"}),
        ("pincode", "400001", {"pincode": "400001"}),
        ("payment_amount", "1250.75", {"payment_amount": Decimal("1250.75")}),
        ("card_number", "4532 0151 1283 0366", {"card_number": "4532 0151 1283 0366"}),
        ("cvv", "123", {"cvv": "123"}),
        ("expiry", "12/2027", {"expiry_month": 12, "expiry_year": 2027}),
        (
            "confirmation",
            "yes",
            {
                "confirmation": True,
                "intent": UserIntent.CONFIRM_PAYMENT,
                "proposed_action": ProposedAction.PROCESS_PAYMENT,
            },
        ),
    ],
)
def test_deterministic_parser_extracts_bare_contextual_slots(
    expected_field: str,
    user_input: str,
    expected_values: dict[str, object],
):
    parser = DeterministicInputParser()
    context = _context(expected_fields=(expected_field,))

    result = parser.extract(user_input, context)

    for key, expected in expected_values.items():
        assert getattr(result, key) == expected


def test_deterministic_parser_extracts_ordered_form_values_in_expected_order():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("dob", "full_name", "card_number"))

    result = parser.extract("1990-05-14, Nithin Jain, 4532015112830366", context)

    assert result.dob == "1990-05-14"
    assert result.full_name == "Nithin Jain"
    assert result.card_number == "4532015112830366"


def test_deterministic_parser_extracts_identity_form_style_reply():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("dob", "aadhaar_last4", "pincode"))

    result = parser.extract("1990-05-14, 4321, 400001", context)

    assert result.dob == "1990-05-14"
    assert result.aadhaar_last4 == "4321"
    assert result.pincode == "400001"


def test_deterministic_parser_extracts_identity_form_style_reply_with_name():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("full_name", "dob", "pincode"))

    result = parser.extract("Nithin Jain, 1990-05-14, 400001", context)

    assert result.full_name == "Nithin Jain"
    assert result.dob == "1990-05-14"
    assert result.pincode == "400001"


def test_deterministic_parser_extracts_card_form_style_reply():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("cardholder_name", "card_number", "cvv", "expiry"))

    result = parser.extract("Nithin Jain, 4532 0151 1283 0366, 123, 12/2027", context)

    assert result.cardholder_name == "Nithin Jain"
    assert result.card_number == "4532 0151 1283 0366"
    assert result.cvv == "123"
    assert result.expiry_month == 12
    assert result.expiry_year == 2027


def test_deterministic_parser_disambiguates_4321_from_expected_field():
    parser = DeterministicInputParser()

    aadhaar_result = parser.extract("4321", _context(expected_fields=("aadhaar_last4",)))
    cvv_result = parser.extract("4321", _context(expected_fields=("cvv",)))

    assert aadhaar_result.aadhaar_last4 == "4321"
    assert aadhaar_result.cvv is None

    assert cvv_result.cvv == "4321"
    assert cvv_result.aadhaar_last4 is None


def test_deterministic_parser_handles_thousands_separator_in_ordered_form_amount():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("payment_amount", "card_number"))

    result = parser.extract("1,250.75, 4532 0151 1283 0366", context)

    assert result.payment_amount == Decimal("1250.75")
    assert result.card_number == "4532 0151 1283 0366"


def test_deterministic_parser_handles_indian_separator_in_ordered_form_amount():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("payment_amount", "card_number"))

    result = parser.extract("1,23,250.75, 4532 0151 1283 0366", context)

    assert result.payment_amount == Decimal("123250.75")
    assert result.card_number == "4532 0151 1283 0366"


def test_deterministic_parser_handles_ordered_form_with_fewer_values_than_expected():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("dob", "full_name", "card_number"))

    result = parser.extract("1990-05-14, Nithin Jain", context)

    assert result.dob == "1990-05-14"
    assert result.full_name == "Nithin Jain"
    assert result.card_number is None


def test_deterministic_parser_handles_ordered_form_with_more_values_than_expected():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("dob", "full_name"))

    result = parser.extract("1990-05-14, Nithin Jain, ignored extra value", context)

    assert result.dob == "1990-05-14"
    assert result.full_name == "Nithin Jain"
    assert result.card_number is None


def test_deterministic_parser_handles_mixed_delimiters_in_ordered_form():
    parser = DeterministicInputParser()
    context = _context(expected_fields=("payment_amount", "card_number", "cvv", "expiry"))

    result = parser.extract("1,250.75; 4532 0151 1283 0366\n123;12/2027", context)

    assert result.payment_amount == Decimal("1250.75")
    assert result.card_number == "4532 0151 1283 0366"
    assert result.cvv == "123"
    assert result.expiry_month == 12
    assert result.expiry_year == 2027


def test_deterministic_parser_retains_valid_fields_when_multiple_fields_are_invalid():
    parser = DeterministicInputParser()

    result = parser.extract(
        "dob is 1989-02-29 amount is abc account ACC1001 card number 4532015112830366"
    )

    assert result.dob is None
    assert result.payment_amount is None
    assert result.account_id == "ACC1001"
    assert result.card_number == "4532015112830366"


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


def test_parser_prompt_payload_contains_context_fields():
    context = _context(expected_fields=("dob", "aadhaar_last4"))
    prompt_json = build_parser_user_prompt("DOB is 1990-05-14", context)
    payload = json.loads(prompt_json)

    assert payload["latest_user_message"] == "DOB is 1990-05-14"
    assert payload["current_step"] == context.current_step
    assert payload["expected_fields"] == ["dob", "aadhaar_last4"]
    assert payload["state_summary"]["card_last4"] == "0366"


def test_parser_instructions_include_policy_boundaries():
    instructions = build_parser_instructions()

    assert "Do not verify identity." in instructions
    assert "Do not decide whether payment is allowed." in instructions
    assert "proposed_action=process_payment" in instructions
