from __future__ import annotations

from settlesentry.agent.deps import AgentDeps
from settlesentry.agent.policy import VALIDATE_PAYMENT_AMOUNT_POLICY, PolicyDecision
from settlesentry.agent.response.messages import ResponseContext
from settlesentry.agent.state import SafeConversationState
from settlesentry.agent.workflow.result import AgentToolResult
from settlesentry.agent.workflow.routing import (
    required_fields,
    required_fields_for_policy_reason,
    set_step_from_required_fields,
)
from settlesentry.core import OperationLogContext, get_logger

logger = get_logger("AgentOperations")


def safe_state_summary(deps: AgentDeps) -> SafeConversationState:
    return deps.state.safe_view(session_id=deps.session_id)


def response_context(deps: AgentDeps, node_result: AgentToolResult | None) -> ResponseContext:
    if node_result is None:
        return ResponseContext(
            status="unknown",
            required_fields=required_fields(deps),
            facts={},
            safe_state=safe_state_summary(deps),
        )

    return ResponseContext(
        status=node_result.status,
        required_fields=node_result.required_fields,
        facts=node_result.facts,
        safe_state=node_result.safe_state,
    )


def validate_payment_amount(
    deps: AgentDeps,
    operation: OperationLogContext,
) -> AgentToolResult | None:
    decision = VALIDATE_PAYMENT_AMOUNT_POLICY.evaluate(deps.state)

    if decision.allowed:
        return None

    deps.state.payment_amount = None
    deps.state.payment_confirmed = False

    return policy_blocked(deps, operation, decision)


def result(
    deps: AgentDeps,
    operation: OperationLogContext,
    *,
    ok: bool,
    status: str,
    required_fields: tuple[str, ...] = (),
    recommended_tool: str | None = None,
    facts: dict | None = None,
) -> AgentToolResult:
    node_result = AgentToolResult(
        ok=ok,
        status=status,
        required_fields=required_fields,
        recommended_tool=recommended_tool,
        facts=facts or {},
        safe_state=safe_state_summary(deps),
    )

    logger.info(
        "agent_operation_completed",
        extra=operation.completed_extra(
            session_id=deps.session_id,
            node_name=operation.operation,
            step=deps.state.step.value,
            ok=node_result.ok,
            status=node_result.status,
            required_fields=",".join(node_result.required_fields) if node_result.required_fields else None,
            recommended_tool=node_result.recommended_tool,
        ),
    )

    return node_result


def policy_blocked(
    deps: AgentDeps,
    operation: OperationLogContext,
    decision: PolicyDecision,
) -> AgentToolResult:
    # Convert policy failures into actionable required fields.
    fields = required_fields_for_policy_reason(deps, decision.reason)
    set_step_from_required_fields(deps, fields)

    return result(
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


def clear_identity_inputs(deps: AgentDeps) -> None:
    deps.state.provided_full_name = None
    deps.state.provided_dob = None
    deps.state.provided_aadhaar_last4 = None
    deps.state.provided_pincode = None


def clear_secondary_identity_inputs(deps: AgentDeps) -> None:
    deps.state.provided_dob = None
    deps.state.provided_aadhaar_last4 = None
    deps.state.provided_pincode = None


def clear_payment_secrets(deps: AgentDeps) -> None:
    # Minimal scrub utility; prefer clear_card_details for terminal cleanup.
    deps.state.card_number = None
    deps.state.cvv = None


def clear_payment_context(deps: AgentDeps) -> None:
    # Reset payment context when upstream identity/account data changes.
    deps.state.payment_amount = None
    deps.state.cardholder_name = None
    deps.state.card_number = None
    deps.state.cvv = None
    deps.state.expiry_month = None
    deps.state.expiry_year = None
    deps.state.payment_confirmed = False
    deps.state.transaction_id = None


def clear_account_context(deps: AgentDeps) -> None:
    # Account corrections invalidate all downstream verification/payment context.
    deps.state.account = None
    deps.state.verified = False
    deps.state.verification_attempts = 0
    deps.state.provided_full_name = None
    deps.state.provided_dob = None
    deps.state.provided_aadhaar_last4 = None
    deps.state.provided_pincode = None
    clear_payment_context(deps)
    deps.state.last_error = None


def clear_card_details(deps: AgentDeps) -> None:
    deps.state.cardholder_name = None
    deps.state.card_number = None
    deps.state.cvv = None
    deps.state.expiry_month = None
    deps.state.expiry_year = None
    deps.state.payment_confirmed = False
