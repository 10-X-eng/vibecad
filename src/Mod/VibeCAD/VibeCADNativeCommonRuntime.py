# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact runtime handlers for the five shared Native capability families."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeDocument import guarded_save
from VibeCADNativeInspect import (
    geometry_validity,
    inspect_element,
    visual_inspection_result,
)
from VibeCADNativeMeasure import (
    mass_properties,
    measure_angle,
    measure_distance,
    measure_radius,
)
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import (
    NativeElementRef,
    NativeObjectRef,
    read_current_selection,
)
from VibeCADNativeView import (
    capture_screenshot,
    fit_all,
    set_grid_visible,
    set_isometric,
)


class NativeCommonRuntimeError(RuntimeError):
    def failure(self) -> dict[str, str]:
        return {"error_code": "NATIVE_COMMON_CALL_FAILED", "message": str(self)}


class NativeCommonRuntime:
    """Bind shared handlers to one exact document and frozen human ribbon turn."""

    def __init__(
        self,
        *,
        context: NativeRuntimeContext,
    ) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context
        self._service = context.service
        self._document = context.document
        self._document_uid = context.document_uid
        self._state = context.state
        self._undo = context.undo_ledger
        self._reauthorize_turn = context.reauthorize_turn
        self._active_document = context.active_document
        self._active_surface_id = context.active_surface_id
        self._edit_or_task_active = context.edit_or_task_active

    def _guard(self) -> None:
        self._context.guard()

    def _object(self, value: Mapping[str, Any]) -> NativeObjectRef:
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeCommonRuntimeError("An exact object target is invalid.")
        return NativeObjectRef(self._document_uid, str(value["object_name"]))

    def _element(self, value: Mapping[str, Any]) -> NativeElementRef:
        if not isinstance(value, Mapping) or set(value) != {
            "object_name",
            "subelement",
        }:
            raise NativeCommonRuntimeError("An exact subelement target is invalid.")
        return NativeElementRef(
            NativeObjectRef(self._document_uid, str(value["object_name"])),
            str(value["subelement"]),
        )

    def _snapshot(self) -> dict[str, Any]:
        return build_active_snapshot(
            self._document,
            str(self._active_surface_id()),
            self._state.snapshot(self._document_uid),
        )

    def read_state(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, _values = strict_variant_arguments(
            arguments,
            {"active": frozenset(), "selection": frozenset()},
        )
        self._guard()
        if operation == "selection":
            return read_current_selection(self._document)
        return self._snapshot()

    def control_view(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "fit_all": frozenset(),
                "isometric": frozenset(),
                "set_grid": frozenset({"visible"}),
                "capture_all": frozenset(),
                "capture_selection": frozenset(),
                "capture_objects": frozenset({"targets"}),
                "capture_active_sketch": frozenset(),
            },
        )
        self._guard()
        if operation == "fit_all":
            return fit_all(self._document)
        if operation == "isometric":
            return set_isometric(self._document)
        if operation == "set_grid":
            return set_grid_visible(self._document, values["visible"])
        frames = {
            "capture_all": "all",
            "capture_selection": "selection",
            "capture_objects": "objects",
            "capture_active_sketch": "active_sketch",
        }
        targets = tuple(
            self._object(value) for value in list(values.get("targets") or [])
        )
        if operation == "capture_objects" and not 1 <= len(targets) <= 16:
            raise NativeCommonRuntimeError(
                "Object-framed capture requires 1 to 16 exact targets."
            )
        return capture_screenshot(
            self._service,
            self._document,
            frame=frames[operation],
            targets=targets,
        )

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "distance": frozenset({"first", "second"}),
                "angle": frozenset({"first", "second"}),
                "radius": frozenset({"target"}),
                "mass_properties": frozenset({"targets"}),
                "visual_result": frozenset({"target"}),
                "element": frozenset({"target"}),
                "validity": frozenset({"target"}),
            },
        )
        self._guard()
        if operation == "distance":
            return measure_distance(
                self._document,
                self._element(values["first"]),
                self._element(values["second"]),
            )
        if operation == "angle":
            return measure_angle(
                self._document,
                self._element(values["first"]),
                self._element(values["second"]),
            )
        if operation == "radius":
            return measure_radius(self._document, self._element(values["target"]))
        if operation == "mass_properties":
            targets = tuple(self._object(value) for value in list(values["targets"]))
            return mass_properties(self._document, targets)
        if operation == "element":
            return inspect_element(
                self._document,
                self._element(values["target"]),
            )
        target = self._object(values["target"])
        if operation == "visual_result":
            return visual_inspection_result(self._document, target)
        return geometry_validity(self._document, target)

    def save_document(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _operation, _values = strict_variant_arguments(
            arguments,
            {"existing_path": frozenset()},
        )
        self._guard()
        return guarded_save(
            self._document,
            active_document=self._active_document,
            edit_or_task_active=self._edit_or_task_active,
        )

    def undo_document(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, _values = strict_variant_arguments(
            arguments,
            {"assistant_local": frozenset()},
        )
        self._guard()
        execution = self._undo.undo_latest(
            ticket=ticket,
            document=self._document,
            state=self._state,
            reauthorize_turn=self._reauthorize_turn,
            active_document=self._active_document,
        )
        return {"result": execution.result, "state": self._snapshot()}
