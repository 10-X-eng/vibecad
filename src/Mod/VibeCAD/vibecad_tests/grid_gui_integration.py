# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI coverage for the native VibeCAD grid command."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Spreadsheet  # noqa: F401 - registers Spreadsheet document types
from PySide import QtCore, QtGui

import VibeCADGrid


DRAFT_PARAMETERS = "User parameter:BaseApp/Preferences/Mod/Draft"


class TestVibeCADGridCommand(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        self.parameters = App.ParamGet(DRAFT_PARAMETERS)
        self.original_visible = self.parameters.GetBool("alwaysShowGrid", False)
        self.document = App.newDocument("VibeCADGridCommand")
        Gui.activateView("Gui::View3DInventor", True)
        VibeCADGrid.setup()
        VibeCADGrid.toggle_grid(False)
        self._wait_until(lambda: not VibeCADGrid.is_grid_visible())

    def tearDown(self):
        VibeCADGrid.toggle_grid(self.original_visible)
        self._process_events()
        if "VibeCADGridCommand" in App.listDocuments():
            App.closeDocument("VibeCADGridCommand")
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
        actions = Gui.Command.get("VibeCAD_ToggleGrid").getAction()
        if not actions:
            return None
        return actions[0]

    @staticmethod
    def _tracker_for(view):
        snapper = getattr(Gui, "Snapper", None)
        if snapper is None:
            return None
        index = VibeCADGrid._tracker_index(snapper, view)
        return None if index is None else snapper.trackers[1][index]

    @classmethod
    def _view_renders_grid(cls, view):
        tracker = cls._tracker_for(view)
        return tracker is not None and VibeCADGrid._grid_is_rendered(view, tracker)

    def test_native_command_toggles_preference_action_and_live_tracker(self):
        command_name = "VibeCAD_ToggleGrid"
        self.assertTrue(Gui.isCommandActive(command_name))
        action = self._command_action()
        self.assertIsNotNone(action)
        self.assertTrue(action.isCheckable())
        self.assertFalse(action.isChecked())

        Gui.runCommand(command_name, 0)
        self.assertTrue(
            self._wait_until(VibeCADGrid.is_grid_visible),
            "Grid command did not create and show a tracker in the active 3D view.",
        )
        self.assertTrue(self.parameters.GetBool("alwaysShowGrid", False))
        self.assertTrue(Gui.isCommandActive(command_name))
        self.assertTrue(action.isChecked())

        snapper = getattr(Gui, "Snapper", None)
        self.assertIsNotNone(snapper)
        self.assertTrue(snapper.trackers[1])
        self.assertTrue(
            any(bool(getattr(tracker, "Visible", False)) for tracker in snapper.trackers[1])
        )

        Gui.runCommand(command_name, 0)
        self.assertTrue(
            self._wait_until(lambda: not VibeCADGrid.is_grid_visible()),
            "Grid command did not hide all live trackers.",
        )
        self.assertFalse(self.parameters.GetBool("alwaysShowGrid", True))
        self.assertTrue(Gui.isCommandActive(command_name))
        self.assertFalse(action.isChecked())
        self.assertTrue(
            all(
                not bool(getattr(tracker, "Visible", False))
                for tracker in snapper.trackers[1]
            )
        )

    def test_spreadsheet_and_3d_view_switching_never_drives_coin_from_the_sheet(self):
        VibeCADGrid.toggle_grid(True)
        self.assertTrue(
            self._wait_until(VibeCADGrid.is_grid_visible),
            "Grid was not visible before the MDI switching stress test.",
        )
        sheet = self.document.addObject("Spreadsheet::Sheet", "GridSwitchSheet")
        sheet.set("A1", "VibeCAD grid MDI safety")
        self.document.recompute()

        for _index in range(25):
            sheet.ViewObject.showSheetMdi()
            self._process_events(20)
            active_window = Gui.getMainWindow().getActiveWindow()
            self.assertIsNotNone(active_window)
            self.assertFalse(
                hasattr(active_window, "getSceneGraph"),
                "Spreadsheet activation still resolved as a 3D MDI view.",
            )

            Gui.activateView("Gui::View3DInventor", False)
            self._process_events(20)
            active_window = Gui.getMainWindow().getActiveWindow()
            self.assertIsNotNone(active_window)
            self.assertTrue(
                hasattr(active_window, "getSceneGraph"),
                "The existing 3D view did not regain MDI focus.",
            )

        self.assertTrue(
            self._wait_until(VibeCADGrid.is_grid_visible),
            "Grid did not remain usable after spreadsheet/3D MDI switching.",
        )

    def test_workbench_switch_restores_the_enabled_grid_in_the_exact_scene(self):
        VibeCADGrid.toggle_grid(True)
        view = VibeCADGrid._active_3d_view()
        self.assertIsNotNone(view)
        self.assertTrue(self._wait_until(lambda: self._view_renders_grid(view)))

        for workbench in (
            "MeshWorkbench",
            "AssemblyWorkbench",
            "PartDesignWorkbench",
        ):
            tracker = self._tracker_for(view)
            self.assertIsNotNone(tracker)
            tracker.off()
            self.assertFalse(self._view_renders_grid(view))

            Gui.activateWorkbench(workbench)
            self.assertTrue(
                self._wait_until(lambda: self._view_renders_grid(view)),
                f"The enabled grid was not restored after activating {workbench}.",
            )
            self.assertIs(VibeCADGrid._active_3d_view(), view)
            self.assertGreaterEqual(view.getSceneGraph().findChild(tracker.switch), 0)

        VibeCADGrid.toggle_grid(False)
        Gui.activateWorkbench("MeshWorkbench")
        self._process_events()
        self.assertFalse(
            self._view_renders_grid(view),
            "Workbench activation overrode the user's disabled grid preference.",
        )

    def test_rapid_view_creation_never_cross_attaches_grid_scene_nodes(self):
        App.closeDocument(self.document.Name)
        self._process_events()
        VibeCADGrid.toggle_grid(True)

        first_document = App.newDocument("VibeCADGridRaceFirst")
        Gui.activateView("Gui::View3DInventor", True)
        first_view = VibeCADGrid._active_3d_view()
        self.assertIsNotNone(first_view)
        VibeCADGrid._show_grid_in_active_view()
        first_grid = self._tracker_for(first_view)
        self.assertIsNotNone(first_grid)

        # Do not process the queued Draft insertion before activating the
        # second view. The old implementation resolved the global active view
        # inside that callback and inserted first_grid into second_view.
        second_document = App.newDocument("VibeCADGridRaceSecond")
        Gui.activateView("Gui::View3DInventor", True)
        second_view = VibeCADGrid._active_3d_view()
        self.assertIsNotNone(second_view)
        self.assertIsNot(second_view, first_view)
        VibeCADGrid._show_grid_in_active_view()
        second_grid = self._tracker_for(second_view)
        self.assertIsNotNone(second_grid)
        self._process_events(1000)

        first_scene = first_view.getSceneGraph()
        second_scene = second_view.getSceneGraph()
        self.assertGreaterEqual(first_scene.findChild(first_grid.switch), 0)
        self.assertEqual(second_scene.findChild(first_grid.switch), -1)
        self.assertGreaterEqual(second_scene.findChild(second_grid.switch), 0)
        self.assertEqual(first_scene.findChild(second_grid.switch), -1)
        self.assertTrue(VibeCADGrid._grid_is_rendered(first_view, first_grid))
        self.assertTrue(VibeCADGrid._grid_is_rendered(second_view, second_grid))

        App.closeDocument(second_document.Name)
        App.closeDocument(first_document.Name)
        self._process_events(500)
        tracked_views = getattr(Gui.Snapper, "trackers", [[]])[0]
        self.assertTrue(all(candidate is not first_view for candidate in tracked_views))
        self.assertTrue(all(candidate is not second_view for candidate in tracked_views))


if __name__ == "__main__":
    unittest.main()
