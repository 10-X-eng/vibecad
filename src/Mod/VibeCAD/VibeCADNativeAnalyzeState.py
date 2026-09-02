# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded FEM analysis and material state for Native Analyze."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeStudy import study_dependency_state, study_intent_state
from VibeCADNativeSnapshot import concise_object


MAX_ANALYSIS_MEMBERS = 64
MAX_MATERIAL_PROPERTY_NAMES = 64
_COMMON_PROPERTIES = (
    ("Name", "name", None),
    ("Density", "density_kg_m3", "kg/m^3"),
    ("YoungsModulus", "young_modulus_mpa", "MPa"),
    ("PoissonRatio", "poisson_ratio", None),
    ("YieldStrength", "yield_strength_mpa", "MPa"),
    ("ThermalConductivity", "thermal_conductivity_w_m_k", "W/m/K"),
    ("ThermalExpansionCoefficient", "thermal_expansion_per_k", "1/K"),
    ("ThermalExpansionReferenceTemperature", "reference_temperature_k", "K"),
    ("SpecificHeat", "specific_heat_j_kg_k", "J/kg/K"),
    ("KinematicViscosity", "kinematic_viscosity_m2_s", "m^2/s"),
)
_NONLINEAR_MODELS = {
    "isotropic hardening": "isotropic_hardening",
    "kinematic hardening": "kinematic_hardening",
}


def is_live(document: Any, obj: Any) -> bool:
    try:
        return (
            obj is not None
            and obj.Document is document
            and document.getObject(str(obj.Name)) is obj
        )
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _member_category(obj: Any) -> str:
    type_id = str(getattr(obj, "TypeId", "") or "")
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    combined = f"{type_id} {proxy_type}"
    for fragment, category in (
        ("Solver", "solver"),
        ("FemMesh", "mesh"),
        ("Material", "material"),
        ("Constraint", "constraint"),
        ("Equation", "equation"),
        ("Result", "result"),
        ("Post", "post"),
    ):
        if fragment in combined:
            return category
    return "member"


def analysis_state(analysis: Any) -> dict[str, Any]:
    document = getattr(analysis, "Document", None)
    try:
        valid_type = bool(analysis.isDerivedFrom("Fem::FemAnalysis"))
    except Exception:
        valid_type = False
    if not is_live(document, analysis) or not valid_type:
        raise NativeAnalyzeError("The FEM analysis is no longer live in its document.")
    members = list(getattr(analysis, "Group", ()) or ())
    counts: dict[str, int] = {}
    summaries: list[dict[str, Any]] = []
    identity_records = []
    for member in members:
        category = _member_category(member)
        counts[category] = counts.get(category, 0) + 1
        identity_records.append(
            {
                "object_name": str(member.Name),
                "object_id": int(member.ID),
                "type_id": str(member.TypeId),
                "suppressed": bool(getattr(member, "Suppressed", False)),
            }
        )
        if len(summaries) < MAX_ANALYSIS_MEMBERS:
            summaries.append({**concise_object(member), "category": category})
    study = study_intent_state(analysis)
    dependencies = study_dependency_state(analysis)
    result = {
        **concise_object(analysis),
        "study": study,
        "dependencies": dependencies,
        "member_count": len(members),
        "member_counts": counts,
        "members": summaries,
        "members_truncated": len(members) > len(summaries),
    }
    result["state_sha256"] = _digest(
        {
            "object_name": str(analysis.Name),
            "object_id": int(analysis.ID),
            "label": str(analysis.Label),
            "study": study,
            "dependencies": dependencies,
            "members": identity_records,
        }
    )
    return result


