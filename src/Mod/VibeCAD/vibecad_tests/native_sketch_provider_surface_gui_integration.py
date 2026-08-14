# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI gate for the compact revision-based Sketch provider surface."""

from __future__ import annotations

import json
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADProvider as Provider
from VibeCADCore import get_service
from VibeCADEditState import active_edit_object
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import (
    _definition_covers,
    _required_actions,
    _shared_requirements,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_geometry_gui_support import process_events


def _operation_schema(provider, tool_name: str, operation: str) -> dict:
    schema = next(item for item in provider.schemas if item["name"] == tool_name)
    parameters = schema["parameters"]
    branches = parameters.get("oneOf", [parameters])
    for branch in branches:
        operation_schema = branch.get("properties", {}).get("operation", {})
        if operation_schema.get("const") == operation or operation in operation_schema.get(
            "enum", []
        ):
            return branch
    raise AssertionError(f"Missing {tool_name} operation schema {operation}")


def _coverage_gaps(surface, registry) -> list[dict]:
    inventory = resolve_native_action_inventory(surface)
    requirements = (
        *_shared_requirements(surface.surface_id, registry),
        *_required_actions(surface, inventory.plans),
    )
    return [
        {
            "family": requirement.capability_family,
            "action": requirement.action_id,
            "operation": requirement.operation_variant,
            "target": requirement.exact_target_type,
        }
        for requirement in requirements
        if (
            (definition := registry.definition(requirement.capability_family))
            is not None
            and not _definition_covers(
                definition,
                requirement,
                surface.surface_id,
            )
        )
    ]


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeSketchProviderSurfaceGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "ProviderSketch")
        document.recompute()
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)

        controller = Gui.getMainWindow().findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "sketch.edit"
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        provider = resolve_native_provider_surface(surface, registry)
        assert provider.available is True, {
            **provider.debug_summary(),
            "coverage_gaps": _coverage_gaps(surface, registry),
        }
        assert len(provider.tool_names) <= 28
        schema_bytes = len(
            json.dumps(
                provider.schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert schema_bytes <= 64 * 1024
        assert not {
            "sketch.geometry",
            "sketch.constraint",
            "sketch.cut",
        } & set(provider.tool_names)
        required = {
            "sketch.inspect",
            "sketch.draw_line",
            "sketch.draw_arc",
            "sketch.draw_three_point_arc",
            "sketch.draw_circle",
            "sketch.draw_ellipse",
            "sketch.draw_profile",
            "sketch.draw_spline",
            "sketch.draw_text",
            "sketch.constrain",
            "sketch.dimension",
            "sketch.transform",
            "sketch.edit",
            "sketch.trim",
            "sketch.split",
            "sketch.extend",
            "sketch.delete",
            "sketch.fillet",
            "sketch.chamfer",
            "sketch.external",
            "sketch.batch",
            "sketch.control",
        }
        assert required <= set(provider.tool_names)
        batch_schema = _operation_schema(provider, "sketch.batch", "create")
        batch_constraints_schema = batch_schema["properties"]["constraints"]
        assert batch_constraints_schema["maxItems"] == 128
        assert "128" in batch_constraints_schema["description"]
        inspect_schema = _operation_schema(
            provider,
            "sketch.inspect",
            "read_state",
        )
        page_size_schema = inspect_schema["properties"]["page_size"]
        assert page_size_schema["minimum"] == 1
        assert page_size_schema["maximum"] == 48
        assert "1 through 48" in page_size_schema["description"]

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-sketch-provider-surface-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: active_edit_object() is not None,
        )
        turn = NativeTurnSnapshot.from_provider_surface(provider)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        call_number = 0

        def call(tool: str, arguments: dict) -> dict:
            nonlocal call_number
            call_number += 1
            return dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"provider-surface-{call_number}",
            )

        turn_start_state = service.native_active_snapshot()
        assert turn_start_state["revision"].startswith("sketch-v1:")
        assert "revision" not in turn_start_state["domain"]
        first = call("sketch.inspect", {"operation": "read_state"})
        assert first.get("ok") is True, first
        first_revision = first["revision"]
        assert turn_start_state["revision"] == first_revision
        assert first["state"]["geometry_count"] == 0

        invalid = call(
            "sketch.draw_line",
            {
                "operation": "create_line",
                "revision": first_revision,
                "start_mm": {"x": 0.0, "y": 0.0},
            },
        )
        assert invalid.get("ok") is False, invalid
        assert invalid.get("error_code") == "NATIVE_ARGUMENTS_INVALID", invalid
        assert invalid.get("argument_error", {}).get("required_field") == "end_mm"
        assert invalid.get("argument_error", {}).get("valid_example")
        assert int(sketch.GeometryCount) == 0

        line = call(
            "sketch.draw_line",
            {
                "operation": "create_line",
                "revision": first_revision,
                "start_mm": {"x": 0.0, "y": 0.0},
                "end_mm": {"x": 25.0, "y": 0.0},
            },
        )
        assert line.get("ok") is True, line
        second_revision = line["revision"]
        assert second_revision != first_revision
        assert int(sketch.GeometryCount) == 1

        stale = call(
            "sketch.constrain",
            {
                "operation": "constrain_horizontal",
                "revision": first_revision,
                "selection": [{"geometry_index": 0, "position": "whole"}],
            },
        )
        assert stale.get("ok") is False, stale
        assert stale.get("error_code") == "NATIVE_SKETCH_REVISION_CONFLICT", stale
        assert stale.get("current_revision") == second_revision
        assert int(sketch.ConstraintCount) == 0

        constrained = call(
            "sketch.constrain",
            {
                "operation": "constrain_horizontal",
                "revision": second_revision,
                "selection": [{"geometry_index": 0, "position": "whole"}],
            },
        )
        assert constrained.get("ok") is True, constrained
        third_revision = constrained["revision"]
        assert third_revision != second_revision
        assert int(sketch.ConstraintCount) == 1

        final = call("sketch.inspect", {"operation": "read_state"})
        assert final.get("ok") is True, final
        assert final["revision"] == third_revision
        assert final["state"]["geometry_count"] == 1
        assert final["state"]["constraint_count"] == 1

        batch_geometry = [
            {
                "ref": f"batch_line_{index}",
                "kind": "line",
                "construction": False,
                "start_mm": {
                    "x": 0.0 if index == 0 else 40.0,
                    "y": 0.0 if index == 0 else 20.0 + index * 2.0,
                },
                "end_mm": {
                    "x": 8.0 if index == 0 else 55.0,
                    "y": 1.0 if index == 0 else 20.0 + index * 2.0,
                },
            }
            for index in range(28)
        ]
        batch_constraints = [
            {
                "ref": "origin_link",
                "kind": "coincident",
                "first": {
                    "geometry_ref": "batch_line_0",
                    "position": "start",
                },
                "second": {"origin": True, "position": "point"},
            },
            *[
                {
                    "ref": f"horizontal_{index}",
                    "kind": "horizontal",
                    "geometry_ref": f"batch_line_{index}",
                }
                for index in range(1, 28)
            ],
        ]
        assert len(batch_constraints) == 28
        batch = call(
            "sketch.batch",
            {
                "operation": "create",
                "revision": third_revision,
                "geometry": batch_geometry,
                "constraints": batch_constraints,
            },
        )
        assert batch.get("ok") is True, batch
        batch_revision = batch["revision"]
        assert int(sketch.GeometryCount) == 29
        assert int(sketch.ConstraintCount) == 29

        overlap_first = call(
            "sketch.draw_line",
            {
                "operation": "create_line",
                "revision": batch_revision,
                "start_mm": {"x": 80.0, "y": 10.0},
                "end_mm": {"x": 90.0, "y": 20.0},
            },
        )
        assert overlap_first.get("ok") is True, overlap_first
        overlap_second = call(
            "sketch.draw_line",
            {
                "operation": "create_line",
                "revision": overlap_first["revision"],
                "start_mm": {"x": 90.0, "y": 20.0},
                "end_mm": {"x": 100.0, "y": 10.0},
            },
        )
        assert overlap_second.get("ok") is True, overlap_second
        coincidence = call(
            "sketch.constrain",
            {
                "operation": "constrain_coincident",
                "revision": overlap_second["revision"],
                "target": {
                    "form": "point_point",
                    "first_point": {"geometry_index": 29, "position": "end"},
                    "second_point": {"geometry_index": 30, "position": "start"},
                },
            },
        )
        assert coincidence.get("ok") is True, coincidence
        assert int(sketch.ConstraintCount) == 30
        assert str(sketch.Constraints[-1].Type) == "Coincident"

        semicircle = call(
            "sketch.draw_arc",
            {
                "operation": "create_arc",
                "revision": coincidence["revision"],
                "center_mm": {"x": 120.0, "y": 20.0},
                "radius_mm": 10.0,
                "start_angle_degrees": 0.0,
                "sweep_angle_degrees": 180.0,
            },
        )
        assert semicircle.get("ok") is True, semicircle
        radius = call(
            "sketch.dimension",
            {
                "operation": "constrain_radius",
                "revision": semicircle["revision"],
                "selection": [{"geometry_index": 31, "position": "whole"}],
                "dimension": {"value": 10.0, "unit": "mm"},
                "driving": True,
            },
        )
        assert radius.get("ok") is True, radius
        redundant_radius = call(
            "sketch.dimension",
            {
                "operation": "constrain_radius",
                "revision": radius["revision"],
                "selection": [{"geometry_index": 31, "position": "whole"}],
                "dimension": {"value": 10.0, "unit": "mm"},
                "driving": True,
            },
        )
        assert redundant_radius.get("ok") is False, redundant_radius
        assert (
            redundant_radius.get("error_code")
            == "NATIVE_SKETCH_CONSTRAINT_REDUNDANT"
        ), redundant_radius
        repair = redundant_radius["repair"]
        assert repair["tool"] == "sketch.dimension"
        assert repair["arguments"] == {
            "operation": "constrain_angle",
            "selection": [{"geometry_index": 31, "position": "whole"}],
            "expected_form": "circular_arc_span",
            "dimension": {"value": 180.0, "unit": "deg"},
            "driving": True,
            "revision": radius["revision"],
        }
        assert int(sketch.ConstraintCount) == 31

        captured = call(
            "view.control",
            {"operation": "capture_active_sketch"},
        )
        assert captured.get("ok") is True, captured
        assert captured.get("captured") is True, captured
        assert "artifact" not in captured, captured
        assert captured.get("image", {}).get("mime_type") == "image/png", captured
        assert captured.get("_vibecad_image_attachment", {}).get("path"), captured
        capture_context = Provider._tool_result_image_context(captured)
        assert capture_context is not None
        capture_items = Provider._codex_tool_image_content_items(capture_context)
        typed_images = [
            item for item in capture_items if item.get("type") == "inputImage"
        ]
        assert len(typed_images) == 1
        assert typed_images[0]["imageUrl"].startswith("data:image/")
        assert all(
            "data:" not in str(item.get("text") or "")
            for item in capture_items
            if item.get("type") == "inputText"
        )
        expected_orientation = App.Placement(
            Gui.activeDocument().EditingTransform
        ).Rotation
        current_orientation = Gui.activeDocument().activeView().getCameraOrientation()
        assert current_orientation.isSame(expected_orientation, 1.0e-9)

        deleted = call(
            "sketch.delete",
            {
                "operation": "delete_geometry",
                "revision": radius["revision"],
                "geometry_indices": [30],
            },
        )
        assert deleted.get("ok") is True, deleted
        leave_revision = deleted["revision"]
        assert int(sketch.GeometryCount) == 31
        assert int(sketch.ConstraintCount) == 30

        left = call(
            "sketch.control",
            {"operation": "leave", "revision": leave_revision},
        )
        assert left.get("ok") is True, left
        assert left.get("next_turn_required") is True, left
        assert active_edit_object() is None

        print(
            "VIBECAD_NATIVE_SKETCH_PROVIDER_SURFACE_GUI_OK "
            f"tools={len(provider.tool_names)} schemas={schema_bytes}B "
            "turn_revision batch128 origin coincidence repair typed_capture "
            "delete leave diagnostics",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=__import__("sys").__stderr__)
    finally:
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
