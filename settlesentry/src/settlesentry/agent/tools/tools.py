from __future__ import annotations

from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from settlesentry.agent.actions import ProposedAction, UserIntent
from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.parsers.base import ExpectedField, ParserContext
from settlesentry.agent.policy import (
    LOOKUP_ACCOUNT_POLICY,
    PREPARE_PAYMENT_POLICY,
    PROCESS_PAYMENT_POLICY,
    VERIFY_IDENTITY_POLICY,
    PolicyDecision,
    PolicyReason,
    identity_matches_account,
)
from settlesentry.agent.state import ConversationStep, SafeConversationState
from settlesentry.agent.tools.models import AgentToolResult
from settlesentry.core import OperationLogContext, get_logger, settings

logger = get_logger("AgentTools")
payment_collection_toolset = FunctionToolset()


def safe_state_summary(deps: AgentDeps) -> SafeConversationState:
    return deps.state.safe_view(session_id=deps.session_id)


@payment_collection_toolset.tool
def greet_user(ctx: RunContext[AgentDeps]) -> AgentToolResult:
    """
    Start the payment collection flow and ask for account ID.
    """
    deps = ctx.deps
    operation = OperationLogContext(operation="greet_user")

    if deps.state.completed:
        return _result(deps, operation, ok=False, status="conversation_closed")

    if deps.state.step == ConversationStep.START:
        deps.state.step = ConversationStep.WAITING_FOR_ACCOUNT_ID

    return _result(
        deps,
        operation,
        ok=True,
        status="greeting",
        required_fields=("account_id",),
    )


@payment_collection_toolset.tool
def submit_user_input(
    ctx: RunContext[AgentDeps],
    user_input: str,
) -> AgentToolResult:
    """
    Capture user input into state.

    The LLM must pass only the raw user_input. Field extraction is handled by
    the deterministic parser using the current expected state.
    """
    deps = ctx.deps
    operation = OperationLogContext(operation="submit_user_input")

    if deps.state.completed:
        return _result(deps, operation, ok=False, status="conversation_closed")

    context = ParserContext.from_state(
        deps.state,
        expected_fields=_expected_fields(deps),
    )

    try:
        extracted = deps.parser.extract(user_input, context=context)

    except ValidationError as exc:
        return _result(
            deps,
            operation,
            ok=False,
            status="invalid_user_input",
            required_fields=_expected_fields(deps),
            facts={"error_type": type(exc).__name__},
        )

    if extracted.intent == UserIntent.CANCEL or extracted.proposed_action == ProposedAction.CANCEL:
        deps.state.mark_closed()
        _clear_payment_secrets(deps)
        return _result(deps, operation, ok=True, status="cancelled")

    confirmation_received = extracted.confirmation is True

    # Confirmation is handled by confirm_payment(), not by state.merge().
    deps.state.merge(extracted.model_copy(update={"confirmation": None}))

    if confirmation_received:
        deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION
        return _result(
            deps,
            operation,
            ok=True,
            status="confirmation_received",
            recommended_tool="confirm_payment",
        )

    required_fields = _required_fields(deps)
    recommended_tool = _recommended_tool(deps)
    _set_step(deps, required_fields)

    return _result(
        deps,
        operation,
        ok=True,
        status="input_captured",
        required_fields=required_fields,
        recommended_tool=recommended_tool,
    )


@payment_collection_toolset.tool
def lookup_account_if_allowed(ctx: RunContext[AgentDeps]) -> AgentToolResult:
    """
    Look up the account only after lookup policy allows it.
    """
    deps = ctx.deps
    operation = OperationLogContext(operation="lookup_account_if_allowed")

    decision = LOOKUP_ACCOUNT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return _policy_blocked(deps, operation, decision)

    deps.state.step = ConversationStep.LOOKING_UP_ACCOUNT
    result = deps.payments_client.lookup_account(deps.state.account_id or "")

    if not result.ok or result.account is None:
        deps.state.account = None
        deps.state.last_error = result.message
        deps.state.step = ConversationStep.WAITING_FOR_ACCOUNT_ID

        return _result(
            deps,
            operation,
            ok=False,
            status=result.error_code.value if result.error_code else "account_lookup_failed",
            required_fields=("account_id",),
            facts={"reason": result.message},
        )

    deps.state.account = result.account
    deps.state.step = ConversationStep.WAITING_FOR_FULL_NAME

    return _result(
        deps,
        operation,
        ok=True,
        status="account_loaded",
        required_fields=("full_name",),
    )


