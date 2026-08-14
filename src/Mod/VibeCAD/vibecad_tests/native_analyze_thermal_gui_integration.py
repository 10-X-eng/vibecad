# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native FEM thermal-condition tools."""

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
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeAnalyzeThermalSchema import ANALYZE_THERMAL_CAPABILITY_NAME
from VibeCADNativeAnalyzeThermalState import thermal_condition_state
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


MODES = (
    "initial_temperature",
    "surface_heat_flux",
    "convection",
    "radiation",
    "boundary_temperature",
    "concentrated_heat_input",
    "mass_heat_generation",
    "total_body_power",
)
OPERATIONS = tuple(
    operation
    for mode in MODES
    for operation in (f"create_{mode}", f"update_{mode}")
)


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
    assert {
        "FEM_ConstraintInitialTemperature",
        "FEM_ConstraintHeatflux",
        "FEM_ConstraintTemperature",
        "FEM_ConstraintBodyHeatSource",
    } <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    thermal = registry.definition(ANALYZE_THERMAL_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and thermal is not None and inspect is not None
    expected_actions = {
        "FEM_ConstraintInitialTemperature": "create_initial_temperature",
        "FEM_ConstraintHeatflux": "create_surface_heat_flux",
        "FEM_ConstraintTemperature": "create_boundary_temperature",
        "FEM_ConstraintBodyHeatSource": "create_mass_heat_generation",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_THERMAL_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in thermal.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    assert contexts["VibeCAD_AnalyzeReadThermalCondition"].operation_variant == (
        "thermal_condition"
    )
    for mode in MODES:
        action_id = "VibeCAD_AnalyzeUpdate" + "".join(
            part.title() for part in mode.split("_")
        )
        assert contexts[action_id].operation_variant == f"update_{mode}"
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_THERMAL_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                thermal.provider_schema(OPERATIONS),
                inspect.provider_schema(("thermal_condition",)),
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


def _condition_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _references(source, *subelements: str) -> list[dict]:
    return [
        {
            "object_name": source.Name,
            "expected_state_sha256": mesh_object_state(source)["state_sha256"],
            "subelements": list(subelements),
        }
    ]


def _publish_box(document, name: str, x: float):
    document.openTransaction(f"Create {name}")
    try:
        source = document.addObject("Part::Box", name)
        source.Length = 30.0
        source.Width = 20.0
        source.Height = 10.0
        source.Placement.Base.x = x
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-thermal-")
        save_path = Path(temporary.name) / "native-analyze-thermal.FCStd"
        document = App.newDocument("NativeAnalyzeThermalGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        first = _publish_box(document, "ThermalPartA", 0.0)
        second = _publish_box(document, "ThermalPartB", 35.0)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        revision_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-thermal-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=revision_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=revision_store,
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
                f"native-analyze-thermal-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Thermal Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(analysis_result["created_analysis"]["object_name"])
        current_analysis = analysis_state(analysis)

        creates = (
            ("initial_temperature", "Initial Temperature", {"temperature_k": 293.15}),
            (
                "surface_heat_flux",
                "Applied Heat Flux",
                {"heat_flux_w_m2": 1200.0, "references": _references(first, "Face1")},
            ),
            (
                "convection",
                "Convective Cooling",
                {
                    "ambient_temperature_k": 298.15,
                    "film_coefficient_w_m2_k": 12.5,
                    "references": _references(first, "Face2"),
                },
            ),
            (
                "radiation",
                "Radiative Cooling",
                {
                    "ambient_temperature_k": 295.0,
                    "emissivity": 0.72,
                    "references": _references(first, "Face3"),
                },
            ),
            (
                "boundary_temperature",
                "Fixed Temperature",
                {"temperature_k": 360.0, "references": _references(second, "Face1")},
            ),
            (
                "concentrated_heat_input",
                "Point Heater",
                {"power_w": 25.0, "references": _references(second, "Vertex1")},
            ),
            (
                "mass_heat_generation",
                "Mass Heat Generation",
                {
                    "dissipation_rate_w_kg": 75.0,
                    "references": _references(first, "Solid1"),
                },
            ),
            (
                "total_body_power",
                "Total Body Power",
                {"total_power_w": 120.0, "references": _references(second, "Solid1")},
            ),
        )
        conditions = {}
        for mode, label, values in creates:
            payload = {
                "operation": f"create_{mode}",
                "analysis": _analysis_target(current_analysis),
                "label": label,
                **values,
            }
            result = call(ANALYZE_THERMAL_CAPABILITY_NAME, payload)
            condition = document.getObject(
                result["created_thermal_condition"]["object_name"]
            )
            conditions[mode] = condition
            current_analysis = analysis_state(analysis)

        duplicate = call(
            ANALYZE_THERMAL_CAPABILITY_NAME,
            {
                "operation": "create_initial_temperature",
                "analysis": _analysis_target(current_analysis),
                "label": "Duplicate Must Fail",
                "temperature_k": 300.0,
            },
            succeeds=False,
        )
        assert "already contains" in duplicate["error"]
        mixed = call(
            ANALYZE_THERMAL_CAPABILITY_NAME,
            {
                "operation": "create_surface_heat_flux",
                "analysis": _analysis_target(current_analysis),
                "label": "Mixed Geometry Must Fail",
                "heat_flux_w_m2": 10.0,
                "references": [
                    *_references(first, "Face4"),
                    *_references(second, "Edge1"),
                ],
            },
            succeeds=False,
        )
        assert "same subelement type" in mixed["error"]
        assert len(tuple(analysis.Group)) == 8

        updates = {
            "initial_temperature": {"temperature_k": 300.0},
            "surface_heat_flux": {"heat_flux_w_m2": -500.0},
            "convection": {
                "ambient_temperature_k": 302.0,
                "film_coefficient_w_m2_k": 18.0,
            },
            "radiation": {"ambient_temperature_k": 300.0, "emissivity": 0.84},
            "boundary_temperature": {"temperature_k": 365.0},
            "concentrated_heat_input": {"power_w": 30.0},
            "mass_heat_generation": {"dissipation_rate_w_kg": 80.0},
            "total_body_power": {"total_power_w": 150.0},
        }
        before_updates = {}
        update_results = {}
        for mode in MODES:
            condition = conditions[mode]
            before_updates[mode] = thermal_condition_state(condition)
            update_results[mode] = call(
                ANALYZE_THERMAL_CAPABILITY_NAME,
                {
                    "operation": f"update_{mode}",
                    "target": _condition_target(before_updates[mode]),
                    **updates[mode],
                },
            )["updated_thermal_condition"]
            assert update_results[mode]["thermal_mode"] == mode

        stale = call(
            ANALYZE_THERMAL_CAPABILITY_NAME,
            {
                "operation": "update_boundary_temperature",
                "target": _condition_target(before_updates["boundary_temperature"]),
                "temperature_k": 400.0,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"

        read_revision = revision_store.current_revision(str(document.Uid))
        for mode in MODES:
            current = thermal_condition_state(conditions[mode])
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "thermal_condition",
                    "target": _condition_target(current),
                },
            )
            assert read["thermal_condition"] == current
        assert revision_store.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["thermal_condition_count"] == 8
        assert not snapshot["thermal_conditions_truncated"]
        assert {item["thermal_mode"] for item in snapshot["thermal_conditions"]} == set(
            MODES
        )
        assert tuple(analysis.Group) == tuple(conditions[mode] for mode in MODES)
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            first.Name,
            second.Name,
            analysis.Name,
            *(conditions[mode].Name for mode in MODES),
        )

        document.undo()
        total = conditions["total_body_power"]
        assert thermal_condition_state(total)["state_sha256"] == before_updates[
            "total_body_power"
        ]["state_sha256"]
        document.redo()
        assert thermal_condition_state(total)["definition"] == update_results[
            "total_body_power"
        ]["definition"]

        expected = {
            condition.Name: thermal_condition_state(condition)
            for condition in conditions.values()
        }
        analysis_name = analysis.Name
        member_names = tuple(obj.Name for obj in analysis.Group)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened_analysis = document.getObject(analysis_name)
        assert tuple(obj.Name for obj in reopened_analysis.Group) == member_names
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        for name, old_state in expected.items():
            new_state = thermal_condition_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["references"] == old_state["references"]

        print(
            "VIBECAD_NATIVE_ANALYZE_THERMAL_GUI_OK "
            "actions=4 modes=8 edits=8 reads=1 exact_references=true "
            "typed_conditions=true global_initial=true history=true "
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
