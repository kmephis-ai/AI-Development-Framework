# Contract: adwf-develop

Назначение: bounded router для developer execution. Router классифицирует intent и подключает только подходящий managed leaf Skill.

Инварианты:
- startup-visible router не выполняет роль leaf procedure;
- deterministic exact-SHA, policy, CI и security enforcement остаются в коде ADWF;
- при отсутствии подходящего leaf Skill router не выдумывает несуществующую capability;
- progressive disclosure важнее загрузки всего каталога Skills.
