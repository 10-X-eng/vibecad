# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact normalized state for FEM mechanical loads."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


_PROXY_KINDS = {
    "Fem::ConstraintCentrif": "centrifugal",
    "Fem::ConstraintSelfWeight": "gravity",
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
        raise NativeAnalyzeError("A FEM mechanical load contains a non-finite value.")
    return float(format(number, ".15g"))


def _vector(value: Any) -> dict[str, float]:
    return {axis: _finite(getattr(value, axis)) for axis in ("x", "y", "z")}


def _reference(raw: Any) -> tuple[dict[str, str], dict[str, Any]]:
    if not isinstance(raw, tuple) or len(raw) != 2:
        raise NativeAnalyzeError("A FEM load contains a malformed exact reference.")
    source, raw_names = raw
    names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
    if len(names) != 1:
        raise NativeAnalyzeError("A FEM load reference must name exactly one subelement.")
    name = str(names[0])
    visible = {"object_name": str(source.Name), "subelement": name}
    try:
        source_sha = mesh_object_state(source)["state_sha256"]
    except Exception:
        source_sha = None
    exact = {
        **visible,
        "object_id": int(getattr(source, "ID", -1)),
        "source_state_sha256": source_sha,
    }
    return visible, exact


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
            source_sha = mesh_object_state(source)["state_sha256"]
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


def load_kind(obj: Any) -> str:
    for type_id, kind in (
        ("Fem::ConstraintForce", "force"),
        ("Fem::ConstraintPressure", "pressure"),
    ):
        try:
            if obj.isDerivedFrom(type_id):
                return kind
        except Exception:
            continue
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    kind = _PROXY_KINDS.get(proxy_type)
    if kind is not None:
        return kind
    raise NativeAnalyzeError(
        "The exact target is not a supported FEM mechanical load.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _link_sub(value: Any) -> tuple[Any, tuple[str, ...]] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 2 or value[0] is None:
        raise NativeAnalyzeError("A FEM load contains a malformed direction reference.")
    names = (value[1],) if isinstance(value[1], str) else tuple(value[1] or ())
    if len(names) > 1:
        raise NativeAnalyzeError("A force direction must contain at most one subelement.")
    return value[0], tuple(str(name) for name in names)


def _direction_definition(obj: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    link = _link_sub(obj.Direction)
    reversed_value = bool(obj.Reversed)
    if link is None:
        return {"kind": "normal", "reversed": reversed_value}, None
    source, names = link
    name = names[0] if names else ""
    visible, exact = _reference((source, (name,)))
    return {
        "kind": "reference",
        **visible,
        "reversed": reversed_value,
    }, exact


def _axis_definition(obj: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    values = tuple(obj.RotationAxis or ())
    if len(values) != 1:
        raise NativeAnalyzeError("A centrifugal load must contain exactly one axis.")
    return _reference(values[0])


def _definition(obj: Any, kind: str, references: list[dict[str, Any]]) -> tuple[dict[str, Any], Any]:
    if kind == "force":
        direction, exact_direction = _direction_definition(obj)
        return {
            "force_n": _finite(obj.Force.getValueAs("N").Value),
            "direction": direction,
        }, exact_direction
    if kind == "pressure":
        return {
            "pressure_pa": _finite(obj.Pressure.getValueAs("Pa").Value),
            "reversed": bool(obj.Reversed),
        }, None
    if kind == "centrifugal":
        axis, exact_axis = _axis_definition(obj)
        return {
            "rotation_frequency_hz": _finite(
                obj.RotationFrequency.getValueAs("1/s").Value
            ),
            "axis": axis,
            "scope": (
                {"kind": "selected_geometry", "references": references}
                if references
                else {"kind": "all_bodies"}
            ),
        }, exact_axis
    return {
        "acceleration_m_s2": _finite(
            obj.GravityAcceleration.getValueAs("m/s^2").Value
        ),
        "direction": _vector(obj.GravityDirection),
    }, None


def _native_values(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "force":
        return {
            "ForceN": _finite(obj.Force.getValueAs("N").Value),
            "DirectionVector": list(_vector(obj.DirectionVector).values()),
            "Reversed": bool(obj.Reversed),
            "EnableAmplitude": bool(obj.EnableAmplitude),
            "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
        }
    if kind == "pressure":
        return {
            "PressurePa": _finite(obj.Pressure.getValueAs("Pa").Value),
            "Reversed": bool(obj.Reversed),
            "EnableAmplitude": bool(obj.EnableAmplitude),
            "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
        }
    if kind == "centrifugal":
        return {
            "RotationFrequencyHz": _finite(
                obj.RotationFrequency.getValueAs("1/s").Value
            )
        }
    return {
        "GravityAccelerationMS2": _finite(
            obj.GravityAcceleration.getValueAs("m/s^2").Value
        ),
        "GravityDirection": list(_vector(obj.GravityDirection).values()),
    }


def load_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM mechanical load is no longer live.")
    kind = load_kind(obj)
    references, exact_references = _references(obj)
    definition, auxiliary_reference = _definition(obj, kind, references)
    result = {
        **concise_object(obj),
        "load_kind": kind,
        "references": references,
        "definition": definition,
    }
    if kind == "force":
        result["resolved_direction"] = _vector(obj.DirectionVector)
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "kind": kind,
            "references": exact_references,
            "auxiliary_reference": auxiliary_reference,
            "native_values": _native_values(obj, kind),
        }
    )
    return result


def load_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return load_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
