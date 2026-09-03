# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small crash-safe progress journal shared by isolated VibeScript workers."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from VibeCADVibeScriptFileIO import (
    TELEMETRY_IO_TIMEOUT_SECONDS,
    atomic_write_text,
)


_SCHEMA = "vibecad-vibescript-worker-progress-v1"
_MAX_TIMINGS = 512
_path: Path | None = None
_started = 0.0
_phase_started = 0.0
_stack: list[dict[str, Any]] = []
_state: dict[str, Any] = {}


def _write() -> None:
    if _path is None:
        return
    payload = snapshot()
    atomic_write_text(
        _path,
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        replace_timeout_seconds=TELEMETRY_IO_TIMEOUT_SECONDS,
        best_effort=True,
    )


def snapshot() -> dict[str, Any]:
    """Return current progress without depending on the status file."""

    payload = dict(_state)
    payload["elapsed_seconds"] = round(time.monotonic() - _started, 6)
    payload["phase_elapsed_seconds"] = round(
        time.monotonic() - _phase_started,
        6,
    )
    return payload


def configure(path: str | Path, domain: str) -> None:
    global _path, _started, _phase_started, _stack, _state
    _path = Path(path)
    _started = time.monotonic()
    _phase_started = _started
    _stack = []
    _state = {
        "schema": _SCHEMA,
        "domain": str(domain),
        "phase": "starting",
        "current_output": "",
        "current_graph_node": None,
        "last_completed_graph_node": None,
        "graph_timings": [],
        "graph_timings_omitted": 0,
        "phase_timings": [],
        "completed": False,
    }
    _write()


def set_phase(phase: str, *, output: str | None = None) -> None:
    global _phase_started
    now = time.monotonic()
    previous = str(_state.get("phase") or "")
    if previous and previous != phase:
        _state.setdefault("phase_timings", []).append(
            {
                "phase": previous,
                "elapsed_seconds": round(now - _phase_started, 6),
            }
        )
        _phase_started = now
    _state["phase"] = str(phase)
    if output is not None:
        _state["current_output"] = str(output)
    _write()


def set_output(output: str) -> None:
    _state["current_output"] = str(output)
    _write()


def set_item_progress(
    item_kind: str,
    *,
    completed: int,
    total: int,
    current: str = "",
) -> None:
    """Publish bounded counters for the current native worker subphase."""

    clean_completed = max(0, int(completed))
    clean_total = max(0, int(total))
    if clean_completed > clean_total:
        clean_completed = clean_total
    _state["item_progress"] = {
        "kind": str(item_kind),
        "completed": clean_completed,
        "total": clean_total,
        **({"current": str(current)} if str(current) else {}),
    }
    _write()


def graph_started(operation: str, graph_id: str) -> None:
    entry = {
        "operation": str(operation),
        "graph_id": str(graph_id),
        "output": str(_state.get("current_output") or ""),
        "started": time.monotonic(),
    }
    _stack.append(entry)
    _state["current_graph_node"] = {
        key: entry[key] for key in ("operation", "graph_id", "output")
    }
    _write()


def graph_completed(operation: str, graph_id: str) -> None:
    now = time.monotonic()
    entry = next(
        (
            item
            for item in reversed(_stack)
            if item["operation"] == str(operation) and item["graph_id"] == str(graph_id)
        ),
        None,
    )
    if entry is None:
        return
    _stack.remove(entry)
    completed = {
        "operation": entry["operation"],
        "graph_id": entry["graph_id"],
        "output": entry["output"],
        "elapsed_seconds": round(now - float(entry["started"]), 6),
    }
    timings = _state.setdefault("graph_timings", [])
    if len(timings) < _MAX_TIMINGS:
        timings.append(completed)
    else:
        _state["graph_timings_omitted"] = int(
            _state.get("graph_timings_omitted") or 0
        ) + 1
    _state["last_completed_graph_node"] = completed
    current = _stack[-1] if _stack else None
    _state["current_graph_node"] = (
        {
            key: current[key] for key in ("operation", "graph_id", "output")
        }
        if current is not None
        else None
    )
    _write()


def failed(exc: BaseException) -> None:
    _state["completed"] = False
    _state["failure"] = {
        "exception_type": type(exc).__name__,
        "error": str(exc)[:2000],
    }
    set_phase("failed")


def finish() -> None:
    _state["current_graph_node"] = None
    _state["completed"] = True
    set_phase("completed", output="")
