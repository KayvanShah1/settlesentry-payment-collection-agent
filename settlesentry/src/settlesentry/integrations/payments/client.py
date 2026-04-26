from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from settlesentry.core import get_logger, settings
from settlesentry.integrations.payments.endpoints import EndpointName, EndpointSpec, endpoints
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    AccountLookupRequest,
    LookupResult,
    PaymentFailureResponse,
    PaymentRequest,
    PaymentResult,
    PaymentsAPIErrorCode,
    PaymentSuccessResponse,
)

logger = get_logger("PaymentsClient")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    wait=wait_exponential_jitter(initial=0.3, max=2.0),
    stop=stop_after_attempt(settings.api.max_retries + 1),
    reraise=True,
)
def _post_with_retry(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> httpx.Response:
    response = client.post(url, json=payload, timeout=timeout_seconds)

    if response.status_code in RETRYABLE_STATUS_CODES:
        response.raise_for_status()

    return response


class PaymentsClient:
    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self.client = http_client or httpx.Client(timeout=settings.api.timeout_seconds)

    def _post_json(self, endpoint: EndpointSpec, payload: dict[str, Any]) -> httpx.Response:
        url = endpoints.url_for(endpoint.name)

        if endpoint.retryable:
            return _post_with_retry(
                self.client,
                url,
                payload,
                endpoint.timeout_seconds,
            )

        return self.client.post(url, json=payload, timeout=endpoint.timeout_seconds)

    def lookup_account(self, account_id: str) -> LookupResult:
        try:
            request = AccountLookupRequest(account_id=account_id)
        except ValidationError:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                message="Invalid account ID format.",
            )

        endpoint = endpoints.get(EndpointName.LOOKUP_ACCOUNT)

        logger.info(
            "lookup_account_started",
            extra={"account_id": request.account_id, "tool_name": endpoint.name.value},
        )

        try:
            response = self._post_json(endpoint, request.model_dump(mode="json"))
        except httpx.TimeoutException:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.TIMEOUT,
                message="Account lookup timed out. Please try again.",
            )
        except httpx.RequestError:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.NETWORK_ERROR,
                message="Account lookup failed due to a network error.",
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response

        data = self._parse_json(response)

        if data is None:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                message="Account lookup returned an invalid response.",
                status_code=response.status_code,
            )

        if response.status_code == 200:
            try:
                return LookupResult(
                    ok=True,
                    account=AccountDetails.model_validate(data),
                    status_code=response.status_code,
                )
            except ValidationError:
                return LookupResult(
                    ok=False,
                    error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                    message="Account lookup returned an unexpected response.",
                    status_code=response.status_code,
                )

        if response.status_code == 404:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND,
                message=data.get("message", "No account found with the provided account ID."),
                status_code=response.status_code,
            )

        return LookupResult(
            ok=False,
            error_code=PaymentsAPIErrorCode.UNEXPECTED_STATUS,
            message=f"Unexpected account lookup status: {response.status_code}.",
            status_code=response.status_code,
        )

    def process_payment(self, payment_request: PaymentRequest) -> PaymentResult:
        endpoint = endpoints.get(EndpointName.PROCESS_PAYMENT)

        logger.info(
            "process_payment_started",
            extra={
                "account_id": payment_request.account_id,
                "amount": float(payment_request.amount),
                "tool_name": endpoint.name.value,
            },
        )

        try:
            response = self._post_json(endpoint, payment_request.model_dump(mode="json"))
        except httpx.TimeoutException:
            return PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.TIMEOUT,
                message="Payment request timed out. I cannot safely retry it automatically.",
            )
        except httpx.RequestError:
            return PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.NETWORK_ERROR,
                message="Payment request failed due to a network error.",
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response

        data = self._parse_json(response)

        if data is None:
            return PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                message="Payment API returned an invalid response.",
                status_code=response.status_code,
            )

        if response.status_code == 200:
            try:
                result = PaymentSuccessResponse.model_validate(data)
                return PaymentResult(
                    ok=True,
                    transaction_id=result.transaction_id,
                    status_code=response.status_code,
                )
            except ValidationError:
                return PaymentResult(
                    ok=False,
                    error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                    message="Payment API returned an unexpected success response.",
                    status_code=response.status_code,
                )

        if response.status_code == 422:
            try:
                failure = PaymentFailureResponse.model_validate(data)
                return PaymentResult(
                    ok=False,
                    error_code=failure.error_code,
                    message=self._payment_error_message(failure.error_code),
                    status_code=response.status_code,
                )
            except ValidationError:
                return PaymentResult(
                    ok=False,
                    error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                    message="Payment API returned an invalid failure response.",
                    status_code=response.status_code,
                )

        return PaymentResult(
            ok=False,
            error_code=PaymentsAPIErrorCode.UNEXPECTED_STATUS,
            message=f"Unexpected payment status: {response.status_code}.",
            status_code=response.status_code,
        )

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any] | None:
        try:
            data = response.json()
        except ValueError:
            return None

        return data if isinstance(data, dict) else None

    @staticmethod
    def _payment_error_message(error_code: PaymentsAPIErrorCode) -> str:
        return {
            PaymentsAPIErrorCode.INVALID_AMOUNT: "The payment amount is invalid.",
            PaymentsAPIErrorCode.INSUFFICIENT_BALANCE: "The payment amount exceeds the outstanding balance.",
            PaymentsAPIErrorCode.INVALID_CARD: "The card number appears to be invalid.",
            PaymentsAPIErrorCode.INVALID_CVV: "The CVV appears to be invalid.",
            PaymentsAPIErrorCode.INVALID_EXPIRY: "The card expiry appears to be invalid or expired.",
        }.get(error_code, "The payment could not be processed.")
