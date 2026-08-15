# Contract: adwf-govern

Назначение: bounded router для truth/governance/review/security intents.

Инварианты:
- evidence не создаётся текстовым утверждением агента;
- unknown не повышается до PASS;
- owner authorization остаётся exact-SHA и provider-authenticated там, где этого требует policy;
- router не исполняет protected trust logic.
