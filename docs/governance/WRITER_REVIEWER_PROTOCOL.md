# Writer / Independent Reviewer Protocol

## Writer
Writer владеет implementation, но не final truth. Он синхронизирует repository, получает atomic lease, создаёт isolated workspace, записывает/readback строгий Issue marker с Writer/UUID lease/workspace/heartbeat/expiry, делает минимальное изменение, tests/docs, project gates и focused PR. Writer не заменяет независимое review своей self-review. Heartbeat старше workspace timeout, время из будущего или истёкший lease немедленно переводят flow в Recovery.

## Reviewer
Reviewer по умолчанию READ-ONLY и проверяет exact PR HEAD SHA: acceptance criteria, correctness, regressions, architecture, scope drift, tests, security/privacy, performance, docs drift, rollback и evidence.

Finding: Severity + Location + Evidence + Impact + Recommendation.

Verdict: `PASS`, `PASS_WITH_NOTES`, `CHANGES_REQUIRED`, `NOT_VERIFIED`. Изменение SHA делает verdict `STALE`.

Для перехода `REVIEW→VERIFICATION` GitHub controller принимает только фактический `APPROVED` review другого login для exact PR HEAD SHA; текст Writer в PR не является approval. В private GitHub Free platform-level запрет обходного merge недоступен по официальным plan rules. Владелец не нажимает merge до зелёного controller flow; Control Plane process может быть проверен, но Security и общий режим панели остаются ограниченными из-за риска ручного обхода.
