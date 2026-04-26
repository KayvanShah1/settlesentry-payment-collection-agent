Based on the Prodigal assignment, the strongest implementation is a **deterministic, production-style payment collection agent**, not a heavy LangChain/LlamaIndex/MCP/RAG system.

The assignment is mainly evaluating **state machine design, context handling, strict verification, tool calling, structured outputs, and failure handling** through the exact `Agent.next(user_input: str) -> dict` interface. Adding OpenRouter, LangChain, Mongo Atlas, FAISS, or RAG would likely make the solution look over-engineered and less deterministic for their automated evaluator. 

## Best implementation direction

Build this as a **rule-driven conversational state machine** with clean modules:

```text
prodigal-payment-agent/
  agent.py
  src/
    state.py
    schemas.py
    parser.py
    validators.py
    tools.py
    policy.py
    messages.py
    logger.py
  tests/
    test_happy_path.py
    test_verification.py
    test_payment.py
    test_edge_cases.py
    test_context.py
  eval/
    run_eval.py
    cases.yaml
  README.md
  DESIGN.md
  requirements.txt
```

## What to use and why

### 1. No LangChain/LlamaIndex for the core agent

Use a plain Python class with internal state.

Reason: the required interface is simple and deterministic:

```python
class Agent:
    def next(self, user_input: str) -> dict:
        return {"message": "..."}
```

LangChain/LangGraph could be mentioned as a future production option, but using them here may create unnecessary complexity. The evaluator will call `agent.next()` repeatedly, so your agent should behave predictably without external memory, workers, async queues, or model calls.

### 2. Use Pydantic heavily

Use Pydantic for:

```text
AccountLookupRequest
AccountLookupResponse
PaymentRequest
PaymentResponse
CardDetails
ConversationState
VerificationInput
PaymentIntent
ToolResult
```

Reason: the assignment explicitly expects validated API payloads and structured outputs. Pydantic gives you strong boundaries and clean validation before API calls.

Use validators for:

```text
account_id format
DOB format and real date validation
Aadhaar last 4 = exactly 4 digits
pincode = exactly 6 digits
amount > 0 and max 2 decimal places
amount <= outstanding balance
card number numeric + valid length + Luhn pre-check
CVV length
expiry month/year
```

### 3. Use `pydantic-settings`, but keep it minimal

Use it for:

```text
API_BASE_URL
REQUEST_TIMEOUT_SECONDS
VERIFY_MAX_ATTEMPTS
PAYMENT_MAX_ATTEMPTS
LOG_LEVEL
ENV
```

Do not make the user set up many environment variables. Provide sane defaults.

Example:

```python
class Settings(BaseSettings):
    api_base_url: str = "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi"
    request_timeout_seconds: int = 10
    verify_max_attempts: int = 3
    payment_max_attempts: int = 3
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_prefix="PRODIGAL_")
```

### 4. Use HTTP client as the tool layer

Create two tools:

```python
lookup_account(account_id: str) -> AccountLookupResult
process_payment(payment: PaymentRequest) -> PaymentResult
```

Use `httpx` or `requests`.

I would use **httpx** because it has clean timeout handling and good test mocking support.

Tool layer should handle:

```text
200 success
404 account_not_found
422 payment failures
timeouts
network errors
invalid JSON
unexpected status codes
```

### 5. Use a policy module for rule-based checks

This is important. Create `policy.py` with deterministic decisions.

Examples:

```python
def can_attempt_verification(state) -> PolicyDecision
def can_reveal_balance(state) -> PolicyDecision
def can_collect_payment(state) -> PolicyDecision
def can_process_payment(state) -> PolicyDecision
```

Core policy rules:

```text
Never reveal account details before verification
Never expose DOB, Aadhaar, or pincode back to the user
Never proceed to payment unless full name matches exactly
Require at least one secondary factor match
Stop verification after retry limit
Do not process zero balance accounts
Do not process amount > balance
Do not call payment API with invalid card fields
Do not log raw card number or CVV
```

