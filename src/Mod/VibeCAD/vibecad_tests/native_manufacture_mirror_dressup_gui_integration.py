# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for exact CAM Mirror generation and replacement lifecycle."""

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

import Constants
import Path.Dressup.Gui.Mirror as MirrorGui
import Path.Dressup.Mirror as MirrorCore
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import PathScripts.PathUtils as PathUtils
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureDressupMirror import (
    MirrorDressupSpec,
    preflight_mirror_dressup,
)
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)

from VibeCADNativeManufactureState import (
    candidate_model_state,
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["mirror_dressup"]


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


def _offset(x=0.0, y=0.0, z=0.0) -> dict:
    return {"x_mm": x, "y_mm": y, "z_mm": z}


def _arguments(job, base, mirror, label="Native CAM Mirror") -> dict:
    return {
        "operation": "mirror_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "mirror": mirror,
    }


def _origin(axis="x", *, offset=None, keep=False) -> dict:
    return {
        "kind": "axis_at_origin",
        "axis": axis,
        "offset_mm": offset or _offset(),
        "keep_base_path": keep,
    }


def _model_center(model, axis="x", *, offset=None, keep=False) -> dict:
    return {
        "kind": "axis_at_model_center",
        "axis": axis,
        "model": _target(candidate_model_state(model)),
        "offset_mm": offset or _offset(),
        "keep_base_path": keep,
    }


def _reference(reference, subelement="Edge1", *, offset=None, keep=False) -> dict:
    target = _target(candidate_model_state(reference))
    target["subelement"] = subelement
    return {
        "kind": "axis_aligned_reference",
        "reference": target,
        "offset_mm": offset or _offset(),
        "keep_base_path": keep,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("mirror_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    mirror_variants = variant["properties"]["mirror"]["oneOf"]
    assert {
        item["properties"]["kind"]["const"] for item in mirror_variants
    } == {"axis_at_origin", "axis_at_model_center", "axis_aligned_reference"}
    assert all(item["additionalProperties"] is False for item in mirror_variants)
    reference = next(
        item
        for item in mirror_variants
        if item["properties"]["kind"]["const"] == "axis_aligned_reference"
    )
    assert reference["properties"]["reference"]["additionalProperties"] is False
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
    model = document.addObject("Part::Feature", "MirrorGateModel")
    model.Label = "Mirror gate transformed model"
    model.Shape = Part.makeBox(20.0, 10.0, 4.0)
    model.Placement = App.Placement(
        App.Vector(30.0, 40.0, 2.0),
        App.Rotation(App.Vector(0.0, 0.0, 1.0), 90.0),
    )
    reference = document.addObject("Part::Feature", "MirrorGateReference")
    reference.Shape = Part.makeLine(
        App.Vector(0.0, -6.0, 0.0),
        App.Vector(0.0, 6.0, 0.0),
    )
    reference.Placement.Base = App.Vector(17.0, -3.0, 1.0)
    diagonal = document.addObject("Part::Feature", "MirrorGateDiagonal")
    diagonal.Shape = Part.makeLine(
        App.Vector(0.0, 0.0, 0.0),
        App.Vector(8.0, 5.0, 0.0),
    )
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    source = _custom(
        job,
        controller,
        "MirrorSource",
        (
            "G0 X1 Y2 Z8",
            "G1 X4 Y2 Z-1 F120",
            "G2 X6 Y4 I0 J2 F120",
            "G3 X4 Y6 I-2 J0 F120",
            "G0 Z8",
        ),
    )
    no_effect = _custom(
        job,
        controller,
        "MirrorNoEffectSource",
        ("G0 X0 Y0 Z2", "G1 X10 Y0 Z-1 F100"),
    )
    assert document.recompute(None, True, True) is not False
    center = App.Vector(2.0, -3.0, 7.0)
    job_path = job.Path
    job_path.Center = center
    job.Path = job_path
    for operation in (source, no_effect):
        operation_path = operation.Path
        operation_path.Center = center
        operation.Path = operation_path
        operation.ViewObject.Visibility = True
    reference.ViewObject.Visibility = False
    diagonal.ViewObject.Visibility = False
    assert source.isValid() and source.Path.Size
    return model, reference, diagonal, job, source, no_effect


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


def _command_signature(command) -> tuple:
    return str(command.Name), tuple(sorted(dict(command.Parameters).items()))


def _assert_axis_transform(source, axis, offset, keep) -> None:
    placed = tuple(PathUtils.getPathWithPlacement(source).Commands or ())
    before = tuple(_command_signature(command) for command in placed)
    path, metadata = MirrorCore.generatePathWithMetadata(
        source,
        MirrorCore.MirrorDefinition(
            axis=axis,
            offset_mm=offset,
            keep_base_path=keep,
        ),
    )
    output = tuple(path.Commands or ())
    mirrored = output[len(placed) :] if keep else output
    if keep:
        assert tuple(_command_signature(command) for command in output[: len(placed)]) == before
    assert len(mirrored) == len(placed)
    for original, transformed in zip(placed, mirrored, strict=True):
        for coordinate, reflect in (
            ("x", axis in ("Y", "XY")),
            ("y", axis in ("X", "XY")),
            ("z", False),
        ):
            value = getattr(original, coordinate)
            actual = getattr(transformed, coordinate)
            if value is None:
                assert actual is None
                continue
            expected = (-value if reflect else value) + offset["xyz".index(coordinate)]
            assert abs(actual - expected) < 1.0e-9
        if original.Name in Constants.GCODE_MOVE_ARC:
            if axis == "XY":
                assert transformed.Name == original.Name
            else:
                assert transformed.Name != original.Name
    assert metadata["resolved_axis"] == axis
    assert tuple(metadata["resolved_offset_mm"]) == offset
    assert metadata["output_command_count"] == len(output)
    assert tuple(path.Center) == tuple(source.Path.Center)
    assert tuple(_command_signature(command) for command in placed) == before


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mirror-")
        save_path = Path(temporary.name) / "native-manufacture-mirror.FCStd"
        document = App.newDocument("NativeManufactureMirrorGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupMirror"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "mirror_dressup",
            "ExactCamJobOperationAndMirrorPlacementDefinition",
            True,
            False,
        )

        model, reference, diagonal, job, source, no_effect_source = _create_fixture(
            document
        )
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
        ledger.begin_run("native-manufacture-mirror-gui")

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
                f"native-manufacture-mirror-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        stale = _arguments(job, source, _model_center(model))
        stale["mirror"]["model"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        invalid_reference = call(
            _arguments(job, source, _reference(diagonal)),
            succeeds=False,
        )
        assert invalid_reference["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"

        no_effect = call(
            _arguments(job, no_effect_source, _origin("x")),
            succeeds=False,
        )
        assert no_effect["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"

        with patch("Path.Dressup.Mirror.MAX_MIRROR_INPUT_COMMANDS", 2):
            workload = call(
                _arguments(job, source, _origin("x")),
                succeeds=False,
            )
        assert workload["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"

        _assert_axis_transform(source, "X", (2.0, 3.0, 4.0), False)
        _assert_axis_transform(source, "Y", (-2.0, 1.0, 0.5), True)
        _assert_axis_transform(source, "XY", (5.0, -7.0, 2.0), False)

        revision_before = state_store.current_revision(document.Uid)
        model_request = _arguments(
            job,
            source,
            _model_center(model, "xy", offset=_offset(1.0, -2.0, 3.0)),
        )
        prepared_model = preflight_mirror_dressup(
            document,
            MirrorDressupSpec(
                label=model_request["label"],
                job=model_request["job"],
                base_operation=model_request["base_operation"],
                mirror=model_request["mirror"],
            ),
        )
        model_bounds = Part.getShape(model, transform=True).BoundBox
        assert prepared_model.resolved_axis == "XY"
        assert prepared_model.resolved_offset_mm == (
            model_bounds.XMin + model_bounds.XMax + 1.0,
            model_bounds.YMin + model_bounds.YMax - 2.0,
            3.0,
        )

        reference_request = _arguments(
            job,
            source,
            _reference(reference, offset=_offset(1.0, 2.0, 3.0), keep=True),
        )
        prepared_reference = preflight_mirror_dressup(
            document,
            MirrorDressupSpec(
                label=reference_request["label"],
                job=reference_request["job"],
                base_operation=reference_request["base_operation"],
                mirror=reference_request["mirror"],
            ),
        )
        reference_bounds = Part.getShape(
            reference,
            "Edge1",
            needSubElement=True,
            transform=True,
        ).BoundBox
        assert prepared_reference.resolved_axis == "Y"
        assert prepared_reference.resolved_offset_mm == (
            2.0 * reference_bounds.XMin + 1.0,
            2.0,
            3.0,
        )
        assert prepared_reference.expected_command_count == 2 * source.Path.Size
        assert state_store.current_revision(document.Uid) == revision_before
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group
        assert _visibility(document) == initial_visibility
        assert copy_configuration_state(source, {}) == source_configuration
        assert persistent_resource_state(source)["path_sha256"] == source_path_sha256

        payload = _arguments(
            job,
            source,
            _model_center(
                model,
                "x",
                offset=_offset(1.25, -2.5, 3.75),
                keep=True,
            ),
            label="Native Mirror Lifecycle",
        )
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_mirror_dressup",
            side_effect=RuntimeError("forced Mirror postcondition failure"),
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
        model_name = str(model.Name)
        job_name = str(job.Name)
        assert isinstance(output.Proxy, MirrorGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, MirrorGui.ViewProviderDressup)
        assert output.Base is source
        assert output.CenterModel is True
        assert output.CenterModelReference is model
        assert str(output.MirrorAxis) == "X"
        assert result["kind"] == "axis_at_model_center"
        assert result["axis"] == "x"
        assert result["offset_mm"] == [1.25, -2.5, 3.75]
        assert result["keep_base_path"] is True
        assert result["arc_direction_swap_count"] == 2
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
        model = document.getObject(model_name)
        source = document.getObject(source_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert output.Base is source and output in job.Operations.Group
        assert output.CenterModelReference is model

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(20)
        document = App.openDocument(str(save_path))
        _events(24)
        model = document.getObject(model_name)
        source = document.getObject(source_name)
        output = document.getObject(output_name)
        job = document.getObject(job_name)
        assert isinstance(output.Proxy, MirrorGui.ObjectDressup)
        assert isinstance(output.ViewObject.Proxy, MirrorGui.ViewProviderDressup)
        assert output.Base is source and output in job.Operations.Group
        assert output.CenterModelReference is model
        assert tuple(output.VibeCADTimelineReplacedInputs) == (source,)
        assert not source.ViewObject.Visibility and output.ViewObject.Visibility
        assert tuple(output.Path.Center) == tuple(job.Path.Center)

        print(
            "VIBECAD_NATIVE_MANUFACTURE_MIRROR_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true closed_schema=true "
            "invalid_reference=true no_effect=true workload_guard=true rollback=true "
            "three_axes=true model_center=true reference=true global_geometry=true "
            "offset=true keep_base=true arc_direction=true rotary_center=true "
            "source_preserved=true replacement=true history=true receipt=true "
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
