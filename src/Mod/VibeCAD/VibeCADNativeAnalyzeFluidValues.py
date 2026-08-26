# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong value preparation for FEM fluid constraints."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


_AXES = (("x", "X"), ("y", "Y"), ("z", "Z"))


@dataclass(frozen=True, slots=True)
class PreparedFluidValues:
    kind: str
    native: dict[str, Any]
    definition: dict[str, Any]
    allowed_reference_kinds: frozenset[str]
    allow_mixed_reference_kinds: bool
    allow_empty_references: bool

    def normalized(self) -> dict[str, Any]:
        return dict(self.definition)


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > 1.0e30:
        raise NativeAnalyzeError(f"{field} must be finite and within +/-1e30.")
    return number


def _bounded(
    value: Any,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
) -> float:
    number = _finite(value, field=field)
    if minimum is not None and (
        number < minimum if minimum_inclusive else number <= minimum
    ):
        comparison = ">=" if minimum_inclusive else ">"
        raise NativeAnalyzeError(f"{field} must be {comparison} {minimum}.")
    if maximum is not None and number > maximum:
        raise NativeAnalyzeError(f"{field} must be <= {maximum}.")
    return number


def _typed(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(f"{field} must be one object.")
    result = dict(value)
    if not str(result.get("kind", "") or ""):
        raise NativeAnalyzeError(f"{field}.kind is required.")
    return result


def _formula(value: Any, *, field: str) -> str:
    expression = str(value or "")
    if not expression or len(expression) > 512:
        raise NativeAnalyzeError(f"{field} must contain 1 to 512 characters.")
    if any(character in expression for character in ("\r", "\n", "\x00")):
        raise NativeAnalyzeError(
            f"{field} must be one line and contain no null character."
        )
    return expression


def _components(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value or not set(value) <= {"x", "y", "z"}:
        raise NativeAnalyzeError(
            "constraint.components must contain one or more of x, y, and z only."
        )
    result = {}
    for axis, raw in value.items():
        if not isinstance(raw, Mapping):
            raise NativeAnalyzeError(
                f"constraint.components.{axis} must be one object."
            )
        component = dict(raw)
        mode = str(component.get("kind", "") or "")
        if mode == "value" and set(component) == {"kind", "value_m_s"}:
            result[axis] = {
                "kind": mode,
                "value_m_s": _finite(
                    component["value_m_s"],
                    field=f"constraint.components.{axis}.value_m_s",
                ),
            }
        elif mode == "formula" and set(component) == {"kind", "expression"}:
            result[axis] = {
                "kind": mode,
                "expression": _formula(
                    component["expression"],
                    field=f"constraint.components.{axis}.expression",
                ),
            }
        else:
            raise NativeAnalyzeError(
                f"constraint.components.{axis} must be either {{kind: value, value_m_s}} "
                "or {kind: formula, expression}."
            )
    return result


def _velocity_native(components: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    native: dict[str, Any] = {}
    for axis, suffix in _AXES:
        component = components.get(axis)
        native[f"Velocity{suffix}"] = "0 m/s"
        native[f"Velocity{suffix}Formula"] = ""
        native[f"Velocity{suffix}Unspecified"] = component is None
        native[f"Velocity{suffix}HasFormula"] = bool(
            component is not None and component["kind"] == "formula"
        )
        if component is None:
            continue
        if component["kind"] == "value":
            native[f"Velocity{suffix}"] = f"{component['value_m_s']} m/s"
        else:
            native[f"Velocity{suffix}Formula"] = component["expression"]
    return native


_BOUNDARY_CONDITIONS = {
    "wall_no_slip": ("wall", "fixed", None, None),
    "wall_slip": ("wall", "slip", None, None),
    "wall_partial_slip": ("wall", "partialSlip", "slip_ratio", "ratio"),
    "wall_moving": ("wall", "moving", "speed_m_s", "nonnegative"),
    "inlet_total_pressure": ("inlet", "totalPressure", "pressure_pa", "finite"),
    "inlet_velocity": ("inlet", "uniformVelocity", "velocity_m_s", "nonnegative"),
    "inlet_volumetric_flow": (
        "inlet",
        "volumetricFlowRate",
        "flow_m3_s",
        "nonnegative",
    ),
    "inlet_mass_flow": ("inlet", "massFlowRate", "flow_kg_s", "nonnegative"),
    "outlet_total_pressure": ("outlet", "totalPressure", "pressure_pa", "finite"),
    "outlet_static_pressure": ("outlet", "staticPressure", "pressure_pa", "finite"),
    "outlet_velocity": (
        "outlet",
        "uniformVelocity",
        "velocity_m_s",
        "nonnegative",
    ),
    "outlet_outflow": ("outlet", "outFlow", None, None),
    "symmetry": ("interface", "symmetry", None, None),
    "wedge": ("interface", "wedge", None, None),
    "cyclic": ("interface", "cyclic", None, None),
    "empty": ("interface", "empty", None, None),
    "freestream": ("freestream", "freestream", None, None),
}
_BOUNDARY_CONDITIONS_REVERSE = {
    (boundary_type, subtype): (kind, field)
    for kind, (boundary_type, subtype, field, _value_kind) in _BOUNDARY_CONDITIONS.items()
}

_TURBULENCE = {
    "intensity_dissipation_rate": (
        "intensity&DissipationRate",
        "dissipation_rate_m2_s3",
    ),
    "intensity_length_scale": ("intensity&LengthScale", "length_scale_m"),
    "intensity_viscosity_ratio": ("intensity&ViscosityRatio", "viscosity_ratio"),
    "intensity_hydraulic_diameter": (
        "intensity&HydraulicDiameter",
        "hydraulic_diameter_m",
    ),
}
_TURBULENCE_REVERSE = {
    native: (kind, value_field)
    for kind, (native, value_field) in _TURBULENCE.items()
}


def _boundary_condition(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    condition = _typed(value, field="constraint.condition")
    kind = str(condition["kind"])
    try:
        boundary_type, subtype, value_field, value_kind = _BOUNDARY_CONDITIONS[kind]
    except KeyError as exc:
        raise NativeAnalyzeError(
            "constraint.condition.kind is not a supported fluid boundary."
        ) from exc
    expected = {"kind"} | ({value_field} if value_field else set())
    if set(condition) != expected:
        raise NativeAnalyzeError(
            f"constraint.condition for {kind} must contain only {', '.join(sorted(expected))}."
        )
    boundary_value = 0.0
    if value_field:
        if value_kind == "ratio":
            boundary_value = _bounded(
                condition[value_field],
                field=f"constraint.condition.{value_field}",
                minimum=0.0,
                maximum=1.0,
            )
        elif value_kind == "nonnegative":
            boundary_value = _bounded(
                condition[value_field],
                field=f"constraint.condition.{value_field}",
                minimum=0.0,
            )
        else:
            boundary_value = _finite(
                condition[value_field], field=f"constraint.condition.{value_field}"
            )
    normalized = {"kind": kind}
    if value_field:
        normalized[value_field] = boundary_value
    return (
        {
            "BoundaryType": boundary_type,
            "Subtype": subtype,
            "BoundaryValue": boundary_value,
        },
        normalized,
    )


def _boundary_turbulence(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    turbulence = _typed(value, field="constraint.turbulence")
    kind = str(turbulence["kind"])
    if kind == "none":
        if set(turbulence) != {"kind"}:
            raise NativeAnalyzeError(
                "constraint.turbulence with kind none accepts no values."
            )
        return (
            {
                "TurbulenceSpecification": "intensity&LengthScale",
                "TurbulentIntensityValue": 0.0,
                "TurbulentLengthValue": 0.0,
            },
            {"kind": "none"},
        )
    try:
        native_kind, value_field = _TURBULENCE[kind]
    except KeyError as exc:
        raise NativeAnalyzeError(
            "constraint.turbulence.kind is not supported."
        ) from exc
    expected = {"kind", "intensity_ratio", value_field}
    if set(turbulence) != expected:
        raise NativeAnalyzeError(
            f"constraint.turbulence for {kind} must contain only {', '.join(sorted(expected))}."
        )
    intensity = _bounded(
        turbulence["intensity_ratio"],
        field="constraint.turbulence.intensity_ratio",
        minimum=0.0,
        maximum=1.0,
    )
    magnitude = _bounded(
        turbulence[value_field],
        field=f"constraint.turbulence.{value_field}",
        minimum=0.0,
        minimum_inclusive=False,
    )
    return (
        {
            "TurbulenceSpecification": native_kind,
            "TurbulentIntensityValue": intensity,
            "TurbulentLengthValue": magnitude,
        },
        {
            "kind": kind,
            "intensity_ratio": intensity,
            value_field: magnitude,
        },
    )


def _boundary_thermal(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    thermal = _typed(value, field="constraint.thermal")
    kind = str(thermal["kind"])
    native = {
        "TemperatureValue": 0.0,
        "HeatFluxValue": 0.0,
        "HTCoeffValue": 0.0,
    }
    if kind == "adiabatic":
        expected = {"kind"}
        native["ThermalBoundaryType"] = "zeroGradient"
    elif kind == "fixed_temperature":
        expected = {"kind", "temperature_k"}
        native["ThermalBoundaryType"] = "fixedValue"
    elif kind == "fixed_gradient":
        expected = {"kind", "gradient_k_m"}
        native["ThermalBoundaryType"] = "fixedGradient"
    elif kind == "mixed":
        expected = {"kind", "temperature_k", "gradient_k_m"}
        native["ThermalBoundaryType"] = "mixed"
    elif kind == "heat_flux":
        expected = {"kind", "heat_flux_w_m2"}
        native["ThermalBoundaryType"] = "heatFlux"
    elif kind == "heat_transfer_coefficient":
        expected = {"kind", "coefficient_w_m2_k", "ambient_temperature_k"}
        native["ThermalBoundaryType"] = "HTC"
    elif kind == "coupled":
        expected = {"kind"}
        native["ThermalBoundaryType"] = "coupled"
    else:
        raise NativeAnalyzeError("constraint.thermal.kind is not supported.")
    if set(thermal) != expected:
        raise NativeAnalyzeError(
            f"constraint.thermal for {kind} must contain only {', '.join(sorted(expected))}."
        )
    normalized = {"kind": kind}
    if "temperature_k" in thermal:
        temperature = _bounded(
            thermal["temperature_k"],
            field="constraint.thermal.temperature_k",
            minimum=0.0,
        )
        native["TemperatureValue"] = temperature
        normalized["temperature_k"] = temperature
    if "ambient_temperature_k" in thermal:
        temperature = _bounded(
            thermal["ambient_temperature_k"],
            field="constraint.thermal.ambient_temperature_k",
            minimum=0.0,
        )
        native["TemperatureValue"] = temperature
        normalized["ambient_temperature_k"] = temperature
    if "gradient_k_m" in thermal:
        gradient = _finite(
            thermal["gradient_k_m"], field="constraint.thermal.gradient_k_m"
        )
        native["HeatFluxValue"] = gradient
        normalized["gradient_k_m"] = gradient
    if "heat_flux_w_m2" in thermal:
        heat_flux = _finite(
            thermal["heat_flux_w_m2"], field="constraint.thermal.heat_flux_w_m2"
        )
        native["HeatFluxValue"] = heat_flux
        normalized["heat_flux_w_m2"] = heat_flux
    if "coefficient_w_m2_k" in thermal:
        coefficient = _bounded(
            thermal["coefficient_w_m2_k"],
            field="constraint.thermal.coefficient_w_m2_k",
            minimum=0.0,
        )
        native["HTCoeffValue"] = coefficient
        normalized["coefficient_w_m2_k"] = coefficient
    return native, normalized


def _prepare_fluid_boundary(value: Mapping[str, Any]) -> PreparedFluidValues:
    if set(value) != {"condition", "turbulence", "thermal"}:
        raise NativeAnalyzeError(
            "constraint must contain condition, turbulence, and thermal."
        )
    condition_native, condition = _boundary_condition(value["condition"])
    turbulence_native, turbulence = _boundary_turbulence(value["turbulence"])
    thermal_native, thermal = _boundary_thermal(value["thermal"])
    return PreparedFluidValues(
        "fluid_boundary",
        {**condition_native, **turbulence_native, **thermal_native},
        {
            "condition": condition,
            "turbulence": turbulence,
            "thermal": thermal,
        },
        frozenset({"Face"}),
        False,
        False,
    )


def fluid_boundary_definition(obj: Any) -> dict[str, Any]:
    boundary_type = str(obj.BoundaryType)
    subtype = str(obj.Subtype)
    try:
        condition_kind, value_field = _BOUNDARY_CONDITIONS_REVERSE[
            (boundary_type, subtype)
        ]
    except KeyError as exc:
        raise NativeAnalyzeError(
            f"The FEM fluid boundary has unsupported condition {boundary_type}/{subtype}."
        ) from exc
    condition: dict[str, Any] = {"kind": condition_kind}
    if value_field:
        condition[value_field] = _finite(
            obj.BoundaryValue, field="fluid_boundary.BoundaryValue"
        )

    intensity = _finite(
        obj.TurbulentIntensityValue,
        field="fluid_boundary.TurbulentIntensityValue",
    )
    magnitude = _finite(
        obj.TurbulentLengthValue,
        field="fluid_boundary.TurbulentLengthValue",
    )
    if intensity == 0.0 and magnitude == 0.0:
        turbulence: dict[str, Any] = {"kind": "none"}
    else:
        native_turbulence = str(obj.TurbulenceSpecification)
        try:
            turbulence_kind, turbulence_field = _TURBULENCE_REVERSE[
                native_turbulence
            ]
        except KeyError as exc:
            raise NativeAnalyzeError(
                f"The FEM fluid boundary has unsupported turbulence {native_turbulence}."
            ) from exc
        turbulence = {
            "kind": turbulence_kind,
            "intensity_ratio": intensity,
            turbulence_field: magnitude,
        }

    thermal_kind = str(obj.ThermalBoundaryType)
    if thermal_kind == "zeroGradient":
        thermal = {"kind": "adiabatic"}
    elif thermal_kind == "fixedValue":
        thermal = {
            "kind": "fixed_temperature",
            "temperature_k": _finite(
                obj.TemperatureValue, field="fluid_boundary.TemperatureValue"
            ),
        }
    elif thermal_kind == "fixedGradient":
        thermal = {
            "kind": "fixed_gradient",
            "gradient_k_m": _finite(
                obj.HeatFluxValue, field="fluid_boundary.HeatFluxValue"
            ),
        }
    elif thermal_kind == "mixed":
        thermal = {
            "kind": "mixed",
            "temperature_k": _finite(
                obj.TemperatureValue, field="fluid_boundary.TemperatureValue"
            ),
            "gradient_k_m": _finite(
                obj.HeatFluxValue, field="fluid_boundary.HeatFluxValue"
            ),
        }
    elif thermal_kind == "heatFlux":
        thermal = {
            "kind": "heat_flux",
            "heat_flux_w_m2": _finite(
                obj.HeatFluxValue, field="fluid_boundary.HeatFluxValue"
            ),
        }
    elif thermal_kind == "HTC":
        thermal = {
            "kind": "heat_transfer_coefficient",
            "coefficient_w_m2_k": _finite(
                obj.HTCoeffValue, field="fluid_boundary.HTCoeffValue"
            ),
            "ambient_temperature_k": _finite(
                obj.TemperatureValue, field="fluid_boundary.TemperatureValue"
            ),
        }
    elif thermal_kind == "coupled":
        thermal = {"kind": "coupled"}
    else:
        raise NativeAnalyzeError(
            f"The FEM fluid boundary has unsupported thermal mode {thermal_kind}."
        )
    return {
        "condition": condition,
        "turbulence": turbulence,
        "thermal": thermal,
    }


def prepare_fluid_values(kind: str, value: Any) -> PreparedFluidValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(
            "constraint must be one typed FEM fluid constraint object."
        )
    raw = dict(value)
    if kind == "fluid_boundary":
        return _prepare_fluid_boundary(raw)
    if kind == "initial_pressure":
        if set(raw) != {"pressure_pa"}:
            raise NativeAnalyzeError("constraint must contain only pressure_pa.")
        pressure = _finite(raw["pressure_pa"], field="constraint.pressure_pa")
        return PreparedFluidValues(
            kind,
            {"Pressure": f"{pressure} Pa"},
            {"pressure_pa": pressure},
            frozenset({"Solid", "Face"}),
            True,
            True,
        )
    expected = (
        {"components"}
        if kind == "initial_flow_velocity"
        else {
            "components",
            "normal_to_boundary",
        }
    )
    if set(raw) != expected:
        raise NativeAnalyzeError(
            "constraint fields do not match the selected FEM fluid constraint type."
        )
    components = _components(raw["components"])
    native = _velocity_native(components)
    definition: dict[str, Any] = {"components": components}
    if kind == "flow_velocity":
        normal = raw["normal_to_boundary"]
        if type(normal) is not bool:
            raise NativeAnalyzeError(
                "constraint.normal_to_boundary must be true or false."
            )
        native["NormalToBoundary"] = normal
        definition["normal_to_boundary"] = normal
        allowed = frozenset({"Solid", "Face", "Edge", "Vertex"})
        allow_empty = False
    elif kind == "initial_flow_velocity":
        allowed = frozenset({"Solid", "Face"})
        allow_empty = True
    else:
        raise NativeAnalyzeError(
            "The requested FEM fluid constraint kind is unavailable."
        )
    return PreparedFluidValues(
        kind,
        native,
        definition,
        allowed,
        True,
        allow_empty,
    )


def apply_fluid_values(obj: Any, prepared: PreparedFluidValues) -> None:
    if not isinstance(prepared, PreparedFluidValues):
        raise TypeError("prepared must be PreparedFluidValues")
    for name, value in prepared.native.items():
        setattr(obj, name, value)
    if prepared.kind == "fluid_boundary":
        obj.Reversed = prepared.native["BoundaryType"] == "inlet"
