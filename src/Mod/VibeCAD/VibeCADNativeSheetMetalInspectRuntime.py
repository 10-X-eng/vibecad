# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read exact shared sheet states through the same inspection used by the ribbon."""

from collections.abc import Mapping
from concurrent.futures import Future

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSheetMetalInspectSchema import MAX_OFFSET, MAX_PAGE_SIZE, MAX_REVISION
from VibeCADNativeState import NativeRevisionConflict
from VibeCADNativeTargets import NativeObjectRef, NativeTargetError, resolve_object


_PAGING = frozenset({"offset", "page_size", "expected_revision"})
_FIELDS = {"list_sheets": _PAGING, "read_sheet": frozenset({"target"}),
           **{name: _PAGING | {"target"} for name in ("list_history", "list_cuts", "list_regions")}}
_DEFAULTS = {name: {"offset": 0, "page_size": MAX_PAGE_SIZE, "expected_revision": None}
             for name in _FIELDS if name != "read_sheet"}


class NativeSheetMetalInspectError(RuntimeError):
    def failure(self):
        return {"error_code": "NATIVE_SHEETMETAL_STATE_UNAVAILABLE", "message": str(self),
                "repair": "Inspect read_sheet. For an unprepared reopened sheet, use sheet_metal.inspect operation=prepare with its exact target. Repair invalid inputs or return History to an active state before reading geometry."}


def _page(items, values, revision):
    offset, size = values["offset"], values["page_size"]
    end = min(offset + size, len(items))
    return {"items": items[offset:end], "offset": offset, "total": len(items),
            "next_offset": end if end < len(items) else None, "structural_revision": revision}


def _validate_page(values, current):
    for name, lower, upper in (("offset", 0, MAX_OFFSET), ("page_size", 1, MAX_PAGE_SIZE),
                               ("expected_revision", 0, MAX_REVISION)):
        value = values[name]
        if name == "expected_revision" and value is None:
            continue
        if type(value) is not int or not lower <= value <= upper:
            raise NativeArgumentError(f"{name} must be an integer between {lower} and {upper}.")
    expected = values["expected_revision"]
    if values["offset"] and expected is None:
        raise NativeArgumentError("Pass expected_revision from the first page's structural_revision.")
    if expected is not None and expected != current:
        raise NativeRevisionConflict(expected, current)