def material_kind(material: Any) -> str:
    proxy_type = str(getattr(getattr(material, "Proxy", None), "Type", "") or "")
    if proxy_type == "Fem::MaterialMechanicalNonlinear":
        return "nonlinear"
    if proxy_type == "Fem::MaterialReinforced":
        return "reinforced"
    if proxy_type == "Fem::MaterialCommon":
        return "fluid" if str(getattr(material, "Category", "")) == "Fluid" else "solid"
    raise NativeAnalyzeError(
        "The exact target is not a supported FEM material.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _reference_state(material: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visible: list[dict[str, Any]] = []
    exact: list[dict[str, Any]] = []
    for raw in tuple(getattr(material, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            continue
        source, subelements = raw
        record = {
            "object_name": str(getattr(source, "Name", "") or ""),
            "subelements": [str(value) for value in tuple(subelements or ())],
        }
        visible.append(record)
        exact.append({**record, "object_id": int(getattr(source, "ID", -1))})
    return visible, exact


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 12) if math.isfinite(number) else None


def normalized_material_properties(raw: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        from FreeCAD import Units
    except ImportError:
        Units = None
    for native_name, output_name, unit in _COMMON_PROPERTIES:
        if native_name not in raw:
            continue
        value = str(raw[native_name])
        if native_name == "Name":
            if value:
                result[output_name] = value[:160]
            continue
        try:
            quantity = Units.Quantity(value) if Units is not None else None
            number = (
                float(quantity.getValueAs(unit).Value)
                if unit is not None
                else float(quantity.Value)
            )
        except Exception:
            continue
        finite = _finite(number)
        if finite is not None:
            result[output_name] = finite
    return result


def _material_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _material_digest_payload(
    material: Any,
    *,
    kind: str,
    material_map: Mapping[str, str],
    reinforcement_map: Mapping[str, str],
    exact_references: list[dict[str, Any]],
    nonlinear: Any | None,
) -> dict[str, Any]:
    return {
        "object_name": str(material.Name),
        "object_id": int(material.ID),
        "label": str(material.Label),
        "kind": kind,
        "category": str(getattr(material, "Category", "") or ""),
        "uuid": str(getattr(material, "UUID", "") or ""),
        "material": dict(material_map),
        "references": exact_references,
        "nonlinear": (
            [str(nonlinear.Name), int(nonlinear.ID)] if nonlinear is not None else None
        ),
        "reinforcement_uuid": str(
            getattr(material, "ReinforcementUUID", "") or ""
        ),
        "reinforcement": dict(reinforcement_map),
        "model": str(getattr(material, "MaterialModelNonlinearity", "") or ""),
        "yield_points": [
            str(value) for value in tuple(getattr(material, "YieldPoints", ()) or ())
        ],
    }


def material_state(material: Any) -> dict[str, Any]:
    document = getattr(material, "Document", None)
    if not is_live(document, material):
        raise NativeAnalyzeError("The FEM material is no longer live in its document.")
    kind = material_kind(material)
    references, exact_references = _reference_state(material)
    material_map = _material_map(getattr(material, "Material", {}))
    reinforcement_map = _material_map(getattr(material, "Reinforcement", {}))
    nonlinear = (
        getattr(material, "Nonlinear", None)
        if kind in {"solid", "fluid", "reinforced"}
        else None
    )
    result: dict[str, Any] = {
        **concise_object(material),
        "material_kind": kind,
        "references": references,
    }
    if kind in {"solid", "fluid", "reinforced"}:
        result["material_uuid"] = str(getattr(material, "UUID", "") or "")
        result["properties"] = normalized_material_properties(material_map)
        names = sorted(material_map)[:MAX_MATERIAL_PROPERTY_NAMES]
        result["available_property_names"] = names
        result["property_names_truncated"] = len(material_map) > len(names)
        if nonlinear is not None:
            result["nonlinear"] = concise_object(nonlinear)
    if kind == "reinforced":
        result["reinforcement_uuid"] = str(
            getattr(material, "ReinforcementUUID", "") or ""
        )
        result["reinforcement_properties"] = normalized_material_properties(
            reinforcement_map
        )
        names = sorted(reinforcement_map)[:MAX_MATERIAL_PROPERTY_NAMES]
        result["available_reinforcement_property_names"] = names
        result["reinforcement_property_names_truncated"] = len(
            reinforcement_map
        ) > len(names)
    if kind == "nonlinear":
        native_model = str(material.MaterialModelNonlinearity)
        result["model"] = _NONLINEAR_MODELS.get(native_model, native_model)
        yield_points = []
        for raw in tuple(material.YieldPoints or ()):
            pieces = [piece.strip() for piece in str(raw).split(",")]
            if len(pieces) != 2:
                continue
            stress = _finite(pieces[0])
            strain = _finite(pieces[1])
            if stress is not None and strain is not None:
                yield_points.append({"stress_mpa": stress, "plastic_strain": strain})
        result["yield_points"] = yield_points
    result["state_sha256"] = _digest(
        _material_digest_payload(
            material,
            kind=kind,
            material_map=material_map,
            reinforcement_map=reinforcement_map,
            exact_references=exact_references,
            nonlinear=nonlinear,
        )
    )
    return result


def material_without_nonlinear_sha256(material: Any) -> str:
    """Hash one material exactly while excluding only its nonlinear link."""

    kind = material_kind(material)
    _visible, exact_references = _reference_state(material)
    payload = _material_digest_payload(
        material,
        kind=kind,
        material_map=_material_map(getattr(material, "Material", {})),
        reinforcement_map=_material_map(getattr(material, "Reinforcement", {})),
        exact_references=exact_references,
        nonlinear=None,
    )
    return _digest(payload)


def analysis_still_exact(analysis: Any, expected_sha256: str) -> bool:
    try:
        return analysis_state(analysis)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False


def material_still_exact(material: Any, expected_sha256: str) -> bool:
    try:
        return material_state(material)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
