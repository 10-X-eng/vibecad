# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical sweep tool for Body material and standalone geometry."""

from copy import deepcopy
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime, partdesign_additive_pipe, partdesign_pipe_feature


TOOL_SPEC = deepcopy(partdesign_additive_pipe.TOOL_SPEC)
TOOL_SPEC["name"] = "model.sweep"
TOOL_SPEC["description"] = (
    "Sweep a profile along an exact path. Explicitly create a standalone solid or surface, add "
    "material to a Body, or remove material from a Body."
)
TOOL_SPEC["parameters"]["properties"]["operation"] = {
    "type": "string",
    "enum": ["new_solid", "new_surface", "add_material", "remove_material"],
    "description": "Whether the sweep creates standalone geometry or changes Body material.",
}
TOOL_SPEC["parameters"]["required"].insert(0, "operation")
TOOL_SPEC["parameters"]["properties"]["profile_name"]["description"] = (
    "Exact internal name of the profile. Body operations require a closed sketch in one Body; "
    "standalone operations accept a planar wire or face object."
)
TOOL_SPEC["parameters"]["properties"]["spine_name"]["description"] = (
    "Exact internal name of the path object. Body operations require the same Body; standalone "
    "operations may use any edged object in the document."
)


def run(service: Any, operation: str, **arguments: Any) -> dict[str, Any]:
    choices = {
        "add_material": ("additive_pipe", "PartDesign::AdditivePipe"),
        "remove_material": ("subtractive_pipe", "PartDesign::SubtractivePipe"),
    }
    if operation in choices:
        native_operation, type_id = choices[operation]
        result = partdesign_pipe_feature.run(
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
    if isinstance(result, dict):
        result["operation"] = "sweep"
        result["material_operation"] = operation
    return result


def _run_standalone(
    service: Any,
    *,
    profile_name: str,
    spine_name: str,
    section_names: list[str],
    label: str,
    orientation: str,
    transformation: str,
    transition: str,
    spine_tangent: bool,
    auxiliary_spine_tangent: bool,
    auxiliary_curvilinear: bool,
    reversed: bool,
    midplane: bool,
    refine: bool,
    solid: bool,
    auxiliary_spine_name: str | None = None,
    binormal: dict[str, float] | None = None,
) -> dict[str, Any]:
    unsupported_flags = {
        "spine_tangent": spine_tangent,
        "auxiliary_spine_tangent": auxiliary_spine_tangent,
        "auxiliary_curvilinear": auxiliary_curvilinear,
        "reversed": reversed,
        "midplane": midplane,
        "refine": refine,
    }
    enabled_unsupported = [
        name for name, enabled in unsupported_flags.items() if enabled
    ]
    if enabled_unsupported:
        return _invalid(
            "Standalone sweeps do not support these Body-only settings; set them to false.",
            unsupported_settings=enabled_unsupported,
        )
    if auxiliary_spine_name or binormal is not None:
        return _invalid(
            "Standalone sweeps do not support auxiliary_spine_name or binormal."
        )
    if orientation not in {"standard", "frenet"}:
        return _invalid("Standalone sweep orientation must be standard or frenet.")
    if transformation not in {"constant", "multisection"}:
        return _invalid(
            "Standalone sweep transformation must be constant or multisection."
        )
    transition_values = {
        "transformed": "Transformed",
        "right_corner": "Right corner",
        "round_corner": "Round corner",
    }
    if transition not in transition_values:
        return _invalid("Unknown sweep transition.")
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    profile_exact_name = str(profile_name or "").strip()
    spine_exact_name = str(spine_name or "").strip()
    if not profile_exact_name or not spine_exact_name:
        return _invalid("profile_name and spine_name are required.")
    if not isinstance(section_names, list):
        return _invalid("section_names must be an array.")
    extra_names = [str(name or "").strip() for name in section_names]
    if any(not name for name in extra_names):
        return _invalid("Every additional section name must be non-empty.")
    if transformation == "constant" and extra_names:
        return _invalid("constant transformation requires section_names to be empty.")
    if transformation == "multisection" and not extra_names:
        return _invalid(
            "multisection transformation requires additional section_names."
        )
    section_exact_names = [profile_exact_name, *extra_names]
    if len(set(section_exact_names)) != len(section_exact_names):
        return _invalid("The sweep section list contains a duplicate object.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    sections = [doc.getObject(name) for name in section_exact_names]
    spine = doc.getObject(spine_exact_name)
    missing = [
        name for name, section in zip(section_exact_names, sections) if section is None
    ]
    if spine is None:
        missing.append(spine_exact_name)
    if missing:
        return _invalid(
            "One or more sweep inputs were not found by exact internal name.",
            missing_objects=missing,
        )
    diagnostics = {
        section.Name: domain_runtime.shape_profile_diagnostics(section)
        for section in sections
    }
    invalid_sections = [
        name
        for name, state in diagnostics.items()
        if not state.get("planar")
        or not state.get("all_wires_valid")
        or (solid and not state.get("face_buildable"))
    ]
    if invalid_sections:
        return _invalid(
            (
                "Every standalone solid sweep section must be a valid closed planar profile."
                if solid
                else "Every standalone surface sweep section must contain valid planar wires."
            ),
            invalid_sections=invalid_sections,
            section_diagnostics=diagnostics,
        )
    spine_shape = getattr(spine, "Shape", None)
    if spine_shape is None or bool(spine_shape.isNull()) or not spine_shape.Edges:
        return _invalid("The standalone sweep path has no usable edges.")
    try:
        profile_path_distance = float(sections[0].Shape.distToShape(spine_shape)[0])
    except Exception as error:
        return _invalid(
            "FreeCAD could not validate the sweep profile/path relationship.",
            native_error=str(error),
        )
    if profile_path_distance > domain_runtime.GEOMETRY_TOLERANCE_MM:
        return _invalid(
            "The sweep profile must intersect the path.",
            profile_path_distance_mm=profile_path_distance,
        )
    source_objects = [*sections, spine]
    visibility_before = {
        obj.Name: domain_runtime.view_visibility_summary(obj) for obj in source_objects
    }

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target_sections = [active.getObject(name) for name in section_exact_names]
        target_spine = active.getObject(spine_exact_name)
        if target_spine is None or any(section is None for section in target_sections):
            raise RuntimeError("A sweep input no longer exists.")
        sweep = active.addObject("Part::Sweep", "Sweep")
        sweep.Label = clean_label
        sweep.Sections = target_sections
        sweep.Spine = target_spine
        sweep.Solid = bool(solid)
        sweep.Frenet = orientation == "frenet"
        sweep.Transition = transition_values[transition]
        domain_runtime.adopt_part_result(sweep)
        active.recompute()
        for source in [*target_sections, target_spine]:
            view = getattr(source, "ViewObject", None)
            if view is not None and hasattr(view, "Visibility"):
                view.Visibility = False
        return {
            "document": active.Name,
            "feature": sweep.Name,
            "feature_label": sweep.Label,
            "feature_type": sweep.TypeId,
            "profile": target_sections[0].Name,
            "spine": target_spine.Name,
            "sections": [section.Name for section in target_sections[1:]],
            "section_diagnostics": diagnostics,
            "profile_path_distance_mm": profile_path_distance,
            "solid_requested": bool(solid),
            "orientation": orientation,
            "transformation": transformation,
            "transition": str(sweep.Transition),
            "source_visibility_before": visibility_before,
            "source_visibility_after": {
                source.Name: domain_runtime.view_visibility_summary(source)
                for source in [*target_sections, target_spine]
            },
            "shape": domain_runtime.shape_summary(sweep),
            "feature_state": domain_runtime.feature_state_summary(sweep),
        }

    transaction = run_freecad_transaction(
        f"Create standalone sweep: {clean_label}",
        create,
    )
    return domain_runtime.part_feature_result(transaction, operation="sweep")


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
