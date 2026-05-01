from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.parsing.base import ParserContext
from settlesentry.agent.policy import (
    LOOKUP_ACCOUNT_POLICY,
    PREPARE_PAYMENT_POLICY,
    PROCESS_PAYMENT_POLICY,
    VALIDATE_PAYMENT_AMOUNT_POLICY,
    VERIFY_IDENTITY_POLICY,
    PolicyDecision,
    identity_matches_account,
)
from settlesentry.agent.response.messages import ResponseContext
from settlesentry.agent.state import ConversationStep, ExtractedUserInput, SafeConversationState
from settlesentry.agent.workflow.result import AgentToolResult
from settlesentry.agent.workflow.routing import (
    expected_fields,
    recommended_node,
    required_fields,
    required_fields_for_policy_reason,
    set_step_from_required_fields,
)
from settlesentry.core import OperationLogContext, get_logger, settings
from settlesentry.integrations.payments.schemas import PaymentsAPIErrorCode

logger = get_logger("AgentNodes")


# Side questions should answer briefly and preserve the pending workflow state.
SIDE_QUESTION_INTENTS = {
    UserIntent.ASK_AGENT_IDENTITY,
    UserIntent.ASK_AGENT_CAPABILITY,
    UserIntent.ASK_CURRENT_STATUS,
    UserIntent.ASK_TO_REPEAT,
}

# Lightweight correction detector. Parser may miss correction intent, so this
# catches common natural-language corrections.
CORRECTION_TOKENS = (
    "correct",
    "correction",
    "change",
    "update",
    "actually",
    "mistake",
    "wrong",
    "typo",
    "edit",
)

CORRECTABLE_FIELDS = (
    "account_id",
    "full_name",
    "dob",
    "aadhaar_last4",
    "pincode",
    "payment_amount",
    "cardholder_name",
    "card_number",
    "cvv",
    "expiry_month",
    "expiry_year",
)

# Lookup service failures are mapped away from payment failures because payment
# has not started yet.
LOOKUP_SERVICE_ERROR_STATUSES = {
    "invalid_response",
    "unexpected_status",
    "network_error",
    "timeout",
}

# Terminal payment errors are not auto-retried by the agent because payment
# status may be ambiguous.
TERMINAL_PAYMENT_SERVICE_ERRORS = {
    PaymentsAPIErrorCode.NETWORK_ERROR,
    PaymentsAPIErrorCode.TIMEOUT,
    PaymentsAPIErrorCode.INVALID_RESPONSE,
    PaymentsAPIErrorCode.UNEXPECTED_STATUS,
}

AMOUNT_RETRY_ERRORS = {
    PaymentsAPIErrorCode.INVALID_AMOUNT,
    PaymentsAPIErrorCode.INSUFFICIENT_BALANCE,
}


def safe_state_summary(deps: AgentDeps) -> SafeConversationState:
    return deps.state.safe_view(session_id=deps.session_id)


def submit_user_input_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    deps: AgentDeps = graph_state["deps"]
    user_input: str = graph_state.get("user_input", "")

    return {"last_result": submit_user_input(deps, user_input)}


def greet_user_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {"last_result": greet_user(graph_state["deps"])}


def lookup_account_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {"last_result": lookup_account(graph_state["deps"])}


def verify_identity_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {"last_result": verify_identity(graph_state["deps"])}


def prepare_payment_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {"last_result": prepare_payment(graph_state["deps"])}


def confirm_payment_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {"last_result": confirm_payment(graph_state["deps"], confirmed=True)}


def process_payment_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {"last_result": process_payment(graph_state["deps"])}


def recap_and_close_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {"last_result": recap_and_close(graph_state["deps"])}


def response_node(graph_state: dict[str, Any]) -> dict[str, Any]:
    deps: AgentDeps = graph_state["deps"]
    result: AgentToolResult | None = graph_state.get("last_result")

    context = response_context(deps, result)
    message = deps.responder.generate(context)

    return {"final_response": message}


def greet_user(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="greet_user")

    if deps.state.completed:
        return _result(deps, operation, ok=False, status="conversation_closed")

    # Greeting always resets only the step, not state, so repeated "hi" inside a
    # live flow does not erase progress.
    deps.state.step = ConversationStep.WAITING_FOR_ACCOUNT_ID

    return _result(
        deps,
        operation,
        ok=True,
        status="greeting",
        required_fields=("account_id",),
    )


