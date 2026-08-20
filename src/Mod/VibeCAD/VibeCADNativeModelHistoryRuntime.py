# SPDX-License-Identifier: LGPL-2.1-or-later

"""Dependency-safe lifecycle operations for the global Model History."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import (
    NativeCallTicket,
    NativeRevisionConflict,
    NativeStateError,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_MAX_TARGETS = 16
_RESOURCE_ROLES = frozenset({"internal", "resource"})
_DELETE_PREVIEW_FIELDS = ("stage", "preview_id")


def _timeline_role(obj: Any) -> str:
    return str(getattr(obj, "VibeCADTimelineRole", "") or "").strip()


def _timeline(document: Any) -> Any | None:
    matches = [
        obj
        for obj in list(getattr(document, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "") == "App::DocumentTimeline"
    ]
    if len(matches) > 1:
        raise NativeModelError("The document has more than one History controller.")
    return matches[0] if matches else None


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    controller = _timeline(document)
    return tuple(getattr(controller, "Operations", []) or []) if controller else ()


def _is_design_operation(obj: Any) -> bool:
    return bool(
        _timeline_role(obj) == "operation"
        and hasattr(obj, "OperationId")
        and hasattr(obj, "InputStates")
        and hasattr(obj, "OutputBodyIds")
    )


def _managed_source(obj: Any) -> tuple[str, str] | None:
    source_id = str(
        getattr(obj, "VibeCADVibeScriptProgramId", "")
        or getattr(obj, "VibeCADScriptedModelId", "")
        or ""
    ).strip()
    if not source_id and str(getattr(obj, "TypeId", "") or "") == (
        "PartDesign::DesignScriptOperation"
    ):
        return "source-owned-operation", "vibescript.partdesign"
    if not source_id:
        return None
    return source_id, str(getattr(obj, "VibeCADVibeScriptDomain", "") or "")


def _status(obj: Any) -> list[str]:
    try:
        values = [str(value) for value in list(obj.State or []) if str(value)]
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        values = []
    return values[:8]


def _shape_summary(obj: Any) -> dict[str, Any]:
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return {}
    try:
        return {
            "valid": bool(shape.isValid()),
            "solids": len(shape.Solids),
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
        }
    except (AttributeError, ReferenceError, RuntimeError):
        return {}


def _validate_design(document: Any) -> bool | None:
    anchors = [
        obj
        for obj in list(getattr(document, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body"
        or _is_design_operation(obj)
    ]
    if not anchors:
        return None
    try:
        import PartDesign

        PartDesign.validateDesign(anchors[0])
    except Exception as exc:
        raise NativeModelError(f"The Design graph is invalid: {exc}") from exc
    return True


def _document_objects(document: Any) -> tuple[Any, ...]:
    return tuple(list(getattr(document, "Objects", []) or []))


def _identity_map(document: Any) -> dict[str, Any]:
    return {
        str(getattr(obj, "Name", "") or ""): object_identity(obj)
        for obj in _document_objects(document)
        if str(getattr(obj, "Name", "") or "")
    }


def _is_infrastructure_consumer(obj: Any) -> bool:
    return bool(
        str(getattr(obj, "TypeId", "") or "") == "App::DocumentTimeline"
        or _timeline_role(obj) in _RESOURCE_ROLES
    )


def _parent(obj: Any) -> Any | None:
    for name in ("getParentGroup", "getParentGeoFeatureGroup"):
        getter = getattr(obj, name, None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if value is not None:
            return value
    return None


def _delete_order(document: Any, targets: tuple[Any, ...]) -> tuple[Any, ...]:
    positions = {
        id(obj): index for index, obj in enumerate(_timeline_operations(document))
    }
    requested = {id(obj) for obj in targets}
    remaining = list(targets)
    ordered: list[Any] = []
    while remaining:
        ready = [
            obj
            for obj in remaining
            if not any(
                id(consumer) in requested and consumer in remaining
                for consumer in list(getattr(obj, "InList", []) or [])
            )
        ]
        if not ready:
            raise NativeModelError(
                "The requested deletion targets contain a dependency cycle."
            )
        ready.sort(
            key=lambda obj: (
                positions.get(id(obj), -1),
                str(getattr(obj, "Name", "") or ""),
            ),
            reverse=True,
        )
        for obj in ready:
            ordered.append(obj)
            remaining.remove(obj)
    return tuple(ordered)


def _preflight_delete(document: Any, targets: tuple[Any, ...]) -> tuple[Any, ...]:
    operations = set(_timeline_operations(document))
    requested = {id(obj) for obj in targets}
    for obj in targets:
        managed = _managed_source(obj)
        if managed is not None:
            source_id, domain = managed
            raise NativeModelError(
                f"'{obj.Name}' is owned by VibeScript source '{source_id}'"
                f"{f' ({domain})' if domain else ''}; delete it through the source lifecycle."
            )
        role = _timeline_role(obj)
        if str(getattr(obj, "TypeId", "") or "") in {
            "App::Part",
            "PartDesign::Body",
            "PartDesign::Component",
        }:
            raise NativeModelError(
                f"'{obj.Name}' is a physical container. Delete its contributing History "
                "operations before removing the container manually."
            )
        if role in _RESOURCE_ROLES:
            raise NativeModelError(
                f"'{obj.Name}' is an internal History resource; target its owning operation."
            )
        if _is_design_operation(obj):
            continue
        if obj not in operations and role != "operation":
            parent = _parent(obj)
            if parent is not None:
                raise NativeModelError(
                    f"'{obj.Name}' is owned by '{parent.Name}'; delete the owning History "
                    "operation instead."
                )
        blockers = [
            consumer
            for consumer in list(getattr(obj, "InList", []) or [])
            if id(consumer) not in requested
            and not _is_infrastructure_consumer(consumer)
        ]
        if blockers:
            details = ", ".join(
                f"{consumer.Name} ({consumer.TypeId})" for consumer in blockers[:8]
            )
            raise NativeModelError(
                f"'{obj.Name}' is still used by {details}. Delete those dependents in the "
                "same call or retain this feature."
            )
    return _delete_order(document, targets)


def _delete_features(document: Any, targets: tuple[Any, ...]) -> NativeMutationDraft:
    import PartDesign

    ordered = _preflight_delete(document, targets)
    requested_names = tuple(str(obj.Name) for obj in targets)
    ordered_names = tuple(str(obj.Name) for obj in ordered)
    before = _identity_map(document)
    removed_bodies: list[str] = []
    for target in ordered:
        name = str(target.Name)
        current = document.getObject(name)
        if current is None:
            continue
        if _is_design_operation(current):
            try:
                removed_bodies.extend(
                    str(value) for value in list(PartDesign.removeDesignOperation(current) or [])
                )
            except Exception as exc:
                raise NativeModelError(
                    f"History operation '{name}' cannot be deleted safely: {exc}"
                ) from exc
        else:
            document.removeObject(name)
    try:
        document.recompute()
    except Exception as exc:
        raise NativeModelError(f"The document failed to recompute after deletion: {exc}") from exc
    design_valid = _validate_design(document)
    after_names = {
        str(getattr(obj, "Name", "") or "") for obj in _document_objects(document)
    }
    deleted = tuple(before[name] for name in before if name not in after_names)
    return NativeMutationDraft(
        value={
            "requested": requested_names,
            "deletion_order": ordered_names,
            "removed_bodies": tuple(dict.fromkeys(removed_bodies)),
            "design_graph_valid": design_valid,
        },
        deleted=deleted,
    )


def _verify_delete(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    survivors = [
        name for name in draft.value["requested"] if document.getObject(name) is not None
    ]
    if survivors:
        raise NativeModelError(
            "FreeCAD retained requested deletion targets: " + ", ".join(survivors)
        )
    result = {
        "deleted_targets": list(draft.value["requested"]),
        "deletion_order": list(draft.value["deletion_order"]),
        "deleted_object_count": len(draft.deleted),
        "removed_bodies": list(draft.value["removed_bodies"]),
        "body_tip_policy": "stable_body_result",
    }
    if draft.value["design_graph_valid"] is not None:
        result["design_graph_valid"] = True
    return result


def _require_history_end(document: Any) -> Any:
    controller = _timeline(document)
    if controller is None:
        raise NativeModelError("The document has no Model History.")
    operations = tuple(getattr(controller, "Operations", []) or [])
    position = int(getattr(controller, "Position", -1))
    if position != len(operations):
        raise NativeModelError(
            f"History is rolled back to position {position} of {len(operations)}. Move to "
            "the end of History before changing persistent suppression."
        )
    return controller


def _suppression_target(document: Any, target: Any) -> tuple[Any, tuple[Any, ...], int]:
    controller = _require_history_end(document)
    operations = tuple(getattr(controller, "Operations", []) or [])
    if target not in operations or _timeline_role(target) != "operation":
        raise NativeModelError(f"'{target.Name}' is not a global Model History operation.")
    if _managed_source(target) is not None:
        raise NativeModelError(
            f"'{target.Name}' is source-owned; change suppression through its source lifecycle."
        )
    if not hasattr(target, "Suppressed"):
        raise NativeModelError(
            f"'{target.Name}' ({target.TypeId}) is not suppressible."
        )
    return controller, operations, operations.index(target)


def _set_suppressed(
    document: Any,
    target: Any,
    suppressed: bool,
) -> NativeMutationDraft:
    controller, operations, index = _suppression_target(document, target)
    target.Suppressed = bool(suppressed)
    try:
        document.recompute()
    except Exception as exc:
        raise NativeModelError(
            f"History operation '{target.Name}' failed to recompute: {exc}"
        ) from exc
    baseline = list(getattr(controller, "SuppressionAtEnd", []) or [])
    if len(baseline) != len(operations) or bool(baseline[index]) is not bool(suppressed):
        raise NativeModelError(
            f"History did not retain the requested suppression state for '{target.Name}'."
        )
    design_valid = _validate_design(document)
    return NativeMutationDraft(
        value={
            "target": target,
            "suppressed": bool(suppressed),
            "history_position": index,
            "design_graph_valid": design_valid,
        },
        changed=(object_identity(target),),
    )


def _verify_suppressed(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    target = draft.value["target"]
    expected = bool(draft.value["suppressed"])
    if (
        document.getObject(str(target.Name)) is not target
        or bool(target.Suppressed) is not expected
    ):
        raise NativeModelError("The History operation lost its requested suppression state.")
    result = {
        "operation": object_reference(target),
        "suppressed": expected,
        "history_position": int(draft.value["history_position"]),
        "status": _status(target),
        "body_tip_policy": "stable_body_result",
    }
    if draft.value["design_graph_valid"] is not None:
        result["design_graph_valid"] = True
    return result


def _recompute_validate(document: Any, targets: tuple[Any, ...]) -> NativeMutationDraft:
    for target in targets:
        role = _timeline_role(target)
        is_body = str(getattr(target, "TypeId", "") or "") == "PartDesign::Body"
        if role in _RESOURCE_ROLES and not is_body:
            raise NativeModelError(
                f"'{target.Name}' is an internal History resource; target its Body or operation."
            )
        if not is_body and role != "operation":
            raise NativeModelError(
                f"'{target.Name}' is neither a physical Body nor a History operation."
            )
    try:
        result = document.recompute(list(targets), True, True)
    except Exception as exc:
        raise NativeModelError(f"The requested model targets failed to recompute: {exc}") from exc
    failed = [
        target
        for target in targets
        if "Invalid" in _status(target) or "Error" in _status(target)
    ]
    if result is False or failed:
        details = "; ".join(
            f"{target.Name}: {', '.join(_status(target)) or 'invalid'}" for target in failed
        )
        raise NativeModelError(
            "The requested model targets remain invalid after recompute"
            + (f": {details}" if details else ".")
        )
    design_valid = _validate_design(document)
    return NativeMutationDraft(
        value={
            "targets": targets,
            "design_graph_valid": design_valid,
        },
        changed=tuple(object_identity(target) for target in targets),
    )


def _verify_recompute(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    summaries = []
    for target in draft.value["targets"]:
        if document.getObject(str(target.Name)) is not target:
            raise NativeModelError("A recomputed target left the exact document.")
        summary = {
            "object": object_reference(target),
            "status": _status(target),
        }
        shape = _shape_summary(target)
        if shape:
            summary["shape"] = shape
        if str(getattr(target, "TypeId", "") or "") == "PartDesign::Body":
            tip = getattr(target, "Tip", None)
            summary["tip"] = object_reference(tip) if tip is not None else None
            summary["tip_policy"] = "stable_body_result"
        summaries.append(summary)
    result = {
        "targets": summaries,
        "body_tip_policy": "stable_body_result",
    }
    if draft.value["design_graph_valid"] is not None:
        result["design_graph_valid"] = True
    return result


class NativeModelHistoryRuntime:
    """Execute exact History lifecycle controls from one frozen Model turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _targets(self, values: Any) -> tuple[Any, ...]:
        if not isinstance(values, list) or not 1 <= len(values) <= _MAX_TARGETS:
            raise NativeModelError("Model History control requires 1 to 16 exact targets.")
        targets = []
        names = set()
        for value in values:
            if not isinstance(value, Mapping) or set(value) != {"object_name"}:
                raise NativeModelError("A Model History target is invalid.")
            name = str(value.get("object_name") or "")
            if name in names:
                raise NativeModelError(f"Model History target '{name}' is repeated.")
            names.add(name)
            targets.append(
                resolve_object(
                    self._context.document,
                    NativeObjectRef(self._context.document_uid, name),
                )
            )
        return tuple(targets)

    def _maybe_preview_delete_features(
        self, values: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        stage = str(values.get("stage") or "propose").strip()
        if stage == "apply":
            return None
        if stage != "propose":
            raise NativeModelError(
                "model.history delete_features stage must be propose or apply."
            )
        return self._context.state.propose_mutation_preview(
            self._context.document_uid,
            capability_name="model.history",
            arguments={"operation": "delete_features", **dict(values)},
        )

    def _delete_apply_values(self, values: Mapping[str, Any]) -> dict[str, Any]:
        stage = str(values.get("stage") or "propose").strip()
        if stage != "apply":
            return {
                name: value
                for name, value in values.items()
                if name not in {"stage", "preview_id"}
            }
        preview_id = str(values.get("preview_id") or "").strip()
        if not preview_id:
            raise NativeModelError(
                "model.history delete_features apply needs preview_id."
            )
        try:
            stored = self._context.state.consume_mutation_preview(
                self._context.document_uid,
                preview_id,
                capability_name="model.history",
            )
        except NativeRevisionConflict:
            raise
        except NativeStateError as exc:
            raise NativeModelError(str(exc)) from exc
        if str(stored.get("operation") or "") != "delete_features":
            raise NativeModelError("preview_id is not a delete_features preview.")
        return {
            name: value
            for name, value in stored.items()
            if name not in {"stage", "preview_id", "operation"}
        }

    def control_history(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        raw = dict(arguments)
        preview_fields = {}
        if str(raw.get("operation") or "") == "delete_features":
            for name in _DELETE_PREVIEW_FIELDS:
                if name in raw:
                    preview_fields[name] = raw.pop(name)
        operation, values = strict_variant_arguments(
            raw,
            {
                "delete_features": frozenset({"targets"}),
                "set_suppressed": frozenset({"target", "suppressed"}),
            },
        )
        if preview_fields:
            values = {**values, **preview_fields}
        if operation == "delete_features":
            previewed = self._maybe_preview_delete_features(values)
            if previewed is not None:
                return previewed
            values = self._delete_apply_values(values)
        self._context.guard()
        if operation == "set_suppressed":
            target = self._targets([values["target"]])[0]
            requested = bool(values["suppressed"])
            _controller, _operations, position = _suppression_target(
                self._context.document,
                target,
            )
            if bool(target.Suppressed) is requested:
                design_valid = _validate_design(self._context.document)
                result = {
                    "operation": object_reference(target),
                    "suppressed": requested,
                    "changed": False,
                    "assistant_undo_available": False,
                    "history_position": position,
                    "status": _status(target),
                    "body_tip_policy": "stable_body_result",
                }
                if design_valid is not None:
                    result["design_graph_valid"] = True
                return result
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name=(
                    "Suppress Native Model Operation"
                    if requested
                    else "Unsuppress Native Model Operation"
                ),
                mutate=lambda document: _set_suppressed(document, target, requested),
                verify=_verify_suppressed,
            )
        targets = self._targets(values["targets"])
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Delete Native Model Features",
            mutate=lambda document: _delete_features(document, targets),
            verify=_verify_delete,
        )

    def recompute_model(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"recompute_validate": frozenset({"targets"})},
        )
        self._context.guard()
        targets = self._targets(values["targets"])
        return _verify_recompute(
            self._context.document,
            _recompute_validate(self._context.document, targets),
        )
