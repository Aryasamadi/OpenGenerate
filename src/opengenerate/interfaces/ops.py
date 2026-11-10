"""Internal operations HTTP server: /livez /readyz /healthz /metrics."""

from __future__ import annotations

from collections.abc import Mapping

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from opengenerate.infrastructure.health import Check, CheckResult, overall


async def _run_all(checks: Mapping[str, Check]) -> dict[str, CheckResult]:
    results: dict[str, CheckResult] = {}
    for name, check in checks.items():
        try:
            results[name] = await check()
        except Exception as exc:  # a broken check is unknown telemetry, never "ok"
            results[name] = CheckResult("unknown", f"check failed ({type(exc).__name__})")
    return results


def build_ops_app(
    *,
    role: str,
    version: str,
    readiness: Mapping[str, Check],
    health: Mapping[str, Check] | None = None,
    registry: CollectorRegistry | None = None,
) -> Starlette:
    health_checks = {**readiness, **(health or {})}

    async def livez(_: Request) -> Response:
        return JSONResponse({"status": "alive", "role": role})

    async def readyz(_: Request) -> Response:
        results = await _run_all(readiness)
        ready = all(r.status == "ok" for r in results.values())
        body = {"ready": ready, "checks": {k: {"status": v.status, "detail": v.detail} for k, v in results.items()}}
        return JSONResponse(body, status_code=200 if ready else 503)

    async def healthz(_: Request) -> Response:
        results = await _run_all(health_checks)
        return JSONResponse(
            {
                "status": overall(results),
                "role": role,
                "version": version,
                "checks": {k: {"status": v.status, "detail": v.detail} for k, v in results.items()},
            }
        )

    async def metrics(_: Request) -> Response:
        return Response(generate_latest(registry) if registry else generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return Starlette(
        routes=[
            Route("/livez", livez),
            Route("/readyz", readyz),
            Route("/healthz", healthz),
            Route("/metrics", metrics),
        ]
    )
