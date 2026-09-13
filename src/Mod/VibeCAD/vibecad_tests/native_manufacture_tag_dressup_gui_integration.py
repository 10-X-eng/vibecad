# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for holding-tag modes and replacement lifecycle."""

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

import Path.Dressup.Gui.Tags as TagsGui
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureDressupTag import (
    TagDressupSpec,
    preflight_tag_dressup,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)

from VibeCADNativeManufactureState import (
    job_state,
    operation_state,
    persistent_resource_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["tag_dressup"]


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


def _shape(width=4.0, height=1.5, angle=45.0, radius=0.25) -> dict:
    return {
        "material_width_mm": width,
        "material_height_mm": height,
        "side_angle_from_horizontal_degrees": angle,
        "top_fillet_radius_mm": radius,
    }


def _arguments(job, base, placement, *, label="Native CAM Holding Tags") -> dict:
    return {
        "operation": "tag_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_state(base)),
        "placement": placement,
    }


def _spec(payload: dict) -> TagDressupSpec:
    return TagDressupSpec(
        label=payload["label"],
        job=payload["job"],
        base_operation=payload["base_operation"],
        placement=payload["placement"],
    )


def _automatic() -> dict:
    return {
        "kind": "automatic_distribution",
        "shape": _shape(),
        "minimum_per_wire": 2,
        "maximum_for_longest_wire": 4,
    }


def _explicit() -> dict:
    return {
        "kind": "explicit_locations",
        "shape": _shape(),
        "tags": [
            {"x_mm": 15.0, "y_mm": 0.0, "enabled": True},
            {"x_mm": 30.0, "y_mm": 10.0, "enabled": True},
            {"x_mm": 900.0, "y_mm": 900.0, "enabled": False},
        ],
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("tag_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    placements = variant["properties"]["placement"]["oneOf"]
    assert {
        item["properties"]["kind"]["const"] for item in placements
    } == {
        "explicit_locations",
        "automatic_distribution",
        "copy_enabled_from_dressup",
    }
    assert all(item["additionalProperties"] is False for item in placements)
    explicit = next(
        item
        for item in placements
        if item["properties"]["kind"]["const"] == "explicit_locations"
    )
    assert explicit["properties"]["tags"]["maxItems"] == 256
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
    operation.Label = f"{name} profile"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = [
        "G0 X0 Y0 Z8",
        "G0 Z3",
        "G1 Z-2 F80",
        "G1 X30 Y0 Z-2 F120",
        "G1 X30 Y20 Z-2 F120",
        "G1 X0 Y20 Z-2 F120",
        "G1 X0 Y0 Z-2 F120",
        "G0 Z8",
    ]
    return operation


def _fixture(document):
    model = document.addObject("Part::Feature", "HoldingTagGateModel")
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
    operations = tuple(
        _custom(job, controller, name)
        for name in ("TagAutoBase", "TagCopyBase", "TagExplicitBase")
    )
    assert document.recompute(None, True, True) is not False
    center = App.Vector(2.0, -3.0, 7.0)
    job_path = job.Path
    job_path.Center = center
    job.Path = job_path
    for operation in operations:
        path = operation.Path
        path.Center = center
        operation.Path = path
        operation.ViewObject.Visibility = True
        assert operation.isValid() and operation.Path.Size
    return model, job, controller, operations


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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-tag-")
        save_path = Path(temporary.name) / "native-manufacture-tag.FCStd"
        document = App.newDocument("NativeManufactureTagGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupTag"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "tag_dressup",
            "ExactCamJobOperationAndHoldingTagDefinition",
            True,
            False,
        )

        model, job, controller, operations = _fixture(document)
        auto_base, copy_base, explicit_base = operations
        document.clearUndos()
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-tag-gui")

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
                f"native-manufacture-tag-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        stale = _arguments(job, auto_base, _automatic())
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        assert call(stale, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        invalid_angle = _arguments(job, auto_base, _automatic())
        invalid_angle["placement"]["shape"][
            "side_angle_from_horizontal_degrees"
        ] = 0.0
        assert call(invalid_angle, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        off_path = {
            "kind": "explicit_locations",
            "shape": _shape(),
            "tags": [{"x_mm": 500.0, "y_mm": 500.0, "enabled": True}],
        }
        try:
            preflight_tag_dressup(
                document,
                _spec(_arguments(job, auto_base, off_path)),
            )
        except NativeManufactureError as exc:
            assert exc.error_code == "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
            assert exc.repair["rejected_tags"][0]["reason"] == "off_bottom_path"
        else:
            raise AssertionError("an off-path enabled holding tag was accepted")

        with patch(
            "VibeCADNativeManufactureDressupTag.MAX_NATIVE_HOLDING_TAG_SCAN_UNITS",
            1,
        ):
            workload = call(
                _arguments(job, auto_base, _automatic()),
                succeeds=False,
            )
        assert workload["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"

        revision = state_store.current_revision(document.Uid)
        prepared = preflight_tag_dressup(
            document,
            _spec(_arguments(job, auto_base, _automatic())),
        )
        assert prepared.bottom_wire_count == 1
        assert len(prepared.locations) == 4
        assert prepared.expected_command_count > 0
        assert prepared.mapped_segment_count > 0
        assert state_store.current_revision(document.Uid) == revision

        auto_result = call(_arguments(job, auto_base, _automatic()))
        auto_output = document.getObject(auto_result["object_name"])
        assert isinstance(auto_output.Proxy, TagsGui.PathDressupTag.ObjectTagDressup)
        assert auto_result["tag_count"] == 4
        assert auto_result["enabled_tag_count"] == 4
        assert auto_result["placement"]["kind"] == "automatic_distribution"
        auto_state = persistent_resource_state(auto_output)

        copy_placement = {
            "kind": "copy_enabled_from_dressup",
            "source_tag_dressup": _target(operation_state(auto_output)),
        }
        copy_result = call(_arguments(job, copy_base, copy_placement))
        copy_output = document.getObject(copy_result["object_name"])
        assert copy_result["tag_count"] == auto_result["tag_count"]
        assert copy_result["placement"] == {
            "kind": "copy_enabled_from_dressup",
            "source_object_name": auto_output.Name,
        }
        assert float(copy_output.Width.Value) == float(auto_output.Width.Value)
        assert float(copy_output.Height.Value) == float(auto_output.Height.Value)
        assert persistent_resource_state(auto_output) == auto_state

        explicit_payload = _arguments(
            job,
            explicit_base,
            _explicit(),
            label="Native Exact Holding Tags",
        )
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
            "VibeCADNativeManufactureModifyRuntime.verify_created_tag_dressup",
            side_effect=RuntimeError("forced Tag postcondition failure"),
        ):
            failed = call(explicit_payload, succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED"
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

        result = call(explicit_payload)
        _events(16)
        output = document.getObject(result["object_name"])
        output_name = str(output.Name)
        base_name = str(explicit_base.Name)
        job_name = str(job.Name)
        assert isinstance(output.Proxy, TagsGui.PathDressupTag.ObjectTagDressup)
        assert isinstance(output.ViewObject.Proxy, TagsGui.PathDressupTagViewProvider)
        assert output.Base is explicit_base
        assert tuple(output.Disabled) == (2,)
        assert len(output.Positions) == 3
        assert abs(float(output.Positions[0].z) + 2.0) < 1.0e-9
        assert output.Proxy.tags[2].enabled is False
        assert result["enabled_tag_count"] == 2
        assert result["disabled_indices"] == [2]
        assert result["path_center_mm"] == [2.0, -3.0, 7.0]
        assert len(result["receipt"]["created"]) == 1
        assert len(result["receipt"]["replaced"]) == 1
        assert result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert int(document.UndoCount) == before_undo + 1
        assert not explicit_base.ViewObject.Visibility
        assert output.ViewObject.Visibility

        document.undo()
        _events(16)
        assert document.getObject(output_name) is None
        assert document.getObject(base_name).ViewObject.Visibility
        document.redo()
        _events(16)
        explicit_base = document.getObject(base_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert output.Base is explicit_base and output in job.Operations.Group

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(20)
        document = App.openDocument(str(save_path))
        _events(24)
        explicit_base = document.getObject(base_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert isinstance(output.Proxy, TagsGui.PathDressupTag.ObjectTagDressup)
        assert isinstance(output.ViewObject.Proxy, TagsGui.PathDressupTagViewProvider)
        assert output.Base is explicit_base and output in job.Operations.Group
        assert tuple(output.VibeCADTimelineReplacedInputs) == (explicit_base,)
        assert not explicit_base.ViewObject.Visibility and output.ViewObject.Visibility
        assert tuple(output.Disabled) == (2,)
        assert tuple(output.Path.Center) == tuple(job.Path.Center)

        print(
            "VIBECAD_NATIVE_MANUFACTURE_TAG_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true closed_schema=true "
            "invalid_shape=true off_path=true workload_guard=true rollback=true "
            "automatic=true explicit=true copy=true durable_disabled=true "
            "source_preserved=true rotary_center=true replacement=true history=true "
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
