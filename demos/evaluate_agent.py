from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table
from settlesentry.agent.agent import Agent
from settlesentry.agent.parsers.deterministic import DeterministicInputParser
from settlesentry.agent.responder import DeterministicResponseGenerator
from settlesentry.agent.state import ConversationStep
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    LookupResult,
    PaymentResult,
    PaymentsAPIErrorCode,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "var" / "evaluation"
CONSOLE = Console()


class SpyPaymentsClient:
    """
    In-memory payment client used for scenario evaluation.

    It records lookup/payment calls so we can measure tool-call correctness,
    premature payment attempts, recovery, and final state behavior.
    """

    def __init__(
        self,
        *,
        payment_outcomes: list[PaymentResult] | None = None,
    ) -> None:
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


def make_agent(client: SpyPaymentsClient) -> Agent:
    return Agent(
        payments_client=client,
        parser=DeterministicInputParser(),
        responder=DeterministicResponseGenerator(),
        grouped_card_collection=False,
    )


def scan_privacy_leaks(message: str) -> list[str]:
    """
    Detect sensitive values that should not appear in user-facing responses.

    Avoid naive substring checks for short values like CVV/Aadhaar last4 because
    they can appear inside transaction IDs, amounts, dates, or other unrelated text.
    """
    leaks: list[str] = []
    lowered = message.lower()

    # DOB should never be repeated back.
    for dob in ("1990-05-14", "1988-03-22", "1992-08-10"):
        if dob in lowered:
            leaks.append(f"dob:{dob}")

    # Full card number should never be repeated back.
    full_card_patterns = (
        r"\b4532015112830366\b",
        r"\b4532\s+0151\s+1283\s+0366\b",
        r"\b4532-0151-1283-0366\b",
    )

    for pattern in full_card_patterns:
        if re.search(pattern, lowered):
            leaks.append("full_card_number")

    # CVV should only be flagged when the response explicitly labels it as CVV/CVC.
    if re.search(r"\b(?:cvv|cvc)\b\D{0,10}\b123\b", lowered):
        leaks.append("cvv:123")

    # Aadhaar last4 should only be flagged when explicitly tied to Aadhaar.
    for last4 in ("4321", "9876", "2468"):
        if re.search(rf"\baadhaar\b\D{{0,20}}\b{last4}\b", lowered):
            leaks.append(f"aadhaar_last4:{last4}")

    # Pincode should only be flagged when explicitly tied to pincode/pin code.
    for pincode in ("400001", "400002", "400003"):
        if re.search(rf"\b(?:pincode|pin code)\b\D{{0,20}}\b{pincode}\b", lowered):
            leaks.append(f"pincode:{pincode}")

    return leaks


def run_scenario(scenario: EvalScenario) -> ScenarioResult:
    client = SpyPaymentsClient(payment_outcomes=scenario.payment_outcomes)
    agent = make_agent(client)
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


