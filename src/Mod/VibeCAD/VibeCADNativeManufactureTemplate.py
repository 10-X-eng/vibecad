# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, human-authorized CAM Job template serialization and output."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    job_state,
    resolve_job_target,
    resolve_tool_controller_target,
)
from VibeCADNativeOutput import (
    NativeOutputAuthorization,
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict
from VibeCADNativeTargets import read_current_selection


MAX_TEMPLATE_BYTES = 16 * 1024 * 1024
MAX_TEMPLATE_CONTROLLERS = 32
MAX_TEMPLATE_OPERATION_SETTINGS = 64
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(frozen=True, slots=True)
class TemplateContent:
    description: str
    include_postprocessing: bool
    controller_targets: tuple[Mapping[str, Any], ...]
    stock_kind: str
    stock_extent: bool
    stock_placement: bool
    setup_tool_rapids: bool
    setup_coolant: bool
    setup_operation_heights: bool
    setup_operation_depths: bool
    setup_operation_settings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemplateDocumentState:
    objects: tuple[Any, ...] = field(repr=False)
    object_states: tuple[tuple[Any, tuple[str, ...]], ...] = field(repr=False)
    timeline: Any = field(repr=False)
    timeline_operations: tuple[Any, ...] = field(repr=False)
    timeline_visibility: tuple[bool, ...]
    timeline_suppression: tuple[bool, ...]
    timeline_position: int
    selection: Any = field(repr=False)
    visibility: tuple[tuple[Any, bool], ...] = field(repr=False)
    undo_count: int
    redo_count: int
    transaction_id: int
    gui_modified: bool | None


@dataclass(frozen=True, slots=True)
class PreparedTemplateOutput:
    job: Any = field(repr=False)
    job_target: Mapping[str, Any]
    job_before: Mapping[str, Any]
    content: TemplateContent
    controllers: tuple[Any, ...] = field(repr=False)
    controller_states: tuple[Mapping[str, Any], ...]
    encoded_attributes: Mapping[str, Any] = field(repr=False)
    serialized: bytes = field(repr=False)
    template_sha256: str
    document_before: TemplateDocumentState
    output_request: NativeOutputRequest


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID", **repair: Any) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair or None)


def _transaction_id(document: Any) -> int:
    reader = getattr(document, "getBookedTransactionID", None)
    return int(reader() or 0) if callable(reader) else 0


def _transaction_open(document: Any) -> bool:
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or _transaction_id(document) != 0
    )


def _gui_modified(document: Any) -> bool | None:
    try:
        import FreeCADGui as Gui

        gui_document = Gui.getDocument(str(document.Name))
        return None if gui_document is None else bool(gui_document.Modified)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _object_state(obj: Any) -> tuple[str, ...]:
    try:
        return tuple(str(value) for value in obj.State)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()


