# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for process-isolated, cached Mesh From Shape."""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshTessellationGui import start_shape_tessellations
import VibeCADGui


SETTINGS = {
    "method": "standard",
    "linear_deflection_mm": 0.17,
    "angular_deflection_radians": 0.31,
    "relative": False,
    "segments": True,
}

MEFISTO_SETTINGS = {
    "method": "mefisto",
    "maximum_edge_length_mm": 2.0,
}


def _events() -> None:
    Gui.updateGui()
    QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _wait(job_id: str, *, minimum_heartbeats: int) -> dict:
    manager = get_service().native_background_manager()
    heartbeats = 0
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        _events()
        heartbeats += 1
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            assert snapshot.phase == "completed", snapshot
            assert heartbeats >= minimum_heartbeats, heartbeats
            assert isinstance(snapshot.result, dict), snapshot
            return dict(snapshot.result)
        time.sleep(0.01)
    raise AssertionError(f"Mesh From Shape did not finish: {job_id}")


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        VibeCADGui._ensure_document_thread_invoker()
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-mesh-tessellation-")
        path = Path(temporary.name) / "mesh-from-shape.FCStd"
        document = App.newDocument("MeshTessellationGate")
        document.UndoMode = 1
        source = document.addObject("Part::Cylinder", "TessellationSource")
        source.Radius = 13.0 + (time.time_ns() % 1_000_000_000) / 1.0e12
        source.Height = 29.113
        assert document.recompute([source], True, True) is not False
        materials = [App.Material() for _ in range(3)]
        for material, color in zip(
            materials,
            (
                (0.95, 0.20, 0.10, 0.0),
                (0.10, 0.85, 0.25, 0.0),
                (0.15, 0.30, 0.95, 0.0),
            ),
        ):
            material.DiffuseColor = color
        source.ViewObject.ShapeAppearance = materials
        document.saveAs(str(path))

        first = _wait(
            start_shape_tessellations(
                [(source, [], "Background Mesh")],
                SETTINGS,
                True,
            ),
            minimum_heartbeats=2,
        )
        first_report = first["tessellated"][0]
        first_mesh = document.getObject(first_report["created"]["object_name"])
        assert first_mesh is not None
        assert first_mesh.TypeId == "MeshPart::MeshFromShape"
        assert first_mesh.UpdateFromSource is False
        assert first_mesh.getEditorMode("Source") == ["ReadOnly"]
        assert first_mesh.getEditorMode("Method") == ["ReadOnly"]
        assert first_mesh.getEditorMode("LinearDeflection") == ["ReadOnly"]
        assert set(first_mesh.getEditorMode("UpdateFromSource")) == {
            "ReadOnly",
            "Hidden",
        }
        assert first_mesh.Mesh.CountFacets > 0
        assert first_mesh.Mesh.countSegments() == 3
        assert "FaceColors" in first_mesh.PropertiesList
        assert len(first_mesh.FaceColors) == first_mesh.Mesh.CountFacets
        assert first_report["tessellation"]["background"] is True

        before = int(first_mesh.Mesh.CountFacets)
        started = time.monotonic()
        assert document.recompute([first_mesh], True, True) is not False
        assert time.monotonic() - started < 0.25
        assert int(first_mesh.Mesh.CountFacets) == before

        second = _wait(
            start_shape_tessellations(
                [(source, [], "Cached Background Mesh")],
                SETTINGS,
            ),
            minimum_heartbeats=1,
        )
        second_report = second["tessellated"][0]
        assert second_report["tessellation"]["cache_hit"] is True
        second_mesh = document.getObject(second_report["created"]["object_name"])
        assert second_mesh is not None and second_mesh.Mesh.CountFacets == before

        selected_face = _wait(
            start_shape_tessellations(
                [(source, ["Face1"], "Selected Face Mesh")],
                SETTINGS,
            ),
            minimum_heartbeats=2,
        )
        selected_face_report = selected_face["tessellated"][0]
        selected_face_mesh = document.getObject(
            selected_face_report["created"]["object_name"]
        )
        assert selected_face_mesh is not None
        assert selected_face_mesh.Source[0] is source
        assert tuple(selected_face_mesh.Source[1]) == ("Face1",)
        assert 0 < selected_face_mesh.Mesh.CountFacets < before
        assert selected_face_report["tessellation"]["cache_hit"] is False

        mefisto = _wait(
            start_shape_tessellations(
                [(source, [], "Background Mefisto Mesh")],
                MEFISTO_SETTINGS,
            ),
            minimum_heartbeats=2,
        )
        mefisto_report = mefisto["tessellated"][0]
        mefisto_mesh = document.getObject(mefisto_report["created"]["object_name"])
        assert mefisto_mesh is not None
        assert mefisto_mesh.Method == "Mefisto"
        assert mefisto_mesh.Mesh.CountFacets > 0
        assert mefisto_report["tessellation"]["background"] is True
        assert mefisto_report["tessellation"]["cache_hit"] is False

        second_name = second_mesh.Name
        selected_face_name = selected_face_mesh.Name
        mefisto_name = mefisto_mesh.Name
        document.undo()
        assert document.getObject(mefisto_name) is None
        document.undo()
        assert document.getObject(selected_face_name) is None
        document.undo()
        assert document.getObject(second_name) is None
        document.redo()
        document.redo()
        document.redo()
        second_mesh = document.getObject(second_name)
        assert second_mesh is not None and second_mesh.Mesh.CountFacets == before

        cancel_source = document.addObject("Part::Sphere", "CancelledSource")
        cancel_source.Radius = 17.0 + (time.time_ns() % 1_000_000_000) / 1.0e12
        assert document.recompute([cancel_source], True, True) is not False
        before_cancel = tuple(document.Objects)
        cancel_settings = dict(SETTINGS)
        cancel_settings["linear_deflection_mm"] = 0.193731
        cancel_job_id = start_shape_tessellations(
            [(cancel_source, [], "Must Not Be Published")],
            cancel_settings,
        )
        manager = get_service().native_background_manager()
        assert manager.cancel(cancel_job_id)
        deadline = time.monotonic() + 30.0
        cancelled = None
        while time.monotonic() < deadline:
            _events()
            cancelled = manager.snapshot(cancel_job_id)
            if cancelled.terminal:
                break
            time.sleep(0.01)
        assert cancelled is not None and cancelled.phase == "cancelled", cancelled
        assert tuple(document.Objects) == before_cancel

        document.save()
        names = (
            source.Name,
            first_mesh.Name,
            second_mesh.Name,
            selected_face_name,
            mefisto_name,
        )
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        reopened = tuple(document.getObject(name) for name in names)
        assert all(item is not None for item in reopened)
        assert reopened[1].Mesh.CountFacets == before
        assert reopened[2].Mesh.CountFacets == before
        assert 0 < reopened[3].Mesh.CountFacets < before
        assert reopened[4].Mesh.CountFacets > 0

        print(
            "VIBECAD_MESH_TESSELLATION_GUI_OK "
            f"facets={before} cache_hit={second_report['tessellation']['cache_hit']}",
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


QtCore.QTimer.singleShot(500, _run)
