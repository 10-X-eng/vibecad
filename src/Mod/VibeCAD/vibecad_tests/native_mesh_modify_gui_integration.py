# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact retained Native Mesh modifications."""

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
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshComponents import mesh_components
from VibeCADNativeMeshModifySchema import MESH_MODIFY_CAPABILITY_NAME
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from native_mesh_modify_gui_support import (
    add_sources,
    open_tetrahedron,
    point_index,
    smoothing_patch,
    tetrahedron,
    two_components,
    write_fake_gmsh,
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
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    jobs = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    modify = registry.definition(MESH_MODIFY_CAPABILITY_NAME)
    assert jobs is not None and modify is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(NATIVE_BACKGROUND_CAPABILITY_NAME, MESH_MODIFY_CAPABILITY_NAME),
            schemas=(
                jobs.provider_schema(("status", "cancel")),
                modify.provider_schema(
                    (
                        "harmonize_normals",
                        "flip_normals",
                        "fill_holes",
                        "fill_boundary",
                        "add_triangle",
                        "remove_components",
                        "smooth",
                        "gmsh_remesh",
                        "decimate",
                        "scale",
                    )
                ),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(source, label: str) -> dict:
    return {
        "object_name": source.Name,
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "label": label,
    }


def _result_object(document, response: dict, index: int = 0):
    name = response["outputs"][index]["result"]["object_name"]
    result = document.getObject(name)
    assert result is not None
    return result


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    gmsh_preferences = None
    prior_gmsh = ""
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-modify-")
        root = Path(temporary.name)
        save_path = root / "native-mesh-modify.FCStd"
        fake_gmsh = root / "fake-gmsh"
        write_fake_gmsh(fake_gmsh)
        gmsh_preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Mesh/Meshing"
        )
        prior_gmsh = gmsh_preferences.GetString("gmshExe", "")
        gmsh_preferences.SetString("gmshExe", str(fake_gmsh))

        document = App.newDocument("NativeMeshModifyGate")
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
        ledger.begin_run("native-mesh-modify-gui")

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
            response = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-modify-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        sources = add_sources(
            document,
            (
                ("HarmonizeSource", tetrahedron(inconsistent=True)),
                ("FlipSourceA", tetrahedron()),
                ("FlipSourceB", tetrahedron(12.0)),
                ("FillHolesSource", open_tetrahedron()),
                ("FillBoundarySource", open_tetrahedron(12.0)),
                ("AddTriangleSource", open_tetrahedron(24.0)),
                ("RemoveBySizeSource", two_components()),
                ("RemoveByIdSource", two_components()),
                ("SmoothSource", smoothing_patch()),
                ("DecimateSource", Mesh.createSphere(8.0, 24)),
                ("ScaleSource", tetrahedron()),
                ("GmshSource", tetrahedron()),
            ),
        )
        stale = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "flip_normals",
                "targets": [
                    {
                        "object_name": sources["FlipSourceA"].Name,
                        "expected_state_sha256": "0" * 64,
                        "label": "Stale Flip",
                    }
                ],
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_MESH_STATE_STALE"
        assert "current_state_sha256" in stale["repair"]

        harmonized = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "harmonize_normals",
                "targets": [_target(sources["HarmonizeSource"], "Harmonized Mesh")],
            },
        )
        harmonized_result = _result_object(document, harmonized)
        assert harmonized_result.TypeId == "Mesh::HarmonizeNormals"
        assert harmonized_result.Mesh.countNonUniformOrientedFacets() == 0

        flipped = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "flip_normals",
                "targets": [
                    _target(sources["FlipSourceA"], "Flipped Mesh A"),
                    _target(sources["FlipSourceB"], "Flipped Mesh B"),
                ],
            },
        )
        flip_results = tuple(_result_object(document, flipped, index) for index in range(2))
        flip_group = document.getObject(flipped["operation_controller"]["object_name"])
        assert flip_group is not None and flip_group.TypeId == "Mesh::OutputGroup"
        assert tuple(flip_group.Group) == flip_results
        assert all(result.VibeCADTimelineOwner is flip_group for result in flip_results)
        flip_group_name = flip_group.Name
        document.undo()
        assert document.getObject(flip_group_name) is None
        assert all(source.Visibility for source in (sources["FlipSourceA"], sources["FlipSourceB"]))
        document.redo()
        flip_group = document.getObject(flip_group_name)
        assert flip_group is not None and len(flip_group.Group) == 2

        filled = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "fill_holes",
                "targets": [_target(sources["FillHolesSource"], "Filled Holes")],
                "maximum_boundary_edges": 3,
            },
        )
        assert _result_object(document, filled).Mesh.CountFacets == 4

        boundary = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "fill_boundary",
                "target": _target(sources["FillBoundarySource"], "Filled Boundary"),
                "seed_facet_index": 0,
                "refinement_level": 0,
            },
        )
        assert _result_object(document, boundary).Mesh.CountFacets > 3

        add_source = sources["AddTriangleSource"]
        add_indices = [
            point_index(add_source.Mesh, coordinate)
            for coordinate in ((24.0, 7.0, 0.0), (32.0, 0.0, 0.0), (24.0, 0.0, 6.0))
        ]
        added = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "add_triangle",
                "target": _target(add_source, "Added Triangle"),
                "point_indices": add_indices,
            },
        )
        added_result = _result_object(document, added)
        assert added_result.Mesh.CountFacets == 4 and added_result.Mesh.isSolid()

        removed_size = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "remove_components",
                "target": _target(sources["RemoveBySizeSource"], "Removed Small Component"),
                "selection": {"kind": "maximum_facets", "maximum_facets": 1},
            },
        )
        assert _result_object(document, removed_size).Mesh.CountFacets == 4
        id_source = sources["RemoveByIdSource"]
        components = mesh_components(id_source.Mesh)
        small_id = next(item.component_id for item in components if len(item.facet_indices) == 1)
        removed_id = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "remove_components",
                "target": _target(id_source, "Removed Exact Component"),
                "selection": {"kind": "component_ids", "component_ids": [small_id]},
            },
        )
        assert _result_object(document, removed_id).Mesh.CountFacets == 4

        smooth_source = sources["SmoothSource"]
        center = point_index(smooth_source.Mesh, (5.0, 5.0, 4.0))
        smoothed = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "smooth",
                "targets": [
                    {
                        **_target(smooth_source, "Smoothed Center"),
                        "selection": {
                            "kind": "point_ranges",
                            "ranges": [{"first_index": center, "last_index": center}],
                        },
                    }
                ],
                "settings": {"method": "laplace", "iterations": 1, "lambda": 0.5},
            },
        )
        smoothed_result = _result_object(document, smoothed)
        assert tuple(smoothed_result.PointIndices) == (center,)

        decimate_source = sources["DecimateSource"]
        target_facets = int(decimate_source.Mesh.CountFacets * 0.65)
        decimated = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "decimate",
                "targets": [_target(decimate_source, "Decimated Mesh")],
                "settings": {"mode": "target_facets", "target_facet_count": target_facets},
            },
        )
        assert _result_object(document, decimated).Mesh.CountFacets <= target_facets

        scaled = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "scale",
                "targets": [_target(sources["ScaleSource"], "Scaled Mesh")],
                "factor": 1.5,
            },
        )
        scaled_result = _result_object(document, scaled)
        assert abs(scaled_result.Mesh.BoundBox.XLength - 12.0) < 1.0e-6

        gmsh_started = call(
            MESH_MODIFY_CAPABILITY_NAME,
            {
                "operation": "gmsh_remesh",
                "target": _target(sources["GmshSource"], "Background Gmsh Mesh"),
                "algorithm": "automatic",
                "minimum_element_size_mm": 0.0,
                "maximum_element_size_mm": 2.0,
                "surface_angle_degrees": 40.0,
                "timeout_seconds": 10,
            },
        )
        deadline = time.monotonic() + 15.0
        job_id = gmsh_started["job"]["job_id"]
        while time.monotonic() < deadline:
            _process_events(2)
            snapshot = context.background_manager.snapshot(job_id)
            if snapshot.terminal:
                break
            time.sleep(0.01)
        job = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "status", "job_id": job_id},
        )["job"]
        assert job["phase"] == "completed", job
        gmsh_result = document.getObject(job["result"]["outputs"][0]["result"]["object_name"])
        assert gmsh_result is not None and gmsh_result.TypeId == "Mesh::GmshRemesh"
        assert gmsh_result.Mesh.CountFacets == 2
        assert str(gmsh_result.Executable) == str(fake_gmsh.resolve())
        gmsh_result_name = gmsh_result.Name

        document.openTransaction("Invalidate indexed edit")
        add_source.Mesh = tetrahedron(24.0)
        assert document.recompute() is not False
        document.commitTransaction()
        assert not added_result.isValid()
        assert "topology changed" in added_result.getStatusString()
        document.undo()
        assert document.recompute() is not False
        assert added_result.isValid(), added_result.getStatusString()

        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        result_names = tuple(
            obj.Name
            for obj in document.Objects
            if str(getattr(obj, "VibeCADTimelineRole", "") or "") in {"operation", "resource"}
        )
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        assert all(document.getObject(name) is not None for name in result_names)
        reopened_gmsh = document.getObject(gmsh_result_name)
        assert reopened_gmsh is not None and reopened_gmsh.Mesh.CountFacets == 2
        assert all(document.getObject(name).isValid() for name in result_names)

        print(
            "VIBECAD_NATIVE_MESH_MODIFY_GUI_OK "
            f"variants=10 history={len(operation_names)} "
            "multi_output=true undo_redo=true stale=true gmsh_background=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if gmsh_preferences is not None:
            gmsh_preferences.SetString("gmshExe", prior_gmsh)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
