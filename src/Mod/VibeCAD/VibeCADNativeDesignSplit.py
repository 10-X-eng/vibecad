# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact identity-safe Design Split implementation for the Model ribbon."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_DEFINITION_FIELDS = frozenset(
    {"source_body", "splitters", "retained_region_index"}
)
_SPLITTER_FIELDS = frozenset({"object_name", "subelements"})
_ALLOWED_SUBELEMENT_TYPES = frozenset({"Face", "Shell", "Solid"})
_DESIGN_OPERATION_PROPERTIES = frozenset(
    {"ResultOperation", "InputStates", "OutputBodyIds", "OutputFrames"}
)
_MAX_SPLITTERS = 32
_MAX_SUBELEMENTS = 64
_MAX_TOTAL_SUBELEMENTS = 256
_MAX_REGIONS = 256


@dataclass(frozen=True, slots=True)
class DesignSplitDefinitionSpec:
    reference: NativeObjectRef
    subelements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesignSplitSpec:
    source_ref: NativeObjectRef
    splitters: tuple[DesignSplitDefinitionSpec, ...]
    retained_region_index: int


@dataclass(frozen=True, slots=True)
class ResolvedDesignSplitBody:
    body: Any
    state: Any
    body_id: str
    component_id: str
    component: Any | None
    shape: Any
    shape_fingerprint: str | None
    frame: Any


@dataclass(frozen=True, slots=True)
class PreparedDesignSplitter:
    spec: DesignSplitDefinitionSpec
    requested: Any
    exact_target: Any
    body: Any | None
    body_state: Any | None
    body_id: str
    shape: Any
    shape_fingerprint: str | None
    frame: Any
    selected_shapes: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class PreparedDesignSplit:
    spec: DesignSplitSpec
    source: ResolvedDesignSplitBody
    splitters: tuple[PreparedDesignSplitter, ...]


def _object_ref(document_uid: str, value: Any, *, role: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Design Split {role} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def prepare_design_split(
    document_uid: str,
    value: Mapping[str, Any],
) -> DesignSplitSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Design Split definition has incorrect fields.")
    source = _object_ref(document_uid, value["source_body"], role="source Body")
    raw_splitters = value["splitters"]
    if not isinstance(raw_splitters, list) or not 1 <= len(raw_splitters) <= _MAX_SPLITTERS:
        raise NativeModelError("Design Split requires 1 to 32 exact definitions.")
    splitters = []
    names = []
    total_subelements = 0
    for raw in raw_splitters:
        if not isinstance(raw, Mapping) or set(raw) != _SPLITTER_FIELDS:
            raise NativeModelError("A Design Split definition target is invalid.")
        reference = NativeObjectRef(document_uid, str(raw["object_name"] or ""))
        raw_subelements = raw["subelements"]
        if not isinstance(raw_subelements, list) or len(raw_subelements) > _MAX_SUBELEMENTS:
            raise NativeModelError("A Design Split target has invalid subelements.")
        subelements = tuple(str(item or "") for item in raw_subelements)
        if any(not item for item in subelements) or len(subelements) != len(set(subelements)):
            raise NativeModelError(
                "Design Split subelements must be nonempty and distinct."
            )
        total_subelements += len(subelements)
        splitters.append(DesignSplitDefinitionSpec(reference, subelements))
        names.append(reference.object_name)
    if len(names) != len(set(names)):
        raise NativeModelError(
            "Each Design Split object must appear once with all of its subelements."
        )
    if total_subelements > _MAX_TOTAL_SUBELEMENTS:
        raise NativeModelError("Design Split accepts at most 256 exact subelements.")
    retained = value["retained_region_index"]
    if type(retained) is not int or not 0 <= retained < _MAX_REGIONS:
        raise NativeModelError(
            "Design Split retained_region_index must be an integer from 0 to 255."
        )
    return DesignSplitSpec(source, tuple(splitters), retained)


def _shape_fingerprint(shape: Any) -> str | None:
    try:
        return hashlib.sha256(shape.exportBrepToString().encode("utf-8")).hexdigest()
    except Exception:
        return None


def _shape_is_exact(current: Any, expected: Any, fingerprint: str | None) -> bool:
    try:
        if (
            current is not None
            and not current.isNull()
            and current.isPartner(expected)
            and current.Placement == expected.Placement
            and str(current.Orientation) == str(expected.Orientation)
        ):
            return True
        return fingerprint is not None and _shape_fingerprint(current) == fingerprint
    except Exception:
        return False


def _is_derived(obj: Any, type_id: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_id))
    except Exception:
        return str(getattr(obj, "TypeId", "") or "") == type_id


