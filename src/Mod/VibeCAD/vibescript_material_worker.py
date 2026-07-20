# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated catalog resolver for production Material VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import re
import threading
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_material_api import MaterialDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-material-validation-v1"
CATALOG_INDEX_SCHEMA = "vibecad-material-catalog-index-v1"
MAX_CARD_JSON_BYTES = 1_000_000
MAX_CATALOG_CARDS = 256
MAX_CATALOG_TAGS = 32
MAX_DESCRIPTION_CHARS = 2_048
MAX_PROPERTY_DISPLAY_CHARS = 4_096
MAX_CATALOG_SELECTION_VALUE_CHARS = 256
_PHYSICAL_SELECTION_PROPERTIES = (
    "Density",
    "YoungsModulus",
    "PoissonRatio",
    "YieldStrength",
    "UltimateTensileStrength",
    "CompressiveStrength",
    "ThermalConductivity",
    "SpecificHeat",
    "ThermalExpansionCoefficient",
    "ElectricalConductivity",
)
_APPEARANCE_SELECTION_PROPERTIES = (
    "DiffuseColor",
    "Transparency",
)
_GRAPH_FIELDS = {"domain", "operation", "output_type", "arguments", "properties"}
_CARD_PROPERTIES = {
    "require_physical_properties",
    "require_appearance_properties",
}
_ASSIGN_PROPERTIES = {"label"}
_APPEARANCE_PROPERTIES = {
    "shape_color",
    "line_color",
    "point_color",
    "transparency",
    "line_width",
    "point_size",
    "display_mode",
    "visibility",
    "selectable",
    "label",
}
_APPEARANCE_NATIVE_NAMES = {
    "shape_color": "ShapeAppearance",
    "line_color": "LineColor",
    "point_color": "PointColor",
    "transparency": "ShapeAppearance",
    "line_width": "LineWidth",
    "point_size": "PointSize",
    "display_mode": "DisplayMode",
    "visibility": "Visibility",
    "selectable": "Selectable",
}
_CARD_COLOR_APPEARANCE = {
    "AmbientColor": "ambient_color",
    "DiffuseColor": "diffuse_color",
    "SpecularColor": "specular_color",
    "EmissiveColor": "emissive_color",
}
_CARD_SCALAR_APPEARANCE = {
    "Shininess": "shininess",
    "Transparency": "transparency",
}
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MATERIAL_CATALOG_LOCK = threading.RLock()


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded repair instruction for every native failure stage."""

    stage = str(details.get("stage") or "")
    path = str(details.get("path") or "")
    output_name = str(details.get("output_name") or "")
    target = details.get("target")
    target_name = (
        str(target.get("object_name") or "")
        if isinstance(target, Mapping)
        else str(target or "")
    )
    property_name = str(details.get("property") or "")
    if stage == "graph_contract":
        location = f" at {path}" if path else ""
        return (
            f"Rebuild only the malformed Material value{location} with api.material, "
            "api.assign, or api.appearance, and return it under the unchanged declared "
            "result name."
        )
    if stage in {"catalog_open", "host_contract"}:
        return (
            "Keep the Material source unchanged and retry only after the reported native "
            "catalog or captured-host contract is available and internally consistent."
        )
    if stage == "catalog_resolution":
        return (
            "Replace only the failing material_uuid with one exact UUID currently listed "
            "in material_catalog; never guess by name or filesystem location."
        )
    if stage == "catalog_requirements":
        return (
            "Choose a catalog card that lists every consumed property, or remove only a "
            "requirement the design does not consume; keep the output and target unchanged."
        )
    if stage in {"catalog_property_lookup", "catalog_property_readback"}:
        location = f" {property_name!r}" if property_name else ""
        return (
            f"Choose a card with a readable native material property{location}, or retry "
            "after repairing the catalog; do not substitute an unverified literal value."
        )
    if stage == "catalog_appearance":
        return (
            "Choose a card with a valid standard appearance, or remove the card from "
            "api.appearance and provide the intended explicit display fields there."
        )
    if stage == "target_resolution":
        return (
            "Replace only the target with one current material_targets reference copied "
            "through program inputs; never invent document_uid or object_name."
        )
    if stage == "target_capability":
        location = f" {target_name!r}" if target_name else ""
        return (
            f"Remove only the unsupported field for Material target{location}, or choose "
            "an eligible current target that lists the required physical/view capability."
        )
    if stage == "ownership_contract":
        location = f" {output_name!r}" if output_name else ""
        return (
            f"Merge redundant output{location} into one output per target/channel. Keep "
            "physical api.assign and display api.appearance separate when both are required."
        )
    return (
        "Correct only the reported Material definition field and retry the failed working "
        "revision; do not recreate the program or change unrelated targets."
    )


class MaterialCandidateError(RuntimeError):
    """A model-facing Material failure with exact corrective details."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            self.details["correction"] = _default_correction(self.details)
        super().__init__(message)


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def definition_sha256(value: Mapping[str, Any]) -> str:
    """Return the stable digest used to bind native validation to a graph."""

    return _json_sha256(dict(value))


