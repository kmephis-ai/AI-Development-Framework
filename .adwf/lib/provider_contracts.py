"""Dependency-injected provider HTTP contracts used by every GitHub/GitLab adapter."""
from __future__ import annotations
from .strict_json import loads as strict_loads
from dataclasses import dataclass
from typing import Any, Callable
import json, time

@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

class ProviderContractError(RuntimeError):
    pass

Transport = Callable[[str, str, dict[str, str], bytes | None, float], HttpResponse]
TRANSIENT = {429, 500, 502, 503, 504}
FATAL = {400, 401, 403, 404, 409, 412, 422}


def request_value(transport: Transport, method: str, url: str, headers: dict[str, str],
                  payload: dict[str, Any] | None = None, *, timeout: float = 10.0,
                  max_attempts: int = 2) -> tuple[Any, HttpResponse]:
    if max_attempts < 1 or max_attempts > 3: raise ValueError("PROVIDER_RETRY_BUDGET_INVALID")
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    last: HttpResponse | None = None
    for attempt in range(max_attempts):
        try:
            response = transport(method, url, headers, body, timeout)
        except (TimeoutError, OSError) as exc:
            if attempt + 1 >= max_attempts: raise ProviderContractError("PROVIDER_TIMEOUT_OR_NETWORK") from exc
            continue
        last=response
        if 200 <= response.status < 300:
            try: value = strict_loads(response.body.decode("utf-8")) if response.body else {}
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc: raise ProviderContractError("PROVIDER_MALFORMED_JSON") from exc
            return value,response
        if response.status in FATAL: raise ProviderContractError(f"PROVIDER_HTTP_{response.status}")
        if response.status not in TRANSIENT or attempt + 1 >= max_attempts: raise ProviderContractError(f"PROVIDER_HTTP_{response.status}")
        retry_after=response.headers.get("Retry-After","0")
        try: delay=min(max(float(retry_after),0.0),1.0)
        except ValueError: delay=0.0
        if delay: time.sleep(delay)
    raise ProviderContractError(f"PROVIDER_HTTP_{last.status if last else 'UNKNOWN'}")



def request_bytes(transport: Transport, method: str, url: str, headers: dict[str, str], *, timeout: float = 10.0, max_attempts: int = 2) -> tuple[bytes, HttpResponse]:
    """Bounded provider read for non-JSON resources such as trusted job logs."""
    if max_attempts < 1 or max_attempts > 3: raise ValueError("PROVIDER_RETRY_BUDGET_INVALID")
    last: HttpResponse | None = None
    for attempt in range(max_attempts):
        try:
            response=transport(method,url,headers,None,timeout)
        except (TimeoutError,OSError) as exc:
            if attempt+1>=max_attempts: raise ProviderContractError("PROVIDER_TIMEOUT_OR_NETWORK") from exc
            continue
        last=response
        if 200 <= response.status < 300: return response.body,response
        if response.status in FATAL: raise ProviderContractError(f"PROVIDER_HTTP_{response.status}")
        if response.status not in TRANSIENT or attempt+1>=max_attempts: raise ProviderContractError(f"PROVIDER_HTTP_{response.status}")
        retry_after=response.headers.get("Retry-After","0")
        try: delay=min(max(float(retry_after),0.0),1.0)
        except ValueError: delay=0.0
        if delay: time.sleep(delay)
    raise ProviderContractError(f"PROVIDER_HTTP_{last.status if last else 'UNKNOWN'}")

def request_json(transport: Transport, method: str, url: str, headers: dict[str, str],
                 payload: dict[str, Any] | None = None, *, timeout: float = 10.0,
                 max_attempts: int = 2) -> tuple[dict[str, Any], HttpResponse]:
    value,response=request_value(transport,method,url,headers,payload,timeout=timeout,max_attempts=max_attempts)
    if not isinstance(value,dict): raise ProviderContractError("PROVIDER_PARTIAL_OR_NON_OBJECT_RESPONSE")
    return value,response


def _next_link(headers:dict[str,str], current:str)->str|None:
    from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
    link=headers.get("Link","")
    for part in link.split(","):
        if 'rel="next"' in part:
            left=part.split(";",1)[0].strip()
            if left.startswith("<") and left.endswith(">"): return left[1:-1]
    nxt=headers.get("X-Next-Page","").strip()
    if nxt:
        parts=urlsplit(current); query=dict(parse_qsl(parts.query,keep_blank_values=True)); query["page"]=nxt
        return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
    return None


def request_list_pages(transport:Transport,url:str,headers:dict[str,str],*,timeout:float=10.0,max_pages:int=20,
                       object_key:str|None=None)->list[dict[str,Any]]:
    if max_pages<1 or max_pages>100: raise ValueError("PROVIDER_PAGE_BUDGET_INVALID")
    items:list[dict[str,Any]]=[]; current=url
    for _ in range(max_pages):
        value,response=request_value(transport,"GET",current,headers,timeout=timeout)
        page=value.get(object_key) if object_key and isinstance(value,dict) else value
        if not isinstance(page,list) or not all(isinstance(x,dict) for x in page): raise ProviderContractError("PROVIDER_PAGE_ITEMS_INVALID")
        items.extend(page); nxt=_next_link(response.headers,current)
        if not nxt: return items
        current=nxt
    raise ProviderContractError("PROVIDER_PAGINATION_BUDGET_EXCEEDED")


def request_pages(transport: Transport, url: str, headers: dict[str, str], *, timeout: float = 10.0, max_pages: int = 20) -> list[dict[str, Any]]:
    """Backward-compatible object pagination where payload is {'items': [...]}"""
    return request_list_pages(transport,url,headers,timeout=timeout,max_pages=max_pages,object_key="items")


def mutate_with_readback(transport: Transport, *, method: str, url: str, readback_url: str, headers: dict[str,str], payload: dict[str,Any], idempotency_key: str, timeout: float = 10.0) -> tuple[dict[str,Any], dict[str,Any]]:
    if method not in {"POST","PATCH","PUT","DELETE"}: raise ValueError("PROVIDER_MUTATION_METHOD_INVALID")
    if len(str(idempotency_key)) < 12: raise ValueError("PROVIDER_IDEMPOTENCY_KEY_REQUIRED")
    h=dict(headers); h["Idempotency-Key"]=str(idempotency_key)
    mutation,_=request_json(transport,method,url,h,payload,timeout=timeout,max_attempts=1)
    readback,_=request_json(transport,"GET",readback_url,headers,timeout=timeout,max_attempts=2)
    return mutation,readback
