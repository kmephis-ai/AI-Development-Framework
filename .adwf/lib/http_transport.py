"""Standard-library HTTP transport for provider contract tests/adapters."""
from __future__ import annotations

import socket
import urllib.error
import urllib.request
from urllib.parse import SplitResult, urlsplit

from .provider_contracts import HttpResponse

_SENSITIVE_REDIRECT_HEADERS = {"authorization", "proxy-authorization", "cookie"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _effective_port(parts: SplitResult) -> int | None:
    if parts.port is not None:
        return parts.port
    return _DEFAULT_PORTS.get(parts.scheme.lower())


def _origin(url: str) -> tuple[str, str, int | None]:
    """Return RFC-style origin identity with normalized default ports."""
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), _effective_port(parts)


def _strip_sensitive_redirect_headers(request: urllib.request.Request) -> None:
    """Remove sensitive headers case-insensitively from both Request stores."""
    for store in (request.headers, request.unredirected_hdrs):
        for key in list(store):
            if key.lower() in _SENSITIVE_REDIRECT_HEADERS:
                store.pop(key, None)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward provider credentials to a different redirect origin.

    GitHub job-log/download endpoints can redirect an authenticated API request
    to a short-lived signed object-storage URL. The signed target does not need
    the provider credential. Forwarding it across origins violates the provider
    credential boundary and can also make the storage endpoint reject the read.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and _origin(req.full_url) != _origin(newurl):
            _strip_sensitive_redirect_headers(redirected)
        return redirected


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(int(response.status), dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(int(exc.code), dict(exc.headers.items()) if exc.headers else {}, exc.read())
    except (socket.timeout, TimeoutError) as exc:
        raise TimeoutError("PROVIDER_TIMEOUT") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError("PROVIDER_TIMEOUT") from exc
        raise OSError("PROVIDER_NETWORK_ERROR") from exc
