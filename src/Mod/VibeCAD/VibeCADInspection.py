# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic, bounded inspection for the active modeling surface."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from VibeCADEditState import active_edit_state
from VibeCADModelingSurface import resolve_service_surface
from VibeCADWorkbenchTools import get_tool_pack


MAX_INSPECT_RESULT_BYTES = 32 * 1024
_PREVIEW_BYTES = 1024
_PROPERTY_SEQUENCE_LIMIT = 256
_HEAVY_PROPERTIES = frozenset({"Mesh", "Points", "Proxy", "Shape", "ViewObject"})

_NATIVE_DOMAIN_READ_ALIASES: dict[str, tuple[str, dict[str, Any]]] = {
    "AssemblyWorkbench": ("assembly.list_structure", {}),
    "CAMWorkbench": ("cam.list_jobs", {}),
    "DraftWorkbench": ("draft.list_objects", {}),
    "FemWorkbench": ("fem.list_analysis", {}),
    "InspectionWorkbench": ("inspection.list_features", {}),
    "MeshWorkbench": ("mesh.list_meshes", {}),
    "PointsWorkbench": ("points.list_clouds", {}),
    "RobotWorkbench": ("robot.list_setup", {}),
    "TechDrawWorkbench": ("techdraw.list_pages", {}),
}


def _surface_summary(resolution: Any) -> dict[str, Any]:
    result = {
        "workbench": str(resolution.workbench or ""),
        "engine": str(resolution.engine or ""),
        "domain": resolution.domain,
        "surface_id": str(resolution.surface_id or ""),
        "available": bool(resolution.available),
    }
    if not resolution.available:
        result["unavailable_reason"] = str(resolution.unavailable_reason or "")
    return result


def _identity(obj: Any) -> dict[str, Any]:
    return {
        "name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type": str(getattr(obj, "TypeId", "") or type(obj).__name__),
    }


def _document_identity(doc: Any) -> dict[str, Any]:
    if doc is None:
        return {"name": None, "uid": None, "object_count": 0}
    return {
        "name": str(getattr(doc, "Name", "") or ""),
        "uid": str(getattr(doc, "Uid", "") or ""),
        "object_count": len(getattr(doc, "Objects", []) or []),
    }


def _stable_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth >= 5:
        return {"omitted": True, "reason": "value_depth", "type": type(value).__name__}
    if hasattr(value, "Name") and hasattr(value, "TypeId"):
        return _identity(value)
    if isinstance(value, Mapping):
        items = list(value.items())
        result = {
            str(key): _stable_value(item, depth=depth + 1)
            for key, item in items[:_PROPERTY_SEQUENCE_LIMIT]
        }
        if len(items) > _PROPERTY_SEQUENCE_LIMIT:
            result["_items_omitted"] = len(items) - _PROPERTY_SEQUENCE_LIMIT
        return result
    if isinstance(value, (list, tuple)):
        values = list(value)
        result = [
            _stable_value(item, depth=depth + 1)
            for item in values[:_PROPERTY_SEQUENCE_LIMIT]
        ]
        if len(values) > _PROPERTY_SEQUENCE_LIMIT:
            result.append({"items_omitted": len(values) - _PROPERTY_SEQUENCE_LIMIT})
        return result
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        try:
            return {
                "x": float(value.x),
                "y": float(value.y),
                "z": float(value.z),
            }
        except Exception:
            pass
    quantity_value = getattr(value, "Value", None)
    quantity_unit = getattr(value, "Unit", None)
    if isinstance(quantity_value, (int, float)) and quantity_unit is not None:
        return {"value": float(quantity_value), "unit": str(quantity_unit)}
    text = str(value)
    if len(text) <= 2048:
        return text
    return {
        "omitted": True,
        "reason": "value_text_size",
        "characters": len(text),
        "utf8_bytes": len(text.encode("utf-8", errors="replace")),
        "type": type(value).__name__,
    }


