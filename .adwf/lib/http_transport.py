"""Standard-library HTTP transport for provider contract tests/adapters."""
from __future__ import annotations
from .provider_contracts import HttpResponse
import socket, urllib.request, urllib.error

def urllib_transport(method: str, url: str, headers: dict[str,str], body: bytes|None, timeout: float) -> HttpResponse:
    request=urllib.request.Request(url,data=body,method=method,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            return HttpResponse(int(response.status),dict(response.headers.items()),response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(int(exc.code),dict(exc.headers.items()) if exc.headers else {},exc.read())
    except (socket.timeout,TimeoutError) as exc:
        raise TimeoutError('PROVIDER_TIMEOUT') from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason,(socket.timeout,TimeoutError)):
            raise TimeoutError('PROVIDER_TIMEOUT') from exc
        raise OSError('PROVIDER_NETWORK_ERROR') from exc
