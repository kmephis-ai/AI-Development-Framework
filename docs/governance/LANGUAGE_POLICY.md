# Language Policy

Русский язык — основной язык human-facing слоя ADWF.

## MUST писать по-русски

Issues и их описания, PR title/description, README, Roadmap, пользовательская документация, CHANGELOG и Release Notes, audit/review findings, отчёты владельцу, комментарии AI, human-facing GitHub Project fields.

## English сохраняется

Machine-facing identifiers, JSON/YAML keys, API/schema/library/protocol names, code symbols, branch/path/environment names, CLI command names, стабильные machine states (`READY`, `PASS`, `R2`, `A2`) и tooling-required значения.

Пример: `PERF-021: сократить время первой загрузки Dashboard`, label `roadmap:ready`, branch `perf/021-dashboard-load`.