This will make your design document look mature without adding unnecessary frameworks.

## Recommended architecture

```text
User message
   ↓
Agent.next()
   ↓
Input parser
   ↓
Conversation state update
   ↓
Policy check
   ↓
State transition
   ↓
Tool call if needed
   ↓
Tool response normalization
   ↓
User-safe message generation
   ↓
{"message": "..."}
```

Architecture should be a **state machine**, not an open-ended LLM agent.

Suggested states:

```python
class Step(str, Enum):
    START = "start"
    WAITING_FOR_ACCOUNT_ID = "waiting_for_account_id"
    ACCOUNT_LOOKUP_FAILED = "account_lookup_failed"
    WAITING_FOR_FULL_NAME = "waiting_for_full_name"
    WAITING_FOR_SECONDARY_FACTOR = "waiting_for_secondary_factor"
    VERIFIED_BALANCE_SHARED = "verified_balance_shared"
    WAITING_FOR_PAYMENT_AMOUNT = "waiting_for_payment_amount"
    WAITING_FOR_CARDHOLDER_NAME = "waiting_for_cardholder_name"
    WAITING_FOR_CARD_NUMBER = "waiting_for_card_number"
    WAITING_FOR_CVV = "waiting_for_cvv"
    WAITING_FOR_EXPIRY = "waiting_for_expiry"
    READY_TO_PROCESS_PAYMENT = "ready_to_process_payment"
    PAYMENT_SUCCESS = "payment_success"
    PAYMENT_FAILED_RETRYABLE = "payment_failed_retryable"
    CLOSED = "closed"
```

## Context handling strategy

Your agent should extract and store information even if provided early.

Example:

User says:

```text
Hi, my account is ACC1001, name is Nithin Jain, DOB is 1990-05-14
```

The agent should:

```text
1. store account_id
2. call lookup-account
3. store provided name
4. store provided DOB
5. verify immediately if possible
6. avoid re-asking for name/DOB
```

This is one of the biggest scoring areas. The assignment explicitly mentions out-of-order information and avoiding re-asking for data already provided. 

## Parsing approach

Do not use an LLM for parsing. Use deterministic extraction.

Parser should support:

```text
ACC1001
account id is ACC1001
name is Nithin Jain
DOB is 1990-05-14
aadhaar last 4 is 4321
pincode 400001
pay 500
₹500
500.00
card number 4532015112830366
cvv 123
expiry 12/2027
expiry month 12 year 2027
```

Keep it simple but robust.

## Verification implementation

Strict rule:

```text
full_name == account.full_name
AND
(dob == account.dob OR aadhaar_last4 == account.aadhaar_last4 OR pincode == account.pincode)
```

No fuzzy matching. No lowercase comparison. No trimming beyond removing accidental outer whitespace.

Important:

```python
"Nithin Jain" == "Nithin Jain"  # pass
"nithin jain" != "Nithin Jain"  # fail
"Nithin  Jain" != "Nithin Jain" # fail unless you choose to normalize spaces, but I would avoid it
```

For retry limit, use **3 attempts**.

After 3 failed verification attempts:

```text
I’m unable to verify your identity after multiple attempts, so I can’t continue with payment collection in this chat. Please contact support for further assistance.
```

Do not reveal which field was wrong.

## Payment policy for this assignment

For the take-home assignment, do **not** introduce human intervention into the actual flow. Their evaluator expects the agent to process valid payments through the API after verification.

Assignment policy:

```text
Allowed:
Verified user
Balance > 0
Amount > 0
Amount <= outstanding balance
Amount has max 2 decimals
Card fields collected and locally validated
Then call process-payment API

Not allowed:
Unverified user
Zero balance
Amount > balance
Invalid amount
Missing card fields
Invalid local card validation
Verification retries exhausted
```

Production policy can be mentioned in `DESIGN.md` as future work, but not implemented in the core `Agent.next()` flow.

Example production note:

