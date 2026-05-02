from __future__ import annotations

from settlesentry.agent.actions import UserIntent
from settlesentry.integrations.payments.schemas import PaymentsAPIErrorCode

# Side questions should answer briefly and preserve the pending workflow state.
SIDE_QUESTION_INTENTS = {
    UserIntent.ASK_AGENT_IDENTITY,
    UserIntent.ASK_AGENT_CAPABILITY,
    UserIntent.ASK_CURRENT_STATUS,
    UserIntent.ASK_TO_REPEAT,
}

# Lightweight correction detector. Parser may miss correction intent, so this
# catches common natural-language corrections.
CORRECTION_TOKENS = (
    "correct",
    "correction",
    "change",
    "update",
    "actually",
    "mistake",
    "wrong",
    "typo",
    "edit",
)

CORRECTABLE_FIELDS = (
    "account_id",
    "full_name",
    "dob",
    "aadhaar_last4",
    "pincode",
    "payment_amount",
    "cardholder_name",
    "card_number",
    "cvv",
    "expiry_month",
    "expiry_year",
)

# Lookup service failures are mapped away from payment failures because payment
# has not started yet.
LOOKUP_SERVICE_ERROR_STATUSES = {
    "invalid_response",
    "unexpected_status",
    "network_error",
    "timeout",
}

# Terminal payment errors are not auto-retried by the agent because payment
# status may be ambiguous.
TERMINAL_PAYMENT_SERVICE_ERRORS = {
    PaymentsAPIErrorCode.NETWORK_ERROR,
    PaymentsAPIErrorCode.TIMEOUT,
    PaymentsAPIErrorCode.INVALID_RESPONSE,
    PaymentsAPIErrorCode.UNEXPECTED_STATUS,
}

AMOUNT_RETRY_ERRORS = {
    PaymentsAPIErrorCode.INVALID_AMOUNT,
    PaymentsAPIErrorCode.INSUFFICIENT_BALANCE,
}
