# Durable автоматическая оркестрация v1.3

## GitHub

`adwf-pr.yml` исполняет PR code read-only и является быстрым feedback. После него `adwf-control.yml` запускается через `workflow_run`, checkout выполняется только по default branch, а все PR/Issue данные считаются недоверенными.

Controller:

- связывает событие с точным provider HEAD/merge SHA;
- читает exact base…head diff через provider API;
- загружает trust policy из base SHA;
- требует allowlisted check/reviewer provenance;
- проверяет один Roadmap ID, Issue, lease UUID, workspace identity и свежий heartbeat;
- не выполняет READY→IN_PROGRESS без unified authorization/claim;
- применяет transition только через durable ETag/CAS saga.

Feature + trust files дают `BLOCK`. Protected-only PR требует `GOV-*`, R4 и allowlisted human approval для exact SHA. Текст PR сам по себе не является разрешением.

После claim Issue содержит один marker:

<!-- adwf-doc: skip(reason=contextual-example) -->
```text
<!-- ADWF-CONTRACT Roadmap-ID: RM-42 Writer: writer-login Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 Workspace: rm-42-issue-17 State: IN_PROGRESS Heartbeat: 2026-08-13T10:00:00Z Expires: 2026-08-13T12:00:00Z -->
```

Несовпадение label/state/marker/workspace, invalid UUID, future/stale heartbeat или expired lease означает `RECONCILE/RECOVERY`.

Remote Issue transition: provider read → ETag/CAS add target label → marker body patch → remove old label → readback. Crash возобновляет тот же idempotency key; concurrent human edit не перезаписывается. Затем выполняются live reconciliation, optional Project projection/readback и обновление одного Dashboard Issue.

## Полный цикл

`.adwf-runtime/orchestration/<run>.json` хранит:

`RECONCILE → AUTHORIZE → CLAIM → WORKSPACE → EXECUTE → OPEN_PR → CI → REVIEW → PREVIEW → OWNER_ACCEPTANCE → MERGE → PROMOTE → OBSERVE → DONE → CLEANUP → NEXT`.

Внешний adapter исполняет только предложенную фазу и возвращает result. Ядро перед каждым шагом заново применяет Effective Policy, policy hash, provider/cost, exact SHA и evidence. Journal имеет revision CAS, idempotency, deadline, retry/cycle budgets и hash-chain. Повреждённый незавершённый run блокирует нового Writer.

## GitLab

MR pipeline read-only. Trusted control работает только из default branch, сериализуется `resource_group`, использует `adwf-trusted` и не включает shared quota автоматически. GitLab adapter проверяет allowlisted API domain и выдаёт общий exact-SHA snapshot. Default `retry: 0`; selective retry допустим только для точно классифицированного runner/API transient failure.

## Workspace

Claim создаёт isolated git worktree. Повторный запуск возобновляет тот же workspace. Registry heartbeat, workspace и worker identity входят в unified authorization boundary. Dirty/незавершённый worktree не удаляется; `--force` запрещён.

## ChatGPT/Codex

Интерактивный ИИ формирует Product Brief, план, branch/PR, changelog и candidate fix. После каждого завершённого item adapter возвращается в `NEXT`. API-backed action не является mandatory CI и выключен в `FREE_ONLY`; correctness не зависит от памяти или обещания модели.
