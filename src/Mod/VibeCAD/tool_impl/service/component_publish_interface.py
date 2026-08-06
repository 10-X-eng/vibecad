# SPDX-License-Identifier: LGPL-2.1-or-later

"""Publish an exact existing native LCS as a reusable component interface."""

from __future__ import annotations

from typing import Any

from vibescript_assembly_api import JOINT_TYPES


def _reference_schema(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "document_uid": {"type": "string", "minLength": 1},
            "object_name": {"type": "string", "minLength": 1},
            "document_path": {"type": "string", "minLength": 1},
        },
        "required": ["document_uid", "object_name"],
        "additionalProperties": False,
    }


TOOL_SPEC = {
    "name": "component.publish_interface",
    "description": (
        "Publish an existing native local coordinate system as an explicit reusable "
        "component interface. Use this only for human-authored native components; edit "
        "a VibeScript component's interfaces in its source instead. This tool never "
        "infers an axis, face, placement, or compatibility."
    ),
    "contextual": False,
    "requires_document": True,
    "safety": "SAFE_WRITE",
    "workbench": None,
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "component": _reference_schema("Exact component reference from the catalog."),
            "lcs": _reference_schema(
                "Exact existing native coordinate-system reference owned by the component."
            ),
            "name": {
                "type": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$",
                "description": "Stable component-local name such as RotationAxis.",
            },
            "kind": {
                "type": "string",
                "enum": ["axis", "plane", "point", "frame"],
                "description": "Exact semantic kind of this connector frame.",
            },
            "allowed_joints": {
                "type": "array",
                "items": {"type": "string", "enum": list(JOINT_TYPES)},
                "uniqueItems": True,
                "maxItems": len(JOINT_TYPES),
                "description": "Optional exact Assembly joint kinds this interface accepts.",
            },
            "compatibility": {
                "type": "string",
                "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
                "description": "Optional exact token that both mating interfaces must share.",
            },
        },
        "required": ["component", "lcs", "name", "kind"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    component: dict[str, Any],
    lcs: dict[str, Any],
    name: str,
    kind: str,
    allowed_joints: list[str] | None = None,
    compatibility: str = "",
) -> dict[str, Any]:
    from VibeCADDocumentReferences import (
        DocumentReferenceError,
        resolve_reference_target,
    )
    from VibeCADReferenceContracts import (
        ReferenceContractError,
        publish_native_interface,
    )

    owner = service._active_document()
    if owner is None:
        return {
            "ok": False,
            "failure_code": "NO_DOCUMENT",
            "failure_stage": "precondition",
            "error": "No active document is available.",
        }
    try:
        component_obj = resolve_reference_target(
            owner,
            component,
            "Component interface owner",
            open_missing=False,
        )
        lcs_obj = resolve_reference_target(
            owner,
            lcs,
            "Component interface LCS",
            open_missing=False,
        )
    except DocumentReferenceError as exc:
        return {
            "ok": False,
            "failure_code": "INTERFACE_REFERENCE_INVALID",
            "failure_stage": "precondition",
            "error": str(exc),
        }
    if str(getattr(component_obj, "VibeCADVibeScriptProgramId", "") or ""):
        return {
            "ok": False,
            "failure_code": "VIBESCRIPT_SOURCE_REQUIRED",
            "failure_stage": "precondition",
            "error": (
                "This component is VibeScript-owned. Read its authoring_source and "
                "declare the interface in api.body(..., interfaces=...) instead."
            ),
            "required_changes": [
                {
                    "tool": "vibescript.read_source",
                    "arguments": {
                        "program": "/".join(
                            (
                                str(getattr(component_obj.Document, "Name", "") or ""),
                                str(
                                    getattr(
                                        component_obj,
                                        "VibeCADVibeScriptDomain",
                                        "",
                                    )
                                    or ""
                                ),
                                str(
                                    getattr(
                                        component_obj,
                                        "VibeCADVibeScriptProgramLabel",
                                        "",
                                    )
                                    or ""
                                ),
                            )
                        ),
                        "include_logs": False,
                    },
                }
            ],
        }
    document = component_obj.Document
    transaction_open = False
    try:
        if hasattr(document, "openTransaction"):
            document.openTransaction("Publish component interface")
            transaction_open = True
        definition = publish_native_interface(
            component_obj,
            lcs_obj,
            name=name,
            kind=kind,
            allowed_joints=allowed_joints or [],
            compatibility=compatibility,
        )
        document.recompute()
        if hasattr(document, "commitTransaction") and transaction_open:
            document.commitTransaction()
            transaction_open = False
    except (ReferenceContractError, RuntimeError, ValueError) as exc:
        if transaction_open and hasattr(document, "abortTransaction"):
            document.abortTransaction()
        return {
            "ok": False,
            "failure_code": "INTERFACE_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": str(exc),
        }
    return {
        "ok": True,
        "component": dict(component),
        "lcs": dict(lcs),
        "interface_name": str(name),
        "interface": definition,
        "saved": False,
    }
