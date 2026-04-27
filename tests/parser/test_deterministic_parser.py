from decimal import Decimal

import pytest
from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.state import ExtractedUserInput


@pytest.fixture
def parser() -> DeterministicInputParser:
    return DeterministicInputParser()


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


@pytest.mark.parametrize(
    "text",
    [
        "ACC1001",
        "account id ACC1001",
        "account_id=ACC1001",
        "My account number is ACC1001",
        "Please check acc1001",
    ],
)
def test_account_id_common_variants(parser: DeterministicInputParser, text: str):
    result = parser.extract(text)

    assert result.account_id == "ACC1001"


def test_account_id_does_not_extract_invalid_prefix(parser: DeterministicInputParser):
    result = parser.extract("my account is AC1001")

    assert result.account_id is None


def test_full_name_is_extracted_from_common_intro(parser: DeterministicInputParser):
    result = parser.extract("My name is Nithin Jain")

    assert result.full_name == "Nithin Jain"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


@pytest.mark.parametrize(
    ("text", "expected_name"),
    [
        ("I am Nithin Jain", "Nithin Jain"),
        ("I'm Nithin Jain", "Nithin Jain"),
        ("This is Nithin Jain", "Nithin Jain"),
        ("name is Nithin Jain", "Nithin Jain"),
        ("My name is Nithin K. Jain", "Nithin K. Jain"),
        ("My name is Anne-Marie D'Souza", "Anne-Marie D'Souza"),
    ],
)
def test_full_name_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_name: str,
):
    result = parser.extract(text)

    assert result.full_name == expected_name


def test_full_name_stops_before_identity_fields(parser: DeterministicInputParser):
    result = parser.extract("My name is Nithin Jain dob is 1990-05-14")

    assert result.full_name == "Nithin Jain"
    assert result.dob == "1990-05-14"


def test_full_name_stops_before_account_field(parser: DeterministicInputParser):
    result = parser.extract("I am Nithin Jain account is ACC1001")

    assert result.full_name == "Nithin Jain"
    assert result.account_id == "ACC1001"


def test_full_name_stops_before_cvv_field(parser: DeterministicInputParser):
    result = parser.extract("I am Nithin Jain cvv 123")

    assert result.full_name == "Nithin Jain"
    assert result.cvv == "123"


def test_identity_bundle_is_extracted(parser: DeterministicInputParser):
    text = "I am Nithin Jain. DOB is 1990-05-14, aadhaar_last4=4321 and pincode 400001."

    result = parser.extract(text)

    assert result.full_name == "Nithin Jain"
    assert result.dob == "1990-05-14"
    assert result.aadhaar_last4 == "4321"
    assert result.pincode == "400001"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


@pytest.mark.parametrize(
    ("text", "expected_dob"),
    [
        ("dob=1990-05-14", "1990-05-14"),
        ("dob: 1990-05-14", "1990-05-14"),
        ("dob is 1990-05-14", "1990-05-14"),
        ("date of birth is 1990-05-14", "1990-05-14"),
    ],
)
def test_dob_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_dob: str,
):
    result = parser.extract(text)

    assert result.dob == expected_dob


def test_invalid_dob_does_not_crash_parser(parser: DeterministicInputParser):
    result = parser.extract("dob is 1989-02-29")

    assert isinstance(result, ExtractedUserInput)
    assert result.dob is None


@pytest.mark.parametrize(
    ("text", "expected_last4"),
    [
        ("aadhaar=4321", "4321"),
        ("aadhaar 4321", "4321"),
        ("aadhaar_last4=4321", "4321"),
        ("aadhaar last 4 is 4321", "4321"),
    ],
)
def test_aadhaar_last4_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_last4: str,
):
    result = parser.extract(text)

    assert result.aadhaar_last4 == expected_last4


def test_aadhaar_last4_does_not_extract_full_aadhaar_as_last4(parser: DeterministicInputParser):
    result = parser.extract("aadhaar is 432143214321")

    assert result.aadhaar_last4 is None


