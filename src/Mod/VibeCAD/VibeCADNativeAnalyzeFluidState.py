# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact normalized state for live FEM fluid constraints."""

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
    "Fem::ConstraintInitialFlowVelocity": "initial_flow_velocity",
    "Fem::ConstraintInitialPressure": "initial_pressure",
    "Fem::ConstraintFlowVelocity": "flow_velocity",
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
        raise NativeAnalyzeError("A FEM fluid constraint contains a non-finite value.")
    return float(format(number, ".15g"))


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


def _velocity_components(obj: Any) -> dict[str, dict[str, Any]]:
    result = {}
    for axis, suffix in _AXES:
        if bool(getattr(obj, f"Velocity{suffix}Unspecified")):
            continue
        if bool(getattr(obj, f"Velocity{suffix}HasFormula")):
            result[axis] = {
                "kind": "formula",
                "expression": str(getattr(obj, f"Velocity{suffix}Formula")),
            }
        else:
            result[axis] = {
                "kind": "value",
                "value_m_s": _finite(
                    getattr(obj, f"Velocity{suffix}").getValueAs("m/s").Value
                ),
            }
    return result


def fluid_constraint_kind(obj: Any) -> str:
    try:
        if obj.isDerivedFrom("Fem::ConstraintFluidBoundary"):
            return "fluid_boundary"
    except Exception:
        pass
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    kind = _KINDS.get(proxy_type)
    if kind is None:
        raise NativeAnalyzeError(
            "The exact target is not a supported FEM fluid constraint.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    return kind


def _definition(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "fluid_boundary":
        from VibeCADNativeAnalyzeFluidValues import fluid_boundary_definition

        return fluid_boundary_definition(obj)
    if kind == "initial_pressure":
        return {"pressure_pa": _finite(obj.Pressure.getValueAs("Pa").Value)}
    result: dict[str, Any] = {"components": _velocity_components(obj)}
    if kind == "flow_velocity":
        result["normal_to_boundary"] = bool(obj.NormalToBoundary)
    return result


def _native_values(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "fluid_boundary":
        direction = getattr(obj, "Direction", None)
        if isinstance(direction, tuple) and len(direction) == 2:
            linked, subelements = direction
            subelements = (
                (subelements,)
                if isinstance(subelements, str)
                else tuple(subelements or ())
            )
            direction_state = {
                "object_name": str(getattr(linked, "Name", "") or ""),
                "subelements": [str(item) for item in subelements],
            }
        else:
            direction_state = None
        return {
            "BoundaryType": str(obj.BoundaryType),
            "Subtype": str(obj.Subtype),
            "BoundaryValue": _finite(obj.BoundaryValue),
            "Direction": direction_state,
            "Reversed": bool(obj.Reversed),
            "TurbulenceSpecification": str(obj.TurbulenceSpecification),
            "TurbulentIntensityValue": _finite(obj.TurbulentIntensityValue),
            "TurbulentLengthValue": _finite(obj.TurbulentLengthValue),
            "ThermalBoundaryType": str(obj.ThermalBoundaryType),
            "TemperatureValue": _finite(obj.TemperatureValue),
            "HeatFluxValue": _finite(obj.HeatFluxValue),
            "HTCoeffValue": _finite(obj.HTCoeffValue),
        }
    if kind == "initial_pressure":
        return {"Pressure": _finite(obj.Pressure.getValueAs("Pa").Value)}
    result: dict[str, Any] = {}
    for _axis, suffix in _AXES:
        result[f"Velocity{suffix}"] = _finite(
            getattr(obj, f"Velocity{suffix}").getValueAs("m/s").Value
        )
        result[f"Velocity{suffix}Formula"] = str(
            getattr(obj, f"Velocity{suffix}Formula")
        )
        result[f"Velocity{suffix}Unspecified"] = bool(
            getattr(obj, f"Velocity{suffix}Unspecified")
        )
        result[f"Velocity{suffix}HasFormula"] = bool(
            getattr(obj, f"Velocity{suffix}HasFormula")
        )
    if kind == "flow_velocity":
        result["NormalToBoundary"] = bool(obj.NormalToBoundary)
    return result


def fluid_constraint_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM fluid constraint is no longer live.")
    kind = fluid_constraint_kind(obj)
    references, exact_references = _references(obj)
    definition = _definition(obj, kind)
    result = {
        **concise_object(obj),
        "constraint_kind": kind,
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


def fluid_constraint_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return fluid_constraint_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
