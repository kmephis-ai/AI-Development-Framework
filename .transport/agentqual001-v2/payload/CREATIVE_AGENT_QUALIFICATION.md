# Creative Agent Qualification Contract v1

## Назначение

`AGENTQUAL-001` вводит provider-neutral границу квалификации для заменяемого Creative Agent adapter. Квалификация отвечает только на вопрос: **разрешён ли этот способ вызова в заявленных пределах и корректно ли он связывает результат с exact `AIWorkPackage`?**

Ключевой invariant:

**QUALIFIED ≠ TRUSTED.** Creative Agent остаётся low-trust источником изменений; его `PASS` не является CI, governance, review, provider или owner evidence и сам по себе не продвигает trusted Runtime Supervisor.

## Canonical contract

`.adwf/creative-agent-adapters.json` хранит строгую versioned declaration:

- adapter id/version/kind;
- supported creative phases;
- invocation mode;
- declared command runner/path;
- monetary budget;
- network/secrets/filesystem authority declaration;
- timeout/result-channel semantics;
- exact AI Work package/result schema compatibility;
- qualification profile/version/digest;
- exact qualification-report binding.

Registry и qualification report self-sealed SHA-256 digests проверяются fail-closed. Unknown/tampered/stale declaration не становится executable authority.

## Runtime boundary

Обычный raw `ADWF_AGENT_COMMAND` больше не является достаточной authorization surface:

- raw command без `ADWF_AGENT_ADAPTER_ID` → fail closed;
- raw command вместе с adapter id не может переопределить declared command → fail closed;
- неизвестный/unqualified adapter → fail closed;
- result обязан соответствовать exact package/base/phase и существующему `AIWorkResult` contract;
- для локального command path дополнительно проверяются Git HEAD, ancestor/base, exact changed paths и чистый worktree;
- timeout, non-zero exit, missing/invalid result блокируют выполнение.

При отсутствии qualified command adapter Runtime Supervisor сохраняет существующее честное состояние `WAITING_AGENT / CREATIVE_AGENT_RESULT_REQUIRED`. GitHub Agent Inbox остаётся отдельным bounded low-trust handoff/return channel.

## Environment / authority

Command child environment строится из минимального allowlist. Secret-like host variables (`TOKEN`, `SECRET`, `PASSWORD`, `API_KEY`, credentials/private keys) не наследуются. Framework передаёт только bounded ADWF request/result/run/phase/adapter metadata и declared authority markers.

Declaration не является OS/network sandbox. `network=NONE` означает contract/invocation boundary для mandatory reference path; она не выдаётся за packet/domain isolation primitive.

## Deterministic reference adapter

`reference-local` предназначен только для qualification/conformance:

- deterministic Python executor;
- no LLM;
- no external API;
- no Internet/provider/GitHub/device write;
- `monetary_budget_usd=0`;
- `network=NONE`;
- `secrets=FORBIDDEN`;
- `filesystem=PACKAGE_SCOPED`;
- работает только с exact package и пишет только разрешённый synthetic fixture path;
- для использования через production command executor требуется явный test-only opt-in `ADWF_ALLOW_REFERENCE_AGENT=1`.

Он **не является intelligent/production agent** и не доказывает качество реального LLM/Codex/другого external Creative Agent.

## Qualification harness

`.adwf/scripts/qualify_creative_agent.py` выполняет полностью offline disposable proof:

1. создаёт отдельный temporary Git consumer workspace;
2. компилирует exact `AIWorkPackage`;
3. запускает reference adapter через sanitized environment;
4. сверяет exact result/base/head/changed paths и clean worktree;
5. доказывает rejection forbidden write surface;
6. доказывает rejection stale/substituted base;
7. связывает результат с canonical qualification report.

Runtime request/result files размещаются вне synthetic consumer Git tree, чтобы qualification evidence не маскировало mutation consumer workspace.

## Truth boundary

`CREATIVE_AGENT_QUALIFICATION = LIVE_NOT_VERIFIED` после AGENTQUAL-001.

Synthetic provider run может доказать реализацию qualification contract и deterministic reference path, но **не** реального внешнего Creative Agent. `LIVE_VERIFIED` требует отдельного real external adapter/agent result, привязанного к exact `AIWorkPackage`, и downstream trusted/provider exact-SHA evidence.

AGENTQUAL-001 не является Human-by-Exception E2E, не утверждает unattended creative quality и не даёт `FOUNDATION_READY`.