@pytest.mark.parametrize(
    ("text", "expected_pincode"),
    [
        ("pincode=400001", "400001"),
        ("pincode: 400001", "400001"),
        ("pincode is 400001", "400001"),
        ("pin code is 400001", "400001"),
    ],
)
def test_pincode_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_pincode: str,
):
    result = parser.extract(text)

    assert result.pincode == expected_pincode


def test_invalid_pincode_does_not_crash_parser(parser: DeterministicInputParser):
    result = parser.extract("pincode is 40001")

    assert isinstance(result, ExtractedUserInput)
    assert result.pincode is None


@pytest.mark.parametrize(
    ("text", "expected_amount"),
    [
        ("pay 500", Decimal("500")),
        ("pay ₹500", Decimal("500")),
        ("pay INR 500", Decimal("500")),
        ("payment amount is 500", Decimal("500")),
        ("amount=500.50", Decimal("500.50")),
        ("pay 1,250.75", Decimal("1250.75")),
        ("pay 1,23,250.75", Decimal("123250.75")),
        ("settle 1250.75", Decimal("1250.75")),
        ("collect Rs. 300", Decimal("300")),
    ],
)
def test_payment_amount_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_amount: Decimal,
):
    result = parser.extract(text)

    assert result.payment_amount == expected_amount
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


@pytest.mark.parametrize(
    "text",
    [
        "pay 0",
        "pay -100",
        "pay 500.001",
        "amount is abc",
    ],
)
def test_invalid_payment_amount_is_not_extracted(parser: DeterministicInputParser, text: str):
    result = parser.extract(text)

    assert result.payment_amount is None


def test_payment_amount_without_payment_context_is_not_extracted(parser: DeterministicInputParser):
    result = parser.extract("my pincode is 400001 and dob is 1990-05-14")

    assert result.payment_amount is None


@pytest.mark.parametrize(
    ("text", "expected_card"),
    [
        ("card number=4532015112830366", "4532015112830366"),
        ("card number is 4532 0151 1283 0366", "4532 0151 1283 0366"),
        ("credit card: 4532-0151-1283-0366", "4532-0151-1283-0366"),
        ("debit card 4532015112830366", "4532015112830366"),
    ],
)
def test_card_number_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_card: str,
):
    result = parser.extract(text)

    assert result.card_number == expected_card
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_card_number_without_card_label_is_not_extracted(parser: DeterministicInputParser):
    result = parser.extract("4532015112830366")

    assert result.card_number is None


@pytest.mark.parametrize(
    ("text", "expected_cvv"),
    [
        ("cvv=123", "123"),
        ("cvv: 123", "123"),
        ("cvv is 123", "123"),
        ("cvc is 1234", "1234"),
    ],
)
def test_cvv_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_cvv: str,
):
    result = parser.extract(text)

    assert result.cvv == expected_cvv


def test_invalid_cvv_is_not_extracted(parser: DeterministicInputParser):
    result = parser.extract("cvv is 12")

    assert result.cvv is None


@pytest.mark.parametrize(
    ("text", "expected_month", "expected_year"),
    [
        ("expiry=12/2027", 12, 2027),
        ("expiry is 12-2027", 12, 2027),
        ("exp 12/27", 12, 2027),
        ("valid till 1/2028", 1, 2028),
        ("valid till 01/28", 1, 2028),
    ],
)
def test_expiry_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_month: int,
    expected_year: int,
):
    result = parser.extract(text)

    assert result.expiry_month == expected_month
    assert result.expiry_year == expected_year


@pytest.mark.parametrize(
    "text",
    [
        "expiry is 13/2027",
        "expiry is 00/2027",
        "expiry is 12/abc",
    ],
)
def test_invalid_expiry_is_not_extracted(parser: DeterministicInputParser, text: str):
    result = parser.extract(text)

    assert result.expiry_month is None
    assert result.expiry_year is None


