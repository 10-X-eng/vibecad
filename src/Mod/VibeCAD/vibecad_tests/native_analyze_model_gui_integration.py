# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Analyze analysis and material tools."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeState import analysis_state, material_kind, material_state
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeContextManifest import provider_context_actions_for_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


MODEL_OPERATIONS = (
    "create_analysis",
    "create_solid_material",
    "create_fluid_material",
    "create_nonlinear_material",
    "create_reinforced_material",
    "update_material",
)
INSPECT_OPERATIONS = ("analysis", "material", "material_catalog")


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_analyze_ribbon(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    expected = {
        "FEM_Analysis",
        "FEM_MaterialSolid",
        "FEM_MaterialFluid",
        "FEM_MaterialMechanicalNonlinear",
        "FEM_MaterialReinforced",
        "FEM_MaterialEditor",
    }
    assert expected <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and inspect is not None
    expected_actions = {
        "FEM_Analysis": "create_analysis",
        "FEM_MaterialSolid": "create_solid_material",
        "FEM_MaterialFluid": "create_fluid_material",
        "FEM_MaterialMechanicalNonlinear": "create_nonlinear_material",
        "FEM_MaterialReinforced": "create_reinforced_material",
        "FEM_MaterialEditor": "update_material",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_MODEL_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert plan.transaction_behavior == "document"
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in model.variants
        )
    context_actions = tuple(
        action
        for action in provider_context_actions_for_surface("analyze")
        if action.capability_family == ANALYZE_INSPECT_CAPABILITY_NAME
        and action.operation_variant in INSPECT_OPERATIONS
    )
    assert tuple(action.operation_variant for action in context_actions) == (
        "analysis",
        "material",
        "material_catalog",
    )
    assert all(
        any(
            variant.operation == action.operation_variant
            and action.action_id in variant.action_ids
            for variant in inspect.variants
        )
        for action in context_actions
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(MODEL_OPERATIONS),
                inspect.provider_schema(INSPECT_OPERATIONS),
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


def _material_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _reference(source) -> dict:
    return {
        "object_name": source.Name,
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelements": ["Solid1"],
    }


def _create_geometry_source(document):
    document.openTransaction("Create FEM geometry source")
    try:
        source = document.addObject("Part::Box", "AnalysisGeometry")
        source.Label = "Analysis Geometry"
        source.Length = 30.0
        source.Width = 20.0
        source.Height = 10.0
        assert document.recompute([source], True, True) is not False
        assert not source.Shape.isNull() and source.Shape.isValid()
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-model-")
        save_path = Path(temporary.name) / "native-analyze-model.FCStd"
        document = App.newDocument("NativeAnalyzeModelGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        source = _create_geometry_source(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-model-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
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

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-model-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        general_preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Fem/General"
        )
        previous_default_solver = general_preferences.GetInt("DefaultSolver", 0)
        general_preferences.SetInt("DefaultSolver", 1)
        try:
            analysis_result = call(
                ANALYZE_MODEL_CAPABILITY_NAME,
                {
                    "operation": "create_analysis",
                    "label": "Structural Analysis",
                    "default_solver_policy": "user_preference",
                },
            )
        finally:
            general_preferences.SetInt("DefaultSolver", previous_default_solver)
        analysis = document.getObject(
            analysis_result["created_analysis"]["object_name"]
        )
        solver = document.getObject(analysis_result["created_solver"]["object_name"])
        assert analysis is not None and solver is not None
        assert solver in tuple(analysis.Group)
        current_analysis = analysis_result["created_analysis"]

        reference = _reference(source)
        solid_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_solid_material",
                "analysis": _analysis_target(current_analysis),
                "label": "Custom Structural Steel",
                "references": [reference],
                "properties": {
                    "name": "Custom Structural Steel",
                    "density_kg_m3": 7850.0,
                    "young_modulus_mpa": 210000.0,
                    "poisson_ratio": 0.3,
                },
            },
        )
        solid = document.getObject(solid_result["created_material"]["object_name"])
        current_analysis = solid_result["analysis"]
        assert solid is not None and solid_result["created_material"]["material_uuid"] == ""

        read_revision = state.current_revision(str(document.Uid))
        air_search = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "material_catalog",
                "query": "air",
                "category": "fluid",
                "limit": 3,
            },
        )
        concrete_search = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "material_catalog",
                "query": "Concrete-EN-C35_45",
                "category": "solid",
                "limit": 3,
            },
        )
        steel_search = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "material_catalog",
                "query": "CalculiX-Steel",
                "category": "solid",
                "limit": 3,
            },
        )
        assert state.current_revision(str(document.Uid)) == read_revision
        air_uuid = air_search["materials"][0]["uuid"]
        concrete_uuid = concrete_search["materials"][0]["uuid"]
        steel_uuid = steel_search["materials"][0]["uuid"]

        fluid_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_fluid_material",
                "analysis": _analysis_target(current_analysis),
                "label": "Ambient Air",
                "references": [],
                "material_uuid": air_uuid,
            },
        )
        fluid = document.getObject(fluid_result["created_material"]["object_name"])
        current_analysis = fluid_result["analysis"]
        assert fluid is not None and str(fluid.UUID) == air_uuid

        reinforced_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_reinforced_material",
                "analysis": _analysis_target(current_analysis),
                "label": "Reinforced Concrete",
                "references": [reference],
                "material_uuid": concrete_uuid,
                "reinforcement_uuid": steel_uuid,
            },
        )
        reinforced = document.getObject(
            reinforced_result["created_material"]["object_name"]
        )
        current_analysis = reinforced_result["analysis"]
        assert reinforced is not None
        assert str(reinforced.UUID) == concrete_uuid
        assert str(reinforced.ReinforcementUUID) == steel_uuid

        solid_before_nonlinear = material_state(solid)
        nonlinear_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_nonlinear_material",
                "base_material": _material_target(solid_before_nonlinear),
                "label": "Steel Plasticity",
                "model": "isotropic_hardening",
                "yield_points": [
                    {"stress_mpa": 250.0, "plastic_strain": 0.0},
                    {"stress_mpa": 310.0, "plastic_strain": 0.05},
                ],
            },
        )
        nonlinear = document.getObject(
            nonlinear_result["created_material"]["object_name"]
        )
        assert nonlinear is not None and solid.Nonlinear is nonlinear
        assert nonlinear not in tuple(analysis.Group)

        fluid_before_update = material_state(fluid)
        update_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "update_material",
                "target": _material_target(fluid_before_update),
                "label": "Ambient Air at Test Condition",
                "properties": {"density_kg_m3": 1.18},
            },
        )
        assert update_result["updated_material"]["material_uuid"] == ""
        assert update_result["updated_material"]["properties"]["density_kg_m3"] == 1.18

        stale = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "update_material",
                "target": _material_target(fluid_before_update),
                "label": "Must Not Apply",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert str(fluid.Label) == "Ambient Air at Test Condition"

        read_revision = state.current_revision(str(document.Uid))
        analysis_read = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "analysis",
                "target": _analysis_target(analysis_state(analysis)),
            },
        )
        material_read = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "material",
                "target": _material_target(material_state(reinforced)),
            },
        )
        assert analysis_read["analysis"]["member_count"] == 4
        assert material_read["material"]["material_kind"] == "reinforced"
        assert state.current_revision(str(document.Uid)) == read_revision

        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            source.Name,
            solver.Name,
            analysis.Name,
            nonlinear.Name,
            solid.Name,
            fluid.Name,
            reinforced.Name,
        )
        assert nonlinear.VibeCADTimelineRole == "resource"
        assert nonlinear.VibeCADTimelineOwner is solid
        assert solid.VibeCADTimelineRole == "operation"
        assert solver.VibeCADTimelineRole == "resource"
        assert solver.VibeCADTimelineOwner is analysis
        assert tuple(analysis.Group) == (solver, solid, fluid, reinforced)
        assert int(document.UndoCount) == 7

        document.undo()
        assert str(fluid.Label) == "Ambient Air"
        assert str(fluid.UUID) == air_uuid
        document.redo()
        assert str(fluid.Label) == "Ambient Air at Test Condition"
        assert str(fluid.UUID) == ""

        expected_maps = {
            solid.Name: dict(solid.Material),
            fluid.Name: dict(fluid.Material),
            reinforced.Name: dict(reinforced.Material),
        }
        names = {
            "source": source.Name,
            "analysis": analysis.Name,
            "solver": solver.Name,
            "solid": solid.Name,
            "fluid": fluid.Name,
            "reinforced": reinforced.Name,
            "nonlinear": nonlinear.Name,
        }
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened = {key: document.getObject(name) for key, name in names.items()}
        assert all(obj is not None for obj in reopened.values())
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        assert tuple(obj.Name for obj in reopened["analysis"].Group) == (
            names["solver"],
            names["solid"],
            names["fluid"],
            names["reinforced"],
        )
        assert reopened["solid"].Nonlinear is reopened["nonlinear"]
        assert reopened["nonlinear"] not in tuple(reopened["analysis"].Group)
        assert all(
            dict(document.getObject(name).Material) == expected
            for name, expected in expected_maps.items()
        )
        assert material_kind(reopened["solid"]) == "solid"
        assert material_kind(reopened["fluid"]) == "fluid"
        assert material_kind(reopened["reinforced"]) == "reinforced"
        assert material_kind(reopened["nonlinear"]) == "nonlinear"

        print(
            "VIBECAD_NATIVE_ANALYZE_MODEL_GUI_OK "
            "actions=6 reads=3 exact_targets=true catalog=true history=true "
            "undo_redo=true reopen=true read_revision_stable=true",
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
