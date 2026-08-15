# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact normalized state for live FEM electromagnetic constraints."""

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
    "Fem::ConstraintElectromagnetic": "electromagnetic",
    "Fem::ConstraintCurrentDensity": "current_density",
    "Fem::ConstraintMagnetization": "magnetization",
    "Fem::ConstraintElectricChargeDensity": "electric_charge_density",
}
_AXES = (("x", "1"), ("y", "2"), ("z", "3"))


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
            "An electromagnetic constraint contains a non-finite value."
        )
    return float(format(number, ".15g"))


def _quantity(obj: Any, name: str, unit: str) -> float:
    return _finite(getattr(obj, name).getValueAs(unit).Value)


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


def _complex_components(
    obj: Any,
    *,
    enable_stem: str,
    value_stem: str,
    unit: str,
    output_real: str,
    output_imaginary: str,
) -> dict[str, dict[str, float]]:
    result = {}
    for axis, suffix in _AXES:
        if bool(getattr(obj, f"{enable_stem}_{suffix}")):
            result[axis] = {
                output_real: _quantity(obj, f"{value_stem}_re_{suffix}", unit),
                output_imaginary: _quantity(obj, f"{value_stem}_im_{suffix}", unit),
            }
    return result


def _electromagnetic_definition(obj: Any) -> dict[str, Any]:
    capacitance = int(obj.CapacitanceBody) if bool(obj.CapacitanceBodyEnabled) else None
    if str(obj.BoundaryCondition) == "Dirichlet":
        boundary: dict[str, Any] = {
            "kind": "dirichlet",
            "potential_constant": bool(obj.PotentialConstant),
            "far_field": bool(obj.FarField),
        }
        if bool(obj.PotentialEnabled):
            boundary["electric_potential_v"] = _quantity(obj, "Potential", "V")
        if bool(obj.EnableAV):
            boundary["scalar_potential"] = {
                "real_v": _quantity(obj, "AV_re", "V"),
                "imaginary_v": _quantity(obj, "AV_im", "V"),
            }
        components = _complex_components(
            obj,
            enable_stem="EnableAV",
            value_stem="AV",
            unit="Wb/m",
            output_real="real_wb_m",
            output_imaginary="imaginary_wb_m",
        )
        if components:
            boundary["vector_potential"] = components
    elif str(obj.BoundaryCondition) == "Neumann":
        boundary = {
            "kind": "neumann",
            "electric_flux_density_c_m2": _quantity(
                obj, "ElectricFluxDensity", "C/m^2"
            ),
        }
        components = _complex_components(
            obj,
            enable_stem="EnableMagnetic",
            value_stem="Magnetic",
            unit="Wb/m^2",
            output_real="real_wb_m2",
            output_imaginary="imaginary_wb_m2",
        )
        if components:
            boundary["magnetic_flux_density"] = components
    else:
        raise NativeAnalyzeError(
            f"Unsupported electromagnetic boundary condition {obj.BoundaryCondition!r}."
        )
    if capacitance is not None:
        boundary["capacitance_body"] = capacitance
    return boundary


def _current_density_definition(obj: Any) -> dict[str, Any]:
    if str(obj.Mode) == "Normal":
        return {
            "kind": "normal",
            "real_a_m2": _quantity(obj, "NormalCurrentDensity_re", "A/m^2"),
            "imaginary_a_m2": _quantity(obj, "NormalCurrentDensity_im", "A/m^2"),
        }
    if str(obj.Mode) != "Custom":
        raise NativeAnalyzeError(f"Unsupported current-density mode {obj.Mode!r}.")
    return {
        "kind": "cartesian",
        "components": _complex_components(
            obj,
            enable_stem="EnableCurrentDensity",
            value_stem="CurrentDensity",
            unit="A/m^2",
            output_real="real_a_m2",
            output_imaginary="imaginary_a_m2",
        ),
    }


def _magnetization_definition(obj: Any) -> dict[str, Any]:
    return {
        "components": _complex_components(
            obj,
            enable_stem="EnableMagnetization",
            value_stem="Magnetization",
            unit="A/m",
            output_real="real_a_m",
            output_imaginary="imaginary_a_m",
        )
    }


