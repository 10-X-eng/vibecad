# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native FEM mechanical-load tools."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeLoadSchema import ANALYZE_LOAD_CAPABILITY_NAME
from VibeCADNativeAnalyzeLoadState import load_state
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
from VibeCADNativeAnalyzeState import analysis_state
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


KINDS = ("force", "pressure", "centrifugal", "gravity")
OPERATIONS = tuple(
    operation
    for kind in KINDS
    for operation in (f"create_{kind}", f"update_{kind}")
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
        "FEM_ConstraintForce",
        "FEM_ConstraintPressure",
        "FEM_ConstraintCentrif",
        "FEM_ConstraintSelfWeight",
    } <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    load = registry.definition(ANALYZE_LOAD_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and load is not None and inspect is not None
    expected_actions = {
        "FEM_ConstraintForce": "create_force",
        "FEM_ConstraintPressure": "create_pressure",
        "FEM_ConstraintCentrif": "create_centrifugal",
        "FEM_ConstraintSelfWeight": "create_gravity",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_LOAD_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in load.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    expected_contexts = {
        "VibeCAD_AnalyzeReadLoad": (ANALYZE_INSPECT_CAPABILITY_NAME, "load"),
        "VibeCAD_AnalyzeUpdateForce": (ANALYZE_LOAD_CAPABILITY_NAME, "update_force"),
        "VibeCAD_AnalyzeUpdatePressure": (
            ANALYZE_LOAD_CAPABILITY_NAME,
            "update_pressure",
        ),
        "VibeCAD_AnalyzeUpdateCentrifugal": (
            ANALYZE_LOAD_CAPABILITY_NAME,
            "update_centrifugal",
        ),
        "VibeCAD_AnalyzeUpdateGravity": (
            ANALYZE_LOAD_CAPABILITY_NAME,
            "update_gravity",
        ),
    }
    for action_id, expected in expected_contexts.items():
        action = contexts[action_id]
        assert (action.capability_family, action.operation_variant) == expected
        definition = registry.definition(expected[0])
        assert any(
            variant.operation == expected[1] and action_id in variant.action_ids
            for variant in definition.variants
        )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_LOAD_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                load.provider_schema(OPERATIONS),
                inspect.provider_schema(("load",)),
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


def _load_target(state: dict) -> dict:
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


def _singular(source, subelement: str) -> dict:
    return {
        "object_name": source.Name,
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelement": subelement,
    }


def _publish_source(document, type_id: str, name: str):
    document.openTransaction(f"Create {name}")
    try:
        source = document.addObject(type_id, name)
        if type_id == "Part::Box":
            source.Length = 30.0
            source.Width = 20.0
            source.Height = 10.0
        else:
            source.Radius = 8.0
            source.Height = 25.0
            source.Placement.Base.x = 40.0
        assert document.recompute([source], True, True) is not False
        assert not source.Shape.isNull() and source.Shape.isValid()
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _cylindrical_face(source) -> str:
    return next(
        f"Face{index}"
        for index, face in enumerate(source.Shape.Faces, 1)
        if isinstance(face.Surface, Part.Cylinder)
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-load-")
        save_path = Path(temporary.name) / "native-analyze-load.FCStd"
        document = App.newDocument("NativeAnalyzeLoadGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        box = _publish_source(document, "Part::Box", "LoadGeometry")
        cylinder = _publish_source(document, "Part::Cylinder", "CurvedGeometry")
        curved_face = _cylindrical_face(cylinder)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-load-gui")

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
                f"native-analyze-load-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Mechanical Load Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(analysis_result["created_analysis"]["object_name"])
        current_analysis = analysis_state(analysis)

        curved_direction = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "create_force",
                "analysis": _analysis_target(current_analysis),
                "label": "Curved Direction Must Fail",
                "references": _references(box, "Face1"),
                "force_n": 100.0,
                "direction": {
                    "kind": "reference",
                    **_singular(cylinder, curved_face),
                    "reversed": False,
                },
            },
            succeeds=False,
        )
        assert "not a planar face" in curved_direction["error"]
        assert analysis_state(analysis) == current_analysis

        force_result = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "create_force",
                "analysis": _analysis_target(current_analysis),
                "label": "Normal Face Force",
                "references": _references(box, "Face1"),
                "force_n": 100.0,
                "direction": {"kind": "normal", "reversed": False},
            },
        )
        force = document.getObject(force_result["created_load"]["object_name"])
        current_analysis = analysis_state(analysis)
        pressure_result = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "create_pressure",
                "analysis": _analysis_target(current_analysis),
                "label": "Face Pressure",
                "references": _references(box, "Face2", "Face3"),
                "pressure_pa": 500000.0,
                "reversed": False,
            },
        )
        pressure = document.getObject(pressure_result["created_load"]["object_name"])
        current_analysis = analysis_state(analysis)
        centrifugal_result = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "create_centrifugal",
                "analysis": _analysis_target(current_analysis),
                "label": "Global Spin",
                "rotation_frequency_hz": 25.0,
                "axis": _singular(box, "Edge1"),
                "scope": {"kind": "all_bodies"},
            },
        )
        centrifugal = document.getObject(
            centrifugal_result["created_load"]["object_name"]
        )
        current_analysis = analysis_state(analysis)
        gravity_result = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "create_gravity",
                "analysis": _analysis_target(current_analysis),
                "label": "Standard Gravity",
                "acceleration_m_s2": 9.80665,
                "direction": {"x": 0.0, "y": 0.0, "z": -1.0},
            },
        )
        gravity = document.getObject(gravity_result["created_load"]["object_name"])
        current_analysis = analysis_state(analysis)
        duplicate_gravity = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "create_gravity",
                "analysis": _analysis_target(current_analysis),
                "label": "Duplicate Gravity",
                "acceleration_m_s2": 9.81,
                "direction": {"x": 0.0, "y": -1.0, "z": 0.0},
            },
            succeeds=False,
        )
        assert "one global gravity load" in duplicate_gravity["error"]

        assert math.isclose(force.Force.getValueAs("N").Value, 100.0)
        assert force.Direction is None
        assert force.DirectionVector.Length > 0.99
        assert math.isclose(pressure.Pressure.getValueAs("Pa").Value, 500000.0)
        assert math.isclose(
            centrifugal.RotationFrequency.getValueAs("1/s").Value,
            25.0,
        )
        assert math.isclose(
            gravity.GravityAcceleration.getValueAs("m/s^2").Value,
            9.80665,
        )

        force_before = load_state(force)
        force_update = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "update_force",
                "target": _load_target(force_before),
                "label": "Referenced Edge Force",
                "references": _references(box, "Vertex1", "Vertex2"),
                "force_n": 240.0,
                "direction": {
                    "kind": "reference",
                    **_singular(box, "Edge2"),
                    "reversed": True,
                },
            },
        )
        pressure_update = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "update_pressure",
                "target": _load_target(load_state(pressure)),
                "label": "Reversed 2D Pressure",
                "references": _references(box, "Edge3", "Edge4"),
                "pressure_pa": 750000.0,
                "reversed": True,
            },
        )
        centrifugal_update = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "update_centrifugal",
                "target": _load_target(load_state(centrifugal)),
                "label": "Selected Body Spin",
                "rotation_frequency_hz": 30.0,
                "axis": _singular(box, "Edge3"),
                "scope": {
                    "kind": "selected_geometry",
                    "references": _references(box, "Solid1"),
                },
            },
        )
        gravity_before = load_state(gravity)
        gravity_update = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "update_gravity",
                "target": _load_target(gravity_before),
                "label": "Oblique Gravity",
                "acceleration_m_s2": 9.81,
                "direction": {"x": 1.0, "y": -2.0, "z": -3.0},
            },
        )
        assert force_update["updated_load"]["definition"]["direction"] == {
            "kind": "reference",
            "object_name": box.Name,
            "subelement": "Edge2",
            "reversed": True,
        }
        assert force.DirectionVector.Length > 0.99
        assert pressure_update["updated_load"]["references"][0]["subelements"] == [
            "Edge3",
            "Edge4",
        ]
        assert centrifugal_update["updated_load"]["definition"]["scope"]["kind"] == (
            "selected_geometry"
        )
        gravity_direction = gravity_update["updated_load"]["definition"]["direction"]
        assert math.isclose(
            math.sqrt(sum(value * value for value in gravity_direction.values())),
            1.0,
            abs_tol=1.0e-12,
        )

        stale = call(
            ANALYZE_LOAD_CAPABILITY_NAME,
            {
                "operation": "update_force",
                "target": _load_target(force_before),
                "force_n": 999.0,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert math.isclose(force.Force.getValueAs("N").Value, 240.0)

        loads = (force, pressure, centrifugal, gravity)
        read_revision = state.current_revision(str(document.Uid))
        for load in loads:
            current = load_state(load)
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {"operation": "load", "target": _load_target(current)},
            )
            assert read["load"] == current
        assert state.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["load_count"] == 4
        assert not snapshot["loads_truncated"]
        assert {item["load_kind"] for item in snapshot["loads"]} == set(KINDS)
        assert tuple(analysis.Group) == loads
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            box.Name,
            cylinder.Name,
            analysis.Name,
            *(obj.Name for obj in loads),
        )

        document.undo()
        assert load_state(gravity)["state_sha256"] == gravity_before["state_sha256"]
        document.redo()
        assert load_state(gravity)["definition"] == gravity_update["updated_load"][
            "definition"
        ]

        expected = {obj.Name: load_state(obj) for obj in loads}
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
            new_state = load_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["references"] == old_state["references"]

        print(
            "VIBECAD_NATIVE_ANALYZE_LOAD_GUI_OK "
            "actions=4 edits=4 reads=1 exact_directions=true typed_scopes=true "
            "global_gravity=true history=true undo_redo=true reopen=true "
            "read_revision_stable=true",
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
