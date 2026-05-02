from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto

from pydantic import BaseModel

from settlesentry.agent.state import ConversationState
from settlesentry.core import get_logger

logger = get_logger("AgentPolicy")


class PolicyReason(StrEnum):
    ALLOWED = auto()

    CONVERSATION_CLOSED = auto()

    MISSING_ACCOUNT_ID = auto()
    ACCOUNT_ALREADY_LOADED = auto()
    ACCOUNT_NOT_LOADED = auto()

    MISSING_FULL_NAME = auto()
    MISSING_SECONDARY_FACTOR = auto()
    IDENTITY_NOT_VERIFIED = auto()
    VERIFICATION_ATTEMPTS_EXHAUSTED = auto()

    ZERO_BALANCE = auto()
    MISSING_PAYMENT_AMOUNT = auto()
    INVALID_PAYMENT_AMOUNT = auto()
    AMOUNT_EXCEEDS_BALANCE = auto()
    AMOUNT_EXCEEDS_POLICY_LIMIT = auto()

    MISSING_CARD_FIELDS = auto()
    INVALID_PAYMENT_REQUEST = auto()
    PAYMENT_NOT_CONFIRMED = auto()
    PAYMENT_ATTEMPTS_EXHAUSTED = auto()
    PARTIAL_PAYMENT_NOT_ALLOWED = auto()


class PolicyDecision(BaseModel):
    allowed: bool
    reason: PolicyReason
    failed_rule: str | None = None
    message: str | None = None

    @classmethod
    def allow(cls) -> "PolicyDecision":
        return cls(
            allowed=True,
            reason=PolicyReason.ALLOWED,
        )

    @classmethod
    def deny(
        cls,
        reason: PolicyReason,
        message: str | None = None,
    ) -> "PolicyDecision":
        return cls(
            allowed=False,
            reason=reason,
            message=message,
        )


PolicyCheck = Callable[[ConversationState], PolicyDecision]


@dataclass(frozen=True)
class PolicyRule:
    name: str
    check: PolicyCheck


@dataclass(frozen=True)
class PolicySet:
    name: str
    rules: tuple[PolicyRule, ...]

    def _log_decision(self, state: ConversationState, decision: PolicyDecision) -> None:
        log = logger.debug if decision.allowed else logger.info
        log(
            "policy_decision",
            extra={
                "policy_name": self.name,
                "allowed": decision.allowed,
                "reason": decision.reason.value,
                "failed_rule": decision.failed_rule,
            },
        )

    def evaluate(self, state: ConversationState) -> PolicyDecision:
        # Policies are ordered guardrails. First failing rule determines both the
        # status and the next required field.
        for rule in self.rules:
            decision = rule.check(state)

            if not decision.allowed:
                resolved = decision
                if resolved.failed_rule is None:
                    resolved = resolved.model_copy(update={"failed_rule": rule.name})

                self._log_decision(state, resolved)
                return resolved

        allowed = PolicyDecision.allow()
        self._log_decision(state, allowed)
        return allowed


__all__ = [
    "PolicyReason",
    "PolicyDecision",
    "PolicyCheck",
    "PolicyRule",
    "PolicySet",
    "logger",
]
