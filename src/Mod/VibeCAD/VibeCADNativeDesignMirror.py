# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, execution, and verification for current Design Mirror."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeDesignBodies import is_valid_solid_shape
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
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_FACE = re.compile(r"^Face[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class DesignMirrorPlaneSpec:
    kind: str
    origin: tuple[float, float, float] | None = None
    normal: tuple[float, float, float] | None = None
    reference: DesignLinkSpec | None = None


@dataclass(frozen=True, slots=True)
class DesignMirrorSpec:
    source: DesignPatternSourceSpec
    plane: DesignMirrorPlaneSpec


def _vector(
    value: Any,
    *,
    name: str,
    require_nonzero: bool,
) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeModelError(f"A Design Mirror {name} vector is invalid.")
    raw = tuple(value[axis] for axis in ("x", "y", "z"))
    if any(isinstance(number, bool) for number in raw):
        raise NativeModelError(f"A Design Mirror {name} vector must contain numbers.")
    try:
        result = tuple(float(number) for number in raw)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(
            f"A Design Mirror {name} vector must contain numbers."
        ) from exc
    if not all(math.isfinite(number) for number in result):
        raise NativeModelError(
            f"A Design Mirror {name} vector must contain finite numbers."
        )
    if require_nonzero and math.sqrt(sum(number * number for number in result)) < 1.0e-12:
        raise NativeModelError("A Design Mirror plane normal must be non-zero.")
    return result


def _plane_from_mapping(
    document_uid: str,
    value: Any,
) -> DesignMirrorPlaneSpec:
    if not isinstance(value, Mapping) or "kind" not in value:
        raise NativeModelError("A Design Mirror plane is invalid.")
    kind = str(value["kind"])
    if kind == "explicit":
        if set(value) != {"kind", "origin_mm", "normal"}:
            raise NativeModelError("An explicit Design Mirror plane is invalid.")
        return DesignMirrorPlaneSpec(
            kind,
            _vector(value["origin_mm"], name="origin", require_nonzero=False),
            _vector(value["normal"], name="normal", require_nonzero=True),
        )
    expected = {"kind", "object_name"}
    if kind == "object":
        if set(value) != expected:
            raise NativeModelError("A Design Mirror plane object is invalid.")
        subelements: tuple[str, ...] = ()
    elif kind == "subelement":
        if set(value) != expected | {"subelement"}:
            raise NativeModelError("A Design Mirror plane subelement is invalid.")
        subelement = str(value["subelement"])
        if subelement != "N_Axis" and _FACE.fullmatch(subelement) is None:
            raise NativeModelError(
                "A Design Mirror plane requires an exact planar FaceN or sketch N_Axis."
            )
        subelements = (subelement,)
    else:
        raise NativeModelError("A Design Mirror plane is invalid.")
    return DesignMirrorPlaneSpec(
        kind,
        reference=DesignLinkSpec(
            NativeObjectRef(document_uid, str(value["object_name"])),
            subelements,
        ),
    )


def prepare_design_mirror(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignMirrorSpec:
    definition = values.get("definition")
    if (
        not isinstance(definition, Mapping)
        or set(definition) != {"kind", "plane"}
        or definition.get("kind") != "mirror"
    ):
        raise NativeModelError("A Design Pattern definition is not Mirror.")
    return DesignMirrorSpec(
        pattern_source_from_mapping(document_uid, values.get("source")),
        _plane_from_mapping(document_uid, definition["plane"]),
    )


def _derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "")) == type_id:
        return True
    check = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(check(type_id)) if callable(check) else False
    except Exception:
        return False


