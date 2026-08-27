# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact bounded Native Manufacture reads."""

from __future__ import annotations

import json
from pathlib import Path as FilePath
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Path as CamPath
import Path.Main.Job as PathJob
import PathScripts.PathUtils as PathUtils
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackground import NativeBackgroundManager
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeContextManifest import provider_context_actions_for_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureFocusedInspectSchema import (
    MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES,
)
from VibeCADNativeManufactureJobSchema import MANUFACTURE_JOB_CAPABILITY_NAME
from VibeCADNativeManufactureToolSchema import MANUFACTURE_TOOL_CAPABILITY_NAME
from VibeCADNativeManufactureInspect import validate_job as validate_job_direct
from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot
from VibeCADNativeManufactureState import (
    candidate_model_state,
    job_state,
    operation_state,
    tool_controller_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import document_uid, read_current_selection
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


JOB_OPERATIONS = (
    "configure_stock",
    "create_job",
    "orient_workpiece",
    "update_setup",
)
TOOL_OPERATIONS = ("update_controller",)


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _await(manager: NativeBackgroundManager, job_id: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(2)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"Background CAM geometry read {job_id} did not finish")


def _surface():
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    main_window = Gui.getMainWindow()
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture", surface.surface_id
    return controller, surface


def _commit(document, label: str, action):
    document.openTransaction(label)
    transaction = int(document.getBookedTransactionID())
    assert transaction
    try:
        value = action()
        assert document.recompute(None, True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return value


def _fixture(document, stem: str):
    def create_model():
        model = document.addObject("Part::Feature", f"{stem}CamModel")
        model.Label = f"{stem} CAM model"
        model.Shape = Part.makeBox(40.0, 30.0, 12.0).cut(
            Part.makeCylinder(2.5, 12.0, App.Vector(20.0, 15.0, 0.0))
        )
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create CAM inspection model", create_model)

    job = _commit(
        document,
        f"Create {stem} CAM Job",
        lambda: PathJob.Create(f"{stem}InspectionJob", [model]),
    )
    job.PostProcessor = "grbl"

    def create_operation():
        operation = document.addObject(
            "Path::Feature",
            f"{stem}InspectionOperation",
        )
        operation.Label = f"{stem} inspection path"
        if "Active" not in operation.PropertiesList:
            operation.addProperty("App::PropertyBool", "Active")
        operation.Active = True
        if "ToolController" not in operation.PropertiesList:
            operation.addProperty("App::PropertyLink", "ToolController")
        controllers = tuple(getattr(job.Tools, "Group", ()) or ())
        operation.ToolController = controllers[0] if controllers else None
        if "CycleTime" not in operation.PropertiesList:
            operation.addProperty("App::PropertyString", "CycleTime")
        operation.CycleTime = "00:00:12"
        operation.Path = CamPath.Path(
            [
                CamPath.Command("G0", {"X": 0.0, "Y": 0.0, "Z": 5.0}),
                CamPath.Command("G1", {"X": 20.0, "Y": 0.0, "Z": 0.0, "F": 8.0}),
                CamPath.Command("G1", {"X": 20.0, "Y": 15.0, "Z": 0.0, "F": 8.0}),
                CamPath.Command("G0", {"X": 0.0, "Y": 0.0, "Z": 5.0}),
            ]
        )
        job.Proxy.addOperation(operation)
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
        return operation

    operation = _commit(document, "Create CAM inspection operation", create_operation)
    return model, job, operation


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _turn(surface, registry) -> NativeTurnSnapshot:
    job_definition = registry.definition(MANUFACTURE_JOB_CAPABILITY_NAME)
    tool_definition = registry.definition(MANUFACTURE_TOOL_CAPABILITY_NAME)
    inspect_definitions = tuple(
        registry.definition(name)
        for name in MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES.values()
    )
    assert all(definition is not None for definition in inspect_definitions)
    assert job_definition is not None
    assert tool_definition is not None
    schemas = (
        *(
            definition.provider_schema((definition.variants[0].operation,))
            for definition in inspect_definitions
        ),
        job_definition.provider_schema(JOB_OPERATIONS),
        tool_definition.provider_schema(TOOL_OPERATIONS),
    )
    encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    assert '"maximum":128' in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                *MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES.values(),
                MANUFACTURE_JOB_CAPABILITY_NAME,
                MANUFACTURE_TOOL_CAPABILITY_NAME,
            ),
            schemas=schemas,
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-manufacture-")
        save_path = FilePath(temporary.name) / "manufacture-inspect.FCStd"
        document = App.newDocument("NativeManufactureInspectGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        assert (
            plans["CAM_Sanity"].capability_family,
            plans["CAM_Sanity"].operation_variant,
            plans["CAM_Sanity"].exact_target_type,
        ) == (
            MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES["validate_job"],
            "validate_job",
            "ExactCamJobGraphAndState",
        )
        assert (
            plans["CAM_Inspect"].capability_family,
            plans["CAM_Inspect"].operation_variant,
            plans["CAM_Inspect"].exact_target_type,
        ) == (
            MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES["inspect_toolpath"],
            "inspect_toolpath",
            "ExactCamOperationToolpathAndState",
        )
        assert (
            plans["CAM_SelectLoop"].capability_family,
            plans["CAM_SelectLoop"].operation_variant,
            plans["CAM_SelectLoop"].exact_target_type,
        ) == (
            MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES["detect_loop"],
            "detect_loop",
            "ExactCurrentCamModelShapeAndLoopSeed",
        )
        read_plan = next(
            plan
            for plan in provider_context_actions_for_surface("manufacture")
            if plan.action_id == "VibeCAD_ManufactureReadJob"
        )
        assert (
            read_plan.capability_family,
            read_plan.operation_variant,
            read_plan.exact_target_type,
        ) == (
            MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES["read_job"],
            "read_job",
            "ExactCamJobGraphAndState",
        )
        model, job, operation = _fixture(document, "Primary")
        second_model, second_job, second_operation = _fixture(document, "Secondary")
        document.saveAs(str(save_path))
        _events(8)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        manager = NativeBackgroundManager()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-inspect-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            background_manager=manager,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_index = 0

        def call(
            arguments: dict,
            *,
            tool_name: str | None = None,
            succeeds: bool = True,
        ) -> dict:
            nonlocal call_index
            call_index += 1
            payload = dict(arguments)
            if tool_name is None:
                operation = str(payload.pop("operation"))
                tool_name = MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES[operation]
            response = dispatcher.call(
                tool_name,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-inspect-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        initial_job = job_state(job)
        initial_operation = operation_state(operation)
        initial_model = candidate_model_state(model)
        second_job_state = job_state(second_job)
        second_operation_state = operation_state(second_operation)
        second_model_state = candidate_model_state(second_model)
        direct_validation = validate_job_direct(document, _target(initial_job))
        assert direct_validation["validation"]["job"]["object_name"] == job.Name
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(job)
        snapshot = build_manufacture_snapshot(
            document,
            selection=read_current_selection(document),
        )
        assert snapshot["job_count"] == 2
        assert {
            item["object_name"]: item["state_sha256"]
            for item in snapshot["jobs"]
        } == {
            job.Name: initial_job["state_sha256"],
            second_job.Name: second_job_state["state_sha256"],
        }
        assert snapshot["active_job_resolution"] == "selection"
        active_job = snapshot["active_job"]
        assert active_job["object_name"] == job.Name
        assert active_job["state_sha256"] == initial_job["state_sha256"]
        assert active_job["stock"]["present"] is True
        assert active_job["stock"]["valid_solid"] is True
        assert active_job["machine"]["configured"] is False
        assert active_job["configuration"]["fixtures"] == ["G54"]
        assert active_job["configuration"]["postprocessor"] == "grbl"
        assert [item["object_name"] for item in active_job["tools"]] == [
            controller.Name for controller in job.Tools.Group
        ]
        assert [
            item["object_name"] for item in active_job["ordered_operations"]
        ] == [operation.Name]
        assert active_job["ordered_operations"][0]["position"] == 0
        assert active_job["ordered_operations"][0]["toolpath_valid"] is True
        assert active_job["toolpath_validity"] == {
            "all_active_valid": True,
            "active_operation_count": 1,
            "active_command_count": 4,
            "invalid_active_count": 0,
            "uninspected_active_count": 0,
        }
        assert active_job["readiness"]["simulation"]["ready"] is True
        assert active_job["readiness"]["post"]["ready"] is True
        assert active_job["readiness"]["post"]["postprocessor"] == "grbl"
        assert "path" not in json.dumps(
            active_job["readiness"],
            sort_keys=True,
        ).lower()
        model_candidates = {
            item["object_name"]: item for item in snapshot["model_candidates"]
        }
        assert set(model_candidates) == {model.Name, second_model.Name}
        assert model_candidates[model.Name]["state_sha256"] == initial_model[
            "state_sha256"
        ]

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Edge1")
        selected_snapshot = build_manufacture_snapshot(
            document,
            selection=read_current_selection(document),
        )
        assert selected_snapshot["active_job_resolution"] == "selection"
        assert selected_snapshot["active_job"]["object_name"] == job.Name
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        selected_snapshot = build_manufacture_snapshot(
            document,
            selection=read_current_selection(document),
        )
        assert selected_snapshot["active_job_resolution"] == "selection"
        assert selected_snapshot["active_job"]["object_name"] == job.Name
        turn_start_snapshot = build_active_snapshot(
            document,
            "manufacture",
            {
                "document_uid": document_uid(document),
                "structural_revision": 0,
                "recent_receipts": [],
            },
            selection=read_current_selection(document),
        )
        assert turn_start_snapshot["domain"]["active_job_resolution"] == "selection"
        assert turn_start_snapshot["domain"]["active_job"]["object_name"] == job.Name
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Edge1")
        selected_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        object_names_before = tuple(obj.Name for obj in document.Objects)

        stale = call(
            {
                "operation": "read_job",
                "target": {
                    "object_name": job.Name,
                    "expected_state_sha256": "0" * 64,
                },
                "operation_offset": 0,
                "page_size": 1,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        read = call(
            {
                "operation": "read_job",
                "target": _target(initial_job),
                "operation_offset": 0,
                "page_size": 1,
            }
        )["job"]
        assert read["operation_page"]["total"] == 1
        assert read["operation_page"]["items"][0]["object_name"] == operation.Name

        validation = call(
            {"operation": "validate_job", "target": _target(initial_job)}
        )["validation"]
        assert validation["job"]["object_name"] == job.Name
        assert validation["issue_count"] >= validation["critical_count"]
        assert all(
            set(issue) == {"severity", "source", "message"}
            for issue in validation["issues"]
        )

        path = call(
            {
                "operation": "inspect_toolpath",
                "target": _target(initial_operation),
                "offset": 1,
                "page_size": 2,
            }
        )["toolpath"]
        assert path["total"] == 4 and path["count"] == 2 and path["next_offset"] == 3
        assert [item["index"] for item in path["commands"]] == [1, 2]
        assert len(path["toolpath_sha256"]) == 64

        loop_seed = next(
            f"Edge{index}"
            for index, edge in enumerate(model.Shape.Edges, 1)
            if PathUtils.horizontalEdgeLoop(model, edge)
        )
        loop = call(
            {
                "operation": "detect_loop",
                "target": _target(initial_model),
                "selection": {"kind": "edges", "edges": [loop_seed]},
            }
        )["loop"]
        assert loop["selection"]["kind"] == "edges"
        assert loop["element_count"] >= 4
        assert all(name.startswith("Edge") for name in loop["selection"]["edges"])

        drillable_start = call(
            {
                "operation": "read_model_geometry",
                "target": _target(initial_model),
                "elements": "drillable",
            }
        )
        drillable_terminal = _await(manager, drillable_start["job"]["job_id"])
        assert drillable_terminal.phase == "completed", drillable_terminal.error
        drillable = drillable_terminal.result["model_geometry"]
        assert drillable["total"] >= 1
        assert drillable["count"] == drillable["total"]
        assert all(item["drilling"]["accepted"] for item in drillable["items"])
        assert any(
            abs(item["drilling"].get("diameter_mm", 0.0) - 5.0) < 1.0e-9
            for item in drillable["items"]
        )

        _events(8)
        assert _selection() == selected_before
        assert state_store.current_revision(context.document_uid) == revision_before
        assert int(document.UndoCount) == undo_before
        assert tuple(obj.Name for obj in document.Objects) == object_names_before
        assert job_state(job)["state_sha256"] == initial_job["state_sha256"]
        assert operation_state(operation)["state_sha256"] == initial_operation["state_sha256"]

        assert job_state(job)["state_sha256"] == initial_job["state_sha256"]
        Gui.Selection.clearSelection()
        multi_snapshot = build_manufacture_snapshot(
            document,
            selection={"items": []},
        )
        assert multi_snapshot["job_count"] == 2
        assert multi_snapshot["active_job"] is None
        assert multi_snapshot["active_job_resolution"] == "choose_job"
        setup_states = {
            item["object_name"]: item for item in multi_snapshot["jobs"]
        }
        assert set(setup_states) == {job.Name, second_job.Name}
        assert setup_states[job.Name]["readiness"]["simulation"]["ready"] is True
        assert (
            setup_states[second_job.Name]["readiness"]["simulation"]["ready"]
            is True
        )
        catalog = call(
            {
                "operation": "list_setups",
                "query": "secondary",
                "offset": 0,
                "page_size": 1,
            }
        )["setups"]
        assert catalog["total"] == 1
        assert catalog["items"][0]["object_name"] == second_job.Name
        assert catalog["items"][0]["state_sha256"] == second_job_state[
            "state_sha256"
        ], {
            "initial": second_job_state,
            "catalog": catalog["items"][0],
            "current": job_state(second_job),
        }
        postprocessors = call(
            {
                "operation": "search_setup_options",
                "category": "postprocessor",
                "query": "",
                "offset": 0,
                "page_size": 128,
            }
        )["setup_options"]
        assert postprocessors["category"] == "postprocessor"
        assert "grbl" in postprocessors["values"]
        Gui.Selection.addSelection(second_operation)
        selected_second = build_manufacture_snapshot(
            document,
            selection=read_current_selection(document),
        )
        assert selected_second["active_job_resolution"] == "selection"
        assert selected_second["active_job"]["object_name"] == second_job.Name
        first_before_setup_edit = job_state(job)
        selected_before_setup_edit = _selection()
        updated_setup = call(
            {
                "operation": "update_setup",
                "target": _target(second_job_state),
                "changes": {
                    "label": "Secondary finish setup",
                    "description": "Independent second-side finishing setup.",
                    "fixtures": ["G55"],
                    "split_output": True,
                    "output_order": "Tool",
                    "geometry_tolerance_mm": 0.005,
                },
            },
            tool_name=MANUFACTURE_JOB_CAPABILITY_NAME,
        )
        assert updated_setup["configuration"]["label"] == (
            "Secondary finish setup"
        )
        assert updated_setup["configuration"]["fixtures"] == ["G55"]
        assert updated_setup["configuration"]["output_order"] == "Tool"
        assert job_state(job)["state_sha256"] == first_before_setup_edit[
            "state_sha256"
        ]
        assert _selection() == selected_before_setup_edit
        second_job_state = updated_setup["job"]
        first_before_stock_edit = job_state(job)
        selected_before_stock_edit = _selection()
        configured_stock = call(
            {
                "operation": "configure_stock",
                "target": _target(second_job_state),
                "stock": {
                    "kind": "box",
                    "size_mm": {"x": 52.0, "y": 41.0, "z": 16.0},
                    "placement": {
                        "origin_mm": {"x": 2.0, "y": 3.0, "z": 4.0},
                        "rotation": {
                            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
                            "angle_degrees": 0.0,
                        },
                    },
                },
            },
            tool_name=MANUFACTURE_JOB_CAPABILITY_NAME,
        )
        assert configured_stock["stock"]["kind"] == "box"
        assert configured_stock["stock"]["size_mm"] == {
            "x": 52.0,
            "y": 41.0,
            "z": 16.0,
        }
        assert configured_stock["stock"]["placement"]["origin_mm"] == {
            "x": 2.0,
            "y": 3.0,
            "z": 4.0,
        }
        assert job_state(job)["state_sha256"] == first_before_stock_edit[
            "state_sha256"
        ]
        assert _selection() == selected_before_stock_edit
        second_job_state = configured_stock["job"]

        first_before_orientation = job_state(job)
        source_before_orientation = candidate_model_state(second_model)
        selected_before_orientation = _selection()
        resource_placement_before = second_job_state["models"][0][
            "resource_placement"
        ]
        oriented = call(
            {
                "operation": "orient_workpiece",
                "target": _target(second_job_state),
                "frame": {
                    "origin_mm": {"x": 5.0, "y": 0.0, "z": 0.0},
                    "x_direction_hint": {"x": 0.0, "y": 1.0, "z": 0.0},
                    "z_direction": {"x": 0.0, "y": 0.0, "z": 1.0},
                },
                "include_stock": True,
            },
            tool_name=MANUFACTURE_JOB_CAPABILITY_NAME,
        )
        assert oriented["job"]["models"][0]["resource_placement"] != (
            resource_placement_before
        )
        assert oriented["workpiece"]["stock"]["resource_name"] == (
            second_job.Stock.Name
        )
        assert job_state(job)["state_sha256"] == first_before_orientation[
            "state_sha256"
        ]
        assert candidate_model_state(second_model) == source_before_orientation
        assert _selection() == selected_before_orientation
        second_job_state = oriented["job"]

        first_before_tool_edit = job_state(job)
        selected_before_tool_edit = _selection()
        second_controller = tuple(second_job.Tools.Group)[0]
        controller_before = tool_controller_state(second_controller)
        updated_controller = call(
            {
                "operation": "update_controller",
                "target": _target(controller_before),
                "controller": {
                    "label": "Secondary finishing tool",
                    "tool_number": {
                        "kind": "explicit",
                        "value": controller_before["tool_number"],
                    },
                    "tool_length_offset": controller_before[
                        "tool_length_offset"
                    ],
                    "spindle_speed_rpm": 12000.0,
                    "spindle_direction": "Forward",
                    "horizontal_feed_mm_per_minute": 800.0,
                    "vertical_feed_mm_per_minute": 240.0,
                    "ramp_feed_mm_per_minute": 180.0,
                    "lead_in_feed_mm_per_minute": 350.0,
                    "lead_out_feed_mm_per_minute": 350.0,
                    "horizontal_rapid_mm_per_minute": 2500.0,
                    "vertical_rapid_mm_per_minute": 1200.0,
                },
            },
            tool_name=MANUFACTURE_TOOL_CAPABILITY_NAME,
        )["controller"]
        assert updated_controller["spindle_speed_rpm"] == 12000.0
        assert updated_controller["horizontal_feed_mm_per_minute"] == 800.0
        assert job_state(job)["state_sha256"] == first_before_tool_edit[
            "state_sha256"
        ]
        assert _selection() == selected_before_tool_edit
        second_job_state = job_state(second_job)
        second_operation_state = operation_state(second_operation)

        document.save()
        job_name = job.Name
        operation_name = operation.Name
        model_name = model.Name
        second_job_name = second_job.Name
        second_operation_name = second_operation.Name
        second_model_name = second_model.Name
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        reopened_job = document.getObject(job_name)
        reopened_operation = document.getObject(operation_name)
        reopened_model = document.getObject(model_name)
        reopened_second_job = document.getObject(second_job_name)
        reopened_second_operation = document.getObject(second_operation_name)
        reopened_second_model = document.getObject(second_model_name)
        assert reopened_job is not None and reopened_operation is not None
        assert reopened_model is not None
        assert reopened_second_job is not None and reopened_second_operation is not None
        assert reopened_second_model is not None
        reopened_job_state = job_state(reopened_job)
        reopened_operation_state = operation_state(reopened_operation)
        reopened_model_state = candidate_model_state(reopened_model)
        assert reopened_job_state["state_sha256"] == initial_job["state_sha256"], {
            "before": initial_job,
            "after": reopened_job_state,
        }
        assert (
            reopened_operation_state["state_sha256"]
            == initial_operation["state_sha256"]
        ), {"before": initial_operation, "after": reopened_operation_state}
        assert reopened_model_state["state_sha256"] == initial_model["state_sha256"], {
            "before": initial_model,
            "after": reopened_model_state,
        }
        assert job_state(reopened_second_job)["state_sha256"] == second_job_state[
            "state_sha256"
        ]
        assert operation_state(reopened_second_operation)["state_sha256"] == (
            second_operation_state["state_sha256"]
        )
        assert candidate_model_state(reopened_second_model)["state_sha256"] == (
            second_model_state["state_sha256"]
        )

        original_path = reopened_operation.Path
        try:
            reopened_operation.Path = CamPath.Path()
            assert document.recompute(None, True, True) is not False
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(reopened_operation)
            invalid_snapshot = build_manufacture_snapshot(
                document,
                selection=read_current_selection(document),
            )
            invalid_active = invalid_snapshot["active_job"]
            assert invalid_active["ordered_operations"][0]["toolpath_valid"] is False
            assert invalid_active["ordered_operations"][0]["toolpath_issue"] == (
                "TOOLPATH_EMPTY"
            )
            assert invalid_active["readiness"]["simulation"]["ready"] is False
            assert invalid_active["readiness"]["post"]["ready"] is False
        finally:
            reopened_operation.Path = original_path
            assert document.recompute(None, True, True) is not False

        print(
            "VIBECAD_NATIVE_MANUFACTURE_INSPECT_GUI_OK "
            "job_state=true sanity=true toolpath_paging=true loop=true "
            "geometry_background=true drillable_geometry=true "
            "active_job=true human_selection=true stock=true machine=true "
            "tools=true ordered_operations=true toolpath_validity=true "
            "simulation_readiness=true post_readiness=true low_noise=true "
            "invalid_toolpath_reason=true "
            "stale_rejection=true read_only=true multi_setup=true "
            "explicit_scope=true setup_catalog=true setup_options=true "
            "setup_edit=true stock_edit=true workpiece_orientation=true "
            "tool_edit=true reopen=true",
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
