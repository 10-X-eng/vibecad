# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Drilling and Tapping."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Base.Util as PathUtil
import Path.Main.Gui.Job as PathJobGui
import Path.Main.Job as PathJob
import Path.Tool.Controller as PathToolController
from Path.Tool.toolbit import ToolBit
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureState import job_state, operation_state
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


def _create_model_and_job(document):
    def create_model():
        model = document.addObject("Part::Feature", "DrillingGateModel")
        model.Label = "Drilling gate model"
        blank = Part.makeBox(50.0, 40.0, 12.0)
        left = Part.makeCylinder(7.0, 12.0, App.Vector(14.0, 20.0, 0.0))
        right = Part.makeCylinder(7.0, 12.0, App.Vector(36.0, 20.0, 0.0))
        model.Shape = blank.cut(left.fuse(right))
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Drilling gate model", create_model)

    def create_job():
        job = PathJob.Create("DrillingJob", [model], templateFile=None)
        provider = PathJobGui.ViewProvider(job.ViewObject)
        job.ViewObject.Proxy = provider
        job.ViewObject.addExtension("Gui::ViewProviderGroupExtensionPython")
        provider.setupEditVisibility(job)
        try:
            provider.syncTimelineReplacedInputs(job)
        finally:
            provider.resetEditVisibility(job)
        provider.applyAcceptedReplacementVisibilityTransition(job)
        provider.deleteOnReject = False
        return job

    job = _commit(document, "Create Drilling gate Job", create_job)

    def create_controller(tool_file: str, name: str, label: str, spindle_speed: int):
        extension = PathUtil.stageTimelineResourceGraphExtension(job)
        toolbit = ToolBit.from_file(
            Path(App.getResourceDir()).parent
            / "Mod"
            / "CAM"
            / "Tools"
            / "Bit"
            / tool_file
        )
        tool = toolbit.attach_to_doc(doc=document, timeline_owner=job)
        controller = PathToolController.Create(
            name=name,
            tool=tool,
            toolNumber=max(int(value.ToolNumber) for value in job.Tools.Group) + 1,
            document=document,
            timelineOwner=job,
        )
        controller.Label = label
        controller.SpindleSpeed = spindle_speed
        controller.VertFeed = "750 mm/min"
        controller.HorizFeed = "750 mm/min"
        controller.VertRapid = "1200 mm/min"
        controller.HorizRapid = "1200 mm/min"
        job.Proxy.addToolController(controller)
        assert document.recompute(None, True, True) is not False
        PathUtil.finalizeTimelineResourceGraphExtension(
            job,
            extension,
            PathUtil.toolControllerResourceGraph(controller),
        )
        return controller

    drill_controller = _commit(
        document,
        "Create Drilling gate drill controller",
        lambda: create_controller(
            "5mm_Drill.fctb",
            "DrillController",
            "5 mm drill controller",
            1800,
        ),
    )
    tap_controller = _commit(
        document,
        "Create Drilling gate tap controller",
        lambda: create_controller(
            "M8x1.25_Tap.fctb",
            "TapController",
            "M8 x 1.25 tapping controller",
            600,
        ),
    )
    return model, job, drill_controller, tap_controller


def _hole_faces(model) -> tuple[str, str]:
    found = []
    for index, face in enumerate(model.Shape.Faces, start=1):
        if isinstance(face.Surface, Part.Cylinder):
            found.append((round(float(face.Surface.Center.x), 9), f"Face{index}"))
    assert len(found) == 2, found
    found.sort()
    return found[0][1], found[1][1]


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("drilling",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "feature_groups",
        "locations_mm",
        "enabled",
        "automatic",
        "manual",
        "drilling",
        "tapping",
        "standard",
        "peck",
        "dwell",
        "feed_retract",
        "depth_extension",
        "keep_tool_down",
        "collision_clearance_mm",
    ):
        assert field in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MANUFACTURE_OPERATION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _controller_target(state: dict, controller) -> dict:
    controller_state = next(
        value for value in state["tools"] if value["object_name"] == controller.Name
    )
    return _target(controller_state)


def _model_target(state: dict, model) -> dict:
    model_state = next(
        value for value in state["models"] if value["object_name"] == model.Name
    )
    return _target(model_state)


