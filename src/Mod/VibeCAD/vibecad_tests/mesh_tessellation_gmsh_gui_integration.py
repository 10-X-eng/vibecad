# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for process-isolated Gmsh shape tessellation."""

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
    "method": "gmsh",
    "algorithm": 2,
    "minimum_size_mm": 0.0,
    "maximum_size_mm": 2.5,
    "geometry_tolerance_mm": 1.0e-6,
    "element_order": 2,
    "optimize": True,
    "executable": "gmsh",
    "timeout_seconds": 120,
}


def _events() -> None:
    Gui.updateGui()
    QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _wait(job_id: str, *, minimum_heartbeats: int) -> dict:
    manager = get_service().native_background_manager()
    heartbeats = 0
    deadline = time.monotonic() + 180.0
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
    raise AssertionError(f"Gmsh Mesh From Shape did not finish: {job_id}")


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        VibeCADGui._ensure_document_thread_invoker()
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-gmsh-tessellation-")
        path = Path(temporary.name) / "gmsh-mesh-from-shape.FCStd"
        document = App.newDocument("GmshTessellationGate")
        source = document.addObject("Part::Box", "GmshSource")
        source.Length = 19.0 + (time.time_ns() % 1_000_000_000) / 1.0e12
        source.Width = 13.0
        source.Height = 7.0
        assert document.recompute([source], True, True) is not False
        document.saveAs(str(path))

        first = _wait(
            start_shape_tessellations(
                [(source, [], "Background Gmsh Mesh")],
                SETTINGS,
            ),
            minimum_heartbeats=2,
        )
        first_report = first["tessellated"][0]
        first_mesh = document.getObject(first_report["created"]["object_name"])
        assert first_mesh is not None
        assert first_mesh.Method == "Gmsh"
        assert first_mesh.UpdateFromSource is False
        assert first_mesh.Mesh.CountFacets > 0
        assert first_report["tessellation"]["background"] is True
        assert first_report["tessellation"]["cache_hit"] is False

        second = _wait(
            start_shape_tessellations(
                [(source, [], "Cached Gmsh Mesh")],
                SETTINGS,
            ),
            minimum_heartbeats=1,
        )
        second_report = second["tessellated"][0]
        assert second_report["tessellation"]["cache_hit"] is True
        second_mesh = document.getObject(second_report["created"]["object_name"])
        assert second_mesh is not None
        assert second_mesh.Mesh.CountFacets == first_mesh.Mesh.CountFacets

        document.save()
        name = second_mesh.Name
        facets = int(second_mesh.Mesh.CountFacets)
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        reopened = document.getObject(name)
        assert reopened is not None and reopened.Mesh.CountFacets == facets

        print(
            "VIBECAD_GMSH_TESSELLATION_GUI_OK "
            f"facets={facets} cache_hit={second_report['tessellation']['cache_hit']}",
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
