---
name: adwf-verification-before-completion
description: Require fresh, impact-aware, exact-head evidence before ADWF work may be called DONE/PASS, merged or promoted.
---

# ADWF Verification Before Completion

Используй перед заявлением DONE/PASS, merge или promotion.

## Procedure
1. Зафиксируй текущий exact HEAD и сравни его с SHA всех ранее собранных evidence/attestations.
2. Определи impact: code, docs, trust boundary, provider settings, product/runtime, migration/data.
3. Запусти минимальный релевантный deterministic test set; для framework-wide риска — full self-test/structural validators.
4. Пересобери и проверь generated projections каноническими generators, если они входят в impact.
5. Выполни provider exact-head readback required checks.
6. Для product-impact потребуй свежий runtime/visual/functional proof на фактически наблюдаемой revision.
7. Если policy требует owner decision, принимай только provider-authenticated SHA-bound решение для неизменённого HEAD.
8. Любой UNKNOWN, stale, mismatch или failed criterion означает BLOCK/NOT_VERIFIED, а не PASS.

## Completion rule
DONE/PASS допустим только когда все применимые acceptance criteria имеют fresh evidence. Количество закрытых Issues, уверенность агента и старые успешные runs не являются достаточным доказательством.