def _common(job, controller, *, label: str, targets: dict, process: dict) -> dict:
    state = job_state(job)
    return {
        "operation": "drilling",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "targets": targets,
        "process": process,
        "depths": {"start_depth_mm": 12.0, "final_depth_mm": 2.0},
        "heights": {"safe_height_mm": 14.0, "clearance_height_mm": 17.0},
        "linking": {
            "strategy": "clearance_height",
            "collision_clearance_mm": 0.4,
        },
        "coolant": "none",
    }


def _drill_arguments(model, job, controller, left_face: str, right_face: str) -> dict:
    state = job_state(job)
    arguments = _common(
        job,
        controller,
        label="Native peck Drilling",
        targets={
            "feature_groups": [
                {
                    "model": _model_target(state, model),
                    "features": [
                        {"subelement": left_face, "enabled": True},
                        {"subelement": right_face, "enabled": False},
                    ],
                }
            ],
            "locations_mm": [{"x_mm": 25.0, "y_mm": 8.0}],
            "sorting": "manual",
        },
        process={
            "kind": "drilling",
            "cycle": {"kind": "peck", "depth_mm": 2.0, "chip_break": True},
            "depth_extension": "drill_tip",
            "keep_tool_down": True,
        },
    )
    arguments["linking"] = {
        "strategy": "tool_diameter",
        "collision_clearance_mm": 0.4,
    }
    arguments["coolant"] = "mist"
    return arguments


def _tap_arguments(model, job, controller, right_face: str) -> dict:
    state = job_state(job)
    arguments = _common(
        job,
        controller,
        label="Native dwell Tapping",
        targets={
            "feature_groups": [
                {
                    "model": _model_target(state, model),
                    "features": [{"subelement": right_face, "enabled": True}],
                }
            ],
            "locations_mm": [],
            "sorting": "automatic",
        },
        process={
            "kind": "tapping",
            "cycle": {"kind": "dwell", "time_seconds": 0.4},
            "depth_extension": "none",
            "keep_tool_down": False,
        },
    )
    arguments["coolant"] = "flood"
    return arguments


def _feed_arguments(job, controller) -> dict:
    return _common(
        job,
        controller,
        label="Native location feed-retract Drilling",
        targets={
            "feature_groups": [],
            "locations_mm": [
                {"x_mm": 10.0, "y_mm": 8.0},
                {"x_mm": 40.0, "y_mm": 8.0},
            ],
            "sorting": "automatic",
        },
        process={
            "kind": "drilling",
            "cycle": {"kind": "feed_retract"},
            "depth_extension": "none",
            "keep_tool_down": False,
        },
    )


def _job_resource(job, model):
    matches = tuple(
        resource
        for resource in job.Model.Group
        if job.Proxy.baseObject(job, resource) is model
    )
    assert len(matches) == 1, matches
    return matches[0]


