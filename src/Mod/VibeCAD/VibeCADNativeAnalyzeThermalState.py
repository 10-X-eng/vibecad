# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact normalized state for FEM thermal conditions."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


_FAMILY_TYPES = (
    ("Fem::ConstraintInitialTemperature", "initial_temperature"),
    ("Fem::ConstraintHeatflux", "surface_condition"),
    ("Fem::ConstraintTemperature", "nodal_condition"),
)


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
        raise NativeAnalyzeError("A FEM thermal condition contains a non-finite value.")
    return float(format(number, ".15g"))


def thermal_condition_family(obj: Any) -> str:
    for type_id, family in _FAMILY_TYPES:
        try:
            if obj.isDerivedFrom(type_id):
                return family
        except Exception:
            continue
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    if proxy_type == "Fem::ConstraintBodyHeatSource":
        return "body_heat_source"
    raise NativeAnalyzeError(
        "The exact target is not a supported FEM thermal condition.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _references(obj: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visible = []
    exact = []
    for raw in tuple(getattr(obj, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError("A FEM thermal condition has malformed references.")
        source, raw_names = raw
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
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


def _mode(obj: Any, family: str) -> str:
    if family == "initial_temperature":
        return family
    if family == "surface_condition":
        return {
            "Flux": "surface_heat_flux",
            "Convection": "convection",
            "Radiation": "radiation",
        }.get(str(obj.ConstraintType), "")
    if family == "nodal_condition":
        return {
            "Temperature": "boundary_temperature",
            "Flux": "concentrated_heat_input",
        }.get(str(obj.ConstraintType), "")
    return {
        "Dissipation Rate": "mass_heat_generation",
        "Total Power": "total_body_power",
    }.get(str(obj.Mode), "")


def _definition(obj: Any, mode: str) -> dict[str, Any]:
    if mode == "initial_temperature":
        return {"temperature_k": _finite(obj.InitialTemperature.getValueAs("K").Value)}
    if mode == "surface_heat_flux":
        return {"heat_flux_w_m2": _finite(obj.DistributedHeatFlux.getValueAs("W/m^2").Value)}
    if mode == "convection":
        return {
            "ambient_temperature_k": _finite(obj.AmbientTemp.getValueAs("K").Value),
            "film_coefficient_w_m2_k": _finite(
                obj.FilmCoef.getValueAs("W/(m^2*K)").Value
            ),
        }
    if mode == "radiation":
        return {
            "ambient_temperature_k": _finite(obj.AmbientTemp.getValueAs("K").Value),
            "emissivity": _finite(obj.Emissivity),
        }
    if mode == "boundary_temperature":
        return {"temperature_k": _finite(obj.Temperature.getValueAs("K").Value)}
    if mode == "concentrated_heat_input":
        return {"power_w": _finite(obj.ConcentratedHeatFlux.getValueAs("W").Value)}
    if mode == "mass_heat_generation":
        return {
            "dissipation_rate_w_kg": _finite(obj.DissipationRate.getValueAs("W/kg").Value)
        }
    if mode == "total_body_power":
        return {"total_power_w": _finite(obj.TotalPower.getValueAs("W").Value)}
    raise NativeAnalyzeError("A FEM thermal condition has an unsupported native mode.")


def _native_values(obj: Any, family: str) -> dict[str, Any]:
    if family == "initial_temperature":
        return {
            "InitialTemperatureK": _finite(obj.InitialTemperature.getValueAs("K").Value),
            "EnableFinalTemperature": bool(obj.EnableFinalTemperature),
            "FinalTemperatureK": _finite(obj.FinalTemperature.getValueAs("K").Value),
            "EnableAmplitude": bool(obj.EnableAmplitude),
            "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
        }
    if family == "surface_condition":
        return {
            "ConstraintType": str(obj.ConstraintType),
            "AmbientTemperatureK": _finite(obj.AmbientTemp.getValueAs("K").Value),
            "FilmCoefficientWM2K": _finite(obj.FilmCoef.getValueAs("W/(m^2*K)").Value),
            "Emissivity": _finite(obj.Emissivity),
            "DistributedHeatFluxWM2": _finite(
                obj.DistributedHeatFlux.getValueAs("W/m^2").Value
            ),
            "CavityRadiation": bool(obj.CavityRadiation),
            "CavityName": str(obj.CavityName),
            "EnableAmplitude": bool(obj.EnableAmplitude),
            "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
        }
    if family == "nodal_condition":
        return {
            "ConstraintType": str(obj.ConstraintType),
            "TemperatureK": _finite(obj.Temperature.getValueAs("K").Value),
            "ConcentratedHeatFluxW": _finite(obj.ConcentratedHeatFlux.getValueAs("W").Value),
            "EnableAmplitude": bool(obj.EnableAmplitude),
            "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
        }
    return {
        "Mode": str(obj.Mode),
        "DissipationRateWKg": _finite(obj.DissipationRate.getValueAs("W/kg").Value),
        "TotalPowerW": _finite(obj.TotalPower.getValueAs("W").Value),
        "EnableAmplitude": bool(obj.EnableAmplitude),
        "AmplitudeValues": [str(value) for value in obj.AmplitudeValues],
    }


def thermal_condition_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM thermal condition is no longer live.")
    family = thermal_condition_family(obj)
    mode = _mode(obj, family)
    if not mode:
        raise NativeAnalyzeError("A FEM thermal condition has an unsupported native mode.")
    references, exact_references = _references(obj)
    result = {
        **concise_object(obj),
        "thermal_family": family,
        "thermal_mode": mode,
        "references": references,
        "definition": _definition(obj, mode),
    }
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "family": family,
            "references": exact_references,
            "native_values": _native_values(obj, family),
        }
    )
    return result


def thermal_condition_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return thermal_condition_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
