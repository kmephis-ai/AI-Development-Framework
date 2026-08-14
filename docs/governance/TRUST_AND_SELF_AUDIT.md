# Trust Plane & Self-Audit

ADWF не имеет права ослаблять собственные проверки для достижения зелёного статуса.

Любое изменение trust-boundary, повышающее автономность, расширяющее permissions либо ослабляющее required gate, считается R4 и требует отдельного GOV PR + human approval.

`adwf self-test` обязан содержать отрицательные canary fixtures. Если хотя бы один опасный fixture принят, Autopilot блокируется.
