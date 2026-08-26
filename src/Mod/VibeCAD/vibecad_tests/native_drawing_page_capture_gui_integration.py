# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI gate for exact, multi-page Drawing screenshot capture."""

from __future__ import annotations

from pathlib import Path
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from tool_impl.service import core_capture_view_screenshot


def _events(rounds: int = 24) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _page(document, name: str, label: str, source, direction):
    page = document.addObject("TechDraw::DrawPage", name)
    page.Label = label
    template = document.addObject("TechDraw::DrawSVGTemplate", f"{name}Template")
    template.Template = str(
        Path(App.getResourceDir())
        / "Mod"
        / "TechDraw"
        / "Templates"
        / "ISO"
        / "A4_Landscape_TD.svg"
    )
    page.Template = template
    view = document.addObject("TechDraw::DrawViewPart", f"{name}View")
    view.Source = [source]
    view.Direction = direction
    view.XDirection = (
        App.Vector(0.0, 1.0, 0.0)
        if abs(float(direction.x)) > 0.5
        else App.Vector(1.0, 0.0, 0.0)
    )
    assert int(page.addView(view)) >= 1
    view.X = 145.0
    view.Y = 95.0
    return page


def _assert_capture(result, page) -> Path:
    assert result.get("ok") is True, result
    assert result.get("captured") is True, result
    assert result["target"] == {
        "frame": "drawing_page",
        "object_count": 1,
        "object_names": [page.Name],
    }
    path = Path(result["_vibecad_image_attachment"]["path"])
    assert path.is_file() and path.stat().st_size > 0
    assert result["artifact"]["path"] == str(path)
    assert result["visual_observation"].get("mostly_blank") is False
    return path


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("TechDrawWorkbench")
        document = App.newDocument("DrawingPageCaptureGate")
        source = document.addObject("Part::Feature", "CaptureSource")
        source.Shape = Part.makeBox(42.0, 30.0, 10.0)
        first = _page(
            document,
            "FabricationPage",
            "Fabrication",
            source,
            App.Vector(0.0, 0.0, 1.0),
        )
        second = _page(
            document,
            "InspectionPage",
            "Inspection",
            source,
            App.Vector(1.0, 0.0, 0.0),
        )
        assert document.recompute() is not False
        first.ViewObject.show()
        _events()

        service = get_service()
        second_capture = core_capture_view_screenshot.run(
            service,
            page_name=second.Name,
            frame="objects",
            object_names=[source.Name],
            camera={"mode": "front"},
        )
        second_path = _assert_capture(second_capture, second)

        first_capture = core_capture_view_screenshot.run(
            service,
            page_name=first.Label,
        )
        first_path = _assert_capture(first_capture, first)
        assert first_path != second_path

        active_capture = core_capture_view_screenshot.run(
            service,
            frame="selection",
            camera={"mode": "isometric"},
        )
        active_path = _assert_capture(active_capture, first)
        assert active_path not in {first_path, second_path}

        print(
            "VIBECAD_NATIVE_DRAWING_PAGE_CAPTURE_GUI_OK "
            "named_internal=true named_label=true inactive_page=true "
            "active_fallback=true multiple_images=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
