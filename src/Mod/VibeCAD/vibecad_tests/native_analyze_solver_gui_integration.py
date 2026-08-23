# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact Native Analyze solver creation and reads."""

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
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeSolverSchema import ANALYZE_SOLVER_CAPABILITY_NAME
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


SOLVER_OPERATIONS = (
    "create_calculix",
    "create_elmer",
    "create_openfoam",
    "create_mystran",
    "create_z88",
)


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(i for i in range(tabs.count()) if str(tabs.tabData(i)) == "FemWorkbench")
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    assert {
        "FEM_SolverCalculiX",
        "FEM_SolverElmer",
        "FEM_SolverOpenFOAM",
        "FEM_SolverMystran",
        "FEM_SolverZ88",
    } <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    solver = registry.definition(ANALYZE_SOLVER_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert solver is not None and inspect is not None
    expected = {
        "FEM_SolverCalculiX": "create_calculix",
        "FEM_SolverElmer": "create_elmer",
        "FEM_SolverOpenFOAM": "create_openfoam",
        "FEM_SolverMystran": "create_mystran",
        "FEM_SolverZ88": "create_z88",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected
    }
    assert set(plans) == set(expected)
    for action_id, operation in expected.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_SOLVER_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert plan.transaction_behavior == "document"
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ANALYZE_SOLVER_CAPABILITY_NAME, ANALYZE_INSPECT_CAPABILITY_NAME),
            schemas=(
                solver.provider_schema(SOLVER_OPERATIONS),
                inspect.provider_schema(("solver",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _analysis_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "expected_member_count": state["member_count"],
    }


def _solver_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _create_analysis(document):
    document.openTransaction("Create solver gate analysis")
    try:
        analysis = ObjectsFem.makeAnalysis(document, "SolverGateAnalysis")
        analysis.Label = "Solver Gate Analysis"
        document.publishProvisionalTimelineOperationBlock(analysis, (), ())
        document.recompute([analysis], True, True)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return analysis


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    ccx = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx")
    elmer = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Elmer")
    z88 = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Z88")
    previous = {
        "ccx_pipeline": ccx.GetBool("ResultAsPipeline", True),
        "ccx_type": ccx.GetInt("AnalysisType", 0),
        "elmer_binary": elmer.GetBool("BinaryOutput", False),
        "elmer_geometry": elmer.GetBool("SaveGeometryIndex", False),
        "z88_solver": z88.GetString("Solver", "sorcg"),
        "z88_matrix": z88.GetInt("MaxGS", 100000000),
        "z88_vector": z88.GetInt("MaxKOI", 2800000),
    }
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-solver-")
        path = Path(temporary.name) / "native-analyze-solver.FCStd"
        document = App.newDocument("NativeAnalyzeSolverGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        analysis = _create_analysis(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-solver-gui")

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

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-solver-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        ccx.SetBool("ResultAsPipeline", True)
        ccx.SetInt("AnalysisType", 2)
        elmer.SetBool("BinaryOutput", True)
        elmer.SetBool("SaveGeometryIndex", True)
        z88.SetString("Solver", "siccg")
        z88.SetInt("MaxGS", 345678)
        z88.SetInt("MaxKOI", 123456)

        initial_analysis = analysis_state(analysis)
        current_analysis = initial_analysis
        expected_states = {}
        labels = {
            "create_calculix": "Structural CalculiX",
            "create_elmer": "Multiphysics Elmer",
            "create_openfoam": "Steady CFD OpenFOAM",
            "create_mystran": "Structural Mystran",
            "create_z88": "Structural Z88",
        }
        for operation in SOLVER_OPERATIONS:
            result = call(
                ANALYZE_SOLVER_CAPABILITY_NAME,
                {
                    "operation": operation,
                    "analysis": _analysis_target(current_analysis),
                    "label": labels[operation],
                },
            )
            created = result["created_solver"]
            expected_states[operation] = created
            current_analysis = result["analysis"]
            assert created["solver_kind"] == operation.removeprefix("create_")
            assert created["analysis"] == analysis.Name
            assert created["timeline_role"] == "operation"
            assert result["assistant_undo_available"]

        assert expected_states["create_calculix"]["implementation"] == "pipeline"
        assert expected_states["create_calculix"]["settings"]["AnalysisType"] == "thermomech"
        assert expected_states["create_elmer"]["settings"]["BinaryOutput"] is True
        assert expected_states["create_elmer"]["settings"]["SaveGeometryIndex"] is True
        assert expected_states["create_openfoam"]["settings"] == {
            "FlowRegime": "steady",
            "MaxIterations": 1000,
            "PressureTolerance": 1.0e-6,
            "TurbulenceModel": "laminar",
            "VelocityTolerance": 1.0e-5,
            "WriteEveryIterations": 100,
        }
        assert expected_states["create_z88"]["settings"]["SolverType"] == "siccg"
        assert expected_states["create_z88"]["settings"]["MatrixMaximum"] == 345678
        assert expected_states["create_z88"]["settings"]["VectorMaximum"] == 123456

        stale = call(
            ANALYZE_SOLVER_CAPABILITY_NAME,
            {
                "operation": "create_elmer",
                "analysis": _analysis_target(initial_analysis),
                "label": "Must Not Exist",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert document.getObject("Must_Not_Exist") is None

        revision_before_read = state_store.current_revision(str(document.Uid))
        read = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "solver",
                "target": _solver_target(expected_states["create_calculix"]),
            },
        )
        assert read["solver"] == expected_states["create_calculix"]
        assert state_store.current_revision(str(document.Uid)) == revision_before_read

        z88_name = expected_states["create_z88"]["object_name"]
        z88_hash = expected_states["create_z88"]["state_sha256"]
        document.undo()
        assert document.getObject(z88_name) is None
        assert analysis_state(analysis)["member_count"] == 4
        document.redo()
        restored_z88 = document.getObject(z88_name)
        assert solver_state(restored_z88)["state_sha256"] == z88_hash
        assert analysis_state(analysis)["member_count"] == 5

        document.recompute()
        document.save()
        analysis_name = str(analysis.Name)
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        _events(20)
        reopened_analysis = document.getObject(analysis_name)
        assert analysis_state(reopened_analysis)["member_count"] == 5
        for expected in expected_states.values():
            reopened = document.getObject(expected["object_name"])
            assert solver_state(reopened)["state_sha256"] == expected["state_sha256"]

        print(
            "VIBECAD_NATIVE_ANALYZE_SOLVER_GUI_OK "
            "actions=5 variants=5 human_factories=true preferences=true exact_analysis=true "
            "stale_rejection=true inspect=true read_revision_stable=true history=true "
            "undo_redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        ccx.SetBool("ResultAsPipeline", previous["ccx_pipeline"])
        ccx.SetInt("AnalysisType", previous["ccx_type"])
        elmer.SetBool("BinaryOutput", previous["elmer_binary"])
        elmer.SetBool("SaveGeometryIndex", previous["elmer_geometry"])
        z88.SetString("Solver", previous["z88_solver"])
        z88.SetInt("MaxGS", previous["z88_matrix"])
        z88.SetInt("MaxKOI", previous["z88_vector"])
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