def _preflight_plane(document: Any, plane: DesignMirrorPlaneSpec) -> Any | None:
    if plane.reference is None:
        return None
    source = resolve_object(document, plane.reference.object_ref)
    subelements = plane.reference.subelements
    if not subelements:
        if not any(
            _derived(source, type_id)
            for type_id in ("PartDesign::Plane", "App::Plane", "Part::Part2DObject")
        ):
            raise NativeModelError(
                "A Design Mirror plane object must be a datum plane or sketch plane."
            )
        return source
    subelement = subelements[0]
    if subelement == "N_Axis":
        if not _derived(source, "Part::Part2DObject"):
            raise NativeModelError("Design Mirror N_Axis requires an exact sketch.")
        return source
    if not (_derived(source, "Part::Feature") or _derived(source, "PartDesign::Body")):
        raise NativeModelError("A Design Mirror Face must belong to a shape feature.")
    shape = getattr(source, "Shape", None)
    try:
        face = shape.getElement(subelement) if shape is not None else None
    except Exception as exc:
        raise NativeModelError(
            f"Design Mirror plane {plane.reference.object_ref.object_name}.{subelement} no longer exists."
        ) from exc
    if (
        face is None
        or str(getattr(face, "ShapeType", "")) != "Face"
        or str(getattr(getattr(face, "Surface", None), "TypeId", ""))
        != "Part::GeomPlane"
    ):
        raise NativeModelError("A Design Mirror Face reference must be planar.")
    return source


def preflight_design_mirror(document: Any, spec: DesignMirrorSpec) -> None:
    if not isinstance(spec, DesignMirrorSpec):
        raise TypeError("spec must be a DesignMirrorSpec")
    resolve_pattern_source(document, spec.source)
    _preflight_plane(document, spec.plane)


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
    if isinstance(value, tuple):
        obj = value[0] if value else None
        raw = value[1] if len(value) > 1 else ()
    else:
        obj = value
        raw = ()
    return (
        str(getattr(obj, "Name", "")) or None,
        tuple(str(item) for item in list(raw or ()) if str(item)),
    )


def _configure_plane(operation: Any, plane: DesignMirrorPlaneSpec) -> dict[str, Any]:
    import FreeCAD as App

    if plane.reference is None:
        operation.PlaneReference = None
        operation.PlaneOrigin = App.Vector(*plane.origin)
        operation.PlaneNormal = App.Vector(*plane.normal)
        return {
            "kind": "explicit",
            "origin_mm": list(plane.origin),
            "normal": list(plane.normal),
            "frame": _placement_signature(operation.PlaneReferenceFrame),
        }
    resolved = resolve_definition_link(operation, plane.reference)
    operation.PlaneReference = (
        (resolved[0], resolved[1]) if resolved[1] else resolved[0]
    )
    linked_object = operation.PlaneReference[0]
    linked_name, linked_subelements = _link_identity(operation.PlaneReference)
    if linked_object is None or linked_name is None:
        raise NativeModelError("The Design Mirror plane reference did not persist.")
    return {
        "kind": plane.kind,
        "reference": {
            "object": object_reference(linked_object),
            "subelements": list(linked_subelements),
        },
        "frame": _placement_signature(operation.PlaneReferenceFrame),
    }


def create_design_mirror(
    document: Any,
    *,
    label: str,
    spec: DesignMirrorSpec,
) -> NativeMutationDraft:
    import PartDesign

    operation = document.addObject("PartDesign::DesignMirror", "DesignMirror")
    if operation is None or operation.TypeId != "PartDesign::DesignMirror":
        raise NativeModelError("The Design Mirror factory returned the wrong object type.")
    operation.Label = label
    edit = PartDesign.beginDesignOperationEdit(operation)
    source, targets, result_mode = configure_pattern_source(
        operation,
        edit,
        spec.source,
        generated_copy_count=1,
    )
    plane = _configure_plane(operation, spec.plane)
    document.recompute([operation], True, True)
    if not operation.isValid():
        raise NativeModelError(
            str(operation.getStatusString() or "The Design Mirror is invalid.")
        )
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    expected_count = 1 if spec.source.kind == "body" else len(targets)
    if len(outputs) != expected_count:
        raise NativeModelError("The Design Mirror published an unexpected Body count.")
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
            "plane": plane,
        },
        recompute_targets=(operation, *outputs),
        created=tuple(created),
        changed=tuple(changed),
    )


