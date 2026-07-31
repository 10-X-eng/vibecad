# SPDX-License-Identifier: LGPL-2.1-or-later

"""Placement contracts for imported Part Design bases and native clones."""

from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
from PySide import QtCore, QtGui


class _FailingCloneSourceProxy:
    def __init__(self):
        self.fail = False

    def execute(self, obj):
        if self.fail:
            raise RuntimeError("Deliberate clone source failure")
        obj.Shape = Part.makeBox(4, 3, 2)


class TestFeatureBasePlacement(unittest.TestCase):
    """Referenced geometry updates without taking ownership of clone placement."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("FeatureBasePlacement")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="vibecad-featurebase-placement-"
        )
        self._process_events()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
        if self.document is not None:
            document = App.getDocument(self.document.Name)
            if document is not None:
                App.closeDocument(document.Name)
        self._process_events()
        self._temporary_directory.cleanup()

    @staticmethod
    def _process_events():
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()

    @staticmethod
    def _placement(x, y, z, angle=0.0):
        return App.Placement(
            App.Vector(x, y, z),
            App.Rotation(App.Vector(0, 0, 1), angle),
        )

    def _assert_placement_equal(self, actual, expected, message=""):
        actual_matrix = actual.toMatrix()
        expected_matrix = expected.toMatrix()
        for row in range(1, 5):
            for column in range(1, 5):
                attribute = f"A{row}{column}"
                self.assertAlmostEqual(
                    getattr(actual_matrix, attribute),
                    getattr(expected_matrix, attribute),
                    places=9,
                    msg=message or attribute,
                )

    @staticmethod
    def _global_shape(obj):
        shape = obj.Shape.copy()
        parent_placement = (
            obj.getGlobalPlacement() * obj.Placement.inverse()
        )
        shape.Placement = parent_placement * shape.Placement
        return shape

    @staticmethod
    def _link_global_shape(link):
        shape = link.Shape.copy()
        group = link.getParentGeoFeatureGroup()
        if group is not None:
            shape.Placement = (
                group.getGlobalPlacement() * shape.Placement
            )
        return shape

    def _assert_bounds_equal(self, first, second):
        first_bounds = first.BoundBox
        second_bounds = second.BoundBox
        for attribute in (
            "XMin",
            "XMax",
            "YMin",
            "YMax",
            "ZMin",
            "ZMax",
        ):
            self.assertAlmostEqual(
                getattr(first_bounds, attribute),
                getattr(second_bounds, attribute),
                places=7,
                msg=attribute,
            )

    def _clone_selected(self, selected):
        before = {obj.Name for obj in self.document.Objects}
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(selected)
        self._process_events()
        self.assertTrue(Gui.isCommandActive("PartDesign_Clone"))
        Gui.runCommand("PartDesign_Clone", 0)
        self.document.recompute()
        self._process_events()

        created = [
            obj for obj in self.document.Objects if obj.Name not in before
        ]
        clones = [
            obj
            for obj in created
            if obj.TypeId == "PartDesign::DesignClone"
        ]
        bodies = [
            obj for obj in created if obj.TypeId == "PartDesign::Body"
        ]
        self.assertEqual(len(clones), 1)
        self.assertEqual(len(bodies), 1)
        clone = clones[0]
        clone_body = bodies[0]
        self.assertIsNone(clone.getParentGeoFeatureGroup())
        self.assertIsNone(clone.BaseFeature)
        self.assertEqual(clone.ResultOperation, "New Bodies")
        self.assertEqual(clone.OutputBodyIds, [str(clone_body.VibeCADBodyId)])
        self.assertEqual(
            clone_body.Tip.TypeId,
            "PartDesign::DesignBodyPublication",
        )
        self.assertIs(clone_body.Tip.CurrentState.Operation, clone)
        self.assertTrue(clone.isValid(), clone.getStatusString())
        self.assertTrue(clone.Shape.isNull())
        self.assertFalse(clone.PreviewShape.isNull())
        self.assertTrue(clone.PreviewShape.isValid())
        self.assertTrue(clone_body.isValid(), clone_body.getStatusString())
        self.assertFalse(clone_body.Shape.isNull())
        self.assertTrue(clone_body.Shape.isValid())
        PartDesign.validateDesign(clone)
        self.assertFalse(self.document.HasPendingTransaction)
        return clone_body, clone

    def _round_trip(self, file_name):
        path = Path(self._temporary_directory.name) / file_name
        document_name = self.document.Name
        self.document.saveAs(str(path))
        App.closeDocument(document_name)
        self.document = App.openDocument(str(path))
        self.document.recompute()
        self._process_events()

    def test_body_base_proxy_initializes_once_in_body_coordinates(self):
        container = self.document.addObject("App::Part", "ImportContainer")
        container.Placement = self._placement(40, -6, 3, 25)

        body = self.document.addObject("PartDesign::Body", "ImportedBody")
        body.Placement = self._placement(7, 4, 2, -15)
        container.addObject(body)

        source = self.document.addObject("Part::Feature", "ImportSource")
        source.Shape = Part.makeBox(2, 3, 4)
        source.Placement = self._placement(-12, 18, 5, 35)
        self.document.recompute()

        self.document.openTransaction("Adopt external solid")
        body.BaseFeature = source
        self.document.recompute()
        self.document.commitTransaction()

        proxies = [
            obj
            for obj in body.Group
            if obj.TypeId == "PartDesign::FeatureBase"
        ]
        self.assertEqual(len(proxies), 1)
        proxy = proxies[0]
        expected_local = (
            body.getGlobalPlacement().inverse()
            * source.getGlobalPlacement()
        )
        self._assert_placement_equal(proxy.Placement, expected_local)
        self._assert_bounds_equal(
            self._global_shape(body),
            self._global_shape(source),
        )

        initialized_placement = proxy.Placement
        initialized_bounds = self._global_shape(body)
        source.Placement = self._placement(80, -30, 11, 70)
        self.document.recompute()
        self._assert_placement_equal(
            proxy.Placement,
            initialized_placement,
            "source placement must not overwrite the imported destination",
        )
        self._assert_bounds_equal(
            self._global_shape(body),
            initialized_bounds,
        )

        source.Shape = Part.makeBox(5, 3, 2)
        source.Placement = self._placement(80, -30, 11, 70)
        self.document.recompute()
        self.assertAlmostEqual(proxy.Shape.Volume, 30.0)
        self.assertAlmostEqual(body.Shape.Volume, 30.0)
        self._assert_placement_equal(proxy.Placement, initialized_placement)

        proxy_name = proxy.Name
        self._round_trip("ImportedBasePlacement.FCStd")
        reopened_body = self.document.getObject("ImportedBody")
        reopened_proxy = self.document.getObject(proxy_name)
        self._assert_placement_equal(
            reopened_proxy.Placement,
            initialized_placement,
        )
        self.assertAlmostEqual(reopened_body.Shape.Volume, 30.0)
        self.assertAlmostEqual(reopened_proxy.Shape.Volume, 30.0)

    def test_body_base_proxy_creation_is_one_undo_step(self):
        source = self.document.addObject("Part::Feature", "UndoImportSource")
        source.Shape = Part.makeBox(4, 3, 2)
        source.Placement = self._placement(9, -2, 5, 20)
        body = self.document.addObject("PartDesign::Body", "UndoImportBody")
        self.document.recompute()
        self.document.clearUndos()

        self.document.openTransaction("Adopt external solid")
        body.BaseFeature = source
        self.document.recompute()
        self.document.commitTransaction()
        self.assertIs(body.BaseFeature, source)
        self.assertEqual(
            len(
                [
                    obj
                    for obj in body.Group
                    if obj.TypeId == "PartDesign::FeatureBase"
                ]
            ),
            1,
        )

        self.document.undo()
        self.document.recompute()
        self.assertIsNone(body.BaseFeature)
        self.assertFalse(
            any(
                obj.TypeId == "PartDesign::FeatureBase"
                for obj in body.Group
            )
        )
        self.assertIsNotNone(self.document.getObject(source.Name))
        self.assertFalse(self.document.HasPendingTransaction)

    def test_clone_rejects_standalone_geometry_without_a_body_identity(self):
        container = self.document.addObject("App::Part", "FeatureContainer")
        container.Placement = self._placement(30, 8, -4, 22)
        source = self.document.addObject("Part::Feature", "FeatureSource")
        source.Shape = Part.makeBox(2, 3, 4)
        source.Placement = self._placement(6, -5, 2, -17)
        container.addObject(source)
        self.document.recompute()
        source.ViewObject.ShapeColor = (0.72, 0.24, 0.18)
        source.ViewObject.LineColor = (0.11, 0.22, 0.33)
        source.ViewObject.PointColor = (0.44, 0.55, 0.66)
        source.ViewObject.Transparency = 17
        source.ViewObject.DisplayMode = "Flat Lines"

        before = tuple(obj.Name for obj in self.document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        self._process_events()
        self.assertFalse(Gui.isCommandActive("PartDesign_Clone"))
        Gui.runCommand("PartDesign_Clone", 0)
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            before,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_clone_body_preserves_tip_local_transform(self):
        container = self.document.addObject("App::Part", "BodyContainer")
        container.Placement = self._placement(-25, 14, 6, -30)
        source_body = self.document.addObject(
            "PartDesign::Body",
            "BodySource",
        )
        source_body.Placement = self._placement(8, 2, 5, 18)
        container.addObject(source_body)
        source_tip = source_body.newObject(
            "PartDesign::Feature",
            "BodySourceTip",
        )
        source_tip.Shape = Part.makeBox(2, 3, 4)
        source_tip.Placement = self._placement(4, -3, 1, 12)
        source_body.Tip = source_tip
        self.document.recompute()

        expected_root = source_body.getGlobalPlacement()
        clone_body, clone = self._clone_selected(source_body)
        self.assertEqual(clone.InputStates, [source_tip])
        self._assert_placement_equal(clone_body.Placement, expected_root)
        self._assert_bounds_equal(
            self._global_shape(clone_body),
            self._global_shape(source_body),
        )

        source_body.Placement = self._placement(50, -20, 9, 75)
        source_tip.Placement = self._placement(-6, 8, 2, -40)
        self.document.recompute()
        self._assert_placement_equal(clone_body.Placement, expected_root)

        source_tip.Shape = Part.makeBox(5, 3, 2)
        source_tip.Placement = self._placement(-6, 8, 2, -40)
        self.document.recompute()
        self.assertAlmostEqual(clone_body.Shape.Volume, 30.0)
        self._assert_placement_equal(clone_body.Placement, expected_root)

        clone_body_name = clone_body.Name
        clone_name = clone.Name
        identities = (
            str(clone.OperationId),
            str(clone_body.VibeCADBodyId),
            str(clone_body.Tip.CurrentState.BodyStateId),
        )
        self._round_trip("BodyClonePlacement.FCStd")
        reopened_body = self.document.getObject(clone_body_name)
        reopened_clone = self.document.getObject(clone_name)
        self._assert_placement_equal(
            reopened_body.Placement,
            expected_root,
        )
        self.assertAlmostEqual(reopened_body.Shape.Volume, 30.0)
        self.assertEqual(
            (
                str(reopened_clone.OperationId),
                str(reopened_body.VibeCADBodyId),
                str(reopened_body.Tip.CurrentState.BodyStateId),
            ),
            identities,
        )
        PartDesign.validateDesign(reopened_clone)

    def test_clone_rejects_an_assembly_occurrence(self):
        definition = self.document.addObject(
            "PartDesign::Body",
            "LinkDefinition",
        )
        definition_tip = definition.newObject(
            "PartDesign::Feature",
            "LinkDefinitionTip",
        )
        definition_tip.Shape = Part.makeBox(2, 3, 4)
        definition.Tip = definition_tip

        container = self.document.addObject("App::Part", "LinkContainer")
        container.Placement = self._placement(35, -11, 4, 28)
        occurrence = self.document.addObject("App::Link", "LinkOccurrence")
        occurrence.LinkedObject = definition
        occurrence.Placement = self._placement(9, 6, 2, -16)
        container.addObject(occurrence)
        self.document.recompute()

        before = tuple(obj.Name for obj in self.document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrence)
        self._process_events()
        self.assertFalse(Gui.isCommandActive("PartDesign_Clone"))
        Gui.runCommand("PartDesign_Clone", 0)
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            before,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_clone_creation_is_one_undo_step(self):
        source_body = self.document.addObject(
            "PartDesign::Body",
            "UndoCloneBody",
        )
        source = source_body.newObject(
            "PartDesign::Feature",
            "UndoCloneSource",
        )
        source.Shape = Part.makeBox(4, 3, 2)
        source.Placement = self._placement(12, 5, -3, 15)
        source_body.Tip = source
        self.document.recompute()
        self.document.clearUndos()
        original_names = tuple(obj.Name for obj in self.document.Objects)

        self._clone_selected(source_body)
        self.assertNotEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.document.undo()
        self.document.recompute()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_clone_failure_aborts_without_partial_objects_or_undo(self):
        source_body = self.document.addObject(
            "PartDesign::Body",
            "FailingCloneBody",
        )
        source = source_body.newObject(
            "PartDesign::FeaturePython",
            "FailingCloneSource",
        )
        proxy = _FailingCloneSourceProxy()
        source.Proxy = proxy
        source_body.Tip = source
        self.document.recompute()
        self.assertTrue(source.isValid(), source.getStatusString())
        self.assertFalse(source.Shape.isNull())

        source_body.ViewObject.Visibility = True
        source.ViewObject.Visibility = True
        Gui.activeView().setActiveObject("pdbody", source_body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_body, "Face1")
        self._process_events()
        self.assertTrue(Gui.isCommandActive("PartDesign_Clone"))

        self.document.clearUndos()
        proxy.fail = True
        source.touch()
        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_undo_count = self.document.UndoCount
        original_active_object = self.document.ActiveObject
        original_active_body = Gui.activeView().getActiveObject("pdbody")
        original_selection = tuple(
            (item.ObjectName, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        )
        original_visibility = tuple(
            (obj.Name, bool(obj.ViewObject.Visibility))
            for obj in self.document.Objects
            if getattr(obj, "ViewObject", None) is not None
        )

        dismissal = {"complete": False, "seen": False}

        def dismiss_clone_error():
            if dismissal["complete"]:
                return
            for widget in QtGui.QApplication.topLevelWidgets():
                if isinstance(widget, QtGui.QMessageBox) and widget.isVisible():
                    dismissal["seen"] = True
                    widget.accept()
                    return
            QtCore.QTimer.singleShot(10, dismiss_clone_error)

        QtCore.QTimer.singleShot(0, dismiss_clone_error)
        Gui.runCommand("PartDesign_Clone", 0)
        dismissal["complete"] = True
        self._process_events()

        self.assertTrue(dismissal["seen"])
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertEqual(self.document.UndoCount, original_undo_count)
        self.assertIs(self.document.ActiveObject, original_active_object)
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            original_active_body,
        )
        self.assertEqual(
            tuple(
                (item.ObjectName, tuple(item.SubElementNames))
                for item in Gui.Selection.getSelectionEx()
            ),
            original_selection,
        )
        self.assertEqual(
            tuple(
                (obj.Name, bool(obj.ViewObject.Visibility))
                for obj in self.document.Objects
                if getattr(obj, "ViewObject", None) is not None
            ),
            original_visibility,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertFalse(Gui.Control.activeDialog())


if __name__ == "__main__":
    unittest.main()
