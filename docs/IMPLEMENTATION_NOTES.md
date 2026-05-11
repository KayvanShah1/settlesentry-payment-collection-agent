# Implementation Notes

This document summarizes the major issues encountered during the development of the SettleSentry payment collection agent, why they happened, how they were fixed, and what each issue taught us about designing regulated conversational agents.

It is written as an engineering handoff/postmortem rather than a commit log. It covers the broader project lifecycle across the deterministic workflow, LLM-assisted modes, evaluator design, safety/redaction work, and the autonomous tool-calling mode.

---

## 1. Reference Specification Scope and Product Semantics

### Issue
Early in the project, the core product semantics needed clarification: what does the provided `balance` mean, when can it be disclosed, and whether payment should be attempted for zero-balance accounts.

### Symptom
There was ambiguity around whether the account balance represented an available balance, outstanding balance, credit card balance, or payable amount.

### Root Cause
The reference specification used a generic `Balance` field in sample account data, but the business flow was a payment collection workflow. Treating balance as a bank/card available balance would create incorrect product behavior.

### Resolution
The system interpreted `balance` as an **outstanding payable amount** and consistently used the phrase **outstanding balance** in customer-facing responses.

Key behavior established:
- Balance is disclosed only after identity verification.
- Payment amount cannot exceed outstanding balance.
- Zero-balance accounts close without collecting payment by default.
- Partial payments are allowed unless policy says otherwise.

### Lesson
For payment/collection systems, ambiguous domain fields must be converted into explicit product semantics before implementation. “Balance” should never remain semantically vague in a payment agent.

---

## 2. Strict Identity Verification Before Balance Disclosure

### Issue
The agent needed to enforce strict identity verification before showing the outstanding balance.

### Symptom
Early workflow design needed to decide whether a name alone was sufficient or whether secondary verification was mandatory.

### Root Cause
The reference specification provided multiple identity fields: full name, DOB, Aadhaar last 4, and pincode. A safe payment collection flow should not disclose balance after only an account ID or name.

### Resolution
Implemented strict two-part verification:
- exact full-name match
- one exact secondary-factor match: DOB, Aadhaar last 4, or pincode

Balance disclosure and payment amount collection are allowed only after successful verification.

### Lesson
Identity verification should be deterministic and auditable. LLMs can extract fields, but they should not decide whether identity is verified.

---

## 3. Mode 1–3 Baseline Architecture

### Issue
Initial implementation needed to satisfy the reference specification while also being easy to explain and evaluate.

### Symptom
The implementation grew several layers: parser, workflow graph, policy checks, response writer, API client, security logging, evaluator, and tests.

### Root Cause
A regulated payment workflow cannot be safely implemented as a free-form chat loop. It needs deterministic state progression, policy gates, and controlled external calls.

### Resolution
Modes 1–3 were built as ablation layers:

| Mode | Description |
|---|---|
| `deterministic-workflow` | deterministic parser + deterministic response writer |
| `llm-parser-workflow` | LLM parser with deterministic fallback + deterministic responses |
| `llm-parser-responder-workflow` | LLM parser + LLM response writer, both bounded by deterministic workflow and fallback |

Core principle:
- LLM helps with language understanding or phrasing.
- Deterministic workflow controls payment authority.

### Lesson
Ablation modes make the architecture easier to evaluate and defend: each additional LLM responsibility can be compared against a stable deterministic baseline.

---

## 4. LangGraph Usage and Refactor Questions

### Issue
There was concern that parts of the workflow had been manually implemented even though LangGraph could simplify orchestration.

### Symptom
Questions came up around whether memory, checkpointing, graph routing, tool-calling, and evaluation should have been handled through LangGraph earlier.

### Root Cause
The initial implementation prioritized correctness and policy control. LangGraph was used for workflow structure, but not yet as a full native agent/tool-calling runtime.

### Resolution
The project evolved toward a clearer split:
- Modes 1–3: graph-controlled workflow with parser/responder variants.
- Mode 4: autonomous LangGraph/PydanticAI-style tool orchestration over guarded operations.

Mode 4 was added as a separate extension path, not a disruptive refactor of the stable workflow modes.

### Lesson
For project prototypes, a stable, explainable baseline is more valuable than prematurely adopting every framework feature. Agentic tool calling can be added as an ablation layer once the deterministic core is correct.

---

