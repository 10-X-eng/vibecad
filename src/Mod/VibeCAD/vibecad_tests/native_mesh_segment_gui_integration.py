# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for all Native Mesh Segment ribbon actions."""

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
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshSegmentSchema import (
    MESH_COMBINE_CAPABILITY_NAME,
    MESH_SEGMENT_CAPABILITY_NAME,
    MESH_SEPARATE_CAPABILITY_NAME,
)
from VibeCADNativeMeshSnapshot import build_mesh_snapshot
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from native_mesh_modify_gui_support import add_source, open_tetrahedron, tetrahedron, two_components


OPERATIONS = (
    "mesh_segmentation",
    "segmentation_best_fit",
    "reverse_segmentation",
    "segmentation_manual",
    "segmentation_from_components",
    "mesh_boundary",
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
    names = (
        MESH_SEGMENT_CAPABILITY_NAME,
        MESH_COMBINE_CAPABILITY_NAME,
        MESH_SEPARATE_CAPABILITY_NAME,
    )
    definitions = tuple(registry.definition(name) for name in names)
    assert all(definitions)
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=names,
            schemas=(
                definitions[0].provider_schema(OPERATIONS),
                definitions[1].provider_schema(("merge",)),
                definitions[2].provider_schema(("split_components",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _exact(obj) -> dict:
    return {
        "object_name": obj.Name,
        "expected_state_sha256": mesh_object_state(obj)["state_sha256"],
    }


def _flat_patch(offset: float = 0.0):
    a = App.Vector(offset, 0.0, 0.0)
    b = App.Vector(offset + 10.0, 0.0, 0.0)
    c = App.Vector(offset + 10.0, 8.0, 0.0)
    d = App.Vector(offset, 8.0, 0.0)
    return Mesh.Mesh([(a, b, c), (a, c, d)])


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-segment-")
        save_path = Path(temporary.name) / "native-mesh-segment.FCStd"
        document = App.newDocument("NativeMeshSegmentGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller, surface = _select_mesh_ribbon(Gui.getMainWindow())
        merge_a = add_source(document, "MergeA", tetrahedron())
        merge_b = add_source(document, "MergeB", tetrahedron(15.0))
        split_source = add_source(document, "SplitSource", two_components())
        curvature_source = add_source(document, "CurvatureSource", _flat_patch(50.0))
        best_fit_source = add_source(
            document,
            "BestFitSource",
            Mesh.createBox(10.0, 10.0, 10.0),
        )
        reverse_source = add_source(document, "ReverseSource", _flat_patch(80.0))
        manual_source = add_source(document, "ManualSource", open_tetrahedron(110.0))
        components_source = add_source(document, "ComponentsSource", two_components())
        boundary_source = add_source(document, "BoundarySource", open_tetrahedron(150.0))
        _process_events(8)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-mesh-segment-gui")

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

        def call(
            arguments: dict,
            *,
            capability: str = MESH_SEGMENT_CAPABILITY_NAME,
            succeeds: bool = True,
        ) -> dict:
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                capability,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-segment-{call_number}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def call_background_segment(
            arguments: dict,
            *,
            capability: str = MESH_SEGMENT_CAPABILITY_NAME,
            timeout_seconds: float = 30.0,
        ) -> dict:
            started = time.monotonic()
            queued = call(arguments, capability=capability)
            assert time.monotonic() - started < 0.25, queued
            job_id = queued["job"]["job_id"]
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                _process_events(2)
                current = context.background_manager.snapshot(job_id)
                if current.terminal:
                    break
                time.sleep(0.01)
            current = context.background_manager.snapshot(job_id)
            assert current.phase == "completed", current.error
            assert current.changes_document is True
            assert isinstance(current.result, dict)
            return current.result

        stale = call(
            {
                "target": {
                    "object_name": split_source.Name,
                    "expected_state_sha256": "0" * 64,
                },
                "result_label_prefix": "Stale Component",
            },
            capability=MESH_SEPARATE_CAPABILITY_NAME,
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_MESH_STATE_STALE"

        responses = []
        responses.append(
            call_background_segment(
                {
                    "sources": [_exact(merge_a), _exact(merge_b)],
                    "result_label": "Merged Exact Mesh",
                },
                capability=MESH_COMBINE_CAPABILITY_NAME,
            )
        )
        responses.append(
            call_background_segment(
                {
                    "target": _exact(split_source),
                },
                capability=MESH_SEPARATE_CAPABILITY_NAME,
            )
        )
        responses.append(
            call_background_segment(
                {
                    "operation": "mesh_segmentation",
                    "target": _exact(curvature_source),
                    "surfaces": [
                        {
                            "kind": "plane",
                            "minimum_facets": 1,
                            "curvature_tolerance": 100.0,
                        }
                    ],
                    "smoothing_steps": 0,
                    "result_label_prefix": "Curvature Segment",
                }
            )
        )
        responses.append(
            call_background_segment(
                {
                    "operation": "segmentation_best_fit",
                    "target": _exact(best_fit_source),
                    "surfaces": [
                        {
                            "kind": "plane",
                            "minimum_facets": 1,
                            "distance_tolerance_mm": 0.01,
                        }
                    ],
                    "result_label_prefix": "Best Fit Segment",
                }
            )
        )
        responses.append(
            call_background_segment(
                {
                    "operation": "reverse_segmentation",
                    "target": _exact(reverse_source),
                    "minimum_facets": 1,
                    "curvature_tolerance": 100.0,
                    "distance_tolerance_mm": 0.01,
                    "smoothing_steps": 0,
                    "include_unused_facets": False,
                    "create_boundary_faces": True,
                    "result_label_prefix": "Planar Segment",
                }
            )
        )

        responses.append(
            call_background_segment(
                {
                    "operation": "segmentation_from_components",
                    "targets": [_exact(components_source)],
                    "result_label_prefix": "Component Segment",
                }
            )
        )
        boundary = call_background_segment(
            {
                "operation": "mesh_boundary",
                "targets": [
                    {**_exact(boundary_source), "label": "Linked Boundary Face"}
                ],
                "make_faces_when_closed": True,
            }
        )
        responses.append(boundary)
        boundary_object = document.getObject(boundary["results"][0]["object_name"])
        assert boundary_object.TypeId == "MeshPart::Boundary"
        assert len(boundary_object.Shape.Edges) > 0

        before_manual = {obj.Name for obj in document.Objects}
        undo_before_manual = document.UndoCount
        manual = call_background_segment(
            {
                "operation": "segmentation_manual",
                "target": _exact(manual_source),
                "selection": {"kind": "facet_indices", "facet_indices": [0]},
                "result": {
                    "mode": "split",
                    "segment_label": "Selected Facet",
                    "remainder_label": "Remaining Facets",
                },
            }
        )
        assert document.UndoCount == undo_before_manual + 1
        manual_names = [item["object_name"] for item in manual["results"]]
        manual_names.append(manual["operation_controller"]["object_name"])
        document.undo()
        assert {obj.Name for obj in document.Objects} == before_manual
        document.redo()
        _process_events(8)
        assert all(document.getObject(name) is not None for name in manual_names)
        responses.append(manual)

        snapshot = build_mesh_snapshot(document)
        assert snapshot["counts"]["mesh_part"] >= 2
        assert any(item["object_name"] == boundary_object.Name for item in snapshot["objects"])
        assert boundary["results"][0]["topology"]["edges"] > 0

        result_names = []
        for response in responses:
            if "result" in response:
                result_names.append(response["result"]["object_name"])
            result_names.extend(item["object_name"] for item in response.get("results", ()))
            if "operation_controller" in response:
                result_names.append(response["operation_controller"]["object_name"])
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
            "VIBECAD_NATIVE_MESH_SEGMENT_GUI_OK actions=8 stale=true "
            "typed_algorithms=true retained_boundaries=true undo_redo=true reopen=true",
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
