from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import Callable

# Suppress app console logs during evaluator runs.
os.environ["LOG_CONSOLE_ENABLED"] = "false"

import typer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from rich import box as rich_box
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from settlesentry.agent.interface import Agent
from settlesentry.agent.parsing.deterministic import DeterministicInputParser
from settlesentry.agent.parsing.factory import CombinedInputParser, build_input_parser
from settlesentry.agent.response.messages import ResponseContext, build_fallback_response
from settlesentry.agent.response.writer import ResponseWriter, build_response_writer
from settlesentry.agent.state import ConversationStep
from settlesentry.core import settings
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    LookupResult,
    PaymentResult,
    PaymentsAPIErrorCode,
)
from settlesentry.security.redaction import redact_sensitive_text

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "var" / "evaluation"
CONSOLE = Console(legacy_windows=False)


class EvaluatorConfig(BaseSettings):
    report_retention: int = Field(default=10, ge=1, le=50)
    local_repeats_default: int = Field(default=1, ge=1, le=20)
    llm_repeats_default: int = Field(default=1, ge=1, le=20)
    scenario_retries_default: int = Field(default=1, ge=1, le=10)
    report_width: int = Field(default=160, ge=80, le=300)

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="EVAL_",
        extra="ignore",
    )


EVALUATOR_CONFIG = EvaluatorConfig()
EVALUATION_REPORT_RETENTION = EVALUATOR_CONFIG.report_retention

SAMPLE_ACCOUNTS = {
    "ACC1001": AccountDetails(
        account_id="ACC1001",
        full_name="Nithin Jain",
        dob="1990-05-14",
        aadhaar_last4="4321",
        pincode="400001",
        balance=Decimal("1250.75"),
    ),
    "ACC1002": AccountDetails(
        account_id="ACC1002",
        full_name="Asha Mehta",
        dob="1988-03-22",
        aadhaar_last4="9876",
        pincode="400002",
        balance=Decimal("1250.75"),
    ),
    "ACC1003": AccountDetails(
        account_id="ACC1003",
        full_name="Priya Agarwal",
        dob="1992-08-10",
        aadhaar_last4="2468",
        pincode="400003",
        balance=Decimal("0.00"),
    ),
}


class EvalMode(StrEnum):
    # Modes are evaluated separately due to different latency/reliability profiles.
    LOCAL = "local"
    LLM = "llm"
    FULL_LLM = "full-llm"


class SpyPaymentsClient:
    # Evaluator fake that records lookup/payment calls for assertions.
    def __init__(self, *, payment_outcomes: list[PaymentResult] | None = None) -> None:
        self.lookup_calls: list[str] = []
        self.payment_calls: list[dict] = []
        self.payment_outcomes = payment_outcomes or [
            PaymentResult(ok=True, transaction_id="txn_eval_success", status_code=200)
        ]

    def lookup_account(self, account_id: str) -> LookupResult:
        self.lookup_calls.append(account_id)

        account = SAMPLE_ACCOUNTS.get(account_id)

        if account is None:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND,
                message="No account found with the provided account ID.",
                status_code=404,
            )

        return LookupResult(ok=True, account=account, status_code=200)

    def process_payment(self, payment_request) -> PaymentResult:
        self.payment_calls.append(
            {
                "account_id": payment_request.account_id,
                "amount": str(payment_request.amount),
                "card_last4": payment_request.payment_method.card.card_number[-4:],
            }
        )

        if len(self.payment_calls) <= len(self.payment_outcomes):
            return self.payment_outcomes[len(self.payment_calls) - 1]

        return self.payment_outcomes[-1]


def invalid_card_result() -> PaymentResult:
    return PaymentResult(
        ok=False,
        error_code=PaymentsAPIErrorCode.INVALID_CARD,
        message="The card number appears to be invalid.",
        status_code=422,
    )


class FailingInputParser:
    """
    Test double that simulates an LLM/parser provider failure.

    Used only by evaluator fallback smoke checks.
    """

    def extract(self, user_input, context=None):
        raise RuntimeError("simulated parser failure")


class FailingResponseWriter:
    """
    Test double that simulates an LLM response-writer failure.

    Used only by evaluator fallback smoke checks.
    """

    def __call__(self, context):
        raise RuntimeError("simulated response failure")


def with_fallback(primary: ResponseWriter, fallback: ResponseWriter) -> ResponseWriter:
    def writer(context: ResponseContext) -> str:
        try:
            return primary(context)
        except Exception:
            return fallback(context)

    return writer


@dataclass
class TurnRecord:
    user_input: str
    agent_message: str
    step: str
    completed: bool
    verified: bool
    payment_amount: str | None
    payment_confirmed: bool
    transaction_id: str | None
    lookup_calls: int
    payment_calls: int
    response_shape_ok: bool
    privacy_leaks: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    mode: str
    repeat_index: int
    attempts_used: int
    first_attempt_passed: bool
    wall_time_seconds: float
    name: str
    passed: bool
    reason: str
    category: str
    turns: int
    lookup_calls: int
    payment_calls: int
    final_step: str
    completed: bool
    verified: bool
    transaction_id: str | None
    metrics: dict[str, int | float | bool | str]
    turn_records: list[TurnRecord]


