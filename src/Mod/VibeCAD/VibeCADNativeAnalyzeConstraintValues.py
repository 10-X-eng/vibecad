# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong value preparation for FEM electromagnetic constraints."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


_AXES = (("x", "1"), ("y", "2"), ("z", "3"))
_ALL_REFERENCE_KINDS = frozenset({"Solid", "Face", "Edge", "Vertex"})


@dataclass(frozen=True, slots=True)
class PreparedConstraintValues:
    kind: str
    native: dict[str, Any]
    definition: dict[str, Any]
    allowed_reference_kinds: frozenset[str]
    allow_mixed_reference_kinds: bool
    allow_empty_references: bool

    def normalized(self) -> dict[str, Any]:
        return dict(self.definition)


def _object(value: Any, *, field: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(f"{field} must be one object.")
    result = dict(value)
    if set(result) != allowed:
        missing = sorted(allowed - set(result))
        extra = sorted(set(result) - allowed)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(f"{field} has invalid fields: {'; '.join(details)}.")
    return result


def _finite(value: Any, *, field: str, limit: float = 1.0e30) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > limit:
        raise NativeAnalyzeError(f"{field} must be finite and within +/-{limit:g}.")
    return number


def _boolean(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAnalyzeError(f"{field} must be true or false.")
    return value


def _complex(
    value: Any, *, field: str, real_name: str, imaginary_name: str
) -> dict[str, float]:
    raw = _object(value, field=field, allowed=frozenset({real_name, imaginary_name}))
    return {
        real_name: _finite(raw[real_name], field=f"{field}.{real_name}"),
        imaginary_name: _finite(raw[imaginary_name], field=f"{field}.{imaginary_name}"),
    }


def _components(
    value: Any,
    *,
    field: str,
    real_name: str,
    imaginary_name: str,
) -> dict[str, dict[str, float]]:
    if not isinstance(value, Mapping) or not value or not set(value) <= {"x", "y", "z"}:
        raise NativeAnalyzeError(
            f"{field} must contain one or more of x, y, and z only."
        )
    return {
        axis: _complex(
            raw,
            field=f"{field}.{axis}",
            real_name=real_name,
            imaginary_name=imaginary_name,
        )
        for axis, raw in value.items()
    }


def _blank_electromagnetic() -> dict[str, Any]:
    result: dict[str, Any] = {
        "Potential": "0 V",
        "PotentialEnabled": False,
        "AV_re": "0 V",
        "AV_im": "0 V",
        "EnableAV": False,
        "ElectricFluxDensity": "0 C/m^2",
        "BoundaryCondition": "Dirichlet",
        "PotentialConstant": False,
        "FarField": False,
        "ElectricForcecalculation": False,
        "CapacitanceBody": 1,
        "CapacitanceBodyEnabled": False,
    }
    for _axis, suffix in _AXES:
        result[f"AV_re_{suffix}"] = "0 Wb/m"
        result[f"AV_im_{suffix}"] = "0 Wb/m"
        result[f"EnableAV_{suffix}"] = False
        result[f"Magnetic_re_{suffix}"] = "0 Wb/m^2"
        result[f"Magnetic_im_{suffix}"] = "0 Wb/m^2"
        result[f"EnableMagnetic_{suffix}"] = False
    return result


def prepare_electromagnetic(value: Any) -> PreparedConstraintValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(
            "constraint must be one electromagnetic boundary object."
        )
    raw = dict(value)
    mode = str(raw.get("kind", "") or "")
    common = {"kind", "capacitance_body"}
    native = _blank_electromagnetic()
    if mode == "dirichlet":
        allowed = common | {
            "electric_potential_v",
            "scalar_potential",
            "vector_potential",
            "potential_constant",
            "far_field",
        }
        required = {"kind", "potential_constant", "far_field"}
        if not required <= set(raw) or not set(raw) <= allowed:
            raise NativeAnalyzeError(
                "A Dirichlet constraint requires kind, potential_constant, and far_field; "
                "only electric_potential_v, scalar_potential, vector_potential, and "
                "capacitance_body are optional."
            )
        definition: dict[str, Any] = {
            "kind": mode,
            "potential_constant": _boolean(
                raw["potential_constant"], field="constraint.potential_constant"
            ),
            "far_field": _boolean(raw["far_field"], field="constraint.far_field"),
        }
        native["PotentialConstant"] = definition["potential_constant"]
        native["FarField"] = definition["far_field"]
        if "electric_potential_v" in raw:
            potential = _finite(
                raw["electric_potential_v"], field="constraint.electric_potential_v"
            )
            definition["electric_potential_v"] = potential
            native["Potential"] = f"{potential} V"
            native["PotentialEnabled"] = True
        if "scalar_potential" in raw:
            scalar = _complex(
                raw["scalar_potential"],
                field="constraint.scalar_potential",
                real_name="real_v",
                imaginary_name="imaginary_v",
            )
            definition["scalar_potential"] = scalar
            native["AV_re"] = f"{scalar['real_v']} V"
            native["AV_im"] = f"{scalar['imaginary_v']} V"
            native["EnableAV"] = True
        if "vector_potential" in raw:
            components = _components(
                raw["vector_potential"],
                field="constraint.vector_potential",
                real_name="real_wb_m",
                imaginary_name="imaginary_wb_m",
            )
            definition["vector_potential"] = components
            for axis, suffix in _AXES:
                if axis not in components:
                    continue
                component = components[axis]
                native[f"AV_re_{suffix}"] = f"{component['real_wb_m']} Wb/m"
                native[f"AV_im_{suffix}"] = f"{component['imaginary_wb_m']} Wb/m"
                native[f"EnableAV_{suffix}"] = True
        meaningful = (
            set(raw)
            & {
                "electric_potential_v",
                "scalar_potential",
                "vector_potential",
                "capacitance_body",
            }
            or definition["potential_constant"]
            or definition["far_field"]
        )
        if not meaningful:
            raise NativeAnalyzeError(
                "A Dirichlet constraint must enable at least one potential, field option, or capacitance body."
            )
    elif mode == "neumann":
        allowed = common | {"electric_flux_density_c_m2", "magnetic_flux_density"}
        required = {"kind", "electric_flux_density_c_m2"}
        if not required <= set(raw) or not set(raw) <= allowed:
            raise NativeAnalyzeError(
                "A Neumann constraint requires kind and electric_flux_density_c_m2; "
                "only magnetic_flux_density and capacitance_body are optional."
            )
        flux = _finite(
            raw["electric_flux_density_c_m2"],
            field="constraint.electric_flux_density_c_m2",
        )
        definition = {"kind": mode, "electric_flux_density_c_m2": flux}
        native["BoundaryCondition"] = "Neumann"
        native["ElectricFluxDensity"] = f"{flux} C/m^2"
        if "magnetic_flux_density" in raw:
            components = _components(
                raw["magnetic_flux_density"],
                field="constraint.magnetic_flux_density",
                real_name="real_wb_m2",
                imaginary_name="imaginary_wb_m2",
            )
            definition["magnetic_flux_density"] = components
            for axis, suffix in _AXES:
                if axis not in components:
                    continue
                component = components[axis]
                native[f"Magnetic_re_{suffix}"] = f"{component['real_wb_m2']} Wb/m^2"
                native[f"Magnetic_im_{suffix}"] = (
                    f"{component['imaginary_wb_m2']} Wb/m^2"
                )
                native[f"EnableMagnetic_{suffix}"] = True
    else:
        raise NativeAnalyzeError("constraint.kind must be dirichlet or neumann.")
    if "capacitance_body" in raw:
        body = raw["capacitance_body"]
        if type(body) is not int or not 1 <= body <= 1_000_000:
            raise NativeAnalyzeError(
                "constraint.capacitance_body must be an integer from 1 to 1000000."
            )
        definition["capacitance_body"] = body
        native["CapacitanceBody"] = body
        native["CapacitanceBodyEnabled"] = True
    return PreparedConstraintValues(
        "electromagnetic", native, definition, _ALL_REFERENCE_KINDS, True, False
    )


def prepare_current_density(value: Any) -> PreparedConstraintValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("constraint must be one current-density mode object.")
    raw = dict(value)
    mode = str(raw.get("kind", "") or "")
    native: dict[str, Any] = {"Mode": "Custom"}
    for _axis, suffix in _AXES:
        native[f"CurrentDensity_re_{suffix}"] = "0 A/m^2"
        native[f"CurrentDensity_im_{suffix}"] = "0 A/m^2"
        native[f"EnableCurrentDensity_{suffix}"] = False
    native["NormalCurrentDensity_re"] = "0 A/m^2"
    native["NormalCurrentDensity_im"] = "0 A/m^2"
    if mode == "cartesian":
        raw = _object(
            raw, field="constraint", allowed=frozenset({"kind", "components"})
        )
        components = _components(
            raw["components"],
            field="constraint.components",
            real_name="real_a_m2",
            imaginary_name="imaginary_a_m2",
        )
        definition = {"kind": mode, "components": components}
        for axis, suffix in _AXES:
            if axis not in components:
                continue
            component = components[axis]
            native[f"CurrentDensity_re_{suffix}"] = f"{component['real_a_m2']} A/m^2"
            native[f"CurrentDensity_im_{suffix}"] = (
                f"{component['imaginary_a_m2']} A/m^2"
            )
            native[f"EnableCurrentDensity_{suffix}"] = True
        allow_empty = True
    elif mode == "normal":
        raw = _object(
            raw,
            field="constraint",
            allowed=frozenset({"kind", "real_a_m2", "imaginary_a_m2"}),
        )
        real = _finite(raw["real_a_m2"], field="constraint.real_a_m2")
        imaginary = _finite(raw["imaginary_a_m2"], field="constraint.imaginary_a_m2")
        definition = {"kind": mode, "real_a_m2": real, "imaginary_a_m2": imaginary}
        native["Mode"] = "Normal"
        native["NormalCurrentDensity_re"] = f"{real} A/m^2"
        native["NormalCurrentDensity_im"] = f"{imaginary} A/m^2"
        allow_empty = False
    else:
        raise NativeAnalyzeError("constraint.kind must be cartesian or normal.")
    return PreparedConstraintValues(
        "current_density",
        native,
        definition,
        frozenset({"Solid", "Face"}),
        True,
        allow_empty,
    )


def prepare_magnetization(value: Any) -> PreparedConstraintValues:
    raw = _object(value, field="constraint", allowed=frozenset({"components"}))
    components = _components(
        raw["components"],
        field="constraint.components",
        real_name="real_a_m",
        imaginary_name="imaginary_a_m",
    )
    native: dict[str, Any] = {}
    for axis, suffix in _AXES:
        component = components.get(axis)
        native[f"Magnetization_re_{suffix}"] = (
            f"{component['real_a_m'] if component else 0} A/m"
        )
        native[f"Magnetization_im_{suffix}"] = (
            f"{component['imaginary_a_m'] if component else 0} A/m"
        )
        native[f"EnableMagnetization_{suffix}"] = component is not None
    return PreparedConstraintValues(
        "magnetization",
        native,
        {"components": components},
        frozenset({"Solid", "Face"}),
        True,
        True,
    )


def prepare_electric_charge_density(value: Any) -> PreparedConstraintValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(
            "constraint must be one electric-charge-density mode object."
        )
    raw = dict(value)
    mode = str(raw.get("kind", "") or "")
    native: dict[str, Any] = {
        "SourceChargeDensity": "0 C/m^3",
        "InterfaceChargeDensity": "0 C/m^2",
        "TotalCharge": "0 C",
        "Concentrated": False,
    }
    if mode == "interface":
        raw = _object(
            raw,
            field="constraint",
            allowed=frozenset({"kind", "surface_charge_density_c_m2"}),
        )
        value = _finite(
            raw["surface_charge_density_c_m2"],
            field="constraint.surface_charge_density_c_m2",
        )
        native.update(Mode="Interface", InterfaceChargeDensity=f"{value} C/m^2")
        definition = {"kind": mode, "surface_charge_density_c_m2": value}
        allowed = frozenset({"Face", "Edge"})
    elif mode == "source":
        raw = _object(
            raw,
            field="constraint",
            allowed=frozenset({"kind", "volume_charge_density_c_m3"}),
        )
        value = _finite(
            raw["volume_charge_density_c_m3"],
            field="constraint.volume_charge_density_c_m3",
        )
        native.update(Mode="Source", SourceChargeDensity=f"{value} C/m^3")
        definition = {"kind": mode, "volume_charge_density_c_m3": value}
        allowed = frozenset({"Solid", "Face"})
    elif mode == "total_interface":
        raw = _object(
            raw, field="constraint", allowed=frozenset({"kind", "total_charge_c"})
        )
        value = _finite(raw["total_charge_c"], field="constraint.total_charge_c")
        native.update(Mode="Total Interface", TotalCharge=f"{value} C")
        definition = {"kind": mode, "total_charge_c": value}
        allowed = frozenset({"Face", "Edge"})
    elif mode == "total_source":
        raw = _object(
            raw,
            field="constraint",
            allowed=frozenset({"kind", "total_charge_c", "concentrated"}),
        )
        value = _finite(raw["total_charge_c"], field="constraint.total_charge_c")
        concentrated = _boolean(raw["concentrated"], field="constraint.concentrated")
        native.update(
            Mode="Total Source", TotalCharge=f"{value} C", Concentrated=concentrated
        )
        definition = {
            "kind": mode,
            "total_charge_c": value,
            "concentrated": concentrated,
        }
        allowed = _ALL_REFERENCE_KINDS if concentrated else frozenset({"Solid", "Face"})
    else:
        raise NativeAnalyzeError(
            "constraint.kind must be interface, source, total_interface, or total_source."
        )
    return PreparedConstraintValues(
        "electric_charge_density", native, definition, allowed, False, False
    )


def prepare_constraint_values(kind: str, value: Any) -> PreparedConstraintValues:
    if kind == "electromagnetic":
        return prepare_electromagnetic(value)
    if kind == "current_density":
        return prepare_current_density(value)
    if kind == "magnetization":
        return prepare_magnetization(value)
    if kind == "electric_charge_density":
        return prepare_electric_charge_density(value)
    raise NativeAnalyzeError(
        "The requested electromagnetic constraint kind is unavailable."
    )


def apply_constraint_values(obj: Any, prepared: PreparedConstraintValues) -> None:
    if not isinstance(prepared, PreparedConstraintValues):
        raise TypeError("prepared must be PreparedConstraintValues")
    values = dict(prepared.native)
    if "Concentrated" in values and "Mode" in values:
        desired_mode = str(values["Mode"])
        concentrated = bool(values.pop("Concentrated"))
        if str(obj.Mode) == "Total Source" and desired_mode != "Total Source":
            obj.Concentrated = concentrated
        obj.Mode = desired_mode
        values.pop("Mode")
        if desired_mode == "Total Source":
            obj.Concentrated = concentrated
    elif "Mode" in values:
        obj.Mode = values.pop("Mode")
    if "BoundaryCondition" in values:
        obj.BoundaryCondition = values.pop("BoundaryCondition")
    for name, value in values.items():
        setattr(obj, name, value)
