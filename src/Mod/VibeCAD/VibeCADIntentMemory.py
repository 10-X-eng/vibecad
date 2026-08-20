# SPDX-License-Identifier: LGPL-2.1-or-later

"""Structured, provenance-backed project intent memory."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable
import uuid


INTENT_MEMORY_NAME = "intent-memory.json"
DESIGN_DOCUMENT_NAME = "design.md"
INTENT_MEMORY_SCHEMA = "vibecad-intent-memory-v1"
INTENT_MEMORY_UPDATE_TOOL = "commit_intent_memory_update"

ENTRY_CATEGORIES = (
    "outcome",
    "requirement",
    "constraint",
    "decision",
    "component",
    "interface",
    "mechanism",
    "manufacturing",
    "verification",
    "assumption",
    "open_question",
    "rejection",
)
ENTRY_AUTHORITIES = (
    "user_explicit",
    "user_confirmed",
    "model_assumption",
)
_ENTRY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
_TURN_ID = re.compile(r"[0-9a-f]{32}")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_content(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": INTENT_MEMORY_SCHEMA,
        "version": 1,
        "project_id": str(memory.get("project_id") or ""),
        "sources": memory.get("sources") or {},
        "entries": memory.get("entries") or [],
    }


def memory_revision(memory: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_content(memory),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def empty_memory(project_id: str) -> dict[str, Any]:
    memory = {
        "schema": INTENT_MEMORY_SCHEMA,
        "version": 1,
        "project_id": str(project_id),
        "sources": {},
        "entries": [],
        "created_at": now_iso(),
        "updated_at": None,
    }
    memory["revision"] = memory_revision(memory)
    return memory


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_entry(raw: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"Intent Memory {source} entry is not an object.")
    entry_id = str(raw.get("id") or "").strip()
    if not _ENTRY_ID.fullmatch(entry_id):
        raise RuntimeError(f"Intent Memory {source} has invalid entry id {entry_id!r}.")
    category = str(raw.get("category") or "").strip()
    if category not in ENTRY_CATEGORIES:
        raise RuntimeError(
            f"Intent Memory entry {entry_id} has invalid category {category!r}."
        )
    statement = str(raw.get("statement") or "").strip()
    if not statement:
        raise RuntimeError(f"Intent Memory entry {entry_id} has no statement.")
    authority = str(raw.get("authority") or "").strip()
    if authority not in ENTRY_AUTHORITIES:
        raise RuntimeError(
            f"Intent Memory entry {entry_id} has invalid authority {authority!r}."
        )
    source_turn_ids = raw.get("source_turn_ids")
    if not isinstance(source_turn_ids, list) or not source_turn_ids:
        raise RuntimeError(f"Intent Memory entry {entry_id} has no source turns.")
    clean_turn_ids: list[str] = []
    for value in source_turn_ids:
        turn_id = str(value or "").strip().lower()
        if not _TURN_ID.fullmatch(turn_id):
            raise RuntimeError(
                f"Intent Memory entry {entry_id} has invalid turn id {turn_id!r}."
            )
        if turn_id not in clean_turn_ids:
            clean_turn_ids.append(turn_id)
    status = str(raw.get("status") or "active").strip()
    if status not in {"active", "superseded"}:
        raise RuntimeError(
            f"Intent Memory entry {entry_id} has invalid status {status!r}."
        )
    superseded_by = []
    for value in raw.get("superseded_by") or []:
        clean = str(value or "").strip()
        if not _ENTRY_ID.fullmatch(clean):
            raise RuntimeError(
                f"Intent Memory entry {entry_id} has invalid superseding id {clean!r}."
            )
        if clean not in superseded_by:
            superseded_by.append(clean)
    return {
        "id": entry_id,
        "category": category,
        "statement": statement,
        "authority": authority,
        "source_turn_ids": clean_turn_ids,
        "status": status,
        "superseded_by": superseded_by,
        "created_at": str(raw.get("created_at") or now_iso()),
        "updated_at": str(raw.get("updated_at") or now_iso()),
    }


def validate_memory(raw: Any, *, project_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("Intent Memory is not a JSON object.")
    if raw.get("schema") != INTENT_MEMORY_SCHEMA or raw.get("version") != 1:
        raise RuntimeError("Intent Memory has an unsupported schema.")
    stored_project_id = str(raw.get("project_id") or "")
    if stored_project_id != str(project_id):
        raise RuntimeError(
            "Intent Memory belongs to a different VibeCAD project: "
            f"{stored_project_id!r}."
        )
    raw_sources = raw.get("sources") or {}
    if not isinstance(raw_sources, dict):
        raise RuntimeError("Intent Memory sources must be an object.")
    sources: dict[str, int] = {}
    for conversation_id, sequence in raw_sources.items():
        clean_id = str(conversation_id or "").strip().lower()
        if not _TURN_ID.fullmatch(clean_id):
            raise RuntimeError(
                f"Intent Memory has invalid conversation id {clean_id!r}."
            )
        clean_sequence = int(sequence)
        if clean_sequence < 0:
            raise RuntimeError("Intent Memory source sequences cannot be negative.")
        sources[clean_id] = clean_sequence
    raw_entries = raw.get("entries") or []
    if not isinstance(raw_entries, list):
        raise RuntimeError("Intent Memory entries must be an array.")
    entries = [_validated_entry(item, source="stored") for item in raw_entries]
    ids = [item["id"] for item in entries]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Intent Memory contains duplicate entry ids.")
    known_ids = set(ids)
    for item in entries:
        unknown = set(item["superseded_by"]) - known_ids
        if unknown:
            raise RuntimeError(
                f"Intent Memory entry {item['id']} references unknown entries: "
                + ", ".join(sorted(unknown))
            )
    memory = {
        "schema": INTENT_MEMORY_SCHEMA,
        "version": 1,
        "project_id": stored_project_id,
        "sources": sources,
        "entries": entries,
        "created_at": str(raw.get("created_at") or now_iso()),
        "updated_at": raw.get("updated_at"),
    }
    expected_revision = memory_revision(memory)
    supplied_revision = str(raw.get("revision") or "")
    if supplied_revision and supplied_revision != expected_revision:
        raise RuntimeError("Intent Memory revision does not match its content.")
    memory["revision"] = expected_revision
    return memory


def read_memory(project_root: str | Path, project_id: str) -> dict[str, Any]:
    root = Path(project_root)
    path = root / INTENT_MEMORY_NAME
    if not path.exists():
        memory = empty_memory(project_id)
        return {**memory, "exists": False, "path": str(path)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Intent Memory could not be read from {path}: {exc}") from exc
    memory = validate_memory(raw, project_id=project_id)
    return {**memory, "exists": True, "path": str(path)}


def active_memory_context(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": INTENT_MEMORY_SCHEMA,
        "revision": str(memory.get("revision") or ""),
        "updated_at": memory.get("updated_at"),
        "entries": [
            {
                key: item[key]
                for key in (
                    "id",
                    "category",
                    "statement",
                    "authority",
                    "source_turn_ids",
                )
            }
            for item in memory.get("entries") or []
            if item.get("status") == "active"
        ],
    }


def uncovered_turns(
    memory: dict[str, Any],
    conversations: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources = memory.get("sources") or {}
    result: list[dict[str, Any]] = []
    for history in conversations:
        conversation_id = str(history.get("conversation_id") or "").strip().lower()
        covered = int(sources.get(conversation_id) or 0)
        for turn in history.get("conversation") or []:
            if int(turn.get("sequence") or 0) <= covered:
                continue
            result.append(
                {
                    "conversation_id": conversation_id,
                    "conversation_title": str(history.get("title") or ""),
                    "turn_id": str(turn.get("turn_id") or ""),
                    "sequence": int(turn.get("sequence") or 0),
                    "timestamp": str(turn.get("timestamp") or ""),
                    "role": str(turn.get("role") or ""),
                    "content": str(turn.get("content") or ""),
                }
            )
    return result


def _update_entry(
    raw: Any,
    *,
    known_turn_ids: set[str],
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    item = _validated_entry(raw, source="update")
    unknown_turns = set(item["source_turn_ids"]) - known_turn_ids
    if unknown_turns:
        raise RuntimeError(
            f"Intent Memory entry {item['id']} cites unknown turns: "
            + ", ".join(sorted(unknown_turns))
        )
    item["status"] = "active"
    item["superseded_by"] = []
    if existing is not None:
        existing_authority = str(existing.get("authority") or "")
        if existing_authority == "user_explicit" and item.get("authority") not in {
            "user_explicit",
            "user_confirmed",
        }:
            raise RuntimeError(
                f"Intent Memory cannot downgrade user_explicit entry {item['id']}."
            )
        item["created_at"] = existing["created_at"]
        item["source_turn_ids"] = list(
            dict.fromkeys(
                [
                    *list(existing.get("source_turn_ids") or []),
                    *item["source_turn_ids"],
                ]
            )
        )
    item["updated_at"] = now_iso()
    return item


def apply_memory_update(
    memory: dict[str, Any],
    update: Any,
    *,
    expected_turns: list[dict[str, Any]],
    known_turn_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(update, dict):
        raise RuntimeError("Intent Memory compiler tool arguments are not an object.")
    base_revision = str(update.get("base_revision") or "")
    if base_revision != str(memory.get("revision") or ""):
        raise RuntimeError("Intent Memory compiler used a stale base revision.")

    expected_ids = [str(item["turn_id"]) for item in expected_turns]
    dispositions = update.get("turn_dispositions")
    if not isinstance(dispositions, list):
        raise RuntimeError("Intent Memory update has no turn dispositions.")
    disposition_ids: list[str] = []
    referenced_entry_ids: dict[str, list[str]] = {}
    for raw in dispositions:
        if not isinstance(raw, dict):
            raise RuntimeError("Intent Memory turn disposition is not an object.")
        turn_id = str(raw.get("turn_id") or "").strip().lower()
        if turn_id in disposition_ids:
            raise RuntimeError(f"Intent Memory repeats turn disposition {turn_id}.")
        disposition_ids.append(turn_id)
        durable = raw.get("durable")
        if not isinstance(durable, bool):
            raise RuntimeError(
                f"Intent Memory disposition {turn_id} must declare durable as boolean."
            )
        raw_entry_ids = raw.get("entry_ids") or []
        if not isinstance(raw_entry_ids, list):
            raise RuntimeError(
                f"Intent Memory disposition {turn_id} entry_ids must be an array."
            )
        clean_entry_ids = [str(value or "").strip() for value in raw_entry_ids]
        if durable and not clean_entry_ids:
            raise RuntimeError(
                f"Durable Intent Memory turn {turn_id} does not reference an entry."
            )
        if not durable and clean_entry_ids:
            raise RuntimeError(
                f"Non-durable Intent Memory turn {turn_id} references entries."
            )
        referenced_entry_ids[turn_id] = clean_entry_ids
    if disposition_ids != expected_ids:
        raise RuntimeError(
            "Intent Memory must classify every uncovered turn once and in input order."
        )

    current_entries = {
        item["id"]: dict(item) for item in memory.get("entries") or []
    }
    upserts = update.get("upserts") or []
    if not isinstance(upserts, list):
        raise RuntimeError("Intent Memory upserts must be an array.")
    seen_upserts: set[str] = set()
    for raw in upserts:
        candidate_id = str(raw.get("id") or "").strip() if isinstance(raw, dict) else ""
        if candidate_id in seen_upserts:
            raise RuntimeError(f"Intent Memory repeats upsert {candidate_id}.")
        seen_upserts.add(candidate_id)
        current_entries[candidate_id] = _update_entry(
            raw,
            known_turn_ids=known_turn_ids,
            existing=current_entries.get(candidate_id),
        )

    supersessions = update.get("supersessions") or []
    if not isinstance(supersessions, list):
        raise RuntimeError("Intent Memory supersessions must be an array.")
    for raw in supersessions:
        if not isinstance(raw, dict):
            raise RuntimeError("Intent Memory supersession is not an object.")
        entry_id = str(raw.get("entry_id") or "").strip()
        superseded_by = str(raw.get("superseded_by") or "").strip()
        if entry_id == superseded_by:
            raise RuntimeError(f"Intent Memory entry {entry_id} cannot supersede itself.")
        if entry_id not in current_entries or superseded_by not in current_entries:
            raise RuntimeError(
                f"Intent Memory supersession references unknown entries: "
                f"{entry_id!r}, {superseded_by!r}."
            )
        item = current_entries[entry_id]
        if str(item.get("authority") or "") == "user_explicit":
            replacement = current_entries[superseded_by]
            if str(replacement.get("authority") or "") not in {
                "user_explicit",
                "user_confirmed",
            }:
                raise RuntimeError(
                    f"Intent Memory cannot supersede user_explicit entry {entry_id} "
                    "without user authority."
                )
        item["status"] = "superseded"
        item["superseded_by"] = [superseded_by]
        item["updated_at"] = now_iso()

    known_entry_ids = set(current_entries)
    for turn_id, entry_ids in referenced_entry_ids.items():
        unknown = set(entry_ids) - known_entry_ids
        if unknown:
            raise RuntimeError(
                f"Intent Memory disposition {turn_id} references unknown entries: "
                + ", ".join(sorted(unknown))
            )
        for entry_id in entry_ids:
            if turn_id not in current_entries[entry_id]["source_turn_ids"]:
                raise RuntimeError(
                    f"Intent Memory entry {entry_id} does not cite disposition turn {turn_id}."
                )

    sources = dict(memory.get("sources") or {})
    for turn in expected_turns:
        conversation_id = str(turn["conversation_id"])
        sources[conversation_id] = max(
            int(sources.get(conversation_id) or 0), int(turn["sequence"])
        )
    updated = {
        "schema": INTENT_MEMORY_SCHEMA,
        "version": 1,
        "project_id": str(memory["project_id"]),
        "sources": sources,
        "entries": list(current_entries.values()),
        "created_at": str(memory.get("created_at") or now_iso()),
        "updated_at": now_iso(),
    }
    updated["revision"] = memory_revision(updated)
    validated = validate_memory(updated, project_id=updated["project_id"])
    require_user_explicit_preserved(memory, validated)
    return validated


def active_user_explicit_entries(memory: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(memory, dict):
        return ()
    preserved: list[tuple[str, str]] = []
    for item in memory.get("entries") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("authority") or "") != "user_explicit":
            continue
        if str(item.get("status") or "active") != "active":
            continue
        entry_id = str(item.get("id") or "").strip()
        statement = str(item.get("statement") or "").strip()
        if entry_id and statement:
            preserved.append((entry_id, statement))
    return tuple(preserved)


def require_user_explicit_preserved(before: Any, after: Any) -> None:
    """Refuse apply/rebuild that drops or silent-changes user_explicit rows."""

    after_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(after, dict):
        for item in after.get("entries") or []:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                after_by_id[str(item["id"]).strip()] = item
    for entry_id, statement in active_user_explicit_entries(before):
        item = after_by_id.get(entry_id)
        if item is None:
            raise RuntimeError(
                f"Intent Memory apply dropped user_explicit entry {entry_id}."
            )
        authority = str(item.get("authority") or "")
        if authority not in {"user_explicit", "user_confirmed"}:
            raise RuntimeError(
                f"Intent Memory apply dropped user_explicit entry {entry_id}."
            )
        if str(item.get("statement") or "").strip() != statement:
            raise RuntimeError(
                f"Intent Memory apply changed user_explicit entry {entry_id}."
            )
        status = str(item.get("status") or "active")
        if status == "active":
            continue
        successors = item.get("superseded_by") or []
        if status == "superseded" and any(
            str(after_by_id.get(str(successor), {}).get("authority") or "")
            in {"user_explicit", "user_confirmed"}
            for successor in successors
        ):
            continue
        raise RuntimeError(
            f"Intent Memory apply dropped user_explicit entry {entry_id}."
        )


_CATEGORY_HEADINGS = {
    "outcome": "Intended Outcome",
    "requirement": "Requirements",
    "constraint": "Constraints",
    "decision": "Accepted Decisions",
    "component": "Components",
    "interface": "Interfaces",
    "mechanism": "Mechanisms",
    "manufacturing": "Manufacturing",
    "verification": "Verification Obligations",
    "assumption": "Assumptions",
    "open_question": "Open Questions",
    "rejection": "Rejected Directions",
}


def render_design_markdown(memory: dict[str, Any]) -> str:
    lines = [
        "<!-- Generated by VibeCAD Intent Memory. Do not edit manually. -->",
        "# VibeCAD Design Intent",
        "",
    ]
    active = [
        item for item in memory.get("entries") or [] if item.get("status") == "active"
    ]
    for category in ENTRY_CATEGORIES:
        entries = [item for item in active if item.get("category") == category]
        if not entries:
            continue
        lines.extend((f"## {_CATEGORY_HEADINGS[category]}", ""))
        for item in entries:
            authority = str(item.get("authority") or "").replace("_", " ")
            lines.append(f"- {item['statement']}  ")
            lines.append(f"  _{item['id']} · {authority}_")
        lines.append("")
    if not active:
        lines.extend(("No durable design intent has been recorded yet.", ""))
    return "\n".join(lines).rstrip() + "\n"


def write_memory(project_root: str | Path, memory: dict[str, Any]) -> dict[str, Any]:
    root = Path(project_root)
    clean = validate_memory(memory, project_id=str(memory.get("project_id") or ""))
    memory_path = root / INTENT_MEMORY_NAME
    design_path = root / DESIGN_DOCUMENT_NAME
    memory_content = json.dumps(clean, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    design_content = render_design_markdown(clean)
    _atomic_write_text(memory_path, memory_content)
    _atomic_write_text(design_path, design_content)
    return {
        **clean,
        "exists": True,
        "path": str(memory_path),
        "design_path": str(design_path),
    }


def compiler_tool_schema() -> dict[str, Any]:
    entry = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "pattern": _ENTRY_ID.pattern},
            "category": {"type": "string", "enum": list(ENTRY_CATEGORIES)},
            "statement": {"type": "string", "minLength": 1},
            "authority": {"type": "string", "enum": list(ENTRY_AUTHORITIES)},
            "source_turn_ids": {
                "type": "array",
                "items": {"type": "string", "pattern": _TURN_ID.pattern},
                "minItems": 1,
            },
        },
        "required": ["id", "category", "statement", "authority", "source_turn_ids"],
        "additionalProperties": False,
    }
    return {
        "name": INTENT_MEMORY_UPDATE_TOOL,
        "description": (
            "Commit a lossless update to durable project intent. Classify every supplied "
            "turn, preserve prior active entries unless superseding them, and never record "
            "mutable CAD progress, object state, apologies, or tool narration."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base_revision": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                    "description": "Exact revision of the Intent Memory being updated.",
                },
                "turn_dispositions": {
                    "type": "array",
                    "description": (
                        "A lossless durable-or-ephemeral classification for every "
                        "supplied conversation turn."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "turn_id": {"type": "string", "pattern": _TURN_ID.pattern},
                            "durable": {"type": "boolean"},
                            "entry_ids": {
                                "type": "array",
                                "items": {"type": "string", "pattern": _ENTRY_ID.pattern},
                            },
                        },
                        "required": ["turn_id", "durable", "entry_ids"],
                        "additionalProperties": False,
                    },
                },
                "upserts": {
                    "type": "array",
                    "description": "Durable intent entries to add or update.",
                    "items": entry,
                },
                "supersessions": {
                    "type": "array",
                    "description": (
                        "Existing intent entries replaced by newer durable entries."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "entry_id": {"type": "string", "pattern": _ENTRY_ID.pattern},
                            "superseded_by": {
                                "type": "string",
                                "pattern": _ENTRY_ID.pattern,
                            },
                        },
                        "required": ["entry_id", "superseded_by"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "base_revision",
                "turn_dispositions",
                "upserts",
                "supersessions",
            ],
            "additionalProperties": False,
        },
    }
