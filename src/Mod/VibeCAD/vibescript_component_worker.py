# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated validation for component occurrences shared by VibeScript domains."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue

_REFERENCES: Mapping[tuple[str, str], dict[str, Any]] = MappingProxyType({})


def configure_component_references(entries: list[dict[str, Any]]) -> None:
    references: dict[tuple[str, str], dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"document_references[{index}] must be an object.")
        key = (
            str(entry.get("document_uid") or ""),
            str(entry.get("object_name") or ""),
        )
        if not all(key) or key in references:
            raise ValueError(f"document_references[{index}] has missing or duplicate identity.")
        references[key] = {
            name: entry.get(name)
            for name in (
                "document_uid",
                "object_name",
                "document_path",
                "label",
                "type_id",
                "source_kind",
                "source_program_id",
                "source_program_domain",
                "source_revision",
            )
            if entry.get(name) not in (None, "")
        }
    global _REFERENCES
    _REFERENCES = MappingProxyType(references)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise ValueError("A component output must be returned by api.component or api.instances.")
    if set(payload) != {"domain", "operation", "output_type", "arguments", "properties"}:
        raise ValueError("A component output has malformed graph fields.")
    return payload


def validate_component_definition(
    value: Any,
    *,
    domain: str,
    output_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _payload(value)
    if str(payload.get("domain") or "") != domain:
        raise ValueError(f"Component output {output_name!r} belongs to another domain.")
    if (
        str(payload.get("operation") or "") != "component"
        or str(payload.get("output_type") or "") != "component_link"
    ):
        raise ValueError(
            f"Component output {output_name!r} must come from api.component or api.instances."
        )
    arguments = list(payload.get("arguments") or [])
    properties = payload.get("properties")
    if len(arguments) != 1 or not isinstance(arguments[0], Mapping):
        raise ValueError(f"Component output {output_name!r} has no exact source reference.")
    if not isinstance(properties, Mapping):
        raise ValueError(f"Component output {output_name!r} has malformed properties.")
    reference = dict(arguments[0])
    key = (
        str(reference.get("document_uid") or ""),
        str(reference.get("object_name") or ""),
    )
    metadata = _REFERENCES.get(key)
    if metadata is None:
        raise ValueError(
            f"Component output {output_name!r} source {key[1]!r} was not staged from "
            "an x-vibecad-reference input."
        )
    placement = properties.get("placement")
    if not isinstance(placement, Mapping) or set(placement) != {"position", "rotation"}:
        raise ValueError(f"Component output {output_name!r} has malformed placement data.")
    position = list(placement.get("position") or [])
    rotation = list(placement.get("rotation") or [])
    if len(position) != 3 or len(rotation) != 4:
        raise ValueError(f"Component output {output_name!r} has malformed placement dimensions.")
    interfaces = properties.get("interfaces") or {}
    if not isinstance(interfaces, Mapping):
        raise ValueError(
            f"Component output {output_name!r} interfaces must be an object."
        )
    data = {
        "schema": "vibecad-component-occurrence-v1",
        "source": dict(reference),
        "source_metadata": dict(metadata),
        "placement": {
            "position": [float(item) for item in position],
            "rotation": [float(item) for item in rotation],
        },
        "placement_authored": bool(properties.get("placement_authored")),
        "label": str(properties.get("label") or output_name),
    }
    if interfaces:
        data["interface_declarations"] = dict(interfaces)
    return payload, data
