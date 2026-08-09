# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Design Separate implementation for the Model ribbon."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    resolve_current_part_element,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_SEPARATE_FIELDS = frozenset({"source", "destination_component"})
_DESIGN_OPERATION_PROPERTIES = frozenset(
    {"ResultOperation", "InputStates", "OutputBodyIds", "OutputFrames"}
)
_VISUAL_PROPERTIES = (
    "ShapeAppearance",
    "LineColor",
    "PointColor",
    "Transparency",
    "DisplayMode",
)


@dataclass(frozen=True, slots=True)
class DesignSeparateSpec:
    source_ref: NativeObjectRef
    destination_component_ref: NativeObjectRef | None


@dataclass(frozen=True, slots=True)
class PreparedDesignSeparate:
    spec: DesignSeparateSpec
    source: Any
    source_element: CurrentPartElement
    source_label: str
    destination_component: Any | None
    destination_component_id: str
    destination_frame: Any | None
    solid_shapes: tuple[Any, ...]
    solid_signatures: tuple[tuple[float, ...], ...]


def _object_ref(document_uid: str, value: Any, *, role: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Separate {role} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def prepare_design_separate(
    document_uid: str,
    value: Mapping[str, Any],
) -> DesignSeparateSpec:
    if not isinstance(value, Mapping) or set(value) != _SEPARATE_FIELDS:
        raise NativeModelError(
            "Separate requires one exact source and an explicit optional destination Component."
        )
    source = _object_ref(document_uid, value["source"], role="source")
    raw_destination = value["destination_component"]
    destination = (
        None
        if raw_destination is None
        else _object_ref(document_uid, raw_destination, role="destination Component")
    )
    return DesignSeparateSpec(source, destination)


def _is_derived(obj: Any, type_id: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_id))
    except Exception:
        return str(getattr(obj, "TypeId", "") or "") == type_id


def _has_group_parent(obj: Any) -> bool:
    for name in ("getParentGeoFeatureGroup", "getParentGroup"):
        getter = getattr(obj, name, None)
        try:
            if callable(getter) and getter() is not None:
                return True
        except Exception:
            return True
    return False


def _shape_signature(shape: Any) -> tuple[float, ...]:
    bounds = shape.BoundBox
    center = shape.CenterOfMass
    return (
        float(shape.Volume),
        float(shape.Area),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
        float(center.x),
        float(center.y),
        float(center.z),
    )


def _signatures_match(
    expected: tuple[tuple[float, ...], ...],
    actual: tuple[tuple[float, ...], ...],
) -> bool:
    if len(expected) != len(actual):
        return False
    remaining = list(actual)
    for wanted in expected:
        match = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if len(candidate) == len(wanted)
                and all(
                    math.isclose(left, right, rel_tol=1.0e-9, abs_tol=1.0e-7)
                    for left, right in zip(wanted, candidate)
                )
            ),
            None,
        )
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


def _solid_geometry_matches(expected: Any, actual: Any) -> bool:
    try:
        if (
            expected is None
            or actual is None
            or expected.isNull()
            or actual.isNull()
            or not expected.isValid()
            or not actual.isValid()
            or len(expected.Solids) != 1
            or len(actual.Solids) != 1
        ):
            return False
        expected_signature = _shape_signature(expected)
        actual_signature = _shape_signature(actual)
        for index in (0, 1, 8, 9, 10):
            if not math.isclose(
                expected_signature[index],
                actual_signature[index],
                rel_tol=1.0e-9,
                abs_tol=1.0e-7,
            ):
                return False
        overlap = expected.common(actual)
        tolerance = max(1.0e-7, abs(float(expected.Volume)) * 1.0e-8)
        return (
            overlap is not None
            and not overlap.isNull()
            and math.isclose(
                float(overlap.Volume),
                float(expected.Volume),
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            and math.isclose(
                float(overlap.Volume),
                float(actual.Volume),
                rel_tol=0.0,
                abs_tol=tolerance,
            )
        )
    except Exception:
        return False


def _solid_sets_match(expected: tuple[Any, ...], actual: tuple[Any, ...]) -> bool:
    if len(expected) != len(actual):
        return False
    remaining = list(actual)
    for wanted in expected:
        match = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if _solid_geometry_matches(wanted, candidate)
            ),
            None,
        )
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


