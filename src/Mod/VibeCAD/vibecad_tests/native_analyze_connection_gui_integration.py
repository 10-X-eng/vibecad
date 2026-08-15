# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native FEM contact and tie tools."""

from __future__ import annotations

import json
import math
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
from VibeCADNativeAnalyzeConnectionSchema import ANALYZE_CONNECTION_CAPABILITY_NAME
from VibeCADNativeAnalyzeConnectionState import connection_state
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
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


OPERATIONS = ("create_contact", "create_tie", "update_contact", "update_tie")


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
    assert {"FEM_ConstraintContact", "FEM_ConstraintTie"} <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    connection = registry.definition(ANALYZE_CONNECTION_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and connection is not None and inspect is not None
    expected_actions = {
        "FEM_ConstraintContact": "create_contact",
        "FEM_ConstraintTie": "create_tie",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_CONNECTION_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in connection.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    expected_contexts = {
        "VibeCAD_AnalyzeReadConnection": (
            ANALYZE_INSPECT_CAPABILITY_NAME,
            "connection",
        ),
        "VibeCAD_AnalyzeUpdateContact": (
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            "update_contact",
        ),
        "VibeCAD_AnalyzeUpdateTie": (
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            "update_tie",
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
                ANALYZE_CONNECTION_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                connection.provider_schema(OPERATIONS),
                inspect.provider_schema(("connection",)),
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


def _connection_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _endpoint(source, subelement: str) -> dict:
    return {
        "object_name": source.Name,
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelement": subelement,
    }


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
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-connection-"
        )
        save_path = Path(temporary.name) / "native-analyze-connection.FCStd"
        document = App.newDocument("NativeAnalyzeConnectionGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        first = _publish_box(document, "ConnectionPartA", 0.0)
        second = _publish_box(document, "ConnectionPartB", 30.0)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-connection-gui")

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
                f"native-analyze-connection-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Mechanical Connection Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(analysis_result["created_analysis"]["object_name"])
        current_analysis = analysis_state(analysis)
        contact_values = {
            "contact_stiffness_gpa_per_m": 1.0e6,
            "clearance_adjustment_mm": 0.0,
            "friction": {"kind": "frictionless"},
        }

        mixed = call(
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            {
                "operation": "create_contact",
                "analysis": _analysis_target(current_analysis),
                "label": "Mixed Dimensions Must Fail",
                "slave": _endpoint(first, "Face1"),
                "master": _endpoint(second, "Edge1"),
                "connection": contact_values,
            },
            succeeds=False,
        )
        assert "both be faces" in mixed["error"]
        same = call(
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            {
                "operation": "create_tie",
                "analysis": _analysis_target(current_analysis),
                "label": "Same Endpoint Must Fail",
                "slave": _endpoint(first, "Face1"),
                "master": _endpoint(first, "Face1"),
                "connection": {"tolerance_mm": 0.05, "adjust": False},
            },
            succeeds=False,
        )
        assert "different subelements" in same["error"]
        assert analysis_state(analysis) == current_analysis

        contact_result = call(
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            {
                "operation": "create_contact",
                "analysis": _analysis_target(current_analysis),
                "label": "Frictionless Contact",
                "slave": _endpoint(first, "Face2"),
                "master": _endpoint(second, "Face1"),
                "connection": contact_values,
            },
        )
        contact = document.getObject(contact_result["created_connection"]["object_name"])
        current_analysis = analysis_state(analysis)
        tie_result = call(
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            {
                "operation": "create_tie",
                "analysis": _analysis_target(current_analysis),
                "label": "Bonded Interface",
                "slave": _endpoint(first, "Face3"),
                "master": _endpoint(second, "Face4"),
                "connection": {"tolerance_mm": 0.1, "adjust": False},
            },
        )
        tie = document.getObject(tie_result["created_connection"]["object_name"])
        assert str(contact.SurfaceBehavior) == "Linear"
        assert not bool(contact.Friction)
        assert math.isclose(contact.Slope.getValueAs("GPa/m").Value, 1.0e6)
        assert math.isclose(tie.Tolerance.getValueAs("mm").Value, 0.1)

        contact_before = connection_state(contact)
        contact_update = call(
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            {
                "operation": "update_contact",
                "target": _connection_target(contact_before),
                "label": "Frictional Contact",
                "slave": _endpoint(first, "Face5"),
                "master": _endpoint(second, "Face6"),
                "connection": {
                    "contact_stiffness_gpa_per_m": 2.5e6,
                    "clearance_adjustment_mm": 0.2,
                    "friction": {
                        "kind": "coulomb",
                        "coefficient": 0.28,
                        "stick_stiffness_gpa_per_m": 1.0e4,
                    },
                },
            },
        )
        tie_before = connection_state(tie)
        tie_update = call(
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            {
                "operation": "update_tie",
                "target": _connection_target(tie_before),
                "label": "Adjusted 2D Tie",
                "slave": _endpoint(first, "Edge2"),
                "master": _endpoint(second, "Edge3"),
                "connection": {"tolerance_mm": 0.35, "adjust": True},
            },
        )
        assert contact_update["updated_connection"]["slave"]["subelement"] == "Face5"
        assert contact_update["updated_connection"]["definition"]["friction"] == {
            "kind": "coulomb",
            "coefficient": 0.28,
            "stick_stiffness_gpa_per_m": 1.0e4,
        }
        assert bool(contact.Friction)
        assert math.isclose(contact.FrictionCoefficient, 0.28)
        assert tie_update["updated_connection"]["slave"]["subelement"] == "Edge2"
        assert bool(tie.Adjust)

        stale = call(
            ANALYZE_CONNECTION_CAPABILITY_NAME,
            {
                "operation": "update_tie",
                "target": _connection_target(tie_before),
                "label": "Must Not Apply",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert str(tie.Label) == "Adjusted 2D Tie"

        connections = (contact, tie)
        read_revision = state.current_revision(str(document.Uid))
        for connection in connections:
            current = connection_state(connection)
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "connection",
                    "target": _connection_target(current),
                },
            )
            assert read["connection"] == current
        assert state.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["connection_count"] == 2
        assert not snapshot["connections_truncated"]
        assert {item["connection_kind"] for item in snapshot["connections"]} == {
            "contact",
            "tie",
        }
        assert tuple(analysis.Group) == connections
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            first.Name,
            second.Name,
            analysis.Name,
            contact.Name,
            tie.Name,
        )

        document.undo()
        assert connection_state(tie)["state_sha256"] == tie_before["state_sha256"]
        document.redo()
        assert connection_state(tie)["definition"] == tie_update["updated_connection"][
            "definition"
        ]

        expected = {obj.Name: connection_state(obj) for obj in connections}
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
            new_state = connection_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["slave"] == old_state["slave"]
            assert new_state["master"] == old_state["master"]

        print(
            "VIBECAD_NATIVE_ANALYZE_CONNECTION_GUI_OK "
            "actions=2 edits=2 reads=1 exact_roles=true typed_contact=true "
            "history=true undo_redo=true reopen=true read_revision_stable=true",
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