def _object_detail(obj: Any) -> dict[str, Any]:
    result = _identity(obj)
    properties = list(getattr(obj, "PropertiesList", []) or [])
    captured: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for raw_name in properties:
        name = str(raw_name)
        if name in _HEAVY_PROPERTIES:
            captured[name] = {
                "omitted": True,
                "reason": "heavy_native_property",
                "property_type": str(
                    getattr(obj, "getTypeIdOfProperty", lambda _name: "")(name) or ""
                ),
            }
            continue
        try:
            value = getattr(obj, name)
            captured[name] = {
                "property_type": str(
                    getattr(obj, "getTypeIdOfProperty", lambda _name: "")(name) or ""
                ),
                "group": str(
                    getattr(obj, "getGroupOfProperty", lambda _name: "")(name) or ""
                ),
                "value": _stable_value(value),
            }
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
    result["property_count"] = len(properties)
    result["properties"] = captured
    if errors:
        result["property_errors"] = errors
    return result


def _selection_snapshot() -> dict[str, Any]:
    try:
        import FreeCADGui as Gui

        raw = list(Gui.Selection.getSelectionEx() or [])
    except Exception as exc:
        return {"selection_count": 0, "items": [], "error": str(exc)}
    items = []
    for selected in raw:
        try:
            subelements = [str(name) for name in list(selected.SubElementNames or [])]
            items.append(
                {
                    **_identity(selected.Object),
                    "subelement_count": len(subelements),
                    "subelements": subelements,
                }
            )
        except Exception:
            continue
    return {"selection_count": len(raw), "items": items}


def _edit_object() -> dict[str, Any] | None:
    state = active_edit_state()
    if state.document_object is not None:
        return _identity(state.document_object)
    if not state.active:
        return None
    return {
        "name": "",
        "label": "",
        "type": type(state.view_provider).__name__,
        "resolved": False,
    }


def _basic_domain_objects(doc: Any, workbench: str) -> list[dict[str, Any]]:
    pack = get_tool_pack(workbench)
    if doc is None or pack is None:
        return []
    result = []
    for obj in list(getattr(doc, "Objects", []) or []):
        type_id = str(getattr(obj, "TypeId", "") or "")
        if pack.object_types and not any(
            type_id.startswith(prefix) for prefix in pack.object_types
        ):
            continue
        result.append(_identity(obj))
    return result


def _capture_native_domain(service: Any, workbench: str, target: str) -> Any:
    if workbench == "SpreadsheetWorkbench" and target:
        return service.registry.call(
            "spreadsheet.read_sheet", sheet_name=target, cursor=0, limit=500
        )
    alias = _NATIVE_DOMAIN_READ_ALIASES.get(workbench)
    if alias is not None:
        tool_name, arguments = alias
        return service.registry.call(tool_name, **arguments)
    return {
        "workbench": workbench,
        "objects": _basic_domain_objects(service._active_document(), workbench),
    }


def _capture_scripted_domain(service: Any, resolution: Any) -> dict[str, Any]:
    engine = str(resolution.engine)
    doc = service._active_document()
    if engine == "vibescript":
        from VibeCADVibeScriptDomains import capture_domain_programs, get_vibescript_pack

        pack = get_vibescript_pack(str(resolution.workbench or ""))
        return {
            "kind": "domain_v2",
            "domain": str(resolution.domain or ""),
            "workbench": str(resolution.workbench or ""),
            "project_root": str(service.project_scope_snapshot().get("root") or ""),
            "native_programs": (
                capture_domain_programs(doc, str(resolution.domain or ""))
                if doc is not None
                else []
            ),
            "contract": {
                "program_schema": "vibecad-vibescript-program-v2",
                "output_types": list(pack.output_types) if pack is not None else [],
                "api_exports": list(pack.api_exports) if pack is not None else [],
            },
        }
    module_name = "VibeCADBuild123d" if engine == "build123d" else "VibeCADOpenSCAD"
    module = __import__(module_name)
    native = []
    if doc is not None:
        try:
            native = [
                module._model_summary(obj, include_source=False)
                for obj in module._model_objects(doc)
            ]
        except Exception:
            native = []
    return {
        "kind": "scripted_engine_domain",
        "engine": engine,
        "project_root": str(service.project_scope_snapshot().get("root") or ""),
        "native_models": native,
    }


