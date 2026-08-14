"""ADWF v1.6 Executive Portal: Durable Orchestrator is the only workflow SSOT."""
from __future__ import annotations
from .strict_json import loads as strict_loads
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from typing import Any
import html, json, secrets, os

from .adwf_core import load_json
from .dashboard import render_executive_html
from .health import active_state_path, doctor
from .cost_guard import evaluate_provider
from .roadmap_view import build_roadmap_view
from .runtime_supervisor import RuntimeSupervisor
from .durable_orchestrator import OrchestrationJournal
from .work_memory import WorkMemoryStore
from .owner_intent_service import create_intent as durable_create_intent, start_or_queue
from .owner_authority import accept_and_continue
from .github_bootstrap import bootstrap_repository
from .portfolio import register_project, portfolio_view


def bootstrap_plan(product: str, outcome: str, *, public_confirmed: bool, license_acknowledged: bool) -> dict[str, Any]:
    product=str(product).strip(); outcome=str(outcome).strip()
    if len(product)<2 or len(outcome)<5: raise ValueError('OWNER_BOOTSTRAP_ANSWERS_INCOMPLETE')
    if not public_confirmed or not license_acknowledged:
        return {'status':'HUMAN_REQUIRED','questions_used':3,'reason':'PUBLICATION_OR_LICENSE_DECISION_REQUIRED','product':product,'outcome':outcome,'repository_visibility':'NOT_DECIDED'}
    return {'status':'READY','questions_used':3,'product':product,'outcome':outcome,'repository_visibility':'PUBLIC','profile':'FREE_PUBLIC_GITHUB','runner':'ubuntu-24.04','projected_cost_usd':0,'required_checks':['fast-feedback','adwf/governance-gate','adwf/trusted-gate'],'next_action':'GITHUB_BOOTSTRAP'}


def create_intent(task: str) -> dict[str, Any]:
    return durable_create_intent(task)


def persist_intent(root: Path, task: str) -> dict[str, Any]:
    """Compatibility helper. Refuses to mutate an active run."""
    result=start_or_queue(root,task,queue_if_busy=False,wake=False)
    if result.get('status')=='ACTIVE_TASK_EXISTS': raise ValueError('ACTIVE_TASK_EXISTS')
    return result['brief']


def start_autopilot(root: Path, task: str) -> dict[str, Any]:
    return start_or_queue(root,task,queue_if_busy=True,wake=True)

def persist_bootstrap(root: Path, product: str, outcome: str, confirmed: bool) -> dict[str, Any]:
    plan=bootstrap_plan(product,outcome,public_confirmed=confirmed,license_acknowledged=confirmed)
    if plan.get('status')=='READY':
        live=bootstrap_repository(root,apply=True)
        plan['github']=live
        if live.get('status')=='VERIFIED': plan['status']='READY'
        elif live.get('status') in {'WAITING_SEED_CHECKS','WAITING_OWNER_GOVERNANCE_APPROVAL','READY_TO_APPLY'}: plan['status']=live['status']
        else: plan['status']='HUMAN_REQUIRED'
    _atomic_json(root/'.adwf-runtime/bootstrap-plan.json',plan); return plan

@dataclass
class PortalContext: root:Path; token:str

def _status_copy(health:dict[str,Any])->tuple[str,str]:
    product=health['categories']['product_health']['status']; control=health['categories']['control_plane_health']['status']
    if product in {'VERIFIED','HEALTHY'} and control in {'VERIFIED','HEALTHY'}: return '🟢','Работает и подтверждено'
    if 'BROKEN' in {product,control}: return '🔴','Нужна помощь'
    return '🟠','Проверка ещё не завершена'

def _roadmap_html(view:dict[str,Any])->str:
    summary=view['summary']; rows=[]
    icons={'DONE':'✓','IN_PROGRESS':'●','REVIEW':'◐','VERIFICATION':'◐','BLOCKED':'!','RECOVERY':'!','READY':'○','PLANNED':'○'}
    for goal in view.get('goals') or []:
        tasks=''.join(f"<li><b>{icons.get(t['state'],'○')}</b> {html.escape(str(t['title_ru']))} <small>{html.escape(t['state'])}</small></li>" for t in goal.get('tasks') or [])
        rows.append(f"<h3>{html.escape(str(goal.get('title_ru') or 'Цель'))}</h3><ul>{tasks}</ul>")
    return f"<div class='progress'><span style='width:{summary['product_done']*100:.0f}%'></span></div><p>Реализовано {summary['implemented']*100:.0f}% · Проверено {summary['verified']*100:.0f}% · Работает в продукте {summary['product_done']*100:.0f}%</p>"+''.join(rows)