def _body_frame(body: Any, *, role: str):
    try:
        return body.getGlobalPlacement()
    except Exception as exc:
        raise NativeModelError(f"The Design Split {role} has no stable global frame.") from exc


def _resolve_body(document: Any, reference: NativeObjectRef) -> ResolvedDesignSplitBody:
    import PartGui

    body = resolve_object(document, reference, expected_types=("PartDesign::Body",))
    if not PartGui.isModelingObjectActive(body):
        raise NativeModelError("The Design Split source Body is not active in current History.")
    state = PartGui.resolveModelingObject(body)
    shape = getattr(body, "Shape", None)
    body_id = str(getattr(body, "VibeCADBodyId", "") or "")
    component_id = str(getattr(body, "ComponentId", "") or "")
    component = body.getParentGeoFeatureGroup()
    if (
        state is None
        or getattr(state, "Document", None) is not document
        or shape is None
        or shape.isNull()
        or not shape.isValid()
        or len(shape.Solids) != 1
        or not body_id
    ):
        raise NativeModelError(
            "Design Split requires one source Body with one exact current solid state."
        )
    if component is not None and str(getattr(component, "ComponentId", "") or "") != component_id:
        raise NativeModelError("The Design Split source Body has inconsistent Component identity.")
    return ResolvedDesignSplitBody(
        body,
        state,
        body_id,
        component_id,
        component,
        shape,
        _shape_fingerprint(shape),
        _body_frame(body, role="source Body"),
    )


def _definition_frame(target: Any):
    try:
        global_frame = target.getGlobalPlacement()
        local_frame = target.Placement
        return global_frame.multiply(local_frame.inverse())
    except Exception as exc:
        raise NativeModelError(
            "A standalone Design Split definition has no stable containing frame."
        ) from exc


def _selected_shapes(shape: Any, subelements: tuple[str, ...]) -> tuple[Any, ...]:
    if not subelements:
        shape_type = str(shape.ShapeType)
        if shape_type not in {"Face", "Shell", "Solid", "CompSolid", "Compound"}:
            raise NativeModelError(
                "A whole Design Split definition must contain faces, shells, or solids."
            )
        if not shape.Faces and not shape.Solids:
            raise NativeModelError("A Design Split definition has no dividing geometry.")
        return (shape,)
    result = []
    for subelement in subelements:
        try:
            selected = shape.getElement(subelement)
        except Exception as exc:
            raise NativeModelError(
                f"Design Split can no longer resolve {subelement}."
            ) from exc
        if (
            selected is None
            or selected.isNull()
            or not selected.isValid()
            or str(selected.ShapeType) not in _ALLOWED_SUBELEMENT_TYPES
        ):
            raise NativeModelError(
                "Design Split subelements must resolve to exact faces, shells, or solids."
            )
        result.append(selected)
    return tuple(result)


