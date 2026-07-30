# SPDX-License-Identifier: LGPL-2.1-or-later

"""Accepted-output contracts for native multi-input Model ribbon tools."""

import os
from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
from PySide import QtCore, QtGui


class TestPartMultiInputRibbonTools(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("PartMultiInputRibbonTools")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()
        if App.getDocument("PartMultiInputRibbonTools") is not None:
            App.closeDocument("PartMultiInputRibbonTools")
        self._process_events()

    @staticmethod
    def _process_events(wait_ms=20):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        if wait_ms:
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()

    def _body_feature(self, body_name, feature_name, shape):
        body = self.document.addObject("PartDesign::Body", body_name)
        feature = body.newObject("PartDesign::Feature", feature_name)
        feature.Shape = shape
        body.Tip = feature
        self.document.recompute()
        self.assertTrue(feature.isValid(), feature.getStatusString())
        self.assertFalse(feature.Shape.isNull())
        return body, feature

    def _wire_profile(self, x, size=4.0):
        points = [
            App.Vector(x, 0, 0),
            App.Vector(x, size, 0),
            App.Vector(x, size, size),
            App.Vector(x, 0, size),
            App.Vector(x, 0, 0),
        ]
        return Part.makePolygon(points)

    def _select(self, *entries):
        Gui.Selection.clearSelection()
        for entry in entries:
            if isinstance(entry, tuple):
                Gui.Selection.addSelection(entry[0], entry[1])
            else:
                Gui.Selection.addSelection(entry)
        self._process_events()

    @staticmethod
    def _selection_state():
        return tuple(
            (selected.Object, tuple(selected.SubElementNames))
            for selected in Gui.Selection.getSelectionEx()
        )

    def _task_button(self, standard_button):
        self._process_events()
        for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not box.isVisible():
                continue
            button = box.button(standard_button)
            if button is not None and button.isVisible() and button.isEnabled():
                return button
        return None

    def _dismiss_next_message(self):
        def dismiss():
            for widget in QtGui.QApplication.topLevelWidgets():
                if isinstance(widget, QtGui.QMessageBox) and widget.isVisible():
                    widget.accept()
                    return
            QtCore.QTimer.singleShot(10, dismiss)

        QtCore.QTimer.singleShot(0, dismiss)

    def _start_macro_recording(self, directory, name):
        def start():
            widgets = list(QtGui.QApplication.topLevelWidgets())
            main_window = Gui.getMainWindow()
            if main_window is not None:
                widgets.extend(main_window.findChildren(QtGui.QDialog))
            dialog = next(
                (
                    widget for widget in widgets
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
            path = dialog.findChild(QtGui.QLineEdit, "lineEditMacroPath")
            filename = dialog.findChild(QtGui.QLineEdit, "lineEditPath")
            button = dialog.findChild(QtGui.QPushButton, "buttonStart")
            self.assertIsNotNone(path)
            self.assertIsNotNone(filename)
            self.assertIsNotNone(button)
            path.setText(str(directory) + os.sep)
            filename.setText(name)
            button.click()

        QtCore.QTimer.singleShot(0, start)
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events()

    def _stop_macro_recording(self, path):
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events()
        self.assertTrue(path.is_file(), path)
        return path.read_text(encoding="utf-8")

    def _accept_task(self, *, expect_close=True, dismiss_message=False):
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        if dismiss_message:
            self._dismiss_next_message()
        button.click()
        self._process_events(60)
        self.assertEqual(bool(Gui.Control.activeDialog()), not expect_close)

    def _cancel_task(self):
        button = self._task_button(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNotNone(button)
        button.click()
        self._process_events(50)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)

    def _visible_widget(self, widget_type, object_name):
        return next(
            (
                widget
                for widget in Gui.getMainWindow().findChildren(
                    widget_type,
                    object_name,
                )
                if widget.isVisible()
            ),
            None,
        )

    def _new_result(self, original_objects, type_id):
        created = [
            obj
            for obj in self.document.Objects
            if obj not in original_objects and obj.TypeId == type_id
        ]
        self.assertEqual(len(created), 1, [(obj.Name, obj.TypeId) for obj in created])
        return created[0]

    def _assert_valid_shape(self, result):
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.Shape.isValid())

    def _assert_one_step_undo(self, original_objects):
        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertFalse(self.document.HasPendingTransaction)

    def _root_feature(self, name, shape):
        feature = self.document.addObject("Part::Feature", name)
        feature.Shape = shape
        self.document.recompute()
        self.assertTrue(feature.isValid(), feature.getStatusString())
        self.assertFalse(feature.Shape.isNull())
        return feature

    def _accept_input_dialog(self):
        accepted = []

        def accept():
            dialog = next(
                (
                    widget
                    for widget in QtGui.QApplication.topLevelWidgets()
                    if isinstance(widget, QtGui.QInputDialog)
                    and widget.isVisible()
                ),
                None,
            )
            if dialog is None:
                QtCore.QTimer.singleShot(10, accept)
                return
            accepted.append(True)
            dialog.accept()

        QtCore.QTimer.singleShot(0, accept)
        return accepted

    def _created_shape_results(self, original_objects):
        return [
            obj
            for obj in self.document.Objects
            if obj not in original_objects
            and obj.isDerivedFrom("Part::Feature")
        ]

    def _assert_grouped_outputs(
        self,
        original_objects,
        sources,
        *,
        replaces_sources,
        expected_count=2,
    ):
        self._process_events(50)
        self.document.recompute()
        outputs = self._created_shape_results(original_objects)
        self.assertEqual(
            len(outputs),
            expected_count,
            [(obj.Name, obj.TypeId) for obj in outputs],
        )
        operation = outputs[-1]
        resources = outputs[:-1]

        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        if "VibeCADTimelineOwner" in operation.PropertiesList:
            self.assertIsNone(operation.VibeCADTimelineOwner)
        for resource in resources:
            self.assertEqual(resource.VibeCADTimelineRole, "resource")
            self.assertIs(resource.VibeCADTimelineOwner, operation)
            self.assertEqual(
                resource.getTypeIdOfProperty("VibeCADTimelineOwner"),
                "App::PropertyLinkHidden",
            )
            self.assertIn(
                "Hidden",
                resource.getEditorMode("VibeCADTimelineOwner"),
            )
            self.assertNotIn(
                "VibeCADTimelineReplacedInputs",
                resource.PropertiesList,
            )

        if replaces_sources:
            self.assertEqual(
                list(operation.VibeCADTimelineReplacedInputs),
                list(sources),
            )
            self.assertTrue(
                all(not source.Visibility for source in sources)
            )
        else:
            self.assertNotIn(
                "VibeCADTimelineReplacedInputs",
                operation.PropertiesList,
            )
            self.assertTrue(all(source.Visibility for source in sources))

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(operation, list(timeline.Operations))
        visible = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(visible)
        visible_names = {
            visible.item(row).data(QtCore.Qt.UserRole)
            for row in range(visible.count())
        }
        self.assertIn(operation.Name, visible_names)
        self.assertTrue(
            all(resource.Name not in visible_names for resource in resources)
        )
        return operation, resources

    def _assert_failed_multi_selection_is_atomic(
        self,
        original_objects,
        original_visibility,
        original_selection,
        original_active,
        original_modified,
        original_undo_count,
    ):
        self._process_events(50)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(
            tuple(obj.Visibility for obj in original_objects),
            original_visibility,
        )
        self.assertEqual(self._selection_state(), original_selection)
        self.assertIs(self.document.ActiveObject, original_active)
        self.assertEqual(
            bool(Gui.activeDocument().Modified),
            original_modified,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            original_undo_count,
        )
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_loft_body_rows_prepopulate_in_order_and_cross_body_stays_root(self):
        first_body, first_tip = self._body_feature(
            "LoftFirstBody",
            "LoftFirstProfile",
            self._wire_profile(0),
        )
        second_body, second_tip = self._body_feature(
            "LoftSecondBody",
            "LoftSecondProfile",
            self._wire_profile(10),
        )
        first_original_tip = first_body.Tip
        second_original_tip = second_body.Tip
        first_body.ViewObject.Visibility = True
        second_body.ViewObject.Visibility = True
        original_objects = tuple(self.document.Objects)
        original_visibility = (
            second_body.ViewObject.Visibility,
            first_body.ViewObject.Visibility,
        )
        self._select(second_body, first_body)

        Gui.runCommand("Part_Loft", 0)
        self._process_events()
        selected = self._visible_widget(QtGui.QTreeWidget, "selectedTreeWidget")
        self.assertIsNotNone(selected)
        self.assertEqual(selected.topLevelItemCount(), 2)
        self.assertEqual(
            [
                selected.topLevelItem(index).data(0, int(QtCore.Qt.UserRole))
                for index in range(2)
            ],
            [second_tip.Name, first_tip.Name],
        )

        self._accept_task()
        loft = self._new_result(original_objects, "Part::Loft")
        self._assert_valid_shape(loft)
        self.assertEqual(list(loft.Sections), [second_tip, first_tip])
        self.assertEqual(list(loft.ProfileLinks), [])
        self.assertIsNone(loft.getParentGeoFeatureGroup())
        self.assertIs(first_body.Tip, first_original_tip)
        self.assertIs(second_body.Tip, second_original_tip)
        self.assertEqual(
            list(loft.VibeCADTimelineReplacedInputs),
            [second_body, first_body],
        )
        self.assertFalse(second_body.ViewObject.Visibility)
        self.assertFalse(first_body.ViewObject.Visibility)
        self.assertTrue(loft.ViewObject.Visibility)

        self._assert_one_step_undo(original_objects)
        self.assertIs(first_body.Tip, first_original_tip)
        self.assertIs(second_body.Tip, second_original_tip)
        self.assertEqual(
            (
                second_body.ViewObject.Visibility,
                first_body.ViewObject.Visibility,
            ),
            original_visibility,
        )

    def test_loft_rejects_same_name_profile_replacement_while_task_is_open(self):
        first = self.document.addObject(
            "Part::Feature",
            "ExactLoftFirstProfile",
        )
        first.Shape = self._wire_profile(0)
        second = self.document.addObject(
            "Part::Feature",
            "ExactLoftSecondProfile",
        )
        second.Shape = self._wire_profile(10)
        self.document.recompute()
        first_name = first.Name
        original_lofts = tuple(self.document.findObjects("Part::Loft"))
        self._select(first, second)

        Gui.runCommand("Part_Loft", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())

        self.document.removeObject(first_name)
        replacement = self.document.addObject(
            "Part::Feature",
            first_name,
        )
        replacement.Shape = self._wire_profile(0)
        self.document.recompute()
        self.assertEqual(replacement.Name, first_name)

        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertEqual(
            tuple(self.document.findObjects("Part::Loft")),
            original_lofts,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self._cancel_task()

    def test_direct_compound_keeps_root_occurrence_out_of_stale_active_body(self):
        body, source = self._body_feature(
            "DirectCompoundDefinitionBody",
            "DirectCompoundDefinition",
            Part.makeBox(4, 5, 6),
        )
        occurrence = self.document.addObject(
            "App::Link",
            "DirectCompoundOccurrence",
        )
        occurrence.LinkedObject = body
        occurrence.Placement = App.Placement(
            App.Vector(20, 3, 2),
            App.Rotation(),
        )
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        self._select(occurrence)

        Gui.runCommand("Part_Compound", 0)
        self._process_events()

        compound = self._new_result(original_objects, "Part::Compound")
        self._assert_valid_shape(compound)
        self.assertEqual(list(compound.Links), [occurrence])
        self.assertIsNone(compound.getParentGeoFeatureGroup())
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, source)
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            body,
        )
        self._assert_one_step_undo(original_objects)

    def test_python_boolean_composite_keeps_root_occurrences_at_root(self):
        first_body, first_tip = self._body_feature(
            "CompositeFirstBody",
            "CompositeFirstSource",
            Part.makeBox(10, 10, 10),
        )
        second_body, second_tip = self._body_feature(
            "CompositeSecondBody",
            "CompositeSecondSource",
            Part.makeBox(10, 10, 10, App.Vector(5, 0, 0)),
        )
        first_occurrence = self.document.addObject(
            "App::Link",
            "CompositeFirstOccurrence",
        )
        first_occurrence.LinkedObject = first_body
        second_occurrence = self.document.addObject(
            "App::Link",
            "CompositeSecondOccurrence",
        )
        second_occurrence.LinkedObject = second_body
        Gui.activeView().setActiveObject("pdbody", first_body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_groups = (tuple(first_body.Group), tuple(second_body.Group))
        self._select(first_occurrence, second_occurrence)

        # Part_Fuse deliberately uses the Python BOPFeatures composite. Its
        # retained occurrence links, not the stale active Body, define result
        # ownership.
        Gui.runCommand("Part_Fuse", 0)
        self._process_events()

        fusion = self._new_result(original_objects, "Part::MultiFuse")
        self._assert_valid_shape(fusion)
        self.assertEqual(
            list(fusion.Shapes),
            [first_occurrence, second_occurrence],
        )
        self.assertIsNone(fusion.getParentGeoFeatureGroup())
        self.assertEqual(
            (tuple(first_body.Group), tuple(second_body.Group)),
            original_groups,
        )
        self.assertEqual((first_body.Tip, second_body.Tip), (first_tip, second_tip))
        self._assert_one_step_undo(original_objects)

    def test_shared_created_helper_conflict_resolves_entire_graph_to_root(self):
        body, source = self._body_feature(
            "SharedHelperBody",
            "SharedHelperBodySource",
            Part.makeBox(6, 6, 6),
        )
        occurrence = self.document.addObject(
            "App::Link",
            "SharedHelperRootOccurrence",
        )
        occurrence.LinkedObject = body
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)

        self.document.openTransaction("Shared helper ownership conflict")
        helper = self.document.addObject(
            "Part::FeaturePython",
            "SharedCreatedHelper",
        )
        helper.addProperty("App::PropertyLinkGlobal", "Source")
        helper.Source = source
        helper.Shape = source.Shape.copy()
        body_result = self.document.addObject(
            "Part::FeaturePython",
            "SharedBodyConsumer",
        )
        body_result.addProperty("App::PropertyLinkGlobal", "Helper")
        body_result.Helper = helper
        body_result.Shape = helper.Shape.copy()
        root_result = self.document.addObject(
            "Part::FeaturePython",
            "SharedRootConsumer",
        )
        root_result.addProperty("App::PropertyLinkGlobal", "Helper")
        root_result.addProperty("App::PropertyLinkGlobal", "RootOccurrence")
        root_result.Helper = helper
        root_result.RootOccurrence = occurrence
        root_result.Shape = helper.Shape.copy()
        self.document.recompute()
        self.document.commitTransaction()
        self._process_events()

        # A root consumer and a Body consumer share one created helper. The
        # graph has one legal deterministic placement: every unowned created
        # feature remains at document root.
        for result in (helper, body_result, root_result):
            self.assertIsNone(result.getParentGeoFeatureGroup(), result.Name)
            self._assert_valid_shape(result)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, source)

        self._assert_one_step_undo(original_objects)

    def test_loft_same_body_graph_is_adopted_without_reparenting_sources(self):
        body = self.document.addObject("PartDesign::Body", "SameBodyLoft")
        first = body.newObject("PartDesign::Feature", "SameBodyLoftFirst")
        first.Shape = self._wire_profile(0)
        second = body.newObject("PartDesign::Feature", "SameBodyLoftSecond")
        second.Shape = self._wire_profile(10)
        body.Tip = second
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_tip = body.Tip
        self._select(first, second)

        Gui.runCommand("Part_Loft", 0)
        self._process_events()
        self._accept_task()
        loft = self._new_result(original_objects, "Part::Loft")
        self._assert_valid_shape(loft)
        self.assertIs(loft.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, loft)
        self.assertIn(first, body.Group)
        self.assertIn(second, body.Group)
        self.assertTrue(body.ViewObject.Visibility)
        self.assertFalse(first.ViewObject.Visibility)
        self.assertFalse(second.ViewObject.Visibility)
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            loft.PropertiesList,
        )

        self._assert_one_step_undo(original_objects)
        self.assertIs(body.Tip, original_tip)

    def test_loft_no_undo_accept_adopts_exact_validated_result(self):
        body = self.document.addObject("PartDesign::Body", "NoUndoLoftBody")
        first = body.newObject("PartDesign::Feature", "NoUndoLoftFirst")
        first.Shape = self._wire_profile(0)
        second = body.newObject("PartDesign::Feature", "NoUndoLoftSecond")
        second.Shape = self._wire_profile(10)
        body.Tip = second
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        self.document.UndoMode = False
        self._select(first, second)

        Gui.runCommand("Part_Loft", 0)
        self._process_events()
        self._accept_task()
        loft = self._new_result(original_objects, "Part::Loft")
        self._assert_valid_shape(loft)
        self.assertIs(loft.getParentGeoFeatureGroup(), body)
        self.assertEqual(tuple(body.Group), original_group + (loft,))
        self.assertIs(body.Tip, loft)

    def test_loft_face_profiles_preserve_tip_subelements_and_link_occurrence(self):
        first_body, first_tip = self._body_feature(
            "LoftFaceBody",
            "LoftFaceSource",
            Part.makeBox(6, 6, 2),
        )
        definition_body, definition_tip = self._body_feature(
            "LoftLinkDefinitionBody",
            "LoftLinkDefinition",
            Part.makeBox(6, 6, 2),
        )
        occurrence = self.document.addObject("App::Link", "LoftProfileOccurrence")
        occurrence.LinkedObject = definition_body
        occurrence.Placement = App.Placement(
            App.Vector(0, 0, 10),
            App.Rotation(),
        )
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_tips = (first_body.Tip, definition_body.Tip)
        Gui.activeView().setActiveObject("pdbody", first_body)
        self._select((first_body, "Face6"), (occurrence, "Face6"))
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            first_body,
        )

        Gui.runCommand("Part_Loft", 0)
        self._process_events()
        self._accept_task()
        loft = self._new_result(original_objects, "Part::Loft")
        self._assert_valid_shape(loft)
        self.assertEqual(list(loft.Sections), [first_tip, occurrence])
        profile_links = list(loft.ProfileLinks)
        self.assertEqual([item[0] for item in profile_links], [first_tip, occurrence])
        self.assertEqual(
            [tuple(item[1]) for item in profile_links],
            [("Face6",), ("Face6",)],
        )
        # The accepted result is prepared once by Loft and again by the
        # generic task lifecycle. The root occurrence is authoritative on
        # both passes even while the other profile's Body remains active.
        self.assertIsNone(loft.getParentGeoFeatureGroup())
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            first_body,
        )
        self.assertEqual((first_body.Tip, definition_body.Tip), original_tips)

        self._assert_one_step_undo(original_objects)

    def test_loft_legacy_sections_edit_clears_exact_profile_authority(self):
        first_body, first_tip = self._body_feature(
            "LegacyLoftFirstBody",
            "LegacyLoftFirstSource",
            Part.Face(self._wire_profile(0)),
        )
        second_body, second_tip = self._body_feature(
            "LegacyLoftSecondBody",
            "LegacyLoftSecondSource",
            Part.Face(self._wire_profile(10)),
        )
        self.document.recompute()

        loft = self.document.addObject("Part::Loft", "LegacyEditableLoft")
        loft.ProfileLinks = [
            (first_tip, ["Face1"]),
            (second_tip, ["Face1"]),
        ]
        self.document.recompute()
        self.assertEqual(list(loft.Sections), [first_tip, second_tip])
        self.assertEqual(len(list(loft.ProfileLinks)), 2)

        self.document.openTransaction("Legacy Sections edit")
        loft.Sections = [second_tip, first_tip]
        self.document.commitTransaction()
        self.document.recompute()
        self.assertEqual(list(loft.ProfileLinks), [])
        self.assertEqual(list(loft.Sections), [second_tip, first_tip])
        self._assert_valid_shape(loft)

        self.document.undo()
        self.document.recompute()
        self.assertEqual(list(loft.Sections), [first_tip, second_tip])
        self.assertEqual(len(list(loft.ProfileLinks)), 2)

    def test_loft_macro_records_only_the_accepted_operation(self):
        first_body, _first_tip = self._body_feature(
            "MacroLoftFirstBody",
            "MacroLoftFirstProfile",
            self._wire_profile(0),
        )
        second_body, _second_tip = self._body_feature(
            "MacroLoftSecondBody",
            "MacroLoftSecondProfile",
            self._wire_profile(10),
        )

        with tempfile.TemporaryDirectory(prefix="vibecad-macro-") as directory:
            macro_path = Path(directory) / "MultiInputAccepted.FCMacro"
            self._start_macro_recording(directory, "MultiInputAccepted")

            self._select(first_body)
            Gui.runCommand("Part_Loft", 0)
            self._process_events()
            self._accept_task(expect_close=False, dismiss_message=True)
            self._cancel_task()

            self._select(first_body, second_body)
            Gui.runCommand("Part_Loft", 0)
            self._process_events()
            self._accept_task()

            macro = self._stop_macro_recording(macro_path)

        self.assertEqual(macro.count(".addObject('Part::Loft'"), 1, macro)
        self.assertIn("__vibecad_loft.Sections =", macro)
        self.assertNotIn("Gui.runCommand('Part_Loft'", macro)

    def test_open_then_cancel_records_no_part_operation_preamble(self):
        first_body, _first_tip = self._body_feature(
            "CancelledMacroFirstBody",
            "CancelledMacroFirstProfile",
            self._wire_profile(0),
        )
        second_body, _second_tip = self._body_feature(
            "CancelledMacroSecondBody",
            "CancelledMacroSecondProfile",
            self._wire_profile(10),
        )

        with tempfile.TemporaryDirectory(prefix="vibecad-macro-") as directory:
            macro_path = Path(directory) / "CancelledPartTasks.FCMacro"
            self._start_macro_recording(directory, "CancelledPartTasks")

            self._select(first_body, second_body)
            Gui.runCommand("Part_Loft", 0)
            self._process_events()
            self._cancel_task()

            self._select(first_body)
            Gui.runCommand("Part_Sweep", 0)
            self._process_events()
            self._cancel_task()

            self._select(first_body)
            Gui.runCommand("Part_CrossSections", 0)
            self._process_events()
            self._cancel_task()

            Gui.Selection.clearSelection()
            Gui.runCommand("Part_ProjectionOnSurface", 0)
            self._process_events()
            self._cancel_task()

            macro = self._stop_macro_recording(macro_path)

        # The recorder always emits its active-module import. Cancelled tools
        # must contribute no operation or durable-result trace of their own.
        self.assertNotIn(".addObject('Part::Loft'", macro)
        self.assertNotIn(".addObject('Part::Sweep'", macro)
        self.assertNotIn(".addObject('Part::CrossSections'", macro)
        self.assertNotIn(".addObject('Part::ProjectOnSurface'", macro)

    def test_loft_invalid_accept_keeps_task_open_and_commits_nothing(self):
        body, tip = self._body_feature(
            "InvalidLoftBody",
            "InvalidLoftProfile",
            self._wire_profile(0),
        )
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        self.document.UndoMode = False
        self._select(body)

        Gui.runCommand("Part_Loft", 0)
        self._process_events()
        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertFalse(self.document.HasPendingTransaction)
        self._cancel_task()
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, tip)

    def test_loft_no_undo_kernel_failure_removes_attempt_and_restores_state(self):
        body = self.document.addObject(
            "PartDesign::Body",
            "NoUndoFailedLoftBody",
        )
        first = body.newObject(
            "PartDesign::Feature",
            "NoUndoFailedLoftFirst",
        )
        first.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(0, 4, 0),
        )
        second = body.newObject(
            "PartDesign::Feature",
            "NoUndoFailedLoftSecond",
        )
        second.Shape = Part.makeLine(
            App.Vector(10, 0, 0),
            App.Vector(10, 4, 0),
        )
        body.Tip = second
        Gui.activeView().setActiveObject("pdbody", body)
        first.ViewObject.Visibility = False
        second.ViewObject.Visibility = True
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        original_tip = body.Tip
        self.document.UndoMode = False
        self._select(first, second)
        Gui.activeDocument().Modified = False

        Gui.runCommand("Part_Loft", 0)
        self._process_events()
        solid = self._visible_widget(QtGui.QCheckBox, "checkSolid")
        self.assertIsNotNone(solid)
        solid.setChecked(True)
        expected_selection = self._selection_state()
        expected_active = self.document.ActiveObject
        expected_visibility = (
            first.ViewObject.Visibility,
            second.ViewObject.Visibility,
        )

        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)
        self.assertEqual(
            (
                first.ViewObject.Visibility,
                second.ViewObject.Visibility,
            ),
            expected_visibility,
        )
        self.assertEqual(self._selection_state(), expected_selection)
        self.assertIs(self.document.ActiveObject, expected_active)
        self.assertFalse(Gui.activeDocument().Modified)
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            body,
        )
        self._cancel_task()

    def test_sweep_profile_and_explicit_path_roles_accept_once(self):
        profile_body, profile_tip = self._body_feature(
            "SweepProfileBody",
            "SweepProfile",
            self._wire_profile(0),
        )
        path_body, path_tip = self._body_feature(
            "SweepPathBody",
            "SweepPath",
            Part.makeLine(App.Vector(0, 0, 0), App.Vector(12, 0, 0)),
        )
        original_objects = tuple(self.document.Objects)
        original_tips = (profile_body.Tip, path_body.Tip)
        profile_body.ViewObject.Visibility = True
        path_body.ViewObject.Visibility = True
        original_visibility = (
            profile_body.ViewObject.Visibility,
            path_body.ViewObject.Visibility,
        )
        self._select(profile_body)

        Gui.runCommand("Part_Sweep", 0)
        self._process_events()
        path_button = self._visible_widget(QtGui.QPushButton, "buttonPath")
        self.assertIsNotNone(path_button)
        path_button.click()
        self._process_events()
        self._select((path_body, "Edge1"))
        path_button.click()
        self._process_events()

        self._accept_task()
        sweep = self._new_result(original_objects, "Part::Sweep")
        self._assert_valid_shape(sweep)
        self.assertEqual(list(sweep.Sections), [profile_tip])
        self.assertEqual(list(sweep.ProfileLinks), [])
        self.assertIs(sweep.Spine[0], path_tip)
        self.assertEqual(tuple(sweep.Spine[1]), ("Edge1",))
        self.assertIsNone(sweep.getParentGeoFeatureGroup())
        self.assertEqual((profile_body.Tip, path_body.Tip), original_tips)
        self.assertEqual(
            list(sweep.VibeCADTimelineReplacedInputs),
            [profile_body, path_body],
        )
        self.assertFalse(profile_body.ViewObject.Visibility)
        self.assertFalse(path_body.ViewObject.Visibility)
        self.assertTrue(sweep.ViewObject.Visibility)

        self._assert_one_step_undo(original_objects)
        self.assertEqual(
            (
                profile_body.ViewObject.Visibility,
                path_body.ViewObject.Visibility,
            ),
            original_visibility,
        )

    def test_sweep_rejects_same_name_path_replacement_while_task_is_open(self):
        profile = self.document.addObject(
            "Part::Feature",
            "ExactSweepProfile",
        )
        profile.Shape = self._wire_profile(0)
        path = self.document.addObject(
            "Part::Feature",
            "ExactSweepPath",
        )
        path.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(12, 0, 0),
        )
        self.document.recompute()
        path_name = path.Name
        original_sweeps = tuple(self.document.findObjects("Part::Sweep"))
        self._select(profile)

        Gui.runCommand("Part_Sweep", 0)
        self._process_events()
        path_button = self._visible_widget(
            QtGui.QPushButton,
            "buttonPath",
        )
        self.assertIsNotNone(path_button)
        path_button.click()
        self._process_events()
        self._select((path, "Edge1"))
        path_button.click()
        self._process_events()

        self.document.removeObject(path_name)
        replacement = self.document.addObject(
            "Part::Feature",
            path_name,
        )
        replacement.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(12, 0, 0),
        )
        self.document.recompute()
        self.assertEqual(replacement.Name, path_name)

        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertEqual(
            tuple(self.document.findObjects("Part::Sweep")),
            original_sweeps,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self._cancel_task()

    def test_sweep_no_undo_accept_adopts_exact_validated_result(self):
        body = self.document.addObject("PartDesign::Body", "NoUndoSweepBody")
        profile = body.newObject("PartDesign::Feature", "NoUndoSweepProfile")
        profile.Shape = self._wire_profile(0)
        path = body.newObject("PartDesign::Feature", "NoUndoSweepPath")
        path.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(12, 0, 0),
        )
        body.Tip = path
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        self.document.UndoMode = False
        self._select(profile)

        Gui.runCommand("Part_Sweep", 0)
        self._process_events()
        path_button = self._visible_widget(QtGui.QPushButton, "buttonPath")
        self.assertIsNotNone(path_button)
        path_button.click()
        self._process_events()
        self._select((path, "Edge1"))
        path_button.click()
        self._process_events()

        self._accept_task()
        sweep = self._new_result(original_objects, "Part::Sweep")
        self._assert_valid_shape(sweep)
        self.assertIs(sweep.getParentGeoFeatureGroup(), body)
        self.assertEqual(tuple(body.Group), original_group + (sweep,))
        self.assertIs(body.Tip, sweep)
        self.assertTrue(body.ViewObject.Visibility)
        self.assertFalse(profile.ViewObject.Visibility)
        self.assertFalse(path.ViewObject.Visibility)
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            sweep.PropertiesList,
        )

    def test_sweep_face_profile_preserves_exact_profile_link(self):
        profile_body, profile_tip = self._body_feature(
            "SweepFaceProfileBody",
            "SweepFaceProfile",
            Part.Face(self._wire_profile(0)),
        )
        path_body, path_tip = self._body_feature(
            "SweepFacePathBody",
            "SweepFacePath",
            Part.makeLine(App.Vector(0, 2, 2), App.Vector(12, 2, 2)),
        )
        original_objects = tuple(self.document.Objects)
        original_tips = (profile_body.Tip, path_body.Tip)
        self._select((profile_body, "Face1"))

        Gui.runCommand("Part_Sweep", 0)
        self._process_events()
        path_button = self._visible_widget(QtGui.QPushButton, "buttonPath")
        self.assertIsNotNone(path_button)
        path_button.click()
        self._process_events()
        self._select((path_body, "Edge1"))
        path_button.click()
        self._process_events()

        self._accept_task()
        sweep = self._new_result(original_objects, "Part::Sweep")
        self._assert_valid_shape(sweep)
        self.assertEqual(list(sweep.Sections), [profile_tip])
        profile_links = list(sweep.ProfileLinks)
        self.assertEqual(len(profile_links), 1)
        self.assertIs(profile_links[0][0], profile_tip)
        self.assertEqual(tuple(profile_links[0][1]), ("Face1",))
        self.assertIs(sweep.Spine[0], path_tip)
        self.assertEqual(tuple(sweep.Spine[1]), ("Edge1",))
        self.assertIsNone(sweep.getParentGeoFeatureGroup())
        self.assertEqual((profile_body.Tip, path_body.Tip), original_tips)

        self._assert_one_step_undo(original_objects)

    def test_sweep_legacy_sections_edit_clears_exact_profile_authority(self):
        _first_body, first_tip = self._body_feature(
            "LegacySweepFirstBody",
            "LegacySweepFirstProfile",
            self._wire_profile(0, 4),
        )
        _second_body, second_tip = self._body_feature(
            "LegacySweepSecondBody",
            "LegacySweepSecondProfile",
            self._wire_profile(0, 3),
        )
        _path_body, path_tip = self._body_feature(
            "LegacySweepPathBody",
            "LegacySweepPath",
            Part.makeLine(App.Vector(0, 0, 0), App.Vector(12, 0, 0)),
        )

        sweep = self.document.addObject("Part::Sweep", "LegacyEditableSweep")
        sweep.ProfileLinks = [(first_tip, ["Wire1"])]
        sweep.Spine = (path_tip, ["Edge1"])
        sweep.Solid = False
        self.document.recompute()
        self.assertEqual(list(sweep.Sections), [first_tip])
        self.assertEqual(len(list(sweep.ProfileLinks)), 1)

        self.document.openTransaction("Legacy Sweep Sections edit")
        sweep.Sections = [second_tip]
        self.document.commitTransaction()
        self.document.recompute()
        self.assertEqual(list(sweep.ProfileLinks), [])
        self.assertEqual(list(sweep.Sections), [second_tip])
        self._assert_valid_shape(sweep)

        self.document.undo()
        self.document.recompute()
        self.assertEqual(list(sweep.Sections), [first_tip])
        self.assertEqual(len(list(sweep.ProfileLinks)), 1)

    def test_sweep_missing_path_keeps_task_open_without_junk(self):
        body, tip = self._body_feature(
            "MissingSweepPathBody",
            "MissingSweepPathProfile",
            self._wire_profile(0),
        )
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        self.document.UndoMode = False
        self._select(body)

        Gui.runCommand("Part_Sweep", 0)
        self._process_events()
        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertFalse(self.document.HasPendingTransaction)
        self._cancel_task()
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, tip)

    def test_sweep_no_undo_kernel_failure_removes_attempt_and_restores_state(self):
        body = self.document.addObject(
            "PartDesign::Body",
            "NoUndoFailedSweepBody",
        )
        profile = body.newObject(
            "PartDesign::Feature",
            "NoUndoFailedSweepProfile",
        )
        profile.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(0, 4, 0),
        )
        path = body.newObject(
            "PartDesign::Feature",
            "NoUndoFailedSweepPath",
        )
        path.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(12, 0, 0),
        )
        body.Tip = path
        Gui.activeView().setActiveObject("pdbody", body)
        profile.ViewObject.Visibility = False
        path.ViewObject.Visibility = True
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        original_tip = body.Tip
        self.document.UndoMode = False
        self._select(profile)
        Gui.activeDocument().Modified = False

        Gui.runCommand("Part_Sweep", 0)
        self._process_events()
        path_button = self._visible_widget(QtGui.QPushButton, "buttonPath")
        self.assertIsNotNone(path_button)
        path_button.click()
        self._process_events()
        self._select((path, "Edge1"))
        path_button.click()
        self._process_events()
        solid = self._visible_widget(QtGui.QCheckBox, "checkSolid")
        self.assertIsNotNone(solid)
        solid.setChecked(True)
        expected_selection = self._selection_state()
        expected_active = self.document.ActiveObject
        expected_visibility = (
            profile.ViewObject.Visibility,
            path.ViewObject.Visibility,
        )

        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)
        self.assertEqual(
            (
                profile.ViewObject.Visibility,
                path.ViewObject.Visibility,
            ),
            expected_visibility,
        )
        self.assertEqual(self._selection_state(), expected_selection)
        self.assertIs(self.document.ActiveObject, expected_active)
        self.assertFalse(Gui.activeDocument().Modified)
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            body,
        )
        self._cancel_task()

    def test_projection_explicit_roles_direction_and_same_body_ownership(self):
        body = self.document.addObject("PartDesign::Body", "ProjectionBody")
        source = body.newObject("PartDesign::Feature", "ProjectionSource")
        source.Shape = Part.makeLine(
            App.Vector(2, 2, 10),
            App.Vector(8, 2, 10),
        )
        target = body.newObject("PartDesign::Feature", "ProjectionTarget")
        target.Shape = Part.makeBox(10, 10, 5)
        body.Tip = target
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_tip = body.Tip
        Gui.Selection.clearSelection()

        Gui.runCommand("Part_ProjectionOnSurface", 0)
        self._process_events()
        projection = self._new_result(original_objects, "Part::ProjectOnSurface")
        target_button = self._visible_widget(
            QtGui.QPushButton,
            "pushButtonAddProjFace",
        )
        source_button = self._visible_widget(
            QtGui.QPushButton,
            "pushButtonAddEdge",
        )
        self.assertIsNotNone(target_button)
        self.assertIsNotNone(source_button)

        target_button.click()
        self._select((target, "Face6"))
        source_button.click()
        self._select((source, "Edge1"))
        source_button.click()
        self._process_events()

        dir_x = self._visible_widget(QtGui.QDoubleSpinBox, "doubleSpinBoxDirX")
        dir_y = self._visible_widget(QtGui.QDoubleSpinBox, "doubleSpinBoxDirY")
        dir_z = self._visible_widget(QtGui.QDoubleSpinBox, "doubleSpinBoxDirZ")
        self.assertIsNotNone(dir_x)
        self.assertIsNotNone(dir_y)
        self.assertIsNotNone(dir_z)
        dir_x.setValue(0)
        dir_y.setValue(0)
        dir_z.setValue(-1)
        self._process_events()
        self.assertEqual(tuple(projection.Direction), (0.0, 0.0, -1.0))

        self._accept_task()
        self._assert_valid_shape(projection)
        self.assertIs(projection.SupportFace[0], target)
        self.assertEqual(tuple(projection.SupportFace[1]), ("Face6",))
        projection_links = list(projection.Projection)
        self.assertEqual(len(projection_links), 1)
        self.assertIs(projection_links[0][0], source)
        self.assertEqual(tuple(projection_links[0][1]), ("Edge1",))
        self.assertIs(projection.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, projection)

        self._assert_one_step_undo(original_objects)
        self.assertIs(body.Tip, original_tip)

    def test_projection_no_undo_accept_adopts_exact_validated_result(self):
        body = self.document.addObject("PartDesign::Body", "NoUndoProjectionBody")
        source = body.newObject(
            "PartDesign::Feature",
            "NoUndoProjectionSource",
        )
        source.Shape = Part.makeLine(
            App.Vector(2, 2, 10),
            App.Vector(8, 2, 10),
        )
        target = body.newObject(
            "PartDesign::Feature",
            "NoUndoProjectionTarget",
        )
        target.Shape = Part.makeBox(10, 10, 5)
        body.Tip = target
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        self.document.UndoMode = False
        Gui.Selection.clearSelection()

        Gui.runCommand("Part_ProjectionOnSurface", 0)
        self._process_events()
        projection = self._new_result(
            original_objects,
            "Part::ProjectOnSurface",
        )
        target_button = self._visible_widget(
            QtGui.QPushButton,
            "pushButtonAddProjFace",
        )
        source_button = self._visible_widget(
            QtGui.QPushButton,
            "pushButtonAddEdge",
        )
        self.assertIsNotNone(target_button)
        self.assertIsNotNone(source_button)

        target_button.click()
        self._select((target, "Face6"))
        source_button.click()
        self._select((source, "Edge1"))
        source_button.click()
        self._process_events()

        dir_x = self._visible_widget(
            QtGui.QDoubleSpinBox,
            "doubleSpinBoxDirX",
        )
        dir_y = self._visible_widget(
            QtGui.QDoubleSpinBox,
            "doubleSpinBoxDirY",
        )
        dir_z = self._visible_widget(
            QtGui.QDoubleSpinBox,
            "doubleSpinBoxDirZ",
        )
        self.assertIsNotNone(dir_x)
        self.assertIsNotNone(dir_y)
        self.assertIsNotNone(dir_z)
        dir_x.setValue(0)
        dir_y.setValue(0)
        dir_z.setValue(-1)
        self._process_events()

        self._accept_task()
        self._assert_valid_shape(projection)
        self.assertIs(projection.getParentGeoFeatureGroup(), body)
        self.assertEqual(tuple(body.Group), original_group + (projection,))
        self.assertIs(body.Tip, projection)

    def test_projection_missing_roles_remains_provisional_until_cancel(self):
        body, _tip = self._body_feature(
            "InvalidProjectionBody",
            "InvalidProjectionTarget",
            Part.makeBox(10, 10, 5),
        )
        Gui.activeView().setActiveObject("pdbody", body)
        original_objects = tuple(self.document.Objects)
        Gui.Selection.clearSelection()

        Gui.runCommand("Part_ProjectionOnSurface", 0)
        self._process_events()
        self.assertTrue(self.document.HasPendingTransaction)
        provisional_objects = tuple(self.document.Objects)
        self.assertGreater(len(provisional_objects), len(original_objects))

        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertEqual(tuple(self.document.Objects), provisional_objects)
        self._cancel_task()
        self.assertEqual(tuple(self.document.Objects), original_objects)

    def test_projection_no_undo_failed_accept_and_cancel_never_adopt_provisional(self):
        body, original_tip = self._body_feature(
            "NoUndoInvalidProjectionBody",
            "NoUndoInvalidProjectionTarget",
            Part.makeBox(10, 10, 5),
        )
        Gui.activeView().setActiveObject("pdbody", body)
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        self.document.UndoMode = False
        original_tip.ViewObject.Visibility = True
        self._select((original_tip, "Face1"))
        original_selection = self._selection_state()
        original_visibility = original_tip.ViewObject.Visibility
        original_active = self.document.ActiveObject

        Gui.runCommand("Part_ProjectionOnSurface", 0)
        self._process_events()
        projection = self._new_result(
            original_objects,
            "Part::ProjectOnSurface",
        )
        self.assertIsNone(projection.getParentGeoFeatureGroup())

        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertIsNone(projection.getParentGeoFeatureGroup())
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)

        projection_name = projection.Name
        self._cancel_task()
        self.assertIsNone(self.document.getObject(projection_name))
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)
        self.assertEqual(
            original_tip.ViewObject.Visibility,
            original_visibility,
        )
        self.assertEqual(self._selection_state(), original_selection)
        self.assertIs(self.document.ActiveObject, original_active)
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            body,
        )

    def test_projection_no_undo_cancel_edit_never_deletes_existing_feature(self):
        projection = self.document.addObject(
            "Part::ProjectOnSurface",
            "ExistingProjection",
        )
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        self.document.UndoMode = False

        Gui.activeDocument().setEdit(projection.Name)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self._cancel_task()

        self.assertIs(
            self.document.getObject(projection.Name),
            projection,
        )
        self.assertEqual(tuple(self.document.Objects), original_objects)

    def test_cross_sections_accepts_valid_body_source_and_undoes_once(self):
        body, source = self._body_feature(
            "CrossSectionBody",
            "CrossSectionSource",
            Part.makeBox(10, 10, 10),
        )
        Gui.activeView().setActiveObject("pdbody", body)
        original_objects = tuple(self.document.Objects)
        original_tip = body.Tip
        self._select(body)

        Gui.runCommand("Part_CrossSections", 0)
        self._process_events()
        self._accept_task()
        result = self._new_result(original_objects, "Part::CrossSections")
        self._assert_valid_shape(result)
        self.assertIs(result.Source[0], source)
        self.assertEqual(tuple(result.Source[1]), ())
        self.assertGreater(len(result.PlanePositions), 0)
        self.assertGreater(len(result.Shape.Edges), 0)
        self.assertIs(result.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, result)

        self._assert_one_step_undo(original_objects)
        self.assertIs(body.Tip, original_tip)

    def test_cross_sections_recompute_from_source_and_plane_properties(self):
        source = self.document.addObject("Part::Box", "ParametricCrossSource")
        source.Length = 10.0
        source.Width = 8.0
        source.Height = 12.0
        result = self.document.addObject(
            "Part::CrossSections",
            "ParametricCrossResult",
        )
        result.Source = (source, [])
        result.PlaneNormal = App.Vector(0, 0, 1)
        result.PlanePositions = [5.0]
        self.document.recompute()

        self._assert_valid_shape(result)
        self.assertAlmostEqual(result.Shape.BoundBox.XLength, 10.0)
        self.assertAlmostEqual(result.Shape.BoundBox.ZMin, 5.0)
        self.assertAlmostEqual(result.Shape.BoundBox.ZMax, 5.0)

        source.Length = 23.0
        result.PlanePositions = [7.0]
        self.document.recompute()

        self._assert_valid_shape(result)
        self.assertAlmostEqual(result.Shape.BoundBox.XLength, 23.0)
        self.assertAlmostEqual(result.Shape.BoundBox.ZMin, 7.0)
        self.assertAlmostEqual(result.Shape.BoundBox.ZMax, 7.0)

    def test_cross_sections_partial_failure_rolls_back_exactly_without_undo(self):
        valid_source = self.document.addObject(
            "Part::Feature",
            "ValidCrossSource",
        )
        valid_source.Shape = Part.makeBox(10, 10, 10)
        missed_source = self.document.addObject(
            "Part::Feature",
            "MissedCrossSource",
        )
        missed_source.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(0, 0, 100),
        )
        self.document.recompute()
        original_objects = tuple(self.document.Objects)
        self.document.UndoMode = False
        self._select(valid_source, missed_source)
        Gui.activeDocument().Modified = False

        Gui.runCommand("Part_CrossSections", 0)
        self._process_events()
        position = self._visible_widget(QtGui.QWidget, "position")
        self.assertIsNotNone(position)
        self.assertTrue(position.setProperty("rawValue", 5.0))
        self._process_events()
        self._accept_task(expect_close=False, dismiss_message=True)

        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertFalse(self.document.HasPendingTransaction)
        # The task remains open, so its private UndoMode=0 rollback journal
        # remains enabled for the next Apply attempt.  It must not expose a
        # pending transaction, and Cancel must restore the user's setting.
        self.assertTrue(self.document.UndoMode)
        self.assertFalse(Gui.activeDocument().Modified)
        self._cancel_task()
        self.assertFalse(self.document.UndoMode)
        self.assertFalse(Gui.activeDocument().Modified)

    def test_cross_sections_non_intersection_aborts_without_output(self):
        body, _source = self._body_feature(
            "InvalidCrossSectionBody",
            "InvalidCrossSectionSource",
            Part.makeBox(10, 10, 10),
        )
        original_objects = tuple(self.document.Objects)
        self._select(body)

        Gui.runCommand("Part_CrossSections", 0)
        self._process_events()
        position = self._visible_widget(QtGui.QWidget, "position")
        self.assertIsNotNone(position)
        self.assertTrue(position.setProperty("rawValue", 1000.0))
        self._process_events()
        self._accept_task(expect_close=False, dismiss_message=True)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertFalse(self.document.HasPendingTransaction)
        self._cancel_task()

    def test_immediate_multi_output_tools_publish_one_history_operation(self):
        parameter_group = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Part"
        )
        original_parametric_refine = parameter_group.GetBool(
            "ParametricRefine",
            True,
        )
        cases = (
            ("Part_SimpleCopy", "simple", False, None),
            ("Part_TransformedCopy", "transformed", False, None),
            ("Part_ElementCopy", "element", False, None),
            ("Part_RefineShape", "parametric_refine", True, True),
            ("Part_RefineShape", "shape_refine", True, False),
            ("Part_ReverseShape", "reverse", True, None),
            ("Part_MakeSolid", "solid", True, None),
        )
        try:
            for index, (
                command_name,
                prefix,
                replaces_sources,
                parametric_refine,
            ) in enumerate(cases):
                with self.subTest(command_name=command_name, mode=prefix):
                    if parametric_refine is not None:
                        parameter_group.SetBool(
                            "ParametricRefine",
                            parametric_refine,
                        )
                    first_shape = Part.makeBox(
                        6,
                        7,
                        8,
                        App.Vector(index * 30, 0, 0),
                    )
                    second_shape = Part.makeBox(
                        5,
                        6,
                        7,
                        App.Vector(index * 30 + 12, 0, 0),
                    )
                    if command_name == "Part_MakeSolid":
                        first_shape = Part.makeShell(first_shape.Faces)
                        second_shape = Part.makeShell(second_shape.Faces)
                    first = self._root_feature(
                        f"{prefix}First",
                        first_shape,
                    )
                    second = self._root_feature(
                        f"{prefix}Second",
                        second_shape,
                    )
                    original_objects = tuple(self.document.Objects)
                    if command_name == "Part_ElementCopy":
                        self._select(
                            (first, "Face1"),
                            (second, "Face1"),
                        )
                    else:
                        self._select(first, second)
                    self.assertTrue(
                        Gui.isCommandActive(command_name),
                        command_name,
                    )
                    Gui.runCommand(command_name, 0)
                    operation, resources = self._assert_grouped_outputs(
                        original_objects,
                        (first, second),
                        replaces_sources=replaces_sources,
                    )

                    if prefix == "parametric_refine":
                        operation_name = operation.Name
                        resource_name = resources[0].Name
                        with tempfile.TemporaryDirectory() as directory:
                            saved = Path(directory) / "grouped.FCStd"
                            reopened = Path(directory) / "grouped-copy.FCStd"
                            self.document.saveAs(str(saved))
                            reopened.write_bytes(saved.read_bytes())
                            restored = App.openDocument(
                                str(reopened),
                                True,
                            )
                            try:
                                restored_operation = restored.getObject(
                                    operation_name
                                )
                                restored_resource = restored.getObject(
                                    resource_name
                                )
                                self.assertEqual(
                                    restored_operation.VibeCADTimelineRole,
                                    "operation",
                                )
                                self.assertEqual(
                                    restored_resource.VibeCADTimelineRole,
                                    "resource",
                                )
                                self.assertIs(
                                    restored_resource.VibeCADTimelineOwner,
                                    restored_operation,
                                )
                                self.assertNotIn(
                                    "VibeCADTimelineReplacedInputs",
                                    restored_resource.PropertiesList,
                                )
                            finally:
                                App.closeDocument(restored.Name)
                                App.setActiveDocument(self.document.Name)
                                self._process_events()

                    self._assert_one_step_undo(original_objects)
                    self.assertTrue(first.Visibility)
                    self.assertTrue(second.Visibility)
        finally:
            parameter_group.SetBool(
                "ParametricRefine",
                original_parametric_refine,
            )

    def test_single_output_copy_keeps_native_history_shape(self):
        source = self._root_feature(
            "SingleCopySource",
            Part.makeBox(7, 8, 9),
        )
        original_objects = tuple(self.document.Objects)
        self._select(source)

        Gui.runCommand("Part_SimpleCopy", 0)
        outputs = self._created_shape_results(original_objects)
        self.assertEqual(
            len(outputs),
            1,
            [(obj.Name, obj.TypeId) for obj in outputs],
        )
        result = outputs[0]
        self._assert_valid_shape(result)
        self.assertNotIn(
            "VibeCADTimelineRole",
            result.PropertiesList,
        )
        self.assertNotIn(
            "VibeCADTimelineOwner",
            result.PropertiesList,
        )
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            result.PropertiesList,
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(result, list(timeline.Operations))
        self.assertTrue(source.Visibility)
        self._assert_one_step_undo(original_objects)

    def test_duplicate_selection_adopts_exact_active_body_in_history_order(self):
        target_body, target_tip = self._body_feature(
            "DuplicateTargetBody",
            "DuplicateTargetSeed",
            Part.makeBox(4, 5, 6),
        )
        other_body, other_tip = self._body_feature(
            "DuplicateOtherBody",
            "DuplicateOtherSeed",
            Part.makeBox(
                3,
                4,
                5,
                App.Vector(-20, 0, 0),
            ),
        )
        first = self._root_feature(
            "DuplicateChronologyFirst",
            Part.makeBox(
                5,
                6,
                7,
                App.Vector(20, 0, 0),
            ),
        )
        second = self._root_feature(
            "DuplicateChronologySecond",
            Part.makeCylinder(
                3,
                8,
                App.Vector(40, 0, 0),
            ),
        )
        Gui.activeView().setActiveObject("pdbody", target_body)
        target_body.ViewObject.Visibility = False
        self.document.recompute()

        original_objects = tuple(self.document.Objects)
        original_target_group = tuple(target_body.Group)
        original_other_group = tuple(other_body.Group)
        original_undo_count = int(self.document.UndoCount)

        # Click order is deliberately opposite to semantic History order.
        # Duplication must adopt and publish the copied roots in source
        # chronology, into the exact active Body only.
        self._select(second, first)
        Gui.runCommand("PartDesign_DuplicateSelection", 0)
        self._process_events(50)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(
            int(self.document.UndoCount),
            original_undo_count + 1,
        )
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            target_body,
        )
        self.assertEqual(tuple(other_body.Group), original_other_group)
        self.assertIs(other_body.Tip, other_tip)

        adopted = tuple(target_body.Group[len(original_target_group):])
        self.assertEqual(len(adopted), 2)
        created = self._created_shape_results(original_objects)
        self.assertEqual(len(created), 2)
        self.assertTrue(all(output in created for output in adopted))
        for output in adopted:
            self._assert_valid_shape(output)
            self.assertIs(
                output.getParentGeoFeatureGroup(),
                target_body,
            )
            self.assertNotIn(
                "VibeCADTimelineOwner",
                output.PropertiesList,
            )

        self.assertAlmostEqual(
            adopted[0].Shape.BoundBox.XMin,
            first.Shape.BoundBox.XMin,
        )
        self.assertAlmostEqual(
            adopted[0].Shape.Volume,
            first.Shape.Volume,
        )
        self.assertAlmostEqual(
            adopted[1].Shape.BoundBox.XMin,
            second.Shape.BoundBox.XMin,
        )
        self.assertAlmostEqual(
            adopted[1].Shape.Volume,
            second.Shape.Volume,
        )
        self.assertIs(target_body.Tip, adopted[-1])
        self.assertTrue(target_body.ViewObject.Visibility)
        self.assertTrue(adopted[-1].ViewObject.Visibility)
        self.assertEqual(
            self._selection_state(),
            tuple((output, ()) for output in adopted),
        )

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        operations = list(timeline.Operations)
        for output in adopted:
            self.assertIn(output, operations)
        self.assertLess(
            operations.index(adopted[0]),
            operations.index(adopted[1]),
        )
        self.assertTrue(first.ViewObject.Visibility)
        self.assertTrue(second.ViewObject.Visibility)

        # The complete two-output adoption is one undoable operation.
        self._assert_one_step_undo(original_objects)
        self.assertEqual(tuple(target_body.Group), original_target_group)
        self.assertIs(target_body.Tip, target_tip)
        self.assertFalse(target_body.ViewObject.Visibility)
        self.assertEqual(tuple(other_body.Group), original_other_group)
        self.assertIs(other_body.Tip, other_tip)
        self.assertTrue(first.ViewObject.Visibility)
        self.assertTrue(second.ViewObject.Visibility)

    def test_points_duplicate_and_cross_sections_group_multi_outputs(self):
        first = self._root_feature(
            "GroupedPointsFirst",
            Part.makeBox(6, 6, 6),
        )
        second = self._root_feature(
            "GroupedPointsSecond",
            Part.makeBox(6, 6, 6, App.Vector(12, 0, 0)),
        )
        original_objects = tuple(self.document.Objects)
        self._select(first, second)
        accepted = self._accept_input_dialog()
        import BasicShapes.Utils as points_utils

        original_points_factory = points_utils.showCompoundFromPoints
        provisional_states = []
        provisional_outputs = []

        def observed_points_factory(*args, **kwargs):
            result = original_points_factory(*args, **kwargs)
            provisional_outputs.append(result)
            provisional_states.append(
                [
                    self.document
                    .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                        output
                    )
                    for output in provisional_outputs
                ]
            )
            return result

        points_utils.showCompoundFromPoints = observed_points_factory
        try:
            Gui.runCommand("Part_PointsFromMesh", 0)
        finally:
            points_utils.showCompoundFromPoints = original_points_factory
        self.assertTrue(accepted)
        self.assertEqual(provisional_states, [[True], [True, True]])
        self._assert_grouped_outputs(
            original_objects,
            (first, second),
            replaces_sources=False,
        )
        self._assert_one_step_undo(original_objects)

        duplicate_first = self._root_feature(
            "GroupedDuplicateFirst",
            Part.makeCylinder(3, 8),
        )
        duplicate_second = self._root_feature(
            "GroupedDuplicateSecond",
            Part.makeCylinder(2, 6, App.Vector(10, 0, 0)),
        )
        original_objects = tuple(self.document.Objects)
        Gui.activeView().setActiveObject("pdbody", None)
        # Click order is not semantic History order. Duplicate must preserve
        # both independent copied blocks even when the user selects them in
        # reverse chronology.
        self._select(duplicate_second, duplicate_first)
        Gui.runCommand("PartDesign_DuplicateSelection", 0)
        self._process_events(50)
        duplicate_outputs = self._created_shape_results(
            original_objects
        )
        self.assertEqual(
            len(duplicate_outputs),
            2,
            [
                (obj.Name, obj.TypeId)
                for obj in duplicate_outputs
            ],
        )
        duplicate_timeline = self.document.getObject(
            "VibeCADTimeline"
        )
        self.assertIsNotNone(duplicate_timeline)
        for output in duplicate_outputs:
            self._assert_valid_shape(output)
            self.assertIn(
                output,
                list(duplicate_timeline.Operations),
            )
            self.assertNotIn(
                "VibeCADTimelineOwner",
                output.PropertiesList,
            )
        self.assertTrue(duplicate_first.Visibility)
        self.assertTrue(duplicate_second.Visibility)
        self._assert_one_step_undo(original_objects)

        section_first = self._root_feature(
            "GroupedSectionFirst",
            Part.makeBox(10, 10, 10),
        )
        section_second = self._root_feature(
            "GroupedSectionSecond",
            Part.makeBox(10, 10, 10, App.Vector(15, 0, 0)),
        )
        original_objects = tuple(self.document.Objects)
        self._select(section_first, section_second)
        Gui.runCommand("Part_CrossSections", 0)
        self._process_events()
        self._accept_task()
        self._assert_grouped_outputs(
            original_objects,
            (section_first, section_second),
            replaces_sources=False,
        )
        self._assert_one_step_undo(original_objects)

    def test_defeaturing_multi_outputs_publish_one_replacement_step(self):
        sources = []
        selections = []
        for index, x in enumerate((0.0, 20.0)):
            drilled = Part.makeBox(
                12,
                12,
                8,
                App.Vector(x, 0, 0),
            ).cut(
                Part.makeCylinder(
                    2,
                    8,
                    App.Vector(x + 6, 6, 0),
                )
            )
            source = self._root_feature(
                f"GroupedDefeatureSource{index}",
                drilled,
            )
            cylinder_face = next(
                (
                    face_index
                    for face_index, face in enumerate(
                        source.Shape.Faces,
                        start=1,
                    )
                    if "Cylinder" in type(face.Surface).__name__
                ),
                None,
            )
            self.assertIsNotNone(cylinder_face)
            sources.append(source)
            selections.append((source, f"Face{cylinder_face}"))

        original_objects = tuple(self.document.Objects)
        self._select(*selections)
        self.assertTrue(Gui.isCommandActive("Part_Defeaturing"))
        Gui.runCommand("Part_Defeaturing", 0)
        self._assert_grouped_outputs(
            original_objects,
            sources,
            replaces_sources=True,
        )
        self._assert_one_step_undo(original_objects)

    def test_copy_points_make_solid_and_duplicate_fail_atomically(self):
        failure_cases = []

        copy_first = self._root_feature(
            "AtomicCopyFirst",
            Part.makeBox(5, 6, 7),
        )
        copy_second = self._root_feature(
            "AtomicCopySecond",
            Part.makeBox(5, 6, 7, App.Vector(10, 0, 0)),
        )

        def fail_copy():
            original = Part.getShape
            calls = []

            def failing_get_shape(*args, **kwargs):
                calls.append(True)
                if len(calls) == 2:
                    raise RuntimeError("Deliberate second copy failure")
                return original(*args, **kwargs)

            Part.getShape = failing_get_shape
            try:
                Gui.runCommand("Part_SimpleCopy", 0)
            finally:
                Part.getShape = original
            self.assertEqual(len(calls), 2)

        failure_cases.append(
            ("copy", (copy_first, copy_second), fail_copy, False)
        )

        points_first = self._root_feature(
            "AtomicPointsFirst",
            Part.makeBox(4, 4, 4, App.Vector(30, 0, 0)),
        )
        points_second = self._root_feature(
            "AtomicPointsSecond",
            Part.makeBox(4, 4, 4, App.Vector(40, 0, 0)),
        )

        def fail_points():
            import BasicShapes.Utils as utils

            original = utils.showCompoundFromPoints
            calls = []

            def failing_points(*args, **kwargs):
                calls.append(True)
                if len(calls) == 2:
                    raise RuntimeError("Deliberate second points failure")
                return original(*args, **kwargs)

            utils.showCompoundFromPoints = failing_points
            accepted = self._accept_input_dialog()
            try:
                Gui.runCommand("Part_PointsFromMesh", 0)
            finally:
                utils.showCompoundFromPoints = original
            self.assertTrue(accepted)
            self.assertEqual(len(calls), 2)

        failure_cases.append(
            (
                "points",
                (points_first, points_second),
                fail_points,
                False,
            )
        )

        solid_first = self._root_feature(
            "AtomicSolidFirst",
            Part.makeShell(
                Part.makeBox(
                    5,
                    5,
                    5,
                    App.Vector(60, 0, 0),
                ).Faces
            ),
        )
        solid_second = self._root_feature(
            "AtomicSolidSecond",
            Part.makeShell(
                Part.makeBox(
                    5,
                    5,
                    5,
                    App.Vector(70, 0, 0),
                ).Faces
            ),
        )

        def fail_solid():
            original = Part.Solid
            calls = []

            def failing_solid(*args, **kwargs):
                calls.append(True)
                if len(calls) == 2:
                    raise RuntimeError("Deliberate second solid failure")
                return original(*args, **kwargs)

            Part.Solid = failing_solid
            try:
                Gui.runCommand("Part_MakeSolid", 0)
            finally:
                Part.Solid = original
            self.assertEqual(len(calls), 2)

        failure_cases.append(
            (
                "solid",
                (solid_first, solid_second),
                fail_solid,
                False,
            )
        )

        for name, sources, invoke, use_subelements in failure_cases:
            with self.subTest(name=name):
                original_objects = tuple(self.document.Objects)
                if use_subelements:
                    self._select(
                        *((source, "Face1") for source in sources)
                    )
                else:
                    self._select(*sources)
                original_visibility = tuple(
                    obj.Visibility for obj in original_objects
                )
                original_selection = self._selection_state()
                original_active = self.document.ActiveObject
                Gui.activeDocument().Modified = False
                original_modified = bool(
                    Gui.activeDocument().Modified
                )
                original_undo_count = int(self.document.UndoCount)
                try:
                    invoke()
                except RuntimeError as error:
                    self.assertIn("Deliberate", str(error))
                self._assert_failed_multi_selection_is_atomic(
                    original_objects,
                    original_visibility,
                    original_selection,
                    original_active,
                    original_modified,
                    original_undo_count,
                )


if __name__ == "__main__":
    unittest.main()
