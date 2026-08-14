# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for exact, bounded FEM mechanical-result presentation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Fem
import ObjectsFem
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAnalyzePresentationSchema import (
    ANALYZE_PRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeResultState import result_reference_state, result_state
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    assert {"FEM_ResultShow", "FEM_PostApplyChanges"} <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(ANALYZE_PRESENTATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(
        ("show_result", "set_post_auto_recompute")
    )
    variants = {
        variant.operation: variant.provider_parameters()
        for variant in definition.variants
        if variant.operation in {"show_result", "set_post_auto_recompute"}
    }
    variant = variants["show_result"]
    assert variant["properties"]["operation"]["const"] == "show_result"
    assert set(variant["required"]) == {
        "operation",
        "result",
        "field",
        "deformation_scale",
        "visible",
    }
    assert variant["properties"]["deformation_scale"]["minimum"] == 0
    assert variant["properties"]["deformation_scale"]["maximum"] == 1_000_000
    assert set(variants["set_post_auto_recompute"]["required"]) == {
        "operation",
        "expected_enabled",
        "enabled",
    }
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ANALYZE_PRESENTATION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _publish_operation(document, manager, operation, resources=(), owners=()) -> None:
    manager._mark_timeline_operation(operation)
    for resource, owner in zip(resources, owners):
        manager._mark_timeline_resource(resource, owner)
    document.publishProvisionalTimelineOperationBlock(operation, resources, owners)


def _create_result(document):
    from femcommands import manager

    document.openTransaction("Create result presentation graph")
    try:
        mesh = document.addObject("Fem::FemMeshObject", "PresentationMesh")
        mesh.Label = "Presentation Result Mesh"
        fem_mesh = Fem.FemMesh()
        fem_mesh.addNode(0.0, 0.0, 0.0, 1)
        fem_mesh.addNode(12.0, 0.0, 0.0, 2)
        fem_mesh.addNode(0.0, 10.0, 0.0, 3)
        fem_mesh.addNode(0.0, 0.0, 8.0, 4)
        fem_mesh.addVolume([1, 2, 3, 4], 1)
        mesh.FemMesh = fem_mesh
        _publish_operation(document, manager, mesh)

        analysis = ObjectsFem.makeAnalysis(document, "PresentationAnalysis")
        analysis.Label = "Result Presentation Analysis"
        analysis.addObject(mesh)
        _publish_operation(document, manager, analysis)

        result = ObjectsFem.makeResultMechanical(document, "PresentationResult")
        result.Label = "Mechanical Presentation Result"
        result.Mesh = mesh
        result.NodeNumbers = [1, 2, 3, 4]
        result.DisplacementVectors = [
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(0.4, 0.0, 0.0),
            App.Vector(0.0, 0.8, 0.0),
            App.Vector(0.0, 0.0, 1.2),
        ]
        result.DisplacementLengths = [0.0, 0.4, 0.8, 1.2]
        result.vonMises = [12.0, 24.0, 48.0, 96.0]
        result.Temperature = [292.0, 318.0, 347.0, 381.0]
        stats = list(result.Stats)
        stats[6:10] = [0.0, 1.2, 12.0, 96.0]
        stats[20:22] = [292.0, 381.0]
        result.Stats = stats

        solver = ObjectsFem.makeSolverCalculiX(document, "PresentationSolver")
        solver.Label = "Presentation Solver"
        analysis.addObject(result)
        analysis.addObject(solver)
        solver.Results = [result]
        _publish_operation(document, manager, solver, (result,), (solver,))
        assert document.recompute([mesh, analysis, result, solver], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    result.ViewObject.Visibility = False
    mesh.ViewObject.Visibility = False
    return analysis, solver, result, mesh


def _assert_no_nodal_payload(value) -> None:
    if isinstance(value, dict):
        forbidden = {
            "values",
            "node_numbers",
            "displacement_vectors",
            "node_colors",
        }
        assert forbidden.isdisjoint(value)
        for child in value.values():
            _assert_no_nodal_payload(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_nodal_payload(child)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = None
    original_auto_recompute = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-result-presentation-"
        )
        output = Path(temporary.name) / "native-analyze-result-presentation.FCStd"
        document = App.newDocument("NativeAnalyzeResultPresentationGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        analysis, solver, result, mesh = _create_result(document)
        _events(12)

        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-result-presentation-gui")

        def authorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=authorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=authorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                ANALYZE_PRESENTATION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-result-presentation-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            _assert_no_nodal_payload(response)
            assert len(json.dumps(response, separators=(",", ":")).encode("utf-8")) < 8192
            return response

        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem")
        original_auto_recompute = preferences.GetBool("PostAutoRecompute", True)
        toggled_auto_recompute = not original_auto_recompute
        preference_revision = state_store.current_revision(str(document.Uid))
        preference_undo_count = int(document.UndoCount)
        preference = call(
            {
                "operation": "set_post_auto_recompute",
                "expected_enabled": original_auto_recompute,
                "enabled": toggled_auto_recompute,
            }
        )
        assert preference == {
            "ok": True,
            "changed": True,
            "post_auto_recompute": toggled_auto_recompute,
        }
        stale_preference = call(
            {
                "operation": "set_post_auto_recompute",
                "expected_enabled": original_auto_recompute,
                "enabled": original_auto_recompute,
            },
            succeeds=False,
        )
        assert stale_preference["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert stale_preference["repair"] == {
            "current_enabled": toggled_auto_recompute
        }
        restored_preference = call(
            {
                "operation": "set_post_auto_recompute",
                "expected_enabled": toggled_auto_recompute,
                "enabled": original_auto_recompute,
            }
        )
        assert restored_preference["post_auto_recompute"] is original_auto_recompute
        assert state_store.current_revision(str(document.Uid)) == preference_revision
        assert int(document.UndoCount) == preference_undo_count

        initial = result_reference_state(result)
        initial_hash = initial["state_sha256"]
        revision_before = state_store.current_revision(str(document.Uid))
        timeline_before = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        undo_before = int(document.UndoCount)
        unavailable = call(
            {
                "operation": "show_result",
                "result": _target(initial),
                "field": "mass_flow_rate",
                "deformation_scale": 0.0,
                "visible": True,
            },
            succeeds=False,
        )
        assert unavailable["error_code"] == "NATIVE_ANALYZE_PRESENTATION_INVALID"
        assert unavailable["repair"]["available_fields"] == [
            "none",
            "displacement_magnitude",
            "displacement_x",
            "displacement_y",
            "displacement_z",
            "temperature",
            "von_mises_stress",
        ]
        assert state_store.current_revision(str(document.Uid)) == revision_before
        shown = call(
            {
                "operation": "show_result",
                "result": _target(initial),
                "field": "von_mises_stress",
                "deformation_scale": 2.5,
                "visible": True,
            }
        )
        assert shown["changed"] is True
        assert shown["previous_presentation"]["managed"] is False
        assert shown["presentation"] == {
            "managed": True,
            "field": "von_mises_stress",
            "deformation_scale": 2.5,
            "visible": True,
            "available_fields": [
                "none",
                "displacement_magnitude",
                "displacement_x",
                "displacement_y",
                "displacement_z",
                "temperature",
                "von_mises_stress",
            ],
            "range": [12.0, 96.0],
        }
        assert shown["result"]["state_sha256"] != initial_hash
        assert bool(result.ViewObject.Visibility)
        assert bool(mesh.ViewObject.Visibility)
        assert App.FEM_dialog["result_obj"] is result
        assert App.FEM_dialog["results_type"] == "Sabs"
        assert App.FEM_dialog["show_disp"] is True
        assert App.FEM_dialog["disp_factor"] == 2.5
        assert result_state(result, include_ranges=False)["presentation"] == shown[
            "presentation"
        ]
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == timeline_before
        assert int(document.UndoCount) == undo_before

        stale = call(
            {
                "operation": "show_result",
                "result": _target(initial),
                "field": "temperature",
                "deformation_scale": 1.0,
                "visible": True,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE", stale
        assert result_state(result, include_ranges=False)["presentation"] == shown[
            "presentation"
        ]

        changed_field = call(
            {
                "operation": "show_result",
                "result": _target(shown["result"]),
                "field": "temperature",
                "deformation_scale": 0.0,
                "visible": True,
            }
        )
        assert changed_field["presentation"]["field"] == "temperature"
        assert changed_field["presentation"]["range"] == [292.0, 381.0]
        assert changed_field["presentation"]["deformation_scale"] == 0.0
        assert App.FEM_dialog["results_type"] == "Temp"
        assert App.FEM_dialog["show_disp"] is False

        reset = call(
            {
                "operation": "show_result",
                "result": _target(changed_field["result"]),
                "field": "none",
                "deformation_scale": 0.0,
                "visible": False,
            }
        )
        assert reset["presentation"]["field"] == "none"
        assert "range" not in reset["presentation"]
        assert reset["presentation"]["visible"] is False
        assert not result.ViewObject.Visibility
        assert not mesh.ViewObject.Visibility
        assert App.FEM_dialog["results_type"] == "None"
        assert state_store.current_revision(str(document.Uid)) == revision_before
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == timeline_before
        assert int(document.UndoCount) == undo_before
        assert ledger.available(document, state_store) == {"available": False}

        result_name = result.Name
        mesh_name = mesh.Name
        analysis_name = analysis.Name
        solver_name = solver.Name
        document.recompute()
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        _events(20)
        reopened_result = document.getObject(result_name)
        reopened_mesh = document.getObject(mesh_name)
        assert document.getObject(analysis_name) is not None
        assert document.getObject(solver_name) is not None
        reopened = result_state(reopened_result, include_ranges=False)
        assert reopened["presentation"]["managed"] is False
        assert reopened["presentation"]["field"] == "unknown"
        assert reopened["presentation"]["visible"] is False
        assert not reopened_mesh.ViewObject.Visibility
        print(
            "VIBECAD_NATIVE_ANALYZE_RESULT_PRESENTATION_GUI_OK "
            "actions=2 fields=true field_repair=true exact_target=true no_arrays=true "
            "guarded_post_preference=true "
            "stale_rejection=true no_transaction=true revision_stable=true "
            "reset=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if preferences is not None and original_auto_recompute is not None:
            preferences.SetBool("PostAutoRecompute", original_auto_recompute)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
