# SPDX-License-Identifier: LGPL-2.1-or-later

"""Copy-ready connector discovery for reusable Assembly source references."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from VibeCADDocumentReferences import (
    normalize_document_reference,
    resolve_reference_target,
)
from VibeCADGeometrySelector import selections_for_subelements
from VibeCADNativeAssemblyInspect import (
    MAX_JOINT_CONNECTOR_PAIRS,
    NativeAssemblyInspectError,
    rank_connector_pairs,
    source_connector_inventory,
)
from vibescript_assembly_api import explicit_connector_compatibility


class AssemblyConnectorDiscoveryError(RuntimeError):
    pass


_OCCURRENCE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _published_interfaces(source: Any) -> tuple[bool, list[dict[str, Any]]]:
    import VibeCADReferenceContracts as contracts
    try:
        return contracts.component_interface_descriptors(source)
    except contracts.ReferenceContractError as exc:
        raise AssemblyConnectorDiscoveryError(str(exc)) from exc


def _interface_record(descriptor: Mapping[str, Any]) -> dict[str, Any] | None:
    import VibeCADReferenceContracts as contracts

    return contracts.connector_interface_record(descriptor)


def _geometry_records(
    source: Any,
    joint_type: str,
    *,
    preferred_only: bool,
) -> list[dict[str, Any]]:
    result = []
    for record in source_connector_inventory(
        source,
        joint_type,
        preferred_only=preferred_only,
    ):
        element = str(record.get("element") or "")
        if not element:
            if joint_type == "fixed":
                result.append(
                    {
                        **record,
                        "selection": {"type": "component_origin"},
                        "contract": None,
                    }
                )
            continue
        result.append({**record, "contract": None})
    return result


def _candidate_records(
    source: Any,
    joint_type: str,
    *,
    preferred_only: bool,
) -> tuple[bool, list[dict[str, Any]]]:
    semantic_only, descriptors = _published_interfaces(source)
    interfaces = [
        record
        for descriptor in descriptors
        if (record := _interface_record(descriptor)) is not None
        and explicit_connector_compatibility(
            joint_type,
            [record.get("contract")],
        )["ok"]
    ]
    if semantic_only:
        return True, interfaces
    return False, [
        *interfaces,
        *_geometry_records(
            source,
            joint_type,
            preferred_only=preferred_only,
        ),
    ]


def _endpoint(record: Mapping[str, Any], component: str) -> dict[str, Any]:
    return {
        "component": component,
        "selection": dict(record["selection"]),
    }


def _alignment(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "first_origin_mm": list(first.get("origin_mm") or [0.0, 0.0, 0.0]),
        "first_axis": list(first.get("axis") or [0.0, 0.0, 1.0]),
        "second_origin_mm": list(second.get("origin_mm") or [0.0, 0.0, 0.0]),
        "second_axis": list(second.get("axis") or [0.0, 0.0, 1.0]),
    }


def _with_selection(
    record: Mapping[str, Any],
    selections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    if isinstance(record.get("selection"), Mapping):
        return dict(record)
    element = str(record.get("element") or "")
    selection = selections.get(element)
    if selection is None:
        return None
    return {**dict(record), "selection": selection}


def read_source_connector_pairs(
    owner_document: Any,
    first_reference: Mapping[str, Any],
    second_reference: Mapping[str, Any],
    *,
    first_occurrence: str,
    second_occurrence: str,
    joint_type: str,
    limit: int,
) -> dict[str, Any]:
    """Find connector pairs on two exact reusable component definitions."""

    first_ref = normalize_document_reference(first_reference)
    second_ref = normalize_document_reference(second_reference)
    if first_ref == second_ref:
        raise AssemblyConnectorDiscoveryError("Choose two different components.")
    if type(limit) is not int or not 1 <= limit <= MAX_JOINT_CONNECTOR_PAIRS:
        raise AssemblyConnectorDiscoveryError("limit must be from 1 through 24.")
    if _OCCURRENCE_KEY.fullmatch(first_occurrence) is None:
        raise AssemblyConnectorDiscoveryError(
            "first_occurrence must be a stable identifier."
        )
    if _OCCURRENCE_KEY.fullmatch(second_occurrence) is None:
        raise AssemblyConnectorDiscoveryError(
            "second_occurrence must be a stable identifier."
        )
    if first_occurrence == second_occurrence:
        raise AssemblyConnectorDiscoveryError("Choose two different occurrence keys.")

    import FreeCAD as App

    initially_open = set(App.listDocuments())
    try:
        first = resolve_reference_target(
            owner_document,
            first_ref,
            "first_component",
        )
        second = resolve_reference_target(
            owner_document,
            second_ref,
            "second_component",
        )
        def ranked_pairs(preferred_only: bool):
            _first_semantic, first_records = _candidate_records(
                first,
                joint_type,
                preferred_only=preferred_only,
            )
            _second_semantic, second_records = _candidate_records(
                second,
                joint_type,
                preferred_only=preferred_only,
            )
            return rank_connector_pairs(
                first_records,
                second_records,
                joint_type=joint_type,
                limit=MAX_JOINT_CONNECTOR_PAIRS,
                compatible=lambda left, right: bool(
                    explicit_connector_compatibility(
                        joint_type,
                        [left.get("contract"), right.get("contract")],
                    )["ok"]
                ),
            )

        ranked = ranked_pairs(True)
        if not ranked:
            ranked = ranked_pairs(False)
        first_selections = selections_for_subelements(
            first.Shape,
            [str(item.get("element") or "") for pair in ranked for item in pair[:1]],
        )
        second_selections = selections_for_subelements(
            second.Shape,
            [str(item.get("element") or "") for pair in ranked for item in pair[1:]],
        )
        unique_pairs = []
        seen = set()
        for raw_left, raw_right in ranked:
            left = _with_selection(raw_left, first_selections)
            right = _with_selection(raw_right, second_selections)
            if left is None or right is None:
                continue
            key = (
                json.dumps(left["selection"], sort_keys=True),
                json.dumps(right["selection"], sort_keys=True),
            )
            if key in seen:
                continue
            seen.add(key)
            item = {
                "first": _endpoint(left, first_occurrence),
                "second": _endpoint(right, second_occurrence),
                "alignment": _alignment(left, right),
            }
            unique_pairs.append(item)
            if len(unique_pairs) == limit:
                break
        return {
            "joint_type": joint_type,
            "pairs": unique_pairs[:limit],
        }
    except (AssemblyConnectorDiscoveryError, NativeAssemblyInspectError):
        raise
    except Exception as exc:
        raise AssemblyConnectorDiscoveryError(str(exc)) from exc
    finally:
        for name in set(App.listDocuments()) - initially_open:
            App.closeDocument(name)
