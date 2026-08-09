# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, execution, and verification for Design Circular Pattern."""

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
_SUPPORTED_EDGE_CURVES = frozenset({"Part::GeomLine", "Part::GeomCircle"})


@dataclass(frozen=True, slots=True)
class DesignCircularAxisSpec:
    kind: str
    origin: tuple[float, float, float] | None = None
    direction: tuple[float, float, float] | None = None
    reference: DesignLinkSpec | None = None


@dataclass(frozen=True, slots=True)
class DesignCircularPatternSpec:
    source: DesignPatternSourceSpec
    axis: DesignCircularAxisSpec
    angle_degrees: float
    occurrences: int
    reversed: bool


def _vector(
    value: Any,
    *,
    name: str,
    require_nonzero: bool,
) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeModelError(f"A Circular Pattern axis {name} is invalid.")
    raw = tuple(value[coordinate] for coordinate in ("x", "y", "z"))
    if any(isinstance(number, bool) for number in raw):
        raise NativeModelError(
            f"A Circular Pattern axis {name} must contain numbers."
        )
    try:
        result = tuple(float(number) for number in raw)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(
            f"A Circular Pattern axis {name} must contain numbers."
        ) from exc
    if not all(math.isfinite(number) for number in result):
        raise NativeModelError(
            f"A Circular Pattern axis {name} must contain finite numbers."
        )
    if require_nonzero and math.sqrt(sum(number * number for number in result)) < 1.0e-12:
        raise NativeModelError("A Circular Pattern axis direction must be non-zero.")
    return result


def _axis_from_mapping(
    document_uid: str,
    value: Any,
) -> DesignCircularAxisSpec:
    if not isinstance(value, Mapping) or "kind" not in value:
        raise NativeModelError("A Design Circular Pattern axis is invalid.")
    kind = str(value["kind"])
    if kind == "explicit":
        if set(value) != {"kind", "origin_mm", "direction"}:
            raise NativeModelError("An explicit Circular Pattern axis is invalid.")
        return DesignCircularAxisSpec(
            kind,
            _vector(value["origin_mm"], name="origin", require_nonzero=False),
            _vector(value["direction"], name="direction", require_nonzero=True),
        )
    expected = {"kind", "object_name"}
    if kind == "object":
        if set(value) != expected:
            raise NativeModelError("A Circular Pattern axis object is invalid.")
        subelements: tuple[str, ...] = ()
    elif kind == "subelement":
        if set(value) != expected | {"subelement"}:
            raise NativeModelError("A Circular Pattern axis subelement is invalid.")
        subelement = str(value["subelement"])
        if _EDGE.fullmatch(subelement) is None and _SKETCH_AXIS.fullmatch(subelement) is None:
            raise NativeModelError(
                "A Circular Pattern axis requires an exact sketch axis or EdgeN."
            )
        subelements = (subelement,)
    else:
        raise NativeModelError("A Design Circular Pattern axis is invalid.")
    return DesignCircularAxisSpec(
        kind,
        reference=DesignLinkSpec(
            NativeObjectRef(document_uid, str(value["object_name"])),
            subelements,
        ),
    )


def prepare_design_circular_pattern(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignCircularPatternSpec:
    definition = values.get("definition")
    if (
        not isinstance(definition, Mapping)
        or set(definition)
        != {"kind", "axis", "angle_degrees", "occurrences", "reversed"}
        or definition.get("kind") != "circular"
    ):
        raise NativeModelError("A Design Pattern definition is not Circular Pattern.")
    raw_angle = definition["angle_degrees"]
    raw_occurrences = definition["occurrences"]
    reversed_value = definition["reversed"]
    if isinstance(raw_angle, bool):
        raise NativeModelError("Circular Pattern angle must be a number.")
    try:
        angle = float(raw_angle)
    except (TypeError, ValueError) as exc:
        raise NativeModelError("Circular Pattern angle must be a number.") from exc
    if not math.isfinite(angle) or not 0.0 < angle <= 360.0:
        raise NativeModelError(
            "Circular Pattern angle must be finite, positive, and at most 360 degrees."
        )
    if isinstance(raw_occurrences, bool) or not isinstance(raw_occurrences, int):
        raise NativeModelError("Circular Pattern occurrences must be an integer.")
    if not 2 <= raw_occurrences <= 10000:
        raise NativeModelError("Circular Pattern occurrences must be from 2 to 10000.")
    if not isinstance(reversed_value, bool):
        raise NativeModelError("Circular Pattern reversed must be boolean.")
    return DesignCircularPatternSpec(
        pattern_source_from_mapping(document_uid, values.get("source")),
        _axis_from_mapping(document_uid, definition["axis"]),
        angle,
        raw_occurrences,
        reversed_value,
    )


def _derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "")) == type_id:
        return True
    check = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(check(type_id)) if callable(check) else False
    except Exception:
        return False