@payment_collection_toolset.tool
def verify_identity_if_ready(ctx: RunContext[AgentDeps]) -> AgentToolResult:
    """
    Verify identity using strict in-agent matching.
    """
    deps = ctx.deps
    operation = OperationLogContext(operation="verify_identity_if_ready")

    decision = VERIFY_IDENTITY_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return _policy_blocked(deps, operation, decision)

    if identity_matches_account(deps.state):
        deps.state.verified = True
        deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_AMOUNT

        return _result(
            deps,
            operation,
            ok=True,
            status="identity_verified",
            required_fields=("payment_amount",),
            facts={"balance": str(deps.state.outstanding_balance())},
        )

    deps.state.verification_attempts += 1
    attempts_remaining = settings.agent_policy.verification_max_attempts - deps.state.verification_attempts

    _clear_identity_inputs(deps)

    if attempts_remaining <= 0:
        required_fields: tuple[str, ...] = ()
        recommended_tool = "recap_and_close"
        status = "verification_exhausted"
    else:
        deps.state.step = ConversationStep.WAITING_FOR_FULL_NAME
        required_fields = ("full_name",)
        recommended_tool = None
        status = "identity_verification_failed"

    return _result(
        deps,
        operation,
        ok=False,
        status=status,
        required_fields=required_fields,
        recommended_tool=recommended_tool,
        facts={"attempts_remaining": attempts_remaining},
    )


@payment_collection_toolset.tool
def prepare_payment_if_ready(ctx: RunContext[AgentDeps]) -> AgentToolResult:
    """
    Validate payment readiness before asking for confirmation.
    """
    deps = ctx.deps
    operation = OperationLogContext(operation="prepare_payment_if_ready")

    decision = PREPARE_PAYMENT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        deps.state.payment_confirmed = False
        return _policy_blocked(deps, operation, decision)

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


@payment_collection_toolset.tool
def confirm_payment(ctx: RunContext[AgentDeps], confirmed: bool) -> AgentToolResult:
    """
    Record explicit payment confirmation.
    """
    deps = ctx.deps
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

    deps.state.payment_confirmed = True
    deps.state.step = ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION

    return _result(
        deps,
        operation,
        ok=True,
        status="payment_confirmed",
        recommended_tool="process_payment_if_allowed",
    )


@payment_collection_toolset.tool
def process_payment_if_allowed(ctx: RunContext[AgentDeps]) -> AgentToolResult:
    """
    Process payment only after full policy approval.
    """
    deps = ctx.deps
    operation = OperationLogContext(operation="process_payment_if_allowed")

    decision = PROCESS_PAYMENT_POLICY.evaluate(deps.state)

    if not decision.allowed:
        return _policy_blocked(deps, operation, decision)

    deps.state.step = ConversationStep.PROCESSING_PAYMENT
    result = deps.payments_client.process_payment(deps.state.build_payment_request())

    deps.state.payment_attempts += 1
    deps.state.payment_confirmed = False

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

    deps.state.last_error = result.message
    _clear_payment_secrets(deps)

    attempts_remaining = settings.agent_policy.payment_max_attempts - deps.state.payment_attempts

    if attempts_remaining <= 0:
        required_fields: tuple[str, ...] = ()
        recommended_tool = "recap_and_close"
        status = "payment_attempts_exhausted"
    else:
        deps.state.step = ConversationStep.WAITING_FOR_CARD_NUMBER
        required_fields = ("card_number", "cvv", "expiry")
        recommended_tool = None
        status = result.error_code.value if result.error_code else "payment_failed"

    return _result(
        deps,
        operation,
        ok=False,
        status=status,
        required_fields=required_fields,
        recommended_tool=recommended_tool,
        facts={
            "reason": result.message,
            "attempts_remaining": attempts_remaining,
        },
    )


