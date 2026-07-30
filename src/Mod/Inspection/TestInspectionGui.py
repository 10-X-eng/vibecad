# SPDX-License-Identifier: LGPL-2.1-or-later

"""Runtime coverage for Inspection's document-history contract."""

import os
from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui  # noqa: F401 - loads Inspection view providers
import Inspection  # noqa: F401 - registers Inspection document types
import InspectionGui  # noqa: F401 - registers Inspection view-provider types
import Mesh
import MeshGui  # noqa: F401 - registers Mesh view-provider types
import Part
import Points
import PointsGui  # noqa: F401 - registers Points view-provider types
from pivy import coin
from PySide import QtCore, QtGui


OBJECT_NAME_ROLE = int(QtCore.Qt.UserRole)


def _timeline_item(timeline, object_name):
    for row in range(timeline.count()):
        item = timeline.item(row)
        if item.data(OBJECT_NAME_ROLE) == object_name:
            return item
    return None


def _timeline_context_action_names(timeline, item):
    state = {}

    def inspect_menu():
        popup = QtGui.QApplication.activePopupWidget()
        if popup is None:
            state["error"] = "No active timeline context menu"
            return
        try:
            state["actions"] = {
                action.objectName()
                for action in popup.actions()
                if action.objectName()
            }
        finally:
            popup.close()

    timeline.scrollToItem(item)
    Gui.updateGui()
    QtCore.QTimer.singleShot(30, inspect_menu)
    timeline.customContextMenuRequested.emit(
        timeline.visualItemRect(item).center()
    )
    if "error" in state:
        raise AssertionError(state["error"])
    return state.get("actions", set())


def _run_command_without_modal_warning(command):
    state = {}
    watcher = QtCore.QTimer()
    watcher.setInterval(10)

    def reject_warning():
        dialog = QtGui.QApplication.activeModalWidget()
        if not isinstance(dialog, QtGui.QMessageBox):
            return
        state["warning"] = " — ".join(
            part
            for part in (
                dialog.windowTitle(),
                dialog.text(),
                dialog.informativeText(),
            )
            if part
        )
        dialog.reject()

    watcher.timeout.connect(reject_warning)
    watcher.start()
    try:
        Gui.runCommand(command)
        Gui.updateGui()
    finally:
        watcher.stop()
        reject_warning()
    if state.get("warning"):
        raise AssertionError(
            f"{command} opened an unexpected warning: "
            f"{state['warning']}"
        )


