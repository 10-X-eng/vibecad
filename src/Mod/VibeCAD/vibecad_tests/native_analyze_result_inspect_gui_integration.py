# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for bounded exact Native FEM result inspection."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import ObjectsFem
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeResultState import result_reference_state
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
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
    assert "FEM_ResultShow" in surface.command_ids
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert inspect is not None
    schema = inspect.provider_schema(("result",))
    assert schema["parameters"]["oneOf"][0]["properties"]["operation"]["const"] == (
        "result"
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ANALYZE_INSPECT_CAPABILITY_NAME,),
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


def _grid():
    from vtkmodules.vtkCommonCore import vtkDoubleArray, vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkTetra, vtkUnstructuredGrid

    points = vtkPoints()
    for point in ((0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)):
        points.InsertNextPoint(*point)
    tetra = vtkTetra()
    for index in range(4):
        tetra.GetPointIds().SetId(index, index)
    grid = vtkUnstructuredGrid()
    grid.SetPoints(points)
    grid.InsertNextCell(tetra.GetCellType(), tetra.GetPointIds())

    temperature = vtkDoubleArray()
    temperature.SetName("Temperature")
    temperature.SetNumberOfComponents(1)
    for value in (300.0, 320.0, 340.0, 360.0):
        temperature.InsertNextValue(value)
    grid.GetPointData().AddArray(temperature)

    displacement = vtkDoubleArray()
    displacement.SetName("Displacement")
    displacement.SetNumberOfComponents(3)
    for value in ((0, 0, 0), (3, 4, 0), (0, 0, 12), (0, 0, 5)):
        displacement.InsertNextTuple3(*value)
    grid.GetPointData().AddArray(displacement)
    return grid


def _create_result_graph(document):
    from femcommands import manager

    document.openTransaction("Create result inspection graph")
    try:
        analysis = ObjectsFem.makeAnalysis(document, "ResultGateAnalysis")
        manager._mark_timeline_operation(analysis)
        document.publishProvisionalTimelineOperationBlock(analysis, (), ())

        result = ObjectsFem.makeResultMechanical(document, "StructuralResult")
        result.Label = "Structural Result"
        result.NodeNumbers = [1, 2, 3, 4]
        result.DisplacementVectors = [
            App.Vector(0, 0, 0),
            App.Vector(3, 4, 0),
            App.Vector(0, 0, 12),
            App.Vector(0, 0, 5),
        ]
        result.DisplacementLengths = [0.0, 5.0, 12.0, 5.0]
        result.vonMises = [15.0, 25.0, 35.0, 45.0]
        result.Temperature = [300.0, 320.0, 340.0, 360.0]
        stats = list(result.Stats)
        stats[6:10] = [0.0, 12.0, 15.0, 45.0]
        stats[20:22] = [300.0, 360.0]
        result.Stats = stats

        solver = ObjectsFem.makeSolverCalculiX(document, "ResultGateSolver")
        analysis.addObject(result)
        analysis.addObject(solver)
        solver.Results = [result]
        manager._mark_timeline_operation(solver)
        manager._mark_timeline_resource(result, solver)
        document.publishProvisionalTimelineOperationBlock(solver, (result,), (solver,))

        pipeline = document.addObject("Fem::FemPostPipeline", "ResultPipeline")
        pipeline.Label = "Result Pipeline"
        pipeline.Data = _grid()
        analysis.addObject(pipeline)
        manager._mark_timeline_operation(pipeline)
        document.publishProvisionalTimelineOperationBlock(pipeline, (), ())
        warp = ObjectsFem.makePostVtkFilterWarp(document, pipeline, "WarpResult")
        warp.Factor = 2.5
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return analysis, solver, result, pipeline, warp


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-result-inspect-"
        )
        output = Path(temporary.name) / "native-analyze-result-inspect.FCStd"
        document = App.newDocument("NativeAnalyzeResultInspectGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        _analysis, solver, result, pipeline, warp = _create_result_graph(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-result-inspect-gui")

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
        revision_before = state_store.current_revision(context.document_uid)
        timeline_before = tuple(document.VibeCADTimeline.Operations)

        legacy_reference = result_reference_state(result)
        legacy = dispatcher.call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            json.dumps(
                {"operation": "result", "target": _target(legacy_reference)},
                separators=(",", ":"),
            ),
            "native-analyze-result-inspect-legacy",
        )
        assert legacy["ok"], legacy
        legacy_state = legacy["result"]
        assert legacy_state["result_kind"] == "result"
        assert legacy_state["timeline_owner_chain"] == [solver.Name]
        legacy_fields = {field["name"]: field for field in legacy_state["fields"]}
        assert legacy_fields["DisplacementLengths"]["range"] == [0.0, 12.0]
        assert legacy_fields["vonMises"]["range"] == [15.0, 45.0]
        assert legacy_fields["Temperature"]["range"] == [300.0, 360.0]

        pipeline_reference = result_reference_state(pipeline)
        post = dispatcher.call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            json.dumps(
                {"operation": "result", "target": _target(pipeline_reference)},
                separators=(",", ":"),
            ),
            "native-analyze-result-inspect-pipeline",
        )
        assert post["ok"], post
        post_state = post["result"]
        assert post_state["result_kind"] == "pipeline"
        assert post_state["point_count"] == 4 and post_state["cell_count"] == 1
        post_fields = {field["name"]: field for field in post_state["fields"]}
        assert post_fields["Temperature"]["range"] == [300.0, 360.0]
        assert post_fields["Displacement"]["range"] == [0.0, 12.0]
        assert post_state["child_count"] == 1
        assert post_state["children"][0]["object_name"] == warp.Name

        snapshot = build_analyze_snapshot(document)
        snapshot_by_name = {
            item["object_name"]: item for item in snapshot["results"]
        }
        assert {result.Name, pipeline.Name, warp.Name} <= set(snapshot_by_name)
        assert "fields" not in snapshot_by_name[pipeline.Name]
        assert snapshot_by_name[pipeline.Name]["field_names"] == [
            "Temperature",
            "Displacement",
        ]
        assert len(json.dumps(snapshot, separators=(",", ":")).encode("utf-8")) < 32768
        assert state_store.current_revision(context.document_uid) == revision_before
        assert tuple(document.VibeCADTimeline.Operations) == timeline_before

        result_name = result.Name
        pipeline_name = pipeline.Name
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        assert document.getObject(result_name) is not None
        reopened_pipeline = document.getObject(pipeline_name)
        reopened = result_reference_state(reopened_pipeline)
        assert reopened["field_names"] == ["Temperature", "Displacement"]
        print(
            "VIBECAD_NATIVE_ANALYZE_RESULT_INSPECT_GUI_OK "
            "variants=1 exact_targets=true legacy_ranges=true vtk_ranges=true "
            "ownership=true bounded_context=true read_revision_stable=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
