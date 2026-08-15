---
name: adwf-session-bootstrap
description: Restore an ADWF development session from fresh provider, repository, writer and durable-handoff facts before choosing or resuming a work unit.
---

# ADWF Session Bootstrap

Используй при старте новой AI-сессии или после потери локального состояния.

## Procedure
1. Прочитай live default branch и зафиксируй exact SHA.
2. Проверь открытые PR/Issues и provider-visible writer transactions в пересекающихся conflict domains.
3. Прочитай канонические programme/ledger/roadmap sources, относящиеся к задаче.
4. Восстанови только durable handoff facts: принятые решения, exact refs, выполненные проверки, blockers и следующий безопасный шаг.
5. Проверь локальный workspace. Если direct Git недоступен и доступен Connector, маршрутизируйся в `adwf-local-git-mirror`.
6. Сопоставь planning claims с repository/provider/runtime evidence и явно вычисли delta.
7. Только после этого возобновляй существующий writer lease или создавай новый AI-sized work unit.

## Completion output
Кратко зафиксируй source SHA, активную транзакцию или её отсутствие, восстановленные durable facts, обнаруженный delta и выбранный следующий work unit. Hidden reasoning, credentials и секреты в handoff не входят.
