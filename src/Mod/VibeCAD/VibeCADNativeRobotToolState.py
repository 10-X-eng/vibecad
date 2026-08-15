# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded, path-free state for Robot tool-shape targets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeInput import NativeInputError, inspect_native_input_file
from VibeCADNativeRobotState import MAX_ROBOT_VRML_BYTES
from VibeCADNativeTargets import object_reference


MAX_ROBOT_TOOL_SHAPES = 256
MAX_VISIBLE_ROBOT_TOOL_SHAPES = 64


class NativeRobotToolStateError(RuntimeError):
    """A Robot tool-shape target cannot be represented exactly."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ROBOT_TOOL_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class RobotToolShapeRecord:
    tool_shape: Any
    data: Mapping[str, Any]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        return {**dict(self.data), "state_sha256": self.state_sha256}


@dataclass(frozen=True, slots=True)
class RobotToolShapeInventory:
    records: tuple[RobotToolShapeRecord, ...]
    total_count: int

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": True,
            "candidate_count": self.total_count,
            "candidates": [record.summary() for record in self.records],
        }
        if self.total_count > len(self.records):
            result["candidates_truncated"] = True
        return result


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == type_id:
        return True
    reader = getattr(obj, "isDerivedFrom", None)
    if not callable(reader):
        return False
    try:
        return bool(reader(type_id))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def is_robot_tool_shape(obj: Any) -> bool:
    return bool(
        obj is not None
        and getattr(obj, "Document", None) is not None
        and str(getattr(obj, "Name", "") or "")
        and (_is_derived(obj, "Part::Feature") or _is_derived(obj, "App::VRMLObject"))
    )


def _finite(value: Any, label: str) -> float:
    try:
        result = float(getattr(value, "Value", value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeRobotToolStateError(
            f"A Robot tool shape has malformed {label}."
        ) from exc
    if not math.isfinite(result):
        raise NativeRobotToolStateError(f"A Robot tool shape has non-finite {label}.")
    if result == 0.0:
        return 0.0
    # Match FCStd placement precision so an unchanged target retains the same
    # stale-state digest after save/reopen.
    return round(result, 15)


def _placement(obj: Any) -> dict[str, list[float]]:
    try:
        placement = obj.Placement
        base = placement.Base
        quaternion = tuple(placement.Rotation.Q)
        position = (base.x, base.y, base.z)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotToolStateError(
            "A Robot tool shape has no exact placement."
        ) from exc
    if len(quaternion) != 4:
        raise NativeRobotToolStateError("A Robot tool shape has a malformed placement.")
    return {
        "position_mm": [_finite(value, "placement") for value in position],
        "quaternion_xyzw": [_finite(value, "placement") for value in quaternion],
    }


def _part_geometry(obj: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    shape = getattr(obj, "Shape", None)
    try:
        is_null = shape is None or bool(shape.isNull())
        summary = {
            "kind": "part",
            "null": is_null,
            "shape_type": "Null" if is_null else str(shape.ShapeType),
        }
        identity = {"shape_tag": -1 if shape is None else int(shape.Tag)}
        return summary, identity
    except NativeRobotToolStateError:
        raise
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotToolStateError(
            "A Part Robot tool shape has malformed geometry."
        ) from exc


def _vrml_geometry(obj: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        definition = inspect_native_input_file(
            str(getattr(obj, "VrmlFile", "") or ""),
            maximum_bytes=MAX_ROBOT_VRML_BYTES,
        )
    except (NativeInputError, OSError, RuntimeError) as exc:
        raise NativeRobotToolStateError(
            "A VRML Robot tool shape definition is unavailable."
        ) from exc
    return {"kind": "vrml", "definition": definition}, definition


def _sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_robot_tool_shape_record(obj: Any) -> RobotToolShapeRecord:
    if not is_robot_tool_shape(obj):
        raise NativeRobotToolStateError(
            "A Robot tool shape must be a live Part feature or VRML object."
        )
    geometry, geometry_identity = (
        _vrml_geometry(obj)
        if _is_derived(obj, "App::VRMLObject")
        else _part_geometry(obj)
    )
    data = {
        "object": object_reference(obj),
        "object_id": int(getattr(obj, "ID", -1)),
        "label": str(getattr(obj, "Label", "") or "")[:160],
        "placement": _placement(obj),
        "geometry": geometry,
    }
    return RobotToolShapeRecord(
        obj,
        data,
        _sha256({**data, "geometry_identity": geometry_identity}),
    )


def capture_robot_tool_shape_inventory(document: Any) -> RobotToolShapeInventory:
    candidates = tuple(
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if is_robot_tool_shape(obj)
    )
    if len(candidates) > MAX_ROBOT_TOOL_SHAPES:
        raise NativeRobotToolStateError(
            "The active document exceeds the bounded Robot tool-shape inventory."
        )
    visible = candidates[:MAX_VISIBLE_ROBOT_TOOL_SHAPES]
    return RobotToolShapeInventory(
        tuple(capture_robot_tool_shape_record(obj) for obj in visible),
        len(candidates),
    )
