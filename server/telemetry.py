"""Server-side observation of tool activity.

Two consumers:

* the eval harness, which needs server-side ground truth about what actually
  executed (a model can claim it deleted something it never called);
* OpenTelemetry, wired in a later pass. The seam is `_EMITTERS`: anything
  appended there receives every recorded event, so adding spans does not mean
  touching the tool bodies again.
"""

from __future__ import annotations

import threading
from collections import deque
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

_MAX_EVENTS = 5000


@dataclass
class ToolEvent:
    name: str
    payload: dict[str, Any]
    at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    )


_lock = threading.RLock()
_events: deque[ToolEvent] = deque(maxlen=_MAX_EVENTS)
_EMITTERS: list[Callable[[ToolEvent], None]] = []


_TRACE_KEYS = ("traceparent", "tracestate", "baggage")


def trace_meta(ctx: Any) -> dict[str, str]:
    """Pull W3C trace-context keys out of the inbound request's `_meta`.

    2026-07-28 documents `traceparent` / `tracestate` / `baggage` as `_meta`
    keys (SEP-414), so the client's span and the server's tool span join one
    trace with no out-of-band header convention. Deliberately free of any
    OpenTelemetry import: the tool modules stay usable with tracing off.
    """
    try:
        meta = ctx.request_context.meta
    except Exception:
        return {}
    if meta is None:
        return {}
    if hasattr(meta, "model_dump"):
        data = meta.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(meta, dict):
        data = meta
    else:
        return {}
    return {key: str(data[key]) for key in _TRACE_KEYS if data.get(key)}


def record_tool_call(name: str, payload: dict[str, Any]) -> None:
    event = ToolEvent(name=name, payload=payload)
    with _lock:
        _events.append(event)
        emitters = list(_EMITTERS)
    for emit in emitters:
        try:
            emit(event)
        except Exception:  # telemetry must never break a tool call
            pass


def add_emitter(fn: Callable[[ToolEvent], None]) -> None:
    with _lock:
        _EMITTERS.append(fn)


# A span factory turns (name, payload) into a context manager covering the
# tool's execution. Kept as a hook so the OTel dependency stays out of the
# tool modules and the server runs fine with tracing switched off.
_SPAN_FACTORY: list[Callable[[str, dict[str, Any]], AbstractContextManager[Any]]] = []


def set_span_factory(fn: Callable[[str, dict[str, Any]], AbstractContextManager[Any]]) -> None:
    with _lock:
        _SPAN_FACTORY.clear()
        _SPAN_FACTORY.append(fn)


@contextmanager
def tool_span(name: str, payload: dict[str, Any]) -> Iterator[None]:
    """Record a tool call and keep a span open for the duration of its body.

    Duration matters: a point-in-time span cannot show how long a tool took or
    carry sub-observations made while it ran.
    """
    record_tool_call(name, payload)
    with _lock:
        factory = _SPAN_FACTORY[0] if _SPAN_FACTORY else None
    if factory is None:
        yield
        return
    try:
        cm = factory(name, payload)
    except Exception:
        yield
        return
    with cm:
        yield


def drain() -> list[ToolEvent]:
    """Return and clear recorded events. The harness calls this per eval case."""
    with _lock:
        out = list(_events)
        _events.clear()
    return out


def snapshot() -> list[ToolEvent]:
    with _lock:
        return list(_events)
