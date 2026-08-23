# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI coverage for the native VibeCAD Section View command."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui
try:
    from PySide import QtWidgets
except ImportError:  # pragma: no cover - PySide1 compatibility
    QtWidgets = QtGui
from pivy import coin

import VibeCADSectionView


class TestVibeCADSectionViewCommand(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        self.document = App.newDocument("VibeCADSectionViewCommand")
        Gui.activateView("Gui::View3DInventor", True)
        box = self.document.addObject("Part::Box", "SectionBox")
        box.Length = 40.0
        box.Width = 20.0
        box.Height = 10.0
        self.document.recompute()
        view = Gui.ActiveDocument.ActiveView
        if VibeCADSectionView.is_section_view_active(view):
            VibeCADSectionView.set_section_view(False, view=view, document=self.document)
        self._wait_until(lambda: not VibeCADSectionView.is_section_view_active(view))

    def tearDown(self):
        view = None
        try:
            view = Gui.ActiveDocument.ActiveView
        except Exception:
            view = None
        try:
            import VibeCADSectionViewGui

            VibeCADSectionViewGui.close_section_view_dialog()
        except Exception:
            pass
        if view is not None and VibeCADSectionView.is_section_view_active(view):
            VibeCADSectionView.set_section_view(False, view=view, document=self.document)
        self._process_events()
        if "VibeCADSectionViewCommand" in App.listDocuments():
            App.closeDocument("VibeCADSectionViewCommand")
        self._process_events()

    @staticmethod
    def _process_events(wait_ms=20):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(wait_ms, loop.quit)
        loop.exec()

    def _wait_until(self, predicate, timeout_ms=5000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            self._process_events()
            if predicate():
                return True
        return False

    @staticmethod
    def _command_action():
        actions = Gui.Command.get("VibeCAD_SectionView").getAction()
        if not actions:
            return None
        return actions[0]

    @staticmethod
    def _scene_clip_nodes(view):
        scene = view.getSceneGraph()
        children = tuple(scene.getChildren()) if scene is not None else ()
        return tuple(
            node
            for node in children
            if type(node).__name__ in {"SoClipPlane", "SoClipPlaneManip"}
        )

    @staticmethod
    def _section_dialog():
        application = QtGui.QApplication.instance()
        if application is None:
            return None
        for widget in application.topLevelWidgets():
            if widget.objectName() == "VibeCADSectionViewDialog":
                return widget
        return None

    def test_native_command_toggles_clip_plane_action_and_scene(self):
        command_name = "VibeCAD_SectionView"
        self.assertTrue(Gui.isCommandActive(command_name))
        action = self._command_action()
        self.assertIsNotNone(action)
        self.assertTrue(action.isCheckable())
        self.assertFalse(action.isChecked())

        view = Gui.ActiveDocument.ActiveView
        Gui.runCommand(command_name, 0)
        self.assertTrue(
            self._wait_until(lambda: VibeCADSectionView.is_section_view_active(view)),
            "Section View did not enable a clipping plane in the active 3D view.",
        )
        self.assertTrue(Gui.isCommandActive(command_name))
        Gui.Command.get(command_name).getAction()[0]
        self.assertTrue(view.hasClippingPlane())
        clip_nodes = self._scene_clip_nodes(view)
        self.assertTrue(clip_nodes or view.hasClippingPlane())
        self.assertFalse(
            any(type(node).__name__ == "SoClipPlaneManip" for node in clip_nodes),
            "Section View must not use the Coin clip manipulator.",
        )
        dialog = self._section_dialog()
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertIsNotNone(dialog.findChild(QtWidgets.QRadioButton, "planeFront"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QRadioButton, "planeTop"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QRadioButton, "planeRight"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QDoubleSpinBox, "sectionOffset"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QPushButton, "sectionFlip"))

        top = dialog.findChild(QtWidgets.QRadioButton, "planeTop")
        top.setChecked(True)
        self._process_events()
        self.assertEqual(VibeCADSectionView.current_section_view_settings().plane, "top")
        self.assertTrue(view.hasClippingPlane())

        Gui.runCommand(command_name, 0)
        self.assertTrue(
            self._wait_until(
                lambda: not VibeCADSectionView.is_section_view_active(view)
            ),
            "Section View did not remove the clipping plane.",
        )
        self.assertTrue(Gui.isCommandActive(command_name))
        self.assertFalse(view.hasClippingPlane())
        self.assertTrue(
            self._wait_until(lambda: self._section_dialog() is None),
            "Section View did not close its editor dialog.",
        )
