from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.state import ExtractedUserInput

ACCOUNT_ID_RE = re.compile(r"\bACC\d+\b", re.IGNORECASE)

DOB_RE = re.compile(
    r"(?i)\b(?:dob|date\s+of\s+birth)\s*(?:is|:|=)?\s*"
    r"(?P<dob>\d{4}-\d{2}-\d{2})\b"
)

AADHAAR_LAST4_RE = re.compile(
    r"(?i)\b(?:aadhaar(?:_last4|\s+last\s+4)?|aadhaar)\s*(?:is|:|=)?\s*"
    r"(?P<aadhaar_last4>\d{4})\b"
)

PINCODE_RE = re.compile(
    r"(?i)\b(?:pincode|pin\s+code)\s*(?:is|:|=)?\s*"
    r"(?P<pincode>\d{6})\b"
)

AMOUNT_RE = re.compile(
    r"(?i)\b(?:pay|payment|amount|collect|settle)\s*(?:is|of|:|=)?\s*"
    r"(?:rs\.?|inr|\u20B9)?\s*"
    r"(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)(?!\.\d)(?![\d,])\b"
)

CARD_NUMBER_RE = re.compile(
    r"(?i)\b(?:card(?:\s+number|_number)?|credit\s+card|debit\s+card)\s*(?:is|:|=)?\s*"
    r"(?P<card_number>\d[\d\s-]{11,22}\d)\b"
)

CVV_RE = re.compile(
    r"(?i)\b(?:cvv|cvc)\s*(?:is|:|=)?\s*"
    r"(?P<cvv>\d{3,4})\b"
)

EXPIRY_RE = re.compile(
    r"(?i)\b(?:expiry|exp|valid\s+till)\s*(?:is|:|=)?\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s*(?:/|-|\s+)\s*(?P<year>\d{2}|\d{4})\b"
)

CARDHOLDER_RE = re.compile(
    r"(?i)\b(?:cardholder|card\s+holder|name\s+on\s+card)\s*(?:name)?\s*(?:is|:|=)?\s*"
    r"(?P<cardholder_name>[A-Za-z][A-Za-z\s.'-]{1,80})"
)

FULL_NAME_RE = re.compile(
    r"(?i)\b(?:my\s+name\s+is|i\s+am|i'm|this\s+is|name\s+is)\s+"
    r"(?P<full_name>[A-Za-z][A-Za-z\s.'-]{1,80})"
)

CONFIRM_RE = re.compile(
    r"(?i)\b(?:yes|yeah|yep|confirm|confirmed|go\s+ahead|proceed|process\s+it|make\s+the\s+payment)\b"
)

CANCEL_RE = re.compile(r"(?i)\b(?:cancel|stop|exit|quit|never\s+mind|nevermind)\b")


