# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, human-authorized output for one current CAM ToolBit."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import resolve_tool_bit_target, tool_bit_state
from VibeCADNativeOutput import (
    NativeOutputAuthorization,
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict
from VibeCADNativeTargets import read_current_selection


MAX_TOOL_BIT_OUTPUT_BYTES = 4 * 1024 * 1024
_SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_FORMATS = {
    "fctb": {
        "suffixes": (".fctb",),
        "name_filter": "FreeCAD Tool (*.fctb)",
    },
    "yaml": {
        "suffixes": (".yaml", ".yml"),
        "name_filter": "YAML ToolBit (*.yaml *.yml)",
    },
}


@dataclass(frozen=True, slots=True)
class ToolBitOutputSpec:
    operation: str
    target: Mapping[str, Any]
    format_name: str


@dataclass(frozen=True, slots=True)
class PreparedToolBitOutput:
    spec: ToolBitOutputSpec
    tool: Any = field(repr=False, compare=False)
    tool_before: Mapping[str, Any]
    serialized: bytes = field(repr=False)
    definition_sha256: str
    document_objects_before: tuple[Any, ...] = field(repr=False, compare=False)
    selection_before: Any = field(repr=False, compare=False)
    undo_count_before: int
    transaction_id_before: int
    gui_modified_before: bool | None
    output_request: NativeOutputRequest


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


def _serializer(format_name: str) -> Any:
    if format_name == "fctb":
        from Path.Tool.toolbit.serializers.fctb import FCTBSerializer

        return FCTBSerializer
    if format_name == "yaml":
        from Path.Tool.toolbit.serializers.yaml import YamlToolBitSerializer

        return YamlToolBitSerializer
    raise NativeManufactureError(
        "format must be fctb or yaml.",
        error_code="NATIVE_ARGUMENTS_INVALID",
    )


def _definition(serialized: bytes, format_name: str) -> Mapping[str, Any]:
    try:
        if format_name == "fctb":
            value = json.loads(serialized.decode("utf-8"))
        else:
            import yaml

            value = yaml.safe_load(serialized)
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM serializer produced an unreadable ToolBit definition.",
            error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_INVALID",
        ) from exc
    if (
        not isinstance(value, Mapping)
        or int(value.get("version", 0) or 0) != 2
        or not str(value.get("id") or "").strip()
        or not str(value.get("name") or "").strip()
        or not str(value.get("shape") or "").strip()
        or not isinstance(value.get("parameter"), Mapping)
    ):
        raise NativeManufactureError(
            "The native CAM serializer produced an incomplete ToolBit definition.",
            error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_INVALID",
        )
    return dict(value)


