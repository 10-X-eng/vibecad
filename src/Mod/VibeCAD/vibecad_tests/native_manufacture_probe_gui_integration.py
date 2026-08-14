# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for bounded Native CAM Probe grids."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Base.Util as PathUtil
import Path as CamPath
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Gui.Base as PathOpGui
import Path.Op.Probe as PathProbe
import Path.Tool.Controller as PathToolController
from Path.Tool.toolbit import ToolBit
import PathScripts.PathUtils as PathUtils
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureProbeSchema import MANUFACTURE_PROBE_CAPABILITY_NAME
from VibeCADNativeManufactureState import (
    _path_sha256,
    job_state,
    operation_state,
    tool_controller_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    program = next(group for group in surface.groups if group.label == "Program")
    assert tuple(action.command_id for action in program.actions) == (
        "CAM_Comment",
        "CAM_Stop",
        "CAM_Custom",
        "CAM_Probe",
    )
    return controller, surface


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(job, controller, *, label="Native Probe Grid") -> dict:
    bounds = job.Stock.Shape.BoundBox
    return {
        "operation": "create_grid",
        "label": label,
        "job": _target(job_state(job)),
        "tool_controller": _target(tool_controller_state(controller)),
        "grid": {
            "point_count_x": 3,
            "point_count_y": 4,
            "x_offset_mm": 1.25,
            "y_offset_mm": -0.75,
        },
        "motion": {
            "probe_depth_mm": float(bounds.ZMax) - 2.0,
            "safe_height_mm": float(bounds.ZMax) + 3.0,
            "clearance_height_mm": float(bounds.ZMax) + 6.0,
        },
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_PROBE_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("create_grid",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    assert set(variant["required"]) == {
        "operation",
        "label",
        "job",
        "tool_controller",
        "grid",
        "motion",
    }
    assert set(variant["properties"]) == set(variant["required"])
    assert not {"file", "path", "output_file", "filename"} & set(
        variant["properties"]
    )
    grid = variant["properties"]["grid"]
    assert grid["additionalProperties"] is False
    assert grid["properties"]["point_count_x"]["minimum"] == 3
    assert grid["properties"]["point_count_x"]["maximum"] == 64
    assert "1024" in grid["properties"]["point_count_x"]["description"]
    assert variant["properties"]["motion"]["additionalProperties"] is False
    assert "parentheses" in variant["properties"]["label"]["description"]
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MANUFACTURE_PROBE_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _fixture(document):
    model = document.addObject("Part::Feature", "ProbeGateModel")
    model.Label = "Probe gate model"
    model.Shape = Part.makeBox(24.0, 18.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None
    default_controller = job.Tools.Group[0]

    document.openTransaction("Create probe controller fixture")
    transaction = int(document.getBookedTransactionID())
    assert transaction
    try:
        extension = PathUtil.stageTimelineResourceGraphExtension(job)
        toolbit = ToolBit.from_shape_id("probe.fcstd")
        tool = toolbit.attach_to_doc(doc=document, timeline_owner=job)
        probe_controller = PathToolController.Create(
            name="NativeProbeController",
            tool=tool,
            toolNumber=max(int(value.ToolNumber) for value in job.Tools.Group) + 1,
            document=document,
            timelineOwner=job,
        )
        probe_controller.Label = "Native probe controller"
        probe_controller.VertFeed = "125 mm/min"
        probe_controller.HorizFeed = "300 mm/min"
        probe_controller.VertRapid = "900 mm/min"
        probe_controller.HorizRapid = "900 mm/min"
        job.Proxy.addToolController(probe_controller)
        assert document.recompute(None, True, True) is not False
        PathUtil.finalizeTimelineResourceGraphExtension(
            job,
            extension,
            PathUtil.toolControllerResourceGraph(probe_controller),
        )
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    assert PathUtils.getToolShapeName(probe_controller.Tool) == "probe"
    assert PathUtils.getToolShapeName(default_controller.Tool) != "probe"
    return model, job, probe_controller, default_controller


def _dismiss_task() -> None:
    button = None
    for button_box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not button_box.isVisible():
            continue
        for standard in (
            QtWidgets.QDialogButtonBox.Cancel,
            QtWidgets.QDialogButtonBox.Abort,
            QtWidgets.QDialogButtonBox.Close,
        ):
            candidate = button_box.button(standard)
            if candidate is not None and candidate.isVisible() and candidate.isEnabled():
                button = candidate
                break
        if button is not None:
            break
    assert button is not None
    button.click()
    _events(20)
    assert not Gui.Control.activeDialog()


def _human_probe_editor_gate(document, job) -> None:
    Gui.Selection.clearSelection()
    before_objects = tuple(document.Objects)
    before_undo = int(document.UndoCount)
    assert Gui.isCommandActive("CAM_Probe")
    Gui.runCommand("CAM_Probe")
    _events(20)
    assert Gui.Control.activeDialog()
    created = [obj for obj in document.Objects if obj not in before_objects]
    assert len(created) == 1
    operation = created[0]
    assert isinstance(operation.Proxy, PathProbe.ObjectProbing)
    assert operation.getTypeIdOfProperty("Xoffset") == "App::PropertyDistance"
    assert operation.getTypeIdOfProperty("Yoffset") == "App::PropertyDistance"
    operation.Xoffset = "-1.25 mm"
    operation.Yoffset = "-0.75 mm"
    assert float(operation.Xoffset.Value) == -1.25
    assert float(operation.Yoffset.Value) == -0.75
    _dismiss_task()
    assert tuple(document.Objects) == before_objects
    assert tuple(job.Operations.Group) == ()
    assert int(document.UndoCount) == before_undo


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visibility(document) -> tuple:
    return tuple(
        (obj.Name, bool(obj.ViewObject.Visibility))
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    )


def _timeline(document) -> tuple:
    timeline = document.VibeCADTimeline
    return (
        tuple(timeline.Operations),
        tuple(bool(value) for value in timeline.VisibilityAtEnd),
        tuple(bool(value) for value in timeline.SuppressionAtEnd),
        int(timeline.Position),
    )


def _assert_probe(operation, job, controller) -> None:
    assert isinstance(operation.Proxy, PathProbe.ObjectProbing)
    assert isinstance(operation.ViewObject.Proxy, PathOpGui.ViewProvider)
    assert PathUtils.findParentJob(operation) is job
    assert PathUtil.timelineParentJob(operation) is job
    assert PathUtil.toolControllerForOp(operation) is controller
    assert str(operation.OutputFileName) == ""
    assert int(operation.PointCountX) == 3
    assert int(operation.PointCountY) == 4
    commands = tuple(operation.Path.Commands)
    assert len(commands) == 41
    assert commands[0].Name == f"({operation.Label})"
    assert commands[1].Name == "(Begin Probing )"
    assert dict(commands[1].Annotations) == {"probe_open": ""}
    assert commands[-2].Name == "(PROBECLOSE)"
    assert dict(commands[-2].Annotations) == {"probe_close": ""}
    assert sum(command.Name == "G38.2" for command in commands) == 12
    assert operation.Proxy.getGenerationDiagnostics(operation)["status"] == "succeeded"


def _assert_annotation_aware_identity() -> None:
    automatic = CamPath.Command("(Begin Probing )", {}, {"probe_open": ""})
    named = CamPath.Command(
        "(Begin Probing )",
        {},
        {"probe_open": "operator-selected.probe"},
    )
    assert automatic.Name == named.Name
    assert dict(automatic.Parameters) == dict(named.Parameters)
    assert automatic.toGCode() != named.toGCode()
    assert _path_sha256((automatic,)) != _path_sha256((named,))


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-probe-")
        save_path = Path(temporary.name) / "native-manufacture-probe.FCStd"
        document = App.newDocument("NativeManufactureProbeGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        _assert_annotation_aware_identity()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Probe"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
            plan.background_required,
        ) == (
            MANUFACTURE_PROBE_CAPABILITY_NAME,
            "create_grid",
            "ExactCamJobProbeControllerAndBoundedStockGrid",
            True,
            False,
            False,
        )

        model, job, probe_controller, default_controller = _fixture(document)
        _human_probe_editor_gate(document, job)
        document.saveAs(str(save_path))
        document.clearUndos()
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-probe-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller_widget)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                controller_widget
            ).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
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

        def call(payload: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                MANUFACTURE_PROBE_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-probe-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        stale_job = _arguments(job, probe_controller)
        stale_job["job"]["expected_state_sha256"] = "0" * 64
        assert call(stale_job, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        stale_controller = _arguments(job, probe_controller)
        stale_controller["tool_controller"]["expected_state_sha256"] = "0" * 64
        assert call(stale_controller, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        wrong_tool = _arguments(job, default_controller)
        assert call(wrong_tool, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )

        provider_path = _arguments(job, probe_controller)
        provider_path["output_file"] = "/tmp/provider-controlled.probe"
        assert call(provider_path, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        injected = _arguments(job, probe_controller, label="close) M30 (open")
        assert call(injected, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        too_small = _arguments(job, probe_controller)
        too_small["grid"]["point_count_x"] = 2
        assert call(too_small, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        too_large = _arguments(job, probe_controller)
        too_large["grid"]["point_count_x"] = 64
        too_large["grid"]["point_count_y"] = 64
        workload = call(too_large, succeeds=False)
        assert workload["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
        assert workload["repair"]["maximum_point_count"] == 1024

        bounds = job.Stock.Shape.BoundBox
        below_stock = _arguments(job, probe_controller)
        below_stock["motion"]["probe_depth_mm"] = float(bounds.ZMin) - 0.1
        assert call(below_stock, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )
        at_top = _arguments(job, probe_controller)
        at_top["motion"]["probe_depth_mm"] = float(bounds.ZMax)
        assert call(at_top, succeeds=False)["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        unsafe_height = _arguments(job, probe_controller)
        unsafe_height["motion"]["safe_height_mm"] = float(bounds.ZMax)
        assert call(unsafe_height, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )
        bad_clearance = _arguments(job, probe_controller)
        bad_clearance["motion"]["clearance_height_mm"] = float(bounds.ZMax) + 1.0
        assert call(bad_clearance, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )
        assert tuple(job.Operations.Group) == ()

        document.openTransaction("Caller-owned Probe transaction")
        transaction = int(document.getBookedTransactionID())
        assert call(_arguments(job, probe_controller), succeeds=False)[
            "error_code"
        ] == "NATIVE_TRANSACTION_ACTIVE"
        App.closeActiveTransaction(True, transaction)
        assert not document.HasPendingTransaction

        before_objects = tuple(document.Objects)
        before_group = tuple(job.Operations.Group)
        before_visibility = _visibility(document)
        before_timeline = _timeline(document)
        before_undo = int(document.UndoCount)
        with patch(
            "VibeCADNativeManufactureProbeRuntime.verify_created_probe",
            side_effect=RuntimeError("forced Probe postcondition failure"),
        ):
            failed = call(_arguments(job, probe_controller), succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == before_objects
        assert tuple(job.Operations.Group) == before_group
        assert _visibility(document) == before_visibility
        assert _timeline(document) == before_timeline
        assert _selection() == selection_before
        assert int(document.UndoCount) == before_undo

        result = call(_arguments(job, probe_controller))
        operation = document.getObject(result["object_name"])
        operation_name = str(operation.Name)
        job_name = str(job.Name)
        controller_name = str(probe_controller.Name)
        _assert_probe(operation, job, probe_controller)
        assert result["operation"] == "create_grid"
        assert result["point_count_x"] == 3
        assert result["point_count_y"] == 4
        assert result["point_count"] == 12
        assert result["path_command_count"] == 41
        assert result["output_naming"] == "automatic_at_postprocess"
        assert set(result["commanded_bounds_xy_mm"]) == {
            "x_min",
            "y_min",
            "x_max",
            "y_max",
        }
        assert result["path_sha256"] == operation_state(operation)["path_sha256"]
        assert result["tool_controller_state_sha256"] == tool_controller_state(
            probe_controller
        )["state_sha256"]
        assert not {"commands", "gcode", "output_file", "file_path"} & set(result)
        assert len(result["receipt"]["created"]) == 1
        assert len(result["receipt"]["changed"]) == 1
        assert result["assistant_undo_available"] is True
        assert tuple(job.Operations.Group) == (operation,)
        assert _selection() == selection_before
        assert _visibility(document)[: len(before_visibility)] == before_visibility
        assert int(document.UndoCount) == before_undo + 1

        document.undo()
        _events(12)
        job = document.getObject(job_name)
        probe_controller = document.getObject(controller_name)
        assert document.getObject(operation_name) is None
        assert tuple(job.Operations.Group) == ()
        document.redo()
        _events(12)
        job = document.getObject(job_name)
        probe_controller = document.getObject(controller_name)
        operation = document.getObject(operation_name)
        assert tuple(job.Operations.Group) == (operation,)
        _assert_probe(operation, job, probe_controller)

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(16)
        document = App.openDocument(str(save_path))
        _events(20)
        job = document.getObject(job_name)
        probe_controller = document.getObject(controller_name)
        operation = document.getObject(operation_name)
        assert tuple(job.Operations.Group) == (operation,)
        _assert_probe(operation, job, probe_controller)

        print(
            "VIBECAD_NATIVE_MANUFACTURE_PROBE_GUI_OK "
            "ribbon=true exact_job=true exact_controller=true probe_tool=true "
            "closed_schema=true documented_limits=true no_provider_path=true "
            "stale=true injection_guard=true workload_guard=true motion_guard=true "
            "transaction_guard=true rollback=true automatic_output=true grid=true "
            "annotations=true annotation_identity=true diagnostics=true job=true "
            "history=true receipt=true "
            "human_editor=true signed_offsets=true low_noise=true selection=true "
            "visibility=true undo=true redo=true reopen=true"
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            Gui.Control.closeDialog()
        except Exception:
            pass
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        if application is not None:
            application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
