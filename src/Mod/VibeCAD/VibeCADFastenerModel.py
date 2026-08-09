# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared retained Model graph for standard-fastener insertion."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ModelFastenerGraph:
    body: Any
    publication: Any
    state: Any
    operation: Any
    generator: Any
    identity: Mapping[str, Any]


def ensure_timeline_property(
    obj: Any,
    type_id: str,
    name: str,
    description: str,
) -> None:
    if name in obj.PropertiesList:
        existing_type = str(obj.getTypeIdOfProperty(name))
        if existing_type != type_id:
            raise TypeError(f"{name} must be {type_id}, not {existing_type}")
    else:
        obj.addProperty(
            type_id,
            name,
            "Timeline",
            description,
            attr=16,
            hidden=True,
            locked=True,
        )
    obj.setPropertyStatus(name, ("Hidden", "LockDynamic", "NoRecompute"))
    obj.setEditorMode(name, 2)


def mark_timeline_operation(
    operation: Any,
    edit_command: str = "VibeCAD_EditStandardFastener",
    *,
    editor: Any | None = None,
) -> None:
    """Persist one visible standard-fastener History operation and editor."""

    if operation is None:
        raise ValueError("A standard-fastener timeline operation is required")
    if not isinstance(edit_command, str) or not edit_command:
        raise ValueError("A standard-fastener timeline editor command is required")
    ensure_timeline_property(
        operation,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    ensure_timeline_property(
        operation,
        "App::PropertyString",
        "VibeCADTimelineEditCommand",
        "Command which edits this document timeline operation",
    )
    if "VibeCADTimelineOwner" in operation.PropertiesList:
        ensure_timeline_property(
            operation,
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Visible standard-component operation which owns this implementation",
        )
        operation.VibeCADTimelineOwner = None
    operation.VibeCADTimelineRole = "operation"
    operation.VibeCADTimelineEditCommand = edit_command
    if editor is None:
        if "VibeCADTimelineEditor" in operation.PropertiesList:
            ensure_timeline_property(
                operation,
                "App::PropertyLinkHidden",
                "VibeCADTimelineEditor",
                "Implementation object which edits this standard component",
            )
        return
    if editor is operation or getattr(editor, "Document", None) is not getattr(
        operation,
        "Document",
        None,
    ):
        raise ValueError(
            "A standard-fastener editor must be a distinct resource in the "
            "operation document"
        )
    if (
        str(getattr(editor, "VibeCADTimelineRole", "") or "") != "resource"
        or getattr(editor, "VibeCADTimelineOwner", None) is not operation
    ):
        raise ValueError(
            "A standard-fastener editor must already be owned by its "
            "timeline operation"
        )
    ensure_timeline_property(
        operation,
        "App::PropertyLinkHidden",
        "VibeCADTimelineEditor",
        "Implementation object which edits this standard component",
    )
    operation.VibeCADTimelineEditor = editor


def safe_fastener_object_name(value: str, fallback: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")
    if not clean or not re.match(r"[A-Za-z_]", clean):
        clean = fallback
    return clean[:96]


def copy_fastener_appearance(generator: Any, body: Any) -> None:
    """Publish the native generator appearance on the stable Body result."""

    source = getattr(generator, "ViewObject", None)
    publication = getattr(body, "Tip", None)
    targets = (
        getattr(body, "ViewObject", None),
        getattr(publication, "ViewObject", None),
    )
    for target in targets:
        if source is None or target is None:
            continue
        for name in (
            "ShapeColor",
            "LineColor",
            "PointColor",
            "Transparency",
            "LineWidth",
            "PointSize",
        ):
            if hasattr(source, name) and hasattr(target, name):
                setattr(target, name, getattr(source, name))


def generated_fastener_operation(generator: Any) -> Any | None:
    """Return the one retained Design operation backed by *generator*."""

    if generator is None:
        return None
    matches = [
        candidate
        for candidate in list(getattr(generator, "InList", []) or [])
        if str(getattr(candidate, "TypeId", "") or "")
        == "PartDesign::DesignGeneratedOperation"
        and str(getattr(candidate, "GeneratorKind", "") or "")
        == "standard-fastener"
        and getattr(candidate, "Generator", None) is generator
    ]
    return matches[0] if len(matches) == 1 else None


def generated_fastener_body(operation: Any) -> Any | None:
    """Return the one stable Body published by a retained fastener operation."""

    if (
        operation is None
        or str(getattr(operation, "TypeId", "") or "")
        != "PartDesign::DesignGeneratedOperation"
        or str(getattr(operation, "GeneratorKind", "") or "")
        != "standard-fastener"
    ):
        return None
    document = getattr(operation, "Document", None)
    body_ids = tuple(str(value) for value in operation.OutputBodyIds)
    if document is None or len(body_ids) != 1:
        return None
    matches = [
        candidate
        for candidate in list(getattr(document, "Objects", []) or [])
        if str(getattr(candidate, "TypeId", "") or "") == "PartDesign::Body"
        and str(getattr(candidate, "VibeCADBodyId", "") or "") == body_ids[0]
    ]
    return matches[0] if len(matches) == 1 else None


def model_fastener_graph_from_body(document: Any, body: Any) -> ModelFastenerGraph:
    """Resolve one exact modern fastener graph from its published Body."""

    from VibeCADFasteners import fastener_feature_identity

    if (
        document is None
        or body is None
        or getattr(body, "Document", None) is not document
        or document.getObject(str(getattr(body, "Name", "") or "")) is not body
        or str(getattr(body, "TypeId", "") or "") != "PartDesign::Body"
    ):
        raise RuntimeError("The exact standard-fastener Body no longer exists.")
    publication = getattr(body, "Tip", None)
    state = getattr(publication, "CurrentState", None)
    operation = getattr(state, "Operation", None)
    generator = getattr(operation, "Generator", None)
    if (
        publication is None
        or state is None
        or generated_fastener_body(operation) is not body
        or generated_fastener_operation(generator) is not operation
    ):
        raise RuntimeError(
            "The exact Body is not a retained Model standard fastener."
        )
    return ModelFastenerGraph(
        body,
        publication,
        state,
        operation,
        generator,
        dict(fastener_feature_identity(generator)),
    )


def model_fastener_graph_from_generator(generator: Any) -> ModelFastenerGraph:
    """Resolve one exact modern fastener graph from its retained generator."""

    operation = generated_fastener_operation(generator)
    body = generated_fastener_body(operation)
    document = getattr(generator, "Document", None)
    graph = model_fastener_graph_from_body(document, body)
    if graph.generator is not generator:
        raise RuntimeError("The standard-fastener generator ownership is ambiguous.")
    return graph


def create_model_fastener_graph(
    document: Any,
    *,
    label: str,
    standard: Any,
    nominal_thread: Any,
    length_mm: Any,
    model_thread: bool,
    left_handed: bool,
    options: Mapping[str, Any],
) -> ModelFastenerGraph:
    """Create the exact retained graph used by the Model ribbon command."""

    import PartDesign

    from VibeCADFasteners import create_fastener_feature

    visible_label = str(label or "").strip()
    if not visible_label or len(visible_label) > 160:
        raise ValueError("A standard-fastener label must contain 1 to 160 characters.")
    if (
        document is None
        or int(document.getBookedTransactionID()) == 0
    ):
        raise RuntimeError(
            "Model fastener insertion requires one active document transaction."
        )
    generator, identity = create_fastener_feature(
        document,
        standard=standard,
        nominal_thread=nominal_thread,
        length_mm=length_mm,
        model_thread=model_thread,
        left_handed=left_handed,
        options=options,
        object_name=safe_fastener_object_name(
            f"{visible_label}_Generator",
            "StandardFastenerGenerator",
        ),
        label=f"{visible_label} generator",
        targeted_recompute=True,
    )
    document.classifyProvisionalTimelineInternalObject(generator)
    generator_view = getattr(generator, "ViewObject", None)
    if generator_view is not None:
        generator_view.Visibility = False
        if hasattr(generator_view, "ShowInTree"):
            generator_view.ShowInTree = False

    operation = document.addObject(
        "PartDesign::DesignGeneratedOperation",
        safe_fastener_object_name(
            f"{visible_label}_Feature",
            "StandardFastenerFeature",
        ),
    )
    edit = PartDesign.beginDesignOperationEdit(operation)
    operation.Label = f"Fastener: {visible_label}"
    operation.GeneratorKind = "standard-fastener"
    operation.Generator = generator
    operation.OutputLabel = visible_label
    mark_timeline_operation(operation)
    PartDesign.setDesignOperationTargets(edit, "New Body", [])
    if document.recompute([generator, operation], True, True) is False:
        raise RuntimeError("The standard fastener failed its targeted recompute.")
    error = str(getattr(generator, "VibeCADFastenerError", "") or "")
    if error:
        raise RuntimeError(error)
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit, True) or [])
    if len(outputs) != 1 or outputs[0].TypeId != "PartDesign::Body":
        raise RuntimeError("The standard fastener did not publish exactly one Body.")
    body = outputs[0]
    body.Label = visible_label
    copy_fastener_appearance(generator, body)
    publication = getattr(body, "Tip", None)
    state = getattr(publication, "CurrentState", None)
    if publication is None or state is None:
        raise RuntimeError("The standard fastener did not publish a retained Body state.")
    return ModelFastenerGraph(
        body,
        publication,
        state,
        operation,
        generator,
        dict(identity),
    )


