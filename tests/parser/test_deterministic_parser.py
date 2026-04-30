from decimal import Decimal

import pytest
from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.base import ParserContext
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.state import ConversationState, ConversationStep, ExtractedUserInput

# Deterministic parser tests document how bare values are interpreted from
# expected_fields.

@pytest.fixture
def parser() -> DeterministicInputParser:
    return DeterministicInputParser()


def parser_context(
    *expected_fields,
    step: ConversationStep = ConversationStep.START,
) -> ParserContext:
    return ParserContext.from_state(
        ConversationState(step=step),
        expected_fields=expected_fields,
        last_assistant_message="Please provide the requested value.",
    )


def confirmation_context() -> ParserContext:
    return parser_context(
        "confirmation",
        step=ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION,
    )


def test_empty_input_returns_default_extraction(parser: DeterministicInputParser):
    result = parser.extract("")

    assert isinstance(result, ExtractedUserInput)
    assert result.intent == UserIntent.UNKNOWN
    assert result.proposed_action == ProposedAction.NONE
    assert result.account_id is None
    assert result.payment_amount is None
    assert result.confirmation is None


def test_account_id_is_extracted_and_normalized(parser: DeterministicInputParser):
    result = parser.extract("my account is acc1001")

    assert result.account_id == "ACC1001"
    assert result.intent == UserIntent.LOOKUP_ACCOUNT
    assert result.proposed_action == ProposedAction.LOOKUP_ACCOUNT


def test_opaque_account_id_is_extracted(parser: DeterministicInputParser):
    result = parser.extract("my account is AC1001")

    assert result.account_id == "AC1001"
    assert result.intent == UserIntent.LOOKUP_ACCOUNT
    assert result.proposed_action == ProposedAction.LOOKUP_ACCOUNT


def test_full_name_is_extracted_from_common_intro(parser: DeterministicInputParser):
    result = parser.extract("My name is Nithin Jain")

    assert result.full_name == "Nithin Jain"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


def test_identity_fields_are_extracted_from_common_labels(parser: DeterministicInputParser):
    result = parser.extract("DOB is 1990-05-14, aadhaar last 4 is 4321 and pin code is 400001")

    assert result.dob == "1990-05-14"
    assert result.aadhaar_last4 == "4321"
    assert result.pincode == "400001"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


def test_invalid_identity_fields_are_dropped_without_crashing(parser: DeterministicInputParser):
    result = parser.extract("dob is 1989-02-29 pincode is 40001 aadhaar is 432143214321")

    assert isinstance(result, ExtractedUserInput)
    assert result.dob is None
    assert result.pincode is None
    assert result.aadhaar_last4 is None


def test_payment_amount_is_extracted_from_common_payment_phrase(parser: DeterministicInputParser):
    result = parser.extract("pay ₹500.50")

    assert result.payment_amount == Decimal("500.50")
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_invalid_payment_amount_is_dropped(parser: DeterministicInputParser):
    result = parser.extract("pay 500.001")

    assert result.payment_amount is None


def test_card_fields_are_extracted_from_common_labels(parser: DeterministicInputParser):
    result = parser.extract(
        "cardholder name is Nithin Jain. card number is 4532 0151 1283 0366. cvv is 123. expiry is 12/2027."
    )

    assert result.cardholder_name == "Nithin Jain"
    assert result.card_number == "4532015112830366"
    assert result.cvv == "123"
    assert result.expiry_month == 12
    assert result.expiry_year == 2027
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_bare_account_id_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "ACC1001",
        context=parser_context("account_id"),
    )

    assert result.account_id == "ACC1001"
    assert result.intent == UserIntent.LOOKUP_ACCOUNT
    assert result.proposed_action == ProposedAction.LOOKUP_ACCOUNT


def test_bare_full_name_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "Nithin Jain",
        context=parser_context("full_name"),
    )

    assert result.full_name == "Nithin Jain"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


def test_bare_dob_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "1990-05-14",
        context=parser_context("dob"),
    )

    assert result.dob == "1990-05-14"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


