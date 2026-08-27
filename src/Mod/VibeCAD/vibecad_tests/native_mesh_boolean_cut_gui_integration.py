# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Mesh booleans, cuts, and sections."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import MeshGui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshBooleanSchema import MESH_BOOLEAN_CAPABILITY_NAME
from VibeCADNativeMeshCutSchema import MESH_CUT_CAPABILITY_NAME
from VibeCADNativeMeshSnapshot import build_mesh_snapshot
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from native_mesh_modify_gui_support import add_source


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
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    boolean = registry.definition(MESH_BOOLEAN_CAPABILITY_NAME)
    cut = registry.definition(MESH_CUT_CAPABILITY_NAME)
    assert boolean is not None and cut is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MESH_BOOLEAN_CAPABILITY_NAME, MESH_CUT_CAPABILITY_NAME),
            schemas=(
                boolean.provider_schema(("union", "intersection", "difference")),
                cut.provider_schema(
                    (
                        "poly_cut",
                        "poly_trim",
                        "trim_by_plane",
                        "section_by_plane",
                        "cross_sections",
                    )
                ),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _box(x: float) -> object:
    mesh = Mesh.createBox(10.0, 10.0, 10.0)
    mesh.Placement = App.Placement(App.Vector(x, 0.0, 0.0), App.Rotation())
    return mesh


def _sphere(x: float) -> object:
    mesh = Mesh.createSphere(10.0, 32)
    mesh.Placement = App.Placement(App.Vector(x, 0.0, 0.0), App.Rotation())
    return mesh


def _exact(obj) -> dict:
    return {
        "object_name": obj.Name,
        "expected_state_sha256": mesh_object_state(obj)["state_sha256"],
    }


def _add_plane(document):
    document.openTransaction("Create exact section plane")
    try:
        plane = document.addObject("Part::Plane", "SectionPlane")
        plane.Label = "Exact Section Plane"
        plane.Length = 260.0
        plane.Width = 40.0
        plane.Placement = App.Placement(App.Vector(0.0, 0.0, 0.0), App.Rotation())
        assert document.recompute([plane], True, True) is not False
        document.publishProvisionalTimelineOperationBlock(plane, (), ())
        document.commitTransaction()
        return plane
    except Exception:
        document.abortTransaction()
        raise


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-boolean-cut-")
        save_path = Path(temporary.name) / "native-mesh-boolean-cut.FCStd"
        document = App.newDocument("NativeMeshBooleanCutGate")
        document.UndoMode = 1
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
        ledger.begin_run("native-mesh-boolean-cut-gui")

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
            started = time.monotonic()
            response = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-boolean-cut-{call_number}",
            )
            dispatch_ms = int((time.monotonic() - started) * 1000)
            assert response.get("ok") is succeeds, response
            job = response.get("job")
            if succeeds and name == MESH_CUT_CAPABILITY_NAME:
                assert dispatch_ms < 250, dispatch_ms
                assert isinstance(job, dict), (arguments["operation"], response)
            if succeeds and isinstance(job, dict):
                deadline = time.monotonic() + 120.0
                while time.monotonic() < deadline:
                    _process_events(2)
                    snapshot = service.native_background_manager().snapshot(job["job_id"])
                    if snapshot.terminal:
                        assert snapshot.phase == "completed", snapshot.error
                        return {"ok": True, **dict(snapshot.result or {})}
                    time.sleep(0.01)
                raise AssertionError("Native Mesh boolean background job did not finish")
            return response

        sources = {
            "union_a": add_source(document, "UnionA", _box(0.0)),
            "union_b": add_source(document, "UnionB", _box(5.0)),
            "intersection_a": add_source(document, "IntersectionA", _box(25.0)),
            "intersection_b": add_source(document, "IntersectionB", _box(30.0)),
            "difference_a": add_source(document, "DifferenceA", _box(50.0)),
            "difference_b": add_source(document, "DifferenceB", _box(55.0)),
            "poly_cut": add_source(document, "PolygonCutSource", _sphere(80.0)),
            "poly_trim": add_source(document, "PolygonTrimSource", _sphere(105.0)),
            "plane_trim": add_source(document, "PlaneTrimSource", _sphere(130.0)),
            "plane_section": add_source(document, "PlaneSectionSource", _sphere(155.0)),
            "cross_a": add_source(document, "CrossSectionA", _sphere(180.0)),
            "cross_b": add_source(document, "CrossSectionB", _sphere(205.0)),
        }
        plane = _add_plane(document)
        snapshot = build_mesh_snapshot(document)
        assert snapshot["counts"]["datum_plane"] == 1
        assert any(item["object_name"] == plane.Name for item in snapshot["objects"])
        # Fixture creation is deliberately outside the frozen Native turn.
        # Recreate the dispatcher at the document revision the model reads.
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        stale = call(
            MESH_BOOLEAN_CAPABILITY_NAME,
            {
                "operation": "union",
                "first": {
                    "object_name": sources["union_a"].Name,
                    "expected_state_sha256": "0" * 64,
                },
                "second": _exact(sources["union_b"]),
                "result_label": "Stale Union",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_MESH_STATE_STALE", stale

        result_names = []
        for operation, first, second in (
            ("union", "union_a", "union_b"),
            ("intersection", "intersection_a", "intersection_b"),
            ("difference", "difference_a", "difference_b"),
        ):
            response = call(
                MESH_BOOLEAN_CAPABILITY_NAME,
                {
                    "operation": operation,
                    "first": _exact(sources[first]),
                    "second": _exact(sources[second]),
                    "result_label": f"Exact Mesh {operation.title()}",
                },
            )
            result = document.getObject(response["result"]["object_name"])
            assert result.TypeId == "MeshPart::Boolean"
            assert result.Mesh.isSolid()
            result_names.append(result.Name)

        polygons = {
            "poly_cut": [
                {"x_mm": 77.0, "y_mm": -3.0, "z_mm": 0.0},
                {"x_mm": 83.0, "y_mm": -3.0, "z_mm": 0.0},
                {"x_mm": 83.0, "y_mm": 3.0, "z_mm": 0.0},
                {"x_mm": 77.0, "y_mm": 3.0, "z_mm": 0.0},
            ],
            "poly_trim": [
                {"x_mm": 99.0, "y_mm": -6.0, "z_mm": 0.0},
                {"x_mm": 111.0, "y_mm": -6.0, "z_mm": 0.0},
                {"x_mm": 111.0, "y_mm": 6.0, "z_mm": 0.0},
                {"x_mm": 99.0, "y_mm": 6.0, "z_mm": 0.0},
            ],
        }
        poly_cut = call(
            MESH_CUT_CAPABILITY_NAME,
            {
                "operation": "poly_cut",
                "target": _exact(sources["poly_cut"]),
                "polygon": polygons["poly_cut"],
                "result": {"mode": "remove_inside", "result_label": "Facet Polygon Cut"},
            },
        )
        result_names.extend(item["object_name"] for item in poly_cut["outputs"])
        poly_trim = call(
            MESH_CUT_CAPABILITY_NAME,
            {
                "operation": "poly_trim",
                "target": _exact(sources["poly_trim"]),
                "polygon": polygons["poly_trim"],
                "result": {
                    "mode": "split",
                    "inside_result_label": "Trimmed Polygon Inside",
                    "outside_result_label": "Trimmed Polygon Outside",
                },
            },
        )
        assert "operation_controller" in poly_trim
        result_names.extend(item["object_name"] for item in poly_trim["outputs"])

        objects_before_split = {obj.Name for obj in document.Objects}
        undo_before_split = document.UndoCount
        plane_trim = call(
            MESH_CUT_CAPABILITY_NAME,
            {
                "operation": "trim_by_plane",
                "target": _exact(sources["plane_trim"]),
                "plane": _exact(plane),
                "result": {
                    "mode": "split",
                    "below_result_label": "Plane Below",
                    "above_result_label": "Plane Above",
                },
            },
        )
        assert document.UndoCount == undo_before_split + 1
        split_names = [item["object_name"] for item in plane_trim["outputs"]]
        split_names.append(plane_trim["operation_controller"]["object_name"])
        result_names.extend(split_names)
        document.undo()
        assert {obj.Name for obj in document.Objects} == objects_before_split
        document.redo()
        _process_events(10)
        assert all(document.getObject(name) is not None for name in split_names)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        section = call(
            MESH_CUT_CAPABILITY_NAME,
            {
                "operation": "section_by_plane",
                "target": _exact(sources["plane_section"]),
                "plane": _exact(plane),
                "result_label": "Exact Plane Section",
                "settings": {"minimum_length_mm": 1.0e-7, "connect_edges": True},
            },
        )
        section_name = section["result"]["object_name"]
        assert document.getObject(section_name).TypeId == "MeshPart::SectionByPlane"
        result_names.append(section_name)

        cross = call(
            MESH_CUT_CAPABILITY_NAME,
            {
                "operation": "cross_sections",
                "targets": [
                    {**_exact(sources["cross_a"]), "label": "Cross Sections A"},
                    {**_exact(sources["cross_b"]), "label": "Cross Sections B"},
                ],
                "planes": {
                    "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
                    "positions_mm": [-4.0, 0.0, 4.0],
                },
                "settings": {"epsilon_mm": 1.0e-7, "connect_edges": True},
            },
        )
        assert "operation_controller" in cross
        result_names.extend(item["object_name"] for item in cross["outputs"])
        result_names.append(cross["operation_controller"]["object_name"])

        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        assert all(document.getObject(name) is not None for name in result_names)
        document.recompute()
        assert all(document.getObject(name).isValid() for name in result_names)

        print(
            "VIBECAD_NATIVE_MESH_BOOLEAN_CUT_GUI_OK "
            "booleans=3 cuts=5 stale=true polygon_model_space=true "
            "plane_context=true undo_redo=true reopen=true",
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