def _definition_sha256(serialized: bytes, format_name: str) -> str:
    encoded = json.dumps(
        _definition(serialized, format_name),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _suggested_file_name(label: str, format_name: str) -> str:
    stem = _SAFE_FILE_NAME.sub("_", str(label or "ToolBit")).strip(" ._")
    suffix = str(_FORMATS[format_name]["suffixes"][0])
    return f"{(stem or 'ToolBit')[:200]}{suffix}"


def _output_request(tool: Any, operation: str, format_name: str) -> NativeOutputRequest:
    details = _FORMATS[format_name]
    return NativeOutputRequest(
        purpose=f"cam_toolbit_{operation}",
        title="Save CAM ToolBit" if operation == "save" else "Save CAM ToolBit As",
        suggested_file_name=_suggested_file_name(tool.Label, format_name),
        allowed_suffixes=tuple(details["suffixes"]),
        name_filter=str(details["name_filter"]),
        maximum_bytes=MAX_TOOL_BIT_OUTPUT_BYTES,
    )


def require_current_output_ticket(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
) -> None:
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    current = context.state.current_revision(context.document_uid)
    if current != ticket.expected_revision:
        raise NativeRevisionConflict(ticket.expected_revision, current)


def preflight_tool_bit_output(
    context: NativeRuntimeContext,
    spec: ToolBitOutputSpec,
) -> PreparedToolBitOutput:
    """Freeze exact ToolBit bytes and document state before asking for a path."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if not isinstance(spec, ToolBitOutputSpec):
        raise TypeError("spec must be a ToolBitOutputSpec")
    operation = str(spec.operation or "")
    if operation not in {"save", "save_as"}:
        raise NativeManufactureError(
            "The CAM ToolBit output operation must be save or save_as.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    format_name = str(spec.format_name or "")
    serializer = _serializer(format_name)
    context.guard()
    document = context.document
    if _transaction_open(document):
        raise NativeManufactureError(
            "Finish or cancel the open transaction before saving a CAM ToolBit.",
            error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_UNAVAILABLE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeManufactureError(
            "Wait for the active document recompute before saving a CAM ToolBit.",
            error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_UNAVAILABLE",
        )
    tool, before = resolve_tool_bit_target(document, spec.target)
    try:
        from Path.CommandBoundary import is_timeline_input_usable
        from Path.Tool import ToolBit

        usable = bool(is_timeline_input_usable(tool, document))
        valid_type = isinstance(getattr(tool, "Proxy", None), ToolBit)
    except Exception as exc:
        raise NativeManufactureError(
            "The selected CAM ToolBit could not be validated for output.",
            error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_UNAVAILABLE",
        ) from exc
    if not usable or not valid_type:
        raise NativeManufactureError(
            "The exact CAM ToolBit is not usable at the current History position.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        )
    try:
        serialized = bytes(serializer.serialize(tool.Proxy))
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM serializer could not serialize the exact ToolBit.",
            error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_FAILED",
        ) from exc
    if not serialized or len(serialized) > MAX_TOOL_BIT_OUTPUT_BYTES:
        raise NativeManufactureError(
            "The serialized CAM ToolBit is empty or exceeds 4 MiB.",
            error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_INVALID",
        )
    definition_sha256 = _definition_sha256(serialized, format_name)
    if tool_bit_state(tool)["state_sha256"] != before["state_sha256"]:
        raise NativeManufactureError(
            "The CAM ToolBit changed while its output was prepared.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    return PreparedToolBitOutput(
        spec=ToolBitOutputSpec(operation, dict(spec.target), format_name),
        tool=tool,
        tool_before=before,
        serialized=serialized,
        definition_sha256=definition_sha256,
        document_objects_before=tuple(document.Objects),
        selection_before=read_current_selection(document),
        undo_count_before=int(getattr(document, "UndoCount", 0) or 0),
        transaction_id_before=_transaction_id(document),
        gui_modified_before=_gui_modified(document),
        output_request=_output_request(tool, operation, format_name),
    )


def verify_tool_bit_output_source_unchanged(
    context: NativeRuntimeContext,
    prepared: PreparedToolBitOutput,
) -> None:
    if not isinstance(prepared, PreparedToolBitOutput):
        raise TypeError("prepared must be a PreparedToolBitOutput")
    context.guard()
    document = context.document
    tool, current = resolve_tool_bit_target(document, prepared.spec.target)
    if (
        tool is not prepared.tool
        or current["state_sha256"] != prepared.tool_before["state_sha256"]
        or tuple(document.Objects) != prepared.document_objects_before
        or read_current_selection(document) != prepared.selection_before
        or int(getattr(document, "UndoCount", 0) or 0)
        != prepared.undo_count_before
        or _transaction_id(document) != prepared.transaction_id_before
        or _transaction_open(document)
        or _gui_modified(document) != prepared.gui_modified_before
    ):
        raise NativeManufactureError(
            "The ToolBit, document, or human UI state changed during output.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )


def export_tool_bit(
    context: NativeRuntimeContext,
    prepared: PreparedToolBitOutput,
    authorization: NativeOutputAuthorization,
    ticket: NativeCallTicket,
) -> dict[str, Any]:
    if not isinstance(prepared, PreparedToolBitOutput):
        raise TypeError("prepared must be a PreparedToolBitOutput")

    def guard() -> None:
        require_current_output_ticket(context, ticket)
        verify_tool_bit_output_source_unchanged(context, prepared)

    def writer(path: str) -> None:
        with open(path, "wb") as stream:
            stream.write(prepared.serialized)
            stream.flush()
            os.fsync(stream.fileno())

    def validator(path: Path) -> None:
        try:
            serialized = path.read_bytes()
            if serialized != prepared.serialized:
                raise ValueError("serialized bytes changed")
            if (
                _definition_sha256(serialized, prepared.spec.format_name)
                != prepared.definition_sha256
            ):
                raise ValueError("serialized definition changed")
            from Path.Tool import ToolBit

            restored = ToolBit.from_dict(
                dict(_definition(serialized, prepared.spec.format_name)),
                shallow=True,
            )
            if not callable(getattr(restored, "to_dict", None)):
                raise TypeError("serializer returned no ToolBit definition")
            restored.to_dict()
        except NativeManufactureError:
            raise
        except Exception as exc:
            raise NativeManufactureError(
                "The generated CAM ToolBit output failed native round-trip validation.",
                error_code="NATIVE_MANUFACTURE_TOOL_OUTPUT_INVALID",
            ) from exc

    try:
        artifact = publish_authorized_output(
            prepared.output_request,
            authorization,
            writer=writer,
            guard=guard,
            validator=validator,
            temporary_suffix=str(_FORMATS[prepared.spec.format_name]["suffixes"][0]),
        )
    except NativeOutputError as exc:
        raise NativeManufactureError(str(exc), error_code=exc.code) from exc
    return {
        "operation": prepared.spec.operation,
        "format": prepared.spec.format_name,
        "tool": {
            "object_name": prepared.tool_before["object_name"],
            "state_sha256": prepared.tool_before["state_sha256"],
            "definition_sha256": prepared.definition_sha256,
        },
        "output": artifact.summary(),
        "document_unchanged": True,
        "selection_unchanged": True,
    }
