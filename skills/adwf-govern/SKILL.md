---
name: adwf-govern
description: Route ADWF governance, audit, evidence-truth, security-review and completion-verification work while deterministic trust enforcement stays in code and CI.
---

# ADWF Govern Router

Используй этот router для вопросов governance, аудита, evidence truth, traceability и security review.

## Routes
- `adwf-verification-before-completion` — свежая проверка claims перед DONE/PASS.
- `adwf-session-handoff` — передача проверяемых handoff facts между сессиями.

## Boundary
Evidence не возникает из уверенного текста агента. Unknown остаётся unknown/blocking согласно policy. Exact-SHA owner authorization, required checks, rulesets и trusted-controller semantics исполняются deterministic механизмами ADWF, а не этим Skill.
