# SPDX-License-Identifier: LGPL-2.1-or-later

"""Private exact-document input for detached TechDraw page redraw."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import (
    FrozenFile,
    freeze_regular_file,
    resolve_freecadcmd,
)
from VibeCADNativeDrawingRedraw import (
    PreparedPageRedraw,
    restore_page_redraw_commit_state,
    validate_prepared_page_redraw,
)


DRAWING_REDRAW_PROTOCOL = "vibecad-native-drawing-redraw-v1"
MAX_REDRAW_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
MAX_REDRAW_REQUEST_BYTES = 256 * 1024


@dataclass(slots=True)
class DrawingRedrawWorkspace:
    temporary: tempfile.TemporaryDirectory[str] = field(repr=False)
    path: Path = field(repr=False)
    freecadcmd: FrozenFile = field(repr=False)
    child: FrozenFile = field(repr=False)

    def cleanup(self) -> None:
        self.temporary.cleanup()


@dataclass(frozen=True, slots=True)
class FrozenDrawingRedraw:
    workspace: DrawingRedrawWorkspace = field(repr=False, compare=False)
    snapshot: FrozenFile = field(repr=False, compare=False)
    request: FrozenFile = field(repr=False, compare=False)
    request_sha256: str
    page_name: str
    view_names: tuple[str, ...]


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_redraw_workspace() -> DrawingRedrawWorkspace:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-redraw-")
    root = Path(temporary.name).resolve()
    try:
        os.chmod(root, 0o700)
        child = freeze_regular_file(
            Path(__file__).with_name("VibeCADNativeDrawingRedrawChild.py").resolve(),
            maximum=1024 * 1024,
        )
        return DrawingRedrawWorkspace(
            temporary=temporary,
            path=root,
            freecadcmd=resolve_freecadcmd(),
            child=child,
        )
    except Exception:
        temporary.cleanup()
        raise


def materialize_redraw_snapshot(
    document: Any,
    prepared: PreparedPageRedraw,
    workspace: DrawingRedrawWorkspace,
) -> FrozenDrawingRedraw:
    """Write an exact FCStd snapshot after the background receipt exists."""

    if not isinstance(workspace, DrawingRedrawWorkspace):
        raise TypeError("workspace must be a DrawingRedrawWorkspace")
    validate_prepared_page_redraw(document, prepared)
    snapshot_path = workspace.path / "document.FCStd"
    try:
        result = document.saveCopy(str(snapshot_path))
    except Exception as exc:
        raise NativeDrawingError(
            "The exact Drawing document could not be copied for detached redraw.",
            error_code="NATIVE_DRAWING_REDRAW_SNAPSHOT_FAILED",
        ) from exc
    if result is False or not snapshot_path.is_file():
        raise NativeDrawingError(
            "The exact Drawing document snapshot was not created.",
            error_code="NATIVE_DRAWING_REDRAW_SNAPSHOT_FAILED",
        )
    os.chmod(snapshot_path, 0o600)
    snapshot = freeze_regular_file(
        snapshot_path,
        maximum=MAX_REDRAW_SNAPSHOT_BYTES,
    )
    restore_page_redraw_commit_state(document, prepared)
    validate_prepared_page_redraw(document, prepared)
    request_value = {
        "protocol": DRAWING_REDRAW_PROTOCOL,
        "workspace": str(workspace.path),
        "snapshot": "document.FCStd",
        "snapshot_bytes": snapshot.size_bytes,
        "snapshot_sha256": snapshot.sha256,
        "page_name": str(prepared.page.Name),
        "page_state_sha256": str(prepared.page_state_before["state_sha256"]),
        "views": [
            {
                "object_name": item.object_name,
                "type_id": item.type_id,
                "kind": item.kind,
                "state_sha256": item.state_sha256,
            }
            for item in prepared.views
        ],
        "result": "result.json",
    }
    encoded = json.dumps(
        request_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_REDRAW_REQUEST_BYTES:
        raise NativeDrawingError(
            "The exact Drawing redraw request exceeds its metadata bound.",
            error_code="NATIVE_DRAWING_REDRAW_LIMIT",
        )
    request_path = workspace.path / "request.json"
    _write_private(request_path, encoded)
    request = freeze_regular_file(
        request_path,
        maximum=MAX_REDRAW_REQUEST_BYTES,
    )
    if request.sha256 != hashlib.sha256(encoded).hexdigest():
        raise NativeDrawingError(
            "The exact Drawing redraw request changed while it was written.",
            error_code="NATIVE_DRAWING_REDRAW_SNAPSHOT_FAILED",
        )
    return FrozenDrawingRedraw(
        workspace=workspace,
        snapshot=snapshot,
        request=request,
        request_sha256=request.sha256,
        page_name=str(prepared.page.Name),
        view_names=tuple(item.object_name for item in prepared.views),
    )