def edit_model_fastener_graph(
    document: Any,
    *,
    body: Any,
    label: str,
    standard: Any,
    nominal_thread: Any,
    length_mm: Any,
    model_thread: bool,
    left_handed: bool,
    options: Mapping[str, Any],
) -> ModelFastenerGraph:
    """Edit one retained fastener without replacing any graph identity."""

    import PartDesign

    from VibeCADFasteners import (
        compatible_fastener_standards,
        update_fastener_feature,
    )

    visible_label = str(label or "").strip()
    if not visible_label or len(visible_label) > 160:
        raise ValueError("A standard-fastener label must contain 1 to 160 characters.")
    if document is None or int(document.getBookedTransactionID()) == 0:
        raise RuntimeError(
            "Model fastener editing requires one active document transaction."
        )
    graph = model_fastener_graph_from_body(document, body)
    current = validate_model_fastener_graph(
        document,
        graph,
        label=str(graph.body.Label),
        canonical_key=str(graph.identity["canonical_key"]),
    )
    requested_standard = str(standard or "").strip()
    compatible = compatible_fastener_standards(graph.generator)
    if requested_standard not in compatible:
        raise RuntimeError(
            f"standard {requested_standard!r} cannot replace "
            f"{current['standard']!r} in place. Compatible standards: {compatible}."
        )

    edit = PartDesign.beginDesignOperationEdit(graph.operation)
    identity = update_fastener_feature(
        graph.generator,
        standard=standard,
        nominal_thread=nominal_thread,
        length_mm=length_mm,
        model_thread=model_thread,
        left_handed=left_handed,
        options=options,
        label=f"{visible_label} generator",
        targeted_recompute=True,
    )
    graph.operation.Label = f"Fastener: {visible_label}"
    graph.operation.OutputLabel = visible_label
    if document.recompute([graph.generator, graph.operation], True, True) is False:
        raise RuntimeError("The edited standard fastener failed its targeted recompute.")
    error = str(getattr(graph.generator, "VibeCADFastenerError", "") or "")
    if error:
        raise RuntimeError(error)
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit, True) or [])
    if len(outputs) != 1 or outputs[0] is not graph.body:
        raise RuntimeError(
            "The edited standard fastener did not retain its exact Body identity."
        )
    graph.body.Label = visible_label
    copy_fastener_appearance(graph.generator, graph.body)
    updated = model_fastener_graph_from_body(document, graph.body)
    if (
        updated.publication is not graph.publication
        or updated.state is not graph.state
        or updated.operation is not graph.operation
        or updated.generator is not graph.generator
        or str(updated.identity["canonical_key"])
        != str(identity["canonical_key"])
    ):
        raise RuntimeError(
            "The edited standard fastener replaced part of its retained graph."
        )
    validate_model_fastener_graph(
        document,
        updated,
        label=visible_label,
        canonical_key=str(identity["canonical_key"]),
    )
    return updated


