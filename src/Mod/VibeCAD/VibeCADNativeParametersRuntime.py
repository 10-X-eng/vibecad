# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound execution for sharp Parameters spreadsheet capabilities."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError
from VibeCADNativeOutput import NativeOutputError, publish_authorized_output
from VibeCADNativeParameters import (
    NativeParametersError,
    mutate_parameters,
    parameter_read_range,
    prepare_parameters_mutation,
    verify_parameters_mutation,
)
from VibeCADNativeParametersIO import (
    commit_parameters_csv_import,
    parameters_csv_export_source_summary,
    parameters_csv_input_request,
    prepare_parameters_csv_export,
    prepare_parameters_csv_import,
    validate_parameters_csv,
    verify_parameters_csv_export_source,
    verify_parameters_csv_import,
    write_parameters_csv,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_CELL_FIELDS = {
    "write_values": frozenset({"sheet", "updates"}),
    "write_formulas": frozenset({"sheet", "updates"}),
    "set_alias": frozenset({"sheet", "cell", "alias"}),
    "merge": frozenset({"sheet", "target"}),
    "split": frozenset({"sheet", "cell"}),
    "set_properties": frozenset({"sheet", "target", "properties"}),
}
_FORMAT_FIELDS = {
    "align_left": frozenset({"sheet", "target"}),
    "align_center": frozenset({"sheet", "target"}),
    "align_right": frozenset({"sheet", "target"}),
    "align_top": frozenset({"sheet", "target"}),
    "align_vertical_center": frozenset({"sheet", "target"}),
    "align_bottom": frozenset({"sheet", "target"}),
    "set_bold": frozenset({"sheet", "target", "enabled"}),
    "set_italic": frozenset({"sheet", "target", "enabled"}),
    "set_underline": frozenset({"sheet", "target", "enabled"}),
}
_TRANSACTION_NAMES = {
    "create": "Create Native Parameters Sheet",
    "write_values": "Write Native Parameters Values",
    "write_formulas": "Write Native Parameters Formulas",
    "set_alias": "Set Native Parameters Alias",
    "merge": "Merge Native Parameters Cells",
    "split": "Split Native Parameters Cells",
    "set_properties": "Set Native Parameters Cell Properties",
    "align_left": "Align Native Parameters Left",
    "align_center": "Align Native Parameters Center",
    "align_right": "Align Native Parameters Right",
    "align_top": "Align Native Parameters Top",
    "align_vertical_center": "Align Native Parameters Vertically",
    "align_bottom": "Align Native Parameters Bottom",
    "set_bold": "Set Native Parameters Bold",
    "set_italic": "Set Native Parameters Italic",
    "set_underline": "Set Native Parameters Underline",
}


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


def _timeline(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _selection(document: Any) -> Mapping[str, Any]:
    from VibeCADNativeTargets import read_current_selection

    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


class NativeParametersRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _require_ticket(self, ticket: NativeCallTicket) -> None:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("A Parameters mutation requires one exact Native call ticket")
        current = self._context.state.current_revision(self._context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)

    def _mutate(
        self,
        operation: str,
        values: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        context.guard()
        prepared = prepare_parameters_mutation(
            context.document,
            operation=operation,
            values=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=partial(mutate_parameters, prepared=prepared),
            verify=verify_parameters_mutation,
        )

    def sheet(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {"create": frozenset({"label"}), "import_csv": frozenset()},
        )
        if operation == "create":
            return self._mutate(operation, values, ticket)
        return self._start_import(ticket)

    def read(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"read_range": frozenset({"sheet", "range"})},
        )
        self._context.guard()
        result = parameter_read_range(self._context.document, values)
        self._context.guard()
        return result

    def cell(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _CELL_FIELDS)
        return self._mutate(operation, values, ticket)

    def format(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _FORMAT_FIELDS)
        return self._mutate(operation, values, ticket)

    def export(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"export_csv": frozenset({"sheet"})},
        )
        context = self._context
        context.guard()
        self._require_ticket(ticket)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        authorizer = context.authorize_output
        if manager is None or dispatcher is None or authorizer is None:
            raise NativeParametersError(
                "Background human-authorized Parameters export is unavailable.",
                error_code="NATIVE_PARAMETERS_EXPORT_UNAVAILABLE",
            )
        prepared = prepare_parameters_csv_export(context.document, values["sheet"])
        try:
            authorization = authorizer(prepared.output_request)
        except NativeOutputError as exc:
            raise NativeParametersError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeParametersError(
                "The human cancelled Parameters output authorization.",
                error_code="NATIVE_PARAMETERS_EXPORT_CANCELLED",
            )
        self._require_ticket(ticket)

        def validate_source() -> None:
            self._require_ticket(ticket)
            verify_parameters_csv_export_source(context.document, prepared)

        def prepare(cancelled: Any, progress: Any) -> Mapping[str, Any]:
            if cancelled():
                raise NativeBackgroundCancelled()
            progress(10, "Writing exact Parameters CSV")
            artifact = publish_authorized_output(
                prepared.output_request,
                authorization,
                writer=lambda path: write_parameters_csv(prepared, path),
                guard=lambda: dispatcher(validate_source),
                validator=lambda path: validate_parameters_csv(path, prepared.csv_text),
                temporary_suffix=".csv",
            )
            progress(90, "Parameters CSV verified and published")
            return {
                "operation": "export_csv",
                "output": artifact.summary(),
                "source": parameters_csv_export_source_summary(prepared),
            }

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="parameters.export.export_csv",
                prepare=prepare,
                validate_before_commit=lambda: None,
                commit=lambda result: result,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeParametersError(
                str(exc),
                error_code="NATIVE_PARAMETERS_EXPORT_QUEUE_FAILED",
            ) from exc
        return self._next(snapshot)

    def _start_import(self, ticket: NativeCallTicket) -> dict[str, Any]:
        context = self._context
        context.guard()
        self._require_ticket(ticket)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        authorizer = context.authorize_input
        if manager is None or dispatcher is None or authorizer is None:
            raise NativeParametersError(
                "Background human-authorized Parameters import is unavailable.",
                error_code="NATIVE_PARAMETERS_IMPORT_UNAVAILABLE",
            )
        request = parameters_csv_input_request()
        try:
            authorization = authorizer(request)
        except NativeInputError as exc:
            raise NativeParametersError(str(exc), error_code=exc.code) from exc
        if authorization is None:
            raise NativeParametersError(
                "The human cancelled Parameters input authorization.",
                error_code="NATIVE_PARAMETERS_IMPORT_CANCELLED",
            )
        document = context.document
        boundary = {
            "objects_before": tuple(document.Objects),
            "timeline_before": _timeline(document),
            "selection_before": _selection(document),
        }

        def prepare(cancelled: Any, progress: Any) -> Any:
            return prepare_parameters_csv_import(
                authorization,
                request,
                **boundary,
                cancelled=cancelled,
                progress=progress,
            )

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Import Native Parameters CSV",
                mutate=partial(commit_parameters_csv_import, prepared=prepared),
                verify=verify_parameters_csv_import,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="parameters.sheet.import_csv",
                prepare=prepare,
                validate_before_commit=context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
            )
        except NativeBackgroundError as exc:
            raise NativeParametersError(
                str(exc),
                error_code="NATIVE_PARAMETERS_IMPORT_QUEUE_FAILED",
            ) from exc
        return self._next(snapshot)

    @staticmethod
    def _next(snapshot: Any) -> dict[str, Any]:
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }
