# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for every Reverse Engineering ribbon action."""

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
import MeshPart
import Part
import Points
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshApproximateSchema import MESH_APPROXIMATE_CAPABILITY_NAME
from VibeCADNativeMeshRebuildSchema import MESH_REBUILD_CAPABILITY_NAME
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


REVERSE_ACTIONS = frozenset(
    {
        "Reen_PoissonReconstruction",
        "Reen_ViewTriangulation",
        "Reen_ApproxPlane",
        "Reen_ApproxCylinder",
        "Reen_ApproxSphere",
        "Reen_ApproxPolynomial",
        "Reen_ApproxSurface",
        "Reen_ApproxCurve",
    }
)
REBUILD_OPERATIONS = ("poisson_reconstruction", "view_triangulation")
APPROXIMATE_OPERATIONS = (
    "approx_plane",
    "approx_cylinder",
    "approx_sphere",
    "approx_polynomial",
    "approx_surface",
    "approx_curve",
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
    assert REVERSE_ACTIONS <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    jobs = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    rebuild = registry.definition(MESH_REBUILD_CAPABILITY_NAME)
    approximate = registry.definition(MESH_APPROXIMATE_CAPABILITY_NAME)
    assert jobs is not None and rebuild is not None and approximate is not None
    covered = {
        action
        for variant in (*rebuild.variants, *approximate.variants)
        for action in variant.action_ids
    }
    assert covered == REVERSE_ACTIONS
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                NATIVE_BACKGROUND_CAPABILITY_NAME,
                MESH_REBUILD_CAPABILITY_NAME,
                MESH_APPROXIMATE_CAPABILITY_NAME,
            ),
            schemas=(
                jobs.provider_schema(("status", "cancel")),
                rebuild.provider_schema(REBUILD_OPERATIONS),
                approximate.provider_schema(APPROXIMATE_OPERATIONS),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _add_points(document, name: str, coordinates, *, structured=False, width=0, height=0):
    type_id = "Points::Structured" if structured else "Points::Feature"
    obj = document.addObject(type_id, name)
    obj.Label = name
    kernel = Points.Points()
    kernel.addPoints([App.Vector(*point) for point in coordinates])
    obj.Points = kernel
    if structured:
        obj.Width = width
        obj.Height = height
    document.recompute()
    return obj


def _point_target(obj) -> dict:
    state = mesh_object_state(obj)
    return {
        "object_name": obj.Name,
        "expected_state_sha256": state["state_sha256"],
        "expected_point_count": state["topology"]["points"],
    }


def _exact_target(obj) -> dict:
    return {
        "object_name": obj.Name,
        "expected_state_sha256": mesh_object_state(obj)["state_sha256"],
    }


def _mesh_feature(document, name: str, shape) -> object:
    obj = document.addObject("Mesh::Feature", name)
    obj.Label = name
    obj.Mesh = MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=0.35,
        AngularDeflection=0.25,
        Relative=False,
    )
    document.recompute()
    return obj


