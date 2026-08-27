# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM Array dress-up replacements."""

from __future__ import annotations

import json
from pathlib import Path
import random
import sys
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Dressup.Array as DressupArray
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["array_dressup"]


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


def _visibility(document) -> dict[str, bool]:
    return {
        obj.Name: bool(obj.ViewObject.Visibility)
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    }


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(
    job,
    base,
    pattern,
    *,
    label="Native CAM Array Dress-up",
    jitter=None,
) -> dict:
    return {
        "operation": "array_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "pattern": pattern,
        "jitter": jitter if jitter is not None else {"enabled": False},
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("array_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "base_operation",
        "expected_state_sha256",
        "linear_1d",
        "linear_2d",
        "polar",
        "first_direction",
        "percentage",
        "maximum_offset_mm",
    ):
        assert field in encoded
    assert '"maximum":2147483647' in encoded
    assert '"maximum":100' in encoded
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


def _custom(document, job, controller, name, label):
    operation = PathCustom.Create(name, parentJob=job)
    operation.Label = label
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = [
        "G0 X0 Y0 Z4",
        "G0 X0 Y0 Z0",
        "G1 X8 Y0 Z0",
        "G1 X8 Y5 Z0",
    ]
    return operation


def _create_fixture(document):
    model = document.addObject("Part::Feature", "ArrayDressupGateModel")
    model.Label = "Array dress-up gate model"
    model.Shape = Part.makeBox(24.0, 18.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    first = _custom(document, job, controller, "ArrayDressupSeedA", "Seed source")
    second = _custom(document, job, controller, "ArrayDressupSeedB", "Seed source")
    grid = _custom(document, job, controller, "ArrayDressupGrid", "Grid source")
    polar = _custom(document, job, controller, "ArrayDressupPolar", "Polar source")
    assert document.recompute(None, True, True) is not False
    sources = (first, second, grid, polar)
    assert tuple(job.Operations.Group) == sources
    assert all(source.isValid() and source.Path.Size for source in sources)
    return model, job, controller, sources


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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-array-dressup-")
        save_path = Path(temporary.name) / "native-manufacture-array-dressup.FCStd"
        document = App.newDocument("NativeManufactureArrayDressupGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupArray"]
        actual_plan = (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        )
        assert actual_plan == (
            CAPABILITY_NAME,
            "array_dressup",
            "ExactCamJobOperationAndArrayDressupPattern",
            True,
            False,
        ), actual_plan

        model, job, controller, sources = _create_fixture(document)
        first, second, grid, polar = sources
        document.clearUndos()
        source_states = {
            source.Name: (
                copy_configuration_state(source, {}),
                persistent_resource_state(source)["path_sha256"],
            )
            for source in sources
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
        ledger.begin_run("native-manufacture-array-dressup-gui")

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
                f"native-manufacture-array-dressup-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)

        linear_pattern = {
            "kind": "linear_1d",
            "copies": 2,
            "offset_mm": {"x_mm": 15.0, "y_mm": 0.0, "z_mm": 0.0},
        }
        seeded_jitter = {
            "enabled": True,
            "percentage": 100,
            "seed": 3471,
            "maximum_offset_mm": {"x_mm": 0.5, "y_mm": 0.75, "z_mm": 0.0},
        }
        stale = _arguments(job, first, linear_pattern)
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        oversized = _arguments(
            job,
            first,
            {
                "kind": "linear_1d",
                "copies": 256,
                "offset_mm": {"x_mm": 1.0, "y_mm": 0.0, "z_mm": 0.0},
            },
        )
        oversized_result = call(oversized, succeeds=False)
        assert oversized_result["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"

        polar_jitter = _arguments(
            job,
            polar,
            {
                "kind": "polar",
                "copies": 3,
                "total_angle_degrees": 270.0,
                "centre_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            },
            jitter=seeded_jitter,
        )
        polar_jitter_result = call(polar_jitter, succeeds=False)
        assert polar_jitter_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(document.Objects) == initial_objects
        assert int(document.UndoCount) == 0

        first_payload = _arguments(
            job,
            first,
            linear_pattern,
            label="Native Seeded Linear Array A",
            jitter=seeded_jitter,
        )
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_array_dressup",
            side_effect=RuntimeError("forced Array dress-up postcondition failure"),
        ):
            failed = call(first_payload, succeeds=False)
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

        random.seed(90210)
        expected_random = (random.random(), random.random())
        random.seed(90210)
        observed_first = random.random()
        first_result = call(first_payload)
        observed_second = random.random()
        assert (observed_first, observed_second) == expected_random
        _events(16)
        first_output = document.getObject(first_result["object_name"])
        first_output_name = str(first_output.Name)
        assert isinstance(first_output.Proxy, DressupArray.DressupArray)
        assert first_output.Base is first
        assert first_output in job.Operations.Group and first not in job.Operations.Group
        assert first_result["placement_count"] == 3
        assert first_result["jitter_enabled"] is True
        assert len(first_result["receipt"]["created"]) == 1
        assert len(first_result["receipt"]["replaced"]) == 1
        assert first_result["assistant_undo_available"] is True
        assert not first.ViewObject.Visibility and first_output.ViewObject.Visibility
        assert _selection() == selection_before
        assert int(document.UndoCount) == 1

        document.undo()
        _events(16)
        assert document.getObject(first_output_name) is None
        assert tuple(job.Operations.Group) == initial_group
        assert first.ViewObject.Visibility
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        controller = document.getObject(controller.Name)
        first, second, grid, polar = tuple(
            document.getObject(source.Name) for source in sources
        )
        first_output = document.getObject(first_output_name)
        assert first_output.Base is first and first_output in job.Operations.Group

        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-array-dressup-gui-after-redo")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        second_result = call(
            _arguments(
                job,
                second,
                linear_pattern,
                label="Native Seeded Linear Array B",
                jitter=seeded_jitter,
            )
        )
        second_output = document.getObject(second_result["object_name"])
        first_motion = tuple(
            command.toGCode()
            for command in first_output.Path.Commands
            if str(command.Name).startswith("G")
        )
        second_motion = tuple(
            command.toGCode()
            for command in second_output.Path.Commands
            if str(command.Name).startswith("G")
        )
        assert first_motion == second_motion

        grid_result = call(
            _arguments(
                job,
                grid,
                {
                    "kind": "linear_2d",
                    "copies_x": 2,
                    "copies_y": 1,
                    "offset_mm": {"x_mm": 13.0, "y_mm": 11.0, "z_mm": 0.0},
                    "first_direction": "y",
                },
                label="Native Reversed Grid Array",
            )
        )
        grid_output = document.getObject(grid_result["object_name"])
        assert grid_result["placement_count"] == 6
        assert grid_output.Type == "Linear2D"
        assert not bool(grid_output.SwapDirection)

        timeline = document.VibeCADTimeline
        marker = list(timeline.Operations).index(polar) + 1
        _move_timeline_to(document, marker)
        timeline_before_marker = tuple(timeline.Operations)
        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-array-dressup-gui-at-marker")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        polar_result = call(
            _arguments(
                job,
                polar,
                {
                    "kind": "polar",
                    "copies": 3,
                    "total_angle_degrees": 270.0,
                    "centre_mm": {"x_mm": 2.0, "y_mm": 1.0, "z_mm": 0.0},
                },
                label="Native Polar Array Dress-up",
            )
        )
        polar_output = document.getObject(polar_result["object_name"])
        assert tuple(timeline.Operations) == (
            *timeline_before_marker[:marker],
            polar_output,
            *timeline_before_marker[marker:],
        )
        assert polar_result["placement_count"] == 4
        assert polar_output.Type == "Polar"
        _move_timeline_to(document, len(timeline.Operations))

        outputs = (first_output, second_output, grid_output, polar_output)
        sources = (first, second, grid, polar)
        assert all(output.isValid() and output.Path.Size for output in outputs)
        assert all(output.Base is source for output, source in zip(outputs, sources))
        assert all(not source.ViewObject.Visibility for source in sources)
        assert all(output.ViewObject.Visibility for output in outputs)
        assert all(
            (
                copy_configuration_state(source, {}),
                persistent_resource_state(source)["path_sha256"],
            )
            == source_states[source.Name]
            for source in sources
        )
        assert _selection() == selection_before
        final_visibility = _visibility(document)
        for name, visible in visibility_before.items():
            if name not in {source.Name for source in sources}:
                assert final_visibility[name] is visible
        assert not Gui.Control.activeDialog()

        document_name = str(document.Name)
        job_name = str(job.Name)
        source_names = tuple(source.Name for source in sources)
        output_names = tuple(output.Name for output in outputs)
        document.saveAs(str(save_path))
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _events(24)
        job = document.getObject(job_name)
        reopened_sources = tuple(document.getObject(name) for name in source_names)
        reopened_outputs = tuple(document.getObject(name) for name in output_names)
        assert all(reopened_sources) and all(reopened_outputs)
        assert all(output.isValid() and output.Path.Size for output in reopened_outputs)
        assert all(output in job.Operations.Group for output in reopened_outputs)
        assert all(source not in job.Operations.Group for source in reopened_sources)
        assert all(
            output.Base is source
            for output, source in zip(reopened_outputs, reopened_sources)
        )
        assert all(
            isinstance(output.Proxy, DressupArray.DressupArray)
            for output in reopened_outputs
        )
        assert all(str(output.VibeCADTimelineRole) == "operation" for output in reopened_outputs)
        assert all(not source.ViewObject.Visibility for source in reopened_sources)

        print(
            "VIBECAD_NATIVE_MANUFACTURE_ARRAY_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true bounded_work=true "
            "rollback=true linear_1d=true linear_2d=true polar=true "
            "seeded_jitter=true global_random_preserved=true source_preserved=true "
            "replacement=true history=true marker=true receipt=true selection=true "
            "visibility=true undo=true redo=true reopen=true",
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
