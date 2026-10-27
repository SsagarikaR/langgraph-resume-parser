"""Distributed tracing: one span per HTTP request, exported over OTLP/HTTP.

Off by default (no OTEL_EXPORTER_OTLP_ENDPOINT configured) so a dev run or a CI test doesn't try
to reach a collector. Metrics (app.observability.metrics) stay the source of truth for alerting;
this is for following one request's timing across the app's own internals in a trace viewer
(Jaeger/Tempo/etc. behind the collector) without needing LangSmith.
"""

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_SERVICE_NAME = "resume-screener-api"


def setup_tracing(app: FastAPI, otlp_endpoint: str | None) -> None:
    """Instrument `app` for tracing if `otlp_endpoint` is set; a no-op otherwise."""
    if not otlp_endpoint:
        return
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: _SERVICE_NAME}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
    logger.info("Tracing enabled: exporting spans to %s", otlp_endpoint)
