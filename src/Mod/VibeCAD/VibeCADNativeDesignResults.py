# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact placement, Body targeting, and postconditions for Design operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_NATIVE_RESULT_MODES = {
    "new_body": "New Body",
    "join": "Join",
    "cut": "Cut",
    "intersect": "Intersect",
    "modify": "Modify",
}
_PROVIDER_RESULT_MODES = frozenset({"new_body", "join", "cut", "intersect"})


@dataclass(frozen=True, slots=True)
class DesignResultSpec:
    mode: str
    target_refs: tuple[NativeObjectRef, ...]
    destination_component_ref: NativeObjectRef | None

    @property
    def native_mode(self) -> str:
        try:
            return _NATIVE_RESULT_MODES[self.mode]
        except KeyError as exc:
            raise NativeModelError("A Design result mode is unavailable.") from exc


def placement_from_mapping(value: Mapping[str, Any]) -> Any:
    import FreeCAD as App

    if not isinstance(value, Mapping) or set(value) != {"origin_mm", "rotation"}:
        raise NativeModelError("A Design placement is invalid.")
    origin = value["origin_mm"]
    rotation = value["rotation"]
    if (
        not isinstance(origin, Mapping)
        or set(origin) != {"x", "y", "z"}
        or not isinstance(rotation, Mapping)
        or set(rotation) != {"axis", "angle_degrees"}
    ):
        raise NativeModelError("A Design placement is invalid.")
    axis = rotation["axis"]
    if not isinstance(axis, Mapping) or set(axis) != {"x", "y", "z"}:
        raise NativeModelError("A Design rotation axis is invalid.")
    axis_values = tuple(float(axis[name]) for name in ("x", "y", "z"))
    origin_values = tuple(float(origin[name]) for name in ("x", "y", "z"))
    angle = float(rotation["angle_degrees"])
    if not all(math.isfinite(number) for number in (*origin_values, *axis_values, angle)):
        raise NativeModelError("A Design placement must contain finite numbers.")
    if math.sqrt(sum(component * component for component in axis_values)) < 1.0e-12:
        raise NativeModelError("A Design rotation axis must be non-zero.")
    return App.Placement(
        App.Vector(*origin_values),
        App.Rotation(App.Vector(*axis_values), angle),
    )


def result_spec_from_mapping(
    document_uid: str,
    value: Mapping[str, Any],
) -> DesignResultSpec:
    if not isinstance(value, Mapping) or set(value) != {
        "mode",
        "targets",
        "destination_component",
    }:
        raise NativeModelError("A Design result definition is invalid.")
    mode = str(value["mode"])
    if mode not in _PROVIDER_RESULT_MODES:
        raise NativeModelError("A Design result mode is unavailable.")
    targets = value["targets"]
    if not isinstance(targets, list) or len(targets) > 16:
        raise NativeModelError("A Design result requires at most 16 exact Bodies.")
    refs = []
    seen = set()
    for target in targets:
        if not isinstance(target, Mapping) or set(target) != {"object_name"}:
            raise NativeModelError("A Design result Body target is invalid.")
        reference = NativeObjectRef(document_uid, str(target["object_name"]))
        if reference.object_name in seen:
            raise NativeModelError("A Design result repeats the same Body target.")
        seen.add(reference.object_name)
        refs.append(reference)
    component_value = value["destination_component"]
    if component_value is None:
        component = None
    elif isinstance(component_value, Mapping) and set(component_value) == {"object_name"}:
        component = NativeObjectRef(document_uid, str(component_value["object_name"]))
    else:
        raise NativeModelError("A Design destination Component is invalid.")
    if mode == "new_body" and refs:
        raise NativeModelError("New Body mode cannot also target existing Bodies.")
    if mode != "new_body" and (not refs or component is not None):
        raise NativeModelError(
            "Join, Cut, and Intersect require exact Bodies and no destination Component."
        )
    return DesignResultSpec(mode, tuple(refs), component)


