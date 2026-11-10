"""Minimal asyncio HTTP/1.1 server used as a fake upstream (sources, Telegram, LLM) in tests."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class Req:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Resp:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


Handler = Callable[[Req], Awaitable[Resp] | Resp]


class FakeServer:
    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.requests: list[Req] = []
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> FakeServer:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def allow(self) -> str:
        return f"127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)

    async def __aenter__(self) -> FakeServer:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {
                k.strip().lower(): v.strip()
                for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)
            }
            body = await reader.readexactly(int(headers.get("content-length", 0)))
            req = Req(method, path, headers, body)
            self.requests.append(req)
            result = self._handler(req)
            resp = await result if asyncio.iscoroutine(result) else result
            assert isinstance(resp, Resp)
            out = [f"HTTP/1.1 {resp.status} X".encode()]
            hdrs = {"Content-Length": str(len(resp.body)), "Connection": "close", **resp.headers}
            out += [f"{k}: {v}".encode() for k, v in hdrs.items()]
            writer.write(b"\r\n".join(out) + b"\r\n\r\n" + resp.body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
