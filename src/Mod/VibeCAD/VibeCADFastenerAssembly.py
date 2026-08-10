# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared retained Assembly graph for standard-fastener insertion."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADFastenerModel import (
    ensure_timeline_property,
    mark_timeline_operation,
    safe_fastener_object_name,
)


@dataclass(frozen=True, slots=True)
class AssemblyFastenerGraph:
    assembly: Any
    occurrence: Any
    source: Any
    identity: Mapping[str, Any]


def _live_object(document: Any, obj: Any, type_id: str) -> bool:
    return bool(
        document is not None
        and obj is not None
        and getattr(obj, "Document", None) is document
        and document.getObject(str(getattr(obj, "Name", "") or "")) is obj
        and str(getattr(obj, "TypeId", "") or "") == type_id
    )


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _valid_single_solid(shape: Any) -> bool:
    return bool(
        shape is not None
        and not shape.isNull()
        and shape.isValid()
        and len(shape.Solids) == 1
    )


def _shape_summary(shape: Any) -> tuple[int, int, int, float]:
    return (
        len(shape.Faces),
        len(shape.Edges),
        len(shape.Vertexes),
        float(shape.Volume),
    )


def _same_shape_summary(first: Any, second: Any) -> bool:
    left = _shape_summary(first)
    right = _shape_summary(second)
    return left[:3] == right[:3] and math.isclose(
        left[3],
        right[3],
        rel_tol=1.0e-9,
        abs_tol=1.0e-7,
    )


def create_assembly_fastener_graph(
    document: Any,
    *,
    assembly: Any,
    label: str,
    standard: Any,
    nominal_thread: Any,
    length_mm: Any,
    model_thread: bool,
    left_handed: bool,
    options: Mapping[str, Any],
    targeted_recompute: bool = False,
) -> AssemblyFastenerGraph:
    """Create the exact retained graph used by the Assemble ribbon command."""

    import UtilsAssembly

    from VibeCADFasteners import create_fastener_feature

    visible_label = str(label or "").strip()
    if not visible_label:
        raise ValueError("An Assembly standard-fastener label is required.")
    if not isinstance(targeted_recompute, bool):
        raise TypeError("targeted_recompute must be a boolean")
    if (
        document is None
        or not _live_object(document, assembly, "Assembly::AssemblyObject")
        or int(document.getBookedTransactionID()) == 0
    ):
        raise RuntimeError(
            "Assembly fastener insertion requires one live Assembly and active transaction."
        )

    occurrence = assembly.newObject(
        "App::Link",
        safe_fastener_object_name(visible_label, "StandardFastener"),
    )
    if occurrence is None:
        raise RuntimeError(
            "The active Assembly could not create a fastener occurrence."
        )
    occurrence.Label = visible_label
    source, identity = create_fastener_feature(
        document,
        standard=standard,
        nominal_thread=nominal_thread,
        length_mm=length_mm,
        model_thread=model_thread,
        left_handed=left_handed,
        options=options,
        object_name=safe_fastener_object_name(
            f"{standard}_{nominal_thread}_Definition",
            "StandardFastenerDefinition",
        ),
        label=visible_label,
        targeted_recompute=targeted_recompute,
    )
    source_view = getattr(source, "ViewObject", None)
    if source_view is not None:
        source_view.Visibility = False
        if hasattr(source_view, "ShowInTree"):
            source_view.ShowInTree = False
    # Establish operation-owned metadata before App::Link begins exposing the
    # definition's dynamic properties.  Otherwise the linked definition's
    # resource owner can be mistaken for the occurrence's own owner.
    ensure_timeline_property(
        occurrence,
        "App::PropertyLinkHidden",
        "VibeCADTimelineOwner",
        "Visible standard-component operation which owns this implementation",
    )
    occurrence.VibeCADTimelineOwner = None
    mark_timeline_operation(occurrence)
    occurrence.LinkedObject = source
    UtilsAssembly.markTimelineResource(source, occurrence)
    mark_timeline_operation(occurrence, editor=source)
    document.finalizeProvisionalTimelineOperationBlock(
        occurrence,
        [source, occurrence],
    )
    return AssemblyFastenerGraph(
        assembly=assembly,
        occurrence=occurrence,
        source=source,
        identity=dict(identity),
    )


def assembly_fastener_graph_from_occurrence(
    assembly: Any,
    occurrence: Any,
) -> AssemblyFastenerGraph:
    """Resolve one exact modern standard-fastener occurrence graph."""

    from VibeCADFasteners import fastener_feature_identity

    document = getattr(assembly, "Document", None)
    source = getattr(occurrence, "LinkedObject", None)
    if (
        not _live_object(document, assembly, "Assembly::AssemblyObject")
        or not _live_object(document, occurrence, "App::Link")
        or not _live_object(document, source, "Part::FeaturePython")
        or tuple(getattr(assembly, "Group", ()) or ()).count(occurrence) != 1
        or source in tuple(getattr(assembly, "Group", ()) or ())
    ):
        raise RuntimeError(
            "The exact Assembly standard-fastener graph is no longer live."
        )
    try:
        identity = dict(fastener_feature_identity(source))
    except Exception as exc:
        raise RuntimeError(
            "The Assembly occurrence source is not an exact standard fastener."
        ) from exc
    return AssemblyFastenerGraph(assembly, occurrence, source, identity)


