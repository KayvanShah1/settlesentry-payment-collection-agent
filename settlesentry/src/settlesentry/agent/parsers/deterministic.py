from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from re import Pattern

from pydantic import ValidationError

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.parsers.base import ExpectedField, ParserContext
from settlesentry.agent.state import ExtractedUserInput


class ParserPatterns:
    ACCOUNT_ID: Pattern[str] = re.compile(
        r"(?i)\b(?:account\s*id|account_id|account|customer\s*id)\s*(?:is|:|=)?\s*"
        r"(?P<account_id>[A-Za-z0-9][A-Za-z0-9_-]{0,63})\b"
    )
    BARE_ACCOUNT_ID: Pattern[str] = re.compile(
        r"^\s*(?P<account_id>(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9][A-Za-z0-9_-]{1,63})\s*$"
    )

    DOB: Pattern[str] = re.compile(
        r"(?i)\b(?:dob|date\s+of\s+birth)\s*(?:is|:|=)?\s*"
        r"(?P<dob>\d{4}-\d{2}-\d{2})\b"
    )
    BARE_DOB: Pattern[str] = re.compile(r"^\s*(?P<dob>\d{4}-\d{2}-\d{2})\s*$")

    AADHAAR_LAST4: Pattern[str] = re.compile(
        r"(?i)\b(?:aadhaar(?:_last4|\s+last\s+4)?|aadhaar)\s*(?:is|:|=)?\s*"
        r"(?P<aadhaar_last4>\d{4})(?!\d)\b"
    )
    BARE_4_DIGITS: Pattern[str] = re.compile(r"^\s*(?P<value>\d{4})\s*$")

    PINCODE: Pattern[str] = re.compile(
        r"(?i)\b(?:pincode|pin\s+code)\s*(?:is|:|=)?\s*"
        r"(?P<pincode>\d{6})(?!\d)\b"
    )
    BARE_PINCODE: Pattern[str] = re.compile(r"^\s*(?P<pincode>\d{6})\s*$")

    AMOUNT_VALUE = (
        r"(?:"
        r"\d{1,3}(?:,\d{2})*,\d{3}"
        r"|"
        r"\d{1,3}(?:,\d{3})+"
        r"|"
        r"\d+"
        r")"
        r"(?:\.\d{1,2})?"
    )

    AMOUNT: Pattern[str] = re.compile(
        r"(?i)\b(?:pay|payment|amount|collect|settle)\s*(?:is|of|:|=)?\s*"
        r"(?:rs\.?|inr|\u20B9)?\s*"
        rf"(?P<amount>{AMOUNT_VALUE})(?!\.\d)(?![\d,])\b"
    )
    BARE_AMOUNT: Pattern[str] = re.compile(
        r"^\s*(?:rs\.?|inr|\u20B9)?\s*"
        rf"(?P<amount>{AMOUNT_VALUE})(?!\.\d)(?![\d,])\s*$",
        re.IGNORECASE,
    )

    CARD_NUMBER: Pattern[str] = re.compile(
        r"(?i)\b(?:card(?:\s+number|_number)?|credit\s+card|debit\s+card)\s*(?:is|:|=)?\s*"
        r"(?P<card_number>\d[\d\s-]{11,22}\d)\b"
    )
    BARE_CARD_NUMBER: Pattern[str] = re.compile(r"^\s*(?P<card_number>\d[\d\s-]{11,22}\d)\s*$")

    CVV: Pattern[str] = re.compile(
        r"(?i)\b(?:cvv|cvc)\s*(?:is|:|=)?\s*"
        r"(?P<cvv>\d{3,4})\b"
    )
    BARE_CVV: Pattern[str] = re.compile(r"^\s*(?P<cvv>\d{3,4})\s*$")

    EXPIRY: Pattern[str] = re.compile(
        r"(?i)\b(?:expiry|exp|valid\s+till)\s*(?:is|:|=)?\s*"
        r"(?P<month>0?[1-9]|1[0-2])\s*(?:/|-|\s+)\s*(?P<year>\d{2}|\d{4})\b"
    )
    BARE_EXPIRY: Pattern[str] = re.compile(r"^\s*(?P<month>0?[1-9]|1[0-2])\s*(?:/|-|\s+)\s*(?P<year>\d{2}|\d{4})\s*$")

    CARDHOLDER: Pattern[str] = re.compile(
        r"(?i)\b(?:cardholder|card\s+holder|name\s+on\s+card)\s*(?:name)?\s*(?:is|:|=)?\s*"
        r"(?P<cardholder_name>[A-Za-z][A-Za-z\s.'-]{1,80}?)"
        r"(?=\s+(?:and\s+)?(?:card(?:\s+number|_number)?|credit\s+card|debit\s+card|cvv|cvc|expiry|exp|valid\s+till|amount|payment|pay)\b|$)"
    )

    FULL_NAME: Pattern[str] = re.compile(
        r"(?i)\b(?:my\s+name\s+is|i\s+am|i'm|this\s+is|name\s+is)\s+"
        r"(?P<full_name>[A-Za-z][A-Za-z\s.'-]{1,80})"
    )
    BARE_NAME: Pattern[str] = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z\s.'-]{1,80})\s*$")

    CONFIRM: Pattern[str] = re.compile(
        r"(?i)\b(?:yes|yeah|yep|confirm|confirmed|go\s+ahead|proceed|process\s+it|make\s+the\s+payment)\b"
    )

    CANCEL: Pattern[str] = re.compile(
        r"(?i)\b(?:cancel|stop|exit|quit|never\s+mind|nevermind|do\s+not\s+proceed|don't\s+proceed)\b"
    )

    NEGATIVE_CONFIRMATION: Pattern[str] = re.compile(r"(?i)\b(?:no|cancel|stop|do\s+not\s+proceed|don't\s+proceed)\b")

    ASK_AGENT_IDENTITY: Pattern[str] = re.compile(
        r"(?i)\b(?:who\s+are\s+you|what\s+are\s+you|are\s+you\s+(?:a\s+)?bot|your\s+name)\b"
    )
    ASK_AGENT_CAPABILITY: Pattern[str] = re.compile(
        r"(?i)\b(?:what\s+will\s+you\s+do|what\s+can\s+you\s+do|how\s+does\s+this\s+work|help\s+me\s+with)\b"
    )
    ASK_TO_REPEAT: Pattern[str] = re.compile(
        r"(?i)\b(?:repeat|restate|say\s+that\s+again|what\s+did\s+you\s+ask|what\s+do\s+you\s+need)\b"
    )
    ASK_CURRENT_STATUS: Pattern[str] = re.compile(
        r"(?i)\b(?:where\s+are\s+we|current\s+status|status|what\s+is\s+pending|what\s+is\s+left)\b"
    )
    CORRECTION: Pattern[str] = re.compile(
        r"(?i)\b(?:correct|correction|change|update|actually|mistake|wrong|typo|edit)\b"
    )