class NativeSheetMetalInspectRuntime:
    def __init__(self, context):
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _edit_guidance(self):
        if self._context.active_surface_id() == "sheet_metal":
            return {}
        return {"edit_guidance": "For sheet edits, use workspace.switch with workspace=sheet_metal. "
                "End this turn; VibeCAD continues with the editing tools on the next turn."}

    def inspect_async(self, arguments):
        if not isinstance(arguments, Mapping) or arguments.get("operation") != "prepare":
            return self.inspect(arguments)
        _, values = strict_variant_arguments(arguments, {"prepare": frozenset({"target"})})
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        from SheetMetalPreparation import start_preparation
        from SheetMetalPresentation import _gui_thread
        _gui_thread()
        context = self._context
        context.guard()
        target = values["target"]
        if (not isinstance(target, Mapping) or set(target) != {"document_uid", "object_name"}
                or any(not isinstance(value, str) for value in target.values())):
            raise NativeArgumentError("target requires document_uid and object_name from list_sheets.")
        reference = NativeObjectRef.from_mapping(target)
        sheet = resolve_object(context.document, reference)
        if not isinstance(getattr(sheet, "Proxy", None), Editable.PreparedSheetState):
            raise NativeTargetError("Select a shared sheet state from list_sheets.",
                                    exact_target=reference.summary(), actual_type=sheet.TypeId)
        try:
            revision = Shared.capture_revision(sheet)
            run = start_preparation(sheet, expected_revision=revision)
        except (RuntimeError, ValueError) as error:
            raise NativeSheetMetalInspectError(str(error)) from error
        future = Future()

        def completed(pending):
            if not future.set_running_or_notify_cancel():
                return
            try:
                context.guard()
                result = pending.result()
                current = Shared.capture_revision(sheet).summary()
                if (current != revision.summary() or result.get("object_name") != sheet.Name
                        or result.get("input_hash") != current["input_hash"]):
                    raise NativeSheetMetalInspectError("The sheet changed during preparation; inspect its current revision.")
                Editable.get_state_geometry(sheet)
                future.set_result({"target": reference.summary(), "prepared": True, "revision": current,
                                   **self._edit_guidance()})
            except Exception as error:
                future.set_exception(error if callable(getattr(error, "failure", None))
                                     else NativeSheetMetalInspectError(str(error)))

        future.add_done_callback(lambda result, owned=run: owned.future.cancel() if result.cancelled() else None)
        run.future.add_done_callback(completed)
        return future

    def inspect(self, arguments):
        operation, values = strict_variant_arguments(arguments, _FIELDS, defaults=_DEFAULTS)
        # Keep geometry/UI imports lazy: constructing the production registry
        # must not load a workbench or touch a document.
        import SheetMetalEditable as Editable
        import SheetMetalOperations as Operations
        import SheetMetalHistoryOperations as Shared
        from SheetMetalPresentation import _gui_thread
        _gui_thread()
        context = self._context
        context.guard()
        document = context.document
        Operations._document_ready(document)
        revision = context.state.current_revision(context.document_uid)
        if operation != "read_sheet":
            _validate_page(values, revision)
        if operation == "list_sheets":
            items = [{"target": {"document_uid": context.document_uid, "object_name": obj.Name},
                      "label": obj.Label, "state": list(obj.State),
                      "visibility": bool(obj.Visibility),
                      "active": document.isObjectUsableAtCurrentTimelinePosition(obj),
                      "suppressed": bool(getattr(obj, "Suppressed", False))}
                     for obj in document.Objects
                     if isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState)]
            return {"document_uid": context.document_uid, **_page(items, values, revision),
                    **self._edit_guidance()}
        target = values["target"]
        if (not isinstance(target, Mapping) or set(target) != {"document_uid", "object_name"}
                or any(not isinstance(value, str) for value in target.values())):
            raise NativeArgumentError("target requires only document_uid and object_name strings from list_sheets.")
        reference = NativeObjectRef.from_mapping(target)
        obj = resolve_object(document, reference)
        if not isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState):
            raise NativeTargetError("Select a shared sheet state from list_sheets.",
                                    exact_target=reference.summary(), actual_type=obj.TypeId)
        try:
            result = Shared.inspect(obj)
        except (RuntimeError, ValueError) as error:
            raise NativeSheetMetalInspectError(str(error)) from error
        if operation == "read_sheet":
            overview = {name: value for name, value in result.items()
                        if name not in ("definition", "cuts", "regions", "history")}
            overview["target"] = reference.summary()
            overview["history"] = {name: value for name, value in result["history"].items()
                                   if name != "states"}
            overview["counts"] = {"history_states": len(result["history"]["states"]),
                                  "cuts": len(result["cuts"]), "regions": len(result["regions"])}
            overview["sketch_creation"] = {
                "tool": "workspace.switch", "arguments": {"workspace": "sketching"},
                "message": ("Create editable relief-profile or bend-line sketches in Sketching. "
                            "End this turn after switching; sketch-creation tools arrive next turn. "
                            "Finish the sketch and return to sheet_metal for cuts or folds."),
            }
            return {**overview, **self._edit_guidance()}
        if operation == "list_regions" and not result["prepared"]:
            raise NativeSheetMetalInspectError(result.get("error", "The current sheet geometry is not prepared."))
        items = result["history"]["states"] if operation == "list_history" else result[operation[5:]]
        return {"target": reference.summary(), "revision": result["revision"],
                **_page(items, values, result["revision"]["structural_revision"]), **self._edit_guidance()}
