"""
Lightweight observability: request correlation IDs and optional OpenTelemetry traces.

- X-Request-ID is always set (echo client value or generate UUID).
- OpenTelemetry is optional: set OTEL_EXPORTER_OTLP_ENDPOINT or OTEL_TRACES_CONSOLE=1
  to enable export; otherwise a no-op tracer is used.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextvars import ContextVar
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

logger = logging.getLogger(__name__)
_tracer = None
_initialized = False


class _NoOpTracer:
    class _NoOpSpan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def set_attribute(self, *_a, **_k):
            return None

    def start_as_current_span(self, name: str, **_kwargs):
        return self._NoOpSpan()


def get_request_id() -> Optional[str]:
    return _request_id_ctx.get()


def get_tracer():
    """Return OpenTelemetry tracer (no-op if SDK not configured)."""
    global _tracer, _initialized
    if not _initialized:
        _setup_otel()
        _initialized = True
    return _tracer


def _setup_otel() -> None:
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        provider = TracerProvider(
            resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "tradetalk-api")})
        )
        trace.set_tracer_provider(provider)

        if os.environ.get("OTEL_TRACES_CONSOLE", "").strip() in ("1", "true", "yes"):
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
                )
            except ImportError:
                logger.warning(
                    "[telemetry] OTEL_EXPORTER_OTLP_ENDPOINT set but otlp exporter not installed; "
                    "pip install opentelemetry-exporter-otlp-proto-http"
                )

        _tracer = trace.get_tracer("tradetalk", "0.1.0")
    except ImportError:
        logger.info("[telemetry] opentelemetry not installed; tracing disabled")
        _tracer = _NoOpTracer()
    except Exception as e:
        logger.warning("[telemetry] OTEL init failed: %s; using no-op tracer", e)
        _tracer = _NoOpTracer()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Propagate or generate X-Request-ID; store on request.state and context var."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        token = _request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


def log_with_request_id(extra: str = "") -> None:
    """Emit a single log line including request id when in context."""
    rid = get_request_id()
    prefix = f"[req={rid}] " if rid else ""
    logger.debug("%s%s", prefix, extra)