def _charge_definition(obj: Any) -> dict[str, Any]:
    mode = str(obj.Mode)
    if mode == "Interface":
        return {
            "kind": "interface",
            "surface_charge_density_c_m2": _quantity(
                obj, "InterfaceChargeDensity", "C/m^2"
            ),
        }
    if mode == "Source":
        return {
            "kind": "source",
            "volume_charge_density_c_m3": _quantity(
                obj, "SourceChargeDensity", "C/m^3"
            ),
        }
    if mode == "Total Interface":
        return {
            "kind": "total_interface",
            "total_charge_c": _quantity(obj, "TotalCharge", "C"),
        }
    if mode == "Total Source":
        return {
            "kind": "total_source",
            "total_charge_c": _quantity(obj, "TotalCharge", "C"),
            "concentrated": bool(obj.Concentrated),
        }
    raise NativeAnalyzeError(f"Unsupported electric-charge-density mode {mode!r}.")


def electromagnetic_constraint_kind(obj: Any) -> str:
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    kind = _KINDS.get(proxy_type)
    if kind is None:
        raise NativeAnalyzeError(
            "The exact target is not a supported FEM electromagnetic constraint.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    return kind


def _definition(obj: Any, kind: str) -> dict[str, Any]:
    if kind == "electromagnetic":
        return _electromagnetic_definition(obj)
    if kind == "current_density":
        return _current_density_definition(obj)
    if kind == "magnetization":
        return _magnetization_definition(obj)
    return _charge_definition(obj)


def _native_values(obj: Any, kind: str) -> dict[str, Any]:
    quantity_names: tuple[tuple[str, str], ...]
    bool_names: tuple[str, ...]
    string_names: tuple[str, ...]
    integer_names: tuple[str, ...] = ()
    if kind == "electromagnetic":
        quantity_names = (
            ("Potential", "V"),
            ("AV_re", "V"),
            ("AV_im", "V"),
            *((f"AV_re_{i}", "Wb/m") for i in range(1, 4)),
            *((f"AV_im_{i}", "Wb/m") for i in range(1, 4)),
            ("ElectricFluxDensity", "C/m^2"),
            *((f"Magnetic_re_{i}", "Wb/m^2") for i in range(1, 4)),
            *((f"Magnetic_im_{i}", "Wb/m^2") for i in range(1, 4)),
        )
        bool_names = (
            "PotentialEnabled",
            "EnableAV",
            "EnableAV_1",
            "EnableAV_2",
            "EnableAV_3",
            "PotentialConstant",
            "FarField",
            "ElectricForcecalculation",
            "CapacitanceBodyEnabled",
            "EnableMagnetic_1",
            "EnableMagnetic_2",
            "EnableMagnetic_3",
        )
        string_names = ("BoundaryCondition",)
        integer_names = ("CapacitanceBody",)
    elif kind == "current_density":
        quantity_names = (
            *((f"CurrentDensity_re_{i}", "A/m^2") for i in range(1, 4)),
            *((f"CurrentDensity_im_{i}", "A/m^2") for i in range(1, 4)),
            ("NormalCurrentDensity_re", "A/m^2"),
            ("NormalCurrentDensity_im", "A/m^2"),
        )
        bool_names = tuple(f"EnableCurrentDensity_{i}" for i in range(1, 4))
        string_names = ("Mode",)
    elif kind == "magnetization":
        quantity_names = (
            *((f"Magnetization_re_{i}", "A/m") for i in range(1, 4)),
            *((f"Magnetization_im_{i}", "A/m") for i in range(1, 4)),
        )
        bool_names = tuple(f"EnableMagnetization_{i}" for i in range(1, 4))
        string_names = ()
    else:
        quantity_names = (
            ("SourceChargeDensity", "C/m^3"),
            ("InterfaceChargeDensity", "C/m^2"),
            ("TotalCharge", "C"),
        )
        bool_names = ("Concentrated",)
        string_names = ("Mode",)
    values = {name: _quantity(obj, name, unit) for name, unit in quantity_names}
    values.update({name: bool(getattr(obj, name)) for name in bool_names})
    values.update({name: str(getattr(obj, name)) for name in string_names})
    values.update({name: int(getattr(obj, name)) for name in integer_names})
    return values


def electromagnetic_constraint_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError(
            "The FEM electromagnetic constraint is no longer live."
        )
    kind = electromagnetic_constraint_kind(obj)
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


def electromagnetic_constraint_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return electromagnetic_constraint_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
