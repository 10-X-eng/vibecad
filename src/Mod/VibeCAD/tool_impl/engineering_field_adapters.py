# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded presentation adapters over existing FEM, VTK and OpenFOAM state."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from .analysis_contracts import AnalysisContractError, CanonicalJson
from .engineering_experience import (
    DomainPresentation,
    EngineeringFieldProjection,
    MAX_PRESENTATION_FIELDS,
    PresentationMetric,
)


_SEMANTICS = {
    "displacementvectors": ("displacement.vector", "Displacement", "viridis"),
    "displacementlengths": (
        "displacement.magnitude", "Displacement Magnitude", "viridis"
    ),
    "displacementmagnitude": (
        "displacement.magnitude", "Displacement Magnitude", "viridis"
    ),
    "vonmises": ("stress.von_mises", "Von Mises Stress", "turbo"),
    "vonmisesstress": ("stress.von_mises", "Von Mises Stress", "turbo"),
    "principalmax": (
        "stress.principal.maximum", "Maximum Principal Stress", "turbo"
    ),
    "principalmed": (
        "stress.principal.middle", "Middle Principal Stress", "turbo"
    ),
    "principalmin": (
        "stress.principal.minimum", "Minimum Principal Stress", "turbo"
    ),
    "maxshear": ("stress.shear.maximum", "Maximum Shear Stress", "turbo"),
    "peeq": ("strain.plastic.equivalent", "Equivalent Plastic Strain", "viridis"),
    "temperature": ("temperature", "Temperature", "inferno"),
    "temperatureflux": ("heat_flux", "Heat Flux", "viridis"),
    "heatflux": ("heat_flux", "Heat Flux", "viridis"),
    "pressure": ("pressure", "Pressure", "turbo"),
    "networkpressure": ("pressure.network", "Network Pressure", "turbo"),
    "velocity": ("velocity.vector", "Velocity", "viridis"),
    "velocitymagnitude": ("velocity.magnitude", "Velocity Magnitude", "viridis"),
    "turbulentkineticenergy": (
        "turbulence.kinetic_energy", "Turbulent Kinetic Energy", "viridis"
    ),
    "massflowrate": ("flow.mass_rate", "Mass Flow Rate", "viridis"),
    "currentdensity": ("electromagnetic.current_density", "Current Density", "viridis"),
    "electricfield": ("electromagnetic.electric_field", "Electric Field", "viridis"),
    "magneticfluxdensity": (
        "electromagnetic.magnetic_flux_density", "Magnetic Flux Density", "viridis"
    ),
    "magneticfieldstrength": (
        "electromagnetic.magnetic_field_strength", "Magnetic Field Strength", "viridis"
    ),
    "nodalforce": ("force.nodal", "Nodal Force", "viridis"),
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _text(value: Any, field: str, maximum: int = 160) -> str:
    if not isinstance(value, str):
        raise AnalysisContractError(f"{field} must be a string.")
    result = value.strip()
    if not result or len(result) > maximum:
        raise AnalysisContractError(
            f"{field} must contain 1 through {maximum} characters."
        )
    return result


def _finite_pair(value: Any) -> tuple[float, float] | tuple[None, None]:
    if value is None:
        return None, None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise AnalysisContractError("Engineering field range must contain two numbers.")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise AnalysisContractError("Engineering field range must contain two numbers.")
    lower, upper = float(value[0]), float(value[1])
    if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
        raise AnalysisContractError("Engineering field range is invalid.")
    return lower, upper


def _semantic(name: str, declared: Any = None) -> tuple[str, str, str]:
    candidates = (_key(name), _key(str(declared or "")))
    for candidate in candidates:
        if candidate in _SEMANTICS:
            return _SEMANTICS[candidate]
    label = re.sub(r"[_\-]+", " ", name).strip()
    label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
    return f"domain.{_key(str(declared or name))}", label, "viridis"


def _presentation(components: int) -> str:
    if components == 1:
        return "scalar"
    if components in {6, 9}:
        return "tensor"
    return "vector"


def _field_id(name: str, association: str, duplicate_names: frozenset[str]) -> str:
    return f"{association}:{name}" if name in duplicate_names else name


def fields_from_result_state(
    state: Mapping[str, Any],
) -> tuple[EngineeringFieldProjection, ...]:
    """Translate bounded Native result-state metadata without reading field arrays."""

    if not isinstance(state, Mapping):
        raise TypeError("result state must be a mapping")
    raw_fields = state.get("fields", ())
    if not isinstance(raw_fields, (list, tuple)):
        raise AnalysisContractError("Result-state fields must be a sequence.")
    if len(raw_fields) > MAX_PRESENTATION_FIELDS:
        raise AnalysisContractError("Result-state fields exceed the presentation bound.")
    names = []
    for item in raw_fields:
        if not isinstance(item, Mapping):
            raise AnalysisContractError("A result-state field is invalid.")
        names.append(_text(item.get("name"), "field name"))
    duplicate_names = frozenset(name for name in names if names.count(name) > 1)
    projected = []
    for raw, name in zip(raw_fields, names):
        association = str(raw.get("association") or "point")
        if association not in {"point", "cell", "object"}:
            raise AnalysisContractError("A result-state field association is invalid.")
        components = raw.get("components")
        if type(components) is not int or not 1 <= components <= 16:
            raise AnalysisContractError("A result-state field component count is invalid.")
        minimum, maximum = _finite_pair(raw.get("range"))
        unit_value = raw.get("unit")
        unit = None if unit_value is None else _text(unit_value, "field unit", 48)
        semantic, label, color_map = _semantic(name, raw.get("semantic"))
        projected.append(
            EngineeringFieldProjection(
                _field_id(name, association, duplicate_names),
                label,
                semantic,
                association,
                components,
                unit,
                minimum,
                maximum,
                _presentation(components),
                color_map,
            )
        )
    return tuple(projected)


def fields_from_openfoam_summary(
    summary: Mapping[str, Any],
) -> tuple[EngineeringFieldProjection, ...]:
    """Project only fields whose units and ranges are present in the flow summary."""

    if not isinstance(summary, Mapping):
        raise TypeError("OpenFOAM summary must be a mapping")
    pressure_min, pressure_max = _finite_pair(summary.get("pressure_range_pa"))
    velocity_min, velocity_max = _finite_pair(
        summary.get("velocity_magnitude_range_m_s")
    )
    pressure_unit = _text(summary.get("pressure_unit"), "pressure unit", 48)
    velocity_unit = _text(summary.get("velocity_unit"), "velocity unit", 48)
    fields = [
        EngineeringFieldProjection(
            "Pressure", "Pressure", "pressure", "point", 1,
            pressure_unit, pressure_min, pressure_max, "scalar", "turbo",
        ),
        EngineeringFieldProjection(
            "Velocity", "Velocity Magnitude", "velocity.magnitude", "point", 3,
            velocity_unit, velocity_min, velocity_max, "vector", "viridis",
        ),
    ]
    if summary.get("turbulence_model") == "kOmegaSST":
        fields.append(
            EngineeringFieldProjection(
                "TurbulentKineticEnergy", "Turbulent Kinetic Energy",
                "turbulence.kinetic_energy", "point", 1, "m²/s²",
                None, None, "scalar", "viridis",
            )
        )
    return tuple(fields)


def presentation_from_result_state(
    state: Mapping[str, Any],
    *,
    title: str | None = None,
) -> DomainPresentation:
    """Build an inert domain presentation from an exact result-state snapshot."""

    if not isinstance(state, Mapping):
        raise TypeError("result state must be a mapping")
    fields = list(fields_from_result_state(state))
    flow = state.get("flow")
    metrics = []
    if flow is not None:
        flow_fields = fields_from_openfoam_summary(flow)
        existing = {field.semantic for field in fields}
        fields.extend(field for field in flow_fields if field.semantic not in existing)
        metrics.append(
            PresentationMetric(
                "maximum_velocity", "Maximum Velocity",
                float(flow["maximum_velocity_m_s"]), "m/s", "max",
            )
        )
        if "static_pressure_drop_pa" in flow:
            metrics.append(
                PresentationMetric(
                    "pressure_drop", "Pressure Drop",
                    float(flow["static_pressure_drop_pa"]), "Pa", "difference",
                )
            )
    if len(fields) > MAX_PRESENTATION_FIELDS:
        raise AnalysisContractError("Combined result fields exceed the presentation bound.")
    source_title = title or str(state.get("label") or "Engineering result")
    extension = CanonicalJson.from_value(
        {
            "source_result_kind": state.get("result_kind"),
            "source_state_sha256": state.get("state_sha256"),
            "analysis_owners": state.get("analysis_owners") or [],
            "post_pipeline_owners": state.get("post_pipeline_owners") or [],
            "timeline_owner_chain": state.get("timeline_owner_chain") or [],
            "mesh_object_name": state.get("mesh"),
            "point_count": state.get("point_count"),
            "cell_count": state.get("cell_count"),
            "reported_field_count": state.get("field_count", len(fields)),
            "fields_truncated": bool(state.get("fields_truncated", False)),
            "large_arrays_copied": False,
            "presentation_owner_unchanged": True,
        }
    )
    return DomainPresentation(
        _text(source_title, "presentation title"),
        tuple(metrics),
        tuple(fields),
        extension,
    )


__all__ = [
    "fields_from_openfoam_summary",
    "fields_from_result_state",
    "presentation_from_result_state",
]