def submit_user_input(deps: AgentDeps, user_input: str) -> AgentToolResult:
    # Main input ingestion node: parse, handle side/cancel/correction, merge
    # state, then decide next tool.
    operation = OperationLogContext(operation="submit_user_input")

    if deps.state.completed:
        return _result(deps, operation, ok=False, status="conversation_closed")

    current_expected_fields = expected_fields(deps)

    context = ParserContext.from_state(
        deps.state,
        expected_fields=current_expected_fields,
    )

    raw_lower = user_input.lower()
    correction_requested = any(token in raw_lower for token in CORRECTION_TOKENS)

    try:
        extracted = deps.parser.extract(user_input, context=context)

    except ValidationError as exc:
        return _result(
            deps,
            operation,
            ok=False,
            status="invalid_user_input",
            required_fields=current_expected_fields,
            facts={"error_type": type(exc).__name__},
        )

    if correction_requested and extracted.intent != UserIntent.CANCEL:
        # Corrections are forced from raw text because LLM/parser outputs may
        # classify them as ordinary field updates.
        extracted = extracted.model_copy(
            update={
                "intent": UserIntent.CORRECT_PREVIOUS_DETAIL,
                "proposed_action": ProposedAction.HANDLE_CORRECTION,
            }
        )

    if extracted.intent == UserIntent.CANCEL or extracted.proposed_action == ProposedAction.CANCEL:
        deps.state.mark_closed()
        _clear_payment_secrets(deps)
        return _result(deps, operation, ok=True, status="cancelled")

    if extracted.intent in SIDE_QUESTION_INTENTS:
        # Do not merge side-question text into state; return current required
        # fields so the responder can continue the flow.
        facts: dict[str, object] = {}

        if deps.state.verified:
            balance = deps.state.outstanding_balance()
            if balance is not None:
                facts["balance"] = str(balance)

        return _result(
            deps,
            operation,
            ok=True,
            status=extracted.intent.value,
            required_fields=required_fields(deps),
            facts=facts,
        )

    if extracted.intent == UserIntent.CORRECT_PREVIOUS_DETAIL:
        return handle_correction(deps, extracted)

    confirmation_received = extracted.confirmation is True
    confirmation_expected = "confirmation" in current_expected_fields

    # Normal user data enters state here. Merge does not clear previous values
    # unless a specific branch does so later. Confirmation is intentionally not
    # merged here because confirm_payment owns the final confirmation flip.
    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    if extracted.payment_amount is not None and deps.state.verified:
        # Amount is validated immediately after capture so card details are never
        # collected for an invalid amount.
        blocked = _validate_payment_amount(deps, operation)
        if blocked is not None:
            return blocked

    if confirmation_received and confirmation_expected:
        # Confirmation is actionable only when the workflow was already waiting
        # for confirmation. Early "yes" replies must not advance payment.
        deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION
        return _result(
            deps,
            operation,
            ok=True,
            status="confirmation_received",
            recommended_tool="confirm_payment",
        )

    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    return _result(
        deps,
        operation,
        ok=True,
        status="input_captured",
        required_fields=fields,
        recommended_tool=node,
    )


def handle_correction(deps: AgentDeps, extracted: ExtractedUserInput) -> AgentToolResult:
    # Corrections deliberately reset downstream state. Earlier corrected fields
    # can invalidate verification/payment readiness.
    operation = OperationLogContext(operation="handle_correction")

    if deps.state.completed:
        return _result(deps, operation, ok=False, status="conversation_closed")

    provided_fields = [field for field in CORRECTABLE_FIELDS if getattr(extracted, field) is not None]

    if not provided_fields:
        return _result(
            deps,
            operation,
            ok=True,
            status="correction_requested",
            required_fields=required_fields(deps),
        )

    account_changed = extracted.account_id is not None and extracted.account_id != deps.state.account_id
    identity_changed = any(
        getattr(extracted, field) is not None for field in ("full_name", "dob", "aadhaar_last4", "pincode")
    )
    payment_amount_changed = extracted.payment_amount is not None
    card_changed = any(
        getattr(extracted, field) is not None
        for field in ("cardholder_name", "card_number", "cvv", "expiry_month", "expiry_year")
    )

    if account_changed:
        # Account change invalidates every downstream fact because account data,
        # verification, and payment amount belong to the old account.
        _clear_account_context(deps)

    if identity_changed:
        # Identity correction invalidates verification and payment context;
        # amount/card collection must restart after re-verification.
        deps.state.verified = False
        _clear_payment_context(deps)

    if payment_amount_changed or card_changed:
        # Payment/card corrections require reconfirmation but do not affect
        # identity verification.
        deps.state.payment_confirmed = False

    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    if payment_amount_changed and deps.state.verified:
        blocked = _validate_payment_amount(deps, operation)
        if blocked is not None:
            return blocked

    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    return _result(
        deps,
        operation,
        ok=True,
        status="correction_applied",
        required_fields=fields,
        recommended_tool=node,
        facts={"corrected_fields": provided_fields},
    )