```text
In a real system, high-risk payments, repeated failed cards, suspicious identity attempts, or payment amounts above a configured threshold would be routed to human review. For this assignment, the flow remains fully automated after strict verification because the required interface has no reviewer channel and the API is designed for direct payment processing.
```

## Payment edge cases to handle

### Zero balance account

ACC1003 has ₹0.00 balance. After verification:

```text
Identity verified. Your account currently has no outstanding balance, so no payment is due. I’ll close this conversation.
```

Do not ask for card details.

### Leap year DOB

ACC1004 has:

```text
1988-02-29
```

This is valid because 1988 was a leap year.

If user gives:

```text
1988-02-28
```

Verification should fail.

If user gives:

```text
1988-02-30
```

Respond:

```text
That date does not appear to be valid. Please provide your date of birth in YYYY-MM-DD format.
```

This is a strong edge case to highlight in tests.

## Tool call timing

Call `/api/lookup-account` only after you have a valid account ID.

Do not call it on:

```text
empty input
random text
invalid account format
```

Call `/api/process-payment` only after:

```text
account lookup succeeded
user verified
balance > 0
amount collected and validated
all card fields collected
local card validation passed
policy allows payment
```

This shows disciplined tool use.

## Logging

Use structured logs, but redact sensitive fields.

Track:

```text
conversation_id
current_state
previous_state
event_type
tool_name
tool_status
api_status_code
verification_attempt_count
payment_attempt_count
policy_decision
error_code
latency_ms
```

Do not log:

```text
full card number
CVV
raw Aadhaar
DOB
pincode
```

You can log masked forms:

```text
card_number_masked = "************0366"
aadhaar_last4_provided = "***"
```

## Metrics to mention in design doc

For this assignment, keep metrics practical:

```text
Conversation completion rate
Verification success/failure rate
Verification retry exhaustion rate
Payment success rate
Payment failure rate by error code
Invalid input recovery rate
Correct tool-call timing rate
API error handling coverage
Average turns to completion
State transition error count
Sensitive data leakage count
```

The most important metric:

```text
Unsafe payment attempts = 0
```

Meaning:

```text
No payment API call should ever happen before verification.
```

## Evaluation framework

Do not use RAGAS here. RAGAS is for RAG evaluation. This assignment has no retrieval, grounding, chunk recall, or document answering.

Use a custom deterministic eval runner.

Create:

```text
eval/cases.yaml
eval/run_eval.py
```

Each case should define:

```yaml
name: happy_path_full_payment
turns:
  - input: "Hi"
    expect_contains: ["account ID"]
  - input: "ACC1001"
    expect_contains: ["full name"]
  - input: "Nithin Jain"
    expect_contains: ["date of birth", "Aadhaar", "pincode"]
  - input: "1990-05-14"
    expect_contains: ["verified", "₹1,250.75"]
```

Also track internal assertions if possible:

```text
lookup_account called exactly once
process_payment called only after verification
process_payment payload amount correct
no raw sensitive data in messages
state ends in PAYMENT_SUCCESS or CLOSED
```

## Tests to write early

### 1. Happy path

```text
ACC1001
Exact name
Correct DOB
Full payment
Valid card
Success with transaction ID
```

### 2. Partial payment

```text
ACC1001 balance ₹1,250.75
User pays ₹500
Payment succeeds
```

### 3. Verification failure

```text
Correct account
Wrong name or wrong DOB
3 failed attempts
Agent closes
No balance exposed
No payment API call
```

### 4. Out-of-order information

```text
"My name is Nithin Jain and my account is ACC1001 and DOB is 1990-05-14"
```

Agent should not re-ask for already provided fields.

### 5. Account not found

```text
ACC9999
lookup returns account_not_found
Agent asks for valid account ID again
```

### 6. Zero balance

```text
ACC1003
Verified
Agent says no payment due
Does not collect card
```

### 7. Leap year DOB

```text
ACC1004
Rahul Mehta
1988-02-29
Verification passes
```