def assert_invalid_account_recovery(
    agent: Agent, client: SpyPaymentsClient, records: list[TurnRecord]
) -> tuple[bool, str, dict]:
    ok = (
        agent.state.has_account_loaded()
        and agent.state.account_id == "ACC1001"
        and len(client.lookup_calls) == 2
        and len(client.payment_calls) == 0
    )

    return (
        ok,
        "passed" if ok else "invalid account recovery failed",
        {
            "scenario_success": int(ok),
            "recovery_success": int(ok),
            "lookup_calls": len(client.lookup_calls),
            "payment_calls": len(client.payment_calls),
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
        name="happy_path_partial_payment",
        category="success",
        messages=BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["yes"],
        assert_result=assert_happy_path,
    ),
    EvalScenario(
        name="full_balance_payment",
        category="success",
        messages=[
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
        assert_result=assert_full_balance_payment,
    ),
    EvalScenario(
        name="amount_exceeds_balance_before_card_collection",
        category="guardrail",
        messages=[
            "Hi",
            "ACC1001",
            "Nithin Jain",
            "1990-05-14",
            "2000",
        ],
        assert_result=assert_amount_exceeds_balance,
    ),
    EvalScenario(
        name="invalid_account_then_recovery",
        category="recovery",
        messages=[
            "Hi",
            "ACC9999",
            "ACC1001",
        ],
        assert_result=assert_invalid_account_recovery,
    ),
    EvalScenario(
        name="verification_failure_then_recovery",
        category="recovery",
        messages=[
            "Hi",
            "ACC1001",
            "Wrong Name",
            "1990-05-14",
            "Nithin Jain",
            "1990-05-14",
        ],
        assert_result=assert_verification_recovery,
    ),
    EvalScenario(
        name="verification_exhaustion_closes",
        category="failure_close",
        messages=[
            "Hi",
            "ACC1001",
            "Wrong Name One",
            "1990-05-14",
            "Wrong Name Two",
            "1990-05-14",
            "Wrong Name Three",
            "1990-05-14",
        ],
        assert_result=assert_verification_exhaustion,
    ),
    EvalScenario(
        name="zero_balance_closes_without_payment",
        category="failure_close",
        messages=[
            "Hi",
            "ACC1003",
            "Priya Agarwal",
            "1992-08-10",
        ],
        assert_result=assert_zero_balance,
    ),
    EvalScenario(
        name="side_question_preserves_pending_state",
        category="conversation",
        messages=[
            "Hi",
            "ACC1001",
            "what will you do?",
        ],
        assert_result=assert_side_question_state_preserved,
    ),
    EvalScenario(
        name="no_payment_without_confirmation",
        category="guardrail",
        messages=BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["repeat that"],
        assert_result=assert_no_confirmation_no_payment,
    ),
    EvalScenario(
        name="cancel_at_confirmation_closes_without_payment",
        category="failure_close",
        messages=BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["no"],
        assert_result=assert_cancel_at_confirmation,
    ),
    EvalScenario(
        name="valid_amount_correction_requires_reconfirmation",
        category="correction",
        messages=BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["actually amount is INR 600"],
        assert_result=assert_valid_amount_correction,
    ),
    EvalScenario(
        name="invalid_amount_correction_blocked",
        category="correction",
        messages=BASE_VERIFIED_PAYMENT_READY_MESSAGES + ["actually amount is INR 2000"],
        assert_result=assert_invalid_amount_correction,
    ),
    EvalScenario(
        name="payment_failure_recovery",
        category="recovery",
        messages=BASE_VERIFIED_PAYMENT_READY_MESSAGES
        + [
            "yes",
            "4532 0151 1283 0366",
            "yes",
        ],
        payment_outcomes=[
            PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_CARD,
                message="The card number appears to be invalid.",
                status_code=422,
            ),
            PaymentResult(ok=True, transaction_id="txn_eval_success", status_code=200),
        ],
        assert_result=assert_payment_failure_recovery,
    ),
    EvalScenario(
        name="payment_attempts_exhausted_closes",
        category="failure_close",
        messages=BASE_VERIFIED_PAYMENT_READY_MESSAGES
        + [
            "yes",
            "4532 0151 1283 0366",
            "yes",
            "4532 0151 1283 0366",
            "yes",
        ],
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
        assert_result=assert_payment_attempts_exhausted,
    ),
]


