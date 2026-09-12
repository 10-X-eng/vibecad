# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider-reported token usage without retaining prompt or source content.

Codex app-server reports both a latest ``last`` snapshot and a cumulative
``total`` snapshot.  This module keeps those scopes distinct and provides the
small JSON-safe record used by session persistence and the conversation UI.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import threading
from typing import Any

USAGE_SCHEMA = "vibecad-token-usage-v1"
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_COUNT_ALIASES = {
    "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
    "cached_input_tokens": (
        "cached_input_tokens",
        "cachedInputTokens",
        "cached_tokens",
        "cachedTokens",
    ),
    "output_tokens": (
        "output_tokens",
        "outputTokens",
        "completion_tokens",
        "completionTokens",
    ),
    "reasoning_output_tokens": (
        "reasoning_output_tokens",
        "reasoningOutputTokens",
        "reasoning_tokens",
        "reasoningTokens",
    ),
    "total_tokens": ("total_tokens", "totalTokens"),
}
_ALLOWED_STATUS = {"completed", "failed", "cancelled", "incomplete"}
_ALLOWED_AUTH_MODES = {"api_key", "chatgpt"}
_ALLOWED_MODEL_SOURCES = {"transport", "requested", "unknown"}
_ALLOWED_SOURCES = {
    "codex-app-server.thread/tokenUsage/updated",
    "codex-app-server.turn/completed",
}


def empty_token_counts() -> dict[str, int | None]:
    """Return a complete count record with unavailable fields as ``None``."""

    return {field: None for field in USAGE_FIELDS}


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed >= 0 else None
    return None


