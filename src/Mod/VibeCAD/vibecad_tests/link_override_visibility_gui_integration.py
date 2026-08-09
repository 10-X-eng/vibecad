# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI regression for selecting a linked Body result with independent visibility."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from pivy import coin
from PySide import QtCore, QtGui, QtWidgets


def _process_events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _linked_target(link):
    value = link.LinkedObject
    if isinstance(value, tuple):
        return value[0] if value else None
    return value


def _exercise_saved_nested_links(path: Path) -> None:
    before_digest = _file_digest(path)
    document = App.openDocument(str(path))
    try:
        _process_events(24)
        nested_links = [
            obj
            for obj in document.Objects
            if obj.isDerivedFrom("App::Link")
            and (_linked_target(obj) is not None)
            and _linked_target(obj).isDerivedFrom("App::Link")
        ]
        assert nested_links
        assemblies = [
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        ]
        assert len(assemblies) == 1
        assembly = assemblies[0]
        assembly_view_provider = Gui.getDocument(document.Name).getObject(assembly.Name)

        eligible = 0
        resolved = 0
        for occurrence in nested_links:
            linked_occurrence = _linked_target(occurrence)
            definition = linked_occurrence.LinkedObject
            if not isinstance(definition, tuple) or len(definition) < 2:
                continue
            container, body_subname = definition[:2]
            body = container.getSubObject(body_subname, retType=1)
            tip = getattr(body, "Tip", None)
            if tip is None or tip.Shape.isNull() or not tip.Shape.Faces:
                continue
            eligible += 1

            detail_path = coin.SoPath()
            view_provider = Gui.getDocument(document.Name).getObject(occurrence.Name)
            view_provider.getDetailPath(f"{tip.Name}.Face1", detail_path, True)
            assert detail_path.getLength() > 0, occurrence.Name

            Gui.Selection.setPreselection(occurrence, f"{tip.Name}.Face1")
            _process_events(2)

            assembly_subname = f"{occurrence.Name}.{tip.Name}.Face1"
            assembly_detail_path = coin.SoPath()
            assembly_view_provider.getDetailPath(
                assembly_subname,
                assembly_detail_path,
                True,
            )
            assert assembly_detail_path.getLength() > 0, assembly_subname
            Gui.Selection.setPreselection(assembly, assembly_subname)
            _process_events(2)

            for visibility_target in (linked_occurrence, body, tip):
                initial_visibility = visibility_target.Visibility
                try:
                    Gui.Selection.setPreselection(assembly, assembly_subname)
                    visibility_target.Visibility = not initial_visibility
                    _process_events(4)
                    Gui.Selection.setPreselection(assembly, assembly_subname)
                    _process_events(2)
                finally:
                    visibility_target.Visibility = initial_visibility
                    _process_events(4)
                Gui.Selection.setPreselection(assembly, assembly_subname)
                _process_events(2)
            resolved += 1

        assert eligible > 0
        assert resolved == eligible
    finally:
        Gui.Selection.clearSelection()
        App.closeDocument(document.Name)
    assert _file_digest(path) == before_digest


def _exercise_saved_mouse_preselection(path: Path) -> None:
    before_digest = _file_digest(path)
    document = App.openDocument(str(path))
    try:
        _process_events(24)
        view = Gui.getDocument(document.Name).activeView()
        view.viewAxonometric()
        view.fitAll()
        _process_events(24)

        viewport = view.graphicsView().viewport()
        bounds = viewport.rect().adjusted(2, 2, -3, -3)
        step = max(8, min(bounds.width(), bounds.height()) // 24)
        resolved = set()
        application = QtWidgets.QApplication.instance()
        for y in range(bounds.top(), bounds.bottom() + 1, step):
            for x in range(bounds.left(), bounds.right() + 1, step):
                position = QtCore.QPoint(x, y)
                event = QtGui.QMouseEvent(
                    QtCore.QEvent.MouseMove,
                    position,
                    viewport.mapToGlobal(position),
                    QtCore.Qt.NoButton,
                    QtCore.Qt.NoButton,
                    QtCore.Qt.NoModifier,
                )
                application.sendEvent(viewport, event)
                application.processEvents(QtCore.QEventLoop.AllEvents, 5)
                preselection = Gui.Selection.getPreselection()
                if preselection.ObjectName:
                    resolved.add(
                        (
                            preselection.ObjectName,
                            tuple(preselection.SubElementNames),
                        )
                    )

        assert resolved
        assert any(
            document.getObject(subelement.split(".", 1)[0]).isDerivedFrom("App::Link")
            for _name, subelements in resolved
            for subelement in subelements
            if document.getObject(subelement.split(".", 1)[0]) is not None
        )
    finally:
        Gui.Selection.clearSelection()
        App.closeDocument(document.Name)
    assert _file_digest(path) == before_digest


def _exercise_open_with_mouse_activity(path: Path) -> None:
    before_digest = _file_digest(path)
    application = QtWidgets.QApplication.instance()
    emitted_events = 0
    opening_document_name = path.stem
    target_events_during_open = 0
    open_complete = False

    def emit_mouse_event() -> None:
        nonlocal emitted_events, target_events_during_open
        gui_document = Gui.activeDocument()
        if gui_document is None:
            return
        viewport = gui_document.activeView().graphicsView().viewport()
        bounds = viewport.rect().adjusted(2, 2, -3, -3)
        if bounds.isEmpty():
            return
        x = bounds.left() + (emitted_events * 37) % max(1, bounds.width())
        y = bounds.top() + (emitted_events * 53) % max(1, bounds.height())
        position = QtCore.QPoint(x, y)
        event = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove,
            position,
            viewport.mapToGlobal(position),
            QtCore.Qt.NoButton,
            QtCore.Qt.NoButton,
            QtCore.Qt.NoModifier,
        )
        emitted_events += 1
        if not open_complete and gui_document.Document.Name.endswith(
            opening_document_name
        ):
            target_events_during_open += 1
        application.sendEvent(viewport, event)

    timer = QtCore.QTimer()
    timer.timeout.connect(emit_mouse_event)
    timer.start(0)
    document = None
    try:
        document = App.openDocument(str(path))
        open_complete = True
        _process_events(24)
        assert emitted_events > 0
        assert target_events_during_open > 0
    finally:
        timer.stop()
        if document is not None and document.Name in App.listDocuments():
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)
    assert _file_digest(path) == before_digest


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        document = App.newDocument("LinkOverrideVisibilityGate")
        body = document.addObject("PartDesign::Body", "Body")
        result = body.newObject("PartDesign::Feature", "Result")
        result.Shape = Part.makeBox(10.0, 10.0, 10.0)
        body.Tip = result

        occurrence = document.addObject("App::Link", "Occurrence")
        occurrence.LinkedObject = body
        body.Visibility = False
        result.Visibility = False
        occurrence.Visibility = True
        document.recompute()

        view = Gui.activeDocument().activeView()
        view.viewAxonometric()
        view.fitAll()
        _process_events()

        path = coin.SoPath()
        view_provider = Gui.activeDocument().getObject(occurrence.Name)
        view_provider.getDetailPath("Result.Face1", path, True)
        assert path.getLength() > 0
        assert occurrence.Visibility is True
        assert body.Visibility is False
        assert result.Visibility is False

        regression_document = os.environ.get("VIBECAD_LINK_REGRESSION_DOCUMENT", "")
        if regression_document:
            _exercise_open_with_mouse_activity(Path(regression_document))
            _exercise_saved_nested_links(Path(regression_document))
            _exercise_saved_mouse_preselection(Path(regression_document))

        print("VIBECAD_LINK_OVERRIDE_VISIBILITY_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
