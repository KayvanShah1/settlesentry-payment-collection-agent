from decimal import Decimal

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.base import ParserContext
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.state import ConversationState, ConversationStep


def context_for(
    *,
    step: ConversationStep,
    expected_fields: tuple,
    last_assistant_message: str | None = None,
) -> ParserContext:
    return ParserContext.from_state(
        ConversationState(step=step),
        expected_fields=expected_fields,
        last_assistant_message=last_assistant_message,
    )


def test_bare_dob_is_extracted_when_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("dob",),
        last_assistant_message="What is your DOB?",
    )

    result = parser.extract("1990-05-14", context=context)

    assert result.dob == "1990-05-14"
    assert result.intent == UserIntent.VERIFY_IDENTITY
    assert result.proposed_action == ProposedAction.VERIFY_IDENTITY


def test_bare_name_is_extracted_when_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_FULL_NAME,
        expected_fields=("full_name",),
        last_assistant_message="Please enter your full name.",
    )

    result = parser.extract("Nithin Jain", context=context)

    assert result.full_name == "Nithin Jain"
    assert result.intent == UserIntent.VERIFY_IDENTITY


def test_same_bare_digits_are_mapped_by_expected_field():
    parser = DeterministicInputParser()

    aadhaar_context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("aadhaar_last4",),
    )
    cvv_context = context_for(
        step=ConversationStep.WAITING_FOR_CVV,
        expected_fields=("cvv",),
    )

    aadhaar_result = parser.extract("4321", context=aadhaar_context)
    cvv_result = parser.extract("4321", context=cvv_context)

    assert aadhaar_result.aadhaar_last4 == "4321"
    assert aadhaar_result.cvv is None

    assert cvv_result.cvv == "4321"
    assert cvv_result.aadhaar_last4 is None


def test_bare_pincode_is_extracted_when_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("pincode",),
    )

    result = parser.extract("400001", context=context)

    assert result.pincode == "400001"
    assert result.intent == UserIntent.VERIFY_IDENTITY


def test_bare_payment_amount_is_extracted_when_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        expected_fields=("payment_amount",),
    )

    result = parser.extract("1250.75", context=context)

    assert result.payment_amount == Decimal("1250.75")
    assert result.intent == UserIntent.MAKE_PAYMENT


def test_bare_card_number_is_extracted_when_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_CARD_NUMBER,
        expected_fields=("card_number",),
    )

    result = parser.extract("4532 0151 1283 0366", context=context)

    assert result.card_number == "4532015112830366"
    assert result.intent == UserIntent.MAKE_PAYMENT


def test_bare_expiry_is_extracted_when_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_EXPIRY,
        expected_fields=("expiry",),
    )

    result = parser.extract("12/27", context=context)

    assert result.expiry_month == 12
    assert result.expiry_year == 2027
    assert result.intent == UserIntent.MAKE_PAYMENT


def test_bare_confirmation_is_extracted_when_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION,
        expected_fields=("confirmation",),
    )

    result = parser.extract("yes", context=context)

    assert result.confirmation is True
    assert result.intent == UserIntent.CONFIRM_PAYMENT
    assert result.proposed_action == ProposedAction.PROCESS_PAYMENT


def test_ordered_form_values_in_expected_order():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("dob", "full_name", "card_number"),
    )

    result = parser.extract("1990-05-14, Nithin Jain, 4532015112830366", context=context)

    assert result.dob == "1990-05-14"
    assert result.full_name == "Nithin Jain"
    assert result.card_number == "4532015112830366"


def test_identity_form_style_reply_is_mapped_by_expected_order():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("full_name", "dob", "pincode"),
    )

    result = parser.extract("Nithin Jain, 1990-05-14, 400001", context=context)

    assert result.full_name == "Nithin Jain"
    assert result.dob == "1990-05-14"
    assert result.pincode == "400001"
    assert result.intent == UserIntent.VERIFY_IDENTITY


def test_identity_form_style_reply_with_secondary_factors():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("dob", "aadhaar_last4", "pincode"),
    )

    result = parser.extract("1990-05-14, 4321, 400001", context=context)

    assert result.dob == "1990-05-14"
    assert result.aadhaar_last4 == "4321"
    assert result.pincode == "400001"
    assert result.intent == UserIntent.VERIFY_IDENTITY


def test_card_form_style_reply_is_mapped_by_expected_order():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_CARDHOLDER_NAME,
        expected_fields=("cardholder_name", "card_number", "cvv", "expiry"),
    )

    result = parser.extract(
        "Nithin Jain, 4532 0151 1283 0366, 123, 12/2027",
        context=context,
    )

    assert result.cardholder_name == "Nithin Jain"
    assert result.card_number == "4532015112830366"
    assert result.cvv == "123"
    assert result.expiry_month == 12
    assert result.expiry_year == 2027
    assert result.intent == UserIntent.MAKE_PAYMENT


def test_form_style_amount_with_comma_is_not_split_inside_number():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        expected_fields=("cardholder_name", "payment_amount", "cvv"),
    )

    result = parser.extract("Nithin Jain, 1,250.75, 123", context=context)

    assert result.cardholder_name == "Nithin Jain"
    assert result.payment_amount == Decimal("1250.75")
    assert result.cvv == "123"
    assert result.intent == UserIntent.MAKE_PAYMENT


def test_ordered_form_amount_with_thousands_separator_and_card_number():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        expected_fields=("payment_amount", "card_number"),
    )

    result = parser.extract("1,250.75, 4532 0151 1283 0366", context=context)

    assert result.payment_amount == Decimal("1250.75")
    assert result.card_number == "4532015112830366"


def test_ordered_form_amount_with_indian_separator_and_card_number():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        expected_fields=("payment_amount", "card_number"),
    )

    result = parser.extract("1,23,250.75, 4532 0151 1283 0366", context=context)

    assert result.payment_amount == Decimal("123250.75")
    assert result.card_number == "4532015112830366"


def test_ordered_form_with_fewer_values_than_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("dob", "full_name", "card_number"),
    )

    result = parser.extract("1990-05-14, Nithin Jain", context=context)

    assert result.dob == "1990-05-14"
    assert result.full_name == "Nithin Jain"
    assert result.card_number is None


def test_ordered_form_with_more_values_than_expected():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        expected_fields=("dob", "full_name"),
    )

    result = parser.extract("1990-05-14, Nithin Jain, ignored extra value", context=context)

    assert result.dob == "1990-05-14"
    assert result.full_name == "Nithin Jain"
    assert result.card_number is None


def test_ordered_form_handles_mixed_delimiters():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        expected_fields=("payment_amount", "card_number", "cvv", "expiry"),
    )

    result = parser.extract("1,250.75; 4532 0151 1283 0366\n123;12/2027", context=context)

    assert result.payment_amount == Decimal("1250.75")
    assert result.card_number == "4532015112830366"
    assert result.cvv == "123"
    assert result.expiry_month == 12
    assert result.expiry_year == 2027


def test_context_does_not_make_unexpected_bare_value_infer_sensitive_field():
    parser = DeterministicInputParser()
    context = context_for(
        step=ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        expected_fields=("payment_amount",),
    )

    result = parser.extract("4321", context=context)

    assert result.payment_amount == Decimal("4321")
    assert result.aadhaar_last4 is None
    assert result.cvv is None
