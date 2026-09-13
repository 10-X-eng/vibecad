# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact flattened CAM Simple Copy operations."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import PathScripts.PathUtils as PathUtils
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
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


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(job, sources, *, label="Native CAM Simple Copy") -> dict:
    return {
        "operation": "simple_copy",
        "label": label,
        "job": _target(job_state(job)),
        "source_operations": [
            _target(operation_reference_state(source)) for source in sources
        ],
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("simple_copy",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "source_operations",
        "object_name",
        "expected_state_sha256",
        "label",
    ):
        assert field in encoded
    assert '"maxItems":64' in encoded
    assert '"uniqueItems":true' in encoded
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


def _custom(document, job, controller, name, gcode, placement, coolant="Flood"):
    operation = PathCustom.Create(name, parentJob=job)
    job.Proxy.addOperation(operation)
    operation.Label = name
    operation.ToolController = controller
    operation.CoolantMode = coolant
    operation.Gcode = list(gcode)
    operation.Placement = App.Placement(App.Vector(*placement), App.Rotation())
    return operation


def _create_fixture(document):
    model = document.addObject("Part::Feature", "SimpleCopyGateModel")
    model.Label = "Simple Copy gate model"
    model.Shape = Part.makeBox(24.0, 18.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    first = _custom(
        document,
        job,
        controller,
        "SimpleCopyFirst",
        ("G0 X0 Y0 Z4", "G0 X0 Y0 Z0", "G1 X8 Y0 Z0", "G1 X8 Y4 Z0"),
        (10.0, 0.0, 0.0),
    )
    second = _custom(
        document,
        job,
        controller,
        "SimpleCopySecond",
        ("G0 X0 Y0 Z3", "G0 X0 Y0 Z0", "G1 X0 Y7 Z0", "G1 X4 Y7 Z0"),
        (0.0, 20.0, 0.0),
    )
    incompatible = _custom(
        document,
        job,
        controller,
        "SimpleCopyMist",
        ("G0 X0 Y0 Z2", "G0 X0 Y0 Z0", "G1 X3 Y0 Z0"),
        (0.0, 0.0, 0.0),
        coolant="Mist",
    )
    assert document.recompute(None, True, True) is not False
    assert tuple(job.Operations.Group) == (first, second, incompatible)
    assert all(value.isValid() and value.Path.Size for value in (first, second, incompatible))
    return model, job, controller, first, second, incompatible


def _placed_gcode(*sources) -> tuple[str, ...]:
    return tuple(
        command.toGCode()
        for source in sources
        for command in PathUtils.getPathWithPlacement(source).Commands
    )


def _move_timeline_to(document, position: int) -> None:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None and 0 <= position <= len(timeline.Operations)
    window = Gui.getMainWindow()
    end = window.findChild(QtWidgets.QToolButton, "VibeCADFeatureTimelineEnd")
    previous = window.findChild(
        QtWidgets.QToolButton,
        "VibeCADFeatureTimelinePrevious",
    )
    assert end is not None and previous is not None
    end.click()
    _events(8)
    while int(timeline.Position) > position:
        previous.click()
        _events(4)
    assert int(timeline.Position) == position


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-simple-copy-")
        save_path = Path(temporary.name) / "native-manufacture-simple-copy.FCStd"
        document = App.newDocument("NativeManufactureSimpleCopyGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_SimpleCopy"]
        actual_plan = (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        )
        assert actual_plan == (
            "manufacture.copy_path",
            "simple_copy",
            "ExactCamJobPlacedToolpathFlatteningSet",
            True,
            False,
        ), actual_plan

        model, job, controller, first, second, incompatible = _create_fixture(document)
        document.clearUndos()
        sources = (first, second, incompatible)
        source_states = {
            source.Name: persistent_resource_state(source) for source in sources
        }
        initial_objects = tuple(document.Objects)
        initial_group = tuple(job.Operations.Group)
        initial_timeline = (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-simple-copy-gui")

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
                MANUFACTURE_OPERATION_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-simple-copy-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)

        stale = _arguments(job, (first,))
        stale["source_operations"][0]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(document.Objects) == initial_objects
        assert int(document.UndoCount) == 0

        incompatible_result = call(
            _arguments(job, (first, incompatible)),
            succeeds=False,
        )
        assert incompatible_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert tuple(document.Objects) == initial_objects
        assert int(document.UndoCount) == 0

        single_payload = _arguments(job, (first,))
        with patch(
            "VibeCADNativeManufactureOperationRuntime.verify_created_simple_copy",
            side_effect=RuntimeError("forced Simple Copy postcondition failure"),
        ):
            failed = call(single_payload, succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED", failed
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group
        assert initial_timeline == (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )
        assert int(document.UndoCount) == 0

        expected_single = _placed_gcode(first)
        single = call(single_payload)
        _events(16)
        single_object = document.getObject(single["object_name"])
        single_name = str(single_object.Name)
        assert tuple(single_object.Gcode) == expected_single
        assert single["flattened_command_count"] == len(expected_single)
        assert single_object.ToolController is controller
        assert single_object.CoolantMode == "Flood"
        assert first not in tuple(single_object.OutList)
        assert _selection() == selection_before
        assert _visibility(document)[: len(visibility_before)] == visibility_before
        assert all(
            persistent_resource_state(source) == source_states[source.Name]
            for source in sources
        )
        assert single["assistant_undo_available"] is True
        assert len(single["receipt"]["created"]) == 1
        assert int(document.UndoCount) == 1

        document.undo()
        _events(16)
        assert document.getObject(single_name) is None
        assert tuple(job.Operations.Group) == initial_group
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        controller = document.getObject(controller.Name)
        first = document.getObject(first.Name)
        second = document.getObject(second.Name)
        incompatible = document.getObject(incompatible.Name)
        single_object = document.getObject(single_name)
        assert tuple(single_object.Gcode) == _placed_gcode(first)

        expected_multi = _placed_gcode(first, second)
        multi = call(
            _arguments(
                job,
                (first, second),
                label="Native CAM Multi Simple Copy",
            )
        )
        multi_object = document.getObject(multi["object_name"])
        assert tuple(multi_object.Gcode) == expected_multi
        assert multi["source_operation_names"] == [first.Name, second.Name]
        assert multi["flattened_command_count"] == len(expected_multi)
        assert all(source not in tuple(multi_object.OutList) for source in (first, second))

        timeline = document.VibeCADTimeline
        marker = list(timeline.Operations).index(second) + 1
        _move_timeline_to(document, marker)
        timeline_before_marker = (
            tuple(timeline.Operations),
            tuple(bool(value) for value in timeline.VisibilityAtEnd),
            tuple(bool(value) for value in timeline.SuppressionAtEnd),
        )
        marker_result = call(
            _arguments(
                job,
                (first, second),
                label="Native CAM Marker Simple Copy",
            )
        )
        marker_object = document.getObject(marker_result["object_name"])
        assert tuple(timeline.Operations) == (
            *timeline_before_marker[0][:marker],
            marker_object,
            *timeline_before_marker[0][marker:],
        )
        for old_index in range(len(timeline_before_marker[0])):
            new_index = old_index if old_index < marker else old_index + 1
            assert bool(timeline.VisibilityAtEnd[new_index]) is timeline_before_marker[1][old_index]
            assert bool(timeline.SuppressionAtEnd[new_index]) is timeline_before_marker[2][old_index]
        _move_timeline_to(document, len(timeline.Operations))

        assert all(
            persistent_resource_state(source) == source_states[source.Name]
            for source in (first, second, incompatible)
        )
        assert _selection() == selection_before
        assert _visibility(document)[: len(visibility_before)] == visibility_before
        assert not Gui.Control.activeDialog()

        document_name = str(document.Name)
        job_name = str(job.Name)
        source_names = tuple(source.Name for source in (first, second, incompatible))
        output_names = (
            single["object_name"],
            multi["object_name"],
            marker_result["object_name"],
        )
        document.saveAs(str(save_path))
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _events(24)
        job = document.getObject(job_name)
        reopened_sources = tuple(document.getObject(name) for name in source_names)
        outputs = tuple(document.getObject(name) for name in output_names)
        assert all(reopened_sources) and all(outputs)
        assert all(output.isValid() and output.Path.Size for output in outputs)
        assert all(output in job.Operations.Group for output in outputs)
        assert all(str(output.VibeCADTimelineRole) == "operation" for output in outputs)
        assert tuple(outputs[1].Gcode) == _placed_gcode(*reopened_sources[:2])
        assert all(
            source not in tuple(output.OutList)
            for output in outputs
            for source in reopened_sources
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_SIMPLE_COPY_GUI_OK "
            "exact_job=true exact_sources=true stale=true compatibility=true "
            "rollback=true single=true multi=true placements=true flattened=true "
            "no_source_links=true source_preserved=true history=true marker=true "
            "receipt=true selection=true visibility=true undo=true redo=true reopen=true",
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
