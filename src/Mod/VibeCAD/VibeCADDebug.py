# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact provider-request capture for VibeCAD context debugging."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid
from typing import Any


CAPTURE_SCHEMA = "vibecad-provider-request-v1"
NATIVE_DIAGNOSTIC_SCHEMA = "vibecad-native-diagnostic-v1"
CAPTURE_DIRECTORY_ENV = "VIBECAD_CONTEXT_DEBUG_DIR"
LATEST_CAPTURE_NAME = "latest.json"
LATEST_NATIVE_DIAGNOSTIC_NAME = "latest-native.json"
MAX_NATIVE_DIAGNOSTIC_JSON_BYTES = 64 * 1024


def vibecad_home() -> Path:
    configured = str(os.environ.get("VIBECAD_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".vibecad"


def default_capture_directory() -> Path:
    configured = str(os.environ.get(CAPTURE_DIRECTORY_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return vibecad_home() / "debug" / "provider-requests"


def resolve_capture_directory(configured: str | Path | None = None) -> Path:
    clean = str(configured or "").strip()
    return Path(clean).expanduser() if clean else default_capture_directory()


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


def capture_provider_request(
    *,
    directory: str | Path,
    provider: str,
    sdk_call: str,
    turn: int,
    request: dict[str, Any],
    base_url: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Persist the literal keyword arguments used for one provider SDK call."""
    clean_provider = str(provider or "").strip().lower()
    if not clean_provider:
        raise ValueError("Provider request capture requires a provider name.")
    if not isinstance(request, dict):
        raise TypeError("Provider request capture requires a request dictionary.")

    captured_at = datetime.now(timezone.utc)
    envelope = {
        "schema": CAPTURE_SCHEMA,
        "captured_at": captured_at.isoformat(timespec="milliseconds"),
        "provider": clean_provider,
        "sdk_call": str(sdk_call or "").strip(),
        "turn": int(turn),
        "attempt": int(attempt),
        "base_url": str(base_url or "") or None,
        "request": request,
    }
    content = json.dumps(envelope, ensure_ascii=True, indent=2) + "\n"
    capture_dir = resolve_capture_directory(directory)
    timestamp = captured_at.strftime("%Y%m%dT%H%M%S.%fZ")
    filename = (
        f"{timestamp}-{clean_provider}-turn-{int(turn):03d}-"
        f"attempt-{int(attempt):02d}-pid-{os.getpid()}.json"
    )
    timestamped_path = capture_dir / filename
    latest_path = capture_dir / LATEST_CAPTURE_NAME
    provider_latest_path = capture_dir / f"latest-{clean_provider}.json"
    _atomic_write_text(timestamped_path, content)
    _atomic_write_text(provider_latest_path, content)
    _atomic_write_text(latest_path, content)
    return {
        "path": str(timestamped_path),
        "latest_path": str(latest_path),
        "size_bytes": len(content.encode("utf-8")),
    }


def capture_native_diagnostic(
    *,
    directory: str | Path,
    provider_call_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Persist one opt-in Native failure diagnostic outside provider results."""

    if not isinstance(event, dict):
        raise TypeError("Native diagnostic capture requires an event dictionary.")
    captured_at = datetime.now(timezone.utc)
    envelope = {
        "schema": NATIVE_DIAGNOSTIC_SCHEMA,
        "captured_at": captured_at.isoformat(timespec="milliseconds"),
        "provider_call_id": str(provider_call_id or "")[:256],
        "diagnostic": event,
    }
    content = json.dumps(envelope, ensure_ascii=True, indent=2) + "\n"
    size_bytes = len(content.encode("utf-8"))
    if size_bytes > MAX_NATIVE_DIAGNOSTIC_JSON_BYTES:
        raise ValueError("Native diagnostic capture exceeds its 64-KiB bound.")

    capture_dir = resolve_capture_directory(directory)
    timestamp = captured_at.strftime("%Y%m%dT%H%M%S.%fZ")
    filename = (
        f"{timestamp}-native-call-pid-{os.getpid()}-"
        f"{uuid.uuid4().hex[:12]}.json"
    )
    timestamped_path = capture_dir / filename
    latest_path = capture_dir / LATEST_NATIVE_DIAGNOSTIC_NAME
    _atomic_write_text(timestamped_path, content)
    _atomic_write_text(latest_path, content)
    return {
        "path": str(timestamped_path),
        "latest_path": str(latest_path),
        "size_bytes": size_bytes,
    }


def list_provider_request_captures(
    directory: str | Path | None = None,
) -> list[Path]:
    capture_dir = resolve_capture_directory(directory)
    if not capture_dir.is_dir():
        return []
    paths = [
        path
        for path in capture_dir.glob("*.json")
        if path.is_file() and not path.name.startswith("latest")
    ]
    return sorted(paths, key=lambda path: path.name, reverse=True)


def latest_provider_request_capture(
    directory: str | Path | None = None,
) -> Path | None:
    latest = resolve_capture_directory(directory) / LATEST_CAPTURE_NAME
    return latest if latest.is_file() else None
