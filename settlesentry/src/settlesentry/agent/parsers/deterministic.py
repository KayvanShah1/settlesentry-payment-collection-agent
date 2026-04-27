from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.base import ExpectedField, ParserContext
from settlesentry.agent.state import ExtractedUserInput

ACCOUNT_ID_RE = re.compile(r"\bACC\d+\b", re.IGNORECASE)

DOB_RE = re.compile(
    r"(?i)\b(?:dob|date\s+of\s+birth)\s*(?:is|:|=)?\s*"
    r"(?P<dob>\d{4}-\d{2}-\d{2})\b"
)
BARE_DOB_RE = re.compile(r"^\s*(?P<dob>\d{4}-\d{2}-\d{2})\s*$")

AADHAAR_LAST4_RE = re.compile(
    r"(?i)\b(?:aadhaar(?:_last4|\s+last\s+4)?|aadhaar)\s*(?:is|:|=)?\s*"
    r"(?P<aadhaar_last4>\d{4})(?!\d)\b"
)
BARE_4_DIGITS_RE = re.compile(r"^\s*(?P<value>\d{4})\s*$")

PINCODE_RE = re.compile(
    r"(?i)\b(?:pincode|pin\s+code)\s*(?:is|:|=)?\s*"
    r"(?P<pincode>\d{6})(?!\d)\b"
)
BARE_PINCODE_RE = re.compile(r"^\s*(?P<pincode>\d{6})\s*$")

AMOUNT_VALUE_RE = (
    r"(?:"
    r"\d{1,3}(?:,\d{2})*,\d{3}"  # Indian grouping: 1,23,456 or 12,34,56,789
    r"|"
    r"\d{1,3}(?:,\d{3})+"  # Western grouping: 1,234,567
    r"|"
    r"\d+"  # No grouping
    r")"
    r"(?:\.\d{1,2})?"
)

AMOUNT_RE = re.compile(
    r"(?i)\b(?:pay|payment|amount|collect|settle)\s*(?:is|of|:|=)?\s*"
    r"(?:rs\.?|inr|\u20B9)?\s*"
    rf"(?P<amount>{AMOUNT_VALUE_RE})(?!\.\d)(?![\d,])\b"
)
BARE_AMOUNT_RE = re.compile(
    r"^\s*(?:rs\.?|inr|\u20B9)?\s*"
    rf"(?P<amount>{AMOUNT_VALUE_RE})(?!\.\d)(?![\d,])\s*$",
    re.IGNORECASE,
)

CARD_NUMBER_RE = re.compile(
    r"(?i)\b(?:card(?:\s+number|_number)?|credit\s+card|debit\s+card)\s*(?:is|:|=)?\s*"
    r"(?P<card_number>\d[\d\s-]{11,22}\d)\b"
)
BARE_CARD_NUMBER_RE = re.compile(r"^\s*(?P<card_number>\d[\d\s-]{11,22}\d)\s*$")

CVV_RE = re.compile(
    r"(?i)\b(?:cvv|cvc)\s*(?:is|:|=)?\s*"
    r"(?P<cvv>\d{3,4})\b"
)
BARE_CVV_RE = re.compile(r"^\s*(?P<cvv>\d{3,4})\s*$")

EXPIRY_RE = re.compile(
    r"(?i)\b(?:expiry|exp|valid\s+till)\s*(?:is|:|=)?\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s*(?:/|-|\s+)\s*(?P<year>\d{2}|\d{4})\b"
)
BARE_EXPIRY_RE = re.compile(r"^\s*(?P<month>0?[1-9]|1[0-2])\s*(?:/|-|\s+)\s*(?P<year>\d{2}|\d{4})\s*$")

CARDHOLDER_RE = re.compile(
    r"(?i)\b(?:cardholder|card\s+holder|name\s+on\s+card)\s*(?:name)?\s*(?:is|:|=)?\s*"
    r"(?P<cardholder_name>[A-Za-z][A-Za-z\s.'-]{1,80})"
)

FULL_NAME_RE = re.compile(
    r"(?i)\b(?:my\s+name\s+is|i\s+am|i'm|this\s+is|name\s+is)\s+"
    r"(?P<full_name>[A-Za-z][A-Za-z\s.'-]{1,80})"
)
BARE_NAME_RE = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z\s.'-]{1,80})\s*$")

CONFIRM_RE = re.compile(
    r"(?i)\b(?:yes|yeah|yep|confirm|confirmed|go\s+ahead|proceed|process\s+it|make\s+the\s+payment)\b"
)
CANCEL_RE = re.compile(r"(?i)\b(?:cancel|stop|exit|quit|never\s+mind|nevermind)\b")

