# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Area helpers."""

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

import Path.Base.Util as PathTimeline
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureAreaState import area_snapshot, area_state
from VibeCADNativeManufactureAreaSchema import MANUFACTURE_AREA_CAPABILITY_NAME
from VibeCADNativeManufactureState import candidate_model_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_CAM_PREFERENCES = "User parameter:BaseApp/Preferences/Mod/CAM"


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
    area_group = next(group for group in surface.groups if group.label == "Area")
    assert tuple(action.command_id for action in area_group.actions) == (
        "CAM_Area",
        "CAM_Area_Workplane",
    )
    plans = {
        plan.command_id: plan for plan in resolve_native_action_inventory(surface).plans
    }
    assert (
        plans["CAM_Area"].capability_family,
        plans["CAM_Area"].operation_variant,
        plans["CAM_Area"].exact_target_type,
    ) == (
        MANUFACTURE_AREA_CAPABILITY_NAME,
        "create",
        "ExactCurrentPartGeometrySet",
    )
    assert (
        plans["CAM_Area_Workplane"].capability_family,
        plans["CAM_Area_Workplane"].operation_variant,
        plans["CAM_Area_Workplane"].exact_target_type,
    ) == (
        MANUFACTURE_AREA_CAPABILITY_NAME,
        "set_workplane",
        "ExactCurrentFeatureAreaAndPartWorkplane",
    )
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_AREA_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("create", "create_view", "set_workplane"))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 7_000
    parameters = schema["parameters"]
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["operation"]
    assert parameters["properties"]["operation"]["enum"] == [
        "create",
        "create_view",
        "set_workplane",
    ]
    assert parameters["properties"]["operation"]["description"] == (
        "Fields: create=label,sources; create_view=label,area; "
        "set_workplane=area,workplane."
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MANUFACTURE_AREA_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _model_target(model) -> dict[str, str]:
    state = candidate_model_state(model)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _area_target(area) -> dict[str, str]:
    state = area_state(area)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visibility(document) -> tuple[tuple[str, bool], ...]:
    return tuple(
        (obj.Name, bool(obj.ViewObject.Visibility))
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    )


def _timeline(document) -> tuple:
    timeline = document.getObject("VibeCADTimeline")
    return (
        tuple(obj.Name for obj in timeline.Operations),
        tuple(bool(value) for value in timeline.VisibilityAtEnd),
        tuple(bool(value) for value in timeline.SuppressionAtEnd),
        int(timeline.Position),
    )


def _publish_fixture(document, obj) -> None:
    PathTimeline.markTimelineOperation(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = App.ParamGet(_CAM_PREFERENCES)
    preference_present = "EnableExperimentalFeatures" in tuple(preferences.GetBools())
    preference_before = bool(preferences.GetBool("EnableExperimentalFeatures", False))
    exit_code = 1
    try:
        preferences.SetBool("EnableExperimentalFeatures", True)
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-area-")
        save_path = Path(temporary.name) / "native-manufacture-area.FCStd"
        document = App.newDocument("NativeManufactureAreaGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()

        document.openTransaction("Create Area fixtures")
        model = document.addObject("Part::Feature", "AreaPlate")
        model.Label = "Area Plate"
        model.Shape = Part.makeBox(30.0, 20.0, 5.0)
        _publish_fixture(document, model)
        plane = document.addObject("Part::Feature", "AreaPlane")
        plane.Label = "Area Plane"
        plane.Shape = Part.makePlane(18.0, 12.0)
        _publish_fixture(document, plane)
        document.commitTransaction()
        document.recompute()
        model_name = str(model.Name)
        document.saveAs(str(save_path))
        document.clearUndos()

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-area-gui")

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
                MANUFACTURE_AREA_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-area-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)
        timeline_before = _timeline(document)
        objects_before = tuple(document.Objects)
        undo_before = int(document.UndoCount)

        stale = {
            "operation": "create",
            "label": "Stale Area",
            "sources": [
                {
                    "kind": "whole_shape",
                    "model": {
                        "object_name": plane.Name,
                        "expected_state_sha256": "0" * 64,
                    },
                }
            ],
        }
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        duplicate_target = _model_target(model)
        duplicate = {
            "operation": "create",
            "label": "Duplicate Area",
            "sources": [
                {"kind": "subelement", "model": duplicate_target, "name": "Face1"},
                {"kind": "subelement", "model": duplicate_target, "name": "Face1"},
            ],
        }
        duplicate_result = call(duplicate, succeeds=False)
        assert duplicate_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        create_arguments = {
            "operation": "create",
            "label": "Native Profile Area",
            "sources": [
                {
                    "kind": "subelement",
                    "model": _model_target(model),
                    "name": "Face1",
                }
            ],
        }
        document.openTransaction("Caller-owned Area transaction")
        transaction = int(document.getBookedTransactionID())
        blocked = call(create_arguments, succeeds=False)
        assert blocked["error_code"] == "NATIVE_TRANSACTION_ACTIVE"
        App.closeActiveTransaction(True, transaction)

        with patch(
            "VibeCADNativeManufactureAreaRuntime.verify_created_area",
            side_effect=RuntimeError("forced Area verification failure"),
        ):
            rolled_back = call(create_arguments, succeeds=False)
        assert rolled_back["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == objects_before
        assert _timeline(document) == timeline_before
        assert _selection() == selection_before
        assert _visibility(document) == visibility_before
        assert int(document.UndoCount) == undo_before

        created = call(create_arguments)
        area = document.getObject(created["object_name"])
        area_name = str(area.Name)
        resource_name = str(area.Sources[0].Name)
        assert str(area.TypeId) == "Path::FeatureArea"
        assert str(area.Label) == "Native Profile Area"
        assert len(area.Sources) == 1
        resource = area.Sources[0]
        source, subelements = resource.Source
        assert source is model
        assert tuple(subelements) == ("Face1",)
        assert resource.VibeCADTimelineOwner is area
        assert created["resource_count"] == 1
        assert created["source_selections"] == [
            {"object_name": model.Name, "subelement": "Face1"}
        ]
        assert len(created["receipt"]["created"]) == 2
        assert created["receipt"]["changed"] == []
        assert created["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert _visibility(document)[: len(visibility_before)] == visibility_before

        view_result = call(
            {
                "operation": "create_view",
                "label": "Native Area View",
                "area": _area_target(area),
            }
        )
        view = document.getObject(view_result["object_name"])
        view_name = str(view.Name)
        assert str(view.TypeId) == "Path::FeatureAreaView"
        assert view.Source is area
        assert not area.ViewObject.Visibility
        assert view_result["source_area"]["object_name"] == area.Name
        assert len(view_result["receipt"]["created"]) == 1
        assert len(view_result["receipt"]["replaced"]) == 1

        area.ViewObject.Visibility = False
        visibility_before_workplane = _visibility(document)
        workplane_result = call(
            {
                "operation": "set_workplane",
                "area": _area_target(area),
                "workplane": {
                    "kind": "subelement",
                    "model": _model_target(model),
                    "name": "Edge1",
                },
            }
        )
        workplane_source, workplane_subelements = area.WorkPlaneSource
        assert workplane_source is model
        assert tuple(workplane_subelements) == ("Edge1",)
        assert area.WorkPlaneSourceCollection == "Wires"
        assert area.WorkPlaneSourceEnabled
        assert not area.WorkPlane.isNull()
        assert area.ViewObject.Visibility
        assert workplane_result["workplane_source"]["object_name"] == model.Name
        assert workplane_result["workplane_source"]["subelement"] == "Edge1"
        assert workplane_result["receipt"]["created"] == []
        assert len(workplane_result["receipt"]["changed"]) == 1
        assert _selection() == selection_before
        visibility_after_workplane = dict(_visibility(document))
        for name, visible in visibility_before_workplane:
            if name != area.Name:
                assert visibility_after_workplane[name] is visible

        snapshot = area_snapshot(document)
        assert snapshot["area_count"] == 1
        assert snapshot["area_view_count"] == 1
        assert snapshot["areas"][0]["workplane"]["subelement"] == "Edge1"
        snapshot_json = json.dumps(snapshot, sort_keys=True)
        assert "exportBrep" not in snapshot_json

        document.undo()
        _events(12)
        area = document.getObject(area_name)
        assert not area.WorkPlaneSourceEnabled
        document.undo()
        _events(12)
        assert document.getObject(view_name) is None
        document.undo()
        _events(12)
        assert document.getObject(area_name) is None
        assert document.getObject(resource_name) is None
        document.redo()
        document.redo()
        document.redo()
        _events(20)
        area = document.getObject(area_name)
        view = document.getObject(view_name)
        assert area is not None and view is not None
        assert area.WorkPlaneSourceEnabled
        assert view.Source is area

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(16)
        document = App.openDocument(str(save_path))
        _events(20)
        area = document.getObject(area_name)
        view = document.getObject(view_name)
        resource = document.getObject(resource_name)
        assert area is not None and view is not None and resource is not None
        assert area.isValid() and view.isValid() and resource.isValid()
        assert view.Source is area
        source, subelements = resource.Source
        assert source.Name == model_name
        assert tuple(subelements) == ("Face1",)
        reopened = area_snapshot(document)
        assert reopened["area_count"] == 1
        assert reopened["area_view_count"] == 1
        assert reopened["areas"][0]["workplane"]["subelement"] == "Edge1"

        print(
            "VIBECAD_NATIVE_MANUFACTURE_AREA_GUI_OK "
            "ribbon=true closed_schema=true create=true subshape_resource=true "
            "view=true workplane=true exact_targets=true stale=true duplicate_guard=true "
            "transaction_guard=true rollback=true history=true receipt=true low_noise=true "
            "snapshot=true selection=true visibility=true undo=true redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        if preference_present:
            preferences.SetBool("EnableExperimentalFeatures", preference_before)
        else:
            preferences.RemBool("EnableExperimentalFeatures")
        if application is not None:
            application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