def _resolve_splitter(
    document: Any,
    spec: DesignSplitDefinitionSpec,
    source: ResolvedDesignSplitBody,
) -> PreparedDesignSplitter:
    import PartGui

    requested = resolve_object(document, spec.reference)
    if not PartGui.isModelingObjectActive(requested):
        raise NativeModelError("A Design Split definition is not active in current History.")
    if not (
        _is_derived(requested, "PartDesign::Body")
        or _is_derived(requested, "Part::Feature")
        or str(getattr(requested, "TypeId", "") or "")
        == "PartDesign::DesignBodyPublication"
    ):
        raise NativeModelError(
            "A Design Split definition must be a Body, face, surface, shell, or solid."
        )

    found_body = PartGui.findModelingBody(requested)
    body = found_body if _is_derived(found_body, "PartDesign::Body") else None
    if body is not None:
        body_id = str(getattr(body, "VibeCADBodyId", "") or "")
        state = PartGui.resolveModelingObject(body)
        frame = _body_frame(body, role="definition Body")
        if (
            body is source.body
            or not body_id
            or body_id == source.body_id
            or state is None
            or getattr(state, "Document", None) is not document
            or not PartGui.isModelingObjectActive(body)
        ):
            raise NativeModelError(
                "The source Body cannot also be its own Design Split definition."
            )
        exact_target = state
        body_state = state
    else:
        body_id = ""
        body_state = None
        exact_target = PartGui.resolveModelingObject(requested)
        if (
            exact_target is not requested
            or not _is_derived(requested, "Part::Feature")
            or _DESIGN_OPERATION_PROPERTIES.issubset(
                set(getattr(requested, "PropertiesList", ()) or ())
            )
        ):
            raise NativeModelError(
                "A standalone Design Split definition must be a reusable current shape."
            )
        frame = _definition_frame(requested)

    shape = getattr(exact_target, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        raise NativeModelError("A Design Split definition has no valid current shape.")
    selected = _selected_shapes(shape, spec.subelements)
    return PreparedDesignSplitter(
        spec,
        requested,
        exact_target,
        body,
        body_state,
        body_id,
        shape,
        _shape_fingerprint(shape),
        frame,
        selected,
    )


def preflight_design_split(
    document: Any,
    spec: DesignSplitSpec,
) -> PreparedDesignSplit:
    if not isinstance(spec, DesignSplitSpec):
        raise TypeError("spec must be a DesignSplitSpec")
    source = _resolve_body(document, spec.source_ref)
    splitters = tuple(
        _resolve_splitter(document, definition, source)
        for definition in spec.splitters
    )
    exact_targets = tuple(item.exact_target for item in splitters)
    if len(exact_targets) != len({id(item) for item in exact_targets}):
        raise NativeModelError(
            "Design Split definitions resolve to duplicate current shapes."
        )
    body_ids = tuple(item.body_id for item in splitters if item.body_id)
    if len(body_ids) != len(set(body_ids)):
        raise NativeModelError("Design Split definition Bodies must be distinct.")
    return PreparedDesignSplit(spec, source, splitters)


def _source_is_exact(document: Any, source: ResolvedDesignSplitBody) -> bool:
    try:
        import PartGui

        body = source.body
        return (
            document.getObject(body.Name) is body
            and PartGui.isModelingObjectActive(body)
            and PartGui.resolveModelingObject(body) is source.state
            and str(body.VibeCADBodyId) == source.body_id
            and str(getattr(body, "ComponentId", "") or "") == source.component_id
            and body.getParentGeoFeatureGroup() is source.component
            and body.getGlobalPlacement() == source.frame
            and _shape_is_exact(body.Shape, source.shape, source.shape_fingerprint)
        )
    except Exception:
        return False


def _splitter_is_exact(document: Any, splitter: PreparedDesignSplitter) -> bool:
    try:
        import PartGui

        if (
            document.getObject(splitter.requested.Name) is not splitter.requested
            or not PartGui.isModelingObjectActive(splitter.requested)
        ):
            return False
        if splitter.body is not None:
            return (
                document.getObject(splitter.body.Name) is splitter.body
                and PartGui.resolveModelingObject(splitter.body) is splitter.body_state
                and str(splitter.body.VibeCADBodyId) == splitter.body_id
                and splitter.body.getGlobalPlacement() == splitter.frame
                and _shape_is_exact(
                    splitter.body_state.Shape,
                    splitter.shape,
                    splitter.shape_fingerprint,
                )
            )
        return (
            PartGui.resolveModelingObject(splitter.requested) is splitter.exact_target
            and _definition_frame(splitter.requested) == splitter.frame
            and _shape_is_exact(
                splitter.exact_target.Shape,
                splitter.shape,
                splitter.shape_fingerprint,
            )
        )
    except Exception:
        return False


def _splitters_are_exact(document: Any, prepared: PreparedDesignSplit) -> bool:
    return all(_splitter_is_exact(document, item) for item in prepared.splitters)


def _link_sub_groups(values: Any) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    result = []
    for value in tuple(values):
        if isinstance(value, tuple):
            target, raw_subelements = value
            if isinstance(raw_subelements, str):
                subelements = (raw_subelements,) if raw_subelements else ()
            else:
                subelements = tuple(
                    str(item) for item in tuple(raw_subelements or ()) if str(item)
                )
        else:
            target, subelements = value, ()
        result.append((target, subelements))
    return tuple(result)


def _shape_signature(shape: Any) -> tuple[Any, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Faces),
        len(shape.Solids),
        float(shape.Volume),
        float(shape.Area),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
    )


