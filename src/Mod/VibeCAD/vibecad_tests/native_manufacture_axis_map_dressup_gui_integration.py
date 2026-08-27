# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM Axis Map replacements."""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path as CamPath
import Path.Dressup.Gui.AxisMap as AxisMapGui
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["axis_map_dressup"]


_MAPPINGS = ("x_to_a", "y_to_a", "x_to_b", "y_to_b", "x_to_c", "y_to_c")


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
    axis_mapping,
    *,
    label="Native CAM Axis Map",
    radius_mm=10.0,
    reverse=False,
) -> dict:
    return {
        "operation": "axis_map_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "axis_mapping": axis_mapping,
        "radius_mm": radius_mm,
        "reverse": reverse,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("axis_map_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "base_operation",
        "expected_state_sha256",
        "axis_mapping",
        "x_to_a",
        "y_to_c",
        "radius_mm",
        "reverse",
    ):
        assert field in encoded
    radius_schema = schema["parameters"]["oneOf"][0]["properties"]["radius_mm"]
    assert radius_schema["exclusiveMinimum"] == 0
    assert radius_schema["maximum"] == 1_000_000
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


def _custom(document, job, controller, name):
    operation = PathCustom.Create(name, parentJob=job)
    operation.Label = "Axis Map source"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = [
        "G0 X0 Y0 Z4",
        "G0 X0 Y0 Z0",
        "G1 X10 Y0 Z0 F120",
        "G2 X10 Y10 Z0 I0 J5 F120",
        "G1 X0 Y10 Z0 F120",
    ]
    return operation


def _create_fixture(document):
    unrelated = document.addObject("Path::Feature", "UnrelatedAxisCenter")
    unrelated.Path = CamPath.Path([CamPath.Command("G0", {"X": 1.0})])
    unrelated.Path.Center = App.Vector(1.0, 2.0, 3.0)
    model = document.addObject("Part::Feature", "AxisMapGateModel")
    model.Label = "Axis Map gate model"
    model.Shape = Part.makeBox(24.0, 18.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    sources = tuple(
        _custom(document, job, controller, f"AxisMapSeed{index}")
        for index in range(len(_MAPPINGS))
    )
    z_only = PathCustom.Create("AxisMapZOnly", parentJob=job)
    z_only.ToolController = controller
    z_only.CoolantMode = "None"
    z_only.Gcode = ["G0 Z5", "G1 Z0 F20"]
    assert document.recompute(None, True, True) is not False
    assert tuple(job.Operations.Group) == (*sources, z_only)
    assert all(source.isValid() and source.Path.Size for source in sources)
    assert z_only.isValid() and z_only.Path.Size
    return unrelated, model, job, controller, sources, z_only


def _move_timeline_to(document, position: int) -> None:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None and 0 <= position <= len(timeline.Operations)
    window = Gui.getMainWindow()
    end = window.findChild(QtWidgets.QToolButton, "VibeCADFeatureTimelineEnd")
    previous = window.findChild(QtWidgets.QToolButton, "VibeCADFeatureTimelinePrevious")
    assert end is not None and previous is not None
    end.click()
    _events(8)
    while int(timeline.Position) > position:
        previous.click()
        _events(4)
    assert int(timeline.Position) == position


def _maximum_rotary_value(operation, axis: str) -> float:
    values = [
        abs(float(command.Parameters[axis]))
        for command in operation.Path.Commands
        if axis in command.Parameters
    ]
    assert values
    return max(values)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-axis-map-")
        save_path = Path(temporary.name) / "native-manufacture-axis-map.FCStd"
        document = App.newDocument("NativeManufactureAxisMapGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupAxisMap"]
        actual_plan = (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        )
        assert actual_plan == (
            CAPABILITY_NAME,
            "axis_map_dressup",
            "ExactCamJobOperationAndAxisMapParameters",
            True,
            False,
        ), actual_plan

        unrelated, model, job, controller, sources, z_only = _create_fixture(document)
        document.clearUndos()
        source_paths = {
            source.Name: persistent_resource_state(source)["path_sha256"]
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
        ledger.begin_run("native-manufacture-axis-map-gui")

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
                f"native-manufacture-axis-map-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)

        stale = _arguments(job, sources[0], "x_to_a")
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        zero_radius = _arguments(job, sources[0], "x_to_a", radius_mm=0.0)
        radius_result = call(zero_radius, succeeds=False)
        assert radius_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        no_axis_result = call(
            _arguments(job, z_only, "x_to_a"),
            succeeds=False,
        )
        assert no_axis_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group

        first_payload = _arguments(
            job,
            sources[0],
            "x_to_a",
            label="Native Axis Map X to A",
            radius_mm=10.0,
        )
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_axis_map_dressup",
            side_effect=RuntimeError("forced Axis Map postcondition failure"),
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

        first_result = call(first_payload)
        _events(16)
        first_output = document.getObject(first_result["object_name"])
        first_output_name = str(first_output.Name)
        assert isinstance(first_output.Proxy, AxisMapGui.ObjectDressup)
        assert first_output.Base is sources[0]
        assert first_result["axis_mapping"] == "x_to_a"
        assert first_result["input_axis"] == "X"
        assert first_result["output_axis"] == "A"
        assert first_result["rotary_center_mm"] == [0.0, 0.0, -10.0]
        assert math.isclose(
            _maximum_rotary_value(first_output, "A"),
            math.degrees(1.0),
            rel_tol=0.0,
            abs_tol=1.0e-7,
        )
        assert all(command.Name not in {"G2", "G3"} for command in first_output.Path.Commands)
        assert first_result["mapped_command_count"] > 0
        assert len(first_result["receipt"]["created"]) == 1
        assert len(first_result["receipt"]["replaced"]) == 1
        assert first_result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert tuple(unrelated.Path.Center) == (1.0, 2.0, 3.0)
        assert int(document.UndoCount) == 1

        document.undo()
        _events(16)
        assert document.getObject(first_output_name) is None
        assert tuple(job.Operations.Group) == initial_group
        assert sources[0].ViewObject.Visibility
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        controller = document.getObject(controller.Name)
        unrelated = document.getObject(unrelated.Name)
        model = document.getObject(model.Name)
        sources = tuple(document.getObject(source.Name) for source in sources)
        first_output = document.getObject(first_output_name)
        assert first_output.Base is sources[0]

        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-axis-map-gui-after-redo")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        conflict_before = tuple(document.Objects)
        conflict = call(
            _arguments(job, sources[1], "y_to_a", radius_mm=13.0),
            succeeds=False,
        )
        assert conflict["error_code"] == "NATIVE_MANUFACTURE_AXIS_MAP_RADIUS_CONFLICT"
        assert conflict["repair"]["existing_radius_mm"] == 10.0
        assert tuple(document.Objects) == conflict_before

        outputs = [first_output]
        for index, mapping in enumerate(_MAPPINGS[1:4], start=1):
            reverse = bool(index % 2)
            result = call(
                _arguments(
                    job,
                    sources[index],
                    mapping,
                    label=f"Native Axis Map {mapping}",
                    radius_mm=10.0,
                    reverse=reverse,
                )
            )
            output = document.getObject(result["object_name"])
            outputs.append(output)
            assert output.Base is sources[index]
            assert result["reverse"] is reverse
            assert result["output_axis"] == mapping[-1].upper()
            assert all(
                math.isclose(float(obj.Path.Center.z), -10.0, abs_tol=1.0e-9)
                for obj in job.Proxy.allOperations()
            )
            assert tuple(unrelated.Path.Center) == (1.0, 2.0, 3.0)

        timeline = document.getObject("VibeCADTimeline")
        future = sources[5]
        future_index = tuple(timeline.Operations).index(future)
        _move_timeline_to(document, future_index)
        marker_before = int(timeline.Position)
        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-axis-map-gui-at-marker")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        marker_result = call(
            _arguments(
                job,
                sources[4],
                _MAPPINGS[4],
                label="Native Axis Map Before Future",
                radius_mm=10.0,
            )
        )
        marker_output = document.getObject(marker_result["object_name"])
        outputs.append(marker_output)
        assert int(timeline.Position) == marker_before + 1
        assert tuple(timeline.Operations)[marker_before] is marker_output
        assert tuple(timeline.Operations)[marker_before + 1] is future

        end_button = Gui.getMainWindow().findChild(
            QtWidgets.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        assert end_button is not None
        end_button.click()
        _events(12)
        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-axis-map-gui-at-end")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        final_result = call(
            _arguments(
                job,
                sources[5],
                _MAPPINGS[5],
                label="Native Axis Map Y to C Reverse",
                radius_mm=10.0,
                reverse=True,
            )
        )
        final_output = document.getObject(final_result["object_name"])
        outputs.append(final_output)
        assert final_result["output_axis"] == "C"
        assert _maximum_rotary_value(final_output, "C") > 0.0

        for source in sources:
            assert persistent_resource_state(source)["path_sha256"] == source_paths[
                source.Name
            ]
            assert not source.ViewObject.Visibility
        assert all(output.ViewObject.Visibility for output in outputs)
        assert _selection() == selection_before
        assert tuple(unrelated.Path.Center) == (1.0, 2.0, 3.0)
        for name, visible in visibility_before.items():
            if name not in {source.Name for source in sources}:
                assert bool(document.getObject(name).ViewObject.Visibility) is visible

        job_name = str(job.Name)
        output_names = tuple(str(output.Name) for output in outputs)
        source_names = tuple(str(source.Name) for source in sources)
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = None
        _events(20)
        document = App.openDocument(str(save_path))
        _events(24)
        job = document.getObject(job_name)
        reopened_outputs = tuple(document.getObject(name) for name in output_names)
        reopened_sources = tuple(document.getObject(name) for name in source_names)
        assert all(
            isinstance(output.Proxy, AxisMapGui.ObjectDressup)
            and output.Base is source
            and output in job.Operations.Group
            for output, source in zip(reopened_outputs, reopened_sources)
        )
        assert all(not source.ViewObject.Visibility for source in reopened_sources)
        assert all(output.ViewObject.Visibility for output in reopened_outputs)
        assert all(
            tuple(output.VibeCADTimelineReplacedInputs) == (source,)
            for output, source in zip(reopened_outputs, reopened_sources)
        )
        assert tuple(document.getObject("UnrelatedAxisCenter").Path.Center) == (
            1.0,
            2.0,
            3.0,
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_AXIS_MAP_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true invalid_radius=true "
            "missing_axis=true radius_conflict=true rollback=true six_mappings=true reverse=true "
            "arc_linearization=true rotary_center=true outside_job_preserved=true "
            "source_preserved=true replacement=true history=true marker=true "
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
