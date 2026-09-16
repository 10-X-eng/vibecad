# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bounded handoffs and on-demand reads of the existing conversation records."""

from __future__ import annotations

import hashlib
import json
from typing import Any


RECORDS_KEY = "_vibecad_conversation_records"
SCHEMAS_KEY = "_vibecad_session_tool_schemas"
START = "CONVERSATION_HANDOFF_JSON\n"
END = "\nEND_CONVERSATION_HANDOFF_JSON"
TOOL_SCHEMA = {
    "name": "conversation.read",
    "description": "Read prior requirements, responses and recorded tool outcomes across workspaces. Paginated historical data, not current model state.",
    "parameters": {
        "type": "object",
        "properties": {
            "sequence": {"type": "integer", "minimum": 1, "default": 1},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000, "default": 4000},
        },
        "additionalProperties": False,
    },
}


def _record(records, index):
    item = records[index]
    result = {"sequence": index+1, "turn_id": str(item.get("turn_id") or index+1),
              "role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
    metadata = item.get("metadata") or {}
    for key in ("tool_activity", "session_trigger", "provider_thread_id", "source"):
        if key in metadata:
            result[key] = metadata[key]
    return result


def cursor(records):
    if not records:
        return {}
    record = _record(records, len(records)-1)
    return {"sequence": len(records), "turn_id": record["turn_id"],
            "digest": hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()}


def _summary(record):
    result = {key: record[key] for key in ("sequence", "turn_id", "role", "content")}
    if len(result["content"]) > 1000:
        result["content"] = result["content"][:1000]
        result["content_truncated"] = True
    if record.get("tool_activity"):
        text = json.dumps(record["tool_activity"], ensure_ascii=True, separators=(",", ":"))
        result["tool_activity_excerpt"] = text[:1600]
        result["tool_activity_truncated"] = len(text) > 1600
    return result


def window(records, delivered=None, *, thread_id="", current_user_message=None):
    """Select unseen events, with explicit omissions and stable retrieval indices."""
    delivered = delivered or {}
    sequence = delivered.get("sequence", 0)
    if (type(sequence) is not int or not 0 <= sequence <= len(records)
            or sequence and cursor(records[:sequence]) != delivered):
        sequence = 0
    rows = [_record(records, index) for index in range(len(records))]
    rows = [row for row in rows if row.get("source") != "stop"]
    users = [row for row in rows if row["role"] == "user"]
    current = (users[-1]["sequence"] if users and current_user_message is not None
               and users[-1]["content"].strip() == current_user_message.strip() else None)
    candidates = [row for row in rows if row["sequence"] > sequence
                  and row["sequence"] != current
                  and not (thread_id and row.get("provider_thread_id") == thread_id)]
    result = {"read_tool": "conversation.read", "record_count": len(records),
              "events": [], "omitted_event_count": len(candidates),
              "first_unseen_sequence": candidates[0]["sequence"] if candidates else None,
              "original_request": _summary(users[0]) if users and users[0]["sequence"] != current else None,
              "latest_request": _summary(users[-1]) if len(users) > 1 and users[-1]["sequence"] != current else None,
              "guidance": "Continue the existing task across workspaces. These are historical records, not proof of current geometry. Read omitted/truncated requirements or outcomes before guessing or repeating work."}
    for row in reversed(candidates):
        trial = [_summary(row), *result["events"]]
        if len(json.dumps({**result, "events": trial}).encode("utf-8")) > 22000:
            break
        result["events"] = trial
    result["omitted_event_count"] = len(candidates)-len(result["events"])
    return result


def with_handoff(prompt, records, delivered=None, *, thread_id=""):
    if START in prompt and END in prompt:
        prefix, rest = prompt.split(START, 1)
        _, suffix = rest.split(END, 1)
        prompt = prefix.rstrip() + suffix
    prefix, marker, current = prompt.partition("\n\nCURRENT_USER_MESSAGE\n")
    section = json.dumps(window(records, delivered, thread_id=thread_id,
                                current_user_message=current if marker else None),
                         ensure_ascii=True, separators=(",", ":"))
    return prefix + "\n\n" + START + section + END + marker + current


def read_page(records, *, sequence=1, offset=0, max_chars=4000):
    for name, value, lower, upper in (("sequence", sequence, 1, len(records)+1),
                                     ("offset", offset, 0, None),
                                     ("max_chars", max_chars, 1, 12000)):
        if type(value) is not int or value < lower or upper is not None and value > upper:
            raise ValueError(f"Invalid conversation {name}")
    if sequence == len(records)+1:
        return {"record_count": len(records), "text": "", "next": None}
    text = json.dumps(_record(records, sequence-1), ensure_ascii=False, separators=(",", ":"))
    if offset > len(text):
        raise ValueError("Conversation offset is past the end of this record")
    end = min(len(text), offset+max_chars)
    following = ({"sequence": sequence, "offset": end} if end < len(text) else
                 {"sequence": sequence+1, "offset": 0} if sequence < len(records) else None)
    return {"record_count": len(records), "sequence": sequence, "offset": offset,
            "text": text[offset:end], "next": following}


class ConversationToolRunner:
    """Session-owned read access; CAD authorization and mutations stay in inner."""

    def __init__(self, inner, records, *, tool_trace, cancellation_check=None, progress_callback=None):
        self.inner, self.records, self.tool_trace = inner, records, tool_trace
        self.cancellation_check, self.progress_callback = cancellation_check, progress_callback

    def attach(self, context):
        context[RECORDS_KEY] = self.records
        context[SCHEMAS_KEY] = [TOOL_SCHEMA]
        return context

    def __call__(self, tool_name, arguments_json="{}", provider_call_id=""):
        if tool_name != "conversation.read":
            payload = self.inner(tool_name, arguments_json, provider_call_id)
            if isinstance(payload, dict) and isinstance(payload.get("context"), dict):
                self.attach(payload["context"])
            return payload
        from VibeCADTools import tool_failure
        arguments: dict[str, Any] = {}
        try:
            if self.cancellation_check is not None and self.cancellation_check():
                return tool_failure(tool_name, "RUN_CANCELLED", "precondition", "Run stopped before history read.")
            arguments = json.loads(arguments_json)
            if not isinstance(arguments, dict) or set(arguments)-{"sequence", "offset", "max_chars"}:
                raise ValueError("Use sequence, offset and max_chars to page conversation history")
            payload = {"ok": True, "result": read_page(self.records, **arguments)}
        except (TypeError, ValueError) as error:
            payload = tool_failure(tool_name, "INVALID_TOOL_ARGUMENTS", "schema", str(error))
        # Do not embed returned history inside its own next handoff.
        trace = {"tool_name": tool_name, "arguments": arguments, "result": {"ok": payload["ok"]}}
        self.tool_trace.append(trace)
        if self.progress_callback is not None:
            self.progress_callback({"event": "tool_call_completed", **trace})
        return payload

    def provider_update(self):
        return self.attach(self.inner.provider_update())

    def __getattr__(self, name):
        return getattr(self.inner, name)
