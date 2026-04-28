from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rich.console import Console
from rich.panel import Panel
from pydantic import ValidationError

from settlesentry.core import settings
from settlesentry.integrations.payments.client import PaymentsClient
from settlesentry.integrations.payments.schemas import CardDetails, PaymentMethod, PaymentRequest

console = Console()

# Hardcoded lookup examples.
LOOKUP_CASES = [
    {"name": "lookup_valid_account", "account_id": "ACC1001"},
    {"name": "lookup_zero_balance_account", "account_id": "ACC1003"},
    {"name": "lookup_unknown_account", "account_id": "ACC9999"},
    {"name": "lookup_invalid_account_format", "account_id": "BAD1001"},
]

# Hardcoded payment examples.
PAYMENT_CASES = [
    {
        "name": "payment_success",
        "account_id": "ACC1001",
        "amount": "500.00",
        "cardholder_name": "Nithin Jain",
        "card_number": "4532 0151 1283 0366",
        "cvv": "123",
        "expiry_month": 12,
        "expiry_year": 2027,
    },
    {
        "name": "payment_insufficient_balance",
        "account_id": "ACC1002",
        "amount": "9999.00",
        "cardholder_name": "Rajarajeswari Balasubramaniam",
        "card_number": "4532015112830366",
        "cvv": "123",
        "expiry_month": 12,
        "expiry_year": 2027,
    },
    {
        "name": "payment_invalid_card_luhn",
        "account_id": "ACC1001",
        "amount": "100.00",
        "cardholder_name": "Nithin Jain",
        "card_number": "4532015112830367",
        "cvv": "123",
        "expiry_month": 12,
        "expiry_year": 2027,
    },
    {
        "name": "payment_invalid_cvv",
        "account_id": "ACC1001",
        "amount": "100.00",
        "cardholder_name": "Nithin Jain",
        "card_number": "4532015112830366",
        "cvv": "12",
        "expiry_month": 12,
        "expiry_year": 2027,
    },
    {
        "name": "payment_expired_card",
        "account_id": "ACC1001",
        "amount": "100.00",
        "cardholder_name": "Nithin Jain",
        "card_number": "4532015112830366",
        "cvv": "123",
        "expiry_month": 1,
        "expiry_year": 2020,
    },
]

RUN_PAYMENT_CASES = True


def build_payment_request(case: dict[str, str | int]) -> PaymentRequest:
    try:
        amount = Decimal(str(case["amount"]))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {case['amount']}") from exc

    card = CardDetails(
        cardholder_name=str(case["cardholder_name"]),
        card_number=str(case["card_number"]),
        cvv=str(case["cvv"]),
        expiry_month=int(case["expiry_month"]),
        expiry_year=int(case["expiry_year"]),
    )
    return PaymentRequest(
        account_id=str(case["account_id"]),
        amount=amount,
        payment_method=PaymentMethod(card=card),
    )


def main() -> None:
    client = PaymentsClient()

    console.print(
        Panel(
            f"Base URL: {settings.api.base_url}\n"
            f"Timeout: {settings.api.timeout_seconds}s\n"
            f"Retries (lookup only): {settings.api.max_retries}\n"
            f"Lookup Cases: {len(LOOKUP_CASES)}\n"
            f"Payment Cases: {len(PAYMENT_CASES)}\n"
            f"Run Payment Cases: {RUN_PAYMENT_CASES}",
            title="Payments Client Demo",
            expand=False,
        )
    )

    console.rule("[bold cyan]Lookup Cases[/bold cyan]")
    for case in LOOKUP_CASES:
        console.print(f"[bold]Case:[/bold] {case['name']}")
        lookup_result = client.lookup_account(str(case["account_id"]))
        console.print_json(data=lookup_result.model_dump(mode="json"))

    if not RUN_PAYMENT_CASES:
        return

    console.rule("[bold cyan]Payment Cases[/bold cyan]")
    for case in PAYMENT_CASES:
        console.print(f"[bold]Case:[/bold] {case['name']}")
        try:
            payment_request = build_payment_request(case)
        except (ValidationError, ValueError) as exc:
            console.print(f"[bold red]Local validation error:[/bold red] {exc}")
            continue

        payment_result = client.process_payment(payment_request)
        console.print_json(data=payment_result.model_dump(mode="json"))


if __name__ == "__main__":
    main()