def _preflight_axis(document: Any, axis: DesignCircularAxisSpec) -> None:
    if axis.reference is None:
        return
    source = resolve_object(document, axis.reference.object_ref)
    subelements = axis.reference.subelements
    if not subelements:
        if not any(
            _derived(source, type_id)
            for type_id in ("PartDesign::Line", "App::Line", "Part::Part2DObject")
        ):
            raise NativeModelError(
                "A Circular Pattern axis object must be a datum axis or sketch."
            )
        return
    subelement = subelements[0]
    if _SKETCH_AXIS.fullmatch(subelement):
        if not _derived(source, "Part::Part2DObject"):
            raise NativeModelError("A Circular Pattern sketch axis requires an exact sketch.")
        if subelement.startswith("Axis"):
            index = int(subelement[4:])
            if index >= sketch_axis_count(source):
                raise NativeModelError(
                    "The exact Circular Pattern sketch axis no longer exists."
                )
        return
    if not (_derived(source, "Part::Feature") or _derived(source, "PartDesign::Body")):
        raise NativeModelError("A Circular Pattern Edge must belong to a shape feature.")
    shape = getattr(source, "Shape", None)
    try:
        edge = shape.getElement(subelement) if shape is not None else None
    except Exception as exc:
        raise NativeModelError(
            f"Circular Pattern axis {axis.reference.object_ref.object_name}.{subelement} no longer exists."
        ) from exc
    if (
        edge is None
        or str(getattr(edge, "ShapeType", "")) != "Edge"
        or str(getattr(getattr(edge, "Curve", None), "TypeId", ""))
        not in _SUPPORTED_EDGE_CURVES
    ):
        raise NativeModelError(
            "A Circular Pattern axis Edge must be straight or circular."
        )


def preflight_design_circular_pattern(
    document: Any,
    spec: DesignCircularPatternSpec,
) -> None:
    if not isinstance(spec, DesignCircularPatternSpec):
        raise TypeError("spec must be a DesignCircularPatternSpec")
    resolve_pattern_source(document, spec.source)
    _preflight_axis(document, spec.axis)


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


def _configure_axis(
    operation: Any,
    axis: DesignCircularAxisSpec,
) -> dict[str, Any]:
    import FreeCAD as App

    if axis.reference is None:
        operation.AxisReference = None
        operation.AxisOrigin = App.Vector(*axis.origin)
        operation.AxisDirection = App.Vector(*axis.direction)
        return {
            "kind": "explicit",
            "origin_mm": list(axis.origin),
            "direction": list(axis.direction),
            "frame": _placement_signature(operation.AxisReferenceFrame),
        }
    resolved = resolve_definition_link(operation, axis.reference)
    operation.AxisReference = (
        (resolved[0], resolved[1]) if resolved[1] else resolved[0]
    )
    linked_object = operation.AxisReference[0]
    linked_name, linked_subelements = _link_identity(operation.AxisReference)
    if linked_object is None or linked_name is None:
        raise NativeModelError("The Circular Pattern axis reference did not persist.")
    return {
        "kind": axis.kind,
        "reference": {
            "object": object_reference(linked_object),
            "subelements": list(linked_subelements),
        },
        "frame": _placement_signature(operation.AxisReferenceFrame),
    }


