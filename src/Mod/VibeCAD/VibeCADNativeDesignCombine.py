# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained Join, Cut, and Intersect across explicit Design Bodies."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDesignBodies import is_valid_body_shape
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_MODE_TO_NATIVE = {
    "join": "Join",
    "cut": "Cut",
    "intersect": "Intersect",
}
_DEFINITION_FIELDS = frozenset(
    {"mode", "result_body", "tool_bodies", "keep_tools"}
)
_MAX_TOOLS = 15


@dataclass(frozen=True, slots=True)
class DesignCombineSpec:
    mode: str
    result_ref: NativeObjectRef
    tool_refs: tuple[NativeObjectRef, ...]
    keep_tools: bool

    @property
    def native_mode(self) -> str:
        return _MODE_TO_NATIVE[self.mode]


@dataclass(frozen=True, slots=True)
class ResolvedDesignCombineBody:
    body: Any
    state: Any
    body_id: str
    shape: Any
    frame: Any


@dataclass(frozen=True, slots=True)
class PreparedDesignCombine:
    spec: DesignCombineSpec
    result: ResolvedDesignCombineBody
    tools: tuple[ResolvedDesignCombineBody, ...]

    @property
    def bodies(self) -> tuple[ResolvedDesignCombineBody, ...]:
        return (self.result, *self.tools)


