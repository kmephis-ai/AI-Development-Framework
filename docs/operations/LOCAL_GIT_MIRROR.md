# Connector Local Git Mirror

## Назначение

`Connector Local Git Mirror` позволяет ADWF получить полноценный локальный Git-репозиторий в AI-среде, где GitHub доступен через connector, но обычные DNS/HTTPS/`git clone` из вычислительного контейнера заблокированы.

Это не прокси и не попытка снять сетевую изоляцию. GitHub остаётся Source of Truth. Connector используется как разрешённый transport control plane, GitHub Actions — как краткоживущий упаковщик точного provider-side snapshot, а локальный контейнер получает проверенный `git bundle`.

## Что становится доступно

После успешной материализации AI может локально использовать настоящие Git-операции: `log`, `diff`, `merge-base`, `worktree`, history-aware generators, статический анализ и полный test suite. Если прямой outbound Git остаётся закрыт, `fetch/pull/push` выполняются не локальным Git-клиентом, а через разрешённый GitHub Connector и затем подтверждаются provider readback/CI.

## Безопасная схема

1. Сначала выполняется короткая проверка прямого Git. Если он работает, mirror не запускается.
2. Connector читает точный source SHA.
3. От этого SHA создаётся одноразовая transport-ветка.
4. На неё помещается минимальный workflow из `skills/adwf-local-git-mirror/resources/`.
5. Runner с `fetch-depth: 0` создаёт bundle только для синтетического ref, указывающего на заранее зафиксированный source SHA.
6. Preferred lane публикует bundle как Actions artifact на 1 день и имеет только `contents: read`.
7. Connector скачивает artifact bytes в AI-контейнер.
8. Materializer проверяет ZIP, manifest, SHA-256, размер, `git bundle verify`, наличие exact SHA, итоговый HEAD и `git fsck`.
9. Только после PASS локальный workspace допускается к анализу/тестам.

## Почему workflow не хранится как постоянно активный trigger

Постоянный публичный event-trigger на `main` создавал бы ненужную поверхность для случайных/лишних Actions runs. Поэтому основной дизайн — ephemeral transport branch. Это немного сложнее внутри автоматики, но не требует действий владельца и лучше соответствует FREE_ONLY, least privilege и fail-closed.

## Fallback

Если конкретная версия GitHub Connector умеет читать файлы репозитория, но не умеет скачать Actions artifact bytes, Skill использует отдельный fallback-template. Он Base64-кодирует bundle и публикует небольшие chunks только на disposable transport-ветке. Этот lane требует `contents: write`, поэтому он не включён в preferred workflow.

## Trust boundary

Local Mirror не даёт локальному checkout права автоматически объявлять provider state истинным. После локальных изменений остаются обязательными обычные ADWF правила: отдельная remote branch, provider readback, exact-head checks, policy/owner gates для соответствующего риска и отсутствие bypass.

## Повторное использование в новых AI-сессиях

Канонический playbook находится в `skills/adwf-local-git-mirror/SKILL.md`. `AGENTS.md` должен направлять AI к этому Skill, когда обычный Git transport недоступен. Структура `SKILL.md + resources + scripts` намеренно переносима и может использоваться как Agent Skill в поддерживающих этот стандарт средах.