def _lookup(mapping: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for name in aliases:
        if name in mapping:
            return mapping[name]
    return None


def _normalize_counts(value: Any) -> dict[str, int | None]:
    if not isinstance(value, Mapping):
        return empty_token_counts()
    input_details = value.get("input_tokens_details")
    if not isinstance(input_details, Mapping):
        input_details = value.get("inputTokensDetails")
    output_details = value.get("output_tokens_details")
    if not isinstance(output_details, Mapping):
        output_details = value.get("outputTokensDetails")
    result: dict[str, int | None] = {}
    for field in USAGE_FIELDS:
        raw = _lookup(value, _COUNT_ALIASES[field])
        if raw is None and field == "cached_input_tokens":
            raw = _lookup(
                input_details if isinstance(input_details, Mapping) else {},
                ("cached_tokens", "cachedTokens"),
            )
        if raw is None and field == "reasoning_output_tokens":
            raw = _lookup(
                output_details if isinstance(output_details, Mapping) else {},
                ("reasoning_tokens", "reasoningTokens"),
            )
        result[field] = _count(raw)
    return result


def _first_mapping(mapping: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        candidate = mapping.get(name)
        if isinstance(candidate, Mapping):
            return candidate
    return None


def normalize_codex_usage(payload: Mapping[str, Any] | None) -> dict[str, dict[str, int | None]]:
    """Normalize Codex or Responses-shaped usage into ``last`` and ``total``.

    The function only reads counters supplied by the transport.  It never
    computes a total from input/output, because cached and reasoning values
    can be subsets of those counts.
    """

    root: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    nested = _first_mapping(root, ("tokenUsage", "token_usage", "usage"))
    if nested is not None:
        root = nested
    last = _first_mapping(root, ("last", "lastUsage", "last_usage"))
    total = _first_mapping(root, ("total", "totalUsage", "total_usage"))
    if last is None and any(key in root for aliases in _COUNT_ALIASES.values() for key in aliases):
        last = root
    return {
        "last": _normalize_counts(last),
        "total": _normalize_counts(total),
    }


def _has_count(values: Mapping[str, Any] | None) -> bool:
    return bool(
        isinstance(values, Mapping)
        and any(isinstance(values.get(field), int) for field in USAGE_FIELDS)
    )


def _counts_complete(values: Mapping[str, Any] | None) -> bool:
    return isinstance(values, Mapping) and all(
        _count(values.get(field)) is not None for field in USAGE_FIELDS
    )


def _merge_latest(
    current: Mapping[str, Any],
    incoming: Any,
) -> tuple[dict[str, int | None], bool]:
    """Merge a newer snapshot while preserving unknown fields."""

    merged = {
        field: _count(current.get(field)) if isinstance(current, Mapping) else None
        for field in USAGE_FIELDS
    }
    changed = False
    for field in USAGE_FIELDS:
        value = _count(incoming.get(field)) if isinstance(incoming, Mapping) else None
        if value is None:
            continue
        # App-server snapshots are monotone during one logical turn. Taking
        # the maximum also makes a retry/replay harmless if it arrives late.
        current_value = merged[field]
        if current_value is None or value > current_value:
            merged[field] = value
            changed = True
    return merged, changed


def _clean_id(value: Any) -> str:
    return str(value or "").strip()[:256]


def sanitize_usage_metadata(value: Any) -> dict[str, Any] | None:
    """Keep only the bounded usage metadata allowed in conversation history."""

    if not isinstance(value, Mapping):
        return None
    turn = _normalize_counts(value.get("turn"))
    thread_total = _normalize_counts(value.get("thread_total"))
    reported = bool(value.get("reported", value.get("actual", False)))
    if not reported and not (_has_count(turn) or _has_count(thread_total)):
        return None
    status = str(value.get("status") or "incomplete").strip().lower()
    if status not in _ALLOWED_STATUS:
        status = "incomplete"
    auth_mode = str(value.get("auth_mode") or "").strip().lower()
    if auth_mode not in _ALLOWED_AUTH_MODES:
        auth_mode = ""
    source = str(value.get("source") or "").strip()
    if source not in _ALLOWED_SOURCES:
        source = "codex-app-server.thread/tokenUsage/updated"
    model_source = str(value.get("model_source") or "unknown").strip().lower()
    if model_source not in _ALLOWED_MODEL_SOURCES:
        model_source = "unknown"
    complete = bool(value.get("complete")) and status == "completed"
    return {
        "schema": USAGE_SCHEMA,
        "actual": True,
        "reported": reported,
        "status": status,
        "complete": complete,
        "provider": _clean_id(value.get("provider")),
        "auth_mode": auth_mode,
        "model": _clean_id(value.get("model")),
        "model_source": model_source,
        "source": source,
        "thread_id": _clean_id(value.get("thread_id")),
        "turn_id": _clean_id(value.get("turn_id")),
        "turn": turn,
        "thread_total": thread_total,
    }


def usage_metadata_for_status(value: Any, *, status: str) -> dict[str, Any] | None:
    """Apply a terminal request status without expanding the stored payload."""

    clean = sanitize_usage_metadata(value)
    if clean is None:
        return None
    clean_status = str(status or "incomplete").strip().lower()
    if clean_status not in _ALLOWED_STATUS:
        clean_status = "incomplete"
    clean["status"] = clean_status
    clean["complete"] = bool(
        clean_status == "completed"
        and clean.get("reported")
        and _counts_complete(clean.get("turn"))
        and (
            not _has_count(clean.get("thread_total")) or _counts_complete(clean.get("thread_total"))
        )
    )
    return clean


class TokenUsageAccumulator:
    """Deduplicate one Codex thread's streamed usage notifications."""

    def __init__(self, *, provider: str, auth_mode: str) -> None:
        self.provider = _clean_id(provider)
        self.auth_mode = (
            str(auth_mode or "").strip().lower()
            if str(auth_mode or "").strip().lower() in _ALLOWED_AUTH_MODES
            else ""
        )
        self.model = ""
        self.model_source = "unknown"
        self.thread_id = ""
        self.active_turn_id = ""
        self._turn = empty_token_counts()
        self._thread_total = empty_token_counts()
        self._pending_turns: dict[str, dict[str, int | None]] = {}
        self._pending_turn_reports: set[str] = set()
        self._turn_reported = False
        self._source = "codex-app-server.thread/tokenUsage/updated"
        self._lock = threading.RLock()

    def set_model(self, model: Any, *, source: str = "unknown") -> None:
        with self._lock:
            clean_model = _clean_id(model)
            clean_source = str(source or "unknown").strip().lower()
            if clean_source not in _ALLOWED_MODEL_SOURCES:
                clean_source = "unknown"
            self.model = clean_model
            self.model_source = clean_source if clean_model else "unknown"

    @property
    def has_current_turn_usage(self) -> bool:
        with self._lock:
            return self._turn_reported

    @property
    def has_any_usage(self) -> bool:
        with self._lock:
            return self._turn_reported or _has_count(self._thread_total)

    def set_thread_id(self, thread_id: str) -> None:
        with self._lock:
            clean = _clean_id(thread_id)
            if not clean:
                return
            if self.thread_id and self.thread_id != clean:
                self.active_turn_id = ""
                self._turn = empty_token_counts()
                self._thread_total = empty_token_counts()
                self._pending_turns.clear()
                self._pending_turn_reports.clear()
                self._turn_reported = False
            self.thread_id = clean

    def set_active_turn(self, turn_id: str) -> None:
        with self._lock:
            clean = _clean_id(turn_id)
            if clean == self.active_turn_id:
                return
            self.active_turn_id = clean
            self._turn = self._pending_turns.pop(clean, empty_token_counts())
            self._turn_reported = clean in self._pending_turn_reports
            self._pending_turn_reports.discard(clean)

    def observe(
        self,
        payload: Mapping[str, Any] | None,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        source: str = "codex-app-server.thread/tokenUsage/updated",
    ) -> dict[str, Any] | None:
        """Observe a notification and return only when it changes this turn."""

        with self._lock:
            return self._observe_unlocked(
                payload,
                thread_id=thread_id,
                turn_id=turn_id,
                source=source,
            )

    def _observe_unlocked(
        self,
        payload: Mapping[str, Any] | None,
        *,
        thread_id: str | None,
        turn_id: str | None,
        source: str,
    ) -> dict[str, Any] | None:
        """Implementation for :meth:`observe` while its lock is held."""

        params = payload if isinstance(payload, Mapping) else {}
        event_thread_id = _clean_id(thread_id or params.get("threadId"))
        event_turn_id = _clean_id(turn_id or params.get("turnId"))
        if self.thread_id and event_thread_id and event_thread_id != self.thread_id:
            return None
        if not self.thread_id and event_thread_id:
            self.thread_id = event_thread_id
        normalized = normalize_codex_usage(params)
        self._source = source if source in _ALLOWED_SOURCES else self._source
        self._thread_total, total_changed = _merge_latest(
            self._thread_total,
            normalized["total"],
        )
        if not self.active_turn_id:
            # A thread/resume can replay the previous cumulative total before
            # this request has a turn id. It is a baseline, never a new turn.
            if event_turn_id:
                pending = self._pending_turns.get(event_turn_id, empty_token_counts())
                self._pending_turns[event_turn_id], _ = _merge_latest(
                    pending,
                    normalized["last"],
                )
                self._pending_turn_reports.add(event_turn_id)
                if len(self._pending_turns) > 8:
                    oldest = next(iter(self._pending_turns))
                    self._pending_turns.pop(oldest, None)
                    self._pending_turn_reports.discard(oldest)
            return None
        if event_turn_id and event_turn_id != self.active_turn_id:
            # Late notifications from a prior turn can update the cumulative
            # total, but cannot become usage for the current request.
            return None
        self._turn, turn_changed = _merge_latest(self._turn, normalized["last"])
        first_report = not self._turn_reported
        self._turn_reported = True
        if not (first_report or turn_changed or total_changed):
            return None
        return self.metadata(status="incomplete")

    def metadata(self, *, status: str = "incomplete") -> dict[str, Any]:
        """Return a sanitized record suitable for an event or persistence."""

        with self._lock:
            clean_status = str(status or "incomplete").strip().lower()
            if clean_status not in _ALLOWED_STATUS:
                clean_status = "incomplete"
            complete = (
                clean_status == "completed"
                and self._turn_reported
                and _counts_complete(self._turn)
                and (not _has_count(self._thread_total) or _counts_complete(self._thread_total))
            )
            return {
                "schema": USAGE_SCHEMA,
                "actual": True,
                "reported": self._turn_reported,
                "status": clean_status,
                "complete": complete,
                "provider": self.provider,
                "auth_mode": self.auth_mode,
                "model": self.model,
                "model_source": self.model_source,
                "source": self._source,
                "thread_id": self.thread_id,
                "turn_id": self.active_turn_id,
                "turn": deepcopy(self._turn),
                "thread_total": deepcopy(self._thread_total),
            }


def _usage_from_turn(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = entry.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    usage = sanitize_usage_metadata(metadata.get("usage"))
    if not usage or not usage.get("actual") or not usage.get("reported"):
        return None
    return usage


def summarize_conversation_usage(conversation: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize persisted per-turn usage without summing cumulative totals."""

    usages: list[tuple[int, Mapping[str, Any]]] = []
    for index, entry in enumerate(conversation or []):
        if not isinstance(entry, Mapping):
            continue
        usage = _usage_from_turn(entry)
        if usage is not None:
            usages.append((index, usage))
    if not usages:
        return {
            "has_usage": False,
            "turns": [],
            "totals": empty_token_counts(),
            "models": {},
            "complete": False,
        }

    groups: dict[str, list[Mapping[str, Any]]] = {}
    for index, usage in usages:
        group = str(usage.get("thread_id") or f"entry-{index}")
        groups.setdefault(group, []).append(usage)
    totals = empty_token_counts()
    total_fields_seen: set[str] = set()
    models: dict[str, dict[str, Any]] = {}
    complete = True
    for group_usages in groups.values():
        cumulative = [
            usage.get("thread_total")
            for usage in group_usages
            if _has_count(usage.get("thread_total"))
        ]
        if cumulative:
            group_total = empty_token_counts()
            for snapshot in cumulative:
                group_total, _ = _merge_latest(group_total, snapshot)
        else:
            group_total: dict[str, int | None] | None = None
            for usage in group_usages:
                if group_total is None:
                    group_total = _normalize_counts(usage.get("turn"))
                else:
                    group_total, _ = _sum_counts(group_total, usage.get("turn"))
            if group_total is None:
                group_total = empty_token_counts()
        for field in USAGE_FIELDS:
            group_value = group_total[field]
            if field not in total_fields_seen:
                totals[field] = group_value
                total_fields_seen.add(field)
            else:
                current_total = totals[field]
                if current_total is None or group_value is None:
                    totals[field] = None
                else:
                    totals[field] = current_total + group_value
        complete = complete and all(group_total[field] is not None for field in USAGE_FIELDS)
    turns = []
    for index, usage in usages:
        entry = conversation[index]
        turns.append(
            {
                "sequence": int(entry.get("sequence") or index + 1),
                "status": usage.get("status") or "incomplete",
                "complete": bool(usage.get("status") == "completed")
                and _counts_complete(usage.get("turn")),
                "counts": deepcopy(usage.get("turn") or empty_token_counts()),
            }
        )
        complete = complete and turns[-1]["complete"]
        model_name = str(usage.get("model") or "").strip() or "unknown model"
        model = models.get(model_name)
        if model is None:
            model = {
                **_normalize_counts(usage.get("turn")),
                "model_source": str(usage.get("model_source") or "unknown"),
            }
            models[model_name] = model
        else:
            incoming = _normalize_counts(usage.get("turn"))
            for field in USAGE_FIELDS:
                if model[field] is None or incoming[field] is None:
                    model[field] = None
                else:
                    model[field] += incoming[field]
            incoming_source = str(usage.get("model_source") or "unknown")
            if incoming_source != model.get("model_source"):
                model["model_source"] = "unknown"
    return {
        "has_usage": True,
        "turns": turns,
        "totals": totals,
        "models": models,
        "complete": complete,
    }


def _sum_counts(
    current: Mapping[str, Any],
    incoming: Any,
) -> tuple[dict[str, int | None], bool]:
    result = empty_token_counts()
    changed = False
    incoming_counts = _normalize_counts(incoming)
    for field in USAGE_FIELDS:
        current_value = _count(current.get(field)) if isinstance(current, Mapping) else None
        incoming_value = incoming_counts[field]
        if current_value is None or incoming_value is None:
            result[field] = None
        else:
            result[field] = current_value + incoming_value
            changed = True
    return result, changed


def _format_count(value: Any) -> str:
    clean = _count(value)
    return f"{clean:,}" if clean is not None else "unknown"


def _format_counts(counts: Mapping[str, Any] | None) -> str:
    values = counts if isinstance(counts, Mapping) else {}
    return (
        f"input {_format_count(values.get('input_tokens'))} · "
        f"cached input {_format_count(values.get('cached_input_tokens'))} · "
        f"output {_format_count(values.get('output_tokens'))} · "
        f"reasoning {_format_count(values.get('reasoning_output_tokens'))} · "
        f"total {_format_count(values.get('total_tokens'))}"
    )


def format_usage_turns(turns: list[dict[str, Any]]) -> str:
    """Format per-turn detail independently from changing conversation totals."""
    lines = []
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        status = str(turn.get("status") or "unknown")
        if not turn.get("complete"):
            status += ", incomplete"
        lines.append(
            f"Turn {turn.get('sequence', '?')} ({status}): " f"{_format_counts(turn.get('counts'))}"
        )
    return "\n".join(lines)


def format_usage_summary(summary: Mapping[str, Any]) -> str:
    """Format the compact expandable panel content in plain text."""

    if not isinstance(summary, Mapping) or not summary.get("has_usage"):
        return "No actual provider-reported token usage is available."
    marker = "" if summary.get("complete") else " · incomplete totals"
    lines = [
        f"Actual provider-reported tokens{marker}",
        f"Conversation: {_format_counts(summary.get('totals'))}",
    ]
    models = summary.get("models")
    if isinstance(models, Mapping) and models:
        lines.append("By model:")
        for model_name, counts in models.items():
            model_source = (
                str(counts.get("model_source") or "unknown")
                if isinstance(counts, Mapping)
                else "unknown"
            )
            lines.append(f"{model_name} ({model_source}): {_format_counts(counts)}")
    lines.extend(format_usage_turns(summary.get("turns") or []).splitlines())
    lines.append(
        "Cached input and reasoning are subsets; they are not added again. "
        "Serialized bytes/4 estimate is not included."
    )
    return "\n".join(lines)


__all__ = [
    "USAGE_SCHEMA",
    "USAGE_FIELDS",
    "TokenUsageAccumulator",
    "empty_token_counts",
    "format_usage_summary",
    "normalize_codex_usage",
    "sanitize_usage_metadata",
    "summarize_conversation_usage",
    "usage_metadata_for_status",
]


def usage_graph_data(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return conversation/model rows for the usage graph without inference."""

    if not isinstance(summary, Mapping) or not summary.get("has_usage"):
        return {"has_usage": False, "complete": False, "rows": []}

    rows: list[dict[str, Any]] = []
    totals = summary.get("totals")
    if isinstance(totals, Mapping):
        rows.append(
            {
                "label": "Conversation",
                "scope": "conversation",
                "counts": _normalize_counts(totals),
            }
        )
    models = summary.get("models")
    if isinstance(models, Mapping):
        for model_name, model_counts in models.items():
            if not isinstance(model_counts, Mapping):
                continue
            clean_name = str(model_name or "").strip() or "unknown model"
            source = str(model_counts.get("model_source") or "unknown").strip() or "unknown"
            rows.append(
                {
                    "label": f"{clean_name} ({source})",
                    "scope": "model",
                    "model": clean_name,
                    "model_source": source,
                    "counts": _normalize_counts(model_counts),
                }
            )
    return {
        "has_usage": bool(rows),
        "complete": bool(summary.get("complete")),
        "rows": rows,
    }


__all__.append("usage_graph_data")


def render_usage_snapshot(history: list[dict[str, Any]], active: Any) -> dict[str, Any]:
    """Calculate presentation from an owned snapshot without accessing Qt or CAD."""
    entries = list(history)
    clean_active = sanitize_usage_metadata(active)
    if clean_active is not None:
        entries.append({
            "role": "assistant", "sequence": len(entries) + 1,
            "metadata": {"usage": clean_active},
        })
    summary = summarize_conversation_usage(entries)
    return usage_summary_presentation(summary, active=clean_active)


def usage_summary_presentation(summary: dict[str, Any], *, active: Any = None) -> dict[str, Any]:
    """Separate stable turn history from the small, live summary for Qt layout."""
    active_reported = bool(active and active.get("actual") and active.get("reported"))
    turns = summary.get("turns") or []
    return {
        "text": format_usage_summary(summary),
        "summary_text": format_usage_summary({**summary, "turns": turns[-1:] if active_reported else []}),
        "turn_text": format_usage_turns(turns[:-1] if active_reported else turns),
        "graph": usage_graph_data(summary),
    }


class UsageSummaryWorker:
    """One calculation in flight and one replaceable pending snapshot per panel.

    The caller owns the input snapshots and must not mutate them after submission.
    Publishing runs on this worker; GUI callers must use a queued signal and check
    is_current again on delivery. Closing never waits for a calculation.
    """

    def __init__(self, publish: Any, *, compute: Any = None) -> None:
        self._publish = publish
        self._compute = compute or render_usage_snapshot
        self._condition = threading.Condition()
        self._generation = 0
        self._pending = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="VibeCAD usage summary", daemon=True)
        self._thread.start()

    def submit(self, history: list[dict[str, Any]], active: Any) -> int:
        with self._condition:
            self._generation += 1
            if not self._closed:
                self._pending = (self._generation, history, active)
                self._condition.notify()
            return self._generation

    def invalidate(self) -> None:
        with self._condition:
            self._generation += 1
            self._pending = None

    def is_current(self, generation: int) -> bool:
        with self._condition:
            return not self._closed and generation == self._generation

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                generation, history, active = self._pending
                self._pending = None
            try:
                result = self._compute(history, active)
            except Exception as exc:
                result = {"error": str(exc)}
            if self.is_current(generation):
                try:
                    self._publish(generation, result)
                except RuntimeError:
                    # The Qt signal's owner may have been deleted during delivery.
                    self.close()


__all__.extend(["render_usage_snapshot", "UsageSummaryWorker", "format_usage_turns", "usage_summary_presentation"])
