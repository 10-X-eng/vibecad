# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Thread Milling."""

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
from VibeCADNativeManufactureInspectSchema import (
    MANUFACTURE_INSPECT_CAPABILITY_NAME,
)
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeManufactureOperationRuntime import NativeManufactureOperationRuntime
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
        model = document.addObject("Part::Feature", "ThreadMillingGateModel")
        model.Label = "Thread Milling gate model"
        plate = Part.makeBox(52.0, 40.0, 8.0)
        bore = Part.makeCylinder(4.0, 8.0, App.Vector(14.0, 20.0, 0.0))
        boss = Part.makeCylinder(6.0, 6.0, App.Vector(38.0, 20.0, 8.0))
        model.Shape = plate.cut(bore).fuse(boss)
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Thread Milling gate model", create_model)

    def create_job():
        job = PathJob.Create("ThreadMillingJob", [model], templateFile=None)
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

    job = _commit(document, "Create Thread Milling gate Job", create_job)

    def create_controller():
        extension = PathUtil.stageTimelineResourceGraphExtension(job)
        toolbit = ToolBit.from_file(
            Path(App.getResourceDir()).parent
            / "Mod"
            / "CAM"
            / "Tools"
            / "Bit"
            / "5mm-thread-cutter.fctb"
        )
        tool = toolbit.attach_to_doc(doc=document, timeline_owner=job)
        controller = PathToolController.Create(
            name="ThreadMillController",
            tool=tool,
            toolNumber=max(int(value.ToolNumber) for value in job.Tools.Group) + 1,
            document=document,
            timelineOwner=job,
        )
        controller.Label = "5 mm thread mill controller"
        controller.SpindleSpeed = 2400
        controller.VertFeed = "500 mm/min"
        controller.HorizFeed = "650 mm/min"
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

    controller = _commit(
        document,
        "Create Thread Milling gate controller",
        create_controller,
    )
    return model, job, controller


def _thread_features(model) -> tuple[str, str]:
    internal = None
    external = None
    for index, face in enumerate(model.Shape.Faces, start=1):
        name = f"Face{index}"
        if (
            isinstance(face.Surface, Part.Cylinder)
            and round(float(face.Surface.Radius), 7) == 4.0
        ):
            internal = name
        if (
            isinstance(face.Surface, Part.Plane)
            and round(float(face.BoundBox.ZMin), 7) == 14.0
            and len(face.Edges) == 1
            and isinstance(face.Edges[0].Curve, Part.Circle)
            and round(float(face.Edges[0].Curve.Radius), 7) == 6.0
        ):
            external = name
    assert internal is not None, "internal thread feature not found"
    assert external is not None, "external thread feature not found"
    return internal, external


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
    operation_definition = registry.definition(
        MANUFACTURE_OPERATION_CAPABILITY_NAME
    )
    inspect_definition = registry.definition(MANUFACTURE_INSPECT_CAPABILITY_NAME)
    assert operation_definition is not None and inspect_definition is not None
    operation_schema = operation_definition.provider_schema(("thread_milling",))
    inspect_schema = inspect_definition.provider_schema(("read_thread_catalog",))
    encoded = json.dumps(operation_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "feature_groups",
        "enabled",
        "standard",
        "custom",
        "series",
        "designation",
        "fit_percent",
        "pitch_mm",
        "threads_per_inch",
        "orientation",
        "direction",
        "passes",
        "lead_in_out",
        "collision_clearance_mm",
    ):
        assert field in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                MANUFACTURE_OPERATION_CAPABILITY_NAME,
                MANUFACTURE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(operation_schema, inspect_schema),
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


