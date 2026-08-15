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
