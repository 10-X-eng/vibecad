# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Surface sections loft through exact profile curves."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime
from .surface_fill import (
    CURVE_REF_ITEM_SCHEMA,
    build_link_sub_list,
    loft_profile_diagnostics,
    validate_curve_refs,
)


TOOL_SPEC = {
    "name": "surface.loft",
    "description": (
        "Create one native Surface sections loft that blends smoothly through "
        "two or more exact profile curves in order. Profiles must not "
        "intersect each other; list them in the order the surface should pass "
        "through them. Use exact profile and edge names from the active Surface "
        "context."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "SurfaceWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "profiles": {
                "type": "array",
                "items": CURVE_REF_ITEM_SCHEMA,
                "minItems": 2,
                "description": (
                    "Profile curve references, in the order the surface "
                    "passes through them."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new loft surface.",
            },
        },
        "required": ["profiles", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    profiles: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    validation = validate_curve_refs(service, profiles, "profiles")
    if not validation.get("ok"):
        return validation
    refs = validation["refs"]
    if len(refs) < 2:
        return _invalid("profiles must contain at least two curve references.")
    compatibility = loft_profile_diagnostics(service, refs)
    if not compatibility.get("ok"):
        return _invalid(
            "The ordered surface sections intersect, have incompatible closure, or could not be resolved natively; no loft was created.",
            resolved_profiles=validation["resolved_curves"],
            section_compatibility=compatibility,
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        sections = active.addObject("Surface::Sections", "SurfaceLoft")
        sections.Label = clean_label
        sections.NSections = build_link_sub_list(active, refs)
        active.recompute()
        return {
            "document": active.Name,
            "feature": sections.Name,
            "feature_label": sections.Label,
            "feature_type": sections.TypeId,
            "profiles": validation["resolved_curves"],
            "section_compatibility": compatibility,
            "actual_section_count": len(list(getattr(sections, "NSections", []) or [])),
            "shape": domain_runtime.shape_summary(sections),
            "feature_state": domain_runtime.feature_state_summary(sections),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        shape = result.get("shape") or {}
        state = result.get("feature_state") or {}
        checks = [
            {
                "name": "section_count",
                "ok": int(result.get("actual_section_count", 0)) == len(refs),
                "expected": len(refs),
                "actual": result.get("actual_section_count"),
            },
            {
                "name": "surface_created",
                "ok": int(shape.get("faces", 0)) > 0
                and state.get("shape_valid") is True
                and not state.get("marked_invalid"),
                "actual": shape,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create surface loft: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation="surface_loft")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