### 8. Invalid date

```text
1988-02-30
Agent rejects as invalid date format/value
Does not count as wrong verification attempt if you document that assumption
```

### 9. Invalid amount

```text
0
-10
100.999
amount greater than balance
```

Should not call payment API.

### 10. Invalid card

```text
masked card
bad Luhn
invalid CVV
expired expiry
```

Should either reject locally or handle API 422 cleanly.

## What to include in README

README should have:

```text
1. Project overview
2. Architecture summary
3. Setup
4. How to run interactive CLI
5. How to run tests
6. How to run eval
7. Sample conversations
8. Assumptions
9. Known tradeoffs
```

## What to include in DESIGN.md

Keep it 1–2 pages.

Suggested structure:

```text
1. Goal
2. Architecture
3. State machine design
4. Verification design
5. Tool-calling design
6. Payment handling
7. Failure handling
8. Logging and privacy
9. Evaluation strategy
10. Tradeoffs and future improvements
```

Key design decision to explicitly state:

```text
I chose a deterministic state machine over an LLM-driven orchestration framework because the assignment requires strict verification, reproducible behavior, and an exact Agent.next() interface for automated evaluation.
```

That sentence will land well.

## What not to build

Do not build these for the core submission:

```text
MCP server
OpenRouter integration
LangChain agent loop
LlamaIndex
Mongo Atlas Vector Search
FAISS
RAGAS
Complex frontend
Database persistence
Human approval UI
Async workflow queue
Multi-agent system
```

Mention them only as future production extensions.

## Best “production-ready” extras without overengineering

Add these:

```text
Pydantic schemas
State machine
API client with retries/timeouts
Policy layer
Sensitive data redaction
Structured logging
Mockable tool layer
CLI runner
Deterministic eval script
Strong pytest coverage
Sample conversations
Clean design doc
```

That is the highest-signal implementation for Prodigal.

## Suggested final positioning

Build it as:

```text
A deterministic payment collection agent with strict in-agent identity verification, validated tool calls, privacy-safe logging, retry-aware failure handling, and an automated evaluation harness.
```

This directly maps to what Prodigal says they are evaluating: context management, tool calling, failure handling, structured outputs, and system design thinking. 

---

You are right. In the previous answer, I described the **supporting system**, but I did not explicitly show the **agent loop**.

For this assignment, the **agent is the `Agent` class itself**. It is not necessarily a LangChain/OpenRouter-style autonomous LLM agent. It is a **conversation-state agent** that:

```text
observes user input
→ extracts useful fields
→ updates memory/state
→ decides next action
→ calls tools when needed
→ applies policy checks
→ generates the next response
```

That is the agent.

The assignment explicitly requires this interface:

```python
class Agent:
    def next(self, user_input: str) -> dict:
        return {"message": "..."}
```

So the “agent” Prodigal will evaluate is whatever behavior sits behind `Agent.next()` across multiple turns. They will call it repeatedly and check whether it manages context, verifies identity, calls APIs correctly, handles failures, and returns safe messages. 

## Correct agent architecture

```text
                 ┌────────────────────┐
User message ──▶ │ Agent.next(input)   │
                 └─────────┬──────────┘
                           │
                           ▼
                 ┌────────────────────┐
                 │ Input Parser        │
                 │ - account_id        │
                 │ - name              │
                 │ - DOB/Aadhaar/PIN   │
                 │ - amount/card info  │
                 └─────────┬──────────┘
                           │
                           ▼
                 ┌────────────────────┐
                 │ Conversation Memory │
                 │ - current step      │
                 │ - account data      │
                 │ - provided fields   │
                 │ - retry counts      │
                 │ - payment fields    │
                 └─────────┬──────────┘
                           │
                           ▼
                 ┌────────────────────┐
                 │ Agent Decision Loop │
                 │ decide_next_action  │
                 └─────────┬──────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
 ┌────────────────┐ ┌───────────────┐ ┌────────────────┐
 │ Policy Checks  │ │ Tool Calls    │ │ Response Builder│
 │ verification   │ │ lookup API    │ │ safe user msg   │
 │ payment rules  │ │ payment API   │ │ no data leaks   │
 └────────────────┘ └───────────────┘ └────────────────┘
```

