# SPDX-License-Identifier: LGPL-2.1-or-later

"""Lifecycle contracts for native Robot commands on VibeCAD ribbons."""

import __main__
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Robot
import RobotGui  # noqa: F401 - importing registers the native commands
from PySide import QtCore, QtGui

ROBOT_COMMANDS = (
    "Robot_Create",
    "Robot_AddToolShape",
    "Robot_SetDefaultOrientation",
    "Robot_SetDefaultValues",
    "Robot_CreateTrajectory",
    "Robot_InsertWaypoint",
    "Robot_InsertWaypointPreselect",
    "Robot_Edge2Trac",
    "Robot_TrajectoryDressUp",
    "Robot_TrajectoryCompound",
    "Robot_SetHomePos",
    "Robot_RestoreHomePos",
    "Robot_Simulate",
    "Robot_ExportKukaCompact",
    "Robot_ExportKukaFull",
)

OPERATION_COMMANDS = {
    "Robot_Create",
    "Robot_CreateTrajectory",
    "Robot_Edge2Trac",
    "Robot_TrajectoryDressUp",
    "Robot_TrajectoryCompound",
}

IN_PLACE_COMMANDS = {
    "Robot_AddToolShape",
    "Robot_InsertWaypoint",
    "Robot_InsertWaypointPreselect",
    "Robot_SetHomePos",
    "Robot_RestoreHomePos",
}

SESSION_COMMANDS = {
    "Robot_SetDefaultOrientation",
    "Robot_SetDefaultValues",
}

READ_ONLY_COMMANDS = {
    "Robot_Simulate",
    "Robot_ExportKukaCompact",
    "Robot_ExportKukaFull",
}


