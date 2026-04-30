from __future__ import annotations

import argparse
import json
import os
import re
import time
from argparse import BooleanOptionalAction
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Callable

os.environ.setdefault("LOG_CONSOLE_ENABLED", "false")

from rich.console import Console
from rich.table import Table
from settlesentry.agent.interface import Agent
from settlesentry.agent.parsing.deterministic import DeterministicInputParser
from settlesentry.agent.parsing.factory import build_input_parser
from settlesentry.agent.response.writer import (
    DeterministicResponseGenerator,
    build_response_generator,
)
from settlesentry.agent.state import ConversationStep
from settlesentry.core import settings
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    LookupResult,
    PaymentResult,
    PaymentsAPIErrorCode,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "var" / "evaluation"
CONSOLE = Console()


class EvalMode(StrEnum):
    # Evaluate runtime behavior per mode because local, llm, and full-llm have
    # different reliability/latency profiles.
    LOCAL = "local"
    LLM = "llm"
    FULL_LLM = "full-llm"


class SpyPaymentsClient:
    # Fake client records tool calls so correctness can include timing and
    # premature-payment checks, not just final messages.
    def __init__(self, *, payment_outcomes: list[PaymentResult] | None = None) -> None:
        self.lookup_calls: list[str] = []
        self.payment_calls: list[dict] = []
        self.payment_outcomes = payment_outcomes or [
            PaymentResult(ok=True, transaction_id="txn_eval_success", status_code=200)
        ]

    def lookup_account(self, account_id: str) -> LookupResult:
        self.lookup_calls.append(account_id)

        accounts = {
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

        account = accounts.get(account_id)

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
            responder=DeterministicResponseGenerator(),
            grouped_card_collection=False,
        )

    if mode == EvalMode.LLM:
        return Agent(
            payments_client=client,
            parser=build_input_parser(),
            responder=DeterministicResponseGenerator(),
            grouped_card_collection=True,
        )

    return Agent(
        payments_client=client,
        parser=build_input_parser(),
        responder=build_response_generator(),
        grouped_card_collection=True,
    )


def scan_privacy_leaks(message: str) -> list[str]:
    # Conservative scanner for user-facing leaks. Keep in sync with sample
    # account/card fixtures.
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
    # Each scenario creates a fresh Agent instance so state leakage between
    # scenarios is impossible.
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
    # Retries are evaluation-level retries only; they do not change the agent's
    # internal retry behavior.
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


