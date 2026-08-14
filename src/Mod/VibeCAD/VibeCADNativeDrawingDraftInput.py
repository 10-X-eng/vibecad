# SPDX-License-Identifier: LGPL-2.1-or-later

"""Private exact-document input for detached Draft-source rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from VibeCADNativeDrawingDraft import PreparedDraftView, validate_prepared_draft_view
from VibeCADNativeDrawingDraftState import draft_source_fingerprint
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import (
    FrozenFile,
    freeze_regular_file,
    resolve_freecadcmd,
)
from VibeCADNativeDrawingViewState import DRAWING_VIEW_ORIENTATIONS


DRAWING_DRAFT_PROTOCOL = "vibecad-native-drawing-draft-v1"
MAX_DRAFT_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
MAX_DRAFT_REQUEST_BYTES = 512 * 1024


@dataclass(slots=True)
class DrawingDraftWorkspace:
    temporary: tempfile.TemporaryDirectory[str] = field(repr=False)
    path: Path = field(repr=False)
    freecadcmd: FrozenFile = field(repr=False)
    child: FrozenFile = field(repr=False)

    def cleanup(self) -> None:
        self.temporary.cleanup()


@dataclass(frozen=True, slots=True)
class FrozenDrawingDraft:
    workspace: DrawingDraftWorkspace = field(repr=False, compare=False)
    snapshot: FrozenFile = field(repr=False, compare=False)
    request: FrozenFile = field(repr=False, compare=False)
    request_sha256: str
    page_name: str
    source_name: str
    source_state_sha256: str


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_child(root: Path) -> FrozenFile:
    source = freeze_regular_file(
        Path(__file__).with_name("VibeCADNativeDrawingDraftChild.py").resolve(),
        maximum=1024 * 1024,
    )
    descriptor = os.open(
        source.path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        data = bytearray()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                _error(
                    "The fixed Draft rendering child exceeds its safety bound.",
                    "NATIVE_DRAWING_DRAFT_RUNTIME_UNAVAILABLE",
                )
    finally:
        os.close(descriptor)
    if hashlib.sha256(data).hexdigest() != source.sha256:
        _error(
            "The fixed Draft rendering child changed while it was copied.",
            "NATIVE_DRAWING_DRAFT_RUNTIME_CHANGED",
        )
    destination = root / "child.py"
    _write_private(destination, bytes(data))
    copied = freeze_regular_file(destination, maximum=1024 * 1024)
    if copied.sha256 != source.sha256:
        _error(
            "The private Draft rendering child failed authentication.",
            "NATIVE_DRAWING_DRAFT_RUNTIME_UNAVAILABLE",
        )
    return copied


def create_draft_workspace() -> DrawingDraftWorkspace:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-draft-")
    root = Path(temporary.name).resolve()
    try:
        os.chmod(root, 0o700)
        child = _copy_child(root)
        return DrawingDraftWorkspace(
            temporary=temporary,
            path=root,
            freecadcmd=resolve_freecadcmd(),
            child=child,
        )
    except Exception:
        temporary.cleanup()
        raise


def _object_descriptor(obj: Any, state_sha256: str) -> dict[str, Any]:
    return {
        "object_id": int(obj.ID),
        "object_name": str(obj.Name),
        "type_id": str(obj.TypeId),
        "state_sha256": state_sha256,
    }


def materialize_draft_snapshot(
    document: Any,
    prepared: PreparedDraftView,
    workspace: DrawingDraftWorkspace,
) -> FrozenDrawingDraft:
    if not isinstance(workspace, DrawingDraftWorkspace):
        raise TypeError("workspace must be a DrawingDraftWorkspace")
    validate_prepared_draft_view(document, prepared)
    fingerprint = draft_source_fingerprint(prepared.source)
    if fingerprint.state_sha256 != prepared.source_fingerprint.state_sha256:
        _error(
            "The exact Draft source changed before its snapshot was written.",
            "NATIVE_DRAWING_DRAFT_SOURCE_STALE",
        )
    snapshot_path = workspace.path / "document.FCStd"
    try:
        result = document.saveCopy(str(snapshot_path))
    except Exception as exc:
        raise NativeDrawingError(
            "The exact Drawing document could not be copied for Draft rendering.",
            error_code="NATIVE_DRAWING_DRAFT_SNAPSHOT_FAILED",
        ) from exc
    if result is False or not snapshot_path.is_file():
        _error(
            "The exact Drawing snapshot was not created for Draft rendering.",
            "NATIVE_DRAWING_DRAFT_SNAPSHOT_FAILED",
        )
    os.chmod(snapshot_path, 0o600)
    snapshot = freeze_regular_file(snapshot_path, maximum=MAX_DRAFT_SNAPSHOT_BYTES)
    validate_prepared_draft_view(document, prepared)
    fingerprint = draft_source_fingerprint(prepared.source)
    spec = prepared.spec
    style = spec.style
    direction, _x_direction = DRAWING_VIEW_ORIENTATIONS[spec.orientation]
    effective_scale = (
        float(prepared.page_state_before["scale"])
        if spec.requested_scale is None
        else spec.requested_scale
    )
    request_value = {
        "protocol": DRAWING_DRAFT_PROTOCOL,
        "workspace": str(workspace.path),
        "snapshot": "document.FCStd",
        "snapshot_bytes": snapshot.size_bytes,
        "snapshot_sha256": snapshot.sha256,
        "page": _object_descriptor(
            prepared.page,
            prepared.page_state_before["state_sha256"],
        ),
        "source": fingerprint.descriptor(),
        "render": {
            "direction": list(direction),
            "scale": effective_scale,
            "line_width": style.line_width,
            "font_size": style.font_size,
            "color_rgb": list(style.color_rgb),
            "line_style": style.line_style,
            "line_spacing": style.line_spacing,
            "override": style.override,
        },
        "symbol": "outputs/draft-view.svg",
        "result": "result.json",
    }
    encoded = json.dumps(
        request_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_DRAFT_REQUEST_BYTES:
        _error(
            "The exact Draft rendering request exceeds its metadata bound.",
            "NATIVE_DRAWING_DRAFT_LIMIT",
        )
    request_path = workspace.path / "request.json"
    _write_private(request_path, encoded)
    request = freeze_regular_file(request_path, maximum=MAX_DRAFT_REQUEST_BYTES)
    if request.sha256 != hashlib.sha256(encoded).hexdigest():
        _error(
            "The exact Draft rendering request changed while it was written.",
            "NATIVE_DRAWING_DRAFT_SNAPSHOT_FAILED",
        )
    return FrozenDrawingDraft(
        workspace=workspace,
        snapshot=snapshot,
        request=request,
        request_sha256=request.sha256,
        page_name=str(prepared.page.Name),
        source_name=str(prepared.source.Name),
        source_state_sha256=fingerprint.state_sha256,
    )
