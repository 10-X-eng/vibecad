# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact normalized state for paired FEM mechanical connections."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


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
        raise NativeAnalyzeError("A FEM connection contains a non-finite value.")
    return float(format(number, ".15g"))


def connection_kind(obj: Any) -> str:
    try:
        if obj.isDerivedFrom("Fem::ConstraintContact"):
            return "contact"
    except Exception:
        pass
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    if proxy_type == "Fem::ConstraintTie":
        return "tie"
    raise NativeAnalyzeError(
        "The exact target is not a supported paired FEM connection.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _endpoint(raw: Any) -> tuple[dict[str, str], dict[str, Any]]:
    if not isinstance(raw, tuple) or len(raw) != 2:
        raise NativeAnalyzeError("A FEM connection contains a malformed endpoint.")
    source, names = raw
    names = (names,) if isinstance(names, str) else tuple(names or ())
    if len(names) != 1:
        raise NativeAnalyzeError(
            "Each FEM connection endpoint must contain exactly one subelement."
        )
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


def _endpoints(obj: Any) -> tuple[dict[str, str], dict[str, str], list[dict[str, Any]]]:
    references = tuple(getattr(obj, "References", ()) or ())
    if len(references) != 2:
        raise NativeAnalyzeError(
            "A paired FEM connection must contain exactly one slave and one master endpoint."
        )
    slave, slave_exact = _endpoint(references[0])
    master, master_exact = _endpoint(references[1])
    return slave, master, [slave_exact, master_exact]


def _contact_definition(obj: Any) -> dict[str, Any]:
    if bool(obj.Friction):
        friction = {
            "kind": "coulomb",
            "coefficient": _finite(obj.FrictionCoefficient),
            "stick_stiffness_gpa_per_m": _finite(
                obj.StickSlope.getValueAs("GPa/m").Value
            ),
        }
    else:
        friction = {"kind": "frictionless"}
    return {
        "contact_stiffness_gpa_per_m": _finite(
            obj.Slope.getValueAs("GPa/m").Value
        ),
        "clearance_adjustment_mm": _finite(obj.Adjust.getValueAs("mm").Value),
        "friction": friction,
    }


def _tie_definition(obj: Any) -> dict[str, Any]:
    return {
        "tolerance_mm": _finite(obj.Tolerance.getValueAs("mm").Value),
        "adjust": bool(obj.Adjust),
    }


def _placement(value: Any) -> dict[str, Any]:
    return {
        "base": [_finite(value.Base[index]) for index in range(3)],
        "rotation_quaternion": [_finite(component) for component in value.Rotation.Q],
    }


def _native_values(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "contact":
        return {
            "SlopeGPaPerM": _finite(obj.Slope.getValueAs("GPa/m").Value),
            "AdjustMM": _finite(obj.Adjust.getValueAs("mm").Value),
            "Friction": bool(obj.Friction),
            "FrictionCoefficient": _finite(obj.FrictionCoefficient),
            "StickSlopeGPaPerM": _finite(
                obj.StickSlope.getValueAs("GPa/m").Value
            ),
            "SurfaceBehavior": str(obj.SurfaceBehavior),
            "EnableThermalContact": bool(obj.EnableThermalContact),
            "ThermalContactConductance": [
                str(value) for value in obj.ThermalContactConductance
            ],
        }
    return {
        "ToleranceMM": _finite(obj.Tolerance.getValueAs("mm").Value),
        "Adjust": bool(obj.Adjust),
        "CyclicSymmetry": bool(obj.CyclicSymmetry),
        "SymmetryAxis": _placement(obj.SymmetryAxis),
        "Sectors": int(obj.Sectors),
        "ConnectedSectors": int(obj.ConnectedSectors),
    }


def connection_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM connection is no longer live.")
    kind = connection_kind(obj)
    slave, master, exact_endpoints = _endpoints(obj)
    definition = (
        _contact_definition(obj) if kind == "contact" else _tie_definition(obj)
    )
    result = {
        **concise_object(obj),
        "connection_kind": kind,
        "slave": slave,
        "master": master,
        "definition": definition,
    }
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "kind": kind,
            "endpoints": exact_endpoints,
            "native_values": _native_values(obj, kind),
        }
    )
    return result


def connection_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return connection_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