def _valid_single_solid(shape: Any) -> bool:
    return bool(
        shape is not None
        and not shape.isNull()
        and shape.isValid()
        and len(shape.Solids) == 1
    )


def _only_solid(shape: Any) -> Any:
    solids = tuple(getattr(shape, "Solids", ()) or ())
    if len(solids) != 1:
        raise RuntimeError("A standard fastener must contain exactly one solid.")
    return solids[0]


def _solid_signature(
    shape: Any,
) -> tuple[tuple[int, ...], tuple[float, ...]]:
    solid = _only_solid(shape)
    center = solid.CenterOfMass
    inertia = solid.MatrixOfInertia
    face_areas = sorted(float(face.Area) for face in solid.Faces)
    edge_lengths = sorted(float(edge.Length) for edge in solid.Edges)
    vertex_coordinates = sorted(
        (
            float(vertex.Point.x),
            float(vertex.Point.y),
            float(vertex.Point.z),
        )
        for vertex in solid.Vertexes
    )
    return (
        (
            len(solid.Faces),
            len(solid.Edges),
            len(solid.Vertexes),
        ),
        (
            float(solid.Volume),
            float(solid.Area),
            float(center.x),
            float(center.y),
            float(center.z),
            float(inertia.A11),
            float(inertia.A12),
            float(inertia.A13),
            float(inertia.A22),
            float(inertia.A23),
            float(inertia.A33),
            *face_areas,
            *edge_lengths,
            *(coordinate for point in vertex_coordinates for coordinate in point),
        ),
    )


