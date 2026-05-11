# Design Rationale

This document explains the final design choices behind SettleSentry as a governed payment collection agent. It focuses on stable product decisions rather than planning notes.

## Why deterministic payment authority stays outside the LLM

Payment collection is a regulated workflow with strict sequencing requirements. The system therefore keeps payment authority in deterministic workflow operations and policy checks, not in model free-form reasoning.

Core rules enforced deterministically:
- identity verification must succeed before balance disclosure
- amount must be valid before card collection
- explicit confirmation is required before payment execution
- payment success can be claimed only with a transaction ID

The LLM can help with language understanding and response quality, but it does not decide authorization-sensitive transitions.

## Why four modes were implemented

The four modes create an intentional capability ladder and an ablation framework:
- `deterministic-workflow`: baseline for reproducibility and safety verification
- `llm-parser-workflow`: improves extraction while preserving deterministic response generation
- `llm-parser-responder-workflow`: improves extraction and response naturalness with deterministic fallback
- `llm-autonomous-agent`: evaluates LLM-led orchestration under constrained tool access

This structure makes performance and safety tradeoffs measurable rather than subjective.

## Why autonomous mode uses phase-scoped tools

Autonomous mode is constrained by workflow phase so the model cannot access irrelevant or unsafe actions at the wrong time.

Examples:
- payment-processing tools are unavailable before verification, amount validation, and card readiness
- final confirmation tools are separated from earlier preparation actions
- correction paths are explicit and phase-safe (for example, amount correction re-routes to validation and reconfirmation)

This keeps autonomous behavior useful while preserving policy determinism.

## Why evaluation checks intermediate workflow decisions

Final success alone is insufficient for a payment agent. Evaluation must also verify control decisions turn by turn.

Key checks include:
- no balance disclosure before verification
- no payment API call before explicit confirmation
- correct recovery behavior on lookup, verification, amount, and card failures
- correction behavior resets confirmation when required
- privacy-safe user responses and logs
- safe closure when continuation is invalid or ambiguous

This ensures the agent is not only effective, but also governed and auditable.

## Why raw card handling is simulation-only

The repository includes raw card-field collection only to simulate end-to-end control flow and failure handling in a contained prototype context.

Production deployments should replace raw card handling with PCI-aligned provider patterns such as hosted fields, tokenization, or gateway handoff. The current implementation still enforces strict cleanup and privacy controls in simulation:
- full card number and CVV are never exposed in user responses
- sensitive fields are redacted in logs
- card data is cleared after success, cancellation, or terminal closure

This preserves realistic workflow validation without presenting raw card handling as a production recommendation.

## Safety Invariants

The following invariants must hold in every mode:
- no balance disclosure before successful identity verification
- no card collection before valid amount capture
- no payment execution before explicit confirmation
- no success message without transaction ID
- no continuation after terminal closure

These invariants are enforced by deterministic state transitions and policy gates, not by prompt-only instructions.

## Tool-Call Timing Guarantees

Tool invocation is state-dependent, not intent-only:
- account lookup tools run only when an account ID is available
- identity tools run only in verification flow
- payment-processing tools remain unavailable until all prerequisites are satisfied
- confirmation handling cannot bypass amount and card readiness checks

This timing model prevents premature side effects and improves traceability.

## Failure and Recovery Semantics

Failure handling is designed to be explicit and bounded:
- recoverable failures request the next corrective input
- repeated verification/payment failures close safely when limits are reached
- ambiguous service failures prefer safe closure over unsafe continuation
- corrections (for example amount changes) re-enter the required validation path

This makes recovery behavior predictable and auditable.

## Privacy Boundary Model

SettleSentry uses layered privacy boundaries:
- user responses: only safe, policy-approved facts
- logs and traces: redacted sensitive values
- autonomous runtime context: privacy-safe memory payloads
- terminal states: card secrets cleared after success/cancel/failure closure

This separates conversation utility from sensitive data exposure.

## Determinism vs LLM Tradeoffs

The architecture intentionally separates language flexibility from authority:
- LLM components improve extraction, phrasing, and constrained orchestration
- deterministic operations own verification, authorization, and external payment calls
- fallback paths preserve continuity when LLM output is unsafe, incomplete, or unavailable

This gives measurable UX gains without weakening control guarantees.

## Productionization Boundary (Out of Scope for Prototype)

The prototype validates workflow control, not full payment production readiness.

Production deployments should add:
- PCI-aligned card handling (tokenization/hosted fields/provider handoff)
- durable session storage and operational observability
- stronger fraud controls and human review workflows where needed
- secrets management and environment hardening

These are intentionally excluded from the prototype scope.

## Evaluation Contract and Release Gates

Release readiness requires both functional and control integrity:
- interface contract compliance (`Agent.next(user_input: str) -> {"message": str}`)
- zero premature payment calls
- zero sensitive-value leaks in user-facing responses
- confirmation-gate and amount-guardrail correctness
- safe closure on terminal scenarios

A run is considered release-ready only when these control gates pass consistently across selected modes and scenarios.