## 5. Account ID Retry Bug in Autonomous Mode

### Issue
Autonomous mode failed the invalid-account-then-valid-account recovery path.

### Symptom
Conversation example:

```text
USER: ACC89009
AGENT: account not found
USER: ACC1001
AGENT: account not found again
```

Modes 1–3 behaved correctly, but mode 4 did not.

### Root Cause
The autonomous account tool path treated the second account ID like a correction/input update instead of forcing a fresh lookup. Downstream account context was not consistently reset when the account ID changed.

### Resolution
Updated `provide_account_id()` so that:
- it trims but does not autocorrect account IDs
- changing account ID clears downstream account/identity/payment context
- it immediately calls `lookup_account()` after merging the new account ID
- account-not-found recovery requires a fresh lookup

Later cleanup also cleared failed `account_id` on lookup failure to keep state invariant clean.

### Lesson
Autonomous tools should not rely on generic correction handling for critical identifiers. Account lookup is an external state-changing operation and should be explicit.

---

## 6. Account ID Redaction Policy

### Issue
Redaction behavior changed when account IDs were classified as sensitive in logs.

### Symptom
Tests failed because they expected `account_id=ACC1001` to remain visible in logs and structured payloads.

Failures included:
- logger traceability expected raw account ID
- redaction tests expected account ID to be preserved
- nested payload redaction expected account ID to remain unchanged

### Root Cause
The project policy changed: account IDs are allowed in workflow state for continuity, but should be redacted in logs and serialized diagnostics.

### Resolution
Kept account ID in workflow/safe state where needed, but updated redaction rules/tests so account IDs are masked in logs and diagnostic payloads.

Clarified policy:
- account ID may exist in internal workflow state and LLM-safe context
- account ID is treated as sensitive in logs and redacted by the logging layer

### Lesson
“Safe for workflow state” and “safe for logs” are different boundaries. A field can be operationally necessary but still sensitive in telemetry.

---

## 7. Balance and Verification Leak Guards

### Issue
The agent needed to ensure balance was never revealed before identity verification.

### Symptom
LLM-assisted and autonomous modes could potentially write responses from partial context or hallucinated facts.

### Root Cause
LLMs may phrase responses based on conversational context unless constrained by safe facts and state.

### Resolution
Added safety model:
- safe response context excludes raw identity data
- balance only appears in facts after verification
- autonomous safety audit rejects balance leakage before verification
- response writer cannot claim balance availability unless status/facts support it

### Lesson
In regulated flows, response safety is not only a prompt problem. It must be enforced structurally through safe context and audited output.

---

## 8. Payment Amount Guardrails

### Issue
The agent needed to block invalid payment amounts before collecting card details.

### Symptom
Scenarios tested:
- amount greater than outstanding balance
- invalid amount
- policy limit exceeded
- partial payments allowed or blocked based on configuration

### Root Cause
Without a dedicated amount validation policy, the flow could move to card collection too early.

### Resolution
Added policy-gated `capture_payment_amount()` flow:
- validates positive amount
- checks outstanding balance
- checks policy limits
- clears invalid amount and asks only for corrected amount
- blocks card collection until valid amount is accepted

### Lesson
Payment amount validation must occur before collecting payment instrument details. This reduces unnecessary exposure of card data and prevents unsafe payment setup.

---

## 9. Confirmation Gate and Premature Payment Calls

### Issue
Payment processing must never happen before explicit confirmation.

### Symptom
Evaluator needed to verify:
- card submission is not confirmation
- amount correction is not confirmation
- “yes” only processes payment when confirmation is actually pending
- no payment API calls happen prematurely

### Root Cause
LLM and parser flows can interpret short replies like “yes” or card details incorrectly unless state and expected field are considered.

### Resolution
Built explicit confirmation stage:
- payment preparation only stages confirmation
- processing happens only after explicit yes/confirm
- parser never sets direct `process_payment`
- autonomous mode uses phase-scoped tools so processing tool is unavailable until final confirmation phase

### Lesson
“Ready for payment” and “authorized to process payment” must be separate states.

---

## 10. Card Collection Strategy and Full Card Bundle Reset

### Issue
Card failure recovery initially cleared only the field rejected by the payment API.

### Symptom
For `invalid_card`, the agent asked only for the card number again. Later, the desired behavior changed: any card-detail validation failure should clear all card details and request the full card bundle again.

