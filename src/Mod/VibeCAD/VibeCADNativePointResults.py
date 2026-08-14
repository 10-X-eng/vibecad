# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic History publication and proof for Native point-cloud results."""

from __future__ import annotations

from typing import Any

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePointPlan import ProcessedPointOutput, ProcessedPointPlan
from VibeCADNativePointTargets import point_target_still_exact
from VibeCADNativeTargets import object_identity, object_reference


_PUBLICATION = {
    "convert_to_points": (
        "PointsFromGeometry",
        "Points From Geometry",
        "Convert geometry to points",
        True,
    ),
    "structure": (
        "StructuredPoints",
        "Structured Points",
        "Structure point cloud",
        True,
    ),
    "merge": ("MergedPoints", "Merged Points", "Merge point clouds", True),
    "polygon_cut": ("PointCutResults", "Point Cut Results", "Cut point cloud", False),
}


def _set_attribute(obj: Any, property_type: str, name: str, values: tuple[Any, ...]) -> None:
    if not values:
        return
    obj.addProperty(property_type, name, "Point Cloud", locked=True)
    setattr(obj, name, list(values))


def _add_sources(obj: Any, sources: list[Any]) -> None:
    if len(sources) == 1:
        obj.addProperty(
            "App::PropertyLink",
            "Source",
            "Operation",
            "Exact source used to create this point-cloud result",
            locked=True,
        )
        obj.Source = sources[0]
        return
    obj.addProperty(
        "App::PropertyLinkList",
        "Sources",
        "Operation",
        "Exact sources combined by this point-cloud result",
        locked=True,
    )
    obj.Sources = sources


def _create_output(document: Any, output: ProcessedPointOutput, sources: list[Any]) -> Any:
    type_id = "Points::Structured" if output.width > 0 else "Points::Feature"
    obj = document.addObject(type_id, document.getUniqueObjectName("PointCloudResult"))
    if obj is None or str(getattr(obj, "TypeId", "")) != type_id:
        raise NativeMeshError("A point-cloud result object could not be created.")
    obj.Label = output.label
    obj.Points = output.points
    obj.Placement = output.placement
    if output.width > 0:
        obj.Width = output.width
        obj.Height = output.height
    _set_attribute(obj, "Points::PropertyGreyValueList", "Intensity", output.intensities)
    _set_attribute(obj, "App::PropertyColorList", "Color", output.colors)
    _set_attribute(obj, "Points::PropertyNormalList", "Normal", output.normals)
    _add_sources(obj, sources)
    return obj


def create_point_results(document: Any, processed: ProcessedPointPlan) -> NativeMutationDraft:
    if not isinstance(processed, ProcessedPointPlan):
        raise TypeError("processed must be a ProcessedPointPlan")
    prepared = processed.prepared
    if prepared.point_targets and any(
        not point_target_still_exact(document, target) for target in prepared.point_targets
    ):
        raise NativeMeshError(
            "An exact point cloud changed after detached processing.",
            error_code="NATIVE_POINT_CLOUD_STATE_STALE",
        )
    for target in prepared.geometry_targets:
        current = mesh_object_state(target.source)
        if (
            not is_live(document, target.source)
            or current.get("state_sha256") != target.expected_state_sha256
        ):
            raise NativeMeshError(
                "An exact geometry source changed after detached point sampling.",
                error_code="NATIVE_POINT_CLOUD_STATE_STALE",
            )

    if prepared.operation == "convert_to_points":
        sources = [target.source for target in prepared.geometry_targets]
        paired_sources = [[target.source] for target in prepared.geometry_targets]
    elif prepared.operation == "merge":
        sources = [target.source for target in prepared.point_targets]
        paired_sources = [sources]
    else:
        sources = [prepared.point_targets[0].source]
        paired_sources = [sources for _output in processed.outputs]
    if len(paired_sources) != len(processed.outputs):
        raise NativeMeshError("Point-cloud processing lost its exact output/source pairing.")

    outputs = [
        _create_output(document, output, output_sources)
        for output, output_sources in zip(processed.outputs, paired_sources)
    ]
    import MeshGui

    object_name, label, operation_kind, source_preserving = _PUBLICATION[
        prepared.operation
    ]
    if source_preserving:
        group = MeshGui.publishSourcePreservingOutputs(
            str(document.Name),
            sources,
            outputs,
            object_name,
            label,
            operation_kind,
        )
    else:
        paired_replacement_sources = [sources[0] for _output in outputs]
        group = MeshGui.publishReplacingOutputs(
            str(document.Name),
            paired_replacement_sources,
            outputs,
            object_name,
            label,
            operation_kind,
        )
    created = [*outputs, *([group] if group is not None else [])]
    return NativeMutationDraft(
        value={"processed": processed, "outputs": tuple(outputs), "group": group},
        recompute_targets=tuple(created),
        created=tuple(object_identity(obj) for obj in created),
        replaced=(
            (object_identity(prepared.point_targets[0].source),)
            if prepared.operation == "polygon_cut"
            else ()
        ),
    )


