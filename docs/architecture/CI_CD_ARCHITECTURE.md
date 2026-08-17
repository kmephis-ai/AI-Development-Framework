# ADWF v1.6 — CI/CD архитектура

## Fast feedback вместо job sprawl

Default PR lane не разбивается на множество коротких jobs без измеренного выигрыша. Сначала Impact Router определяет, какие дорогие проверки действительно нужны.

Всегда выполняются safety/config/policy/trust preflight. Полный framework suite запускается при изменении ADWF. UI impact включает preview. Provider boundary включает contract tests. Product gates остаются проектно-специфичными.

## Trusted lane

Trusted controller:

- работает из default branch;
- не исполняет PR code/artifacts;
- получает provider facts через API;
- формирует evidence/readback/Assurance для exact HEAD;
- публикует `adwf/governance-gate` и `adwf/trusted-gate`;
- читает exact-run Playwright marker через GitHub job-log API только после trusted/governance verification;
- восстанавливает/сохраняет remote runtime checkpoint.

## Concurrency

Superseded PR runs можно отменять. Trusted transitions, release/promotion transactions сериализуются. Один Writer работает на один conflict domain.

## Cache/artifacts

Cache выключен там, где измеренного выигрыша нет. При включении он является restore-only accelerator для untrusted PR и никогда evidence. Artifacts имеют минимальный retention и sanitization.

## Performance truth

Queue time и execution time измеряются отдельно. Evidence группируется по impact и Project Pack; метрика без достаточного sample window остаётся `NOT_VERIFIED`. Цель — уменьшить time-to-first-useful-failure и активное время владельца, а не максимизировать параллелизм.