@payment_collection_toolset.tool
def recap_and_close(ctx: RunContext[AgentDeps]) -> AgentToolResult:
    """
    Return safe recap facts and close the conversation.

    This tool must not reveal DOB, Aadhaar, pincode, full card number, or CVV.
    """
    deps = ctx.deps
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

    deps.state.mark_closed()
    _clear_payment_secrets(deps)

    return _result(
        deps,
        operation,
        ok=True,
        status="conversation_closed",
        facts=facts,
    )


@payment_collection_toolset.tool
def cancel_payment_flow(ctx: RunContext[AgentDeps]) -> AgentToolResult:
    """
    Cancel and close the flow. No payment is processed.
    """
    deps = ctx.deps
    operation = OperationLogContext(operation="cancel_payment_flow")

    deps.state.mark_closed()
    _clear_payment_secrets(deps)

    return _result(deps, operation, ok=True, status="cancelled")


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
        message="",
        required_fields=required_fields,
        recommended_tool=recommended_tool,
        facts=facts or {},
        safe_state=safe_state_summary(deps),
    )

    logger.info(
        "agent_tool_completed",
        extra=operation.completed_extra(
            session_id=deps.session_id,
            tool_name=operation.operation,
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
    required_fields = _required_fields_for_policy_reason(deps, decision.reason)
    _set_step(deps, required_fields)

    return _result(
        deps,
        operation,
        ok=False,
        status=decision.reason.value,
        required_fields=required_fields,
        facts={
            "policy_reason": decision.reason.value,
            "failed_rule": decision.failed_rule,
        },
    )


def _expected_fields(deps: AgentDeps) -> tuple[ExpectedField, ...]:
    step = deps.state.step

    if step in {ConversationStep.START, ConversationStep.WAITING_FOR_ACCOUNT_ID}:
        return ("account_id",)

    if step == ConversationStep.WAITING_FOR_FULL_NAME:
        return ("full_name",)

    if step == ConversationStep.WAITING_FOR_SECONDARY_FACTOR:
        return ("dob", "aadhaar_last4", "pincode")

    if step == ConversationStep.WAITING_FOR_PAYMENT_AMOUNT:
        return ("payment_amount",)

    if step == ConversationStep.WAITING_FOR_CARDHOLDER_NAME:
        return ("cardholder_name",)

    if step == ConversationStep.WAITING_FOR_CARD_NUMBER:
        return ("card_number",)

    if step == ConversationStep.WAITING_FOR_CVV:
        return ("cvv",)

    if step == ConversationStep.WAITING_FOR_EXPIRY:
        return ("expiry",)

    if step == ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION:
        return ("confirmation",)

    return ()


def _required_fields(deps: AgentDeps) -> tuple[str, ...]:
    state = deps.state

    if state.completed:
        return ()

    if not state.account_id:
        return ("account_id",)

    if not state.has_account_loaded():
        return ()

    if not state.verified:
        if not state.provided_full_name:
            return ("full_name",)

        if not state.has_secondary_factor():
            return ("dob_or_aadhaar_last4_or_pincode",)

        return ()

    if state.payment_amount is None:
        return ("payment_amount",)

    if not state.cardholder_name:
        return ("cardholder_name",)

    if not state.card_number:
        return ("card_number",)

    if not state.cvv:
        return ("cvv",)

    if not state.expiry_month or not state.expiry_year:
        return ("expiry",)

    if not state.payment_confirmed:
        return ("confirmation",)

    return ()


def _recommended_tool(deps: AgentDeps) -> str | None:
    state = deps.state

    if state.step in {ConversationStep.START, ConversationStep.WAITING_FOR_ACCOUNT_ID} and not state.account_id:
        return "greet_user"

    if state.account_id and not state.has_account_loaded():
        return "lookup_account_if_allowed"

    if state.has_account_loaded() and not state.verified and state.provided_full_name and state.has_secondary_factor():
        return "verify_identity_if_ready"

    if state.verified and state.payment_amount is not None and state.has_complete_card_fields():
        return "process_payment_if_allowed" if state.payment_confirmed else "prepare_payment_if_ready"

    return None


def _required_fields_for_policy_reason(
    deps: AgentDeps,
    reason: PolicyReason,
) -> tuple[str, ...]:
    if reason == PolicyReason.MISSING_ACCOUNT_ID:
        return ("account_id",)

    if reason == PolicyReason.MISSING_FULL_NAME:
        return ("full_name",)

    if reason in {PolicyReason.MISSING_SECONDARY_FACTOR, PolicyReason.IDENTITY_NOT_VERIFIED}:
        return ("dob_or_aadhaar_last4_or_pincode",)

    if reason in {
        PolicyReason.MISSING_PAYMENT_AMOUNT,
        PolicyReason.INVALID_PAYMENT_AMOUNT,
        PolicyReason.AMOUNT_EXCEEDS_BALANCE,
        PolicyReason.AMOUNT_EXCEEDS_POLICY_LIMIT,
        PolicyReason.PARTIAL_PAYMENT_NOT_ALLOWED,
    }:
        return ("payment_amount",)

    if reason == PolicyReason.MISSING_CARD_FIELDS:
        return _missing_card_fields(deps)

    if reason == PolicyReason.INVALID_PAYMENT_REQUEST:
        return ("card_number", "cvv", "expiry")

    if reason == PolicyReason.PAYMENT_NOT_CONFIRMED:
        return ("confirmation",)

    return ()


def _missing_card_fields(deps: AgentDeps) -> tuple[str, ...]:
    state = deps.state
    missing: list[str] = []

    if not state.cardholder_name:
        missing.append("cardholder_name")

    if not state.card_number:
        missing.append("card_number")

    if not state.cvv:
        missing.append("cvv")

    if not state.expiry_month or not state.expiry_year:
        missing.append("expiry")

    return tuple(missing)


def _set_step(deps: AgentDeps, required_fields: tuple[str, ...]) -> None:
    if not required_fields:
        return

    step_by_field = {
        "account_id": ConversationStep.WAITING_FOR_ACCOUNT_ID,
        "full_name": ConversationStep.WAITING_FOR_FULL_NAME,
        "dob_or_aadhaar_last4_or_pincode": ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        "payment_amount": ConversationStep.WAITING_FOR_PAYMENT_AMOUNT,
        "cardholder_name": ConversationStep.WAITING_FOR_CARDHOLDER_NAME,
        "card_number": ConversationStep.WAITING_FOR_CARD_NUMBER,
        "cvv": ConversationStep.WAITING_FOR_CVV,
        "expiry": ConversationStep.WAITING_FOR_EXPIRY,
        "confirmation": ConversationStep.WAITING_FOR_PAYMENT_CONFIRMATION,
    }

    deps.state.step = step_by_field.get(required_fields[0], deps.state.step)


def _clear_identity_inputs(deps: AgentDeps) -> None:
    deps.state.provided_full_name = None
    deps.state.provided_dob = None
    deps.state.provided_aadhaar_last4 = None
    deps.state.provided_pincode = None


def _clear_payment_secrets(deps: AgentDeps) -> None:
    deps.state.card_number = None
    deps.state.cvv = None


__all__ = [
    "payment_collection_toolset",
    "safe_state_summary",
    "submit_user_input",
    "lookup_account_if_allowed",
    "verify_identity_if_ready",
    "prepare_payment_if_ready",
    "confirm_payment",
    "process_payment_if_allowed",
    "recap_and_close",
    "cancel_payment_flow",
    "greet_user",
]