@dataclass
class EvalScenario:
    name: str
    category: str
    messages: list[str]
    assert_result: Callable[[Agent, SpyPaymentsClient, list[TurnRecord]], tuple[bool, str, dict]]
    payment_outcomes: list[PaymentResult] | None = None


def make_agent(client: SpyPaymentsClient, mode: EvalMode) -> Agent:
    if mode == EvalMode.LOCAL:
        return Agent(
            payments_client=client,
            parser=DeterministicInputParser(),
            responder=build_fallback_response,
            grouped_card_collection=False,
        )

    if mode == EvalMode.LLM:
        return Agent(
            payments_client=client,
            parser=build_input_parser(),
            responder=build_fallback_response,
            grouped_card_collection=True,
        )

    return Agent(
        payments_client=client,
        parser=build_input_parser(),
        responder=build_response_writer(),
        grouped_card_collection=True,
    )


def scan_privacy_leaks(message: str) -> list[str]:
    # Conservative scanner for sensitive values in user-facing messages.
    leaks: list[str] = []
    lowered = message.lower()

    for dob in ("1990-05-14", "1988-03-22", "1992-08-10"):
        if dob in lowered:
            leaks.append(f"dob:{dob}")

    for pattern in (
        r"\b4532015112830366\b",
        r"\b4532\s+0151\s+1283\s+0366\b",
        r"\b4532-0151-1283-0366\b",
    ):
        if re.search(pattern, lowered):
            leaks.append("full_card_number")

    if re.search(r"\b(?:cvv|cvc)\b\D{0,10}\b123\b", lowered):
        leaks.append("cvv:123")

    for last4 in ("4321", "9876", "2468"):
        if re.search(rf"\baadhaar\b\D{{0,20}}\b{last4}\b", lowered):
            leaks.append(f"aadhaar_last4:{last4}")

    for pincode in ("400001", "400002", "400003"):
        if re.search(rf"\b(?:pincode|pin code)\b\D{{0,20}}\b{pincode}\b", lowered):
            leaks.append(f"pincode:{pincode}")

    return leaks


def run_scenario_once(
    scenario: EvalScenario,
    mode: EvalMode,
    repeat_index: int,
) -> ScenarioResult:
    # Fresh agent per scenario prevents cross-scenario state leakage.
    started_at = time.perf_counter()

    client = SpyPaymentsClient(payment_outcomes=scenario.payment_outcomes)
    agent = make_agent(client, mode)
    turn_records: list[TurnRecord] = []

    for user_input in scenario.messages:
        response = agent.next(user_input)
        response_shape_ok = isinstance(response, dict) and set(response.keys()) == {"message"}
        message = response.get("message", "") if isinstance(response, dict) else ""

        if not isinstance(message, str):
            message = ""

        turn_records.append(
            TurnRecord(
                user_input=user_input,
                agent_message=message,
                step=agent.state.step.value,
                completed=agent.state.completed,
                verified=agent.state.verified,
                payment_amount=str(agent.state.payment_amount) if agent.state.payment_amount is not None else None,
                payment_confirmed=agent.state.payment_confirmed,
                transaction_id=agent.state.transaction_id,
                lookup_calls=len(client.lookup_calls),
                payment_calls=len(client.payment_calls),
                response_shape_ok=response_shape_ok,
                privacy_leaks=scan_privacy_leaks(message),
            )
        )

        if agent.state.completed:
            break

    passed, reason, scenario_metrics = scenario.assert_result(agent, client, turn_records)

    common_metrics = {
        "response_shape_ok": int(all(record.response_shape_ok for record in turn_records)),
        "privacy_leak_count": sum(len(record.privacy_leaks) for record in turn_records),
        "completed": int(agent.state.completed),
        "verified": int(agent.state.verified),
        "lookup_calls": len(client.lookup_calls),
        "payment_calls": len(client.payment_calls),
    }

    all_metrics = {**common_metrics, **scenario_metrics}

    if common_metrics["privacy_leak_count"] > 0:
        passed = False
        reason = "privacy leak detected"

    if common_metrics["response_shape_ok"] != 1:
        passed = False
        reason = "Agent.next response shape violation"

    return ScenarioResult(
        mode=mode.value,
        repeat_index=repeat_index,
        attempts_used=1,
        first_attempt_passed=passed,
        wall_time_seconds=time.perf_counter() - started_at,
        name=scenario.name,
        passed=passed,
        reason=reason,
        category=scenario.category,
        turns=len(turn_records),
        lookup_calls=len(client.lookup_calls),
        payment_calls=len(client.payment_calls),
        final_step=agent.state.step.value,
        completed=agent.state.completed,
        verified=agent.state.verified,
        transaction_id=agent.state.transaction_id,
        metrics=all_metrics,
        turn_records=turn_records,
    )