def _payload(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise MaterialCandidateError(
            f"{context} must be a value returned by the active Material api.",
            details={"stage": "graph_contract", "path": context},
        )
    if set(payload) != _GRAPH_FIELDS:
        raise MaterialCandidateError(
            f"{context} has malformed Material graph fields.",
            details={
                "stage": "graph_contract",
                "path": context,
                "missing": sorted(_GRAPH_FIELDS - set(payload)),
                "unexpected": sorted(set(payload) - _GRAPH_FIELDS),
            },
        )
    if not isinstance(payload.get("arguments"), list) or not isinstance(
        payload.get("properties"), dict
    ):
        raise MaterialCandidateError(
            f"{context} arguments and properties must be serialized containers.",
            details={"stage": "graph_contract", "path": context},
        )
    return payload


def _expect_graph(
    value: Any,
    *,
    operation: str,
    output_type: str,
    argument_count: int,
    property_names: set[str],
    context: str,
) -> dict[str, Any]:
    payload = _payload(value, context=context)
    observed = (
        str(payload.get("domain") or ""),
        str(payload.get("operation") or ""),
        str(payload.get("output_type") or ""),
    )
    expected = ("material", operation, output_type)
    arguments = list(payload["arguments"])
    properties = dict(payload["properties"])
    if observed != expected or len(arguments) != argument_count or set(properties) != property_names:
        raise MaterialCandidateError(
            f"{context} does not match the exact api.{operation} schema.",
            details={
                "stage": "graph_contract",
                "path": context,
                "expected_graph": list(expected),
                "observed_graph": list(observed),
                "expected_argument_count": argument_count,
                "received_argument_count": len(arguments),
                "missing_properties": sorted(property_names - set(properties)),
                "unexpected_properties": sorted(set(properties) - property_names),
                "correction": f"Rebuild this value with api.{operation}.",
            },
        )
    return payload


def _first_difference(expected: Any, observed: Any, path: str = "definition") -> str:
    if type(expected) is not type(observed):
        return f"{path} has type {type(observed).__name__}, expected {type(expected).__name__}"
    if isinstance(expected, dict):
        if set(expected) != set(observed):
            return f"{path} has different fields"
        for key in expected:
            if expected[key] != observed[key]:
                return _first_difference(expected[key], observed[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(expected) != len(observed):
            return f"{path} has {len(observed)} items, expected {len(expected)}"
        for index, (left, right) in enumerate(zip(expected, observed)):
            if left != right:
                return _first_difference(left, right, f"{path}[{index}]")
    return f"{path} is not in canonical form"


def _rebuild_card(raw: Any, *, context: str, api: MaterialDomainAPI) -> DomainValue:
    payload = _expect_graph(
        raw,
        operation="material",
        output_type="material_card",
        argument_count=1,
        property_names=_CARD_PROPERTIES,
        context=context,
    )
    arguments = list(payload["arguments"])
    properties = dict(payload["properties"])
    try:
        return api.material(
            arguments[0],
            require_physical_properties=properties["require_physical_properties"],
            require_appearance_properties=properties["require_appearance_properties"],
        )
    except (TypeError, ValueError) as exc:
        raise MaterialCandidateError(
            f"{context} is invalid: {exc}",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": "Use api.material with an exact catalog UUID and valid requirements.",
            },
        ) from exc


def validate_material_definition(
    value: Any,
    *,
    expected_output_type: str,
    context: str,
) -> dict[str, Any]:
    """Reconstruct a definition through the explicit API and require exact form."""

    api = MaterialDomainAPI(
        ("material", "assign", "appearance"),
        ("material_assignment", "appearance"),
    )
    if expected_output_type == "material_assignment":
        payload = _expect_graph(
            value,
            operation="assign",
            output_type="material_assignment",
            argument_count=2,
            property_names=_ASSIGN_PROPERTIES,
            context=context,
        )
        arguments = list(payload["arguments"])
        properties = dict(payload["properties"])
        card = _rebuild_card(arguments[1], context=f"{context}.card", api=api)
        try:
            rebuilt = api.assign(arguments[0], card, label=properties["label"])
        except (TypeError, ValueError) as exc:
            raise MaterialCandidateError(
                f"{context} is invalid: {exc}",
                details={
                    "stage": "graph_contract",
                    "path": context,
                    "correction": "Use api.assign with an input reference and api.material card.",
                },
            ) from exc
    elif expected_output_type == "appearance":
        payload = _expect_graph(
            value,
            operation="appearance",
            output_type="appearance",
            argument_count=2,
            property_names=_APPEARANCE_PROPERTIES,
            context=context,
        )
        arguments = list(payload["arguments"])
        properties = dict(payload["properties"])
        card = (
            None
            if arguments[1] is None
            else _rebuild_card(arguments[1], context=f"{context}.card", api=api)
        )
        try:
            rebuilt = api.appearance(arguments[0], card, **properties)
        except (TypeError, ValueError) as exc:
            raise MaterialCandidateError(
                f"{context} is invalid: {exc}",
                details={
                    "stage": "graph_contract",
                    "path": context,
                    "correction": (
                        "Use api.appearance with an optional api.material card and/or a "
                        "supported explicit display subset."
                    ),
                },
            ) from exc
    else:
        raise MaterialCandidateError(
            f"{context} has unsupported Material output type {expected_output_type!r}.",
            details={"stage": "graph_contract", "path": context},
        )
    canonical = rebuilt.to_payload()
    serialized = _payload(value, context=context)
    if canonical != serialized:
        raise MaterialCandidateError(
            f"{context} is not canonical: {_first_difference(canonical, serialized)}.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": "Return the immutable value directly from the active Material api.",
            },
        )
    return canonical


def _string(value: Any, *, limit: int = MAX_PROPERTY_DISPLAY_CHARS) -> str:
    text = str(value if value is not None else "")
    return text[:limit]


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}