def _component_id(component: Any | None) -> str:
    return str(getattr(component, "ComponentId", "") or "") if component else ""


def _global_placement(obj: Any | None) -> Any | None:
    if obj is None:
        return None
    getter = getattr(obj, "getGlobalPlacement", None)
    try:
        return getter() if callable(getter) else obj.Placement
    except Exception as exc:
        raise NativeModelError("The Separate destination Component has no stable frame.") from exc


def preflight_design_separate(
    document: Any,
    spec: DesignSeparateSpec,
) -> PreparedDesignSeparate:
    import PartGui

    if not isinstance(spec, DesignSeparateSpec):
        raise TypeError("spec must be a DesignSeparateSpec")
    source = resolve_object(document, spec.source_ref)
    if (
        not _is_derived(source, "Part::Feature")
        or _is_derived(source, "App::Link")
        or _is_derived(source, "App::LinkElement")
        or _is_derived(source, "PartDesign::Body")
        or _has_group_parent(source)
        or _DESIGN_OPERATION_PROPERTIES.issubset(
            set(getattr(source, "PropertiesList", ()) or ())
        )
    ):
        raise NativeModelError(
            "Separate requires a reusable Design-root Part definition, not a Body, Link, "
            "grouped feature, or Design operation."
        )
    if not PartGui.isModelingObjectActive(source):
        raise NativeModelError("The Separate source is not active in current History.")
    source_element = resolve_current_part_element(
        document,
        spec.source_ref,
        subelement=None,
        operation="Separate source",
    )
    if source_element.target is not source:
        raise NativeModelError("Separate cannot resolve through a linked modeling presentation.")
    # DesignSeparate evaluates a root definition from its located Shape.  Using
    # Part.getShape(..., transform=True) here would bake the same rigid transform
    # into curved geometry and yield a different OCC representation.
    solids = tuple(source.Shape.Solids)
    if len(solids) < 2:
        raise NativeModelError(
            "Separate requires one reusable definition containing at least two solids."
        )

    destination = None
    if spec.destination_component_ref is not None:
        destination = resolve_object(
            document,
            spec.destination_component_ref,
            expected_types=("App::Part",),
        )
        if not _component_id(destination):
            raise NativeModelError(
                "The Separate destination must be a VibeCAD Component."
            )
    return PreparedDesignSeparate(
        spec=spec,
        source=source,
        source_element=source_element,
        source_label=str(source.Label),
        destination_component=destination,
        destination_component_id=_component_id(destination),
        destination_frame=_global_placement(destination),
        solid_shapes=solids,
        solid_signatures=tuple(_shape_signature(solid) for solid in solids),
    )


def _prepared_is_exact(document: Any, prepared: PreparedDesignSeparate) -> bool:
    source = prepared.source
    destination = prepared.destination_component
    return (
        document.getObject(source.Name) is source
        and str(source.Label) == prepared.source_label
        and current_part_element_is_exact(document, prepared.source_element)
        and (
            destination is None
            or (
                document.getObject(destination.Name) is destination
                and _component_id(destination) == prepared.destination_component_id
                and _global_placement(destination) == prepared.destination_frame
            )
        )
    )


def _copy_visual_properties(source: Any, output: Any) -> dict[str, Any]:
    source_view = getattr(source, "ViewObject", None)
    output_view = getattr(output, "ViewObject", None)
    if source_view is None or output_view is None:
        return {}
    expected = {}
    for name in _VISUAL_PROPERTIES:
        if not hasattr(source_view, name) or not hasattr(output_view, name):
            continue
        try:
            value = getattr(source_view, name)
            setattr(output_view, name, value)
            expected[name] = value
        except Exception as exc:
            raise NativeModelError(
                "Separate could not preserve the source appearance on every output Body."
            ) from exc
    return expected


