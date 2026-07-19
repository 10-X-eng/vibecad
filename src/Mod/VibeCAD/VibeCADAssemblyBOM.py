# SPDX-License-Identifier: LGPL-2.1-or-later

"""Pure, bounded planning for model-authored native Assembly BOMs.

The same planner is used by the isolated FreeCAD worker and by the host-side
reauthorizer.  It consumes only authenticated JSON snapshots and produces a
stable table contract.  FreeCAD-specific creation and readback remain separate
so native behavior is still checked independently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from typing import Any


ASSEMBLY_BOM_SCHEMA = "vibecad-assembly-bom-v1"
MAX_BOM_COLUMNS = 32
MAX_BOM_ROWS = 4096
MAX_BOM_OCCURRENCE_PATHS = 8192
MAX_BOM_PATH_SEGMENTS = 16
MAX_BOM_ERROR_PATHS = 256
MAX_BOM_CONTRACT_BYTES = 400_000

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_BUILTINS = {
    "index": ("Index", "Index"),
    "name": ("Name", "Name"),
    "quantity": ("Quantity", "Quantity"),
    "file_name": ("File Name", "File Name"),
}


class AssemblyBOMError(ValueError):
    """A deterministic BOM failure with copy-ready model feedback."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


def _error(
    message: str,
    *,
    stage: str,
    correction: str,
    **details: Any,
) -> AssemblyBOMError:
    return AssemblyBOMError(
        message,
        details={"stage": stage, **details, "correction": correction},
    )


def _bounded_text(value: Any, *, context: str, limit: int = 4096) -> str:
    text = str(value or "")
    if len(text) > limit:
        raise _error(
            f"{context} contains {len(text)} characters; the limit is {limit}.",
            stage="bom_source_contract",
            correction="Shorten the source label, property, or custom BOM value and retry.",
            context=context,
            observed_length=len(text),
            maximum_length=limit,
        )
    return text


