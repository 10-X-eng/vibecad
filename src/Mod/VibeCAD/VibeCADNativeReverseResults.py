# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic History publication and proof for Reverse Engineering results."""

from __future__ import annotations

from typing import Any

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeReversePlan import ProcessedReverseOutput, ProcessedReversePlan
from VibeCADNativeTargets import object_identity, object_reference


_PUBLICATION = {
    "poisson_reconstruction": ("PoissonSurface", "Poisson Surface", "Poisson reconstruction"),
    "view_triangulation": (
        "PointTriangulations",
        "Point Triangulations",
        "Triangulate structured points",
    ),
    "approx_plane": ("PlaneFits", "Fitted Planes", "Fit planes"),
    "approx_cylinder": ("CylinderFits", "Fitted Cylinders", "Fit cylinders"),
    "approx_sphere": ("SphereFits", "Fitted Spheres", "Fit spheres"),
    "approx_polynomial": (
        "PolynomialFits",
        "Polynomial Surfaces",
        "Fit polynomial surfaces",
    ),
    "approx_surface": ("FittedSurface", "Fitted Surface", "Fit B-spline surface"),
    "approx_curve": ("FittedCurve", "Fitted Curve", "Fit B-spline curve"),
}


def _sources(processed: ProcessedReversePlan) -> list[Any]:
    prepared = processed.prepared
    if prepared.point_targets:
        return [target.source for target in prepared.point_targets]
    if prepared.geometry_targets:
        return [target.source for target in prepared.geometry_targets]
    return [target.exact.source for target in prepared.mesh_targets]


def _add_source(output: Any, source: Any) -> None:
    output.addProperty(
        "App::PropertyLink",
        "Source",
        "Operation",
        "Exact source used for this retained Reverse Engineering result",
        locked=True,
    )
    output.Source = source


def _create_output(
    document: Any,
    output: ProcessedReverseOutput,
    source: Any,
) -> Any:
    if output.kind == "mesh":
        obj = document.addObject("Mesh::Feature", document.getUniqueObjectName("ReverseMesh"))
        obj.Mesh = output.geometry
    elif output.kind == "plane":
        obj = document.addObject("Part::Plane", document.getUniqueObjectName("PlaneFit"))
        obj.Length = output.geometry["length_mm"]
        obj.Width = output.geometry["width_mm"]
    elif output.kind == "cylinder":
        obj = document.addObject("Part::Cylinder", document.getUniqueObjectName("CylinderFit"))
        obj.Radius = output.geometry["radius_mm"]
        obj.Height = output.geometry["height_mm"]
    elif output.kind == "sphere":
        obj = document.addObject("Part::Sphere", document.getUniqueObjectName("SphereFit"))
        obj.Radius = output.geometry["radius_mm"]
    elif output.kind == "part_shape":
        obj = document.addObject("Part::Spline", document.getUniqueObjectName("SplineFit"))
        obj.Shape = output.geometry
    else:
        raise NativeMeshError("A Reverse Engineering result has an unavailable output kind.")
    if obj is None:
        raise NativeMeshError("A Reverse Engineering result object could not be created.")
    obj.Label = output.label
    obj.Placement = output.placement
    _add_source(obj, source)
    return obj


def create_reverse_results(document: Any, processed: ProcessedReversePlan) -> NativeMutationDraft:
    from VibeCADNativeReversePlan import reverse_plan_still_exact

    if not isinstance(processed, ProcessedReversePlan):
        raise TypeError("processed must be a ProcessedReversePlan")
    if not reverse_plan_still_exact(document, processed.prepared):
        raise NativeMeshError(
            "An exact Reverse Engineering source changed after detached processing.",
            error_code="NATIVE_REVERSE_STATE_STALE",
        )
    sources = _sources(processed)
    if len(sources) != len(processed.outputs):
        raise NativeMeshError("Reverse Engineering lost its exact source/output pairing.")
    outputs = tuple(
        _create_output(document, output, source)
        for output, source in zip(processed.outputs, sources)
    )

    import MeshGui

    object_name, label, operation_kind = _PUBLICATION[processed.prepared.operation]
    group = MeshGui.publishSourcePreservingOutputs(
        str(document.Name),
        sources,
        list(outputs),
        object_name,
        label,
        operation_kind,
    )
    created = (*outputs, *([group] if group is not None else []))
    return NativeMutationDraft(
        value={"processed": processed, "outputs": outputs, "group": group},
        recompute_targets=created,
        created=tuple(object_identity(obj) for obj in created),
    )


