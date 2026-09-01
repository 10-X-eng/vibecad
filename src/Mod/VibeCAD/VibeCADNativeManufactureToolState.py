# SPDX-License-Identifier: LGPL-2.1-or-later

"""Path-free, fingerprinted CAM tool catalog and editable property state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError


MAX_TOOL_CATALOG_PAGE_SIZE = 128
MAX_TOOL_CATALOG_ITEMS = 4096
MAX_TOOL_ASSET_BYTES = 2 * 1024 * 1024
MAX_TOOL_PROPERTIES = 64
_INTEGER_TEXT = re.compile(r"^[+-]?[0-9]+$")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, noun: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            f"The CAM tool {noun} is not numeric.",
            error_code="NATIVE_MANUFACTURE_TOOL_STATE_INVALID",
        ) from exc
    if not math.isfinite(result):
        raise NativeManufactureError(
            f"The CAM tool {noun} is not finite.",
            error_code="NATIVE_MANUFACTURE_TOOL_STATE_INVALID",
        )
    return round(result, 9)


def _quantity_descriptor(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"kind": "boolean", "value": value}
    if isinstance(value, int):
        return {"kind": "integer", "value": value}
    if isinstance(value, float):
        return {"kind": "number", "value": _finite(value, "property")}
    text = str(value or "").strip()
    if _INTEGER_TEXT.fullmatch(text):
        return {"kind": "integer", "value": int(text)}
    try:
        import FreeCAD as App

        quantity = App.Units.Quantity(text)
        if quantity.Unit == App.Units.Length:
            return {
                "kind": "length_mm",
                "value": _finite(quantity.getValueAs("mm"), "length"),
            }
        if quantity.Unit == App.Units.Angle:
            return {
                "kind": "angle_degrees",
                "value": _finite(quantity.getValueAs("deg"), "angle"),
            }
        if quantity.Unit == App.Units.Unit():
            return {"kind": "number", "value": _finite(quantity.Value, "property")}
    except Exception:
        pass
    return {"kind": "string", "value": text[:320]}


def _property_descriptor(obj: Any, name: str) -> dict[str, Any]:
    property_type = str(obj.getTypeIdOfProperty(name) or "")
    value = obj.getPropertyByName(name)
    if property_type in {"App::PropertyLength", "App::PropertyDistance"}:
        descriptor = {
            "kind": "length_mm",
            "value": _finite(value.getValueAs("mm"), name),
        }
    elif property_type == "App::PropertyAngle":
        descriptor = {
            "kind": "angle_degrees",
            "value": _finite(value.getValueAs("deg"), name),
        }
    elif property_type in {"App::PropertyInteger", "App::PropertyIntegerConstraint"}:
        descriptor = {"kind": "integer", "value": int(value)}
    elif property_type in {"App::PropertyFloat", "App::PropertyFloatConstraint"}:
        descriptor = {"kind": "number", "value": _finite(value, name)}
    elif property_type == "App::PropertyBool":
        descriptor = {"kind": "boolean", "value": bool(value)}
    elif property_type == "App::PropertyEnumeration":
        descriptor = {
            "kind": "choice",
            "value": str(value),
            "allowed_values": [
                str(item) for item in obj.getEnumerationsOfProperty(name)
            ][:64],
        }
    elif property_type == "App::PropertyString":
        descriptor = {"kind": "string", "value": str(value)[:320]}
    else:
        raise NativeManufactureError(
            f"CAM tool property {name!r} has unsupported type {property_type!r}.",
            error_code="NATIVE_MANUFACTURE_TOOL_STATE_INVALID",
        )
    return {
        "property_name": name,
        "group": str(obj.getGroupOfProperty(name) or ""),
        **descriptor,
    }


def tool_property_state(tool: Any) -> list[dict[str, Any]]:
    """Return every human-editable Shape/Attributes property with exact type."""

    names = [
        str(name)
        for name in getattr(tool, "PropertiesList", ()) or ()
        if str(tool.getGroupOfProperty(name) or "") in {"Shape", "Attributes"}
    ]
    if len(names) > MAX_TOOL_PROPERTIES:
        raise NativeManufactureError(
            "The CAM tool exposes more than 64 editable properties.",
            error_code="NATIVE_MANUFACTURE_TOOL_STATE_INVALID",
        )
    return [_property_descriptor(tool, name) for name in sorted(names)]


def normalize_tool_property_changes(
    tool: Any,
    changes: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    """Resolve natural values against the exact ToolBit property contract."""

    current = {item["property_name"]: item for item in tool_property_state(tool)}
    if not 1 <= len(changes) <= MAX_TOOL_PROPERTIES:
        raise NativeManufactureError(
            "tool_property_changes must contain 1 through 64 distinct changes.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    normalized = []
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, Mapping) or set(change) != {
            "property_name",
            "value",
        }:
            raise NativeManufactureError(
                "Every tool property change must contain property_name and value.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        name = str(change.get("property_name") or "").strip()
        expected = current.get(name)
        if expected is None or name in seen:
            raise NativeManufactureError(
                f"CAM tool property {name!r} is unavailable or duplicated.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        seen.add(name)
        supplied = change.get("value")
        if isinstance(supplied, Mapping):
            if set(supplied) != {"kind", "value"}:
                raise NativeManufactureError(
                    f"CAM tool property {name!r} has an invalid typed value.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                )
            kind = str(supplied.get("kind") or "")
            raw = supplied.get("value")
        else:
            kind = str(expected["kind"])
            raw = supplied
        if kind != expected["kind"]:
            raise NativeManufactureError(
                f"CAM tool property {name!r} requires value kind {expected['kind']!r}.",
                error_code="NATIVE_ARGUMENTS_INVALID",
                repair={"property": expected},
            )
        if kind in {"length_mm", "angle_degrees", "number"}:
            if (
                isinstance(raw, bool)
                or not isinstance(raw, (int, float))
                or not math.isfinite(raw)
            ):
                raise NativeManufactureError(
                    f"CAM tool property {name!r} requires a finite number.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                )
        elif kind == "integer":
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise NativeManufactureError(
                    f"CAM tool property {name!r} requires an integer.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                )
        elif kind == "boolean":
            if not isinstance(raw, bool):
                raise NativeManufactureError(
                    f"CAM tool property {name!r} requires a boolean.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                )
        elif not isinstance(raw, str) or len(raw) > 320:
            raise NativeManufactureError(
                f"CAM tool property {name!r} requires a bounded string.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        elif kind == "choice" and raw not in expected.get("allowed_values", ()):
            raise NativeManufactureError(
                f"CAM tool property {name!r} rejected choice {raw!r}.",
                error_code="NATIVE_ARGUMENTS_INVALID",
                repair={"allowed_values": expected.get("allowed_values", [])},
            )
        normalized.append(
            {"property_name": name, "value": {"kind": kind, "value": raw}}
        )
    return tuple(normalized)


def apply_tool_property_changes(
    tool: Any,
    changes: tuple[Mapping[str, Any], ...],
    *,
    shape: Any = None,
) -> None:
    """Apply exact typed property changes to an attached or detached ToolBit."""

    changes = normalize_tool_property_changes(tool, changes)
    current = {
        item["property_name"]: item for item in tool_property_state(tool)
    }
    if not 1 <= len(changes) <= MAX_TOOL_PROPERTIES:
        raise NativeManufactureError(
            "tool_property_changes must contain 1 through 64 distinct changes.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, Mapping) or set(change) != {
            "property_name",
            "value",
        }:
            raise NativeManufactureError(
                "Every tool property change must contain property_name and one typed value.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        name = str(change.get("property_name") or "").strip()
        expected = current.get(name)
        if expected is None or name in seen:
            raise NativeManufactureError(
                f"CAM tool property {name!r} is unavailable or duplicated.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        seen.add(name)
        typed = change.get("value")
        if not isinstance(typed, Mapping) or set(typed) != {"kind", "value"}:
            raise NativeManufactureError(
                f"CAM tool property {name!r} requires one exact typed value.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        kind = str(typed.get("kind") or "")
        if kind != expected["kind"]:
            raise NativeManufactureError(
                f"CAM tool property {name!r} requires value kind {expected['kind']!r}.",
                error_code="NATIVE_ARGUMENTS_INVALID",
                repair={"property": expected},
            )
        raw = typed.get("value")
        if kind == "length_mm":
            value = f"{_finite(raw, name)} mm"
        elif kind == "angle_degrees":
            value = f"{_finite(raw, name)} deg"
        elif kind == "integer":
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise NativeManufactureError(
                    f"CAM tool property {name!r} requires an integer.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                )
            value = raw
        elif kind == "number":
            value = _finite(raw, name)
        elif kind == "boolean":
            if not isinstance(raw, bool):
                raise NativeManufactureError(
                    f"CAM tool property {name!r} requires a boolean.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                )
            value = raw
        else:
            if not isinstance(raw, str) or len(raw) > 320:
                raise NativeManufactureError(
                    f"CAM tool property {name!r} requires a bounded string.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                )
            if kind == "choice" and raw not in expected.get("allowed_values", ()):
                raise NativeManufactureError(
                    f"CAM tool property {name!r} rejected choice {raw!r}.",
                    error_code="NATIVE_ARGUMENTS_INVALID",
                    repair={"allowed_values": expected.get("allowed_values", [])},
                )
            value = raw
        try:
            setattr(tool, name, value)
            if expected.get("group") == "Shape":
                target_shape = (
                    shape
                    if shape is not None
                    else getattr(
                        getattr(tool, "Proxy", None),
                        "_tool_bit_shape",
                        None,
                    )
                )
                set_parameter = getattr(target_shape, "set_parameter", None)
                if callable(set_parameter):
                    set_parameter(name, tool.getPropertyByName(name))
        except Exception as exc:
            raise NativeManufactureError(
                f"CAM tool property {name!r} rejected the requested value.",
                error_code="NATIVE_ARGUMENTS_INVALID",
                repair={"property": expected},
            ) from exc


@dataclass(frozen=True, slots=True)
class ToolCatalogRecord:
    catalog_id: str
    uri: str
    label: str
    shape_type: str
    content_sha256: str
    definition: Mapping[str, Any]
    parameters: tuple[Mapping[str, Any], ...]

    def summary(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog_id,
            "label": self.label,
            "shape_type": self.shape_type,
            "content_sha256": self.content_sha256,
            "parameters": [dict(item) for item in self.parameters],
        }


@dataclass(frozen=True, slots=True)
class ToolCatalogState:
    state_sha256: str
    records: tuple[ToolCatalogRecord, ...]

    def page(
        self,
        offset: int,
        page_size: int,
        *,
        query: str = "",
    ) -> dict[str, Any]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise NativeManufactureError(
                "offset must be a non-negative integer.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= MAX_TOOL_CATALOG_PAGE_SIZE
        ):
            raise NativeManufactureError(
                "page_size must be an integer from 1 through 128.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        clean_query = str(query or "").strip()
        if len(clean_query) > 80:
            raise NativeManufactureError(
                "query must contain at most 80 characters.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        needle = re.sub(r"[^a-z0-9]+", "", clean_query.casefold())
        records = (
            tuple(
                record
                for record in self.records
                if needle
                in re.sub(
                    r"[^a-z0-9]+",
                    "",
                    f"{record.label} {record.shape_type}".casefold(),
                )
            )
            if needle
            else self.records
        )
        page = records[offset : offset + page_size]
        return {
            "state_sha256": self.state_sha256,
            "catalog_count": len(self.records),
            "count": len(records),
            "query": clean_query,
            "offset": offset,
            "items": [record.summary() for record in page],
            "next_offset": offset + len(page) if offset + len(page) < len(records) else None,
        }


def _asset_bytes(manager: Any, uri: str) -> bytes:
    try:
        content = bytes(manager.get_raw(uri, store=("local", "builtin")))
    except Exception as exc:
        raise NativeManufactureError(
            "A CAM tool catalog asset could not be read.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_UNAVAILABLE",
        ) from exc
    if not content or len(content) > MAX_TOOL_ASSET_BYTES:
        raise NativeManufactureError(
            "A CAM tool catalog asset is empty or exceeds 2 MiB.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_INVALID",
        )
    return content


def capture_tool_catalog() -> ToolCatalogState:
    """Capture the same local ToolBit inventory used by the shipped picker."""

    try:
        from Path.Tool import cam_assets

        uris = sorted(
            (str(uri) for uri in cam_assets.list_assets("toolbit", store="local")),
            key=str.casefold,
        )
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM tool catalog is unavailable.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_UNAVAILABLE",
        ) from exc
    if len(uris) > MAX_TOOL_CATALOG_ITEMS:
        raise NativeManufactureError(
            "The CAM tool catalog exceeds the supported 4096 definitions.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_INVALID",
        )
    records: list[ToolCatalogRecord] = []
    for uri in uris:
        raw = _asset_bytes(cam_assets, uri)
        try:
            decoded = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise NativeManufactureError(
                "A CAM tool definition is not valid JSON.",
                error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_INVALID",
            ) from exc
        if not isinstance(decoded, Mapping) or int(decoded.get("version", 0) or 0) != 2:
            raise NativeManufactureError(
                "A CAM tool definition does not use supported version 2.",
                error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_INVALID",
            )
        shape_id = Path(str(decoded.get("shape") or "")).stem
        if not shape_id:
            raise NativeManufactureError(
                "A CAM tool definition has no shape asset.",
                error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_INVALID",
            )
        shape = _asset_bytes(cam_assets, f"toolbitshape://{shape_id}")
        content_hash = hashlib.sha256()
        for content in (raw, shape):
            content_hash.update(len(content).to_bytes(8, "big"))
            content_hash.update(content)
        catalog_id = "cam-toolbit-v1:" + hashlib.sha256(uri.encode("utf-8")).hexdigest()
        parameters = decoded.get("parameter", {})
        if not isinstance(parameters, Mapping) or len(parameters) > MAX_TOOL_PROPERTIES:
            raise NativeManufactureError(
                "A CAM tool definition has an invalid parameter map.",
                error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_INVALID",
            )
        records.append(
            ToolCatalogRecord(
                catalog_id=catalog_id,
                uri=uri,
                label=str(decoded.get("name") or Path(uri).stem)[:160],
                shape_type=str(decoded.get("shape-type") or shape_id)[:80],
                content_sha256=content_hash.hexdigest(),
                definition=deepcopy(dict(decoded)),
                parameters=tuple(
                    {
                        "property_name": str(name)[:128],
                        **_quantity_descriptor(value),
                    }
                    for name, value in sorted(parameters.items(), key=lambda item: str(item[0]))
                ),
            )
        )
    records.sort(key=lambda item: (item.label.casefold(), item.catalog_id))
    state_sha256 = _digest(
        [
            {
                "catalog_id": record.catalog_id,
                "content_sha256": record.content_sha256,
            }
            for record in records
        ]
    )
    return ToolCatalogState(state_sha256, tuple(records))


def resolve_catalog_record(
    catalog_id: Any,
    expected_content_sha256: Any,
) -> tuple[ToolCatalogState, ToolCatalogRecord]:
    catalog = capture_tool_catalog()
    identifier = str(catalog_id or "").strip()
    expected = str(expected_content_sha256 or "").strip()
    record = next((item for item in catalog.records if item.catalog_id == identifier), None)
    if record is None:
        raise NativeManufactureError(
            "The selected CAM tool is no longer in the host catalog.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_STALE",
        )
    if record.content_sha256 != expected:
        raise NativeManufactureError(
            "The selected CAM tool changed after turn start.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_STALE",
            repair={"current_content_sha256": record.content_sha256},
        )
    return catalog, record


def instantiate_catalog_tool(record: ToolCatalogRecord) -> Any:
    try:
        from Path.Tool.toolbit import ToolBit

        return ToolBit.from_dict(deepcopy(dict(record.definition)))
    except Exception as exc:
        raise NativeManufactureError(
            "The selected CAM tool definition could not be instantiated.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_INVALID",
        ) from exc


def catalog_tool_detail(record: ToolCatalogRecord) -> dict[str, Any]:
    toolbit = instantiate_catalog_tool(record)
    result = record.summary()
    result["editable_properties"] = tool_property_state(toolbit.obj)
    return result