def _object_ref(document_uid: str, value: Any, *, role: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Design Combine {role} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def prepare_design_combine(
    document_uid: str,
    value: Mapping[str, Any],
) -> DesignCombineSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Design Combine definition has incorrect fields.")
    mode = str(value["mode"] or "")
    if mode not in _MODE_TO_NATIVE:
        raise NativeModelError("Design Combine mode must be join, cut, or intersect.")
    if type(value["keep_tools"]) is not bool:
        raise NativeModelError("Design Combine keep_tools must be true or false.")
    result = _object_ref(document_uid, value["result_body"], role="result Body")
    raw_tools = value["tool_bodies"]
    if not isinstance(raw_tools, list) or not 1 <= len(raw_tools) <= _MAX_TOOLS:
        raise NativeModelError("Design Combine requires 1 to 15 exact tool Bodies.")
    tools = tuple(
        _object_ref(document_uid, item, role="tool Body") for item in raw_tools
    )
    names = (result.object_name, *(item.object_name for item in tools))
    if len(names) != len(set(names)):
        raise NativeModelError("Design Combine result and tool Bodies must be distinct.")
    return DesignCombineSpec(mode, result, tools, value["keep_tools"])


def _shape_is_exact(current: Any, expected: Any) -> bool:
    return (
        current is not None
        and not current.isNull()
        and current.isPartner(expected)
        and current.Placement == expected.Placement
        and str(current.Orientation) == str(expected.Orientation)
    )


def _resolve_body(
    document: Any,
    reference: NativeObjectRef,
    *,
    role: str,
) -> ResolvedDesignCombineBody:
    import PartGui

    body = resolve_object(
        document,
        reference,
        expected_types=("PartDesign::Body",),
    )
    if not PartGui.isModelingObjectActive(body):
        raise NativeModelError(f"The Design Combine {role} is not active in current History.")
    state = PartGui.resolveModelingObject(body)
    shape = getattr(body, "Shape", None)
    body_id = str(getattr(body, "VibeCADBodyId", "") or "")
    try:
        frame = body.getGlobalPlacement()
    except Exception as exc:
        raise NativeModelError(
            f"The Design Combine {role} has no stable global frame."
        ) from exc
    if (
        state is None
        or getattr(state, "Document", None) is not document
        or not is_valid_body_shape(body, shape)
        or not body_id
    ):
        raise NativeModelError(
            f"The Design Combine {role} must contain one exact current Body state."
        )
    return ResolvedDesignCombineBody(body, state, body_id, shape, frame)


def preflight_design_combine(
    document: Any,
    spec: DesignCombineSpec,
) -> PreparedDesignCombine:
    if not isinstance(spec, DesignCombineSpec):
        raise TypeError("spec must be a DesignCombineSpec")
    result = _resolve_body(document, spec.result_ref, role="result Body")
    tools = tuple(
        _resolve_body(document, reference, role="tool Body")
        for reference in spec.tool_refs
    )
    bodies = (result, *tools)
    if len({id(item.body) for item in bodies}) != len(bodies):
        raise NativeModelError("Design Combine Bodies resolve to duplicate objects.")
    if len({id(item.state) for item in bodies}) != len(bodies):
        raise NativeModelError("Design Combine Bodies resolve to duplicate History states.")
    if len({item.body_id for item in bodies}) != len(bodies):
        raise NativeModelError("Design Combine Bodies require distinct persistent identities.")
    return PreparedDesignCombine(spec, result, tools)


def _body_is_exact(document: Any, target: ResolvedDesignCombineBody) -> bool:
    try:
        import PartGui

        body = target.body
        return (
            document.getObject(body.Name) is body
            and PartGui.isModelingObjectActive(body)
            and PartGui.resolveModelingObject(body) is target.state
            and str(body.VibeCADBodyId) == target.body_id
            and _shape_is_exact(body.Shape, target.shape)
            and body.getGlobalPlacement() == target.frame
        )
    except Exception:
        return False


def _property_number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def create_design_combine(
    document: Any,
    *,
    label: str,
    prepared: PreparedDesignCombine,
) -> NativeMutationDraft:
    import PartDesign

    if not isinstance(prepared, PreparedDesignCombine):
        raise TypeError("prepared must be a PreparedDesignCombine")
    if any(not _body_is_exact(document, target) for target in prepared.bodies):
        raise NativeModelError("A Design Combine Body changed after preflight.")

    operation = document.addObject("PartDesign::DesignCombine", "Combine")
    if operation is None or operation.TypeId != "PartDesign::DesignCombine":
        raise NativeModelError("The Design Combine factory returned the wrong object type.")
    operation.Label = label
    refine = bool(operation.Refine)
    fuzzy_tolerance = _property_number(operation.FuzzyTolerance)
    edit = PartDesign.beginDesignOperationEdit(operation)
    PartDesign.setDesignCombineBodies(
        edit,
        prepared.spec.native_mode,
        prepared.result.body,
        [tool.body for tool in prepared.tools],
        prepared.spec.keep_tools,
    )
    recomputed = document.recompute([operation], True, True)
    if recomputed is False or not operation.isValid():
        raise NativeModelError(
            str(operation.getStatusString() or "Design Combine produced invalid geometry.")
        )
    output_shapes = tuple(operation.OutputShapes)
    expected_count = 1 if prepared.spec.keep_tools else len(prepared.bodies)
    if (
        len(output_shapes) != expected_count
        or not is_valid_body_shape(prepared.result.body, output_shapes[0])
    ):
        raise NativeModelError("Design Combine produced an invalid solid output.")
    outputs = tuple(PartDesign.finalizeDesignOperationEdit(edit) or ())
    expected_outputs = (
        (prepared.result.body,)
        if prepared.spec.keep_tools
        else tuple(item.body for item in prepared.bodies)
    )
    if outputs != expected_outputs:
        raise NativeModelError("Design Combine published unexpected Body outputs.")
    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "operation": operation,
            "outputs": outputs,
            "refine": refine,
            "fuzzy_tolerance": fuzzy_tolerance,
        },
        recompute_targets=(operation, *outputs),
        created=(object_identity(operation),),
        changed=tuple(object_identity(body) for body in outputs),
    )


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


