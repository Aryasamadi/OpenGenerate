"""Worker role: ops server, scheduler-leader heartbeat, job runner, outbox dispatcher."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Mapping

import uvicorn

from opengenerate import __version__
from opengenerate.bootstrap.settings import Settings
from opengenerate.infrastructure import health
from opengenerate.infrastructure.db.engine import Database
from opengenerate.infrastructure.jobs import Handler, JobRunner
from opengenerate.infrastructure.leader import AdvisoryLockLeader
from opengenerate.infrastructure.logging import configure_logging, get_logger
from opengenerate.infrastructure.outbox import OutboxDispatcher
from opengenerate.infrastructure.ratelimit import record_heartbeat
from opengenerate.infrastructure.telegram.raw_api import RawBotApi, TelegramOutboxSender
from opengenerate.interfaces.ops import build_ops_app
from opengenerate.migrations import head_revision

log = get_logger("worker")
SCHEDULER_LOCK_KEY = 0x4F47_5343_4845_4455  # "OGSCHEDU"
HEARTBEAT_NAME = "worker"


async def run_worker(
    settings: Settings,
    *,
    stop: asyncio.Event | None = None,
    handlers: Mapping[str, Handler] | None = None,
    serve_ops: bool = True,
    heartbeat_interval: float = 5.0,
    outbox_interval: float = 1.0,
) -> None:
    stop = stop or asyncio.Event()
    db = Database(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        application_name="og-worker",
    )
    api = RawBotApi(settings.bot_token, base_url=settings.telegram_api_base)
    leader = AdvisoryLockLeader(settings.database_url.get_secret_value(), SCHEDULER_LOCK_KEY)
    runner = JobRunner(db, handlers or {}, concurrency=4)
    dispatcher = OutboxDispatcher(db, TelegramOutboxSender(api))

    readiness = {
        "database": health.db_check(db),
        "migrations": health.migration_check(db, head_revision()),
    }
    detail = {
        "worker_heartbeat": health.heartbeat_check(db, HEARTBEAT_NAME, max_age=heartbeat_interval * 6),
        "outbox": health.outbox_check(db),
        "ledger": health.ledger_check(db),
    }
    server: uvicorn.Server | None = None
    tasks: list[asyncio.Task[None]] = []

    async def beat_loop() -> None:
        while not stop.is_set():
            if not leader.is_leader:
                await leader.try_acquire()
            else:
                await leader.check()
            await record_heartbeat(db, HEARTBEAT_NAME, {"leader": leader.is_leader, "version": __version__})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=heartbeat_interval)

    async def outbox_loop() -> None:
        while not stop.is_set():
            try:
                handled = await dispatcher.run_once() if settings.send_enabled else 0
            except Exception:
                log.exception("outbox_loop_error")
                handled = 0
            if not handled:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=outbox_interval)

    try:
        if serve_ops:
            app = build_ops_app(role="worker", version=settings.git_sha, readiness=readiness, health=detail)
            server = uvicorn.Server(
                uvicorn.Config(app, host=settings.ops_host, port=settings.ops_port, log_config=None, access_log=False)
            )
            tasks.append(asyncio.create_task(server.serve(), name="ops"))
        tasks += [
            asyncio.create_task(beat_loop(), name="heartbeat"),
            asyncio.create_task(outbox_loop(), name="outbox"),
            asyncio.create_task(runner.run(settings.shutdown_grace_seconds), name="jobs"),
        ]
        log.info("worker_started", version=__version__)
        await stop.wait()
    finally:
        log.info("worker_stopping")
        runner.stop()
        if server is not None:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        await leader.release()
        await api.aclose()
        await db.dispose()
        log.info("worker_stopped")


def install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loop
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def worker_main(settings: Settings) -> None:
    configure_logging(settings.log_level, json=settings.log_json)
    stop = asyncio.Event()
    install_signal_handlers(stop)
    await run_worker(settings, stop=stop)
