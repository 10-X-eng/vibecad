# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD contracts for native Part Design task acceptance and cancellation."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design objects
import Sketcher  # noqa: F401 - registers Sketcher objects
from PySide import QtCore, QtGui


class TestPartDesignTaskLifecycle(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("PartDesignTaskLifecycle")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()

    def tearDown(self):
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()
        Gui.Selection.clearSelection()
        if App.getDocument("PartDesignTaskLifecycle") is not None:
            App.closeDocument("PartDesignTaskLifecycle")
        self._process_events()

    @staticmethod
    def _process_events():
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()

    def _profile(self, body, name, *, closed=True):
        sketch = body.newObject("Sketcher::SketchObject", name)
        if closed:
            sketch.addGeometry(
                Part.Circle(
                    App.Vector(0, 0, 0),
                    App.Vector(0, 0, 1),
                    1.0,
                ),
                False,
            )
        else:
            sketch.addGeometry(
                Part.LineSegment(
                    App.Vector(-1, 0, 0),
                    App.Vector(1, 0, 0),
                ),
                False,
            )
        return sketch

    def _external_path(self, name, *, x=0.0):
        sketch = self.document.addObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(x, 0, 0),
                App.Vector(x, 10, 0),
            ),
            False,
        )
        sketch.Placement = App.Placement(
            App.Vector(),
            App.Rotation(App.Vector(1, 0, 0), 90),
        )
        return sketch

    def _pipe_with_external_paths(self, *, valid_profile):
        body = self.document.addObject("PartDesign::Body", "Body")
        Gui.activeView().setActiveObject("pdbody", body)
        profile = self._profile(body, "Profile", closed=valid_profile)
        spine = self._external_path("ExternalSpine")
        auxiliary = self._external_path("ExternalAuxiliary", x=2.0)
        pipe = body.newObject("PartDesign::AdditivePipe", "Pipe")
        pipe.Profile = profile
        pipe.Spine = spine
        pipe.AuxiliarySpine = auxiliary
        pipe.Mode = "Auxiliary"
        body.Tip = pipe
        self.document.recompute()
        return body, profile, spine, auxiliary, pipe

    def _begin_tree_edit(self, feature):
        gui_document = Gui.getDocument(self.document.Name)
        gui_document.openCommand(f"Edit {feature.Label}")
        self.assertTrue(gui_document.setEdit(feature.Name))
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())

    def _section_feature(self, kind):
        body = self.document.addObject(
            "PartDesign::Body",
            f"{kind}SectionBody",
        )
        Gui.activeView().setActiveObject("pdbody", body)
        profile = self._profile(body, f"{kind}Profile")
        sections = [
            self._profile(body, f"{kind}Section{index}")
            for index in range(3)
        ]
        for index, section in enumerate(sections, start=1):
            section.Placement.Base.z = 5.0 * index
            section.Label = f"{kind} section"

        if kind == "Loft":
            feature = body.newObject(
                "PartDesign::AdditiveLoft",
                "IdentityLoft",
            )
            feature.Profile = profile
        else:
            spine = self._external_path(f"{kind}Spine")
            feature = body.newObject(
                "PartDesign::AdditivePipe",
                "IdentityPipe",
            )
            feature.Profile = profile
            feature.Spine = spine

        feature.Sections = sections
        body.Tip = feature
        self.document.recompute()
        return body, feature, sections

    def _task_list(self, panel_name):
        for widget in Gui.getMainWindow().findChildren(
            QtGui.QListWidget,
            "listWidgetReferences",
        ):
            parent = widget
            while parent is not None:
                if parent.objectName() == panel_name:
                    return widget
                parent = parent.parentWidget()
        self.fail(f"Task panel {panel_name!r} has no section list")

    @staticmethod
    def _section_objects(feature):
        """Return the linked objects from PropertyLinkSubList's Python pairs."""

        return [
            section[0] if isinstance(section, tuple) else section
            for section in feature.Sections
        ]

    def _remove_action(self, widget):
        action = next(
            (
                candidate
                for candidate in widget.actions()
                if "remove" in candidate.text().lower()
            ),
            None,
        )
        self.assertIsNotNone(action, "Section list has no Remove action")
        return action

    def _accept_modal_dialogs(self, count):
        accepted = []
        attempts = [0]

        def accept_next():
            attempts[0] += 1
            if attempts[0] > 200:
                return
            modal = QtGui.QApplication.activeModalWidget()
            if modal is None:
                QtCore.QTimer.singleShot(10, accept_next)
                return
            accepted.append(modal.windowTitle())
            modal.accept()
            if len(accepted) < count:
                QtCore.QTimer.singleShot(10, accept_next)

        QtCore.QTimer.singleShot(0, accept_next)
        return accepted

    def test_pipe_cancel_restores_exact_input_and_result_visibility(self):
        body, profile, spine, auxiliary, pipe = (
            self._pipe_with_external_paths(valid_profile=True)
        )
        pipe.Visibility = False
        profile.Visibility = False
        spine.Visibility = True
        auxiliary.Visibility = False
        initial = {
            obj.Name: obj.Visibility
            for obj in (body, profile, spine, auxiliary, pipe)
        }

        self._begin_tree_edit(pipe)
        self.assertTrue(profile.Visibility)
        Gui.Control.activeTaskDialog().reject()
        self._process_events()

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(
            initial,
            {
                obj.Name: obj.Visibility
                for obj in (body, profile, spine, auxiliary, pipe)
            },
        )

    def test_pipe_imports_both_external_paths_before_committing(self):
        body, profile, spine, auxiliary, pipe = (
            self._pipe_with_external_paths(valid_profile=True)
        )
        self.assertTrue(pipe.isValid(), pipe.getStatusString())
        self.assertFalse(pipe.Shape.isNull())
        profile.Visibility = False
        original_objects = tuple(self.document.Objects)

        self._begin_tree_edit(pipe)
        handled = self._accept_modal_dialogs(1)
        Gui.Control.activeTaskDialog().accept()
        self._process_events()

        self.assertEqual(1, len(handled), "External-reference dialog was not handled")
        self.assertFalse(Gui.Control.activeDialog())
        self.assertIs(body.Tip, pipe)
        self.assertTrue(pipe.isValid(), pipe.getStatusString())
        self.assertFalse(pipe.Shape.isNull())

        imported_spine = pipe.Spine[0]
        imported_auxiliary = pipe.AuxiliarySpine[0]
        self.assertIsNot(imported_spine, spine)
        self.assertIsNot(imported_auxiliary, auxiliary)
        self.assertIsNot(imported_spine, imported_auxiliary)
        self.assertIn(imported_spine, body.Group)
        self.assertIn(imported_auxiliary, body.Group)
        self.assertLess(body.Group.index(imported_spine), body.Group.index(pipe))
        self.assertLess(body.Group.index(imported_auxiliary), body.Group.index(pipe))
        self.assertFalse(imported_spine.Visibility)
        self.assertFalse(imported_auxiliary.Visibility)

        created = [
            obj for obj in self.document.Objects if obj not in original_objects
        ]
        self.assertCountEqual(
            [imported_spine, imported_auxiliary],
            created,
        )
        self.assertFalse(profile.Visibility)

    def test_failed_pipe_accept_restores_links_body_and_helpers(self):
        body, _profile, spine, auxiliary, pipe = (
            self._pipe_with_external_paths(valid_profile=False)
        )
        initial_objects = tuple(self.document.Objects)
        initial_group = tuple(body.Group)
        initial_tip = body.Tip
        initial_spine = pipe.Spine
        initial_auxiliary = pipe.AuxiliarySpine

        self._begin_tree_edit(pipe)
        handled = self._accept_modal_dialogs(2)
        Gui.Control.activeTaskDialog().accept()
        self._process_events()

        self.assertEqual(
            2,
            len(handled),
            "Reference and validation dialogs were not both handled",
        )
        self.assertTrue(
            Gui.Control.activeDialog(),
            "Invalid OK must leave the task open for correction",
        )
        self.assertEqual(initial_objects, tuple(self.document.Objects))
        self.assertEqual(initial_group, tuple(body.Group))
        self.assertIs(initial_tip, body.Tip)
        self.assertEqual(initial_spine, pipe.Spine)
        self.assertEqual(initial_auxiliary, pipe.AuxiliarySpine)
        self.assertIs(spine, pipe.Spine[0])
        self.assertIs(auxiliary, pipe.AuxiliarySpine[0])

    def test_loft_and_pipe_remove_without_a_row_is_a_noop(self):
        """Delete with no current row cannot remove a section by accident."""

        cases = (
            ("Loft", "PartDesignGui__TaskLoftParameters"),
            ("Pipe", "PartDesignGui__TaskPipeScaling"),
        )
        for kind, panel_name in cases:
            with self.subTest(kind=kind):
                _body, feature, sections = self._section_feature(kind)
                self._begin_tree_edit(feature)
                section_list = self._task_list(panel_name)
                self.assertEqual(section_list.count(), len(sections))
                section_list.clearSelection()
                section_list.setCurrentRow(-1)

                self._remove_action(section_list).trigger()
                self._process_events()

                self.assertTrue(Gui.Control.activeDialog())
                self.assertEqual(self._section_objects(feature), sections)
                self.assertEqual(section_list.count(), len(sections))
                Gui.Control.activeTaskDialog().reject()
                self._process_events()

    def test_loft_and_pipe_stale_row_never_targets_recreated_name(self):
        """A deleted input's row cannot resolve to a new object with its name."""

        cases = (
            ("Loft", "PartDesignGui__TaskLoftParameters"),
            ("Pipe", "PartDesignGui__TaskPipeScaling"),
        )
        for kind, panel_name in cases:
            with self.subTest(kind=kind):
                _body, feature, sections = self._section_feature(kind)
                stale = sections[0]
                stale_name = stale.Name
                stale_label = stale.Label
                self._begin_tree_edit(feature)
                section_list = self._task_list(panel_name)
                stale_identity = section_list.item(0).data(
                    QtCore.Qt.UserRole
                )

                self.document.removeObject(stale_name)
                self._process_events()
                self.assertEqual(section_list.count(), len(sections) - 1)
                replacement = self.document.addObject(
                    "Sketcher::SketchObject",
                    stale_name,
                )
                replacement.Label = stale_label
                replacement.addGeometry(
                    Part.Circle(
                        App.Vector(0, 0, 0),
                        App.Vector(0, 0, 1),
                        2.0,
                    ),
                    False,
                )
                self.document.recompute()
                self.assertEqual(replacement.Name, stale_name)

                # Model a delayed row event that still carries the deleted
                # object's identity after the live list has refreshed.
                delayed_row = QtGui.QListWidgetItem(stale_label)
                delayed_row.setData(QtCore.Qt.UserRole, stale_identity)
                section_list.insertItem(0, delayed_row)
                section_list.setCurrentRow(0)
                self._remove_action(section_list).trigger()
                self._process_events()

                self.assertTrue(Gui.Control.activeDialog())
                self.assertIs(
                    self.document.getObject(stale_name),
                    replacement,
                )
                self.assertNotIn(
                    replacement,
                    self._section_objects(feature),
                )
                self.assertEqual(section_list.count(), len(feature.Sections))
                Gui.Control.activeTaskDialog().reject()
                self._process_events()

    def test_loft_and_pipe_sections_and_labels_refresh_immediately(self):
        """Open section panels mirror live links and labels without reopening."""

        cases = (
            ("Loft", "PartDesignGui__TaskLoftParameters"),
            ("Pipe", "PartDesignGui__TaskPipeScaling"),
        )
        for kind, panel_name in cases:
            with self.subTest(kind=kind):
                _body, feature, sections = self._section_feature(kind)
                self._begin_tree_edit(feature)
                section_list = self._task_list(panel_name)

                feature.Sections = [sections[2], sections[0]]
                self._process_events()
                self.assertEqual(section_list.count(), 2)

                renamed = f"{kind} renamed section"
                sections[2].Visibility = False
                sections[2].Label = renamed
                self._process_events()
                self.assertIn(renamed, section_list.item(0).text())
                self.assertFalse(
                    sections[2].Visibility,
                    "Refreshing a row must not override user visibility",
                )

                sections[1].Visibility = False
                feature.Sections = [sections[1]]
                self._process_events()
                self.assertEqual(section_list.count(), 1)
                self.assertIn(sections[1].Label, section_list.item(0).text())
                self.assertFalse(
                    sections[1].Visibility,
                    "Replacing section rows must not reveal hidden inputs",
                )
                Gui.Control.activeTaskDialog().reject()
                self._process_events()

    def test_loft_and_pipe_reorder_uses_visible_section_identities(self):
        """Dragging rows applies their identities, never stale property indexes."""

        cases = (
            ("Loft", "PartDesignGui__TaskLoftParameters"),
            ("Pipe", "PartDesignGui__TaskPipeScaling"),
        )
        for kind, panel_name in cases:
            with self.subTest(kind=kind):
                _body, feature, sections = self._section_feature(kind)
                self._begin_tree_edit(feature)
                section_list = self._task_list(panel_name)
                self.assertEqual(section_list.count(), len(sections))

                # Simulate a UI event-queue delay: the displayed order has
                # changed but the property has not yet received rowsMoved.
                delayed = section_list.takeItem(2)
                section_list.insertItem(0, delayed)
                moved = section_list.model().moveRow(
                    QtCore.QModelIndex(),
                    1,
                    QtCore.QModelIndex(),
                    len(sections),
                )
                self.assertTrue(moved, "Section list rejected an internal move")
                self._process_events()

                self.assertEqual(
                    self._section_objects(feature),
                    [sections[2], sections[1], sections[0]],
                )
                Gui.Control.activeTaskDialog().reject()
                self._process_events()


if __name__ == "__main__":
    unittest.main()