def verify_design_combine(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign
    import PartGui

    prepared: PreparedDesignCombine = draft.value["prepared"]
    spec = prepared.spec
    operation = draft.value["operation"]
    bodies = prepared.bodies
    output_count = 1 if spec.keep_tools else len(bodies)
    output_bodies = tuple(item.body for item in bodies[:output_count])
    body_ids = tuple(item.body_id for item in bodies)
    output_ids = body_ids[:output_count]
    frames = tuple(item.frame for item in bodies)
    output_frames = frames[:output_count]
    output_shapes = tuple(operation.OutputShapes)
    expected_presence = (True, *([False] * (output_count - 1)))
    if (
        document.getObject(operation.Name) is not operation
        or operation.TypeId != "PartDesign::DesignCombine"
        or str(operation.Label) != draft.value["label"]
        or operation.ResultOperation != spec.native_mode
        or str(operation.ResultBodyId) != prepared.result.body_id
        or bool(operation.KeepTools) is not spec.keep_tools
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
        or tuple(operation.InputStates) != tuple(item.state for item in bodies)
        or tuple(operation.InputBodyIds) != body_ids
        or tuple(operation.InputFrames) != frames
        or tuple(operation.OutputBodyIds) != output_ids
        or tuple(operation.OutputFrames) != output_frames
        or tuple(operation.OutputPreviousInputIndices) != tuple(range(output_count))
        or tuple(operation.OutputPresence) != expected_presence
        or any(str(value) for value in operation.OutputComponentIds)
        or tuple(operation.TargetBodyIds) != output_ids
        or tuple(operation.TargetFrames) != output_frames
        or tuple(draft.value["outputs"]) != output_bodies
        or len(output_shapes) != output_count
        or not is_valid_body_shape(prepared.result.body, output_shapes[0])
        or any(not shape.isNull() for shape in output_shapes[1:])
    ):
        raise NativeModelError("The Design Combine controls or exact Body ports changed.")

    result_shape = prepared.result.body.Shape
    if result_shape.isNull() or not _same_signature(
        _shape_signature(result_shape),
        _shape_signature(output_shapes[0]),
    ):
        raise NativeModelError("Design Combine did not publish its result solid to the Body.")
    global_result = output_shapes[0].copy()
    global_result.Placement = prepared.result.frame.multiply(global_result.Placement)
    if operation.PreviewShape.isNull() or not _same_signature(
        _shape_signature(global_result),
        _shape_signature(operation.PreviewShape),
    ):
        raise NativeModelError("Design Combine preview differs from its result Body.")

    body_results = []
    for index, target in enumerate(bodies):
        body = target.body
        present = index == 0 or spec.keep_tools
        if index == 0 or not spec.keep_tools:
            tip = getattr(body, "Tip", None)
            current = getattr(tip, "CurrentState", None)
            if current is target.state:
                raise NativeModelError("Design Combine did not advance an output Body state.")
            if getattr(current, "Operation", None) is not operation:
                raise NativeModelError("Design Combine Body state lost its operation.")
            if getattr(current, "PreviousState", None) is not target.state:
                raise NativeModelError("Design Combine Body state lost its exact predecessor.")
            if bool(getattr(current, "Present", False)) is not present:
                raise NativeModelError("Design Combine Body state has incorrect presence.")
        elif (
            PartGui.resolveModelingObject(body) is not target.state
            or not _shape_is_exact(body.Shape, target.shape)
            or body.getGlobalPlacement() != target.frame
        ):
            raise NativeModelError("Design Combine changed a preserved tool Body.")
        shape = body.Shape
        if present != (not shape.isNull()):
            raise NativeModelError("Design Combine Body presence differs from its saved port.")
        body_results.append(
            {
                "body": object_reference(body),
                "role": "result" if index == 0 else "tool",
                "present": present,
                "volume_mm3": float(shape.Volume) if present else 0.0,
            }
        )

    PartDesign.validateDesign(operation)
    return {
        "operation": object_reference(operation),
        "mode": spec.mode,
        "keep_tools": spec.keep_tools,
        "bodies": body_results,
    }