def validate_assembly_fastener_graph(
    document: Any,
    graph: AssemblyFastenerGraph,
    *,
    label: str,
    canonical_key: str,
) -> Mapping[str, Any]:
    """Prove source ownership, History, link identity, and generated geometry."""

    if not isinstance(graph, AssemblyFastenerGraph):
        raise TypeError("graph must be an AssemblyFastenerGraph")
    resolved = assembly_fastener_graph_from_occurrence(
        graph.assembly,
        graph.occurrence,
    )
    assembly = resolved.assembly
    occurrence = resolved.occurrence
    source = resolved.source
    if (
        graph.assembly is not assembly
        or graph.occurrence is not occurrence
        or graph.source is not source
    ):
        raise RuntimeError("The Assembly standard-fastener graph changed identity.")
    history_violations = []
    if str(occurrence.Label) != label:
        history_violations.append("occurrence label")
    if occurrence.LinkedObject is not source:
        history_violations.append("linked definition")
    if str(getattr(occurrence, "VibeCADTimelineRole", "") or "") != "operation":
        history_violations.append("occurrence role")
    if (
        str(getattr(occurrence, "VibeCADTimelineEditCommand", "") or "")
        != "VibeCAD_EditStandardFastener"
    ):
        history_violations.append("edit command")
    if getattr(occurrence, "VibeCADTimelineOwner", None) is not None:
        history_violations.append("occurrence owner")
    if getattr(occurrence, "VibeCADTimelineEditor", None) is not source:
        history_violations.append("editor definition")
    if str(getattr(source, "VibeCADTimelineRole", "") or "") != "resource":
        history_violations.append("definition role")
    if getattr(source, "VibeCADTimelineOwner", None) is not occurrence:
        history_violations.append("definition owner")
    if not _timeline_active(occurrence):
        history_violations.append("occurrence activity")
    if not _timeline_active(source):
        history_violations.append("definition activity")
    if history_violations:
        raise RuntimeError(
            "The Assembly standard-fastener History ownership is inconsistent: "
            + ", ".join(history_violations)
            + "."
        )
    timeline = document.getObject("VibeCADTimeline")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(getattr(timeline, "VisibilityAtEnd", ()) or ())
    resources = tuple(
        candidate
        for candidate in operations
        if str(getattr(candidate, "VibeCADTimelineRole", "") or "") == "resource"
        and getattr(candidate, "VibeCADTimelineOwner", None) is occurrence
    )
    try:
        occurrence_index = operations.index(occurrence)
        source_index = operations.index(source)
    except ValueError as exc:
        raise RuntimeError(
            "The Assembly standard fastener is absent from document History."
        ) from exc
    source_view = getattr(source, "ViewObject", None)
    occurrence_view = getattr(occurrence, "ViewObject", None)
    if (
        timeline is None
        or str(getattr(timeline, "TypeId", "") or "") != "App::DocumentTimeline"
        or len(operations) != len(visibility)
        or resources != (source,)
        or source_index + 1 != occurrence_index
        or bool(visibility[source_index])
        or bool(visibility[occurrence_index])
        is not bool(getattr(occurrence_view, "Visibility", True))
        or source_view is not None
        and (
            bool(getattr(source_view, "Visibility", False))
            or bool(getattr(source_view, "ShowInTree", False))
        )
        or occurrence_view is not None
        and not bool(getattr(occurrence_view, "ShowInTree", True))
    ):
        raise RuntimeError(
            "The Assembly standard-fastener History presentation is inconsistent."
        )
    if (
        not _valid_single_solid(source.Shape)
        or not _valid_single_solid(occurrence.Shape)
        or not _same_shape_summary(source.Shape, occurrence.Shape)
    ):
        raise RuntimeError(
            "The Assembly standard-fastener occurrence differs from its source."
        )
    if str(resolved.identity.get("canonical_key") or "") != str(canonical_key) or str(
        graph.identity.get("canonical_key") or ""
    ) != str(canonical_key):
        raise RuntimeError("The Assembly standard-fastener catalog identity changed.")
    return resolved.identity


def assembly_fastener_summary(
    assembly: Any,
    occurrence: Any,
) -> dict[str, Any] | None:
    """Return bounded provider state only for one exact modern occurrence."""

    try:
        graph = assembly_fastener_graph_from_occurrence(assembly, occurrence)
        identity = validate_assembly_fastener_graph(
            assembly.Document,
            graph,
            label=str(occurrence.Label),
            canonical_key=str(graph.identity["canonical_key"]),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        return None
    return {
        "source": {"object_name": str(graph.source.Name)},
        "canonical_key": str(identity["canonical_key"]),
        "part_number": str(identity["part_number"]),
        "standard": str(identity["standard"]),
        "nominal_thread": str(identity["nominal_size"]),
        "length_mm": identity["length_mm"],
        "model_thread": bool(identity["model_thread"]),
        "left_handed": bool(identity["left_handed"]),
        "options": dict(identity["options"]),
    }
