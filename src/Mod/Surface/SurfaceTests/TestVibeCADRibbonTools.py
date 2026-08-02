# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD lifecycle contracts for native Surface commands on Model."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import Part
import Surface  # noqa: F401 - registers native Surface object types
from PySide import QtCore, QtGui

SURFACE_COMMANDS = (
    "Surface_Filling",
    "Surface_GeomFillSurface",
    "Surface_Sections",
    "Surface_ExtendFace",
    "Surface_CurveOnMesh",
    "Surface_BlendCurve",
)

TASK_COMMANDS = (
    "Surface_Filling",
    "Surface_GeomFillSurface",
    "Surface_Sections",
)


@unittest.skipIf(not App.GuiUp, "VibeCAD Surface ribbon tests require the GUI")
class TestVibeCADSurfaceRibbonTools(unittest.TestCase):
    """Surface tools must own one exact, parametric modeling operation."""

    def setUp(self):
        Gui.activateWorkbench("PartDesignWorkbench")
        self._process_events()
        self.documents = []
        self.document = self._new_document("VibeCADSurfaceRibbon")
        self._make_sources(self.document)
        self.document.clearUndos()

    def tearDown(self):
        Gui.Selection.clearSelection()
        for document_name in reversed(self.documents):
            document = App.listDocuments().get(document_name)
            if document is None:
                continue
            gui_document = Gui.getDocument(document_name)
            if gui_document and Gui.Control.activeDialog(gui_document):
                task = Gui.Control.activeTaskDialog(gui_document)
                if task is not None:
                    try:
                        task.reject()
                    except RuntimeError:
                        pass
                Gui.Control.closeDialog(gui_document)
                self._process_events()
            transaction = document.getBookedTransactionID()
            if transaction:
                App.closeActiveTransaction(True, transaction)
            App.closeDocument(document_name)
        self.documents = []
        self.document = None
        self._process_events()

    @staticmethod
    def _process_events(rounds=4):
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
    def _make_sources(document):
        square = document.addObject("Part::Feature", "SquareBoundary")
        square.Shape = Part.makePolygon(
            [
                App.Vector(0, 0, 0),
                App.Vector(10, 0, 0),
                App.Vector(10, 10, 0),
                App.Vector(0, 10, 0),
                App.Vector(0, 0, 0),
            ]
        )

        section_a = document.addObject("Part::Feature", "SectionA")
        section_a.Shape = Part.Wire([Part.makeCircle(4, App.Vector(20, 0, 0))])
        section_b = document.addObject("Part::Feature", "SectionB")
        section_b.Shape = Part.Wire([Part.makeCircle(6, App.Vector(20, 0, 10))])

        plane = document.addObject("Part::Feature", "PlaneSource")
        plane.Shape = Part.makePlane(10, 8, App.Vector(40, 0, 0))

        blend_a = document.addObject("Part::Feature", "BlendEdgeA")
        blend_a.Shape = Part.makeLine(
            App.Vector(60, 0, 0),
            App.Vector(70, 0, 0),
        )
        blend_b = document.addObject("Part::Feature", "BlendEdgeB")
        blend_b.Shape = Part.makeLine(
            App.Vector(60, 10, 0),
            App.Vector(70, 10, 0),
        )

        mesh = document.addObject("Mesh::Feature", "SurfaceMesh")
        mesh.Mesh = Mesh.Mesh(
            [
                (
                    App.Vector(80, 0, 0),
                    App.Vector(90, 0, 0),
                    App.Vector(80, 10, 0),
                )
            ]
        )
        document.recompute()

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
        self.assertTrue(button.isVisible(), object_name)
        self.assertTrue(button.isEnabled(), object_name)
        return button

    def _select_subelements(self, selections):
        Gui.Selection.clearSelection()
        for obj, subelement in selections:
            Gui.Selection.addSelection(obj, subelement)
            self._process_events()

    def _accept_surface_task(self, command_name, selections, type_id):
        objects_before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand(command_name)
        self._process_events()
        gui_document = Gui.getDocument(self.document.Name)
        self.assertTrue(Gui.Control.activeDialog(gui_document), command_name)

        self._select_subelements(selections)
        ok = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok, command_name)
        ok.click()
        self._process_events(8)

        self.assertFalse(Gui.Control.activeDialog(gui_document), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)
        self.assertEqual(self.document.UndoCount, undo_before + 1, command_name)
        results = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId == type_id
        ]
        self.assertEqual(len(results), 1, command_name)
        result = results[0]
        self._assert_operation(result)
        return result

    def _assert_operation(self, result):
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.hasExtension("App::SuppressibleExtension"))
        if "VibeCADTimelineRole" in result.PropertiesList:
            self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            result.PropertiesList,
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(result, timeline.Operations)
        self.assertEqual(list(timeline.Operations).count(result), 1)

        result.Suppressed = True
        self.document.recompute()
        self.assertTrue(result.Shape.isNull())
        result.Suppressed = False
        self.document.recompute()
        self.assertFalse(result.Shape.isNull())
        self._process_events(8)

        end_position = int(timeline.Position)
        self.assertGreater(end_position, 0)
        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(8)
        self.assertEqual(int(timeline.Position), end_position - 1)
        self.assertTrue(result.Suppressed)
        self.assertTrue(result.Shape.isNull())
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(8)
        self.assertEqual(int(timeline.Position), end_position)
        self.assertFalse(result.Suppressed)
        self.assertFalse(result.Shape.isNull())

    def _assert_command_undo_redo(self, result):
        result_name = result.Name
        type_id = result.TypeId
        # Previous and End are each exact undoable marker operations. Remove
        # both before undoing the modeling command itself.
        for _ in range(3):
            self.document.undo()
        self._process_events(6)
        self.assertIsNone(self.document.getObject(result_name))
        for _ in range(3):
            self.document.redo()
        self._process_events(6)
        restored = self.document.getObject(result_name)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.TypeId, type_id)
        self.document.recompute()
        self.assertTrue(restored.isValid(), restored.getStatusString())
        self.assertFalse(restored.Shape.isNull())
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(restored, timeline.Operations)
        self.assertEqual(list(timeline.Operations).count(restored), 1)
        return restored

    def test_exact_inventory_and_icons_are_available_on_model(self):
        registered = set(Gui.listCommands())
        self.assertFalse(set(SURFACE_COMMANDS) - registered)
        for command_name in SURFACE_COMMANDS:
            actions = Gui.Command.get(command_name).getAction()
            self.assertTrue(actions, command_name)
            self.assertTrue(
                all(not action.icon().pixmap(24, 24).isNull() for action in actions),
                command_name,
            )

    def test_create_tasks_cancel_without_geometry_or_undo(self):
        for command_name in TASK_COMMANDS:
            with self.subTest(command=command_name):
                objects_before = tuple(self.document.Objects)
                undo_before = self.document.UndoCount
                Gui.runCommand(command_name)
                self._process_events()
                gui_document = Gui.getDocument(self.document.Name)
                self.assertTrue(Gui.Control.activeDialog(gui_document))
                cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
                self.assertIsNotNone(cancel, command_name)
                cancel.click()
                self._process_events(8)
                self.assertFalse(Gui.Control.activeDialog(gui_document))
                self.assertEqual(tuple(self.document.Objects), objects_before)
                self.assertEqual(self.document.UndoCount, undo_before)
                self.assertFalse(self.document.HasPendingTransaction)

    def test_filling_task_accepts_one_parametric_history_operation(self):
        square = self.document.getObject("SquareBoundary")
        result = self._accept_surface_task(
            "Surface_Filling",
            [(square, f"Edge{index}") for index in range(1, 5)],
            "Surface::Filling",
        )
        self.assertEqual(result.BoundaryEdges[0][0], square)
        self.assertTrue(square.Visibility)
        result = self._assert_command_undo_redo(result)
        self.assertEqual(result.BoundaryEdges[0][0], square)

    def test_boundary_fill_task_accepts_one_parametric_history_operation(self):
        square = self.document.getObject("SquareBoundary")
        result = self._accept_surface_task(
            "Surface_GeomFillSurface",
            [(square, f"Edge{index}") for index in range(1, 5)],
            "Surface::GeomFillSurface",
        )
        self.assertEqual(result.BoundaryList[0][0], square)
        self.assertTrue(square.Visibility)
        result = self._assert_command_undo_redo(result)
        self.assertEqual(result.BoundaryList[0][0], square)

    def test_sections_task_accepts_one_parametric_history_operation(self):
        section_a = self.document.getObject("SectionA")
        section_b = self.document.getObject("SectionB")
        result = self._accept_surface_task(
            "Surface_Sections",
            [(section_a, "Edge1"), (section_b, "Edge1")],
            "Surface::Sections",
        )
        self.assertEqual(
            [entry[0] for entry in result.NSections],
            [section_a, section_b],
        )
        self.assertTrue(section_a.Visibility)
        self.assertTrue(section_b.Visibility)
        result = self._assert_command_undo_redo(result)
        self.assertEqual(
            [entry[0] for entry in result.NSections],
            [section_a, section_b],
        )

    def test_extend_and_blend_are_parametric_history_operations(self):
        plane = self.document.getObject("PlaneSource")
        blend_a = self.document.getObject("BlendEdgeA")
        blend_b = self.document.getObject("BlendEdgeB")

        for command_name, selections, type_id in (
            (
                "Surface_ExtendFace",
                [(plane, "Face1")],
                "Surface::Extend",
            ),
            (
                "Surface_BlendCurve",
                [(blend_a, "Edge1"), (blend_b, "Edge1")],
                "Surface::FeatureBlendCurve",
            ),
        ):
            with self.subTest(command=command_name):
                objects_before = set(self.document.Objects)
                undo_before = self.document.UndoCount
                self._select_subelements(selections)
                self.assertTrue(Gui.isCommandActive(command_name))
                Gui.runCommand(command_name)
                self._process_events(6)
                results = [
                    obj
                    for obj in self.document.Objects
                    if obj not in objects_before and obj.TypeId == type_id
                ]
                self.assertEqual(len(results), 1)
                self.assertEqual(self.document.UndoCount, undo_before + 1)
                self.assertFalse(self.document.HasPendingTransaction)
                self._assert_operation(results[0])
                self._assert_command_undo_redo(results[0])

    def test_curve_on_mesh_alias_cancels_without_geometry_or_undo(self):
        mesh = self.document.getObject("SurfaceMesh")
        self._select_subelements([(mesh, "")])
        self.assertTrue(Gui.isCommandActive("Surface_CurveOnMesh"))
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount

        Gui.runCommand("Surface_CurveOnMesh")
        self._process_events(6)
        gui_document = Gui.getDocument(self.document.Name)
        self.assertTrue(Gui.Control.activeDialog(gui_document))
        cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
        if cancel is None:
            cancel = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(cancel)
        cancel.click()
        self._process_events(8)

        self.assertFalse(Gui.Control.activeDialog(gui_document))
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_mutating_commands_refuse_a_caller_owned_transaction(self):
        plane = self.document.getObject("PlaneSource")
        blend_a = self.document.getObject("BlendEdgeA")
        blend_b = self.document.getObject("BlendEdgeB")
        mesh = self.document.getObject("SurfaceMesh")
        selections = {
            "Surface_ExtendFace": [(plane, "Face1")],
            "Surface_BlendCurve": [
                (blend_a, "Edge1"),
                (blend_b, "Edge1"),
            ],
            "Surface_CurveOnMesh": [(mesh, "")],
        }

        for command_name in SURFACE_COMMANDS:
            with self.subTest(command=command_name):
                selected = selections.get(command_name, ())
                self._select_subelements(selected)
                self.assertTrue(Gui.isCommandActive(command_name))
                objects_before = tuple(self.document.Objects)
                undo_before = self.document.UndoCount
                self.document.openTransaction("Caller owned")
                transaction = self.document.getBookedTransactionID()
                self.assertNotEqual(transaction, 0)
                self._process_events()
                self.assertFalse(Gui.isCommandActive(command_name))
                Gui.runCommand(command_name)
                self._process_events()
                self.assertEqual(tuple(self.document.Objects), objects_before)
                self.assertEqual(self.document.UndoCount, undo_before)
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    transaction,
                )
                App.closeActiveTransaction(True, transaction)
                self.assertEqual(self.document.UndoCount, undo_before)

    def test_surface_features_suppress_and_survive_save_reopen(self):
        persistence = self._new_document("VibeCADSurfacePersistence")
        self._make_sources(persistence)
        square = persistence.getObject("SquareBoundary")
        section_a = persistence.getObject("SectionA")
        section_b = persistence.getObject("SectionB")
        plane = persistence.getObject("PlaneSource")
        blend_a = persistence.getObject("BlendEdgeA")
        blend_b = persistence.getObject("BlendEdgeB")

        filling = persistence.addObject("Surface::Filling", "SavedFilling")
        filling.BoundaryEdges = [(square, [f"Edge{index}" for index in range(1, 5)])]
        boundary = persistence.addObject(
            "Surface::GeomFillSurface",
            "SavedBoundaryFill",
        )
        boundary.BoundaryList = [(square, [f"Edge{index}" for index in range(1, 5)])]
        sections = persistence.addObject("Surface::Sections", "SavedSections")
        sections.NSections = [
            (section_a, ["Edge1"]),
            (section_b, ["Edge1"]),
        ]
        extend = persistence.addObject("Surface::Extend", "SavedExtend")
        extend.Face = (plane, ["Face1"])
        extend.ExtendUPos = 2.0
        blend = persistence.addObject(
            "Surface::FeatureBlendCurve",
            "SavedBlend",
        )
        blend.StartEdge = (blend_a, ["Edge1"])
        blend.EndEdge = (blend_b, ["Edge1"])
        blend.StartParameter = 0.5
        blend.EndParameter = 0.5
        persistence.recompute()

        feature_names = (
            "SavedFilling",
            "SavedBoundaryFill",
            "SavedSections",
            "SavedExtend",
            "SavedBlend",
        )
        for name in feature_names:
            feature = persistence.getObject(name)
            self.assertTrue(feature.isValid(), feature.getStatusString())
            self.assertFalse(feature.Shape.isNull())
            feature.Suppressed = True
        persistence.recompute()
        self.assertTrue(
            all(persistence.getObject(name).Shape.isNull() for name in feature_names)
        )
        for name in feature_names:
            persistence.getObject(name).Suppressed = False
        persistence.recompute()

        with TemporaryDirectory() as directory:
            path = Path(directory) / "surface-lifecycle.FCStd"
            persistence.saveAs(str(path))
            persistence_name = persistence.Name
            App.closeDocument(persistence_name)
            self.documents.remove(persistence_name)

            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            reopened.recompute()
            for name in feature_names:
                feature = reopened.getObject(name)
                self.assertIsNotNone(feature)
                self.assertTrue(feature.isValid(), feature.getStatusString())
                self.assertFalse(feature.Shape.isNull())
                self.assertFalse(feature.Suppressed)

            self.assertIs(
                reopened.getObject("SavedExtend").Face[0],
                reopened.getObject("PlaneSource"),
            )
            self.assertIs(
                reopened.getObject("SavedBlend").StartEdge[0],
                reopened.getObject("BlendEdgeA"),
            )

    def test_closing_task_document_does_not_target_another_document(self):
        gui_document = Gui.getDocument(self.document.Name)
        Gui.runCommand("Surface_Filling")
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog(gui_document))

        other = self._new_document("VibeCADSurfaceOther")
        marker = other.addObject("App::FeaturePython", "Untouched")
        other.recompute()
        objects_before = tuple(other.Objects)
        undo_before = other.UndoCount

        closed_name = self.document.Name
        App.closeDocument(closed_name)
        self.documents.remove(closed_name)
        self._process_events(8)
        self.assertFalse(Gui.Control.activeDialog(Gui.getDocument(other.Name)))
        self.assertEqual(tuple(other.Objects), objects_before)
        self.assertEqual(other.UndoCount, undo_before)
        self.assertIs(other.getObject(marker.Name), marker)
