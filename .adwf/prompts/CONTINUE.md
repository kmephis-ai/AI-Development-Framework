# «Делай далее» — нормативный prompt

Следуй `AGENTS.md` и исполняемым policy/state/evidence engines ADWF v1.6.

1. Получи свежие repository/runtime facts и раздельный Health; неизвестное не считать PASS.
2. Выполни reconciliation. При split-brain, нескольких Writer или stale mandatory evidence останови feature work.
3. Сначала Recovery/unfinished Review/Verification.
4. При ровно одном active Writer продолжи только его `IN_PROGRESS` Issue.
5. Без Writer выбери ровно один dependency-ready `READY` Issue из единственной Roadmap; получи atomic lease, isolated workspace и запиши/readback строгого Issue marker с Writer/lease/workspace/heartbeat/expiry.
6. Выполни один scope, exact-SHA CI, independent review и требуемое runtime evidence.
7. Merge/deploy только при `ALLOW`. После DONE автоматически повтори с шага 1.
8. Остановись только на Roadmap end или конкретном hard blocker, требующем владельца. Владелец получает одно понятное решение/вопрос по-русски.
