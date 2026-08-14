# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI-independent semantic copy support for CAM operations.

An operation copy is a copy of the operation's complete document-History
closure, not a shallow copy of the visible Path feature.  This module owns the
shared graph rules used by both the human ribbon command and Native assistance;
transaction ownership remains with each caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import FreeCAD

import Path.Base.Util as PathUtil
import Path.Dressup.Utils as PathDressup
import Path.Main.Job as PathJob
import PathScripts.PathUtils as PathUtils
from Path.CommandBoundary import is_timeline_input_usable


@dataclass(frozen=True, slots=True)
class OperationCopyPlan:
    """Frozen, validated source graph for one atomic copy command."""

    document: Any
    selection: tuple[tuple[Any, Any], ...]
    timeline: Any
    timeline_operations: tuple[Any, ...]
    timeline_visibility: tuple[bool, ...]
    timeline_suppression: tuple[bool, ...]
    timeline_position: int
    source_closure: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class CopiedOperationOutput:
    """One selected source and its copied Job-owned output."""

    source: Any
    job: Any
    copied: Any


@dataclass(frozen=True, slots=True)
class OperationCopyResult:
    """Exact graph created by :func:`copyOperations`."""

    plan: OperationCopyPlan
    source_copy_pairs: tuple[tuple[Any, Any], ...]
    outputs: tuple[CopiedOperationOutput, ...]
    timeline_operation: Any
    adoption_order: tuple[Any, ...]
    created: tuple[Any, ...]

    @property
    def copied_source_order(self) -> tuple[Any, ...]:
        return tuple(copied for _source, copied in self.source_copy_pairs)

    @property
    def copied_outputs(self) -> tuple[Any, ...]:
        return tuple(value.copied for value in self.outputs)


def _live_object(document: Any, obj: Any) -> bool:
    try:
        name = str(obj.Name)
        return bool(
            name
            and obj.Document is document
            and document.getObject(name) is obj
        )
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _timeline_state(document: Any) -> tuple[Any, tuple[Any, ...], tuple[bool, ...], tuple[bool, ...], int]:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline":
        raise RuntimeError("The CAM document has no valid operation History")
    try:
        operations = tuple(timeline.Operations or ())
        visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
        suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
        position = int(timeline.Position)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError("The CAM operation History cannot be read") from exc
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        raise RuntimeError("The CAM operation History has inconsistent state")
    names = tuple(str(getattr(operation, "Name", "") or "") for operation in operations)
    if (
        any(not name for name in names)
        or len(set(names)) != len(names)
        or any(not _live_object(document, operation) for operation in operations)
    ):
        raise RuntimeError("The CAM operation History has no exact object chronology")
    return timeline, operations, visibility, suppression, position


def _validated_selection_entry(document: Any, operation: Any, job: Any) -> None:
    try:
        valid_job = isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
        group = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
        timeline_operation = (
            "VibeCADTimelineRole" in tuple(getattr(operation, "PropertiesList", ()) or ())
            and str(operation.VibeCADTimelineRole) == "operation"
        )
        valid_operation = bool(PathDressup.isOp(operation) or timeline_operation)
        parent_job = PathUtils.findParentJob(operation)
    except Exception as exc:
        raise RuntimeError("A selected CAM operation could not be validated") from exc
    if (
        not _live_object(document, operation)
        or not _live_object(document, job)
        or not valid_job
        or getattr(job, "Operations", None) is None
        or operation not in group
        or parent_job is not job
        or not valid_operation
        or not is_timeline_input_usable(job, document)
        or not is_timeline_input_usable(operation, document)
    ):
        raise RuntimeError(
            "A selected CAM operation is not a usable member of its exact Job"
        )


