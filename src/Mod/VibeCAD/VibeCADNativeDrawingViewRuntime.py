# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for responsive exact Drawing views."""

from __future__ import annotations

from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeDrawingBroken import (
    capture_broken_view_commit_state,
    create_broken_view,
    prepare_broken_view_create,
    validate_prepared_broken_view,
    verify_broken_view_create,
)
from VibeCADNativeDrawingBrokenInput import (
    create_broken_workspace,
    materialize_broken_snapshot,
)
from VibeCADNativeDrawingBrokenWorker import execute_broken_projection
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import (
    DrawingProjectionFit,
    DrawingProjectionJob,
    DrawingProjectionSource,
    freeze_projection_batch,
)
from VibeCADNativeDrawingProjectionGroup import (
    capture_projection_group_commit_state,
    create_projection_group,
    prepare_projection_group_create,
    projection_group_jobs,
    validate_prepared_projection_group,
    verify_projection_group_create,
)
from VibeCADNativeDrawingProjectionWorker import (
    execute_projection_batch,
    projection_snapshot,
)
from VibeCADNativeDrawingView import (
    capture_standard_view_commit_state,
    create_standard_view,
    prepare_standard_view_create,
    standard_view_line_flags,
    validate_prepared_standard_view,
    verify_standard_view_create,
)
from VibeCADNativeDrawingViewState import DRAWING_VIEW_ORIENTATIONS
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


def _projection_fit_bounds(
    page_geometry: Mapping[str, Any],
) -> tuple[float, float, float, float]:
    drawing_bounds = page_geometry.get("drawing_bounds_mm")
    if not isinstance(drawing_bounds, Mapping):
        drawing_bounds = {
            "min_x_mm": 0.0,
            "min_y_mm": 0.0,
            "max_x_mm": page_geometry["width_mm"],
            "max_y_mm": page_geometry["height_mm"],
        }
    bounds = tuple(
        float(drawing_bounds[name])
        for name in ("min_x_mm", "min_y_mm", "max_x_mm", "max_y_mm")
    )
    clearance = float(page_geometry.get("drawing_clearance_mm") or 0.0)
    if any(not math.isfinite(value) for value in (*bounds, clearance)):
        raise ValueError("Drawing projection fit bounds must be finite")
    if clearance < 0.0:
        raise ValueError("Drawing projection clearance must be non-negative")
    inset = (
        bounds[0] + clearance,
        bounds[1] + clearance,
        bounds[2] - clearance,
        bounds[3] - clearance,
    )
    if inset[0] >= inset[2] or inset[1] >= inset[3]:
        raise ValueError("Drawing projection fit bounds have no usable area")
    return inset


class NativeDrawingViewRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        normalized = dict(arguments)
        if normalized.get("operation") != "create_projection_group":
            normalized.setdefault("scale", "page")
        operation, values = strict_variant_arguments(
            normalized,
            {
                "create_standard_view": frozenset(
                    {
                        "label",
                        "page",
                        "sources",
                        "orientation",
                        "position",
                        "scale",
                        "line_style",
                    }
                ),
                "create_projection_group": frozenset(
                    {
                        "label",
                        "page",
                        "sources",
                        "front_orientation",
                        "views",
                        "convention",
                        "line_style",
                    }
                ),
                "create_broken_view": frozenset(
                    {
                        "label",
                        "page",
                        "sources",
                        "breaks",
                        "gap_mm",
                        "orientation",
                        "position",
                        "scale",
                        "line_style",
                    }
                ),
            },
        )
        if operation == "create_broken_view":
            return self._create_broken(values, ticket=ticket)
        if operation == "create_projection_group":
            return self._create_projection_group(values, ticket=ticket)
        return self._create_standard(values, ticket=ticket)

    def _create_projection_group(
        self,
        values: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError(
                "Projection group creation requires one exact Native call ticket"
            )
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive projection groups are unavailable in this session.",
                error_code=(
                    "NATIVE_DRAWING_PROJECTION_GROUP_BACKGROUND_UNAVAILABLE"
                ),
            )
        prepared = prepare_projection_group_create(context.document, values=values)
        jobs = tuple(
            DrawingProjectionJob(
                key=str(value["key"]),
                sources=tuple(
                    DrawingProjectionSource(
                        object_name=str(source["object_name"]),
                        state_sha256=str(source["state_sha256"]),
                        source=source["source"],
                    )
                    for source in value["sources"]
                ),
                direction=tuple(value["direction"]),
                x_direction=tuple(value["x_direction"]),
                scale=float(value["scale"]),
                line_flags=dict(value["line_flags"]),
            )
            for value in projection_group_jobs(prepared)
        )
        page_geometry = prepared.standard.page_state_before.get("template_geometry")
        if not isinstance(page_geometry, Mapping):
            raise NativeDrawingError(
                "The exact Drawing page has no usable paper bounds.",
                error_code="NATIVE_DRAWING_PROJECTION_GROUP_PAGE_INVALID",
            )
        try:
            fit = DrawingProjectionFit(
                views=prepared.group.views,
                convention=prepared.group.convention,
                page_width_mm=float(page_geometry["width_mm"]),
                page_height_mm=float(page_geometry["height_mm"]),
                drawable_bounds_mm=_projection_fit_bounds(page_geometry),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NativeDrawingError(
                "The exact Drawing page has no usable paper bounds.",
                error_code="NATIVE_DRAWING_PROJECTION_GROUP_PAGE_INVALID",
            ) from exc
        frozen = freeze_projection_batch(jobs, fit=fit)

        def prepare(cancelled: Any, progress: Any) -> Any:
            return execute_projection_batch(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_prepared_projection_group(context.document, prepared)

        def commit(worker_result: Any) -> Mapping[str, Any]:
            commit_prepared = capture_projection_group_commit_state(
                context.document,
                prepared,
            )
            snapshots = {
                job.key: projection_snapshot(worker_result.projection(job.key))
                for job in jobs
            }
            if worker_result.layout is None:
                raise NativeDrawingError(
                    "The detached projection set has no verified page layout.",
                    error_code="NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
                )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native Drawing Projection Group",
                mutate=partial(
                    create_projection_group,
                    prepared=commit_prepared,
                    projection_snapshots=snapshots,
                    layout=worker_result.layout,
                ),
                verify=verify_projection_group_create,
            )

        try:
            background = manager.submit(
                document_uid=context.document_uid,
                capability_name="drawing.projection_group",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Adopting coordinated Drawing projections",
                cleanup=lambda _worker_result: frozen.cleanup(),
            )
        except NativeBackgroundError as exc:
            frozen.cleanup()
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_PROJECTION_GROUP_QUEUE_FAILED",
            ) from exc
        except Exception:
            frozen.cleanup()
            raise
        return {
            "job": _job_summary(background),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(background.job_id),
            },
        }

    def _create_standard(
        self,
        values: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("Drawing view creation requires one exact Native call ticket")
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive Drawing projection is unavailable in this session.",
                error_code="NATIVE_DRAWING_PROJECTION_BACKGROUND_UNAVAILABLE",
            )
        prepared = prepare_standard_view_create(context.document, values=values)
        direction, x_direction = DRAWING_VIEW_ORIENTATIONS[
            prepared.spec.orientation
        ]
        effective_scale = (
            float(prepared.page_state_before["scale"])
            if prepared.spec.scale is None
            else float(prepared.spec.scale)
        )
        frozen = freeze_projection_batch(
            (
                DrawingProjectionJob(
                    key="standard_view",
                    sources=tuple(
                        DrawingProjectionSource(
                            object_name=str(source.Name),
                            state_sha256=str(state["state_sha256"]),
                            source=source,
                        )
                        for source, state in zip(
                            prepared.sources,
                            prepared.source_states,
                            strict=True,
                        )
                    ),
                    direction=direction,
                    x_direction=x_direction,
                    scale=effective_scale,
                    line_flags=standard_view_line_flags(prepared.spec.line_style),
                ),
            )
        )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return execute_projection_batch(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_prepared_standard_view(context.document, prepared)

        def commit(worker_result: Any) -> Mapping[str, Any]:
            commit_prepared = capture_standard_view_commit_state(
                context.document,
                prepared,
            )
            snapshot = projection_snapshot(
                worker_result.projection("standard_view")
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native Standard Drawing View",
                mutate=partial(
                    create_standard_view,
                    prepared=commit_prepared,
                    projection_snapshot=snapshot,
                ),
                verify=verify_standard_view_create,
            )

        try:
            background = manager.submit(
                document_uid=context.document_uid,
                capability_name="drawing.standard_view",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Adopting exact Drawing projection",
                cleanup=lambda _worker_result: frozen.cleanup(),
            )
        except NativeBackgroundError as exc:
            frozen.cleanup()
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_PROJECTION_QUEUE_FAILED",
            ) from exc
        except Exception:
            frozen.cleanup()
            raise
        return {
            "job": _job_summary(background),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(background.job_id),
            },
        }

    def _create_broken(
        self,
        values: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("Broken Drawing view creation requires one exact Native call ticket")
        current_revision = context.state.current_revision(context.document_uid)
        if current_revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current_revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeDrawingError(
                "Responsive broken-view projection is unavailable in this session.",
                error_code="NATIVE_DRAWING_BROKEN_BACKGROUND_UNAVAILABLE",
            )
        prepared = prepare_broken_view_create(context.document, values=values)
        workspace = create_broken_workspace()

        def prepare(cancelled: Any, progress: Any) -> Any:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(5, "Freezing exact Drawing document")
            frozen = dispatcher(
                lambda: materialize_broken_snapshot(
                    context.document,
                    prepared,
                    workspace,
                )
            )
            if cancelled():
                raise NativeBackgroundCancelled()
            return execute_broken_projection(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            revision = context.state.current_revision(context.document_uid)
            if revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, revision)
            validate_prepared_broken_view(context.document, prepared)

        def commit(worker_result: Any) -> Mapping[str, Any]:
            commit_prepared = capture_broken_view_commit_state(
                context.document,
                prepared,
            )
            snapshot = projection_snapshot(worker_result.projection)
            worker_breaks = tuple(
                {
                    "object_name": item.object_name,
                    "kind": item.kind,
                    "removed_length_mm": item.removed_length_mm,
                }
                for item in worker_result.breaks
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native Broken Drawing View",
                mutate=partial(
                    create_broken_view,
                    prepared=commit_prepared,
                    projection_snapshot=snapshot,
                    worker_breaks=worker_breaks,
                ),
                verify=verify_broken_view_create,
            )

        try:
            background = manager.submit(
                document_uid=context.document_uid,
                capability_name="drawing.broken_view",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Adopting exact broken-view projection",
                cleanup=lambda _worker_result: workspace.cleanup(),
            )
        except NativeBackgroundError as exc:
            workspace.cleanup()
            raise NativeDrawingError(
                str(exc),
                error_code="NATIVE_DRAWING_BROKEN_QUEUE_FAILED",
            ) from exc
        except Exception:
            workspace.cleanup()
            raise
        return {
            "job": _job_summary(background),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(background.job_id),
            },
        }
