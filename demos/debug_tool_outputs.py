from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint
from settlesentry.agent.autonomous.tools.account import provide_account_id
from settlesentry.agent.autonomous.tools.identity import provide_identity_details
from settlesentry.agent.autonomous.tools.lifecycle import start_payment_flow
from settlesentry.agent.autonomous.tools.payment import (
    confirm_and_process_payment,
    prepare_payment_for_confirmation,
    provide_card_details,
    provide_payment_amount,
)
from settlesentry.agent.deps import AgentDeps
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    LookupResult,
    PaymentResult,
    PaymentsAPIErrorCode,
)

console = Console()


@dataclass
class ToolCtx:
    deps: AgentDeps


class FakePaymentsClient:
    def __init__(self) -> None:
        self.lookup_calls: list[str] = []
        self.payment_calls: list[object] = []

    def lookup_account(self, account_id: str) -> LookupResult:
        self.lookup_calls.append(account_id)

        if account_id != "ACC1001":
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND,
                message="No account found with the provided account ID.",
                status_code=404,
            )

        return LookupResult(
            ok=True,
            account=AccountDetails(
                account_id="ACC1001",
                full_name="Nithin Jain",
                dob="1990-05-14",
                aadhaar_last4="4321",
                pincode="400001",
                balance=Decimal("1250.75"),
            ),
            status_code=200,
        )

    def process_payment(self, payment_request) -> PaymentResult:
        self.payment_calls.append(payment_request)

        return PaymentResult(
            ok=True,
            transaction_id="txn_debug_123",
            status_code=200,
        )


def dump_result(label: str, deps: AgentDeps, result: object) -> None:
    console.print(Panel.fit(label, border_style="blue"))

    if hasattr(result, "model_dump"):
        pprint(result.model_dump(mode="json"))
    else:
        pprint(result)

    console.print("[bold]Safe state:[/bold]")
    pprint(deps.state.safe_view(session_id=deps.session_id).model_dump(mode="json"))

    console.print("[bold]Internal step:[/bold]", deps.state.step.value)
    console.print()


def main() -> None:
    deps = AgentDeps(
        payments_client=FakePaymentsClient(),
        grouped_card_collection=True,
    )
    ctx = ToolCtx(deps=deps)

    steps = [
        (
            "1. start_payment_flow",
            lambda: start_payment_flow(ctx),
        ),
        (
            "2. provide_account_id",
            lambda: provide_account_id(ctx, account_id="ACC1001"),
        ),
        (
            "3. provide_identity_details",
            lambda: provide_identity_details(
                ctx,
                full_name="Nithin Jain",
                dob="1990-05-14",
            ),
        ),
        (
            "4. provide_payment_amount",
            lambda: provide_payment_amount(ctx, amount=Decimal("500.00")),
        ),
        (
            "5. provide_card_details",
            lambda: provide_card_details(
                ctx,
                cardholder_name="Nithin Jain",
                card_number="4532 0151 1283 0366",
                expiry_month=12,
                expiry_year=2027,
                cvv="123",
            ),
        ),
        (
            "6. prepare_payment_for_confirmation",
            lambda: prepare_payment_for_confirmation(ctx),
        ),
        (
            "7. confirm_and_process_payment",
            lambda: confirm_and_process_payment(ctx),
        ),
    ]

    for label, call in steps:
        result = call()
        dump_result(label, deps, result)

    console.print("[bold green]Lookup calls:[/bold green]", deps.payments_client.lookup_calls)
    console.print("[bold green]Payment calls:[/bold green]", len(deps.payments_client.payment_calls))


if __name__ == "__main__":
    main()