def planOperations(
    document: Any,
    selection: Iterable[tuple[Any, Any]],
) -> OperationCopyPlan:
    """Freeze the complete semantic copy closure for exact operation/Job pairs."""

    if document is None:
        raise RuntimeError("A CAM operation copy requires an active document")
    exact_selection = tuple(selection)
    if not exact_selection:
        raise RuntimeError("A CAM operation copy requires at least one operation")
    if any(not isinstance(value, tuple) or len(value) != 2 for value in exact_selection):
        raise TypeError("selection must contain exact (operation, Job) pairs")
    selected = tuple(operation for operation, _job in exact_selection)
    if len({id(operation) for operation in selected}) != len(selected):
        raise RuntimeError("A CAM operation can only be copied once per command")
    for operation, job in exact_selection:
        _validated_selection_entry(document, operation, job)

    timeline, operations, visibility, suppression, position = _timeline_state(document)
    source_indices = {id(operation): index for index, operation in enumerate(operations)}
    if len(source_indices) != len(operations):
        raise RuntimeError("The CAM source History contains duplicate object identities")
    try:
        source_closure = tuple(document.semanticTimelineCopyClosure(list(selected)))
    except Exception as exc:
        raise RuntimeError("The selected CAM operation closure could not be resolved") from exc
    if not source_closure or len({id(value) for value in source_closure}) != len(source_closure):
        raise RuntimeError("The selected CAM operations have no exact History closure")
    for source in source_closure:
        source_index = source_indices.get(id(source))
        if (
            source_index is None
            or operations[source_index] is not source
            or source_index >= position
            or not _live_object(document, source)
        ):
            raise RuntimeError(
                "The selected CAM operation has an inactive or incomplete source-History closure"
            )
    if any(not any(source is value for value in source_closure) for source in selected):
        raise RuntimeError("A selected CAM output is absent from its History closure")
    for operation in selected:
        proxy_module = str(
            getattr(getattr(operation, "Proxy", None), "__module__", "") or ""
        )
        if "Path.Dressup" not in proxy_module:
            continue
        base = PathDressup.baseOp(operation)
        if (
            base is None
            or base is operation
            or not any(base is value for value in source_closure)
        ):
            raise RuntimeError(
                f"CAM dress-up {operation.Name!r} has no complete base-operation "
                "History closure"
            )

    return OperationCopyPlan(
        document=document,
        selection=exact_selection,
        timeline=timeline,
        timeline_operations=operations,
        timeline_visibility=visibility,
        timeline_suppression=suppression,
        timeline_position=position,
        source_closure=source_closure,
    )


def assertPlanCurrent(plan: OperationCopyPlan) -> None:
    """Reject any graph, History, or Job-membership drift after planning."""

    if not isinstance(plan, OperationCopyPlan):
        raise TypeError("plan must be an OperationCopyPlan")
    document = plan.document
    current = _timeline_state(document)
    if (
        current[0] is not plan.timeline
        or current[1] != plan.timeline_operations
        or current[2] != plan.timeline_visibility
        or current[3] != plan.timeline_suppression
        or current[4] != plan.timeline_position
    ):
        raise RuntimeError("The CAM operation History changed after copy planning")
    for operation, job in plan.selection:
        _validated_selection_entry(document, operation, job)
    try:
        closure = tuple(
            document.semanticTimelineCopyClosure(
                [operation for operation, _job in plan.selection]
            )
        )
    except Exception as exc:
        raise RuntimeError("The CAM operation closure changed after copy planning") from exc
    if len(closure) != len(plan.source_closure) or any(
        current_value is not planned_value
        for current_value, planned_value in zip(closure, plan.source_closure, strict=True)
    ):
        raise RuntimeError("The CAM operation closure changed after copy planning")


def removeCopiedTimelineReplacement(operation: Any) -> None:
    """Make a copied operation source-preserving rather than replacement-owned."""

    property_name = "VibeCADTimelineReplacedInputs"
    if property_name not in tuple(getattr(operation, "PropertiesList", ()) or ()):
        return
    if operation.getTypeIdOfProperty(property_name) != "App::PropertyLinkListHidden":
        raise RuntimeError(f"{operation.Name} has invalid CAM replacement metadata")
    operation.setPropertyStatus(property_name, "-LockDynamic")
    if not operation.removeProperty(property_name):
        raise RuntimeError(f"{operation.Name} retained copied CAM replacement metadata")


