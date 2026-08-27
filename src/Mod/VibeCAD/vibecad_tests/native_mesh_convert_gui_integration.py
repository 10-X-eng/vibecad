# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact retained Native Mesh conversions."""

from __future__ import annotations

import json
import math
from pathlib import Path
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
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshConvertSchema import (
    MESH_CONVERT_CAPABILITY_NAME,
    MESH_CURVE_ON_MESH_CAPABILITY_NAME,
    MESH_FROM_SHAPE_CAPABILITY_NAME,
    MESH_TO_SHAPE_CAPABILITY_NAME,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


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
    definition = registry.definition(MESH_CONVERT_CAPABILITY_NAME)
    assert definition is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MESH_CONVERT_CAPABILITY_NAME,),
            schemas=(
                definition.provider_schema(
                    (
                        "shape_to_mesh",
                        "mesh_to_shape",
                        "mesh_to_solid",
                        "curve_on_mesh",
                    )
                ),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _create_source(document):
    document.openTransaction("Create Mesh conversion source")
    try:
        source = document.addObject("Part::Box", "ConversionSource")
        source.Label = "Conversion Source"
        source.Length = 20.0
        source.Width = 15.0
        source.Height = 10.0
        assert document.recompute([source], True, True) is not False
        assert not source.Shape.isNull() and source.Shape.isValid()
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _create_disconnected_mesh(document):
    document.openTransaction("Create disconnected Mesh conversion source")
    try:
        mesh = Mesh.createBox(4.0, 4.0, 4.0)
        second = Mesh.createBox(4.0, 4.0, 4.0)
        second.translate(10.0, 0.0, 0.0)
        mesh.addMesh(second)
        source = document.addObject("Mesh::Feature", "DisconnectedMesh")
        source.Label = "Disconnected Mesh"
        source.Mesh = mesh
        assert document.recompute([source], True, True) is not False
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _curve_arguments(mesh, state_sha256: str) -> dict:
    return {
        "operation": "curve_on_mesh",
        "source": {
            "object_name": mesh.Name,
            "expected_state_sha256": state_sha256,
        },
        "anchors": [
            {"origin_mm": [2.0, 2.0, 25.0], "direction": [0.0, 0.0, -1.0]},
            {"origin_mm": [8.0, 3.0, 25.0], "direction": [0.0, 0.0, -1.0]},
            {"origin_mm": [14.0, 8.0, 25.0], "direction": [0.0, 0.0, -1.0]},
        ],
        "label": "Retained Mesh Curve",
        "closed": False,
        "approximate": True,
        "maximum_degree": 5,
        "continuity": "C2",
        "tolerance_mm": 0.2,
        "split_angle_degrees": 45.0,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-convert-")
        path = Path(temporary.name) / "native-mesh-convert.FCStd"
        document = App.newDocument("NativeMeshConvertGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller, surface = _select_mesh_ribbon(Gui.getMainWindow())
        source = _create_source(document)
        disconnected_mesh = _create_disconnected_mesh(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        actual_surface = resolve_native_provider_surface(surface, registry)
        assert actual_surface.available, {
            "reason": actual_surface.unavailable_reason,
            "missing_definitions": actual_surface.missing_definition_names,
            "missing_implementations": actual_surface.missing_implementation_names,
            "incomplete_definitions": actual_surface.incomplete_definition_names,
        }
        assert MESH_TO_SHAPE_CAPABILITY_NAME in actual_surface.tool_names
        assert MESH_FROM_SHAPE_CAPABILITY_NAME in actual_surface.tool_names
        assert MESH_CURVE_ON_MESH_CAPABILITY_NAME in actual_surface.tool_names
        assert MESH_CONVERT_CAPABILITY_NAME not in actual_surface.tool_names
        actual_from_shape_schema = next(
            schema
            for schema in actual_surface.schemas
            if schema["name"] == MESH_FROM_SHAPE_CAPABILITY_NAME
        )
        actual_from_shape_parameters = actual_from_shape_schema["parameters"][
            "oneOf"
        ][0]
        assert actual_from_shape_parameters["required"] == ["source"]
        assert "operation" not in actual_from_shape_parameters["properties"]
        actual_to_shape_schema = next(
            schema
            for schema in actual_surface.schemas
            if schema["name"] == MESH_TO_SHAPE_CAPABILITY_NAME
        )
        assert actual_to_shape_schema["parameters"]["properties"]["operation"][
            "enum"
        ] == ["shell", "solid"]
        actual_curve_schema = next(
            schema
            for schema in actual_surface.schemas
            if schema["name"] == MESH_CURVE_ON_MESH_CAPABILITY_NAME
        )
        actual_curve_parameters = actual_curve_schema["parameters"]["oneOf"][0]
        assert actual_curve_parameters["required"] == ["source", "anchors"]
        assert "operation" not in actual_curve_parameters["properties"]
        turn = _turn(surface, registry)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-mesh-convert-gui")

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

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                MESH_CONVERT_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-convert-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        def call_job(arguments: dict, *, succeeds: bool = True) -> dict:
            started = call(arguments)
            job = started.get("job")
            assert isinstance(job, dict) and job.get("job_id"), started
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                _process_events(2)
                snapshot = service.native_background_manager().snapshot(job["job_id"])
                if snapshot.terminal:
                    break
            else:
                raise AssertionError(f"Mesh conversion job did not finish: {job}")
            if succeeds:
                assert snapshot.phase == "completed", snapshot
                assert isinstance(snapshot.result, dict), snapshot
                return dict(snapshot.result)
            assert snapshot.phase == "failed", snapshot
            assert isinstance(snapshot.error, dict), snapshot
            return {"ok": False, **dict(snapshot.error)}

        full = call_job(
            {
                "operation": "shape_to_mesh",
                "source": {"object_name": source.Name},
                "subelements": [],
                "label": "Complete Source Mesh",
                "linear_deflection_mm": 0.25,
                "angular_deflection_degrees": 20.0,
                "relative": False,
                "segments": True,
            }
        )
        full_mesh = document.getObject(full["created"]["object_name"])
        assert full_mesh is not None and full_mesh.TypeId == "MeshPart::MeshFromShape"
        assert full_mesh.Source == (source, [])
        assert full_mesh.Mesh.CountFacets > 0
        assert MeshGui.isNativeMeshInputActive(full_mesh)

        selected = call_job(
            {
                "operation": "shape_to_mesh",
                "source": {"object_name": source.Name},
                "subelements": ["Face1"],
                "label": "Selected Face Mesh",
                "linear_deflection_mm": 0.2,
                "angular_deflection_degrees": 15.0,
                "relative": False,
                "segments": False,
            }
        )
        face_mesh = document.getObject(selected["created"]["object_name"])
        assert face_mesh is not None and face_mesh.Source == (source, ["Face1"])
        assert face_mesh.Mesh.CountFacets > 0

        full_state = mesh_object_state(full_mesh)
        stale = call(
            {
                "operation": "mesh_to_shape",
                "source": {
                    "object_name": full_mesh.Name,
                    "expected_state_sha256": "0" * 64,
                },
                "label": "Stale Shape",
                "tolerance_mm": 0.1,
                "sew_adjacent_faces": True,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_MESH_STATE_STALE"
        assert "current_state_sha256" in stale["repair"]

        converted = call_job(
            {
                "operation": "mesh_to_shape",
                "source": {
                    "object_name": full_mesh.Name,
                    "expected_state_sha256": full_state["state_sha256"],
                },
                "label": "Linked Mesh Shape",
                "tolerance_mm": 0.1,
                "sew_adjacent_faces": True,
            }
        )
        converted_shape = document.getObject(converted["created"]["object_name"])
        assert converted_shape is not None
        assert converted_shape.TypeId == "MeshPart::ShapeFromMesh"
        assert converted_shape.Source is full_mesh
        assert converted_shape.UpdateFromSource is False
        assert not converted_shape.Shape.isNull() and converted_shape.Shape.isValid()
        assert converted["shape_type"] == "Shell"
        assert converted["topology"]["solids"] == 0
        assert converted["topology"]["shells"] == 1
        assert len(converted_shape.Shape.Faces) == 6
        assert converted["topology"]["faces"] == 6
        assert converted["representation"] == "faceted_shell"

        open_state = mesh_object_state(face_mesh)
        open_solid = call_job(
            {
                "operation": "mesh_to_solid",
                "source": {
                    "object_name": face_mesh.Name,
                    "expected_state_sha256": open_state["state_sha256"],
                },
                "label": "Rejected Open Mesh Solid",
                "tolerance_mm": 0.1,
            },
            succeeds=False,
        )
        assert open_solid["error_code"] == "NATIVE_MESH_SOLID_REQUIRED"

        solid_state = mesh_object_state(full_mesh)
        solid = call_job(
            {
                "operation": "mesh_to_solid",
                "source": {
                    "object_name": full_mesh.Name,
                    "expected_state_sha256": solid_state["state_sha256"],
                },
                "label": "Linked Mesh Solid",
                "tolerance_mm": 0.1,
            }
        )
        converted_solid = document.getObject(solid["created"]["object_name"])
        assert converted_solid is not None
        assert converted_solid.TypeId == "MeshPart::ShapeFromMesh"
        assert converted_solid.Source is full_mesh
        assert converted_solid.UpdateFromSource is False
        assert converted_solid.SewShape is True
        assert converted_solid.MakeSolid is True
        assert converted_solid.Shape.ShapeType == "Solid"
        assert len(converted_solid.Shape.Solids) == 1
        assert math.isclose(float(converted_solid.Shape.Volume), 3000.0, abs_tol=1.0e-6)
        assert solid["shape_type"] == "Solid"
        assert solid["topology"]["solids"] == 1
        assert solid["topology"]["shells"] == 1
        assert len(converted_solid.Shape.Faces) == 6
        assert solid["topology"]["faces"] == 6
        assert solid["representation"] == "faceted_solid"

        disconnected_state = mesh_object_state(disconnected_mesh)
        disconnected_solid = call_job(
            {
                "operation": "mesh_to_solid",
                "source": {
                    "object_name": disconnected_mesh.Name,
                    "expected_state_sha256": disconnected_state["state_sha256"],
                },
                "label": "Rejected Disconnected Solid",
                "tolerance_mm": 0.1,
            },
            succeeds=False,
        )
        assert disconnected_solid["error_code"] == "NATIVE_MESH_SINGLE_SOLID_REQUIRED"
        assert document.getObject("DisconnectedMesh_shape") is None

        curve_state = mesh_object_state(full_mesh)["state_sha256"]
        stale_curve_arguments = _curve_arguments(full_mesh, "f" * 64)
        stale_curve = call(stale_curve_arguments, succeeds=False)
        assert stale_curve["error_code"] == "NATIVE_MESH_STATE_STALE"
        curve = call(_curve_arguments(full_mesh, curve_state))
        curve_object = document.getObject(curve["root"]["object_name"])
        assert curve_object is not None and curve_object.TypeId == "MeshPart::CurveOnMesh"
        assert curve_object.Source is full_mesh
        assert len(curve_object.AnchorFacets) == 3
        assert not curve_object.Shape.isNull() and curve_object.Shape.isValid()

        operations = tuple(document.VibeCADTimeline.Operations)
        assert operations == (
            source,
            disconnected_mesh,
            full_mesh,
            face_mesh,
            converted_shape,
            converted_solid,
            curve_object,
        )
        assert all(obj.VibeCADTimelineRole == "operation" for obj in operations)
        assert int(document.UndoCount) == 7
        operation_names = tuple(obj.Name for obj in operations)

        document.undo()
        assert document.getObject(operation_names[-1]) is None
        document.redo()
        curve_object = document.getObject(operation_names[-1])
        assert curve_object is not None and not curve_object.Shape.isNull()

        document.openTransaction("Edit retained Mesh conversion source")
        source.Length = 24.0
        assert document.recompute() is not False
        document.commitTransaction()
        assert math.isclose(float(full_mesh.Mesh.BoundBox.XLength), 20.0, abs_tol=1.0e-7)
        assert math.isclose(float(converted_shape.Shape.BoundBox.XLength), 20.0, abs_tol=1.0e-7)
        assert math.isclose(float(converted_solid.Shape.BoundBox.XLength), 20.0, abs_tol=1.0e-7)
        assert not curve_object.Shape.isNull() and curve_object.Shape.isValid()

        document.save()
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(10)
        reopened = tuple(document.getObject(name) for name in operation_names)
        assert all(obj is not None for obj in reopened)
        (
            reopened_source,
            reopened_disconnected,
            reopened_full,
            reopened_face,
            reopened_shape,
            reopened_solid,
            reopened_curve,
        ) = reopened
        assert reopened_disconnected.Mesh.countComponents() == 2
        assert reopened_full.Source == (reopened_source, [])
        assert reopened_face.Source == (reopened_source, ["Face1"])
        assert reopened_shape.Source is reopened_full
        assert reopened_shape.UpdateFromSource is False
        assert reopened_solid.Source is reopened_full
        assert reopened_solid.UpdateFromSource is False
        assert reopened_solid.MakeSolid is True
        assert reopened_solid.Shape.ShapeType == "Solid"
        assert len(reopened_solid.Shape.Solids) == 1
        assert reopened_curve.Source is reopened_full
        assert tuple(document.VibeCADTimeline.Operations) == reopened
        assert math.isclose(float(reopened_full.Mesh.BoundBox.XLength), 20.0, abs_tol=1.0e-7)
        assert math.isclose(float(reopened_shape.Shape.BoundBox.XLength), 20.0, abs_tol=1.0e-7)
        assert math.isclose(float(reopened_solid.Shape.BoundBox.XLength), 20.0, abs_tol=1.0e-7)

        print(
            "VIBECAD_NATIVE_MESH_CONVERT_GUI_OK "
            f"mesh_facets={reopened_full.Mesh.CountFacets} "
            f"shape_type={reopened_shape.Shape.ShapeType} "
            f"solids={len(reopened_shape.Shape.Solids)} "
            f"shells={len(reopened_shape.Shape.Shells)} "
            f"shape_faces={len(reopened_shape.Shape.Faces)} "
            f"solid_volume={reopened_solid.Shape.Volume:g} "
            f"curve_edges={len(reopened_curve.Shape.Edges)} history={len(reopened)}",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
