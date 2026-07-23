# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical loft tool for Body material and standalone geometry."""

from copy import deepcopy
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime, partdesign_additive_loft, partdesign_loft_feature


TOOL_SPEC = deepcopy(partdesign_additive_loft.TOOL_SPEC)
TOOL_SPEC["name"] = "model.loft"
TOOL_SPEC["description"] = (
    "Loft through ordered profiles. Explicitly create a standalone solid or surface, add "
    "material to a Body, or remove material from a Body."
)
TOOL_SPEC["parameters"]["properties"]["operation"] = {
    "type": "string",
    "enum": ["new_solid", "new_surface", "add_material", "remove_material"],
    "description": "Whether the loft creates standalone geometry or changes Body material.",
}
TOOL_SPEC["parameters"]["required"].insert(0, "operation")
TOOL_SPEC["parameters"]["properties"]["profile_names"]["description"] = (
    "Exact internal names of section objects in loft order. Body operations require closed "
    "sketches in one Body; standalone operations accept planar wire or face objects."
)
for _body_only in ("reversed", "midplane", "refine"):
    TOOL_SPEC["parameters"]["properties"][_body_only]["description"] += (
        " This setting applies only to Body material operations."
    )


def run(service: Any, operation: str, **arguments: Any) -> dict[str, Any]:
    choices = {
        "add_material": ("additive_loft", "PartDesign::AdditiveLoft"),
        "remove_material": ("subtractive_loft", "PartDesign::SubtractiveLoft"),
    }
    if operation in choices:
        native_operation, type_id = choices[operation]
        result = partdesign_loft_feature.run(
            service,
            operation=native_operation,
            type_id=type_id,
            **arguments,
        )
    elif operation in {"new_solid", "new_surface"}:
        result = _run_standalone(
            service,
            solid=operation == "new_solid",
            **arguments,
        )
    else:
        return _invalid(
            "operation must be new_solid, new_surface, add_material, or remove_material."
        )
    return _retag(result, operation)


def _run_standalone(
    service: Any,
    *,
    profile_names: list[str],
    label: str,
    closed: bool,
    ruled: bool,
    reversed: bool,
    midplane: bool,
    refine: bool,
    solid: bool,
) -> dict[str, Any]:
    if reversed or midplane:
        return _invalid(
            "Standalone lofts do not support reversed or midplane; both must be false."
        )
    if refine:
        return _invalid(
            "Standalone lofts do not have a native refine option; set refine=false."
        )
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if not isinstance(profile_names, list) or len(profile_names) < 2:
        return _invalid("profile_names must contain at least two ordered object names.")
    exact_names = [str(name or "").strip() for name in profile_names]
    if any(not name for name in exact_names):
        return _invalid("Every profile name must be non-empty.")
    if len(set(exact_names)) != len(exact_names):
        return _invalid("profile_names contains a duplicate object.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    profiles = [doc.getObject(name) for name in exact_names]
    missing = [name for name, profile in zip(exact_names, profiles) if profile is None]
    if missing:
        return _invalid(
            "One or more loft profiles were not found by exact internal name.",
            missing_profiles=missing,
        )
    diagnostics = {
        profile.Name: domain_runtime.shape_profile_diagnostics(profile)
        for profile in profiles
    }
    invalid = [
        name
        for name, state in diagnostics.items()
        if not state.get("planar")
        or not state.get("all_wires_valid")
        or (solid and not state.get("face_buildable"))
    ]
    if invalid:
        return _invalid(
            (
                "Every standalone solid loft section must be a valid closed planar profile."
                if solid
                else "Every standalone surface loft section must contain valid planar wires."
            ),
            invalid_profiles=invalid,
            profile_diagnostics=diagnostics,
        )
    visibility_before = {
        profile.Name: domain_runtime.view_visibility_summary(profile)
        for profile in profiles
    }

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        sections = [active.getObject(name) for name in exact_names]
        if any(section is None for section in sections):
            raise RuntimeError("A loft profile no longer exists.")
        loft = active.addObject("Part::Loft", "Loft")
        loft.Label = clean_label
        loft.Sections = sections
        loft.Solid = bool(solid)
        loft.Ruled = bool(ruled)
        loft.Closed = bool(closed)
        domain_runtime.adopt_part_result(loft)
        active.recompute()
        for section in sections:
            view = getattr(section, "ViewObject", None)
            if view is not None and hasattr(view, "Visibility"):
                view.Visibility = False
        return {
            "document": active.Name,
            "feature": loft.Name,
            "feature_label": loft.Label,
            "feature_type": loft.TypeId,
            "profiles": [section.Name for section in sections],
            "profile_diagnostics": diagnostics,
            "solid_requested": bool(solid),
            "ruled": bool(loft.Ruled),
            "closed": bool(loft.Closed),
            "source_visibility_before": visibility_before,
            "source_visibility_after": {
                section.Name: domain_runtime.view_visibility_summary(section)
                for section in sections
            },
            "shape": domain_runtime.shape_summary(loft),
            "feature_state": domain_runtime.feature_state_summary(loft),
        }

    transaction = run_freecad_transaction(
        f"Create standalone loft: {clean_label}",
        create,
    )
    return domain_runtime.part_feature_result(transaction, operation="loft")


def _retag(result: dict[str, Any], operation: str) -> dict[str, Any]:
    if isinstance(result, dict):
        result["operation"] = "loft"
        result["material_operation"] = operation
    return result


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