def _columns(raw_columns: Any) -> list[dict[str, str]]:
    if (
        isinstance(raw_columns, (str, bytes))
        or not isinstance(raw_columns, Sequence)
        or not 1 <= len(raw_columns) <= MAX_BOM_COLUMNS
    ):
        raise _error(
            f"Assembly BOM columns must contain 1-{MAX_BOM_COLUMNS} normalized columns.",
            stage="bom_columns",
            correction=(
                "Use built-ins 'index', 'name', 'quantity', or 'file_name'; "
                "a property column; or one declared custom heading."
            ),
        )
    result: list[dict[str, str]] = []
    headings: set[str] = set()
    native_names: set[str] = set()
    builtin_keys: set[str] = set()
    for index, raw in enumerate(raw_columns):
        context = f"columns[{index}]"
        if not isinstance(raw, Mapping):
            raise _error(
                f"Assembly BOM {context} is not a normalized column object.",
                stage="bom_columns",
                correction="Pass columns only through api.bill_of_materials(...).",
                column_index=index,
            )
        kind = str(raw.get("kind") or "")
        if kind == "builtin":
            if set(raw) != {"kind", "key", "heading", "native_name"}:
                raise _error(
                    f"Assembly BOM {context} has malformed built-in fields.",
                    stage="bom_columns",
                    correction="Recreate the BOM with a supported built-in column name.",
                    column_index=index,
                )
            key = str(raw.get("key") or "")
            expected = _BUILTINS.get(key)
            if expected is None or (
                str(raw.get("heading") or ""), str(raw.get("native_name") or "")
            ) != expected:
                raise _error(
                    f"Assembly BOM {context} changed built-in column {key!r}.",
                    stage="bom_columns",
                    correction="Use the normalized value returned by api.bill_of_materials.",
                    column_index=index,
                    builtin_key=key,
                )
            if key in builtin_keys:
                raise _error(
                    f"Assembly BOM built-in column {key!r} is duplicated.",
                    stage="bom_columns",
                    correction=f"Keep exactly one {key!r} column.",
                    column_index=index,
                )
            builtin_keys.add(key)
            clean = dict(raw)
        elif kind == "property":
            if set(raw) != {"kind", "property", "heading", "native_name"}:
                raise _error(
                    f"Assembly BOM {context} has malformed property-column fields.",
                    stage="bom_columns",
                    correction=(
                        "Use {'property':'PartNumber','heading':'Part Number'} exactly."
                    ),
                    column_index=index,
                )
            property_name = str(raw.get("property") or "")
            if not _PROPERTY_NAME.fullmatch(property_name) or str(
                raw.get("native_name") or ""
            ) != f".{property_name}":
                raise _error(
                    f"Assembly BOM {context} has an invalid native property name.",
                    stage="bom_columns",
                    correction="Use one exact scalar FreeCAD property name.",
                    column_index=index,
                    property_name=property_name,
                )
            clean = dict(raw)
        elif kind == "custom":
            if set(raw) != {"kind", "heading", "native_name"} or str(
                raw.get("heading") or ""
            ) != str(raw.get("native_name") or ""):
                raise _error(
                    f"Assembly BOM {context} has malformed custom-column fields.",
                    stage="bom_columns",
                    correction="Use {'heading':'Description'} for a custom column.",
                    column_index=index,
                )
            clean = dict(raw)
        else:
            raise _error(
                f"Assembly BOM {context} uses unsupported kind {kind!r}.",
                stage="bom_columns",
                correction="Use a built-in, property, or custom BOM column.",
                column_index=index,
                column_kind=kind,
            )
        heading = _bounded_text(clean.get("heading"), context=f"{context}.heading", limit=80)
        native_name = _bounded_text(
            clean.get("native_name"), context=f"{context}.native_name", limit=129
        )
        if not heading or heading.startswith("."):
            raise _error(
                f"Assembly BOM {context} has invalid heading {heading!r}.",
                stage="bom_columns",
                correction="Use a nonempty heading that does not start with '.'.",
                column_index=index,
            )
        if heading in headings or native_name in native_names:
            duplicate = heading if heading in headings else native_name
            raise _error(
                f"Assembly BOM column identity {duplicate!r} is duplicated.",
                stage="bom_columns",
                correction="Keep one best column for each heading or native property.",
                column_index=index,
                duplicate=duplicate,
            )
        headings.add(heading)
        native_names.add(native_name)
        result.append(clean)
    if "name" not in builtin_keys:
        raise _error(
            "Assembly BOM columns must include the 'name' built-in.",
            stage="bom_columns",
            correction="Add 'name' to columns so every retained row has a stable identity.",
        )
    return result


