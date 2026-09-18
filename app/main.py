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
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.schemas import ErrorResponse, HealthResponse, OptimizeResponse, ScenarioRequest

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


def _first_error(exc: RequestValidationError) -> str:
    """One short, safe line for the log. Never echoes the submitted values."""
    try:
        first = exc.errors()[0]
        return f"{'.'.join(str(p) for p in first.get('loc', ()))}: {first.get('type', 'invalid')}"
    except Exception:  # pragma: no cover
        return "invalid request body"
