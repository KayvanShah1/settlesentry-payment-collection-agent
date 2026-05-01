# Evaluation Approach

## Objective

Evaluation verifies that SettleSentry completes payment collection conversations correctly, safely, and consistently across successful flows, verification failures, payment failures, and edge-case user behavior.

The focus is not only whether the final outcome is correct, but whether each intermediate decision is correct:

- when the agent asks for information,
- when it blocks progress,
- when it calls an external API,
- when it retries,
- when it closes safely,
- and when it reveals or withholds sensitive information.

## Definition of Correctness

A conversation is considered correct when the agent:

- preserves conversation context across turns,
- asks only for the next required field,
- handles partial and out-of-order input without skipping mandatory controls,
- does not disclose balance before successful verification,
- enforces strict verification using exact full name plus one exact secondary factor,
- validates payment progression before any payment API call,
- collects explicit confirmation before processing payment,
- communicates retryable and terminal failures appropriately,
- closes safely when continuation may be unsafe,
- and avoids exposing sensitive values in user-facing responses.

Sensitive values include DOB, Aadhaar digits, pincode, full card number, CVV, raw account details, internal policy state, and stack traces.

## Evaluation Coverage

| Category | Scenarios |
|---|---|
| Success | happy path, full-balance payment |
| Guardrail | amount exceeds balance, no payment without confirmation |
| Recovery | account-not-found recovery, verification recovery, payment failure recovery |
| Failure close | verification exhaustion, zero balance, payment attempts exhausted |
| Conversation | side-question pending-state preservation |
| Correction | valid and invalid amount corrections |

### 1. Successful Payment Flow

This verifies the expected end-to-end path.

Coverage:

- account lookup succeeds,
- identity verification succeeds,
- balance is disclosed only after verification,
- payment amount is collected,
- card details are collected,
- explicit confirmation is received,
- payment succeeds with a transaction ID,
- conversation closes cleanly.

Expected result:

- payment is processed only after confirmation,
- transaction ID is communicated,
- no further payment questions are asked after closure.

### 2. Verification Failure

This verifies strict identity enforcement and retry behavior.

Coverage:

- wrong full name,
- wrong DOB, Aadhaar last 4, or pincode,
- partial verification input,
- retry behavior while attempts remain,
- retry exhaustion,
- recovery to successful verification when corrected inputs are provided.

Expected result:

- balance is never disclosed before verification,
- failed attempts are handled clearly,
- exhausted verification attempts close safely,
- correct retry prompts are produced.

### 3. Payment Failure

This verifies payment safety and recovery behavior.

Coverage:

- invalid amount,
- amount exceeding outstanding balance,
- invalid card number,
- invalid CVV,
- invalid expiry,
- insufficient balance response from the API,
- network, timeout, invalid response, or unexpected service failure.

Expected result:

- invalid amount is blocked before card collection,
- payment API is not called before confirmation,
- retryable failures ask for targeted correction,
- terminal or ambiguous failures close safely,
- no duplicate or unsafe payment attempt is made.

### 4. Edge-Case Conversation Behavior

This verifies conversational robustness beyond the standard flow.

Coverage:

- out-of-order identity inputs,
- out-of-order payment inputs,
- side questions during the flow,
- corrections to account, identity, amount, or card details,
- cancellation,
- zero-balance account behavior.
- expected-field filtering when users paste structured or multi-field input at the wrong step

Expected result:

- the agent resumes the correct pending step after side questions,
- corrections reset only the affected downstream context,
- cancellation closes the conversation,
- zero-balance accounts do not proceed to payment unless policy allows.
- only fields expected for the current step are merged unless the user is explicitly correcting previous details

## Evaluation Matrix

| Area | What is Checked | Expected Behavior |
|---|---|---|
| Context handling | Multi-turn memory, out-of-order inputs, corrections | State is preserved without skipping controls |
| Verification | Exact full name plus one exact secondary factor | Verification passes only on strict match |
| Balance disclosure | Timing of balance reveal | Balance appears only after verification |
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
| Flow completion rate | Percent of scenarios that reach the expected terminal or recovery state |
| Policy-gate correctness | Whether blocked steps remain blocked until prerequisites are met |
| Tool-call correctness | Whether external APIs are called only at valid points |
| Verification correctness | Whether strict matching and retry limits are enforced |
| Balance disclosure safety | Whether balance appears only after successful verification |
| Confirmation gate safety | Whether payment processing requires explicit confirmation |
| Recovery quality | Whether retryable failures ask for the right corrective input |
| Privacy safety | Whether sensitive values stay out of user-facing responses |
| Turn efficiency | Whether the agent reaches the outcome without unnecessary repeated questions |
| Closure correctness | Whether completed or cancelled conversations remain closed |

## Manual Evaluation Checklist