@unittest.skipIf(
    not App.GuiUp,
    "VibeCAD Robot ribbon tests require the GUI",
)
class TestVibeCADRobotRibbonTools(unittest.TestCase):
    """Every shipped robot tool must own one exact native lifecycle."""

    def setUp(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        self._process_events()
        self.documents = []
        self.document = self._new_document("VibeCADRobotRibbon")
        self.robot = self.document.addObject(
            "Robot::RobotObject",
            "TestRobot",
        )
        self.trajectory = self._add_trajectory(
            self.document,
            "PrimaryTrajectory",
            0.0,
        )
        self.second_trajectory = self._add_trajectory(
            self.document,
            "SecondTrajectory",
            40.0,
        )
        self.tool = self.document.addObject(
            "Part::Feature",
            "RobotTool",
        )
        self.tool.Shape = Part.makeCylinder(2.0, 12.0)
        self.edge_source = self.document.addObject(
            "Part::Feature",
            "PathEdge",
        )
        self.edge_source.Shape = Part.makeLine(
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(25.0, 0.0, 0.0),
        )
        self.document.recompute()
        self.document.clearUndos()

    def tearDown(self):
        Gui.Selection.clearSelection()
        Gui.Selection.clearPreselection()
        for document_name in reversed(self.documents):
            document = App.listDocuments().get(document_name)
            if document is None:
                continue
            gui_document = Gui.getDocument(document_name)
            if gui_document and Gui.Control.activeDialog(gui_document):
                Gui.Control.reject(document)
                self._process_events()
            transaction = document.getBookedTransactionID()
            if transaction:
                App.closeActiveTransaction(True, transaction)
            App.closeDocument(document_name)
        self.documents = []
        self.document = None
        self._process_events()

    @staticmethod
    def _process_events(rounds=5):
        for _ in range(rounds):
            Gui.updateGui()
            QtGui.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )

    def _new_document(self, name):
        document = App.newDocument(name)
        document.UndoMode = True
        self.documents.append(document.Name)
        App.setActiveDocument(document.Name)
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()
        return document

    @staticmethod
    def _trajectory_value(offset=0.0):
        value = Robot.Trajectory()
        for index, x_coordinate in enumerate(
            (1000.0 + offset, 1020.0 + offset),
            start=1,
        ):
            value.insertWaypoints(
                Robot.Waypoint(
                    App.Placement(
                        App.Vector(x_coordinate, 0.0, 1200.0),
                        App.Rotation(),
                    ),
                    type="LIN",
                    name=f"P{index}",
                    vel="1 m/s",
                    cont=False,
                    acc="1 m/s^2",
                    tool=1,
                )
            )
        return value

    def _add_trajectory(self, document, name, offset=0.0):
        trajectory = document.addObject(
            "Robot::TrajectoryObject",
            name,
        )
        trajectory.Trajectory = self._trajectory_value(offset)
        return trajectory

    def _select(self, *objects):
        Gui.Selection.clearSelection()
        for obj in objects:
            Gui.Selection.addSelection(obj)
        self._process_events()

    def _select_edge(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.edge_source, "Edge1")
        self._process_events()

    def _task_button(self, standard_button):
        self._process_events()
        for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not box.isVisible():
                continue
            parent = box.parentWidget()
            while parent is not None:
                if parent.metaObject().className() == "Gui::TaskView::TaskView":
                    break
                parent = parent.parentWidget()
            if parent is None:
                continue
            button = box.button(standard_button)
            if button and button.isVisible() and button.isEnabled():
                return button
        return None

    def _timeline_button(self, object_name):
        button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            object_name,
        )
        self.assertIsNotNone(button, object_name)
        self.assertTrue(button.isEnabled(), object_name)
        return button

    def _assert_single_operation(self, operation):
        timeline = operation.Document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(list(timeline.Operations).count(operation), 1)

    def _queue_file_dialogs(self, paths):
        pending = list(paths)
        seen = set()
        attempts = {"remaining": 1600}

        def respond():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QFileDialog):
                    continue
                if not widget.isVisible() or id(widget) in seen:
                    continue
                seen.add(id(widget))
                response = pending.pop(0)
                if response is None:
                    widget.reject()
                else:
                    path = Path(response)
                    widget.setDirectory(str(path.parent))
                    file_name = widget.findChild(
                        QtGui.QLineEdit,
                        "fileNameEdit",
                    )
                    if file_name is None:
                        seen.remove(id(widget))
                        break
                    file_name.setText(path.name)
                    widget.accept()
                if pending:
                    QtCore.QTimer.singleShot(5, respond)
                return
            attempts["remaining"] -= 1
            if pending and attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, respond)

        QtCore.QTimer.singleShot(0, respond)

    def _run_with_file_dialogs(self, command_name, paths):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Dialog")
        original = preferences.GetBool("DontUseNativeDialog", False)
        try:
            preferences.SetBool("DontUseNativeDialog", True)
            self._queue_file_dialogs(paths)
            Gui.runCommand(command_name, 0)
            self._process_events(12)
        finally:
            preferences.SetBool("DontUseNativeDialog", original)

    @staticmethod
    def _capture_command_warnings(command_name):
        capture = {"active": True, "messages": []}

        def respond():
            if not capture["active"]:
                return
            for widget in QtGui.QApplication.topLevelWidgets():
                if isinstance(widget, QtGui.QMessageBox) and widget.isVisible():
                    capture["messages"].append(
                        f"{widget.windowTitle()}: {widget.text()}"
                    )
                    widget.accept()
                    return
            QtCore.QTimer.singleShot(5, respond)

        QtCore.QTimer.singleShot(0, respond)
        Gui.runCommand(command_name, 0)
        capture["active"] = False
        return capture["messages"]

    def _queue_default_value_dialogs(self):
        values = {
            "Set default speed": "2 m/s",
            "Set default continuity": "True",
            "Set default acceleration": "3 m/s^2",
        }
        completed = set()
        attempts = {"remaining": 800}

        def respond():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QInputDialog):
                    continue
                if not widget.isVisible():
                    continue
                title = widget.windowTitle()
                if title in completed or title not in values:
                    continue
                value = values[title]
                combo = widget.findChild(QtGui.QComboBox)
                if combo is not None:
                    index = combo.findText(value)
                    if index >= 0:
                        combo.setCurrentIndex(index)
                else:
                    widget.setTextValue(value)
                completed.add(title)
                widget.accept()
                QtCore.QTimer.singleShot(5, respond)
                return
            attempts["remaining"] -= 1
            if len(completed) < len(values) and attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, respond)

        QtCore.QTimer.singleShot(0, respond)

    def _queue_placement_accept(self):
        attempts = {"remaining": 600}

        def respond():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QDialog):
                    continue
                if (
                    not widget.isVisible()
                    or isinstance(widget, QtGui.QInputDialog)
                    or isinstance(widget, QtGui.QFileDialog)
                    or isinstance(widget, QtGui.QMessageBox)
                ):
                    continue
                widget.accept()
                return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, respond)

        QtCore.QTimer.singleShot(0, respond)

    def _round_trip(self, path):
        old_name = self.document.Name
        self.document.saveAs(str(path))
        App.closeDocument(old_name)
        reopened = App.openDocument(str(path))
        self.documents.remove(old_name)
        self.documents.append(reopened.Name)
        self.document = reopened
        App.setActiveDocument(reopened.Name)
        self._process_events(10)
        return reopened

    def test_inventory_icons_classification_and_ribbon_placement(self):
        registered = set(Gui.listCommands())
        self.assertFalse(set(ROBOT_COMMANDS) - registered)
        Gui.activateWorkbench("CAMWorkbench")
        self._process_events()
        Gui.activateWorkbench("AssemblyWorkbench")
        self._process_events()
        for command_name in ROBOT_COMMANDS:
            actions = Gui.Command.get(command_name).getAction()
            self.assertTrue(actions, command_name)
            self.assertTrue(
                all(not action.icon().pixmap(24, 24).isNull() for action in actions),
                command_name,
            )

        contracts = (
            OPERATION_COMMANDS,
            IN_PLACE_COMMANDS,
            SESSION_COMMANDS,
            READ_ONLY_COMMANDS,
        )
        self.assertEqual(set().union(*contracts), set(ROBOT_COMMANDS))
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertFalse(contract & other)

        repository = next(
            parent
            for parent in Path(__file__).resolve().parents
            if (parent / "src/Gui/VibeCADRibbon.cpp").is_file()
        )
        ribbon = (repository / "src/Gui/VibeCADRibbon.cpp").read_text(encoding="utf-8")
        for command_name in ROBOT_COMMANDS:
            self.assertIn(f'"{command_name}"', ribbon)
        for group_name in ("Robot", "Trajectory", "Motion", "Export"):
            self.assertIn(f'QObject::tr("{group_name}")', ribbon)

    def test_every_document_mutator_refuses_caller_owned_work(self):
        self.robot.Home = [0.0] * 6
        selections = {
            "Robot_Create": (),
            "Robot_CreateTrajectory": (),
            "Robot_AddToolShape": (self.robot, self.tool),
            "Robot_InsertWaypoint": (self.robot, self.trajectory),
            "Robot_Edge2Trac": (),
            "Robot_TrajectoryDressUp": (self.trajectory,),
            "Robot_TrajectoryCompound": (),
            "Robot_SetHomePos": (self.robot,),
            "Robot_RestoreHomePos": (self.robot,),
        }
        for command_name, selection in selections.items():
            with self.subTest(command=command_name):
                if command_name == "Robot_Edge2Trac":
                    self._select_edge()
                else:
                    self._select(*selection)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                self.document.openTransaction("Caller owned")
                transaction = self.document.getBookedTransactionID()
                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    transaction,
                )
                App.closeActiveTransaction(True, transaction)

        self._select(self.trajectory)
        Gui.Selection.setPreselection(
            self.edge_source,
            "Edge1",
            3.0,
            4.0,
            5.0,
        )
        self.assertTrue(Gui.isCommandActive("Robot_InsertWaypointPreselect"))
        self.document.openTransaction("Caller owned preselection")
        transaction = self.document.getBookedTransactionID()
        self.assertFalse(Gui.isCommandActive("Robot_InsertWaypointPreselect"))
        App.closeActiveTransaction(True, transaction)

    def test_place_robot_cancel_success_undo_and_reopen(self):
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._run_with_file_dialogs("Robot_Create", [None])
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)

        with TemporaryDirectory() as temporary_directory:
            definition_directory = Path(temporary_directory) / "robot's definition"
            definition_directory.mkdir()
            vrml_path = definition_directory / "display robot.wrl"
            vrml_path.write_text(
                "#VRML V2.0 utf8\n"
                "Transform { children [ "
                "Shape { geometry Box { size 100 100 100 } } "
                "] }\n",
                encoding="utf-8",
            )
            csv_path = definition_directory / "kinematic robot.csv"
            csv_path.write_text(
                "a,alpha,d,theta,rotation,max,min,velocity\n"
                "500,-90,1045,0,-1,185,-185,156\n"
                "1300,0,0,0,1,35,-155,156\n"
                "55,90,0,-90,1,154,-130,156\n"
                "0,-90,-1025,0,1,350,-350,330\n"
                "0,90,0,0,1,130,-130,330\n"
                "0,180,-300,0,1,350,-350,615\n",
                encoding="utf-8",
            )

            self._run_with_file_dialogs(
                "Robot_Create",
                [vrml_path, None],
            )
            self.assertEqual(tuple(self.document.Objects), objects_before)
            self.assertEqual(self.document.UndoCount, undo_before)

            self._run_with_file_dialogs(
                "Robot_Create",
                [vrml_path, csv_path],
            )
            created = [
                obj
                for obj in self.document.Objects
                if obj not in objects_before and obj.TypeId == "Robot::RobotObject"
            ]
            self.assertEqual(len(created), 1)
            placed = created[0]
            self._assert_single_operation(placed)
            self.assertTrue(placed.hasExtension("App::SuppressibleExtension"))
            self.assertTrue(
                placed.ViewObject.hasExtension("Gui::ViewProviderSuppressibleExtension")
            )
            self.assertTrue(placed.RobotVrmlFile)
            self.assertTrue(placed.RobotKinematicFile)
            self.assertEqual(self.document.UndoCount, undo_before + 1)

            placed_name = placed.Name
            self.document.undo()
            self._process_events()
            self.assertIsNone(self.document.getObject(placed_name))
            self.document.redo()
            self._process_events(8)
            placed = self.document.getObject(placed_name)
            self.assertIsNotNone(placed)

            file_path = Path(temporary_directory) / "robot.FCStd"
            reopened = self._round_trip(file_path)
            restored = reopened.getObject(placed_name)
            self.assertIsNotNone(restored)
            self._assert_single_operation(restored)
            self.assertTrue(restored.RobotVrmlFile)
            self.assertTrue(restored.RobotKinematicFile)
            self.assertTrue(restored.isValid())

    def test_trajectory_operation_history_suppression_and_reopen(self):
        before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select()
        Gui.runCommand("Robot_CreateTrajectory", 0)
        self._process_events(8)
        created = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Robot::TrajectoryObject"
        ]
        self.assertEqual(len(created), 1)
        trajectory = created[0]
        self._assert_single_operation(trajectory)
        self.assertTrue(trajectory.hasExtension("App::SuppressibleExtension"))
        self.assertTrue(
            trajectory.ViewObject.hasExtension("Gui::ViewProviderSuppressibleExtension")
        )
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        name = trajectory.Name
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(name))
        self.document.redo()
        self._process_events(8)
        trajectory = self.document.getObject(name)
        self.assertIsNotNone(trajectory)

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(8)
        self.assertTrue(trajectory.Suppressed)
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(8)
        self.assertFalse(trajectory.Suppressed)

        with TemporaryDirectory() as temporary_directory:
            reopened = self._round_trip(Path(temporary_directory) / "trajectory.FCStd")
            restored = reopened.getObject(name)
            self.assertIsNotNone(restored)
            self._assert_single_operation(restored)
            self.assertFalse(restored.Suppressed)

    def test_home_tool_and_both_waypoint_commands_are_exact_edits(self):
        self.robot.Axis1 = 12.0
        self._select(self.robot)
        undo_before = self.document.UndoCount
        Gui.runCommand("Robot_SetHomePos", 0)
        self._process_events()
        self.assertEqual(list(self.robot.Home), [12.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.robot.Axis1 = 47.0
        Gui.runCommand("Robot_RestoreHomePos", 0)
        self._process_events()
        self.assertAlmostEqual(self.robot.Axis1, 12.0)
        self.document.undo()
        self._process_events()
        self.assertAlmostEqual(self.robot.Axis1, 47.0)
        self.document.redo()
        self._process_events()
        self.assertAlmostEqual(self.robot.Axis1, 12.0)

        self._select(self.robot, self.tool)
        undo_before = self.document.UndoCount
        Gui.runCommand("Robot_AddToolShape", 0)
        self._process_events()
        self.assertIs(self.robot.ToolShape, self.tool)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.robot.ToolShape)
        self.document.redo()
        self._process_events()
        self.assertIs(self.robot.ToolShape, self.tool)

        self._select(self.robot, self.trajectory)
        count_before = len(self.trajectory.Trajectory.Waypoints)
        undo_before = self.document.UndoCount
        Gui.runCommand("Robot_InsertWaypoint", 0)
        self._process_events()
        self.assertEqual(
            len(self.trajectory.Trajectory.Waypoints),
            count_before + 1,
        )
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.document.undo()
        self._process_events()
        self.assertEqual(
            len(self.trajectory.Trajectory.Waypoints),
            count_before,
        )
        self.document.redo()
        self._process_events()

        self._select(self.trajectory)
        Gui.Selection.setPreselection(
            self.edge_source,
            "Edge1",
            3.0,
            4.0,
            5.0,
        )
        self.assertTrue(Gui.isCommandActive("Robot_InsertWaypointPreselect"))
        count_before = len(self.trajectory.Trajectory.Waypoints)
        Gui.runCommand("Robot_InsertWaypointPreselect", 0)
        self._process_events()
        self.assertEqual(
            len(self.trajectory.Trajectory.Waypoints),
            count_before + 1,
        )
        waypoint = self.trajectory.Trajectory.Waypoints[-1]
        self.assertAlmostEqual(waypoint.Pos.Base.x, 3.0)
        self.assertAlmostEqual(waypoint.Pos.Base.y, 4.0)
        self.assertAlmostEqual(waypoint.Pos.Base.z, 5.0)

        value = self.trajectory.Trajectory
        size = len(value.Waypoints)
        value.deleteLast(1)
        self.assertEqual(len(value.Waypoints), size - 1)
        value.deleteLast(10000)
        self.assertEqual(len(value.Waypoints), 0)

    def test_edge_trajectory_accept_cancel_and_tree_edit(self):
        before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select_edge()
        Gui.runCommand("Robot_Edge2Trac", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNotNone(cancel)
        cancel.click()
        self._process_events(8)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertTrue(self.edge_source.Visibility)

        self._select_edge()
        Gui.runCommand("Robot_Edge2Trac", 0)
        self._process_events()
        ok = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok)
        ok.click()
        self._process_events(10)
        operations = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Robot::Edge2TracObject"
        ]
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertIs(operation.Source[0], self.edge_source)
        self.assertEqual(
            list(operation.Source[1]),
            ["Edge1"],
        )
        self.assertEqual(len(operation.Trajectory.Waypoints), 2)
        self.assertTrue(self.edge_source.Visibility)
        self._assert_single_operation(operation)

        previous = operation.SegValue
        self.assertTrue(operation.ViewObject.doubleClicked())
        self._process_events()
        operation.SegValue = 2.5
        cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNotNone(cancel)
        cancel.click()
        self._process_events(8)
        self.assertAlmostEqual(operation.SegValue, previous)

        undo_before = self.document.UndoCount
        self.assertTrue(operation.ViewObject.doubleClicked())
        self._process_events()
        operation.SegValue = 1.25
        ok = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok)
        ok.click()
        self._process_events(8)
        self.assertAlmostEqual(operation.SegValue, 1.25)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.document.undo()
        self._process_events()
        self.assertAlmostEqual(operation.SegValue, previous)

    def test_trajectory_modifier_replaces_source_and_cancels_cleanly(self):
        self._select(self.trajectory)
        before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        warnings = self._capture_command_warnings("Robot_TrajectoryDressUp")
        self.assertFalse(warnings, "\n".join(warnings))
        self._process_events()
        self.assertFalse(self.trajectory.Visibility)
        cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNotNone(cancel)
        cancel.click()
        self._process_events(8)
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertTrue(self.trajectory.Visibility)

        self._select(self.trajectory)
        Gui.runCommand("Robot_TrajectoryDressUp", 0)
        self._process_events()
        use_speed = Gui.getMainWindow().findChild(
            QtGui.QCheckBox,
            "checkBoxUseSpeed",
        )
        speed = Gui.getMainWindow().findChild(
            QtGui.QDoubleSpinBox,
            "doubleSpinBoxSpeed",
        )
        self.assertIsNotNone(use_speed)
        self.assertIsNotNone(speed)
        use_speed.setChecked(True)
        speed.setValue(2.0)
        ok = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok)
        ok.click()
        self._process_events(10)

        modifiers = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Robot::TrajectoryDressUpObject"
        ]
        self.assertEqual(len(modifiers), 1)
        modifier = modifiers[0]
        self.assertIs(modifier.Source, self.trajectory)
        self.assertEqual(
            list(modifier.VibeCADTimelineReplacedInputs),
            [self.trajectory],
        )
        self.assertFalse(self.trajectory.Visibility)
        self.assertEqual(len(modifier.Trajectory.Waypoints), 2)
        self.assertTrue(
            all(
                abs(point.Velocity - 2000.0) < 1.0e-9
                for point in modifier.Trajectory.Waypoints
            )
        )
        self._assert_single_operation(modifier)

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(8)
        self.assertTrue(modifier.Suppressed)
        self.assertTrue(self.trajectory.Visibility)
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(8)
        self.assertFalse(modifier.Suppressed)
        self.assertFalse(self.trajectory.Visibility)

    def test_trajectory_sequence_reconciles_edited_sources_and_delete(self):
        third = self._add_trajectory(
            self.document,
            "ThirdTrajectory",
            80.0,
        )
        self.document.recompute()
        self._select(self.trajectory, self.second_trajectory)
        before = set(self.document.Objects)
        Gui.runCommand("Robot_TrajectoryCompound", 0)
        self._process_events()
        ok = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok)
        ok.click()
        self._process_events(10)
        sequences = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Robot::TrajectoryCompound"
        ]
        self.assertEqual(len(sequences), 1)
        sequence = sequences[0]
        self.assertEqual(
            list(sequence.Source),
            [self.trajectory, self.second_trajectory],
        )
        self.assertEqual(len(sequence.Trajectory.Waypoints), 4)
        self.assertFalse(self.trajectory.Visibility)
        self.assertFalse(self.second_trajectory.Visibility)
        self.assertTrue(third.Visibility)
        self._assert_single_operation(sequence)

        undo_before = self.document.UndoCount
        self.assertTrue(sequence.ViewObject.doubleClicked())
        self._process_events()
        self._select(self.second_trajectory, third)
        ok = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok)
        ok.click()
        self._process_events(10)
        self.assertEqual(
            list(sequence.Source),
            [self.second_trajectory, third],
        )
        self.assertTrue(self.trajectory.Visibility)
        self.assertFalse(self.second_trajectory.Visibility)
        self.assertFalse(third.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.document.undo()
        self._process_events(8)
        self.assertEqual(
            list(sequence.Source),
            [self.trajectory, self.second_trajectory],
        )
        self.assertFalse(self.trajectory.Visibility)
        self.assertFalse(self.second_trajectory.Visibility)
        self.assertTrue(third.Visibility)
        self.document.redo()
        self._process_events(8)

        sequence_name = sequence.Name
        self._select(sequence)
        Gui.runCommand("Std_Delete", 0)
        self._process_events(10)
        self.assertIsNone(self.document.getObject(sequence_name))
        self.assertTrue(self.second_trajectory.Visibility)
        self.assertTrue(third.Visibility)
        self.document.undo()
        self._process_events(10)
        sequence = self.document.getObject(sequence_name)
        self.assertIsNotNone(sequence)
        self.assertFalse(self.second_trajectory.Visibility)
        self.assertFalse(third.Visibility)

    def test_simulation_is_preview_only_and_restores_display_state(self):
        self._select(self.robot, self.trajectory)
        axes_before = tuple(getattr(self.robot, f"Axis{axis}") for axis in range(1, 7))
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Robot_Simulate", 0)
        self._process_events(10)
        self.assertTrue(Gui.Control.activeDialog())
        slider = Gui.getMainWindow().findChild(
            QtGui.QSlider,
            "timeSlider",
        )
        self.assertIsNotNone(slider)
        slider.setValue(500)
        self._process_events(8)
        self.assertEqual(
            tuple(getattr(self.robot, f"Axis{axis}") for axis in range(1, 7)),
            axes_before,
        )
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertFalse(self.document.HasPendingTransaction)

        close = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(close)
        close.click()
        self._process_events(10)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(
            tuple(getattr(self.robot, f"Axis{axis}") for axis in range(1, 7)),
            axes_before,
        )

    def test_session_defaults_do_not_mutate_the_document(self):
        self.tool.Placement = App.Placement(
            App.Vector(17.0, 19.0, 23.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 31.0),
        )
        self.document.recompute()
        self.document.clearUndos()
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        placement_before = self.tool.Placement

        self._queue_default_value_dialogs()
        Gui.runCommand("Robot_SetDefaultValues", 0)
        self._process_events(8)
        self.assertEqual(__main__._DefSpeed, "2 m/s")
        self.assertTrue(__main__._DefCont)
        self.assertEqual(__main__._DefAcceleration, "3 m/s^2")

        self._select(self.tool)
        self._queue_placement_accept()
        Gui.runCommand("Robot_SetDefaultOrientation", 0)
        self._process_events(8)
        self.assertIsInstance(__main__._DefOrientation, App.Rotation)
        self.assertIsInstance(__main__._DefDisplacement, App.Vector)
        self.assertTrue(self.tool.Placement.isSame(placement_before, 1.0e-12))
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_compact_and_full_export_are_read_only_and_complete(self):
        self._select(self.robot, self.trajectory)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory) / "KUKA output"
            directory.mkdir()
            compact_path = directory / "compact program.src"
            self._run_with_file_dialogs(
                "Robot_ExportKukaCompact",
                [compact_path],
            )
            self.assertTrue(compact_path.is_file())
            compact = compact_path.read_text(encoding="utf-8")
            self.assertIn("DEF PrimaryTrajectory", compact)
            self.assertEqual(compact.count("\nLIN {"), 2)
            self.assertIn("\nEND\n", compact)

            full_path = directory / "full program.src"
            self._run_with_file_dialogs(
                "Robot_ExportKukaFull",
                [full_path],
            )
            data_path = full_path.with_suffix(".dat")
            self.assertTrue(full_path.is_file())
            self.assertTrue(data_path.is_file())
            full = full_path.read_text(encoding="utf-8")
            data = data_path.read_text(encoding="utf-8")
            self.assertIn("LIN XP1", full)
            self.assertIn("LIN XP2", full)
            self.assertIn("DECL E6POS XP1", data)
            self.assertIn("DECL E6POS XP2", data)
            self.assertIn("ENDDAT", data)

            cancelled_path = directory / "cancelled.src"
            self._run_with_file_dialogs(
                "Robot_ExportKukaCompact",
                [None],
            )
            self.assertFalse(cancelled_path.exists())

        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_linked_occurrence_edit_and_source_close_are_safe(self):
        source = self._new_document("VibeCADRobotSource")
        source_robot = source.addObject(
            "Robot::RobotObject",
            "SourceRobot",
        )
        source_trajectory = self._add_trajectory(
            source,
            "SourceTrajectory",
            120.0,
        )
        source.recompute()
        source.clearUndos()
        source_directory = TemporaryDirectory()
        self.addCleanup(source_directory.cleanup)
        source.saveAs(str(Path(source_directory.name) / "robot-source.FCStd"))
        self.document.saveAs(str(Path(source_directory.name) / "robot-assembly.FCStd"))

        App.setActiveDocument(self.document.Name)
        robot_link = self.document.addObject("App::Link", "RobotLink")
        robot_link.LinkedObject = source_robot
        trajectory_link = self.document.addObject(
            "App::Link",
            "TrajectoryLink",
        )
        trajectory_link.LinkedObject = source_trajectory
        self.document.recompute()
        self.document.clearUndos()

        self._select(robot_link, trajectory_link)
        count_before = len(source_trajectory.Trajectory.Waypoints)
        Gui.runCommand("Robot_InsertWaypoint", 0)
        self._process_events(10)
        self.assertEqual(
            len(source_trajectory.Trajectory.Waypoints),
            count_before + 1,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertFalse(source.HasPendingTransaction)
        self.assertTrue(Gui.isCommandActive("Std_Undo"))
        Gui.runCommand("Std_Undo", 0)
        self._process_events(10)
        self.assertEqual(
            len(source_trajectory.Trajectory.Waypoints),
            count_before,
        )

        self.assertTrue(Gui.isCommandActive("Std_Redo"))
        Gui.runCommand("Std_Redo", 0)
        self._process_events(10)
        self._select(robot_link, trajectory_link)
        Gui.runCommand("Robot_Simulate", 0)
        self._process_events(10)
        self.assertTrue(Gui.Control.activeDialog())
        source_name = source.Name
        App.closeDocument(source_name)
        self.documents.remove(source_name)
        self._process_events(12)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)

    def test_source_document_close_cancels_operation_task(self):
        self._select_edge()
        Gui.runCommand("Robot_Edge2Trac", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        document_name = self.document.Name
        App.closeDocument(document_name)
        self.documents.remove(document_name)
        self._process_events(12)
        self.assertFalse(Gui.Control.activeDialog())

        self.document = self._new_document("VibeCADRobotAfterTaskClose")
        self.assertFalse(self.document.HasPendingTransaction)
