# Внешние инженерные ориентиры ADWF v1.6

ADWF не делает перечисленные сервисы обязательными зависимостями. Они используются как источники инженерных паттернов.

## GitHub

- GitHub-hosted runners: https://docs.github.com/actions/using-github-hosted-runners/about-github-hosted-runners
- Actions security: https://docs.github.com/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions
- Repository rulesets: https://docs.github.com/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets
- Artifact attestations: https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations

Заимствовано: disposable hosted runners, default-branch trusted controller, required checks, ruleset readback, attestable release evidence.

## Playwright

- CI: https://playwright.dev/docs/ci
- Visual comparisons: https://playwright.dev/docs/test-snapshots

Заимствовано: reproducible browser environment, desktop/mobile preview, stable CI configuration.

## Vercel

- Toolbar: https://vercel.com/docs/vercel-toolbar
- Comments: https://vercel.com/docs/comments

Заимствовано: обсуждать прежде всего визуальный результат, отделять preview от promotion. Vercel не является mandatory provider ADWF.

## GitLab

- Issue Boards: https://docs.gitlab.com/user/project/issue_board/
- CI compute usage: https://docs.gitlab.com/ci/pipelines/compute_minutes/
- Duo Agent Platform: https://docs.gitlab.com/user/duo_agent_platform/

Заимствовано: issue-driven workflow, sessions/agent flows и board projection. Paid/shared-quota AI не является обязательным correctness path.

## Claude Code

- Permissions: https://code.claude.com/docs/en/permissions
- Hooks: https://code.claude.com/docs/en/hooks

Заимствовано: permissions/hooks исполняются harness-ом; deny не заменяется обещанием модели; session resume опирается на durable context.

## OpenAI Codex

- Codex security/enterprise guidance: https://openai.com/index/running-codex-safely/

Заимствовано: sandbox/approval/network boundaries и хранение credentials вне workspace. Codex остаётся Creative Plane, а не источником `Machine Verified`.

## Claude Engineer

- OSS project: https://github.com/Doriandarko/claude-engineer

Заимствована только идея обнаружения capability gap и предложения инструмента через sandbox/governance. Runtime self-extension trusted plane без проверки в ADWF запрещён.
