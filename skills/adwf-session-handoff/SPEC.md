# Contract: adwf-session-handoff

Цель: передать следующей AI-сессии минимальный проверяемый state, достаточный для безопасного продолжения работы.

Handoff содержит: objective, canonical repository/ref/SHA, active work unit/writer lease, merged/open PR facts, changed surfaces, fresh test/evidence status, blockers, owner decision if exact-SHA valid, next safe action.

Не содержит: hidden reasoning, credentials, tokens, private user data без необходимости, непроверенные claims.
