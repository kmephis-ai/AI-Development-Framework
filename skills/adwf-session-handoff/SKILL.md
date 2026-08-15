---
name: adwf-session-handoff
description: Produce a durable, verifiable ADWF session handoff with exact refs, evidence, blockers and next action while excluding secrets and hidden reasoning.
---

# ADWF Session Handoff

Используй при завершении или переключении AI-сессии, когда работа должна безопасно продолжиться позже или другим агентом.

## Include only durable facts
- цель и текущий work unit;
- canonical repository, branch и exact SHA;
- writer lease/conflict domains;
- что реально изменено и что уже merged;
- какие fresh tests/provider gates/runtime checks относятся к какому exact HEAD;
- открытые blockers и незавершённые acceptance criteria;
- действующее owner decision только если оно привязано к неизменённому exact SHA;
- следующий безопасный action.

## Exclude
Не сохраняй hidden reasoning, credentials, tokens, secret values или догадки, замаскированные под факты. Старое evidence помечай stale, если exact HEAD или boundary изменились.

## Output
Handoff должен позволять следующей сессии сначала выполнить live readback, а затем сверить durable facts с текущей реальностью, не доверяя им автоматически.