class DeterministicInputParser:
    """
    Lightweight parser for deterministic/local agent runs.

    It extracts only explicit values from user text. It does not infer identity
    verification, approve payments, or decide whether tools are allowed.
    """

    def extract(self, user_input: str) -> ExtractedUserInput:
        text = user_input.strip()
        extracted: dict[str, object] = {}

        self._extract_account_id(text, extracted)
        self._extract_identity_fields(text, extracted)
        self._extract_payment_fields(text, extracted)
        self._extract_intent_and_action(text, extracted)

        return self._safe_model_validate(extracted)

    def _extract_account_id(self, text: str, extracted: dict[str, object]) -> None:
        match = ACCOUNT_ID_RE.search(text)

        if match:
            extracted["account_id"] = match.group(0).upper()

    def _extract_identity_fields(self, text: str, extracted: dict[str, object]) -> None:
        if match := FULL_NAME_RE.search(text):
            extracted["full_name"] = self._clean_name(match.group("full_name"))

        if match := DOB_RE.search(text):
            extracted["dob"] = match.group("dob")

        if match := AADHAAR_LAST4_RE.search(text):
            extracted["aadhaar_last4"] = match.group("aadhaar_last4")

        if match := PINCODE_RE.search(text):
            extracted["pincode"] = match.group("pincode")

    def _extract_payment_fields(self, text: str, extracted: dict[str, object]) -> None:
        if match := AMOUNT_RE.search(text):
            amount = self._parse_decimal(match.group("amount"))
            if amount is not None:
                extracted["payment_amount"] = amount

        if match := CARDHOLDER_RE.search(text):
            extracted["cardholder_name"] = self._clean_name(match.group("cardholder_name"))

        if match := CARD_NUMBER_RE.search(text):
            extracted["card_number"] = match.group("card_number").strip()

        if match := CVV_RE.search(text):
            extracted["cvv"] = match.group("cvv")

        if match := EXPIRY_RE.search(text):
            extracted["expiry_month"] = int(match.group("month"))
            extracted["expiry_year"] = self._normalize_expiry_year(match.group("year"))

    def _extract_intent_and_action(self, text: str, extracted: dict[str, object]) -> None:
        lowered = text.lower()

        if CANCEL_RE.search(text):
            extracted["intent"] = UserIntent.CANCEL
            extracted["proposed_action"] = ProposedAction.CANCEL
            return

        if CONFIRM_RE.search(text):
            extracted["intent"] = UserIntent.CONFIRM_PAYMENT
            extracted["proposed_action"] = ProposedAction.PROCESS_PAYMENT
            extracted["confirmation"] = True
            return

        if "account_id" in extracted or "account_id" in lowered or ACCOUNT_ID_RE.search(text):
            extracted.setdefault("intent", UserIntent.LOOKUP_ACCOUNT)
            extracted.setdefault("proposed_action", ProposedAction.LOOKUP_ACCOUNT)

        if any(key in extracted for key in ("full_name", "dob", "aadhaar_last4", "pincode")):
            extracted.setdefault("intent", UserIntent.VERIFY_IDENTITY)
            extracted.setdefault("proposed_action", ProposedAction.VERIFY_IDENTITY)

        if any(
            key in extracted
            for key in (
                "payment_amount",
                "cardholder_name",
                "card_number",
                "cvv",
                "expiry_month",
                "expiry_year",
            )
        ):
            extracted["intent"] = UserIntent.MAKE_PAYMENT
            extracted["proposed_action"] = ProposedAction.PREPARE_PAYMENT

    @staticmethod
    def _clean_name(value: str) -> str:
        cleaned = value.strip(" .,'-\n\t")

        stop_words = (
            " dob ",
            " date of birth ",
            " aadhaar ",
            " pincode ",
            " account ",
            " card ",
            " cvv ",
            " cvc ",
            " expiry ",
            " exp ",
            " valid till ",
            " amount ",
            " payment ",
            " pay ",
        )

        lowered = f" {cleaned.lower()} "
        cut_positions = [lowered.find(stop_word) for stop_word in stop_words if lowered.find(stop_word) != -1]

        if cut_positions:
            cleaned = cleaned[: min(cut_positions)].strip(" .,'-\n\t")

        return " ".join(cleaned.split())

    @staticmethod
    def _parse_decimal(value: str) -> Decimal | None:
        try:
            return Decimal(value.replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _normalize_expiry_year(value: str) -> int:
        year = int(value)

        if year < 100:
            return 2000 + year

        return year

    @staticmethod
    def _safe_model_validate(data: dict[str, object]) -> ExtractedUserInput:
        try:
            return ExtractedUserInput.model_validate(data)
        except ValidationError:
            # Keep as many valid extracted fields as possible instead of dropping
            # everything due to one invalid value (for example, bad DOB format).
            sanitized: dict[str, object] = {}
            for key, value in data.items():
                candidate = {**sanitized, key: value}
                try:
                    ExtractedUserInput.model_validate(candidate)
                except ValidationError:
                    continue
                sanitized[key] = value

            return ExtractedUserInput.model_validate(sanitized)

