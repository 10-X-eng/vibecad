# SPDX-License-Identifier: LGPL-2.1-or-later

"""One exact revision guard for the human-opened Native Sketch."""

from __future__ import annotations

import hashlib
from itertools import islice
import json
from typing import Any, Iterable, Mapping

from VibeCADEditState import active_edit_object
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchLimits import (
    DEFAULT_SKETCH_INSPECT_PAGE_SIZE,
    MAX_SKETCH_INSPECT_PAGE_SIZE,
)
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
    serialize_sketch_state,
)


SKETCH_REVISION_SCHEMA = {
    "type": "string",
    "pattern": "^sketch-v1:[0-9a-f]{64}$",
    "maxLength": 74,
    "description": "Exact revision returned by sketch.inspect or the preceding Sketch tool.",
}


class NativeSketchRevisionConflict(NativeSketchError):
    """The human-opened Sketch no longer matches the provider's read state."""

    def __init__(self, current_revision: str) -> None:
        super().__init__(
            "The active Sketch changed after the supplied revision. Read it with "
            "sketch.inspect and retry using the returned revision."
        )
        self.current_revision = current_revision

    def failure(self) -> dict[str, Any]:
        return {
            "error_code": "NATIVE_SKETCH_REVISION_CONFLICT",
            "message": str(self),
            "current_revision": self.current_revision,
            "repair": {
                "tool": "sketch.inspect",
                "arguments": {"operation": "read_state"},
            },
            "retry_same_call": False,
        }


def require_active_sketch(context: NativeRuntimeContext) -> Any:
    """Return the exact Sketch the human currently has open for editing."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    context.guard()
    if (
        str(context.active_surface_id() or "") != "sketch.edit"
        or not bool(context.edit_or_task_active())
    ):
        raise NativeSketchError(
            "The human must keep a Sketch open on the Sketch ribbon."
        )
    sketch = active_edit_object()
    if (
        sketch is None
        or getattr(sketch, "Document", None) is not context.document
        or str(getattr(sketch, "TypeId", "") or "")
        != "Sketcher::SketchObject"
    ):
        raise NativeSketchError(
            "The exact human-opened Sketch is unavailable."
        )
    return sketch


def _update_records(
    digest: Any,
    category: str,
    records: Iterable[Mapping[str, Any]],
) -> None:
    digest.update(category.encode("ascii"))
    for record in records:
        encoded = json.dumps(
            dict(record),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)


def sketch_revision(sketch: Any) -> str:
    """Hash every mutable Sketch record used by Native edit operations."""

    digest = hashlib.sha256(b"vibecad-native-sketch-v1\0")
    identity = {
        "name": str(getattr(sketch, "Name", "") or ""),
        "geometry_count": int(getattr(sketch, "GeometryCount", 0) or 0),
        "constraint_count": int(getattr(sketch, "ConstraintCount", 0) or 0),
    }
    _update_records(digest, "identity", (identity,))
    _update_records(digest, "geometry", iter_sketch_geometry_records(sketch))
    _update_records(digest, "constraints", iter_sketch_constraint_records(sketch))
    _update_records(
        digest,
        "external_geometry",
        iter_sketch_external_geometry_records(sketch),
    )
    external_references = []
    for source, names in list(getattr(sketch, "ExternalGeometry", []) or []):
        raw_names = [names] if isinstance(names, str) else list(names or [])
        external_references.append(
            {
                "object_name": str(getattr(source, "Name", "") or ""),
                "subelements": [str(name) for name in raw_names],
            }
        )
    _update_records(digest, "external_references", external_references)
    return "sketch-v1:" + digest.hexdigest()


def require_sketch_revision(sketch: Any, expected: Any) -> str:
    current = sketch_revision(sketch)
    if not isinstance(expected, str) or expected != current:
        raise NativeSketchRevisionConflict(current)
    return current


def _page_value(arguments: Mapping[str, Any], name: str, maximum: int) -> int:
    value = arguments.get(name, 0)
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeSketchError(
            f"Sketch inspect {name} must be an integer from 0 to {maximum}."
        )
    return value


def sketch_read_result(
    sketch: Any,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request = dict(arguments or {})
    state = serialize_sketch_state(sketch)
    page_size = request.get("page_size", DEFAULT_SKETCH_INSPECT_PAGE_SIZE)
    if (
        type(page_size) is not int
        or not 1 <= page_size <= MAX_SKETCH_INSPECT_PAGE_SIZE
    ):
        raise NativeSketchError(
            "Sketch inspect page_size must be an integer from 1 to "
            f"{MAX_SKETCH_INSPECT_PAGE_SIZE}."
        )
    geometry_offset = _page_value(
        request,
        "geometry_offset",
        int(state["geometry_count"]),
    )
    constraint_offset = _page_value(
        request,
        "constraint_offset",
        int(state["constraint_count"]),
    )
    external_offset = _page_value(
        request,
        "external_geometry_offset",
        int(state["external_geometry_count"]),
    )
    state["geometry"] = list(
        islice(
            iter_sketch_geometry_records(sketch),
            geometry_offset,
            geometry_offset + page_size,
        )
    )
    state["constraints"] = list(
        islice(
            iter_sketch_constraint_records(sketch),
            constraint_offset,
            constraint_offset + page_size,
        )
    )
    state["external_geometry"] = list(
        islice(
            iter_sketch_external_geometry_records(sketch),
            external_offset,
            external_offset + page_size,
        )
    )
    state.update(
        {
            "geometry_offset": geometry_offset,
            "constraint_offset": constraint_offset,
            "external_geometry_offset": external_offset,
            "page_size": page_size,
            "geometry_truncated": geometry_offset + len(state["geometry"])
            < int(state["geometry_count"]),
            "constraints_truncated": constraint_offset
            + len(state["constraints"])
            < int(state["constraint_count"]),
            "external_geometry_truncated": external_offset
            + len(state["external_geometry"])
            < int(state["external_geometry_count"]),
        }
    )
    return {
        "_vibecad_complete_api_result": True,
        "sketch": {
            "object_name": str(getattr(sketch, "Name", "") or ""),
            "label": str(getattr(sketch, "Label", "") or ""),
        },
        "revision": sketch_revision(sketch),
        "state": state,
    }
