# ADWF v1.6 scripts

Основной интерфейс: `python .adwf/adwf.py <command>`.

Commands: `doctor`, `status`, `validate`, `issue-audit`, `roadmap-audit`, `permission`, `continue`, `claim`, `reconcile`, `transition`, `evidence-verify`, `provider-check`, `self-test`, `render-control-center --format md|html`, `policy-compile`, `workspace-*`, `metrics-summary`, `orchestration-*`, `incident-*`, `healing-*`, `owner-*`, `ci-setup-plan`.

`run_project_gates.py` исполняет project-specific command arrays без shell. Пустая optional команда = `N/A`; пустая required = `NOT_VERIFIED` и failure. Для product bootstrap дополнительно обязательны реальный PR/main/unit gate и runtime smoke/Golden Paths, если продукт развёртывается.

`validate_ci.py` блокирует unpinned/unlocked Actions, non-Node24 action runtime, PR secrets/write permission, mixed runner trust domains, floating runtime, metered AI secrets, незарегистрированные containers, remote GitLab include и pipe-to-shell. `github_reconcile.py`/`gitlab_reconcile.py` пишут live snapshot в `.adwf-runtime`; `project_item_sync.py` и `sync_dashboard_issue.py` обновляют projection.
