# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native folded/flat requests use the same cached presentation as the ribbon."""

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef, NativeTargetError, resolve_object


class NativeSheetMetalViewError(RuntimeError):
    def failure(self):
        return {"error_code": "NATIVE_SHEETMETAL_VIEW_UNAVAILABLE", "message": str(self),
                "repair": "Read the sheet with sheet_metal.inspect; finish pending edits and prepare the current History state before switching."}


class NativeSheetMetalViewTargetError(NativeTargetError):
    """Preserve exact-target failures while explaining source/state recovery."""

    def failure(self):
        return {**super().failure(), "repair": (
            "Use sheet_metal.inspect operation=list_sheets to find the intended existing shared sheet. "
            "If the intended source has no shared sheet yet, use sheet_metal.create operation=from_source "
            "with that source's object_name and a reference_face from its observed "
            "source_geometry.reference_faces. Switch the returned shared sheet's object_name. "
            "View switching uses an already prepared folded/flat pair; creating a base shape only creates the source.")}


class NativeSheetMetalViewRuntime:
    def __init__(self, context):
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments):
        _, values = strict_variant_arguments(arguments, {
            "set_representation": frozenset({"object_name", "representation"})})
        object_name, representation = values["object_name"], values["representation"]
        if not isinstance(representation, str) or representation not in ("folded", "flat"):
            raise NativeArgumentError("representation must be folded or flat.")
        if not isinstance(object_name, str):
            raise NativeArgumentError("Use object_name from sheet_metal.inspect in the current document.")
        # Registry construction must not load geometry or activate a workbench.
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        from SheetMetalPresentation import _gui_thread
        _gui_thread()
        context = self._context
        context.guard()
        reference = NativeObjectRef(context.document_uid, object_name)
        sheet = resolve_object(context.document, reference)
        if not isinstance(getattr(sheet, "Proxy", None), Editable.PreparedSheetState):
            raise NativeSheetMetalViewTargetError("Select a shared sheet state from sheet_metal.inspect.",
                                                 exact_target=reference.summary(), actual_type=sheet.TypeId)
        try:
            Shared.capture_revision(sheet)
            display = Shared._display_state(sheet)
            fingerprint = display.ViewObject.Proxy.current_display()
            Shared.switch(sheet, representation)
        except (RuntimeError, ValueError) as error:
            raise NativeSheetMetalViewError(str(error)) from error
        return {"target": reference.summary(),
                "display_state": {"document_uid": context.document_uid, "object_name": display.Name},
                "representation": display.ViewObject.Proxy.mode, "input_hash": fingerprint,
                "visibility": bool(display.Visibility),
                "structural_revision": context.state.current_revision(context.document_uid)}