### Root Cause
Partial retry was more complex for autonomous tool-calling and could leave stale cardholder/expiry/CVV context.

### Resolution
Changed retry policy:
- invalid card, CVV, or expiry clears all card details
- user is asked again for cardholder name, full card number, expiry, and CVV
- deterministic fallback messages updated
- evaluator scenario updated to resend full card bundle
- tests added/updated for full card-bundle clearing

### Lesson
For payment instruments, bundle-level retry is often simpler and safer than field-level retry, especially in LLM-driven conversation.

---

## 11. Terminal Card Data Cleanup

### Issue
Terminal cleanup originally cleared only card number and CVV.

### Symptom
Cardholder name and expiry could remain in internal state after success/cancellation/closure.

### Root Cause
The original `clear_payment_secrets()` focused on highest-risk raw secrets, but later policy treated the entire card bundle as sensitive.

### Resolution
Added/used `clear_card_details()` for terminal outcomes:
- payment success
- terminal service failure
- payment attempts exhausted
- cancellation
- verification exhaustion
- recap and close

### Lesson
Once a privacy policy changes, cleanup helpers must be aligned globally. Partial cleanup is easy to miss.

---

## 12. Payment Failure Recovery and Retry Messaging

### Issue
Payment API rejection recovery worked, but response wording was sometimes too vague.

### Symptom
Trace showed:
- first payment call fails
- user retries card details
- second payment call succeeds
- workflow is correct, but evaluator fails because message did not clearly explain card validation failure

### Root Cause
Autonomous LLM sometimes asked for details again without explaining why.

### Resolution
Updated:
- deterministic fallback messages
- autonomous payment prompt
- card tool instructions
- evaluator scenario and assertions

Later refined policy:
- invalid card/CVV/expiry asks for full card bundle again
- payment attempts exhausted is not necessarily a service issue

### Lesson
Correct state transition is not enough. In customer support/payment flows, retry messages must explain the reason at the right level of specificity without leaking internals.

---

## 13. Amount Correction After Confirmation Was Staged

### Issue
Autonomous mode could not handle “actually amount is INR 600” after payment was already staged for confirmation.

### Symptom
The agent said the amount could not be changed or repeated the old confirmation.

### Root Cause
In confirmation phase, the autonomous tool surface exposed final confirmation tools but not an explicit tool for corrected payment amount.

### Resolution
Added `correct_payment_amount()` to final confirmation toolset:
- accepts corrected amount
- calls `capture_payment_amount()`
- validates amount
- re-stages payment confirmation if valid
- blocks and asks for corrected amount if invalid

Updated evaluator for:
- valid amount correction requiring reconfirmation
- invalid amount correction blocked

### Lesson
Autonomous tool surfaces must include phase-specific correction tools. Otherwise the LLM may answer textually because the right action is unavailable.

---

## 14. Tool Surface Too Broad in Confirmation Phase

### Issue
Autonomous mode sometimes repeated payment preparation after user said “yes” instead of processing payment.

### Symptom
In happy path, after user confirmed with `yes`, the agent repeated:
“Payment is ready. Please reply yes...”
and payment call count stayed zero.

### Root Cause
Prepare and final confirmation tools were exposed together. The LLM could call `prepare_payment_for_confirmation` again instead of `confirm_and_process_payment`.

### Resolution
Split tool surface phases:
- `PREPARE_CONFIRMATION`: only prepare/stage payment
- `FINAL_CONFIRMATION`: confirm/process, decline, correct amount

### Lesson
Tool availability is as important as prompt wording. Phase-scoped tools reduce the model’s action ambiguity.

---

## 15. Secondary Factor Retry in Autonomous Mode

### Issue
Autonomous mode sometimes failed to treat bare pincode/DOB/Aadhaar values as actionable verification factors.

### Symptom
User provided `400001` after a failed secondary factor, but agent asked again for verification factor instead of calling identity tool.

### Root Cause
Prompt/tool instructions were not specific enough about bare values in identity phase.

### Resolution
Added identity instructions:
- YYYY-MM-DD → DOB
- 4 digits → Aadhaar last 4
- 6 digits → pincode
- when `dob_or_aadhaar_last4_or_pincode` is required, bare values are actionable and must be submitted through the identity tool

### Lesson
LLM tool agents need explicit short-input mapping. “Use context” is not enough for bare values like `4321`, `400001`, or `yes`.

