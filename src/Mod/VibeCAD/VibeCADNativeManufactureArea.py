# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Native execution for the shipped experimental CAM Area helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureAreaState import (
    AreaGeometryTarget,
    area_state,
    area_view_state,
    geometry_target_is_current,
    is_feature_area,
    resolve_area_target,
    resolve_geometry_target,
    selected_workplane_shape,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import shape_sha256
from VibeCADNativeManufactureState import (
    persistent_configuration_sha256,
    persistent_resource_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    object_identity,
    object_reference,
    read_current_selection,
)


@dataclass(frozen=True, slots=True)
class AreaCreateSpec:
    label: Any
    sources: Any


@dataclass(frozen=True, slots=True)
class AreaViewCreateSpec:
    label: Any
    area: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AreaWorkplaneSpec:
    area: Mapping[str, Any]
    workplane: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AreaTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class AreaDocumentBoundary:
    objects: tuple[Any, ...]
    visibility: tuple[tuple[Any, bool], ...]
    selection: Mapping[str, Any]
    timeline: AreaTimelineState


@dataclass(frozen=True, slots=True)
class PreparedAreaCreate:
    label: str
    sources: tuple[AreaGeometryTarget, ...]
    source_resources_before: tuple[Mapping[str, Any], ...]
    boundary: AreaDocumentBoundary


@dataclass(frozen=True, slots=True)
class PreparedAreaViewCreate:
    label: str
    area: Any
    area_before: Mapping[str, Any]
    area_resource_before: Mapping[str, Any]
    area_configuration_before: str
    area_shape_before: str
    boundary: AreaDocumentBoundary


