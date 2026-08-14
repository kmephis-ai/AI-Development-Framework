# ADWF v1.6 — Recovery Playbook

Recovery запускается не от эмоции «CI красный», а от классифицированного failure fingerprint.

## Разрешённая лестница

- Observe/redact evidence.
- Один bounded retry только для подтверждённого transient/idempotent случая.
- Пересоздать disposable workspace/cache namespace.
- Применить только certified recipe к разрешённой branch/workspace.
- Targeted regression → full relevant gates → preview/runtime evidence.
- Открыть fix PR; без self-approval.
- Rollback только на exact certified artifact, если policy разрешает.

## Fail-closed

Unknown fingerprint, security/privacy/data-loss, policy/secrets/provider registry, visual baseline, destructive migration и исчерпанный budget переводятся в `HUMAN_REQUIRED/BLOCK`, а не в бесконечную AI-импровизацию.

Runtime Supervisor хранит attempt/cycle/deadline/cost limits. Recovery result не становится PASS без fresh evidence и Trusted Context.
