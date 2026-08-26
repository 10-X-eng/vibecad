# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed FEM material maps and bounded native-card search."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import normalized_material_properties


_PROPERTY_FIELDS = {
    "name": ("Name", None, 160),
    "density_kg_m3": ("Density", "kg/m^3", 1.0e9),
    "young_modulus_mpa": ("YoungsModulus", "MPa", 1.0e12),
    "poisson_ratio": ("PoissonRatio", None, 0.5),
    "yield_strength_mpa": ("YieldStrength", "MPa", 1.0e12),
    "thermal_conductivity_w_m_k": ("ThermalConductivity", "W/m/K", 1.0e9),
    "thermal_expansion_per_k": ("ThermalExpansionCoefficient", "1/K", 1.0),
    "reference_temperature_k": ("ThermalExpansionReferenceTemperature", "K", 100_000.0),
    "specific_heat_j_kg_k": ("SpecificHeat", "J/kg/K", 1.0e12),
    "kinematic_viscosity_m2_s": ("KinematicViscosity", "m^2/s", 1.0e6),
}
_SPELLING_FOLDS = (("aluminium", "aluminum"), ("fibre", "fiber"))
_FLUID_PROPERTY_NAMES = frozenset({"KinematicViscosity", "DynamicViscosity"})
_SOLID_PROPERTY_NAMES = frozenset(
    native_name
    for field, (native_name, _unit, _maximum) in _PROPERTY_FIELDS.items()
    if field not in {"name", "kinematic_viscosity_m2_s"}
)


