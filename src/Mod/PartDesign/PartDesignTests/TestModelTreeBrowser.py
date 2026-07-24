# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI integration tests for VibeCAD's type-organized model browser."""

import os
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
import Sketcher  # noqa: F401 - registers Sketcher document types
from PySide import QtCore, QtGui


BROWSER_FOLDER_TYPE = 1002
TREE_PARAMETER_PATH = "User parameter:BaseApp/Preferences/TreeView"


def _tag_scripted_object(obj, *, role, model_id, output_key=""):
    values = {
        "VibeCADScriptedRole": role,
        "VibeCADScriptedEngine": "vibescript:partdesign",
        "VibeCADScriptedModelId": model_id,
        "VibeCADScriptedOutputKey": output_key,
    }
    for name, value in values.items():
        obj.addProperty("App::PropertyString", name, "VibeCAD Publication")
        setattr(obj, name, value)


def _visible_children(item):
    return [
        item.child(index)
        for index in range(item.childCount())
        if not item.child(index).isHidden()
    ]


def _visible_walk(item):
    if item.isHidden():
        return
    yield item
    for child in _visible_children(item):
        yield from _visible_walk(child)


def _child(item, label, item_type=None):
    if item is None:
        return None
    for child in _visible_children(item):
        if child.text(0) == label and (
            item_type is None or child.type() == item_type
        ):
            return child
    return None


def _snapshot(item):
    return (
        item.text(0),
        item.type(),
        tuple(_snapshot(child) for child in _visible_children(item)),
    )


def _snapshot_has_path(snapshot, labels):
    if snapshot is None or snapshot[0] != labels[0]:
        return False
    if len(labels) == 1:
        return True
    return any(
        _snapshot_has_path(child, labels[1:]) for child in snapshot[2]
    )


def _event_step(milliseconds=10):
    Gui.updateGui()
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _wait_until(predicate, timeout_ms=10000):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < timeout_ms:
        _event_step()
        try:
            result = predicate()
        except RuntimeError:
            # Tree refreshes replace Python wrappers for presentation items.
            result = None
        if result:
            return result
    return None


def _press_space(widget):
    for event_type in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
        event = QtGui.QKeyEvent(
            event_type,
            QtCore.Qt.Key_Space,
            QtCore.Qt.NoModifier,
        )
        QtGui.QApplication.sendEvent(widget, event)


