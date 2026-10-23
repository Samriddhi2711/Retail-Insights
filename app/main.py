from __future__ import annotations
from fastapi.middleware.cors import CORSMiddleware

import logging
import os
import uuid
from pathlib import Path
from typing import Any

# Load .env file before anything else so DATABASE_URL etc. are visible.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass  # python-dotenv not installed — env vars must be set externally

import structlog


def configure_logging() -> None:
    """Configure structlog for structured JSON output."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


configure_logging()
logger = structlog.get_logger(__name__)

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.db import init_db
from app.health import router as health_router
from app.ingestion import router as ingestion_router
from app.metrics import router as metrics_router
from app.anomalies import router as anomalies_router
from app.funnel import router as funnel_router
from app.heatmap import router as heatmap_router


def get_trace_id() -> str:
    return str(uuid.uuid4())


import time
from contextlib import asynccontextmanager

from app.db import DATABASE_URL


@asynccontextmanager
async def lifespan_context(app: FastAPI):
    """Application lifespan: initialise DB on startup, log active backend."""
    db_backend = "sqlite" if DATABASE_URL.startswith("sqlite") else "postgresql"
    logger.info(
        "api_startup",
        db_backend=db_backend,
        database_url=DATABASE_URL[:40] + "..." if len(DATABASE_URL) > 40 else DATABASE_URL,
    )
    await init_db()
    logger.info("db_initialized", db_backend=db_backend)
    yield
    logger.info("api_shutdown")

def create_app() -> FastAPI:
    app = FastAPI(
        title="Apex Retail Store Intelligence",
        lifespan=lifespan_context
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_trace_id(request: Request, call_next):
        trace_id = get_trace_id()
        request.state.trace_id = trace_id
        t0 = time.monotonic()
        response = await call_next(request)
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        response.headers["X-Trace-Id"] = trace_id
        logger.info(
            "http_request",
            trace_id=trace_id,
            method=request.method,
            endpoint=str(request.url.path),
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "trace_id": request.state.trace_id,
                "error": "Validation failed",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logging.exception("unhandled_exception", exc_info=exc)
        return JSONResponse(
            status_code=503,
            content={
                "trace_id": request.state.trace_id,
                "error": "Service unavailable",
            },
        )

    app.include_router(ingestion_router)
    app.include_router(metrics_router)
    app.include_router(funnel_router)
    app.include_router(heatmap_router)
    app.include_router(anomalies_router)
    app.include_router(health_router)



    @app.get("/", response_model=dict)
    async def root(request: Request) -> dict[str, Any]:
        return {
            "trace_id": request.state.trace_id,
            "message": "Apex Retail Store Intelligence API",
        }

    return app


app = create_app()


class TraceRoute(APIRoute):
    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Any:
            request.state.trace_id = request.state.trace_id or get_trace_id()
            return await original_route_handler(request)

        return custom_route_handler