def _finite(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise NativeAnalyzeError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise NativeAnalyzeError(f"{field} must be one finite number.")
    return result


def _validate_property(field: str, value: Any) -> str:
    native, unit, maximum = _PROPERTY_FIELDS[field]
    if field == "name":
        result = str(value or "").strip()
        if not result or len(result) > maximum:
            raise NativeAnalyzeError("properties.name must contain 1 to 160 visible characters.")
        return result
    number = _finite(value, f"properties.{field}")
    if field in {
        "density_kg_m3",
        "young_modulus_mpa",
        "yield_strength_mpa",
        "specific_heat_j_kg_k",
    }:
        valid = 0.0 < number <= maximum
    elif field == "poisson_ratio":
        valid = -1.0 < number < 0.5
    elif field == "thermal_expansion_per_k":
        valid = -1.0 <= number <= maximum
    else:
        valid = 0.0 <= number <= maximum
    if not valid:
        raise NativeAnalyzeError(f"properties.{field} is outside its physical input bound.")
    return f"{number:.17g}{f' {unit}' if unit else ''}"


def typed_property_updates(value: Any, *, field_name: str = "properties") -> dict[str, str]:
    if not isinstance(value, Mapping) or not set(value) <= set(_PROPERTY_FIELDS):
        raise NativeAnalyzeError(
            f"{field_name} must contain only the published typed material properties."
        )
    return {
        _PROPERTY_FIELDS[str(field)][0]: _validate_property(str(field), item)
        for field, item in value.items()
    }


def cleared_native_properties(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > len(_PROPERTY_FIELDS):
        raise NativeAnalyzeError(
            f"{field_name} must list at most {len(_PROPERTY_FIELDS)} property names."
        )
    names = tuple(str(item) for item in value)
    if len(names) != len(set(names)) or any(name not in _PROPERTY_FIELDS for name in names):
        raise NativeAnalyzeError(
            f"{field_name} must contain unique published material property names."
        )
    return tuple(_PROPERTY_FIELDS[name][0] for name in names)


def _catalog_category(card: Any) -> str:
    properties = dict(getattr(card, "Properties", {}) or {})
    names = set(properties)
    if names.intersection(_FLUID_PROPERTY_NAMES):
        return "fluid"
    if names.intersection(_SOLID_PROPERTY_NAMES):
        return "solid"
    return "unsupported"


def resolve_material_card(uuid: Any, *, category: str) -> tuple[str, dict[str, str]]:
    value = str(uuid or "").strip().lower()
    if len(value) != 36:
        raise NativeAnalyzeError("material UUID must be one exact 36-character catalog UUID.")
    try:
        import Materials

        card = Materials.MaterialManager().getMaterial(value)
    except Exception as exc:
        raise NativeAnalyzeError(
            "The installed material catalog could not resolve the requested UUID.",
            error_code="NATIVE_ANALYZE_MATERIAL_CARD_UNAVAILABLE",
        ) from exc
    if card is None:
        raise NativeAnalyzeError(
            f"Material card {value} is not installed.",
            error_code="NATIVE_ANALYZE_MATERIAL_CARD_UNAVAILABLE",
        )
    actual = _catalog_category(card)
    if category in {"solid", "fluid"} and actual != category:
        raise NativeAnalyzeError(
            f"Material card {value} is {actual}, not {category}.",
            error_code="NATIVE_ANALYZE_MATERIAL_CATEGORY_MISMATCH",
        )
    properties = {str(key): str(item) for key, item in dict(card.Properties).items()}
    return value, properties


def resolve_material_card_name(
    name: Any,
    *,
    category: str,
) -> tuple[str, dict[str, str]]:
    value = str(name or "").strip()
    if not value or len(value) > 160:
        raise NativeAnalyzeError(
            "material_name must be one exact catalog name from 1 to 160 characters."
        )
    try:
        import Materials

        cards = list(dict(Materials.MaterialManager().Materials).values())
    except Exception as exc:
        raise NativeAnalyzeError(
            "The installed material catalog is unavailable.",
            error_code="NATIVE_ANALYZE_MATERIAL_CATALOG_UNAVAILABLE",
        ) from exc
    candidates = [
        card
        for card in cards
        if category == "any" or _catalog_category(card) == category
    ]
    matches = [
        card
        for card in candidates
        if str(getattr(card, "Name", "") or "") == value
    ]
    if not matches:
        matches = [
            card
            for card in candidates
            if str(getattr(card, "Name", "") or "").casefold()
            == value.casefold()
        ]
    if len(matches) != 1:
        raise NativeAnalyzeError(
            f"Material catalog name {value!r} does not identify one {category} material.",
            error_code="NATIVE_ANALYZE_MATERIAL_CARD_UNAVAILABLE",
        )
    card = matches[0]
    properties = {
        str(key): str(item) for key, item in dict(card.Properties).items()
    }
    return str(card.UUID).lower(), properties


def material_map(
    current: Mapping[str, Any] | None,
    *,
    category: str,
    material_uuid: Any | None = None,
    properties: Any | None = None,
    current_uuid: Any = "",
    clear_properties: Any | None = None,
) -> tuple[dict[str, str], str]:
    result = {str(key): str(item) for key, item in dict(current or {}).items()}
    uuid = str(current_uuid or "")
    if material_uuid is not None:
        uuid, result = resolve_material_card(material_uuid, category=category)
    updates = typed_property_updates(properties or {})
    cleared = cleared_native_properties(
        clear_properties or [], field_name="clear_properties"
    )
    for name in cleared:
        result.pop(name, None)
    result.update(updates)
    if updates or cleared:
        uuid = ""
    return result, uuid


def _normalize(value: Any) -> str:
    result = str(value or "").casefold()
    for british, american in _SPELLING_FOLDS:
        result = result.replace(british, american)
    return " ".join("".join(char if char.isalnum() else " " for char in result).split())


def search_material_catalog(query: Any, category: Any, limit: Any) -> dict[str, Any]:
    text = str(query or "")
    selected_category = str(category or "")
    if len(text) > 160 or selected_category not in {"any", "solid", "fluid"}:
        raise NativeAnalyzeError("Material catalog search arguments are outside their published bounds.")
    if type(limit) is not int or not 1 <= limit <= 25:
        raise NativeAnalyzeError("Material catalog limit must be an integer from 1 through 25.")
    normalized_query = _normalize(text)
    tokens = normalized_query.split()
    try:
        import Materials

        cards = list(dict(Materials.MaterialManager().Materials).values())
    except Exception as exc:
        raise NativeAnalyzeError(
            "The installed material catalog is unavailable.",
            error_code="NATIVE_ANALYZE_MATERIAL_CATALOG_UNAVAILABLE",
        ) from exc
    matches = []
    for card in cards:
        card_category = _catalog_category(card)
        if card_category not in {"solid", "fluid"}:
            continue
        if selected_category != "any" and card_category != selected_category:
            continue
        searchable = _normalize(
            " ".join(
                (
                    str(getattr(card, "Name", "") or ""),
                    str(getattr(card, "Directory", "") or ""),
                    str(getattr(card, "Description", "") or ""),
                    *[str(tag) for tag in tuple(getattr(card, "Tags", ()) or ())],
                )
            )
        )
        matched_tokens = tuple(token for token in tokens if token in searchable)
        if tokens and not matched_tokens:
            continue
        raw = {str(key): str(item) for key, item in dict(card.Properties).items()}
        normalized_name = _normalize(getattr(card, "Name", ""))
        matches.append(
            (
                (
                    int(normalized_name == normalized_query),
                    int(
                        bool(normalized_query)
                        and normalized_name.startswith(normalized_query)
                    ),
                    int(len(matched_tokens) == len(tokens)),
                    len(matched_tokens),
                ),
                {
                "uuid": str(card.UUID),
                "name": str(card.Name)[:160],
                "category": card_category,
                "description": str(getattr(card, "Description", "") or "")[:240],
                "properties": normalized_material_properties(raw),
                },
            )
        )
    matches.sort(
        key=lambda item: (
            *(-value for value in item[0]),
            item[1]["name"].casefold(),
            item[1]["uuid"],
        )
    )
    returned = [item for _score, item in matches[:limit]]
    return {
        "query": text,
        "category": selected_category,
        "match_count": len(matches),
        "returned_count": len(returned),
        "truncated": len(matches) > len(returned),
        "materials": returned,
    }
