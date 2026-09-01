# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM Dogbone replacements."""

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

import Path.Dressup.DogboneII as Dogbone
import Path.Dressup.Gui.DogboneII as DogboneGui
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["dogbone_dressup"]


_STYLES = (
    "dogbone",
    "t_bone_horizontal",
    "t_bone_vertical",
    "t_bone_long_edge",
    "t_bone_short_edge",
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
    style,
    *,
    label="Native CAM Dogbone",
    side="right",
    incision=None,
    only_closed_profiles=False,
    disabled_bone_locations_mm=None,
) -> dict:
    return {
        "operation": "dogbone_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "style": style,
        "side": side,
        "incision": incision
        if incision is not None
        else {"kind": "adaptive", "maximum_length_mm": 0.0},
        "only_closed_profiles": only_closed_profiles,
        "disabled_bone_locations_mm": disabled_bone_locations_mm or [],
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("dogbone_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "base_operation",
        "expected_state_sha256",
        "style",
        "t_bone_horizontal",
        "t_bone_short_edge",
        "side",
        "incision",
        "maximum_length_mm",
        "length_mm",
        "only_closed_profiles",
        "disabled_bone_locations_mm",
        "x_mm",
        "y_mm",
    ):
        assert field in encoded
    variant = schema["parameters"]["oneOf"][0]
    locations = variant["properties"]["disabled_bone_locations_mm"]
    assert locations["maxItems"] == 256
    assert locations["uniqueItems"] is True
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


def _profile_gcode(*, clockwise: bool, include_open_profile: bool = True) -> list[str]:
    if clockwise:
        corners = ("X0 Y12", "X24 Y12", "X24 Y0", "X0 Y0")
    else:
        corners = ("X24 Y0", "X24 Y12", "X0 Y12", "X0 Y0")
    result = ["G0 X0 Y0 Z5"]
    for depth in (-1, -2):
        result.extend(
            [
                f"G0 X0 Y0 Z{depth}",
                *(f"G1 {corner} Z{depth} F120" for corner in corners),
                "G0 Z5",
            ]
        )
        if include_open_profile:
            result.extend(
                [
                    "G0 X32 Y0 Z5",
                    f"G0 Z{depth}",
                    f"G1 X42 Y0 Z{depth} F120",
                    f"G1 X42 Y8 Z{depth} F120",
                    "G0 Z5",
                ]
            )
    return result


def _custom(document, job, controller, name, *, clockwise=False):
    operation = PathCustom.Create(name, parentJob=job)
    operation.Label = "Dogbone gate source"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = _profile_gcode(clockwise=clockwise)
    return operation


def _heavy_open_profile(job, controller):
    operation = PathCustom.Create("DogboneHeavyOpen", parentJob=job)
    operation.Label = "Dogbone workload guard source"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = [
        "G0 X0 Y0 Z5",
        "G0 Z-1",
        *(
            f"G1 X{index} Y{index % 2} Z-1 F120"
            for index in range(1, 2003)
        ),
    ]
    return operation


def _create_fixture(document):
    model = document.addObject("Part::Feature", "DogboneGateModel")
    model.Label = "Dogbone gate model"
    model.Shape = Part.makeBox(24.0, 12.0, 6.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    sources = tuple(
        _custom(
            document,
            job,
            controller,
            f"DogboneSeed{index}",
            clockwise=index == 1,
        )
        for index in range(7)
    )
    heavy = _heavy_open_profile(job, controller)
    assert document.recompute(None, True, True) is not False
    center = App.Vector(3.0, 4.0, -7.0)
    job_path = job.Path
    job_path.Center = center
    job.Path = job_path
    for source in sources:
        source_path = source.Path
        source_path.Center = center
        source.Path = source_path
    assert tuple(job.Operations.Group) == (*sources, heavy)
    assert all(source.isValid() and source.Path.Size for source in sources)
    assert heavy.isValid() and heavy.Path.Size
    return model, job, controller, sources, heavy


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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-dogbone-")
        save_path = Path(temporary.name) / "native-manufacture-dogbone.FCStd"
        document = App.newDocument("NativeManufactureDogboneGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupDogbone"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "dogbone_dressup",
            "ExactCamJobOperationAndDogboneReliefDefinition",
            True,
            False,
        )

        model, job, controller, sources, heavy = _create_fixture(document)
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
        ledger.begin_run("native-manufacture-dogbone-gui")

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
                f"native-manufacture-dogbone-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)

        stale = _arguments(job, sources[0], "dogbone")
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        invalid_custom = _arguments(
            job,
            sources[0],
            "dogbone",
            incision={"kind": "custom", "length_mm": 0.0},
        )
        custom_result = call(invalid_custom, succeeds=False)
        assert custom_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        wrong_side = call(
            _arguments(job, sources[0], "dogbone", side="left"),
            succeeds=False,
        )
        assert wrong_side["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"

        workload = call(
            _arguments(
                job,
                heavy,
                "dogbone",
                only_closed_profiles=True,
            ),
            succeeds=False,
        )
        assert workload["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
        assert workload["repair"]["estimated_corner_comparisons"] > workload[
            "repair"
        ]["maximum_corner_comparisons"]

        unavailable_location = call(
            _arguments(
                job,
                sources[0],
                "dogbone",
                disabled_bone_locations_mm=[{"x_mm": 9999.0, "y_mm": 9999.0}],
            ),
            succeeds=False,
        )
        assert unavailable_location["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        available = unavailable_location["repair"]["available_bone_locations_mm"]
        assert len(available) >= 4
        disabled_location = {
            "x_mm": available[0]["x_mm"],
            "y_mm": available[0]["y_mm"],
        }
        assert available[0]["cutting_depth_count"] == 2
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group

        first_payload = _arguments(
            job,
            sources[0],
            "dogbone",
            label="Native Dogbone Group Disable",
            disabled_bone_locations_mm=[disabled_location],
        )
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_dogbone_dressup",
            side_effect=RuntimeError("forced Dogbone postcondition failure"),
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
        assert isinstance(first_output.Proxy, Dogbone.Proxy)
        assert isinstance(first_output.ViewObject.Proxy, DogboneGui.ViewProviderDressup)
        assert first_output.Base is sources[0]
        assert first_result["style"] == "dogbone"
        assert first_result["side"] == "right"
        assert first_result["disabled_bone_group_count"] == 1
        assert len(first_output.BoneBlacklist) == 2
        assert first_result["candidate_bone_count"] > first_result["enabled_bone_count"]
        assert first_result["inserted_command_count"] == (
            first_result["enabled_bone_count"] * 2
        )
        assert first_result["path_center_mm"] == [3.0, 4.0, -7.0]
        assert len(first_result["receipt"]["created"]) == 1
        assert len(first_result["receipt"]["replaced"]) == 1
        assert first_result["assistant_undo_available"] is True
        assert _selection() == selection_before
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
        model = document.getObject(model.Name)
        sources = tuple(document.getObject(source.Name) for source in sources)
        first_output = document.getObject(first_output_name)
        assert first_output.Base is sources[0]

        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-dogbone-gui-after-redo")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        cases = (
            (
                1,
                "t_bone_horizontal",
                "left",
                {"kind": "fixed"},
                False,
                "Native T-bone Horizontal",
            ),
            (
                2,
                "t_bone_vertical",
                "right",
                {"kind": "custom", "length_mm": 1.75},
                False,
                "Native T-bone Vertical",
            ),
            (
                3,
                "t_bone_long_edge",
                "right",
                {"kind": "adaptive", "maximum_length_mm": 1.25},
                False,
                "Native T-bone Long Edge",
            ),
            (
                4,
                "t_bone_short_edge",
                "right",
                {"kind": "fixed"},
                False,
                "Native T-bone Short Edge",
            ),
        )
        outputs = [first_output]
        results = [first_result]
        marker_checked = False
        for source_index, style, side, incision, closed_only, label in cases:
            if source_index == 3:
                timeline = document.getObject("VibeCADTimeline")
                future = sources[6]
                future_index = tuple(timeline.Operations).index(future)
                _move_timeline_to(document, future_index)
                marker_before = int(timeline.Position)
                turn = _turn(surface, registry)
                frozen = turn.surface
                ledger.begin_run("native-manufacture-dogbone-gui-at-marker")
                dispatcher = NativeTurnDispatcher(
                    document=document,
                    state=state_store,
                    registry=registry,
                    turn=turn,
                    runtimes=build_native_runtime_bindings(context, turn.tool_names),
                    reauthorize_turn=reauthorize,
                    active_document=lambda: App.ActiveDocument,
                )
            result = call(
                _arguments(
                    job,
                    sources[source_index],
                    style,
                    label=label,
                    side=side,
                    incision=incision,
                    only_closed_profiles=closed_only,
                )
            )
            output = document.getObject(result["object_name"])
            outputs.append(output)
            results.append(result)
            assert output.Base is sources[source_index]
            assert result["style"] == style
            assert result["side"] == side
            assert result["incision"] == incision["kind"]
            assert result["enabled_bone_count"] > 0
            assert result["inserted_command_count"] == result["enabled_bone_count"] * 2
            assert result["path_center_mm"] == [3.0, 4.0, -7.0]
            if source_index == 2:
                assert result["custom_length_mm"] == 1.75
                assert float(output.Custom.Value) == 1.75
            if source_index == 3:
                assert result["custom_length_mm"] == 1.25
                assert int(timeline.Position) == marker_before + 1
                assert tuple(timeline.Operations)[marker_before] is output
                assert tuple(timeline.Operations)[marker_before + 1] is future
                marker_checked = True

        assert marker_checked
        end = Gui.getMainWindow().findChild(
            QtWidgets.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        assert end is not None
        end.click()
        _events(12)
        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-dogbone-gui-at-end")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        unfiltered = call(
            _arguments(
                job,
                sources[5],
                "dogbone",
                label="Native Dogbone All Profiles",
                only_closed_profiles=False,
            )
        )
        filtered = call(
            _arguments(
                job,
                sources[6],
                "dogbone",
                label="Native Dogbone Outer Closed Profiles",
                only_closed_profiles=True,
            )
        )
        unfiltered_output = document.getObject(unfiltered["object_name"])
        filtered_output = document.getObject(filtered["object_name"])
        outputs.extend((unfiltered_output, filtered_output))
        results.extend((unfiltered, filtered))
        assert filtered["only_closed_profiles"] is True
        assert unfiltered["candidate_bone_count"] > filtered["candidate_bone_count"]
        assert {result["style"] for result in results} == set(_STYLES)
        assert {result["side"] for result in results} == {"left", "right"}
        assert {result["incision"] for result in results} == {
            "adaptive",
            "fixed",
            "custom",
        }

        for source in sources:
            configuration, path_sha256 = source_states[source.Name]
            assert copy_configuration_state(source, {}) == configuration
            assert persistent_resource_state(source)["path_sha256"] == path_sha256
            assert tuple(source.Path.Center) == (3.0, 4.0, -7.0)
            assert not source.ViewObject.Visibility
        assert all(output.ViewObject.Visibility for output in outputs)
        assert _selection() == selection_before
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
            isinstance(output.Proxy, Dogbone.Proxy)
            and isinstance(output.ViewObject.Proxy, DogboneGui.ViewProviderDressup)
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
        assert len(reopened_outputs[0].BoneBlacklist) == 2

        print(
            "VIBECAD_NATIVE_MANUFACTURE_DOGBONE_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true invalid_custom=true "
            "wrong_side=true workload_guard=true actionable_locations=true rollback=true "
            "five_styles=true "
            "both_sides=true three_incisions=true grouped_disable=true closed_only=true "
            "source_preserved=true replacement=true history=true marker=true receipt=true "
            "selection=true visibility=true undo=true redo=true reopen=true"
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
