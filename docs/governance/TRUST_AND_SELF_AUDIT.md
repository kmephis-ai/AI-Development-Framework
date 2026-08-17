# Trust Plane & Self-Audit

ADWF не имеет права ослаблять собственные проверки для достижения зелёного статуса.

## Human-by-exception

Trust-sensitive изменение сначала классифицируется по **trusted BASE revision**. Кандидат не может использовать собственные изменения policy/evaluator/classifier для своей же авторизации. Если BASE отличается от текущего защищённого base ref, standing authorization недействительна.

`Standing Owner Authorization` разрешает без отдельной SHA-аттестации только обратимые trust-support изменения, для которых одновременно доказано: нет weakening, изменённые protected blobs полностью прочитаны, не затронут owner-reserved security surface и exact-head machine gates зелёные. В таком случае решение публикуется как `AUTO-AUTHORIZED BY STANDING POLICY`.

Ручное exact-HEAD решение сохраняется для изменения authorization/trust evaluator или самой standing policy, permissions/rulesets/workflows, FREE_ONLY/provider/cost/security controls, secrets/credentials, release/bootstrap trust surface, иных owner-reserved путей, а также для любого обнаруженного weakening или неоднозначности.

`FREE_ONLY`, запрет bypass, целостность evidence и запрет self-authorization являются non-overridable invariants: Owner-Attestation не превращает их нарушение в допустимое изменение.

Standing policy versioned и revocable. `status=REVOKED`, невалидная policy, неполный content readback либо base drift приводят к fail-closed возврату к human gate или BLOCK, но никогда к автоматическому разрешению.

## Self-audit

`adwf self-test` обязан содержать отрицательные canary fixtures. Если хотя бы один опасный fixture принят, Autopilot блокируется. Обязательны adversarial cases: self-policy modification, gate/permission weakening, stale/revoked standing policy, base drift, forged/stale exact-head evidence и попытка использовать standing authorization для абсолютного блока.

## Bounded recent Actions telemetry

Exact PR, ruleset, commit/check and governance facts remain on exact provider contracts because completeness is authority. Repository-wide Actions history used only for reconciliation CI summaries, performance telemetry, or bounded runner-provenance lookup is different: those callers use the explicit newest-first `GitHubClient.recent_runs(limit<=100, event=...)` single-page contract, never follow pagination, and never claim exhaustive history. For trusted reconciliation, the exact required gate truth still comes from the exact subject commit check-runs and exact ruleset readback; the bounded `ADWF PR` run lookup only proves runner labels (`ubuntu-24.04`, no `self-hosted`). If that exact-subject successful run is absent from the bounded recent window, runner provenance is `NOT_VERIFIED` and reconciliation fails closed. The existing `runs()` contract remains exhaustive and fail-closed for callers that actually require complete Actions history. A bounded recent window therefore cannot authorize merge, weaken exact-head governance, or substitute for provider truth.
