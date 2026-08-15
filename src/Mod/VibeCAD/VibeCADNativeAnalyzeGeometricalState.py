# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact normalized state for FEM geometrical analysis features."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


_SECTION_VARIABLES = {
    "Section Force": "section_force",
    "Heat Flux": "heat_flux",
    "Drag Stress": "drag_stress",
    "Electric Flux": "electric_flux",
}


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise NativeAnalyzeError(
            "A FEM geometrical analysis feature contains a non-finite value."
        )
    return float(format(number, ".15g"))


def _vector(value: Any) -> dict[str, float]:
    return {
        "x": _finite(value.x),
        "y": _finite(value.y),
        "z": _finite(value.z),
    }


def canonical_axis_angle(axis: Any, angle_degrees: float) -> dict[str, Any]:
    components = [_finite(axis.x), _finite(axis.y), _finite(axis.z)]
    length = math.sqrt(sum(component * component for component in components))
    angle = math.fmod(_finite(angle_degrees), 360.0)
    if angle < -180.0:
        angle += 360.0
    elif angle > 180.0:
        angle -= 360.0
    if angle < 0.0:
        components = [-component for component in components]
        angle = -angle
    if abs(angle) <= 1.0e-12:
        components = [0.0, 0.0, 1.0]
        angle = 0.0
    elif length <= 1.0e-15:
        raise NativeAnalyzeError(
            "A rectangular FEM transform contains a zero-length rotation axis."
        )
    else:
        components = [component / length for component in components]
        if abs(angle - 180.0) <= 1.0e-12:
            for component in components:
                if abs(component) > 1.0e-15:
                    if component < 0.0:
                        components = [-value for value in components]
                    break
    components = [float(format(value, ".15g")) for value in components]
    return {
        "axis": dict(zip(("x", "y", "z"), components)),
        "angle_degrees": float(format(angle, ".15g")),
    }


def _references(obj: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    values = tuple(getattr(obj, "References", ()) or ())
    if len(values) != 1 or not isinstance(values[0], tuple) or len(values[0]) != 2:
        raise NativeAnalyzeError(
            "The FEM geometrical analysis feature does not contain one exact face reference."
        )
    source, names = values[0]
    names = (names,) if isinstance(names, str) else tuple(names or ())
    if len(names) != 1 or not str(names[0]).startswith("Face"):
        raise NativeAnalyzeError(
            "The FEM geometrical analysis feature does not contain one exact face reference."
        )
    visible = {
        "object_name": str(getattr(source, "Name", "") or ""),
        "subelement": str(names[0]),
    }
    try:
        source_sha = mesh_object_state(source).get("state_sha256")
    except Exception:
        source_sha = None
    exact = {
        **visible,
        "object_id": int(getattr(source, "ID", -1)),
        "source_state_sha256": source_sha,
    }
    return visible, exact


def geometrical_feature_kind(obj: Any) -> str:
    try:
        if obj.isDerivedFrom("Fem::ConstraintPlaneRotation"):
            return "plane_rotation"
        if obj.isDerivedFrom("Fem::ConstraintTransform"):
            return "transform"
    except Exception:
        pass
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    if proxy_type == "Fem::ConstraintSectionPrint":
        return "section_print"
    raise NativeAnalyzeError(
        "The exact target is not a supported FEM geometrical analysis feature.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _definition(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "plane_rotation":
        return {}
    if kind == "section_print":
        variable = _SECTION_VARIABLES.get(str(obj.Variable))
        if variable is None:
            raise NativeAnalyzeError(
                "The section-print feature contains an unsupported result variable."
            )
        return {"variable": variable}
    transform_type = str(obj.TransformType)
    if transform_type == "Rectangular":
        rotation = obj.Rotation
        return {
            "coordinate_system": {
                "kind": "rectangular",
                "rotation": canonical_axis_angle(
                    rotation.Axis,
                    math.degrees(_finite(rotation.Angle)),
                ),
            }
        }
    if transform_type == "Cylindrical":
        return {"coordinate_system": {"kind": "cylindrical"}}
    raise NativeAnalyzeError(
        "The local-coordinate-system feature contains an unsupported transform type."
    )


def _native_values(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "plane_rotation":
        return {}
    if kind == "section_print":
        return {"Variable": str(obj.Variable)}
    rotation = obj.Rotation
    return {
        "TransformType": str(obj.TransformType),
        "RotationQuaternion": [_finite(value) for value in rotation.Q],
        "BasePoint": list(_vector(obj.BasePoint).values()),
        "Axis": list(_vector(obj.Axis).values()),
    }


def geometrical_feature_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError(
            "The FEM geometrical analysis feature is no longer live."
        )
    kind = geometrical_feature_kind(obj)
    face, exact_face = _references(obj)
    definition = _definition(obj, kind)
    result = {
        **concise_object(obj),
        "feature_kind": kind,
        "face": face,
        "definition": definition,
    }
    if kind == "transform" and str(obj.TransformType) == "Cylindrical":
        result["derived_frame"] = {
            "origin_mm": _vector(obj.BasePoint),
            "axis": _vector(obj.Axis),
        }
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "kind": kind,
            "face": exact_face,
            "native_values": _native_values(obj, kind),
        }
    )
    return result


def geometrical_feature_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return geometrical_feature_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