def _history_exact(
    document: Any,
    processed: ProcessedPointPlan,
    outputs: tuple[Any, ...],
    group: Any,
) -> bool:
    prepared = processed.prepared
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return False
    if prepared.operation == "convert_to_points":
        sources = tuple(target.source for target in prepared.geometry_targets)
    else:
        sources = tuple(target.source for target in prepared.point_targets)
    source_preserving = _PUBLICATION[prepared.operation][3]
    replaced = ()
    if not source_preserving:
        replaced = tuple(
            target.source for target in prepared.point_targets if target.source_visible
        )
    if group is None:
        return (
            len(outputs) == 1
            and is_live(document, outputs[0])
            and list(getattr(timeline, "Operations", ()) or ()).count(outputs[0]) == 1
            and str(getattr(outputs[0], "VibeCADTimelineRole", "") or "")
            == "operation"
            and getattr(outputs[0], "VibeCADTimelineOwner", None) is None
            and tuple(getattr(outputs[0], "VibeCADTimelineReplacedInputs", ()) or ())
            == replaced
        )
    return (
        is_live(document, group)
        and list(getattr(timeline, "Operations", ()) or ()).count(group) == 1
        and str(getattr(group, "VibeCADTimelineRole", "") or "") == "operation"
        and getattr(group, "VibeCADTimelineOwner", None) is None
        and tuple(getattr(group, "Sources", ()) or ()) == sources
        and tuple(getattr(group, "Group", ()) or ()) == outputs
        and str(getattr(group, "InputMode", "") or "")
        == ("Source preserving" if source_preserving else "Replacement")
        and str(getattr(group, "OperationKind", "") or "")
        == _PUBLICATION[prepared.operation][2]
        and tuple(getattr(group, "VibeCADTimelineReplacedInputs", ()) or ()) == replaced
        and all(
            str(getattr(output, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(output, "VibeCADTimelineOwner", None) is group
            for output in outputs
        )
    )


def _attribute_count(obj: Any, name: str) -> int:
    if name not in set(getattr(obj, "PropertiesList", ()) or ()):
        return 0
    return len(getattr(obj, name))


def verify_point_results(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    processed = value.get("processed") if isinstance(value, dict) else None
    outputs = value.get("outputs") if isinstance(value, dict) else None
    group = value.get("group") if isinstance(value, dict) else None
    if (
        not isinstance(processed, ProcessedPointPlan)
        or not isinstance(outputs, tuple)
        or len(outputs) != len(processed.outputs)
        or not _history_exact(document, processed, outputs, group)
    ):
        raise NativeMeshError("The point-cloud operation failed its History postcondition.")
    summaries = []
    for obj, expected in zip(outputs, processed.outputs):
        count = int(obj.Points.CountPoints)
        expected_count = int(expected.points.CountPoints)
        expected_type = "Points::Structured" if expected.width > 0 else "Points::Feature"
        source_property = "Sources" if len(getattr(obj, "Sources", ()) or ()) > 1 else "Source"
        if (
            not is_live(document, obj)
            or str(obj.TypeId) != expected_type
            or str(obj.Label) != expected.label
            or count != expected_count
            or count < 1
            or obj.Placement != expected.placement
            or not bool(obj.isValid())
            or (
                expected.width > 0
                and (int(obj.Width), int(obj.Height))
                != (expected.width, expected.height)
            )
            or _attribute_count(obj, "Intensity") != len(expected.intensities)
            or _attribute_count(obj, "Color") != len(expected.colors)
            or _attribute_count(obj, "Normal") != len(expected.normals)
            or source_property not in set(obj.PropertiesList)
            or not bool(obj.Visibility)
        ):
            raise NativeMeshError("A point-cloud output failed its exact postcondition.")
        summaries.append(
            {
                **mesh_object_state(obj),
                "attributes": [
                    name
                    for name in ("Intensity", "Color", "Normal")
                    if name in set(obj.PropertiesList)
                ],
            }
        )
    prepared = processed.prepared
    if prepared.operation == "polygon_cut":
        source = prepared.point_targets[0].source
        if not is_live(document, source) or bool(source.Visibility):
            raise NativeMeshError("The point-cloud cut did not replace its exact source.")
    else:
        point_sources = prepared.point_targets
        if any(not point_target_still_exact(document, target) for target in point_sources):
            raise NativeMeshError("A point-cloud operation changed one of its exact sources.")
    source_objects = (
        [target.source for target in prepared.geometry_targets]
        if prepared.geometry_targets
        else [target.source for target in prepared.point_targets]
    )
    response = {
        "operation": prepared.operation,
        "sources": [object_reference(obj) for obj in source_objects],
        "outputs": summaries,
    }
    if group is not None:
        response["operation_controller"] = object_reference(group)
    if processed.dropped_attributes:
        response["dropped_incomplete_attributes"] = list(processed.dropped_attributes)
    if prepared.operation == "polygon_cut":
        response["result_mode"] = prepared.settings["result_mode"]
    return response