def _same_signature(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    return left[:5] == right[:5] and all(
        math.isclose(actual, expected, rel_tol=1.0e-9, abs_tol=1.0e-7)
        for actual, expected in zip(left[5:], right[5:])
    )


def create_design_split(
    document: Any,
    *,
    label: str,
    prepared: PreparedDesignSplit,
) -> NativeMutationDraft:
    import PartDesign

    if not isinstance(prepared, PreparedDesignSplit):
        raise TypeError("prepared must be a PreparedDesignSplit")
    if not _source_is_exact(document, prepared.source) or not _splitters_are_exact(
        document,
        prepared,
    ):
        raise NativeModelError("A Design Split source or definition changed after preflight.")

    operation = document.addObject("PartDesign::DesignSplit", "Split")
    if operation is None or operation.TypeId != "PartDesign::DesignSplit":
        raise NativeModelError("The Design Split factory returned the wrong object type.")
    operation.Label = label
    refine = bool(operation.Refine)
    fuzzy_tolerance = float(operation.FuzzyTolerance)
    edit = PartDesign.beginDesignOperationEdit(operation)
    references = [
        (splitter.requested, list(splitter.spec.subelements))
        for splitter in prepared.splitters
    ]
    try:
        witnesses = tuple(
            PartDesign.setDesignSplitDefinition(
                edit,
                prepared.source.body,
                references,
            )
            or ()
        )
    except RuntimeError as exc:
        raise NativeModelError(
            "The exact Design Split definitions do not divide the source into valid regions."
        ) from exc
    if not 2 <= len(witnesses) <= _MAX_REGIONS:
        raise NativeModelError("Design Split must produce 2 to 256 solid regions.")
    retained = prepared.spec.retained_region_index
    if retained >= len(witnesses):
        raise NativeModelError(
            f"Design Split found {len(witnesses)} regions; retained_region_index is outside them."
        )
    try:
        PartDesign.assignDesignSplitRegions(
            edit,
            prepared.source.body,
            witnesses,
            retained,
        )
    except RuntimeError as exc:
        raise NativeModelError(
            "Design Split could not assign the selected retained region."
        ) from exc
    recomputed = document.recompute([operation], True, True)
    output_shapes = tuple(operation.OutputShapes)
    if (
        recomputed is False
        or not operation.isValid()
        or len(output_shapes) != len(witnesses)
        or any(shape.isNull() or not shape.isValid() or len(shape.Solids) != 1 for shape in output_shapes)
    ):
        raise NativeModelError(
            str(operation.getStatusString() or "Design Split produced invalid regions.")
        )
    normalized_splitters = _link_sub_groups(operation.Splitters)
    splitter_frames = tuple(operation.SplitterFrames)
    ordered_witnesses = tuple(operation.RegionWitnesses)
    outputs = tuple(PartDesign.finalizeDesignOperationEdit(edit) or ())
    if (
        len(outputs) != len(witnesses)
        or outputs[0] is not prepared.source.body
        or len({id(body) for body in outputs}) != len(outputs)
    ):
        raise NativeModelError("Design Split published unexpected output Bodies.")
    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "operation": operation,
            "outputs": outputs,
            "refine": refine,
            "fuzzy_tolerance": fuzzy_tolerance,
            "normalized_splitters": normalized_splitters,
            "splitter_frames": splitter_frames,
            "witnesses": ordered_witnesses,
            "body_ids": tuple(str(body.VibeCADBodyId) for body in outputs),
        },
        recompute_targets=(operation, *outputs),
        created=(
            object_identity(operation),
            *(object_identity(body) for body in outputs[1:]),
        ),
        changed=(object_identity(outputs[0]),),
    )


def _witness_tuple(value: Any) -> tuple[float, float, float]:
    return (float(value.x), float(value.y), float(value.z))