class InspectionTimelineTest(unittest.TestCase):
    def setUp(self):
        self.document = App.newDocument("InspectionTimeline")
        self.saved_file = None

    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        if self.saved_file and os.path.exists(self.saved_file):
            os.remove(self.saved_file)

    @staticmethod
    def _process_events(wait_ms=50):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        if wait_ms:
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()

    def _start_macro_recording(self, directory, name):
        def start():
            widgets = list(QtGui.QApplication.topLevelWidgets())
            main_window = Gui.getMainWindow()
            if main_window is not None:
                widgets.extend(main_window.findChildren(QtGui.QDialog))
            dialog = next(
                (
                    widget
                    for widget in widgets
                    if widget.isVisible()
                    and (
                        widget.objectName()
                        == "Gui::Dialog::DlgMacroRecord"
                        or (
                            widget.findChild(
                                QtGui.QLineEdit,
                                "lineEditMacroPath",
                            )
                            is not None
                            and widget.findChild(
                                QtGui.QPushButton,
                                "buttonStart",
                            )
                            is not None
                        )
                    )
                ),
                None,
            )
            if dialog is None:
                QtCore.QTimer.singleShot(10, start)
                return
            path = dialog.findChild(
                QtGui.QLineEdit,
                "lineEditMacroPath",
            )
            filename = dialog.findChild(
                QtGui.QLineEdit,
                "lineEditPath",
            )
            start_button = dialog.findChild(
                QtGui.QPushButton,
                "buttonStart",
            )
            self.assertIsNotNone(path)
            self.assertIsNotNone(filename)
            self.assertIsNotNone(start_button)
            path.setText(str(directory) + os.sep)
            filename.setText(name)
            start_button.click()

        QtCore.QTimer.singleShot(0, start)
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events()

    def _stop_macro_recording(self, path):
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events()
        self.assertTrue(path.is_file(), path)
        return path.read_text(encoding="utf-8")

    def _create_occurrence(
        self,
        name,
        source,
        *,
        parent_y,
        link_x,
        link_y,
    ):
        parent = self.document.addObject("App::Part", f"{name}Parent")
        parent.Placement = App.Placement(
            App.Vector(0.0, parent_y, 0.0),
            App.Rotation(),
        )
        link = self.document.addObject("App::Link", name)
        link.LinkedObject = source
        link.LinkPlacement = App.Placement(
            App.Vector(link_x, link_y, 0.0),
            App.Rotation(),
        )
        parent.addObject(link)
        return parent, link

    def _create_inspection(self, name, actual, nominal):
        result = self.document.addObject("Inspection::Feature", name)
        result.Actual = actual
        result.Nominals = [nominal]
        result.SearchRadius = 10.0
        self.document.recompute()
        self.assertNotIn("Invalid", result.State)
        return result

    def _assert_distances_zero(self, result, expected_count):
        distances = list(result.Distances)
        self.assertEqual(len(distances), expected_count)
        for distance in distances:
            self.assertAlmostEqual(distance, 0.0, places=5)

    def _inspection_scene_coordinate_sets(self, result):
        search = coin.SoSearchAction()
        search.setType(coin.SoCoordinate3.getClassTypeId())
        search.setInterest(coin.SoSearchAction.ALL)
        search.setSearchingAll(True)
        search.apply(result.ViewObject.RootNode)
        paths = search.getPaths()
        coordinate_sets = []
        for index in range(paths.getLength()):
            node = paths.get(index).getTail()
            if node.point.getNum() == 0:
                continue
            coordinate_sets.append(
                [
                    tuple(value.getValue())
                    for value in node.point.getValues()
                ]
            )
        return coordinate_sets

    def _inspection_scene_coordinates(self, result):
        coordinate_sets = self._inspection_scene_coordinate_sets(result)
        self.assertTrue(coordinate_sets)
        return max(coordinate_sets, key=len)

    def test_inspection_is_a_persistent_suppressible_timeline_operation(self):
        actual = self.document.addObject("Part::Feature", "Actual")
        actual.Shape = Part.makeBox(10, 10, 10)
        nominal = self.document.addObject("Part::Feature", "Nominal")
        nominal.Shape = Part.makeBox(10, 10, 10)

        inspection = self.document.addObject(
            "Inspection::Feature",
            "DimensionalInspection",
        )
        inspection.Actual = actual
        inspection.Nominals = [nominal]
        self.document.recompute()

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(inspection, timeline.Operations)
        self.assertTrue(
            inspection.hasExtension("App::SuppressibleExtension")
        )
        self.assertTrue(
            inspection.ViewObject.hasExtension(
                "Gui::ViewProviderSuppressibleExtension"
            )
        )
        self.assertEqual(len(inspection.Distances), 8)

        inspection.Suppressed = True
        self.document.recompute()
        self.assertEqual(list(inspection.Distances), [])

        handle, self.saved_file = tempfile.mkstemp(
            prefix="vibecad_inspection_timeline_",
            suffix=".FCStd",
        )
        os.close(handle)
        self.document.saveAs(self.saved_file)
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(self.saved_file)

        inspection = self.document.getObject("DimensionalInspection")
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(inspection)
        self.assertIsNotNone(timeline)
        self.assertIn(inspection, timeline.Operations)
        self.assertTrue(inspection.Suppressed)
        self.assertEqual(list(inspection.Distances), [])
        self.assertTrue(
            inspection.ViewObject.hasExtension(
                "Gui::ViewProviderSuppressibleExtension"
            )
        )

        inspection.Suppressed = False
        self.document.recompute()
        self.assertEqual(len(inspection.Distances), 8)

    def test_visual_inspection_is_one_operation_with_exact_source_history(self):
        if not App.GuiUp:
            self.skipTest("Visual Inspection history requires the GUI")

        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        actual = self.document.addObject("Part::Feature", "Actual")
        actual.Shape = Part.makeBox(10, 10, 10)
        nominal = self.document.addObject("Part::Feature", "Nominal")
        nominal.Shape = Part.makeBox(12, 12, 12)
        self.document.recompute()
        self._process_events()
        self.assertTrue(actual.Visibility)
        self.assertTrue(nominal.Visibility)

        def accept_visual_inspection():
            dialog = QtGui.QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            actual_tree = dialog.findChild(
                QtGui.QTreeWidget,
                "treeWidgetActual",
            )
            nominal_tree = dialog.findChild(
                QtGui.QTreeWidget,
                "treeWidgetNominal",
            )
            self.assertIsNotNone(actual_tree)
            self.assertIsNotNone(nominal_tree)

            def select(tree, object_name):
                for index in range(tree.topLevelItemCount()):
                    item = tree.topLevelItem(index)
                    if (
                        item.data(0, QtCore.Qt.UserRole)
                        == object_name
                    ):
                        item.setCheckState(0, QtCore.Qt.Checked)
                        tree.itemClicked.emit(item, 0)
                        return
                self.fail(f"{object_name} was not offered for inspection")

            select(actual_tree, actual.Name)
            select(nominal_tree, nominal.Name)
            button_box = dialog.findChild(QtGui.QDialogButtonBox)
            self.assertIsNotNone(button_box)
            ok = button_box.button(QtGui.QDialogButtonBox.Ok)
            self.assertTrue(ok.isEnabled())
            ok.click()

        QtCore.QTimer.singleShot(100, accept_visual_inspection)
        Gui.runCommand("Inspection_VisualInspection")
        self._process_events(100)
        self.assertIsNone(QtGui.QApplication.activeModalWidget())

        group = self.document.getObject("Inspection")
        self.assertIsNotNone(group)
        self.assertEqual(group.VibeCADTimelineRole, "operation")
        self.assertTrue(
            group.hasExtension("App::SuppressibleExtension")
        )
        self.assertTrue(
            group.ViewObject.hasExtension(
                "Gui::ViewProviderSuppressibleExtension"
            )
        )
        self.assertCountEqual(
            list(group.VibeCADTimelineReplacedInputs),
            [actual, nominal],
        )
        results = list(group.Group)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.VibeCADTimelineRole, "resource")
        self.assertIs(result.VibeCADTimelineOwner, group)
        self.assertEqual(
            result.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertFalse(actual.Visibility)
        self.assertFalse(nominal.Visibility)

        group_name = group.Name
        result_name = result.Name
        timeline_widget = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline_widget)
        group_item = self._wait_for(
            lambda: _timeline_item(timeline_widget, group_name)
        )
        self.assertIsNotNone(group_item)
        self.assertNotIn(
            "VibeCADTimelineEdit",
            _timeline_context_action_names(
                timeline_widget,
                group_item,
            ),
        )
        group_item = self._wait_for(
            lambda: _timeline_item(timeline_widget, group_name)
        )
        timeline_widget.itemDoubleClicked.emit(group_item)
        self._process_events()
        self.assertIsNone(Gui.activeDocument().getInEdit())

        self.document.undo()
        self._process_events(100)
        self.assertIsNone(self.document.getObject(group_name))
        self.assertTrue(actual.Visibility)
        self.assertTrue(nominal.Visibility)

        self.document.redo()
        self._process_events(100)
        group = self.document.getObject(group_name)
        actual = self.document.getObject("Actual")
        nominal = self.document.getObject("Nominal")
        result = list(group.Group)[0]
        self.assertEqual(group.VibeCADTimelineRole, "operation")
        self.assertIs(result.VibeCADTimelineOwner, group)
        self.assertFalse(actual.Visibility)
        self.assertFalse(nominal.Visibility)

        timeline = self.document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        result_index = operations.index(result)
        group_index = operations.index(group)
        self.assertEqual(group_index, result_index + 1)
        self.assertEqual(int(timeline.Position), len(operations))
        previous = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(end)
        previous.click()
        self._process_events()
        self.assertEqual(int(timeline.Position), result_index)
        self.assertTrue(group.Suppressed)
        self.assertTrue(result.Suppressed)
        self.assertTrue(actual.Visibility)
        self.assertTrue(nominal.Visibility)
        self.assertFalse(group.Visibility)
        self.assertFalse(result.Visibility)

        handle, self.saved_file = tempfile.mkstemp(
            prefix="vibecad_visual_inspection_timeline_",
            suffix=".FCStd",
        )
        os.close(handle)
        expected_position = int(timeline.Position)
        document_name = self.document.Name
        self.document.saveAs(self.saved_file)
        App.closeDocument(document_name)
        self._process_events(100)
        self.document = App.openDocument(self.saved_file)
        App.setActiveDocument(self.document.Name)
        self._process_events(150)

        timeline = self.document.getObject("VibeCADTimeline")
        group = self.document.getObject(group_name)
        actual = self.document.getObject("Actual")
        nominal = self.document.getObject("Nominal")
        result = list(group.Group)[0]
        self.assertEqual(int(timeline.Position), expected_position)
        self.assertTrue(group.Suppressed)
        self.assertIs(result.VibeCADTimelineOwner, group)
        self.assertCountEqual(
            list(group.VibeCADTimelineReplacedInputs),
            [actual, nominal],
        )
        self.assertTrue(actual.Visibility)
        self.assertTrue(nominal.Visibility)
        self.assertFalse(result.Visibility)

        end = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(end)
        end.click()
        self._process_events(100)
        self.assertFalse(group.Suppressed)
        self.assertFalse(result.Suppressed)
        self.assertFalse(actual.Visibility)
        self.assertFalse(nominal.Visibility)
        self.assertTrue(result.Visibility)

        self.document.UndoMode = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(group)
        _run_command_without_modal_warning("Std_Delete")
        self._process_events(100)
        self.assertIsNone(self.document.getObject(group_name))
        self.assertIsNone(self.document.getObject(result_name))
        self.assertTrue(actual.Visibility)
        self.assertTrue(nominal.Visibility)

        self.document.undo()
        self._process_events(100)
        group = self.document.getObject(group_name)
        result = self.document.getObject(result_name)
        actual = self.document.getObject("Actual")
        nominal = self.document.getObject("Nominal")
        self.assertIsNotNone(group)
        self.assertIsNotNone(result)
        self.assertIs(result.VibeCADTimelineOwner, group)
        self.assertIn(result, group.Group)
        self.assertFalse(actual.Visibility)
        self.assertFalse(nominal.Visibility)
        self.assertTrue(result.Visibility)

    def test_visual_inspection_macro_replay_restores_one_exact_saved_block(self):
        if not App.GuiUp:
            self.skipTest("Visual Inspection macro replay requires the GUI")

        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        actual = self.document.addObject("Part::Feature", "MacroActual")
        actual.Shape = Part.makeBox(10, 10, 10)
        nominal = self.document.addObject("Part::Feature", "MacroNominal")
        nominal.Shape = Part.makeBox(12, 12, 12)
        self.document.recompute()
        self._process_events()

        def accept_visual_inspection():
            dialog = QtGui.QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            actual_tree = dialog.findChild(
                QtGui.QTreeWidget,
                "treeWidgetActual",
            )
            nominal_tree = dialog.findChild(
                QtGui.QTreeWidget,
                "treeWidgetNominal",
            )
            self.assertIsNotNone(actual_tree)
            self.assertIsNotNone(nominal_tree)

            def select(tree, object_name):
                for index in range(tree.topLevelItemCount()):
                    item = tree.topLevelItem(index)
                    if item.data(0, QtCore.Qt.UserRole) == object_name:
                        item.setCheckState(0, QtCore.Qt.Checked)
                        tree.itemClicked.emit(item, 0)
                        return
                self.fail(f"{object_name} was not offered for inspection")

            select(actual_tree, actual.Name)
            select(nominal_tree, nominal.Name)
            button_box = dialog.findChild(QtGui.QDialogButtonBox)
            self.assertIsNotNone(button_box)
            ok = button_box.button(QtGui.QDialogButtonBox.Ok)
            self.assertTrue(ok.isEnabled())
            ok.click()

        with tempfile.TemporaryDirectory(
            prefix="vibecad-inspection-macro-"
        ) as directory:
            macro_path = Path(directory) / "VisualInspection.FCMacro"
            self._start_macro_recording(directory, "VisualInspection")
            QtCore.QTimer.singleShot(100, accept_visual_inspection)
            Gui.runCommand("Inspection_VisualInspection")
            self._process_events(100)
            macro = self._stop_macro_recording(macro_path)

        self.assertNotIn(
            "Gui.runCommand('Inspection_VisualInspection'",
            macro,
        )
        self.assertIn(
            "publishProvisionalTimelineOperationBlock(",
            macro,
        )
        self.assertLess(
            macro.index("'Inspection::Feature'"),
            macro.index("'Inspection::Group'"),
        )
        self.assertLess(
            macro.index("'Inspection::Group'"),
            macro.index(
                "publishProvisionalTimelineOperationBlock("
            ),
        )

        document_name = self.document.Name
        App.closeDocument(document_name)
        self._process_events()
        self.document = App.newDocument(document_name)
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        replay_actual = self.document.addObject(
            "Part::Feature",
            "MacroActual",
        )
        replay_actual.Shape = Part.makeBox(10, 10, 10)
        replay_nominal = self.document.addObject(
            "Part::Feature",
            "MacroNominal",
        )
        replay_nominal.Shape = Part.makeBox(12, 12, 12)
        self.document.recompute()

        execution_globals = {
            "App": App,
            "Gui": Gui,
            "FreeCAD": App,
            "FreeCADGui": Gui,
        }
        exec(
            compile(macro, str(macro_path), "exec"),
            execution_globals,
            execution_globals,
        )
        self._process_events(150)
        group = self.document.getObject("Inspection")
        self.assertIsNotNone(group)
        results = list(group.Group)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(group.VibeCADTimelineRole, "operation")
        self.assertEqual(result.VibeCADTimelineRole, "resource")
        self.assertIs(result.VibeCADTimelineOwner, group)
        self.assertCountEqual(
            list(group.VibeCADTimelineReplacedInputs),
            [replay_actual, replay_nominal],
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(
            list(timeline.Operations)[-2:],
            [result, group],
        )
        self.assertFalse(replay_actual.Visibility)
        self.assertFalse(replay_nominal.Visibility)

        handle, self.saved_file = tempfile.mkstemp(
            prefix="vibecad_visual_inspection_macro_replay_",
            suffix=".FCStd",
        )
        os.close(handle)
        group_name = group.Name
        result_name = result.Name
        self.document.saveAs(self.saved_file)
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(self.saved_file)
        self._process_events(150)

        group = self.document.getObject(group_name)
        result = self.document.getObject(result_name)
        replay_actual = self.document.getObject("MacroActual")
        replay_nominal = self.document.getObject("MacroNominal")
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(group)
        self.assertIsNotNone(result)
        self.assertIs(result.VibeCADTimelineOwner, group)
        self.assertIn(result, group.Group)
        self.assertCountEqual(
            list(group.VibeCADTimelineReplacedInputs),
            [replay_actual, replay_nominal],
        )
        self.assertEqual(
            list(timeline.Operations)[-2:],
            [result, group],
        )
        self.assertFalse(replay_actual.Visibility)
        self.assertFalse(replay_nominal.Visibility)

    def test_visual_inspection_offers_linked_part_mesh_and_points_occurrences(self):
        if not App.GuiUp:
            self.skipTest("Visual Inspection candidates require the GUI")

        part_source = self.document.addObject(
            "Part::Feature",
            "PartDefinition",
        )
        part_source.Shape = Part.makeBox(2.0, 3.0, 4.0)
        mesh_source = self.document.addObject(
            "Mesh::Feature",
            "MeshDefinition",
        )
        mesh_source.Mesh = Mesh.Mesh(
            [
                (
                    App.Vector(0.0, 0.0, 0.0),
                    App.Vector(2.0, 0.0, 0.0),
                    App.Vector(0.0, 3.0, 0.0),
                )
            ]
        )
        points_source = self.document.addObject(
            "Points::Feature",
            "PointsDefinition",
        )
        points_source.Points = Points.Points(
            [
                App.Vector(0.0, 0.0, 0.0),
                App.Vector(1.0, 2.0, 3.0),
            ]
        )

        expected = set()
        for name, source in (
            ("LinkedPart", part_source),
            ("LinkedMesh", mesh_source),
            ("LinkedPoints", points_source),
        ):
            link = self.document.addObject("App::Link", name)
            link.LinkedObject = source
            expected.add(link.Name)
        self.document.recompute()
        self._process_events()

        observed = {}

        def inspect_candidates():
            dialog = QtGui.QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            tree = dialog.findChild(
                QtGui.QTreeWidget,
                "treeWidgetActual",
            )
            self.assertIsNotNone(tree)
            observed["names"] = {
                tree.topLevelItem(index).data(
                    0,
                    QtCore.Qt.UserRole,
                )
                for index in range(tree.topLevelItemCount())
            }
            dialog.reject()

        QtCore.QTimer.singleShot(100, inspect_candidates)
        Gui.runCommand("Inspection_VisualInspection")
        self._process_events(100)
        self.assertTrue(
            expected.issubset(observed.get("names", set())),
            (expected, observed),
        )

    def test_linked_points_use_exact_occurrences_through_undo_and_reopen(self):
        self.document.UndoMode = True
        points = [
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(2.0, 0.0, 0.0),
            App.Vector(0.0, 3.0, 0.0),
            App.Vector(0.0, 0.0, 4.0),
        ]
        actual_source = self.document.addObject(
            "Points::Feature",
            "ActualPointsDefinition",
        )
        actual_source.Points = Points.Points(points)
        actual_source.Placement = App.Placement(
            App.Vector(100.0, 0.0, 0.0),
            App.Rotation(),
        )
        nominal_source = self.document.addObject(
            "Points::Feature",
            "NominalPointsDefinition",
        )
        nominal_source.Points = Points.Points(points)
        nominal_source.Placement = App.Placement(
            App.Vector(200.0, 0.0, 0.0),
            App.Rotation(),
        )
        actual_parent, actual_link = self._create_occurrence(
            "ActualPointsOccurrence",
            actual_source,
            parent_y=10.0,
            link_x=0.0,
            link_y=-10.0,
        )
        nominal_parent, nominal_link = self._create_occurrence(
            "NominalPointsOccurrence",
            nominal_source,
            parent_y=20.0,
            link_x=0.0,
            link_y=-20.0,
        )
        result = self._create_inspection(
            "LinkedPointsInspection",
            actual_link,
            nominal_link,
        )
        self._assert_distances_zero(result, len(points))
        self._process_events()
        displayed = self._inspection_scene_coordinates(result)
        self.assertEqual(len(displayed), len(points))
        for shown, expected in zip(displayed, points):
            self.assertAlmostEqual(shown[0], expected.x, places=5)
            self.assertAlmostEqual(shown[1], expected.y, places=5)
            self.assertAlmostEqual(shown[2], expected.z, places=5)
        self.assertTrue(
            {
                actual_source,
                nominal_source,
                actual_parent,
                nominal_parent,
                actual_link,
                nominal_link,
            }.issubset(set(result.SourceDependencies))
        )

        self.document.clearUndos()
        self.document.openTransaction("Move nominal points occurrence")
        nominal_parent.Placement = App.Placement(
            App.Vector(0.0, 21.0, 0.0),
            App.Rotation(),
        )
        self.document.commitTransaction()
        self.document.recompute()
        for distance in result.Distances:
            self.assertAlmostEqual(distance, 1.0, places=5)

        self.document.undo()
        self.document.recompute()
        self._assert_distances_zero(result, len(points))
        self.document.redo()
        self.document.recompute()
        for distance in result.Distances:
            self.assertAlmostEqual(distance, 1.0, places=5)

        handle, self.saved_file = tempfile.mkstemp(
            prefix="vibecad_linked_points_inspection_",
            suffix=".FCStd",
        )
        os.close(handle)
        self.document.saveAs(self.saved_file)
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(self.saved_file)
        self.document.recompute()

        result = self.document.getObject("LinkedPointsInspection")
        nominal_parent = self.document.getObject(
            "NominalPointsOccurrenceParent"
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(nominal_parent)
        self._process_events()
        displayed = self._inspection_scene_coordinates(result)
        self.assertEqual(len(displayed), len(points))
        for shown, expected in zip(displayed, points):
            self.assertAlmostEqual(shown[0], expected.x, places=5)
            self.assertAlmostEqual(shown[1], expected.y, places=5)
            self.assertAlmostEqual(shown[2], expected.z, places=5)
        for distance in result.Distances:
            self.assertAlmostEqual(distance, 1.0, places=5)
        nominal_parent.Placement = App.Placement(
            App.Vector(0.0, 20.0, 0.0),
            App.Rotation(),
        )
        self.document.recompute()
        self._assert_distances_zero(result, len(points))

        result.Actual = None
        self._process_events()
        self.assertEqual(
            self._inspection_scene_coordinate_sets(result),
            [],
        )

    def test_linked_mesh_and_part_follow_parent_occurrence_placement(self):
        cases = (
            (
                "Mesh",
                "Mesh::Feature",
                lambda feature: setattr(
                    feature,
                    "Mesh",
                    Mesh.Mesh(
                        [
                            (
                                App.Vector(0.0, 0.0, 0.0),
                                App.Vector(2.0, 0.0, 0.0),
                                App.Vector(0.0, 3.0, 0.0),
                            ),
                            (
                                App.Vector(0.0, 0.0, 0.0),
                                App.Vector(0.0, 0.0, 4.0),
                                App.Vector(2.0, 0.0, 0.0),
                            ),
                            (
                                App.Vector(0.0, 0.0, 0.0),
                                App.Vector(0.0, 3.0, 0.0),
                                App.Vector(0.0, 0.0, 4.0),
                            ),
                            (
                                App.Vector(2.0, 0.0, 0.0),
                                App.Vector(0.0, 0.0, 4.0),
                                App.Vector(0.0, 3.0, 0.0),
                            ),
                        ]
                    ),
                ),
                4,
            ),
            (
                "Part",
                "Part::Feature",
                lambda feature: setattr(
                    feature,
                    "Shape",
                    Part.makeBox(2.0, 3.0, 4.0),
                ),
                None,
            ),
        )

        for prefix, type_id, assign_geometry, expected_count in cases:
            with self.subTest(source_kind=prefix):
                actual_source = self.document.addObject(
                    type_id,
                    f"{prefix}ActualDefinition",
                )
                assign_geometry(actual_source)
                actual_source.Placement = App.Placement(
                    App.Vector(100.0, 0.0, 0.0),
                    App.Rotation(),
                )
                nominal_source = self.document.addObject(
                    type_id,
                    f"{prefix}NominalDefinition",
                )
                assign_geometry(nominal_source)
                nominal_source.Placement = App.Placement(
                    App.Vector(200.0, 0.0, 0.0),
                    App.Rotation(),
                )
                actual_parent, actual_link = self._create_occurrence(
                    f"{prefix}ActualOccurrence",
                    actual_source,
                    parent_y=10.0,
                    link_x=0.0,
                    link_y=-10.0,
                )
                nominal_parent, nominal_link = self._create_occurrence(
                    f"{prefix}NominalOccurrence",
                    nominal_source,
                    parent_y=20.0,
                    link_x=0.0,
                    link_y=-20.0,
                )
                result = self._create_inspection(
                    f"{prefix}OccurrenceInspection",
                    actual_link,
                    nominal_link,
                )
                initial = list(result.Distances)
                self.assertGreater(len(initial), 0)
                if expected_count is not None:
                    self.assertEqual(len(initial), expected_count)
                for distance in initial:
                    self.assertAlmostEqual(distance, 0.0, places=5)
                self.assertIn(actual_parent, result.SourceDependencies)
                self.assertIn(nominal_parent, result.SourceDependencies)

                nominal_parent.Placement = App.Placement(
                    App.Vector(0.0, 21.0, 0.0),
                    App.Rotation(),
                )
                self.document.recompute()
                moved = list(result.Distances)
                self.assertEqual(len(moved), len(initial))
                self.assertTrue(
                    any(abs(distance) > 0.1 for distance in moved),
                    moved,
                )

                self.document.removeObject(result.Name)
                self.document.removeObject(actual_parent.Name)
                self.document.removeObject(nominal_parent.Name)
                self.document.removeObject(actual_link.Name)
                self.document.removeObject(nominal_link.Name)
                self.document.removeObject(actual_source.Name)
                self.document.removeObject(nominal_source.Name)
                self.document.recompute()

    @classmethod
    def _wait_for(cls, predicate, timeout_ms=10000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            cls._process_events(0)
            try:
                value = predicate()
            except RuntimeError:
                value = None
            if value:
                return value
        return None
