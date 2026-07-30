# SPDX-License-Identifier: LGPL-2.1-or-later

import unittest

import FreeCAD

try:
    import FreeCADGui

    GUI_MODULE_AVAILABLE = True
except ImportError:
    FreeCADGui = None
    GUI_MODULE_AVAILABLE = False

try:
    from PySide import QtCore, QtGui

    QT_MODULE_AVAILABLE = True
except ImportError:
    QtCore = None
    QtGui = None
    QT_MODULE_AVAILABLE = False


def gui_available():
    if not GUI_MODULE_AVAILABLE:
        return False

    try:
        return FreeCADGui.getMainWindow() is not None
    except (AttributeError, RuntimeError):
        return False


class SketcherGuiTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        if not gui_available():
            self.skipTest("GUI not available")
        self.drain_deferred_deletes()
        self._document_uids_before_test = frozenset(
            str(doc.Uid)
            for doc in FreeCAD.listDocuments().values()
        )
        active_document = FreeCAD.activeDocument()
        self._active_document_uid_before_test = (
            str(active_document.Uid)
            if active_document is not None
            else None
        )
        self._qt_widget_refs = []

    def tearDown(self):
        try:
            self._qt_widget_refs.clear()
            self.cleanup_gui_documents()
        finally:
            super().tearDown()

    def drain_deferred_deletes(self):
        """Finish Qt widget destruction queued by a document close.

        ``Gui::Document`` detaches its MDI views synchronously, but Qt owns
        their final destruction through ``deleteLater()``.  Merely spinning a
        nested event loop is not sufficient to guarantee delivery of those
        ``DeferredDelete`` events.  A following test can otherwise activate a
        detached MDI view and receive a Python wrapper whose C++ widget is then
        deleted underneath it.
        """
        if not gui_available() or not QT_MODULE_AVAILABLE:
            return

        QtGui.QApplication.processEvents()
        FreeCADGui.updateGui()
        QtCore.QCoreApplication.sendPostedEvents(
            None,
            QtCore.QEvent.DeferredDelete,
        )
        QtGui.QApplication.processEvents()

    def pump(self, timeout_ms=50):
        if not QT_MODULE_AVAILABLE:
            return

        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec_()

    def flush_gui(self, timeout_ms=0):
        if not gui_available():
            return

        if QT_MODULE_AVAILABLE:
            QtGui.QApplication.processEvents()

        FreeCADGui.updateGui()

        if timeout_ms:
            self.pump(timeout_ms)

    def unique_document_name(self, stem):
        return "_".join(
            (
                stem,
                type(self).__name__,
                self._testMethodName,
            )
        )

    def new_document(self, stem):
        document_name = self.unique_document_name(stem)
        if document_name in FreeCAD.listDocuments():
            self.fail(
                "A previous GUI test left its exact document open: "
                + document_name
            )
        return FreeCAD.newDocument(document_name)

    def _gui_document(self, document_name):
        try:
            return FreeCADGui.getDocument(document_name)
        except (NameError, RuntimeError):
            return None

    @staticmethod
    def _document_name(doc):
        try:
            return doc.Name if doc is not None else None
        except RuntimeError:
            return None

    def close_gui_document(self, doc):
        if not gui_available():
            return

        document_name = self._document_name(doc)
        if not document_name or document_name not in FreeCAD.listDocuments():
            self.drain_deferred_deletes()
            return

        self._qt_widget_refs.clear()
        gui_doc = self._gui_document(document_name)
        if gui_doc is not None:
            if gui_doc.getInEdit() is not None:
                gui_doc.resetEdit()
            self.flush_gui()

        active_document = FreeCAD.activeDocument()
        if (
            active_document is not None
            and active_document.Name == document_name
            and FreeCADGui.Control.activeDialog() is not None
        ):
            FreeCADGui.Control.closeDialog()
            self.flush_gui()

        if document_name in FreeCAD.listDocuments():
            FreeCAD.closeDocument(document_name)
        self.drain_deferred_deletes()

    def cleanup_gui_documents(self):
        if not gui_available():
            return

        created_document_names = tuple(
            name
            for name, doc in FreeCAD.listDocuments().items()
            if str(doc.Uid) not in self._document_uids_before_test
        )
        primary_document = getattr(self, "doc", None)
        primary_name = self._document_name(primary_document)
        if primary_name in created_document_names:
            created_document_names = (
                primary_name,
                *(
                    name
                    for name in created_document_names
                    if name != primary_name
                ),
            )

        for document_name in created_document_names:
            current_documents = FreeCAD.listDocuments()
            if document_name in current_documents:
                self.close_gui_document(current_documents[document_name])

        previous_uid = self._active_document_uid_before_test
        previous_document = next(
            (
                doc
                for doc in FreeCAD.listDocuments().values()
                if str(doc.Uid) == previous_uid
            ),
            None,
        )
        if previous_document is not None:
            FreeCAD.setActiveDocument(previous_document.Name)
        self.drain_deferred_deletes()

    def cleanup_gui_document(self, doc, timeout_ms=80):
        """Compatibility wrapper for older Sketcher GUI tests."""
        del timeout_ms
        self.close_gui_document(doc)

    def wait_until(self, predicate, timeout_ms=1000, step_ms=50):
        remaining = timeout_ms
        while remaining > 0:
            if predicate():
                return True
            self.flush_gui(step_ms)
            remaining -= step_ms
        return predicate()

    def send_mouse(self, widget, event_type, pos, button, buttons):
        global_pos = widget.mapToGlobal(pos)
        event = QtGui.QMouseEvent(
            event_type,
            pos,
            global_pos,
            button,
            buttons,
            QtCore.Qt.NoModifier,
        )
        QtGui.QApplication.sendEvent(widget, event)

    def click(self, widget, pos):
        self.send_mouse(
            widget,
            QtCore.QEvent.MouseButtonPress,
            pos,
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
        )
        self.send_mouse(
            widget,
            QtCore.QEvent.MouseButtonRelease,
            pos,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoButton,
        )
        self.pump(120)

    def right_click(self, widget, pos):
        self.send_mouse(
            widget,
            QtCore.QEvent.MouseButtonPress,
            pos,
            QtCore.Qt.RightButton,
            QtCore.Qt.RightButton,
        )
        self.send_mouse(
            widget,
            QtCore.QEvent.MouseButtonRelease,
            pos,
            QtCore.Qt.RightButton,
            QtCore.Qt.NoButton,
        )
        self.pump(120)

    def move(self, widget, pos):
        self.send_mouse(
            widget,
            QtCore.QEvent.MouseMove,
            pos,
            QtCore.Qt.NoButton,
            QtCore.Qt.NoButton,
        )
        self.pump(80)

    def key_click(self, widget, key, text=""):
        press = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier, text)
        release = QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, key, QtCore.Qt.NoModifier, text)
        QtGui.QApplication.sendEvent(widget, press)
        QtGui.QApplication.sendEvent(widget, release)
        self.pump(60)

    def clamp_to_widget(self, widget, pos, margin=10):
        rect = widget.rect()
        return QtCore.QPoint(
            max(margin, min(pos.x(), rect.right() - margin)),
            max(margin, min(pos.y(), rect.bottom() - margin)),
        )

    def device_pixel_ratio(self, widget):
        try:
            return widget.devicePixelRatioF()
        except RuntimeError:
            return 1.0

    def active_viewport(self, view=None):
        if view is None:
            view = FreeCADGui.ActiveDocument.ActiveView
        graphics_view = view.graphicsView()
        self._qt_widget_refs.append(graphics_view)
        viewport = graphics_view.viewport()
        self._qt_widget_refs.append(viewport)
        return viewport

    def viewport_to_qpoint(self, view, viewport, point):
        _, height = view.getSize()
        scale = self.device_pixel_ratio(viewport)
        x = int(round(point[0] / scale))
        y = int(round((height - point[1] - 1) / scale))
        return QtCore.QPoint(x, y)
