from __future__ import annotations

from decimal import Decimal

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.policy import (
    LOOKUP_ACCOUNT_POLICY,
    PREPARE_PAYMENT_POLICY,
    PROCESS_PAYMENT_POLICY,
    VERIFY_IDENTITY_POLICY,
    identity_matches_account,
)
from settlesentry.agent.state import ConversationStep, ExtractedUserInput
from settlesentry.agent.workflow.constants import (
    AMOUNT_RETRY_ERRORS,
    LOOKUP_SERVICE_ERROR_STATUSES,
    TERMINAL_PAYMENT_SERVICE_ERRORS,
)
from settlesentry.agent.workflow.helpers import (
    clear_card_details,
    clear_identity_inputs,
    clear_secondary_identity_inputs,
    policy_blocked,
    result,
    validate_payment_amount,
)
from settlesentry.agent.workflow.result import AgentToolResult
from settlesentry.agent.workflow.routing import (
    recommended_node,
    required_fields,
    set_step_from_required_fields,
)
from settlesentry.core import OperationLogContext, settings
from settlesentry.integrations.payments.schemas import PaymentsAPIErrorCode


def greet_user(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="greet_user")

    if deps.state.completed:
        return result(deps, operation, ok=False, status="conversation_closed")

    # Greeting resets the prompt step without clearing collected state.
    deps.state.step = ConversationStep.WAITING_FOR_ACCOUNT_ID

    return result(
        deps,
        operation,
        ok=True,
        status="greeting",
        required_fields=("account_id",),
    )


def lookup_account(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="lookup_account")

    decision = LOOKUP_ACCOUNT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return policy_blocked(deps, operation, decision)

    deps.state.step = ConversationStep.LOOKING_UP_ACCOUNT
    lookup_result = deps.payments_client.lookup_account(deps.state.account_id or "")

    if not lookup_result.ok or lookup_result.account is None:
        deps.state.account_id = None
        deps.state.account = None
        deps.state.last_error = lookup_result.message
        deps.state.step = ConversationStep.WAITING_FOR_ACCOUNT_ID

        status = lookup_result.error_code.value if lookup_result.error_code else "account_lookup_failed"

        if status in LOOKUP_SERVICE_ERROR_STATUSES:
            status = "account_lookup_failed"

        return result(
            deps,
            operation,
            ok=False,
            status=status,
            required_fields=("account_id",),
            facts={"reason": lookup_result.message},
        )

    deps.state.account = lookup_result.account

    # Recompute required fields to respect out-of-order inputs.
    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    return result(
        deps,
        operation,
        ok=True,
        status="account_loaded",
        required_fields=fields,
        recommended_tool=node,
    )


def verify_identity(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="verify_identity")

    decision = VERIFY_IDENTITY_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return policy_blocked(deps, operation, decision)

    if identity_matches_account(deps.state):
        deps.state.verified = True

        # Reveal balance only after verification succeeds.
        balance = deps.state.outstanding_balance()
        if balance is not None and balance <= Decimal("0") and not settings.agent_policy.allow_zero_balance_payment:
            deps.state.mark_closed()
            clear_card_details(deps)
            return result(
                deps,
                operation,
                ok=True,
                status="zero_balance",
                facts={"balance": str(balance)},
            )

        deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_AMOUNT

        return result(
            deps,
            operation,
            ok=True,
            status="identity_verified",
            required_fields=("payment_amount",),
            facts={"balance": str(balance)},
        )

    # Any failed verification attempt counts toward the retry limit.
    deps.state.verification_attempts += 1
    attempts_remaining = settings.agent_policy.verification_max_attempts - deps.state.verification_attempts

    account = deps.state.account

    if account is not None and deps.state.provided_full_name == account.full_name:
        # Keep matched name and ask only for a new secondary factor.
        clear_secondary_identity_inputs(deps)
    else:
        # Reset identity inputs when the full name may be incorrect.
        clear_identity_inputs(deps)

    if attempts_remaining <= 0:
        deps.state.mark_closed()
        clear_card_details(deps)

        return result(
            deps,
            operation,
            ok=False,
            status="verification_exhausted",
            facts={"attempts_remaining": attempts_remaining},
        )

    fields = required_fields(deps)
    set_step_from_required_fields(deps, fields)

    return result(
        deps,
        operation,
        ok=False,
        status="identity_verification_failed",
        required_fields=fields,
        facts={"attempts_remaining": attempts_remaining},
    )


def capture_payment_amount(
    deps: AgentDeps,
    extracted: ExtractedUserInput,
) -> AgentToolResult:
    """Capture and validate payment amount, then sync the next required step."""
    operation = OperationLogContext(operation="capture_payment_amount")

    if deps.state.completed:
        return result(deps, operation, ok=False, status="conversation_closed")

    if extracted.payment_amount is None:
        fields = required_fields(deps)
        set_step_from_required_fields(deps, fields)

        return result(
            deps,
            operation,
            ok=False,
            status="missing_payment_amount",
            required_fields=fields,
        )

    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    blocked = validate_payment_amount(deps, operation)
    if blocked is not None:
        return blocked

    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    return result(
        deps,
        operation,
        ok=True,
        status="payment_amount_captured",
        required_fields=fields,
        recommended_tool=node,
    )


