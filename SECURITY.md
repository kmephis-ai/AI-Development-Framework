# Безопасность ADWF v1.6

## Основное правило

Creative Agent и PR code не обладают правом подтверждать собственную корректность. Authority живёт в default-branch trusted code + provider identity/readback + Effective Policy.

## PR и governance

Обычный PR может менять продукт, но его `fast-feedback` не является доказательством неизменности evaluator. Trusted controller через API читает changed files. Изменения `.github/workflows/adwf-*`, policy, trusted evaluators/context/evidence/cost/ruleset/release control относятся к trust boundary и требуют отдельного exact-HEAD approval repository admin, отличного от PR author.

Canonical main ruleset: PR required, `fast-feedback`, `adwf/governance-gate`, `adwf/trusted-gate`, strict checks, deletion/non-fast-forward denied, `bypass_actors=[]`, check contexts bound to one GitHub Actions integration id.

## Runtime ledger и privacy

Public repository **не является private memory store**. Public Runtime Ledger публикует только restorable operational projection: IDs, phase/status/revision, hashes, safe reason codes и allowlisted metadata. Raw owner task, raw Work Memory, arbitrary stderr/agent text и hidden reasoning туда не попадают.

Каждый remote checkpoint связан с provider comment id/created_at/actor и protected annotated tag `adwf-runtime-anchor-*`. Отдельный tag ruleset запрещает update/delete без bypass; missing/orphan anchors делают ledger invalid.

## Work Memory

Raw chain-of-thought не сохраняется. Private Work Memory — структурированный handoff: brief, решения, ограничения, progress, blockers, next action. Он локальный/owner-controlled.

## Secrets

PR jobs: secrets = 0. Optional hosted AI: GitHub/Environment Secret. Local AI: OS credential store. `.adwf-runtime` не secret vault. Token не помещается в Issue/PR/Work Memory/public checkpoint.

## Preview

`http://` разрешён только loopback; credentials in URL запрещены. Loopback source identity доказывается локальным Git HEAD. Hosted marker из PR logs сам по себе недоверенный: trusted controller принимает его только после exact-run + required trusted/governance checks от одного GitHub Actions app source. Remote HTTPS source identity требует независимый provider deployment readback exact SHA.

## Release

External release требует owner-confirmed publication/LICENSE policy, internal-version equality, full gates, exact tag/release readback. Caller не может навязать версию, отличную от canonical source.

## Live boundary

Hosted Windows/Linux, GitHub ruleset behavior, external tag immutability и production deployment считаются `NOT_VERIFIED` до живого provider cycle, даже если mocks/unit tests PASS.
