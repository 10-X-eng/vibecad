# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded atomic persistence for host-owned Native document state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


NATIVE_STATE_FILE_NAME = "native-state.json"
MAX_NATIVE_STATE_JSON_BYTES = 2 * 1024 * 1024


class NativeStatePersistenceError(RuntimeError):
    """Persisted Native state is unreadable or exceeds its storage contract."""


def native_state_path(project_scope: Mapping[str, Any]) -> Path:
    root = str(project_scope.get("root") or "").strip()
    if not root:
        raise NativeStatePersistenceError("VibeCAD project has no state directory.")
    return Path(root).expanduser() / NATIVE_STATE_FILE_NAME


def read_native_state(path: str | Path) -> dict[str, Any] | None:
    source = Path(path)
    if not source.exists():
        return None
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise NativeStatePersistenceError(
            f"Native state metadata could not be read: {exc}"
        ) from exc
    if size > MAX_NATIVE_STATE_JSON_BYTES:
        raise NativeStatePersistenceError("Persisted Native state exceeds its bound.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise NativeStatePersistenceError(
            f"Persisted Native state could not be read: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise NativeStatePersistenceError("Persisted Native state is not an object.")
    return payload


def write_native_state(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    if not isinstance(payload, Mapping):
        raise NativeStatePersistenceError("Native state output must be an object.")
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise NativeStatePersistenceError(
            f"Native state output is not JSON: {exc}"
        ) from exc
    if len(encoded.encode("utf-8")) > MAX_NATIVE_STATE_JSON_BYTES:
        raise NativeStatePersistenceError("Native state output exceeds its bound.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(target)
    except OSError as exc:
        raise NativeStatePersistenceError(
            f"Native state output could not be written: {exc}"
        ) from exc
    return target