def _canonical_card(card: Any) -> dict[str, Any]:
    value = {
        "uuid": str(getattr(card, "UUID", "") or "").lower(),
        "name": str(getattr(card, "Name", "") or ""),
        "description": str(getattr(card, "Description", "") or ""),
        "parent": str(getattr(card, "Parent", "") or "").lower(),
        "library_name": str(getattr(card, "LibraryName", "") or ""),
        "physical_models": sorted(
            str(item).lower() for item in list(getattr(card, "PhysicalModels", []) or [])
        ),
        "appearance_models": sorted(
            str(item).lower() for item in list(getattr(card, "AppearanceModels", []) or [])
        ),
        "tags": sorted(str(item) for item in list(getattr(card, "Tags", []) or [])),
        "physical_properties": _string_dict(getattr(card, "PhysicalProperties", {}) or {}),
        "appearance_properties": _string_dict(
            getattr(card, "AppearanceProperties", {}) or {}
        ),
        "properties": _string_dict(getattr(card, "Properties", {}) or {}),
    }
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_CARD_JSON_BYTES:
        raise MaterialCandidateError(
            "A native material card exceeds the bounded validation contract.",
            details={
                "stage": "catalog_resolution",
                "card_json_bytes": len(encoded),
                "maximum_json_bytes": MAX_CARD_JSON_BYTES,
            },
        )
    return value


def material_card_digest(card: Any) -> str:
    """Digest all bounded identity, model, and property content of a native card."""

    return _json_sha256(_canonical_card(card))


