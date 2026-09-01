# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM Path Boundary replacements."""

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

import Path.Dressup.Boundary as Boundary
import Path.Dressup.Gui.Boundary as BoundaryGui
import Path.Main.Gui.Job as PathJobGui
import Path.Main.Stock as PathStock
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["path_boundary_dressup"]


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


def _placement(x: float, y: float, z: float) -> dict:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation_axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "rotation_degrees": 0.0,
    }


def _arguments(
    job,
    base,
    boundary,
    *,
    label="Native CAM Path Boundary",
    inside=True,
    offset_mm=0.0,
    retract_threshold_mm=0.0,
    rest_machining_pass=False,
) -> dict:
    return {
        "operation": "path_boundary_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "boundary": boundary,
        "inside": inside,
        "offset_mm": offset_mm,
        "retract_threshold_mm": retract_threshold_mm,
        "rest_machining_pass": rest_machining_pass,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("path_boundary_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "base_operation",
        "expected_state_sha256",
        "boundary",
        "model_bounds",
        "box",
        "cylinder",
        "existing_solid",
        "inside",
        "offset_mm",
        "retract_threshold_mm",
        "rest_machining_pass",
    ):
        assert field in encoded
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
    operation.Label = "Path Boundary source"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    if not hasattr(operation, "SafeHeight"):
        operation.addProperty("App::PropertyLength", "SafeHeight", "Heights")
    if not hasattr(operation, "ClearanceHeight"):
        operation.addProperty("App::PropertyLength", "ClearanceHeight", "Heights")
    operation.SafeHeight = 5.0
    operation.ClearanceHeight = 10.0
    operation.Gcode = [
        "G0 X-10 Y15 Z10",
        "G0 Z0",
        "G1 X50 Y15 Z0 F120",
    ]
    return operation


def _create_fixture(document):
    model = document.addObject("Part::Feature", "PathBoundaryGateModel")
    model.Label = "Path Boundary gate model"
    model.Shape = Part.makeBox(40.0, 30.0, 5.0)
    existing = document.addObject("Part::Feature", "ReusableBoundarySolid")
    existing.Label = "Reusable boundary solid"
    existing.Shape = Part.makeBox(30.0, 30.0, 20.0, App.Vector(5.0, 0.0, -5.0))
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    sources = tuple(
        _custom(job, controller, f"PathBoundarySeed{index}")
        for index in range(5)
    )
    assert document.recompute(None, True, True) is not False
    assert all(source.isValid() and source.Path.Size for source in sources)
    return model, existing, job, controller, sources


def _definitions(existing) -> tuple[tuple[str, dict, bool, float, float, bool], ...]:
    return (
        (
            "model_bounds",
            {
                "kind": "model_bounds",
                "x_negative_mm": 2.0,
                "x_positive_mm": 2.0,
                "y_negative_mm": 1.0,
                "y_positive_mm": 1.0,
                "z_negative_mm": 1.0,
                "z_positive_mm": 1.0,
            },
            True,
            0.0,
            0.0,
            False,
        ),
        (
            "box",
            {
                "kind": "box",
                "length_mm": 30.0,
                "width_mm": 30.0,
                "height_mm": 20.0,
                "placement": _placement(5.0, 0.0, -5.0),
            },
            True,
            1.0,
            6.0,
            True,
        ),
        (
            "cylinder",
            {
                "kind": "cylinder",
                "radius_mm": 14.0,
                "height_mm": 20.0,
                "placement": _placement(20.0, 15.0, -5.0),
            },
            True,
            0.5,
            3.0,
            False,
        ),
        (
            "existing_solid",
            {
                "kind": "existing_solid",
                "source": _target(candidate_model_state(existing)),
            },
            False,
            0.0,
            0.0,
            False,
        ),
    )


def _assert_stock_kind(operation, kind: str, existing) -> None:
    stock = operation.Stock
    assert stock is not None and stock.VibeCADTimelineOwner is operation
    assert stock.IsBoundary and stock.Label.startswith("Boundary")
    if kind == "model_bounds":
        assert isinstance(stock.Proxy, PathStock.StockFromBase)
    elif kind == "box":
        assert isinstance(stock.Proxy, PathStock.StockCreateBox)
    elif kind == "cylinder":
        assert isinstance(stock.Proxy, PathStock.StockCreateCylinder)
    else:
        assert tuple(stock.Objects) == (existing,)
        assert stock.PathResource == "Stock"


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-path-boundary-")
        save_path = Path(temporary.name) / "native-manufacture-path-boundary.FCStd"
        document = App.newDocument("NativeManufacturePathBoundaryGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupPathBoundary"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "path_boundary_dressup",
            "ExactCamJobOperationAndPathBoundaryDefinition",
            True,
            False,
        )

        model, existing, job, controller, sources = _create_fixture(document)
        document.clearUndos()
        source_states = {
            source.Name: (
                copy_configuration_state(source, {}),
                persistent_resource_state(source)["path_sha256"],
                persistent_resource_state(source)["active"],
            )
            for source in sources
        }
        existing_state = candidate_model_state(existing)
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
        ledger.begin_run("native-manufacture-path-boundary-gui")

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
                f"native-manufacture-path-boundary-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)
        definitions = _definitions(existing)

        stale = _arguments(job, sources[0], definitions[0][1])
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        invalid = _arguments(
            job,
            sources[0],
            {
                "kind": "box",
                "length_mm": 0.0,
                "width_mm": 30.0,
                "height_mm": 20.0,
                "placement": _placement(0.0, 0.0, 0.0),
            },
        )
        invalid_result = call(invalid, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        excludes_all = _arguments(
            job,
            sources[0],
            {
                "kind": "box",
                "length_mm": 5.0,
                "width_mm": 5.0,
                "height_mm": 5.0,
                "placement": _placement(200.0, 200.0, 200.0),
            },
        )
        empty_result = call(excludes_all, succeeds=False)
        assert empty_result["error_code"] == "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group

        first_kind, first_boundary, first_inside, first_offset, first_retract, first_rest = (
            definitions[0]
        )
        first_payload = _arguments(
            job,
            sources[0],
            first_boundary,
            label="Native Model Bounds Boundary",
            inside=first_inside,
            offset_mm=first_offset,
            retract_threshold_mm=first_retract,
            rest_machining_pass=first_rest,
        )
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_path_boundary_dressup",
            side_effect=RuntimeError("forced Path Boundary postcondition failure"),
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
        first_stock_name = str(first_output.Stock.Name)
        assert isinstance(first_output.Proxy, Boundary.DressupPathBoundary)
        assert isinstance(first_output.ViewObject.Proxy, BoundaryGui.DressupPathBoundaryViewProvider)
        assert first_output.Base is sources[0]
        _assert_stock_kind(first_output, first_kind, existing)
        assert first_result["boundary_kind"] == first_kind
        assert first_result["command_count"] > 0
        assert first_result["cutting_command_count"] > 0
        assert first_result["intersection_work"] > 0
        assert len(first_result["receipt"]["created"]) == 2
        assert len(first_result["receipt"]["replaced"]) == 1
        assert first_result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert int(document.UndoCount) == 1

        document.undo()
        _events(16)
        assert document.getObject(first_output_name) is None
        assert document.getObject(first_stock_name) is None
        assert tuple(job.Operations.Group) == initial_group
        assert sources[0].ViewObject.Visibility
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        model = document.getObject(model.Name)
        existing = document.getObject(existing.Name)
        sources = tuple(document.getObject(source.Name) for source in sources)
        first_output = document.getObject(first_output_name)
        assert first_output.Base is sources[0]
        _assert_stock_kind(first_output, first_kind, existing)

        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-path-boundary-gui-after-redo")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        outputs = [first_output]
        for index, definition in enumerate(definitions[1:], start=1):
            kind, boundary, inside, offset, retract, rest = definition
            if kind == "existing_solid":
                boundary = {
                    "kind": "existing_solid",
                    "source": _target(candidate_model_state(existing)),
                }
            result = call(
                _arguments(
                    job,
                    sources[index],
                    boundary,
                    label=f"Native {kind} Boundary",
                    inside=inside,
                    offset_mm=offset,
                    retract_threshold_mm=retract,
                    rest_machining_pass=rest,
                )
            )
            output = document.getObject(result["object_name"])
            outputs.append(output)
            assert output.Base is sources[index]
            assert result["boundary_kind"] == kind
            assert result["inside"] is inside
            assert result["offset_mm"] == offset
            assert result["retract_threshold_mm"] == retract
            assert result["rest_machining_pass"] is rest
            _assert_stock_kind(output, kind, existing)

        timeline = document.getObject("VibeCADTimeline")
        for output in outputs:
            index = tuple(timeline.Operations).index(output)
            assert index > 0 and tuple(timeline.Operations)[index - 1] is output.Stock
            assert tuple(output.VibeCADTimelineReplacedInputs) == (output.Base,)

        for source in sources:
            source_after = persistent_resource_state(source)
            assert (
                copy_configuration_state(source, {}),
                source_after["path_sha256"],
                source_after["active"],
            ) == source_states[source.Name], (
                source.Name,
                source_states[source.Name],
                source_after,
            )
        assert candidate_model_state(existing) == existing_state
        assert all(not source.ViewObject.Visibility for source in sources[:4])
        assert all(output.ViewObject.Visibility for output in outputs)
        assert all(not output.Stock.ViewObject.Visibility for output in outputs)
        assert _selection() == selection_before
        for name, visible in visibility_before.items():
            if name not in {source.Name for source in sources[:4]}:
                assert bool(document.getObject(name).ViewObject.Visibility) is visible

        job_name = str(job.Name)
        existing_name = str(existing.Name)
        output_names = tuple(str(output.Name) for output in outputs)
        stock_names = tuple(str(output.Stock.Name) for output in outputs)
        source_names = tuple(str(source.Name) for source in sources)
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(20)
        document = App.openDocument(str(save_path))
        _events(24)
        job = document.getObject(job_name)
        existing = document.getObject(existing_name)
        reopened_outputs = tuple(document.getObject(name) for name in output_names)
        reopened_stocks = tuple(document.getObject(name) for name in stock_names)
        reopened_sources = tuple(document.getObject(name) for name in source_names)
        assert all(
            isinstance(output.Proxy, Boundary.DressupPathBoundary)
            and output.Base is source
            and output.Stock is stock
            and stock.VibeCADTimelineOwner is output
            and output in job.Operations.Group
            for output, stock, source in zip(
                reopened_outputs,
                reopened_stocks,
                reopened_sources,
            )
        )
        assert tuple(reopened_stocks[-1].Objects) == (existing,)
        assert all(not source.ViewObject.Visibility for source in reopened_sources[:4])
        assert all(output.ViewObject.Visibility for output in reopened_outputs)
        assert all(not stock.ViewObject.Visibility for stock in reopened_stocks)

        print(
            "VIBECAD_NATIVE_MANUFACTURE_PATH_BOUNDARY_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true invalid_definition=true "
            "empty_clip=true rollback=true model_bounds=true box=true cylinder=true "
            "existing_solid=true inside_outside=true offset=true retract=true rest=true "
            "owned_stock=true source_preserved=true replacement=true history=true "
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