FIELD_TO_OUTPUT_KEYS: dict[ExpectedField, tuple[str, ...]] = {
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


class DeterministicInputParser:
    """
    Deterministic fallback parser.

    It extracts explicit/labeled values globally. When parser context provides
    expected fields, it also supports safe bare slot replies.
    """

    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput:
        text = user_input.strip()
        extracted: dict[str, object] = {}

        self._extract_account_id(text, extracted)
        self._extract_identity_fields(text, extracted)
        self._extract_payment_fields(text, extracted)
        self._extract_contextual_slots(text, context, extracted)
        self._extract_intent_and_action(text, context, extracted)

        return self._safe_model_validate(extracted)

    def _extract_account_id(self, text: str, extracted: dict[str, object]) -> None:
        if match := ACCOUNT_ID_RE.search(text):
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
            extracted["card_number"] = match.group("card_number").strip(" .,;")

        if match := CVV_RE.search(text):
            extracted["cvv"] = match.group("cvv")

        if match := EXPIRY_RE.search(text):
            extracted["expiry_month"] = int(match.group("month"))
            extracted["expiry_year"] = self._normalize_expiry_year(match.group("year"))

    def _extract_contextual_slots(
        self,
        text: str,
        context: ParserContext | None,
        extracted: dict[str, object],
    ) -> None:
        if not context or not context.expected_fields:
            return

        parts = self._split_form_parts(text)

        if len(context.expected_fields) > 1 and len(parts) > 1:
            self._extract_ordered_form_parts(parts, context.expected_fields, extracted)
            return

        for field in context.expected_fields:
            if self._field_already_extracted(field, extracted):
                continue

            self._extract_bare_field(text, field, extracted)

    def _extract_ordered_form_parts(
        self,
        parts: list[str],
        expected_fields: tuple[ExpectedField, ...],
        extracted: dict[str, object],
    ) -> None:
        for field, part in zip(expected_fields, parts, strict=False):
            if self._field_already_extracted(field, extracted):
                continue

            self._extract_bare_field(part, field, extracted)

    def _extract_bare_field(
        self,
        text: str,
        field: ExpectedField,
        extracted: dict[str, object],
    ) -> None:
        if field == "account_id":
            self._extract_account_id(text, extracted)
            return

        if field in {"full_name", "cardholder_name"}:
            if match := BARE_NAME_RE.fullmatch(text):
                extracted[field] = self._clean_name(match.group("name"))
            return

        if field == "dob":
            if match := BARE_DOB_RE.fullmatch(text):
                extracted["dob"] = match.group("dob")
            return

        if field == "aadhaar_last4":
            if match := BARE_4_DIGITS_RE.fullmatch(text):
                extracted["aadhaar_last4"] = match.group("value")
            return

        if field == "pincode":
            if match := BARE_PINCODE_RE.fullmatch(text):
                extracted["pincode"] = match.group("pincode")
            return

        if field == "payment_amount":
            if match := BARE_AMOUNT_RE.fullmatch(text):
                amount = self._parse_decimal(match.group("amount"))
                if amount is not None:
                    extracted["payment_amount"] = amount
            return

        if field == "card_number":
            if match := BARE_CARD_NUMBER_RE.fullmatch(text):
                extracted["card_number"] = match.group("card_number").strip(" .,;")
            return

        if field == "cvv":
            if match := BARE_CVV_RE.fullmatch(text):
                extracted["cvv"] = match.group("cvv")
            return

        if field == "expiry":
            if match := BARE_EXPIRY_RE.fullmatch(text):
                extracted["expiry_month"] = int(match.group("month"))
                extracted["expiry_year"] = self._normalize_expiry_year(match.group("year"))
            return

        if field == "confirmation" and CONFIRM_RE.search(text):
            extracted["confirmation"] = True

    def _extract_intent_and_action(
        self,
        text: str,
        context: ParserContext | None,
        extracted: dict[str, object],
    ) -> None:
        lowered = text.lower()

        if CANCEL_RE.search(text):
            extracted["intent"] = UserIntent.CANCEL
            extracted["proposed_action"] = ProposedAction.CANCEL
            return

        if CONFIRM_RE.search(text) or extracted.get("confirmation") is True:
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

        if context and not extracted.get("intent") and context.expected_fields:
            self._set_intent_from_expected_fields(context.expected_fields, extracted)

    @staticmethod
    def _set_intent_from_expected_fields(
        expected_fields: tuple[ExpectedField, ...],
        extracted: dict[str, object],
    ) -> None:
        if any(field in expected_fields for field in ("full_name", "dob", "aadhaar_last4", "pincode")):
            extracted.setdefault("intent", UserIntent.VERIFY_IDENTITY)
            extracted.setdefault("proposed_action", ProposedAction.VERIFY_IDENTITY)
            return

        if any(
            field in expected_fields for field in ("payment_amount", "cardholder_name", "card_number", "cvv", "expiry")
        ):
            extracted.setdefault("intent", UserIntent.MAKE_PAYMENT)
            extracted.setdefault("proposed_action", ProposedAction.PREPARE_PAYMENT)
            return

        if "account_id" in expected_fields:
            extracted.setdefault("intent", UserIntent.LOOKUP_ACCOUNT)
            extracted.setdefault("proposed_action", ProposedAction.LOOKUP_ACCOUNT)

    @staticmethod
    def _field_already_extracted(
        field: ExpectedField,
        extracted: dict[str, object],
    ) -> bool:
        return any(key in extracted for key in FIELD_TO_OUTPUT_KEYS[field])

    @staticmethod
    def _split_form_parts(text: str) -> list[str]:
        # Protect commas inside numeric tokens (for example, 1,250.75 and
        # 1,23,250.75) so they are not treated as form delimiters.
        protected = re.sub(
            r"\d(?:[\d,]*\d)?(?:\.\d+)?",
            lambda match: match.group(0).replace(",", "<THOUSANDS_COMMA>"),
            text,
        )
        return [
            part.strip().replace("<THOUSANDS_COMMA>", ",")
            for part in re.split(r"[,\n;]+", protected)
            if part.strip()
        ]

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
            sanitized: dict[str, object] = {}

            for key, value in data.items():
                candidate = {**sanitized, key: value}

                try:
                    ExtractedUserInput.model_validate(candidate)
                except ValidationError:
                    continue

                sanitized[key] = value

            return ExtractedUserInput.model_validate(sanitized)