def _required_property_readback(
    card: Any,
    names: list[str],
    *,
    physical: bool,
) -> list[dict[str, Any]]:
    has_method = card.hasPhysicalProperty if physical else card.hasAppearanceProperty
    value_method = card.getPhysicalValue if physical else card.getAppearanceValue
    raw_properties = (
        _string_dict(getattr(card, "PhysicalProperties", {}) or {})
        if physical
        else _string_dict(getattr(card, "AppearanceProperties", {}) or {})
    )
    missing: list[str] = []
    empty: list[str] = []
    readback: list[dict[str, Any]] = []
    for name in names:
        try:
            present = bool(has_method(name))
        except Exception as exc:
            raise MaterialCandidateError(
                f"Native material property lookup failed for {name!r}: {exc}",
                details={
                    "stage": "catalog_property_lookup",
                    "property": name,
                    "property_kind": "physical" if physical else "appearance",
                    "native_exception": type(exc).__name__,
                    "native_error": str(exc),
                },
            ) from exc
        if not present:
            missing.append(name)
            continue
        try:
            native_value = value_method(name)
        except Exception as exc:
            raise MaterialCandidateError(
                f"Native material property {name!r} could not be read: {exc}",
                details={
                    "stage": "catalog_property_readback",
                    "property": name,
                    "property_kind": "physical" if physical else "appearance",
                    "native_exception": type(exc).__name__,
                    "native_error": str(exc),
                },
            ) from exc
        raw = raw_properties.get(name, str(native_value))
        if native_value is None or not str(raw).strip():
            empty.append(name)
            continue
        sensitive = any(token in name.lower() for token in ("file", "url", "source", "path"))
        item = {
            "name": name,
            "value_sha256": hashlib.sha256(str(raw).encode("utf-8")).hexdigest(),
            "display": "<catalog path or URL omitted>" if sensitive else _string(raw),
            "display_truncated": not sensitive and len(str(raw)) > MAX_PROPERTY_DISPLAY_CHARS,
        }
        readback.append(item)
    if missing or empty:
        kind = "physical" if physical else "appearance"
        available = sorted(raw_properties)
        raise MaterialCandidateError(
            f"Material card {getattr(card, 'Name', '')!r} does not provide usable "
            f"required {kind} properties: {', '.join([*missing, *empty])}.",
            details={
                "stage": "catalog_requirements",
                "material_uuid": str(getattr(card, "UUID", "") or ""),
                "material_name": str(getattr(card, "Name", "") or ""),
                "property_kind": kind,
                "missing_properties": missing,
                "empty_properties": empty,
                "available_properties": available[:256],
                "available_properties_truncated": len(available) > 256,
                "correction": (
                    "Choose a catalog UUID whose domain context lists every required property, "
                    "or remove requirements the design does not consume."
                ),
            },
        )
    return readback