def _polynomial_mesh(document):
    facets = []
    points = [[(float(x), float(y), 0.04 * x * x + 0.02 * y * y) for x in range(4)] for y in range(4)]
    for row in range(3):
        for column in range(3):
            a = points[row][column]
            b = points[row][column + 1]
            c = points[row + 1][column]
            d = points[row + 1][column + 1]
            facets.extend(((a, b, d), (a, d, c)))
    obj = document.addObject("Mesh::Feature", "PolynomialSource")
    obj.Label = "Polynomial Source"
    obj.Mesh = Mesh.Mesh(facets)
    document.recompute()
    return obj


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        import ReverseEngineering

        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-reverse-")
        save_path = Path(temporary.name) / "native-mesh-reverse.FCStd"
        document = App.newDocument("NativeMeshReverseGate")
        document.UndoMode = 1
        plane_points = _add_points(
            document,
            "PlanePoints",
            ((0, 0, 2), (10, 0, 2.01), (0, 8, 1.99), (10, 8, 2)),
        )
        structured = _add_points(
            document,
            "StructuredPoints",
            ((0, 0, 0), (2, 0, 0.2), (0, 2, 0.1), (2, 2, 0.3)),
            structured=True,
            width=2,
            height=2,
        )
        curve_points = _add_points(
            document,
            "CurvePoints",
            ((0, 0, 0), (2, 1, 0), (4, 0, 0), (6, -1, 0), (8, 0, 0)),
        )
        surface_points = _add_points(
            document,
            "SurfacePoints",
            tuple((x, y, 0.1 * x * y) for x in range(4) for y in range(4)),
        )
        cylinder_mesh = _mesh_feature(document, "CylinderMesh", Part.makeCylinder(5, 10))
        sphere_mesh = _mesh_feature(document, "SphereMesh", Part.makeSphere(6))
        polynomial_mesh = _polynomial_mesh(document)
        document.saveAs(str(save_path))

        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller, surface = _select_mesh_ribbon(Gui.getMainWindow())
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        complete_surface = resolve_native_provider_surface(surface, registry)
        if not complete_surface.available:
            mismatches = []
            for action in resolve_native_action_inventory(surface).plans:
                if action.classification.parent_only or action.classification.human_only:
                    continue
                definition = registry.definition(action.capability_family)
                matching = [
                    variant
                    for variant in (() if definition is None else definition.variants)
                    if variant.operation == action.operation_variant
                    and action.command_id in variant.action_ids
                ]
                if not matching or any(
                    variant.transaction_behavior != action.transaction_behavior
                    or variant.background_required is not action.background_required
                    for variant in matching
                ):
                    mismatches.append(
                        (
                            action.command_id,
                            action.capability_family,
                            action.operation_variant,
                            action.transaction_behavior,
                            action.background_required,
                            [
                                (
                                    variant.transaction_behavior,
                                    variant.background_required,
                                )
                                for variant in matching
                            ],
                        )
                    )
            raise AssertionError(
                f"{complete_surface.debug_summary()} mismatches={mismatches}"
            )
        assert {MESH_REBUILD_CAPABILITY_NAME, MESH_APPROXIMATE_CAPABILITY_NAME} <= set(
            complete_surface.tool_names
        )
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-mesh-reverse-gui")

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
                f"native-mesh-reverse-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def wait_for_job(started: dict, *, timeout: float = 35.0) -> dict:
            job_id = started["job"]["job_id"]
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                _process_events(2)
                snapshot = context.background_manager.snapshot(job_id)
                if snapshot.terminal:
                    return call(
                        NATIVE_BACKGROUND_CAPABILITY_NAME,
                        {"operation": "status", "job_id": job_id},
                    )["job"]
                time.sleep(0.01)
            raise AssertionError(f"Background Reverse Engineering job {job_id} did not finish")

        initial_undo = int(document.UndoCount)
        stale = call(
            MESH_APPROXIMATE_CAPABILITY_NAME,
            {
                "operation": "approx_plane",
                "geometry_sources": [
                    {
                        "object_name": plane_points.Name,
                        "expected_state_sha256": "0" * 64,
                        "result_label": "Stale Plane",
                    }
                ],
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_REVERSE_STATE_STALE"
        assert int(document.UndoCount) == initial_undo

        triangulated = wait_for_job(
            call(
                MESH_REBUILD_CAPABILITY_NAME,
                {
                    "operation": "view_triangulation",
                    "structured_clouds": [
                        {**_point_target(structured), "result_label": "Structured Triangulation"}
                    ],
                },
            )
        )
        assert triangulated["phase"] == "completed", triangulated
        triangulated_obj = document.getObject(
            triangulated["result"]["outputs"][0]["object_name"]
        )
        assert triangulated_obj.Mesh.CountFacets == 2 and triangulated_obj.Source is structured

        plane = wait_for_job(
            call(
                MESH_APPROXIMATE_CAPABILITY_NAME,
                {
                    "operation": "approx_plane",
                    "geometry_sources": [
                        {**_exact_target(plane_points), "result_label": "Best Fit Plane"}
                    ],
                },
            )
        )
        assert plane["phase"] == "completed", plane
        plane_obj = document.getObject(plane["result"]["outputs"][0]["object_name"])
        assert plane_obj.TypeId == "Part::Plane" and not plane_obj.Shape.isNull()

        cylinder = wait_for_job(
            call(
                MESH_APPROXIMATE_CAPABILITY_NAME,
                {
                    "operation": "approx_cylinder",
                    "cylinder_meshes": [
                        {**_exact_target(cylinder_mesh), "result_label": "Best Fit Cylinder"}
                    ],
                },
            )
        )
        assert cylinder["phase"] == "completed", cylinder
        cylinder_obj = document.getObject(cylinder["result"]["outputs"][0]["object_name"])
        assert abs(float(cylinder_obj.Radius) - 5.0) < 0.02
        assert abs(float(cylinder_obj.Height) - 10.0) < 0.02

        sphere = wait_for_job(
            call(
                MESH_APPROXIMATE_CAPABILITY_NAME,
                {
                    "operation": "approx_sphere",
                    "sphere_meshes": [
                        {**_exact_target(sphere_mesh), "result_label": "Best Fit Sphere"}
                    ],
                },
            )
        )
        assert sphere["phase"] == "completed", sphere
        sphere_obj = document.getObject(sphere["result"]["outputs"][0]["object_name"])
        assert abs(float(sphere_obj.Radius) - 6.0) < 0.02

        polynomial = wait_for_job(
            call(
                MESH_APPROXIMATE_CAPABILITY_NAME,
                {
                    "operation": "approx_polynomial",
                    "polynomial_meshes": [
                        {
                            **_exact_target(polynomial_mesh),
                            "result_label": "Polynomial Surface",
                        }
                    ],
                },
            )
        )
        assert polynomial["phase"] == "completed", polynomial
        polynomial_obj = document.getObject(
            polynomial["result"]["outputs"][0]["object_name"]
        )
        assert polynomial_obj.TypeId == "Part::Spline" and len(polynomial_obj.Shape.Faces) == 1

        ui_dispatched = {"value": False}
        QtCore.QTimer.singleShot(0, lambda: ui_dispatched.__setitem__("value", True))
        surface_fit = wait_for_job(
            call(
                MESH_APPROXIMATE_CAPABILITY_NAME,
                {
                    "operation": "approx_surface",
                    "surface_source": _exact_target(surface_points),
                    "result_label": "B-Spline Surface",
                    "u_degree": 2,
                    "v_degree": 2,
                    "u_control_points": 4,
                    "v_control_points": 4,
                    "iterations": 2,
                    "patch_size_factor": 1.0,
                    "parameter_correction": True,
                    "smoothing": {
                        "enabled": True,
                        "total_weight": 0.1,
                        "gradient_weight": 1.0,
                        "bending_weight": 0.0,
                        "curvature_weight": 0.0,
                    },
                    "uv_directions": {"mode": "automatic"},
                },
            )
        )
        assert ui_dispatched["value"] is True
        assert surface_fit["phase"] == "completed", surface_fit
        surface_obj = document.getObject(surface_fit["result"]["outputs"][0]["object_name"])
        assert surface_obj.TypeId == "Part::Spline" and len(surface_obj.Shape.Faces) == 1

        curve_fit = wait_for_job(
            call(
                MESH_APPROXIMATE_CAPABILITY_NAME,
                {
                    "operation": "approx_curve",
                    "curve_source": _point_target(curve_points),
                    "result_label": "B-Spline Curve",
                    "fit": {
                        "mode": "approximation",
                        "minimum_degree": 2,
                        "maximum_degree": 4,
                        "continuity": "C1",
                        "closed": False,
                        "parametrization": "chord_length",
                        "tolerance_mm": 0.001,
                    },
                },
            )
        )
        assert curve_fit["phase"] == "completed", curve_fit
        curve_obj = document.getObject(curve_fit["result"]["outputs"][0]["object_name"])
        assert curve_obj.TypeId == "Part::Spline" and len(curve_obj.Shape.Edges) == 1

        poisson = wait_for_job(
            call(
                MESH_REBUILD_CAPABILITY_NAME,
                {
                    "operation": "poisson_reconstruction",
                    "target": _point_target(surface_points),
                    "result_label": "Poisson Surface",
                    "octree_depth": 6,
                    "solver_divide": 6,
                    "samples_per_node": 2.0,
                    "normal_neighbors": 5,
                },
            ),
            timeout=60.0,
        )
        if hasattr(ReverseEngineering, "poissonReconstruction"):
            assert poisson["phase"] == "completed", poisson
            poisson_obj = document.getObject(poisson["result"]["outputs"][0]["object_name"])
            assert poisson_obj is not None and poisson_obj.Mesh.CountFacets > 0
        else:
            assert poisson["phase"] == "failed", poisson
            assert poisson["failure"]["error_code"] == "NATIVE_POISSON_UNAVAILABLE"
            poisson_obj = None

        assert int(document.UndoCount) == initial_undo + 7 + int(
            hasattr(ReverseEngineering, "poissonReconstruction")
        )
        names = (
            triangulated_obj.Name,
            plane_obj.Name,
            cylinder_obj.Name,
            sphere_obj.Name,
            polynomial_obj.Name,
            surface_obj.Name,
            curve_obj.Name,
            *([poisson_obj.Name] if poisson_obj is not None else []),
        )
        history_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        last_output_name = poisson_obj.Name if poisson_obj is not None else curve_obj.Name
        document.undo()
        assert document.getObject(last_output_name) is None
        document.redo()
        assert document.getObject(last_output_name) is not None
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        assert all(document.getObject(name) is not None for name in names)
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == history_names
        assert all(obj.isValid() for obj in document.Objects)

        print(
            "VIBECAD_NATIVE_MESH_REVERSE_GUI_OK actions=8 background=true "
            "fits=6 triangulation=true poisson_conditional=true undo_redo=true reopen=true",
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