---

## 16. Verification Exhaustion Flake in Autonomous Mode

### Issue
`verification_exhaustion_closes` sometimes failed in autonomous mode.

### Symptom
Evaluator trace showed:
- wrong name / DOB attempts
- state stuck at `waiting_for_secondary_factor`
- `verification_attempts = 2`
- `completed = False`
- no payment calls

### Root Cause
The LLM sometimes answered directly instead of calling the identity tool on an actionable DOB turn. Without the tool call, `verify_identity()` was not executed and the attempt counter did not advance.

A related prompt issue: the model sometimes omitted remaining attempts or revealed “name does not match.”

### Resolution
Prompt specificity was tightened instead of adding deterministic identity routing:
- identity tool must be called before replying when identity details are provided
- do not answer from prompt memory
- mention `attempts_remaining` exactly once when present
- never reveal which identity field failed
- verification exhaustion must state identity failure, no payment processed, and closure

Fallback remains as safety, but the goal is for normal path to be LLM tool call + LLM response.

### Lesson
Prompt specificity matters most at tool-calling boundaries. Deterministic repair can stabilize behavior, but overusing it weakens the “autonomous agent” story.

---

## 17. Safety Fallback in Autonomous Mode

### Issue
Autonomous fallback was used for some terminal messages, especially verification exhaustion.

### Symptom
Agent returned deterministic text:
“I’m unable to verify your identity after multiple attempts...”

### Root Cause
Safety audit rejected vague terminal LLM responses that did not clearly mention verification exhaustion.

### Resolution
Kept safety fallback as a safety net, but improved prompt specificity so fallback is less frequent.

Added logging:
- `autonomous_safety_audit_failed`
- `autonomous_fallback_response_used`

### Lesson
Fallback is not a failure if it protects compliance. But for an autonomous-agent demo, prompts should be specific enough that normal LLM tool-calling succeeds most of the time.

---

## 18. Autonomous Safety Audit Coverage

### Issue
Mode 4 needed a safety layer after LLM-written responses.

### Risks Covered
Safety audit checks for:
- DOB leakage
- Aadhaar leakage
- pincode leakage
- full card number leakage
- CVV leakage
- false identity verification claims
- balance leakage before verification
- false payment success claims
- vague verification-exhausted closure
- weak payment retry reason

### Resolution
Autonomous graph routes:
1. LLM/tool turn
2. safety audit
3. persist response if safe
4. deterministic fallback if unsafe or failed

### Lesson
For tool-calling agents in regulated flows, “tool results are source of truth” is not enough. Final messages still need safety validation.

---

## 19. Evaluator Assertion Brittleness

### Issue
Some evaluator failures were caused by narrow phrase matching, not wrong behavior.

### Examples
- Agent said “could not be found,” evaluator expected “could not find.”
- Agent said “must not exceed outstanding balance,” evaluator expected “cannot exceed.”
- Agent blocked invalid amount correctly but failed wording check.

### Root Cause
The evaluator originally checked exact phrases instead of semantic patterns.

### Resolution
Broadened assertions to accept equivalent wording:
- account not found variants
- amount exceeds balance variants
- card retry variants
- recovery and closure behavior validated through state and tool call counts, not only text

### Lesson
Agent evaluators should validate behavior and meaning, not brittle exact wording, especially for LLM-written responses.

---

## 20. Scenario Filtering Bug

### Issue
Running targeted scenarios across all modes crashed.

### Symptom
Error:
`TypeError: 'EvalScenario' object is not iterable`

### Root Cause
CLI option variable `scenario` was shadowed by loop variable `scenario`. Python function scope caused the CLI list to be overwritten by an `EvalScenario` object.

### Resolution
Renamed CLI option to `scenario_names` and avoided loop variable shadowing.

### Lesson
Evaluation tooling needs the same engineering rigor as product code. A broken filter slows down debugging and encourages expensive full reruns.

---

## 21. Exhaustive vs Subset Evaluation

### Issue
The 7-scenario autonomous subset passed, but exhaustive mode exposed additional failures.

### Symptom
Initial mode-4 default run: 7/7 passed.
Exhaustive run: exposed amount correction and payment retry issues.

### Root Cause
Core scenarios validated happy path, account recovery, guardrails, secondary-factor recovery, side questions, and cancellation. They did not cover all correction and terminal retry paths.

