"""Русскоязычная контрольная панель из проверяемого snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import html
from urllib.parse import urlsplit

from .evidence import parse_time
from .owner_experience import ceo_control_center_projection, render_ceo_control_center


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "NOT_VERIFIED"


def _metric(value: Any, suffix: str = "") -> str:
    if value is None:
        return "NOT_VERIFIED"
    try:
        return f"{float(value):.1f}{suffix}"
    except (TypeError, ValueError):
        return "NOT_VERIFIED"


def next_operator_action(state: dict[str, Any], decision: dict[str, Any] | None = None) -> str:
    owner_decisions = state.get("owner_decisions") or []
    if owner_decisions:
        return str(owner_decisions[0])
    blockers = state.get("blockers") or []
    if blockers:
        first = blockers[0]
        return str(first.get("owner_action_ru") if isinstance(first, dict) else first)
    if decision:
        action = decision.get("action")
        issue = decision.get("issue") or {}
        if action == "CONTINUE_EXISTING":
            return f"ИИ продолжает единственную активную задачу {issue.get('roadmap_id', issue.get('id'))}."
        if action == "CLAIM_ONE_READY":
            return f"ИИ берёт ровно одну задачу {issue.get('roadmap_id', issue.get('id'))}."
        if action == "RECONCILE":
            return "Feature work остановлен: ИИ должен устранить конфликт состояния."
    return "Настроить проект и получить свежие evidence; до этого автономное продвижение заблокировано."


def snapshot_status(state: dict[str, Any], *, now: datetime | None = None) -> str:
    snapshot = state.get("snapshot", {})
    try:
        valid_until = parse_time(snapshot["valid_until"])
        exact = snapshot.get("source_main_sha") == state.get("main", {}).get("head")
        return "FRESH" if exact and (now or datetime.now(timezone.utc)).astimezone(timezone.utc) < valid_until else "STALE"
    except (AttributeError, KeyError, TypeError, ValueError):
        return "NOT_VERIFIED"


def overall_message(state: dict[str, Any], health: dict[str, Any], cost: dict[str, Any]) -> str:
    categories = health.get("categories", {})
    governance = state.get("health", {})
    if state.get("owner_decisions"):
        return "ТРЕБУЕТСЯ РЕШЕНИЕ ВЛАДЕЛЬЦА"
    if (cost.get("result") != "ALLOW" or any(item.get("status") == "BROKEN" for item in categories.values())
            or any(governance.get(name) in {"BROKEN", "CRITICAL"} for name in ("roadmap", "architecture", "security", "debt"))):
        return "ОСТАНОВЛЕНО — разрешены только диагностика и Recovery"
    categories_safe = all(categories.get(name, {}).get("status") == "VERIFIED" for name in ("package_integrity", "config_health", "control_plane_health", "product_health"))
    governance_safe = all(governance.get(name) in {"VERIFIED", "HEALTHY"} for name in ("roadmap", "architecture", "security", "debt"))
    if categories_safe and governance_safe:
        return "МОЖНО ПРОДОЛЖАТЬ в пределах текущей автономности и риска"
    return "ОГРАНИЧЕННЫЙ РЕЖИМ — только настройка, проверка или Recovery"


def render_dashboard(state: dict[str, Any], health: dict[str, Any], cost: dict[str, Any], decision: dict[str, Any] | None = None) -> str:
    h = state.get("health", {})
    p = state.get("progress", {})
    q = state.get("queue", {})
    o = state.get("orchestration", {})
    active = state.get("active", {})
    provider = state.get("provider", {})
    snapshot = state.get("snapshot", {})
    workspace = state.get("workspace", {})
    metrics = state.get("ci_metrics", {})
    usage = state.get("cost_usage", {})
    lines = [
        "# ADWF Control Center",
        "",
        "> Генерируется из состояния и evidence. `NOT_VERIFIED` никогда не преобразуется в зелёный статус.",
        "",
        "## Общий статус",
        "",
        f"**{overall_message(state, health, cost)}**",
        "",
        "## Решение владельцу",
        "",
        f"**Следующее действие:** {next_operator_action(state, decision)}",
        "",
        *render_ceo_control_center(state).rstrip().splitlines(),
        "",
        "## Свежесть и источник истины",
        "",
        f"- Canonical provider: `{provider.get('mode', 'NOT_VERIFIED')}`",
        f"- Snapshot status: `{snapshot_status(state)}`",
        f"- Snapshot observed / valid until: `{snapshot.get('observed_at') or 'NOT_VERIFIED'}` / `{snapshot.get('valid_until') or 'NOT_VERIFIED'}`",
        f"- Snapshot main SHA: `{snapshot.get('source_main_sha') or 'NOT_VERIFIED'}`",
        f"- Evidence digest: `{snapshot.get('evidence_digest') or 'NOT_VERIFIED'}`",
        f"- GitHub Project projection/readback: `{state.get('project_projection', {}).get('status', 'NOT_VERIFIED')}` / `{state.get('project_projection', {}).get('observed_at') or 'NOT_VERIFIED'}`",
        "",
        "## Здоровье",
        "",
        "| Контур | Статус |",
        "| --- | --- |",
        f"| Package integrity | `{health.get('categories', {}).get('package_integrity', {}).get('status', 'NOT_VERIFIED')}` |",
        f"| Configuration | `{health.get('categories', {}).get('config_health', {}).get('status', 'NOT_VERIFIED')}` |",
        f"| Control plane | `{health.get('categories', {}).get('control_plane_health', {}).get('status', 'NOT_VERIFIED')}` |",
        f"| Product | `{health.get('categories', {}).get('product_health', {}).get('status', h.get('product', 'NOT_VERIFIED'))}` |",
        f"| Roadmap | `{h.get('roadmap', 'NOT_VERIFIED')}` |",
        f"| Architecture | `{h.get('architecture', 'NOT_VERIFIED')}` |",
        f"| Security | `{h.get('security', 'NOT_VERIFIED')}` |",
        f"| Debt | `{h.get('debt', 'NOT_VERIFIED')}` |",
        "",
        "## Работа",
        "",
        f"- Активный Roadmap ID: `{active.get('roadmap_id') or 'NONE'}`",
        f"- Стадия: `{active.get('state') or 'NONE'}`",
        f"- Issue / PR: `{active.get('issue') or 'NONE'}` / `{active.get('pr') or 'NONE'}`",
        f"- Writer: `{active.get('writer') or 'NONE'}`; активных Writer: `{o.get('writers_active', 'NOT_VERIFIED')}`",
        f"- Workspace: `{workspace.get('status', 'NOT_VERIFIED')}` / `{workspace.get('workspace_id') or 'NONE'}`",
        f"- Heartbeat / lease до / retry: `{workspace.get('heartbeat_at') or 'NOT_VERIFIED'}` / `{workspace.get('expires_at') or 'NOT_VERIFIED'}` / `{workspace.get('retry_count', 0)}`",
        f"- Queue READY / IN_PROGRESS / REVIEW / BLOCKED: `{q.get('ready', 0)}` / `{q.get('in_progress', 0)}` / `{q.get('review', 0)}` / `{q.get('blocked', 0)}`",
        "",
        "## Прогресс по evidence",
        "",
        f"- Implementation: `{_pct(p.get('implementation'))}`",
        f"- Verification: `{_pct(p.get('verification'))}`",
        f"- Product Readiness: `{_pct(p.get('product_readiness'))}`",
        f"- Verification Gap: `{_pct(p.get('verification_gap'))}`",
        "",
        "## Release и реальный продукт",
        "",
        f"- Main SHA: `{state.get('main', {}).get('head') or 'NOT_VERIFIED'}`",
        f"- Release / commit: `{state.get('release', {}).get('latest') or 'NOT_VERIFIED'}` / `{state.get('release', {}).get('commit') or 'NOT_VERIFIED'}`",
        f"- Deployed revision: `{state.get('runtime', {}).get('canonical_revision') or 'NOT_VERIFIED'}`",
        f"- Smoke / Golden Paths: `{state.get('runtime', {}).get('smoke', 'NOT_VERIFIED')}` / `{state.get('runtime', {}).get('golden_paths', 'NOT_VERIFIED')}`",
        f"- Последняя Reality-проверка: `{state.get('runtime', {}).get('last_reality_check') or 'NOT_VERIFIED'}`",
        "",
        "## Производительность CI",
        "",
        f"- Метрики / runs: `{metrics.get('status', 'NOT_VERIFIED')}` / `{metrics.get('runs', 0)}`",
        f"- p50 / p95 pipeline: `{_metric(metrics.get('p50_duration_seconds'), 's')}` / `{_metric(metrics.get('p95_duration_seconds'), 's')}`",
        f"- p95 до первой полезной ошибки: `{_metric(metrics.get('p95_time_to_first_failure_seconds'), 's')}`",
        f"- p95 очередь / flake rate: `{_metric(metrics.get('p95_queue_seconds'), 's')}` / `{_pct(metrics.get('flake_rate'))}`",
        "",
        "## Бесплатность и лимиты",
        "",
        f"- Результат cost guard: `{cost.get('result', 'NOT_VERIFIED')}`",
        f"- Provider: `{cost.get('provider', 'NOT_CONFIGURED')}`",
        f"- Денежный бюджет: `0`",
        f"- Причины блокировки: `{', '.join(cost.get('reason_codes', [])) or 'NONE'}`",
        f"- Usage snapshot: `{usage.get('status', 'NOT_VERIFIED')}`; capability `{usage.get('capability') or 'NOT_VERIFIED'}`",
        f"- Hosted minutes: `{_metric(usage.get('hosted_minutes_used'))}` / internal hard `{_metric(usage.get('hosted_minutes_internal_hard'))}`",
        f"- Artifacts / cache: `{_metric(usage.get('artifact_mb'), ' MB')}` / `{_metric(usage.get('cache_mb'), ' MB')}`",
        "",
        "## Требуется от владельца",
        "",
    ]
    decisions = state.get("owner_decisions") or []
    lines.extend([f"- {item}" for item in decisions] if decisions else ["- Отдельных решений не зарегистрировано; общая блокировка показана выше."])
    lines.extend([
        "",
        "## Технические блокировки",
        "",
    ])
    blockers = state.get("blockers") or []
    lines.extend([f"- {item}" for item in blockers] if blockers else ["- Нет зарегистрированных; отсутствие evidence показано в Health."])
    return "\n".join(lines) + "\n"


def _safe_url(value: Any) -> str | None:
    """Allow only HTTPS without credentials or a verified local-style artifact path."""
    text = str(value or "").strip()
    if not text or "\\" in text or text.startswith("//"):
        return None
    if text.startswith("./") or (text.startswith("/") and not text.startswith("//")):
        return html.escape(text, quote=True)
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return html.escape(text, quote=True)


def _cost_display(cost: dict[str, Any]) -> str:
    result = str(cost.get("result") or "NOT_VERIFIED").upper()
    projected = cost.get("projected_cost_usd", cost.get("projected_cost"))
    if result in {"BLOCK", "DENY"}:
        return "BLOCK"
    if result in {"ALLOW", "PASS", "VERIFIED"} and projected in {0, 0.0, "0", "0.0"}:
        return "$0 VERIFIED"
    return "NOT_VERIFIED"


def render_executive_html(
    state: dict[str, Any],
    health: dict[str, Any],
    cost: dict[str, Any],
    decision: dict[str, Any] | None = None,
) -> str:
    """Self-contained escaped CEO dashboard; no JS, network or paid service."""
    view = ceo_control_center_projection(state)
    esc = lambda value: html.escape(str(value if value is not None else "NOT_VERIFIED"))
    product_health = health.get("categories", {}).get("product_health", {}).get("status", "NOT_VERIFIED")
    overall = overall_message(state, health, cost)
    next_action = next_operator_action(state, decision)
    preview_url = _safe_url(view.get("preview_url"))
    preview = (
        f'<a class="primary" href="{preview_url}">Открыть точный результат</a>'
        if preview_url else '<span class="muted">Предпросмотр ещё не создан</span>'
    )
    status_class = "good" if product_health in {"VERIFIED", "HEALTHY"} else (
        "bad" if product_health == "BROKEN" else "warn"
    )
    health_rows = "".join(
        f"<tr><th>{esc(label)}</th><td><code>{esc(health.get('categories', {}).get(key, {}).get('status', 'NOT_VERIFIED'))}</code></td></tr>"
        for key, label in (
            ("package_integrity", "Пакет"), ("config_health", "Настройка"),
            ("control_plane_health", "Управление"), ("product_health", "Продукт"),
        )
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ADWF — панель владельца</title>
<style>
:root{{--ink:#172033;--muted:#667085;--line:#e4e7ec;--panel:#fff;--bg:#f5f7fb;--accent:#3157d5;--good:#087443;--warn:#a15c00;--bad:#b42318}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1120px;margin:auto;padding:32px 20px 64px}} h1{{font-size:clamp(28px,5vw,46px);line-height:1.05;margin:8px 0}} h2{{font-size:18px;margin:0 0 12px}}
.eyebrow{{color:var(--accent);font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:12px}} .muted{{color:var(--muted)}}
.banner,.card{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 8px 28px rgba(23,32,51,.05)}}
.banner{{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:center;margin:24px 0}} .grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}
.card{{grid-column:span 6}} .wide{{grid-column:span 12}} .metric{{font-size:28px;font-weight:750;margin:4px 0}} .good{{color:var(--good)}} .warn{{color:var(--warn)}} .bad{{color:var(--bad)}}
.primary{{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;font-weight:700;padding:11px 16px;border-radius:11px}} code{{font-size:13px;background:#f2f4f7;padding:3px 7px;border-radius:7px}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;border-bottom:1px solid var(--line);padding:9px 0}} th{{font-weight:600}} details{{margin-top:16px}} summary{{cursor:pointer;font-weight:700}}
@media(max-width:760px){{.banner{{grid-template-columns:1fr}}.card{{grid-column:span 12}}}}
</style>
</head>
<body><main>
<div class="eyebrow">ADWF v1.6 · Executive Control Center</div>
<h1>Цифровой продукт — одним взглядом</h1>
<p class="muted">Статусы выводятся только из state и evidence. Неизвестное не становится зелёным.</p>
<section class="banner">
  <div><div class="eyebrow">Общий статус</div><div class="metric {status_class}">{esc(overall)}</div><div>{esc(view['product_status_ru'])}</div></div>
  <div>{preview}</div>
</section>
<div class="grid">
  <section class="card wide"><h2>Что изменилось</h2><p>{esc(view['what_changed_ru'])}</p></section>
  <section class="card"><h2>Проверка автоматикой</h2><div class="metric"><code>{esc(view['machine_verified'])}</code></div><p class="muted">Привязана к exact SHA и evidence.</p></section>
  <section class="card"><h2>Решение владельца</h2><div class="metric"><code>{esc(view['owner_acceptance'])}</code></div><p>{esc(view['owner_decision_ru'])}</p></section>
  <section class="card"><h2>Надёжность</h2><p>Открытых инцидентов: <strong>{esc(view['open_incidents'])}</strong><br>Повторных: <strong>{esc(view['repeated_incidents'])}</strong></p><p>Safe Healing: <code>{esc(view['healing_status'])}</code> / <code>{esc(view['healing_level'] or 'NONE')}</code></p></section>
  <section class="card"><h2>Стоимость</h2><div class="metric"><code>{esc(_cost_display(cost))}</code></div><p>Cost guard: <code>{esc(cost.get('result', 'NOT_VERIFIED'))}</code><br>Provider: <code>{esc(cost.get('provider', 'NOT_CONFIGURED'))}</code></p></section>
  <section class="card wide"><h2>Следующее действие</h2><p><strong>{esc(next_action)}</strong></p></section>
</div>
<details class="card"><summary>Технические доказательства</summary><table>{health_rows}
<tr><th>Main SHA</th><td><code>{esc(state.get('main', {}).get('head'))}</code></td></tr>
<tr><th>Preview SHA</th><td><code>{esc(view.get('preview_head_sha'))}</code></td></tr>
<tr><th>Evidence digest</th><td><code>{esc(state.get('snapshot', {}).get('evidence_digest'))}</code></td></tr>
<tr><th>Policy/Control blockers</th><td>{esc(', '.join(str(item) for item in state.get('blockers', [])) or 'NONE')}</td></tr>
</table></details>
</main></body></html>
"""
