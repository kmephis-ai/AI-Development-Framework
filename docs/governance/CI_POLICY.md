# Политика CI/CD

CI доказывает только выполненные deterministic checks. Он не заменяет Product Health.

- Mandatory checks не вызывают AI/API.
- Default executor — self-hosted runner; hosted quota providers выключены.
- GitHub PR workflow имеет только `contents: read`; credentials не сохраняются в checkout.
- Mutating controller исполняет code только из default branch, сериализован, читает trust policy из base SHA и классифицирует provider diff без PR checkout.
- Fast PR validation не считается trusted merge gate; exact checks/reviews требуют allowlisted provenance.
- Third-party Actions pinned на full commit SHA; GitLab includes только local.
- Python — `3.12.13`; Node-проект — Node `24.x`.
- Project commands — непустые arrays, `shell=False`. Пустая required команда = `NOT_VERIFIED` + fail.
- Cache только lockfile-keyed; cache miss не влияет на correctness. Артефакты default `FAILURE_ONLY`, retention 1 day.
- Concurrency отменяет только superseded read-only checks. Control/release не отменяются.
- Deployment и release отделены; production всегда human-gated.

Root `.github/workflows` состоит из `adwf-pr.yml`, `adwf-main.yml`, `adwf-control.yml`, `adwf-release.yml`. Root `.gitlab-ci.yml` является картой локальных include.
