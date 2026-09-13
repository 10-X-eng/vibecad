# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact runtime handlers for the five shared Native capability families."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyProviderState import provider_assembly_state
from VibeCADNativeDocument import guarded_save
from VibeCADNativeDrawingGeometryState import (
    MAX_DRAWING_PROJECTED_PAGE_SIZE,
    drawing_projected_geometry_page,
    provider_projected_geometry_page,
)
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeDrawingSourceCatalog import (
    MAX_DRAWING_SOURCE_PAGE_SIZE,
    drawing_source_catalog_page,
)
from VibeCADNativeInspect import (
    MAX_INSPECTION_ELEMENTS,
    geometry_validity,
    inspect_element,
    visual_inspection_result,
)
from VibeCADNativeMeasure import (
    MAX_RADIUS_MEASUREMENTS,
    mass_properties,
    measure_angle,
    measure_distance,
    measure_radius,
)
from VibeCADNativeProviderContext import provider_visible_native_state
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import (
    NativeElementRef,
    NativeObjectRef,
    read_current_selection,
    resolve_object,
)
from VibeCADNativeView import (
    capture_screenshot,
    fit_all,
    set_object_visibility,
    set_grid_visible,
    set_isometric,
    set_standard_view,
    set_section_view_visible,
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
        self._scoped_capability_prefix = context.scoped_capability_prefix

    def _guard(
        self,
        *,
        allow_owned_playback: bool = False,
        allow_owned_cam_simulation: bool = False,
    ) -> None:
        self._context.guard(
            allow_owned_playback=allow_owned_playback,
            allow_owned_cam_simulation=allow_owned_cam_simulation,
        )

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
        surface_id = str(self._active_surface_id())
        snapshot = build_active_snapshot(
            self._document,
            surface_id,
            self._state.snapshot(self._document_uid),
        )
        if surface_id == "assemble" and isinstance(
            snapshot.get("domain"), Mapping
        ):
            snapshot["domain"] = provider_assembly_state(snapshot["domain"])
        return provider_visible_native_state(snapshot)

    def _drawing_projected_geometry(
        self,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        target = values["view"]
        if (
            not isinstance(target, Mapping)
            or "object_name" not in target
            or not set(target)
            <= {
                "object_name",
                "expected_state_sha256",
                "expected_projection_state_sha256",
            }
        ):
            raise NativeCommonRuntimeError(
                "An exact Drawing view target is invalid."
            )
        view = resolve_object(
            self._document,
            NativeObjectRef(self._document_uid, str(target["object_name"])),
            expected_types=("TechDraw::DrawViewPart",),
        )
        expected_view_state = str(target.get("expected_state_sha256") or "")
        if (
            expected_view_state
            and drawing_view_state(view)["state_sha256"] != expected_view_state
        ):
            raise NativeCommonRuntimeError(
                "The exact Drawing view changed after it was inspected."
            )
        page_arguments = {
            "offset": int(values["offset"]),
            "page_size": MAX_DRAWING_PROJECTED_PAGE_SIZE,
        }
        expected_projection = str(
            target.get("expected_projection_state_sha256") or ""
        )
        if expected_projection:
            page_arguments["expected_projection_state_sha256"] = expected_projection
        return provider_projected_geometry_page(
            drawing_projected_geometry_page(view, **page_arguments),
            view=view,
        )

    def read_state(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, _values = strict_variant_arguments(
            arguments,
            {"active": frozenset(), "selection": frozenset()},
        )
        self._guard(
            allow_owned_playback=True,
            allow_owned_cam_simulation=True,
        )
        if operation == "selection":
            return read_current_selection(self._document)
        return self._snapshot()

    def control_view(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(arguments)
        if normalized.get("operation") == "set_grid" and "visible" not in normalized:
            normalized["visible"] = True
        operation, values = strict_variant_arguments(
            normalized,
            {
                "fit_all": frozenset(),
                "isometric": frozenset(),
                "set_isometric": frozenset(),
                "set_front": frozenset(),
                "set_rear": frozenset(),
                "set_left": frozenset(),
                "set_right": frozenset(),
                "set_top": frozenset(),
                "set_bottom": frozenset(),
                "set_grid": frozenset({"visible"}),
                "set_section_view": frozenset({"visible"}),
                "set_object_visibility": frozenset({"targets", "visible"}),
                "capture_all": frozenset(),
                "capture_selection": frozenset(),
                "capture_objects": frozenset({"targets"}),
                "capture_drawing_page": frozenset({"page"}),
                "capture_active_sketch": frozenset(),
            },
        )
        self._guard(allow_owned_playback=True)
        if operation == "fit_all":
            return fit_all(self._document)
        if operation in {"isometric", "set_isometric"}:
            return set_isometric(self._document)
        if operation.startswith("set_") and operation.removeprefix("set_") in {
            "front",
            "rear",
            "left",
            "right",
            "top",
            "bottom",
        }:
            return set_standard_view(
                self._document,
                operation.removeprefix("set_"),
            )
        if operation == "set_grid":
            return set_grid_visible(self._document, values["visible"])
        if operation == "set_section_view":
            return set_section_view_visible(self._document, values["visible"])
        if operation == "set_object_visibility":
            targets = tuple(
                self._object(value) for value in list(values["targets"])
            )
            return set_object_visibility(
                self._document,
                targets,
                values["visible"],
            )
        frames = {
            "capture_all": "all",
            "capture_selection": "selection",
            "capture_objects": "objects",
            "capture_drawing_page": "all",
            "capture_active_sketch": "active_sketch",
        }
        targets = tuple(
            self._object(value) for value in list(values.get("targets") or [])
        )
        if operation == "capture_objects" and not 1 <= len(targets) <= 16:
            raise NativeCommonRuntimeError(
                "Object-framed capture requires 1 to 16 exact targets."
            )
        capture_arguments: dict[str, Any] = {
            "frame": frames[operation],
            "targets": targets,
        }
        if operation == "capture_drawing_page":
            capture_arguments["page_name"] = str(values["page"]["object_name"])
        return capture_screenshot(
            self._service,
            self._document,
            **capture_arguments,
        )

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "distance": frozenset({"targets"}),
                "angle": frozenset({"targets"}),
                "radius": frozenset({"targets"}),
                "mass_properties": frozenset({"targets"}),
                "inspection_result": frozenset({"targets"}),
                "element": frozenset({"targets"}),
                "validity": frozenset({"targets"}),
            },
        )
        self._guard(
            allow_owned_playback=True,
            allow_owned_cam_simulation=True,
        )
        targets = list(values.get("targets") or [])
        if operation == "distance":
            return measure_distance(
                self._document,
                self._element(targets[0]),
                self._element(targets[1]),
            )
        if operation == "angle":
            return measure_angle(
                self._document,
                self._element(targets[0]),
                self._element(targets[1]),
            )
        if operation == "radius":
            if not 1 <= len(targets) <= MAX_RADIUS_MEASUREMENTS:
                raise NativeCommonRuntimeError(
                    "Radius inspection requires 1 to "
                    f"{MAX_RADIUS_MEASUREMENTS} exact elements."
                )
            measurements = [
                measure_radius(self._document, self._element(target))
                for target in targets
            ]
            return measurements[0] if len(measurements) == 1 else {
                "measurements": measurements
            }
        if operation == "mass_properties":
            return mass_properties(
                self._document,
                tuple(self._object(value) for value in targets),
            )
        if operation == "element":
            if not 1 <= len(targets) <= MAX_INSPECTION_ELEMENTS:
                raise NativeCommonRuntimeError(
                    "Element inspection requires 1 to "
                    f"{MAX_INSPECTION_ELEMENTS} exact elements."
                )
            elements = [
                inspect_element(self._document, self._element(target))
                for target in targets
            ]
            return elements[0] if len(elements) == 1 else {"elements": elements}
        target = self._object(targets[0])
        if operation == "inspection_result":
            return visual_inspection_result(self._document, target)
        return geometry_validity(self._document, target)

    def read_drawing_sources(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(arguments)
        normalized.setdefault("offset", 0)
        _operation, values = strict_variant_arguments(
            normalized,
            {"list": frozenset({"offset"})},
        )
        self._guard(allow_owned_playback=True)
        structural_revision = int(
            self._state.snapshot(self._document_uid).get("structural_revision", 0)
            or 0
        )
        return drawing_source_catalog_page(
            self._document,
            offset=int(values["offset"]),
            page_size=MAX_DRAWING_SOURCE_PAGE_SIZE,
            structural_revision=structural_revision,
            require_cached=self._context.document_thread_dispatch is not None,
        )

    def read_projected_geometry(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(arguments)
        normalized.setdefault("offset", 0)
        _operation, values = strict_variant_arguments(
            normalized,
            {
                "read": frozenset(
                    {
                        "view",
                        "offset",
                    }
                )
            },
        )
        self._guard(allow_owned_playback=True)
        return self._drawing_projected_geometry(values)

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
            capability_prefix=self._scoped_capability_prefix,
        )
        result = {"result": execution.result}
        try:
            result["state"] = self._snapshot()
        except Exception:
            # The undo is already committed and independently verified. The
            # provider runner refreshes live context after this successful call.
            pass
        return result