def _assert_operation(
    document,
    job,
    operation,
    *,
    label: str,
    expected_base: tuple,
    expected_locations: tuple,
    strategy: str,
    cycle_command: str,
    cycle_count: int,
    diagnostics_required: bool = True,
) -> None:
    assert operation in tuple(job.Operations.Group)
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController in tuple(job.Tools.Group)
    assert operation.ViewObject.Proxy.__class__.__name__ == "ViewProvider"
    if hasattr(operation.ViewObject.Proxy, "deleteOnReject"):
        assert operation.ViewObject.Proxy.deleteOnReject is False
    assert tuple(operation.Base) == expected_base
    assert tuple(
        tuple(round(float(getattr(point, axis)), 9) for axis in ("x", "y", "z"))
        for point in operation.Locations
    ) == expected_locations
    assert operation.Label == label
    assert operation.Strategy == strategy
    assert round(operation.StartDepth.getValueAs("mm"), 9) == 12.0
    assert round(operation.FinalDepth.getValueAs("mm"), 9) == 2.0
    assert round(operation.SafeHeight.getValueAs("mm"), 9) == 14.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 9) == 17.0
    assert operation.AddTipLength is False
    assert operation.UseEndPoint is False
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    commands = tuple(operation.Path.Commands)
    assert sum(command.Name == cycle_command for command in commands) == cycle_count
    if diagnostics_required:
        diagnostics = operation.Proxy.getGenerationDiagnostics(operation)
        assert diagnostics["status"] == "succeeded", diagnostics
        assert diagnostics["stage"] == "complete", diagnostics
        assert diagnostics["error"] is None, diagnostics


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-drilling-")
        save_path = Path(temporary.name) / "native-manufacture-drilling.FCStd"
        document = App.newDocument("NativeManufactureDrillingGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Drilling"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            "drilling",
            "ExactCamJobHoleTargetsControllerAndDrillingParameters",
            True,
            False,
        )

        model, job, drill_controller, tap_controller = _create_model_and_job(document)
        default_controller = job.Tools.Group[0]
        left_face, right_face = _hole_faces(model)
        model_resource = _job_resource(job, model)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-drilling-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, ribbon_controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=undo_ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                ribbon_controller
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
                MANUFACTURE_OPERATION_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-drilling-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, left_face)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        drill_arguments = _drill_arguments(
            model,
            job,
            drill_controller,
            left_face,
            right_face,
        )

        stale = json.loads(json.dumps(drill_arguments))
        stale["targets"]["feature_groups"][0]["model"][
            "expected_state_sha256"
        ] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert int(document.UndoCount) == undo_before

        duplicate = json.loads(json.dumps(drill_arguments))
        duplicate["targets"]["locations_mm"] = [{"x_mm": 14.0, "y_mm": 20.0}]
        duplicate_result = call(duplicate, succeeds=False)
        assert duplicate_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "same XY center" in duplicate_result["error"]
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert int(document.UndoCount) == undo_before

        invalid_tap = _tap_arguments(
            model,
            job,
            default_controller,
            right_face,
        )
        invalid_tap_result = call(invalid_tap, succeeds=False)
        assert invalid_tap_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert "positive Pitch" in invalid_tap_result["error"]
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        drill_result = call(drill_arguments)
        _events(12)
        drill_name = drill_result["drilling"]["object_name"]
        drill_operation = document.getObject(drill_name)
        assert drill_operation is not None
        _assert_operation(
            document,
            job,
            drill_operation,
            label="Native peck Drilling",
            expected_base=((model_resource, (left_face, right_face)),),
            expected_locations=((25.0, 8.0, 0.0),),
            strategy="Drilling",
            cycle_command="G73",
            cycle_count=2,
        )
        assert drill_operation.SortingMode == "Manual"
        assert drill_operation.PeckEnabled is True
        assert round(drill_operation.PeckDepth.getValueAs("mm"), 9) == 2.0
        assert drill_operation.ChipBreakEnabled is True
        assert drill_operation.DwellEnabled is False
        assert drill_operation.FeedRetractEnabled is False
        assert drill_operation.ExtraOffset == "Drill Tip"
        assert drill_operation.KeepToolDown is True
        assert drill_operation.CollisionAvoidanceStrategy == "Tool Diameter"
        assert drill_operation.CoolantMode == "Mist"
        assert tuple(drill_operation.Disabled) == (
            f"{model_resource.Name}.{right_face}",
        )
        assert drill_result["drilling"]["enabled_target_count"] == 2
        assert drill_result["drilling"]["cycle_command"] == "G73"
        assert drill_result["drilling"]["geometry"]["kind"] == "hole_targets"
        drill_state = operation_state(drill_operation)

        tap_arguments = _tap_arguments(
            model,
            job,
            tap_controller,
            right_face,
        )
        tap_result = call(tap_arguments)
        _events(12)
        tap_name = tap_result["drilling"]["object_name"]
        tap_operation = document.getObject(tap_name)
        assert tap_operation is not None
        _assert_operation(
            document,
            job,
            tap_operation,
            label="Native dwell Tapping",
            expected_base=((model_resource, (right_face,)),),
            expected_locations=(),
            strategy="Tapping",
            cycle_command="G84",
            cycle_count=1,
        )
        assert tap_operation.SortingMode == "Automatic"
        assert tap_operation.PeckEnabled is False
        assert tap_operation.DwellEnabled is True
        assert round(float(tap_operation.DwellTime), 9) == 0.4
        assert tap_operation.FeedRetractEnabled is False
        assert tap_operation.ExtraOffset == "None"
        assert tap_operation.CoolantMode == "Flood"
        tap_commands = tuple(command.Name for command in tap_operation.Path.Commands)
        assert "M8" in tap_commands and "M9" in tap_commands
        assert tap_result["drilling"]["cycle_command"] == "G84"
        tap_state = operation_state(tap_operation)

        feed_arguments = _feed_arguments(job, drill_controller)
        feed_result = call(feed_arguments)
        _events(12)
        feed_name = feed_result["drilling"]["object_name"]
        feed_operation = document.getObject(feed_name)
        assert feed_operation is not None
        _assert_operation(
            document,
            job,
            feed_operation,
            label="Native location feed-retract Drilling",
            expected_base=(),
            expected_locations=((10.0, 8.0, 0.0), (40.0, 8.0, 0.0)),
            strategy="Drilling",
            cycle_command="G85",
            cycle_count=2,
        )
        assert feed_operation.FeedRetractEnabled is True
        assert feed_result["drilling"]["geometry"] == {
            "kind": "hole_targets",
            "features": [],
            "locations_mm": [
                {"x_mm": 10.0, "y_mm": 8.0},
                {"x_mm": 40.0, "y_mm": 8.0},
            ],
        }
        assert feed_result["drilling"]["cutting_command_count"] == 2
        assert feed_result["job"]["operation_count"] == len(initial_operations) + 3
        assert int(document.UndoCount) == undo_before + 3
        assert state_store.current_revision(context.document_uid) == revision_before + 3
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        feed_state = operation_state(feed_operation)

        for expected_name in (feed_name, tap_name, drill_name):
            document.undo()
            _events(12)
            assert document.getObject(expected_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        for _index in range(3):
            document.redo()
            _events(12)
        model = document.getObject("DrillingGateModel")
        job = document.getObject("DrillingJob")
        model_resource = _job_resource(job, model)
        drill_operation = document.getObject(drill_name)
        tap_operation = document.getObject(tap_name)
        feed_operation = document.getObject(feed_name)
        assert all(
            value is not None
            for value in (model, job, drill_operation, tap_operation, feed_operation)
        )
        _assert_operation(
            document,
            job,
            drill_operation,
            label="Native peck Drilling",
            expected_base=((model_resource, (left_face, right_face)),),
            expected_locations=((25.0, 8.0, 0.0),),
            strategy="Drilling",
            cycle_command="G73",
            cycle_count=2,
        )
        _assert_operation(
            document,
            job,
            tap_operation,
            label="Native dwell Tapping",
            expected_base=((model_resource, (right_face,)),),
            expected_locations=(),
            strategy="Tapping",
            cycle_command="G84",
            cycle_count=1,
        )
        _assert_operation(
            document,
            job,
            feed_operation,
            label="Native location feed-retract Drilling",
            expected_base=(),
            expected_locations=((10.0, 8.0, 0.0), (40.0, 8.0, 0.0)),
            strategy="Drilling",
            cycle_command="G85",
            cycle_count=2,
        )
        assert operation_state(drill_operation)["state_sha256"] == drill_state["state_sha256"]
        assert operation_state(tap_operation)["state_sha256"] == tap_state["state_sha256"]
        assert operation_state(feed_operation)["state_sha256"] == feed_state["state_sha256"]

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("DrillingGateModel")
        job = document.getObject("DrillingJob")
        model_resource = _job_resource(job, model)
        drill_operation = document.getObject(drill_name)
        tap_operation = document.getObject(tap_name)
        feed_operation = document.getObject(feed_name)
        assert all(
            value is not None
            for value in (model, job, drill_operation, tap_operation, feed_operation)
        )
        _assert_operation(
            document,
            job,
            drill_operation,
            label="Native peck Drilling",
            expected_base=((model_resource, (left_face, right_face)),),
            expected_locations=((25.0, 8.0, 0.0),),
            strategy="Drilling",
            cycle_command="G73",
            cycle_count=2,
            diagnostics_required=False,
        )
        _assert_operation(
            document,
            job,
            tap_operation,
            label="Native dwell Tapping",
            expected_base=((model_resource, (right_face,)),),
            expected_locations=(),
            strategy="Tapping",
            cycle_command="G84",
            cycle_count=1,
            diagnostics_required=False,
        )
        _assert_operation(
            document,
            job,
            feed_operation,
            label="Native location feed-retract Drilling",
            expected_base=(),
            expected_locations=((10.0, 8.0, 0.0), (40.0, 8.0, 0.0)),
            strategy="Drilling",
            cycle_command="G85",
            cycle_count=2,
            diagnostics_required=False,
        )
        assert operation_state(drill_operation)["state_sha256"] == drill_state["state_sha256"]
        assert operation_state(tap_operation)["state_sha256"] == tap_state["state_sha256"]
        assert operation_state(feed_operation)["state_sha256"] == feed_state["state_sha256"]

        print(
            "VIBECAD_NATIVE_MANUFACTURE_DRILLING_GUI_OK "
            "exact_targets=true feature_enablement=true locations=true drilling=true "
            "tapping=true cycles=true parameters=true linking=true coolant=true "
            "toolpath=true history=true rollback=true undo=true redo=true reopen=true",
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
