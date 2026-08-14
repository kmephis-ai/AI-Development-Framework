# Семантика пользовательских команд

## «Делай далее»

Означает: reconcile → Recovery/unfinished Review → продолжить единственного Writer → иначе взять ровно один claimable READY item → gates/review/runtime evidence → разрешённый transition → автоматически повторить цикл. Не требует повторной команды после каждого Issue.

Не означает: придумывать вторую Roadmap, брать несколько Writer, обходить blocker, расширять scope, включать provider, ослаблять gate или выполнять платное/разрушительное действие.

## «Статус»

Показать владельцу четыре Health-контура, текущий Roadmap ID/Issue/Writer, queue, evidence-based progress, cost/provider и ровно одно следующее действие. `NOT_VERIFIED` показывается явно.

## «Добавь функцию» / «измени поведение»

ИИ сначала создаёт или изменяет один Roadmap item и его Issue contract, классифицирует risk/dependencies/product impact, затем выполняет обычный lifecycle. Внедрение не начинается, если item не READY.

## «Проводи аудит»

По умолчанию read-only. Findings содержат severity, конкретное evidence, impact, remediation и критерий закрытия.

## «Внедряй результаты аудита»

Дедупликация → приоритет → отдельные Roadmap IDs/Issues → обычный lifecycle. Mega-PR запрещён.

