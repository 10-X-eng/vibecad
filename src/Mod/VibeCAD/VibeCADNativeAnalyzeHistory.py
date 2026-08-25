# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact document and History boundaries for Native Analyze mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live


@dataclass(frozen=True, slots=True)
class AnalyzeCreationBoundary:
    objects: tuple[Any, ...]
    timeline: Any | None
    operations: tuple[Any, ...]


def creation_boundary(document: Any) -> AnalyzeCreationBoundary:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    return AnalyzeCreationBoundary(tuple(document.Objects), timeline, operations)


def require_boundary(document: Any, boundary: AnalyzeCreationBoundary) -> None:
    if not isinstance(boundary, AnalyzeCreationBoundary):
        raise TypeError("boundary must be an AnalyzeCreationBoundary")
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        tuple(document.Objects) != boundary.objects
        or timeline is not boundary.timeline
        or operations != boundary.operations
    ):
        raise NativeAnalyzeError(
            "The document or History changed after Analyze preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )


def publish_operation(
    document: Any,
    boundary: AnalyzeCreationBoundary,
    operation: Any,
    resources: tuple[Any, ...] = (),
    replaced_inputs: tuple[Any, ...] = (),
) -> None:
    if not is_live(document, operation):
        raise NativeAnalyzeError("The new FEM operation is not live in its document.")
    if len(resources) != len(set(resources)) or operation in resources:
        raise NativeAnalyzeError("A FEM History block contains duplicate object identities.")
    if any(not is_live(document, resource) for resource in resources):
        raise NativeAnalyzeError("A new FEM History resource is no longer live.")
    if (
        len(replaced_inputs) != len(set(replaced_inputs))
        or operation in replaced_inputs
        or any(not is_live(document, value) for value in replaced_inputs)
    ):
        raise NativeAnalyzeError("A FEM replacement contains invalid input identities.")
    try:
        from femcommands import manager

        manager._mark_timeline_operation(operation)
        for resource in resources:
            manager._mark_timeline_resource(resource, operation)
        if replaced_inputs:
            manager._mark_timeline_replaced_inputs(operation, replaced_inputs)
        document.publishProvisionalTimelineOperationBlock(
            operation,
            resources,
            tuple(operation for _resource in resources),
        )
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM History block could not be published: {exc}",
            error_code="NATIVE_ANALYZE_HISTORY_PUBLICATION_FAILED",
        ) from exc
    verify_operation_block(
        document,
        boundary,
        operation,
        resources,
        replaced_inputs,
    )


def verify_operation_block(
    document: Any,
    boundary: AnalyzeCreationBoundary,
    operation: Any,
    resources: tuple[Any, ...] = (),
    replaced_inputs: tuple[Any, ...] = (),
) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if timeline is None or (
        boundary.timeline is not None and timeline is not boundary.timeline
    ):
        raise NativeAnalyzeError("The FEM operation changed the document History identity.")
    expected = (*boundary.operations, *resources, operation)
    if operations != expected:
        raise NativeAnalyzeError(
            "The FEM operation was not published as the exact final History block."
        )
    if (
        str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(operation, "VibeCADTimelineOwner", None) is not None
        or tuple(getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ())
        != replaced_inputs
    ):
        raise NativeAnalyzeError("The FEM History root has invalid role metadata.")
    for resource in resources:
        if (
            str(getattr(resource, "VibeCADTimelineRole", "") or "") != "resource"
            or getattr(resource, "VibeCADTimelineOwner", None) is not operation
        ):
            raise NativeAnalyzeError("A FEM History resource has invalid owner metadata.")


def _semantic_root(candidate: Any, document: Any) -> Any | None:
    current = candidate
    visited: set[int] = set()
    while is_live(document, current):
        identity = int(getattr(current, "ID", -1))
        if identity in visited:
            return None
        visited.add(identity)
        if str(getattr(current, "VibeCADTimelineRole", "") or "") != "resource":
            return current
        current = getattr(current, "VibeCADTimelineOwner", None)
    return None


def stage_operation_resource_reconciliation(
    document: Any,
    boundary: AnalyzeCreationBoundary,
    owner: Any,
) -> tuple[Any, ...]:
    require_boundary(document, boundary)
    root = _semantic_root(owner, document)
    if owner not in boundary.operations or root not in boundary.operations:
        raise NativeAnalyzeError(
            "The owner does not resolve to one exact root operation in current History."
        )
    resources = tuple(
        candidate
        for candidate in boundary.operations
        if candidate is not root and _semantic_root(candidate, document) is root
    )
    direct_roots = tuple(
        resource
        for resource in resources
        if getattr(resource, "VibeCADTimelineOwner", None) is root
    )
    try:
        document.stageTimelineOperationResourceReconciliation(root, direct_roots)
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The operation resource graph could not be staged: {exc}",
            error_code="NATIVE_ANALYZE_HISTORY_RECONCILIATION_FAILED",
        ) from exc
    return resources


def finalize_new_operation_resource(
    document: Any,
    boundary: AnalyzeCreationBoundary,
    owner: Any,
    old_resources: tuple[Any, ...],
    resource: Any,
) -> None:
    if not is_live(document, resource) or resource in old_resources or resource is owner:
        raise NativeAnalyzeError("The new material resource identity is invalid.")
    root = _semantic_root(owner, document)
    if root not in boundary.operations:
        raise NativeAnalyzeError(
            "The resource owner no longer resolves to its exact History root."
        )
    try:
        from femcommands import manager

        manager._mark_timeline_resource(resource, owner)
        insertion_index = (
            old_resources.index(owner) if owner in old_resources else len(old_resources)
        )
        final_resources = (
            *old_resources[:insertion_index],
            resource,
            *old_resources[insertion_index:],
        )
        source_indices = [
            index if index < insertion_index else index - 1
            for index in range(len(final_resources))
        ]
        source_indices[insertion_index] = -1
        old_positions = [
            index if index < insertion_index else index + 1
            for index in range(len(old_resources))
        ]
        document.finalizeProvisionalTimelineOperationResourceReconciliation(
            root,
            final_resources,
            source_indices,
            old_positions,
        )
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The operation resource graph could not be finalized: {exc}",
            error_code="NATIVE_ANALYZE_HISTORY_RECONCILIATION_FAILED",
        ) from exc
    verify_new_operation_resource(
        document,
        boundary,
        owner,
        old_resources,
        resource,
    )


def verify_new_operation_resource(
    document: Any,
    boundary: AnalyzeCreationBoundary,
    owner: Any,
    old_resources: tuple[Any, ...],
    resource: Any,
) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    root = _semantic_root(owner, document)
    if (
        timeline is not boundary.timeline
        or owner not in boundary.operations
        or root not in boundary.operations
    ):
        raise NativeAnalyzeError("The operation resource changed History identity.")
    owner_index = boundary.operations.index(owner)
    expected = (
        *boundary.operations[:owner_index],
        resource,
        *boundary.operations[owner_index:],
    )
    if operations != expected:
        raise NativeAnalyzeError(
            "The new material resource is not in its exact canonical History block."
        )
    insertion_index = (
        old_resources.index(owner) if owner in old_resources else len(old_resources)
    )
    expected_resources = (
        *old_resources[:insertion_index],
        resource,
        *old_resources[insertion_index:],
    )
    if tuple(
        candidate
        for candidate in operations
        if candidate is not root and _semantic_root(candidate, document) is root
    ) != expected_resources:
        raise NativeAnalyzeError("The operation resource graph changed unexpectedly.")
    if (
        str(getattr(resource, "VibeCADTimelineRole", "") or "") != "resource"
        or getattr(resource, "VibeCADTimelineOwner", None) is not owner
    ):
        raise NativeAnalyzeError("The new material resource has invalid owner metadata.")
