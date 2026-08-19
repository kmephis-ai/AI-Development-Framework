# Contract: adwf-ci-failure-triage

Цель: не позволять CI failure автоматически превращаться в повторный запуск, product blame или ослабление gate без fresh provider evidence и cause classification.

Skill связывает диагноз с exact SHA/run/job/step, выбирает один primary class из `INTRODUCED | INHERITED | FLAKY | PROVIDER | ENVIRONMENT | POLICY | UNKNOWN`, затем выбирает правильный repair layer. Timeout/cancellation остаются симптомом до установления failure boundary. `UNKNOWN` блокирует positive claim.

Skill не является источником merge/deploy authorization и не заменяет deterministic policy/provider checks. Consumer-specific remediation не становится generic ADWF behavior без отдельного evidence.