def _visual_value(value: Any) -> Any:
    if all(
        hasattr(value, name)
        for name in (
            "AmbientColor",
            "DiffuseColor",
            "EmissiveColor",
            "SpecularColor",
            "Shininess",
            "Transparency",
        )
    ):
        colors = tuple(
            tuple(float(component) for component in getattr(value, name))
            for name in (
                "AmbientColor",
                "DiffuseColor",
                "EmissiveColor",
                "SpecularColor",
            )
        )
        return (*colors, float(value.Shininess), float(value.Transparency))
    if isinstance(value, (list, tuple)):
        return tuple(_visual_value(item) for item in value)
    if isinstance(value, float):
        return float(value)
    return value


def _same_visual_value(left: Any, right: Any) -> bool:
    left_value = _visual_value(left)
    right_value = _visual_value(right)

    def same(first: Any, second: Any) -> bool:
        if isinstance(first, tuple) and isinstance(second, tuple):
            return len(first) == len(second) and all(
                same(a, b) for a, b in zip(first, second)
            )
        if isinstance(first, float) or isinstance(second, float):
            try:
                return math.isclose(
                    float(first),
                    float(second),
                    rel_tol=1.0e-7,
                    abs_tol=1.0e-7,
                )
            except (TypeError, ValueError):
                return False
        return first == second

    return same(left_value, right_value)


def create_design_separate(
    document: Any,
    *,
    label: str,
    prepared: PreparedDesignSeparate,
) -> NativeMutationDraft:
    import PartDesign
    import PartGui

    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("The exact Separate source or destination changed after preflight.")
    source = prepared.source
    destination = prepared.destination_component
    if "VibeCADDefinitionId" not in set(source.PropertiesList):
        PartDesign.finalizeDesignDefinition(source)

    operation = document.addObject("PartDesign::DesignSeparate", "Separate")
    if operation is None or operation.TypeId != "PartDesign::DesignSeparate":
        raise NativeModelError("The Design Separate factory returned the wrong object type.")
    operation.Label = label
    edit = PartDesign.beginDesignOperationEdit(operation)
    if destination is None:
        PartDesign.setDesignSeparateDefinition(edit, source)
    else:
        PartDesign.setDesignSeparateDefinition(edit, source, destination)
    if not PartGui.setModelingReplacedInputs(operation, (source,)):
        raise NativeModelError("Separate could not retain its replaced source definition.")
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    if len(outputs) != len(prepared.solid_signatures) or any(
        output is None for output in outputs
    ):
        raise NativeModelError("Separate did not publish one Body per source solid.")

    appearances = []
    for index, output in enumerate(outputs, 1):
        output.Label = f"{prepared.source_label} {index}"
        if hasattr(output, "ShapeMaterial") and hasattr(source, "ShapeMaterial"):
            output.ShapeMaterial = source.ShapeMaterial
        appearances.append(_copy_visual_properties(source, output))
    source.Visibility = False

    changed = tuple(
        object_identity(item) for item in (destination,) if item is not None
    )
    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "operation": operation,
            "outputs": tuple(outputs),
            "appearances": tuple(appearances),
            "body_ids": tuple(str(output.VibeCADBodyId) for output in outputs),
        },
        recompute_targets=(operation, *outputs),
        created=(object_identity(operation), *(object_identity(item) for item in outputs)),
        changed=changed,
        replaced=(object_identity(source),),
    )


