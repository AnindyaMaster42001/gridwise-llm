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

This file starts life already serving /health so Fayek can deploy the
container in the first 20 minutes and keep a live URL warm all round.
"""

from __future__ import annotations

import logging
import time
import uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
    """Readiness probe. Must never call the LLM and must answer in milliseconds."""
    return HealthResponse(status="ok")


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(payload: ScenarioRequest, request: Request) -> OptimizeResponse:
    from app.pipeline import run_pipeline  # imported late so /health never depends on it

    rid = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    result = await run_pipeline(payload, request_id=rid)
    log.info(
        "scenario=%s request_id=%s elapsed_ms=%.1f cost=%.2f",
        payload.scenario_id,
        rid,
        (time.perf_counter() - started) * 1000,
        result.total_cost_bdt,
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
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="invalid_request",
            detail="Request body does not match the required scenario schema.",
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)  # server-side only
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="internal_error").model_dump(),
    )