def lookup_account(deps: AgentDeps) -> AgentToolResult:
    operation = OperationLogContext(operation="lookup_account")

    decision = LOOKUP_ACCOUNT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return _policy_blocked(deps, operation, decision)

    deps.state.step = ConversationStep.LOOKING_UP_ACCOUNT
    result = deps.payments_client.lookup_account(deps.state.account_id or "")

    if not result.ok or result.account is None:
        deps.state.account = None
        deps.state.last_error = result.message
        deps.state.step = ConversationStep.WAITING_FOR_ACCOUNT_ID

        status = result.error_code.value if result.error_code else "account_lookup_failed"

        if status in LOOKUP_SERVICE_ERROR_STATUSES:
            status = "account_lookup_failed"

        return _result(
            deps,
            operation,
            ok=False,
            status=status,
            required_fields=("account_id",),
            facts={"reason": result.message},
        )

    deps.state.account = result.account

    # After account load, recompute required fields dynamically to support
    # out-of-order name/DOB provided before lookup completes.
    fields = required_fields(deps)
    node = recommended_node(deps)
    set_step_from_required_fields(deps, fields)

    return _result(
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
        return _policy_blocked(deps, operation, decision)

    if identity_matches_account(deps.state):
        deps.state.verified = True

        # Balance is revealed only after full identity verification succeeds.
        balance = deps.state.outstanding_balance()
        if balance is not None and balance <= Decimal("0") and not settings.agent_policy.allow_zero_balance_payment:
            deps.state.mark_closed()
            return _result(
                deps,
                operation,
                ok=True,
                status="zero_balance",
                facts={"balance": str(balance)},
            )

        deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_AMOUNT

        return _result(
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
        _clear_secondary_identity_inputs(deps)
    else:
        # If the full name may be wrong, clear all identity inputs and restart
        # from full name.
        _clear_identity_inputs(deps)

    if attempts_remaining <= 0:
        deps.state.mark_closed()
        _clear_payment_secrets(deps)

        return _result(
            deps,
            operation,
            ok=False,
            status="verification_exhausted",
            facts={"attempts_remaining": attempts_remaining},
        )

    fields = required_fields(deps)
    set_step_from_required_fields(deps, fields)

    return _result(
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
        return _policy_blocked(deps, operation, decision)

    # Payment preparation only creates a confirmation prompt; no money movement
    # happens here.
    deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION

    return _result(
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
        return _result(
            deps,
            operation,
            ok=False,
            status="payment_not_confirmed",
            required_fields=("confirmation",),
        )

    decision = PREPARE_PAYMENT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return _policy_blocked(deps, operation, decision)

    # User confirmation flips state only after prepare-payment policy is rechecked.
    deps.state.payment_confirmed = True
    deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION

    return _result(
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
        return _policy_blocked(deps, operation, decision)

    deps.state.step = ConversationStep.PROCESSING_PAYMENT
    result = deps.payments_client.process_payment(deps.state.build_payment_request())

    deps.state.payment_attempts += 1
    deps.state.payment_confirmed = False
    deps.state.last_error = result.message

    if result.ok:
        deps.state.transaction_id = result.transaction_id
        deps.state.step = ConversationStep.PAYMENT_SUCCESS
        _clear_payment_secrets(deps)

        return _result(
            deps,
            operation,
            ok=True,
            status="payment_success",
            recommended_tool="recap_and_close",
            facts={
                "amount": str(deps.state.payment_amount),
                "transaction_id": result.transaction_id,
            },
        )

    if result.error_code in TERMINAL_PAYMENT_SERVICE_ERRORS:
        # Close on ambiguous service failures so the agent does not double-charge
        # or retry an unknown payment state.
        deps.state.mark_closed()
        _clear_payment_secrets(deps)

        return _result(
            deps,
            operation,
            ok=False,
            status=result.error_code.value if result.error_code else "payment_failed",
            facts={
                "reason": result.message,
                "attempts_remaining": settings.agent_policy.payment_max_attempts - deps.state.payment_attempts,
            },
        )

    # User-fixable API errors clear only the affected field so the user can retry
    # without re-entering everything.
    if result.error_code in AMOUNT_RETRY_ERRORS:
        deps.state.payment_amount = None

    if result.error_code in {PaymentsAPIErrorCode.INVALID_CARD, None}:
        deps.state.card_number = None

    if result.error_code in {PaymentsAPIErrorCode.INVALID_CVV, None}:
        deps.state.cvv = None

    if result.error_code == PaymentsAPIErrorCode.INVALID_EXPIRY:
        deps.state.expiry_month = None
        deps.state.expiry_year = None

    attempts_remaining = settings.agent_policy.payment_max_attempts - deps.state.payment_attempts

    if attempts_remaining <= 0:
        # Payment retries are capped; after exhaustion, clear secrets and close
        # with no successful transaction.
        deps.state.mark_closed()
        _clear_payment_secrets(deps)

        return _result(
            deps,
            operation,
            ok=False,
            status="payment_attempts_exhausted",
            facts={"reason": result.message, "attempts_remaining": attempts_remaining},
        )

    fields = required_fields(deps)
    set_step_from_required_fields(deps, fields)

    return _result(
        deps,
        operation,
        ok=False,
        status=result.error_code.value if result.error_code else "payment_failed",
        required_fields=fields,
        facts={
            "reason": result.message,
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
    _clear_payment_secrets(deps)

    return _result(
        deps,
        operation,
        ok=True,
        status="conversation_closed",
        facts=facts,
    )


def response_context(deps: AgentDeps, result: AgentToolResult | None) -> ResponseContext:
    if result is None:
        return ResponseContext(
            status="unknown",
            required_fields=required_fields(deps),
            facts={},
            safe_state=safe_state_summary(deps),
        )

    return ResponseContext(
        status=result.status,
        required_fields=result.required_fields,
        facts=result.facts,
        safe_state=result.safe_state,
    )


def _validate_payment_amount(
    deps: AgentDeps,
    operation: OperationLogContext,
) -> AgentToolResult | None:
    decision = VALIDATE_PAYMENT_AMOUNT_POLICY.evaluate(deps.state)

    if decision.allowed:
        return None

    deps.state.payment_amount = None
    deps.state.payment_confirmed = False

    return _policy_blocked(deps, operation, decision)


def _result(
    deps: AgentDeps,
    operation: OperationLogContext,
    *,
    ok: bool,
    status: str,
    required_fields: tuple[str, ...] = (),
    recommended_tool: str | None = None,
    facts: dict | None = None,
) -> AgentToolResult:
    result = AgentToolResult(
        ok=ok,
        status=status,
        required_fields=required_fields,
        recommended_tool=recommended_tool,
        facts=facts or {},
        safe_state=safe_state_summary(deps),
    )

    logger.info(
        "agent_node_completed",
        extra=operation.completed_extra(
            session_id=deps.session_id,
            node_name=operation.operation,
            step=deps.state.step.value,
            ok=result.ok,
            status=result.status,
            required_fields=",".join(result.required_fields) if result.required_fields else None,
            recommended_tool=result.recommended_tool,
        ),
    )

    return result


def _policy_blocked(
    deps: AgentDeps,
    operation: OperationLogContext,
    decision: PolicyDecision,
) -> AgentToolResult:
    # Policy failures are converted into required fields so the responder can
    # give a specific next action.
    fields = required_fields_for_policy_reason(deps, decision.reason)
    set_step_from_required_fields(deps, fields)

    return _result(
        deps,
        operation,
        ok=False,
        status=decision.reason.value,
        required_fields=fields,
        facts={
            "policy_reason": decision.reason.value,
            "failed_rule": decision.failed_rule,
        },
    )


def _clear_identity_inputs(deps: AgentDeps) -> None:
    deps.state.provided_full_name = None
    deps.state.provided_dob = None
    deps.state.provided_aadhaar_last4 = None
    deps.state.provided_pincode = None


def _clear_secondary_identity_inputs(deps: AgentDeps) -> None:
    deps.state.provided_dob = None
    deps.state.provided_aadhaar_last4 = None
    deps.state.provided_pincode = None


def _clear_payment_secrets(deps: AgentDeps) -> None:
    # Clear raw card number and CVV whenever payment completes, fails terminally,
    # or the session closes.
    deps.state.card_number = None
    deps.state.cvv = None


def _clear_payment_context(deps: AgentDeps) -> None:
    # Clears all downstream payment fields when identity/account context changes.
    deps.state.payment_amount = None
    deps.state.cardholder_name = None
    deps.state.card_number = None
    deps.state.cvv = None
    deps.state.expiry_month = None
    deps.state.expiry_year = None
    deps.state.payment_confirmed = False
    deps.state.transaction_id = None


def _clear_account_context(deps: AgentDeps) -> None:
    # Account correction resets the entire downstream workflow because all prior
    # verification/payment data belonged to the old account.
    deps.state.account = None
    deps.state.verified = False
    deps.state.verification_attempts = 0
    deps.state.provided_full_name = None
    deps.state.provided_dob = None
    deps.state.provided_aadhaar_last4 = None
    deps.state.provided_pincode = None
    _clear_payment_context(deps)
    deps.state.last_error = None
