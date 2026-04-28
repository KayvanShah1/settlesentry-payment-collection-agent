from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Callable

import httpx
import pytest
import settlesentry.integrations.payments.client as payments_client_module
from settlesentry.core import settings
from settlesentry.integrations.payments.client import PaymentsClient
from settlesentry.integrations.payments.schemas import (
    CardDetails,
    PaymentMethod,
    PaymentRequest,
    PaymentsAPIErrorCode,
)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> PaymentsClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return PaymentsClient(http_client=http_client)


def make_payment_request(amount: Decimal = Decimal("500.00")) -> PaymentRequest:
    return PaymentRequest(
        account_id="ACC1001",
        amount=amount,
        payment_method=PaymentMethod(
            card=CardDetails(
                cardholder_name="Nithin Jain",
                card_number="4532015112830366",
                cvv="123",
                expiry_month=12,
                expiry_year=date.today().year + 1,
            )
        ),
    )


def json_response(status_code: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def account_payload() -> dict[str, Any]:
    return {
        "account_id": "ACC1001",
        "full_name": "Nithin Jain",
        "dob": "1990-05-14",
        "aadhaar_last4": "4321",
        "pincode": "400001",
        "balance": 1250.75,
    }


def test_lookup_account_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return json_response(200, account_payload())

    result = make_client(handler).lookup_account("ACC1001")

    assert result.ok is True
    assert result.account is not None
    assert result.account.account_id == "ACC1001"
    assert result.account.full_name == "Nithin Jain"
    assert result.account.balance == Decimal("1250.75")
    assert result.status_code == 200


def test_lookup_account_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            404,
            {
                "error_code": "account_not_found",
                "message": "No account found with the provided account_id.",
            },
        )

    result = make_client(handler).lookup_account("ACC9999")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND
    assert result.message == "No account found with the provided account_id."
    assert result.status_code == 404


def test_lookup_account_404_with_invalid_error_shape_uses_default_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            404,
            {
                "message": "",
            },
        )

    result = make_client(handler).lookup_account("ACC9999")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND
    assert result.message == PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND.default_message()
    assert result.status_code == 404


def test_lookup_account_invalid_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=b"not-json",
        )

    result = make_client(handler).lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE


def test_lookup_account_200_with_unexpected_shape_returns_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {
                "account_id": "ACC1001",
                "balance": 1250.75,
            },
        )

    result = make_client(handler).lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE
    assert result.status_code == 200


def test_lookup_account_invalid_account_id_is_rejected_before_api_call():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(500, {"message": "should not be called"})

    result = make_client(handler).lookup_account("not-an-account-id")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE
    assert result.message == "Invalid account ID format."
    assert calls["count"] == 0


def test_lookup_account_retries_transient_http_status():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(503, {"message": "service unavailable"})

    result = make_client(handler).lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.UNEXPECTED_STATUS
    assert result.status_code == 503
    assert calls["count"] == settings.api.max_retries + 1


def test_lookup_account_timeout_is_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.TimeoutException("request timed out")

    result = make_client(handler).lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.TIMEOUT
    assert calls["count"] == settings.api.max_retries + 1


def test_lookup_account_network_error_is_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("network down", request=request)

    result = make_client(handler).lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.NETWORK_ERROR
    assert calls["count"] == settings.api.max_retries + 1


def test_process_payment_success():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))

        assert request.method == "POST"
        assert payload["account_id"] == "ACC1001"
        assert payload["amount"] == 500.0
        assert payload["payment_method"]["type"] == "card"
        assert payload["payment_method"]["card"]["card_number"] == "4532015112830366"

        return json_response(
            200,
            {
                "success": True,
                "transaction_id": "txn_1762510325322_l1fl4oy",
            },
        )

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is True
    assert result.transaction_id == "txn_1762510325322_l1fl4oy"
    assert result.status_code == 200


def test_process_payment_failure_invalid_card():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            422,
            {
                "success": False,
                "error_code": "invalid_card",
            },
        )

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_CARD
    assert result.message == PaymentsAPIErrorCode.INVALID_CARD.default_message()
    assert result.status_code == 422


@pytest.mark.parametrize(
    "api_error_code",
    [
        PaymentsAPIErrorCode.INVALID_AMOUNT,
        PaymentsAPIErrorCode.INSUFFICIENT_BALANCE,
        PaymentsAPIErrorCode.INVALID_CVV,
        PaymentsAPIErrorCode.INVALID_EXPIRY,
    ],
)
def test_process_payment_failure_maps_known_error_codes(api_error_code: PaymentsAPIErrorCode):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            422,
            {
                "success": False,
                "error_code": api_error_code.value,
            },
        )

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == api_error_code
    assert result.message == api_error_code.default_message()
    assert result.status_code == 422


def test_process_payment_invalid_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=b"not-json",
        )

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE


def test_process_payment_200_with_unexpected_shape_returns_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"success": True})

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE
    assert result.status_code == 200


def test_process_payment_422_with_unknown_error_code_returns_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            422,
            {
                "success": False,
                "error_code": "not_a_valid_code",
            },
        )

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE
    assert result.status_code == 422