EXPECTED_FIELD_OUTPUT_KEYS: dict[ExpectedField, tuple[str, ...]] = {
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
    Slot-first deterministic fallback parser.

    It extracts explicit/labeled fields globally. Bare replies are parsed only
    when ParserContext.expected_fields tells us what the agent asked for.
    """

    def extract(
        self,
        user_input: str,
        context: ParserContext | None = None,
    ) -> ExtractedUserInput:
        text = user_input.strip()

        if not text:
            return ExtractedUserInput()

        extracted: dict[str, object] = {}

        self._extract_cancel(text, context, extracted)

        if extracted.get("intent") == UserIntent.CANCEL:
            return self._safe_model_validate(extracted)

        self._extract_side_intents(text, extracted)
        self._extract_correction_intent(text, extracted)
        self._extract_labeled_fields(text, extracted)
        self._extract_expected_slots(text, context, extracted)
        self._extract_confirmation(text, context, extracted)
        self._infer_intent_and_action(extracted)

        return self._safe_model_validate(extracted)

    def _extract_cancel(
        self,
        text: str,
        context: ParserContext | None,
        extracted: dict[str, object],
    ) -> None:
        if ParserPatterns.CANCEL.search(text):
            extracted["intent"] = UserIntent.CANCEL
            extracted["proposed_action"] = ProposedAction.CANCEL
            return

        confirmation_expected = context is not None and "confirmation" in context.expected_fields
        if confirmation_expected and ParserPatterns.NEGATIVE_CONFIRMATION.search(text):
            extracted["intent"] = UserIntent.CANCEL
            extracted["proposed_action"] = ProposedAction.CANCEL

    def _extract_side_intents(self, text: str, extracted: dict[str, object]) -> None:
        if ParserPatterns.ASK_AGENT_IDENTITY.search(text):
            extracted["intent"] = UserIntent.ASK_AGENT_IDENTITY
            return

        if ParserPatterns.ASK_AGENT_CAPABILITY.search(text):
            extracted["intent"] = UserIntent.ASK_AGENT_CAPABILITY
            return

        if ParserPatterns.ASK_TO_REPEAT.search(text):
            extracted["intent"] = UserIntent.ASK_TO_REPEAT
            return

        if ParserPatterns.ASK_CURRENT_STATUS.search(text):
            extracted["intent"] = UserIntent.ASK_CURRENT_STATUS

    def _extract_correction_intent(self, text: str, extracted: dict[str, object]) -> None:
        if ParserPatterns.CORRECTION.search(text):
            extracted["intent"] = UserIntent.CORRECT_PREVIOUS_DETAIL
            extracted["proposed_action"] = ProposedAction.HANDLE_CORRECTION

    def _extract_confirmation(
        self,
        text: str,
        context: ParserContext | None,
        extracted: dict[str, object],
    ) -> None:
        if extracted.get("intent") == UserIntent.CANCEL:
            return

        confirmation_expected = context is not None and "confirmation" in context.expected_fields

        if confirmation_expected and ParserPatterns.NEGATIVE_CONFIRMATION.search(text):
            extracted["intent"] = UserIntent.CANCEL
            extracted["proposed_action"] = ProposedAction.CANCEL
            return

        if confirmation_expected and ParserPatterns.CONFIRM.search(text):
            extracted["confirmation"] = True
            extracted["intent"] = UserIntent.CONFIRM_PAYMENT
            extracted["proposed_action"] = ProposedAction.CONFIRM_PAYMENT

    def _extract_labeled_fields(self, text: str, extracted: dict[str, object]) -> None:
        if match := ParserPatterns.ACCOUNT_ID.search(text):
            extracted["account_id"] = match.group("account_id").strip().upper()

        if match := ParserPatterns.FULL_NAME.search(text):
            extracted["full_name"] = self._clean_name(match.group("full_name"))

        if match := ParserPatterns.DOB.search(text):
            extracted["dob"] = match.group("dob")

        if match := ParserPatterns.AADHAAR_LAST4.search(text):
            extracted["aadhaar_last4"] = match.group("aadhaar_last4")

        if match := ParserPatterns.PINCODE.search(text):
            extracted["pincode"] = match.group("pincode")

        if match := ParserPatterns.AMOUNT.search(text):
            amount = self._parse_decimal(match.group("amount"))
            if amount is not None:
                extracted["payment_amount"] = amount

        if match := ParserPatterns.CARDHOLDER.search(text):
            extracted["cardholder_name"] = self._clean_name(match.group("cardholder_name"))

        if match := ParserPatterns.CARD_NUMBER.search(text):
            extracted["card_number"] = match.group("card_number").strip(" .,;")

        if match := ParserPatterns.CVV.search(text):
            extracted["cvv"] = match.group("cvv")

        if match := ParserPatterns.EXPIRY.search(text):
            extracted["expiry_month"] = int(match.group("month"))
            extracted["expiry_year"] = self._normalize_expiry_year(match.group("year"))

    def _extract_expected_slots(
        self,
        text: str,
        context: ParserContext | None,
        extracted: dict[str, object],
    ) -> None:
        if not context or not context.expected_fields:
            return

        for field in context.expected_fields:
            if self._field_already_extracted(field, extracted):
                continue

            self._extract_bare_field(text, field, extracted)

    def _extract_bare_field(
        self,
        text: str,
        field: ExpectedField,
        extracted: dict[str, object],
    ) -> None:
        if field == "account_id":
            self._extract_bare_account_id(text, extracted)
            return

        if field in {"full_name", "cardholder_name"}:
            if match := ParserPatterns.BARE_NAME.fullmatch(text):
                extracted[field] = self._clean_name(match.group("name"))
            return

        if field == "dob":
            if match := ParserPatterns.BARE_DOB.fullmatch(text):
                extracted["dob"] = match.group("dob")
            return

        if field == "aadhaar_last4":
            if match := ParserPatterns.BARE_4_DIGITS.fullmatch(text):
                extracted["aadhaar_last4"] = match.group("value")
            return

        if field == "pincode":
            if match := ParserPatterns.BARE_PINCODE.fullmatch(text):
                extracted["pincode"] = match.group("pincode")
            return

        if field == "payment_amount":
            if match := ParserPatterns.BARE_AMOUNT.fullmatch(text):
                amount = self._parse_decimal(match.group("amount"))
                if amount is not None:
                    extracted["payment_amount"] = amount
            return

        if field == "card_number":
            if match := ParserPatterns.BARE_CARD_NUMBER.fullmatch(text):
                extracted["card_number"] = match.group("card_number").strip(" .,;")
            return

        if field == "cvv":
            if match := ParserPatterns.BARE_CVV.fullmatch(text):
                extracted["cvv"] = match.group("cvv")
            return

        if field == "expiry":
            if match := ParserPatterns.BARE_EXPIRY.fullmatch(text):
                extracted["expiry_month"] = int(match.group("month"))
                extracted["expiry_year"] = self._normalize_expiry_year(match.group("year"))
            return

        if field == "confirmation" and ParserPatterns.CONFIRM.search(text):
            extracted["confirmation"] = True

    @staticmethod
    def _extract_bare_account_id(text: str, extracted: dict[str, object]) -> None:
        if match := ParserPatterns.BARE_ACCOUNT_ID.fullmatch(text.strip()):
            extracted["account_id"] = match.group("account_id").strip().upper()

    @staticmethod
    def _infer_intent_and_action(extracted: dict[str, object]) -> None:
        if extracted.get("intent") and extracted.get("intent") != UserIntent.CORRECT_PREVIOUS_DETAIL:
            return

        if extracted.get("intent") == UserIntent.CORRECT_PREVIOUS_DETAIL:
            extracted["proposed_action"] = ProposedAction.HANDLE_CORRECTION
            return

        if "account_id" in extracted:
            extracted["intent"] = UserIntent.LOOKUP_ACCOUNT
            extracted["proposed_action"] = ProposedAction.LOOKUP_ACCOUNT
            return

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
            return

        if any(key in extracted for key in ("full_name", "dob", "aadhaar_last4", "pincode")):
            extracted["intent"] = UserIntent.VERIFY_IDENTITY
            extracted["proposed_action"] = ProposedAction.VERIFY_IDENTITY

    @staticmethod
    def _field_already_extracted(
        field: ExpectedField,
        extracted: dict[str, object],
    ) -> bool:
        return any(key in extracted for key in EXPECTED_FIELD_OUTPUT_KEYS[field])

    @staticmethod
    def _clean_name(value: str) -> str:
        cleaned = value.strip(" .,'-\n\t")

        stop_words = (
            " and ",
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

        trailing_connectors = (" and", " with", " plus")
        lowered_cleaned = cleaned.lower()

        for connector in trailing_connectors:
            if lowered_cleaned.endswith(connector):
                cleaned = cleaned[: -len(connector)].strip(" .,'-\n\t")
                break

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
        return 2000 + year if year < 100 else year

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
