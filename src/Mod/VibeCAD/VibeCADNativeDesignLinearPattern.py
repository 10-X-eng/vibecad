# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, execution, and verification for Design Linear Pattern."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeDesignPatterns import (
    DesignPatternSourceSpec,
    configure_pattern_source,
    pattern_source_from_mapping,
    pattern_source_summary,
    resolve_pattern_source,
)
from VibeCADNativeDesignReferences import (
    DesignLinkSpec,
    resolve_definition_link,
    sketch_axis_count,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_EDGE = re.compile(r"^Edge[1-9][0-9]*$")
_SKETCH_AXIS = re.compile(r"^(?:H_Axis|V_Axis|N_Axis|Axis[0-9]+)$")


@dataclass(frozen=True, slots=True)
class DesignLinearDirectionSpec:
    kind: str
    vector: tuple[float, float, float] | None = None
    reference: DesignLinkSpec | None = None


@dataclass(frozen=True, slots=True)
class DesignLinearPatternSpec:
    source: DesignPatternSourceSpec
    direction: DesignLinearDirectionSpec
    spacing_mm: float
    occurrences: int
    centered: bool


def _vector(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeModelError("A Design Linear Pattern direction vector is invalid.")
    raw = tuple(value[axis] for axis in ("x", "y", "z"))
    if any(isinstance(number, bool) for number in raw):
        raise NativeModelError("A Design Linear Pattern direction must contain numbers.")
    try:
        result = tuple(float(number) for number in raw)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(
            "A Design Linear Pattern direction must contain numbers."
        ) from exc
    if not all(math.isfinite(number) for number in result):
        raise NativeModelError(
            "A Design Linear Pattern direction must contain finite numbers."
        )
    if math.sqrt(sum(number * number for number in result)) < 1.0e-12:
        raise NativeModelError("A Design Linear Pattern direction must be non-zero.")
    return result


def _direction_from_mapping(
    document_uid: str,
    value: Any,
) -> DesignLinearDirectionSpec:
    if not isinstance(value, Mapping) or "kind" not in value:
        raise NativeModelError("A Design Linear Pattern direction is invalid.")
    kind = str(value["kind"])
    if kind == "explicit":
        if set(value) != {"kind", "vector"}:
            raise NativeModelError("An explicit Linear Pattern direction is invalid.")
        return DesignLinearDirectionSpec(kind, vector=_vector(value["vector"]))
    expected = {"kind", "object_name"}
    if kind == "object":
        if set(value) != expected:
            raise NativeModelError("A Linear Pattern direction object is invalid.")
        subelements: tuple[str, ...] = ()
    elif kind == "subelement":
        if set(value) != expected | {"subelement"}:
            raise NativeModelError("A Linear Pattern direction subelement is invalid.")
        subelement = str(value["subelement"])
        if _EDGE.fullmatch(subelement) is None and _SKETCH_AXIS.fullmatch(subelement) is None:
            raise NativeModelError(
                "A Linear Pattern direction requires an exact sketch axis or EdgeN."
            )
        subelements = (subelement,)
    else:
        raise NativeModelError("A Design Linear Pattern direction is invalid.")
    return DesignLinearDirectionSpec(
        kind,
        reference=DesignLinkSpec(
            NativeObjectRef(document_uid, str(value["object_name"])),
            subelements,
        ),
    )


def prepare_design_linear_pattern(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignLinearPatternSpec:
    definition = values.get("definition")
    if not isinstance(definition, Mapping) or set(definition) != {
        "kind",
        "direction",
        "spacing_mm",
        "occurrences",
        "centered",
    } or definition.get("kind") != "linear":
        raise NativeModelError("A Design Pattern definition is not Linear Pattern.")
    raw_spacing = definition["spacing_mm"]
    raw_occurrences = definition["occurrences"]
    centered = definition["centered"]
    if isinstance(raw_spacing, bool):
        raise NativeModelError("A Linear Pattern spacing must be a number.")
    try:
        spacing = float(raw_spacing)
    except (TypeError, ValueError) as exc:
        raise NativeModelError("A Linear Pattern spacing must be a number.") from exc
    if not math.isfinite(spacing) or not 0.0 < spacing <= 1.0e9:
        raise NativeModelError(
            "A Linear Pattern spacing must be finite, positive, and at most 1e9 mm."
        )
    if isinstance(raw_occurrences, bool) or not isinstance(raw_occurrences, int):
        raise NativeModelError("Linear Pattern occurrences must be an integer.")
    if not 2 <= raw_occurrences <= 10000:
        raise NativeModelError("Linear Pattern occurrences must be from 2 to 10000.")
    if not isinstance(centered, bool):
        raise NativeModelError("Linear Pattern centered must be boolean.")
    return DesignLinearPatternSpec(
        pattern_source_from_mapping(document_uid, values.get("source")),
        _direction_from_mapping(document_uid, definition["direction"]),
        spacing,
        raw_occurrences,
        centered,
    )


def _derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "")) == type_id:
        return True
    check = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(check(type_id)) if callable(check) else False
    except Exception:
        return False


def _preflight_direction(document: Any, direction: DesignLinearDirectionSpec) -> None:
    if direction.reference is None:
        return
    source = resolve_object(document, direction.reference.object_ref)
    subelements = direction.reference.subelements
    if not subelements:
        if not any(
            _derived(source, type_id)
            for type_id in ("PartDesign::Line", "App::Line", "Part::Part2DObject")
        ):
            raise NativeModelError(
                "A Linear Pattern direction object must be a datum axis or sketch."
            )
        return
    subelement = subelements[0]
    if _SKETCH_AXIS.fullmatch(subelement):
        if not _derived(source, "Part::Part2DObject"):
            raise NativeModelError("A Linear Pattern sketch axis requires an exact sketch.")
        if subelement.startswith("Axis"):
            index = int(subelement[4:])
            count = sketch_axis_count(source)
            if index >= count:
                raise NativeModelError("The exact Linear Pattern sketch axis no longer exists.")
        return
    if not (_derived(source, "Part::Feature") or _derived(source, "PartDesign::Body")):
        raise NativeModelError("A Linear Pattern Edge must belong to a shape feature.")
    shape = getattr(source, "Shape", None)
    try:
        edge = shape.getElement(subelement) if shape is not None else None
    except Exception as exc:
        raise NativeModelError(
            f"Linear Pattern direction {direction.reference.object_ref.object_name}.{subelement} no longer exists."
        ) from exc
    if (
        edge is None
        or str(getattr(edge, "ShapeType", "")) != "Edge"
        or str(getattr(getattr(edge, "Curve", None), "TypeId", "")) != "Part::GeomLine"
    ):
        raise NativeModelError("A Linear Pattern direction Edge must be straight.")


def preflight_design_linear_pattern(
    document: Any,
    spec: DesignLinearPatternSpec,
) -> None:
    if not isinstance(spec, DesignLinearPatternSpec):
        raise TypeError("spec must be a DesignLinearPatternSpec")
    resolve_pattern_source(document, spec.source)
    _preflight_direction(document, spec.direction)


def _placement_signature(value: Any) -> tuple[float, ...]:
    return (
        float(value.Base.x),
        float(value.Base.y),
        float(value.Base.z),
        *(float(component) for component in value.Rotation.Q),
    )


def _link_identity(value: Any) -> tuple[str | None, tuple[str, ...]]:
    if not value:
        return None, ()
    obj, raw = value if isinstance(value, tuple) else (value, ())
    return (
        str(getattr(obj, "Name", "")) or None,
        tuple(str(item) for item in list(raw or ()) if str(item)),
    )


def _configure_direction(
    operation: Any,
    direction: DesignLinearDirectionSpec,
) -> dict[str, Any]:
    import FreeCAD as App

    if direction.reference is None:
        operation.DirectionReference = None
        operation.Direction = App.Vector(*direction.vector)
        return {
            "kind": "explicit",
            "vector": list(direction.vector),
            "frame": _placement_signature(operation.DirectionReferenceFrame),
        }
    resolved = resolve_definition_link(operation, direction.reference)
    operation.DirectionReference = (
        (resolved[0], resolved[1]) if resolved[1] else resolved[0]
    )
    linked_object = operation.DirectionReference[0]
    linked_name, linked_subelements = _link_identity(operation.DirectionReference)
    if linked_object is None or linked_name is None:
        raise NativeModelError("The Linear Pattern direction reference did not persist.")
    return {
        "kind": direction.kind,
        "reference": {
            "object": object_reference(linked_object),
            "subelements": list(linked_subelements),
        },
        "frame": _placement_signature(operation.DirectionReferenceFrame),
    }


def create_design_linear_pattern(
    document: Any,
    *,
    label: str,
    spec: DesignLinearPatternSpec,
) -> NativeMutationDraft:
    import PartDesign

    operation = document.addObject(
        "PartDesign::DesignLinearPattern",
        "DesignLinearPattern",
    )
    if operation is None or operation.TypeId != "PartDesign::DesignLinearPattern":
        raise NativeModelError("The Design Linear Pattern factory returned the wrong type.")
    operation.Label = label
    operation.Spacing = spec.spacing_mm
    operation.Occurrences = spec.occurrences
    operation.Centered = spec.centered
    edit = PartDesign.beginDesignOperationEdit(operation)
    source, targets, result_mode = configure_pattern_source(
        operation,
        edit,
        spec.source,
        generated_copy_count=spec.occurrences - 1,
    )
    direction = _configure_direction(operation, spec.direction)
    document.recompute([operation], True, True)
    if not operation.isValid():
        raise NativeModelError(
            str(operation.getStatusString() or "The Design Linear Pattern is invalid.")
        )
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    expected_count = spec.occurrences - 1 if spec.source.kind == "body" else len(targets)
    if len(outputs) != expected_count:
        raise NativeModelError("The Linear Pattern published an unexpected Body count.")
    created = [object_identity(operation)]
    changed = [object_identity(target) for target in targets]
    component = None
    if spec.source.kind == "body":
        created.extend(object_identity(output) for output in outputs)
        component = source.getParentGeoFeatureGroup()
        if component is not None and component.TypeId == "PartDesign::Component":
            changed.append(object_identity(component))
    return NativeMutationDraft(
        value={
            "operation": operation,
            "source": source,
            "targets": targets,
            "outputs": outputs,
            "component": component,
            "spec": spec,
            "result_mode": result_mode,
            "direction": direction,
        },
        recompute_targets=(operation, *outputs),
        created=tuple(created),
        changed=tuple(changed),
    )


def _vector_close(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(
        math.isclose(float(component), wanted, rel_tol=1.0e-9, abs_tol=1.0e-7)
        for component, wanted in zip((actual.x, actual.y, actual.z), expected)
    )


def verify_design_linear_pattern(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    import PartDesign

    operation = draft.value["operation"]
    source = draft.value["source"]
    targets = list(draft.value["targets"])
    outputs = list(draft.value["outputs"])
    component = draft.value["component"]
    spec: DesignLinearPatternSpec = draft.value["spec"]
    direction = draft.value["direction"]
    result_mode = draft.value["result_mode"]
    generated = spec.occurrences - 1
    if (
        document.getObject(operation.Name) is not operation
        or operation.TypeId != "PartDesign::DesignLinearPattern"
        or not operation.isValid()
        or int(operation.GeneratedOccurrenceCount) != generated
        or int(operation.Occurrences) != spec.occurrences
        or not math.isclose(
            float(getattr(operation.Spacing, "Value", operation.Spacing)),
            spec.spacing_mm,
            abs_tol=1.0e-7,
        )
        or bool(operation.Centered) is not spec.centered
        or operation.BaseFeature is not None
        or operation.ResultOperation != result_mode
        or str(operation.PatternSource) != ("Body" if spec.source.kind == "body" else "Feature")
        or any(document.getObject(body.Name) is not body for body in outputs)
    ):
        raise NativeModelError("The Linear Pattern failed its exact operation postcondition.")
    output_shapes = list(operation.OutputShapes)
    if len(output_shapes) != len(outputs) or any(
        shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
        for shape in output_shapes
    ):
        raise NativeModelError("The Linear Pattern produced an invalid solid result.")

    if spec.source.kind == "body":
        source_id = str(source.VibeCADBodyId)
        output_ids = [str(output.VibeCADBodyId) for output in outputs]
        if (
            operation.SourceOperation is not None
            or list(operation.InputBodyIds) != [source_id]
            or len(list(operation.InputStates)) != 1
            or list(operation.OutputBodyIds) != output_ids
            or list(operation.OutputPreviousInputIndices) != [-1] * generated
            or len(set((source_id, *output_ids))) != generated + 1
            or any(output.getParentGeoFeatureGroup() is not component for output in outputs)
        ):
            raise NativeModelError("The Body Linear Pattern changed its output contract.")
    else:
        body_ids = [str(target.VibeCADBodyId) for target in targets]
        if (
            operation.SourceOperation is not source
            or list(operation.InputBodyIds) != body_ids
            or list(operation.OutputBodyIds) != body_ids
            or list(operation.OutputPreviousInputIndices) != list(range(len(targets)))
            or len(list(operation.InputStates)) != len(targets)
            or outputs != targets
        ):
            raise NativeModelError("The Feature Linear Pattern changed its target contract.")

    if direction["kind"] == "explicit":
        if (
            _link_identity(operation.DirectionReference) != (None, ())
            or not _vector_close(operation.Direction, spec.direction.vector)
        ):
            raise NativeModelError("The explicit Linear Pattern direction changed.")
    else:
        reference = direction["reference"]
        expected_link = (
            reference["object"]["object_name"],
            tuple(reference["subelements"]),
        )
        if _link_identity(operation.DirectionReference) != expected_link:
            raise NativeModelError("The referenced Linear Pattern direction changed.")
    if _placement_signature(operation.DirectionReferenceFrame) != tuple(direction["frame"]):
        raise NativeModelError("The Linear Pattern direction frame changed.")

    PartDesign.validateDesign(operation)
    return {
        "operation": object_reference(operation),
        "source": pattern_source_summary(spec.source, source, targets),
        "result_mode": "new_body" if result_mode == "New Bodies" else result_mode.lower(),
        "bodies": [
            {
                "body": object_reference(body),
                "solid_count": len(body.Shape.Solids),
                "volume_mm3": float(body.Shape.Volume),
            }
            for body in outputs
        ],
        "definition": {
            "kind": "linear",
            "direction": {key: value for key, value in direction.items() if key != "frame"},
            "spacing_mm": spec.spacing_mm,
            "occurrences": spec.occurrences,
            "centered": spec.centered,
            "generated_occurrence_count": generated,
        },
    }
