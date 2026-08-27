# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact coordinated orthographic projection groups for Native Drawing."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionWorker import PreparedDrawingProjectionLayout
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeDrawingView import (
    PreparedStandardView,
    capture_standard_view_commit_state,
    prepare_standard_view_create,
    standard_view_line_flags,
)
from VibeCADNativeDrawingViewSchema import DRAWING_PROJECTION_GROUP_VIEWS
from VibeCADNativeDrawingViewState import (
    DRAWING_VIEW_ORIENTATIONS,
    drawing_source_state,
    drawing_view_state,
    is_part_drawing_view,
)
from VibeCADNativeLabel import matches_preferred_document_label
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


_NATIVE_PROJECTION_NAMES = {
    "front": "Front",
    "top": "Top",
    "right": "Right",
    "left": "Left",
    "bottom": "Bottom",
    "rear": "Rear",
}


@dataclass(frozen=True, slots=True)
class ProjectionGroupSpec:
    views: tuple[str, ...]
    convention: str


@dataclass(frozen=True, slots=True)
class PreparedProjectionGroup:
    standard: PreparedStandardView
    group: ProjectionGroupSpec


def _vector(values: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(length) or length <= 1.0e-12:
        raise ValueError("A projection direction must be a nonzero finite vector")
    return tuple(
        0.0 if abs(value / length) < 1.0e-15 else value / length
        for value in values
    )


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return _vector(
        (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        )
    )


def _negate(
    value: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(-item if item else 0.0 for item in value)


def projection_group_directions(
    front_orientation: str,
) -> dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Return TechDraw's six orthographic directions for one chosen front."""

    if front_orientation not in DRAWING_VIEW_ORIENTATIONS:
        raise ValueError("front_orientation must be one published Drawing orientation")
    front, x_axis = DRAWING_VIEW_ORIENTATIONS[front_orientation]
    front = _vector(tuple(float(value) for value in front))
    x_axis = _vector(tuple(float(value) for value in x_axis))
    y_axis = _cross(front, x_axis)
    return {
        "front": (front, x_axis),
        "top": (y_axis, x_axis),
        "right": (x_axis, _negate(front)),
        "left": (_negate(x_axis), front),
        "bottom": (_negate(y_axis), x_axis),
        "rear": (_negate(front), _negate(x_axis)),
    }


def prepare_projection_group_create(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedProjectionGroup:
    views_value = values.get("views")
    if not isinstance(views_value, list):
        raise NativeDrawingError(
            "Projection group views must be an ordered list.",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_PARAMETERS_INVALID",
        )
    views = tuple(str(value or "") for value in views_value)
    if (
        not 2 <= len(views) <= len(DRAWING_PROJECTION_GROUP_VIEWS)
        or len(views) != len(set(views))
        or "front" not in views
        or any(value not in DRAWING_PROJECTION_GROUP_VIEWS for value in views)
    ):
        raise NativeDrawingError(
            "Projection group views require front and one or more unique orthographic views.",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_PARAMETERS_INVALID",
        )
    convention = str(values.get("convention") or "")
    if convention not in {"first_angle", "third_angle"}:
        raise NativeDrawingError(
            "Projection group convention must be first_angle or third_angle.",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_PARAMETERS_INVALID",
        )
    standard = prepare_standard_view_create(
        document,
        values={
            "label": values["label"],
            "page": values["page"],
            "sources": values["sources"],
            "orientation": values["front_orientation"],
            "position": {"x_mm": 0.0, "y_mm": 0.0},
            "scale": 1.0,
            "line_style": values["line_style"],
        },
        validate_position=False,
    )
    return PreparedProjectionGroup(
        standard=standard,
        group=ProjectionGroupSpec(
            views=views,
            convention=convention,
        ),
    )


def capture_projection_group_commit_state(
    document: Any,
    prepared: PreparedProjectionGroup,
) -> PreparedProjectionGroup:
    if not isinstance(prepared, PreparedProjectionGroup):
        raise TypeError("prepared must be a PreparedProjectionGroup")
    return replace(
        prepared,
        standard=capture_standard_view_commit_state(document, prepared.standard),
    )


def validate_prepared_projection_group(
    document: Any,
    prepared: PreparedProjectionGroup,
) -> None:
    if not isinstance(prepared, PreparedProjectionGroup):
        raise TypeError("prepared must be a PreparedProjectionGroup")
    capture_standard_view_commit_state(document, prepared.standard)


def projection_group_jobs(
    prepared: PreparedProjectionGroup,
) -> tuple[dict[str, Any], ...]:
    """Return the exact ordered detached projections required by the group."""

    directions = projection_group_directions(prepared.standard.spec.orientation)
    sources = tuple(
        {
            "object_name": str(source.Name),
            "state_sha256": str(state["state_sha256"]),
            "source": source,
        }
        for source, state in zip(
            prepared.standard.sources,
            prepared.standard.source_states,
            strict=True,
        )
    )
    ordered = ("front", *(view for view in prepared.group.views if view != "front"))
    return tuple(
        {
            "key": f"projection_group:{view}",
            "sources": sources,
            "direction": directions[view][0],
            "x_direction": directions[view][1],
            "scale": 1.0,
            "line_flags": standard_view_line_flags(
                prepared.standard.spec.line_style
            ),
        }
        for view in ordered
    )


def _apply_line_style(view: Any, style: str) -> None:
    for name, value in standard_view_line_flags(style).items():
        setattr(view, name, value)


def _state_vector(value: Any) -> list[float]:
    return [
        round(float(getattr(value, name)), 12)
        for name in ("x", "y", "z")
    ]


def projection_group_summary(group: Any) -> dict[str, Any]:
    """Return the compact durable semantics of one TechDraw projection group."""

    if str(getattr(group, "TypeId", "") or "") != "TechDraw::DrawProjGroup":
        raise TypeError("group must be a TechDraw projection group")
    convention = {
        "First angle": "first_angle",
        "Third angle": "third_angle",
    }.get(str(getattr(group, "ProjectionType", "") or ""))
    if convention is None:
        raise ValueError("The projection group has no supported projection convention")
    children = tuple(getattr(group, "Views", ()) or ())
    front = next(
        (
            child
            for child in children
            if str(getattr(child, "Type", "") or "").casefold() == "front"
        ),
        None,
    )
    if front is None:
        raise ValueError("The projection group has no front view")
    group_x = float(group.X)
    group_y = float(group.Y)
    group_name = str(getattr(group, "Name", "") or "")
    if not group_name:
        raise ValueError("The projection group has no object name")
    views = []
    names = set()
    for child in children:
        orientation = str(getattr(child, "Type", "") or "").casefold()
        name = str(getattr(child, "Name", "") or "")
        if orientation not in _NATIVE_PROJECTION_NAMES or not name or name in names:
            raise ValueError("The projection group contains an invalid projected view")
        names.add(name)
        views.append(
            {
                "orientation": orientation,
                "view_name": name,
                "view_target": {"object_name": name},
                "placement_parent": {"object_name": group_name},
                "placement_target": {"object_name": group_name},
                "position_on_page_mm": {
                    "x_mm": round(group_x + float(child.X), 9),
                    "y_mm": round(group_y + float(child.Y), 9),
                },
            }
        )
    return {
        "convention": convention,
        "scale": round(float(group.Scale), 12),
        "front_direction": _state_vector(front.Direction),
        "front_x_direction": _state_vector(front.XDirection),
        "views": views,
    }


def create_projection_group(
    document: Any,
    *,
    prepared: PreparedProjectionGroup,
    projection_snapshots: Mapping[str, Mapping[str, Any]],
    layout: PreparedDrawingProjectionLayout,
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedProjectionGroup):
        raise TypeError("prepared must be a PreparedProjectionGroup")
    if not isinstance(layout, PreparedDrawingProjectionLayout):
        raise TypeError("layout must be a PreparedDrawingProjectionLayout")
    standard = prepared.standard
    spec = standard.spec
    group_spec = prepared.group
    expected_keys = {f"projection_group:{view}" for view in group_spec.views}
    if set(projection_snapshots) != expected_keys:
        raise NativeDrawingError(
            "The detached projection set does not match the requested group.",
            error_code="NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    group = document.addObject("TechDraw::DrawProjGroup", "ProjectionGroup")
    group.Label = spec.label
    group.Source = list(standard.sources)
    group.ProjectionType = (
        "First angle" if group_spec.convention == "first_angle" else "Third angle"
    )
    standard.page.ProjectionType = group.ProjectionType
    group.ScaleType = "Custom"
    group.Scale = layout.scale
    group.spacingX = layout.spacing_x_mm
    group.spacingY = layout.spacing_y_mm
    group.AutoDistribute = False
    front_position = layout.position("front")
    group.X = front_position[0]
    group.Y = front_position[1]
    if int(standard.page.addPrecomputedView(group)) < 1:
        raise NativeDrawingError(
            "The projection group could not join its exact page.",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_CREATE_FAILED",
        )

    directions = projection_group_directions(spec.orientation)
    ordered = ("front", *(view for view in group_spec.views if view != "front"))
    children = []
    for view_name in ordered:
        child = group.addPrecomputedProjection(_NATIVE_PROJECTION_NAMES[view_name])
        if child is None or not is_part_drawing_view(child):
            raise NativeDrawingError(
                f"TechDraw could not create the {view_name} projection.",
                error_code="NATIVE_DRAWING_PROJECTION_GROUP_CREATE_FAILED",
            )
        if view_name == "front":
            child.Direction = App.Vector(*directions[view_name][0])
            child.XDirection = App.Vector(*directions[view_name][1])
        position = layout.position(view_name)
        child.X = position[0] - front_position[0]
        child.Y = position[1] - front_position[1]
        _apply_line_style(child, spec.line_style)
        children.append(child)

    # Timeline publication adds ownership metadata to every child.  Publish
    # before adopting the detached projections so the final purge below also
    # covers that metadata and no redundant native HLR cycle can start.
    document.publishProvisionalTimelineOperationBlock(group, tuple(children), ())
    for view_name, child in zip(ordered, children, strict=True):
        setter = getattr(child, "setPrecomputedProjection", None)
        if not callable(setter):
            raise NativeDrawingError(
                "The installed TechDraw runtime cannot adopt a projection group.",
                error_code="NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
            )
        setter(dict(projection_snapshots[f"projection_group:{view_name}"]))
        position = layout.position(view_name)
        child.X = position[0] - front_position[0]
        child.Y = position[1] - front_position[1]
        child.purgeTouched()
    group.purgeTouched()

    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "group": group,
            "children": tuple(children),
            "layout": layout,
        },
        recompute_targets=(standard.page,),
        created=tuple(object_identity(value) for value in (group, *children)),
        changed=(object_identity(standard.page),),
    )


def _identity(obj: Any) -> tuple[int, str, str]:
    return (
        int(getattr(obj, "ID", -1)),
        str(getattr(obj, "Name", "") or ""),
        str(getattr(obj, "TypeId", "") or ""),
    )


def _identities(objects: tuple[Any, ...]) -> tuple[tuple[int, str, str], ...]:
    return tuple(_identity(obj) for obj in objects)


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _current_selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _same_vector(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(
        math.isclose(float(getattr(actual, name)), value, abs_tol=1.0e-10)
        for name, value in zip(("x", "y", "z"), expected, strict=True)
    )


def verify_projection_group_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedProjectionGroup = draft.value["prepared"]
    group = draft.value["group"]
    children = tuple(draft.value["children"])
    layout: PreparedDrawingProjectionLayout = draft.value["layout"]
    standard = prepared.standard
    spec = standard.spec
    group_spec = prepared.group
    object_ids_before = set(_identities(standard.objects_before))
    new_objects = tuple(
        obj for obj in document.Objects if _identity(obj) not in object_ids_before
    )
    ordered_views = ("front", *(view for view in group_spec.views if view != "front"))
    expected_types = tuple(_NATIVE_PROJECTION_NAMES[value] for value in ordered_views)
    directions = projection_group_directions(spec.orientation)
    source_states = tuple(
        drawing_source_state(source) for source in tuple(getattr(group, "Source", ()) or ())
    )
    front_position = layout.position("front")
    page_views = tuple(getattr(standard.page, "Views", ()) or ())
    timeline_owner = tuple(getattr(child, "VibeCADTimelineOwner", None) for child in children)
    checks = {
        "created objects": _identities(new_objects) == _identities((group, *children)),
        "page type": is_drawing_page(standard.page),
        "group type": str(getattr(group, "TypeId", "")) == "TechDraw::DrawProjGroup",
        "label": matches_preferred_document_label(str(group.Label), spec.label),
        "sources": _identities(tuple(getattr(group, "Source", ()) or ()))
        == _identities(standard.sources),
        "source states": tuple(value["state_sha256"] for value in source_states)
        == tuple(value["state_sha256"] for value in standard.source_states),
        "page membership": _identities(page_views)
        == _identities((*standard.page_views_before, group)),
        "group membership": _identities(tuple(getattr(group, "Views", ()) or ()))
        == _identities(children),
        "view types": tuple(str(getattr(child, "Type", "")) for child in children)
        == expected_types,
        "projection convention": str(group.ProjectionType)
        == ("First angle" if group_spec.convention == "first_angle" else "Third angle"),
        "page projection convention": str(standard.page.ProjectionType)
        == ("First angle" if group_spec.convention == "first_angle" else "Third angle"),
        "scale type": str(group.ScaleType) == "Custom",
        "scale": math.isclose(float(group.Scale), layout.scale, abs_tol=1.0e-12),
        "x position": math.isclose(float(group.X), front_position[0], abs_tol=1.0e-9),
        "y position": math.isclose(float(group.Y), front_position[1], abs_tol=1.0e-9),
        "horizontal spacing": math.isclose(
            float(group.spacingX),
            layout.spacing_x_mm,
            abs_tol=1.0e-9,
        ),
        "vertical spacing": math.isclose(
            float(group.spacingY),
            layout.spacing_y_mm,
            abs_tol=1.0e-9,
        ),
        "settled distribution": not bool(group.AutoDistribute),
        "timeline role": str(getattr(group, "VibeCADTimelineRole", "") or "")
        == "operation",
        "timeline ownership": all(owner is group for owner in timeline_owner),
        "timeline order": _identities(_timeline_operations(document))
        == _identities((*standard.timeline_before, *children, group)),
        "validity": bool(group.isValid()),
    }
    failed_checks = tuple(name for name, passed in checks.items() if not passed)
    if failed_checks:
        raise NativeDrawingError(
            "The projection group failed exact postconditions: "
            + ", ".join(failed_checks)
            + ".",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_POSTCONDITION_FAILED",
        )
    child_states = []
    for view_name, child in zip(ordered_views, children, strict=True):
        direction, x_direction = directions[view_name]
        position = layout.position(view_name)
        expected_child_x = position[0] - front_position[0]
        expected_child_y = position[1] - front_position[1]
        if (
            not bool(child.isValid())
            or not _same_vector(child.Direction, direction)
            or not _same_vector(child.XDirection, x_direction)
            or not math.isclose(float(child.X), expected_child_x, abs_tol=1.0e-9)
            or not math.isclose(float(child.Y), expected_child_y, abs_tol=1.0e-9)
            or not math.isclose(float(child.getScale()), layout.scale, abs_tol=1.0e-12)
        ):
            raise NativeDrawingError(
                f"The {view_name} projection did not retain its exact orientation.",
                error_code="NATIVE_DRAWING_PROJECTION_GROUP_POSTCONDITION_FAILED",
            )
        state = drawing_view_state(child)
        if (state["visible_edge_count"] or 0) < 1:
            raise NativeDrawingError(
                f"The {view_name} projection produced no visible geometry.",
                error_code="NATIVE_DRAWING_VIEW_PROJECTION_FAILED",
            )
        child_states.append(
            {
                "orientation": view_name,
                "placement_parent": {"object_name": str(group.Name)},
                "placement_target": {"object_name": str(group.Name)},
                "view": {
                    "object_name": state["object_name"],
                    "state_sha256": state["state_sha256"],
                },
                "position_on_page_mm": {
                    "x_mm": position[0],
                    "y_mm": position[1],
                },
            }
        )
    if _current_selection(document) != standard.selection_before:
        raise NativeDrawingError(
            "Projection group creation changed the human selection.",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_POSTCONDITION_FAILED",
        )
    actual_visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in standard.visibility_before
    )
    if actual_visibility != standard.visibility_before:
        raise NativeDrawingError(
            "Projection group creation changed existing object visibility.",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_POSTCONDITION_FAILED",
        )
    page_state = drawing_page_state(standard.page)
    if page_state["view_count"] != standard.page_state_before["view_count"] + 1:
        raise NativeDrawingError(
            "The Drawing page did not retain the projection group.",
            error_code="NATIVE_DRAWING_PROJECTION_GROUP_POSTCONDITION_FAILED",
        )
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "projection_group": {
            "object_name": str(group.Name),
            "label": str(group.Label),
            "convention": group_spec.convention,
            "scale": layout.scale,
            "placement_target": {"object_name": str(group.Name)},
            "views": child_states,
        },
    }


__all__ = [
    "PreparedProjectionGroup",
    "capture_projection_group_commit_state",
    "create_projection_group",
    "prepare_projection_group_create",
    "projection_group_directions",
    "projection_group_jobs",
    "validate_prepared_projection_group",
    "verify_projection_group_create",
]
