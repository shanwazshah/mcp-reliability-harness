"""OpenTelemetry wiring for tool execution.

Trace context is taken from the MCP request envelope. 2026-07-28 documents
`traceparent` / `tracestate` / `baggage` as `_meta` keys (SEP-414), so a client
span and the server-side tool span join the same trace without any out-of-band
header convention.

On attribute naming: the `gen_ai.*` semantic conventions are NOT stable. They
were moved out of the main semantic-conventions repo into
`open-telemetry/semantic-conventions-genai` in June 2026 and every attribute
still carries "Development" status with no tagged release to pin against.
Rather than pretend otherwise, tool attributes are emitted under a local
`mcp.*` namespace declared in `ATTR_NAMESPACE`, and the few gen_ai names used
are listed in `PROVISIONAL_GENAI_ATTRS` so the blast radius of a rename is one
constant.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from server.telemetry import ToolEvent, add_emitter, set_span_factory

ATTR_NAMESPACE = "mcp"

# Provisional. Kept in one place so a convention rename is a single edit.
PROVISIONAL_GENAI_ATTRS = {
    "tool_name": "gen_ai.tool.name",
    "tool_type": "gen_ai.tool.type",
    "operation": "gen_ai.operation.name",
}

# `_meta` keys the spec documents for trace propagation.
TRACEPARENT_KEY = "traceparent"
TRACESTATE_KEY = "tracestate"
BAGGAGE_KEY = "baggage"

_propagator = TraceContextTextMapPropagator()
_configured = False


def configure(service_name: str = "doc-store", console: bool | None = None) -> None:
    """Install a tracer provider. Idempotent."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

    use_console = (
        console
        if console is not None
        else os.environ.get("MCP_OTEL_CONSOLE", "0") == "1"
    )
    if use_console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            # Exporter package is optional; console/no-op still works.
            pass

    trace.set_tracer_provider(provider)
    add_emitter(_emit_span)
    set_span_factory(_tool_span)
    _configured = True


def context_from_meta(meta: Mapping[str, Any] | None) -> Context | None:
    """Rebuild the caller's trace context from the request `_meta`."""
    if not meta:
        return None
    carrier = {
        key: str(meta[key])
        for key in (TRACEPARENT_KEY, TRACESTATE_KEY, BAGGAGE_KEY)
        if meta.get(key)
    }
    if not carrier:
        return None
    return _propagator.extract(carrier)


def _attributes(payload: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "_meta":
            continue
        if isinstance(value, (str, int, float, bool)):
            out[f"{prefix}{key}"] = value
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            out[f"{prefix}{key}"] = value
    return out


@contextmanager
def _tool_span(name: str, payload: dict[str, Any]) -> Iterator[None]:
    """Span covering one tool's execution, parented to the caller's trace."""
    tracer = trace.get_tracer(__name__)
    parent = context_from_meta(payload.get("_meta"))
    with tracer.start_as_current_span(
        f"{ATTR_NAMESPACE}.tool/{name}",
        context=parent,
        kind=trace.SpanKind.SERVER,
    ) as span:
        span.set_attribute(PROVISIONAL_GENAI_ATTRS["tool_name"], name)
        span.set_attribute(PROVISIONAL_GENAI_ATTRS["operation"], "execute_tool")
        span.set_attribute(f"{ATTR_NAMESPACE}.protocol_version", "2026-07-28")
        for attr, value in _attributes(payload, f"{ATTR_NAMESPACE}.arg.").items():
            span.set_attribute(attr, value)
        yield


def _emit_span(event: ToolEvent) -> None:
    """Attach sub-observations to the tool span that is currently open.

    Dotted names (`read_document.content`) are observations made *while* a tool
    runs, not separate operations. They are span events on the active tool
    span; emitting them as their own root spans would scatter one tool call
    across several traces.
    """
    if "." not in event.name:
        return  # the span itself is opened by `_tool_span`
    current = trace.get_current_span()
    if current is None or not current.is_recording():
        return
    current.add_event(
        event.name, attributes=_attributes(event.payload, f"{ATTR_NAMESPACE}.")
    )