def capture_card_details(
    deps: AgentDeps,
    extracted: ExtractedUserInput,
) -> AgentToolResult:
    """Capture partial or complete card details, then sync the next required step."""
    operation = OperationLogContext(operation="capture_card_details")

    if deps.state.completed:
        return result(deps, operation, ok=False, status="conversation_closed")

    has_card_detail = any(
        getattr(extracted, field) is not None
        for field in (
            "cardholder_name",
            "card_number",
            "cvv",
            "expiry_month",
            "expiry_year",
        )
    )

    if not has_card_detail:
        fields = required_fields(deps)
        set_step_from_required_fields(deps, fields)

        return result(
            deps,
            operation,
            ok=False,
            status="missing_card_fields",
            required_fields=fields,
        )

    if not deps.state.verified or deps.state.payment_amount is None:
        fields = required_fields(deps)
        set_step_from_required_fields(deps, fields)

        return result(
            deps,
            operation,
            ok=False,
            status="payment_amount_required",
            required_fields=fields,
        )

    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    facts: dict[str, object] = {}

    card_last4 = deps.state.card_last4()
    if card_last4:
        facts["card_last4"] = card_last4

    return result(
        deps,
        operation,
        ok=True,
        status="card_details_captured",
        required_fields=fields,
        recommended_tool=node,
        facts=facts,
    )


def prepare_payment(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="prepare_payment")

    decision = PREPARE_PAYMENT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        deps.state.payment_confirmed = False
        return policy_blocked(deps, operation, decision)

    # Preparation only stages confirmation; no money movement happens here.
    deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION

    return result(
        deps,
        operation,
        ok=True,
        status="payment_ready_for_confirmation",
        required_fields=("confirmation",),
        facts={
            "amount": str(deps.state.payment_amount),
            "card_last4": deps.state.card_last4(),
        },
    )


def confirm_payment(deps: AgentDeps, confirmed: bool) -> AgentToolResult:
    operation = OperationLogContext(operation="confirm_payment")

    if not confirmed:
        deps.state.payment_confirmed = False
        return result(
            deps,
            operation,
            ok=False,
            status="payment_not_confirmed",
            required_fields=("confirmation",),
        )

    decision = PREPARE_PAYMENT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return policy_blocked(deps, operation, decision)

    # Confirmation is applied only after policy re-validation.
    deps.state.payment_confirmed = True
    deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION

    return result(
        deps,
        operation,
        ok=True,
        status="payment_confirmed",
        recommended_tool="process_payment",
    )


def process_payment(deps: AgentDeps) -> AgentToolResult:
    # Only this operation may call the payment API.
    operation = OperationLogContext(operation="process_payment")

    decision = PROCESS_PAYMENT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return policy_blocked(deps, operation, decision)

    deps.state.step = ConversationStep.PROCESSING_PAYMENT
    payment_result = deps.payments_client.process_payment(deps.state.build_payment_request())

    deps.state.payment_attempts += 1
    deps.state.payment_confirmed = False
    deps.state.last_error = payment_result.message

    if payment_result.ok:
        deps.state.transaction_id = payment_result.transaction_id
        deps.state.step = ConversationStep.PAYMENT_SUCCESS
        clear_card_details(deps)

        return result(
            deps,
            operation,
            ok=True,
            status="payment_success",
            recommended_tool="recap_and_close",
            facts={
                "amount": str(deps.state.payment_amount),
                "transaction_id": payment_result.transaction_id,
            },
        )

    if payment_result.error_code in TERMINAL_PAYMENT_SERVICE_ERRORS:
        # Close on ambiguous service failures to avoid unsafe retries.
        deps.state.mark_closed()
        clear_card_details(deps)

        return result(
            deps,
            operation,
            ok=False,
            status=payment_result.error_code.value if payment_result.error_code else "payment_failed",
            facts={
                "reason": payment_result.message,
                "attempts_remaining": settings.agent_policy.payment_max_attempts - deps.state.payment_attempts,
            },
        )

    # Retryable errors clear the impacted payment context.
    if payment_result.error_code in AMOUNT_RETRY_ERRORS:
        deps.state.payment_amount = None
        clear_card_details(deps)

    elif payment_result.error_code in {
        PaymentsAPIErrorCode.INVALID_CARD,
        PaymentsAPIErrorCode.INVALID_CVV,
        PaymentsAPIErrorCode.INVALID_EXPIRY,
        None,
    }:
        clear_card_details(deps)

    attempts_remaining = settings.agent_policy.payment_max_attempts - deps.state.payment_attempts

    if attempts_remaining <= 0:
        # Retry budget exhausted: close safely and clear secrets.
        deps.state.mark_closed()
        clear_card_details(deps)

        return result(
            deps,
            operation,
            ok=False,
            status="payment_attempts_exhausted",
            facts={"reason": payment_result.message, "attempts_remaining": attempts_remaining},
        )

    fields = required_fields(deps)
    set_step_from_required_fields(deps, fields)

    return result(
        deps,
        operation,
        ok=False,
        status=payment_result.error_code.value if payment_result.error_code else "payment_failed",
        required_fields=fields,
        facts={
            "reason": payment_result.message,
            "attempts_remaining": attempts_remaining,
        },
    )


def recap_and_close(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="recap_and_close")

    facts = {
        "account_id": deps.state.account_id,
        "verified": deps.state.verified,
        "payment_amount": str(deps.state.payment_amount) if deps.state.payment_amount is not None else None,
        "transaction_id": deps.state.transaction_id,
        "payment_status": "success" if deps.state.transaction_id else "not_completed",
        "payment_attempts": deps.state.payment_attempts,
        "verification_attempts": deps.state.verification_attempts,
        "reason": deps.state.last_error,
    }

    # Final recap uses safe facts; raw card data is cleared before close.
    deps.state.mark_closed()
    clear_card_details(deps)

    return result(
        deps,
        operation,
        ok=True,
        status="conversation_closed",
        facts=facts,
    )