def resolve_design_result(
    document: Any,
    spec: DesignResultSpec,
) -> tuple[list[Any], Any | None]:
    targets = [
        resolve_object(document, ref, expected_types=("PartDesign::Body",))
        for ref in spec.target_refs
    ]
    component = (
        resolve_object(
            document,
            spec.destination_component_ref,
            expected_types=("PartDesign::Component",),
        )
        if spec.destination_component_ref is not None
        else None
    )
    return targets, component


def create_design_operation(
    document: Any,
    *,
    type_id: str,
    base_name: str,
    label: str,
    result_spec: DesignResultSpec,
    configure: Callable[[Any], Mapping[str, Any]],
    verify_feature: Callable[[Any, Mapping[str, Any]], Mapping[str, Any]],
    configure_after_targets: bool = False,
) -> NativeMutationDraft:
    import PartDesign

    targets, component = resolve_design_result(document, result_spec)
    operation = document.addObject(type_id, base_name)
    if operation is None or operation.TypeId != type_id:
        raise NativeModelError("The Design feature factory returned the wrong object type.")
    operation.Label = label
    edit = PartDesign.beginDesignOperationEdit(operation)
    if not configure_after_targets:
        configuration = configure(operation)
        if not isinstance(configuration, Mapping):
            raise NativeModelError("The Design feature configuration is invalid.")
    PartDesign.setDesignOperationTargets(
        edit,
        result_spec.native_mode,
        targets,
        component,
    )
    if configure_after_targets:
        configuration = configure(operation)
        if not isinstance(configuration, Mapping):
            raise NativeModelError("The Design feature configuration is invalid.")
    document.recompute([operation], True, True)
    if not operation.isValid():
        raise NativeModelError(
            str(operation.getStatusString() or "The Design feature is invalid.")
        )
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    if len(outputs) != (1 if result_spec.mode == "new_body" else len(targets)):
        raise NativeModelError("The Design feature published an unexpected Body count.")
    created = [object_identity(operation)]
    changed = [object_identity(target) for target in targets]
    if result_spec.mode == "new_body":
        created.extend(object_identity(output) for output in outputs)
        if component is not None:
            changed.append(object_identity(component))
    return NativeMutationDraft(
        value={
            "operation": operation,
            "outputs": outputs,
            "targets": targets,
            "component": component,
            "result_spec": result_spec,
            "configuration": dict(configuration),
            "verify_feature": verify_feature,
        },
        recompute_targets=(operation, *outputs),
        created=tuple(created),
        changed=tuple(changed),
    )


def verify_design_operation(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    operation = draft.value["operation"]
    outputs = list(draft.value["outputs"])
    targets = list(draft.value["targets"])
    spec = draft.value["result_spec"]
    if (
        document.getObject(operation.Name) is not operation
        or operation.ResultOperation != spec.native_mode
        or not operation.isValid()
        or any(document.getObject(body.Name) is not body for body in outputs)
    ):
        raise NativeModelError("The Design feature failed its exact output postcondition.")
    if spec.mode == "new_body":
        if len(outputs) != 1 or outputs[0].TypeId != "PartDesign::Body":
            raise NativeModelError("The Design feature did not create one exact Body.")
    elif [body.Name for body in outputs] != [body.Name for body in targets]:
        raise NativeModelError("The Design feature changed its explicit Body targets.")
    PartDesign.validateDesign(operation)
    feature = draft.value["verify_feature"](
        operation,
        draft.value["configuration"],
    )
    if not isinstance(feature, Mapping):
        raise NativeModelError("The Design feature verifier returned invalid evidence.")
    body_results = []
    presence = list(getattr(operation, "OutputPresence", []) or [])
    for index, body in enumerate(outputs):
        present = bool(presence[index]) if index < len(presence) else True
        shape = body.Shape
        body_results.append(
            {
                "body": object_reference(body),
                "present": present,
                "solid_count": len(shape.Solids) if not shape.isNull() else 0,
                "volume_mm3": float(shape.Volume) if not shape.isNull() else 0.0,
            }
        )
    result = {
        "operation": object_reference(operation),
        "result_mode": spec.mode,
        "bodies": body_results,
    }
    if feature:
        result["feature"] = dict(feature)
    return result