class TestModelTreeBrowser(unittest.TestCase):
    """Verify type folders without changing the underlying document graph."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")

        self.tree_parameters = App.ParamGet(TREE_PARAMETER_PATH)
        self.previous_browser_preference = self.tree_parameters.GetBool(
            "OrganizeModelByType", True
        )
        self.tree_parameters.SetBool("OrganizeModelByType", True)

        self.document = App.newDocument("ModelTreeBrowser")
        self.document.Label = "Model Browser Test"
        Gui.activateView("Gui::View3DInventor", True)

        self.component = self.document.addObject("App::Part", "BrowserComponent")
        self.component.Label = "Browser Component"

        self.parameters = self.document.addObject("App::VarSet", "DesignParameters")
        self.parameters.Label = "Design Parameters"
        self.component.addObject(self.parameters)

        self.sketch_body = self.document.addObject(
            "PartDesign::Body", "SketchBody"
        )
        self.sketch_body.Label = "Sketch Body"
        self.component.addObject(self.sketch_body)
        self.profile_alpha = self.document.addObject(
            "Sketcher::SketchObject", "ProfileAlpha"
        )
        self.profile_alpha.Label = "Profile Alpha"
        self.sketch_body.addObject(self.profile_alpha)
        self.profile_beta = self.document.addObject(
            "Sketcher::SketchObject", "ProfileBeta"
        )
        self.profile_beta.Label = "Profile Beta"
        self.sketch_body.addObject(self.profile_beta)

        self.feature_body = self.document.addObject(
            "PartDesign::Body", "FeatureBody"
        )
        self.feature_body.Label = "Feature Body"
        self.component.addObject(self.feature_body)
        self.feature = self.feature_body.newObject(
            "PartDesign::AdditiveBox", "ExtrudeFeature"
        )
        self.feature.Label = "Extrude Feature"
        self.feature.Length = 3
        self.feature.Width = 4
        self.feature.Height = 5
        self.feature_body.Origin.OriginFeatures[0].Label = "Fixture Axis 42"

        self.source = self.document.addObject(
            "Part::Feature", "ImplementationSource"
        )
        self.source.Label = "Implementation Source"
        self.source.Shape = Part.makeBox(2, 2, 2)
        self.component.addObject(self.source)

        self.output = self.document.addObject("App::Link", "PublishedOutput")
        self.output.Label = "Published Output"
        self.output.LinkedObject = self.source

        # A scripted solid intentionally has two persistent representations:
        # an editable native Body and a stable publication link for downstream
        # references. The model browser must present that pair only once.
        scripted_model_id = "browser-body-backed-publication"
        self.vibe_component = self.document.addObject(
            "App::Part", "VibeProgram"
        )
        self.vibe_component.Label = "Vibe Program"
        _tag_scripted_object(
            self.vibe_component,
            role="model",
            model_id=scripted_model_id,
        )
        self.vibe_body = self.document.addObject(
            "PartDesign::Body", "VibeCandidateBody"
        )
        self.vibe_body.Label = "Utility Blade 38755A29"
        self.vibe_component.addObject(self.vibe_body)
        _tag_scripted_object(
            self.vibe_body,
            role="implementation",
            model_id=scripted_model_id,
            output_key="UtilityBlade",
        )
        self.vibe_result = self.vibe_body.newObject(
            "PartDesign::Feature",
            "VibePD_AdoptedResult_1",
        )
        # Reproduce an already-saved document from before adopted results kept
        # their source-operation label. Duplicate-label protection turns this
        # into the confusing "...001" label seen in the reported document.
        self.vibe_result.Label = self.vibe_body.Label
        self.vibe_result.Shape = Part.makeBox(6, 2, 1)
        self.vibe_body.Tip = self.vibe_result

        self.vibe_target = self.document.addObject(
            "Part::Feature", "VibeUtilityBladeSource"
        )
        self.vibe_target.Shape = self.vibe_result.Shape
        self.vibe_component.addObject(self.vibe_target)
        self.vibe_target.Visibility = False
        _tag_scripted_object(
            self.vibe_target,
            role="publication_target",
            model_id=scripted_model_id,
            output_key="UtilityBlade",
        )
        self.vibe_output = self.document.addObject(
            "App::Link", "VibeUtilityBlade"
        )
        self.vibe_output.Label = self.vibe_body.Label
        self.vibe_output.LinkedObject = (
            self.vibe_component,
            f"{self.vibe_target.Name}.",
        )
        self.vibe_output.LinkTransform = True
        _tag_scripted_object(
            self.vibe_output,
            role="publication",
            model_id=scripted_model_id,
            output_key="UtilityBlade",
        )

        self.standalone = self.document.addObject(
            "Part::Feature", "StandaloneGeometry"
        )
        self.standalone.Label = "Standalone Geometry"
        self.standalone.Shape = Part.makeCylinder(1, 2)

        self.notes_group = self.document.addObject(
            "App::DocumentObjectGroup", "NotesGroup"
        )
        self.notes_group.Label = "Notes Group"
        self.note = self.document.addObject("App::FeaturePython", "DesignNote")
        self.note.Label = "Design Note"
        self.notes_group.addObject(self.note)
        self.grouped_sketch = self.document.addObject(
            "Sketcher::SketchObject", "GroupedLayout"
        )
        self.grouped_sketch.Label = "Grouped Layout"
        self.notes_group.addObject(self.grouped_sketch)

        self.document.recompute()
        self.expected_object_names = tuple(
            obj.Name for obj in self.document.Objects
        )
        self.expected_component_group = tuple(
            obj.Name for obj in self.component.Group
        )
        self.expected_sketch_group = tuple(
            obj.Name for obj in self.sketch_body.Group
        )
        self.expected_feature_group = tuple(
            obj.Name for obj in self.feature_body.Group
        )
        self.expected_notes_group = tuple(
            obj.Name for obj in self.notes_group.Group
        )
        self.expected_vibe_component_group = tuple(
            obj.Name for obj in self.vibe_component.Group
        )
        self.expected_vibe_body_group = tuple(
            obj.Name for obj in self.vibe_body.Group
        )

        self.assertIsNotNone(
            _wait_until(self._browser_ready),
            "Type-organized model browser did not become ready",
        )

    def tearDown(self):
        Gui.Selection.clearSelection()
        if (
            getattr(self, "document", None) is not None
            and App.getDocument(self.document.Name) is not None
        ):
            App.closeDocument(self.document.Name)
        self.tree_parameters.SetBool(
            "OrganizeModelByType", self.previous_browser_preference
        )
        _event_step()

    def _tree_and_document_item(self):
        main_window = Gui.getMainWindow()
        for tree in main_window.findChildren(QtGui.QTreeWidget):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                if not item.isHidden() and item.text(0) == self.document.Label:
                    return tree, item
        return None, None

    def _browser_ready(self):
        tree, document_item = self._tree_and_document_item()
        if tree is None:
            return None
        component_item = _child(document_item, "Browser Component")
        if component_item is None:
            return None
        if _child(component_item, "Bodies", BROWSER_FOLDER_TYPE) is None:
            return None
        return tree, document_item

    def _browser_items(self):
        ready = _wait_until(self._browser_ready)
        self.assertIsNotNone(ready)
        return ready

    def _browser_snapshot(self):
        return _wait_until(self._browser_snapshot_now)

    def _browser_snapshot_now(self):
        _tree, document_item = self._tree_and_document_item()
        return _snapshot(document_item) if document_item is not None else None

    def _component_item(self):
        component_item = _wait_until(self._component_item_now)
        self.assertIsNotNone(component_item)
        return component_item

    def _component_item_now(self):
        _tree, document_item = self._tree_and_document_item()
        return (
            _child(document_item, "Browser Component")
            if document_item is not None
            else None
        )

    def _assert_document_unchanged(self):
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            self.expected_object_names,
        )
        self.assertEqual(
            tuple(obj.Name for obj in self.component.Group),
            self.expected_component_group,
        )
        self.assertEqual(
            tuple(obj.Name for obj in self.sketch_body.Group),
            self.expected_sketch_group,
        )
        self.assertEqual(
            tuple(obj.Name for obj in self.feature_body.Group),
            self.expected_feature_group,
        )
        self.assertEqual(
            tuple(obj.Name for obj in self.notes_group.Group),
            self.expected_notes_group,
        )
        self.assertEqual(
            tuple(obj.Name for obj in self.vibe_component.Group),
            self.expected_vibe_component_group,
        )
        self.assertEqual(
            tuple(obj.Name for obj in self.vibe_body.Group),
            self.expected_vibe_body_group,
        )

    def test_browser_groups_by_type_with_one_visible_item_per_object(self):
        tree, document_item = self._browser_items()
        component_item = self._component_item()

        component_labels = {
            item.text(0) for item in _visible_children(component_item)
        }
        self.assertTrue(
            {"Origin", "Parameters", "Bodies", "Sketches", "Geometry"}.issubset(
                component_labels
            ),
            component_labels,
        )

        parameters_folder = _child(
            component_item, "Parameters", BROWSER_FOLDER_TYPE
        )
        self.assertEqual(
            [item.text(0) for item in _visible_children(parameters_folder)],
            ["Design Parameters"],
        )

        bodies_folder = _child(component_item, "Bodies", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            {item.text(0) for item in _visible_children(bodies_folder)},
            {"Sketch Body", "Feature Body"},
        )

        sketch_body_item = _child(bodies_folder, "Sketch Body")
        sketch_body_labels = {
            item.text(0) for item in _visible_children(sketch_body_item)
        }
        self.assertIn("Origin", sketch_body_labels)
        self.assertNotIn("Profile Alpha", sketch_body_labels)
        self.assertNotIn("Profile Beta", sketch_body_labels)

        feature_body_item = _child(bodies_folder, "Feature Body")
        features_folder = _child(
            feature_body_item, "Features", BROWSER_FOLDER_TYPE
        )
        self.assertIsNotNone(features_folder)
        self.assertEqual(
            [item.text(0) for item in _visible_children(features_folder)],
            ["Extrude Feature"],
        )

        sketches_folder = _child(
            component_item, "Sketches", BROWSER_FOLDER_TYPE
        )
        self.assertEqual(
            [item.text(0) for item in _visible_children(sketches_folder)],
            ["Profile Alpha", "Profile Beta"],
        )

        root_sketches = _child(
            document_item, "Sketches", BROWSER_FOLDER_TYPE
        )
        self.assertEqual(
            [item.text(0) for item in _visible_children(root_sketches)],
            ["Grouped Layout"],
        )

        feature_origin = _child(feature_body_item, "Origin")
        origin_labels = {
            item.text(0) for item in _visible_children(feature_origin)
        }
        self.assertIn("Fixture Axis 42", origin_labels)
        self.assertTrue(
            {
                "Y-axis",
                "Z-axis",
                "XY-plane",
                "XZ-plane",
                "YZ-plane",
                "Origin-Point",
            }.issubset(origin_labels),
            origin_labels,
        )

        component_geometry = _child(
            component_item, "Geometry", BROWSER_FOLDER_TYPE
        )
        self.assertEqual(
            [item.text(0) for item in _visible_children(component_geometry)],
            ["Published Output"],
        )
        self.assertTrue(self.source.ViewObject.ShowInTree)

        self.document.ShowHidden = True
        _event_step()
        component_geometry = _child(
            self._component_item(), "Geometry", BROWSER_FOLDER_TYPE
        )
        self.assertEqual(
            {item.text(0) for item in _visible_children(component_geometry)},
            {"Implementation Source", "Published Output"},
        )
        self.document.ShowHidden = False
        _event_step()

        root_geometry = _child(document_item, "Geometry", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            [item.text(0) for item in _visible_children(root_geometry)],
            ["Standalone Geometry"],
        )
        root_groups = _child(document_item, "Groups", BROWSER_FOLDER_TYPE)
        notes_item = _child(root_groups, "Notes Group")
        self.assertEqual(
            [item.text(0) for item in _visible_children(notes_item)],
            ["Design Note"],
        )

        root_labels = {item.text(0) for item in _visible_children(document_item)}
        axis_labels = {
            feature.Label
            for body in (self.sketch_body, self.feature_body)
            for feature in body.Origin.OriginFeatures
        }
        self.assertEqual(root_labels & axis_labels, set())

        all_visible = list(_visible_walk(document_item))
        for unique_label in (
            "Browser Component",
            "Design Parameters",
            "Sketch Body",
            "Profile Alpha",
            "Profile Beta",
            "Grouped Layout",
            "Feature Body",
            "Extrude Feature",
            "Published Output",
            "Standalone Geometry",
            "Notes Group",
            "Design Note",
        ):
            self.assertEqual(
                sum(item.text(0) == unique_label for item in all_visible),
                1,
                unique_label,
            )

        for item in all_visible:
            if item.type() == BROWSER_FOLDER_TYPE:
                self.assertFalse(item.icon(0).isNull(), item.text(0))
                self.assertFalse(item.icon(0).pixmap(24, 24).isNull(), item.text(0))

        def expand(item):
            item.setExpanded(True)
            for child in _visible_children(item):
                expand(child)

        expand(document_item)
        _event_step()
        self._assert_document_unchanged()
        self.assertIs(tree, self._tree_and_document_item()[0])

    def test_body_backed_publication_uses_body_as_its_only_normal_tree_item(self):
        tree, document_item = self._browser_items()
        vibe_component = _child(document_item, "Vibe Program")
        self.assertIsNotNone(vibe_component)

        bodies = _child(vibe_component, "Bodies", BROWSER_FOLDER_TYPE)
        self.assertIsNotNone(bodies)
        self.assertEqual(
            [item.text(0) for item in _visible_children(bodies)],
            ["Utility Blade 38755A29"],
        )
        body_item = _child(bodies, "Utility Blade 38755A29")
        features = _child(body_item, "Features", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            [item.text(0) for item in _visible_children(features)],
            ["Result"],
        )

        # The implementation Body is the semantic solid in the normal browser.
        # Its stable carrier and private copied shape remain present in the
        # document but do not create a duplicate Geometry branch.
        self.assertIsNone(
            _child(vibe_component, "Geometry", BROWSER_FOLDER_TYPE)
        )
        visible_labels = {
            item.text(0) for item in _visible_walk(vibe_component)
        }
        self.assertNotIn(self.vibe_result.Label, visible_labels)
        self.assertNotIn(self.vibe_output.Label, visible_labels)
        self.assertIs(self.vibe_output.getLinkedObject(), self.vibe_target)

        # The Body row controls the complete visible output, including the
        # stable carrier that owns presentation and downstream identity.
        self.vibe_body.Visibility = True
        self.vibe_output.Visibility = True
        tree.clearSelection()
        tree.setCurrentItem(body_item)
        body_item.setSelected(True)
        tree.setFocus()
        _press_space(tree)
        self.assertIsNotNone(
            _wait_until(
                lambda: not self.vibe_body.Visibility
                and not self.vibe_output.Visibility
            )
        )
        _press_space(tree)
        self.assertIsNotNone(
            _wait_until(
                lambda: self.vibe_body.Visibility
                and self.vibe_output.Visibility
            )
        )

        self.document.ShowHidden = True

        def hidden_geometry_visible():
            component = _child(
                self._tree_and_document_item()[1],
                "Vibe Program",
            )
            geometry = _child(component, "Geometry", BROWSER_FOLDER_TYPE)
            return geometry if geometry is not None else None

        hidden_geometry = _wait_until(hidden_geometry_visible)
        self.assertIsNotNone(hidden_geometry)
        self.assertEqual(
            {
                item.text(0)
                for item in _visible_children(hidden_geometry)
            },
            {self.vibe_target.Label, self.vibe_output.Label},
        )
        self.document.ShowHidden = False
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    _child(
                        _child(
                            self._tree_and_document_item()[1],
                            "Vibe Program",
                        ),
                        "Geometry",
                        BROWSER_FOLDER_TYPE,
                    )
                    is None
                )
            )
        )
        self._assert_document_unchanged()

    def test_late_publication_metadata_refreshes_live_tree_to_body(self):
        component = self.document.addObject("App::Part", "LateVibeProgram")
        component.Label = "Late Vibe Program"

        source = self.document.addObject("Part::Feature", "LateSource")
        source.Label = "Late Source"
        source.Shape = Part.makeBox(4, 3, 2)
        component.addObject(source)

        body = self.document.addObject("PartDesign::Body", "LateBody")
        body.Label = "Late Body"
        component.addObject(body)
        result = body.newObject("PartDesign::Feature", "LateResult")
        result.Label = "Late Result"
        result.Shape = source.Shape
        body.Tip = result

        publication = self.document.addObject("App::Link", "LatePublication")
        publication.Label = "Late Published Solid"
        publication.LinkedObject = (component, f"{source.Name}.")
        publication.LinkTransform = True
        self.document.recompute()

        def unpaired_tree():
            _tree, document_item = self._tree_and_document_item()
            component_item = _child(document_item, component.Label)
            geometry = _child(
                component_item, "Geometry", BROWSER_FOLDER_TYPE
            )
            return (
                component_item
                if geometry is not None
                and _child(geometry, publication.Label) is not None
                else None
            )

        self.assertIsNotNone(
            _wait_until(unpaired_tree),
            self._browser_snapshot(),
        )

        model_id = "late-publication-metadata"
        _tag_scripted_object(
            body,
            role="implementation",
            model_id=model_id,
            output_key="Result",
        )
        _tag_scripted_object(
            publication,
            role="publication",
            model_id=model_id,
            output_key="Result",
        )

        def paired_tree():
            _tree, document_item = self._tree_and_document_item()
            component_item = _child(document_item, component.Label)
            bodies = _child(component_item, "Bodies", BROWSER_FOLDER_TYPE)
            geometry = _child(
                component_item, "Geometry", BROWSER_FOLDER_TYPE
            )
            return (
                component_item
                if bodies is not None
                and _child(bodies, body.Label) is not None
                and geometry is None
                else None
            )

        self.assertIsNotNone(
            _wait_until(paired_tree),
            self._browser_snapshot(),
        )

    def test_sketch_folder_toggles_all_sketch_visibility(self):
        tree, document_item = self._browser_items()
        sketches_folder = _child(
            self._component_item(), "Sketches", BROWSER_FOLDER_TYPE
        )
        self.profile_alpha.Visibility = True
        self.profile_beta.Visibility = True
        _event_step()

        tree.clearSelection()
        tree.setCurrentItem(sketches_folder)
        sketches_folder.setSelected(True)
        tree.setFocus()
        _press_space(tree)
        self.assertIsNotNone(
            _wait_until(
                lambda: not self.profile_alpha.Visibility
                and not self.profile_beta.Visibility
            )
        )

        _press_space(tree)
        self.assertIsNotNone(
            _wait_until(
                lambda: self.profile_alpha.Visibility
                and self.profile_beta.Visibility
            )
        )

        root_sketches = _child(
            document_item, "Sketches", BROWSER_FOLDER_TYPE
        )
        self.grouped_sketch.Visibility = True
        tree.clearSelection()
        tree.setCurrentItem(root_sketches)
        root_sketches.setSelected(True)
        _press_space(tree)
        self.assertIsNotNone(
            _wait_until(lambda: not self.grouped_sketch.Visibility)
        )
        _press_space(tree)
        self.assertIsNotNone(
            _wait_until(lambda: self.grouped_sketch.Visibility)
        )
        self._assert_document_unchanged()

    def test_selection_uses_logical_ownership_not_virtual_folders(self):
        tree, _document_item = self._browser_items()

        output_item = _child(
            _child(self._component_item(), "Geometry", BROWSER_FOLDER_TYPE),
            "Published Output",
        )
        Gui.Selection.clearSelection()
        tree.clearSelection()
        tree.setCurrentItem(output_item)
        output_item.setSelected(True)
        self.assertIsNotNone(
            _wait_until(
                lambda: Gui.Selection.getSelection(self.document.Name)
                == [self.output]
            )
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            self.document.Name,
            self.sketch_body.Name,
            f"{self.profile_alpha.Name}.",
        )

        def selected_profile():
            selected = [
                item
                for item in tree.selectedItems()
                if not item.isHidden() and item.text(0) == "Profile Alpha"
            ]
            return selected[0] if len(selected) == 1 else None

        self.assertIsNotNone(_wait_until(selected_profile))
        self.assertEqual(
            [
                item.text(0)
                for item in tree.selectedItems()
                if not item.isHidden()
            ],
            ["Profile Alpha"],
        )

    def test_select_all_from_type_folder_selects_its_objects(self):
        tree, _document_item = self._browser_items()
        sketches_folder = _child(
            self._component_item(), "Sketches", BROWSER_FOLDER_TYPE
        )
        Gui.Selection.clearSelection()
        tree.clearSelection()
        tree.setCurrentItem(sketches_folder)
        sketches_folder.setSelected(True)
        tree.selectAll()

        self.assertIsNotNone(
            _wait_until(
                lambda: set(Gui.Selection.getSelection(self.document.Name))
                == {self.profile_alpha, self.profile_beta}
            )
        )

    def test_undo_redo_reclassifies_new_sketch(self):
        self.document.openTransaction("Add browser sketch")
        third = self.document.addObject("Sketcher::SketchObject", "ProfileGamma")
        third.Label = "Profile Gamma"
        self.sketch_body.addObject(third)
        self.document.commitTransaction()

        gamma_path = (
            self.document.Label,
            "Browser Component",
            "Sketches",
            "Profile Gamma",
        )

        def gamma_present():
            snapshot = self._browser_snapshot_now()
            return snapshot if _snapshot_has_path(snapshot, gamma_path) else None

        added = _wait_until(gamma_present)
        self.assertIsNotNone(
            added,
            (
                [obj.Name for obj in self.sketch_body.Group],
                third.getParentGeoFeatureGroup(),
                self._browser_snapshot(),
            ),
        )
        self.document.undo()

        def gamma_absent():
            snapshot = self._browser_snapshot_now()
            return (
                snapshot
                if snapshot is not None
                and not _snapshot_has_path(snapshot, gamma_path)
                else None
            )

        self.assertIsNotNone(_wait_until(gamma_absent))
        self.document.redo()
        self.assertIsNotNone(_wait_until(gamma_present))

    def test_saved_document_reopens_into_same_projection_without_migration(self):
        expected_types = {
            obj.Name: obj.TypeId for obj in self.document.Objects
        }
        expected_component_group = tuple(
            obj.Name for obj in self.component.Group
        )

        with tempfile.TemporaryDirectory(
            prefix="vibecad_model_browser_"
        ) as temporary_directory:
            path = os.path.join(temporary_directory, "browser.FCStd")
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)

            self.assertIsNotNone(_wait_until(self._browser_ready))
            self.component = self.document.getObject("BrowserComponent")
            self.sketch_body = self.document.getObject("SketchBody")
            self.feature_body = self.document.getObject("FeatureBody")
            self.vibe_component = self.document.getObject("VibeProgram")
            self.vibe_body = self.document.getObject("VibeCandidateBody")
            self.assertEqual(
                {obj.Name: obj.TypeId for obj in self.document.Objects},
                expected_types,
            )
            self.assertEqual(
                tuple(obj.Name for obj in self.component.Group),
                expected_component_group,
            )
            self.assertEqual(
                self.document.getObject("PublishedOutput").LinkedObject,
                self.document.getObject("ImplementationSource"),
            )

            def categories_ready():
                snapshot = self._browser_snapshot_now()
                has_sketches = _snapshot_has_path(
                    snapshot,
                    (self.document.Label, "Browser Component", "Sketches"),
                )
                has_bodies = _snapshot_has_path(
                    snapshot,
                    (self.document.Label, "Browser Component", "Bodies"),
                )
                return snapshot if has_sketches and has_bodies else None

            self.assertIsNotNone(
                _wait_until(categories_ready),
                (
                    self.document.getObject(
                        "ProfileAlpha"
                    ).getParentGeoFeatureGroup(),
                    [obj.Name for obj in self.sketch_body.Group],
                    self._browser_snapshot(),
                ),
            )
            snapshot = self._browser_snapshot()
            self.assertTrue(
                _snapshot_has_path(
                    snapshot,
                    (
                        self.document.Label,
                        "Vibe Program",
                        "Bodies",
                        "Utility Blade 38755A29",
                        "Features",
                        "Result",
                    ),
                ),
                snapshot,
            )
            self.assertFalse(
                _snapshot_has_path(
                    snapshot,
                    (self.document.Label, "Vibe Program", "Geometry"),
                ),
                snapshot,
            )

    def test_legacy_dependency_tree_remains_available_as_fallback(self):
        self.tree_parameters.SetBool("OrganizeModelByType", False)

        def legacy_ready():
            _tree, document_item = self._tree_and_document_item()
            if document_item is None:
                return None
            visible_items = list(_visible_walk(document_item))
            if any(item.type() == BROWSER_FOLDER_TYPE for item in visible_items):
                return None
            return document_item if any(
                item.text(0) == "Browser Component" for item in visible_items
            ) else None

        self.assertIsNotNone(_wait_until(legacy_ready))

        self.tree_parameters.SetBool("OrganizeModelByType", True)
        self.assertIsNotNone(_wait_until(self._browser_ready))
