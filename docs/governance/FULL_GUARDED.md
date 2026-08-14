# Профиль FULL_GUARDED

Используется только когда repository rulesets/required checks/reviews реально доступны и проверены. Это не повышает autonomy автоматически: старт остаётся A1, A2/A3 требуют отдельной evidence-based certification. A4 human-only.

Рекомендуемые controls: PR-only main, exact required checks, force-push/deletion blocked, up-to-date policy, trusted release controller и отдельный production approval. Платная platform feature не становится обязательной зависимостью ADWF; если она недоступна, используется FREE_PRIVATE.