def verify_design_separate(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    prepared: PreparedDesignSeparate = draft.value["prepared"]
    operation = draft.value["operation"]
    outputs = tuple(draft.value["outputs"])
    source = prepared.source
    destination = prepared.destination_component
    body_ids = tuple(draft.value["body_ids"])
    if (
        document.getObject(operation.Name) is not operation
        or operation.TypeId != "PartDesign::DesignSeparate"
        or str(operation.Label) != draft.value["label"]
        or operation.Source is not source
        or str(operation.ResultOperation) != "New Bodies"
        or operation.getParentGeoFeatureGroup() is not None
        or not operation.isValid()
        or not operation.Shape.isNull()
    ):
        raise NativeModelError("The Design Separate operation failed its exact postcondition.")
    if (
        tuple(operation.InputStates)
        or tuple(operation.InputBodyIds)
        or tuple(operation.InputFrames)
        or tuple(int(value) for value in operation.OutputPreviousInputIndices)
        != tuple(-1 for _item in outputs)
        or tuple(str(value) for value in operation.OutputBodyIds) != body_ids
        or len(set(body_ids)) != len(body_ids)
        or any(not value for value in body_ids)
        or len(tuple(operation.RegionWitnesses)) != len(outputs)
        or len(tuple(operation.OutputFrames)) != len(outputs)
        or tuple(str(value) for value in operation.OutputComponentIds)
        != tuple(prepared.destination_component_id for _item in outputs)
        or not all(bool(value) for value in operation.OutputPresence)
    ):
        raise NativeModelError("The Design Separate result identity ports are inconsistent.")
    if (
        str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(operation, "VibeCADTimelineOwner", None) is not None
        or tuple(getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ()) != (source,)
        or bool(source.Visibility)
        or not str(getattr(source, "VibeCADDefinitionId", "") or "")
    ):
        raise NativeModelError("The Design Separate History publication is inconsistent.")
    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("Separate changed its reusable source definition.")

    body_signatures = []
    bodies = []
    for index, (output, appearance) in enumerate(
        zip(outputs, draft.value["appearances"]),
        1,
    ):
        shape = output.Shape
        state = getattr(getattr(output, "Tip", None), "CurrentState", None)
        if (
            document.getObject(output.Name) is not output
            or output.TypeId != "PartDesign::Body"
            or str(output.VibeCADBodyId) != body_ids[index - 1]
            or str(output.Label) != f"{prepared.source_label} {index}"
            or output.getParentGeoFeatureGroup() is not destination
            or str(getattr(output, "ComponentId", "") or "")
            != prepared.destination_component_id
            or shape.isNull()
            or not shape.isValid()
            or len(shape.Solids) != 1
            or state is None
            or state.Operation is not operation
        ):
            raise NativeModelError("A separated output Body failed its exact postcondition.")
        if hasattr(output, "ShapeMaterial") and hasattr(source, "ShapeMaterial"):
            if str(output.ShapeMaterial.UUID) != str(source.ShapeMaterial.UUID):
                raise NativeModelError("Separate did not preserve the source material.")
        output_view = getattr(output, "ViewObject", None)
        if output_view is not None:
            for name, value in appearance.items():
                if not _same_visual_value(getattr(output_view, name), value):
                    raise NativeModelError(
                        f"Separate did not preserve the source {name} appearance."
                    )
        body_signatures.append(_shape_signature(shape))
        bodies.append(
            {
                "body": object_reference(output),
                "volume_mm3": float(shape.Volume),
            }
        )
    output_shape_signatures = tuple(
        _shape_signature(shape) for shape in tuple(operation.OutputShapes)
    )
    preview_solids = tuple(operation.PreviewShape.Solids)
    if not _signatures_match(output_shape_signatures, tuple(body_signatures)):
        raise NativeModelError("Separate Bodies differ from their exact Design outputs.")
    if not _solid_sets_match(prepared.solid_shapes, preview_solids):
        raise NativeModelError("Separate output geometry differs from the source solids.")

    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if source not in operations or operation not in operations or operations.index(source) >= operations.index(operation):
        raise NativeModelError("Separate is not ordered after its reusable source in History.")
    PartDesign.validateDesign(operation)
    result = {
        "operation": object_reference(operation),
        "source": object_reference(source),
        "body_count": len(outputs),
        "bodies": bodies,
    }
    if destination is not None:
        result["destination_component"] = object_reference(destination)
    return result