## The actual “agent loop”

Inside `Agent.next()`, the flow should look like this:

```python
def next(self, user_input: str) -> dict:
    extracted = self.parser.extract(user_input)

    self.state.update_with(extracted)

    action = self.decide_next_action()

    result = self.execute_action(action)

    message = self.response_builder.build(action, result, self.state)

    return {"message": message}
```

That is the agent.

## Agent responsibilities

The agent should own these decisions:

```text
Should I ask for account ID?
Should I call lookup-account now?
Do I already have the user's name?
Do I need DOB, Aadhaar last 4, or pincode?
Has verification passed?
Can I reveal the balance?
Can I collect payment?
Is the payment amount valid?
Do I have all card fields?
Can I call process-payment now?
Should I allow retry or close the conversation?
```

The tools do not decide these things. The agent does.

## Recommended internal agent design

```python
class Agent:
    def __init__(self):
        self.state = ConversationState()
        self.parser = InputParser()
        self.account_tool = AccountLookupTool()
        self.payment_tool = PaymentTool()
        self.policy = PaymentCollectionPolicy()
        self.messages = MessageBuilder()

    def next(self, user_input: str) -> dict:
        extracted = self.parser.extract(user_input)
        self.state.merge(extracted)

        action = self._decide_next_action()

        tool_result = self._execute_if_needed(action)

        self.state.apply_result(action, tool_result)

        return {
            "message": self.messages.build(self.state, action, tool_result)
        }
```

## The agent is not just a state machine

A plain state machine says:

```text
If state = WAITING_FOR_NAME, ask for name.
```

A proper agent for this assignment does more:

```text
User: "Hi, I am Nithin Jain, account ACC1001, DOB 1990-05-14"

Agent:
- extracts account ID
- calls lookup API
- stores returned account
- extracts name
- extracts DOB
- verifies identity
- skips already-satisfied questions
- shares balance
- moves to payment collection
```

That is agentic behavior within the required deterministic interface.

## Best naming in code

To make the “agent” visible to reviewers, structure files like this:

```text
agent.py
src/
  agent_core.py          # Agent decision loop
  state.py               # Conversation memory
  actions.py             # Agent action enum
  parser.py              # Extracts fields from user input
  policy.py              # Rule checks
  tools.py               # API calls
  validators.py          # Input validation
  messages.py            # User-safe responses
```

In `actions.py`:

```python
class AgentAction(str, Enum):
    ASK_ACCOUNT_ID = "ask_account_id"
    LOOKUP_ACCOUNT = "lookup_account"
    ASK_FULL_NAME = "ask_full_name"
    ASK_SECONDARY_FACTOR = "ask_secondary_factor"
    VERIFY_IDENTITY = "verify_identity"
    SHARE_BALANCE = "share_balance"
    ASK_PAYMENT_AMOUNT = "ask_payment_amount"
    ASK_CARDHOLDER_NAME = "ask_cardholder_name"
    ASK_CARD_NUMBER = "ask_card_number"
    ASK_CVV = "ask_cvv"
    ASK_EXPIRY = "ask_expiry"
    PROCESS_PAYMENT = "process_payment"
    HANDLE_PAYMENT_FAILURE = "handle_payment_failure"
    CLOSE = "close"
```

Then the agent has a real decision policy:

```python
def _decide_next_action(self) -> AgentAction:
    if not self.state.account_id:
        return AgentAction.ASK_ACCOUNT_ID

    if not self.state.account_loaded:
        return AgentAction.LOOKUP_ACCOUNT

    if not self.state.provided_full_name:
        return AgentAction.ASK_FULL_NAME

    if not self.state.has_secondary_factor():
        return AgentAction.ASK_SECONDARY_FACTOR

    if not self.state.verified:
        return AgentAction.VERIFY_IDENTITY

    if self.state.balance == 0:
        return AgentAction.CLOSE

    if not self.state.payment_amount:
        return AgentAction.ASK_PAYMENT_AMOUNT

    if not self.state.cardholder_name:
        return AgentAction.ASK_CARDHOLDER_NAME

    if not self.state.card_number:
        return AgentAction.ASK_CARD_NUMBER

    if not self.state.cvv:
        return AgentAction.ASK_CVV

    if not self.state.expiry_complete:
        return AgentAction.ASK_EXPIRY

    return AgentAction.PROCESS_PAYMENT
```

## Should you use an LLM agent?

For this assignment: **no, not as the core controller**.

Because Prodigal’s evaluator will call `Agent.next()` and expect deterministic behavior. An LLM can introduce:

```text
non-deterministic responses
extra setup/API keys
unexpected skipped steps
privacy risk with card/DOB/Aadhaar data
harder automated testing
```

A better compromise:

```text
Core agent = deterministic state machine
Optional future extension = LLM only for natural-language extraction
```

But even extraction can be done with regex and simple parsing here.

## How to explain it in DESIGN.md

Use this wording:

```text
The agent is implemented as a deterministic conversational decision loop behind the required Agent.next() interface. Each turn is processed by extracting structured fields from the user message, updating internal conversation state, selecting the next action, applying policy checks, calling external tools only when allowed, and returning a user-safe response.

I intentionally avoided an LLM-driven autonomous agent for the core control flow because this payment workflow requires strict verification, reproducibility, privacy-safe handling of sensitive data, and deterministic behavior under automated evaluation.
```

## Final answer

The agent is:

```text
Agent.next()
+ internal memory
+ action planner
+ policy checker
+ tool caller
+ response generator
```

Not:

```text
LangChain agent
OpenRouter model
MCP server
RAG pipeline
```

For Prodigal, the strongest submission is a **deterministic tool-using payment collection agent**, not a framework-heavy LLM agent.

---

OpenRouter is **not strictly needed** to pass the assignment.

But yes, since this is for an **Agent Engineer** role, using an LLM can make the submission look more aligned **only if you use it safely**.

The best design is:

```text
LLM via OpenRouter = natural-language understanding layer
Deterministic Python agent = controller, policy, verification, tool calls, payment safety
```

Do **not** let the LLM decide whether the user is verified, whether payment is allowed, or what API payload should be trusted directly.

## Where to use the LLM

Use OpenRouter in one narrow place:

```text
User message
   ↓
LLM extractor via OpenRouter
   ↓
Structured JSON fields
   ↓
Pydantic validation
   ↓
Deterministic Agent.next() state machine
   ↓
Policy checks
   ↓
API tool calls
   ↓
Safe response
```

The LLM should extract structured fields from messy user input.

Example user input:

```text
Hi, this is Nithin Jain. My account is ACC1001 and DOB is 1990-05-14. I want to pay 500 using my card.
```

OpenRouter should return something like:

```json
{
  "account_id": "ACC1001",
  "full_name": "Nithin Jain",
  "dob": "1990-05-14",
  "aadhaar_last4": null,
  "pincode": null,
  "payment_amount": 500.00,
  "cardholder_name": null,
  "card_number": null,
  "cvv": null,
  "expiry_month": null,
  "expiry_year": null,
  "intent": "make_payment"
}
```

Then your Python code validates and decides what to do next.

OpenRouter supports structured JSON outputs through `response_format` with JSON schema, which fits this use case well. It also exposes model metadata and supported parameters, including tool calling and structured outputs. ([OpenRouter][1])

## Where not to use the LLM

Do **not** use the LLM for these:

```text
identity verification
payment approval
amount validation
retry limits
balance disclosure decision
card validation
API tool call authorization
policy decisions
final source of truth for state
```

