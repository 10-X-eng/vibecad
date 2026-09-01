# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for authorized Z Correction and replacement lifecycle."""

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

import Path.Dressup.Gui.ZCorrect as ZCorrectGui
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)

from VibeCADNativeManufactureState import (
    job_state,
    operation_reference_state,
    persistent_resource_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["z_correct_dressup"]


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
    return controller, surface


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(job, base, *, label="Native Probe Z Correction") -> dict:
    return {
        "operation": "z_correct_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "arc_maximum_deflection_mm": 0.05,
        "line_maximum_segment_length_mm": 2.0,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("z_correct_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    assert "path" not in schema["parameters"]["oneOf"][0]["properties"]
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    assert set(variant["required"]) == {
        "label",
        "job",
        "base_operation",
        "arc_maximum_deflection_mm",
        "line_maximum_segment_length_mm",
    }
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _custom(job, controller):
    operation = PathCustom.Create("ZCorrectGateBase", parentJob=job)
    operation.Label = "Probe correction base"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = [
        "G90",
        "G0 X0 Y0 Z8",
        "G1 Z-2 F80",
        "G1 X30 Y0 Z-2 F120",
        "G2 X30 Y20 Z-2 I0 J10 F120",
        "G1 X0 Y20 Z-2 F120",
        "G1 X0 Y0 Z-2 F120",
        "G0 Z8",
    ]
    return operation


def _fixture(document):
    model = document.addObject("Part::Feature", "ZCorrectGateModel")
    model.Shape = Part.makeBox(30.0, 20.0, 4.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    for property_name, value in (
        ("HorizFeed", 120.0),
        ("VertFeed", 80.0),
        ("HorizRapid", 300.0),
        ("VertRapid", 250.0),
    ):
        controller.setExpression(property_name, None)
        setattr(controller, property_name, value)
    operation = _custom(job, controller)
    assert document.recompute(None, True, True) is not False
    center = App.Vector(2.0, -3.0, 7.0)
    job_path = job.Path
    job_path.Center = center
    job.Path = job_path
    path = operation.Path
    path.Center = center
    operation.Path = path
    operation.ViewObject.Visibility = True
    assert operation.isValid() and operation.Path.Size
    return model, job, operation


def _probe_text(*, x_values=(-5.0, 20.0, 45.0), z_scale=1.0) -> str:
    lines = ["# X Y Z A B C U V W"]
    for y in (-5.0, 10.0, 25.0):
        for x in x_values:
            z = z_scale * (0.1 + 0.002 * x + 0.003 * y)
            lines.append(f"{x} {y} {z} 0 0 0 0 0 0")
    return "\n".join(lines) + "\n"


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


def _wait(manager, job_id: str):
    for _index in range(2000):
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            return snapshot
        QtCore.QThread.msleep(2)
    raise AssertionError("the Z Correction background job did not finish")


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-zcorrect-")
        root = Path(temporary.name)
        probe_path = root / "surface.probe"
        invalid_path = root / "incomplete.probe"
        outside_path = root / "outside.probe"
        zero_path = root / "zero.probe"
        save_path = root / "native-manufacture-zcorrect.FCStd"
        probe_path.write_text(_probe_text(), encoding="utf-8")
        invalid_path.write_text("0 0 0.1\n10 0 0.2\n0 10 0.3\n", encoding="utf-8")
        outside_path.write_text(
            _probe_text(x_values=(0.0, 5.0, 10.0)),
            encoding="utf-8",
        )
        zero_path.write_text(_probe_text(z_scale=0.0), encoding="utf-8")

        document = App.newDocument("NativeManufactureZCorrectGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupZCorrect"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
            plan.background_required,
        ) == (
            CAPABILITY_NAME,
            "z_correct_dressup",
            "ExactCamJobOperationAndHumanAuthorizedProbeMap",
            True,
            False,
            True,
        )

        model, job, base = _fixture(document)
        _events(12)
        source_before = persistent_resource_state(base)
        document.clearUndos()
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-zcorrect-gui")
        selected = {"path": probe_path, "mutate_after_authorization": False}
        authorization_requests = []

        def authorize(request):
            authorization_requests.append(request)
            authorization = authorize_native_input_path(request, selected["path"])
            if selected["mutate_after_authorization"]:
                selected["path"].write_text(
                    selected["path"].read_text(encoding="utf-8") + "# changed\n",
                    encoding="utf-8",
                )
            return authorization

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller_widget)

        manager = service.native_background_manager()
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
            authorize_input=authorize,
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

        def call(payload: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-zcorrect-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def call_and_wait(payload: dict):
            started = call(payload)
            snapshot = _wait(manager, started["job"]["job_id"])
            return started, snapshot

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        stale = _arguments(job, base)
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        assert call(stale, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )
        assert not authorization_requests

        invalid_deflection = _arguments(job, base)
        invalid_deflection["arc_maximum_deflection_mm"] = 0.0
        assert call(invalid_deflection, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )
        assert not authorization_requests

        selected["path"] = invalid_path
        _started, invalid = call_and_wait(_arguments(job, base))
        assert invalid.phase == "failed"
        assert invalid.error["error_code"] == "NATIVE_MANUFACTURE_PROBE_MAP_INVALID"
        assert tuple(job.Operations.Group) == (base,)

        selected["path"] = outside_path
        _started, outside = call_and_wait(_arguments(job, base))
        assert outside.phase == "failed"
        assert outside.error["error_code"] == "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        assert "outside probe bounds" in outside.error["repair"]["native_error"]

        selected["path"] = zero_path
        _started, no_effect = call_and_wait(_arguments(job, base))
        assert no_effect.phase == "failed"
        assert no_effect.error["error_code"] == "NATIVE_MANUFACTURE_NO_EFFECT"

        selected["path"] = probe_path
        with patch("Path.Dressup.ZCorrect.MAX_Z_CORRECT_OUTPUT_COMMANDS", 5):
            _started, workload = call_and_wait(_arguments(job, base))
        assert workload.phase == "failed"
        assert workload.error["error_code"] == (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
        )

        selected["mutate_after_authorization"] = True
        _started, drift = call_and_wait(_arguments(job, base))
        assert drift.phase == "failed"
        assert drift.error["error_code"] == "NATIVE_INPUT_AUTHORIZATION_FAILED"
        selected["mutate_after_authorization"] = False
        probe_path.write_text(_probe_text(), encoding="utf-8")

        before_objects = tuple(document.Objects)
        before_group = tuple(job.Operations.Group)
        before_visibility = _visibility(document)
        before_timeline = (
            tuple(document.VibeCADTimeline.Operations),
            tuple(document.VibeCADTimeline.VisibilityAtEnd),
            tuple(document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )
        before_undo = int(document.UndoCount)
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_z_correct_dressup",
            side_effect=RuntimeError("forced Z Correction postcondition failure"),
        ):
            _started, failed = call_and_wait(_arguments(job, base))
        assert failed.phase == "failed"
        assert failed.error["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == before_objects
        assert tuple(job.Operations.Group) == before_group
        assert _visibility(document) == before_visibility
        assert before_timeline == (
            tuple(document.VibeCADTimeline.Operations),
            tuple(document.VibeCADTimeline.VisibilityAtEnd),
            tuple(document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )
        assert int(document.UndoCount) == before_undo

        started, completed = call_and_wait(
            _arguments(job, base, label="Native Exact Probe Correction")
        )
        assert started["job"]["phase"] in {"queued", "preparing"}
        assert completed.phase == "completed", completed.error
        result = completed.result
        _events(16)
        output = document.getObject(result["object_name"])
        output_name = str(output.Name)
        base_name = str(base.Name)
        job_name = str(job.Name)
        assert isinstance(output.Proxy, ZCorrectGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, ZCorrectGui.ViewProviderDressup)
        assert output.Base is base
        assert not output.probefile
        assert output.ProbeDataSHA256 == result["input"]["sha256"]
        assert output.ProbePointCount == 9
        assert output.ProbeGridXCount == 3 and output.ProbeGridYCount == 3
        assert list(output.VibeCADExternalInputs) == [probe_path.name]
        assert result["linearized_arc_count"] == 1
        assert result["generated_linear_move_count"] > 10
        assert result["path_center_mm"] == [2.0, -3.0, 7.0]
        assert len(result["receipt"]["created"]) == 1
        assert len(result["receipt"]["replaced"]) == 1
        assert result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert int(document.UndoCount) == before_undo + 1
        assert not base.ViewObject.Visibility and output.ViewObject.Visibility
        source_after = persistent_resource_state(base)
        assert {
            key: source_after.get(key)
            for key in ("path_sha256", "command_count", "active")
        } == {
            key: source_before.get(key)
            for key in ("path_sha256", "command_count", "active")
        }

        generated_state = persistent_resource_state(output)
        probe_path.write_text("not a probe map\n", encoding="utf-8")
        output.touch()
        assert document.recompute((output,), True, True) is not False
        assert persistent_resource_state(output) == generated_state

        document.undo()
        _events(16)
        assert document.getObject(output_name) is None
        assert document.getObject(base_name).ViewObject.Visibility
        document.redo()
        _events(16)
        base = document.getObject(base_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert output.Base is base and output in job.Operations.Group

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(20)
        document = App.openDocument(str(save_path))
        _events(24)
        base = document.getObject(base_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert isinstance(output.Proxy, ZCorrectGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, ZCorrectGui.ViewProviderDressup)
        assert output.Base is base and output in job.Operations.Group
        assert tuple(output.VibeCADTimelineReplacedInputs) == (base,)
        assert not base.ViewObject.Visibility and output.ViewObject.Visibility
        assert not output.probefile
        assert list(output.VibeCADExternalInputs) == [probe_path.name]
        assert tuple(output.Path.Center) == tuple(job.Path.Center)
        output.touch()
        assert document.recompute((output,), True, True) is not False
        assert persistent_resource_state(output) == generated_state

        print(
            "VIBECAD_NATIVE_MANUFACTURE_Z_CORRECT_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true closed_schema=true "
            "human_authorization=true no_provider_path=true malformed_grid=true "
            "out_of_bounds=true no_effect=true workload_guard=true file_drift=true "
            "background=true "
            "rollback=true embedded=true hash_pinned=true source_preserved=true "
            "arc_linearization=true rotary_center=true replacement=true history=true "
            "receipt=true selection=true visibility=true undo=true redo=true reopen=true"
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