@pytest.mark.parametrize(
    ("text", "expected_name"),
    [
        ("cardholder name is Nithin Jain", "Nithin Jain"),
        ("card holder is Nithin Jain", "Nithin Jain"),
        ("name on card is Nithin Jain", "Nithin Jain"),
        ("cardholder=Nithin K. Jain", "Nithin K. Jain"),
    ],
)
def test_cardholder_name_variants(
    parser: DeterministicInputParser,
    text: str,
    expected_name: str,
):
    result = parser.extract(text)

    assert result.cardholder_name == expected_name


def test_cardholder_name_stops_before_cvv_field(parser: DeterministicInputParser):
    result = parser.extract("cardholder name is Nithin Jain cvv 123")

    assert result.cardholder_name == "Nithin Jain"
    assert result.cvv == "123"


def test_complete_payment_message_extracts_multiple_fields(parser: DeterministicInputParser):
    text = (
        "I want to pay ₹500. "
        "Cardholder name is Nithin Jain. "
        "Card number is 4532 0151 1283 0366. "
        "CVV is 123. "
        "Expiry is 12/2027."
    )

    result = parser.extract(text)

    assert result.payment_amount == Decimal("500")
    assert result.cardholder_name == "Nithin Jain"
    assert result.card_number == "4532 0151 1283 0366"
    assert result.cvv == "123"
    assert result.expiry_month == 12
    assert result.expiry_year == 2027
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "yeah",
        "yep",
        "confirm",
        "confirmed",
        "go ahead",
        "proceed",
        "process it",
        "make the payment",
    ],
)
def test_confirmation_variants(parser: DeterministicInputParser, text: str):
    result = parser.extract(text)

    assert result.confirmation is True
    assert result.intent == UserIntent.CONFIRM_PAYMENT
    assert result.proposed_action == ProposedAction.PROCESS_PAYMENT


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
    result = parser.extract("cancel, do not proceed yes")

    assert result.intent == UserIntent.CANCEL
    assert result.proposed_action == ProposedAction.CANCEL
    assert result.confirmation is None


def test_out_of_order_user_message_extracts_all_relevant_fields(parser: DeterministicInputParser):
    text = (
        "Pay 500 using card number 4532015112830366, "
        "my account is ACC1001, dob is 1990-05-14, "
        "I am Nithin Jain, cvv 123, expiry 12/2027."
    )

    result = parser.extract(text)

    assert result.account_id == "ACC1001"
    assert result.full_name == "Nithin Jain"
    assert result.dob == "1990-05-14"
    assert result.payment_amount == Decimal("500")
    assert result.card_number == "4532015112830366"
    assert result.cvv == "123"
    assert result.expiry_month == 12
    assert result.expiry_year == 2027
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_parser_does_not_infer_missing_fields(parser: DeterministicInputParser):
    result = parser.extract("I want to make a payment")

    assert result.account_id is None
    assert result.full_name is None
    assert result.payment_amount is None
    assert result.card_number is None
    assert result.confirmation is None


def test_parser_returns_model_even_when_mixed_valid_and_invalid_fields(parser: DeterministicInputParser):
    result = parser.extract("account ACC1001 dob is 1989-02-29 pay 500")

    assert isinstance(result, ExtractedUserInput)
    assert result.account_id == "ACC1001"
    assert result.payment_amount == Decimal("500")
    assert result.dob is None
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_retains_valid_fields_when_multiple_fields_are_invalid(parser: DeterministicInputParser):
    result = parser.extract(
        "dob is 1989-02-29 amount is abc account ACC1001 card number 4532015112830366"
    )

    assert result.dob is None
    assert result.payment_amount is None
    assert result.account_id == "ACC1001"
    assert result.card_number == "4532015112830366"


def test_invalid_amount_does_not_drop_other_valid_fields(parser: DeterministicInputParser):
    result = parser.extract("account ACC1001 pay 0")

    assert result.account_id == "ACC1001"
    assert result.payment_amount is None
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT


def test_extremely_large_amount_is_extracted_precisely(parser: DeterministicInputParser):
    result = parser.extract("pay 10970787975385595793.09")

    assert result.payment_amount == Decimal("10970787975385595793.09")
    assert result.intent == UserIntent.MAKE_PAYMENT
    assert result.proposed_action == ProposedAction.PREPARE_PAYMENT
