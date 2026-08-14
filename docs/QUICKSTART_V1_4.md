# ADWF v1.4 — проверяемый быстрый старт

Этот документ содержит минимальные machine-executable примеры. Остальные контекстные фрагменты документации явно помечены `skip(reason=...)`, чтобы пример нельзя было ошибочно считать проверенным.

## Проверка CLI

<!-- adwf-doc: run -->
```bash
python .adwf/adwf.py --help
```

## Проверка Pipeline IR

<!-- adwf-doc: run -->
```bash
python .adwf/scripts/validate_pipeline_ir.py
```

## Профиль по умолчанию

<!-- adwf-doc: parse -->
```json
{
  "profile": "FREE_PUBLIC_GITHUB",
  "runner": "ubuntu-24.04",
  "mandatory_ai_api_calls": 0,
  "projected_cost_usd": 0,
  "larger_runners": "BLOCK"
}
```
