# Evaluation Sample

Generated from:

```bash
uv run python scripts/evaluate_agent.py --all --exhaustive
```

Generated at: `2026-05-08T17:49:03`  
Modes: `deterministic-workflow`, `llm-parser-workflow`, `llm-parser-responder-workflow`, `llm-autonomous-agent`  
Exhaustive: `True`  
Repeats (local): `1`  
Repeats (llm/full-llm): `1`  
Scenario retries: `1`

## Fallback Smoke Check

| Status | Reason | Parser Fallback | Response Fallback | Shape |
|---|---|---|---|---|
| PASS | passed | PASS | PASS | PASS |

## Mode Performance Summary

| Mode | Passed/Total | Success | First Attempt | Avg Attempts | Wall Time | Avg/Run |
|---|---:|---:|---:|---:|---:|---:|
| deterministic-workflow | 15/15 | 100.00% | 100.00% | 1.00 | 0.47s | 0.03s |
| llm-parser-workflow | 15/15 | 100.00% | 100.00% | 1.00 | 1115.32s | 74.35s |
| llm-parser-responder-workflow | 15/15 | 100.00% | 100.00% | 1.00 | 2031.34s | 135.42s |
| llm-autonomous-agent | 15/15 | 100.00% | 100.00% | 1.00 | 1293.34s | 86.22s |

## Overall Metrics

| Metric | Value |
|---|---:|
| run_success_rate | 100.00% |
| passed_runs | 60/60 |
| total_wall_time_seconds | 4440.47s |
| average_wall_time_seconds | 74.01s |
| interface_compliance_rate | 100.00% |
| privacy_leak_count | 0 |
| premature_payment_calls | 0 |
| total_lookup_calls | 64 |
| total_payment_calls | 28 |
| average_turns_per_run | 8.60 |
| clear_error_message_rate | 100.00% |
| graceful_close_rate | 100.00% |
| amount_guardrail_success_rate | 100.00% |
| correction_success_rate | 100.00% |
| recovery_success_rate | 100.00% |
| payment_recovery_success_rate | 100.00% |
| confirmation_gate_success_rate | 100.00% |

## Scenario Matrix (All Modes Passed)

| Category | Scenario |
|---|---|
| success | happy_path_partial_payment |
| success | full_balance_payment |
| recovery | account_not_found_then_recovery |
| guardrail | amount_exceeds_balance_before_card_collection |
| recovery | verification_failure_then_recovery |
| recovery | secondary_factor_failure_then_recovery |
| failure_close | verification_exhaustion_closes |
| failure_close | zero_balance_closes_without_payment |
| conversation | side_question_preserves_pending_state |
| guardrail | no_payment_without_confirmation |
| failure_close | cancel_at_confirmation_closes_without_payment |
| correction | valid_amount_correction_requires_reconfirmation |
| correction | invalid_amount_correction_blocked |
| recovery | payment_failure_recovery |
| failure_close | payment_attempts_exhausted_closes |

> [!Note]
> Latest exhaustive all-mode run passed the full scenario matrix with `0` privacy leaks and `0` premature payment calls.