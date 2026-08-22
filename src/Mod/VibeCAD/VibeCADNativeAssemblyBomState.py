# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact state for native Assembly bill-of-materials authoring."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeTargets import object_reference


MAX_BOM_SOURCE_NODES = 4_096
MAX_BOM_SOURCE_EDGES = 8_192
MAX_BOM_PROPERTIES_PER_NODE = 256
MAX_BOM_PROPERTIES = 32_768
MAX_BOM_OPERATIONS = 1_024
MAX_BOM_COLUMNS = 32
MAX_BOM_ROWS = 4_096
MAX_BOM_CELLS = 262_144
MAX_BOM_VALUE_CHARACTERS = 4_096
MAX_BOM_PREVIEW_VALUE_CHARACTERS = 160
MAX_BOM_OPERATION_PREVIEW = 16
MAX_BOM_ROW_PREVIEW = 8
MAX_BOM_PROPERTY_COLUMN_PREVIEW = 64

_CELL_ADDRESS = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")
_IGNORED_GROUP_TYPES = frozenset(
    {
        "Assembly::BomGroup",
        "Assembly::JointGroup",
        "Assembly::SimulationGroup",
        "Assembly::ViewGroup",
    }
)


class NativeAssemblyBomStateError(RuntimeError):
    """The live Assembly BOM graph cannot be represented exactly."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_BOM_STATE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblyBomState:
    assembly: Any
    components: tuple[Any, ...]
    source_records: tuple[dict[str, Any], ...]
    bom_group: Any | None
    boms: tuple[Any, ...]
    bom_records: tuple[dict[str, Any], ...]
    state_sha256: str

    def summary(self) -> dict[str, Any]:
        property_types: dict[str, set[str]] = {}
        for record in self.source_records[1:]:
            for prop in record.get("properties", ()):
                name = str(prop.get("name", "") or "")
                property_type = str(prop.get("type", "") or "")
                if name and property_type:
                    property_types.setdefault(name, set()).add(property_type)
        property_columns = [
            {
                "column": f".{name}",
                "property_types": sorted(types),
            }
            for name, types in sorted(property_types.items())
        ]
        result: dict[str, Any] = {
            "available": True,
            "state_sha256": self.state_sha256,
            "component_count": len(self.components),
            "source_node_count": len(self.source_records),
            "bom_count": len(self.boms),
            "supported_builtin_columns": [
                "Index",
                "Name",
                "File Name",
                "Quantity",
            ],
            "property_column_format": ".PropertyName",
            "available_property_columns": property_columns[
                :MAX_BOM_PROPERTY_COLUMN_PREVIEW
            ],
            "custom_columns_supported": True,
            "new_custom_cells_are_blank": True,
            "boms": [
                {
                    **dict(record["bom"]),
                    "label": str(record["label"]),
                    "columns": list(record["columns"]),
                    "row_count": int(record["table"]["row_count"]),
                    "settings": dict(record["settings"]),
                }
                for record in self.bom_records[:MAX_BOM_OPERATION_PREVIEW]
            ],
        }
        if len(self.bom_records) > MAX_BOM_OPERATION_PREVIEW:
            result["boms_truncated"] = True
        if len(property_columns) > MAX_BOM_PROPERTY_COLUMN_PREVIEW:
            result["available_property_columns_truncated"] = True
        return result


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _live_object(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    name = str(getattr(obj, "Name", "") or "")
    reader = getattr(document, "getObject", None)
    return bool(
        document is not None and name and callable(reader) and reader(name) is obj
    )


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == type_id:
        return True
    reader = getattr(obj, "isDerivedFrom", None)
    if not callable(reader):
        return False
    try:
        return bool(reader(type_id))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _identity_record(obj: Any) -> dict[str, Any]:
    if not _live_object(obj):
        raise NativeAssemblyBomStateError(
            "An Assembly BOM source is not one exact live object."
        )
    try:
        object_id = int(obj.ID)
        reference = object_reference(obj)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyBomStateError(
            "An Assembly BOM source has an invalid identity."
        ) from exc
    if object_id <= 0:
        raise NativeAssemblyBomStateError(
            "An Assembly BOM source has an invalid identity."
        )
    return {
        **reference,
        "document_name": str(obj.Document.Name),
        "object_id": object_id,
        "type_id": str(obj.TypeId),
    }


def _bounded_text(value: Any, context: str) -> str:
    try:
        result = str(value or "")
    except Exception as exc:
        raise NativeAssemblyBomStateError(f"{context} is unreadable.") from exc
    if len(result) > MAX_BOM_VALUE_CHARACTERS:
        raise NativeAssemblyBomStateError(
            f"{context} exceeds the {MAX_BOM_VALUE_CHARACTERS}-character Native BOM bound."
        )
    return result


def _scalar_property(obj: Any, name: str, property_type: str) -> dict[str, Any] | None:
    try:
        value = getattr(obj, name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return None
    context = f"Assembly BOM property {name!r}"
    if property_type == "App::PropertyString":
        normalized: Any = _bounded_text(value, context)
    elif property_type == "App::PropertyEnumeration":
        normalized = _bounded_text(value, context)
    elif property_type == "App::PropertyBool":
        if type(value) is not bool:
            raise NativeAssemblyBomStateError(f"{context} is not boolean.")
        normalized = value
    elif property_type.startswith(
        "App::PropertyInteger"
    ) and not property_type.endswith("List"):
        if type(value) is not int:
            raise NativeAssemblyBomStateError(f"{context} is not an integer.")
        normalized = value
    elif property_type.startswith("App::PropertyFloat") and not property_type.endswith(
        "List"
    ):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise NativeAssemblyBomStateError(f"{context} is not numeric.") from exc
        if not math.isfinite(number):
            raise NativeAssemblyBomStateError(f"{context} is not finite.")
        normalized = number
    else:
        numeric = getattr(value, "Value", None)
        unit = getattr(value, "Unit", None)
        if (
            not property_type.startswith("App::Property")
            or isinstance(numeric, bool)
            or not isinstance(numeric, (int, float))
            or unit is None
        ):
            return None
        number = float(numeric)
        if not math.isfinite(number):
            raise NativeAssemblyBomStateError(f"{context} is not finite.")
        normalized = {
            "value": number,
            "unit": _bounded_text(unit, context),
            "assignment": _bounded_text(value, context),
        }
    return {"name": name, "type": property_type, "value": normalized}


def _bom_properties(obj: Any) -> tuple[dict[str, Any], ...]:
    result = []
    names = sorted(str(item) for item in (getattr(obj, "PropertiesList", ()) or ()))
    for name in names:
        if name.startswith("VibeCAD") or name.startswith("_"):
            continue
        try:
            property_type = str(obj.getTypeIdOfProperty(name) or "")
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            continue
        item = _scalar_property(obj, name, property_type)
        if item is not None:
            result.append(item)
        if len(result) > MAX_BOM_PROPERTIES_PER_NODE:
            raise NativeAssemblyBomStateError(
                f"Assembly BOM source {obj.Name!r} exceeds the "
                f"{MAX_BOM_PROPERTIES_PER_NODE}-property per-object bound."
            )
    return tuple(result)


def _linked_target(obj: Any) -> Any:
    for method_name in ("getLinkedAssembly", "getLinkedObject"):
        reader = getattr(obj, method_name, None)
        if not callable(reader):
            continue
        try:
            target = reader()
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            target = None
        if target is not None:
            return target
    return obj


def _component_candidate(obj: Any) -> bool:
    return any(
        _is_derived(obj, type_id)
        for type_id in (
            "Assembly::AssemblyObject",
            "App::Part",
            "Part::Feature",
        )
    )


def _container_children(container: Any) -> tuple[Any, ...]:
    if not (
        _is_derived(container, "Assembly::AssemblyObject")
        or _is_derived(container, "App::Part")
    ):
        return ()
    children = getattr(container, "OutList", None)
    if children is None:
        children = getattr(container, "Group", ())
    return tuple(child for child in tuple(children or ()) if child is not None)


def _occurrence_scale(obj: Any) -> float:
    values = [getattr(obj, "Scale", 1.0)]
    if _is_derived(obj, "App::LinkElement"):
        reader = getattr(obj, "getLinkGroup", None)
        if callable(reader):
            try:
                group = reader()
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                group = None
            if group is not None:
                values.append(getattr(group, "Scale", 1.0))
    result = 1.0
    try:
        for value in values:
            result *= float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeAssemblyBomStateError(
            "An Assembly BOM occurrence has an invalid scale."
        ) from exc
    if not math.isfinite(result):
        raise NativeAssemblyBomStateError(
            "An Assembly BOM occurrence has an invalid scale."
        )
    return result


def _source_graph(assembly: Any) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    by_identity: dict[tuple[str, str, int], str] = {}
    active: set[tuple[str, str, int]] = set()
    edge_count = 0
    property_count = 0

    def visit(obj: Any) -> str:
        nonlocal edge_count, property_count
        identity = _identity_record(obj)
        key = (
            str(identity["document_uid"]),
            str(identity["object_name"]),
            int(identity["object_id"]),
        )
        existing = by_identity.get(key)
        if existing is not None:
            if key in active:
                raise NativeAssemblyBomStateError(
                    "The Assembly BOM source hierarchy contains a cycle."
                )
            return existing
        if len(records) >= MAX_BOM_SOURCE_NODES:
            raise NativeAssemblyBomStateError(
                f"The Assembly exceeds the {MAX_BOM_SOURCE_NODES}-source-node Native BOM bound."
            )
        node_id = f"n{len(records):04d}"
        properties = _bom_properties(obj)
        property_count += len(properties)
        if property_count > MAX_BOM_PROPERTIES:
            raise NativeAssemblyBomStateError(
                f"The Assembly exceeds the {MAX_BOM_PROPERTIES}-property Native BOM bound."
            )
        record: dict[str, Any] = {
            "node_id": node_id,
            "object": identity,
            "label": _bounded_text(getattr(obj, "Label", ""), "Assembly BOM label"),
            "document_file_name": _bounded_text(
                getattr(obj.Document, "FileName", ""),
                "Assembly BOM document file name",
            ),
            "timeline_active": _timeline_active(obj),
            "properties": list(properties),
            "occurrences": [],
        }
        records.append(record)
        by_identity[key] = node_id
        active.add(key)
        try:
            for occurrence in _container_children(obj):
                if str(
                    getattr(occurrence, "TypeId", "") or ""
                ) in _IGNORED_GROUP_TYPES or not _timeline_active(occurrence):
                    continue
                target = _linked_target(occurrence)
                if (
                    not _live_object(target)
                    or not _timeline_active(target)
                    or not _component_candidate(target)
                ):
                    continue
                edge_count += 1
                if edge_count > MAX_BOM_SOURCE_EDGES:
                    raise NativeAssemblyBomStateError(
                        f"The Assembly exceeds the {MAX_BOM_SOURCE_EDGES}-occurrence Native BOM bound."
                    )
                scale_value = _occurrence_scale(occurrence)
                record["occurrences"].append(
                    {
                        "object": _identity_record(occurrence),
                        "scale": scale_value,
                        "mirrored": scale_value < 0.0,
                        "target_node_id": visit(target),
                    }
                )
        finally:
            active.remove(key)
        return node_id

    visit(assembly)
    return tuple(records)


def _column_number(label: str) -> int:
    result = 0
    for character in label:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _column_label(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _cell_coordinates(address: str) -> tuple[int, int]:
    match = _CELL_ADDRESS.fullmatch(str(address or ""))
    if match is None:
        raise NativeAssemblyBomStateError(
            "An Assembly BOM has an invalid spreadsheet range."
        )
    return _column_number(match.group(1)), int(match.group(2))


def _cell_content(bom: Any, address: str) -> str:
    try:
        value = str(bom.getContents(address) or "")
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyBomStateError(
            "An Assembly BOM cell is unreadable."
        ) from exc
    value = value[1:] if value.startswith("'") else value
    return _bounded_text(value, f"Assembly BOM cell {address}")


def _preview_text(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_BOM_PREVIEW_VALUE_CHARACTERS:
        return value, False
    return value[: MAX_BOM_PREVIEW_VALUE_CHARACTERS - 3] + "...", True


def read_bom_table(
    bom: Any,
    *,
    maximum_cells: int = MAX_BOM_CELLS,
) -> dict[str, Any]:
    """Read and hash one bounded native BOM spreadsheet."""

    if type(maximum_cells) is not int or not 1 <= maximum_cells <= MAX_BOM_CELLS:
        raise NativeAssemblyBomStateError(
            "The Assembly BOM table cell budget is invalid."
        )

    try:
        raw_range = bom.getUsedRange()
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyBomStateError(
            "An Assembly BOM has no readable spreadsheet range."
        ) from exc
    if raw_range is None:
        return {
            "used_range": [],
            "column_count": 0,
            "row_count": 0,
            "cell_count": 0,
            "headers": [],
            "row_preview": [],
            "preview_values_truncated": False,
            "table_sha256": hashlib.sha256(b"").hexdigest(),
        }
    if not isinstance(raw_range, tuple) or len(raw_range) != 2:
        raise NativeAssemblyBomStateError(
            "An Assembly BOM has an invalid spreadsheet range."
        )
    first = str(raw_range[0])
    last = str(raw_range[1])
    first_column, first_row = _cell_coordinates(first)
    last_column, last_row = _cell_coordinates(last)
    column_count = last_column - first_column + 1
    row_count = max(0, last_row - first_row)
    cell_count = column_count * (row_count + 1)
    if (
        first_column != 1
        or first_row != 1
        or not 1 <= column_count <= MAX_BOM_COLUMNS
        or row_count > MAX_BOM_ROWS
        or cell_count > maximum_cells
    ):
        raise NativeAssemblyBomStateError(
            "An Assembly BOM exceeds its bounded A1-origin table or cell budget."
        )
    digest = hashlib.sha256()
    headers = []
    preview = []
    preview_values_truncated = False
    for row in range(1, last_row + 1):
        values = []
        for column in range(1, last_column + 1):
            address = f"{_column_label(column)}{row}"
            value = _cell_content(bom, address)
            digest.update(
                json.dumps(
                    [address, value],
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            values.append(value)
        if row == 1:
            headers = values
        elif len(preview) < MAX_BOM_ROW_PREVIEW:
            preview_values = []
            for value in values:
                preview_value, truncated = _preview_text(value)
                preview_values.append(preview_value)
                preview_values_truncated = preview_values_truncated or truncated
            preview.append(dict(zip(headers, preview_values, strict=True)))
    return {
        "used_range": [first, last],
        "column_count": column_count,
        "row_count": row_count,
        "cell_count": cell_count,
        "headers": headers,
        "row_preview": preview,
        "preview_values_truncated": preview_values_truncated,
        "table_sha256": digest.hexdigest(),
    }


def _bom_graph(
    assembly: Any,
) -> tuple[Any | None, tuple[Any, ...], tuple[dict[str, Any], ...]]:
    groups = tuple(
        child
        for child in tuple(getattr(assembly, "Group", ()) or ())
        if str(getattr(child, "TypeId", "") or "") == "Assembly::BomGroup"
    )
    if len(groups) > 1:
        raise NativeAssemblyBomStateError(
            "The active Assembly contains multiple bill-of-materials groups."
        )
    if not groups:
        return None, (), ()
    group = groups[0]
    if not _live_object(group):
        raise NativeAssemblyBomStateError(
            "The Assembly bill-of-materials group is not one exact live object."
        )
    members = tuple(getattr(group, "Group", ()) or ())
    boms = tuple(
        member
        for member in members
        if str(getattr(member, "TypeId", "") or "") == "Assembly::BomObject"
    )
    resources = tuple(member for member in members if member not in boms)
    if len(boms) > MAX_BOM_OPERATIONS:
        raise NativeAssemblyBomStateError(
            f"The Assembly exceeds the {MAX_BOM_OPERATIONS}-BOM Native bound."
        )
    if len(resources) > MAX_BOM_OPERATIONS:
        raise NativeAssemblyBomStateError(
            f"The Assembly exceeds the {MAX_BOM_OPERATIONS}-resource Native BOM bound."
        )
    resources_by_bom = {bom: [] for bom in boms}
    for resource in resources:
        owner = getattr(resource, "VibeCADTimelineOwner", None)
        if (
            not _live_object(resource)
            or not _timeline_active(resource)
            or str(getattr(resource, "VibeCADTimelineRole", "") or "") != "resource"
            or owner not in resources_by_bom
        ):
            raise NativeAssemblyBomStateError(
                "The bill-of-materials group contains a stale or invalid resource."
            )
        resources_by_bom[owner].append(resource)
    records = []
    remaining_cells = MAX_BOM_CELLS
    for bom in boms:
        if remaining_cells <= 0:
            raise NativeAssemblyBomStateError(
                f"The Assembly exceeds the {MAX_BOM_CELLS}-cell Native BOM bound."
            )
        if (
            not _live_object(bom)
            or str(getattr(bom, "TypeId", "") or "") != "Assembly::BomObject"
            or not _timeline_active(bom)
        ):
            raise NativeAssemblyBomStateError(
                "The bill-of-materials group contains a stale or invalid operation."
            )
        columns = tuple(
            str(value) for value in (getattr(bom, "columnsNames", ()) or ())
        )
        if not 1 <= len(columns) <= MAX_BOM_COLUMNS or any(
            not value or len(value) > 129 for value in columns
        ):
            raise NativeAssemblyBomStateError(
                "An existing Assembly BOM has invalid columns."
            )
        table = read_bom_table(bom, maximum_cells=remaining_cells)
        remaining_cells -= int(table["cell_count"])
        records.append(
            {
                "bom": _identity_record(bom),
                "label": _bounded_text(bom.Label, "Assembly BOM label"),
                "columns": list(columns),
                "settings": {
                    "detail_subassemblies": bool(bom.detailSubAssemblies),
                    "detail_parts": bool(bom.detailParts),
                    "only_parts": bool(bom.onlyParts),
                    "auto_generate": bool(bom.autoGenerate),
                },
                "resources": [
                    _identity_record(resource)
                    for resource in resources_by_bom[bom]
                ],
                "table": table,
            }
        )
    return group, boms, tuple(records)


def capture_assembly_bom_state(assembly: Any) -> AssemblyBomState:
    """Capture the exact active Assembly source graph and durable BOMs."""

    if not _live_object(assembly) or not _timeline_active(assembly):
        raise NativeAssemblyBomStateError(
            "The human-active Assembly is not one exact live History object."
        )
    components = assembly_components(assembly)
    source_records = _source_graph(assembly)
    bom_group, boms, bom_records = _bom_graph(assembly)
    canonical = {
        "assembly": _identity_record(assembly),
        "assembly_label": _bounded_text(assembly.Label, "Assembly label"),
        "direct_components": [_identity_record(item) for item in components],
        "source_graph": list(source_records),
        "bom_group": None if bom_group is None else _identity_record(bom_group),
        "boms": list(bom_records),
    }
    try:
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (OverflowError, TypeError, ValueError) as exc:
        raise NativeAssemblyBomStateError(
            "The Assembly BOM state cannot be represented exactly."
        ) from exc
    return AssemblyBomState(
        assembly=assembly,
        components=components,
        source_records=source_records,
        bom_group=bom_group,
        boms=boms,
        bom_records=bom_records,
        state_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def assembly_bom_state_summary(assembly: Any) -> dict[str, Any]:
    return capture_assembly_bom_state(assembly).summary()
