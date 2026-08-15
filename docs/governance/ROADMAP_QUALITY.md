# Roadmap Quality Engine (RQE)

RQE делает Roadmap тестируемым артефактом и не позволяет считать количество закрытых Issues эквивалентом готового результата.

## Каноническая модель ROADMAP-001

ADWF использует **один Roadmap DAG**. `dependencies` — это проверяемые рёбра графа, а не комментарии для человека. Перед проекцией прогресса система детерминированно проверяет:

1. уникальность `Roadmap ID`;
2. отсутствие ссылок на неизвестные зависимости;
3. отсутствие self-dependency;
4. отсутствие циклов;
5. фактическую готовность зависимостей.

Любая ошибка графа переводит DAG projection в `FAIL` и обнуляет автоматический ready frontier. Fail-open интерпретация запрещена.

## Verified Outcome Progress

Executive Roadmap показывает три разные оси:

- **Implementation** — работа дошла до REVIEW / VERIFICATION / DONE;
- **Verification** — завершённые work items подтверждены свежим exact-main provider snapshot;
- **Outcome Readiness** — verified work с влиянием на продукт дополнительно подтверждён здоровым Product Health.

`DONE` сам по себе не является свежим evidence. Если provider snapshot отсутствует, устарел, относится к другому SHA или ADWF health не подтверждён, Verification остаётся 0. Если Product Health `BROKEN`, `NOT_VERIFIED` или `STALE`, Outcome Readiness не может стать 100%.

Compatibility field `product_done` является алиасом `outcome_ready`, а не счётчиком закрытых Issues.

## Dependency truth и выбор следующей работы

`dependencies_resolved` в provider projection вычисляется из канонического состояния зависимостей и проверяется против self-claim Issue. Расхождение = reconciliation error.

DAG дополнительно вычисляет:

- `blocked_by` для каждого work item;
- `ready_frontier` — реально доступные узлы;
- `critical_path_score` — детерминированную downstream-глубину, которую уже использует штатный `choose_next()`;
- `critical_path` для executive projection.

Это связывает Roadmap truth с существующим orchestration engine без создания второго планировщика.

## Остальные проверки RQE

1. Issue Quality Gate.
2. Oversized/undersized detection.
3. Scope drift по фактическому diff.
4. False Progress Detector.
5. Verification Gap.
6. Capability coverage и orphan work.
7. Dependency cycle/dead-end/starvation.
8. Architecture drift и ADR requirement.
9. Technical Debt Ledger/Budget/Interest.
10. QCP/ACP checkpoints.
11. Stale READY/specification revalidation.
12. Product/Engineering value balance.

**Количество закрытых Issues не является показателем Product Readiness.**

`AIWorkPackage`, `AIWorkResult` и Decision/Requirement Traceability сознательно не входят в ROADMAP-001 и остаются следующим отдельным capability v1.7.
