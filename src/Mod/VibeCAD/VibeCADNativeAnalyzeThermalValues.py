# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong value preparation for FEM thermal conditions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


MODES = (
    "initial_temperature",
    "surface_heat_flux",
    "convection",
    "radiation",
    "boundary_temperature",
    "concentrated_heat_input",
    "mass_heat_generation",
    "total_body_power",
)


@dataclass(frozen=True, slots=True)
class PreparedThermalValues:
    mode: str
    values: dict[str, float]

    def normalized(self) -> dict[str, float]:
        return dict(self.values)


def thermal_family_for_mode(mode: str) -> str:
    if mode == "initial_temperature":
        return mode
    if mode in {"surface_heat_flux", "convection", "radiation"}:
        return "surface_condition"
    if mode in {"boundary_temperature", "concentrated_heat_input"}:
        return "nodal_condition"
    if mode in {"mass_heat_generation", "total_body_power"}:
        return "body_heat_source"
    raise NativeAnalyzeError("The requested FEM thermal mode is unavailable.")


def thermal_value_fields(mode: str) -> tuple[str, ...]:
    fields = {
        "initial_temperature": ("temperature_k",),
        "surface_heat_flux": ("heat_flux_w_m2",),
        "convection": ("ambient_temperature_k", "film_coefficient_w_m2_k"),
        "radiation": ("ambient_temperature_k", "emissivity"),
        "boundary_temperature": ("temperature_k",),
        "concentrated_heat_input": ("power_w",),
        "mass_heat_generation": ("dissipation_rate_w_kg",),
        "total_body_power": ("total_power_w",),
    }
    try:
        return fields[mode]
    except KeyError as exc:
        raise NativeAnalyzeError("The requested FEM thermal mode is unavailable.") from exc


def _finite(
    value: Any,
    *,
    field: str,
    minimum: float | None = None,
    positive: bool = False,
    nonzero: bool = False,
) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > 1.0e30:
        raise NativeAnalyzeError(f"{field} must be finite and within +/-1e30.")
    if minimum is not None and number < minimum:
        raise NativeAnalyzeError(f"{field} must be at least {minimum}.")
    if positive and number <= 0.0:
        raise NativeAnalyzeError(f"{field} must be greater than zero.")
    if nonzero and number == 0.0:
        raise NativeAnalyzeError(f"{field} must not be zero.")
    return float(format(number, ".15g"))


def prepare_thermal_values(mode: str, value: Any) -> PreparedThermalValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("values must be one typed FEM thermal-value object.")
    fields = thermal_value_fields(mode)
    if set(value) != set(fields):
        raise NativeAnalyzeError(
            f"{mode} values must contain exactly {', '.join(fields)}."
        )
    raw = dict(value)
    if mode in {"initial_temperature", "boundary_temperature"}:
        prepared = {"temperature_k": _finite(raw["temperature_k"], field="temperature_k", minimum=0.0)}
    elif mode == "surface_heat_flux":
        prepared = {"heat_flux_w_m2": _finite(raw["heat_flux_w_m2"], field="heat_flux_w_m2", nonzero=True)}
    elif mode == "convection":
        prepared = {
            "ambient_temperature_k": _finite(raw["ambient_temperature_k"], field="ambient_temperature_k", minimum=0.0),
            "film_coefficient_w_m2_k": _finite(raw["film_coefficient_w_m2_k"], field="film_coefficient_w_m2_k", positive=True),
        }
    elif mode == "radiation":
        emissivity = _finite(raw["emissivity"], field="emissivity", positive=True)
        if emissivity > 1.0:
            raise NativeAnalyzeError("emissivity must be greater than zero and at most one.")
        prepared = {
            "ambient_temperature_k": _finite(raw["ambient_temperature_k"], field="ambient_temperature_k", minimum=0.0),
            "emissivity": emissivity,
        }
    elif mode == "concentrated_heat_input":
        prepared = {"power_w": _finite(raw["power_w"], field="power_w", nonzero=True)}
    elif mode == "mass_heat_generation":
        prepared = {"dissipation_rate_w_kg": _finite(raw["dissipation_rate_w_kg"], field="dissipation_rate_w_kg", nonzero=True)}
    else:
        prepared = {"total_power_w": _finite(raw["total_power_w"], field="total_power_w", nonzero=True)}
    return PreparedThermalValues(mode, prepared)


def apply_thermal_values(obj: Any, prepared: PreparedThermalValues) -> None:
    if not isinstance(prepared, PreparedThermalValues):
        raise TypeError("prepared must be PreparedThermalValues")
    mode = prepared.mode
    values = prepared.values
    if mode == "initial_temperature":
        obj.InitialTemperature = f"{values['temperature_k']} K"
    elif mode == "surface_heat_flux":
        obj.ConstraintType = "Flux"
        obj.DistributedHeatFlux = f"{values['heat_flux_w_m2']} W/m^2"
    elif mode == "convection":
        obj.ConstraintType = "Convection"
        obj.AmbientTemp = f"{values['ambient_temperature_k']} K"
        obj.FilmCoef = f"{values['film_coefficient_w_m2_k']} W/(m^2*K)"
    elif mode == "radiation":
        obj.ConstraintType = "Radiation"
        obj.AmbientTemp = f"{values['ambient_temperature_k']} K"
        obj.Emissivity = values["emissivity"]
    elif mode == "boundary_temperature":
        obj.ConstraintType = "Temperature"
        obj.Temperature = f"{values['temperature_k']} K"
    elif mode == "concentrated_heat_input":
        obj.ConstraintType = "Flux"
        obj.ConcentratedHeatFlux = f"{values['power_w']} W"
    elif mode == "mass_heat_generation":
        obj.Mode = "Dissipation Rate"
        obj.DissipationRate = f"{values['dissipation_rate_w_kg']} W/kg"
    elif mode == "total_body_power":
        obj.Mode = "Total Power"
        obj.TotalPower = f"{values['total_power_w']} W"
    else:
        raise NativeAnalyzeError("The requested FEM thermal mode is unavailable.")