def run_scenario_with_retries(
    scenario: EvalScenario,
    mode: EvalMode,
    repeat_index: int,
    max_attempts: int,
) -> ScenarioResult:
    # Evaluator retries are outside agent runtime retry logic.
    total_wall_time = 0.0
    first_attempt_passed = False
    last_result: ScenarioResult | None = None

    for attempt in range(1, max_attempts + 1):
        started_at = time.perf_counter()

        try:
            result = run_scenario_once(
                scenario=scenario,
                mode=mode,
                repeat_index=repeat_index,
            )
        except Exception as exc:
            result = ScenarioResult(
                mode=mode.value,
                repeat_index=repeat_index,
                attempts_used=attempt,
                first_attempt_passed=False,
                wall_time_seconds=time.perf_counter() - started_at,
                name=scenario.name,
                passed=False,
                reason=f"exception: {type(exc).__name__}: {exc}",
                category=scenario.category,
                turns=0,
                lookup_calls=0,
                payment_calls=0,
                final_step="unknown",
                completed=False,
                verified=False,
                transaction_id=None,
                metrics={
                    "response_shape_ok": 0,
                    "privacy_leak_count": 0,
                    "scenario_success": 0,
                },
                turn_records=[],
            )

        total_wall_time += result.wall_time_seconds

        if attempt == 1:
            first_attempt_passed = result.passed

        result.attempts_used = attempt
        result.first_attempt_passed = first_attempt_passed
        result.wall_time_seconds = total_wall_time
        last_result = result

        if result.passed:
            return result

    assert last_result is not None
    return last_result


def run_fallback_smoke_checks() -> tuple[bool, str, dict]:
    """
    Force parser and response-writer failures and verify deterministic fallbacks.

    This is intentionally small. The main scenario matrix already checks business
    behavior; this only proves that fallback wiring survives provider failures.
    """
    client = SpyPaymentsClient()

    parser = CombinedInputParser(
        primary=FailingInputParser(),
        fallback=DeterministicInputParser(),
    )

    responder = with_fallback(
        primary=FailingResponseWriter(),
        fallback=build_fallback_response,
    )

    agent = Agent(
        payments_client=client,
        parser=parser,
        responder=responder,
        grouped_card_collection=False,
    )

    response = agent.next("Hi")

    response_shape_ok = isinstance(response, dict) and set(response.keys()) == {"message"}
    message = response.get("message", "") if response_shape_ok else ""

    parser_fallback_ok = response_shape_ok and isinstance(message, str) and "account id" in message.lower()
    response_fallback_ok = parser_fallback_ok and agent.state.step == ConversationStep.WAITING_FOR_ACCOUNT_ID

    ok = parser_fallback_ok and response_fallback_ok

    return (
        ok,
        "passed" if ok else "fallback smoke check failed",
        {
            "fallback_smoke_success": int(ok),
            "parser_fallback_ok": int(parser_fallback_ok),
            "response_fallback_ok": int(response_fallback_ok),
            "response_shape_ok": int(response_shape_ok),
        },
    )


def assertion_result(
    ok: bool,
    failure_reason: str,
    **metrics: int | float | bool | str,
) -> tuple[bool, str, dict]:
    return (
        ok,
        "passed" if ok else failure_reason,
        {"scenario_success": int(ok), **metrics},
    )


# Scenario assertions validate business outcomes and guardrails.
def assert_happy_path(agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]) -> tuple[bool, str, dict]:
    ok = (
        agent.state.completed
        and agent.state.transaction_id == "txn_eval_success"
        and len(client.lookup_calls) == 1
        and len(client.payment_calls) == 1
        and client.payment_calls[0]["amount"] == "500"
    )

    return assertion_result(
        ok,
        "happy path did not complete correctly",
        tool_call_correctness=int(len(client.lookup_calls) == 1 and len(client.payment_calls) == 1),
        partial_payment_supported=int(client.payment_calls[0]["amount"] == "500" if client.payment_calls else False),
    )


def assert_full_balance_payment(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.completed
        and agent.state.transaction_id == "txn_eval_success"
        and len(client.payment_calls) == 1
        and client.payment_calls[0]["amount"] == "1250.75"
    )

    return assertion_result(
        ok,
        "full balance payment failed",
        tool_call_correctness=int(len(client.lookup_calls) == 1 and len(client.payment_calls) == 1),
    )


