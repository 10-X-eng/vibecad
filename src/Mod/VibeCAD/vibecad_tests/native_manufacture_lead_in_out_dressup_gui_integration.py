# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for every Lead In/Out style and replacement lifecycle."""

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

import Path.Dressup.Gui.LeadInOut as LeadInOutGui
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureDressupLeadInOut import (
    LeadInOutDressupSpec,
    preflight_lead_in_out_dressup,
)
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["lead_in_out_dressup"]


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
    label="Native CAM Lead In Out",
    lead_in=None,
    lead_out=None,
    retract_threshold_mm=0.5,
    rapid_plunge=False,
) -> dict:
    return {
        "operation": "lead_in_out_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "lead_in": lead_in
        or {
            "style": "arc",
            "angle_degrees": 45.0,
            "radius_mm": 2.0,
            "invert": False,
            "offset_mm": 0.0,
            "extend_mm": 0.5,
        },
        "lead_out": lead_out
        or {
            "style": "line",
            "angle_degrees": 30.0,
            "length_mm": 2.0,
            "invert": True,
            "offset_mm": 0.0,
            "extend_mm": 0.25,
        },
        "retract_threshold_mm": retract_threshold_mm,
        "rapid_plunge": rapid_plunge,
    }


def _style_requests() -> tuple[dict, ...]:
    return (
        {
            "style": "arc",
            "angle_degrees": 45.0,
            "radius_mm": 2.0,
            "invert": False,
            "offset_mm": 0.0,
            "extend_mm": 0.5,
        },
        {
            "style": "line",
            "angle_degrees": 30.0,
            "length_mm": 2.0,
            "invert": True,
            "offset_mm": 0.0,
            "extend_mm": 0.5,
        },
        {
            "style": "perpendicular",
            "length_mm": 2.0,
            "offset_mm": 0.0,
            "extend_mm": 0.5,
        },
        {"style": "tangent", "length_mm": 2.0, "offset_mm": 0.0, "extend_mm": 0.5},
        {
            "style": "arc_3d",
            "angle_degrees": 45.0,
            "radius_mm": 2.0,
            "invert": False,
            "offset_mm": 0.0,
        },
        {"style": "arc_z", "angle_degrees": 45.0, "radius_mm": 2.0, "offset_mm": 0.0},
        {
            "style": "arc_z_follow",
            "angle_degrees": 45.0,
            "radius_mm": 2.0,
            "offset_mm": 0.0,
        },
        {
            "style": "helix",
            "angle_degrees": 45.0,
            "radius_mm": 2.0,
            "invert": False,
            "offset_mm": 0.0,
        },
        {
            "style": "line_3d",
            "angle_degrees": 30.0,
            "length_mm": 2.0,
            "invert": False,
            "offset_mm": 0.0,
        },
        {"style": "line_z", "angle_degrees": 30.0, "length_mm": 2.0, "offset_mm": 0.0},
        {
            "style": "line_z_follow",
            "angle_degrees": 30.0,
            "length_mm": 2.0,
            "offset_mm": 0.0,
        },
        {"style": "no_retract"},
        {"style": "vertical", "offset_mm": 0.0},
    )


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("lead_in_out_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    variant = schema["parameters"]["oneOf"][0]["properties"]
    assert variant["lead_in"]["oneOf"] == variant["lead_out"]["oneOf"]
    assert len(variant["lead_in"]["oneOf"]) == 10
    styles = {
        style
        for item in variant["lead_in"]["oneOf"]
        for style in item["properties"]["style"].get(
            "enum",
            [item["properties"]["style"].get("const")],
        )
    }
    assert styles == {"disabled", *(request["style"] for request in _style_requests())}
    assert all(
        item["additionalProperties"] is False for item in variant["lead_in"]["oneOf"]
    )
    arc_z_follow = next(
        item
        for item in variant["lead_in"]["oneOf"]
        if "arc_z_follow"
        in item["properties"]["style"].get(
            "enum",
            [item["properties"]["style"].get("const")],
        )
    )
    assert arc_z_follow["properties"]["angle_degrees"]["maximum"] == 179.0
    line_z_follow = next(
        item
        for item in variant["lead_in"]["oneOf"]
        if "line_z_follow"
        in item["properties"]["style"].get(
            "enum",
            [item["properties"]["style"].get("const")],
        )
    )
    assert line_z_follow["properties"]["angle_degrees"]["maximum"] == 89.0
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


def _custom(job, controller, name):
    operation = PathCustom.Create(name, parentJob=job)
    operation.Label = "Lead In Out gate source"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = [
        "G0 X0 Y0 Z8",
        "G0 Z3",
        "G1 Z-1 F80",
        "G1 X10 Y0 Z-1 F120",
        "G1 X10 Y10 Z-1 F120",
        "G1 X0 Y10 Z-1 F120",
        "G1 X0 Y0 Z-1 F120",
        "G0 Z8",
    ]
    return operation


def _create_fixture(document):
    model = document.addObject("Part::Feature", "LeadInOutGateModel")
    model.Label = "Lead In Out gate model"
    model.Shape = Part.makeBox(20.0, 20.0, 4.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    controller.HorizFeed = 120.0
    controller.VertFeed = 80.0
    source = _custom(job, controller, "LeadInOutSource")
    assert document.recompute(None, True, True) is not False
    center = App.Vector(2.0, -3.0, 7.0)
    job_path = job.Path
    job_path.Center = center
    job.Path = job_path
    source_path = source.Path
    source_path.Center = center
    source.Path = source_path
    assert source.isValid() and source.Path.Size
    return model, job, controller, source


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-lead-in-out-")
        save_path = Path(temporary.name) / "native-manufacture-lead-in-out.FCStd"
        document = App.newDocument("NativeManufactureLeadInOutGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupLeadInOut"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "lead_in_out_dressup",
            "ExactCamJobOperationAndLeadInOutMotionDefinition",
            True,
            False,
        )

        model, job, controller, source = _create_fixture(document)
        document.clearUndos()
        source_configuration = copy_configuration_state(source, {})
        source_path_sha256 = persistent_resource_state(source)["path_sha256"]
        initial_objects = tuple(document.Objects)
        initial_group = tuple(job.Operations.Group)
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
        ledger.begin_run("native-manufacture-lead-in-out-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller_widget)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: (
                read_active_ribbon_surface(controller_widget).surface_id
            ),
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
                f"native-manufacture-lead-in-out-{call_index}",
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

        disabled = {"style": "disabled"}
        no_effect = call(
            _arguments(job, source, lead_in=disabled, lead_out=disabled),
            succeeds=False,
        )
        assert no_effect["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        invalid_angle = _arguments(job, source)
        invalid_angle["lead_in"] = {
            "style": "arc_z_follow",
            "angle_degrees": 180.0,
            "radius_mm": 2.0,
            "offset_mm": 0.0,
        }
        rejected = call(invalid_angle, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        with patch(
            "VibeCADNativeManufactureDressupLeadInOut.MAX_LEAD_IN_OUT_INPUT_COMMANDS",
            3,
        ):
            workload = call(_arguments(job, source), succeeds=False)
        assert workload["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"

        # Every shipped style is prepared on both sides through the exact Native
        # normalizer and the shared generator. Preflight is read-only.
        style_names = []
        for request in _style_requests():
            style_names.append(request["style"])
            for side in ("in", "out"):
                payload = _arguments(
                    job,
                    source,
                    lead_in=request if side == "in" else disabled,
                    lead_out=request if side == "out" else disabled,
                )
                revision_before = state_store.current_revision(document.Uid)
                prepared = preflight_lead_in_out_dressup(
                    document,
                    LeadInOutDressupSpec(
                        label=payload["label"],
                        job=payload["job"],
                        base_operation=payload["base_operation"],
                        lead_in=payload["lead_in"],
                        lead_out=payload["lead_out"],
                        retract_threshold_mm=payload["retract_threshold_mm"],
                        rapid_plunge=payload["rapid_plunge"],
                    ),
                )
                assert prepared.expected_command_count > 0
                assert prepared.expected_cutting_count > 0
                assert prepared.profile_count == 1
                assert state_store.current_revision(document.Uid) == revision_before, (
                    request["style"],
                    side,
                    revision_before,
                    state_store.current_revision(document.Uid),
                )
        assert len(set(style_names)) == 13
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group

        payload = _arguments(job, source, label="Native Lead In Out Lifecycle")
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_lead_in_out_dressup",
            side_effect=RuntimeError("forced Lead In/Out postcondition failure"),
        ):
            failed = call(payload, succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED", failed
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group
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
        assert isinstance(output.Proxy, LeadInOutGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, LeadInOutGui.ViewProviderDressup)
        assert output.Base is source
        assert result["lead_in"]["style"] == "arc"
        assert result["lead_out"]["style"] == "line"
        assert result["profile_count"] == 1
        assert result["closed_profile_count"] == 1
        assert result["lead_in_profile_count"] == 1
        assert result["lead_out_profile_count"] == 1
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
        assert isinstance(output.Proxy, LeadInOutGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, LeadInOutGui.ViewProviderDressup)
        assert output.Base is source and output in job.Operations.Group
        assert tuple(output.VibeCADTimelineReplacedInputs) == (source,)
        assert not source.ViewObject.Visibility and output.ViewObject.Visibility
        assert tuple(output.Path.Center) == tuple(job.Path.Center)

        print(
            "VIBECAD_NATIVE_MANUFACTURE_LEAD_IN_OUT_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true closed_schema=true "
            "invalid_angle=true no_effect=true workload_guard=true rollback=true "
            "thirteen_styles=true both_sides=true rotary_center=true source_preserved=true "
            "replacement=true history=true receipt=true selection=true visibility=true "
            "undo=true redo=true reopen=true"
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
