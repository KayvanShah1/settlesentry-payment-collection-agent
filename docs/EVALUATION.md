# Evaluation Approach

## Objective

Evaluation verifies that SettleSentry completes payment collection conversations correctly, safely, and consistently across success, recovery, guardrail, correction, and closure paths.

The focus is not only the final outcome, but also whether each intermediate decision is correct:

- when the agent asks for information
- when it blocks progress
- when it calls an external API
- when it retries
- when it closes safely
- when it reveals or withholds sensitive information

## Definition of Correctness

A conversation is considered correct when the agent:

- preserves context across turns
- asks for the next required field without unnecessary repetition
- handles partial and out-of-order input without bypassing mandatory controls
- verifies identity before disclosing the outstanding amount
- validates payment progression before any payment API call
- collects explicit confirmation before processing payment
- communicates retryable and terminal failures appropriately
- closes safely when continuation may be unsafe
- avoids exposing sensitive values in user-facing responses

Sensitive values include DOB, Aadhaar digits, pincode, full card number, CVV, raw account details, internal policy state, and stack traces.

## Scenario Coverage

| Category | Scenarios |
|---|---|
| Success | happy path, full-balance payment |
| Guardrail | amount exceeds balance, no payment without confirmation |
| Recovery | account-not-found recovery, verification recovery, secondary-factor recovery, payment failure recovery |
| Failure close | verification exhaustion, zero balance, cancellation, payment attempts exhausted |
| Conversation | side-question pending-state preservation |
| Correction | valid amount correction, invalid amount correction |

The automated evaluator covers end-to-end payment success, verification failures and recovery, amount guardrails, API rejection recovery, correction handling, cancellation, zero-balance closure, side questions, and terminal failure closure.

## Evaluation Matrix

| Area | What is Checked | Expected Behavior |
|---|---|---|
| Context handling | Multi-turn memory, out-of-order inputs, corrections | State is preserved without skipping mandatory controls |
| Verification | Exact full name plus one exact secondary factor | Verification passes only on strict match |
| Balance disclosure | Timing of amount-due reveal | Balance appears only after verification |
| Payment amount | Positive amount, amount within balance, policy limits | Invalid amounts are blocked before card collection |
| Card collection | Cardholder, card number, expiry, CVV | Missing or invalid fields are requested clearly |
| Confirmation | Explicit yes/no confirmation | Payment is processed only after confirmation |
| Tool calls | Account lookup and payment processing timing | APIs are called only at valid workflow points |
| Failure handling | Retryable vs terminal failures | User-fixable errors retry; ambiguous failures close safely |
| Privacy | Sensitive values in responses | Sensitive verification and card data are not exposed |
| Closure | Success, cancellation, exhausted attempts, terminal failures | Closed conversations do not continue payment flow |

## Metrics

| Metric | Meaning |
|---|---|
| `run_success_rate` | Percentage of scenario runs that passed |
| `interface_compliance_rate` | Whether `Agent.next()` returned exactly `{"message": str}` |
| `privacy_leak_count` | Count of detected sensitive-value leaks in user-facing responses |
| `premature_payment_calls` | Count of payment API calls made before the scenario expected payment |
| `clear_error_message_rate` | Percentage of applicable failures with a clear user-facing explanation |
| `graceful_close_rate` | Percentage of applicable terminal scenarios that closed safely |
| `amount_guardrail_success_rate` | Percentage of amount guardrail scenarios blocked correctly |
| `correction_success_rate` | Percentage of correction scenarios that reset and rerouted correctly |
| `recovery_success_rate` | Percentage of recovery scenarios that returned to the expected flow |
| `payment_recovery_success_rate` | Percentage of payment API recovery scenarios handled correctly |
| `confirmation_gate_success_rate` | Percentage of confirmation-gate scenarios that prevented premature payment |

Metrics that are not exercised by the selected scenario subset are reported as `N/A`, not `0.00%`. This separates "not evaluated in this run" from "evaluated and failed."

## Automated Evaluation

### Core Test Suite

Run the core test suite:

```bash
uv run pytest -q
```

The tests cover workflow state transitions, verification behavior, policy gates, payment preparation, confirmation gates, payment success/failure handling, privacy-safe responses, parser behavior, and public interface compatibility.

### Scenario Evaluator

Run one selected mode:

```bash
uv run python scripts/evaluate_agent.py --no-all --mode deterministic-workflow
uv run python scripts/evaluate_agent.py --no-all --mode llm-parser-workflow
uv run python scripts/evaluate_agent.py --no-all --mode llm-parser-responder-workflow
uv run python scripts/evaluate_agent.py --no-all --mode llm-autonomous-agent
```

Run the full scenario matrix for one selected mode:

```bash
uv run python scripts/evaluate_agent.py --no-all --mode llm-autonomous-agent --exhaustive
```

Run all configured modes:

```bash
uv run python scripts/evaluate_agent.py --all
```

Run all configured modes with the full scenario matrix:

```bash
uv run python scripts/evaluate_agent.py --all --exhaustive
```

Run targeted scenarios:

```bash
uv run python scripts/evaluate_agent.py \
  --all \
  --exhaustive \
  --scenario verification_exhaustion_closes \
  --scenario payment_attempts_exhausted_closes
```

Full LLM-assisted evaluation can take significantly longer depending on model latency, retries, and scenario count.

## Evaluator Output

The evaluator reports:

- fallback smoke-check status
- mode-level pass rate, first-attempt success rate, average attempts, and wall time
- scenario-level pass/fail status and failure reason
- aggregate safety and quality metrics
- dated text reports under `var/evaluation/`

When a scenario fails, the evaluator can include a redacted turn trace showing user input, agent response, workflow step, payment amount, confirmation status, completion status, and payment-call count after each turn.

By default, only the latest 10 evaluation text reports are retained.

## Sample Evaluation Snapshot

```bash
uv run python scripts/evaluate_agent.py --all --exhaustive
```

Latest exhaustive snapshot (see [evaluation sample](evaluation_sample.md)):

| Mode | Passed / Total | Success Rate | Notes |
|---|---:|---:|---|
| deterministic-workflow | 15 / 15 | 100.00% | no LLM dependency |
| llm-parser-workflow | 15 / 15 | 100.00% | LLM parser with deterministic fallback |
| llm-parser-responder-workflow | 15 / 15 | 100.00% | LLM parser/responder with deterministic fallback |
| llm-autonomous-agent | 15 / 15 | 100.00% | LLM-led phase-scoped tool orchestration |

| Metric | Result |
|---|---:|
| Run success rate | 100.00% |
| Passed runs | 60 / 60 |
| Interface compliance rate | 100.00% |
| Privacy leak count | 0 |
| Premature payment calls | 0 |
| Clear error message rate | 100.00% |
| Graceful close rate | 100.00% |
| Amount guardrail success rate | 100.00% |
| Correction success rate | 100.00% |
| Payment recovery success rate | 100.00% |
| Confirmation gate success rate | 100.00% |
| Recovery success rate | 100.00% |

## Mode-Specific Expectations

### Deterministic Workflow Mode

`deterministic-workflow` is the deterministic baseline.

Expected use:

- baseline correctness
- regression testing
- policy and state validation
- evaluator-safe behavior without external LLM dependency

### LLM Parser Workflow Mode

`llm-parser-workflow` improves natural-language extraction while keeping deterministic response wording.

Expected use:

- flexible input handling
- side-question interpretation
- correction detection
- out-of-order input extraction

### LLM Parser Responder Workflow Mode

`llm-parser-responder-workflow` uses the LLM for both parsing and response phrasing, with deterministic fallback.

Expected use:

- natural response quality checks
- prompt adherence testing
- safety-boundary validation under LLM-written responses

### LLM Autonomous Agent Mode

Autonomous mode uses the LLM to decide whether to ask a question or call one of the currently available phase-scoped tools. The model does not receive unrestricted tool access. Tool availability is scoped by workflow phase, and every payment-critical tool delegates to deterministic operations and policy checks.

Expected use:

- tool-orchestration evaluation
- comparison against deterministic and hybrid modes
- stress testing LLM-led recovery, confirmation, correction, and closure behavior
- measuring latency and reliability tradeoffs of agentic control

Key checks:

- account-not-found recovery submits the next account ID for fresh lookup
- identity retries call the identity tool instead of repeating prompts
- verification exhaustion closes safely without balance disclosure
- confirmation replies trigger payment processing only after preparation
- amount corrections reset confirmation and require reconfirmation
- card API failures clear the full card bundle and require fresh card details
- cancellation closes without payment
- safety audit prevents privacy leaks, false verification claims, false payment success, and vague terminal closure

In all modes, verification, balance disclosure, payment authorization, and API execution remain controlled by deterministic workflow and policy gates.

## Review Checklist

For failed or suspicious runs, review:

- Did any scenario call payment before explicit confirmation?
- Did any response expose DOB, Aadhaar, pincode, full card number, CVV, raw account details, or internal policy state?
- Did verification failures avoid balance disclosure?
- Did invalid amount scenarios block before card collection?
- Did recovery scenarios request the correct next input?
- Did correction scenarios reset confirmation and reroute appropriately?
- Did terminal outcomes close the conversation safely?
- Did LLM-assisted modes change extraction quality, response quality, or latency?

## Acceptance Criteria

A release is evaluation-ready when:

- all core tests pass
- deterministic-workflow mode passes the full scenario matrix
- LLM-assisted modes pass the selected smoke/core or exhaustive scenario set being used for release validation
- fallback smoke checks pass
- no interface-shape violations are detected
- no premature payment calls are detected
- no user-facing sensitive-value leaks are detected
- verification failures never disclose balance
- invalid amounts are blocked before card collection
- payment processing never happens before explicit confirmation
- success, cancellation, exhausted attempts, and terminal failures close correctly
