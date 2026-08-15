# SPDX-License-Identifier: LGPL-2.1-or-later

"""Private exact-document input for a detached TechDraw complex section."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from VibeCADNativeDrawingComplexSection import (
    PreparedComplexSectionView,
    validate_prepared_complex_section_view,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import (
    FrozenFile,
    freeze_regular_file,
    resolve_freecadcmd,
)


DRAWING_COMPLEX_SECTION_PROTOCOL = "vibecad-native-drawing-complex-section-v1"
MAX_COMPLEX_SECTION_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPLEX_SECTION_REQUEST_BYTES = 256 * 1024


@dataclass(slots=True)
class DrawingComplexSectionWorkspace:
    temporary: tempfile.TemporaryDirectory[str] = field(repr=False)
    path: Path = field(repr=False)
    freecadcmd: FrozenFile = field(repr=False)
    child: FrozenFile = field(repr=False)

    def cleanup(self) -> None:
        self.temporary.cleanup()


@dataclass(frozen=True, slots=True)
class FrozenDrawingComplexSection:
    workspace: DrawingComplexSectionWorkspace = field(repr=False, compare=False)
    snapshot: FrozenFile = field(repr=False, compare=False)
    request: FrozenFile = field(repr=False, compare=False)
    request_sha256: str
    page_name: str
    base_name: str
    profile_name: str
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


def create_complex_section_workspace() -> DrawingComplexSectionWorkspace:
    temporary = tempfile.TemporaryDirectory(
        prefix="vibecad-native-drawing-complex-section-"
    )
    root = Path(temporary.name).resolve()
    try:
        os.chmod(root, 0o700)
        child = freeze_regular_file(
            Path(__file__).with_name(
                "VibeCADNativeDrawingComplexSectionChild.py"
            ).resolve(),
            maximum=1024 * 1024,
        )
        return DrawingComplexSectionWorkspace(
            temporary=temporary,
            path=root,
            freecadcmd=resolve_freecadcmd(),
            child=child,
        )
    except Exception:
        temporary.cleanup()
        raise


def materialize_complex_section_snapshot(
    document: Any,
    prepared: PreparedComplexSectionView,
    workspace: DrawingComplexSectionWorkspace,
) -> FrozenDrawingComplexSection:
    """Write the exact FCStd only after the job has a cancellation owner."""

    if not isinstance(workspace, DrawingComplexSectionWorkspace):
        raise TypeError("workspace must be a DrawingComplexSectionWorkspace")
    validate_prepared_complex_section_view(document, prepared)
    snapshot_path = workspace.path / "document.FCStd"
    try:
        result = document.saveCopy(str(snapshot_path))
    except Exception as exc:
        raise NativeDrawingError(
            "The exact Drawing document could not be copied for a detached complex section.",
            error_code="NATIVE_DRAWING_COMPLEX_SECTION_SNAPSHOT_FAILED",
        ) from exc
    if result is False or not snapshot_path.is_file():
        raise NativeDrawingError(
            "The exact complex-section document snapshot was not created.",
            error_code="NATIVE_DRAWING_COMPLEX_SECTION_SNAPSHOT_FAILED",
        )
    os.chmod(snapshot_path, 0o600)
    snapshot = freeze_regular_file(
        snapshot_path,
        maximum=MAX_COMPLEX_SECTION_SNAPSHOT_BYTES,
    )
    validate_prepared_complex_section_view(document, prepared)
    common = prepared.section
    spec = common.spec
    request_value = {
        "protocol": DRAWING_COMPLEX_SECTION_PROTOCOL,
        "workspace": str(workspace.path),
        "snapshot": "document.FCStd",
        "snapshot_bytes": snapshot.size_bytes,
        "snapshot_sha256": snapshot.sha256,
        "page": _object_descriptor(common.page, common.page_state_before),
        "base_view": _object_descriptor(
            common.base_view,
            common.base_state_before,
        ),
        "profile": _object_descriptor(
            prepared.profile,
            prepared.profile_state_before,
        ),
        "sources": [
            _object_descriptor(obj, state)
            for obj, state in zip(
                common.sources,
                common.source_states,
                strict=True,
            )
        ],
        "section": {
            "normal": list(spec.section_normal),
            "x_direction": list(spec.section_x_direction),
            "rotation_degrees": spec.rotation_degrees,
            "projection_strategy": prepared.projection_strategy,
            "scale_kind": spec.scale_kind,
            "requested_scale": spec.requested_scale,
            "page_scale": float(common.page_state_before["scale"]),
            "page_size_mm": list(common.page_size_mm),
            "line_flags": dict(common.line_flags),
            "fuse_before_cut": common.fuse_before_cut,
        },
        "result": "result.json",
    }
    encoded = json.dumps(
        request_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_COMPLEX_SECTION_REQUEST_BYTES:
        raise NativeDrawingError(
            "The exact complex-section request exceeds its metadata bound.",
            error_code="NATIVE_DRAWING_COMPLEX_SECTION_LIMIT",
        )
    request_path = workspace.path / "request.json"
    _write_private(request_path, encoded)
    request = freeze_regular_file(
        request_path,
        maximum=MAX_COMPLEX_SECTION_REQUEST_BYTES,
    )
    if request.sha256 != hashlib.sha256(encoded).hexdigest():
        raise NativeDrawingError(
            "The exact complex-section request changed while it was written.",
            error_code="NATIVE_DRAWING_COMPLEX_SECTION_SNAPSHOT_FAILED",
        )
    return FrozenDrawingComplexSection(
        workspace=workspace,
        snapshot=snapshot,
        request=request,
        request_sha256=request.sha256,
        page_name=str(common.page.Name),
        base_name=str(common.base_view.Name),
        profile_name=str(prepared.profile.Name),
        source_names=tuple(str(obj.Name) for obj in common.sources),
    )