def _capture_program(service: Any, resolution: Any, target: str) -> dict[str, Any]:
    if not target:
        raise ValueError("program scope requires an exact stable program or model id.")
    engine = str(resolution.engine)
    if engine == "vibescript":
        from VibeCADVibeScriptDomains import capture_domain_programs, get_vibescript_pack

        pack = get_vibescript_pack(str(resolution.workbench or ""))
        if pack is None:
            raise ValueError("The active VibeScript domain is unavailable.")
        doc = service._active_document()
        return {
            "kind": "domain_v2_program",
            "captured": {
                "tool_name": f"vibescript.{resolution.domain}.inspect_program",
                "pack": pack,
                "project_root": str(service.project_scope_snapshot().get("root") or ""),
                "program_id": target,
                "live_programs": (
                    capture_domain_programs(doc, str(resolution.domain or ""))
                    if doc is not None
                    else []
                ),
            },
        }
    if engine not in {"build123d", "openscad"}:
        raise ValueError("program scope is available only for a scripted modeling engine.")
    module_name = "VibeCADBuild123d" if engine == "build123d" else "VibeCADOpenSCAD"
    module = __import__(module_name)
    doc = service._active_document()
    container = None
    native = None
    if doc is not None:
        try:
            container = module._find_model(doc, target)
            if container is not None:
                native = module._model_summary(container, include_source=True)
        except Exception:
            native = None
    return {
        "kind": "scripted_engine_program",
        "engine": engine,
        "program_id": target,
        "project_root": str(service.project_scope_snapshot().get("root") or ""),
        "native_model": native,
    }