def create_design_circular_pattern(
    document: Any,
    *,
    label: str,
    spec: DesignCircularPatternSpec,
) -> NativeMutationDraft:
    import PartDesign

    operation = document.addObject(
        "PartDesign::DesignCircularPattern",
        "DesignCircularPattern",
    )
    if operation is None or operation.TypeId != "PartDesign::DesignCircularPattern":
        raise NativeModelError(
            "The Design Circular Pattern factory returned the wrong type."
        )
    operation.Label = label
    operation.Angle = spec.angle_degrees
    operation.Occurrences = spec.occurrences
    operation.Reversed = spec.reversed
    edit = PartDesign.beginDesignOperationEdit(operation)
    source, targets, result_mode = configure_pattern_source(
        operation,
        edit,
        spec.source,
        generated_copy_count=spec.occurrences - 1,
    )
    axis = _configure_axis(operation, spec.axis)
    document.recompute([operation], True, True)
    if not operation.isValid():
        raise NativeModelError(
            str(operation.getStatusString() or "The Design Circular Pattern is invalid.")
        )
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    expected_count = spec.occurrences - 1 if spec.source.kind == "body" else len(targets)
    if len(outputs) != expected_count:
        raise NativeModelError("The Circular Pattern published an unexpected Body count.")
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
            "axis": axis,
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


def _quantity(value: Any) -> float:
    return float(getattr(value, "Value", value))


def verify_design_circular_pattern(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    import PartDesign

    operation = draft.value["operation"]
    source = draft.value["source"]
    targets = list(draft.value["targets"])
    outputs = list(draft.value["outputs"])
    component = draft.value["component"]
    spec: DesignCircularPatternSpec = draft.value["spec"]
    axis = draft.value["axis"]
    result_mode = draft.value["result_mode"]
    generated = spec.occurrences - 1
    if (
        document.getObject(operation.Name) is not operation
        or operation.TypeId != "PartDesign::DesignCircularPattern"
        or not operation.isValid()
        or int(operation.GeneratedOccurrenceCount) != generated
        or int(operation.Occurrences) != spec.occurrences
        or not math.isclose(
            _quantity(operation.Angle),
            spec.angle_degrees,
            abs_tol=1.0e-7,
        )
        or bool(operation.Reversed) is not spec.reversed
        or operation.BaseFeature is not None
        or operation.ResultOperation != result_mode
        or str(operation.PatternSource)
        != ("Body" if spec.source.kind == "body" else "Feature")
        or any(document.getObject(body.Name) is not body for body in outputs)
    ):
        raise NativeModelError(
            "The Circular Pattern failed its exact operation postcondition."
        )
    output_shapes = list(operation.OutputShapes)
    if len(output_shapes) != len(outputs) or any(
        shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
        for shape in output_shapes
    ):
        raise NativeModelError("The Circular Pattern produced an invalid solid result.")

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
            raise NativeModelError("The Body Circular Pattern changed its output contract.")
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
            raise NativeModelError(
                "The Feature Circular Pattern changed its target contract."
            )

    if axis["kind"] == "explicit":
        if (
            _link_identity(operation.AxisReference) != (None, ())
            or not _vector_close(operation.AxisOrigin, spec.axis.origin)
            or not _vector_close(operation.AxisDirection, spec.axis.direction)
        ):
            raise NativeModelError("The explicit Circular Pattern axis changed.")
    else:
        reference = axis["reference"]
        expected_link = (
            reference["object"]["object_name"],
            tuple(reference["subelements"]),
        )
        if _link_identity(operation.AxisReference) != expected_link:
            raise NativeModelError("The referenced Circular Pattern axis changed.")
    if _placement_signature(operation.AxisReferenceFrame) != tuple(axis["frame"]):
        raise NativeModelError("The Circular Pattern axis frame changed.")

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
            "kind": "circular",
            "axis": {key: value for key, value in axis.items() if key != "frame"},
            "angle_degrees": spec.angle_degrees,
            "occurrences": spec.occurrences,
            "reversed": spec.reversed,
            "generated_occurrence_count": generated,
        },
    }
