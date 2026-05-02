# SettleSentry Package

This directory contains the installable Python package used by the SettleSentry payment collection agent.

Canonical project documentation is maintained at the repository root and in `docs/`. This file intentionally focuses only on package structure and module responsibilities.

## Package Layout

```text
settlesentry/
  pyproject.toml                  # Package metadata, dependencies, and console entry point
  src/
    settlesentry/
      cli.py                      # Typer CLI entry point
      agent/                      # Conversational agent implementation
        interface.py              # Public Agent interface: Agent.next(user_input)
        contracts.py              # Public/LLM message response contract
        deps.py                   # Session-scoped dependencies and conversation context
        actions.py                # Parser intent and proposed-action enums
        workflow/
          graph.py                # LangGraph construction and edge wiring
          routing.py              # Required-field resolution and next-node routing
          nodes.py                # LangGraph adapter nodes and node registry
          input.py                # User input ingestion, merge, side-question, and correction handling
          operations.py           # Domain workflow operations: lookup, verify, prepare, confirm, process, close
          helpers.py              # Shared workflow helpers, response context, and cleanup utilities
          constants.py            # Workflow intent, correction, and service-error constants
          result.py               # AgentToolResult model
        parsing/
          base.py                 # Parser protocols, expected fields, and parser context models
          deterministic.py        # Rule-based parser for local mode and fallback repair
          llm.py                  # PydanticAI/OpenRouter parser wrapper
          prompts.py              # Parser instructions and prompt payload construction
          factory.py              # Parser builder and LLM/deterministic fallback composition
        response/
          messages.py             # Deterministic response messages and formatting helpers
          prompts.py              # LLM responder instructions and prompt payload construction
          writer.py               # Response writer factory and optional LLM response writer
        policy/
          models.py               # Policy decision, reason, and ruleset models
          rules.py                # Policy rule functions
          payment.py              # Payment and verification policy sets
        state/
          models.py               # Conversation state, safe state view, and extracted-input models
      integrations/
        payments/                 # Payment API client, schemas, and endpoint definitions
      security/                   # Card validation, identity validation, and redaction helpers
      core/                       # Settings and logging
      utils/                      # Timing utilities
```

## Repository Layout Notes

- Tests are maintained at repository root: `../tests`
- Scenario evaluator is maintained at repository root: `../scripts/evaluate_agent.py`
- Generated evaluation reports are written under `../var/evaluation/`

## Canonical Documentation

- [Project README](../README.md)
- [Design Document](../docs/DESIGN.md)
- [Evaluation Approach](../docs/EVALUATION.md)
- [Sample Conversations](../docs/SAMPLE_CONVERSATIONS.md)
- [Assignment Instructions](../docs/instructions/ASSIGNMENT.md)