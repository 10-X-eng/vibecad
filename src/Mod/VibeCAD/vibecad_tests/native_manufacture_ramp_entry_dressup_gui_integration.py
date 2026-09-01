# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for all CAM Ramp Entry methods and replacement lifecycle."""

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

import Path.Dressup.Gui.RampEntry as RampEntryGui
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureDressupRampEntry import (
    RampEntryDressupSpec,
    preflight_ramp_entry_dressup,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)

from VibeCADNativeManufactureState import (
    copy_configuration_state,
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["ramp_entry_dressup"]


_METHODS = (
    "forward_then_return",
    "reverse_into_cut",
    "zigzag",
    "contour_helix",
)


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


def _arguments(
    job,
    base,
    *,
    method="forward_then_return",
    angle=60.0,
    activation=None,
    label="Native CAM Ramp Entry",
) -> dict:
    return {
        "operation": "ramp_entry_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "method": method,
        "angle_from_vertical_degrees": angle,
        "activation": activation or {"kind": "all_plunges"},
    }


def _spec(payload: dict) -> RampEntryDressupSpec:
    return RampEntryDressupSpec(
        label=payload["label"],
        job=payload["job"],
        base_operation=payload["base_operation"],
        method=payload["method"],
        angle_from_vertical_degrees=payload["angle_from_vertical_degrees"],
        activation=payload["activation"],
    )


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("ramp_entry_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    assert variant["properties"]["method"]["enum"] == list(_METHODS)
    assert variant["properties"]["angle_from_vertical_degrees"] == {
        "type": "number",
        "minimum": 0.1,
        "maximum": 89.9,
        "description": (
            "Ramp angle measured from vertical: small values are steep and values near "
            "90 degrees are shallow."
        ),
    }
    activations = variant["properties"]["activation"]["oneOf"]
    assert {
        item["properties"]["kind"]["const"] for item in activations
    } == {"all_plunges", "below_absolute_z"}
    assert all(item["additionalProperties"] is False for item in activations)
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


def _custom(job, controller, name, gcode):
    operation = PathCustom.Create(name, parentJob=job)
    operation.Label = f"{name} gate source"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = list(gcode)
    return operation


def _create_fixture(document):
    model = document.addObject("Part::Feature", "RampEntryGateModel")
    model.Shape = Part.makeBox(20.0, 20.0, 4.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    for property_name, value in (
        ("HorizFeed", 120.0),
        ("VertFeed", 80.0),
        ("RampFeed", 70.0),
        ("HorizRapid", 300.0),
        ("VertRapid", 250.0),
    ):
        controller.setExpression(property_name, None)
        setattr(controller, property_name, value)
    source = _custom(
        job,
        controller,
        "RampEntrySource",
        (
            "G0 X0 Y0 Z8",
            "G0 Z3",
            "G1 Z-1 F80",
            "G1 X10 Y0 Z-1 F120",
            "G2 X10 Y10 I0 J5 F120",
            "G1 X0 Y10 Z-1 F120",
            "G1 X0 Y0 Z-1 F120",
            "G0 Z8",
        ),
    )
    no_ramp = _custom(
        job,
        controller,
        "RampEntryNoPlunge",
        ("G0 X0 Y0 Z3", "G1 X10 Y0 Z-1 F100", "G0 Z8"),
    )
    assert document.recompute(None, True, True) is not False
    center = App.Vector(2.0, -3.0, 7.0)
    job_path = job.Path
    job_path.Center = center
    job.Path = job_path
    for operation in (source, no_ramp):
        operation_path = operation.Path
        operation_path.Center = center
        operation.Path = operation_path
        operation.ViewObject.Visibility = True
    assert source.isValid() and source.Path.Size
    return model, job, controller, source, no_ramp


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


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-ramp-entry-")
        save_path = Path(temporary.name) / "native-manufacture-ramp-entry.FCStd"
        document = App.newDocument("NativeManufactureRampEntryGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupRampEntry"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "ramp_entry_dressup",
            "ExactCamJobOperationAndRampEntryDefinition",
            True,
            False,
        )

        model, job, controller, source, no_ramp = _create_fixture(document)
        ramp_feed = float(controller.RampFeed.Value)
        controller.RampFeed = 0.0
        document.recompute()
        zero_rate_payload = _arguments(job, source)
        try:
            preflight_ramp_entry_dressup(document, _spec(zero_rate_payload))
        except NativeManufactureError as exc:
            assert exc.error_code == "NATIVE_MANUFACTURE_MACHINE_PARAMETERS_UNAVAILABLE"
            assert "ramp_feed" in exc.repair["required_positive_properties"]
        else:
            raise AssertionError("zero RampFeed was accepted")
        controller.RampFeed = ramp_feed
        document.recompute()
        document.clearUndos()
        source_configuration = copy_configuration_state(source, {})
        source_path_sha256 = persistent_resource_state(source)["path_sha256"]
        initial_objects = tuple(document.Objects)
        initial_group = tuple(job.Operations.Group)
        initial_visibility = _visibility(document)
        initial_timeline = (
            tuple(document.VibeCADTimeline.Operations),
            tuple(document.VibeCADTimeline.VisibilityAtEnd),
            tuple(document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-ramp-entry-gui")

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
                CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-ramp-entry-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        stale = _arguments(job, source)
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        invalid_angle = _arguments(job, source, angle=90.0)
        invalid_result = call(invalid_angle, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        no_effect_result = call(
            _arguments(job, no_ramp),
            succeeds=False,
        )
        assert no_effect_result["error_code"] == "NATIVE_MANUFACTURE_TOOLPATH_INVALID"

        with patch("Path.Dressup.RampEntry.MAX_RAMP_ENTRY_INPUT_COMMANDS", 3):
            workload = call(_arguments(job, source), succeeds=False)
        assert workload["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"

        hashes = set()
        for method in _METHODS:
            revision_before = state_store.current_revision(document.Uid)
            payload = _arguments(job, source, method=method)
            prepared = preflight_ramp_entry_dressup(document, _spec(payload))
            assert prepared.ramped_plunge_count == 1
            assert prepared.ramp_motion_count > 0
            assert prepared.expected_command_count > 0
            assert prepared.expected_cutting_count > 0
            assert state_store.current_revision(document.Uid) == revision_before
            hashes.add(prepared.expected_path_sha256)
        assert len(hashes) == 4

        depth_payload = _arguments(
            job,
            source,
            method="zigzag",
            angle=55.0,
            activation={"kind": "below_absolute_z", "z_mm": 1.0},
        )
        depth_prepared = preflight_ramp_entry_dressup(
            document,
            _spec(depth_payload),
        )
        assert depth_prepared.start_depth_split_count == 1
        assert depth_prepared.ramped_plunge_count == 1
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group
        assert _visibility(document) == initial_visibility
        assert copy_configuration_state(source, {}) == source_configuration
        assert persistent_resource_state(source)["path_sha256"] == source_path_sha256

        payload = _arguments(
            job,
            source,
            method="zigzag",
            angle=55.0,
            activation={"kind": "below_absolute_z", "z_mm": 1.0},
            label="Native Ramp Entry Lifecycle",
        )
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_ramp_entry_dressup",
            side_effect=RuntimeError("forced Ramp Entry postcondition failure"),
        ):
            failed = call(payload, succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group
        assert _visibility(document) == initial_visibility
        assert initial_timeline == (
            tuple(document.VibeCADTimeline.Operations),
            tuple(document.VibeCADTimeline.VisibilityAtEnd),
            tuple(document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )
        assert int(document.UndoCount) == 0

        result = call(payload)
        _events(16)
        output = document.getObject(result["object_name"])
        output_name = str(output.Name)
        source_name = str(source.Name)
        job_name = str(job.Name)
        assert isinstance(output.Proxy, RampEntryGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, RampEntryGui.ViewProviderDressup)
        assert output.Base is source
        assert str(output.Method) == "RampMethod3"
        assert abs(float(output.Angle.Value) - 55.0) < 1.0e-9
        assert output.UseStartDepth is True
        assert abs(float(output.DressupStartDepth.Value) - 1.0) < 1.0e-9
        assert result["method"] == "zigzag"
        assert result["activation"] == {"kind": "below_absolute_z", "z_mm": 1.0}
        assert result["ramped_plunge_count"] == 1
        assert result["ramp_motion_count"] > 0
        assert result["start_depth_split_count"] == 1
        assert result["path_center_mm"] == [2.0, -3.0, 7.0]
        assert len(result["receipt"]["created"]) == 1
        assert len(result["receipt"]["replaced"]) == 1
        assert result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert int(document.UndoCount) == 1
        assert copy_configuration_state(source, {}) == source_configuration
        assert persistent_resource_state(source)["path_sha256"] == source_path_sha256
        assert not source.ViewObject.Visibility
        assert output.ViewObject.Visibility

        document.undo()
        _events(16)
        assert document.getObject(output_name) is None
        assert document.getObject(source_name).ViewObject.Visibility
        document.redo()
        _events(16)
        source = document.getObject(source_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert output.Base is source and output in job.Operations.Group

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(20)
        document = App.openDocument(str(save_path))
        _events(24)
        source = document.getObject(source_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert isinstance(output.Proxy, RampEntryGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, RampEntryGui.ViewProviderDressup)
        assert output.Base is source and output in job.Operations.Group
        assert tuple(output.VibeCADTimelineReplacedInputs) == (source,)
        assert not source.ViewObject.Visibility and output.ViewObject.Visibility
        assert tuple(output.Path.Center) == tuple(job.Path.Center)
        assert output.Proxy.lastGenerationStats["ramped_plunge_count"] == 1

        print(
            "VIBECAD_NATIVE_MANUFACTURE_RAMP_ENTRY_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true closed_schema=true "
            "invalid_angle=true no_effect=true machine_rates=true workload_guard=true "
            "rollback=true four_methods=true start_depth=true ramp_motion=true "
            "rotary_center=true source_preserved=true replacement=true history=true "
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
