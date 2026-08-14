# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for every Native Mesh Points ribbon action."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import MeshGui
import Part
import Points
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeMeshExportSchema import MESH_EXPORT_CAPABILITY_NAME
from VibeCADNativeMeshPointsSchema import MESH_POINTS_CAPABILITY_NAME
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


POINT_ACTIONS = frozenset(
    {
        "Points_Import",
        "Points_Export",
        "Points_Convert",
        "Points_Structure",
        "Points_Merge",
        "Points_PolyCut",
    }
)
POINT_OPERATIONS = (
    "import_point_cloud",
    "convert_to_points",
    "structure",
    "merge",
    "polygon_cut",
)


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_mesh_ribbon(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "MeshWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "mesh"
    assert POINT_ACTIONS <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    jobs = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    points = registry.definition(MESH_POINTS_CAPABILITY_NAME)
    export = registry.definition(MESH_EXPORT_CAPABILITY_NAME)
    assert jobs is not None and points is not None and export is not None
    covered = {
        action
        for variant in (*points.variants, *export.variants)
        for action in variant.action_ids
        if variant.operation in (*POINT_OPERATIONS, "export_point_cloud")
    }
    assert covered == POINT_ACTIONS
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                NATIVE_BACKGROUND_CAPABILITY_NAME,
                MESH_POINTS_CAPABILITY_NAME,
                MESH_EXPORT_CAPABILITY_NAME,
            ),
            schemas=(
                jobs.provider_schema(("status", "cancel")),
                points.provider_schema(POINT_OPERATIONS),
                export.provider_schema(("export_point_cloud",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _add_cloud(document, name: str, coordinates, *, offset: float = 0.0):
    obj = document.addObject("Points::Feature", name)
    obj.Label = name
    kernel = Points.Points()
    kernel.addPoints([App.Vector(*point) for point in coordinates])
    obj.Points = kernel
    obj.Placement = App.Placement(App.Vector(offset, 0, 0), App.Rotation())
    count = len(coordinates)
    obj.addProperty("Points::PropertyGreyValueList", "Intensity")
    obj.Intensity = [float(index + 1) for index in range(count)]
    obj.addProperty("App::PropertyColorList", "Color")
    obj.Color = [
        (float(index % 2), float((index + 1) % 2), 0.25, 1.0)
        for index in range(count)
    ]
    obj.addProperty("Points::PropertyNormalList", "Normal")
    obj.Normal = [(0.0, 0.0, 1.0)] * count
    document.recompute()
    return obj


def _point_target(obj) -> dict:
    state = mesh_object_state(obj)
    return {
        "object_name": obj.Name,
        "expected_state_sha256": state["state_sha256"],
        "expected_point_count": state["topology"]["points"],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-points-")
        root = Path(temporary.name)
        save_path = root / "native-mesh-points.FCStd"
        output_path = root / "exported-points.ply"
        document = App.newDocument("NativeMeshPointsGate")
        document.UndoMode = 1
        shape = document.addObject("Part::Feature", "GeometrySource")
        shape.Label = "Geometry Source"
        shape.Shape = Part.makeBox(4.0, 3.0, 2.0)
        shape_two = document.addObject("Part::Feature", "SecondGeometrySource")
        shape_two.Label = "Second Geometry Source"
        shape_two.Shape = Part.makeCylinder(1.5, 4.0)
        grid = _add_cloud(
            document,
            "GridSource",
            ((0, 0, 0), (1, 0, 1), (0, 1, 2), (1, 1, 3)),
            offset=10.0,
        )
        second = _add_cloud(
            document,
            "SecondSource",
            ((0, 0, 4), (1, 0, 5), (0, 1, 6), (1, 1, 7)),
            offset=-5.0,
        )
        cut_source = _add_cloud(
            document,
            "CutSource",
            ((-2, 0, 0), (-0.25, 0, 0), (0.25, 0, 0), (2, 0, 0)),
        )
        document.recompute()
        document.saveAs(str(save_path))

        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller, surface = _select_mesh_ribbon(Gui.getMainWindow())
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-mesh-points-gui")
        authorizations = {"input": 0, "output": 0}

        def input_authorizer(request):
            authorizations["input"] += 1
            return authorize_native_input_path(request, output_path)

        def output_authorizer(request):
            authorizations["output"] += 1
            return authorize_native_output_path(request, output_path)

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            authorize_input=input_authorizer,
            authorize_output=output_authorizer,
            background_manager=service.native_background_manager(),
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def call(name: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-points-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def wait_for_job(started: dict) -> dict:
            job_id = started["job"]["job_id"]
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                _process_events(2)
                snapshot = context.background_manager.snapshot(job_id)
                if snapshot.terminal:
                    return call(
                        NATIVE_BACKGROUND_CAPABILITY_NAME,
                        {"operation": "status", "job_id": job_id},
                    )["job"]
                time.sleep(0.01)
            raise AssertionError(f"Background point-cloud job {job_id} did not finish")

        initial_undo = int(document.UndoCount)
        shape_state = mesh_object_state(shape)
        shape_two_state = mesh_object_state(shape_two)
        converted = wait_for_job(
            call(
                MESH_POINTS_CAPABILITY_NAME,
                {
                    "operation": "convert_to_points",
                    "geometry_sources": [
                        {
                            "object_name": shape.Name,
                            "expected_state_sha256": shape_state["state_sha256"],
                            "label": "Sampled Box",
                        },
                        {
                            "object_name": shape_two.Name,
                            "expected_state_sha256": shape_two_state["state_sha256"],
                            "label": "Sampled Cylinder",
                        },
                    ],
                    "maximum_distance_mm": 0.5,
                },
            )
        )
        assert converted["phase"] == "completed", converted
        converted_objects = [
            document.getObject(item["object_name"])
            for item in converted["result"]["outputs"]
        ]
        assert len(converted_objects) == 2
        assert all(obj is not None and obj.Points.CountPoints > 8 for obj in converted_objects)
        assert [obj.Source for obj in converted_objects] == [shape, shape_two]
        converted_group = document.getObject(
            converted["result"]["operation_controller"]["object_name"]
        )
        assert tuple(converted_group.Group) == tuple(converted_objects)

        ui_dispatched = {"value": False}
        QtCore.QTimer.singleShot(0, lambda: ui_dispatched.__setitem__("value", True))
        structured = wait_for_job(
            call(
                MESH_POINTS_CAPABILITY_NAME,
                {
                    "operation": "structure",
                    "target": _point_target(grid),
                    "result_label": "Structured Grid",
                    "coordinate_tolerance_mm": 0.01,
                },
            )
        )
        assert ui_dispatched["value"] is True
        assert structured["phase"] == "completed", structured
        structured_obj = document.getObject(structured["result"]["outputs"][0]["object_name"])
        assert structured_obj.TypeId == "Points::Structured"
        assert (int(structured_obj.Width), int(structured_obj.Height)) == (2, 2)
        assert len(structured_obj.Intensity) == 4 and structured_obj.Source is grid

        merged = wait_for_job(
            call(
                MESH_POINTS_CAPABILITY_NAME,
                {
                    "operation": "merge",
                    "point_clouds": [_point_target(grid), _point_target(second)],
                    "result_label": "Merged In Document Coordinates",
                },
            )
        )
        assert merged["phase"] == "completed", merged
        merged_obj = document.getObject(merged["result"]["outputs"][0]["object_name"])
        assert merged_obj.Points.CountPoints == 8
        assert tuple(merged_obj.Sources) == (grid, second)
        assert len(merged_obj.Intensity) == len(merged_obj.Color) == len(merged_obj.Normal) == 8
        assert merged_obj.Points.BoundBox.XMin < -4.9
        assert merged_obj.Points.BoundBox.XMax > 10.9

        stale = call(
            MESH_POINTS_CAPABILITY_NAME,
            {
                "operation": "polygon_cut",
                "target": {
                    **_point_target(cut_source),
                    "expected_state_sha256": "0" * 64,
                },
                "polygon": [
                    {"x_mm": -1, "y_mm": -1, "z_mm": 0},
                    {"x_mm": 1, "y_mm": -1, "z_mm": 0},
                    {"x_mm": 1, "y_mm": 1, "z_mm": 0},
                    {"x_mm": -1, "y_mm": 1, "z_mm": 0},
                ],
                "result": {
                    "mode": "split",
                    "inside_result_label": "Inside Points",
                    "outside_result_label": "Outside Points",
                },
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_POINT_CLOUD_STATE_STALE"
        cut = wait_for_job(
            call(
                MESH_POINTS_CAPABILITY_NAME,
                {
                    "operation": "polygon_cut",
                    "target": _point_target(cut_source),
                    "polygon": [
                        {"x_mm": -1, "y_mm": -1, "z_mm": 0},
                        {"x_mm": 1, "y_mm": -1, "z_mm": 0},
                        {"x_mm": 1, "y_mm": 1, "z_mm": 0},
                        {"x_mm": -1, "y_mm": 1, "z_mm": 0},
                    ],
                    "result": {
                        "mode": "split",
                        "inside_result_label": "Inside Points",
                        "outside_result_label": "Outside Points",
                    },
                },
            )
        )
        assert cut["phase"] == "completed", cut
        cut_outputs = [document.getObject(item["object_name"]) for item in cut["result"]["outputs"]]
        assert [int(obj.Points.CountPoints) for obj in cut_outputs] == [2, 2]
        assert not cut_source.Visibility

        before_export_revision = state.current_revision(str(document.Uid))
        before_export_undo = int(document.UndoCount)
        stale_export = call(
            MESH_EXPORT_CAPABILITY_NAME,
            {
                "operation": "export_point_cloud",
                "target": {"object_name": merged_obj.Name},
                "expected_state_sha256": "0" * 64,
                "expected_point_count": 8,
                "format": "ply",
            },
            succeeds=False,
        )
        assert stale_export["error_code"] == "NATIVE_POINT_CLOUD_STATE_STALE"
        assert authorizations["output"] == 0
        merged_state = mesh_object_state(merged_obj)
        exported = wait_for_job(
            call(
                MESH_EXPORT_CAPABILITY_NAME,
                {
                    "operation": "export_point_cloud",
                    "target": {"object_name": merged_obj.Name},
                    "expected_state_sha256": merged_state["state_sha256"],
                    "expected_point_count": 8,
                    "format": "ply",
                },
            )
        )
        assert exported["phase"] == "completed" and output_path.is_file(), exported
        assert state.current_revision(str(document.Uid)) == before_export_revision
        assert int(document.UndoCount) == before_export_undo
        assert str(root) not in json.dumps(exported["result"], sort_keys=True)

        imported = wait_for_job(
            call(MESH_POINTS_CAPABILITY_NAME, {"operation": "import_point_cloud"})
        )
        assert imported["phase"] == "completed", imported
        imported_obj = document.getObject(imported["result"]["imported"]["object_name"])
        assert imported_obj is not None and imported_obj.Points.CountPoints == 8
        assert all(name in imported_obj.PropertiesList for name in ("Intensity", "Color", "Normal"))
        assert (
            len(imported_obj.Intensity)
            == len(imported_obj.Color)
            == len(imported_obj.Normal)
            == 8
        )
        assert imported_obj.VibeCADTimelineRole == "operation"
        assert list(imported_obj.VibeCADExternalInputs) == [output_path.name]
        assert str(root) not in json.dumps(imported["result"], sort_keys=True)
        assert authorizations == {"input": 1, "output": 1}
        assert int(document.UndoCount) == initial_undo + 5

        imported_name = imported_obj.Name
        history_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        document.undo()
        assert document.getObject(imported_name) is None
        document.redo()
        assert document.getObject(imported_name) is not None
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened = document.getObject(imported_name)
        assert reopened is not None and reopened.Points.CountPoints == 8
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == history_names
        assert all(obj.isValid() for obj in document.Objects)

        print(
            "VIBECAD_NATIVE_MESH_POINTS_GUI_OK actions=6 background=true "
            "attributes=true model_polygon=true io=true undo_redo=true reopen=true",
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