def applyCopiedTimelineContract(
    document: Any,
    copied_source_order: Iterable[Any],
    copied_outputs: Iterable[Any],
) -> tuple[Any, tuple[Any, ...]]:
    """Finalize one complete copied semantic closure for document History."""

    copied_objects = tuple(copied_source_order)
    exact_outputs = tuple(copied_outputs)
    if (
        not copied_objects
        or not exact_outputs
        or len({id(value) for value in copied_objects}) != len(copied_objects)
        or len({id(value) for value in exact_outputs}) != len(exact_outputs)
        or any(not any(output is copied for copied in copied_objects) for output in exact_outputs)
        or any(not _live_object(document, copied) for copied in copied_objects)
    ):
        raise RuntimeError("The copied CAM graph has no exact source chronology")

    for copied in copied_objects:
        removeCopiedTimelineReplacement(copied)

    if len(exact_outputs) == 1:
        operation = exact_outputs[0]
        for resource in copied_objects:
            if resource is operation:
                continue
            PathUtil.markTimelineResource(resource, operation)
            if resource.ViewObject:
                resource.ViewObject.Visibility = False
        PathUtil.markTimelineOperation(operation)
        return operation, copied_objects

    controller = PathUtil.createTimelineOperationController(
        document,
        "CAMOperationCopy",
        FreeCAD.Qt.translate("CAM_OperationCopy", "Copied CAM Operations"),
        "Copy CAM operations",
        exact_outputs,
    )
    for resource in copied_objects:
        PathUtil.markTimelineResource(resource, controller)
        if not any(resource is output for output in exact_outputs) and resource.ViewObject:
            resource.ViewObject.Visibility = False
    return controller, (*copied_objects, controller)


def copyOperations(document: Any, plan: OperationCopyPlan) -> OperationCopyResult:
    """Copy a planned CAM closure and publish it at the current History marker.

    The caller must own an open transaction and is responsible for recompute,
    postcondition verification, and commit/abort.
    """

    if not isinstance(plan, OperationCopyPlan) or plan.document is not document:
        raise TypeError("plan must belong to the exact CAM document")
    assertPlanCurrent(plan)
    try:
        copied_order = tuple(
            document.copyObject(
                list(plan.source_closure),
                False,
                False,
                False,
            )
        )
    except Exception as exc:
        raise RuntimeError("The complete CAM History closure could not be copied") from exc
    if (
        len(copied_order) != len(plan.source_closure)
        or len({id(value) for value in copied_order}) != len(copied_order)
        or any(not _live_object(document, copied) for copied in copied_order)
    ):
        raise RuntimeError("The copied CAM History closure is incomplete")
    source_copy_pairs = tuple(zip(plan.source_closure, copied_order, strict=True))

    def copied_for(source: Any) -> Any:
        for exact_source, copied in source_copy_pairs:
            if exact_source is source:
                return copied
        raise RuntimeError("A selected CAM output is absent from its copied History closure")

    source_indices = {
        id(operation): index for index, operation in enumerate(plan.timeline_operations)
    }
    entries = sorted(
        (
            source_indices[id(source)],
            source,
            job,
            copied_for(source),
        )
        for source, job in plan.selection
    )
    outputs = []
    for _index, source, job, copied in entries:
        _validated_selection_entry(document, source, job)
        job.Proxy.addOperation(copied)
        if (
            copied not in tuple(job.Operations.Group or ())
            or PathUtils.findParentJob(copied) is not job
        ):
            raise RuntimeError("The copied operation could not be added to its exact CAM Job")
        outputs.append(CopiedOperationOutput(source=source, job=job, copied=copied))

    timeline_operation, adoption_order = applyCopiedTimelineContract(
        document,
        copied_order,
        [value.copied for value in outputs],
    )
    created = copied_order
    if not any(timeline_operation is value for value in created):
        created = (*created, timeline_operation)
    document.adoptImportedTimelineOperations(list(adoption_order), list(adoption_order))
    return OperationCopyResult(
        plan=plan,
        source_copy_pairs=source_copy_pairs,
        outputs=tuple(outputs),
        timeline_operation=timeline_operation,
        adoption_order=tuple(adoption_order),
        created=tuple(created),
    )


def recomputeAndValidate(document: Any, result: OperationCopyResult) -> None:
    """Recompute and validate every exact object created by a copy result."""

    if not isinstance(result, OperationCopyResult) or result.plan.document is not document:
        raise TypeError("result must belong to the exact CAM document")
    document.recompute()
    if any(
        not _live_object(document, operation) or not operation.isValid()
        for operation in result.created
    ):
        raise RuntimeError("A copied CAM operation graph is invalid")
