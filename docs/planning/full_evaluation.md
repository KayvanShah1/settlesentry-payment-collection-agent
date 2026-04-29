                                 Mode Performance Summary                                 
┏━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┓
┃ Mode     ┃ Passed/Total ┃ Success ┃ First Attempt ┃ Avg Attempts ┃ Wall Time ┃ Avg/Run ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━┩
│ local    │ 14/14        │ 100.00% │ 100.00%       │ 1.00         │ 2.36s     │ 0.17s   │
│ llm      │ 13/14        │ 92.86%  │ 92.86%        │ 1.07         │ 809.11s   │ 57.79s  │
│ full-llm │ 13/14        │ 92.86%  │ 92.86%        │ 1.07         │ 1437.67s  │ 102.69s │
└──────────┴──────────────┴─────────┴───────────────┴──────────────┴───────────┴─────────┘
                                                                   Scenario Results                                                                   
┏━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Status ┃ Mode     ┃ Category      ┃ Scenario                                        ┃ Repeat ┃ Attempts ┃ Wall Time ┃ Reason                       ┃
┡━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  PASS  │ local    │ success       │ happy_path_partial_payment                      │      1 │        1 │     0.25s │ passed                       │
│  PASS  │ local    │ success       │ full_balance_payment                            │      1 │        1 │     0.20s │ passed                       │
│  PASS  │ local    │ recovery      │ account_not_found_then_recovery                 │      1 │        1 │     0.06s │ passed                       │
│  PASS  │ local    │ guardrail     │ amount_exceeds_balance_before_card_collection   │      1 │        1 │     0.11s │ passed                       │
│  PASS  │ local    │ recovery      │ verification_failure_then_recovery              │      1 │        1 │     0.13s │ passed                       │
│  PASS  │ local    │ failure_close │ verification_exhaustion_closes                  │      1 │        1 │     0.15s │ passed                       │
│  PASS  │ local    │ failure_close │ zero_balance_closes_without_payment             │      1 │        1 │     0.09s │ passed                       │
│  PASS  │ local    │ conversation  │ side_question_preserves_pending_state           │      1 │        1 │     0.08s │ passed                       │
│  PASS  │ local    │ guardrail     │ no_payment_without_confirmation                 │      1 │        1 │     0.18s │ passed                       │
│  PASS  │ local    │ failure_close │ cancel_at_confirmation_closes_without_payment   │      1 │        1 │     0.17s │ passed                       │
│  PASS  │ local    │ correction    │ valid_amount_correction_requires_reconfirmation │      1 │        1 │     0.18s │ passed                       │
│  PASS  │ local    │ correction    │ invalid_amount_correction_blocked               │      1 │        1 │     0.17s │ passed                       │
│  PASS  │ local    │ recovery      │ payment_failure_recovery                        │      1 │        1 │     0.30s │ passed                       │
│  PASS  │ local    │ failure_close │ payment_attempts_exhausted_closes               │      1 │        1 │     0.28s │ passed                       │
│  PASS  │ llm      │ success       │ happy_path_partial_payment                      │      1 │        1 │    71.31s │ passed                       │
│  PASS  │ llm      │ success       │ full_balance_payment                            │      1 │        1 │    63.90s │ passed                       │
│  PASS  │ llm      │ recovery      │ account_not_found_then_recovery                 │      1 │        1 │    20.78s │ passed                       │
│  PASS  │ llm      │ guardrail     │ amount_exceeds_balance_before_card_collection   │      1 │        1 │    21.18s │ passed                       │
│  FAIL  │ llm      │ recovery      │ verification_failure_then_recovery              │      1 │        2 │    81.50s │ verification recovery failed │
│  PASS  │ llm      │ failure_close │ verification_exhaustion_closes                  │      1 │        1 │    66.90s │ passed                       │
│  PASS  │ llm      │ failure_close │ zero_balance_closes_without_payment             │      1 │        1 │    19.75s │ passed                       │
│  PASS  │ llm      │ conversation  │ side_question_preserves_pending_state           │      1 │        1 │    31.69s │ passed                       │
│  PASS  │ llm      │ guardrail     │ no_payment_without_confirmation                 │      1 │        1 │    67.13s │ passed                       │
│  PASS  │ llm      │ failure_close │ cancel_at_confirmation_closes_without_payment   │      1 │        1 │    68.90s │ passed                       │
│  PASS  │ llm      │ correction    │ valid_amount_correction_requires_reconfirmation │      1 │        1 │    61.87s │ passed                       │
│  PASS  │ llm      │ correction    │ invalid_amount_correction_blocked               │      1 │        1 │    63.63s │ passed                       │
│  PASS  │ llm      │ recovery      │ payment_failure_recovery                        │      1 │        1 │    78.74s │ passed                       │
│  PASS  │ llm      │ failure_close │ payment_attempts_exhausted_closes               │      1 │        1 │    91.84s │ passed                       │
│  PASS  │ full-llm │ success       │ happy_path_partial_payment                      │      1 │        1 │   133.51s │ passed                       │
│  PASS  │ full-llm │ success       │ full_balance_payment                            │      1 │        1 │   138.10s │ passed                       │
│  PASS  │ full-llm │ recovery      │ account_not_found_then_recovery                 │      1 │        1 │    20.89s │ passed                       │
│  PASS  │ full-llm │ guardrail     │ amount_exceeds_balance_before_card_collection   │      1 │        1 │    51.37s │ passed                       │
│  FAIL  │ full-llm │ recovery      │ verification_failure_then_recovery              │      1 │        2 │   149.57s │ verification recovery failed │
│  PASS  │ full-llm │ failure_close │ verification_exhaustion_closes                  │      1 │        1 │    78.36s │ passed                       │
│  PASS  │ full-llm │ failure_close │ zero_balance_closes_without_payment             │      1 │        1 │    30.04s │ passed                       │
│  PASS  │ full-llm │ conversation  │ side_question_preserves_pending_state           │      1 │        1 │    28.33s │ passed                       │
│  PASS  │ full-llm │ guardrail     │ no_payment_without_confirmation                 │      1 │        1 │   119.95s │ passed                       │
│  PASS  │ full-llm │ failure_close │ cancel_at_confirmation_closes_without_payment   │      1 │        1 │   126.73s │ passed                       │
│  PASS  │ full-llm │ correction    │ valid_amount_correction_requires_reconfirmation │      1 │        1 │   128.28s │ passed                       │
│  PASS  │ full-llm │ correction    │ invalid_amount_correction_blocked               │      1 │        1 │   118.31s │ passed                       │
│  PASS  │ full-llm │ recovery      │ payment_failure_recovery                        │      1 │        1 │   146.48s │ passed                       │
│  PASS  │ full-llm │ failure_close │ payment_attempts_exhausted_closes               │      1 │        1 │   167.76s │ passed                       │
└────────┴──────────┴───────────────┴─────────────────────────────────────────────────┴────────┴──────────┴───────────┴──────────────────────────────┘
               Overall Metrics               
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Metric                         ┃ Value    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ run_success_rate               │ 95.24%   │
│ passed_runs                    │ 40/42    │
│ total_wall_time_seconds        │ 2249.13s │
│ average_wall_time_seconds      │ 53.55s   │
│ interface_compliance_rate      │ 100.00%  │
│ privacy_leak_count             │ 0        │
│ premature_payment_calls        │ 0        │
│ total_lookup_calls             │ 45       │
│ total_payment_calls            │ 21       │
│ average_turns_per_run          │ 8.21     │
│ clear_error_message_rate       │ 100.00%  │
│ graceful_close_rate            │ 100.00%  │
│ amount_guardrail_success_rate  │ 100.00%  │
│ correction_success_rate        │ 100.00%  │
│ recovery_success_rate          │ 66.67%   │
│ payment_recovery_success_rate  │ 100.00%  │
│ confirmation_gate_success_rate │ 100.00%  │
└────────────────────────────────┴──────────┘