def _vectors_close(actual: Any, expected: tuple[float, float, float]) -> bool:
    return all(
        math.isclose(float(component), wanted, rel_tol=1.0e-9, abs_tol=1.0e-7)
        for component, wanted in zip((actual.x, actual.y, actual.z), expected)
    )


def verify_design_mirror(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    operation = draft.value["operation"]
    source = draft.value["source"]
    targets = list(draft.value["targets"])
    outputs = list(draft.value["outputs"])
    component = draft.value["component"]
    spec: DesignMirrorSpec = draft.value["spec"]
    plane = draft.value["plane"]
    if (
        document.getObject(operation.Name) is not operation
        or operation.TypeId != "PartDesign::DesignMirror"
        or not operation.isValid()
        or int(operation.GeneratedOccurrenceCount) != 1
        or operation.BaseFeature is not None
        or operation.ResultOperation != draft.value["result_mode"]
        or str(operation.PatternSource) != ("Body" if spec.source.kind == "body" else "Feature")
        or any(document.getObject(body.Name) is not body for body in outputs)
    ):
        raise NativeModelError("The Design Mirror failed its exact operation postcondition.")

    output_shapes = list(operation.OutputShapes)
    if len(output_shapes) != len(outputs) or any(
        not is_valid_solid_shape(shape) for shape in output_shapes
    ):
        raise NativeModelError("The Design Mirror produced an invalid solid result.")
    if any(body.Shape.isNull() or not body.Shape.isValid() for body in outputs):
        raise NativeModelError("A Design Mirror output Body is invalid.")

    if spec.source.kind == "body":
        source_id = str(source.VibeCADBodyId)
        output_id = str(outputs[0].VibeCADBodyId)
        if (
            operation.SourceOperation is not None
            or list(operation.InputBodyIds) != [source_id]
            or len(list(operation.InputStates)) != 1
            or list(operation.OutputBodyIds) != [output_id]
            or list(operation.OutputPreviousInputIndices) != [-1]
            or output_id == source_id
            or outputs[0] is source
            or outputs[0].getParentGeoFeatureGroup() is not component
        ):
            raise NativeModelError("The Body Mirror changed its independent output contract.")
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
            raise NativeModelError("The Feature Mirror changed its exact target contract.")

    if plane["kind"] == "explicit":
        if (
            _link_identity(operation.PlaneReference) != (None, ())
            or not _vectors_close(operation.PlaneOrigin, spec.plane.origin)
            or not _vectors_close(operation.PlaneNormal, spec.plane.normal)
        ):
            raise NativeModelError("The explicit Design Mirror plane changed before commit.")
    else:
        reference = plane["reference"]
        expected_link = (
            reference["object"]["object_name"],
            tuple(reference["subelements"]),
        )
        actual_link = _link_identity(operation.PlaneReference)
        if actual_link != expected_link:
            raise NativeModelError("The referenced Design Mirror plane changed before commit.")
    if _placement_signature(operation.PlaneReferenceFrame) != tuple(plane["frame"]):
        raise NativeModelError("The Design Mirror plane frame changed before commit.")

    PartDesign.validateDesign(operation)
    result = {
        "operation": object_reference(operation),
        "source": pattern_source_summary(spec.source, source, targets),
        "result_mode": (
            "new_body" if draft.value["result_mode"] == "New Bodies"
            else draft.value["result_mode"].lower()
        ),
        "bodies": [
            {
                "body": object_reference(body),
                "solid_count": len(body.Shape.Solids),
                "volume_mm3": float(body.Shape.Volume),
            }
            for body in outputs
        ],
        "definition": {
            "kind": "mirror",
            "plane": {key: value for key, value in plane.items() if key != "frame"},
            "occurrence_count": 1,
        },
    }
    return result