def _property_map(raw: Any, *, context: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 64:
        raise _error(
            f"{context} has an invalid BOM property table.",
            stage="bom_source_contract",
            correction="Use at most 64 native BOM-supported scalar properties per source.",
            context=context,
        )
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise _error(
                f"{context}[{index}] is not a property object.",
                stage="bom_source_contract",
                correction="Regenerate after repairing the source object's scalar properties.",
                context=context,
                property_index=index,
            )
        name = str(item.get("name") or "")
        kind = str(item.get("kind") or "")
        expected = {"name", "property_type", "kind", "value"}
        if kind == "enumeration":
            expected.add("choices")
        elif kind == "quantity":
            expected.add("assignment")
        if (
            set(item) != expected
            or not _PROPERTY_NAME.fullmatch(name)
            or kind not in {"string", "enumeration", "boolean", "integer", "float", "quantity"}
            or name in result
        ):
            raise _error(
                f"{context}[{index}] is malformed or duplicated.",
                stage="bom_source_contract",
                correction="Regenerate from native String, Quantity, Enumeration, Float, Integer, or Bool properties.",
                context=context,
                property_index=index,
                property_name=name,
            )
        result[name] = dict(item)
    return result


def _property_text(item: Mapping[str, Any] | None) -> str:
    if item is None:
        return "N/A"
    kind = str(item.get("kind") or "")
    value = item.get("value")
    if kind in {"string", "enumeration"}:
        return _bounded_text(value, context=f"BOM property {item.get('name')!r}")
    if kind == "boolean":
        return "True" if bool(value) else "False"
    if kind == "integer":
        if isinstance(value, bool):
            raise _error(
                f"BOM property {item.get('name')!r} has a boolean integer value.",
                stage="bom_source_contract",
                correction="Repair the native Integer property and regenerate.",
            )
        return str(int(value))
    if kind == "float":
        number = float(value)
        if not math.isfinite(number):
            raise _error(
                f"BOM property {item.get('name')!r} is not finite.",
                stage="bom_source_contract",
                correction="Set the native Float property to a finite value.",
            )
        return json.dumps(number, ensure_ascii=True, allow_nan=False)
    if kind == "quantity":
        return _bounded_text(
            item.get("assignment"),
            context=f"BOM property {item.get('name')!r} quantity",
        )
    raise _error(
        f"BOM property {item.get('name')!r} uses unsupported kind {kind!r}.",
        stage="bom_source_contract",
        correction="Use a native BOM-supported scalar property.",
    )


def _identity(node: Mapping[str, Any], *, context: str) -> tuple[str, str]:
    raw = node.get("identity")
    if not isinstance(raw, Mapping) or set(raw) != {"document_uid", "object_name"}:
        raise _error(
            f"{context} has no stable source identity.",
            stage="bom_source_contract",
            correction="Regenerate after the source object has a stable document identity.",
            context=context,
        )
    identity = (str(raw.get("document_uid") or ""), str(raw.get("object_name") or ""))
    if not all(identity):
        raise _error(
            f"{context} has an empty stable source identity.",
            stage="bom_source_contract",
            correction="Regenerate after the source object has a stable document identity.",
            context=context,
        )
    return identity


def _node_contract(node: Mapping[str, Any], *, context: str) -> dict[str, Any]:
    kind = str(node.get("kind") or "")
    if kind not in {"assembly", "part", "shape"}:
        raise _error(
            f"{context} uses unsupported source kind {kind!r}.",
            stage="bom_source_contract",
            correction="Use a native Assembly, App::Part, or shape component source.",
            context=context,
            source_kind=kind,
        )
    file_name = str(node.get("document_file_name") or "")
    if len(file_name) > 4096 or "/" in file_name or "\\" in file_name:
        raise _error(
            f"{context} contains a raw or invalid document path.",
            stage="bom_source_contract",
            correction="Expose only the source document basename, never a filesystem path.",
            context=context,
        )
    occurrences = node.get("occurrences", [])
    if not isinstance(occurrences, list):
        raise _error(
            f"{context} has a malformed occurrence list.",
            stage="bom_source_contract",
            correction="Regenerate the authenticated source hierarchy.",
            context=context,
        )
    return {
        "identity": _identity(node, context=context),
        "kind": kind,
        "label": _bounded_text(node.get("label"), context=f"{context}.label"),
        "document_file_name": file_name,
        "properties": _property_map(
            node.get("bom_properties", []), context=f"{context}.bom_properties"
        ),
        "occurrences": occurrences,
        "raw": node,
    }


def _reference_graph(
    component: Mapping[str, Any], *, index: int
) -> tuple[str, dict[str, Any], dict[str, Mapping[str, Any]], bool]:
    if set(component) != {"output_name", "reference"}:
        raise _error(
            f"Assembly BOM component source {index} is malformed.",
            stage="bom_graph",
            correction="Use the exact returned api.component values in api.assembly.",
            component_index=index,
        )
    output_name = str(component.get("output_name") or "")
    reference = component.get("reference")
    if not _IDENTIFIER.fullmatch(output_name) or not isinstance(reference, Mapping):
        raise _error(
            f"Assembly BOM component source {index} has no stable output/reference identity.",
            stage="bom_graph",
            correction="Return every component under one valid stable output name.",
            component_index=index,
            component_output=output_name,
        )
    hierarchy = reference.get("assembly_hierarchy")
    if hierarchy is not None:
        if not isinstance(hierarchy, Mapping) or str(hierarchy.get("schema") or "") != (
            "vibecad-assembly-source-hierarchy-v1"
        ):
            raise _error(
                f"Assembly BOM component {output_name!r} has an unsupported hierarchy.",
                stage="bom_source_contract",
                correction="Regenerate while the native source hierarchy is available.",
                component_output=output_name,
            )
        raw_nodes = hierarchy.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise _error(
                f"Assembly BOM component {output_name!r} hierarchy has no nodes.",
                stage="bom_source_contract",
                correction="Regenerate while the native source hierarchy is available.",
                component_output=output_name,
            )
        nodes = {
            str(node.get("node_id") or ""): node
            for node in raw_nodes
            if isinstance(node, Mapping)
        }
        if len(nodes) != len(raw_nodes) or "" in nodes:
            raise _error(
                f"Assembly BOM component {output_name!r} hierarchy has duplicate nodes.",
                stage="bom_source_contract",
                correction="Regenerate the source hierarchy before creating the BOM.",
                component_output=output_name,
            )
        root = nodes.get(str(hierarchy.get("root_node_id") or ""))
        if root is None:
            raise _error(
                f"Assembly BOM component {output_name!r} hierarchy has no root node.",
                stage="bom_source_contract",
                correction="Regenerate the source hierarchy before creating the BOM.",
                component_output=output_name,
            )
        reference_identity = (
            str(reference.get("document_uid") or ""),
            str(reference.get("object_name") or ""),
        )
        if _identity(root, context=f"component {output_name!r} root") != reference_identity:
            raise _error(
                f"Assembly BOM component {output_name!r} hierarchy belongs to another source.",
                stage="bom_source_contract",
                correction="Regenerate from the exact referenced source object.",
                component_output=output_name,
            )
        return output_name, _node_contract(
            root, context=f"component {output_name!r} root"
        ), nodes, True
    source_kind = str(reference.get("source_kind") or "shape")
    kind = source_kind if source_kind in {"assembly", "part"} else "shape"
    pseudo = {
        "identity": {
            "document_uid": str(reference.get("document_uid") or ""),
            "object_name": str(reference.get("object_name") or ""),
        },
        "kind": kind,
        "label": str(reference.get("label") or reference.get("object_name") or ""),
        "document_file_name": str(reference.get("document_file_name") or ""),
        "bom_properties": list(reference.get("bom_properties") or []),
        "occurrences": [],
    }
    return output_name, _node_contract(
        pseudo, context=f"component {output_name!r} source"
    ), {}, False


def _path(parts: Sequence[str], *, component_output: str) -> str:
    if len(parts) > MAX_BOM_PATH_SEGMENTS or any(
        not _IDENTIFIER.fullmatch(part) for part in parts
    ):
        joined = "/".join(parts)
        raise _error(
            f"Assembly BOM occurrence path {joined!r} is not copy-ready.",
            stage="bom_occurrence_path",
            correction=(
                f"Keep stable source object names identifier-safe and limit component "
                f"{component_output!r} to {MAX_BOM_PATH_SEGMENTS - 1} nested levels."
            ),
            component_output=component_output,
            requested_path=joined,
            maximum_segments=MAX_BOM_PATH_SEGMENTS,
        )
    return "/".join(parts)


def _custom_text(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error(
                "A BOM custom value is not finite.",
                stage="bom_row_overrides",
                correction="Use a finite JSON scalar custom value.",
            )
        return json.dumps(value, ensure_ascii=True, allow_nan=False)
    return _bounded_text(value, context="BOM custom value")


def _column_text(
    column: Mapping[str, str],
    *,
    index: str,
    node: Mapping[str, Any],
    quantity: int,
    mirrored: bool,
) -> str:
    kind = str(column["kind"])
    if kind == "builtin":
        key = str(column["key"])
        if key == "index":
            return index
        if key == "name":
            label = str(node["label"])
            return f"{label} (mirrored)" if mirrored else label
        if key == "quantity":
            return str(quantity)
        if key == "file_name":
            return str(node["document_file_name"])
    if kind == "property":
        return _property_text(node["properties"].get(str(column["property"])))
    if kind == "custom":
        return ""
    raise AssertionError(f"Unhandled BOM column: {column!r}")


def _column_label(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _stable_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def plan_assembly_bom(
    component_sources: Sequence[Mapping[str, Any]],
    *,
    columns: Any,
    detail_subassemblies: Any,
    detail_parts: Any,
    only_parts: Any,
    row_overrides: Any,
) -> dict[str, Any]:
    """Derive one exact, bounded BOM from authenticated source snapshots."""

    if (
        isinstance(component_sources, (str, bytes))
        or not isinstance(component_sources, Sequence)
        or not component_sources
    ):
        raise _error(
            "An Assembly BOM requires at least one authenticated component source.",
            stage="bom_graph",
            correction="Create components, pass them to api.assembly, then pass that assembly to api.bill_of_materials.",
        )
    for name, value in (
        ("detail_subassemblies", detail_subassemblies),
        ("detail_parts", detail_parts),
        ("only_parts", only_parts),
    ):
        if not isinstance(value, bool):
            raise _error(
                f"Assembly BOM {name} must be a boolean.",
                stage="bom_settings",
                correction=f"Set {name}=True or {name}=False explicitly.",
                setting=name,
            )
    clean_columns = _columns(columns)
    has_quantity = any(
        column.get("kind") == "builtin" and column.get("key") == "quantity"
        for column in clean_columns
    )
    custom_headings = {
        str(column["heading"])
        for column in clean_columns
        if column["kind"] == "custom"
    }
    roots: list[dict[str, Any]] = []
    seen_outputs: set[str] = set()
    for component_index, raw_component in enumerate(component_sources):
        if not isinstance(raw_component, Mapping):
            raise _error(
                f"Assembly BOM component source {component_index} is not an object.",
                stage="bom_graph",
                correction="Use exact returned component outputs from one api.assembly graph.",
                component_index=component_index,
            )
        output_name, root, nodes, has_hierarchy = _reference_graph(
            raw_component, index=component_index
        )
        if output_name in seen_outputs:
            raise _error(
                f"Assembly BOM component output {output_name!r} is duplicated.",
                stage="bom_graph",
                correction="Return each component under one unique stable output name.",
                component_output=output_name,
            )
        seen_outputs.add(output_name)
        roots.append(
            {
                "paths": [_path([output_name], component_output=output_name)],
                "node": root,
                "nodes": nodes,
                "has_hierarchy": has_hierarchy,
                "mirrored": False,
                "units": 1,
                "component_output": output_name,
            }
        )

    rows: list[dict[str, Any]] = []
    total_paths = 0

    def child_instances(parent: Mapping[str, Any]) -> list[dict[str, Any]]:
        node = parent["node"]
        nodes = parent["nodes"]
        result: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for occurrence_index, raw_occurrence in enumerate(node["occurrences"]):
            if not isinstance(raw_occurrence, Mapping):
                raise _error(
                    f"BOM source {node['identity'][1]!r} occurrence {occurrence_index} is malformed.",
                    stage="bom_source_contract",
                    correction="Regenerate the authenticated native source hierarchy.",
                    source_object_name=node["identity"][1],
                    occurrence_index=occurrence_index,
                )
            name = str(raw_occurrence.get("name") or "")
            source_node_id = str(raw_occurrence.get("source_node_id") or "")
            if name in seen_names or not _IDENTIFIER.fullmatch(name) or source_node_id not in nodes:
                raise _error(
                    f"BOM source {node['identity'][1]!r} occurrence {name!r} is ambiguous.",
                    stage="bom_source_contract",
                    correction="Use unique identifier-safe native occurrence names, then regenerate.",
                    source_object_name=node["identity"][1],
                    occurrence_name=name,
                )
            seen_names.add(name)
            source = _node_contract(
                nodes[source_node_id],
                context=f"source occurrence {node['identity'][1]}/{name}",
            )
            scale = raw_occurrence.get("scale", 1.0)
            if isinstance(scale, bool):
                scale = 0.0
            try:
                scale_value = float(scale)
            except (TypeError, ValueError) as exc:
                raise _error(
                    f"BOM occurrence {name!r} has a nonnumeric scale.",
                    stage="bom_source_contract",
                    correction="Repair the native link scale and regenerate.",
                    occurrence_name=name,
                ) from exc
            if not math.isfinite(scale_value) or abs(scale_value) <= 1.0e-12:
                raise _error(
                    f"BOM occurrence {name!r} has invalid scale {scale_value!r}.",
                    stage="bom_source_contract",
                    correction="Set the native link to a finite nonzero scale and regenerate.",
                    occurrence_name=name,
                )
            paths = [
                _path(
                    [*parent_path.split("/"), name],
                    component_output=str(parent["component_output"]),
                )
                for parent_path in parent["paths"]
            ]
            result.append(
                {
                    "paths": paths,
                    "node": source,
                    "nodes": nodes,
                    "has_hierarchy": True,
                    "mirrored": scale_value < 0.0,
                    "units": 1,
                    "component_output": parent["component_output"],
                }
            )
        return result

    def add_siblings(
        instances: list[dict[str, Any]],
        *,
        index_prefix: str,
        active: tuple[tuple[str, str], ...],
    ) -> None:
        nonlocal total_paths
        eligible: list[dict[str, Any]] = []
        for instance in instances:
            if only_parts and instance["node"]["kind"] == "shape":
                continue
            eligible.append(instance)
        groups: list[list[dict[str, Any]]] = []
        group_index: dict[tuple[tuple[str, str], bool], int] = {}
        for instance in eligible:
            key = (instance["node"]["identity"], bool(instance["mirrored"]))
            if has_quantity and key in group_index:
                groups[group_index[key]].append(instance)
            else:
                group_index[key] = len(groups)
                groups.append([instance])
        for sibling_index, members in enumerate(groups, start=1):
            first = members[0]
            node = first["node"]
            source_identity = node["identity"]
            if source_identity in active:
                chain = [name for _uid, name in (*active, source_identity)]
                raise _error(
                    f"Assembly BOM source hierarchy is cyclic: {' -> '.join(chain)}.",
                    stage="bom_graph",
                    correction="Break the cyclic native component link before creating a BOM.",
                    source_chain=chain,
                )
            row_index = f"{index_prefix}.{sibling_index}" if index_prefix else str(sibling_index)
            paths = list(
                dict.fromkeys(path for member in members for path in member["paths"])
            )
            total_paths += len(paths)
            if len(rows) >= MAX_BOM_ROWS or total_paths > MAX_BOM_OCCURRENCE_PATHS:
                raise _error(
                    "The detailed Assembly BOM exceeds its bounded row/path budget.",
                    stage="bom_budget",
                    correction=(
                        "Set detail_subassemblies=False or detail_parts=False, or split "
                        "the design into smaller module BOMs."
                    ),
                    maximum_rows=MAX_BOM_ROWS,
                    maximum_occurrence_paths=MAX_BOM_OCCURRENCE_PATHS,
                    observed_rows=len(rows) + 1,
                    observed_occurrence_paths=total_paths,
                )
            quantity = sum(int(member["units"]) for member in members)
            cells = {
                str(column["heading"]): _column_text(
                    column,
                    index=row_index,
                    node=node,
                    quantity=quantity,
                    mirrored=bool(first["mirrored"]),
                )
                for column in clean_columns
            }
            row = {
                "index": row_index,
                "occurrence_paths": paths,
                "source_object_name": source_identity[1],
                "source_kind": str(node["kind"]),
                "mirrored": bool(first["mirrored"]),
                "quantity": quantity,
                "cells": cells,
            }
            rows.append(row)
            should_recurse = (
                node["kind"] == "assembly" and detail_subassemblies
            ) or (node["kind"] == "part" and detail_parts)
            if not should_recurse:
                continue
            if not first["has_hierarchy"]:
                setting = (
                    "detail_subassemblies" if node["kind"] == "assembly" else "detail_parts"
                )
                raise _error(
                    f"BOM row {paths[0]!r} requests details, but its native {node['kind']} hierarchy was not authenticated.",
                    stage="bom_source_hierarchy",
                    correction=(
                        f"Regenerate while the live source is available, or set {setting}=False "
                        "to keep only the stable top-level row."
                    ),
                    occurrence_path=paths[0],
                    source_kind=node["kind"],
                    required_setting=setting,
                )
            aggregate_parent = {
                **first,
                "paths": paths,
                "units": 1,
            }
            add_siblings(
                child_instances(aggregate_parent),
                index_prefix=row_index,
                active=(*active, source_identity),
            )

    add_siblings(roots, index_prefix="", active=())

    if (
        isinstance(row_overrides, (str, bytes))
        or not isinstance(row_overrides, Sequence)
        or len(row_overrides) > 512
    ):
        raise _error(
            "Assembly BOM row_overrides must be an array with at most 512 entries.",
            stage="bom_row_overrides",
            correction="Pass normalized occurrence_path/value objects through api.bill_of_materials.",
        )
    path_to_row: dict[str, dict[str, Any]] = {}
    for row in rows:
        for path in row["occurrence_paths"]:
            if path in path_to_row:
                raise _error(
                    f"Assembly BOM occurrence path {path!r} resolves to multiple rows.",
                    stage="bom_graph",
                    correction="Use unique stable occurrence names in each native source container.",
                    occurrence_path=path,
                )
            path_to_row[path] = row
    values_by_row_heading: dict[tuple[int, str], list[tuple[str, Any]]] = {}
    seen_override_paths: set[str] = set()
    for override_index, raw_override in enumerate(row_overrides):
        if not isinstance(raw_override, Mapping) or set(raw_override) != {
            "occurrence_path",
            "values",
        }:
            raise _error(
                f"Assembly BOM row_overrides[{override_index}] is malformed.",
                stage="bom_row_overrides",
                correction="Use {'occurrence_path':'Module/Gear','values':{'Description':'Drive gear'}}.",
                override_index=override_index,
            )
        path = str(raw_override.get("occurrence_path") or "")
        if path in seen_override_paths:
            raise _error(
                f"Assembly BOM row override path {path!r} is duplicated.",
                stage="bom_row_overrides",
                correction="Merge all custom values for that occurrence into one override object.",
                occurrence_path=path,
            )
        seen_override_paths.add(path)
        row = path_to_row.get(path)
        if row is None:
            available = list(path_to_row)[:MAX_BOM_ERROR_PATHS]
            raise _error(
                f"Assembly BOM row override path {path!r} is not present in the requested table.",
                stage="bom_row_overrides",
                correction=(
                    "Copy one exact available_occurrence_paths entry. If the intended row "
                    "is nested, enable detail_subassemblies/detail_parts; if it is a shape, "
                    "set only_parts=False."
                ),
                requested_path=path,
                available_occurrence_paths=available,
                available_paths_truncated=len(path_to_row) > len(available),
                available_paths_omitted=max(0, len(path_to_row) - len(available)),
                settings={
                    "detail_subassemblies": detail_subassemblies,
                    "detail_parts": detail_parts,
                    "only_parts": only_parts,
                },
            )
        values = raw_override.get("values")
        if not isinstance(values, Mapping) or not values:
            raise _error(
                f"Assembly BOM row override {path!r} has no custom values.",
                stage="bom_row_overrides",
                correction="Provide at least one declared custom heading/value pair.",
                occurrence_path=path,
            )
        unknown = set(str(key) for key in values) - custom_headings
        if unknown:
            raise _error(
                f"Assembly BOM row override {path!r} uses undeclared custom headings {sorted(unknown)}.",
                stage="bom_row_overrides",
                correction=f"Use only declared custom headings {sorted(custom_headings)}.",
                occurrence_path=path,
                unknown_headings=sorted(unknown),
                available_headings=sorted(custom_headings),
            )
        row_key = id(row)
        for heading, value in values.items():
            values_by_row_heading.setdefault((row_key, str(heading)), []).append(
                (path, value)
            )
    for row in rows:
        for heading in custom_headings:
            assignments = values_by_row_heading.get((id(row), heading), [])
            if not assignments:
                continue
            distinct = {
                json.dumps(
                    value,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                for _path_value, value in assignments
            }
            if len(distinct) > 1:
                raise _error(
                    f"Aggregated BOM row {row['index']!r} receives conflicting {heading!r} overrides.",
                    stage="bom_row_overrides",
                    correction=(
                        "Use the same value for every listed equivalent occurrence path, "
                        "or omit 'quantity' so the native BOM retains separate rows."
                    ),
                    row_index=row["index"],
                    heading=heading,
                    conflicting_occurrence_paths=[path for path, _value in assignments],
                    values=[value for _path, value in assignments],
                )
            row["cells"][heading] = _custom_text(assignments[0][1])

    settings = {
        "detail_subassemblies": detail_subassemblies,
        "detail_parts": detail_parts,
        "only_parts": only_parts,
    }
    used_range = ["A1", f"{_column_label(len(clean_columns))}{len(rows) + 1}"]
    contract: dict[str, Any] = {
        "schema": ASSEMBLY_BOM_SCHEMA,
        "columns": clean_columns,
        "settings": settings,
        "rows": rows,
        "row_count": len(rows),
        "occurrence_path_count": sum(len(row["occurrence_paths"]) for row in rows),
        "used_range": used_range,
        "limits": {
            "columns": MAX_BOM_COLUMNS,
            "rows": MAX_BOM_ROWS,
            "occurrence_paths": MAX_BOM_OCCURRENCE_PATHS,
            "path_segments": MAX_BOM_PATH_SEGMENTS,
            "contract_bytes": MAX_BOM_CONTRACT_BYTES,
        },
    }
    contract["table_sha256"] = _stable_digest(contract)
    encoded_bytes = len(
        json.dumps(
            contract,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    if encoded_bytes > MAX_BOM_CONTRACT_BYTES:
        raise _error(
            "The Assembly BOM exceeds its bounded publication contract size.",
            stage="bom_budget",
            correction=(
                "Reduce columns or hierarchy detail, omit long custom values, or split "
                "the design into smaller named module BOMs."
            ),
            observed_contract_bytes=encoded_bytes,
            maximum_contract_bytes=MAX_BOM_CONTRACT_BYTES,
            observed_rows=len(rows),
            observed_occurrence_paths=contract["occurrence_path_count"],
        )
    return contract


def bom_summary(contract: Mapping[str, Any], *, output_name: str) -> dict[str, Any]:
    """Return the compact cross-output summary used in solver diagnostics."""

    return {
        "bom_output": str(output_name),
        "column_count": len(list(contract.get("columns") or [])),
        "row_count": int(contract.get("row_count", 0)),
        "occurrence_path_count": int(contract.get("occurrence_path_count", 0)),
        "table_sha256": str(contract.get("table_sha256") or ""),
    }