Those must remain deterministic because the assignment has hard rules: no payment before verification, strict matching, no sensitive data exposure, validated API payloads, and consistent behavior across repeated `Agent.next()` calls. 

## Recommended implementation

Use this architecture:

```text
Agent.next(user_input)
  ├── LLMExtractor.extract(user_input)         # OpenRouter
  ├── Pydantic validates extracted fields
  ├── fallback RegexExtractor if LLM fails
  ├── ConversationState.merge(valid_fields)
  ├── PolicyEngine decides allowed next action
  ├── ToolLayer calls lookup/payment APIs
  ├── MessageBuilder returns safe response
  └── {"message": "..."}
```

This gives you an actual **LLM-powered agent**, but the sensitive workflow is still controlled.

## Why OpenRouter is useful here

Use it for:

```text
messy natural language extraction
out-of-order information handling
intent detection
field normalization into structured JSON
showing modern LLM integration
model flexibility through one API
```

OpenRouter’s API is OpenAI-compatible and supports structured output modes like `json_object` and `json_schema`, so you can keep responses machine-parseable instead of relying on free-form model text. ([OpenRouter][2])

## Why OpenRouter should not control tools directly

OpenRouter also supports tool-calling style parameters, but for this assignment I would **not** let the model call tools directly. ([OpenRouter][3])

Reason:

```text
Bad design:
LLM decides → call lookup/payment API

Better design:
LLM extracts fields → deterministic agent decides → tool layer calls API
```

Payment collection is a regulated, sensitive workflow. Your reviewer will care more about safety and determinism than about showing a fancy autonomous tool-calling loop.

## Best final positioning

In your design doc, say:

```text
The agent uses OpenRouter as an optional LLM-based understanding layer to convert natural user messages into structured fields. However, all verification, policy enforcement, state transitions, payment authorization, and API tool calls are handled deterministically in Python. This keeps the agent conversational while preserving safety, reproducibility, and strict compliance with the assignment rules.
```

That is the right balance.

## OpenRouter failure handling

Your agent should still work if OpenRouter fails.

Use fallback parsing:

```text
Primary parser: OpenRouter structured JSON extraction
Fallback parser: regex/rule-based extraction
```

If OpenRouter times out:

```text
log llm_extraction_failed
use regex parser
continue conversation
```

This shows production maturity.

## Should MCP be used?

No, not for the assignment core.

MCP makes sense when you have many tools, external systems, and standardized tool discovery. Here you only have two APIs:

```text
lookup-account
process-payment
```

Adding MCP would add setup complexity without improving the evaluator’s experience.

Mention it as future work:

```text
In a larger production deployment, the account lookup and payment APIs could be exposed through an MCP server so multiple agents or clients can discover and invoke tools consistently. For this take-home, I used a direct typed tool layer to keep the required Agent.next() interface deterministic and easy to evaluate.
```

## Final recommendation

Use OpenRouter, but only as a **structured extraction layer**.

Do not use:

```text
Mongo Atlas Vector Search
FAISS
LlamaIndex
RAGAS
MCP server
LangChain agent executor
```

Use:

```text
OpenRouter structured extraction
Pydantic schemas
deterministic state machine
policy engine
typed API tools
privacy-safe logging
pytest + deterministic eval cases
```

That gives you the strongest implementation for Prodigal: visibly AI-powered, but still safe, testable, and aligned with the automated evaluation interface.

[1]: https://openrouter.ai/docs/features/structured-outputs?utm_source=chatgpt.com "Structured Outputs | Enforce JSON Schema in OpenRouter API Responses | OpenRouter | Documentation"
[2]: https://openrouter.ai/docs/api/reference/overview/?utm_source=chatgpt.com "OpenRouter API Reference | Complete API Documentation | OpenRouter | Documentation"
[3]: https://openrouter.ai/docs/api/reference/parameters?utm_source=chatgpt.com "API Parameters | Configure OpenRouter API Requests | OpenRouter | Documentation"