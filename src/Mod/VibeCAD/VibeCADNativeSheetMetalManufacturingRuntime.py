# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native RMFG calls use the GUI-owned controller and detached worker requests."""

from concurrent.futures import Future

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSheetMetalManufacturingSchema import FIELDS, WORKER_OPERATIONS
from VibeCADNativeTargets import NativeObjectRef, NativeTargetError, resolve_object


class NativeSheetMetalManufacturingError(RuntimeError):
    def failure(self):
        return {"error_code": "NATIVE_SHEETMETAL_MANUFACTURING_UNAVAILABLE", "message": str(self),
                "repair": "Read manufacturing status for the current shared sheet. Finish its edits, connect RMFG, and use observed part/material IDs."}


class NativeSheetMetalManufacturingRuntime:
    def __init__(self, context):
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(self, arguments, *, asynchronous=False):
        operation, values = strict_variant_arguments(arguments, FIELDS)
        if not isinstance(values["object_name"], str):
            raise NativeArgumentError("Use a shared sheet object_name from sheet_metal.inspect.")
        for name in ("part_id", "material_id", "job_key"):
            if name in values and (not isinstance(values[name], str) or not values[name].strip()
                                   or len(values[name]) > 256):
                raise NativeArgumentError("Use observed RMFG part, material or saved job IDs.")
        if "quantity" in values and (type(values["quantity"]) is not int or not 1 <= values["quantity"] <= 1_000_000):
            raise NativeArgumentError("Quantity must be 1 to 1000000 completed design units.")
        if "more" in values and type(values["more"]) is not bool:
            raise NativeArgumentError("more must be a boolean.")
        for name, lower, upper in (("offset", 0, 1_000_000), ("limit", 1, 50)):
            if name in values and (type(values[name]) is not int or not lower <= values[name] <= upper):
                raise NativeArgumentError(f"{name} must be an integer from {lower} to {upper}.")
        if operation == "list_jobs" and values["limit"] > 20:
            raise NativeArgumentError("List at most 20 saved jobs per page.")
        if operation in WORKER_OPERATIONS and not asynchronous:
            raise NativeSheetMetalManufacturingError("Use asynchronous native dispatch for manufacturing network operations")
        import SheetMetalEditable as Editable
        import SheetMetalOperations as Operations
        import SheetMetalRMFGManufacturingGui as Manufacturing
        from SheetMetalPresentation import _gui_thread
        _gui_thread()
        context = self._context
        context.guard()
        reference = NativeObjectRef(context.document_uid, values["object_name"])
        sheet = resolve_object(context.document, reference)
        if not isinstance(getattr(sheet, "Proxy", None), Editable.PreparedSheetState):
            raise NativeTargetError("Choose a shared sheet state from sheet_metal.inspect, not its source feature.",
                                    exact_target=reference.summary(), actual_type=sheet.TypeId)
        Operations.capture_revision(sheet)
        revision = context.state.current_revision(context.document_uid)
        controller = Manufacturing.manufacturing_controller(sheet)

        def guard():
            _gui_thread()
            context.guard()
            if (context.state.current_revision(context.document_uid) != revision
                    or resolve_object(context.document, reference) is not sheet):
                raise NativeSheetMetalManufacturingError("The document changed while manufacturing work was pending")

        def result(**extra):
            guard()
            state = dict(controller.status())
            materials = state.get("materials", [])
            offset, limit = (values["offset"], values["limit"]) if operation == "list_materials" else (0, 50)
            state["materials"] = [{key: item[key] for key in ("id", "material", "type", "thickness_mm", "bendable")
                                   if key in item} for item in materials[offset:offset + limit]]
            state.update(material_count=len(materials), material_offset=offset,
                         next_material_offset=offset + limit if offset + limit < len(materials) else None)
            return {**state, "target": reference.summary(), "structural_revision": revision,
                    "repair_workflow": {
                        "inspect": {"tool": "sheet_metal.inspect", "arguments": {
                            "operation": "read_sheet", "target": reference.summary()}},
                        "message": (
                            "For DFM failures, inspect the current sheet's source/history/profile links. "
                            "Edit bends in sheet_metal; edit an existing sketch via sketching, sketch.open, "
                            "sketch.finish, then return to sheet_metal. Do not guess a finding-to-face mapping. "
                            "After repairs, verify the sheet, run analyze on the new revision, then quote; "
                            "old findings/prices are not verification of the repaired design."),
                    }, **extra}

        try:
            if operation == "show_panel":
                Manufacturing.show_manufacturing(sheet)
                return result(panel_open=True)
            if operation in ("status", "list_materials"):
                return result()
            if operation == "set_material":
                controller.set_material(values["part_id"], values["material_id"])
                return result()
            if operation == "clear_material":
                controller.clear_material(values["part_id"])
                return result()
            if operation == "set_quantity":
                controller.set_quantity(values["quantity"])
                return result()
            if operation == "list_jobs":
                pending = controller.list_jobs(offset=values["offset"], limit=values["limit"])
            elif operation in ("inspect_job", "resume_job"):
                pending = getattr(controller, operation)(values["job_key"])
            elif operation == "load_materials":
                pending = controller.load_materials(more=values["more"])
            else:
                method = {"analyze": controller.analyze, "quote": controller.request_quote,
                          "refresh": controller.refresh, "checkout": controller.checkout}[operation]
                pending = method()
        except (RuntimeError, ValueError) as error:
            raise NativeSheetMetalManufacturingError(str(error)) from error

        response = Future()
        def completed(future):
            if future.cancelled():
                response.cancel()
                return
            if not response.set_running_or_notify_cancel():
                return
            try:
                future.result()
                guard()
                extra = {}
                if operation == "checkout":
                    extra["browser_opened"] = bool(Manufacturing.open_checkout(controller))
                payload = result(**extra)
            except Exception as error:
                response.set_exception(error)
            else:
                response.set_result(payload)
        response.add_done_callback(lambda future: pending.cancel() if future.cancelled() else None)
        pending.add_done_callback(completed)
        return response