def _document_state(document: Any) -> TemplateDocumentState:
    objects = tuple(document.Objects)
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline":
        _error(
            "CAM template export requires a valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility_at_end = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression_at_end = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility_at_end)
        or len(operations) != len(suppression_at_end)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "CAM template export found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    presentation = []
    for obj in objects:
        view = getattr(obj, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            presentation.append((obj, bool(view.Visibility)))
    return TemplateDocumentState(
        objects=objects,
        object_states=tuple((obj, _object_state(obj)) for obj in objects),
        timeline=timeline,
        timeline_operations=operations,
        timeline_visibility=visibility_at_end,
        timeline_suppression=suppression_at_end,
        timeline_position=position,
        selection=read_current_selection(document),
        visibility=tuple(presentation),
        undo_count=int(getattr(document, "UndoCount", 0) or 0),
        redo_count=int(getattr(document, "RedoCount", 0) or 0),
        transaction_id=_transaction_id(document),
        gui_modified=_gui_modified(document),
    )


def _document_matches(document: Any, before: TemplateDocumentState) -> bool:
    try:
        return bool(
            tuple(document.Objects) == before.objects
            and all(_object_state(obj) == state for obj, state in before.object_states)
            and document.getObject("VibeCADTimeline") is before.timeline
            and tuple(before.timeline.Operations) == before.timeline_operations
            and tuple(bool(value) for value in before.timeline.VisibilityAtEnd)
            == before.timeline_visibility
            and tuple(bool(value) for value in before.timeline.SuppressionAtEnd)
            == before.timeline_suppression
            and int(before.timeline.Position) == before.timeline_position
            and read_current_selection(document) == before.selection
            and all(
                bool(obj.ViewObject.Visibility) == visible
                for obj, visible in before.visibility
            )
            and int(getattr(document, "UndoCount", 0) or 0) == before.undo_count
            and int(getattr(document, "RedoCount", 0) or 0) == before.redo_count
            and _transaction_id(document) == before.transaction_id
            and not _transaction_open(document)
            and _gui_modified(document) == before.gui_modified
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _content(values: Mapping[str, Any]) -> TemplateContent:
    stock = values["stock"]
    setup = values["setup_sheet"]
    if not isinstance(stock, Mapping) or not isinstance(setup, Mapping):
        _error("CAM template stock and setup_sheet must be structured objects.")
    stock_kind = str(stock.get("kind") or "")
    if stock_kind == "exclude" and set(stock) == {"kind"}:
        stock_extent = False
        stock_placement = False
    elif stock_kind == "include" and set(stock) == {
        "kind",
        "extent",
        "placement",
    }:
        stock_extent = bool(stock["extent"])
        stock_placement = bool(stock["placement"])
    else:
        _error("stock must be exact exclude or include content.")
    required_setup = {
        "tool_rapids",
        "coolant",
        "operation_heights",
        "operation_depths",
        "operation_settings",
    }
    if set(setup) != required_setup or not isinstance(
        setup["operation_settings"], list
    ):
        _error("setup_sheet contains an invalid field set.")
    operation_settings = tuple(str(value) for value in setup["operation_settings"])
    if (
        len(operation_settings) > MAX_TEMPLATE_OPERATION_SETTINGS
        or len(set(operation_settings)) != len(operation_settings)
    ):
        _error("setup_sheet operation_settings must be at most 64 distinct names.")
    controller_targets = values["tool_controllers"]
    if not isinstance(controller_targets, list) or len(
        controller_targets
    ) > MAX_TEMPLATE_CONTROLLERS:
        _error("tool_controllers must contain at most 32 exact targets.")
    return TemplateContent(
        description=str(values["description"]).strip(),
        include_postprocessing=bool(values["include_postprocessing"]),
        controller_targets=tuple(dict(value) for value in controller_targets),
        stock_kind=stock_kind,
        stock_extent=stock_extent,
        stock_placement=stock_placement,
        setup_tool_rapids=bool(setup["tool_rapids"]),
        setup_coolant=bool(setup["coolant"]),
        setup_operation_heights=bool(setup["operation_heights"]),
        setup_operation_depths=bool(setup["operation_depths"]),
        setup_operation_settings=operation_settings,
    )


def _resolve_controllers(
    document: Any,
    job: Any,
    content: TemplateContent,
) -> tuple[tuple[Any, ...], tuple[Mapping[str, Any], ...]]:
    group = tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
    resolved = tuple(
        resolve_tool_controller_target(document, target)
        for target in content.controller_targets
    )
    controllers = tuple(value[0] for value in resolved)
    states = tuple(value[1] for value in resolved)
    if len({id(controller) for controller in controllers}) != len(controllers):
        _error("CAM template tool controller targets must be distinct.")
    if any(controller not in group for controller in controllers):
        _error(
            "Every CAM template tool controller must belong directly to the exact Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return controllers, states


def _serialize(job: Any, content: TemplateContent, controllers: tuple[Any, ...]) -> tuple[
    Mapping[str, Any], bytes
]:
    available_settings = tuple(job.Proxy.setupSheet.operationsWithSettings())
    unavailable = tuple(
        value for value in content.setup_operation_settings if value not in available_settings
    )
    if unavailable:
        _error(
            "CAM template operation settings must name current SetupSheet overrides.",
            unavailable_operation_settings=list(unavailable),
            available_operation_settings=list(available_settings),
        )
    try:
        encoded = job.Proxy.exportTemplateAttributes(
            job,
            description=content.description,
            includePostProcessing=content.include_postprocessing,
            toolControllers=controllers,
            includeStock=content.stock_kind == "include",
            includeStockExtent=content.stock_extent,
            includeStockPlacement=content.stock_placement,
            includeSettingToolRapid=content.setup_tool_rapids,
            includeSettingCoolant=content.setup_coolant,
            includeSettingOperationHeights=content.setup_operation_heights,
            includeSettingOperationDepths=content.setup_operation_depths,
            includeSettingOperations=content.setup_operation_settings,
        )
        serialized = json.dumps(
            encoded,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Job could not be serialized as a template.",
            error_code="NATIVE_MANUFACTURE_TEMPLATE_SERIALIZATION_FAILED",
        ) from exc
    if (
        not isinstance(encoded, Mapping)
        or int(encoded.get("Version", 0) or 0) != 1
        or not serialized
        or len(serialized) > MAX_TEMPLATE_BYTES
    ):
        _error(
            "The CAM template is invalid or exceeds the 16 MiB output bound.",
            "NATIVE_MANUFACTURE_TEMPLATE_INVALID",
        )
    return dict(encoded), serialized


def _suggested_name(job: Any) -> str:
    stem = _SAFE_STEM.sub("_", str(getattr(job, "Label", "") or job.Name)).strip(
        " ._"
    )
    return f"job_{(stem or 'CAM_Job')[:200]}.json"


def preflight_template_output(
    context: NativeRuntimeContext,
    *,
    job_target: Mapping[str, Any],
    values: Mapping[str, Any],
) -> PreparedTemplateOutput:
    context.guard()
    document = context.document
    if _transaction_open(document):
        _error(
            "Finish or cancel the open transaction before exporting a CAM template.",
            "NATIVE_MANUFACTURE_TEMPLATE_UNAVAILABLE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for the active document recompute before exporting a CAM template.",
            "NATIVE_MANUFACTURE_TEMPLATE_UNAVAILABLE",
        )
    job, before = resolve_job_target(document, job_target)
    content = _content(values)
    controllers, controller_states = _resolve_controllers(document, job, content)
    document_before = _document_state(document)
    encoded, serialized = _serialize(job, content, controllers)
    if (
        job_state(job).get("state_sha256") != before.get("state_sha256")
        or not _document_matches(document, document_before)
    ):
        _error(
            "Serializing the CAM template changed its exact Job or document state.",
            "NATIVE_MANUFACTURE_STATE_INVALID",
        )
    return PreparedTemplateOutput(
        job=job,
        job_target=dict(job_target),
        job_before=before,
        content=content,
        controllers=controllers,
        controller_states=controller_states,
        encoded_attributes=encoded,
        serialized=serialized,
        template_sha256=hashlib.sha256(serialized).hexdigest(),
        document_before=document_before,
        output_request=NativeOutputRequest(
            purpose="cam_job_template_export",
            title="Save CAM Job Template",
            suggested_file_name=_suggested_name(job),
            allowed_suffixes=(".json",),
            name_filter="CAM Job Template (job_*.json)",
            maximum_bytes=MAX_TEMPLATE_BYTES,
        ),
    )


def require_current_template_ticket(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
) -> None:
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    revision = context.state.current_revision(context.document_uid)
    if revision != ticket.expected_revision:
        raise NativeRevisionConflict(ticket.expected_revision, revision)


def verify_template_source(
    context: NativeRuntimeContext,
    prepared: PreparedTemplateOutput,
) -> None:
    context.guard()
    document = context.document
    job, current = resolve_job_target(document, prepared.job_target)
    controllers, states = _resolve_controllers(document, job, prepared.content)
    if (
        job is not prepared.job
        or current.get("state_sha256") != prepared.job_before.get("state_sha256")
        or controllers != prepared.controllers
        or tuple(state.get("state_sha256") for state in states)
        != tuple(state.get("state_sha256") for state in prepared.controller_states)
        or not _document_matches(document, prepared.document_before)
    ):
        _error(
            "The exact CAM Job, template resources, History, or human UI state changed during output.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    encoded, serialized = _serialize(job, prepared.content, controllers)
    if (
        encoded != prepared.encoded_attributes
        or serialized != prepared.serialized
        or not _document_matches(document, prepared.document_before)
    ):
        _error(
            "The exact CAM template content changed during output.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def export_template(
    context: NativeRuntimeContext,
    prepared: PreparedTemplateOutput,
    authorization: NativeOutputAuthorization,
    ticket: NativeCallTicket,
) -> dict[str, Any]:
    def guard() -> None:
        require_current_template_ticket(context, ticket)
        verify_template_source(context, prepared)

    def writer(path: str) -> None:
        with open(path, "wb") as stream:
            stream.write(prepared.serialized)
            stream.flush()
            os.fsync(stream.fileno())

    def validator(path: Path) -> None:
        try:
            serialized = path.read_bytes()
            restored = json.loads(serialized.decode("utf-8"))
            if (
                serialized != prepared.serialized
                or restored != prepared.encoded_attributes
                or int(restored.get("Version", 0) or 0) != 1
            ):
                raise ValueError("template content changed")
            decoded = prepared.job.Proxy.setupSheet.decodeTemplateAttributes(restored)
            if not isinstance(decoded, Mapping) or int(decoded.get("Version", 0) or 0) != 1:
                raise ValueError("template cannot be decoded")
        except Exception as exc:
            raise NativeManufactureError(
                "The generated CAM Job template failed native round-trip validation.",
                error_code="NATIVE_MANUFACTURE_TEMPLATE_INVALID",
            ) from exc

    try:
        artifact = publish_authorized_output(
            prepared.output_request,
            authorization,
            writer=writer,
            guard=guard,
            validator=validator,
            temporary_suffix=".json",
        )
    except NativeOutputError as exc:
        raise NativeManufactureError(str(exc), error_code=exc.code) from exc
    content = prepared.content
    return {
        "operation": "export_template",
        "job": {
            "object_name": prepared.job_before["object_name"],
            "state_sha256": prepared.job_before["state_sha256"],
        },
        "template": {
            "version": 1,
            "sha256": prepared.template_sha256,
            "description_included": bool(content.description),
            "postprocessing_included": content.include_postprocessing,
            "tool_controller_count": len(prepared.controllers),
            "stock": content.stock_kind,
            "setup_sheet_operation_setting_count": len(
                content.setup_operation_settings
            ),
        },
        "output": artifact.summary(),
        "document_unchanged": True,
        "history_unchanged": True,
        "selection_unchanged": True,
        "visibility_unchanged": True,
    }
