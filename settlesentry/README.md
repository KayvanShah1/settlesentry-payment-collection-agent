# SettleSentry Package

This directory contains the installable Python package (`settlesentry`) used by the assignment project.

Canonical project documentation is maintained at the repository root and in `docs/` to avoid duplicated setup and run instructions.

## Package Layout

```text
settlesentry/
  pyproject.toml
  src/
    settlesentry/
      agent/
        interface.py
        deps.py
        actions.py
        workflow/
          graph.py
          routing.py
          nodes.py
          input.py
          operations.py
          helpers.py
          constants.py
          result.py
        parsing/
        response/
        policy/
        state/
      integrations/   # Payment API client and schemas
      security/       # Card and identity validation helpers
      core/           # Settings and logging
      utils/          # Timing utilities
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