def _arguments(
    model,
    job,
    controller,
    *,
    label: str,
    features: list[dict],
    sorting: str,
    definition: dict,
    orientation: str,
    direction: str,
    passes: int,
    lead_in_out: bool,
    coolant: str,
) -> dict:
    state = job_state(job)
    return {
        "operation": "thread_milling",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "targets": {
            "feature_groups": [
                {
                    "model": _model_target(state, model),
                    "features": features,
                }
            ],
            "sorting": sorting,
        },
        "thread": {
            "definition": definition,
            "orientation": orientation,
            "direction": direction,
            "passes": passes,
            "lead_in_out": lead_in_out,
        },
        "depths": {"start_depth_mm": 8.0, "final_depth_mm": 2.0},
        "heights": {"safe_height_mm": 10.0, "clearance_height_mm": 13.0},
        "linking": {
            "strategy": "tool_diameter",
            "collision_clearance_mm": 0.5,
        },
        "coolant": coolant,
    }


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
    thread_type: str,
    designation: str,
    major_mm: float,
    minor_mm: float,
    pitch_mm: float,
    tpi: int,
    orientation: str,
    direction: str,
    passes: int,
    lead_in_out: bool,
    coolant: str,
    disabled: tuple = (),
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
    assert operation.Label == label
    assert operation.ThreadType == thread_type
    assert operation.ThreadName == designation
    assert round(operation.MajorDiameter.getValueAs("mm"), 7) == round(major_mm, 7)
    assert round(operation.MinorDiameter.getValueAs("mm"), 7) == round(minor_mm, 7)
    assert round(operation.Pitch.getValueAs("mm"), 7) == round(pitch_mm, 7)
    assert operation.TPI == tpi
    assert operation.ThreadOrientation == orientation
    assert operation.Direction == direction
    assert operation.Passes == passes
    assert operation.LeadInOut is lead_in_out
    assert operation.CoolantMode == coolant
    assert operation.CollisionAvoidanceStrategy == "Tool Diameter"
    assert tuple(operation.Disabled) == disabled
    assert operation.UseEndPoint is False
    assert operation.ClearanceOp is None
    assert round(operation.StartDepth.getValueAs("mm"), 7) == 8.0
    assert round(operation.FinalDepth.getValueAs("mm"), 7) == 2.0
    assert round(operation.SafeHeight.getValueAs("mm"), 7) == 10.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 7) == 13.0
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    assert any(command.Name in {"G2", "G3"} for command in operation.Path.Commands)
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
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-cam-thread-milling-"
        )
        save_path = Path(temporary.name) / "native-manufacture-thread-milling.FCStd"
        document = App.newDocument("NativeManufactureThreadMillingGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_ThreadMilling"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.thread_mill",
            "thread_milling",
            "ExactCamJobHoleFeaturesControllerAndThreadDefinition",
            True,
            False,
        )

        model, job, controller = _create_model_and_job(document)
        default_controller = job.Tools.Group[0]
        internal_face, external_face = _thread_features(model)
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
        undo_ledger.begin_run("native-manufacture-thread-milling-gui")

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
        runtimes = build_native_runtime_bindings(context, turn.tool_names)
        runtimes[MANUFACTURE_OPERATION_CAPABILITY_NAME] = (
            NativeManufactureOperationRuntime(context)
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=runtimes,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_index = 0

        def call(tool: str, payload: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-thread-milling-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, internal_face)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)

        catalog = call(
            MANUFACTURE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "read_thread_catalog",
                "series": "metric_internal_6h",
                "query": "M10 x 1.5",
                "offset": 0,
                "page_size": 8,
            },
        )["thread_catalog"]
        assert catalog["count"] == 1, catalog
        assert catalog["items"][0]["designation"] == "M10 x 1.5"
        assert catalog["items"][0]["pitch_mm"] == 1.5
        assert state_store.current_revision(context.document_uid) == revision_before
        assert int(document.UndoCount) == undo_before

        standard_arguments = _arguments(
            model,
            job,
            controller,
            label="Native M10 internal thread",
            features=[
                {"subelement": internal_face, "enabled": True},
                {"subelement": external_face, "enabled": False},
            ],
            sorting="manual",
            definition={
                "kind": "standard",
                "series": "metric_internal_6h",
                "designation": "M10 x 1.5",
                "fit_percent": 50,
            },
            orientation="right_hand",
            direction="climb",
            passes=2,
            lead_in_out=True,
            coolant="mist",
        )

        stale = json.loads(json.dumps(standard_arguments))
        stale["targets"]["feature_groups"][0]["model"][
            "expected_state_sha256"
        ] = "0" * 64
        stale_result = call(
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            stale,
            succeeds=False,
        )
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        bad_designation = json.loads(json.dumps(standard_arguments))
        bad_designation["thread"]["definition"]["designation"] = "M10 nonsense"
        designation_result = call(
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            bad_designation,
            succeeds=False,
        )
        assert designation_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert designation_result["repair"]["use"].endswith("read_thread_catalog")

        bad_tool = json.loads(json.dumps(standard_arguments))
        bad_tool["tool_controller"] = _controller_target(
            job_state(job),
            default_controller,
        )
        tool_result = call(
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            bad_tool,
            succeeds=False,
        )
        assert tool_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert "Crest" in tool_result["error"]

        missing_stock = json.loads(json.dumps(standard_arguments))
        missing_stock["thread"]["definition"] = {
            "kind": "custom",
            "side": "internal",
            "major_diameter_mm": 7.0,
            "minor_diameter_mm": 6.5,
            "pitch": {"kind": "pitch_mm", "value": 1.0},
        }
        stock_result = call(
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            missing_stock,
            succeeds=False,
        )
        assert stock_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert "no material" in stock_result["error"]
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        standard_result = call(
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            standard_arguments,
        )
        _events(12)
        standard_name = standard_result["thread_milling"]["object_name"]
        standard_operation = document.getObject(standard_name)
        assert standard_operation is not None
        _assert_operation(
            document,
            job,
            standard_operation,
            label="Native M10 internal thread",
            expected_base=((model_resource, (internal_face, external_face)),),
            thread_type="MetricInternal6H",
            designation="M10 x 1.5",
            major_mm=10.1985,
            minor_mm=8.526,
            pitch_mm=1.5,
            tpi=0,
            orientation="RightHand",
            direction="Climb",
            passes=2,
            lead_in_out=True,
            coolant="Mist",
            disabled=(f"{model_resource.Name}.{external_face}",),
        )
        assert standard_result["thread_milling"]["thread"]["kind"] == "standard"
        assert standard_result["thread_milling"]["targets"]["enabled_count"] == 1
        assert standard_result["thread_milling"]["pass_count"] == 2
        standard_state = operation_state(standard_operation)

        custom_arguments = _arguments(
            model,
            job,
            controller,
            label="Native custom external thread",
            features=[{"subelement": external_face, "enabled": True}],
            sorting="automatic",
            definition={
                "kind": "custom",
                "side": "external",
                "major_diameter_mm": 12.0,
                "minor_diameter_mm": 10.5,
                "pitch": {"kind": "threads_per_inch", "value": 20},
            },
            orientation="left_hand",
            direction="conventional",
            passes=3,
            lead_in_out=False,
            coolant="flood",
        )
        custom_result = call(
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            custom_arguments,
        )
        _events(12)
        custom_name = custom_result["thread_milling"]["object_name"]
        custom_operation = document.getObject(custom_name)
        assert custom_operation is not None
        _assert_operation(
            document,
            job,
            custom_operation,
            label="Native custom external thread",
            expected_base=((model_resource, (external_face,)),),
            thread_type="CustomExternal",
            designation="",
            major_mm=12.0,
            minor_mm=10.5,
            pitch_mm=0.0,
            tpi=20,
            orientation="LeftHand",
            direction="Conventional",
            passes=3,
            lead_in_out=False,
            coolant="Flood",
        )
        assert custom_result["thread_milling"]["thread"]["kind"] == "custom"
        assert custom_result["thread_milling"]["thread"]["threads_per_inch"] == 20
        assert custom_result["job"]["operation_count"] == len(initial_operations) + 2
        assert int(document.UndoCount) == undo_before + 2
        assert state_store.current_revision(context.document_uid) == revision_before + 2
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        custom_state = operation_state(custom_operation)

        document.undo()
        _events(12)
        assert document.getObject(custom_name) is None
        document.undo()
        _events(12)
        assert document.getObject(standard_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        document.redo()
        _events(12)
        model = document.getObject("ThreadMillingGateModel")
        job = document.getObject("ThreadMillingJob")
        model_resource = _job_resource(job, model)
        standard_operation = document.getObject(standard_name)
        custom_operation = document.getObject(custom_name)
        assert all(value is not None for value in (standard_operation, custom_operation))
        _assert_operation(
            document,
            job,
            standard_operation,
            label="Native M10 internal thread",
            expected_base=((model_resource, (internal_face, external_face)),),
            thread_type="MetricInternal6H",
            designation="M10 x 1.5",
            major_mm=10.1985,
            minor_mm=8.526,
            pitch_mm=1.5,
            tpi=0,
            orientation="RightHand",
            direction="Climb",
            passes=2,
            lead_in_out=True,
            coolant="Mist",
            disabled=(f"{model_resource.Name}.{external_face}",),
        )
        _assert_operation(
            document,
            job,
            custom_operation,
            label="Native custom external thread",
            expected_base=((model_resource, (external_face,)),),
            thread_type="CustomExternal",
            designation="",
            major_mm=12.0,
            minor_mm=10.5,
            pitch_mm=0.0,
            tpi=20,
            orientation="LeftHand",
            direction="Conventional",
            passes=3,
            lead_in_out=False,
            coolant="Flood",
        )
        assert operation_state(standard_operation)["state_sha256"] == standard_state[
            "state_sha256"
        ]
        assert operation_state(custom_operation)["state_sha256"] == custom_state[
            "state_sha256"
        ]

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("ThreadMillingGateModel")
        job = document.getObject("ThreadMillingJob")
        model_resource = _job_resource(job, model)
        standard_operation = document.getObject(standard_name)
        custom_operation = document.getObject(custom_name)
        assert all(value is not None for value in (standard_operation, custom_operation))
        _assert_operation(
            document,
            job,
            standard_operation,
            label="Native M10 internal thread",
            expected_base=((model_resource, (internal_face, external_face)),),
            thread_type="MetricInternal6H",
            designation="M10 x 1.5",
            major_mm=10.1985,
            minor_mm=8.526,
            pitch_mm=1.5,
            tpi=0,
            orientation="RightHand",
            direction="Climb",
            passes=2,
            lead_in_out=True,
            coolant="Mist",
            disabled=(f"{model_resource.Name}.{external_face}",),
            diagnostics_required=False,
        )
        _assert_operation(
            document,
            job,
            custom_operation,
            label="Native custom external thread",
            expected_base=((model_resource, (external_face,)),),
            thread_type="CustomExternal",
            designation="",
            major_mm=12.0,
            minor_mm=10.5,
            pitch_mm=0.0,
            tpi=20,
            orientation="LeftHand",
            direction="Conventional",
            passes=3,
            lead_in_out=False,
            coolant="Flood",
            diagnostics_required=False,
        )
        assert operation_state(standard_operation)["state_sha256"] == standard_state[
            "state_sha256"
        ]
        assert operation_state(custom_operation)["state_sha256"] == custom_state[
            "state_sha256"
        ]

        print(
            "VIBECAD_NATIVE_MANUFACTURE_THREAD_MILLING_GUI_OK "
            "catalog=true exact_targets=true feature_enablement=true standard=true "
            "custom=true parameters=true linking=true coolant=true toolpath=true "
            "history=true rollback=true undo=true redo=true reopen=true",
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