def test_process_payment_timeout_is_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.TimeoutException("request timed out")

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.TIMEOUT
    assert calls["count"] == 1


def test_process_payment_network_error_is_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("network down", request=request)

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.NETWORK_ERROR
    assert calls["count"] == 1


def test_process_payment_server_error_is_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(503, {"message": "service unavailable"})

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.UNEXPECTED_STATUS
    assert result.status_code == 503
    assert calls["count"] == 1


def test_lookup_account_logs_endpoint_and_completed_events(monkeypatch: pytest.MonkeyPatch):
    emitted_info: list[tuple[str, dict[str, Any]]] = []
    emitted_debug: list[tuple[str, dict[str, Any]]] = []

    def fake_info(message, *args, **kwargs):
        emitted_info.append((message, kwargs.get("extra", {})))

    def fake_debug(message, *args, **kwargs):
        emitted_debug.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr(payments_client_module.logger, "info", fake_info)
    monkeypatch.setattr(payments_client_module.logger, "debug", fake_debug)

    result = make_client(lambda _request: json_response(200, account_payload())).lookup_account("ACC1001")

    assert result.ok is True

    resolved = [extra for message, extra in emitted_debug if message == "payment_endpoint_resolved"]
    completed = [extra for message, extra in emitted_info if message == "lookup_account_completed"]

    assert len(resolved) == 1
    assert len(completed) == 1

    assert resolved[0]["tool_name"] == "lookup_account"
    assert resolved[0]["method"] == "POST"
    assert resolved[0]["retryable"] is True
    assert resolved[0]["timeout_seconds"] == settings.api.timeout_seconds

    assert completed[0]["tool_name"] == "lookup_account"
    assert completed[0]["ok"] is True
    assert completed[0]["status_code"] == 200
    assert completed[0]["http_call_made"] is True
    assert completed[0]["retryable"] is True
    assert isinstance(completed[0]["duration_ms"], int)


def test_lookup_account_logs_validation_failure_without_http_call(monkeypatch: pytest.MonkeyPatch):
    emitted_info: list[tuple[str, dict[str, Any]]] = []

    def fake_info(message, *args, **kwargs):
        emitted_info.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr(payments_client_module.logger, "info", fake_info)

    result = make_client(lambda _request: json_response(500, {"message": "unused"})).lookup_account("bad-id")

    assert result.ok is False

    failed = [extra for message, extra in emitted_info if message == "lookup_account_failed"]

    assert len(failed) == 1
    assert failed[0]["http_call_made"] is False
    assert failed[0]["error_code"] == PaymentsAPIErrorCode.INVALID_RESPONSE.value
    assert failed[0]["status_code"] is None


def test_process_payment_logs_endpoint_and_completed_events(monkeypatch: pytest.MonkeyPatch):
    emitted_info: list[tuple[str, dict[str, Any]]] = []
    emitted_debug: list[tuple[str, dict[str, Any]]] = []

    def fake_info(message, *args, **kwargs):
        emitted_info.append((message, kwargs.get("extra", {})))

    def fake_debug(message, *args, **kwargs):
        emitted_debug.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr(payments_client_module.logger, "info", fake_info)
    monkeypatch.setattr(payments_client_module.logger, "debug", fake_debug)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {
                "success": True,
                "transaction_id": "txn_test_123",
            },
        )

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is True

    resolved = [extra for message, extra in emitted_debug if message == "payment_endpoint_resolved"]
    completed = [extra for message, extra in emitted_info if message == "process_payment_completed"]

    assert len(resolved) == 1
    assert len(completed) == 1

    assert resolved[0]["tool_name"] == "process_payment"
    assert resolved[0]["method"] == "POST"
    assert resolved[0]["retryable"] is False

    assert completed[0]["tool_name"] == "process_payment"
    assert completed[0]["ok"] is True
    assert completed[0]["status_code"] == 200
    assert completed[0]["transaction_id"] == "txn_test_123"
    assert completed[0]["http_call_made"] is True
    assert completed[0]["amount"] == 500.0
    assert isinstance(completed[0]["duration_ms"], int)


def test_process_payment_logs_do_not_include_card_or_cvv(monkeypatch: pytest.MonkeyPatch):
    emitted_info: list[tuple[str, dict[str, Any]]] = []
    emitted_debug: list[tuple[str, dict[str, Any]]] = []

    def fake_info(message, *args, **kwargs):
        emitted_info.append((message, kwargs.get("extra", {})))

    def fake_debug(message, *args, **kwargs):
        emitted_debug.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr(payments_client_module.logger, "info", fake_info)
    monkeypatch.setattr(payments_client_module.logger, "debug", fake_debug)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {
                "success": True,
                "transaction_id": "txn_test_abc",
            },
        )

    result = make_client(handler).process_payment(make_payment_request())

    assert result.ok is True

    serialized_logs = json.dumps(
        {
            "info": emitted_info,
            "debug": emitted_debug,
        },
        default=str,
    )

    assert "4532015112830366" not in serialized_logs
    assert '"cvv"' not in serialized_logs
    assert '"card_number"' not in serialized_logs

    for _message, extra in emitted_info + emitted_debug:
        assert "cvv" not in extra
        assert "card_number" not in extra
