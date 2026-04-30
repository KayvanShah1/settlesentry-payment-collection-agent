from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from settlesentry.core import OperationLogContext, get_logger, settings
from settlesentry.integrations.payments.endpoints import EndpointName, endpoint_registry
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

# Retries are used for account lookup only through _post_with_retry; payment
# processing is not auto-retried to avoid duplicate-charge ambiguity.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


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
    # Shared retry wrapper for idempotent/read-like lookup calls. Do not use
    # blindly for process_payment.
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
        context = OperationLogContext(operation=endpoint.name.value)
        http_call_made = False

        self._log_endpoint(context=context, endpoint=endpoint, url=url)

        try:
            # Account IDs are opaque, but empty/invalid schema input is treated as
            # not found so the user can retry cleanly.
            request = AccountLookupRequest(account_id=account_id)
        except ValidationError:
            result = LookupResult(
                ok=False,
                error_code=PaymentsAPIErrorCode.ACCOUNT_NOT_FOUND,
                message="No account found with the provided account ID.",
            )
            self._log_result(
                context=context,
                event="lookup_account_failed",
                endpoint=endpoint,
                account_id=str(account_id).strip(),
                result=result,
                http_call_made=http_call_made,
            )
            return result

        try:
            http_call_made = True
            # This is the only account lookup API boundary. If lookup calls look
            # wrong, inspect request.model_dump here.
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

        self._log_result(
            context=context,
            event="lookup_account_completed" if result.ok else "lookup_account_failed",
            endpoint=endpoint,
            account_id=request.account_id,
            result=result,
            http_call_made=http_call_made,
        )
        return result

    def process_payment(self, payment_request: PaymentRequest) -> PaymentResult:
        endpoint = endpoint_registry.get(EndpointName.PROCESS_PAYMENT)
        url = endpoint_registry.url_for(endpoint.name)
        context = OperationLogContext(operation=endpoint.name.value)
        http_call_made = False

        self._log_endpoint(context=context, endpoint=endpoint, url=url)

        try:
            http_call_made = True
            # Payment API is called once per confirmed attempt; no retry wrapper
            # here by design.
            response = self.client.post(
                url,
                json=payment_request.model_dump(mode="json"),
                timeout=endpoint.timeout_seconds,
            )
        except httpx.TimeoutException:
            # Timeout leaves payment status ambiguous, so the agent should close
            # rather than retry automatically.
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

        self._log_result(
            context=context,
            event="process_payment_completed" if result.ok else "process_payment_failed",
            endpoint=endpoint,
            account_id=payment_request.account_id,
            result=result,
            http_call_made=http_call_made,
            amount=float(payment_request.amount),
        )
        return result

    @staticmethod
    def _log_endpoint(
        *,
        context: OperationLogContext,
        endpoint,
        url: str,
    ) -> None:
        logger.debug(
            "payment_endpoint_resolved",
            extra=context.extra(
                tool_name=endpoint.name.value,
                method=endpoint.method,
                url=url,
                retryable=endpoint.retryable,
                timeout_seconds=endpoint.timeout_seconds,
                max_retries=settings.api.max_retries if endpoint.retryable else 0,
            ),
        )

    @staticmethod
    def _log_result(
        *,
        context: OperationLogContext,
        event: str,
        endpoint,
        account_id: str,
        result: LookupResult | PaymentResult,
        http_call_made: bool,
        amount: float | None = None,
    ) -> None:
        # Logs include account ID, amount, status, and transaction ID only; never
        # add full card number or CVV here.
        extra = context.completed_extra(
            tool_name=endpoint.name.value,
            method=endpoint.method,
            account_id=account_id,
            ok=result.ok,
            status_code=result.status_code,
            error_code=result.error_code.value if result.error_code else None,
            http_call_made=http_call_made,
            retryable=endpoint.retryable,
        )

        if amount is not None:
            extra["amount"] = amount

        if isinstance(result, PaymentResult) and result.transaction_id:
            extra["transaction_id"] = result.transaction_id

        logger.info(event, extra=extra)

    @staticmethod
    def _map_lookup_response(response: httpx.Response) -> LookupResult:
        # Normalize all lookup outcomes into LookupResult so nodes do not depend
        # on raw HTTP status bodies.
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
        # Normalize payment success/failure/error responses into PaymentResult for
        # policy/retry handling.
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
