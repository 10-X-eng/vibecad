# SPDX-License-Identifier: LGPL-2.1-or-later

"""Private exact-document input for a detached TechDraw detail view."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from VibeCADNativeDrawingDetail import PreparedDetailView, validate_prepared_detail_view
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import (
    FrozenFile,
    freeze_regular_file,
    resolve_freecadcmd,
)


DRAWING_DETAIL_PROTOCOL = "vibecad-native-drawing-detail-v1"
MAX_DETAIL_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
MAX_DETAIL_REQUEST_BYTES = 256 * 1024


@dataclass(slots=True)
class DrawingDetailWorkspace:
    temporary: tempfile.TemporaryDirectory[str] = field(repr=False)
    path: Path = field(repr=False)
    freecadcmd: FrozenFile = field(repr=False)
    child: FrozenFile = field(repr=False)

    def cleanup(self) -> None:
        self.temporary.cleanup()


@dataclass(frozen=True, slots=True)
class FrozenDrawingDetail:
    workspace: DrawingDetailWorkspace = field(repr=False, compare=False)
    snapshot: FrozenFile = field(repr=False, compare=False)
    request: FrozenFile = field(repr=False, compare=False)
    request_sha256: str
    page_name: str
    base_name: str
    source_names: tuple[str, ...]


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _object_descriptor(obj: Any, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_id": int(obj.ID),
        "object_name": str(obj.Name),
        "type_id": str(obj.TypeId),
        "state_sha256": str(state["state_sha256"]),
    }


def create_detail_workspace() -> DrawingDetailWorkspace:
    temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-detail-")
    root = Path(temporary.name).resolve()
    try:
        os.chmod(root, 0o700)
        child = freeze_regular_file(
            Path(__file__).with_name("VibeCADNativeDrawingDetailChild.py").resolve(),
            maximum=1024 * 1024,
        )
        return DrawingDetailWorkspace(
            temporary=temporary,
            path=root,
            freecadcmd=resolve_freecadcmd(),
            child=child,
        )
    except Exception:
        temporary.cleanup()
        raise


def materialize_detail_snapshot(
    document: Any,
    prepared: PreparedDetailView,
    workspace: DrawingDetailWorkspace,
) -> FrozenDrawingDetail:
    """Write the exact FCStd only after the background job can be cancelled."""

    if not isinstance(workspace, DrawingDetailWorkspace):
        raise TypeError("workspace must be a DrawingDetailWorkspace")
    validate_prepared_detail_view(document, prepared)
    snapshot_path = workspace.path / "document.FCStd"
    try:
        result = document.saveCopy(str(snapshot_path))
    except Exception as exc:
        raise NativeDrawingError(
            "The exact Drawing document could not be copied for a detached detail.",
            error_code="NATIVE_DRAWING_DETAIL_SNAPSHOT_FAILED",
        ) from exc
    if result is False or not snapshot_path.is_file():
        raise NativeDrawingError(
            "The exact Drawing document snapshot was not created.",
            error_code="NATIVE_DRAWING_DETAIL_SNAPSHOT_FAILED",
        )
    os.chmod(snapshot_path, 0o600)
    snapshot = freeze_regular_file(snapshot_path, maximum=MAX_DETAIL_SNAPSHOT_BYTES)
    validate_prepared_detail_view(document, prepared)
    spec = prepared.spec
    request_value = {
        "protocol": DRAWING_DETAIL_PROTOCOL,
        "workspace": str(workspace.path),
        "snapshot": "document.FCStd",
        "snapshot_bytes": snapshot.size_bytes,
        "snapshot_sha256": snapshot.sha256,
        "page": _object_descriptor(prepared.page, prepared.page_state_before),
        "base_view": _object_descriptor(prepared.base_view, prepared.base_state_before),
        "sources": [
            _object_descriptor(obj, state)
            for obj, state in zip(
                prepared.sources,
                prepared.source_states,
                strict=True,
            )
        ],
        "detail": {
            "anchor_mm": list(spec.anchor_mm),
            "radius_mm": spec.radius_mm,
            "position_mm": list(spec.position_mm),
            "scale_kind": spec.scale_kind,
            "requested_scale": spec.requested_scale,
            "page_scale": float(prepared.page_state_before["scale"]),
            "base_scale": prepared.base_scale,
            "line_flags": dict(prepared.line_flags),
            "matting_style": prepared.matting_style,
        },
        "result": "result.json",
    }
    encoded = json.dumps(
        request_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_DETAIL_REQUEST_BYTES:
        raise NativeDrawingError(
            "The exact detail request exceeds its metadata bound.",
            error_code="NATIVE_DRAWING_DETAIL_LIMIT",
        )
    request_path = workspace.path / "request.json"
    _write_private(request_path, encoded)
    request = freeze_regular_file(request_path, maximum=MAX_DETAIL_REQUEST_BYTES)
    if request.sha256 != hashlib.sha256(encoded).hexdigest():
        raise NativeDrawingError(
            "The exact detail request changed while it was written.",
            error_code="NATIVE_DRAWING_DETAIL_SNAPSHOT_FAILED",
        )
    return FrozenDrawingDetail(
        workspace=workspace,
        snapshot=snapshot,
        request=request,
        request_sha256=request.sha256,
        page_name=str(prepared.page.Name),
        base_name=str(prepared.base_view.Name),
        source_names=tuple(str(obj.Name) for obj in prepared.sources),
    )
