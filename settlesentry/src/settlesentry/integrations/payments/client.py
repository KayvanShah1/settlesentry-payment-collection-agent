from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from settlesentry.core import get_logger, settings
from settlesentry.integrations.payments.endpoints import EndpointName, EndpointSpec, endpoint_registry
from settlesentry.integrations.payments.schemas import (
    AccountDetails,
    AccountLookupError,
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


@dataclass
class ToolCallLog:
    endpoint: EndpointSpec
    account_id: str
    amount: float | None = None
    operation_id: str = field(default_factory=lambda: uuid4().hex[:12])
    started_at: float = field(default_factory=perf_counter)
    http_call_made: bool = False

    @property
    def duration_ms(self) -> int:
        return int((perf_counter() - self.started_at) * 1000)

    def log_endpoint(self, *, url: str) -> None:
        logger.debug(
            "payment_endpoint_resolved",
            extra={
                "operation_id": self.operation_id,
                "tool_name": self.endpoint.name.value,
                "method": self.endpoint.method,
                "url": url,
                "retryable": self.endpoint.retryable,
                "timeout_seconds": self.endpoint.timeout_seconds,
                "max_retries": settings.api.max_retries if self.endpoint.retryable else 0,
            },
        )

    def log_completed(
        self,
        *,
        success_event: str,
        failure_event: str,
        result: LookupResult | PaymentResult,
    ) -> None:
        extra: dict[str, Any] = {
            "operation_id": self.operation_id,
            "tool_name": self.endpoint.name.value,
            "method": self.endpoint.method,
            "account_id": self.account_id,
            "ok": result.ok,
            "status_code": result.status_code,
            "error_code": result.error_code.value if result.error_code else None,
            "duration_ms": self.duration_ms,
            "http_call_made": self.http_call_made,
            "retryable": self.endpoint.retryable,
        }

        if self.amount is not None:
            extra["amount"] = self.amount

        if isinstance(result, PaymentResult) and result.transaction_id:
            extra["transaction_id"] = result.transaction_id

        logger.info(
            success_event if result.ok else failure_event,
            extra=extra,
        )


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    wait=wait_exponential_jitter(initial=0.3, max=2.0),
    stop=stop_after_attempt(settings.api.max_retries + 1),
    reraise=True,
)
def _post_with_retry(
    client: httpx.Client,
    *,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> httpx.Response:
    response = client.post(
        url,
        json=payload,
        timeout=timeout_seconds,
    )

    if response.status_code in RETRYABLE_STATUS_CODES:
        response.raise_for_status()

    return response


def _parse_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except ValueError:
        return None

    return data if isinstance(data, dict) else None


class PaymentsClient:
    """HTTP client for account lookup and payment processing."""

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self.client = http_client or httpx.Client(timeout=settings.api.timeout_seconds)

    def lookup_account(self, account_id: str) -> LookupResult:
        endpoint = endpoint_registry.get(EndpointName.LOOKUP_ACCOUNT)
        url = endpoint_registry.url_for(endpoint.name)
        normalized_account_id = str(account_id).strip()
        tool_log = ToolCallLog(
            endpoint=endpoint,
            account_id=normalized_account_id,
        )
        tool_log.log_endpoint(url=url)

        try:
            request = AccountLookupRequest(account_id=account_id)
        except ValidationError:
            result = LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                message="Invalid account ID format.",
            )
            tool_log.log_completed(
                success_event="lookup_account_completed",
                failure_event="lookup_account_failed",
                result=result,
            )
            return result

        try:
            tool_log.http_call_made = True
            response = _post_with_retry(
                self.client,
                url=url,
                payload=request.model_dump(mode="json"),
                timeout_seconds=endpoint.timeout_seconds,
            )
        except httpx.TimeoutException:
            result = LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.TIMEOUT,
                message=PaymentsAPIErrorCode.TIMEOUT.default_message(),
            )
        except httpx.RequestError:
            result = LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.NETWORK_ERROR,
                message=PaymentsAPIErrorCode.NETWORK_ERROR.default_message(),
            )
        except httpx.HTTPStatusError as exc:
            result = self._map_lookup_response(exc.response)
        else:
            result = self._map_lookup_response(response)

        tool_log.log_completed(
            success_event="lookup_account_completed",
            failure_event="lookup_account_failed",
            result=result,
        )
        return result

    def process_payment(self, payment_request: PaymentRequest) -> PaymentResult:
        endpoint = endpoint_registry.get(EndpointName.PROCESS_PAYMENT)
        url = endpoint_registry.url_for(endpoint.name)
        tool_log = ToolCallLog(
            endpoint=endpoint,
            account_id=payment_request.account_id,
            amount=float(payment_request.amount),
        )
        tool_log.log_endpoint(url=url)

        try:
            tool_log.http_call_made = True
            response = self.client.post(
                url,
                json=payment_request.model_dump(mode="json"),
                timeout=endpoint.timeout_seconds,
            )
        except httpx.TimeoutException:
            result = PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.TIMEOUT,
                message="Payment request timed out. I cannot safely retry it automatically.",
            )
        except httpx.RequestError:
            result = PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.NETWORK_ERROR,
                message=PaymentsAPIErrorCode.NETWORK_ERROR.default_message(),
            )
        else:
            result = self._map_payment_response(response)

        tool_log.log_completed(
            success_event="process_payment_completed",
            failure_event="process_payment_failed",
            result=result,
        )
        return result

    @staticmethod
    def _map_lookup_response(response: httpx.Response) -> LookupResult:
        data = _parse_json(response)

        if data is None:
            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                message=PaymentsAPIErrorCode.INVALID_RESPONSE.default_message(),
                status_code=response.status_code,
            )

        if response.status_code == 200:
            try:
                account = AccountDetails.model_validate(data)
            except ValidationError:
                return LookupResult(
                    ok=False,
                    error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                    message="Account lookup returned an unexpected response.",
                    status_code=response.status_code,
                )

            return LookupResult(
                ok=True,
                account=account,
                status_code=response.status_code,
            )

        if response.status_code == 404:
            try:
                lookup_error = AccountLookupError.model_validate(data)
                message = lookup_error.message
            except ValidationError:
                message = PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND.default_message()

            return LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND,
                message=message,
                status_code=response.status_code,
            )

        return LookupResult(
            ok=False,
            error_code=PaymentsAPIErrorCode.UNEXPECTED_STATUS,
            message=f"Unexpected account lookup status: {response.status_code}.",
            status_code=response.status_code,
        )

    @staticmethod
    def _map_payment_response(response: httpx.Response) -> PaymentResult:
        data = _parse_json(response)

        if data is None:
            return PaymentResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                message=PaymentsAPIErrorCode.INVALID_RESPONSE.default_message(),
                status_code=response.status_code,
            )

        if response.status_code == 200:
            try:
                success = PaymentSuccessResponse.model_validate(data)
            except ValidationError:
                return PaymentResult(
                    ok=False,
                    error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                    message="Payment API returned an unexpected success response.",
                    status_code=response.status_code,
                )

            return PaymentResult(
                ok=True,
                transaction_id=success.transaction_id,
                status_code=response.status_code,
            )

        if response.status_code == 422:
            try:
                failure = PaymentFailureResponse.model_validate(data)
            except ValidationError:
                return PaymentResult(
                    ok=False,
                    error_code=PaymentsAPIErrorCode.INVALID_RESPONSE,
                    message="Payment API returned an invalid failure response.",
                    status_code=response.status_code,
                )

            return PaymentResult(
                ok=False,
                error_code=failure.error_code,
                message=failure.error_code.default_message(),
                status_code=response.status_code,
            )

        return PaymentResult(
            ok=False,
            error_code=PaymentsAPIErrorCode.UNEXPECTED_STATUS,
            message=f"Unexpected payment status: {response.status_code}.",
            status_code=response.status_code,
        )
