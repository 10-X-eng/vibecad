# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Component, Body, and Design Clone mutation algorithms."""

from __future__ import annotations

from typing import Any

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


def _classify_structure(document: Any, obj: Any) -> None:
    classify = getattr(document, "classifyProvisionalTimelineInternalObject", None)
    if not callable(classify):
        raise NativeModelError("The document cannot classify Design structure.")
    classify(obj)


def create_component(
    document: Any,
    *,
    label: str,
    parent_ref: NativeObjectRef | None,
) -> NativeMutationDraft:
    parent = (
        resolve_object(
            document,
            parent_ref,
            expected_types=("PartDesign::Component",),
        )
        if parent_ref is not None
        else None
    )
    component = document.addObject("PartDesign::Component", "Component")
    if component is None or str(getattr(component, "TypeId", "")) != "PartDesign::Component":
        raise NativeModelError("The Component factory returned the wrong object type.")
    _classify_structure(document, component)
    component.Label = label
    if parent is not None:
        parent.addObject(component)
    changed = (object_identity(parent),) if parent is not None else ()
    return NativeMutationDraft(
        value={"component": component, "parent": parent},
        recompute_targets=tuple(value for value in (component, parent) if value is not None),
        created=(object_identity(component),),
        changed=changed,
    )


def verify_component(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    component = draft.value["component"]
    parent = draft.value["parent"]
    if (
        document.getObject(component.Name) is not component
        or component.TypeId != "PartDesign::Component"
        or (parent is not None and component not in list(parent.Group))
        or (parent is None and component.getParentGeoFeatureGroup() is not None)
        or not str(getattr(component, "ComponentId", "") or "")
        or not str(getattr(component, "DesignId", "") or "")
    ):
        raise NativeModelError("The new Component failed its Design structure postcondition.")
    result: dict[str, Any] = {"component": object_reference(component)}
    if parent is not None:
        result["parent_component"] = object_reference(parent)
    return result


def create_body(
    document: Any,
    *,
    label: str,
    component_ref: NativeObjectRef | None,
) -> NativeMutationDraft:
    component = (
        resolve_object(
            document,
            component_ref,
            expected_types=("PartDesign::Component",),
        )
        if component_ref is not None
        else None
    )
    body = document.addObject("PartDesign::Body", "Body")
    if body is None or str(getattr(body, "TypeId", "")) != "PartDesign::Body":
        raise NativeModelError("The Body factory returned the wrong object type.")
    _classify_structure(document, body)
    body.Label = label
    if component is not None:
        component.addObject(body)
    changed = (object_identity(component),) if component is not None else ()
    return NativeMutationDraft(
        value={"body": body, "component": component},
        recompute_targets=tuple(value for value in (body, component) if value is not None),
        created=(object_identity(body),),
        changed=changed,
    )


def verify_body(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    body = draft.value["body"]
    component = draft.value["component"]
    body_component_id = str(getattr(body, "ComponentId", "") or "")
    expected_component_id = (
        str(getattr(component, "ComponentId", "") or "") if component is not None else ""
    )
    if (
        document.getObject(body.Name) is not body
        or body.TypeId != "PartDesign::Body"
        or list(body.Group)
        or getattr(body, "Tip", None) is not None
        or (component is not None and body not in list(component.Group))
        or (component is None and body.getParentGeoFeatureGroup() is not None)
        or body_component_id != expected_component_id
        or not str(getattr(body, "VibeCADBodyId", "") or "")
        or not str(getattr(body, "DesignId", "") or "")
    ):
        raise NativeModelError("The new Body failed its empty Design structure postcondition.")
    result: dict[str, Any] = {
        "body": object_reference(body),
        "empty": True,
        "allow_compound": bool(body.AllowCompound),
    }
    if component is not None:
        result["component"] = object_reference(component)
    return result


def create_design_clone(
    document: Any,
    *,
    source_ref: NativeObjectRef,
    label: str,
    output_body_label: str,
) -> NativeMutationDraft:
    import PartDesign

    source = resolve_object(
        document,
        source_ref,
        expected_types=("PartDesign::Body",),
    )
    source_shape = getattr(source, "Shape", None)
    if source_shape is None or source_shape.isNull() or not source_shape.isValid():
        raise NativeModelError("The exact source Body has no valid current History shape.")
    operation = document.addObject("PartDesign::DesignClone", "Clone")
    if operation is None or operation.TypeId != "PartDesign::DesignClone":
        raise NativeModelError("The Design Clone factory returned the wrong object type.")
    operation.Label = label
    edit = PartDesign.beginDesignOperationEdit(operation)
    PartDesign.setDesignCloneSource(edit, source)
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    if len(outputs) != 1 or outputs[0] is None:
        raise NativeModelError("The Design Clone did not publish exactly one Body.")
    output = outputs[0]
    output.Label = output_body_label
    if hasattr(output, "ShapeMaterial") and hasattr(source, "ShapeMaterial"):
        output.ShapeMaterial = source.ShapeMaterial
    component = output.getParentGeoFeatureGroup()
    changed = (
        (object_identity(component),)
        if component is not None and component.TypeId == "PartDesign::Component"
        else ()
    )
    return NativeMutationDraft(
        value={
            "operation": operation,
            "source": source,
            "output": output,
            "component": component,
        },
        recompute_targets=(operation, output),
        created=(object_identity(operation), object_identity(output)),
        changed=changed,
    )


def _bounds(shape: Any) -> dict[str, float]:
    box = shape.BoundBox
    return {
        name: float(getattr(box, name))
        for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
    }


def verify_design_clone(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    operation = draft.value["operation"]
    source = draft.value["source"]
    output = draft.value["output"]
    if (
        document.getObject(operation.Name) is not operation
        or document.getObject(output.Name) is not output
        or operation.TypeId != "PartDesign::DesignClone"
        or output.TypeId != "PartDesign::Body"
        or operation.ResultOperation != "New Bodies"
        or len(list(operation.InputStates)) != 1
        or output.Shape.isNull()
        or not output.Shape.isValid()
        or str(output.VibeCADBodyId) == str(source.VibeCADBodyId)
        or output.Placement != source.Placement
    ):
        raise NativeModelError("The Design Clone failed its exact output postcondition.")
    PartDesign.validateDesign(operation)
    source_bounds = _bounds(source.Shape)
    output_bounds = _bounds(output.Shape)
    if any(abs(source_bounds[key] - output_bounds[key]) > 1.0e-7 for key in source_bounds):
        raise NativeModelError("The Design Clone output bounds differ from its source state.")
    return {
        "operation": object_reference(operation),
        "source_body": object_reference(source),
        "output_body": object_reference(output),
        "output_volume_mm3": float(output.Shape.Volume),
    }
