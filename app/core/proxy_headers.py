from __future__ import annotations

from typing import Iterable, List, Union

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class ProxyHeadersMiddleware(BaseHTTPMiddleware):
    """Minimal implementation of Starlette's ProxyHeadersMiddleware.

    Adjusts ``scope['server']`` and ``scope['scheme']`` based on standard
    proxy headers such as ``X-Forwarded-Host`` and ``X-Forwarded-Proto``.
    This allows the application to correctly generate URLs when running
    behind a reverse proxy that forwards these headers.

    Parameters
    ----------
    app:
        The ASGI application.
    trusted_hosts:
        Iterable of hostnames that are allowed to override the host header.
        Use ``"*"`` to trust any host.
    """

    def __init__(
        self,
        app,
        trusted_hosts: Union[str, Iterable[str]] = "*",
    ) -> None:
        super().__init__(app)
        if isinstance(trusted_hosts, str):
            self.trusted_hosts: List[str] = [trusted_hosts]
        else:
            self.trusted_hosts = list(trusted_hosts)

    def _host_allowed(self, host: str) -> bool:
        return "*" in self.trusted_hosts or host in self.trusted_hosts

    async def dispatch(self, request: Request, call_next):
        headers = request.headers

        host = headers.get("x-forwarded-host")
        proto = headers.get("x-forwarded-proto")

        if host and self._host_allowed(host):
            request.scope["server"] = (host, request.scope.get("server", (None, None))[1])
        if proto:
            request.scope["scheme"] = proto

        return await call_next(request)