def test_bare_aadhaar_last4_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "4321",
        context=parser_context("aadhaar_last4"),
    )

    assert result.aadhaar_last4 == "4321"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


def test_bare_pincode_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "400001",
        context=parser_context("pincode"),
    )

    assert result.pincode == "400001"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


def test_bare_payment_amount_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "500.00",
        context=parser_context("payment_amount"),
    )

    assert result.payment_amount == Decimal("500.00")
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_bare_cardholder_name_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "Nithin Jain",
        context=parser_context("cardholder_name"),
    )

    assert result.cardholder_name == "Nithin Jain"
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_bare_card_number_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "4532 0151 1283 0366",
        context=parser_context("card_number"),
    )

    assert result.card_number == "4532015112830366"
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_bare_cvv_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "123",
        context=parser_context("cvv"),
    )

    assert result.cvv == "123"
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_bare_expiry_is_extracted_when_expected(parser: DeterministicInputParser):
    result = parser.extract(
        "12/27",
        context=parser_context("expiry"),
    )

    assert result.expiry_month == 12
    assert result.expiry_year == 2027
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_same_bare_digits_are_mapped_by_expected_field(parser: DeterministicInputParser):
    aadhaar_result = parser.extract(
        "4321",
        context=parser_context("aadhaar_last4"),
    )
    cvv_result = parser.extract(
        "4321",
        context=parser_context("cvv"),
    )

    assert aadhaar_result.aadhaar_last4 == "4321"
    assert aadhaar_result.cvv is None
    assert cvv_result.cvv == "4321"
    assert cvv_result.aadhaar_last4 is None


def test_ordered_form_values_are_not_mapped_without_explicit_labels(parser: DeterministicInputParser):
    result = parser.extract(
        "1990-05-14, Nithin Jain, 4532015112830366",
        context=parser_context("dob", "full_name", "card_number"),
    )

    assert result.dob is None
    assert result.full_name is None
    assert result.card_number is None
    assert result.intent == UserIntent.UNKNOWN
    assert result.proposed_action == ProposedAction.NONE


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "confirm",
        "confirmed",
        "go ahead",
        "proceed",
        "process it",
        "make the payment",
    ],
)
def test_confirmation_is_extracted_only_when_expected(
    parser: DeterministicInputParser,
    text: str,
):
    result = parser.extract(text, context=confirmation_context())

    assert result.confirmation is True
    assert result.intent == UserIntent.CONFIRM_PAYMENT
    assert result.proposed_action == ProposedAction.CONFIRM_PAYMENT


def test_confirmation_without_context_is_not_extracted(parser: DeterministicInputParser):
    result = parser.extract("yes")

    assert result.confirmation is None
    assert result.intent == UserIntent.UNKNOWN
    assert result.proposed_action == ProposedAction.NONE


@pytest.mark.parametrize(
    "text",
    [
        "cancel",
        "stop",
        "exit",
        "quit",
        "never mind",
        "nevermind",
    ],
)
def test_cancel_variants(parser: DeterministicInputParser, text: str):
    result = parser.extract(text)

    assert result.intent == UserIntent.CANCEL
    assert result.proposed_action == ProposedAction.CANCEL
    assert result.confirmation is None


def test_cancel_takes_precedence_over_confirmation(parser: DeterministicInputParser):
    result = parser.extract("cancel, do not proceed yes", context=confirmation_context())

    assert result.intent == UserIntent.CANCEL
    assert result.proposed_action == ProposedAction.CANCEL
    assert result.confirmation is None


def test_parser_returns_model_even_when_mixed_valid_and_invalid_fields(
    parser: DeterministicInputParser,
):
    result = parser.extract("account ACC1001 dob is 1989-02-29 pay 500")

    assert isinstance(result, ExtractedUserInput)
    assert result.account_id == "ACC1001"
    assert result.payment_amount == Decimal("500")
    assert result.dob is None
    assert result.intent == UserIntent.LOOKUP_ACCOUNT
    assert result.proposed_action == ProposedAction.LOOKUP_ACCOUNT
