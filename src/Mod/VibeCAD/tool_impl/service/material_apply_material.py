# SPDX-License-Identifier: LGPL-2.1-or-later

"""Apply one material card to one shaped document object."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_SUMMARY_PHYSICAL_PROPERTIES = (
    "Density",
    "YoungsModulus",
    "PoissonRatio",
    "ThermalConductivity",
)

_SUMMARY_APPEARANCE_PROPERTIES = (
    "DiffuseColor",
    "AmbientColor",
    "SpecularColor",
    "EmissiveColor",
    "Transparency",
)


TOOL_SPEC = {
    "name": "material.apply_material",
    "description": (
        "Apply one material card from the library to one shaped document "
        "object by exact material UUID. This sets the object's ShapeMaterial, "
        "which carries physical properties (density, elasticity) used by FEM "
        "and updates the rendered appearance when the card defines one. Find "
        "UUIDs with material.list_materials first."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "MaterialWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the shaped object (Part::Feature "
                    "or derived) to apply the material to."
                ),
            },
            "material_uuid": {
                "type": "string",
                "description": (
                    "Exact UUID of the material card, as returned by "
                    "material.list_materials."
                ),
            },
        },
        "required": ["object_name", "material_uuid"],
        "additionalProperties": False,
    },
}


def run(service: Any, object_name: str, material_uuid: str) -> dict[str, Any]:
    clean_name = str(object_name or "").strip()
    clean_uuid = str(material_uuid or "").strip()
    if not clean_uuid:
        return _invalid("material_uuid is required.")
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    if not hasattr(obj, "ShapeMaterial"):
        return _invalid(
            f"Object has no ShapeMaterial property (not a shaped Part "
            f"feature): {clean_name}. Materials apply to solid/shape objects, "
            "not groups, sketches, or spreadsheets."
        )
    try:
        import Materials
    except ImportError:
        return _invalid(
            "The Materials module is not available in this FreeCAD build; "
            "materials cannot be applied."
        )
    try:
        manager = Materials.MaterialManager()
        material = manager.getMaterial(clean_uuid)
    except Exception as exc:
        return _invalid(
            f"Material not found by UUID {clean_uuid}: {exc}. Use "
            "material.list_materials to find valid UUIDs."
        )
    if material is None:
        return _invalid(
            f"Material not found by UUID: {clean_uuid}. Use "
            "material.list_materials to find valid UUIDs."
        )
    requested_material = _material_summary(material)
    material_before = _material_summary(getattr(obj, "ShapeMaterial", None))
    appearance_before = _view_appearance_summary(obj)

    def apply() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target = active.getObject(clean_name)
        if target is None:
            raise RuntimeError("The object no longer exists.")
        target.ShapeMaterial = material
        active.recompute()
        assigned_material = getattr(target, "ShapeMaterial", None)
        return {
            "document": active.Name,
            "object": target.Name,
            "object_label": target.Label,
            "requested_material": requested_material,
            "material_before": material_before,
            "material_after": _material_summary(assigned_material),
            "assigned_uuid_readback": str(
                getattr(assigned_material, "UUID", "") or ""
            ),
            "physical_property_readback": _physical_summary(assigned_material),
            "appearance_before": appearance_before,
            "appearance_after": _view_appearance_summary(target),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        physical = result.get("physical_property_readback") or {}
        property_errors = [
            value
            for value in physical.values()
            if isinstance(value, dict) and value.get("status") == "error"
        ]
        before = result.get("appearance_before") or {}
        after = result.get("appearance_after") or {}
        checks = [
            {
                "name": "assigned_uuid_readback",
                "ok": str(result.get("assigned_uuid_readback") or "").lower()
                == clean_uuid.lower(),
                "requested": clean_uuid,
                "actual": result.get("assigned_uuid_readback"),
            },
            {
                "name": "physical_property_readback_complete",
                "ok": not property_errors,
                "properties": physical,
                "property_errors": property_errors,
            },
            {
                "name": "appearance_readback_complete",
                "ok": not list(before.get("errors") or [])
                and not list(after.get("errors") or []),
                "before": before,
                "after": after,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Apply material: {clean_name}",
        apply,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "apply_material", **result},
        next_action=(
            "Use the returned physical-property statuses to decide which FEM "
            "analyses this material supports; inspect the appearance readback "
            "or viewport before continuing."
        ),
    )


def _physical_summary(material: Any) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for name in _SUMMARY_PHYSICAL_PROPERTIES:
        try:
            if not material.hasPhysicalProperty(name):
                summary[name] = {"status": "missing", "value": None}
                continue
            value = material.getPhysicalValue(name)
        except Exception as exc:
            summary[name] = {
                "status": "error",
                "value": None,
                "native_error": str(exc),
            }
            continue
        if value is None:
            summary[name] = {"status": "missing", "value": None}
            continue
        user_string = getattr(value, "UserString", None)
        summary[name] = {
            "status": "present",
            "value": str(user_string) if user_string is not None else str(value),
        }
    return summary


def _material_summary(material: Any) -> dict[str, Any] | None:
    if material is None:
        return None
    summary: dict[str, Any] = {
        "name": str(getattr(material, "Name", "") or ""),
        "uuid": str(getattr(material, "UUID", "") or ""),
        "physical_models": [
            str(item) for item in list(getattr(material, "PhysicalModels", []) or [])
        ],
        "appearance_models": [
            str(item) for item in list(getattr(material, "AppearanceModels", []) or [])
        ],
        "physical_properties": _physical_summary(material),
        "appearance_properties": {},
    }
    appearance: dict[str, dict[str, Any]] = {}
    for name in _SUMMARY_APPEARANCE_PROPERTIES:
        try:
            if not material.hasAppearanceProperty(name):
                appearance[name] = {"status": "missing", "value": None}
                continue
            value = material.getAppearanceValue(name)
            appearance[name] = {"status": "present", "value": _serializable(value)}
        except Exception as exc:
            appearance[name] = {
                "status": "error",
                "value": None,
                "native_error": str(exc),
            }
    summary["appearance_properties"] = appearance
    return summary


def _view_appearance_summary(obj: Any) -> dict[str, Any]:
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return {"supported": False, "values": {}, "errors": []}
    values: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    supported: list[str] = []
    for property_name in ("ShapeColor", "Transparency", "ShapeAppearance"):
        if not hasattr(view, property_name):
            continue
        supported.append(property_name)
        try:
            values[property_name] = _serializable(getattr(view, property_name))
        except Exception as exc:
            errors.append({"property": property_name, "native_error": str(exc)})
    return {
        "supported": bool(supported),
        "supported_properties": supported,
        "values": values,
        "errors": errors,
    }


def _serializable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [_serializable(item) for item in value]
    user_string = getattr(value, "UserString", None)
    if user_string is not None:
        return str(user_string)
    for attributes in (("r", "g", "b", "a"), ("x", "y", "z")):
        if all(hasattr(value, name) for name in attributes):
            return {name: float(getattr(value, name)) for name in attributes}
    return str(value)


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
