# SPDX-License-Identifier: LGPL-2.1-or-later

"""Lifecycle contracts for Reverse Engineering commands on the Mesh ribbon."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import MeshPart
import Part
import Points
from PySide import QtCore, QtGui

REVERSE_ENGINEERING_COMMANDS = (
    "Reen_ApproxCurve",
    "Reen_ApproxSurface",
    "Reen_ApproxPlane",
    "Reen_ApproxCylinder",
    "Reen_ApproxSphere",
    "Reen_ApproxPolynomial",
    "Reen_Segmentation",
    "Reen_SegmentationManual",
    "Reen_SegmentationFromComponents",
    "Reen_MeshBoundary",
    "Reen_PoissonReconstruction",
    "Reen_ViewTriangulation",
)

TASK_COMMANDS = (
    "Reen_ApproxCurve",
    "Reen_ApproxSurface",
    "Reen_Segmentation",
    "Reen_SegmentationManual",
    "Reen_PoissonReconstruction",
)


def _tetrahedron(offset=0.0, closed=True):
    a = App.Vector(offset + 0.0, 0.0, 0.0)
    b = App.Vector(offset + 8.0, 0.0, 0.0)
    c = App.Vector(offset + 0.0, 7.0, 0.0)
    d = App.Vector(offset + 0.0, 0.0, 6.0)
    facets = [
        (a, c, b),
        (a, b, d),
        (c, a, d),
    ]
    if closed:
        facets.insert(2, (b, c, d))
    return Mesh.Mesh(facets)


def _planar_grid(columns=12, rows=12, spacing=1.0):
    facets = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            lower_left = App.Vector(column * spacing, row * spacing, 0.0)
            lower_right = App.Vector(
                (column + 1) * spacing,
                row * spacing,
                0.0,
            )
            upper_left = App.Vector(
                column * spacing,
                (row + 1) * spacing,
                0.0,
            )
            upper_right = App.Vector(
                (column + 1) * spacing,
                (row + 1) * spacing,
                0.0,
            )
            facets.extend(
                (
                    (lower_left, lower_right, upper_right),
                    (lower_left, upper_right, upper_left),
                )
            )
    return Mesh.Mesh(facets)


@unittest.skipIf(
    not App.GuiUp,
    "VibeCAD Reverse Engineering ribbon tests require the GUI",
)
class TestVibeCADReverseEngineeringRibbonTools(unittest.TestCase):
    """Every shipped reconstruction command must own an exact lifecycle."""

    def setUp(self):
        Gui.activateWorkbench("MeshWorkbench")
        self._process_events()
        self.documents = []
        self.document = self._new_document("VibeCADReverseEngineering")

        combined = _tetrahedron()
        combined.addMesh(_tetrahedron(25.0))
        self.components = self.document.addObject(
            "Mesh::Feature",
            "ComponentSource",
        )
        self.components.Mesh = combined

        self.open_mesh = self.document.addObject(
            "Mesh::Feature",
            "OpenMesh",
        )
        self.open_mesh.Mesh = _tetrahedron(50.0, closed=False)

        self.planar_mesh = self.document.addObject(
            "Mesh::Feature",
            "PlanarMesh",
        )
        self.planar_mesh.Mesh = _planar_grid()

        self.cylinder_mesh = self.document.addObject(
            "Mesh::Feature",
            "CylinderMesh",
        )
        self.cylinder_mesh.Mesh = MeshPart.meshFromShape(
            Shape=Part.makeCylinder(5.0, 12.0),
            LinearDeflection=0.3,
            AngularDeflection=0.2,
            Relative=False,
        )

        self.sphere_mesh = self.document.addObject(
            "Mesh::Feature",
            "SphereMesh",
        )
        self.sphere_mesh.Mesh = MeshPart.meshFromShape(
            Shape=Part.makeSphere(6.0),
            LinearDeflection=0.3,
            AngularDeflection=0.2,
            Relative=False,
        )

        curve_kernel = Points.Points()
        curve_kernel.addPoints(
            [App.Vector(float(index), 0.08 * index * index, 0.0) for index in range(14)]
        )
        self.curve_points = self.document.addObject(
            "Points::Feature",
            "CurvePoints",
        )
        self.curve_points.Points = curve_kernel

        grid_points = [
            App.Vector(float(column), float(row), 0.0)
            for row in range(4)
            for column in range(4)
        ]
        grid_kernel = Points.Points()
        grid_kernel.addPoints(grid_points)
        self.grid_points = self.document.addObject(
            "Points::Feature",
            "GridPoints",
        )
        self.grid_points.Points = grid_kernel

        structured_kernel = Points.Points()
        structured_kernel.addPoints(grid_points)
        self.structured = self.document.addObject(
            "Points::Structured",
            "StructuredPoints",
        )
        self.structured.Points = structured_kernel
        self.structured.Width = 4
        self.structured.Height = 4

        self.document.recompute()
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

    def _select(self, *objects):
        Gui.Selection.clearSelection()
        for obj in objects:
            Gui.Selection.addSelection(obj)
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
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(list(timeline.Operations).count(operation), 1)

    def _run_direct_fit(self, command_name, source, type_id):
        self._select(source)
        self.assertTrue(Gui.isCommandActive(command_name), command_name)
        before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand(command_name, 0)
        self._process_events(10)
        created = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId != "App::DocumentTimeline"
        ]
        outputs = [obj for obj in created if obj.TypeId == type_id]
        self.assertEqual(len(outputs), 1, command_name)
        output = outputs[0]
        self.assertIs(output.Source, source, command_name)
        self._assert_single_operation(output)
        self.assertEqual(
            self.document.UndoCount,
            undo_before + 1,
            command_name,
        )
        self.assertTrue(output.isValid(), output.getStatusString())
        self.assertFalse(output.Shape.isNull(), command_name)

    def test_inventory_icons_and_source_contract_are_complete(self):
        registered = set(Gui.listCommands())
        self.assertFalse(set(REVERSE_ENGINEERING_COMMANDS) - registered)
        for command_name in REVERSE_ENGINEERING_COMMANDS:
            actions = Gui.Command.get(command_name).getAction()
            self.assertTrue(actions, command_name)
            self.assertTrue(
                all(not action.icon().pixmap(24, 24).isNull() for action in actions),
                command_name,
            )

        repository = next(
            parent
            for parent in Path(__file__).resolve().parents
            if (parent / "src/Mod/ReverseEngineering/Gui/Command.cpp").is_file()
        )
        command = (repository / "src/Mod/ReverseEngineering/Gui/Command.cpp").read_text(
            encoding="utf-8"
        )
        segmentation = (
            repository / "src/Mod/ReverseEngineering/Gui/Segmentation.cpp"
        ).read_text(encoding="utf-8")
        manual = (
            repository / "src/Mod/ReverseEngineering/Gui/SegmentationManual.cpp"
        ).read_text(encoding="utf-8")
        for source in (command, segmentation, manual):
            self.assertNotIn("App.ActiveDocument", source)
        self.assertNotIn("editmesh->deleteFacets", manual)
        self.assertIn("Gui::ExactTransaction", command)
        self.assertIn("Gui::ExactTransaction", segmentation)
        self.assertIn("Gui::ExactTransaction", manual)
        self.assertIn("publishOutputGroup", segmentation)
        self.assertIn("publishOutputGroup", manual)

    def test_every_mutator_refuses_a_caller_owned_transaction(self):
        selections = {
            "Reen_ApproxCurve": (self.curve_points,),
            "Reen_ApproxSurface": (self.grid_points,),
            "Reen_ApproxPlane": (self.grid_points,),
            "Reen_ApproxCylinder": (self.cylinder_mesh,),
            "Reen_ApproxSphere": (self.sphere_mesh,),
            "Reen_ApproxPolynomial": (self.planar_mesh,),
            "Reen_Segmentation": (self.planar_mesh,),
            "Reen_SegmentationManual": (),
            "Reen_SegmentationFromComponents": (self.components,),
            "Reen_MeshBoundary": (self.open_mesh,),
            "Reen_PoissonReconstruction": (self.grid_points,),
            "Reen_ViewTriangulation": (self.structured,),
        }
        for command_name, selection in selections.items():
            with self.subTest(command=command_name):
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

    def test_direct_fit_commands_create_linked_history_operations(self):
        cases = (
            ("Reen_ApproxPlane", self.grid_points, "Part::Plane"),
            (
                "Reen_ApproxCylinder",
                self.cylinder_mesh,
                "Part::Cylinder",
            ),
            ("Reen_ApproxSphere", self.sphere_mesh, "Part::Sphere"),
            (
                "Reen_ApproxPolynomial",
                self.planar_mesh,
                "Part::Spline",
            ),
        )
        for command_name, source, type_id in cases:
            with self.subTest(command=command_name):
                self._run_direct_fit(command_name, source, type_id)

    def test_component_segmentation_is_one_parametric_replacement(self):
        self._select(self.components)
        before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Reen_SegmentationFromComponents", 0)
        self._process_events(10)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId != "App::DocumentTimeline"
        ]
        controllers = [obj for obj in created if obj.TypeId == "Mesh::OutputGroup"]
        segments = [obj for obj in created if obj.TypeId == "Mesh::FacetSubset"]
        self.assertEqual(len(controllers), 1)
        self.assertEqual(len(segments), 2)
        controller = controllers[0]
        self.assertEqual(controller.InputMode, "Replacement")
        self.assertEqual(list(controller.Sources), [self.components])
        self.assertEqual(set(controller.Group), set(segments))
        self.assertEqual(
            list(controller.VibeCADTimelineReplacedInputs),
            [self.components],
        )
        self._assert_single_operation(controller)
        for segment in segments:
            self.assertIs(segment.Source, self.components)
            self.assertEqual(segment.VibeCADTimelineRole, "resource")
            self.assertIs(segment.VibeCADTimelineOwner, controller)
            self.assertGreater(segment.Mesh.CountFacets, 0)
        self.assertFalse(self.components.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        controller_name = controller.Name
        segment_names = [segment.Name for segment in segments]
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(controller_name))
        self.assertTrue(self.components.Visibility)
        self.document.redo()
        self._process_events(8)
        controller = self.document.getObject(controller_name)
        segments = [self.document.getObject(name) for name in segment_names]
        self.assertIsNotNone(controller)
        self.assertTrue(all(segments))

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(8)
        self.assertTrue(controller.Suppressed)
        self.assertTrue(self.components.Visibility)
        self.assertTrue(all(not segment.Visibility for segment in segments))
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(8)
        self.assertFalse(controller.Suppressed)
        self.assertFalse(self.components.Visibility)
        self.assertTrue(all(segment.Visibility for segment in segments))

    def test_boundary_and_triangulation_preserve_their_sources(self):
        self._select(self.open_mesh)
        boundary_before = set(self.document.Objects)
        Gui.runCommand("Reen_MeshBoundary", 0)
        self._process_events(8)
        boundary = next(
            obj
            for obj in self.document.Objects
            if obj not in boundary_before and obj.TypeId == "Part::Feature"
        )
        self.assertIs(boundary.Source, self.open_mesh)
        self.assertFalse(boundary.Shape.isNull())
        self.assertTrue(self.open_mesh.Visibility)
        self._assert_single_operation(boundary)

        self._select(self.structured)
        triangulation_before = set(self.document.Objects)
        Gui.runCommand("Reen_ViewTriangulation", 0)
        self._process_events(8)
        triangulation = next(
            obj
            for obj in self.document.Objects
            if obj not in triangulation_before and obj.TypeId == "Mesh::Feature"
        )
        self.assertIs(triangulation.Source, self.structured)
        self.assertGreater(triangulation.Mesh.CountFacets, 0)
        self.assertTrue(self.structured.Visibility)
        self._assert_single_operation(triangulation)

    def test_curve_task_accepts_one_linked_undoable_result(self):
        self._select(self.curve_points)
        before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Reen_ApproxCurve", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        ok = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok)
        ok.click()
        self._process_events(12)

        self.assertFalse(Gui.Control.activeDialog())
        outputs = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Part::Spline"
        ]
        self.assertEqual(len(outputs), 1)
        output = outputs[0]
        self.assertIs(output.Source, self.curve_points)
        self.assertFalse(output.Shape.isNull())
        self._assert_single_operation(output)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_tasks_cancel_without_geometry_or_transaction(self):
        selections = {
            "Reen_ApproxCurve": self.curve_points,
            "Reen_ApproxSurface": self.grid_points,
            "Reen_Segmentation": self.planar_mesh,
            "Reen_PoissonReconstruction": self.grid_points,
        }
        for command_name, source in selections.items():
            with self.subTest(command=command_name):
                self._select(source)
                before = tuple(self.document.Objects)
                undo_before = self.document.UndoCount
                Gui.runCommand(command_name, 0)
                self._process_events()
                self.assertTrue(Gui.Control.activeDialog())
                cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
                self.assertIsNotNone(cancel)
                cancel.click()
                self._process_events(8)
                self.assertFalse(Gui.Control.activeDialog())
                self.assertEqual(tuple(self.document.Objects), before)
                self.assertEqual(self.document.UndoCount, undo_before)
                self.assertFalse(self.document.HasPendingTransaction)

        self._select()
        before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Reen_SegmentationManual", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        close = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(close)
        close.click()
        self._process_events(8)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_task_closes_with_its_source_document_without_crash(self):
        self._select(self.curve_points)
        Gui.runCommand("Reen_ApproxCurve", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())

        document_name = self.document.Name
        App.closeDocument(document_name)
        self.documents.remove(document_name)
        self._process_events(12)
        self.assertFalse(Gui.Control.activeDialog())

        self.document = self._new_document("VibeCADReverseEngineeringAfterClose")
        self.assertFalse(self.document.HasPendingTransaction)

    def test_replacement_round_trips_save_and_reopen(self):
        self._select(self.components)
        Gui.runCommand("Reen_SegmentationFromComponents", 0)
        self._process_events(10)
        controller = next(
            obj for obj in self.document.Objects if obj.TypeId == "Mesh::OutputGroup"
        )
        names = (
            controller.Name,
            self.components.Name,
            tuple(obj.Name for obj in controller.Group),
        )

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "reconstruction.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            reopened = App.openDocument(str(path))
            if reopened.Name != document_name:
                self.documents.remove(document_name)
                self.documents.append(reopened.Name)
            self.document = reopened
            self._process_events(10)

            restored_controller = reopened.getObject(names[0])
            restored_source = reopened.getObject(names[1])
            restored_segments = [reopened.getObject(name) for name in names[2]]
            self.assertIsNotNone(restored_controller)
            self.assertIsNotNone(restored_source)
            self.assertTrue(all(restored_segments))
            self.assertEqual(
                list(restored_controller.Sources),
                [restored_source],
            )
            self.assertEqual(
                set(restored_controller.Group),
                set(restored_segments),
            )
            self.assertFalse(restored_source.Visibility)
            self._assert_single_operation(restored_controller)