def capture_inspection(service: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Capture only document-affine state; artifact reads happen in complete()."""

    scope = str(arguments.get("scope") or "")
    target = str(arguments.get("target") or "")
    path = str(arguments.get("path") or "")
    offset = int(arguments.get("offset") or 0)
    limit = int(arguments.get("limit") or 20)
    attach = bool(arguments.get("attach", False))
    if offset < 0 or offset > 100000000:
        raise ValueError("offset must be between 0 and 100000000.")
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50.")
    if len(target) > 512 or len(path) > 512:
        raise ValueError("target and path are each limited to 512 characters.")
    if attach and scope != "image":
        raise ValueError("attach=true is valid only for image scope.")
    if scope in {"object", "program"} and not target:
        raise ValueError(f"{scope} scope requires an exact target.")
    if scope == "image" and attach and not target:
        raise ValueError("image scope requires an exact target when attach=true.")
    workbench = service.active_workbench_name()
    resolution = resolve_service_surface(service, workbench)
    doc = service._active_document()
    common = {
        "scope": scope,
        "target": target,
        "path": path,
        "offset": offset,
        "limit": limit,
        "attach": attach,
        "surface": _surface_summary(resolution),
        "document": _document_identity(doc),
    }
    if scope == "document":
        objects = (getattr(doc, "Objects", []) or []) if doc is not None else []
        if path == "/objects":
            total = len(objects)
            end = min(offset + limit, total)
            values = [_identity(objects[index]) for index in range(offset, end)]
            return {
                **common,
                "kind": "document_objects_page",
                "values": values,
                "total": total,
            }
        if path.startswith("/objects/"):
            raise ValueError(
                "Inspect /objects as a page, then use object scope with the exact "
                "returned internal name."
            )
        raw = {
            **common["document"],
            "edit_object": _edit_object(),
            "objects": {
                "type": "array",
                "item_count": len(objects),
                "inspect_path": "/objects",
            },
        }
        return {**common, "kind": "captured", "raw": raw}
    if scope == "selection":
        return {**common, "kind": "captured", "raw": _selection_snapshot()}
    if scope == "object":
        if doc is None:
            raise ValueError("No active document.")
        obj = doc.getObject(target) if target else None
        if obj is None:
            raise ValueError(f"Object not found by exact internal name: {target!r}.")
        return {**common, "kind": "captured", "raw": _object_detail(obj)}
    if scope == "domain":
        if not resolution.available:
            raise ValueError(
                str(resolution.unavailable_reason or "The active domain is unavailable.")
            )
        if str(resolution.engine) == "native":
            if workbench == "MaterialWorkbench":
                # MaterialManager may scan and parse the installed material
                # library. It needs no live-document access, so defer it until
                # after the document-thread capture has returned.
                return {
                    **common,
                    "kind": "native_material_domain",
                    "name_filter": target,
                }
            return {
                **common,
                "kind": "captured",
                "raw": _capture_native_domain(service, str(workbench or ""), target),
            }
        return {**common, **_capture_scripted_domain(service, resolution)}
    if scope == "program":
        return {**common, **_capture_program(service, resolution, target)}
    if scope == "api":
        schemas = [
            service.registry.get(name).to_schema(active_workbench=workbench)
            for name in resolution.tool_names
            if name in service.registry.names()
        ]
        return {**common, "kind": "api", "schemas": schemas}
    if scope == "image":
        return {
            **common,
            "kind": "image",
            "project_root": str(service.project_scope_snapshot().get("root") or ""),
        }
    raise ValueError(f"Unknown core.inspect scope: {scope!r}.")


def _complete_scripted_engine_domain(captured: Mapping[str, Any]) -> dict[str, Any]:
    engine = str(captured["engine"])
    module = __import__("VibeCADBuild123d" if engine == "build123d" else "VibeCADOpenSCAD")
    project_root = Path(str(captured["project_root"]))
    summaries = {
        str(item.get("model_id") or ""): dict(item)
        for item in list(captured.get("native_models") or [])
        if isinstance(item, dict) and item.get("model_id")
    }
    root = project_root / engine
    if root.is_dir():
        for directory in sorted(root.iterdir()):
            if not directory.is_dir():
                continue
            try:
                contract = module._artifact_contract(project_root, directory.name)
                if contract is None:
                    continue
                item = module._artifact_summary(contract, include_source=False)
            except Exception:
                continue
            native = summaries.get(directory.name)
            if native is not None and native.get("object_name"):
                item["object_name"] = native["object_name"]
            summaries[directory.name] = item
    return {"engine": engine, "models": list(summaries.values())}


def _complete_scripted_engine_program(captured: Mapping[str, Any]) -> dict[str, Any]:
    engine = str(captured["engine"])
    module = __import__("VibeCADBuild123d" if engine == "build123d" else "VibeCADOpenSCAD")
    project_root = Path(str(captured["project_root"]))
    program_id = str(captured["program_id"])
    contract = module._artifact_contract(project_root, program_id)
    native = captured.get("native_model")
    if contract is None and not isinstance(native, dict):
        return {"ok": False, "error": f"No {engine} model has id {program_id!r}."}
    model = (
        module._artifact_summary(contract, include_source=True)
        if contract is not None
        else dict(native)
    )
    if isinstance(native, dict) and native.get("object_name"):
        model["object_name"] = native["object_name"]
    return {"ok": True, "model": model}


def _complete_api(captured: Mapping[str, Any]) -> Any:
    surface = captured["surface"]
    if surface.get("engine") != "vibescript":
        return {"surface": surface, "tools": list(captured.get("schemas") or [])}
    from VibeCADVibeScriptDomains import get_vibescript_pack
    from VibeCADVibeScriptDomainRuntime import describe_api

    pack = get_vibescript_pack(str(surface.get("workbench") or ""))
    if pack is None:
        return {"ok": False, "error": "The active VibeScript domain is unavailable."}
    return describe_api(pack)


def _complete_image(captured: Mapping[str, Any]) -> tuple[Any, dict[str, Any] | None]:
    root = Path(str(captured.get("project_root") or ""))
    state_path = root / "references.json"
    entries: list[dict[str, Any]] = []
    if state_path.is_file():
        data = json.loads(state_path.read_text(encoding="utf-8"))
        raw = data.get("reference_images") if isinstance(data, dict) else None
        if isinstance(raw, list):
            entries = [dict(item) for item in raw if isinstance(item, dict)]
    target = str(captured.get("target") or "")
    if not target:
        return {
            "image_count": len(entries),
            "images": [
                {key: item.get(key) for key in ("id", "name", "label", "format", "image_size")}
                for item in entries
            ],
        }, None
    selected = next(
        (
            item
            for item in entries
            if str(item.get("id") or "") == target
            or str(item.get("name") or "") == target
        ),
        None,
    )
    if selected is None:
        return {"ok": False, "error": f"No reference image matches {target!r}."}, None
    public = {
        key: selected.get(key)
        for key in ("id", "name", "label", "format", "size_bytes", "image_size", "attached_at")
        if selected.get(key) not in (None, "", [], {})
    }
    if not captured.get("attach"):
        return {"ok": True, "image": public, "attached": False}, None
    path = Path(str(selected.get("path") or ""))
    allowed_root = (root / "references").resolve()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(allowed_root)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Reference image artifact is unavailable: {exc}"}, None
    if not resolved.is_file():
        return {"ok": False, "error": "Reference image artifact is not a file."}, None
    return (
        {"ok": True, "image": public, "attached": True},
        {"path": str(resolved), "name": str(selected.get("name") or target)},
    )


def _json_pointer_parts(pointer: str) -> list[str]:
    if not pointer:
        return []
    if not pointer.startswith("/"):
        raise ValueError("path must be an empty string or a JSON Pointer beginning with '/'.")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _resolve_pointer(value: Any, pointer: str) -> Any:
    current = value
    for part in _json_pointer_parts(pointer):
        if isinstance(current, Mapping):
            if part not in current:
                raise ValueError(f"JSON Pointer path does not exist: {pointer!r}.")
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"JSON Pointer path does not exist: {pointer!r}.") from exc
        else:
            raise ValueError(f"JSON Pointer traverses a scalar: {pointer!r}.")
    return current


def _pointer_child(pointer: str, value: str | int) -> str:
    escaped = str(value).replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{escaped}" if pointer else f"/{escaped}"


def _encoded_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _preview(value: Any, pointer: str) -> Any:
    if _encoded_bytes(value) <= _PREVIEW_BYTES:
        return value
    if isinstance(value, Mapping):
        return {"type": "object", "entry_count": len(value), "inspect_path": pointer}
    if isinstance(value, list):
        return {"type": "array", "item_count": len(value), "inspect_path": pointer}
    if isinstance(value, str):
        return {
            "type": "string",
            "characters": len(value),
            "utf8_bytes": len(value.encode("utf-8", errors="replace")),
            "inspect_path": pointer,
        }
    return {"type": type(value).__name__, "json_bytes": _encoded_bytes(value)}


def _page_value(value: Any, pointer: str, offset: int, limit: int) -> tuple[Any, dict[str, Any]]:
    if isinstance(value, Mapping):
        keyed = sorted(((str(key), key) for key in value), key=lambda item: item[0])
        string_keys = [item[0] for item in keyed]
        if len(set(string_keys)) != len(string_keys):
            raise ValueError("The inspected mapping contains ambiguous non-string keys.")
        selected = keyed[offset : offset + limit]
        page = {
            public_key: _preview(value[actual_key], _pointer_child(pointer, public_key))
            for public_key, actual_key in selected
        }
        return page, {
            "kind": "mapping",
            "total": len(keyed),
            "offset": offset,
            "returned": len(selected),
            "next_offset": (
                offset + len(selected)
                if offset + len(selected) < len(keyed)
                else None
            ),
        }
    if isinstance(value, list):
        selected = value[offset : offset + limit]
        return [
            _preview(item, _pointer_child(pointer, offset + index))
            for index, item in enumerate(selected)
        ], {
            "kind": "array",
            "total": len(value),
            "offset": offset,
            "returned": len(selected),
            "next_offset": offset + len(selected) if offset + len(selected) < len(value) else None,
        }
    if isinstance(value, str):
        character_limit = limit * 1024
        selected = value[offset : offset + character_limit]
        return selected, {
            "kind": "string",
            "total_characters": len(value),
            "offset": offset,
            "returned_characters": len(selected),
            "next_offset": offset + len(selected) if offset + len(selected) < len(value) else None,
        }
    if offset:
        raise ValueError("offset must be zero when the selected path is a scalar.")
    return value, {"kind": "scalar", "returned": 1, "next_offset": None}


def _bounded_page(raw: Any, captured: Mapping[str, Any]) -> dict[str, Any]:
    pointer = str(captured.get("path") or "")
    selected = _resolve_pointer(raw, pointer)
    offset = int(captured.get("offset") or 0)
    requested_limit = int(captured.get("limit") or 1)
    effective_limit = requested_limit
    while True:
        value, page = _page_value(selected, pointer, offset, effective_limit)
        result = {
            "ok": True,
            "scope": str(captured.get("scope") or ""),
            "target": str(captured.get("target") or ""),
            "path": pointer,
            "surface": dict(captured.get("surface") or {}),
            "document": dict(captured.get("document") or {}),
            "page": {
                **page,
                "requested_limit": requested_limit,
                "effective_limit": effective_limit,
            },
            "value": value,
        }
        result["result_json_bytes"] = 0
        size = _encoded_bytes(result)
        while result["result_json_bytes"] != size:
            result["result_json_bytes"] = size
            size = _encoded_bytes(result)
        if size <= MAX_INSPECT_RESULT_BYTES:
            return result
        if effective_limit > 1:
            effective_limit = max(1, effective_limit // 2)
            continue
        raise RuntimeError("The selected scalar exceeds the 32 KiB inspection result limit.")


def _bounded_document_objects_page(captured: Mapping[str, Any]) -> dict[str, Any]:
    values = list(captured.get("values") or [])
    requested_limit = int(captured.get("limit") or 1)
    effective_limit = len(values)
    offset = int(captured.get("offset") or 0)
    total = int(captured.get("total") or 0)
    while True:
        page_values = values[:effective_limit]
        returned = len(page_values)
        result = {
            "ok": True,
            "scope": "document",
            "target": str(captured.get("target") or ""),
            "path": "/objects",
            "surface": dict(captured.get("surface") or {}),
            "document": dict(captured.get("document") or {}),
            "page": {
                "kind": "array",
                "total": total,
                "offset": offset,
                "returned": returned,
                "next_offset": offset + returned if offset + returned < total else None,
                "requested_limit": requested_limit,
                "effective_limit": effective_limit,
            },
            "value": page_values,
        }
        result["result_json_bytes"] = 0
        size = _encoded_bytes(result)
        while result["result_json_bytes"] != size:
            result["result_json_bytes"] = size
            size = _encoded_bytes(result)
        if size <= MAX_INSPECT_RESULT_BYTES:
            return result
        if effective_limit > 1:
            effective_limit = max(1, effective_limit // 2)
            continue
        raise RuntimeError("One document object identity exceeds the inspection result limit.")


def complete_inspection(captured: Mapping[str, Any]) -> dict[str, Any]:
    """Complete artifact-backed reads and apply the exact result-size boundary."""

    try:
        kind = str(captured.get("kind") or "")
        attachment = None
        if kind == "captured":
            raw = captured.get("raw")
        elif kind == "document_objects_page":
            return _bounded_document_objects_page(captured)
        elif kind == "domain_v2":
            from VibeCADVibeScriptDomains import complete_domain_context

            raw = complete_domain_context(
                {
                    "_vibecad_deferred_vibescript_domain_context": True,
                    "domain": captured["domain"],
                    "workbench": captured["workbench"],
                    "project_root": captured["project_root"],
                    "native_programs": list(captured.get("native_programs") or []),
                    "contract": dict(captured.get("contract") or {}),
                }
            )
        elif kind == "scripted_engine_domain":
            raw = _complete_scripted_engine_domain(captured)
        elif kind == "domain_v2_program":
            from VibeCADVibeScriptDomainRuntime import complete_inspection as complete_program

            raw = complete_program(captured["captured"])
        elif kind == "scripted_engine_program":
            raw = _complete_scripted_engine_program(captured)
        elif kind == "native_material_domain":
            from tool_impl.service import material_list_materials

            raw = material_list_materials.run(
                service=None,
                name_filter=str(captured.get("name_filter") or ""),
            )
        elif kind == "api":
            raw = _complete_api(captured)
        elif kind == "image":
            raw, attachment = _complete_image(captured)
        else:
            raise ValueError("Invalid captured core.inspect operation.")
        result = _bounded_page(raw, captured)
        if attachment is not None:
            result["_vibecad_image_attachment"] = attachment
        return result
    except Exception as exc:
        error = str(exc)
        if len(error) > 4096:
            error = error[:4093] + "..."
        result = {
            "ok": False,
            "tool": "core.inspect",
            "failure_code": "INSPECTION_FAILED",
            "failure_stage": "precondition",
            "error": error,
            "scope": str(captured.get("scope") or ""),
            "target": str(captured.get("target") or ""),
        }
        result["result_json_bytes"] = 0
        size = _encoded_bytes(result)
        while result["result_json_bytes"] != size:
            result["result_json_bytes"] = size
            size = _encoded_bytes(result)
        return result
