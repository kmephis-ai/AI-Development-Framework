---
name: adwf-develop
description: Route ADWF development, debugging, local-workspace recovery, session continuity and completion-verification requests to a small managed leaf Skill set.
---

# ADWF Develop Router

Используй этот router для задач разработки и инженерного исполнения. Сначала классифицируй intent, затем подключи только минимально необходимый leaf Skill.

## Routes
- `adwf-local-git-mirror` — когда direct Git недоступен, а Connector может восстановить exact-SHA workspace.
- `adwf-ci-failure-triage` — когда CI/workflow/job упал, timeout/cancelled/flaky и нужно evidence-bound cause classification до retry/repair.
- `adwf-session-bootstrap` — когда новая AI-сессия должна восстановить live provider/workspace/task state.
- `adwf-session-handoff` — когда нужен durable handoff между сессиями без secrets и hidden reasoning.
- `adwf-verification-before-completion` — когда работа близка к DONE/PASS и требуется fresh evidence.

## Boundary
Router не заменяет deterministic policy. Exact SHA, required checks, FREE_ONLY, trust classification и owner gates остаются машинными инвариантами ADWF. Если ни один leaf Skill не соответствует intent, используй канонические процедуры ADWF и зафиксируй отсутствие подходящего Skill вместо ложной маршрутизации.
