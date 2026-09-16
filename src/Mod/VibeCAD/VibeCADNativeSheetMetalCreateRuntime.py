# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create shared sheet geometry through an exact native document transaction."""

import re

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSheetMetalCreateSchema import SHEETMETAL_CREATE_CAPABILITY_NAME, CREATE_FIELDS, CREATE_DEFAULTS
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef, resolve_object


class NativeSheetMetalCreateRequestError(RuntimeError):
    def __init__(self, message, *, operation="from_source"):
        super().__init__(message)
        self.operation = operation

    def failure(self):
        return {"error_code": "NATIVE_SHEETMETAL_CREATE_INVALID", "message": str(self),
                "parameters_committed": False,
                "repair": ("Inspect the source's current geometry and select one planar sheet face before retrying."
                           if self.operation == "from_source" else
                           "Review the requested dimensions, exact source/container names and selected faces or edges before retrying.")}


class NativeSheetMetalCreateRuntime:
    def __init__(self, context):
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments, *, ticket):
        raise NativeArgumentError("Sheet creation requires native asynchronous document dispatch.")

    def execute_async(self, arguments, *, ticket):
        operation, values = strict_variant_arguments(arguments, CREATE_FIELDS, defaults=CREATE_DEFAULTS)
        if operation == "from_source":
            name, face = values["object_name"], values["reference_face"]
            if (not isinstance(name, str) or not isinstance(face, str) or len(face) > 32
                    or re.fullmatch(r"Face[1-9][0-9]*", face) is None):
                raise NativeArgumentError("Use an exact source object_name and planar reference_face.")
        context = self._context
        if (not isinstance(ticket, NativeCallTicket) or ticket.document_uid != context.document_uid
                or ticket.capability_name != SHEETMETAL_CREATE_CAPABILITY_NAME):
            raise NativeArgumentError("Sheet creation needs its own call ticket in this exact document.")
        import SheetMetalOperations as Operations
        import SheetMetalNativeEdit
        from SheetMetalPresentation import _gui_thread
        _gui_thread()
        context.guard()
        if operation == "from_source":
            source = resolve_object(context.document, NativeObjectRef(context.document_uid, name))
        try:
            if operation != "from_source":
                import SheetMetalSourceOperations as Sources
                prepared = Sources.prepare(context.document, {"operation": operation, **values},
                    expected_revision=Sources.capture_revision(context.document))
                return SheetMetalNativeEdit.start_source_creation(context, ticket, prepared)
            revision = Operations.capture_source_revision(source)
            return SheetMetalNativeEdit.start_creation(context, ticket, source, face, expected_revision=revision)
        except (RuntimeError, ValueError) as error:
            if callable(getattr(error, "failure", None)):
                raise
            raise NativeSheetMetalCreateRequestError(str(error), operation=operation) from error