def _portal_page(ctx:PortalContext,message:str='')->str:
    root=ctx.root; state=load_json(active_state_path(root)); health=doctor(root); cfg=load_json(root/'.adwf/config.json'); providers=load_json(root/'.adwf/providers.json'); cap=cfg.get('cost',{}).get('default_ci_capability')
    provider_observed=(state.get('provider') or {}).get('observed_at'); cost_usage=state.get('cost_usage') or {}
    if provider_observed and cost_usage.get('status') in {'VERIFIED','VERIFIED_ZERO','ALLOW_ZERO_COST'}:
        cost=evaluate_provider(providers,{'provider':cap,'mandatory_ci':False,'automated':True,'projected_cost':0,'repository_visibility':'PUBLIC','runner_class':'standard'},canonical_provider='github')
    else: cost={'result':'NOT_VERIFIED','reason_codes':['LIVE_PROVIDER_COST_READBACK_MISSING'],'provider':cap,'projected_cost_usd':None}
    roadmap=build_roadmap_view(root,state); icon,status_ru=_status_copy(health); exp=state.get('owner_experience') or {}; preview=exp.get('current_preview') or {}; brief=exp.get('product_brief') or {}
    memory=WorkMemoryStore(root).load(); active_runs=OrchestrationJournal(root).list_active()
    active_phase=active_runs[0].get('phase') if len(active_runs)==1 else None
    next_ru=(memory or {}).get('next_action_ru') or (f'Текущий этап: {active_phase}.' if active_phase else ('Опишите первую задачу.' if not brief.get('brief_id') else 'Система готовит следующий безопасный шаг.'))
    preview_link=''; url=preview.get('url')
    if isinstance(url,str) and (url.startswith('https://') or url.startswith('http://127.0.0.1') or url.startswith('http://localhost')): preview_link=f"<p><a class='secondary' href='{html.escape(url,quote=True)}' target='_blank' rel='noopener'>Посмотреть результат</a></p>"
    dashboard=html.escape(render_executive_html(state,health,cost),quote=True)
    details=f"<details><summary>Технические детали</summary><iframe sandbox srcdoc=\"{dashboard}\"></iframe></details>"
    continue_disabled='' if brief.get('brief_id') or preview.get('head_sha') else 'disabled'
    try:
        portfolio=portfolio_view();cards=''.join(f"<li><b>{html.escape(str(x.get('name')))}</b> — {html.escape(str(x.get('product_status')))}{(' · '+html.escape(str(x.get('active_phase')))) if x.get('active_phase') else ''}</li>" for x in portfolio.get('projects') or [])
        portfolio_html=f"<section><h2>Все проекты</h2><ul>{cards or '<li>Только текущий проект</li>'}</ul></section>"
    except (OSError,ValueError):portfolio_html=''
    metrics_path=root/'.adwf-runtime/metrics/current.json'; performance_html=''
    if metrics_path.is_file():
        try:
            perf=strict_loads(metrics_path.read_text(encoding='utf-8')); ps=perf.get('status','NOT_VERIFIED'); summ=perf.get('summary') or {}
            performance_html=f"<section><h2>Скорость CI</h2><p><b>{html.escape(str(ps))}</b> · измерений {int(summ.get('runs') or 0)} · p95 {html.escape(str(summ.get('p95_duration_seconds') or '—'))} с</p><small>Порог считается доказанным только после достаточного числа живых запусков.</small></section>"
        except (ValueError,OSError,TypeError): performance_html=''
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ADWF v1.6 Executive Portal</title>
<style>body{{font:17px system-ui;margin:0;background:#f5f7fb;color:#172033}}main{{max-width:980px;margin:auto;padding:28px}}.hero,section{{background:white;border:1px solid #e4e7ec;border-radius:20px;padding:22px;margin:16px 0}}h1{{font-size:34px;margin:.2em 0}}.state{{font-size:28px;font-weight:750}}textarea,input{{font:inherit;padding:12px;border:1px solid #ccd2dc;border-radius:10px;box-sizing:border-box}}textarea{{width:100%;min-height:100px}}button.primary{{width:100%;font-size:24px;font-weight:750;padding:20px;border:0;border-radius:16px;background:#172033;color:white;cursor:pointer}}button.primary:disabled{{opacity:.35}}button.secondary,.secondary{{display:inline-block;padding:10px 14px;border:1px solid #ccd2dc;border-radius:10px;background:white;color:#172033;text-decoration:none}}small{{color:#667085}}ul{{line-height:1.8}}.progress{{height:12px;background:#eef1f5;border-radius:9px;overflow:hidden}}.progress span{{display:block;height:100%;background:#172033}}details{{margin-top:18px}}iframe{{width:100%;height:820px;border:0;margin-top:12px}}</style><main>
<h1>Мой цифровой продукт</h1><div class="hero"><div class="state">{icon} {status_ru}</div><p>{html.escape(message)}</p><h2>Что делать дальше</h2><p>{html.escape(str(next_ru))}</p>{preview_link}<form method="post" action="/continue"><input type="hidden" name="csrf" value="{ctx.token}"><button class="primary" {continue_disabled}>ПРОДОЛЖИТЬ</button></form><p><small>Кнопка выполняет только рекомендованное безопасное следующее действие. Она не обходит проверки.</small></p></div>
<section><h2>Новая задача</h2><form method="post" action="/intent"><input type="hidden" name="csrf" value="{ctx.token}"><textarea name="task" required placeholder="Например: Сделай страницу регистрации и покажи, как она выглядит на компьютере и телефоне"></textarea><button class="secondary">Передать задачу</button></form></section>
<section><h2>Дорожная карта</h2>{_roadmap_html(roadmap)}</section>{portfolio_html}{performance_html}
<section><h2>Первое подключение</h2><details><summary>Настройка нового проекта</summary><form method="post" action="/bootstrap"><input type="hidden" name="csrf" value="{ctx.token}"><p><input name="product" required placeholder="Что за продукт?" style="width:100%"></p><textarea name="outcome" required placeholder="Какой результат нужен?"></textarea><p><label><input type="checkbox" name="confirmed" value="yes" required> Repository публичный; решение по LICENSE принято владельцем.</label></p><button class="secondary">Подключить и защитить проект</button></form></details></section>{details}</main></html>'''

def _continue(ctx:PortalContext)->str:
    state=load_json(active_state_path(ctx.root)); exp=state.get('owner_experience') or {}; preview=exp.get('current_preview') or {}; brief=exp.get('product_brief') or {}
    active=OrchestrationJournal(ctx.root).list_active()
    if len(active)!=1:return 'Нет одной однозначной активной задачи. Новая работа не будет начата автоматически.'
    run=active[0]
    if run.get('phase')=='OWNER_ACCEPTANCE':
        head=str(run.get('subject_sha') or preview.get('head_sha') or '');digest=str(run.get('preview_digest') or preview.get('preview_digest') or '');brief_id=str(run.get('roadmap_id') or brief.get('brief_id') or '')
        result=accept_and_continue(ctx.root,brief_id=brief_id,head_sha=head,preview_digest=digest)
        if result.get('status')=='CONTINUED':return 'Результат подтверждён владельцем. Trusted controller получил сигнал и продолжает безопасный цикл.'
        return 'Работа остановлена безопасно: '+str(result.get('reason') or result.get('status'))
    result=RuntimeSupervisor(ctx.root).tick(run['run_id'])
    return f"Autopilot: {result.get('status')}."

def make_handler(ctx:PortalContext):
    class Handler(BaseHTTPRequestHandler):
        def _send(self,status:int,body:str):
            data=body.encode(); self.send_response(status); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-store'); self.send_header('X-Frame-Options','DENY'); self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'"); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            if self.path!='/':return self._send(404,'Not found')
            self._send(200,_portal_page(ctx))
        def do_POST(self):
            length=min(int(self.headers.get('Content-Length','0') or 0),100_000); form=parse_qs(self.rfile.read(length).decode('utf-8','replace'))
            if (form.get('csrf') or [''])[0]!=ctx.token:return self._send(403,_portal_page(ctx,'Сессия устарела. Обновите страницу.'))
            try:
                if self.path=='/bootstrap':
                    plan=persist_bootstrap(ctx.root,(form.get('product') or [''])[0],(form.get('outcome') or [''])[0],(form.get('confirmed') or [''])[0]=='yes');return self._send(200,_portal_page(ctx,f"Подключение: {plan['status']}."))
                if self.path=='/intent':
                    started=start_autopilot(ctx.root,(form.get('task') or [''])[0]);brief=(started.get('brief') or {});bid=brief.get('brief_id')
                    message=(f"Задача принята: {bid}. Autopilot: {started['status']}." if bid else f"Autopilot не начал новую задачу: {started.get('status')}. {started.get('reason','')}")
                    return self._send(200,_portal_page(ctx,message))
                if self.path=='/continue': return self._send(200,_portal_page(ctx,_continue(ctx)))
                if self.path=='/decision': return self._send(410,_portal_page(ctx,'Используйте одну кнопку «Продолжить».'))
                return self._send(404,'Not found')
            except (TypeError,ValueError,KeyError) as exc:return self._send(400,_portal_page(ctx,f'Работа остановлена безопасно: {exc}'))
        def log_message(self,fmt,*args):pass
    return Handler

def serve(root:Path,*,bind:str='127.0.0.1',port:int=8765)->None:
    if bind not in {'127.0.0.1','localhost','::1'}:raise ValueError('OWNER_PORTAL_NON_LOOPBACK_FORBIDDEN')
    try:register_project(root)
    except (OSError,ValueError):pass
    token=secrets.token_urlsafe(32); server=ThreadingHTTPServer((bind,port),make_handler(PortalContext(root.resolve(),token)))
    print(f'ADWF Executive Portal: http://{bind}:{server.server_address[1]}/',flush=True); print('Local session active; trusted merge still requires provider readback.',flush=True); server.serve_forever()
