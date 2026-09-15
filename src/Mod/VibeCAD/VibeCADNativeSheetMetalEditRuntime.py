# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exact native sheet edits use the shared asynchronous History service."""

from collections.abc import Mapping

from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSheetMetalEditSchema import EDIT_FIELDS, EDIT_REQUIRED, SHEETMETAL_EDIT_CAPABILITY_NAME
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef, NativeTargetError, resolve_object


class NativeSheetMetalEditRequestError(RuntimeError):
    def failure(self):
        return {"error_code": "NATIVE_SHEETMETAL_EDIT_INVALID", "message": str(self),
                "parameters_committed": False,
                "repair": "Read the current sheet and cuts with sheet_metal.inspect, then correct the edit request."}


class NativeSheetMetalFoldedRegionError(NativeSheetMetalEditRequestError):
    def failure(self):
        return {**super().failure(), "repair": (
            "Use sheet_metal.inspect operation=list_regions for this exact sheet, then supply "
            "the region matching the folded center's sheet face. A region is required for folded "
            "centers; flat-coordinate and radius-only edits may omit it.")}


class NativeSheetMetalEditRuntime:
    def __init__(self, context):
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments, *, ticket):
        raise NativeArgumentError("Sheet edits require native asynchronous document dispatch.")

    def execute_async(self, arguments, *, ticket):
        if not isinstance(arguments, Mapping):
            raise NativeArgumentError("Sheet edit arguments must be an object.")
        values = dict(arguments)
        operation = values.pop("operation", None)
        name = values.pop("object_name", None)
        if (not isinstance(operation, str) or operation not in EDIT_FIELDS
                or not isinstance(name, str) or set(values) - EDIT_FIELDS[operation]
                or not set(EDIT_REQUIRED[operation]).issubset(values)):
            raise NativeArgumentError("Use an exact sheet object_name and the fields for the selected edit operation.")
        context = self._context
        if (not isinstance(ticket, NativeCallTicket) or ticket.document_uid != context.document_uid
                or ticket.capability_name != SHEETMETAL_EDIT_CAPABILITY_NAME):
            raise NativeArgumentError("The sheet edit requires a call ticket for this exact document.")
        # Import geometry only when called on the document thread, never while
        # constructing a provider registry or listing its tools.
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        import SheetMetalNativeEdit
        from SheetMetalPresentation import _gui_thread
        _gui_thread()
        context.guard()
        reference = NativeObjectRef(context.document_uid, name)
        sheet = resolve_object(context.document, reference)
        if not isinstance(getattr(sheet, "Proxy", None), Editable.PreparedSheetState):
            raise NativeTargetError("Select a shared sheet state from sheet_metal.inspect.",
                                    exact_target=reference.summary(), actual_type=sheet.TypeId)
        if (operation in ("add_circle", "update_circle") and values.get("center") is not None
                and values.get("representation") == "folded" and values.get("region") is None):
            raise NativeSheetMetalFoldedRegionError("A folded center requires its prepared sheet region.")
        try:
            prepared = Shared.prepare(sheet, {"operation": operation, **values},
                                      expected_revision=Shared.capture_revision(sheet))
        except (RuntimeError, ValueError) as error:
            raise NativeSheetMetalEditRequestError(str(error)) from error
        return SheetMetalNativeEdit.start(context, ticket, prepared)
