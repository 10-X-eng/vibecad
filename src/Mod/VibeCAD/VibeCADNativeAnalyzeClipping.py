# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded Native control of FEM clipping-plane presentation."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef, resolve_object


MAX_CLIPPING_STATE_PLANES = 1024
MAX_RETURNED_CLIPPING_PLANES = 16


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("A clipping-plane value is not finite")
    return float(format(result, ".15g"))


def _active_gui_document(document):
    import FreeCADGui as Gui

    gui_document = Gui.getDocument(str(document.Name))
    if gui_document is None or Gui.activeDocument() is not gui_document:
        raise NativeAnalyzeError(
            "The exact FEM document has no active 3D view.",
            error_code="NATIVE_ANALYZE_PRESENTATION_UNAVAILABLE",
        )
    return gui_document


def _plane_summary(index, node):
    plane = node.plane.getValue()
    normal = plane.getNormal().getValue()
    return {
        "scene_index": int(index),
        "normal": [_finite(normal[0]), _finite(normal[1]), _finite(normal[2])],
        "distance_from_origin_mm": _finite(plane.getDistanceFromOrigin()),
        "manipulator": type(node).__name__ == "SoClipPlaneManip",
    }


def clipping_state(document) -> dict[str, Any]:
    """Return a concise exact digest of root-level clipping planes in the view."""

    from femcommands.clipping import clipping_plane_nodes

    gui_document = _active_gui_document(document)
    nodes = clipping_plane_nodes(gui_document)
    if len(nodes) > MAX_CLIPPING_STATE_PLANES:
        raise NativeAnalyzeError(
            f"The active view exceeds the {MAX_CLIPPING_STATE_PLANES}-plane inspection bound.",
            error_code="NATIVE_ANALYZE_PRESENTATION_TOO_LARGE",
        )
    all_planes = [_plane_summary(index, node) for index, node in nodes]
    identity = {
        "document_uid": str(document.Uid),
        "planes": all_planes,
    }
    return {
        "plane_count": len(all_planes),
        "planes": all_planes[:MAX_RETURNED_CLIPPING_PLANES],
        "planes_truncated": len(all_planes) > MAX_RETURNED_CLIPPING_PLANES,
        "state_sha256": _digest(identity),
    }


def clipping_face_source_state(obj) -> dict[str, Any]:
    """Return an exact face-source token without serializing shape geometry."""

    document = getattr(obj, "Document", None)
    shape = getattr(obj, "Shape", None)
    try:
        if (
            document is None
            or document.getObject(str(obj.Name)) is not obj
            or shape is None
            or shape.isNull()
            or not shape.isValid()
        ):
            raise ValueError
        face_count = len(shape.Faces)
        shape_hash = int(shape.hashCode())
        placement = getattr(obj, "getGlobalPlacement", lambda: obj.Placement)()
        matrix = placement.toMatrix()
        matrix_values = [_finite(value) for value in tuple(matrix.A)]
    except Exception as exc:
        raise NativeAnalyzeError(
            "A clipping-plane face source requires one live valid shape.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        ) from exc
    identity = {
        "object": [str(obj.Name), int(obj.ID), str(obj.TypeId)],
        "shape_hash": shape_hash,
        "face_count": face_count,
        "global_placement": matrix_values,
    }
    return {
        "object_name": str(obj.Name),
        "object_id": int(obj.ID),
        "face_count": face_count,
        "state_sha256": _digest(identity),
    }


def _exact_count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_CLIPPING_STATE_PLANES:
        raise NativeAnalyzeError(
            f"{field} must be an integer from 0 through {MAX_CLIPPING_STATE_PLANES}."
        )
    return value


def _exact_digest(value: Any, field: str) -> str:
    result = str(value or "")
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise NativeAnalyzeError(f"{field} must be one lowercase SHA-256 digest.")
    return result


def _require_clipping_state(document, expected_hash, expected_count):
    expected = _exact_digest(expected_hash, "expected_clipping_state_sha256")
    count = _exact_count(expected_count, "expected_clipping_plane_count")
    current = clipping_state(document)
    if current["state_sha256"] != expected or current["plane_count"] != count:
        raise NativeAnalyzeError(
            "The active view clipping planes changed after the provider read them.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "expected_clipping_state_sha256": current["state_sha256"],
                "expected_clipping_plane_count": current["plane_count"],
            },
        )
    return current