def _property_number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def verify_design_split(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign
    import PartGui

    prepared: PreparedDesignSplit = draft.value["prepared"]
    source = prepared.source
    operation = draft.value["operation"]
    outputs = tuple(draft.value["outputs"])
    body_ids = tuple(draft.value["body_ids"])
    output_shapes = tuple(operation.OutputShapes)
    output_frames = tuple(operation.OutputFrames)
    expected_component_ids = ("",) + tuple(
        source.component_id for _item in outputs[1:]
    )
    expected_previous = (0,) + tuple(-1 for _item in outputs[1:])
    input_states = (source.state,) + tuple(
        splitter.body_state
        for splitter in prepared.splitters
        if splitter.body_state is not None
    )
    input_body_ids = (source.body_id,) + tuple(
        splitter.body_id for splitter in prepared.splitters if splitter.body_id
    )
    input_frames = (source.frame,) + tuple(
        splitter.frame for splitter in prepared.splitters if splitter.body is not None
    )
    if (
        document.getObject(operation.Name) is not operation
        or operation.TypeId != "PartDesign::DesignSplit"
        or str(operation.Label) != draft.value["label"]
        or str(operation.ResultOperation) != "Split"
        or str(operation.SourceBodyId) != source.body_id
        or not bool(operation.RetainedRegionChosen)
        or bool(operation.Refine) is not draft.value["refine"]
        or not math.isclose(
            _property_number(operation.FuzzyTolerance),
            draft.value["fuzzy_tolerance"],
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        or operation.BaseFeature is not None
        or not operation.Shape.isNull()
        or operation.getParentGeoFeatureGroup() is not None
        or not operation.isValid()
        or tuple(operation.InputStates) != input_states
        or tuple(str(item) for item in operation.InputBodyIds) != input_body_ids
        or tuple(operation.InputFrames) != input_frames
        or _link_sub_groups(operation.Splitters) != draft.value["normalized_splitters"]
        or tuple(operation.SplitterFrames) != draft.value["splitter_frames"]
        or tuple(operation.RegionWitnesses) != draft.value["witnesses"]
        or tuple(str(item) for item in operation.OutputBodyIds) != body_ids
        or tuple(int(item) for item in operation.OutputPreviousInputIndices)
        != expected_previous
        or tuple(str(item) for item in operation.OutputComponentIds)
        != expected_component_ids
        or tuple(bool(item) for item in operation.OutputPresence)
        != tuple(True for _item in outputs)
        or tuple(str(item) for item in operation.TargetBodyIds) != body_ids
        or tuple(operation.TargetFrames) != output_frames
        or len(output_shapes) != len(outputs)
        or len(output_frames) != len(outputs)
        or any(frame != source.frame for frame in output_frames)
        or str(operation.DestinationComponentId)
        != (source.component_id if len(outputs) == 2 else "")
    ):
        raise NativeModelError("The Design Split controls or identity ports changed.")
    if (
        str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(operation, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(operation, "DesignId", "") or "")
        or not str(getattr(operation, "OperationId", "") or "")
        or not _splitters_are_exact(document, prepared)
    ):
        raise NativeModelError("The Design Split retained definition is inconsistent.")
    if (
        document.getObject(source.body.Name) is not source.body
        or str(source.body.VibeCADBodyId) != source.body_id
        or source.body.getGlobalPlacement() != source.frame
        or source.body.getParentGeoFeatureGroup() is not source.component
    ):
        raise NativeModelError("The Design Split source Body lost its identity or frame.")

    regions = []
    total_volume = 0.0
    for index, (body, shape, frame, witness) in enumerate(
        zip(outputs, output_shapes, output_frames, operation.RegionWitnesses),
    ):
        current = PartGui.resolveModelingObject(body)
        body_shape = body.Shape
        expected_previous_state = source.state if index == 0 else None
        if (
            document.getObject(body.Name) is not body
            or body.TypeId != "PartDesign::Body"
            or str(body.VibeCADBodyId) != body_ids[index]
            or body.getGlobalPlacement() != frame
            or body.getParentGeoFeatureGroup() is not source.component
            or body_shape.isNull()
            or not body_shape.isValid()
            or len(body_shape.Solids) != 1
            or shape.isNull()
            or not shape.isValid()
            or len(shape.Solids) != 1
            or current is None
            or current.Operation is not operation
            or current.PreviousState is not expected_previous_state
            or not bool(current.Present)
            or not _same_signature(_shape_signature(body_shape), _shape_signature(shape))
        ):
            raise NativeModelError("A Design Split output Body failed its exact postcondition.")
        volume = float(body_shape.Volume)
        total_volume += volume
        regions.append(
            {
                "body": object_reference(body),
                "retains_source_identity": index == 0,
                "witness_mm": {
                    "x": _witness_tuple(witness)[0],
                    "y": _witness_tuple(witness)[1],
                    "z": _witness_tuple(witness)[2],
                },
                "volume_mm3": volume,
            }
        )
    if not math.isclose(
        total_volume,
        float(source.shape.Volume),
        rel_tol=1.0e-8,
        abs_tol=1.0e-7,
    ):
        raise NativeModelError("Design Split output volumes do not partition the source solid.")
    PartDesign.validateDesign(operation)
    return {
        "operation": object_reference(operation),
        "source_body": object_reference(source.body),
        "splitter_count": len(prepared.splitters),
        "retained_region_index": prepared.spec.retained_region_index,
        "regions": regions,
    }
