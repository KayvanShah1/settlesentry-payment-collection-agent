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
from settlesentry.agent.state import ConversationStep
from settlesentry.agent.workflow.constants import (
    AMOUNT_RETRY_ERRORS,
    LOOKUP_SERVICE_ERROR_STATUSES,
    TERMINAL_PAYMENT_SERVICE_ERRORS,
)
from settlesentry.agent.workflow.helpers import (
    clear_identity_inputs,
    clear_payment_secrets,
    clear_secondary_identity_inputs,
    policy_blocked,
    result,
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

    # Greeting always resets only the step, not state, so repeated "hi" inside a
    # live flow does not erase progress.
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

    # After account load, recompute required fields dynamically to support
    # out-of-order name/DOB provided before lookup completes.
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

        # Balance is revealed only after full identity verification succeeds.
        balance = deps.state.outstanding_balance()
        if balance is not None and balance <= Decimal("0") and not settings.agent_policy.allow_zero_balance_payment:
            deps.state.mark_closed()
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

    # Failed verification counts as an attempt even when only one piece of the
    # pair was wrong.
    deps.state.verification_attempts += 1
    attempts_remaining = settings.agent_policy.verification_max_attempts - deps.state.verification_attempts

    account = deps.state.account

    if account is not None and deps.state.provided_full_name == account.full_name:
        # If the full name matched, keep it and retry only the secondary factor
        # to avoid re-asking known-correct information.
        clear_secondary_identity_inputs(deps)
    else:
        # If the full name may be wrong, clear all identity inputs and restart
        # from full name.
        clear_identity_inputs(deps)

    if attempts_remaining <= 0:
        deps.state.mark_closed()
        clear_payment_secrets(deps)

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


def prepare_payment(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="prepare_payment")

    decision = PREPARE_PAYMENT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        deps.state.payment_confirmed = False
        return policy_blocked(deps, operation, decision)

    # Payment preparation only creates a confirmation prompt; no money movement
    # happens here.
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

    # User confirmation flips state only after prepare-payment policy is rechecked.
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
    # This is the only node that calls the payment API. Any premature payment bug
    # should be debugged here and in PROCESS_PAYMENT_POLICY.
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
        clear_payment_secrets(deps)

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
        # Close on ambiguous service failures so the agent does not double-charge
        # or retry an unknown payment state.
        deps.state.mark_closed()
        clear_payment_secrets(deps)

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

    # User-fixable API errors clear only the affected field so the user can retry
    # without re-entering everything.
    if payment_result.error_code in AMOUNT_RETRY_ERRORS:
        deps.state.payment_amount = None

    if payment_result.error_code in {PaymentsAPIErrorCode.INVALID_CARD, None}:
        deps.state.card_number = None

    if payment_result.error_code in {PaymentsAPIErrorCode.INVALID_CVV, None}:
        deps.state.cvv = None

    if payment_result.error_code == PaymentsAPIErrorCode.INVALID_EXPIRY:
        deps.state.expiry_month = None
        deps.state.expiry_year = None

    attempts_remaining = settings.agent_policy.payment_max_attempts - deps.state.payment_attempts

    if attempts_remaining <= 0:
        # Payment retries are capped; after exhaustion, clear secrets and close
        # with no successful transaction.
        deps.state.mark_closed()
        clear_payment_secrets(deps)

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

    # Final recap uses safe facts only and clears payment secrets before marking
    # the session closed.
    deps.state.mark_closed()
    clear_payment_secrets(deps)

    return result(
        deps,
        operation,
        ok=True,
        status="conversation_closed",
        facts=facts,
    )
