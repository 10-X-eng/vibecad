# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact normalized state for FEM mechanical support conditions."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


_KINDS = {
    "Fem::ConstraintFixed": "fixed",
    "Fem::ConstraintRigidBody": "rigid_body",
    "Fem::ConstraintDisplacement": "displacement",
    "Fem::ConstraintSpring": "spring",
}
_AXES = (("x", "X"), ("y", "Y"), ("z", "Z"))


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
            "A FEM mechanical support condition contains a non-finite value."
        )
    return float(format(number, ".15g"))


def _vector(value: Any) -> dict[str, float]:
    return {
        "x": _finite(value.x),
        "y": _finite(value.y),
        "z": _finite(value.z),
    }


def _references(obj: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visible = []
    exact = []
    for raw in tuple(getattr(obj, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            continue
        source, names = raw
        names = (names,) if isinstance(names, str) else tuple(names or ())
        record = {
            "object_name": str(getattr(source, "Name", "") or ""),
            "subelements": [str(name) for name in names],
        }
        visible.append(record)
        try:
            source_sha = mesh_object_state(source).get("state_sha256")
        except Exception:
            source_sha = None
        exact.append(
            {
                **record,
                "object_id": int(getattr(source, "ID", -1)),
                "source_state_sha256": source_sha,
            }
        )
    return visible, exact


def support_condition_kind(obj: Any) -> str:
    for type_id, kind in _KINDS.items():
        try:
            if obj.isDerivedFrom(type_id):
                return kind
        except Exception:
            continue
    raise NativeAnalyzeError(
        "The exact target is not a supported FEM mechanical support condition.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _rigid_rotation_components(obj: Any) -> dict[str, float]:
    rotation = obj.Rotation
    angle = math.degrees(_finite(rotation.Angle))
    constrained = [
        _finite(rotation.Axis[index])
        if str(getattr(obj, f"RotationalMode{suffix}")) == "Constraint"
        else 0.0
        for index, (_axis, suffix) in enumerate(_AXES)
    ]
    length = math.sqrt(sum(value * value for value in constrained))
    if length <= 1.0e-15:
        constrained = [0.0, 0.0, 0.0]
    else:
        constrained = [value * angle / length for value in constrained]
    return {
        axis: float(format(constrained[index], ".15g"))
        for index, (axis, _suffix) in enumerate(_AXES)
    }


def _rigid_definition(obj: Any) -> dict[str, Any]:
    translation = {}
    rotation = {}
    rotation_values = _rigid_rotation_components(obj)
    for axis, suffix in _AXES:
        trans_mode = str(getattr(obj, f"TranslationalMode{suffix}"))
        if trans_mode == "Free":
            translation[axis] = {"kind": "free"}
        elif trans_mode == "Constraint":
            translation[axis] = {
                "kind": "prescribed",
                "displacement_mm": _finite(getattr(obj.Displacement, axis)),
            }
        elif trans_mode == "Load":
            translation[axis] = {
                "kind": "load",
                "force_n": _finite(
                    getattr(obj, f"Force{suffix}").getValueAs("N").Value
                ),
            }
        else:
            raise NativeAnalyzeError("A rigid-body translation mode is unsupported.")

        rot_mode = str(getattr(obj, f"RotationalMode{suffix}"))
        if rot_mode == "Free":
            rotation[axis] = {"kind": "free"}
        elif rot_mode == "Constraint":
            rotation[axis] = {
                "kind": "prescribed",
                "rotation_degrees": rotation_values[axis],
            }
        elif rot_mode == "Load":
            rotation[axis] = {
                "kind": "load",
                "moment_n_mm": _finite(
                    getattr(obj, f"Moment{suffix}").getValueAs("N*mm").Value
                ),
            }
        else:
            raise NativeAnalyzeError("A rigid-body rotation mode is unsupported.")
    return {
        "reference_node_mm": _vector(obj.ReferenceNode),
        "translation": translation,
        "rotation": rotation,
    }


def _displacement_definition(obj: Any) -> dict[str, Any]:
    translation = {}
    rotation = {}
    for axis, suffix in _AXES:
        if bool(getattr(obj, f"{axis}Free")):
            translation[axis] = {"kind": "free"}
        elif bool(getattr(obj, f"has{suffix}Formula")):
            translation[axis] = {
                "kind": "formula",
                "expression": str(getattr(obj, f"{axis}DisplacementFormula")),
            }
        else:
            translation[axis] = {
                "kind": "value",
                "displacement_mm": _finite(
                    getattr(obj, f"{axis}Displacement").getValueAs("mm").Value
                ),
            }
        if bool(getattr(obj, f"rot{axis}Free")):
            rotation[axis] = {"kind": "free"}
        else:
            rotation[axis] = {
                "kind": "value",
                "rotation_degrees": _finite(
                    getattr(obj, f"{axis}Rotation").getValueAs("deg").Value
                ),
            }
    return {
        "translation": translation,
        "rotation": rotation,
        "flow_surface_force": bool(obj.useFlowSurfaceForce),
    }


def _definition(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "fixed":
        return {}
    if kind == "rigid_body":
        return _rigid_definition(obj)
    if kind == "displacement":
        return _displacement_definition(obj)
    elmer_value = str(obj.ElmerStiffness)
    if elmer_value not in {"Normal Stiffness", "Tangential Stiffness"}:
        raise NativeAnalyzeError("A spring condition contains an unsupported Elmer component.")
    return {
        "normal_stiffness_n_m": _finite(
            obj.NormalStiffness.getValueAs("N/m").Value
        ),
        "tangential_stiffness_n_m": _finite(
            obj.TangentialStiffness.getValueAs("N/m").Value
        ),
        "elmer_component": "normal" if elmer_value == "Normal Stiffness" else "tangential",
    }


def _native_values(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "fixed":
        return {}
    if kind == "rigid_body":
        return {
            "ReferenceNode": list(_vector(obj.ReferenceNode).values()),
            "Displacement": list(_vector(obj.Displacement).values()),
            "RotationQuaternion": [_finite(value) for value in obj.Rotation.Q],
            **{
                f"Force{suffix}": _finite(
                    getattr(obj, f"Force{suffix}").getValueAs("N").Value
                )
                for _axis, suffix in _AXES
            },
            **{
                f"Moment{suffix}": _finite(
                    getattr(obj, f"Moment{suffix}").getValueAs("N*mm").Value
                )
                for _axis, suffix in _AXES
            },
            **{
                f"TranslationalMode{suffix}": str(
                    getattr(obj, f"TranslationalMode{suffix}")
                )
                for _axis, suffix in _AXES
            },
            **{
                f"RotationalMode{suffix}": str(
                    getattr(obj, f"RotationalMode{suffix}")
                )
                for _axis, suffix in _AXES
            },
            "EnableAmplitude": bool(obj.EnableAmplitude),
            "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
        }
    if kind == "displacement":
        values: dict[str, Any] = {
            "useFlowSurfaceForce": bool(obj.useFlowSurfaceForce),
            "EnableAmplitude": bool(obj.EnableAmplitude),
            "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
        }
        for axis, suffix in _AXES:
            values[f"{axis}Free"] = bool(getattr(obj, f"{axis}Free"))
            values[f"{axis}Displacement"] = _finite(
                getattr(obj, f"{axis}Displacement").getValueAs("mm").Value
            )
            values[f"has{suffix}Formula"] = bool(
                getattr(obj, f"has{suffix}Formula")
            )
            values[f"{axis}DisplacementFormula"] = str(
                getattr(obj, f"{axis}DisplacementFormula")
            )
            values[f"rot{axis}Free"] = bool(getattr(obj, f"rot{axis}Free"))
            values[f"{axis}Rotation"] = _finite(
                getattr(obj, f"{axis}Rotation").getValueAs("deg").Value
            )
        return values
    return {
        "NormalStiffness": _finite(obj.NormalStiffness.getValueAs("N/m").Value),
        "TangentialStiffness": _finite(
            obj.TangentialStiffness.getValueAs("N/m").Value
        ),
        "ElmerStiffness": str(obj.ElmerStiffness),
    }


def support_condition_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM mechanical support condition is no longer live.")
    kind = support_condition_kind(obj)
    references, exact_references = _references(obj)
    definition = _definition(obj, kind)
    result = {
        **concise_object(obj),
        "condition_kind": kind,
        "references": references,
        "definition": definition,
    }
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "kind": kind,
            "references": exact_references,
            "native_values": _native_values(obj, kind),
        }
    )
    return result


def support_condition_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return support_condition_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
