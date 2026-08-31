# SPDX-License-Identifier: LGPL-2.1-or-later

"""Crash-safe snapshots for the VibeCAD composer and active provider run."""

from __future__ import annotations

import json
from pathlib import Path
import re
import threading
from typing import Any

from VibeCADProject import now_iso


RECOVERY_FILE_NAME = "session-recovery.json"
RECOVERY_SCHEMA = "vibecad-session-recovery-v1"
RECOVERY_VERSION = 1
RECOVERY_PHASES = frozenset({"draft", "running"})

_RECOVERY_LOCK = threading.RLock()
_CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_CONTINUATION_RECOVERY_PROMPT = (
    "Continue the interrupted CAD work. Inspect the current document state "
    "before making any changes."
)


def _clean_conversation_id(value: Any) -> str | None:
    clean = str(value or "").strip().lower()
    if not clean:
        return None
    if _CONVERSATION_ID_PATTERN.fullmatch(clean) is None:
        raise RuntimeError("VibeCAD recovery contains an invalid conversation id.")
    return clean


def _validated_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("VibeCAD recovery is not a JSON object.")
    if value.get("schema") != RECOVERY_SCHEMA:
        raise RuntimeError("VibeCAD recovery has an unsupported schema.")
    if value.get("version") != RECOVERY_VERSION:
        raise RuntimeError("VibeCAD recovery has an unsupported version.")
    phase = str(value.get("phase") or "").strip().lower()
    if phase not in RECOVERY_PHASES:
        raise RuntimeError("VibeCAD recovery has an invalid phase.")
    document_uid = str(value.get("document_uid") or "").strip()
    if not document_uid:
        raise RuntimeError("VibeCAD recovery has no document identity.")
    prompt = str(value.get("prompt") or "")
    if phase == "draft" and not prompt.strip():
        raise RuntimeError("VibeCAD draft recovery is empty.")
    return {
        "schema": RECOVERY_SCHEMA,
        "version": RECOVERY_VERSION,
        "phase": phase,
        "prompt": prompt,
        "document_uid": document_uid,
        "document_name": str(value.get("document_name") or "").strip(),
        "file_path": str(value.get("file_path") or "").strip(),
        "conversation_id": _clean_conversation_id(value.get("conversation_id")),
        "instance_id": str(value.get("instance_id") or "").strip(),
        "created_at": str(value.get("created_at") or "").strip(),
        "updated_at": str(value.get("updated_at") or "").strip(),
    }


def recovery_composer_text(snapshot: dict[str, Any]) -> str:
    """Return reviewable composer text; never request automatic replay."""

    if not isinstance(snapshot, dict) or snapshot.get("recoverable") is False:
        return ""
    prompt = str(snapshot.get("prompt") or "")
    if prompt.strip():
        return prompt
    if snapshot.get("phase") == "running":
        return _CONTINUATION_RECOVERY_PROMPT
    return ""


class SessionRecoveryStore:
    """One atomic recovery snapshot scoped to a VibeCAD project root."""

    def __init__(self, project_root: str | Path) -> None:
        root = Path(str(project_root)).expanduser()
        if not str(project_root).strip():
            raise RuntimeError("VibeCAD recovery requires a project root.")
        self.project_root = root
        self.path = root / RECOVERY_FILE_NAME

    def write(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        phase = str(snapshot.get("phase") or "").strip().lower()
        prompt = str(snapshot.get("prompt") or "")
        if phase == "draft" and not prompt.strip():
            return self.discard()
        timestamp = now_iso()
        created_at = timestamp
        with _RECOVERY_LOCK:
            if self.path.is_file():
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(current, dict):
                        created_at = str(current.get("created_at") or timestamp)
                except (OSError, ValueError):
                    pass
            payload = _validated_snapshot(
                {
                    **dict(snapshot),
                    "schema": RECOVERY_SCHEMA,
                    "version": RECOVERY_VERSION,
                    "created_at": created_at,
                    "updated_at": timestamp,
                }
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        return {"written": True, "discarded": False, "path": str(self.path)}

    def load(
        self,
        *,
        document_uid: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        with _RECOVERY_LOCK:
            if not self.path.is_file():
                return {
                    "available": False,
                    "reason": "missing",
                    "path": str(self.path),
                }
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                snapshot = _validated_snapshot(raw)
            except (OSError, ValueError, RuntimeError) as exc:
                return {
                    "available": True,
                    "recoverable": False,
                    "error": str(exc),
                    "path": str(self.path),
                }

        if snapshot["document_uid"] != str(document_uid or "").strip():
            return {
                "available": False,
                "reason": "document_changed",
                "path": str(self.path),
            }
        current_conversation = _clean_conversation_id(conversation_id)
        stored_conversation = snapshot.get("conversation_id")
        if (
            current_conversation
            and stored_conversation
            and current_conversation != stored_conversation
        ):
            return {
                "available": False,
                "reason": "conversation_changed",
                "path": str(self.path),
            }
        return {
            **snapshot,
            "available": True,
            "recoverable": True,
            "path": str(self.path),
        }

    def discard(self) -> dict[str, Any]:
        with _RECOVERY_LOCK:
            existed = self.path.exists()
            try:
                self.path.unlink()
            except FileNotFoundError:
                existed = False
        return {
            "written": False,
            "discarded": existed,
            "path": str(self.path),
        }