# Assertions define business correctness for each scenario: final state, tool
# calls, no privacy leak, no premature payment.
def assert_happy_path(agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]) -> tuple[bool, str, dict]:
    ok = (
        agent.state.completed
        and agent.state.transaction_id == "txn_eval_success"
        and len(client.lookup_calls) == 1
        and len(client.payment_calls) == 1
        and client.payment_calls[0]["amount"] == "500"
    )

    return (
        ok,
        "passed" if ok else "happy path did not complete correctly",
        {
            "scenario_success": int(ok),
            "tool_call_correctness": int(len(client.lookup_calls) == 1 and len(client.payment_calls) == 1),
            "partial_payment_supported": int(
                client.payment_calls[0]["amount"] == "500" if client.payment_calls else False
            ),
        },
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

    return (
        ok,
        "passed" if ok else "full balance payment failed",
        {
            "scenario_success": int(ok),
            "tool_call_correctness": int(len(client.lookup_calls) == 1 and len(client.payment_calls) == 1),
        },
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

    return (
        ok,
        "passed" if ok else "account-not-found recovery failed",
        {
            "scenario_success": int(ok),
            "recovery_success": int(ok),
            "clear_error_message": int(has_clear_error),
            "lookup_calls": len(client.lookup_calls),
            "payment_calls": len(client.payment_calls),
        },
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

    return (
        ok,
        "passed" if ok else "amount > balance was not blocked before card collection",
        {
            "scenario_success": int(ok),
            "amount_guardrail_success": int(ok),
            "premature_payment_calls": len(client.payment_calls),
            "clear_error_message": int(has_clear_error),
        },
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

    return (
        ok,
        "passed" if ok else "verification recovery failed",
        {
            "scenario_success": int(ok),
            "recovery_success": int(ok),
            "premature_payment_calls": len(client.payment_calls),
        },
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

    return (
        ok,
        "passed" if ok else "secondary-factor recovery failed",
        {
            "scenario_success": int(ok),
            "recovery_success": int(ok),
            "premature_payment_calls": len(client.payment_calls),
        },
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

    return (
        ok,
        "passed" if ok else "verification exhaustion did not close safely",
        {
            "scenario_success": int(ok),
            "graceful_close": int(ok),
            "premature_payment_calls": len(client.payment_calls),
        },
    )


def assert_zero_balance(agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]) -> tuple[bool, str, dict]:
    has_zero_balance_message = any("no outstanding balance" in record.agent_message.lower() for record in records)

    ok = (
        agent.state.completed
        and agent.state.step == ConversationStep.CLOSED
        and len(client.payment_calls) == 0
        and has_zero_balance_message
    )

    return (
        ok,
        "passed" if ok else "zero balance did not close safely",
        {
            "scenario_success": int(ok),
            "graceful_close": int(ok),
            "premature_payment_calls": len(client.payment_calls),
            "clear_error_message": int(has_zero_balance_message),
        },
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

    return (
        ok,
        "passed" if ok else "side question changed workflow state incorrectly",
        {
            "scenario_success": int(ok),
            "side_question_state_preserved": int(ok),
        },
    )


def assert_no_confirmation_no_payment(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION
        and not agent.state.completed
        and len(client.payment_calls) == 0
    )

    return (
        ok,
        "passed" if ok else "payment was processed without confirmation",
        {
            "scenario_success": int(ok),
            "confirmation_gate_success": int(ok),
            "premature_payment_calls": len(client.payment_calls),
        },
    )


def assert_cancel_at_confirmation(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = agent.state.completed and agent.state.step == ConversationStep.CLOSED and len(client.payment_calls) == 0

    return (
        ok,
        "passed" if ok else "cancel did not close safely before payment",
        {
            "scenario_success": int(ok),
            "graceful_close": int(ok),
            "premature_payment_calls": len(client.payment_calls),
        },
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

    return (
        ok,
        "passed" if ok else "valid amount correction did not reset confirmation and reprepare",
        {
            "scenario_success": int(ok),
            "correction_success": int(ok),
            "premature_payment_calls": len(client.payment_calls),
        },
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

    return (
        ok,
        "passed" if ok else "invalid corrected amount was not blocked",
        {
            "scenario_success": int(ok),
            "correction_success": int(ok),
            "amount_guardrail_success": int(ok),
            "premature_payment_calls": len(client.payment_calls),
        },
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

    return (
        ok,
        "passed" if ok else "payment failure recovery failed",
        {
            "scenario_success": int(ok),
            "payment_recovery_success": int(ok),
            "clear_error_message": int(has_invalid_card_message),
            "payment_calls": len(client.payment_calls),
        },
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

    return (
        ok,
        "passed" if ok else "payment attempts exhaustion did not close safely",
        {
            "scenario_success": int(ok),
            "graceful_close": int(ok),
            "payment_calls": len(client.payment_calls),
        },
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
            PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_CARD,
                message="The card number appears to be invalid.",
                status_code=422,
            ),
            PaymentResult(ok=True, transaction_id="txn_eval_success", status_code=200),
        ],
    ),
    EvalScenario(
        "payment_attempts_exhausted_closes",
        "failure_close",
        BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["yes", "4532 0151 1283 0366", "yes", "4532 0151 1283 0366", "yes"],
        assert_payment_attempts_exhausted,
        payment_outcomes=[
            PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_CARD,
                message="The card number appears to be invalid.",
                status_code=422,
            ),
            PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_CARD,
                message="The card number appears to be invalid.",
                status_code=422,
            ),
            PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_CARD,
                message="The card number appears to be invalid.",
                status_code=422,
            ),
        ],
    ),
]

LLM_CORE_SCENARIOS = {
    # Default LLM coverage is intentionally smaller because full LLM exhaustive
    # evaluation is slow and provider-dependent.
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
    # LLM modes are skipped unless OpenRouter is configured so local evaluation
    # remains runnable anywhere.
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
    # Metrics are run-level, not scenario-name-level, because modes/repeats can
    # produce multiple runs per scenario.
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

    def rate(metric_name: str) -> float:
        eligible = [result for result in results if metric_name in result.metrics]
        if not eligible:
            return 0.0

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


def print_summary(results: list[ScenarioResult], metrics: dict) -> None:
    mode_table = Table(title="Mode Performance Summary")
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

    CONSOLE.print(mode_table)

    scenario_table = Table(title="Scenario Results")
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

    CONSOLE.print(scenario_table)

    metrics_table = Table(title="Overall Metrics")
    metrics_table.add_column("Metric", style="cyan")
    metrics_table.add_column("Value", style="white")

    metrics_table.add_row("run_success_rate", f"{metrics['run_success_rate']:.2%}")
    metrics_table.add_row("passed_runs", f"{metrics['passed_runs']}/{metrics['total_runs']}")
    metrics_table.add_row("total_wall_time_seconds", f"{metrics['total_wall_time_seconds']:.2f}s")
    metrics_table.add_row("average_wall_time_seconds", f"{metrics['average_wall_time_seconds']:.2f}s")
    metrics_table.add_row("interface_compliance_rate", f"{metrics['interface_compliance_rate']:.2%}")
    metrics_table.add_row("privacy_leak_count", str(metrics["privacy_leak_count"]))
    metrics_table.add_row("premature_payment_calls", str(metrics["premature_payment_calls"]))
    metrics_table.add_row("total_lookup_calls", str(metrics["total_lookup_calls"]))
    metrics_table.add_row("total_payment_calls", str(metrics["total_payment_calls"]))
    metrics_table.add_row("average_turns_per_run", f"{metrics['average_turns_per_run']:.2f}")
    metrics_table.add_row("clear_error_message_rate", f"{metrics['clear_error_message_rate']:.2%}")
    metrics_table.add_row("graceful_close_rate", f"{metrics['graceful_close_rate']:.2%}")
    metrics_table.add_row("amount_guardrail_success_rate", f"{metrics['amount_guardrail_success_rate']:.2%}")
    metrics_table.add_row("correction_success_rate", f"{metrics['correction_success_rate']:.2%}")
    metrics_table.add_row("recovery_success_rate", f"{metrics['recovery_success_rate']:.2%}")
    metrics_table.add_row("payment_recovery_success_rate", f"{metrics['payment_recovery_success_rate']:.2%}")
    metrics_table.add_row("confirmation_gate_success_rate", f"{metrics['confirmation_gate_success_rate']:.2%}")

    CONSOLE.print(metrics_table)


def write_report(results: list[ScenarioResult], metrics: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "metrics": metrics,
        "results": [
            {
                **asdict(result),
                "turn_records": [asdict(record) for record in result.turn_records],
            }
            for result in results
        ],
    }

    output_path = OUTPUT_DIR / "latest_metrics.json"
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    return output_path


def parse_args() -> argparse.Namespace:
    # Default run should be practical; use --exhaustive only for full all-mode
    # stress evaluation.
    parser = argparse.ArgumentParser(description="Run scenario-based SettleSentry evaluation.")
    parser.add_argument(
        "--all",
        action=BooleanOptionalAction,
        default=True,
        help="Run all available modes. Enabled by default. Use --no-all with --mode for a single mode.",
    )
    parser.add_argument(
        "--mode",
        choices=["local", "llm", "full-llm"],
        default="local",
        help="Single mode to evaluate when --no-all is used.",
    )
    parser.add_argument("--repeats", type=int, default=1, help="Repeats for local mode.")
    parser.add_argument("--llm-repeats", type=int, default=1, help="Repeats for llm and full-llm modes.")
    parser.add_argument(
        "--scenario-retries",
        type=int,
        default=1,
        help="Retry attempts per scenario run. Retries are visible in the report.",
    )
    parser.add_argument("--json-only", action="store_true", help="Only write JSON report.")
    parser.add_argument(
        "--exhaustive",
        action=BooleanOptionalAction,
        default=False,
        help="Run every scenario for every selected mode. Disabled by default to avoid excessive LLM calls.",
    )

    args = parser.parse_args()

    if args.repeats < 1:
        raise SystemExit("--repeats must be >= 1")

    if args.llm_repeats < 1:
        raise SystemExit("--llm-repeats must be >= 1")

    if args.scenario_retries < 1:
        raise SystemExit("--scenario-retries must be >= 1")

    return args


def main() -> None:
    args = parse_args()
    modes = resolve_modes(run_all=args.all, requested_mode=args.mode)

    results: list[ScenarioResult] = []

    for mode in modes:
        repeats = args.llm_repeats if mode in {EvalMode.LLM, EvalMode.FULL_LLM} else args.repeats

        mode_scenarios = scenarios_for_mode(
            mode=mode,
            exhaustive=args.exhaustive,
        )

        for repeat_index in range(1, repeats + 1):
            for scenario in mode_scenarios:
                results.append(
                    run_scenario_with_retries(
                        scenario=scenario,
                        mode=mode,
                        repeat_index=repeat_index,
                        max_attempts=args.scenario_retries,
                    )
                )

    metrics = aggregate_metrics(results)
    report_path = write_report(results, metrics)

    if not args.json_only:
        print_summary(results, metrics)
        print(f"\nWrote report: {report_path}")

    failed = [result for result in results if not result.passed]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
