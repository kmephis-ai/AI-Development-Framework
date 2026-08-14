# GitHub Project: ADWF Control Center

`.adwf/project-layout.json` — canonical specification полей и представлений. Первый уровень владельца: `Главное для владельца`, `Посмотреть и принять`, `Требует владельца`, `Инциденты и восстановление`. Он показывает Preview, Machine Verified, Owner Accepted, incidents и Safe Healing. Инженерные views (`Сейчас`, `Roadmap и риски`, `Готово к работе`, `Качество и Reality`, `Безопасность и стоимость`, `CI performance`, `Releases и deployments`, `Debt и Architecture`) остаются вторым уровнем.

Project является удобным UI, но не выше runtime/repository evidence. Labels и fields должны отражать state engine. При split-brain выставляется `control:split-brain`, feature work блокируется и выполняется reconcile.

`sync_labels.py` создаёт или исправляет только canonical ADWF labels и не удаляет пользовательские. `project_bootstrap.py` создаёт отсутствующие fields dry-run-first. `project_item_sync.py --from-state --apply` использует реальные node/field/option IDs, повторно читает item после записи и только затем записывает `project_projection=PASS` в runtime snapshot. Для активной GitHub Issue отсутствие свежего readback не позволяет Control Plane стать `VERIFIED`. GitHub CLI не гарантирует воспроизводимое создание всех типов views, поэтому ИИ сверяет views отдельно и не заявляет, что view создан, пока не проверит интерфейс.

Один pinned Dashboard Issue содержит человеческий результат и технический fallback. `CONTROL_CENTER.html` даёт тот же escaped CEO view без внешней библиотеки/hosting; Markdown остаётся canonical бесплатным fallback. Controller обновляет body и читает его обратно; комментарий на каждый transition запрещён.

Чтобы не засыпать почту, ADWF не создаёт комментарий на каждый успешный check. Нормальный канал — Checks и одна обновляемая панель. Настройки account-level email notifications меняет только владелец или ИИ через явно предоставленный UI-доступ.