def _resolve_face(document, document_uid, value):
    required = {"object_name", "face_index", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "face must contain only object_name, face_index, and expected_state_sha256."
        )
    obj = resolve_object(document, NativeObjectRef(document_uid, value["object_name"]))
    current = clipping_face_source_state(obj)
    expected = _exact_digest(value["expected_state_sha256"], "face.expected_state_sha256")
    if expected != current["state_sha256"]:
        raise NativeAnalyzeError(
            "The clipping-plane face source changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={"face": current},
        )
    index = value["face_index"]
    if type(index) is not int or not 1 <= index <= current["face_count"]:
        raise NativeAnalyzeError(
            f"face_index must be an integer from 1 through {current['face_count']}.",
            repair={"face": current},
        )
    try:
        face = obj.Shape.getElement(f"Face{index}")
        point = face.CenterOfMass
        uv = face.Surface.parameter(point)
        normal = face.normalAt(uv[0], uv[1])
        placement = getattr(obj, "getGlobalPlacement", lambda: obj.Placement)()
        point = placement.multVec(point)
        normal = placement.Rotation.multVec(normal)
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The exact Face{index} cannot define a clipping plane.",
            error_code="NATIVE_ANALYZE_TARGET_INVALID",
        ) from exc
    return obj, index, point, normal, current


def add_face_clipping_plane(
    context: NativeRuntimeContext,
    *,
    face: Any,
    reverse: Any,
    expected_clipping_state_sha256: Any,
    expected_clipping_plane_count: Any,
) -> dict[str, Any]:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if type(reverse) is not bool:
        raise NativeAnalyzeError("reverse must be a boolean.")
    context.guard()
    before = _require_clipping_state(
        context.document,
        expected_clipping_state_sha256,
        expected_clipping_plane_count,
    )
    obj, face_index, point, normal, face_state = _resolve_face(
        context.document,
        context.document_uid,
        face,
    )
    if reverse:
        normal = normal.negative()
    gui_document = _active_gui_document(context.document)
    from femcommands.clipping import (
        add_clipping_plane,
        remove_exact_clipping_plane,
    )

    node = None
    try:
        node = add_clipping_plane(gui_document, context.document, point, normal)
        context.guard()
        after = clipping_state(context.document)
        if after["plane_count"] != before["plane_count"] + 1:
            raise RuntimeError("The exact clipping plane was not added")
    except Exception as exc:
        if node is not None:
            try:
                remove_exact_clipping_plane(gui_document, node)
            except Exception:
                pass
        if isinstance(exc, NativeAnalyzeError):
            raise
        raise NativeAnalyzeError(
            f"The clipping plane could not be added: {exc}",
            error_code="NATIVE_ANALYZE_PRESENTATION_FAILED",
        ) from exc
    return {
        "changed": True,
        "added": {
            "source": f"{obj.Name}.Face{face_index}",
            "source_state_sha256": face_state["state_sha256"],
            "reversed": reverse,
            "point_mm": [_finite(point.x), _finite(point.y), _finite(point.z)],
            "face_normal": [_finite(normal.x), _finite(normal.y), _finite(normal.z)],
        },
        "clipping": after,
    }


def remove_all_view_clipping_planes(
    context: NativeRuntimeContext,
    *,
    expected_clipping_state_sha256: Any,
    expected_clipping_plane_count: Any,
) -> dict[str, Any]:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    context.guard()
    before = _require_clipping_state(
        context.document,
        expected_clipping_state_sha256,
        expected_clipping_plane_count,
    )
    gui_document = _active_gui_document(context.document)
    from femcommands.clipping import (
        remove_all_clipping_planes,
        restore_clipping_planes,
    )

    removed = ()
    try:
        removed = remove_all_clipping_planes(gui_document)
        context.guard()
        after = clipping_state(context.document)
        if after["plane_count"] != 0:
            raise RuntimeError("Not every clipping plane was removed")
    except Exception as exc:
        if removed:
            try:
                restore_clipping_planes(gui_document, removed)
            except Exception:
                pass
        if isinstance(exc, NativeAnalyzeError):
            raise
        raise NativeAnalyzeError(
            f"The clipping planes could not be removed: {exc}",
            error_code="NATIVE_ANALYZE_PRESENTATION_FAILED",
        ) from exc
    return {
        "changed": bool(removed),
        "removed_plane_count": len(removed),
        "previous_clipping_state_sha256": before["state_sha256"],
        "clipping": after,
    }