@dataclass(frozen=True, slots=True)
class PreparedAreaWorkplane:
    area: Any
    area_before: Mapping[str, Any]
    area_invariant_before: str
    area_sources_before: tuple[Any, ...]
    plane: AreaGeometryTarget
    plane_resource_before: Mapping[str, Any]
    workplane_shape_sha256: str
    boundary: AreaDocumentBoundary


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _clean_label(value: Any, noun: str) -> str:
    if not isinstance(value, str):
        _error(f"{noun} label must be text.", repair={"field": "label"})
    label = value.strip()
    if not label or len(label) > 160:
        _error(
            f"{noun} label must contain 1 through 160 characters.",
            repair={"field": "label"},
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in label):
        _error(
            f"{noun} label cannot contain control characters.",
            repair={"field": "label"},
        )
    return label


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> AreaTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        _error(
            "CAM Area operations require valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(timeline.Operations or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "Document History has inconsistent Area insertion state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return AreaTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _capture_boundary(document: Any) -> AreaDocumentBoundary:
    if _transaction_open(document):
        _error(
            "Finish or cancel the open task before changing a CAM Area.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for document recompute to finish before changing a CAM Area.",
            "NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    objects = tuple(document.Objects)
    return AreaDocumentBoundary(
        objects=objects,
        visibility=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in objects
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection=read_current_selection(document),
        timeline=_timeline_state(document),
    )


def _boundary_is_current(document: Any, boundary: AreaDocumentBoundary) -> bool:
    return bool(
        tuple(document.Objects) == boundary.objects
        and _timeline_state(document) == boundary.timeline
        and read_current_selection(document) == boundary.selection
        and all(
            bool(obj.ViewObject.Visibility) is visible
            for obj, visible in boundary.visibility
        )
    )


def _captured_visibility(boundary: AreaDocumentBoundary, target: Any) -> bool:
    for obj, visible in boundary.visibility:
        if obj is target:
            return visible
    raise RuntimeError("A CAM Area target has no captured presentation state")


def preflight_area_create(
    document: Any,
    spec: AreaCreateSpec,
) -> PreparedAreaCreate:
    if not isinstance(spec, AreaCreateSpec):
        raise TypeError("spec must be an AreaCreateSpec")
    boundary = _capture_boundary(document)
    if not isinstance(spec.sources, list) or not 1 <= len(spec.sources) <= 64:
        _error("Area sources must contain one through 64 exact geometry targets.")
    sources = tuple(
        resolve_geometry_target(document, request) for request in spec.sources
    )
    keys = tuple(source.key for source in sources)
    if len(keys) != len(set(keys)):
        _error("Area source selections must be distinct.")
    if (
        len(sources) == 1
        and sources[0].kind == "whole_shape"
        and is_feature_area(sources[0].model)
    ):
        _error(
            "Use create_view when the sole whole-shape source is an existing CAM Area.",
            repair={"operation": "create_view"},
        )
    return PreparedAreaCreate(
        label=_clean_label(spec.label, "Area"),
        sources=sources,
        source_resources_before=tuple(
            persistent_resource_state(source.model) for source in sources
        ),
        boundary=boundary,
    )


def preflight_area_view_create(
    document: Any,
    spec: AreaViewCreateSpec,
) -> PreparedAreaViewCreate:
    if not isinstance(spec, AreaViewCreateSpec):
        raise TypeError("spec must be an AreaViewCreateSpec")
    boundary = _capture_boundary(document)
    area, state = resolve_area_target(document, spec.area)
    return PreparedAreaViewCreate(
        label=_clean_label(spec.label, "Area view"),
        area=area,
        area_before=state,
        area_resource_before=persistent_resource_state(area),
        area_configuration_before=persistent_configuration_sha256(
            area,
            excluded_names=("Visibility",),
        ),
        area_shape_before=shape_sha256(area.Shape, "CAM Area view source"),
        boundary=boundary,
    )


_WORKPLANE_PROPERTIES = (
    "WorkPlane",
    "WorkPlaneSourceEnabled",
    "WorkPlaneSource",
    "WorkPlaneSourceCollection",
)
_WORKPLANE_MUTATED_PROPERTIES = (*_WORKPLANE_PROPERTIES, "Visibility")


def preflight_area_workplane(
    document: Any,
    spec: AreaWorkplaneSpec,
) -> PreparedAreaWorkplane:
    if not isinstance(spec, AreaWorkplaneSpec):
        raise TypeError("spec must be an AreaWorkplaneSpec")
    boundary = _capture_boundary(document)
    area, state = resolve_area_target(document, spec.area)
    plane = resolve_geometry_target(document, spec.workplane, workplane=True)
    if plane.model is area:
        _error(
            "An Area cannot use itself as its workplane source.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    selected = selected_workplane_shape(plane)
    if selected is None or selected.isNull():
        _error(
            "The exact Area workplane geometry is empty.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return PreparedAreaWorkplane(
        area=area,
        area_before=state,
        area_invariant_before=persistent_configuration_sha256(
            area,
            excluded_names=_WORKPLANE_MUTATED_PROPERTIES,
        ),
        area_sources_before=tuple(area.Sources or ()),
        plane=plane,
        plane_resource_before=persistent_resource_state(plane.model),
        workplane_shape_sha256=shape_sha256(selected, "Area workplane"),
        boundary=boundary,
    )


def _sources_are_current(prepared: PreparedAreaCreate) -> bool:
    return all(
        geometry_target_is_current(source)
        and persistent_resource_state(source.model) == resource
        for source, resource in zip(
            prepared.sources,
            prepared.source_resources_before,
            strict=True,
        )
    )


def _area_is_current(
    document: Any,
    area: Any,
    state: Mapping[str, Any],
    resource: Mapping[str, Any],
) -> bool:
    try:
        return bool(
            document.getObject(str(area.Name)) is area
            and area_state(area) == state
            and persistent_resource_state(area) == resource
        )
    except Exception:
        return False


def _publish_operation_block(
    document: Any,
    operation: Any,
    resources: tuple[Any, ...],
    replaced_inputs: tuple[Any, ...] = (),
) -> None:
    import Path.Base.Util as PathTimeline

    PathTimeline.markTimelineOperation(operation)
    for resource in resources:
        PathTimeline.markTimelineResource(resource, operation)
    if replaced_inputs:
        PathTimeline.markTimelineReplacedInputs(operation, replaced_inputs)
    enrolled = (operation, *resources)
    if any(
        not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(obj)
        for obj in enrolled
    ):
        raise RuntimeError("A CAM Area History object was not provisionally enrolled")
    document.publishProvisionalTimelineOperationBlock(
        operation,
        resources,
        tuple(operation for _resource in resources),
    )


def create_area(
    document: Any,
    *,
    prepared: PreparedAreaCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedAreaCreate):
        raise TypeError("prepared must be a PreparedAreaCreate")
    if not _boundary_is_current(
        document, prepared.boundary
    ) or not _sources_are_current(prepared):
        _error(
            "The exact Area sources, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    import PathCommands

    resources = []
    linked_sources = []
    for source in prepared.sources:
        if source.subelement is None:
            linked_sources.append(source.model)
            continue
        resource = PathCommands.createSubshapeResource(
            document,
            source.model,
            source.subelement,
            "Wires" if source.element_type == "Edge" else None,
            f"{source.model.Name}_{source.subelement}",
        )
        resources.append(resource)
        linked_sources.append(resource)
    area = document.addObject("Path::FeatureArea", "FeatureArea")
    if area is None:
        raise RuntimeError("The CAM Area factory returned no object")
    area.Label = prepared.label
    area.Sources = linked_sources
    for resource in resources:
        resource.ViewObject.Visibility = False
    _publish_operation_block(document, area, tuple(resources))
    created = tuple(object_identity(obj) for obj in (*resources, area))
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "area": area,
            "resources": tuple(resources),
            "linked_sources": tuple(linked_sources),
        },
        recompute_targets=(*resources, area),
        created=created,
    )


def create_area_view(
    document: Any,
    *,
    prepared: PreparedAreaViewCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedAreaViewCreate):
        raise TypeError("prepared must be a PreparedAreaViewCreate")
    if not _boundary_is_current(prepared.area.Document, prepared.boundary) or not (
        _area_is_current(
            document,
            prepared.area,
            prepared.area_before,
            prepared.area_resource_before,
        )
    ):
        _error(
            "The exact Area source, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    view = document.addObject("Path::FeatureAreaView", "FeatureAreaView")
    if view is None:
        raise RuntimeError("The CAM Area view factory returned no object")
    view.Label = prepared.label
    view.Source = prepared.area
    source_visibility = _captured_visibility(prepared.boundary, prepared.area)
    prepared.area.ViewObject.Visibility = source_visibility
    _publish_operation_block(document, view, (), (prepared.area,))
    prepared.area.ViewObject.Visibility = False
    return NativeMutationDraft(
        value={"prepared": prepared, "view": view},
        recompute_targets=(view,),
        created=(object_identity(view),),
        replaced=(object_identity(prepared.area),),
    )


def set_area_workplane(
    document: Any,
    *,
    prepared: PreparedAreaWorkplane,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedAreaWorkplane):
        raise TypeError("prepared must be a PreparedAreaWorkplane")
    if (
        not _boundary_is_current(document, prepared.boundary)
        or area_state(prepared.area) != prepared.area_before
        or not geometry_target_is_current(prepared.plane)
        or persistent_resource_state(prepared.plane.model)
        != prepared.plane_resource_before
    ):
        _error(
            "The exact Area, workplane, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    selected = selected_workplane_shape(prepared.plane)
    if (
        selected is None
        or selected.isNull()
        or shape_sha256(selected, "Area workplane") != prepared.workplane_shape_sha256
    ):
        _error(
            "The exact Area workplane geometry changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    subelements = (
        [prepared.plane.subelement] if prepared.plane.subelement is not None else []
    )
    prepared.area.WorkPlaneSourceEnabled = True
    prepared.area.WorkPlaneSource = (prepared.plane.model, subelements)
    prepared.area.WorkPlaneSourceCollection = (
        "Wires" if prepared.plane.element_type == "Edge" else ""
    )
    prepared.area.WorkPlane = selected
    prepared.area.ViewObject.Visibility = True
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.area,),
        changed=(object_identity(prepared.area),),
    )


def _verify_timeline_insert(
    document: Any,
    boundary: AreaDocumentBoundary,
    operation: Any,
    resources: tuple[Any, ...],
    replaced_inputs: tuple[Any, ...] = (),
) -> int:
    before = boundary.timeline
    after = _timeline_state(document)
    inserted = (*resources, operation)
    expected = (
        *before.operations[: before.position],
        *inserted,
        *before.operations[before.position :],
    )
    if (
        after.timeline is not before.timeline
        or after.operations != expected
        or after.position != before.position + len(inserted)
    ):
        _error(
            "The CAM Area operation was not inserted at the exact History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index in range(len(before.operations)):
        new_index = (
            old_index if old_index < before.position else old_index + len(inserted)
        )
        expected_visibility = (
            False
            if before.operations[old_index] in replaced_inputs
            else before.visibility[old_index]
        )
        if (
            after.visibility[new_index] is not expected_visibility
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            _error(
                "CAM Area creation changed existing History state.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )
    if any(after.suppression[before.position : before.position + len(inserted)]):
        _error(
            "A created CAM Area History object was unexpectedly suppressed.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return after.position


def _verify_existing_presentation(
    document: Any,
    boundary: AreaDocumentBoundary,
    *,
    visibility_overrides: tuple[tuple[Any, bool], ...] = (),
) -> None:
    if read_current_selection(document) != boundary.selection:
        _error(
            "The CAM Area operation changed human selection.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    overrides = {id(obj): visible for obj, visible in visibility_overrides}
    for obj, visible in boundary.visibility:
        expected = overrides.get(id(obj), visible)
        if bool(obj.ViewObject.Visibility) is not expected:
            _error(
                "The CAM Area operation changed unrelated visibility.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )


def _timeline_matches_visibility_overrides(
    document: Any,
    boundary: AreaDocumentBoundary,
    overrides: tuple[tuple[Any, bool], ...],
) -> bool:
    before = boundary.timeline
    after = _timeline_state(document)
    expected = {id(obj): visible for obj, visible in overrides}
    return bool(
        after.timeline is before.timeline
        and after.operations == before.operations
        and after.visibility
        == tuple(
            expected.get(id(operation), before.visibility[index])
            for index, operation in enumerate(before.operations)
        )
        and after.suppression == before.suppression
        and after.position == before.position
    )


def verify_created_area(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value
    prepared: PreparedAreaCreate = value["prepared"]
    area = value["area"]
    resources: tuple[Any, ...] = value["resources"]
    linked_sources: tuple[Any, ...] = value["linked_sources"]
    if tuple(document.Objects) != (*prepared.boundary.objects, *resources, area):
        _error(
            "CAM Area creation changed objects outside its exact output block.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    failures = []
    if document.getObject(str(area.Name)) is not area:
        failures.append("identity")
    if str(area.TypeId) != "Path::FeatureArea":
        failures.append("type")
    if str(area.Label) != prepared.label:
        failures.append("label")
    if tuple(area.Sources or ()) != linked_sources:
        failures.append("sources")
    if not bool(area.isValid()) or area.Shape.isNull():
        failures.append("validity")
    if str(getattr(area, "VibeCADTimelineRole", "") or "") != "operation":
        failures.append("history_role")
    for resource, source in zip(
        resources,
        (item for item in prepared.sources if item.subelement is not None),
        strict=True,
    ):
        try:
            linked_model, subelements = resource.Source
        except Exception:
            failures.append(f"resource:{resource.Name}:source")
            continue
        expected_collection = "Wires" if source.element_type == "Edge" else ""
        if (
            str(resource.TypeId) != "Part::FeaturePython"
            or linked_model is not source.model
            or tuple(subelements or ()) != (source.subelement,)
            or str(resource.SubshapeCollection or "") != expected_collection
            or bool(resource.ViewObject.Visibility)
            or str(getattr(resource, "VibeCADTimelineRole", "") or "") != "resource"
            or getattr(resource, "VibeCADTimelineOwner", None) is not area
            or not bool(resource.isValid())
            or resource.Shape.isNull()
        ):
            failures.append(f"resource:{resource.Name}")
    if failures:
        _error(
            "The created CAM Area failed exact checks: " + ", ".join(failures) + ".",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={"failed_invariants": failures},
        )
    position = _verify_timeline_insert(
        document,
        prepared.boundary,
        area,
        resources,
    )
    _verify_existing_presentation(document, prepared.boundary)
    result = area_state(area)
    result.update(
        operation="create",
        history_position=position,
        resource_count=len(resources),
        source_selections=[
            {
                "object_name": source.model.Name,
                "subelement": source.subelement,
            }
            for source in prepared.sources
        ],
    )
    return result


def verify_created_area_view(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value
    prepared: PreparedAreaViewCreate = value["prepared"]
    view = value["view"]
    if tuple(document.Objects) != (*prepared.boundary.objects, view):
        _error(
            "CAM Area view creation changed objects outside its exact output.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    failures = tuple(
        name
        for name, valid in (
            ("identity", document.getObject(str(view.Name)) is view),
            ("type", str(view.TypeId) == "Path::FeatureAreaView"),
            ("label", str(view.Label) == prepared.label),
            ("source", view.Source is prepared.area),
            ("validity", bool(view.isValid())),
            ("shape", not view.Shape.isNull()),
            (
                "history_role",
                str(getattr(view, "VibeCADTimelineRole", "") or "")
                == "operation",
            ),
            (
                "replaced_inputs",
                tuple(getattr(view, "VibeCADTimelineReplacedInputs", ()) or ())
                == (prepared.area,),
            ),
            (
                "source_configuration",
                persistent_configuration_sha256(
                    prepared.area,
                    excluded_names=("Visibility",),
                )
                == prepared.area_configuration_before,
            ),
            (
                "source_shape",
                shape_sha256(prepared.area.Shape, "CAM Area view source")
                == prepared.area_shape_before,
            ),
        )
        if not valid
    )
    if failures:
        _error(
            "The created CAM Area view failed exact checks: "
            + ", ".join(failures)
            + ".",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={"failed_invariants": list(failures)},
        )
    position = _verify_timeline_insert(
        document,
        prepared.boundary,
        view,
        (),
        (prepared.area,),
    )
    _verify_existing_presentation(
        document,
        prepared.boundary,
        visibility_overrides=((prepared.area, False),),
    )
    result = area_view_state(view)
    result.update(operation="create_view", history_position=position)
    return result


def verify_area_workplane(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedAreaWorkplane = draft.value["prepared"]
    area = prepared.area
    if tuple(document.Objects) != prepared.boundary.objects:
        _error(
            "Area workplane assignment changed the document graph.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    try:
        source, subelements = area.WorkPlaneSource
    except Exception as exc:
        raise NativeManufactureError(
            "The Area workplane link could not be read.",
            error_code="NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        ) from exc
    expected_subelements = (
        (prepared.plane.subelement,) if prepared.plane.subelement is not None else ()
    )
    expected_collection = "Wires" if prepared.plane.element_type == "Edge" else ""
    failures = tuple(
        name
        for name, valid in (
            ("validity", bool(area.isValid()) and not area.Shape.isNull()),
            ("source_enabled", bool(area.WorkPlaneSourceEnabled)),
            ("source", source is prepared.plane.model),
            ("subelement", tuple(subelements or ()) == expected_subelements),
            (
                "collection",
                str(area.WorkPlaneSourceCollection or "") == expected_collection,
            ),
            ("workplane", not area.WorkPlane.isNull()),
            (
                "workplane_identity",
                shape_sha256(area.WorkPlane, "assigned Area workplane")
                == prepared.workplane_shape_sha256,
            ),
            ("area_sources", tuple(area.Sources or ()) == prepared.area_sources_before),
            (
                "area_invariant",
                persistent_configuration_sha256(
                    area,
                    excluded_names=_WORKPLANE_MUTATED_PROPERTIES,
                )
                == prepared.area_invariant_before,
            ),
            (
                "plane_invariant",
                persistent_resource_state(prepared.plane.model)
                == prepared.plane_resource_before,
            ),
            (
                "history",
                _timeline_matches_visibility_overrides(
                    document,
                    prepared.boundary,
                    ((area, True),),
                ),
            ),
        )
        if not valid
    )
    if failures:
        _error(
            "The Area workplane failed exact checks: " + ", ".join(failures) + ".",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={"failed_invariants": list(failures)},
        )
    _verify_existing_presentation(
        document,
        prepared.boundary,
        visibility_overrides=((area, True),),
    )
    result = area_state(area)
    result.update(
        operation="set_workplane",
        workplane_source={
            **object_reference(prepared.plane.model),
            "subelement": prepared.plane.subelement,
            "collection": expected_collection,
        },
    )
    return result
