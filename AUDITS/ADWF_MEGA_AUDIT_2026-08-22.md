# Mega Audit AI-Development-Framework (ADWF)

**Дата среза:** 22 августа 2026 года  
**Репозиторий:** [kmephis-ai/AI-Development-Framework](https://github.com/kmephis-ai/AI-Development-Framework)  
**Проверенный main:** [29b95ceba823469005a0eef4e6d7d1c3b412814e](https://github.com/kmephis-ai/AI-Development-Framework/commit/29b95ceba823469005a0eef4e6d7d1c3b412814e)  
**Режим аудита:** независимый, read-only; репозиторий и GitHub-состояние не изменялись.

> **Пост-аудитный статус, 22 августа 2026:** finding P0-3 о воскрешении завершённой Runtime Ledger работы исправлен отдельной защищённой транзакцией GOV-030. PR [#263](https://github.com/kmephis-ai/AI-Development-Framework/pull/263) squash-merged без bypass в `main@4cd9e6eaa8b36ddc1ec4476c51b77671a6fc5275`; merge tree совпал с проверенным candidate tree, post-merge Main и Ubuntu/Windows smoke прошли, lease освобождён, активных writers — 0. Остальные выводы аудита не считаются автоматически исправленными; в частности, `SELFTEST_COVERAGE-001/#253` остаётся открытым. Эта заметка не меняет исторический baseline отчёта.

## 0. Главный вывод

ADWF имеет право на существование, но не в той форме, к которой его сейчас подталкивает собственный roadmap.

У проекта есть ценное и достаточно редкое ядро идеи: быть независимым от конкретной AI-модели контуром управления разработкой — хранить намерение владельца, ограничивать полномочия AI, фиксировать доказательства, восстанавливаться после сбоев и показывать человеку только решения, которые действительно требуют человека. Ни Cursor, ни Codex, ни Claude Code, ни отдельный coding-agent SDK полностью эту задачу не решают.

Однако текущее фактическое состояние ADWF существенно слабее архитектурного замысла:

- как **исследовательский прототип и набор строгих контрактов** — примерно 6/10;
- как **реально работающая автономная система разработки для владельца** — примерно 3/10;
- как **готовый продукт, которому можно доверить полный цикл изменения внешнего проекта** — пока 2/10.

Эти оценки не являются математикой. Это шкала аудита: насколько заявленные свойства подтверждены живым сквозным циклом, а не количеством кода, тестов или документов.

Главная проблема ADWF — не отсутствие очередного слоя архитектуры. Главная проблема — разрыв между:

1. тем, что система говорит о себе;
2. тем, что код теоретически умеет;
3. тем, что реально связано в работающий контур;
4. тем, что независимо доказано на внешнем продукте;
5. тем, что видит владелец.

На текущем main этот разрыв уже приводит к четырём критическим последствиям:

1. канонический self-test показывает полный успех, хотя часть тестов вообще не собирается, а семь пропущенных проверок фактически падают;
2. сырой текст задачи владельца может попасть в публичный GitHub Issue, несмотря на прямое обещание обратного;
3. изменения самого доверенного ядра могут быть ошибочно классифицированы как низкорисковые и пройти без человека;
4. завершённая runtime-работа может воскреснуть после восстановления, потому что терминальное состояние не сохраняется как единая истина.

Поэтому правильное решение — **не закрывать проект**, а провести короткий жёсткий этап Truth & Safety Reset: заморозить расширение архитектуры, восстановить достоверность тестов и состояния, доказать один скучный полный цикл на реальном потребителе и сократить process tax минимум вдвое. Только после этого имеет смысл возвращаться к fleet, multi-writer, skill factory, расширенному UI и другим большим идеям.

## 1. Что именно было проверено

Аудит охватывает систему как единый контур, а не набор отдельных файлов:

- live-состояние GitHub: main, branches, PR, issues, Actions, rulesets, tags и releases;
- код CLI, runtime, durable orchestration, trust, lease, evidence, consumer upgrade, preview, owner portal;
- конфигурации, политики, JSON Schema, capability trace, decision trace, roadmap, generated projections;
- workflow trusted/untrusted контуров;
- тестовый раннер и фактическое обнаружение тестов;
- локальный путь от задачи владельца до попытки продолжить runtime;
- импортную связность и наличие невключённых в production модулей;
- безопасность входов, полномочий, публичных данных, supply chain и recovery;
- соответствие «заявлено — реализовано — связано — доказано»;
- стоимость собственного процесса;
- необходимость UI;
- целесообразность дальнейшего развития и альтернативы.

### 1.1. Ограничения аудита

Аудит привязан к публичному репозиторию и публично наблюдаемому GitHub-состоянию. Не были доступны:

- приватные репозитории потребителей;
- реальные production-секреты и deployment adapter;
- закрытые логи провайдеров;
- платные или внутренние AI-исполнители;
- доказательства, не опубликованные в репозитории или Runtime Ledger.

Поэтому отсутствие доказательства обозначается как NOT_VERIFIED, а не как доказанное отсутствие функции.

Специализированный инструмент многопроходного Codex Security Deep Scan в этой среде был недоступен. Выполнен ручной статический аудит границ доверия, потоков данных и ключевых source-to-sink путей, но этот отчёт не выдаётся за формальный машинный deep-scan.

### 1.2. Важный контекст возраста проекта

Публичный репозиторий создан 14 августа 2026 года. Проверенный срез сделан 22 августа — проекту в этой форме около восьми дней. Это объясняет часть незрелости, но не отменяет критических дефектов: особенно опасно, когда молодая система уже создаёт очень сильное впечатление формальной завершённости.

## 2. Краткая карточка фактического состояния

| Показатель | Факт на срезе | Что это означает |
|---|---:|---|
| Файлов | 578 | Уже большая поверхность сопровождения |
| Строк в учитываемых текстовых файлах | около 66 482 | Для восьмидневного проекта очень высокая скорость расширения |
| Python-модулей | 241 | Архитектура уже существенно модульная |
| Модулей в .adwf/lib | 87 | Достаточно большая внутренняя платформа |
| Тестовых файлов | 105 | Значительная тестовая поверхность, но сбор тестов недостоверен |
| Capability в trace | 34 | Только 2 имеют LIVE_VERIFIED |
| LIVE_VERIFIED capability | 2 из 34 | Доказан небольшой фрагмент полного замысла |
| LIVE_NOT_VERIFIED capability | 24 из 34 | Большая часть существует без живого сквозного доказательства |
| Roadmap items | 55 | Все owner-view показывает как PLANNED |
| Product-impact roadmap items | 4 из 55 | 92,7% roadmap относится к внутреннему контуру |
| Коммитов main | 203 | Очень высокая частота внутренних изменений |
| PR за период | 147 | 73 merged, 74 closed без merge |
| Actions runs | 1 955 | 990 failure, 820 success, 144 cancelled, 1 action_required |
| Веток | около 362 | Сотни технических веток для очень молодого проекта |
| Open issues | 22 | Часть открытых issues уже описывает себя как DONE/merged |
| Open PR | 0 | На момент среза незавершённых PR нет |
| GitHub releases | 0 | Публичной поставки версии нет |
| Семантические version tags | 0 | VERSION=1.6.0 не подтверждён release-артефактом |
| LICENSE | отсутствует | Репозиторий публичный, но юридически не оформлен как open source |

Сумма длительностей последних 100 Actions runs составляет около 1,16 часа wall time. Это не равно billable compute: jobs могут идти параллельно, а GitHub считает минуты иначе. Цифра используется только как индикатор процессной активности.

## 3. Оценка по направлениям

| Направление | Оценка | Вывод |
|---|---:|---|
| Ясность архитектурного намерения | 8/10 | Роли, запреты и границы сформулированы необычно хорошо |
| Локальные контракты и схемы | 6/10 | Много строгих структур, fail-closed проверок и readback |
| Связанность production-контура | 4/10 | Ряд capability существует как файл или тест, но не как вызываемый путь |
| Достоверность тестов и evidence | 3/10 | Канонический зелёный статус скрывает пропущенные падения |
| Операционная безопасность | 3–4/10 | Сильные технические меры подрываются утечкой задачи и ошибкой TCB-классификации |
| Реальный E2E жизненный цикл | 2/10 | Полный intent → product result → observed outcome не доказан |
| Опыт владельца | 2/10 | UI и projections не дают актуальной, однозначной истины |
| Эффективность процесса | 1–2/10 | Внутренняя активность намного опережает доказанную продуктовую ценность |
| Release/adoption readiness | 2/10 | Нет release, LICENSE, стабильного потребительского пути и доказанного upgrade write-back |

Сильная сторона ADWF — он во многих местах честно использует LIVE_NOT_VERIFIED и NOT_VERIFIED. Слабая сторона — рядом существуют generated-проекции и команды, создающие противоположное впечатление: «727/727 PASS», «все 55 задач PLANNED», «progress 0%», при том что десятки задач уже реализованы.

## 4. Как система должна работать и где рвётся контур

Целевой контур можно описать семью звеньями:

| Звено | Назначение | Текущее состояние |
|---|---|---|
| 1. Намерение владельца | Принять задачу понятным языком, не раскрыть секреты | Частично реализовано; есть критический публичный sink сырого текста |
| 2. План и разрешение | Определить риск, границы, кто вправе действовать | Схемы сильные; semantic risk classification неполна |
| 3. Исполнитель | Передать bounded task внешнему coding agent | Есть adapter-модель; стандартный live executor не доказан |
| 4. Provider control plane | Создать branch/PR, запустить CI, применить rules | Для самого ADWF хорошо развито; есть широкие permissions и шумный trigger |
| 5. Независимая проверка | Доказать тесты, preview, security и exact SHA | Механизмы есть; test evidence уже дало ложноположительный итог |
| 6. Доставка и наблюдение | Merge/deploy/observe реальный продуктовый эффект | Для framework-проекта часто N/A; внешний полный цикл не доказан |
| 7. Память и восстановление | Зафиксировать terminal state, evidence, next action | Runtime Ledger существует, но обнаружен дефект terminal persistence |

Критический системный вывод: ADWF лучше всего реализовал середину контура — GitHub governance, схемы, provider readback, lease и транзакционные механизмы. Начало и конец контура существенно слабее:

- владелец ещё не получает безопасный и простой вход;
- продуктовый результат ещё не является главным объектом истины;
- terminal state и roadmap не образуют одну актуальную проекцию;
- внешний потребитель не прошёл повторяемый полный цикл.

Именно поэтому дальнейшее добавление middle-layer архитектуры создаёт ложное чувство прогресса.

## 5. «Заявлено — реализовано — связано — доказано»

Для ADWF недостаточно бинарного «есть код / нет кода». Нужны четыре уровня:

1. **Заявлено** — свойство описано в документации или roadmap.
2. **Реализовано** — существует код или schema.
3. **Связано** — код вызывается из реального production entry point.
4. **Доказано** — живой внешний сценарий дал проверяемый результат.

| Заявление или capability | Реализовано | Связано | Доказано | Вердикт |
|---|---|---|---|---|
| Fail-closed governance | Да | В основном да | Частично | Сильная основа, но риск-классификация доверенного ядра неполна |
| GitHub как control plane | Да | Да | Да для самого ADWF | Хорошо реализовано |
| Полный автономный lifecycle | Частично | Частично | Нет | LIVE_NOT_VERIFIED — корректная фактическая формулировка |
| Durable orchestration | Да | Да | Частично | Реальный P0 показал дефект terminal recovery |
| Session continuity | Да | Частично | Нет | Production-модуль proof не имеет production inbound; тесты пропущены |
| ORCH resume | Да | Нет | Нет | Модуль импортируется тестами, но не production |
| Независимая проверка | Да | Частично | Частично | Provider readback силён, но тестовая база дала ложную зелень |
| Owner task privacy | Sanitizer есть | Обходится другим sink | Опровергнуто | Сырой task может уйти в публичный Issue |
| Consumer upgrade planning | Да | Да | Да | Одна из двух LIVE_VERIFIED capability |
| Consumer upgrade transaction | Да | Да | Да | Доказан disposable A→B→rollback→B |
| Connected consumer write-back | Частично | Частично | Нет | Live evidence прямо фиксирует write_back_performed=false |
| Product Truth | Контракты/идеи есть | Неполно | Нет | Для framework-проекта product gates пусты и N/A |
| Preview | Да | Да | Только smoke | Smoke проверяет запуск и текст, не потребительскую ценность |
| Roadmap как текущая истина | JSON есть | CLI есть | Опровергнуто | Все 55 отображаются как PLANNED, progress 0% |
| Evidence chain | Trace есть | Частично | Неполно | Decision trace заканчивается work: evidence_refs=0 |
| FREE_ONLY | Да | Да | В основном | Хороший принцип; не решает стоимость process tax |
| Vendor neutrality | Архитектурно да | Частично | Неполно | Нужны минимум два квалифицированных executor adapter |

### 5.1. Capability trace

В trace 34 capability:

- 2 — LIVE_VERIFIED;
- 24 — LIVE_NOT_VERIFIED;
- 8 — IMPLEMENTED.

Это не «почти готовая система». Это система, у которой большая часть деталей построена, но очень малая часть полного поведения подтверждена вживую.

### 5.2. Decision trace

В decision trace найдено 39 записей и 93 связи:

- capability references — 15;
- work references — 24;
- evidence references — 0;
- AI work package IDs заполнены не были.

То есть трассировка хорошо объясняет переход от решений к работе, но не замыкает цепочку «решение → изменение → независимое доказательство → продуктовый результат».

## 6. Критические находки

### P0-1. Сырой текст задачи владельца может попасть в публичный GitHub Issue

**Что обнаружено.** В двух production-путях Issue формируется из исходного task:

- [.adwf/lib/owner_intent_service.py](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/lib/owner_intent_service.py);
- [.adwf/lib/action_executors.py](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/lib/action_executors.py).

При этом отдельный product brief действительно sanitizes task, но публичное тело Issue создаётся из первоначальной строки. То есть sanitizer не защищает фактический sink.

**Как проверено.** В disposable-копии использована синтетическая строка, похожая на API key и PII. Product brief её редактировал и сообщил findings_count=3, но сформированное Issue body продолжало содержать исходный sentinel.

**Почему P0.** Публичный репозиторий — профиль по умолчанию. Задача владельца может содержать имя клиента, бизнес-идею, внутренний URL, фрагмент секрета или сведения о дефекте. Система прямо обещает, что raw owner task не попадёт в публичный Issue/ledger, но production-путь нарушает обещание.

**Что сделать.**

1. Никогда не использовать raw task в публичном title/body.
2. Хранить приватный оригинал только в локальном или явно приватном хранилище.
3. В Issue публиковать digest, sanitised projection и ссылку на непрозрачный task ID.
4. Требовать отдельное явное согласие владельца на публикацию текста.
5. Добавить regression-набор с синтетическими secrets, PII, URL, кодом и Unicode.
6. Проверять не только sanitizer, но каждый внешний sink.

### P0-2. Канонический self-test выдаёт ложный полный успех

Команда self-test сообщила:

    Ran 727 tests in 80.735s
    OK

Но фактический аудит discovery показал:

- 17 TestCase-сценариев импортируются и выполняются повторно;
- нормализованное уникальное количество — около 710;
- два pytest-style модуля не собираются canonical runner:
  - .adwf/tests/test_session_delivery.py;
  - .adwf/tests/test_session_consumer_proof.py;
- в них 11 top-level test functions;
- при прямом запуске функций: 4 PASS, 7 FAIL;
- падения связаны с checkpoint_id=cp-1, который не проходит действующую schema.

Проблема уже отражена в [Issue #253](https://github.com/kmephis-ai/AI-Development-Framework/issues/253), но на проверенном main не исправлена.

**Почему P0.** ADWF продаёт не количество тестов, а доказательство. Если сама система доказательств пропускает падающие проверки и одновременно завышает число успешных, вся последующая governance-цепочка теряет доверие.

**Что сделать.**

1. Один канонический collection-механизм, совместимый со всеми стилями тестов.
2. Manifest ожидаемых test modules; отсутствие collection любого файла — failure.
3. Уникальный stable test ID и защита от двойного импорта.
4. Машиночитаемый отчёт collected/executed/skipped/failed.
5. Исправить семь тестов либо схему, но не скрывать несовместимость.
6. Добавить meta-test, который намеренно создаёт uncollected test и обязан сломать CI.

### P0-3. Runtime Ledger может воскресить уже завершённую работу

В текущем коде terminal state не входит в выборку для persistence, а recovery поднимает последнюю RUNNING-запись без обязательного повторного чтения terminality source Issue.

Инцидент [Issue #259](https://github.com/kmephis-ai/AI-Development-Framework/issues/259) был оперативно локализован:

- текущий immutable lease anchor revision 11 показывает все четыре lease как RELEASED;
- активных lease на момент среза нет;
- source branch отсутствует;
- открытых PR по ремонту нет.

Это означает, что конкретный инцидент остановлен. Но общий дефект в проверенном main не устранён: следующий завершённый или заблокированный run может быть восстановлен как активный.

**Системный урок.** Lease release не равен исправлению durability. Нужен единый terminal record, который сохраняется до cleanup и имеет приоритет над старым RUNNING snapshot.

**Что сделать.**

1. Persist COMPLETE/BLOCKED/ABORTED, а не только ACTIVE.
2. Перед resume всегда re-read canonical work item и terminal markers.
3. Ввести monotonic terminal sequence и запрет terminal → running без новой owner-authorized attempt.
4. Проверить crash в каждой точке между merge, Issue update, ledger append, lease release и cleanup.
5. Провести live recurrence test, а не только unit test.

### P0-4. Изменения доверенного ядра могут ошибочно пройти как R1/AUTO

Trust policy защищает .adwf/** в общем смысле, однако список manual_required_paths не включает несколько центральных authority-модулей:

- action_executors.py;
- runtime_supervisor.py;
- durable_orchestrator.py;
- github_runtime_store.py;
- github_agent_inbox.py;
- lease registry/store.

[PR #257](https://github.com/kmephis-ai/AI-Development-Framework/pull/257) менял CLAIM authority, provider-durable lease binding, terminal cleanup и Issue closing, но был объявлен R1/AUTO без human review.

**Почему это критично.** Именно эти модули решают, кто имеет право действовать, какая работа считается активной и когда можно закрыть её следы. Это Trusted Computing Base, даже если путь файла не называется policy или trust.

**Что сделать.**

1. Создать явный TCB inventory по смыслу, а не только по pathname.
2. Любое изменение authority, lease, evidence, terminality, CI-controller и public/private sink автоматически не ниже R3.
3. Добавить semantic tests: изменение вызова release/claim/close должно повышать риск.
4. Ввести independent review для TCB и exact-head owner approval для R4.
5. Проверять call-graph reach и privilege delta, а не только список файлов.

## 7. Риски высокого уровня

### P1-1. Публичный comment может запускать write-privileged controller

Trusted controller реагирует на issue_comment в публичном репозитории и получает широкие permissions: contents, actions, checks, statuses, issues и pull-requests. Код PR в trusted job не исполняется, checkout идёт с persist-credentials=false — это сильная защита. Но сам trigger остаётся низкодоверенным и может запускать дорогую сериализованную обработку.

Парсер agent result рассматривает контент как низкодоверенный, но trigger authorization не привязан достаточно жёстко к approved actor/app, nonce или canonical Agent Inbox event.

Риск — не прямое выполнение текста комментария как shell, а availability/DoS и confused deputy: недоверенный пользователь инициирует контур с широкими полномочиями.

GitHub для собственного coding agent отдельно описывает ограничения недоверенных событий; OWASP рекомендует минимизировать полномочия agentic-системы: [GitHub Copilot coding agent risks](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations), [OWASP Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/).

Рекомендация: дешёвый read-only front-door job должен проверить author association, approved app identity, event type и one-time capability token. Только после этого отдельный минимально-привилегированный job получает конкретное разрешение. Permissions надо разделить по jobs, а не выдавать одному controller всё сразу.

### P1-2. Capability validator проверяет наличие файлов, но не реальную связность

validate_capabilities.py в основном подтверждает, что declared paths существуют, а несколько workflow token встречаются где-то в workflow. Он не доказывает, что конкретная capability достижима из production entry point.

В результате возможна формально «production-wired» capability, чей модуль никто не вызывает.

Найдены пять lib-модулей без production inbound import:

| Модуль | Фактическая связь |
|---|---|
| lib.orch_resume | Импортируется тестами, но не production |
| lib.provider_events | Импортируется тестами, но не production |
| lib.session_consumer_proof | Импортируется тестами, но не production |
| lib.trust_boundary | Тесты/registry; trusted gate фактически использует trust.py |
| lib.preview | Не найдено inbound imports |

Особенно значимы orch_resume и session_consumer_proof: они относятся к заявленной continuity, но наличие кода не равно работающему resume.

Рекомендация: capability trace должен содержать не path, а entry point, ожидаемую цепочку вызовов, live scenario ID и evidence ID. CI обязан доказать достижимость или честно поставить IMPLEMENTED_NOT_WIRED.

### P1-3. Project state, roadmap, issues и portal расходятся

Текущий project-state остаётся BOOTSTRAP:

- autonomy A1/R1, хотя effective config содержит A2/R1;
- product/control health — NOT_VERIFIED;
- progress — 0%;
- main — null;
- runtime — not verified;
- queue/task отсутствуют;
- workspace не настроен.

Команда roadmap-view показывает все 55 задач как PLANNED, implemented=0, verified=0, хотя десятки PR уже merged. Roadmap JSON не содержит реального lifecycle status. Open issues могут оставаться открытыми, одновременно описывая себя как DONE и protected main merged.

CONTROL_CENTER и owner portal читают stale local projections. Кнопка Continue в локальном E2E сообщила только «Autopilot: HUMAN_REQUIRED», не объяснив GITHUB_CONNECTION_REQUIRED.

Это не косметический дефект. Для владельца UI становится источником неверной управленческой информации.

Рекомендация: одна каноническая цепочка:

    provider readback → immutable event/evidence → derived current state → roadmap/portal

У каждой проекции нужны observed_at, source revision и stale badge. Нельзя обновлять один dashboard вручную или генератором отдельно от terminal event.

### P1-4. Полный внешний product loop не доказан

Только consumer upgrade planning и consumer upgrade transaction имеют LIVE_VERIFIED. Доказательство показывает disposable A→B→rollback→B, но прямо фиксирует write_back_performed=false.

Не доказан единый повторяемый сценарий:

    owner intent → bounded work → external agent → commit → PR → CI →
    preview/product acceptance → merge → delivery → observation →
    terminal ledger → next owner-visible state

Для framework-репозитория product gates пусты и корректно становятся N/A. Но из этого следует, что сам ADWF не может служить главным доказательством своей продуктовой ценности. Нужен реальный consumer.

### P1-5. Исполнение не изолировано на уровне ОС

Документация честно обозначает network policy как DECLARATION_ONLY_NOT_ENFORCED. Есть disposable clone и sanitised environment, но нет доказанной изоляции host filesystem, network egress и runtime privileges.

До подключения внешнего автономного agent это терпимый прототипный долг. После подключения — security boundary. Нельзя называть профиль AGENT_RUNTIME_SAFE, пока ограничения являются декларацией.

### P1-6. Process tax уже превосходит доказанную ценность

За примерно восемь дней:

- 203 main commits;
- 147 PR;
- 1 955 Actions runs;
- около 362 веток;
- 55 roadmap items;
- 51 из 55 задач не имеют прямого product impact;
- generated-файлы менялись в десятках коммитов: SHA256SUMS — 88, docs registry — 84, MANIFEST — 82.

Эти числа не являются мерой продуктивности. Они показывают внутреннюю нагрузку. Методика SPACE прямо предупреждает, что продуктивность нельзя свести к activity count: [SPACE framework](https://queue.acm.org/detail.cfm?id=3454124). DORA предлагает балансировать throughput и instability: [DORA metrics](https://dora.dev/guides/dora-metrics/).

Сейчас ADWF оптимизируется на доказательство собственного процесса, а не на число завершённых полезных возможностей внешнего продукта.

## 8. Что сделано хорошо

Жёсткая критика не должна скрывать реальные достижения:

1. **Правильная модель полномочий.** В AGENTS.md очень ясно разделены AI, supervisor, policy, provider/evidence и owner.
2. **Fail-closed язык состояния.** UNKNOWN не должен превращаться в PASS; текст не считается machine verified; N/A отделён от VERIFIED.
3. **Provider readback.** Exact SHA, ruleset и immutable anchors проверяются у провайдера, а не принимаются на веру от agent.
4. **Защита trusted workflow.** PR-код не исполняется в write-privileged controller; Actions pinned на exact SHA; persist-credentials отключён.
5. **Branch rules.** main защищён PR, strict checks, запретом force push/delete и отсутствием bypass.
6. **Транзакции и rollback.** Consumer upgrade и managed-surface механизмы явно моделируют rollback и postconditions.
7. **Строгие JSON Schema.** Они уменьшают двусмысленность машинных контрактов.
8. **Отсутствие очевидных опасных примитивов.** Не обнаружены shell=True, os.system, небезопасные eval/pickle/YAML sinks или plaintext secrets в отслеживаемых файлах.
9. **Нет циклов Python imports.** При большой модульности это хороший признак.
10. **Честные LIVE_NOT_VERIFIED статусы.** Capability trace в большинстве мест не выдаёт IMPLEMENTED за живое доказательство.
11. **Privacy-механизм Runtime Ledger.** Публичная проекция и immutable anchor сами по себе спроектированы существенно лучше, чем обычная публикация полного рабочего контекста.
12. **FREE_ONLY как дисциплина.** Она предотвращает незаметное превращение базовой работоспособности в платный обязательный сервис.

Главная ценность проекта уже видна: это не «ещё один prompt wrapper», а попытка построить доказуемый control plane. Задача — сохранить это ядро и удалить всё, что не помогает ему доказуемо обслуживать продукт.

## 9. Аудит инфраструктурных слоёв

### 9.1. Contract и policy layer

**Сильные стороны**

- роли и запреты сформулированы явно;
- schemas используют строгие enum и required fields;
- разделены local claim, provider fact и owner acceptance;
- unknown, N/A, not verified и verified имеют разный смысл;
- предусмотрены risk level, authorization и rollback.

**Разрыв**

Policy сейчас преимущественно path-based. Файл может не называться trust или policy, но фактически менять authority. Именно поэтому центральный lease/runtime код оказался вне обязательной ручной проверки.

**Вывод**

Не нужен новый policy DSL. Сначала нужен семантический TCB inventory и простая функция risk score, учитывающая полномочия, данные, обратимость и радиус поражения.

### 9.2. Durable orchestration layer

**Сильные стороны**

- state machine выражена явно;
- присутствуют lease, heartbeat, recovery, replay/readback идеи;
- один writer снижает гонки;
- failure и human-required моделируются как состояния, а не исключения «где-то в логах».

**Разрыв**

- terminal persistence неполна;
- orch_resume существует отдельно от фактического production path;
- provider state, local projection, Issue и Ledger могут расходиться;
- recovery complexity выросла раньше, чем доказана базовая одна последовательная петля.

**Вывод**

Слой концептуально нужен, но его нельзя расширять до multi-writer, пока один writer не проходит десятки повторяемых E2E без stale resurrection.

### 9.3. Provider/GitHub control plane

**Сильные стороны**

- strict rulesets;
- exact-SHA checks;
- protected main;
- no bypass;
- pinned Actions;
- trusted workflow не исполняет PR-код;
- provider readback имеет приоритет над текстом agent.

**Разрыв**

- issue_comment — слишком широкий вход в privileged controller;
- job permissions шире, чем отдельным шагам действительно нужно;
- required approvals=0, CODEOWNERS=false, thread resolution=false;
- сотни технических веток и stale issues делают provider неудобным для человека;
- публичный Issue используется как sink для потенциально приватного intent.

**Вывод**

GitHub подходит как provider control plane для текущего масштаба. Не надо строить собственный forge. Нужна минимизация входов/permissions и строгая гигиена provider state.

### 9.4. Executor adapter layer

**Сильные стороны**

- архитектура не обязана доверять конкретному AI;
- agent result рассматривается как предложение, а не authority;
- отсутствие adapter приводит к WAITING_AGENT/HUMAN_REQUIRED, а не к фальшивому успеху.

**Разрыв**

- нет двух независимо квалифицированных live adapter;
- нет воспроизводимого sandbox/egress envelope;
- Agent Runtime Safe пока декларативен;
- CODEX_EXECUTOR рискует занять центр roadmap до доказательства базовой петли.

**Вывод**

ADWF не должен писать своего coding agent. Он должен стандартизировать bounded work package, result envelope и qualification suite для внешних исполнителей.

### 9.5. Verification и evidence layer

**Сильные стороны**

- machine-readable evidence;
- exact revision/provider fact;
- capability trace;
- immutable anchor;
- отдельные adversarial/fault сценарии;
- rollback evidence.

**Разрыв**

- self-test collection недостоверен;
- decision trace не ссылается на evidence;
- capability validator не доказывает call reachability;
- нет coverage baseline и mutation testing критического ядра;
- typecheck_core существует, но hosted CI его не запускает;
- часть отчётов устарела и продолжает описывать 200 тестов.

**Вывод**

Это центральный слой ADWF и одновременно зона самого опасного дефекта. До восстановления evidence integrity нельзя наращивать новые типы доказательств.

### 9.6. Product truth layer

**Сильные стороны**

- framework различает control-plane correctness и product value;
- допускается честный N/A;
- описаны acceptance и observation.

**Разрыв**

- для самого ADWF project gate commands пусты/optional;
- внешний продуктовый цикл не завершён;
- golden paths и debt ledger пусты;
- «0 debt» для такой молодой и сложной системы выглядит не как качество, а как отсутствие инвентаризации;
- product truth не определяет приоритет большинства roadmap items.

**Вывод**

Product Truth должен стать не ещё одним schema layer, а живым набором из 3–5 пользовательских исходов на реальном consumer.

### 9.7. Owner experience layer

**Сильные стороны**

- есть локальный portal;
- loopback, CSRF и CSP обработаны разумно;
- owner decisions отделены от AI decisions;
- предусмотрен human-by-exception.

**Разрыв**

- portal показывает stale local state;
- не объясняет конкретную причину HUMAN_REQUIRED;
- roadmap-view объективно неверен;
- нет одного экрана «что происходит, зачем, какой результат, что нужно от меня»;
- UI пока не является read-only проекцией одной истины.

**Вывод**

Минимальный UI нужен, но только после ремонта truth source. Большой UI сейчас закрепит недостоверность красивой оболочкой.

### 9.8. Distribution и adoption layer

**Сильные стороны**

- есть install/upgrade/rollback механизмы;
- consumer transaction live-verified в disposable среде;
- checksum и managed surface тщательно контролируются.

**Разрыв**

- нет GitHub Release и semantic version tag;
- VERSION=1.6.0 существует без release artifact;
- README/SPEC и generated reports частично устарели;
- LICENSE отсутствует, есть только решение о необходимости его выбрать;
- connected write-back не доказан;
- чистый bootstrap нового consumer не показан как owner-friendly путь.

**Вывод**

ADWF пока нельзя честно называть распространяемым open-source framework. Это публичный source-available исследовательский репозиторий без оформленной лицензии и стабильной поставки.

## 10. Техническое состояние кода

### 10.1. Размер и структура

Примерное распределение строк:

| Группа | Файлов | Строк |
|---|---:|---:|
| Core .adwf/adwf.py, lib и scripts | 144 | 26 727 |
| Tests | 105 | 12 942 |
| Schemas/config/policy | 76 | 14 078 |
| Docs | 114 | 4 555 |
| GitHub workflows | 5 | 342 |
| Generated projections | 3 | 1 565 |
| Остальное | 131 | 6 273 |
| Всего | 578 | около 66 482 |

Для ранней стадии отношение tests к core выглядит неплохо. Но 14 тысяч строк schemas/config/policy и высокий churn generated surfaces показывают, что формализация растёт почти так же быстро, как исполняемое поведение.

Крупнейшие модули:

- managed_surface_transaction — около 1 655 строк;
- reference_conformance — около 1 228;
- consumer_upgrade_transaction — около 1 146;
- adwf.py — около 837;
- capability_live_evidence — около 668;
- healing — около 622;
- consumer_upgrade — около 617;
- skill_layer — около 572;
- trust — около 562;
- durable_orchestrator — около 499.

### 10.2. Сложность

Статическая приблизительная оценка 1 246 production-функций:

- медианная branch complexity — около 4;
- 95-й процентиль — около 28;
- 109 функций имеют показатель 20 и выше;
- 30 — 40 и выше.

Самые сложные функции находятся ровно там, где цена ошибки высока:

- reconciliation.reconcile_snapshot — около 92;
- cost_guard.evaluate_provider — около 79;
- capability_live_evidence — около 71;
- consumer_upgrade recovery — около 69;
- managed surface adoption — около 66;
- lease registry — около 64;
- project packs — около 64;
- trust.classify_diff — около 59;
- durable advance — около 58;
- ORCH resume — около 56.

Это не формальное измерение cyclomatic complexity инструментом уровня Sonar; это AST-based индикатор для поиска зон риска. Он показывает важное: самые ветвистые функции управляют authority, recovery, trust и transaction. Именно там нужны state-table tests, fault injection и упрощение.

### 10.3. Читаемость и reviewability

В 65 production-файлах есть строки длиннее 160 символов; всего около 360 таких строк, 75 длиннее 240. Максимумы в generated/embedded content достигают сотен и тысяч символов.

Длинная строка сама по себе не дефект. Но сжатый стиль в authority-коде:

- усложняет независимый review;
- скрывает ветвление;
- повышает риск неверного diff classification;
- делает AI-generated patch визуально правдоподобным, но трудным для человека.

Рекомендация: в TCB установить более строгую читаемость, разложить branch-heavy функции в явные state transitions и назвать каждый fail-closed decision.

### 10.4. Import topology

Положительный результат: среди 241 Python-модуля не найдено import cycle.

Негативный результат: отсутствие цикла не означает достижимость. Из 87 lib-модулей пять не имеют production inbound imports, а некоторые capability запускаются через standalone scripts и registries, что делает граф непрозрачным.

Нужен generated call/reachability inventory:

- entry point;
- module/function;
- вызывающая capability;
- risk zone;
- tests;
- last live evidence;
- owner-visible outcome.

### 10.5. Static quality gates

Файл typecheck_core.py и pinned requirements существуют. Документация утверждает, что mypy gate встроен в hosted GitHub CI. Фактически ни один workflow его не вызывает и dependencies не устанавливает.

Также не найдено:

- канонического coverage threshold;
- lint gate;
- SAST/secret/dependency scan в обязательном workflow;
- mutation test критических модулей.

Это не призыв немедленно добавить пять тяжёлых инструментов. Сначала:

1. один быстрый formatter/lint;
2. typecheck только TCB и публичных контрактов;
3. secret/dependency scan;
4. coverage по критическим переходам;
5. mutation testing только trust/runtime/lease/evidence.

Исследование Google показывает, что mutation testing может находить слабость тестов и коррелирует с реальными дефектами, но его нужно применять выборочно: [Long-Term Effects of Mutation Testing](https://research.google/pubs/long-term-effects-of-mutation-testing/).

### 10.6. Python pin

Workflow требует ровно Python 3.12.10. На машине аудита установлен 3.12.13, поэтому run_project_gates корректно завершился failure ещё до project gates. На дату аудита актуальный security release линии 3.12 — 3.12.14: [Python 3.12 latest](https://www.python.org/downloads/latest/python3.12/).

Exact pin полезен для воспроизводимости, но фиксировать устаревший security patch бессрочно опасно. Нужна политика:

- канонический CI pin обновляется автоматически контролируемым PR;
- локально разрешён совместимый patch range для non-release checks;
- release evidence фиксирует exact interpreter digest/version.

### 10.7. Preview supply chain

Preview динамически выполняет npm install --no-save playwright@1.62.0. Нет package-lock и npm ci. Версия Playwright зафиксирована, но полное дерево транзитивных зависимостей — нет.

Флаг pixel_environment_pinned фактически подтверждает наличие runtime values, а не воспроизводимость artifact provenance.

Рекомендация:

- committed lockfile;
- npm ci;
- pinned browser/container digest;
- отдельный provenance record.

Официальные основы: [package-lock.json](https://docs.npmjs.com/cli/v8/configuring-npm/package-lock-json/), [npm ci](https://docs.npmjs.com/cli/v9/commands/npm-ci/), [Playwright Docker pinning](https://playwright.dev/docs/docker).

### 10.8. Release и provenance

Вместо расширения собственного attestation vocabulary стоит совместиться со стандартами:

- [SLSA build requirements](https://slsa.dev/spec/v1.2/build-requirements);
- [in-toto attestations](https://in-toto.io/);
- [in-toto test result predicate](https://in-toto.io/attestation/test-result/).

Минимальная первая release-линия:

1. LICENSE;
2. SemVer tag;
3. changelog;
4. source archive/checksums;
5. SBOM;
6. SLSA-compatible provenance;
7. supported Python/version matrix;
8. upgrade and rollback evidence.

## 11. Сквозная E2E-трассировка

### 11.1. Локальный сценарий владельца

В disposable clone выполнен запуск с задачей владельца понятным языком.

Внешняя команда вернула AUTOPILOT_STARTED и exit code 0. Внутренний wakeup немедленно пришёл к:

- status=HUMAN_REQUIRED;
- reason=GITHUB_CONNECTION_REQUIRED;
- phase=RECONCILE;
- work item=PENDING.

После runtime-tick перехода не произошло. Команда status показала:

- package/config — VERIFIED;
- control — BROKEN или NOT_VERIFIED;
- product — NOT_VERIFIED;
- progress — 0%.

Это корректное fail-closed поведение при отсутствии GitHub auth. Но owner experience некорректен:

- outer status AUTOPILOT_STARTED маскирует немедленный blocker;
- exit code 0 выглядит как успешное начало;
- portal сообщает HUMAN_REQUIRED без причины;
- не даётся конкретная инструкция восстановления.

Правильный owner output:

    Не могу продолжить: GitHub connection не настроено.
    Я ничего не изменил.
    Чтобы продолжить: выберите репозиторий и подтвердите read/write scope.

### 11.2. Трассировка полного цикла

| Этап | Наличие кода | Production-связь | Live evidence | Итог |
|---|---|---|---|---|
| Intent capture | Да | Да | Локально | PARTIAL; privacy P0 |
| Sanitised brief | Да | Да | Синтетически | Sanitizer работает, но Issue sink его обходит |
| Risk/authorization | Да | Да | Частично | Semantic TCB gap |
| Work package | Да | Да | Частично | Нет полного live executor path |
| Creative/coding agent | Adapter contract | Частично | Нет внешней qualification | WAITING_AGENT без adapter |
| Branch/commit/PR | Да | Да | Да для ADWF | Сильный provider layer |
| CI/trust checks | Да | Да | Да | Evidence false-green из-за test collection |
| Preview | Да | Да | Platform smoke | Не доказан внешний продуктовый acceptance |
| Owner acceptance | Да | Частично | Нет полного consumer loop | LIVE_NOT_VERIFIED |
| Merge/delivery | Да | Частично | Framework PR — да | Product delivery не доказана |
| Observation | Контракты есть | Неполно | Нет | Product Truth отсутствует |
| Terminal ledger | Да | Да | Инцидент #259 | Generic defect остаётся |
| Resume/session continuity | Да | Частично/нет | Нет | Dead wiring + omitted tests |
| Consumer upgrade | Да | Да | Да disposable | Planning/transaction LIVE_VERIFIED |
| Connected write-back | Частично | Частично | Нет | write_back_performed=false |
| Roadmap/current state | Да | Да | Опровергнуто | 55 PLANNED, 0% |

### 11.3. Что считается настоящим E2E-доказательством

Не PR в самом ADWF и не зелёный self-test. Настоящее доказательство должно содержать:

1. исходное owner intent с privacy controls;
2. bounded acceptance criteria;
3. внешний consumer revision до изменения;
4. work package и executor identity;
5. точный diff/commit/PR;
6. selected checks с объяснением риска;
7. независимый result;
8. owner acceptance либо формально допустимый auto-accept;
9. delivery/rollback evidence;
10. наблюдаемый пользовательский outcome;
11. terminal ledger record;
12. актуальный portal/roadmap state;
13. process-tax record.

Пока хотя бы один пункт скрыт за «код существует», capability должна оставаться LIVE_NOT_VERIFIED.

## 12. Process tax: главная экономическая угроза

### 12.1. Что такое process tax для ADWF

Process tax — это все действия, которые система выполняет не ради пользовательской возможности, а ради управления собственным процессом:

- создание дополнительных branch/Issue/PR;
- повторные Actions runs;
- генерация projections и checksum;
- lease/anchor/mirror/materializer операции;
- перенос статусов между несколькими источниками;
- запросы человеку;
- recovery после ошибок самого control plane;
- полный набор проверок для микроскопического изменения;
- поддержка схем, которые ещё не дают owner-visible value.

Governance не является бесполезным налогом: часть нужна для безопасности. Проблема начинается, когда ADWF не измеряет предельную полезность проверки и не умеет удалить проверку, которая ничего не находит.

### 12.2. Фактические сигналы

- 92,7% roadmap items помечены как не product-impact;
- 147 PR при 73 merge;
- около половины PR закрыто без merge;
- 1 955 Actions runs, больше failure, чем success;
- сотни branches, большинство tip не является ancestor main;
- hot generated surfaces менялись в 80+ коммитах;
- один P0 recovery породил source-authority, helper, lease, mirror и materializer контуры;
- canonical owner view всё равно остался 0%.

Это показывает, что control-plane activity плохо конвертируется в актуальную owner truth.

### 12.3. Что измерять на каждую реально завершённую возможность

| Метрика | Зачем |
|---|---|
| Intent-to-value lead time | Сколько прошло от задачи владельца до наблюдаемого результата |
| AI active time | Сколько времени AI реально работал, а не ждал |
| Wait time | Очередь, CI, provider, human wait |
| Tool calls / agent turns | Сложность машинного пути |
| PR и branch count | Накладная coordination complexity |
| Actions runs и compute minutes | Инфраструктурная стоимость |
| Failure/rerun count | Потери из-за нестабильности процесса |
| Generated-only commits | Сколько изменений не затронуло поведение |
| Human decisions/questions | Насколько система действительно human-by-exception |
| Recovery count/time | Цена ненадёжности control plane |
| Verification time | Стоимость проверок |
| Verification yield | Сколько реальных дефектов нашла каждая проверка |
| First-pass success | Доля циклов без repair |
| Change failure rate | Доля доставок с rollback/repair |
| Terminal truth latency | Время от фактического завершения до правильного ledger/portal |
| Product-impact ratio | Доля работы с наблюдаемым внешним результатом |

Нельзя оптимизировать одну метрику. Например, уменьшение CI time ценой пропуска дефектов — ложная победа. Поэтому нужны как минимум:

- throughput;
- stability;
- human burden;
- evidence quality;
- owner-visible outcome.

### 12.4. Process budget

Для каждой bounded capability до начала работы задаётся бюджет:

| Элемент | Нормальный путь |
|---|---|
| Canonical PR | 1 |
| Helper/materializer branch | 0 |
| Human decision | 0 для R0–R2 routine; 1 для R3/R4 |
| Generated projection updates | 1 при terminal finalization |
| Recovery contour | 0 |
| Full-suite run | Только по риску или shared-core impact |
| Повторный полный AI-review | Только после существенного изменения |
| Owner-facing status transitions | Не более 4–6 понятных состояний |

Превышение бюджета не блокирует критический ремонт, но создаёт process-debt item с root cause.

### 12.5. Практическая цель

До добавления новых архитектурных слоёв:

- снизить median PR/branch/Actions operations на завершённую capability минимум на 50%;
- для routine R0–R2 довести median human actions до 0;
- исключить helper/materializer branches из нормального пути;
- сократить generated-only commits минимум на 80%;
- сделать terminal truth latency менее 5 минут;
- повысить product-impact ratio ближнего roadmap минимум до 50%;
- добиться, чтобы процессные метрики считались автоматически из provider/ledger, а не вручную.

### 12.6. Механизм удаления лишних проверок

Раз в 20 завершённых изменений:

1. построить стоимость каждой проверки;
2. посчитать найденные ею уникальные дефекты;
3. отметить проверки, которые только дублировали другие;
4. перевести низкоценные проверки из mandatory в sampled;
5. усилить проверку, которая обнаружила escaped defect;
6. удалить или объединить schema/projection без потребителя.

ADWF должен уметь не только добавлять governance, но и доказывать, что governance можно безопасно убрать.

## 13. Сколько проверок действительно нужно: risk-adaptive verification

Сейчас система рискует одинаково серьёзно относиться к изменению опечатки и к изменению authority. Это и медленно, и небезопасно: шум от мелочей снижает внимание к TCB.

### 13.1. Предлагаемые уровни

| Риск | Пример | Обязательные проверки | Что обычно не нужно |
|---|---|---|---|
| R0 | Текст, комментарий, generated projection без поведения | format/schema/link, targeted snapshot | full suite, preview, независимый security review |
| R1 | Изолированная чистая функция | affected unit, lint/typecheck, contract test | полный E2E, owner approval |
| R2 | Shared logic, state schema, consumer files | affected integration, compatibility, rollback/fault test, selected E2E | все preview/platform сценарии без impact |
| R3 | Runtime state, agent input, evidence, CI controller, lease, deploy | full core suite, adversarial tests, independent review, provider readback, canary/sandbox | auto-authorization |
| R4 | Prod/delete/secrets/ruleset/release/trust expansion/irreversible | explicit owner exact-head approval, independent review, backup/rollback, provider readback | unattended merge |

### 13.2. Risk score должен учитывать смысл

Минимальные факторы:

- **blast radius** — один файл, один consumer или весь fleet;
- **authority delta** — меняются ли права AI/controller;
- **data sensitivity** — может ли путь увидеть секрет/PII/raw intent;
- **reversibility** — есть ли проверенный rollback;
- **uncertainty** — знакомая зона или новый компонент;
- **call-graph reach** — сколько production entry points достигает изменённый код;
- **state migration** — можно ли испортить durable state;
- **external side effect** — Issue/PR/deploy/delete/message;
- **evidence impact** — может ли изменение объявить себя успешным.

Путь файла — только один сигнал.

### 13.3. Правило неизвестности

Если impact graph неполон, риск повышается на один уровень. UNKNOWN не должен автоматически запускать все проверки навсегда: после анализа graph нужно либо классифицировать, либо создать debt на observability.

### 13.4. Независимость проверки

«Independent review» не означает обязательный вызов другой дорогой модели для каждого PR. Независимость может обеспечиваться:

- другой реализацией oracle;
- provider readback;
- deterministic schema/check;
- mutation/fault injection;
- вторым agent для R3/R4;
- владельцем для irreversible;
- живым consumer outcome.

Для низкого риска лучше дешёвый детерминированный oracle, чем формальная AI-дискуссия.

## 14. Нужен ли ADWF UI

### Короткий ответ

**Да, минимальный UI нужен. Большой самостоятельный UI сейчас не нужен.**

Без UI непрограммист не сможет безопасно владеть процессом. Но UI не должен быть:

- IDE;
- terminal;
- code editor;
- chat clone;
- вторым orchestrator;
- местом, где вручную редактируют каноническое состояние.

Его роль — read-only owner projection плюс очень небольшое число подтверждаемых решений.

### 14.1. Пять обязательных блоков

1. **Состояние продукта/consumer.** Работает ли реальный пользовательский путь.
2. **Текущая работа.** Что делается и почему это сейчас важнее другого.
3. **Блокер.** Точная причина, что уже сделано и какой один выбор нужен от владельца.
4. **Результат и доказательство.** Что изменилось, где посмотреть, можно ли откатить.
5. **Process tax.** Время, проверки, human actions, PR/branch/CI и отклонение от бюджета.

### 14.2. Обязательные свойства

- live provider/ledger readback;
- observed_at и source revision;
- яркий stale indicator;
- объяснение N/A/NOT_VERIFIED человеческим языком;
- никаких progress percent из несвязанных плановых записей;
- owner action подписывается через доверенный provider identity;
- каждая кнопка показывает side effect до подтверждения;
- routine path умещается в «увидел → понял → при необходимости подтвердил».

### 14.3. Что делать с текущим portal

Не переписывать. Пометить как experimental owner projection и:

1. заменить local project-state на derived live state;
2. показывать reason и recovery instruction;
3. убрать ложный progress;
4. добавить links к exact evidence;
5. добавить process-tax card;
6. провести usability test с непрограммистом на пяти сценариях.

Пока truth source не исправлен, дальнейший дизайн UI следует заморозить.

## 15. Изобретает ли ADWF велосипед

### 15.1. Где да

ADWF строит собственные варианты уже зрелых категорий:

- durable workflow/recovery;
- policy engine;
- developer portal;
- attestation/evidence format;
- coding-agent dispatch;
- spec-driven workflow;
- catalog/consumer management;
- CI governance.

Если продолжать делать каждую категорию полностью самостоятельно, проект превратится в дорогое объединение Temporal + OPA + Backstage + in-toto + coding agent — без зрелости каждого из них.

### 15.2. Где нет

Ни один из этих инструментов не даёт целиком:

- owner-as-root-of-trust для непрограммиста;
- vendor-neutral agent governance;
- intent privacy;
- evidence-linked lifecycle;
- human-by-exception;
- consumer upgrade/rollback;
- process-tax optimization;
- одну простую проекцию «почему, что произошло, чем доказано».

Уникальность ADWF — в **интеграционном контракте и политике доверия**, а не в реализации каждого нижнего слоя.

### 15.3. Сравнение с актуальными инструментами

| Инструмент/стандарт | Что уже решает | Что ADWF не должен повторять | Что остаётся ADWF |
|---|---|---|---|
| [OpenHands SDK](https://docs.openhands.dev/sdk) | Coding-agent runtime, tools, local/cloud, multi-agent | Собственного универсального coding agent | Qualification, bounded work, authority, evidence |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | Issue-to-fix research agent | Ещё один issue-solving loop | Provider-neutral governance и product lifecycle |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | Spec-driven templates для разных AI | Собственный большой язык спецификаций | Проверяемая доставка и owner truth |
| [Temporal](https://docs.temporal.io/evaluate/understanding-temporal) | Durable execution, event history, replay | Зрелый workflow engine при масштабе | Domain policy и простой zero-cost profile |
| [OPA](https://www.openpolicyagent.org/docs/) | Policy-as-code | Новый общий policy DSL | ADWF risk model и owner decisions |
| [Backstage Catalog](https://backstage.io/docs/features/software-catalog/) | Software catalog/portal/templates | Большую developer platform для одного владельца | Минимальный owner portal |
| [SLSA](https://slsa.dev/spec/v1.2/build-requirements) | Build provenance model | Собственный несовместимый provenance format | Связь provenance с lifecycle |
| [in-toto](https://in-toto.io/) | Attestation envelope/predicates | Изобретение evidence envelope | ADWF-specific claims и owner view |
| GitHub Actions/rulesets | CI, provider identity, protection | Собственный forge и scheduler | Fail-closed orchestration поверх GitHub |

### 15.4. Buy/adopt/build правило

Новый внутренний subsystem разрешён только если:

1. нет зрелого бесплатного стандарта или adapter;
2. его поведение относится к уникальному trust contract ADWF;
3. есть live consumer pain;
4. стоимость поддержки ниже стоимости интеграции;
5. он уменьшает, а не увеличивает owner/process burden.

Иначе — adapter, standard predicate или optional backend.

### 15.5. Temporal и OPA: не сейчас

Полная миграция на Temporal сейчас увеличит инфраструктуру, deployment burden и нарушит простоту FREE_ONLY/local-first. Полная миграция policy в OPA добавит второй язык и второй runtime.

Правильный путь:

- заимствовать event-history, replay и idempotency semantics;
- сделать storage/orchestrator interface;
- добавить optional Temporal backend только при доказанном fleet/multiwriter масштабе;
- держать risk policy в понятном Python/JSON до тех пор, пока её сложность не станет измеримой проблемой;
- при необходимости экспортировать часть policy в OPA/Rego через adapter.

## 16. Почему ADWF не должен становиться Cursor, Codex или Claude Code

Coding agents соревнуются в том, насколько хорошо они пишут и меняют код. ADWF должен соревноваться в другом:

- кто имел право предложить изменение;
- почему именно это изменение выбрано;
- какой риск у него был;
- какие проверки действительно нужны;
- какое независимое доказательство получено;
- что увидел пользователь продукта;
- можно ли восстановиться;
- сколько процесс стоил владельцу;
- что теперь является правдой.

Практическая граница:

| ADWF владеет | Внешний executor владеет |
|---|---|
| Intent contract | Поиском решения |
| Risk и authorization | Редактированием кода |
| Work boundary | Tool use в sandbox |
| Provider policy | Предложением commit/patch |
| Verification selection | Самопроверкой как одним сигналом |
| Independent evidence | Исправлением по feedback |
| Owner acceptance | Ничем необратимым |
| Ledger/recovery | Никакой authority над ledger |

CODEX_EXECUTOR, OpenHands или другой agent — сменный двигатель. Нельзя делать его архитектурным центром ADWF или давать ему право объявлять собственный результат истинным.

## 17. Аудит текущего roadmap

### 17.1. Структурные проблемы

1. **Нет фактического lifecycle status.** Roadmap JSON хранит план, но owner-view отображает все 55 items как PLANNED.
2. **Слишком длинная почти линейная цепочка.** Около 37 foundation items связаны последовательными dependencies. Один blocker искусственно тормозит всё.
3. **Product value слишком поздно.** Только 4 из 55 items имеют product_impact=true.
4. **Evidence не замыкает work.** Roadmap и decision trace не приводят к живому evidence/result.
5. **Формальная завершённость не синхронизирована.** Merged PR, closed/unclosed Issue, capability status и roadmap расходятся.
6. **Архитектурные опции выглядят как обязательная работа.** Multi-writer, PPCF, skill factory, fleet и executor integration попадают в один поток с исправлением базовой истины.
7. **Приоритет CODEX_EXECUTOR завышен.** Executor не исправит test evidence, privacy, terminal recovery или stale owner state.
8. **Нет process budget.** Roadmap оценивает, что построить, но не сколько внутренней работы допустимо на outcome.

### 17.2. Как должен выглядеть roadmap

Вместо плоских 55 items — четыре outcome lanes:

| Lane | Главный вопрос |
|---|---|
| Truth & Safety | Можно ли верить статусу, данным и полномочиям? |
| Owner Value Loop | Может ли владелец получить один доказанный результат без технической рутины? |
| Consumer Adoption | Можно ли безопасно подключить, обновить и восстановить внешний проект? |
| Research Options | Какие сложные идеи заслужили реализацию живыми данными? |

Каждый item обязан иметь:

- owner-visible outcome;
- risk level;
- current state;
- exact source revision;
- evidence ID;
- process budget;
- rollback;
- reason for priority;
- stale/blocked reason;
- измеримый exit criterion.

Near-term одновременно открыты не более восьми items. Research options не считаются committed roadmap, пока не выполнен trigger.

## 18. Предлагаемый roadmap

### Фаза 0. Truth & Safety Reset — 0–2 недели

На эту фазу следует заморозить новые capability и архитектурное расширение.

#### 0.1. Закрыть публичную утечку owner intent — P0

**Работа**

- все public sinks переводятся на sanitised projection/digest;
- raw task хранится отдельно;
- public sharing требует явного consent;
- sink-level privacy tests.

**Выход**

- synthetic secret/PII suite не обнаруживает raw data ни в Issue, ledger, logs, artifacts, branch name, PR body;
- доказательство опубликовано без самих секретов;
- документация соответствует production path.

#### 0.2. Восстановить достоверность test evidence — P0

**Работа**

- единый test collector;
- исправление семи падающих test functions;
- защита от duplicate execution;
- manifest и machine-readable report.

**Выход**

- каждый intended test собирается ровно один раз;
- искусственно uncollected test ломает CI;
- число unique/executed/skipped/failed совпадает между local и hosted;
- self-test больше не может показать 727/727 при пропуске файла.

#### 0.3. Исправить Runtime Ledger terminality — P0

**Работа**

- persist terminal states;
- re-read source terminality before resume;
- monotonic sequence/idempotency;
- crash matrix.

**Выход**

- ремонт merged в protected main;
- live work item проходит RUNNING→COMPLETE→process restart без resurrection;
- lease, Issue, Ledger, roadmap и portal согласованы;
- повторная попытка требует нового attempt ID.

#### 0.4. Перестроить TCB и risk classification — P0

**Работа**

- inventory authority/evidence/runtime/public-sink modules;
- semantic risk rules;
- independent review для R3;
- owner exact-head для R4.

**Выход**

- mutation PR, аналогичный #257, автоматически получает R3+;
- изменение permissions, claim/release/terminal/evidence/public sink не может быть AUTO R1;
- no-bypass подтверждён provider readback.

#### 0.5. Создать одну каноническую current truth — P1

**Работа**

- provider/ledger становится первичным фактом;
- state/roadmap/portal — только derived projections;
- миграция всех 55 items в реальные states;
- issue hygiene.

**Выход**

- один и тот же item имеет одинаковый status во всех проекциях;
- stale projection маркируется;
- DONE issue не остаётся бессрочно OPEN без объяснения;
- owner status больше не показывает одновременно «0%» и merged capabilities.

#### 0.6. Минимальный security/release baseline — P1

**Работа**

- обновляемая Python patch policy;
- lockfile + npm ci;
- secret/dependency scan;
- узкий SAST для TCB;
- SECURITY.md с supported versions и private reporting;
- CODEOWNERS/required review для TCB;
- проверить/включить GitHub private vulnerability reporting: [официальная инструкция](https://docs.github.com/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository).

**Выход**

- у security defect есть приватный канал;
- release dependencies воспроизводимы;
- TCB change не проходит без требуемого review.

#### 0.7. Оформить distribution identity — P1

**Работа**

- выбрать LICENSE;
- определить SemVer;
- создать первый GitHub Release;
- changelog, SBOM, provenance;
- согласовать README/SPEC/VERSION.

**Выход**

- пользователь может однозначно скачать версию, проверить checksum, понять лицензию, совместимость и rollback.

#### 0.8. Остановить branch/generated churn — P1

**Работа**

- запрет helper/materializer branch в normal path;
- projections генерируются один раз при finalization;
- архивный/cleanup plan для сотен stale branches без немедленного destructive удаления;
- retention policy.

**Выход**

- новая routine capability использует один branch и один PR;
- generated-only commit не создаётся без terminal transition;
- provider UI остаётся читаемым.

#### Gate фазы 0

Фаза 1 не начинается, пока одновременно не выполнено:

- privacy P0 закрыт;
- canonical tests действительно зелёные;
- Runtime Ledger defect исправлен и live-проверен;
- authority change получает корректный риск;
- current truth едина;
- near-term roadmap отражает фактическое состояние.

### Фаза 1. One Boring Full Loop — 2–6 недель

Цель — не впечатляющая демо, а скучная повторяемость.

#### 1.1. Один реальный consumer

Выбрать PrihRash либо другой репрезентативный проект с:

- реальным пользователем;
- тестируемым outcome;
- staging или безопасным delivery;
- возможностью rollback;
- неидеальной, но понятной кодовой базой.

#### 1.2. Серия из 10–20 последовательных product changes

Каждая проходит весь lifecycle без ручного «подталкивания» скрытыми командами:

    intent → work selection → executor → PR → risk checks →
    acceptance → merge → delivery → observation → terminal truth

Изменения должны быть разными:

- UI/copy;
- business logic;
- schema/data;
- dependency;
- defect repair;
- rollback;
- deliberately blocked unsafe request.

#### 1.3. Executor qualification

- один deterministic/reference adapter;
- один реальный coding agent adapter, например Codex Goal или OpenHands;
- одинаковый work/result contract;
- sandbox/egress declaration плюс enforcement;
- adversarial output tests;
- executor не закрывает собственную работу как VERIFIED.

OpenHands может быть adapter, потому что уже предоставляет coding-agent SDK и tools: [OpenHands SDK](https://docs.openhands.dev/sdk). SWE-agent полезен как второй исследовательский reference: [SWE-agent](https://github.com/SWE-agent/SWE-agent).

#### 1.4. Закрыть разрывы связности

- ORCH resume либо включается в production и live-verified, либо удаляется;
- session_consumer_proof подключается и доказывается, либо capability понижается;
- provider_events/trust_boundary/preview получают owner outcome или deprecate;
- legacy orchestrate_event исключается из package либо явно становится compatibility shim.

#### 1.5. Process-tax telemetry

Автоматический отчёт на каждый цикл и aggregate dashboard.

#### Gate фазы 1

Рекомендуемые цели после первых 20 циклов:

- не менее 90% циклов завершаются без ручного repair control plane;
- routine median human actions ≤1, целевое 0;
- normal path: 1 branch, 1 PR, 0 helper;
- terminal truth latency <5 минут;
- ни одного stale resurrection;
- zero raw intent leak;
- у каждого terminal cycle есть наблюдаемый consumer outcome;
- process cost на последние 10 циклов ниже первых 10 минимум на 30%.

Если цели не достигнуты, не открывать fleet/multi-writer: сокращать state machine и число projections.

### Фаза 2. Product Truth и adaptive governance — 6–12 недель

#### 2.1. Product Truth contract

Не универсальная абстракция на все случаи, а 3–5 outcomes каждого consumer:

- пользователь может завершить ключевую задачу;
- latency/error rate не ухудшились;
- UI acceptance доступен;
- deploy observable;
- rollback проверяем.

#### 2.2. Risk-adaptive check router

- impact graph;
- TCB semantic zones;
- selective suites;
- verification yield;
- sampled checks;
- automatic escalation on UNKNOWN.

#### 2.3. Independent evaluation

- fixed scenario set;
- hidden/adversarial cases;
- executor comparison;
- pass@1 полного lifecycle, а не patch generation;
- cost/latency/human-burden;
- regression across versions.

#### 2.4. Evidence standardization

- in-toto compatible envelopes;
- SLSA provenance;
- ADWF claims как extension, не fork стандарта.

#### 2.5. Минимальный owner portal v1

Только после единой truth:

- пять owner cards;
- exact evidence links;
- blocker/recovery;
- process tax;
- authenticated approvals.

#### 2.6. Codebase reduction

- удалить/объединить мёртвые модули;
- упростить top complexity;
- сократить schema/projection count;
- зафиксировать public extension surface;
- deprecate legacy CLI.

#### Gate фазы 2

- минимум два разных consumer;
- один и тот же executor contract работает с двумя agent adapters;
- risk router уменьшает median verification time без роста escaped defects;
- owner portal проходит usability test;
- release upgrade/rollback проходит из предыдущей версии.

### Фаза 3. Масштабирование — 3–6 месяцев, только по фактам

#### Возможные направления

1. Fleet desired/observed state для 3+ consumer.
2. Staged upgrade/canary.
3. Cross-consumer learning без утечки приватного контекста.
4. Optional Temporal backend.
5. Optional OPA adapter.
6. Два writer в непересекающихся conflict domains.
7. External practice intelligence и исследовательские benchmarks.

#### Trigger для multi-writer

Multi-writer допускается только если:

- очередь одного writer измеримо ограничивает lead time;
- normal loop стабилен;
- conflict domains формально определены;
- merge остаётся сериализованным;
- race/fault suite проходит;
- process-tax экономия превышает сложность.

Начинать с двух writer в двух непересекающихся domains, а не с общей параллельной автономии.

#### Trigger для Temporal/OPA

Интеграция оправдана, если годовая стоимость поддержки собственного subsystem или число production incidents выше стоимости optional backend. До измерения — research option, не roadmap commitment.

## 19. Что следует отложить

До прохождения фазы 1:

- multi-writer autonomy;
- сложный PPCF;
- автоматическая skill factory;
- self-generated roadmap;
- broad fleet automation;
- hosted/mobile/voice UI;
- второй orchestrator;
- собственный coding agent;
- глубокая кастомная observability platform;
- новые evidence schemas без реального consumer;
- расширение golden paths до появления хотя бы одного доказанного path.

CODEX_EXECUTOR следует перевести из центра P0 в adapter-задачу фазы 1. Сначала истина и безопасность, затем сменный исполнитель.

## 20. Матрица приоритетных находок

| ID | Уровень | Находка | Фактический статус | Первый шаг |
|---|---|---|---|---|
| F-01 | P0 | Raw owner task попадает в public Issue | Воспроизведено | Закрыть raw sinks |
| F-02 | P0 | Self-test пропускает 11 tests, 7 падают; 17 duplicate | Воспроизведено | Единый collector |
| F-03 | P0 | Terminal run может воскреснуть | Конкретный lease released, общий дефект открыт | Persist terminality |
| F-04 | P0 | TCB change может стать R1/AUTO | Подтверждено PR #257/policy | Semantic TCB |
| F-05 | P1 | issue_comment запускает broad privileged controller | Подтверждено workflow | Read-only front door |
| F-06 | P1 | Capability validator не доказывает wiring | Подтверждено кодом | Reachability/evidence |
| F-07 | P1 | Roadmap/state/portal/issues расходятся | Подтверждено CLI/live | Одна derived truth |
| F-08 | P1 | Полный consumer lifecycle не доказан | Capability trace/evidence | 20 boring loops |
| F-09 | P1 | Runtime sandbox/egress декларативен | Документация | Enforced sandbox |
| F-10 | P1 | Process tax не измеряется и быстро растёт | GitHub telemetry | Process budget |
| F-11 | P1 | Нет LICENSE/release/tag | Live GitHub | Release baseline |
| F-12 | P1 | Security reporting/CODEOWNERS baseline неполон | Live/config | Private reporting + owners |
| F-13 | P2 | typecheck заявлен, но не запускается | Workflow/code | Узкий TCB typecheck |
| F-14 | P2 | Нет coverage/mutation baseline | Tool/config audit | Critical-path metrics |
| F-15 | P2 | Preview dependency tree не locked | Код preview | lockfile/npm ci |
| F-16 | P2 | Exact Python pin устарел | Workflow vs release | Patch update policy |
| F-17 | P2 | 5 lib-модулей не имеют production inbound | Import graph | Wire or remove |
| F-18 | P2 | Branch/Issue/generated churn | Live GitHub/history | Retention/finalization |
| F-19 | P2 | Сложность сконцентрирована в TCB | AST analysis | State-table refactor |
| F-20 | P2 | SECURITY.md не даёт полного reporting contract | Документ | Supported versions/process |

## 21. Модель управления roadmap

### 21.1. Definition of Done

Item нельзя считать DONE только потому, что:

- код merged;
- tests green;
- Issue closed;
- capability path существует.

DONE требует:

1. exact main revision;
2. intended call path connected;
3. selected verification passed;
4. independent evidence;
5. owner/product outcome либо честный N/A;
6. terminal ledger;
7. current projections updated;
8. process-tax record;
9. rollback/recovery instruction.

Для research-only item outcome может быть «гипотеза опровергнута», но это должно быть явно.

### 21.2. WIP limits

- P0: не более 2 одновременно, один owner;
- near-term capability: не более 3;
- research options: не более 1 active;
- один writer на conflict domain;
- новый item не открывается, если есть stale terminal state.

### 21.3. Delete-to-add

После фазы 0 новая архитектурная capability добавляется только вместе с одним из:

- удаление/объединение старого слоя;
- доказанный consumer pain;
- снижение process tax;
- standard adapter вместо custom subsystem.

Это защитит ADWF от бесконечного роста поверхности.

## 22. Критерии продолжения, сокращения или остановки

### Продолжать и инвестировать, если

- privacy/evidence P0 закрыты;
- один consumer проходит 20 полных циклов;
- process tax снижается;
- владелец понимает состояние без CLI;
- два executor adapter проходят один contract;
- ADWF обнаруживает или предотвращает дефекты, которые обычный coding-agent loop пропускает.

### Сократить до компактного governance toolkit, если

- полный runtime остаётся нестабильным;
- 20 циклов требуют постоянного helper/recovery;
- owner portal нельзя построить из одной truth;
- большая часть ценности фактически обеспечивается GitHub Actions + несколько scripts.

В таком случае сохранить:

- risk/authorization;
- exact provider readback;
- evidence/attestation;
- owner acceptance;
- consumer upgrade/rollback;
- process-tax metrics.

И отказаться от собственного durable orchestrator/portal/fleet до будущей необходимости.

### Остановить как отдельный продукт, если

- нет ни одного реального consumer и владельца, регулярно получающего value;
- governance не уменьшает escaped risk;
- стоимость процесса после упрощения выше ручной разработки;
- уникальный trust contract не удаётся выразить проще существующими стандартами.

Это не провал. Хороший исследовательский результат может заключаться в выделении небольшого набора полезных governance-компонентов.

## 23. Программа независимой оценки и benchmark

Обычные coding benchmarks измеряют, смог ли agent исправить repository test. Для ADWF этого недостаточно: его ценность заявлена на уровне жизненного цикла, безопасности и снижения рутины.

### 23.1. Единица оценки

Один benchmark episode:

1. owner даёт намерение;
2. система выбирает и ограничивает work;
3. executor предлагает изменение;
4. provider применяет governance;
5. verification выбирается по риску;
6. продукт доставляется или безопасно блокируется;
7. outcome наблюдается;
8. state становится terminal и остаётся таким после restart;
9. владелец получает понятный результат.

Patch, прошедший tests, но раскрывший raw intent, получивший ложный VERIFIED или воскресший после restart, считается failure.

### 23.2. Сравниваемые baseline

Минимум три режима на одинаковых задачах:

| Режим | Назначение |
|---|---|
| B0: человек + GitHub CI | Понять стоимость обычного процесса без AI |
| B1: coding agent + GitHub CI | Понять ценность agent без ADWF |
| B2: тот же coding agent + ADWF | Измерить добавочную ценность governance |

Опционально:

- Spec Kit + agent;
- OpenHands/SWE-agent как другой executor;
- ADWF без adaptive checks;
- ADWF без independent readback;
- ADWF с упрощённым runtime.

Последние варианты — ablation: они показывают, какой слой действительно приносит пользу.

### 23.3. Семейства задач

1. Простая безопасная правка.
2. Логический дефект.
3. Изменение schema/state.
4. Dependency update.
5. UI с visual acceptance.
6. Deployment/config.
7. Задача с PII/secret-like текстом.
8. Malicious prompt в Issue/PR/comment.
9. Agent, который ложно объявляет PASS.
10. CI flaky/failure.
11. Provider API timeout/rate limit.
12. Crash между merge и terminal persistence.
13. Conflict с параллельным изменением.
14. Rollback после observation failure.
15. Неопределённая или небезопасная задача, которую система должна остановить.

### 23.4. Главные метрики

#### Product outcome

- episode success;
- pass@1 полного lifecycle;
- escaped product defect;
- regression rate;
- rollback success.

#### Evidence integrity

- false VERIFIED rate;
- false BLOCKED rate;
- missing evidence rate;
- stale-state rate;
- terminal resurrection rate;
- точность risk classification.

#### Safety

- raw data exposure;
- unauthorized side effect;
- privilege escalation;
- untrusted trigger reaching privileged action;
- sandbox/egress violation.

#### Efficiency

- intent-to-value;
- agent turns/tool calls;
- CI minutes;
- PR/branch count;
- human actions;
- recovery time;
- process-tax/value ratio.

#### Owner experience

- правильно ли владелец понял status;
- время до решения;
- число непонятных терминов;
- доля блокеров, для которых ясна следующая кнопка;
- ошибочные approvals.

### 23.5. Experimental discipline

- tasks и acceptance фиксируются до запуска;
- hidden tests не видит executor;
- одинаковые repository snapshots;
- paired comparison одного и того же task;
- несколько повторов для stochastic agent;
- failures не удаляются как «инфраструктурный шум», а классифицируются;
- version, prompt, tools, model и provider state записываются;
- self-authored ADWF tests не являются единственным oracle;
- результаты публикуются вместе с negative episodes.

### 23.6. Минимальные критерии полезности

После 20–50 episodes ADWF должен по сравнению с B1:

- существенно снизить false VERIFIED и unauthorized side effects;
- не ухудшить median intent-to-value более чем на заранее заданный предел;
- снизить human actions на routine tasks;
- успешно восстановить fault episodes;
- давать владельцу более точное понимание результата;
- показать, какие governance layers дают эффект в ablation.

Если безопасность улучшается на 2%, а lead time растёт в пять раз, система не прошла product test. Если скорость выше, но raw task утекает, она также не прошла.

## 24. Reliability и fault-injection программа

Существующие fault tests — хорошее начало. Следующий набор должен проверять весь контур, а не только локальную функцию.

| Fault point | Ожидаемое свойство |
|---|---|
| После создания Issue, до ledger append | Повтор не создаёт вторую работу |
| После CLAIM, до lease anchor | Нет двух writers |
| После branch create, до commit | Recovery находит или удаляет orphan безопасно |
| После commit, до PR | Commit не теряется и не дублируется |
| После PR, до checks | State остаётся ожиданием, не VERIFIED |
| После checks, до independent review | Merge не разрешён для R3 |
| После merge, до Issue terminal | Provider readback восстанавливает terminality |
| После terminal ledger, до lease release | Restart не создаёт RUNNING |
| После lease release, до cleanup | Cleanup идемпотентен |
| Provider API timeout | UNKNOWN/HUMAN_REQUIRED, не PASS |
| Rate limit | Backoff без штормов Actions |
| Duplicate event | Один transition |
| Out-of-order event | Monotonic revision отклоняет старое |
| Corrupt local projection | Восстановление из provider/ledger |
| Tampered evidence | Digest/readback failure |
| Agent hallucinated SHA | Provider mismatch blocks |
| Preview dependency unavailable | Честный NOT_VERIFIED/N/A |
| Consumer rollback fails | Эскалация владельцу, сохранение forensic state |

Для каждого fault нужны:

- injection point;
- expected invariant;
- expected owner message;
- persisted evidence;
- maximum recovery time;
- cleanup proof.

## 25. Threat model в человеческом виде

### 25.1. Что защищаем

- намерение и приватные данные владельца;
- право изменять code/provider settings;
- целостность main;
- честность test/evidence;
- durable state и lease;
- consumer secrets/deployment;
- возможность rollback;
- внимание и время владельца.

### 25.2. От кого и от чего

| Источник риска | Типичный сценарий |
|---|---|
| Публичный пользователь GitHub | Комментарий запускает дорогой privileged workflow |
| Недоверенный PR | Prompt/код пытается повлиять на trusted controller |
| Coding agent | Ложно объявляет успех, расширяет scope, раскрывает context |
| Ошибка самого ADWF | Terminal state теряется, risk занижается |
| Компрометированная dependency | Preview/install получает подменённый пакет |
| Ошибка владельца | Подтверждает неясное необратимое действие |
| Provider outage | Система принимает отсутствие ответа за успех |
| Stale projection | Владелец действует по устаревшему состоянию |

### 25.3. Границы доверия

1. Raw owner input — приватный и недоверенный как данные.
2. Executor output — недоверенное предложение.
3. PR code — недоверенный до проверки.
4. Trusted controller — TCB, минимальные permissions.
5. GitHub provider fact — сильный источник, но API failure остаётся UNKNOWN.
6. Evidence — доверяется только после digest/provider verification.
7. Portal — проекция, не authority.
8. Owner — Root of Trust для irreversible, но UI обязан предотвращать неинформированное согласие.

### 25.4. Security backlog

Кроме четырёх P0:

- split permissions/jobs;
- event allowlist и actor/app binding;
- signed/one-time inbox events;
- enforced sandbox и egress policy;
- dependency/secret scanning;
- CODEOWNERS для TCB;
- supported version policy;
- private vulnerability reporting;
- audit log для owner approvals;
- test raw data absence во всех artifacts/logs;
- rate limits и quotas на public triggers.

Руководства: [GitHub secure use of Actions](https://docs.github.com/en/actions/reference/security/secure-use), [NIST SSDF practices for generative AI](https://csrc.nist.gov/pubs/sp/800/218/a/final).

## 26. Ответы на ключевые вопросы владельца

### Целесообразно ли продолжать ADWF?

Да, если сфокусировать его на vendor-neutral governance/evidence/recovery и доказать ценность на consumer. Нет, если цель — построить ещё одного coding agent, IDE или универсальную developer platform.

### Изобретает ли он велосипед?

Да на уровне отдельных компонентов; нет на уровне объединённого owner trust contract. Следует оставить уникальный glue, а нижние механизмы стандартизировать или подключать adapter.

### Связаны ли модули и функции?

Частично. Import graph без циклов, но минимум пять lib-модулей не имеют production inbound. Capability validation подтверждает paths, а не достижимость. Критические continuity-функции существуют, но не доказаны в production.

### Сколько проверок нужно?

Ровно столько, сколько обосновано risk/impact. Опечатка не должна платить за full E2E, а изменение authority не должно проходить R1/AUTO. Verification yield должен измеряться.

### Не станет ли ADWF слишком медленным?

Уже становится. 92,7% roadmap — internal, а provider churn огромен. Снижение process tax должно стать самостоятельной product capability и gate для новой архитектуры.

### Нужен ли UI?

Да как минимальная, свежая owner projection. Нет как IDE/chat/terminal или новый источник состояния.

### Соответствует ли текущий roadmap цели?

Нет. Он слишком линейный, внутренний, несинхронизированный и преждевременно расширяет систему. Его следует заменить фазами Truth & Safety → One Boring Loop → Adaptive Governance → Conditional Scale.

### Какой рекомендуемый фокус?

Одна фраза:

> Сделать ADWF самым надёжным и самым дешёвым по вниманию владельца способом превратить намерение в доказанный результат через любого сменного coding agent.

## 27. Итоговый вердикт

ADWF сегодня — не готовая автономная development system, а быстро выросший, интеллектуально сильный исследовательский прототип control plane.

Его главный актив:

- продуманная модель доверия;
- fail-closed контракты;
- provider readback;
- evidence и rollback;
- идея human-by-exception.

Его главный риск:

- формальная архитектура и процессная активность создают видимость доказанности раньше, чем замкнут реальный продуктовый цикл.

Самая опасная стратегия — продолжить roadmap как есть, добавить multi-writer, executor, fleet, skill factory и большой UI, а текущие разрывы считать «техническим долгом». Это сделает систему сложнее для аудита, медленнее для владельца и опаснее при ошибочном VERIFIED.

Самая рациональная стратегия:

1. две недели truth/safety freeze;
2. один реальный consumer;
3. 20 скучных полных циклов;
4. автоматический process-tax budget;
5. risk-adaptive verification;
6. минимальный owner UI;
7. стандарты и adapters вместо новых внутренних платформ;
8. масштабирование только после количественного доказательства.

Если это выполнено, ADWF может занять полезную нишу над coding agents: не писать код лучше них, а делать их работу управляемой, доказуемой, восстанавливаемой и почти незаметной для владельца.

---

# Приложения

## A. Зафиксированный live-baseline

### A.1. Repository identity

- main SHA: 29b95ceba823469005a0eef4e6d7d1c3b412814e;
- tree SHA: 164707659e77f385a66793f9a7fff748349ebb19;
- commit time: 2026-08-21T17:09:15Z;
- commit: [Bind trusted CLAIM to provider-durable lease authority (#257)](https://github.com/kmephis-ai/AI-Development-Framework/commit/29b95ceba823469005a0eef4e6d7d1c3b412814e);
- local audit clone после checkout был clean;
- repository visibility: public;
- created_at: 2026-08-14T16:34:43Z;
- на срезе: 0 stars, 0 forks.

### A.2. Provider state

- 0 open pull requests;
- 22 open issues;
- 147 total observed PR: 73 merged, 74 closed unmerged;
- 1 955 observed Actions runs:
  - 820 success;
  - 990 failure;
  - 144 cancelled;
  - 1 action_required;
- среди последних 100 runs:
  - 59 failure;
  - 37 success;
  - 4 cancelled;
- около 362 remote branches;
- около 331 branch tips не являлись ancestors main;
- GitHub Releases: 0;
- semantic version tags: 0.

GitHub-состояние динамично. Эти числа относятся к моменту аудита и не должны копироваться в будущую документацию без повторного readback.

### A.3. Main rules

Подтверждено:

- pull request required;
- non-fast-forward protected;
- branch deletion protected;
- strict required checks;
- no bypass;
- GitHub Actions как разрешённый source;
- required approvals=0;
- code owner review=false;
- required conversation resolution=false.

Техническая защита main сильная, но semantic underclassification остаётся: строгий gate исполняет тот риск, который ему присвоили.

### A.4. Runtime Ledger incident state

На последнем прочитанном immutable lease anchor revision 11:

- четыре lease имели RELEASED;
- active lease=0;
- source branch для #259 отсутствовала;
- recovery event имел COMPLETE.

Это доказательство containment, не доказательство code fix.

## B. Основные проверенные источники внутри репозитория

- [AGENTS.md](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/AGENTS.md) — канонические роли и запреты.
- [README.md](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/README.md) — публичные заявления.
- [SPECIFICATION.md](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/SPECIFICATION.md) — архитектурный контракт.
- [SECURITY.md](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/SECURITY.md) — security boundary.
- [roadmap.json](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/roadmap.json) — план.
- [project-state.json](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/project-state.json) — checked-in state.
- [capability-traceability.json](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/capability-traceability.json) — заявленная capability topology.
- [capability-live-evidence.json](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/capability-live-evidence.json) — live evidence.
- [decision-requirement-traceability.json](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/decision-requirement-traceability.json) — decision/work links.
- [adwf-control.yml](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.github/workflows/adwf-control.yml) — privileged controller.
- [adwf-pr.yml](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.github/workflows/adwf-pr.yml) — PR checks.
- [adwf-main.yml](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.github/workflows/adwf-main.yml) — main checks.
- [project_execution.py](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/lib/project_execution.py) — runtime safety limitations.
- [PROJECT_RUNTIME_SAFETY.md](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/docs/governance/PROJECT_RUNTIME_SAFETY.md) — declared execution boundary.
- [typecheck_core.py](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/scripts/typecheck_core.py) — existing but unwired mypy gate.
- [test_session_delivery.py](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/tests/test_session_delivery.py) и [test_session_consumer_proof.py](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/.adwf/tests/test_session_consumer_proof.py) — uncollected tests.
- [CONTROL_CENTER.md](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/CONTROL_CENTER.md) — owner projection.
- [LICENSE_DECISION_REQUIRED.md](https://github.com/kmephis-ai/AI-Development-Framework/blob/29b95ceba823469005a0eef4e6d7d1c3b412814e/LICENSE_DECISION_REQUIRED.md) — отсутствие принятой лицензии.

## C. Выполненные локальные проверки

### C.1. Канонические команды

В точном checkout main:

    python .adwf/adwf.py self-test
    python .adwf/adwf.py validate-ci
    python .adwf/adwf.py validate-framework
    python .adwf/adwf.py validate-docs
    python .adwf/adwf.py validate-pipeline
    python .adwf/adwf.py doctor
    python .adwf/adwf.py status
    python .adwf/adwf.py roadmap-view
    python .adwf/adwf.py run-project-gates --phase pr
    python .adwf/adwf.py run-project-gates --phase main

Результаты:

- self-test — формально OK, 727 runs;
- validate-ci/framework/docs/pipeline — PASS;
- doctor — package/config VERIFIED, control/product NOT_VERIFIED;
- platform smoke — PASS, но проверяет запуск portal и наличие текстовых token;
- project gates — failure из-за Python 3.12.13 вместо exact 3.12.10;
- product gate commands в framework config — empty/optional, поэтому сами проверки N/A.

### C.2. Проверка collection

Проведены:

- inventory всех test files;
- сравнение unittest discovery и module functions;
- нормализация duplicate TestCase IDs;
- прямой безопасный вызов 11 top-level test functions без изменения репозитория.

Результат:

- 17 duplicate executions;
- 11 tests отсутствуют в canonical collection;
- 7 из 11 падают;
- 4 проходят.

### C.3. Static topology

Проведены:

- AST import graph;
- cycle detection;
- inbound reference analysis для .adwf/lib;
- approximate branch-complexity scan;
- long-line inventory;
- поиск опасных execution primitives и secret-like content;
- проверка workflow pinning/permissions/triggers.

Результат:

- import cycles не найдены;
- пять lib modules без production inbound;
- самые сложные функции сосредоточены в authority/recovery;
- shell=True/os.system/eval/pickle unsafe sinks не обнаружены;
- plaintext secrets в tracked source по применённым patterns не обнаружены.

Отсутствие совпадения regex не является гарантией отсутствия секрета. Для релиза нужен отдельный secret scanner и history scan.

### C.4. Disposable E2E

Отдельная локальная копия использована для:

- owner intent start;
- runtime tick;
- status/roadmap/portal path;
- отсутствующий GitHub adapter/auth;
- synthetic privacy sentinel.

Исходный audit clone и GitHub не изменялись.

## D. Интерпретация CI telemetry

Большое число failures не означает автоматически плохое качество main:

- часть runs относится к ожидаемым repair/helper веткам;
- часть отражает fail-closed incidents;
- latest main/PR checks на момент среза были зелёными.

Но operational quality control plane всё равно низкая, потому что:

- failures превосходят successes;
- среди последних 100 runs 59 failure;
- control workflow особенно шумный;
- stale issues/branches сохраняются;
- зелёный self-test не является надёжным oracle.

Правильные метрики:

- first-pass success по canonical outcome;
- unique failure root causes;
- retry amplification;
- time in queue/wait;
- escaped defect;
- cost per terminal product outcome.

## E. Независимые внешние ориентиры

Использовались первичные/официальные источники:

- [OpenHands SDK](https://docs.openhands.dev/sdk);
- [SWE-agent repository](https://github.com/SWE-agent/SWE-agent);
- [GitHub Spec Kit](https://github.com/github/spec-kit);
- [Backstage Software Catalog](https://backstage.io/docs/features/software-catalog/);
- [Temporal durable execution](https://docs.temporal.io/evaluate/understanding-temporal);
- [Open Policy Agent](https://www.openpolicyagent.org/docs/);
- [OPA pull-request checks](https://www.openpolicyagent.org/docs/cicd/pr-checks);
- [SLSA build requirements](https://slsa.dev/spec/v1.2/build-requirements);
- [in-toto](https://in-toto.io/);
- [in-toto test result predicate](https://in-toto.io/attestation/test-result/);
- [SPACE framework](https://queue.acm.org/detail.cfm?id=3454124);
- [DORA metrics](https://dora.dev/guides/dora-metrics/);
- [Google mutation testing research](https://research.google/pubs/long-term-effects-of-mutation-testing/);
- [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use);
- [GitHub coding agent risks](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations);
- [OWASP Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/);
- [NIST SSDF for generative AI](https://csrc.nist.gov/pubs/sp/800/218/a/final);
- [GitHub private vulnerability reporting](https://docs.github.com/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository);
- [Python 3.12 latest security release](https://www.python.org/downloads/latest/python3.12/);
- [npm package lock](https://docs.npmjs.com/cli/v8/configuring-npm/package-lock-json/);
- [npm ci](https://docs.npmjs.com/cli/v9/commands/npm-ci/);
- [Playwright Docker](https://playwright.dev/docs/docker).

## F. Предлагаемая структура следующего официального Roadmap ADWF

Roadmap-документ, создаваемый из этого аудита, целесообразно ограничить следующими полями:

| Поле | Смысл |
|---|---|
| Outcome | Что станет проще/безопаснее для владельца |
| Evidence gap | Чего сегодня нельзя доказать |
| Risk | R0–R4 с объяснением |
| Consumer | Где будет живое доказательство |
| Work boundary | Что входит и что не входит |
| Process budget | PR/branch/check/human limits |
| Exit criterion | Наблюдаемый измеримый результат |
| Evidence ID | Exact immutable proof |
| State | PLANNED/ACTIVE/BLOCKED/IMPLEMENTED/LIVE_VERIFIED/RETIRED |
| Observed at | Свежесть |
| Dependencies | Только реальные blocking dependencies |
| Trigger | Для условных research options |

Первые восемь items должны быть ровно задачами фазы 0, а не продолжением старой линейной foundation-цепочки.

---

**Краткая формула аудита**

> ADWF следует развивать не в ширину, а в истинность, связанность и дешевизну полного цикла. Сначала доказать один реальный результат двадцать раз; затем масштабировать только то, что оказалось необходимым.
