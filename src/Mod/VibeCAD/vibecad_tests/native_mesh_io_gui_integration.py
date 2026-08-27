# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Mesh state, I/O, and regular solids."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback
import zipfile

import FreeCAD as App
import FreeCADGui as Gui
import MeshGui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeMeshExportSchema import MESH_EXPORT_CAPABILITY_NAME
from VibeCADNativeMeshIOSchema import MESH_IO_CAPABILITY_NAME
from VibeCADNativeMeshSnapshot import build_mesh_snapshot
from VibeCADNativeOutput import authorize_native_output_path
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
    jobs = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    mesh_io = registry.definition(MESH_IO_CAPABILITY_NAME)
    export = registry.definition(MESH_EXPORT_CAPABILITY_NAME)
    assert jobs is not None and mesh_io is not None and export is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                NATIVE_BACKGROUND_CAPABILITY_NAME,
                MESH_IO_CAPABILITY_NAME,
                MESH_EXPORT_CAPABILITY_NAME,
            ),
            schemas=(
                jobs.provider_schema(("status", "cancel")),
                mesh_io.provider_schema(("import_mesh", "regular_solid")),
                export.provider_schema(("export_mesh",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _placement(x: float, y: float, z: float) -> dict:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-mesh-io-")
        root = Path(temporary.name)
        document = App.newDocument("NativeMeshIOGate")
        document.UndoMode = 1
        document.saveAs(str(root / "native-mesh-io.FCStd"))
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
        ledger.begin_run("native-mesh-io-gui")
        selected_input = {"path": None}
        selected_output = {"path": root / "exported-mesh.3mf"}
        input_authorizations = {"count": 0}
        output_authorizations = {"count": 0}

        def input_authorizer(request):
            input_authorizations["count"] += 1
            path = selected_input["path"]
            return None if path is None else authorize_native_input_path(request, path)

        def output_authorizer(request):
            output_authorizations["count"] += 1
            return authorize_native_output_path(request, selected_output["path"])

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
            result = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-mesh-io-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        solids = (
            ("Gate Box", {"kind": "box", "length_mm": 20.0, "width_mm": 15.0, "height_mm": 10.0}),
            ("Gate Cylinder", {"kind": "cylinder", "radius_mm": 5.0, "length_mm": 18.0, "edge_length_mm": 1.0, "sampling": 32, "closed": True}),
            ("Gate Cone", {"kind": "cone", "radius1_mm": 7.0, "radius2_mm": 2.0, "length_mm": 16.0, "edge_length_mm": 1.0, "sampling": 32, "closed": True}),
            ("Gate Sphere", {"kind": "sphere", "radius_mm": 8.0, "sampling": 40}),
            ("Gate Ellipsoid", {"kind": "ellipsoid", "radius1_mm": 9.0, "radius2_mm": 5.0, "sampling": 40}),
            ("Gate Torus", {"kind": "torus", "major_radius_mm": 12.0, "minor_radius_mm": 3.0, "sampling": 40}),
        )
        created = []
        for index, (label, solid) in enumerate(solids):
            result = call(
                MESH_IO_CAPABILITY_NAME,
                {
                    "operation": "regular_solid",
                    "label": label,
                    "placement": _placement(index * 30.0, 0.0, 0.0),
                    "solid": solid,
                },
            )
            object_name = result["created"]["object_name"]
            obj = document.getObject(object_name)
            assert obj is not None and obj.Mesh.CountFacets > 0
            assert MeshGui.isNativeMeshInputActive(obj)
            created.append(obj)
        assert int(document.UndoCount) == len(solids)
        assert tuple(document.VibeCADTimeline.Operations) == tuple(created)
        meshes_group = document.getObject("Meshes")
        assert meshes_group is not None
        assert meshes_group.TypeId == "App::DocumentObjectGroup"
        assert meshes_group.Label == "Meshes"
        assert tuple(meshes_group.Group) == tuple(created)

        snapshot = build_mesh_snapshot(document)
        assert snapshot["counts"]["mesh"] == len(solids)
        assert len(snapshot["inventory_sha256"]) == 64
        target_state = next(
            item for item in snapshot["objects"] if item["object_name"] == created[3].Name
        )
        topology = target_state["topology"]
        auth_before = output_authorizations["count"]
        stale = call(
            MESH_EXPORT_CAPABILITY_NAME,
            {
                "operation": "export_mesh",
                "target": {
                    "object_name": created[3].Name,
                    "expected_state_sha256": "0" * 64,
                },
                "format": "3mf",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_MESH_STATE_STALE", stale
        assert output_authorizations["count"] == auth_before

        export_started = call(
            MESH_EXPORT_CAPABILITY_NAME,
            {
                "operation": "export_mesh",
                "target": {
                    "object_name": created[3].Name,
                    "expected_state_sha256": target_state["state_sha256"],
                },
                "format": "3mf",
            },
        )

        def wait_for_job(job_id: str) -> dict:
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                _process_events(2)
                snapshot_value = context.background_manager.snapshot(job_id)
                if snapshot_value.terminal:
                    status = call(
                        NATIVE_BACKGROUND_CAPABILITY_NAME,
                        {"operation": "status", "job_id": job_id},
                    )
                    return status["job"]
                time.sleep(0.01)
            raise AssertionError(f"Background Mesh job {job_id} did not finish")

        export_job = wait_for_job(export_started["job"]["job_id"])
        assert export_job["phase"] == "completed", export_job
        assert selected_output["path"].is_file()
        assert export_job["result"]["output"]["size_bytes"] > 0
        assert int(document.UndoCount) == len(solids)

        empty_3mf = root / "empty.3mf"
        with zipfile.ZipFile(empty_3mf, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                "</Types>",
            )
            archive.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
                'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                "</Relationships>",
            )
            archive.writestr(
                "3D/3dmodel.model",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<model unit="millimeter" xml:lang="en-US" '
                'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
                '<resources><object id="1" type="model"><mesh><vertices/><triangles/>'
                '</mesh></object></resources><build><item objectid="1"/></build></model>',
            )
        objects_before_empty = tuple(document.Objects)
        undo_before_empty = int(document.UndoCount)
        selected_input["path"] = empty_3mf
        empty_started = call(MESH_IO_CAPABILITY_NAME, {"operation": "import_mesh"})
        empty_job = wait_for_job(empty_started["job"]["job_id"])
        assert empty_job["phase"] == "failed", empty_job
        assert empty_job["failure"]["error_code"] == "NATIVE_MESH_IMPORT_EMPTY"
        assert tuple(document.Objects) == objects_before_empty
        assert int(document.UndoCount) == undo_before_empty

        selected_input["path"] = selected_output["path"]
        import_started = call(
            MESH_IO_CAPABILITY_NAME,
            {"operation": "import_mesh"},
        )
        import_job = wait_for_job(import_started["job"]["job_id"])
        assert import_job["phase"] == "completed", import_job
        imported_name = import_job["result"]["imported"]["object_name"]
        imported = document.getObject(imported_name)
        assert imported is not None and imported.Mesh.CountFacets > 0
        assert imported.VibeCADTimelineRole == "operation"
        assert list(imported.VibeCADExternalInputs) == [selected_output["path"].name]
        assert MeshGui.isNativeMeshInputActive(imported)
        assert int(document.UndoCount) == len(solids) + 1
        assert tuple(document.VibeCADTimeline.Operations)[-1] is imported
        assert imported in meshes_group.Group
        assert input_authorizations["count"] == 2
        assert output_authorizations["count"] == 1

        document.save()
        imported_facets = int(imported.Mesh.CountFacets)
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(root / "native-mesh-io.FCStd"))
        App.setActiveDocument(document.Name)
        _process_events(10)
        reopened = document.getObject(imported_name)
        assert reopened is not None
        assert int(reopened.Mesh.CountFacets) == imported_facets
        assert reopened.VibeCADTimelineRole == "operation"
        assert list(reopened.VibeCADExternalInputs) == [selected_output["path"].name]
        assert tuple(document.VibeCADTimeline.Operations)[-1] is reopened
        reopened_meshes_group = document.getObject("Meshes")
        assert reopened_meshes_group is not None
        assert reopened in reopened_meshes_group.Group

        print(
            "VIBECAD_NATIVE_MESH_IO_GUI_OK "
            f"solids={len(solids)} imported_facets={imported_facets} "
            f"history={len(document.VibeCADTimeline.Operations)} "
            "background_import=true background_export=true format=3mf "
            "empty_rejected=true",
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