### Resolution
Used layered evaluation:
1. targeted failing scenarios
2. full autonomous exhaustive
3. full all-modes exhaustive
4. focused reruns after changes

### Lesson
A small smoke set is useful for speed, but exhaustive scenarios are necessary before merging agentic changes.

---

## 22. Redaction Tests and Logger Traceability

### Issue
Stricter redaction changed logger expectations.

### Symptom
Tests failed because `account_id=ACC1001` became masked.

### Root Cause
Security policy changed, tests still reflected old policy.

### Resolution
Updated tests to expect redacted account ID in logs and nested payloads.

### Lesson
When privacy policy changes, tests should encode the new policy rather than preserving old expectations.

---

## 23. Docs Became Stale After Mode 4

### Issue
README and design docs still described three modes and old CLI names.

### Symptom
Docs referenced:
- `local`
- `llm`
- `full-llm`
- “LLM only used for parsing/response phrasing”
- graph-native tool calling as future work

### Root Cause
Mode 4 introduced autonomous tool orchestration, new mode names, and new evaluator commands, but docs were not updated immediately.

### Resolution
Update docs to reflect:
- four modes
- new mode names
- autonomous mode
- phase-scoped tool surfaces
- safety audit and fallback
- latest evaluation results
- report retention and scenario filtering
- accurate CLI commands

### Lesson
Architecture docs should be updated after the code stabilizes, not during every debugging pass. But before merge, stale docs become a visible correctness issue.

---

## 24. Final Mode 4 Architecture

### Final Design
Mode 4 is an LLM-led autonomous tool-calling agent over deterministic policy-gated operations.

Key components:
- `autonomous/runtime.py`: PydanticAI/OpenRouter runtime
- `autonomous/memory.py`: safe LLM-facing state and redacted turns
- `autonomous/prompts.py`: global tool-calling/customer-response contract
- `autonomous/tools/*`: phase-specific tools
- `autonomous/tools/factory.py`: phase-scoped tool surface
- `autonomous/safety.py`: final response safety audit
- `autonomous/graph.py`: LangGraph wrapper for LLM turn, audit, fallback, persist

### Tool Phases
- lifecycle
- account
- identity
- amount
- card
- prepare confirmation
- final confirmation
- closed

### Safety Position
The LLM controls conversation and tool choice, but:
- tool availability is phase-scoped
- tools delegate to deterministic operations
- policies gate sensitive actions
- safety audit validates final response
- fallback handles unsafe/failed outputs

### Lesson
A safe autonomous agent is not “LLM does everything.” It is “LLM chooses within a constrained, policy-enforced action space.”

---

## 25. Final Validation Strategy

### Tests
Run:
```bash
uv run pytest -q
```

### Focused Autonomous Validation
Run targeted scenarios after prompt/tool changes:
```bash
uv run python scripts/evaluate_agent.py   --mode llm-autonomous-agent   --no-all   --exhaustive   --scenario verification_exhaustion_closes   --scenario valid_amount_correction_requires_reconfirmation   --scenario invalid_amount_correction_blocked   --scenario payment_attempts_exhausted_closes
```

### Final Exhaustive Validation
Run:
```bash
uv run python scripts/evaluate_agent.py --all --exhaustive
```

### Expected Release Bar
- tests pass
- all modes pass scenario matrix
- no privacy leaks
- no premature payment calls
- terminal outcomes close safely
- corrections reset confirmation
- recovery flows progress without unsafe disclosure

---

## 26. Summary

SettleSentry evolved from a deterministic payment workflow into a four-mode ablation framework for evaluating how much control can safely be given to an LLM in a regulated payment collection flow.

The most important engineering decisions were:
- keep verification and payment authority deterministic
- make LLM responsibility progressive and measurable
- use phase-scoped tools for autonomous mode
- validate every sensitive transition with policy gates
- audit LLM-written final responses
- build an evaluator that catches premature payment calls, privacy leaks, failed recovery, and terminal closure issues

The hardest failures were not simple coding bugs. They were contract failures:
- LLM skipped a tool call
- tool surface exposed the wrong action
- prompt was too broad at a critical phase
- evaluator checked wording too narrowly
- security policy changed but tests still expected old behavior

The final architecture is stronger because every such failure resulted in a clearer contract between language, tools, state, policy, and evaluation.