def material_card_record(
    card: Any,
    *,
    required_physical_properties: list[str] | tuple[str, ...] = (),
    required_appearance_properties: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return bounded, deterministic native card identity and requirement readback."""

    canonical = _canonical_card(card)
    material_uuid = canonical["uuid"]
    if not _UUID.fullmatch(material_uuid):
        raise MaterialCandidateError(
            "The resolved native material has no canonical UUID.",
            details={
                "stage": "catalog_resolution",
                "material_name": canonical["name"],
                "observed_uuid": material_uuid,
            },
        )
    physical = list(required_physical_properties)
    appearance = list(required_appearance_properties)
    return {
        "uuid": material_uuid,
        "name": canonical["name"],
        "description": canonical["description"][:MAX_DESCRIPTION_CHARS],
        "description_truncated": len(canonical["description"]) > MAX_DESCRIPTION_CHARS,
        "library_name": canonical["library_name"],
        "physical_models": canonical["physical_models"],
        "appearance_models": canonical["appearance_models"],
        "tags": canonical["tags"][:MAX_CATALOG_TAGS],
        "tags_truncated": len(canonical["tags"]) > MAX_CATALOG_TAGS,
        "physical_property_names": sorted(canonical["physical_properties"]),
        "appearance_property_names": sorted(canonical["appearance_properties"]),
        "required_physical_properties": _required_property_readback(
            card,
            physical,
            physical=True,
        ),
        "required_appearance_properties": _required_property_readback(
            card,
            appearance,
            physical=False,
        ),
        "card_sha256": _json_sha256(canonical),
    }


def _card_color(value: Any, *, property_name: str, card: Any) -> list[float]:
    text = str(value or "").strip()
    if not (text.startswith("(") and text.endswith(")")):
        raise MaterialCandidateError(
            f"Material appearance property {property_name!r} is not a native color tuple.",
            details={
                "stage": "catalog_appearance",
                "material_uuid": str(getattr(card, "UUID", "") or ""),
                "material_name": str(getattr(card, "Name", "") or ""),
                "property": property_name,
                "correction": "Choose a card with a valid standard Material appearance model.",
            },
        )
    raw_channels = [item.strip() for item in text[1:-1].split(",")]
    if len(raw_channels) not in {3, 4}:
        raise MaterialCandidateError(
            f"Material appearance property {property_name!r} must have three or four channels.",
            details={
                "stage": "catalog_appearance",
                "material_uuid": str(getattr(card, "UUID", "") or ""),
                "material_name": str(getattr(card, "Name", "") or ""),
                "property": property_name,
                "channel_count": len(raw_channels),
            },
        )
    try:
        channels = [float(item) for item in raw_channels]
    except ValueError as exc:
        raise MaterialCandidateError(
            f"Material appearance property {property_name!r} has a non-numeric channel.",
            details={
                "stage": "catalog_appearance",
                "material_uuid": str(getattr(card, "UUID", "") or ""),
                "material_name": str(getattr(card, "Name", "") or ""),
                "property": property_name,
            },
        ) from exc
    if any(not math.isfinite(item) or not 0.0 <= item <= 1.0 for item in channels):
        raise MaterialCandidateError(
            f"Material appearance property {property_name!r} has a channel outside 0-1.",
            details={
                "stage": "catalog_appearance",
                "material_uuid": str(getattr(card, "UUID", "") or ""),
                "material_name": str(getattr(card, "Name", "") or ""),
                "property": property_name,
            },
        )
    if len(channels) == 3:
        channels.append(1.0)
    return channels


def material_card_appearance(card: Any) -> dict[str, Any]:
    """Return the card's standard native display material as bounded JSON."""

    result: dict[str, Any] = {}
    for native_name, output_name in _CARD_COLOR_APPEARANCE.items():
        try:
            present = bool(card.hasAppearanceProperty(native_name))
            value = card.getAppearanceValue(native_name) if present else None
        except Exception as exc:
            raise MaterialCandidateError(
                f"Native material appearance lookup failed for {native_name!r}: {exc}",
                details={
                    "stage": "catalog_appearance",
                    "material_uuid": str(getattr(card, "UUID", "") or ""),
                    "material_name": str(getattr(card, "Name", "") or ""),
                    "property": native_name,
                    "native_exception": type(exc).__name__,
                    "native_error": str(exc),
                },
            ) from exc
        if present:
            result[output_name] = _card_color(
                value,
                property_name=native_name,
                card=card,
            )
    for native_name, output_name in _CARD_SCALAR_APPEARANCE.items():
        try:
            present = bool(card.hasAppearanceProperty(native_name))
            value = card.getAppearanceValue(native_name) if present else None
            clean = float(value) if present else None
        except Exception as exc:
            raise MaterialCandidateError(
                f"Native material appearance lookup failed for {native_name!r}: {exc}",
                details={
                    "stage": "catalog_appearance",
                    "material_uuid": str(getattr(card, "UUID", "") or ""),
                    "material_name": str(getattr(card, "Name", "") or ""),
                    "property": native_name,
                    "native_exception": type(exc).__name__,
                    "native_error": str(exc),
                },
            ) from exc
        if present:
            if clean is None or not math.isfinite(clean) or not 0.0 <= clean <= 1.0:
                raise MaterialCandidateError(
                    f"Material appearance property {native_name!r} must be in the range 0-1.",
                    details={
                        "stage": "catalog_appearance",
                        "material_uuid": str(getattr(card, "UUID", "") or ""),
                        "material_name": str(getattr(card, "Name", "") or ""),
                        "property": native_name,
                    },
                )
            result[output_name] = clean
    if not result:
        raise MaterialCandidateError(
            f"Material card {getattr(card, 'Name', '')!r} has no standard display appearance.",
            details={
                "stage": "catalog_appearance",
                "material_uuid": str(getattr(card, "UUID", "") or ""),
                "material_name": str(getattr(card, "Name", "") or ""),
                "required_standard_properties": sorted(
                    {*_CARD_COLOR_APPEARANCE, *_CARD_SCALAR_APPEARANCE}
                ),
                "correction": (
                    "Choose a card with a standard appearance model, or omit the card and "
                    "provide explicit display fields."
                ),
            },
        )
    return result


def resolve_material_appearance(
    requested: Mapping[str, Any],
    card_appearance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge explicit fields over a card style into the publication request."""

    resolved = dict(requested)
    resolved["shape_material"] = None
    if card_appearance is None:
        return resolved
    shape_material = {
        str(name): list(value) if isinstance(value, list) else value
        for name, value in card_appearance.items()
    }
    shape_color = requested.get("shape_color")
    if shape_color is not None:
        previous = list(shape_material.get("diffuse_color") or [0.0, 0.0, 0.0, 1.0])
        shape_material["diffuse_color"] = [
            *[float(value) for value in list(shape_color)],
            float(previous[3]) if len(previous) == 4 else 1.0,
        ]
        resolved["shape_color"] = None
    transparency = requested.get("transparency")
    if transparency is not None:
        shape_material["transparency"] = float(transparency) / 100.0
        resolved["transparency"] = None
    resolved["shape_material"] = shape_material
    return resolved


def material_catalog_index(*, limit: int = MAX_CATALOG_CARDS) -> dict[str, Any]:
    """Resolve a bounded provider-facing index without exposing catalog paths."""

    import Materials

    with MATERIAL_CATALOG_LOCK:
        manager = Materials.MaterialManager()
        cards = list(dict(manager.Materials).values())
        cards.sort(
            key=lambda card: (
                str(getattr(card, "Name", "") or "").casefold(),
                str(getattr(card, "UUID", "") or ""),
            )
        )
        safe_limit = max(1, min(int(limit), MAX_CATALOG_CARDS))
        records = []
        for card in cards[:safe_limit]:
            canonical = _canonical_card(card)
            physical_values = {
                name: _string(
                    canonical["physical_properties"][name],
                    limit=MAX_CATALOG_SELECTION_VALUE_CHARS,
                )
                for name in _PHYSICAL_SELECTION_PROPERTIES
                if str(canonical["physical_properties"].get(name) or "").strip()
            }
            physical_values_truncated = [
                name
                for name in physical_values
                if len(str(canonical["physical_properties"][name]))
                > MAX_CATALOG_SELECTION_VALUE_CHARS
            ]
            appearance_values = {
                name: _string(
                    canonical["appearance_properties"][name],
                    limit=MAX_CATALOG_SELECTION_VALUE_CHARS,
                )
                for name in _APPEARANCE_SELECTION_PROPERTIES
                if str(canonical["appearance_properties"].get(name) or "").strip()
            }
            appearance_values_truncated = [
                name
                for name in appearance_values
                if len(str(canonical["appearance_properties"][name]))
                > MAX_CATALOG_SELECTION_VALUE_CHARS
            ]
            records.append(
                {
                    "uuid": canonical["uuid"],
                    "name": canonical["name"],
                    "library_name": canonical["library_name"],
                    "tags": canonical["tags"][:MAX_CATALOG_TAGS],
                    "tags_truncated": len(canonical["tags"]) > MAX_CATALOG_TAGS,
                    "physical_property_names": sorted(canonical["physical_properties"]),
                    "appearance_property_names": sorted(canonical["appearance_properties"]),
                    "selection_physical_values": physical_values,
                    "selection_physical_values_truncated": physical_values_truncated,
                    "selection_appearance_values": appearance_values,
                    "selection_appearance_values_truncated": (
                        appearance_values_truncated
                    ),
                }
            )
    return {
        "schema": CATALOG_INDEX_SCHEMA,
        "catalog_count": len(cards),
        "card_limit": safe_limit,
        "cards_truncated": len(cards) > len(records),
        "cards_omitted": max(0, len(cards) - len(records)),
        "selection_value_character_limit": MAX_CATALOG_SELECTION_VALUE_CHARS,
        "selection_physical_property_priority": list(_PHYSICAL_SELECTION_PROPERTIES),
        "selection_appearance_property_priority": list(
            _APPEARANCE_SELECTION_PROPERTIES
        ),
        "selection_contract": (
            "Use exact UUID plus name/tags and these common bounded values to select a "
            "candidate card. physical_property_names and appearance_property_names are the "
            "complete availability lists. Declare every consumed property in api.material; "
            "inspect accepted output validation for its exact authenticated value."
        ),
        "cards": records,
    }


def _target_reference(definition: Mapping[str, Any]) -> dict[str, str]:
    arguments = list(definition["arguments"])
    return dict(arguments[0])


def _target_by_reference(
    reference: Mapping[str, Any],
    *,
    document_uid: str,
    targets: Mapping[tuple[str, str], Mapping[str, Any]],
    output_name: str,
) -> Mapping[str, Any]:
    key = (
        str(reference.get("document_uid") or ""),
        str(reference.get("object_name") or ""),
    )
    target = targets.get(key)
    if key[0] != document_uid or target is None:
        raise MaterialCandidateError(
            f"Material output {output_name!r} refers to unavailable target {key[1]!r}.",
            details={
                "stage": "target_resolution",
                "output_name": output_name,
                "target": dict(reference),
                "correction": "Pass one current domain-context reference through program inputs.",
            },
        )
    return target


def appearance_controlled_properties(properties: Mapping[str, Any]) -> list[str]:
    controlled = {
        native
        for key, native in _APPEARANCE_NATIVE_NAMES.items()
        if properties.get(key) is not None
    }
    if properties.get("shape_material") is not None:
        controlled.add("ShapeAppearance")
    return sorted(controlled)


def validate_and_resolve_materials(
    result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    *,
    document_uid: str,
    material_targets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate exact graphs and resolve every physical card in the isolated worker."""

    target_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw in material_targets:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("reference"), Mapping):
            raise MaterialCandidateError(
                "The host supplied a malformed Material target snapshot.",
                details={"stage": "host_contract"},
            )
        reference = raw["reference"]
        key = (
            str(reference.get("document_uid") or ""),
            str(reference.get("object_name") or ""),
        )
        if key in target_map:
            raise MaterialCandidateError(
                "The host supplied duplicate Material target identities.",
                details={"stage": "host_contract", "target": dict(reference)},
            )
        target_map[key] = raw

    manager = None
    card_cache: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]] = {}
    native_card_cache: dict[str, Any] = {}
    outputs: list[dict[str, Any]] = []
    owned_channels: set[tuple[str, str]] = set()
    assignment_count = 0
    appearance_count = 0

    def resolve_card(
        card_definition: Mapping[str, Any],
        *,
        output_name: str,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...], Any, dict[str, Any]]:
        nonlocal manager
        card_uuid = str(list(card_definition["arguments"])[0])
        card_properties = dict(card_definition["properties"])
        required_physical = tuple(card_properties["require_physical_properties"])
        required_appearance = tuple(card_properties["require_appearance_properties"])
        cache_key = (card_uuid, required_physical, required_appearance)
        card = native_card_cache.get(card_uuid)
        card_record = card_cache.get(cache_key)
        if card is None:
            if manager is None:
                try:
                    import Materials

                    with MATERIAL_CATALOG_LOCK:
                        manager = Materials.MaterialManager()
                except Exception as exc:
                    raise MaterialCandidateError(
                        f"The native Material catalog is unavailable: {exc}",
                        details={
                            "stage": "catalog_open",
                            "native_exception": type(exc).__name__,
                            "native_error": str(exc),
                            "correction": (
                                "Install or repair the FreeCAD Material module and catalog."
                            ),
                        },
                    ) from exc
            try:
                with MATERIAL_CATALOG_LOCK:
                    card = manager.getMaterial(card_uuid)
            except Exception as exc:
                raise MaterialCandidateError(
                    f"Material card {card_uuid!r} could not be resolved: {exc}",
                    details={
                        "stage": "catalog_resolution",
                        "output_name": output_name,
                        "material_uuid": card_uuid,
                        "native_exception": type(exc).__name__,
                        "native_error": str(exc),
                        "correction": (
                            "Choose an exact UUID from material_catalog in domain context."
                        ),
                    },
                ) from exc
            if card is None:
                raise MaterialCandidateError(
                    f"Material card {card_uuid!r} does not exist in the active catalog.",
                    details={
                        "stage": "catalog_resolution",
                        "output_name": output_name,
                        "material_uuid": card_uuid,
                        "correction": (
                            "Choose an exact UUID from material_catalog in domain context."
                        ),
                    },
                )
            native_card_cache[card_uuid] = card
        if card_record is None:
            with MATERIAL_CATALOG_LOCK:
                card_record = material_card_record(
                    card,
                    required_physical_properties=required_physical,
                    required_appearance_properties=required_appearance,
                )
            if card_record["uuid"] != card_uuid:
                raise MaterialCandidateError(
                    "The native catalog returned a card with a different UUID.",
                    details={
                        "stage": "catalog_resolution",
                        "requested_uuid": card_uuid,
                        "resolved_uuid": card_record["uuid"],
                    },
                )
            card_cache[cache_key] = card_record
        return (
            card_uuid,
            required_physical,
            required_appearance,
            card,
            card_record,
        )

    for declaration in expected_outputs:
        name = str(declaration.get("name") or "")
        output_type = str(declaration.get("type") or "")
        definition = validate_material_definition(
            result[name],
            expected_output_type=output_type,
            context=f"result.{name}",
        )
        target_reference = _target_reference(definition)
        target = _target_by_reference(
            target_reference,
            document_uid=document_uid,
            targets=target_map,
            output_name=name,
        )
        if target.get("managed_material_output") is True:
            raise MaterialCandidateError(
                f"Material output {name!r} cannot target another managed Material carrier.",
                details={
                    "stage": "target_capability",
                    "output_name": name,
                    "target": target_reference,
                    "correction": "Choose the underlying design object, not a VibeScript ownership carrier.",
                },
            )
        channel = "physical" if output_type == "material_assignment" else "appearance"
        ownership_key = (str(target_reference["object_name"]), channel)
        if ownership_key in owned_channels:
            raise MaterialCandidateError(
                f"Material output {name!r} duplicates {channel} ownership of target "
                f"{ownership_key[0]!r}.",
                details={
                    "stage": "ownership_contract",
                    "output_name": name,
                    "target": target_reference,
                    "channel": channel,
                    "correction": "Return at most one output per target and ownership channel.",
                },
            )
        owned_channels.add(ownership_key)

        validation: dict[str, Any] = {
            "schema": VALIDATION_SCHEMA,
            "output_name": name,
            "output_type": output_type,
            "definition_sha256": definition_sha256(definition),
            "target": target_reference,
            "target_type_id": str(target.get("type_id") or ""),
            "channel": channel,
        }
        if output_type == "material_assignment":
            if target.get("physical_assignment_supported") is not True:
                raise MaterialCandidateError(
                    f"Target {target_reference['object_name']!r} has no native ShapeMaterial property.",
                    details={
                        "stage": "target_capability",
                        "output_name": name,
                        "target": target_reference,
                        "target_type_id": target.get("type_id"),
                        "required_property": "ShapeMaterial",
                        "correction": "Choose a shaped Part feature listed as physical-assignment capable.",
                    },
                )
            card_definition = dict(list(definition["arguments"])[1])
            _card_uuid, _required_physical, _required_appearance, _card, card_record = (
                resolve_card(card_definition, output_name=name)
            )
            validation["material_card"] = card_record
            assignment_count += 1
        else:
            requested = dict(definition["properties"])
            requested.pop("label")
            card_definition = list(definition["arguments"])[1]
            card_record = None
            card_appearance = None
            if card_definition is not None:
                (
                    _card_uuid,
                    _required_physical,
                    _required_appearance,
                    card,
                    card_record,
                ) = resolve_card(dict(card_definition), output_name=name)
                with MATERIAL_CATALOG_LOCK:
                    card_appearance = material_card_appearance(card)
            resolved = resolve_material_appearance(requested, card_appearance)
            controlled = appearance_controlled_properties(resolved)
            supported = list(target.get("appearance_supported_properties") or [])
            missing = sorted(set(controlled) - set(supported))
            if missing:
                raise MaterialCandidateError(
                    f"Target {target_reference['object_name']!r} cannot apply requested "
                    f"appearance properties: {', '.join(missing)}.",
                    details={
                        "stage": "target_capability",
                        "output_name": name,
                        "target": target_reference,
                        "target_type_id": target.get("type_id"),
                        "missing_native_properties": missing,
                        "supported_native_properties": supported,
                        "correction": "Remove unsupported appearance fields or choose a display-capable target.",
                    },
                )
            display_mode = resolved.get("display_mode")
            display_modes = list(target.get("display_modes") or [])
            if display_mode is not None and display_mode not in display_modes:
                raise MaterialCandidateError(
                    f"Target {target_reference['object_name']!r} does not support display mode "
                    f"{display_mode!r}.",
                    details={
                        "stage": "target_capability",
                        "output_name": name,
                        "target": target_reference,
                        "requested_display_mode": display_mode,
                        "available_display_modes": display_modes,
                        "correction": "Choose one exact display mode listed for the target in domain context.",
                    },
                )
            validation["requested"] = requested
            validation["resolved"] = resolved
            validation["controlled_properties"] = controlled
            validation["material_card"] = card_record
            validation["card_appearance"] = card_appearance
            appearance_count += 1
        outputs.append(
            {
                "name": name,
                "type": output_type,
                "definition": definition,
                "material_validation": validation,
            }
        )

    summaries = [
        {
            "output_name": item["name"],
            "output_type": item["type"],
            "target": dict(item["material_validation"]["target"]),
            "channel": item["material_validation"]["channel"],
            **(
                {
                    "material_uuid": item["material_validation"]["material_card"]["uuid"],
                    "card_sha256": item["material_validation"]["material_card"]["card_sha256"],
                }
                if item["type"] == "material_assignment"
                else {
                    "controlled_properties": list(
                        item["material_validation"]["controlled_properties"]
                    ),
                    "material_uuid": str(
                        dict(item["material_validation"].get("material_card") or {}).get(
                            "uuid"
                        )
                        or ""
                    ),
                    "card_sha256": str(
                        dict(item["material_validation"].get("material_card") or {}).get(
                            "card_sha256"
                        )
                        or ""
                    ),
                }
            ),
        }
        for item in outputs
    ]
    validation = {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "assignment_count": assignment_count,
        "appearance_count": appearance_count,
        "unique_target_count": len({item["target"]["object_name"] for item in summaries}),
        "resolved_card_count": len(card_cache),
        "outputs": summaries,
    }
    return outputs, validation