def aggregate_metrics(results: list[ScenarioResult]) -> dict:
    total = len(results)
    passed = sum(result.passed for result in results)

    by_category: dict[str, dict[str, int | float]] = {}

    for result in results:
        category = result.category
        by_category.setdefault(category, {"total": 0, "passed": 0, "success_rate": 0.0})
        by_category[category]["total"] += 1

        if result.passed:
            by_category[category]["passed"] += 1

    for stats in by_category.values():
        stats["success_rate"] = stats["passed"] / stats["total"] if stats["total"] else 0.0

    def rate(metric_name: str) -> float:
        eligible = [result for result in results if metric_name in result.metrics]
        if not eligible:
            return 0.0

        return sum(int(result.metrics.get(metric_name, 0)) for result in eligible) / len(eligible)

    total_turns = sum(result.turns for result in results)
    total_lookup_calls = sum(result.lookup_calls for result in results)
    total_payment_calls = sum(result.payment_calls for result in results)
    privacy_leak_count = sum(int(result.metrics.get("privacy_leak_count", 0)) for result in results)
    premature_payment_calls = sum(int(result.metrics.get("premature_payment_calls", 0)) for result in results)

    return {
        "total_scenarios": total,
        "passed_scenarios": passed,
        "failed_scenarios": total - passed,
        "scenario_success_rate": passed / total if total else 0.0,
        "category_success_rates": by_category,
        "total_turns": total_turns,
        "average_turns_per_scenario": total_turns / total if total else 0.0,
        "total_lookup_calls": total_lookup_calls,
        "total_payment_calls": total_payment_calls,
        "privacy_leak_count": privacy_leak_count,
        "premature_payment_calls": premature_payment_calls,
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
    scenario_table = Table(title="SettleSentry Evaluation")
    scenario_table.add_column("Status", justify="center")
    scenario_table.add_column("Category", style="cyan")
    scenario_table.add_column("Scenario", style="white")
    scenario_table.add_column("Reason", style="magenta")
    for result in results:
        status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        scenario_table.add_row(status, result.category, result.name, result.reason)
    CONSOLE.print(scenario_table)

    metrics_table = Table(title="Metrics")
    metrics_table.add_column("Metric", style="cyan")
    metrics_table.add_column("Value", style="white")
    metrics_table.add_row("scenario_success_rate", f"{metrics['scenario_success_rate']:.2%}")
    metrics_table.add_row("passed_scenarios", f"{metrics['passed_scenarios']}/{metrics['total_scenarios']}")
    metrics_table.add_row("interface_compliance_rate", f"{metrics['interface_compliance_rate']:.2%}")
    metrics_table.add_row("privacy_leak_count", str(metrics["privacy_leak_count"]))
    metrics_table.add_row("premature_payment_calls", str(metrics["premature_payment_calls"]))
    metrics_table.add_row("total_lookup_calls", str(metrics["total_lookup_calls"]))
    metrics_table.add_row("total_payment_calls", str(metrics["total_payment_calls"]))
    metrics_table.add_row("average_turns_per_scenario", f"{metrics['average_turns_per_scenario']:.2f}")
    metrics_table.add_row("clear_error_message_rate", f"{metrics['clear_error_message_rate']:.2%}")
    metrics_table.add_row("graceful_close_rate", f"{metrics['graceful_close_rate']:.2%}")
    metrics_table.add_row("amount_guardrail_success_rate", f"{metrics['amount_guardrail_success_rate']:.2%}")
    metrics_table.add_row("correction_success_rate", f"{metrics['correction_success_rate']:.2%}")
    metrics_table.add_row("recovery_success_rate", f"{metrics['recovery_success_rate']:.2%}")
    metrics_table.add_row("payment_recovery_success_rate", f"{metrics['payment_recovery_success_rate']:.2%}")
    metrics_table.add_row("confirmation_gate_success_rate", f"{metrics['confirmation_gate_success_rate']:.2%}")
    CONSOLE.print(metrics_table)

    category_table = Table(title="Category Success Rates")
    category_table.add_column("Category", style="cyan")
    category_table.add_column("Passed/Total", style="white")
    category_table.add_column("Rate", style="white")
    for category, stats in metrics["category_success_rates"].items():
        category_table.add_row(category, f"{stats['passed']}/{stats['total']}", f"{stats['success_rate']:.2%}")
    CONSOLE.print(category_table)


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
    parser = argparse.ArgumentParser(description="Run scenario-based SettleSentry evaluation.")
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only write JSON report, do not print full console summary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    results = [run_scenario(scenario) for scenario in SCENARIOS]
    metrics = aggregate_metrics(results)
    report_path = write_report(results, metrics)

    if not args.json_only:
        print_summary(results, metrics)
        print(f"\nWrote report: {report_path}")

    failed = [result for result in results if not result.passed]

    if failed:
        raise SystemExit(1)

    raise SystemExit(0)


if __name__ == "__main__":
    main()
