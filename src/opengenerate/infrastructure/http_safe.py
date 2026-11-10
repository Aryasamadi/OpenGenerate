"""SSRF-safe outbound HTTP.

Design (ADR-0009): every connection is validated *at connect time* inside the network backend, so
the address that is checked is exactly the address that is dialled (no DNS-rebinding gap). Redirects
are followed manually with full re-validation, credentials are stripped on origin change, and
responses are size/time bounded. Proxy environment variables and cookies are ignored.
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpcore
import httpx

from opengenerate.domain.errors import PermanentError, TransientError

Resolver = Callable[[str, int], Awaitable[list[str]]]

# Never reachable, not even through the private-network allowlist (cloud metadata etc.).
_ALWAYS_BLOCKED = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::/32"),
    ipaddress.ip_network("100.100.100.200/32"),
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_DEFAULT_SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


class SSRFBlocked(PermanentError):
    """Destination rejected by egress policy."""


class ResponseTooLarge(PermanentError):
    pass


class TooManyRedirects(PermanentError):
    pass


class UnexpectedContentType(PermanentError):
    pass


class UpstreamUnavailable(TransientError):
    """Network-level failure or timeout talking to the remote host."""


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _SIX_TO_FOUR:
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    if ip.teredo is not None:
        return ip.teredo[1]
    return None


def classify_ip(raw: str) -> tuple[bool, bool]:
    """Return (is_global, always_blocked) for an address string, unwrapping embedded IPv4."""
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(raw.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_v4(ip)
        if inner is not None:
            ip = inner
    always = any(ip in net for net in _ALWAYS_BLOCKED if net.version == ip.version)
    return ip.is_global and not always, always


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    private_allowlist: frozenset[str] = frozenset()  # exact "host:port"

    @classmethod
    def from_allowlist(cls, entries: Iterable[str]) -> EgressPolicy:
        return cls(private_allowlist=frozenset(e.lower() for e in entries))

    def allowlisted(self, host: str, port: int) -> bool:
        return f"{host.lower()}:{port}" in self.private_allowlist

    def check_url(self, url: httpx.URL) -> tuple[str, int]:
        if url.scheme not in self.allowed_schemes:
            raise SSRFBlocked(f"scheme '{url.scheme}' is not allowed")
        if url.userinfo:
            raise SSRFBlocked("credentials in URL are not allowed")
        host = url.host
        if not host:
            raise SSRFBlocked("URL has no host")
        port = url.port or (443 if url.scheme == "https" else 80)
        if port not in self.allowed_ports and not self.allowlisted(host, port):
            raise SSRFBlocked(f"port {port} is not allowed")
        return host, port

    def validate_addresses(self, host: str, port: int, addresses: list[str]) -> list[str]:
        if not addresses:
            raise SSRFBlocked("host did not resolve")
        allow_private = self.allowlisted(host, port)
        for addr in addresses:
            is_global, always = classify_ip(addr)
            if always or (not is_global and not allow_private):
                raise SSRFBlocked("destination address is not permitted")
        return addresses


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, policy: EgressPolicy, resolver: Resolver) -> None:
        self._policy = policy
        self._resolver = resolver
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = self._policy.validate_addresses(host, port, await self._resolver(host, port))
        last: Exception | None = None
        for addr in addresses:
            try:
                return await self._inner.connect_tcp(
                    addr, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except httpcore.ConnectError as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise SSRFBlocked("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: httpcore.AsyncNetworkBackend, *, limits: httpx.Limits) -> None:
        super().__init__(limits=limits)
        # httpx does not expose a network-backend hook; httpx is pinned to 0.28.x and the
        # behaviour is covered by tests/integration/test_safe_http.py.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            network_backend=backend,
        )


@dataclass(slots=True)
class SafeResponse:
    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str
    redirects: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        encoding = httpx.Response(200, headers=self.headers).charset_encoding or "utf-8"
        return self.content.decode(encoding, errors="replace")

    def json(self) -> Any:
        import json

        return json.loads(self.content)


class SafeHttpClient:
    def __init__(
        self,
        policy: EgressPolicy | None = None,
        *,
        resolver: Resolver = system_resolver,
        max_redirects: int = 5,
        max_response_bytes: int = 10 * 1024 * 1024,
        total_timeout: float = 30.0,
        user_agent: str = "OpenGenerate/0.1 (+https://github.com/Aryasamadi/OpenGenerate)",
        sensitive_headers: Iterable[str] = (),
    ) -> None:
        self.policy = policy or EgressPolicy()
        self._resolver = resolver
        self._max_redirects = max_redirects
        self._max_bytes = max_response_bytes
        self._total_timeout = total_timeout
        self._sensitive = _DEFAULT_SENSITIVE_HEADERS | {h.lower() for h in sensitive_headers}
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=10, keepalive_expiry=5.0)
        self._client = httpx.AsyncClient(
            transport=_PinnedTransport(_PinnedBackend(self.policy, resolver), limits=limits),
            timeout=httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": user_agent},
        )
        self._client.cookies.jar.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SafeHttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _preflight(self, url: httpx.URL) -> None:
        host, port = self.policy.check_url(url)
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
            addresses = [str(literal)]
        except ValueError:
            try:
                addresses = await self._resolver(host, port)
            except OSError as exc:
                raise UpstreamUnavailable(f"DNS resolution failed: {exc.__class__.__name__}") from None
        self.policy.validate_addresses(host, port, addresses)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        content: bytes | None = None,
        allowed_content_types: Iterable[str] | None = None,
        max_bytes: int | None = None,
    ) -> SafeResponse:
        limit = max_bytes or self._max_bytes
        try:
            async with asyncio.timeout(self._total_timeout):
                return await self._send(method, url, headers, json, content, allowed_content_types, limit)
        except TimeoutError:
            raise UpstreamUnavailable("request timed out") from None
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(f"{type(exc).__name__}") from None

    async def get(self, url: str, **kwargs: Any) -> SafeResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> SafeResponse:
        return await self.request("POST", url, **kwargs)

    async def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        json: Any,
        content: bytes | None,
        allowed_content_types: Iterable[str] | None,
        limit: int,
    ) -> SafeResponse:
        current = httpx.URL(url)
        hdrs = dict(headers or {})
        redirects: list[str] = []
        for _hop in range(self._max_redirects + 1):
            await self._preflight(current)
            request = self._client.build_request(
                method, current, headers=hdrs, json=json if method != "GET" else None,
                content=content if method != "GET" else None,
            )
            response = await self._client.send(request, stream=True)
            try:
                if response.status_code in _REDIRECT_CODES and "location" in response.headers:
                    nxt = httpx.URL(urljoin(str(current), response.headers["location"]))
                    redirects.append(str(nxt.copy_with(query=None)))
                    if (nxt.scheme, nxt.host, nxt.port or 0) != (current.scheme, current.host, current.port or 0):
                        hdrs = {k: v for k, v in hdrs.items() if k.lower() not in self._sensitive}
                    if response.status_code == 303 or (response.status_code in {301, 302} and method == "POST"):
                        method, json, content = "GET", None, None
                    current = nxt
                    continue
                return await self._read(response, current, redirects, allowed_content_types, limit)
            finally:
                await response.aclose()
        raise TooManyRedirects(f"more than {self._max_redirects} redirects")

    async def _read(
        self,
        response: httpx.Response,
        url: httpx.URL,
        redirects: list[str],
        allowed_content_types: Iterable[str] | None,
        limit: int,
    ) -> SafeResponse:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise ResponseTooLarge("declared content-length exceeds limit")
        if allowed_content_types is not None:
            ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in {c.lower() for c in allowed_content_types}:
                raise UnexpectedContentType(f"content-type '{ctype or 'missing'}' not allowed")
        body = bytearray()
        async for chunk in response.aiter_bytes():  # decoded bytes: also bounds decompression bombs
            body.extend(chunk)
            if len(body) > limit:
                raise ResponseTooLarge("response body exceeds limit")
        return SafeResponse(response.status_code, response.headers, bytes(body), str(url.copy_with(query=None)), redirects)