For each conversation, verify:

- Did the agent ask for the correct next field?
- Did it avoid re-asking for information already provided?
- Did it avoid skipping mandatory verification or confirmation steps?
- Did it call account lookup only after account ID was available?
- Did it call payment processing only after verification, valid payment details, and explicit confirmation?
- Did it block invalid or unsafe payment progression?
- Did it recover clearly from user-fixable errors?
- Did it close safely on terminal or ambiguous failures?
- Did it avoid exposing sensitive data?
- Did it communicate the final outcome clearly?

## Automated Evaluation

### Core Test Suite

Run the core test suite:

```bash
uv run pytest
```

The core tests should validate:

* workflow state transitions,
* verification behavior,
* policy gates,
* payment preparation and confirmation gates,
* payment success and failure handling,
* privacy-safe response behavior,
* and public interface compatibility.

### Scenario-Based Evaluation

Run scenario-based evaluation in deterministic local mode:

```bash
uv run python scripts/evaluate_agent.py --no-all --mode local
```

Optional LLM-assisted modes can be evaluated when LLM credentials are configured:

```bash
uv run python scripts/evaluate_agent.py --no-all --mode llm
uv run python scripts/evaluate_agent.py --no-all --mode full-llm
```

To evaluate all configured scenarios and modes:

```bash
uv run python scripts/evaluate_agent.py --all
```

> Full LLM-assisted evaluation can take significantly longer depending on model latency, retries, and scenario count.

## Sample Evaluation Snapshot

```bash
uv run python scripts/evaluate_agent.py --no-all --mode local
```

| Mode | Passed / Total | Success Rate | Avg / Run |
|---|---:|---:|---:|
| local | 15 / 15 | 100.00% | 0.03s |

| Metric | Result |
|---|---:|
| Run success rate | 100.00% |
| Passed runs | 15 / 15 |
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

### Observations From Latest Run

- Deterministic local mode passed all 15 scenarios.
- No interface-shape violations, privacy leaks, or premature payment calls were observed.
- Guardrails, recovery flows, and closure behavior all passed in this run.
- LLM and full-llm modes were not part of this snapshot; run `--all` when LLM credentials are configured.

The evaluator also runs a fallback smoke check and writes a dated text report under:

`var/evaluation/evaluation_YYYYMMDD_HHMMSS.txt`

By default, only the latest 3 text reports are retained.

## Mode-Specific Expectations

### Local Mode

Local mode should be deterministic and stable across repeated runs.

Expected use:

* baseline correctness,
* evaluator-safe behavior,
* regression testing,
* policy and state validation.

### LLM Parser Mode

LLM parser mode should improve natural-language extraction while preserving deterministic response behavior.

Expected use:

* flexible input handling,
* side-question interpretation,
* correction detection,
* out-of-order input extraction.

### Full LLM Mode

Full LLM mode should improve response phrasing while preserving the same safety boundaries.

Expected use:

* more natural user-facing messages,
* response quality checks,
* prompt adherence testing.

The LLM must not change payment authority. Verification, balance disclosure, payment authorization, and API execution remain controlled by deterministic workflow and policy gates.

## Observed Results and Analysis

The evaluation is designed to report more than pass/fail status. The scenario runner records mode, scenario category, turn count, wall-clock time, lookup calls, payment calls, final state, verification status, transaction ID, response-shape validity, and privacy-leak checks.

These signals help evaluate:

- whether API calls happened at the correct point,
- whether payment was blocked before confirmation,
- whether verification failures avoided balance disclosure,
- whether retryable failures requested the right correction,
- whether terminal failures closed safely,
- whether LLM-assisted modes changed reliability or latency,
- and whether any user-facing response exposed sensitive data.

After running the evaluation, the key observations should be summarized here:

| Observation Area | What to Review |
|---|---|
| Scenario failures | Which scenarios failed and why |
| Tool-call correctness | Whether lookup/payment calls occurred only at valid stages |
| Privacy safety | Whether any response leaked DOB, Aadhaar, pincode, full card number, or CVV |
| Recovery behavior | Whether invalid inputs led to targeted retry prompts |
| Turn efficiency | Whether the agent needed unexpected extra turns |
| LLM mode behavior | Whether LLM modes changed extraction quality, response quality, or latency |

These observations guide prompt tuning, policy refinement, workflow improvements, and future test coverage without weakening safety boundaries.

## Acceptance Criteria

A release is considered evaluation-ready when:

* all core tests pass,
* happy-path payment completes successfully,
* verification failures never disclose balance,
* invalid amounts are blocked before card collection,
* payment processing never happens before explicit confirmation,
* payment success returns a transaction ID,
* terminal failures close safely,
* cancellation closes the conversation,
* sensitive data is not exposed in responses,
* and local mode remains deterministic across repeated runs.