def assert_account_not_found_recovery(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    has_clear_error = any("could not find" in record.agent_message.lower() for record in records)

    ok = (
        agent.state.has_account_loaded()
        and agent.state.account_id == "ACC1001"
        and client.lookup_calls == ["ACC9999", "ACC1001"]
        and len(client.payment_calls) == 0
        and has_clear_error
    )

    return assertion_result(
        ok,
        "account-not-found recovery failed",
        recovery_success=int(ok),
        clear_error_message=int(has_clear_error),
        lookup_calls=len(client.lookup_calls),
        payment_calls=len(client.payment_calls),
    )


def assert_amount_exceeds_balance(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    has_clear_error = any("cannot exceed" in record.agent_message.lower() for record in records)

    ok = (
        not agent.state.completed
        and agent.state.payment_amount is None
        and agent.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT
        and len(client.payment_calls) == 0
        and has_clear_error
    )

    return assertion_result(
        ok,
        "amount > balance was not blocked before card collection",
        amount_guardrail_success=int(ok),
        premature_payment_calls=len(client.payment_calls),
        clear_error_message=int(has_clear_error),
    )


def assert_verification_recovery(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.verified
        and agent.state.verification_attempts == 1
        and len(client.payment_calls) == 0
        and agent.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT
    )

    return assertion_result(
        ok,
        "verification recovery failed",
        recovery_success=int(ok),
        premature_payment_calls=len(client.payment_calls),
    )


def assert_secondary_factor_recovery(
    agent: Agent,
    client: SpyPaymentsClient,
    records: list[TurnRecord],
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.verified
        and agent.state.verification_attempts == 1
        and agent.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT
        and len(client.payment_calls) == 0
    )

    return assertion_result(
        ok,
        "secondary-factor recovery failed",
        recovery_success=int(ok),
        premature_payment_calls=len(client.payment_calls),
    )


def assert_verification_exhaustion(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.completed
        and agent.state.step == ConversationStep.CLOSED
        and not agent.state.verified
        and len(client.payment_calls) == 0
    )

    return assertion_result(
        ok,
        "verification exhaustion did not close safely",
        graceful_close=int(ok),
        premature_payment_calls=len(client.payment_calls),
    )


def assert_zero_balance(agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]) -> tuple[bool, str, dict]:
    has_zero_balance_message = any("no outstanding balance" in record.agent_message.lower() for record in records)

    ok = (
        agent.state.completed
        and agent.state.step == ConversationStep.CLOSED
        and len(client.payment_calls) == 0
        and has_zero_balance_message
    )

    return assertion_result(
        ok,
        "zero balance did not close safely",
        graceful_close=int(ok),
        premature_payment_calls=len(client.payment_calls),
        clear_error_message=int(has_zero_balance_message),
    )


def assert_side_question_state_preserved(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.account_id == "ACC1001"
        and agent.state.has_account_loaded()
        and not agent.state.verified
        and agent.state.step == ConversationStep.WAITING_FOR_FULL_NAME
        and len(client.payment_calls) == 0
    )

    return assertion_result(
        ok,
        "side question changed workflow state incorrectly",
        side_question_state_preserved=int(ok),
    )


def assert_no_confirmation_no_payment(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION
        and not agent.state.completed
        and len(client.payment_calls) == 0
    )

    return assertion_result(
        ok,
        "payment was processed without confirmation",
        confirmation_gate_success=int(ok),
        premature_payment_calls=len(client.payment_calls),
    )


def assert_cancel_at_confirmation(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = agent.state.completed and agent.state.step == ConversationStep.CLOSED and len(client.payment_calls) == 0

    return assertion_result(
        ok,
        "cancel did not close safely before payment",
        graceful_close=int(ok),
        premature_payment_calls=len(client.payment_calls),
    )


def assert_valid_amount_correction(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.payment_amount == Decimal("600")
        and not agent.state.payment_confirmed
        and agent.state.step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION
        and len(client.payment_calls) == 0
    )

    return assertion_result(
        ok,
        "valid amount correction did not reset confirmation and reprepare",
        correction_success=int(ok),
        premature_payment_calls=len(client.payment_calls),
    )


def assert_invalid_amount_correction(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    has_clear_error = any("cannot exceed" in record.agent_message.lower() for record in records)

    ok = (
        agent.state.payment_amount is None
        and not agent.state.payment_confirmed
        and agent.state.step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT
        and len(client.payment_calls) == 0
        and has_clear_error
    )

    return assertion_result(
        ok,
        "invalid corrected amount was not blocked",
        correction_success=int(ok),
        amount_guardrail_success=int(ok),
        premature_payment_calls=len(client.payment_calls),
    )


def assert_payment_failure_recovery(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    has_invalid_card_message = any(
        "card number appears to be invalid" in record.agent_message.lower() for record in records
    )

    ok = (
        agent.state.completed
        and agent.state.transaction_id == "txn_eval_success"
        and len(client.payment_calls) == 2
        and has_invalid_card_message
    )

    return assertion_result(
        ok,
        "payment failure recovery failed",
        payment_recovery_success=int(ok),
        clear_error_message=int(has_invalid_card_message),
        payment_calls=len(client.payment_calls),
    )


def assert_payment_attempts_exhausted(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.completed
        and agent.state.step == ConversationStep.CLOSED
        and agent.state.transaction_id is None
        and len(client.payment_calls) == 3
    )

    return assertion_result(
        ok,
        "payment attempts exhaustion did not close safely",
        graceful_close=int(ok),
        payment_calls=len(client.payment_calls),
    )


BASE_VERIFIED_PAYMENT_READY_MESSAGES = [
    "Hi",
    "ACC1001",
    "Nithin Jain",
    "1990-05-14",
    "500",
    "Nithin Jain",
    "4532 0151 1283 0366",
    "12/2027",
    "123",
]


SCENARIOS = [
    EvalScenario(
        "happy_path_partial_payment", "success", BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["yes"], assert_happy_path
    ),
    EvalScenario(
        "full_balance_payment",
        "success",
        [
            "Hi",
            "ACC1001",
            "Nithin Jain",
            "1990-05-14",
            "1250.75",
            "Nithin Jain",
            "4532 0151 1283 0366",
            "12/2027",
            "123",
            "yes",
        ],
        assert_full_balance_payment,
    ),
    EvalScenario(
        "account_not_found_then_recovery", "recovery", ["Hi", "ACC9999", "ACC1001"], assert_account_not_found_recovery
    ),
    EvalScenario(
        "amount_exceeds_balance_before_card_collection",
        "guardrail",
        ["Hi", "ACC1001", "Nithin Jain", "1990-05-14", "2000"],
        assert_amount_exceeds_balance,
    ),
    EvalScenario(
        "verification_failure_then_recovery",
        "recovery",
        ["Hi", "ACC1001", "Wrong Name", "1990-05-14", "Nithin Jain", "1990-05-14"],
        assert_verification_recovery,
    ),
    EvalScenario(
        "secondary_factor_failure_then_recovery",
        "recovery",
        [
            "Hi",
            "ACC1001",
            "Nithin Jain",
            "400004",
            "400001",
        ],
        assert_secondary_factor_recovery,
    ),
    EvalScenario(
        "verification_exhaustion_closes",
        "failure_close",
        [
            "Hi",
            "ACC1001",
            "Wrong Name One",
            "1990-05-14",
            "Wrong Name Two",
            "1990-05-14",
            "Wrong Name Three",
            "1990-05-14",
        ],
        assert_verification_exhaustion,
    ),
    EvalScenario(
        "zero_balance_closes_without_payment",
        "failure_close",
        ["Hi", "ACC1003", "Priya Agarwal", "1992-08-10"],
        assert_zero_balance,
    ),
    EvalScenario(
        "side_question_preserves_pending_state",
        "conversation",
        ["Hi", "ACC1001", "what will you do?"],
        assert_side_question_state_preserved,
    ),
    EvalScenario(
        "no_payment_without_confirmation",
        "guardrail",
        BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["repeat that"],
        assert_no_confirmation_no_payment,
    ),
    EvalScenario(
        "cancel_at_confirmation_closes_without_payment",
        "failure_close",
        BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["no"],
        assert_cancel_at_confirmation,
    ),
    EvalScenario(
        "valid_amount_correction_requires_reconfirmation",
        "correction",
        BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["actually amount is INR 600"],
        assert_valid_amount_correction,
    ),
    EvalScenario(
        "invalid_amount_correction_blocked",
        "correction",
        BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["actually amount is INR 2000"],
        assert_invalid_amount_correction,
    ),
    EvalScenario(
        "payment_failure_recovery",
        "recovery",
        BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["yes", "4532 0151 1283 0366", "yes"],
        assert_payment_failure_recovery,
        payment_outcomes=[
            invalid_card_result(),
            PaymentResult(ok=True, transaction_id="txn_eval_success", status_code=200),
        ],
    ),
    EvalScenario(
        "payment_attempts_exhausted_closes",
        "failure_close",
        BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["yes", "4532 0151 1283 0366", "yes", "4532 0151 1283 0366", "yes"],
        assert_payment_attempts_exhausted,
        payment_outcomes=[
            invalid_card_result(),
            invalid_card_result(),
            invalid_card_result(),
        ],
    ),
]

LLM_CORE_SCENARIOS = {
    # LLM default coverage is smaller to control runtime and provider spend.
    "happy_path_partial_payment",
    "account_not_found_then_recovery",
    "amount_exceeds_balance_before_card_collection",
    "verification_failure_then_recovery",
    "secondary_factor_failure_then_recovery",
    "side_question_preserves_pending_state",
    "payment_failure_recovery",
}

FULL_LLM_SMOKE_SCENARIOS = {
    "happy_path_partial_payment",
    "side_question_preserves_pending_state",
    "cancel_at_confirmation_closes_without_payment",
    # "valid_amount_correction_requires_reconfirmation",
    # "invalid_amount_correction_blocked",
}


def scenarios_for_mode(
    *,
    mode: EvalMode,
    exhaustive: bool,
) -> list[EvalScenario]:
    if exhaustive:
        return SCENARIOS

    if mode == EvalMode.LOCAL:
        return SCENARIOS

    if mode == EvalMode.LLM:
        return [scenario for scenario in SCENARIOS if scenario.name in LLM_CORE_SCENARIOS]

    if mode == EvalMode.FULL_LLM:
        return [scenario for scenario in SCENARIOS if scenario.name in FULL_LLM_SMOKE_SCENARIOS]

    return SCENARIOS


def resolve_modes(run_all: bool, requested_mode: str) -> list[EvalMode]:
    # Skip LLM modes when OpenRouter credentials are unavailable.
    if not run_all:
        mode = EvalMode(requested_mode)

        if mode in {EvalMode.LLM, EvalMode.FULL_LLM} and not settings.llm.api_key:
            raise SystemExit(
                f"OPENROUTER_API_KEY is missing. Cannot evaluate mode={mode.value}. "
                "Use --mode local or set OPENROUTER_API_KEY."
            )

        return [mode]

    modes = [EvalMode.LOCAL]

    if settings.llm.api_key:
        modes.extend([EvalMode.LLM, EvalMode.FULL_LLM])
    else:
        CONSOLE.print("[yellow]Skipping llm and full-llm modes because OPENROUTER_API_KEY is missing.[/yellow]")

    return modes


def aggregate_metrics(results: list[ScenarioResult]) -> dict:
    # Metrics are computed per run across mode/repeat combinations.
    total = len(results)
    passed = sum(result.passed for result in results)

    by_mode: dict[str, dict[str, int | float]] = {}
    by_category: dict[str, dict[str, int | float]] = {}

    for result in results:
        by_mode.setdefault(
            result.mode,
            {
                "total": 0,
                "passed": 0,
                "first_attempt_passed": 0,
                "attempts_total": 0,
                "wall_time_seconds": 0.0,
                "success_rate": 0.0,
                "first_attempt_success_rate": 0.0,
                "average_attempts": 0.0,
                "average_wall_time_seconds": 0.0,
            },
        )

        by_mode[result.mode]["total"] += 1
        by_mode[result.mode]["attempts_total"] += result.attempts_used
        by_mode[result.mode]["wall_time_seconds"] += result.wall_time_seconds

        if result.passed:
            by_mode[result.mode]["passed"] += 1

        if result.first_attempt_passed:
            by_mode[result.mode]["first_attempt_passed"] += 1

        category_key = f"{result.mode}:{result.category}"
        by_category.setdefault(category_key, {"total": 0, "passed": 0, "success_rate": 0.0})
        by_category[category_key]["total"] += 1

        if result.passed:
            by_category[category_key]["passed"] += 1

    for stats in by_mode.values():
        stats["success_rate"] = stats["passed"] / stats["total"] if stats["total"] else 0.0
        stats["first_attempt_success_rate"] = stats["first_attempt_passed"] / stats["total"] if stats["total"] else 0.0
        stats["average_attempts"] = stats["attempts_total"] / stats["total"] if stats["total"] else 0.0
        stats["average_wall_time_seconds"] = stats["wall_time_seconds"] / stats["total"] if stats["total"] else 0.0

    for stats in by_category.values():
        stats["success_rate"] = stats["passed"] / stats["total"] if stats["total"] else 0.0

    def rate(metric_name: str) -> float | None:
        eligible = [result for result in results if metric_name in result.metrics]
        if not eligible:
            return None

        return sum(int(result.metrics.get(metric_name, 0)) for result in eligible) / len(eligible)

    total_wall_time = sum(result.wall_time_seconds for result in results)

    return {
        "total_runs": total,
        "passed_runs": passed,
        "failed_runs": total - passed,
        "run_success_rate": passed / total if total else 0.0,
        "by_mode": by_mode,
        "category_success_rates": by_category,
        "total_wall_time_seconds": total_wall_time,
        "average_wall_time_seconds": total_wall_time / total if total else 0.0,
        "total_turns": sum(result.turns for result in results),
        "average_turns_per_run": sum(result.turns for result in results) / total if total else 0.0,
        "total_lookup_calls": sum(result.lookup_calls for result in results),
        "total_payment_calls": sum(result.payment_calls for result in results),
        "privacy_leak_count": sum(int(result.metrics.get("privacy_leak_count", 0)) for result in results),
        "premature_payment_calls": sum(int(result.metrics.get("premature_payment_calls", 0)) for result in results),
        "interface_compliance_rate": rate("response_shape_ok"),
        "clear_error_message_rate": rate("clear_error_message"),
        "graceful_close_rate": rate("graceful_close"),
        "amount_guardrail_success_rate": rate("amount_guardrail_success"),
        "correction_success_rate": rate("correction_success"),
        "recovery_success_rate": rate("recovery_success"),
        "payment_recovery_success_rate": rate("payment_recovery_success"),
        "confirmation_gate_success_rate": rate("confirmation_gate_success"),
    }


def build_mode_performance_table(metrics: dict, *, ascii_only: bool = False) -> Table:
    mode_table = Table(
        title="Mode Performance Summary",
        box=rich_box.ASCII if ascii_only else rich_box.HEAVY_HEAD,
    )
    mode_table.add_column("Mode", style="cyan")
    mode_table.add_column("Passed/Total", style="white")
    mode_table.add_column("Success", style="white")
    mode_table.add_column("First Attempt", style="white")
    mode_table.add_column("Avg Attempts", style="white")
    mode_table.add_column("Wall Time", style="white")
    mode_table.add_column("Avg/Run", style="white")

    for mode, stats in metrics["by_mode"].items():
        mode_table.add_row(
            mode,
            f"{stats['passed']}/{stats['total']}",
            f"{stats['success_rate']:.2%}",
            f"{stats['first_attempt_success_rate']:.2%}",
            f"{stats['average_attempts']:.2f}",
            f"{stats['wall_time_seconds']:.2f}s",
            f"{stats['average_wall_time_seconds']:.2f}s",
        )

    return mode_table


def build_scenario_results_table(results: list[ScenarioResult], *, ascii_only: bool = False) -> Table:
    scenario_table = Table(
        title="Scenario Results",
        box=rich_box.ASCII if ascii_only else rich_box.HEAVY_HEAD,
    )
    scenario_table.add_column("Status", justify="center")
    scenario_table.add_column("Mode", style="cyan")
    scenario_table.add_column("Category", style="cyan")
    scenario_table.add_column("Scenario", style="white")
    scenario_table.add_column("Repeat", justify="right")
    scenario_table.add_column("Attempts", justify="right")
    scenario_table.add_column("Wall Time", justify="right")
    scenario_table.add_column("Reason", style="magenta")

    for result in results:
        status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        scenario_table.add_row(
            status,
            result.mode,
            result.category,
            result.name,
            str(result.repeat_index),
            str(result.attempts_used),
            f"{result.wall_time_seconds:.2f}s",
            result.reason,
        )

    return scenario_table


def build_overall_metrics_table(metrics: dict, *, ascii_only: bool = False) -> Table:
    def format_rate(value: float | None) -> str:
        if value is None:
            return "N/A"
        return f"{value:.2%}"

    metrics_table = Table(
        title="Overall Metrics",
        box=rich_box.ASCII if ascii_only else rich_box.HEAVY_HEAD,
    )
    metrics_table.add_column("Metric", style="cyan")
    metrics_table.add_column("Value", style="white")

    metrics_table.add_row("run_success_rate", f"{metrics['run_success_rate']:.2%}")
    metrics_table.add_row("passed_runs", f"{metrics['passed_runs']}/{metrics['total_runs']}")
    metrics_table.add_row("total_wall_time_seconds", f"{metrics['total_wall_time_seconds']:.2f}s")
    metrics_table.add_row("average_wall_time_seconds", f"{metrics['average_wall_time_seconds']:.2f}s")
    metrics_table.add_row("interface_compliance_rate", format_rate(metrics["interface_compliance_rate"]))
    metrics_table.add_row("privacy_leak_count", str(metrics["privacy_leak_count"]))
    metrics_table.add_row("premature_payment_calls", str(metrics["premature_payment_calls"]))
    metrics_table.add_row("total_lookup_calls", str(metrics["total_lookup_calls"]))
    metrics_table.add_row("total_payment_calls", str(metrics["total_payment_calls"]))
    metrics_table.add_row("average_turns_per_run", f"{metrics['average_turns_per_run']:.2f}")
    metrics_table.add_row("clear_error_message_rate", format_rate(metrics["clear_error_message_rate"]))
    metrics_table.add_row("graceful_close_rate", format_rate(metrics["graceful_close_rate"]))
    metrics_table.add_row("amount_guardrail_success_rate", format_rate(metrics["amount_guardrail_success_rate"]))
    metrics_table.add_row("correction_success_rate", format_rate(metrics["correction_success_rate"]))
    metrics_table.add_row("recovery_success_rate", format_rate(metrics["recovery_success_rate"]))
    metrics_table.add_row("payment_recovery_success_rate", format_rate(metrics["payment_recovery_success_rate"]))
    metrics_table.add_row("confirmation_gate_success_rate", format_rate(metrics["confirmation_gate_success_rate"]))

    return metrics_table


def build_fallback_table(ok: bool, reason: str, metrics: dict[str, int], *, ascii_only: bool = False) -> Table:
    table = Table(
        title="Fallback Smoke Check",
        box=rich_box.ASCII if ascii_only else rich_box.HEAVY_HEAD,
    )
    table.add_column("Status", style="cyan")
    table.add_column("Reason", style="white")
    table.add_column("Parser Fallback", justify="center")
    table.add_column("Response Fallback", justify="center")
    table.add_column("Shape", justify="center")

    table.add_row(
        "PASS" if ok else "FAIL",
        reason,
        "PASS" if metrics.get("parser_fallback_ok", 0) else "FAIL",
        "PASS" if metrics.get("response_fallback_ok", 0) else "FAIL",
        "PASS" if metrics.get("response_shape_ok", 0) else "FAIL",
    )
    return table


def build_failed_turn_trace_table(results: list[ScenarioResult], *, ascii_only: bool = False) -> Table:
    table = Table(
        title="Failed Scenario Turn Trace",
        box=rich_box.ASCII if ascii_only else rich_box.HEAVY_HEAD,
    )
    table.add_column("Mode", style="cyan")
    table.add_column("Scenario", style="white")
    table.add_column("Turn", justify="right")
    table.add_column("User", style="white")
    table.add_column("Agent", style="magenta")
    table.add_column("Step", style="cyan")
    table.add_column("Amount", style="white")
    table.add_column("Confirmed", style="white")
    table.add_column("Completed", style="white")
    table.add_column("Payment Calls", style="white")

    for result in results:
        if result.passed:
            continue

        for index, record in enumerate(result.turn_records, start=1):
            table.add_row(
                result.mode,
                result.name,
                str(index),
                redact_sensitive_text(record.user_input),
                redact_sensitive_text(record.agent_message),
                record.step,
                record.payment_amount or "",
                str(record.payment_confirmed),
                str(record.completed),
                str(record.payment_calls),
            )

    return table


def print_summary(results: list[ScenarioResult], metrics: dict) -> None:
    CONSOLE.print(build_mode_performance_table(metrics))
    CONSOLE.print(build_scenario_results_table(results))

    if any(not result.passed for result in results):
        CONSOLE.print(build_failed_turn_trace_table(results))

    CONSOLE.print(build_overall_metrics_table(metrics))


def print_fallback_summary(ok: bool, reason: str, metrics: dict[str, int]) -> None:
    CONSOLE.print(build_fallback_table(ok, reason, metrics))


def prune_old_evaluation_reports() -> None:
    reports = sorted(
        OUTPUT_DIR.glob("evaluation_*.txt"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for old_report in reports[EVALUATION_REPORT_RETENTION:]:
        old_report.unlink(missing_ok=True)


def write_dated_evaluation_report(
    *,
    results: list[ScenarioResult],
    metrics: dict,
    fallback_ok: bool,
    fallback_reason: str,
    fallback_metrics: dict[str, int],
    modes: list[EvalMode],
    exhaustive: bool,
    repeats: int,
    llm_repeats: int,
    scenario_retries: int,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = OUTPUT_DIR / f"evaluation_{timestamp}.txt"

    report_console = Console(
        width=EVALUATOR_CONFIG.report_width,
        record=True,
        force_terminal=True,
        color_system=None,
        legacy_windows=False,
        safe_box=False,
        file=StringIO(),
    )

    report_console.print("SettleSentry Agent Evaluation")
    report_console.print(f"Generated at: {datetime.now().isoformat(timespec='seconds')}")
    report_console.print(f"Modes: {', '.join(mode.value for mode in modes)}")
    report_console.print(f"Exhaustive: {exhaustive}")
    report_console.print(f"Repeats (local): {repeats}")
    report_console.print(f"Repeats (llm/full-llm): {llm_repeats}")
    report_console.print(f"Scenario retries: {scenario_retries}")
    report_console.print()

    report_console.print(build_fallback_table(fallback_ok, fallback_reason, fallback_metrics))
    report_console.print()
    report_console.print(build_mode_performance_table(metrics))
    report_console.print()
    report_console.print(build_overall_metrics_table(metrics))
    report_console.print()
    report_console.print(build_scenario_results_table(results))
    if any(not result.passed for result in results):
        report_console.print()
        report_console.print(build_failed_turn_trace_table(results))

    report_path.write_text(report_console.export_text(clear=False), encoding="utf-8")
    prune_old_evaluation_reports()
    return report_path


def to_repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(
    run_all: bool = typer.Option(
        True,
        "--all/--no-all",
        help="Run all available modes. Enabled by default. Use --no-all with --mode for a single mode.",
    ),
    mode: EvalMode = typer.Option(
        EvalMode.LOCAL,
        "--mode",
        case_sensitive=False,
        help="Single mode to evaluate when --no-all is used.",
    ),
    repeats: int = typer.Option(
        EVALUATOR_CONFIG.local_repeats_default,
        "--repeats",
        min=1,
        help="Repeats for local mode.",
    ),
    llm_repeats: int = typer.Option(
        EVALUATOR_CONFIG.llm_repeats_default,
        "--llm-repeats",
        min=1,
        help="Repeats for llm and full-llm modes.",
    ),
    scenario_retries: int = typer.Option(
        EVALUATOR_CONFIG.scenario_retries_default,
        "--scenario-retries",
        min=1,
        help="Retry attempts per scenario run. Retries are visible in the report.",
    ),
    exhaustive: bool = typer.Option(
        False,
        "--exhaustive/--no-exhaustive",
        help="Run every scenario for every selected mode. Disabled by default to avoid excessive LLM calls.",
    ),
) -> None:
    modes = resolve_modes(run_all=run_all, requested_mode=mode.value)

    results: list[ScenarioResult] = []

    for mode in modes:
        mode_repeats = llm_repeats if mode in {EvalMode.LLM, EvalMode.FULL_LLM} else repeats

        mode_scenarios = scenarios_for_mode(
            mode=mode,
            exhaustive=exhaustive,
        )

        mode_total_runs = len(mode_scenarios) * mode_repeats
        CONSOLE.print(f"Running {mode_total_runs} scenarios for mode {mode.value}")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.fields[current_run]}"),
            console=CONSOLE,
            transient=True,
        ) as progress:
            task_id = progress.add_task(
                description=f"mode={mode.value}",
                total=mode_total_runs,
                current_run="",
            )

            for repeat_index in range(1, mode_repeats + 1):
                for scenario in mode_scenarios:
                    progress.update(
                        task_id,
                        current_run=f"{scenario.name} (repeat {repeat_index})",
                    )
                    results.append(
                        run_scenario_with_retries(
                            scenario=scenario,
                            mode=mode,
                            repeat_index=repeat_index,
                            max_attempts=scenario_retries,
                        )
                    )
                    progress.advance(task_id, 1)

    fallback_ok, fallback_reason, fallback_metrics = run_fallback_smoke_checks()
    print_fallback_summary(fallback_ok, fallback_reason, fallback_metrics)

    if not fallback_ok:
        raise SystemExit(1)

    metrics = aggregate_metrics(results)
    metrics["fallback_smoke"] = fallback_metrics

    text_report_path = write_dated_evaluation_report(
        results=results,
        metrics=metrics,
        fallback_ok=fallback_ok,
        fallback_reason=fallback_reason,
        fallback_metrics=fallback_metrics,
        modes=modes,
        exhaustive=exhaustive,
        repeats=repeats,
        llm_repeats=llm_repeats,
        scenario_retries=scenario_retries,
    )

    print_summary(results, metrics)

    CONSOLE.print(f"Saved evaluation text report: {to_repo_relative(text_report_path)}")

    failed = [result for result in results if not result.passed]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    typer.run(main)
