import json

import httpx
import pytest
from settlesentry.core import settings
from settlesentry.integrations.payments.client import PaymentsClient
from settlesentry.integrations.payments.schemas import CardDetails, PaymentMethod, PaymentRequest, PaymentsAPIErrorCode


def make_client(handler) -> PaymentsClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return PaymentsClient(http_client=http_client)


def make_payment_request(amount: float = 500.00) -> PaymentRequest:
    return PaymentRequest(
        account_id="ACC1001",
        amount=amount,
        payment_method=PaymentMethod(
            card=CardDetails(
                cardholder_name="Nithin Jain",
                card_number="4532015112830366",
                cvv="123",
                expiry_month=12,
                expiry_year=2027,
            )
        ),
    )


def json_response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def test_lookup_account_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"

        return json_response(
            200,
            {
                "account_id": "ACC1001",
                "full_name": "Nithin Jain",
                "dob": "1990-05-14",
                "aadhaar_last4": "4321",
                "pincode": "400001",
                "balance": 1250.75,
            },
        )

    client = make_client(handler)

    result = client.lookup_account("ACC1001")

    assert result.ok is True
    assert result.account is not None
    assert result.account.account_id == "ACC1001"
    assert result.account.full_name == "Nithin Jain"
    assert result.account.balance == 1250.75


def test_lookup_account_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            404,
            {
                "error_code": "account_not_found",
                "message": "No account found with the provided account_id.",
            },
        )

    client = make_client(handler)

    result = client.lookup_account("ACC9999")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND
    assert result.status_code == 404


def test_lookup_account_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=b"not-json",
        )

    client = make_client(handler)

    result = client.lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE


def test_lookup_account_retries_transient_http_status():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(503, {"message": "service unavailable"})

    client = make_client(handler)

    result = client.lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.UNEXPECTED_STATUS
    assert result.status_code == 503
    assert calls["count"] == settings.api.max_retries + 1


def test_lookup_account_timeout_is_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.TimeoutException("request timed out")

    client = make_client(handler)

    result = client.lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.TIMEOUT
    assert calls["count"] == settings.api.max_retries + 1


def test_lookup_account_network_error_is_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("network down", request=request)

    client = make_client(handler)

    result = client.lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.NETWORK_ERROR
    assert calls["count"] == settings.api.max_retries + 1


def test_lookup_account_200_with_unexpected_shape_returns_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {
                "account_id": "ACC1001",
                "balance": 1250.75,
            },
        )

    client = make_client(handler)

    result = client.lookup_account("ACC1001")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE


def test_lookup_account_invalid_account_id_is_rejected_before_api_call():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(500, {"message": "should not be called"})

    client = make_client(handler)

    result = client.lookup_account("not-an-account-id")

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE
    assert result.message == "Invalid account ID format."
    assert calls["count"] == 0


def test_process_payment_success():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))

        assert request.method == "POST"
        assert payload["account_id"] == "ACC1001"
        assert payload["amount"] == 500.0
        assert payload["payment_method"]["type"] == "card"

        return json_response(
            200,
            {
                "success": True,
                "transaction_id": "txn_1762510325322_l1fl4oy",
            },
        )

    client = make_client(handler)

    result = client.process_payment(make_payment_request())

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

    client = make_client(handler)

    result = client.process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_CARD
    assert result.status_code == 422
    assert "card" in result.message.lower()


@pytest.mark.parametrize(
    ("api_error_code", "message_fragment"),
    [
        (PaymentsAPIErrorCode.INVALID_AMOUNT.value, "amount"),
        (PaymentsAPIErrorCode.INSUFFICIENT_BALANCE.value, "balance"),
        (PaymentsAPIErrorCode.INVALID_CVV.value, "cvv"),
        (PaymentsAPIErrorCode.INVALID_EXPIRY.value, "expiry"),
    ],
)
def test_process_payment_failure_maps_known_error_codes(api_error_code: str, message_fragment: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            422,
            {
                "success": False,
                "error_code": api_error_code,
            },
        )

    client = make_client(handler)

    result = client.process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode(api_error_code)
    assert result.status_code == 422
    assert message_fragment in (result.message or "").lower()


def test_process_payment_200_with_unexpected_shape_returns_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"success": True})

    client = make_client(handler)

    result = client.process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE


def test_process_payment_422_with_unknown_error_code_returns_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            422,
            {
                "success": False,
                "error_code": "not_a_valid_code",
            },
        )

    client = make_client(handler)

    result = client.process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.INVALID_RESPONSE


def test_process_payment_timeout_is_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.TimeoutException("request timed out")

    client = make_client(handler)

    result = client.process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.TIMEOUT
    assert calls["count"] == 1


def test_process_payment_server_error_is_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(503, {"message": "service unavailable"})

    client = make_client(handler)

    result = client.process_payment(make_payment_request())

    assert result.ok is False
    assert result.error_code == PaymentsAPIErrorCode.UNEXPECTED_STATUS
    assert result.status_code == 503
    assert calls["count"] == 1