def _history_exact(
    document: Any,
    processed: ProcessedReversePlan,
    outputs: tuple[Any, ...],
    group: Any,
) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return False
    sources = tuple(_sources(processed))
    if group is None:
        return (
            len(outputs) == 1
            and is_live(document, outputs[0])
            and list(getattr(timeline, "Operations", ()) or ()).count(outputs[0]) == 1
            and str(getattr(outputs[0], "VibeCADTimelineRole", "") or "") == "operation"
            and getattr(outputs[0], "VibeCADTimelineOwner", None) is None
            and tuple(getattr(outputs[0], "VibeCADTimelineReplacedInputs", ()) or ()) == ()
        )
    return (
        is_live(document, group)
        and list(getattr(timeline, "Operations", ()) or ()).count(group) == 1
        and str(getattr(group, "VibeCADTimelineRole", "") or "") == "operation"
        and getattr(group, "VibeCADTimelineOwner", None) is None
        and tuple(getattr(group, "Sources", ()) or ()) == sources
        and tuple(getattr(group, "Group", ()) or ()) == outputs
        and str(getattr(group, "InputMode", "") or "") == "Source preserving"
        and str(getattr(group, "OperationKind", "") or "")
        == _PUBLICATION[processed.prepared.operation][2]
        and tuple(getattr(group, "VibeCADTimelineReplacedInputs", ()) or ()) == ()
        and all(
            str(getattr(output, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(output, "VibeCADTimelineOwner", None) is group
            for output in outputs
        )
    )


def _valid_output(obj: Any, expected: ProcessedReverseOutput, source: Any) -> bool:
    if (
        not is_live(obj.Document, obj)
        or str(obj.Label) != expected.label
        or obj.Placement != expected.placement
        or getattr(obj, "Source", None) is not source
        or not bool(obj.isValid())
        or not bool(obj.Visibility)
    ):
        return False
    if expected.kind == "mesh":
        return str(obj.TypeId) == "Mesh::Feature" and int(obj.Mesh.CountFacets) > 0
    if expected.kind == "plane":
        return (
            str(obj.TypeId) == "Part::Plane"
            and abs(float(obj.Length) - expected.geometry["length_mm"]) <= 1.0e-7
            and abs(float(obj.Width) - expected.geometry["width_mm"]) <= 1.0e-7
            and not obj.Shape.isNull()
        )
    if expected.kind == "cylinder":
        return (
            str(obj.TypeId) == "Part::Cylinder"
            and abs(float(obj.Radius) - expected.geometry["radius_mm"]) <= 1.0e-7
            and abs(float(obj.Height) - expected.geometry["height_mm"]) <= 1.0e-7
            and not obj.Shape.isNull()
        )
    if expected.kind == "sphere":
        return (
            str(obj.TypeId) == "Part::Sphere"
            and abs(float(obj.Radius) - expected.geometry["radius_mm"]) <= 1.0e-7
            and not obj.Shape.isNull()
        )
    return str(obj.TypeId) == "Part::Spline" and not obj.Shape.isNull()


def verify_reverse_results(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    from VibeCADNativeReversePlan import reverse_plan_still_exact

    value = draft.value
    processed = value.get("processed") if isinstance(value, dict) else None
    outputs = value.get("outputs") if isinstance(value, dict) else None
    group = value.get("group") if isinstance(value, dict) else None
    if (
        not isinstance(processed, ProcessedReversePlan)
        or not isinstance(outputs, tuple)
        or len(outputs) != len(processed.outputs)
        or not _history_exact(document, processed, outputs, group)
        or not reverse_plan_still_exact(document, processed.prepared)
    ):
        raise NativeMeshError("The Reverse Engineering operation failed its History postcondition.")
    sources = _sources(processed)
    if any(
        not _valid_output(obj, expected, source)
        for obj, expected, source in zip(outputs, processed.outputs, sources)
    ):
        raise NativeMeshError("A Reverse Engineering output failed its exact postcondition.")

    summaries = []
    for obj, expected in zip(outputs, processed.outputs):
        summaries.append({**mesh_object_state(obj), "fit": dict(expected.metrics)})
    response: dict[str, Any] = {
        "operation": processed.prepared.operation,
        "sources": [object_reference(source) for source in sources],
        "outputs": summaries,
    }
    if group is not None:
        response["operation_controller"] = object_reference(group)
    return response
