# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI and provider-boundary gate for a usable fresh Sketch turn."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets
import SketcherGui

import VibeCADGui as VibeGui
import VibeCADNativeSketchConstraintRuntime as ConstraintRuntimeModule
import VibeCADNativeSketchGeometryRuntime as GeometryRuntimeModule
import VibeCADProvider as ProviderModule
from VibeCADCore import get_service
from VibeCADEditState import active_edit_object
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeProviderContext import resolve_production_native_surface
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSessionFactory import create_native_session_execution
from VibeCADNativeSketchGeometryBindings import SKETCH_GEOMETRY_CAPABILITY_NAME
from VibeCADRibbonSurface import read_active_ribbon_surface
from VibeCADSession import _capture_context_for_provider
from vibecad_tests.native_sketch_geometry_gui_support import (
    edit_boundary,
    line_arguments,
    process_events,
)
from vibecad_tests.native_sketch_virtual_space_gui_case import (
    exercise_virtual_space_case,
    verify_reopened_virtual_space,
)


def _phase(name: str) -> None:
    os.write(2, f"VIBECAD_NATIVE_SKETCH_VIRTUAL_SPACE_PHASE {name}\n".encode())


def _accept_orientation_dialog() -> None:
    attempts = [0]

    def accept() -> None:
        attempts[0] += 1
        modal = QtWidgets.QApplication.activeModalWidget()
        if modal is None:
            if attempts[0] < 200:
                QtCore.QTimer.singleShot(5, accept)
            return
        modal.accept()

    QtCore.QTimer.singleShot(0, accept)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    execution = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeFreshSketchGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        service = get_service()
        service.select_modeling_engine("native")
        process_events(16)

        controller = Gui.getMainWindow().findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        model_surface = read_active_ribbon_surface(controller)
        assert model_surface.surface_id == "model"
        model_provider = resolve_native_provider_surface(
            model_surface,
            build_native_capability_registry(),
        )
        assert model_provider.available is True, model_provider.debug_summary()
        assert model_provider.tool_names

        original_objects = tuple(document.Objects)
        original_undo_count = int(document.UndoCount)
        Gui.activateWorkbench("SketcherWorkbench")
        Gui.Selection.clearSelection()
        _accept_orientation_dialog()
        Gui.runCommand("Sketcher_NewSketch", 0)
        process_events(100)
        created_sketches = [
            obj
            for obj in document.Objects
            if obj not in original_objects
            and obj.isDerivedFrom("Sketcher::SketchObject")
        ]
        assert len(created_sketches) == 1, created_sketches
        sketch = created_sketches[0]
        sketch.Label = "Fresh Native Sketch provider gate"
        assert active_edit_object() is sketch
        assert int(document.getBookedTransactionID()) != 0
        assert bool(document.HasPendingTransaction)
        assert document.isProvisionallyEnrolledInTimelineByCurrentTransaction(sketch)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "sketch.edit"
        assert active_edit_object() is sketch
        production_registry, production = resolve_production_native_surface()
        assert production.available is True, production.debug_summary()
        assert production.missing_action_ids == ()
        assert production.missing_definition_names == ()
        assert production.missing_implementation_names == ()
        assert production.incomplete_definition_names == ()
        assert production.tool_names
        assert production.schemas
        assert "sketch.geometry" in production.tool_names
        assert "sketch.constraint" in production.tool_names
        assert production.tool_names != model_provider.tool_names
        constraint_schema = next(
            schema for schema in production.schemas if schema["name"] == "sketch.constraint"
        )
        assert "set_virtual_space" in json.dumps(constraint_schema, sort_keys=True)

        plan_context = _capture_context_for_provider(
            service,
            interaction_mode="plan",
        )
        plan_names = plan_context["provider_tool_surface"]["tool_names"]
        assert plan_names
        assert set(plan_names) < set(production.tool_names)
        assert "sketch.geometry" not in plan_names
        assert "sketch.constraint" not in plan_names
        assert plan_context["_vibecad_codex_thread_surface"][
            "provider_tool_surface"
        ]["tool_names"] == list(production.tool_names)

        _phase("provider_surface")
        context = _capture_context_for_provider(service)
        turn_surface = context["provider_tool_surface"]
        schemas = context["provider_tool_schemas"]
        assert turn_surface["kind"] == "turn_start_snapshot", turn_surface
        assert turn_surface["frozen"] is True
        assert turn_surface["tool_names"] == list(production.tool_names)
        assert schemas
        codex_tools, codex_names = ProviderModule._codex_dynamic_tool_surface(context)
        assert codex_tools
        assert codex_names
        assert set(codex_names.values()) == set(production.tool_names)
        assert sum(len(namespace["tools"]) for namespace in codex_tools) == len(
            production.tool_names
        )

        execution = create_native_session_execution(
            service=service,
            expected_surface=turn_surface,
            expected_schemas=schemas,
            registry=production_registry,
            controller=controller,
        )
        boundary = edit_boundary(document, sketch, controller)
        line_response = execution.dispatcher.call(
            SKETCH_GEOMETRY_CAPABILITY_NAME,
            json.dumps(
                line_arguments(
                    sketch,
                    geometry_count=0,
                    start=(-5.0, 0.0),
                    end=(5.0, 0.0),
                ),
                separators=(",", ":"),
            ),
            "fresh-sketch-provider-line",
        )
        assert line_response.get("ok") is True, line_response
        assert int(sketch.GeometryCount) == 1
        assert line_response["geometry"]["kind"] == "line"
        assert line_response["assistant_undo_available"] is False
        assert edit_boundary(document, sketch, controller) == boundary

        line_operation = GeometryRuntimeModule._OPERATIONS["create_line"]

        def fail_line_verifier(_document, _draft):
            raise RuntimeError("forced provisional Sketch postcondition failure")

        GeometryRuntimeModule._OPERATIONS["create_line"] = (
            *line_operation[:3],
            fail_line_verifier,
            line_operation[4],
        )
        try:
            rolled_back_line = execution.dispatcher.call(
                SKETCH_GEOMETRY_CAPABILITY_NAME,
                json.dumps(
                    line_arguments(
                        sketch,
                        geometry_count=1,
                        start=(-5.0, 2.0),
                        end=(5.0, 2.0),
                    ),
                    separators=(",", ":"),
                ),
                "fresh-sketch-provider-forced-rollback",
            )
        finally:
            GeometryRuntimeModule._OPERATIONS["create_line"] = line_operation
        assert rolled_back_line.get("ok") is False, rolled_back_line
        assert int(sketch.GeometryCount) == 1
        assert edit_boundary(document, sketch, controller) == boundary

        execution.close()
        execution = None
        Gui.runCommand("Sketcher_LeaveSketch", 0)
        process_events(80)
        assert active_edit_object() is None
        assert int(document.getBookedTransactionID()) == 0
        assert not bool(document.HasPendingTransaction)
        assert document.getObject(sketch.Name) is sketch
        assert int(document.UndoCount) == original_undo_count + 1

        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(32)
        production_registry, production = resolve_production_native_surface()
        assert production.available is True, production.debug_summary()
        context = _capture_context_for_provider(service)
        turn_surface = context["provider_tool_surface"]
        schemas = context["provider_tool_schemas"]
        execution = create_native_session_execution(
            service=service,
            expected_surface=turn_surface,
            expected_schemas=schemas,
            registry=production_registry,
            controller=controller,
        )
        boundary = edit_boundary(document, sketch, controller)
        assert boundary[-2:] == (0, False)

        _phase("view_actions")
        presentation_base = {
            "sketch": {"object_name": sketch.Name},
            "expected_geometry_count": 1,
            "expected_constraint_count": 0,
            "expected_external_geometry_count": 0,
        }
        Gui.activeDocument().activeView().viewAxonometric()
        process_events(12)
        aligned = execution.dispatcher.call(
            "sketch.presentation",
            json.dumps(
                {"operation": "align_view_to_sketch", **presentation_base},
                separators=(",", ":"),
            ),
            "fresh-sketch-align-view",
        )
        assert aligned.get("ok") is True, aligned
        assert aligned["changed"] is True
        assert len(aligned["camera_orientation_xyzw"]) == 4
        assert edit_boundary(document, sketch, controller) == boundary

        section_before = bool(sketch.ViewObject.SectionView)
        shown_section = execution.dispatcher.call(
            "sketch.presentation",
            json.dumps(
                {
                    "operation": "section_view",
                    **presentation_base,
                    "expected_visible": section_before,
                    "visible": not section_before,
                },
                separators=(",", ":"),
            ),
            "fresh-sketch-section-show",
        )
        assert shown_section.get("ok") is True, shown_section
        assert bool(sketch.ViewObject.SectionView) is not section_before
        hidden_section = execution.dispatcher.call(
            "sketch.presentation",
            json.dumps(
                {
                    "operation": "section_view",
                    **presentation_base,
                    "expected_visible": not section_before,
                    "visible": section_before,
                },
                separators=(",", ":"),
            ),
            "fresh-sketch-section-restore",
        )
        assert hidden_section.get("ok") is True, hidden_section
        assert bool(sketch.ViewObject.SectionView) is section_before
        assert edit_boundary(document, sketch, controller) == boundary

        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = execution.dispatcher.call(
                "sketch.constraint",
                json.dumps(arguments, separators=(",", ":")),
                f"virtual-space-focused-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            current_boundary = edit_boundary(document, sketch, controller)
            assert current_boundary == boundary, (boundary, current_boundary)
            return response

        def install_failing_verifier():
            verifier = ConstraintRuntimeModule.verify_sketch_virtual_space_constraints

            def fail(_document, _draft):
                raise RuntimeError("forced virtual-space postcondition failure")

            ConstraintRuntimeModule.verify_sketch_virtual_space_constraints = fail

            def restore() -> None:
                ConstraintRuntimeModule.verify_sketch_virtual_space_constraints = verifier

            return restore

        _phase("human_native_parity")
        expected = exercise_virtual_space_case(
            sketch=sketch,
            document=document,
            native_call=native_call,
            process_events=process_events,
            edit_boundary=edit_boundary,
            boundary=boundary,
            controller=controller,
            read_view=SketcherGui.getActiveSketchVirtualSpace,
            install_failing_verifier=install_failing_verifier,
        )

        execution.close()
        execution = None
        Gui.activeDocument().resetEdit()
        process_events(32)
        assert active_edit_object() is None
        assert int(document.getBookedTransactionID()) == 0
        assert not bool(document.HasPendingTransaction)
        assert document.getObject(sketch.Name) is sketch
        Gui.activateWorkbench("PartDesignWorkbench")
        process_events(24)
        next_surface = read_active_ribbon_surface(controller)
        assert next_surface.surface_id == "model"
        next_context = _capture_context_for_provider(service)
        assert next_context["provider_tool_surface"]["tool_names"] == list(
            model_provider.tool_names
        )
        assert next_context["provider_tool_surface"]["tool_names"] != turn_surface[
            "tool_names"
        ]

        save_path = (
            Path(tempfile.mkdtemp(prefix="vibecad-native-virtual-space-"))
            / "NativeVirtualSpace.FCStd"
        )
        sketch_name = sketch.Name
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        document.recompute()
        VibeGui._connect_document_observer()
        process_events(16)
        reopened = document.getObject(sketch_name)
        assert reopened is not None
        assert Gui.activeDocument().setEdit(reopened.Name)
        process_events(24)
        verify_reopened_virtual_space(reopened, expected)
        assert SketcherGui.getActiveSketchVirtualSpace() is False
        reopened_surface = read_active_ribbon_surface(controller)
        reopened_provider = resolve_native_provider_surface(
            reopened_surface,
            build_native_capability_registry(),
        )
        assert reopened_provider.available is True
        assert reopened_provider.tool_names == production.tool_names
        _phase("complete")
        print(
            "VIBECAD_NATIVE_FRESH_SKETCH_PROVIDER_GUI_OK "
            "provisional-transaction nonempty codex-declarations real-line "
            "view-actions inter-turn-swap virtual-view constraints atomic "
            "stale rollback one-undo reopen",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=__import__("sys").__stderr__)
    finally:
        if execution is not None:
            execution.close()
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
