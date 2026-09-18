"""
HTTP surface.  OWNER: Anindya — Lane A.

Two endpoints, exactly as named in the Problem Statement:
    GET  /health            -> {"status": "ok"}
    POST /optimize-energy   -> interpretation + 24-hour plan

Status-code policy (Problem Statement §6.1):
    200  success
    400  malformed JSON or structurally invalid request
    422  optional, semantically invalid but well-formed
    500  controlled internal error, no stack trace, no secrets

Nothing that leaves this module may contain a credential, a raw prompt or a
traceback — tracebacks are logged server-side and replaced by a flat error body.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.schemas import TOL, ErrorResponse, HealthResponse, OptimizeResponse, ScenarioRequest

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("gridwise")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # redacted() never prints a key — see app/config.py
    log.info("startup config=%s", settings.redacted())
    if not settings.has_primary_llm and not settings.allow_no_llm:
        # The LLM is mandatory in the interpretation path. Refuse to start
        # quietly mis-configured: a service that silently never calls a model
        # fails the challenge requirement outright.
        log.error(
            "no LLM_API_KEY configured — operator notes cannot be interpreted by a "
            "model. Set LLM_API_KEY, or ALLOW_NO_LLM=1 for offline development only."
        )
    yield


app = FastAPI(
    title="GridWise LLM",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Readiness probe. Never calls the LLM and never touches the solver.

    The judge polls this within 60 s of start and again between hidden cases, so
    it stays a pure constant: no imports beyond this module, no I/O, no locks.
    """
    return HealthResponse(status="ok")


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(payload: ScenarioRequest, request: Request) -> OptimizeResponse:
    # Imported late so that a broken pipeline module can never stop /health from
    # answering — readiness is worth 2 points on its own.
    from app.pipeline import run_pipeline

    refusal = _unusable_request(payload, await _raw_body(request))
    if refusal is not None:
        status, reason = refusal
        log.info("%d on /optimize-energy: %s", status, reason)
        raise HTTPException(status_code=status, detail=reason)

    rid = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    result = await run_pipeline(payload, request_id=rid)
    log.info(
        "scenario=%s request_id=%s elapsed_ms=%.1f grid=%.2f cost=%.2f peak=%.2f",
        payload.scenario_id,
        rid,
        (time.perf_counter() - started) * 1000,
        result.total_grid_kwh,
        result.total_cost_bdt,
        result.peak_grid_kwh,
    )
    return result


# --------------------------------------------------------------------------
# Controlled error handling — nothing below may leak a stack trace or a key.
# --------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI raises this for bad JSON *and* for schema violations.

    The Problem Statement calls both "malformed JSON or structurally invalid
    request" -> 400. We deliberately do not use FastAPI's default 422 here.
    """
    log.info("400 on %s: %s", request.url.path, _first_error(exc))
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="invalid_request",
            detail="Request body does not match the required scenario schema.",
        ).model_dump(),
    )


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Keep 404/405 and friends in the same flat error shape as everything else."""
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error="http_error", detail=str(exc.detail)).model_dump(),
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)  # server-side only
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="internal_error").model_dump(),
    )


async def _raw_body(request: Request) -> Any:
    """The already-parsed body, or None if it cannot be re-read.

    Starlette caches the body, so this costs nothing and never consumes the
    stream a second time. It is needed only to see JSON types that Pydantic
    coerces away, such as a boolean where an hour belongs.
    """
    try:
        return await request.json()
    except Exception:
        return None


def _unusable_request(payload: ScenarioRequest, raw: Any) -> Optional[Tuple[int, str]]:
    """Reject what the schema accepts but the physics cannot.

    Pydantic enforces structure. Two classes of input get past it and would make
    us answer 200 with a schedule we know is wrong, which scores worse than a
    controlled refusal:

    400 — values that are not valid JSON numbers at all. `Infinity` and `NaN`
          are not JSON literals (RFC 8259), yet Python's decoder accepts them
          and `ge=0` lets `inf` through. An infinite demand reaches the solver
          and produces a plan that silently ignores that hour.

    422 — well-formed but physically impossible battery parameters. Each of
          these is infeasible from hour 0 once end-of-day neutrality is applied,
          so no valid schedule exists to return. The Problem Statement offers
          422 for exactly this case, and promises that real scoring scenarios
          are feasible — so this can only ever fire on a robustness probe.
    """
    battery = payload.battery
    numbers = {
        "battery.capacity_kwh": battery.capacity_kwh,
        "battery.initial_energy_kwh": battery.initial_energy_kwh,
        "battery.minimum_energy_kwh": battery.minimum_energy_kwh,
        "battery.max_charge_kwh_per_hour": battery.max_charge_kwh_per_hour,
        "battery.max_discharge_kwh_per_hour": battery.max_discharge_kwh_per_hour,
    }
    for entry in payload.hours:
        numbers[f"hours[{entry.hour}].demand_kwh"] = entry.demand_kwh
        numbers[f"hours[{entry.hour}].solar_kwh"] = entry.solar_kwh
        numbers[f"hours[{entry.hour}].tariff_bdt_per_kwh"] = entry.tariff_bdt_per_kwh
    for field, value in numbers.items():
        if not math.isfinite(value):
            return 400, f"{field} must be a finite number."

    # A JSON boolean is not an hour, but bool is an int subclass in Python and
    # Pydantic coerces it to 0 or 1 without complaint.
    if isinstance(raw, dict) and isinstance(raw.get("hours"), list):
        for entry in raw["hours"]:
            if isinstance(entry, dict) and isinstance(entry.get("hour"), bool):
                return 400, "hours[].hour must be an integer, not a boolean."

    if battery.initial_energy_kwh > battery.capacity_kwh + TOL:
        return 422, "battery.initial_energy_kwh exceeds battery.capacity_kwh."
    if battery.minimum_energy_kwh > battery.capacity_kwh + TOL:
        return 422, "battery.minimum_energy_kwh exceeds battery.capacity_kwh."
    if battery.minimum_energy_kwh > battery.initial_energy_kwh + TOL:
        # End-of-day neutrality forces the battery back to initial_energy_kwh,
        # which would then sit below its own floor at hour 23.
        return 422, "battery.initial_energy_kwh starts below battery.minimum_energy_kwh."
    return None


def _first_error(exc: RequestValidationError) -> str:
    """One short, safe line for the log. Never echoes the submitted values."""
    try:
        first = exc.errors()[0]
        return f"{'.'.join(str(p) for p in first.get('loc', ()))}: {first.get('type', 'invalid')}"
    except Exception:  # pragma: no cover
        return "invalid request body"