def _same_solid_geometry(left: Any, right: Any) -> bool:
    left_counts, left_values = _solid_signature(left)
    right_counts, right_values = _solid_signature(right)
    return left_counts == right_counts and all(
        math.isclose(
            left_value,
            right_value,
            rel_tol=1.0e-9,
            abs_tol=1.0e-7,
        )
        for left_value, right_value in zip(
            left_values,
            right_values,
            strict=True,
        )
    )


def _same_or_equivalent_solid(left: Any, right: Any) -> bool:
    """Use persistent kernel identity first and exact geometry after restoration."""

    try:
        if left.isSame(right) or left.isPartner(right):
            return True
    except Exception:
        pass
    return _same_solid_geometry(left, right)


def _shape_at_origin(shape: Any) -> Any:
    """Return a copy suitable for comparing retained local body geometry."""

    import FreeCAD as App

    local = shape.copy()
    local.Placement = App.Placement()
    return local


def validate_model_fastener_graph(
    document: Any,
    graph: ModelFastenerGraph,
    *,
    label: str,
    canonical_key: str,
) -> Mapping[str, Any]:
    """Prove the exact retained fastener graph after recompute or restoration."""

    import PartDesign

    from VibeCADFasteners import fastener_feature_identity

    if not isinstance(graph, ModelFastenerGraph):
        raise TypeError("graph must be a ModelFastenerGraph")
    body = graph.body
    publication = graph.publication
    state = graph.state
    operation = graph.operation
    generator = graph.generator
    exact_objects = (
        (body, "PartDesign::Body"),
        (publication, "PartDesign::DesignBodyPublication"),
        (state, "PartDesign::DesignBodyState"),
        (operation, "PartDesign::DesignGeneratedOperation"),
        (generator, "Part::FeaturePython"),
    )
    if any(
        document.getObject(obj.Name) is not obj or obj.TypeId != type_id
        for obj, type_id in exact_objects
    ):
        raise RuntimeError("The standard-fastener retained graph changed identity.")
    if (
        str(body.Label) != label
        or body.Tip is not publication
        or publication.getParentGeoFeatureGroup() is not body
        or publication.CurrentState is not state
        or state.Operation is not operation
        or str(state.BodyId) != str(body.VibeCADBodyId)
        or str(state.OperationId) != str(operation.OperationId)
        or operation.getParentGeoFeatureGroup() is not None
        or operation.Generator is not generator
        or str(operation.GeneratorKind) != "standard-fastener"
        or str(operation.ResultOperation) != "New Body"
        or str(operation.OutputLabel) != label
        or str(operation.Label) != f"Fastener: {label}"
        or tuple(str(value) for value in operation.OutputBodyIds)
        != (str(body.VibeCADBodyId),)
        or tuple(bool(value) for value in operation.OutputPresence) != (True,)
        or not operation.Shape.isNull()
        or not operation.isValid()
    ):
        raise RuntimeError("The standard-fastener Design publication is inconsistent.")
    if (
        str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
        or str(getattr(operation, "VibeCADTimelineEditCommand", "") or "")
        != "VibeCAD_EditStandardFastener"
        or getattr(operation, "VibeCADTimelineOwner", None) is not None
        or str(getattr(generator, "VibeCADTimelineRole", "") or "") != "internal"
        or getattr(generator, "VibeCADTimelineOwner", None) is not None
        or generator.getParentGeoFeatureGroup() is not None
        or str(generator.Label) != f"{label} generator"
    ):
        raise RuntimeError("The standard-fastener History ownership is inconsistent.")
    generator_view = getattr(generator, "ViewObject", None)
    if generator_view is not None and (
        bool(generator_view.Visibility)
        or bool(getattr(generator_view, "ShowInTree", True))
    ):
        raise RuntimeError("The standard-fastener generator is not retained internally.")
    timeline = document.getObject("VibeCADTimeline")
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        operations.count(state) != 1
        or operations.count(operation) != 1
        or operations.index(operation) == 0
        or operations[operations.index(operation) - 1] is not state
    ):
        raise RuntimeError("The standard-fastener semantic History block is invalid.")
    generated_shape = generator.Shape
    output_shapes = tuple(operation.OutputShapes)
    retained_shapes = (state.Shape, publication.Shape)
    generator_placement = generator.Placement
    body_placement = body.getGlobalPlacement()
    if (
        not _valid_single_solid(generated_shape)
        or len(output_shapes) != 1
        or not _valid_single_solid(output_shapes[0])
        or not all(_valid_single_solid(shape) for shape in retained_shapes)
        or not _valid_single_solid(body.Shape)
        or generated_shape.Placement != generator_placement
        or body.Shape.Placement != body_placement
        or body_placement != generator_placement
        or not _same_solid_geometry(
            _shape_at_origin(generated_shape),
            output_shapes[0],
        )
        or not _same_or_equivalent_solid(output_shapes[0], state.Shape)
        or not _same_or_equivalent_solid(output_shapes[0], publication.Shape)
        or not _same_solid_geometry(
            output_shapes[0],
            _shape_at_origin(body.Shape),
        )
    ):
        raise RuntimeError("The standard-fastener Body differs from its generator.")
    identity = fastener_feature_identity(generator)
    if (
        str(identity["canonical_key"]) != str(canonical_key)
        or str(graph.identity["canonical_key"]) != str(canonical_key)
    ):
        raise RuntimeError("The standard-fastener catalog identity changed.")
    PartDesign.validateDesign(operation)
    return identity
