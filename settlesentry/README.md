# SettleSentry Package

This directory contains the installable Python package (`settlesentry`) used by the assignment project.

Canonical project documentation is maintained at the repository root and in `docs/` to avoid duplicated setup and run instructions.
The root `README.md` intentionally keeps structure high-level; this file is the detailed package-layout reference.

## Package Layout

```text
settlesentry/
  pyproject.toml                  # Package metadata and dependencies
  src/
    settlesentry/
      agent/                      # Conversational agent implementation
        interface.py              # Public Agent interface (Agent.next)
        contracts.py              # Shared public/LLM message response contract
        deps.py                   # Session-scoped runtime dependencies
        actions.py                # Parser intent/proposed-action enums
        workflow/
          graph.py                # LangGraph construction and edge wiring
          routing.py              # Required-field and next-node routing
          nodes.py                # LangGraph adapter nodes and node registry
          input.py                # Input ingestion and correction handling
          operations.py           # Domain workflow operations
          helpers.py              # Shared workflow helpers and cleanup
          constants.py            # Workflow intent/error constants
          result.py               # AgentToolResult model
        parsing/
          base.py                 # Parser interfaces and context models
          deterministic.py        # Rule-based parser
          llm.py                  # LLM parser wrapper
          prompts.py              # Parser prompts
          factory.py              # Parser builder and fallback composition
        response/
          messages.py             # Deterministic response message helpers
          prompts.py              # Responder prompts
          writer.py               # Response writer factory and optional LLM response writer
        policy/
          models.py               # Policy models (decision/reason/ruleset)
          rules.py                # Policy rule functions
          payment.py              # Payment/verification policy sets
        state/
          models.py               # Conversation state models
      integrations/               # Payment API client and schemas
      security/                   # Card and identity validation helpers
      core/                       # Settings and logging
      utils/                      # Timing utilities
```

## Repository Layout Notes

- Tests are maintained at repository root: `../tests`
- Scenario evaluation script is at repository root: `../scripts/evaluate_agent.py`

## Canonical Docs

- [Project README](../README.md)
- [Design Document](../docs/DESIGN.md)
- [Evaluation Approach](../docs/EVALUATION.md)
- [Sample Conversations](../docs/SAMPLE_CONVERSATIONS.md)
- [Assignment Instructions](../docs/instructions/ASSIGNMENT.md)
