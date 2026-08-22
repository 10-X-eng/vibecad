# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, human-authorized ASMT export for one active Assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable

from VibeCADNativeAssemblyDiagnosisState import (
    AssemblyDiagnosisState,
    NativeAssemblyDiagnosisError,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeOutput import (
    NativeOutputArtifact,
    NativeOutputAuthorization,
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict
from VibeCADNativeTargets import (
    NativeObjectRef,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_EXPORT_FAILED = "NATIVE_ASSEMBLY_EXPORT_FAILED"
MAX_ASMT_OUTPUT_BYTES = 256 * 1024 * 1024
_SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class NativeAssemblyExportError(RuntimeError):
    """The exact active Assembly could not be exported safely."""

    def __init__(self, message: str) -> None:
        super().__init__(str(message).strip())

    def failure(self) -> dict[str, str]:
        return {
            "error_code": NATIVE_ASSEMBLY_EXPORT_FAILED,
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblyAsmtExportSpec:
    assembly_ref: NativeObjectRef


@dataclass(frozen=True, slots=True)
class PreparedAssemblyAsmtExport:
    spec: AssemblyAsmtExportSpec
    state: AssemblyDiagnosisState
    active_before: Any
    selection_before: dict[str, Any]
    document_objects_before: tuple[Any, ...]
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


def _undo_count(document: Any) -> int:
    return int(getattr(document, "UndoCount", 0) or 0)


def _gui_modified(document: Any) -> bool | None:
    try:
        import FreeCADGui as Gui

        gui_document = Gui.getDocument(str(document.Name))
        return None if gui_document is None else bool(gui_document.Modified)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _exact_active_assembly(
    context: NativeRuntimeContext,
    reference: NativeObjectRef,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    try:
        assembly = resolve_object(
            context.document,
            reference,
            expected_types=("Assembly::AssemblyObject",),
        )
        active = active_reader(context.document)
    except Exception as exc:
        raise NativeAssemblyExportError(str(exc)) from exc
    if not same_assembly(assembly, active):
        raise NativeAssemblyExportError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    return assembly


def _suggested_file_name(document: Any) -> str:
    base = _SAFE_FILE_NAME.sub("_", str(getattr(document, "Name", "") or "Assembly"))
    base = base.strip(" ._")[:200] or "Assembly"
    return f"{base}.asmt"


def _output_request(document: Any) -> NativeOutputRequest:
    return NativeOutputRequest(
        purpose="assembly_asmt_export",
        title="Export active Assembly as ASMT",
        suggested_file_name=_suggested_file_name(document),
        allowed_suffixes=(".asmt",),
        name_filter="ASMT Files (*.asmt)",
        maximum_bytes=MAX_ASMT_OUTPUT_BYTES,
    )


def _same_state(first: AssemblyDiagnosisState, second: AssemblyDiagnosisState) -> bool:
    return bool(
        first.assembly is second.assembly
        and first.joint_group is second.joint_group
        and first.components == second.components
        and first.grounded_joints == second.grounded_joints
        and first.regular_joints == second.regular_joints
        and first.state_sha256 == second.state_sha256
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


def preflight_assembly_asmt_export(
    context: NativeRuntimeContext,
    spec: AssemblyAsmtExportSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblyAsmtExport:
    """Freeze the exact ASMT source state before asking the human for a path."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if not isinstance(spec, AssemblyAsmtExportSpec):
        raise TypeError("spec must be an AssemblyAsmtExportSpec")
    if not isinstance(spec.assembly_ref, NativeObjectRef):
        raise TypeError("spec.assembly_ref must be a NativeObjectRef")
    context.guard()
    document = context.document
    if _transaction_open(document):
        raise NativeAssemblyExportError(
            "Finish or cancel the open transaction before exporting ASMT."
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeAssemblyExportError(
            "Wait for the active document recompute before exporting ASMT."
        )
    assembly = _exact_active_assembly(context, spec.assembly_ref, active_reader)
    try:
        state = capture_assembly_diagnosis_state(assembly)
    except NativeAssemblyDiagnosisError as exc:
        raise NativeAssemblyExportError(str(exc)) from exc
    selection = selection_reader(document)
    if not same_assembly(assembly, active_reader(document)):
        raise NativeAssemblyExportError(
            "The human-active Assembly changed during ASMT export preflight."
        )
    return PreparedAssemblyAsmtExport(
        spec=spec,
        state=state,
        active_before=assembly,
        selection_before=selection,
        document_objects_before=tuple(getattr(document, "Objects", ()) or ()),
        undo_count_before=_undo_count(document),
        transaction_id_before=_transaction_id(document),
        gui_modified_before=_gui_modified(document),
        output_request=_output_request(document),
    )


def verify_assembly_asmt_source_unchanged(
    context: NativeRuntimeContext,
    prepared: PreparedAssemblyAsmtExport,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> None:
    """Reject turn, source, selection, transaction, or document-state drift."""

    if not isinstance(prepared, PreparedAssemblyAsmtExport):
        raise TypeError("prepared must be a PreparedAssemblyAsmtExport")
    context.guard()
    document = context.document
    assembly = _exact_active_assembly(
        context, prepared.spec.assembly_ref, active_reader
    )
    try:
        state = capture_assembly_diagnosis_state(assembly)
    except NativeAssemblyDiagnosisError as exc:
        raise NativeAssemblyExportError(str(exc)) from exc
    if (
        not same_assembly(prepared.active_before, assembly)
        or not _same_state(prepared.state, state)
        or selection_reader(document) != prepared.selection_before
        or tuple(getattr(document, "Objects", ()) or ())
        != prepared.document_objects_before
        or _undo_count(document) != prepared.undo_count_before
        or _transaction_id(document) != prepared.transaction_id_before
        or _transaction_open(document)
        or _gui_modified(document) != prepared.gui_modified_before
    ):
        raise NativeAssemblyExportError(
            "The Assembly, document, or human UI state changed during ASMT export."
        )


def _validate_asmt_file(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            first = stream.readline(64).rstrip(b"\r\n")
            second = stream.readline(64).rstrip(b"\r\n")
    except OSError as exc:
        raise NativeAssemblyExportError(
            "The generated ASMT file could not be verified."
        ) from exc
    if first != b"OndselSolver" or second != b"Assembly":
        raise NativeAssemblyExportError(
            "The native Assembly serializer produced an invalid ASMT header."
        )


def export_assembly_asmt(
    context: NativeRuntimeContext,
    prepared: PreparedAssemblyAsmtExport,
    authorization: NativeOutputAuthorization,
    ticket: NativeCallTicket,
    *,
    exporter: Callable[[Any, str], Any] | None = None,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Serialize privately and atomically publish only the human-approved file."""

    if not isinstance(prepared, PreparedAssemblyAsmtExport):
        raise TypeError("prepared must be a PreparedAssemblyAsmtExport")
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    assembly = prepared.state.assembly

    def write(path: str) -> Any:
        if exporter is not None:
            return exporter(assembly, path)
        return assembly.exportAsASMT(path)

    def guard() -> None:
        require_current_output_ticket(context, ticket)
        verify_assembly_asmt_source_unchanged(
            context,
            prepared,
            active_reader=active_reader,
            selection_reader=selection_reader,
        )

    try:
        artifact: NativeOutputArtifact = publish_authorized_output(
            prepared.output_request,
            authorization,
            writer=write,
            guard=guard,
            validator=_validate_asmt_file,
        )
    except NativeOutputError as exc:
        raise NativeAssemblyExportError(str(exc)) from exc
    result = {
        "operation": "asmt",
        "assembly": {
            "object_name": str(assembly.Name),
            "object_id": int(assembly.ID),
        },
        "output": artifact.summary(),
        "source_state_sha256": prepared.state.state_sha256,
        "document_unchanged": True,
        "selection_unchanged": True,
        "active_assembly_unchanged": True,
    }
    return result
