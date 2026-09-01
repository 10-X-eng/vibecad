# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI regressions for VibeCAD's simplified model browser and Body renderer."""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
import Sketcher  # noqa: F401 - registers Sketcher document types
from PySide import QtCore, QtGui
from pivy import coin

try:
    import Fem  # noqa: F401 - registers optional FEM document types
except ImportError:
    Fem = None

try:
    import TechDraw  # noqa: F401 - registers optional TechDraw document types
    import TechDrawGui  # noqa: F401 - registers TechDraw view providers
except ImportError:
    TechDraw = None

try:
    import Mesh
except ImportError:
    Mesh = None


BROWSER_FOLDER_TYPE = 1002
BROWSER_DETAIL_TYPE = 1003
TREE_PARAMETER_PATH = "User parameter:BaseApp/Preferences/TreeView"
VIBESCRIPT_HISTORY_LABEL = "VibeScript Build"


def _tag_scripted_object(obj, *, role, model_id, output_key=""):
    values = {
        "VibeCADScriptedRole": role,
        "VibeCADScriptedEngine": "vibescript:partdesign",
        "VibeCADScriptedModelId": model_id,
        "VibeCADScriptedOutputKey": output_key,
        "VibeCADPublishedRevision": "accepted",
    }
    for name, value in values.items():
        if name not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyString",
                name,
                "VibeCAD Publication",
            )
        setattr(obj, name, value)


def _tag_timeline_role(obj, role):
    if "VibeCADTimelineRole" not in obj.PropertiesList:
        obj.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "VibeCAD History",
        )
    obj.VibeCADTimelineRole = role


def _visible_children(item):
    return [
        item.child(index)
        for index in range(item.childCount())
        if not item.child(index).isHidden()
    ]


def _visible_walk(item):
    if item is None or item.isHidden():
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


def _snapshot_has_label(snapshot, label):
    return snapshot is not None and (
        snapshot[0] == label
        or any(_snapshot_has_label(child, label) for child in snapshot[2])
    )


def _snapshot_child(snapshot, label, item_type=None):
    if snapshot is None:
        return None
    for child in snapshot[2]:
        if child[0] == label and (
            item_type is None or child[1] == item_type
        ):
            return child
    return None


def _snapshot_labels(snapshot):
    if snapshot is None:
        return ()
    return (snapshot[0],) + tuple(
        label
        for child in snapshot[2]
        for label in _snapshot_labels(child)
    )


def _icon_png(icon, size=16):
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    if not buffer.open(QtCore.QIODevice.WriteOnly):
        raise RuntimeError("Could not open the icon comparison buffer")
    if not icon.pixmap(size, size).save(buffer, "PNG"):
        raise RuntimeError("Could not serialize the icon for comparison")
    buffer.close()
    return bytes(data)


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
            # Browser rebuilds replace the wrapped QTreeWidgetItems.
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


def _primitive_counts(obj):
    counts = coin.SoGetPrimitiveCountAction()
    counts.setCanApproximate(True)
    counts.apply(obj.ViewObject.RootNode)
    return counts.getTriangleCount(), counts.getLineCount()


def _is_in_active_scene(obj):
    """Return whether the object's view-provider root is mounted in the view."""
    gui_document = Gui.activeDocument()
    if obj is None or gui_document is None:
        return False
    search = coin.SoSearchAction()
    search.setNode(obj.ViewObject.RootNode)
    search.setInterest(coin.SoSearchAction.FIRST)
    search.setSearchingAll(True)
    search.apply(gui_document.activeView().getSceneGraph())
    return search.getPath() is not None


def _rewrite_saved_visibility(path, values):
    """Write an intentionally stale pre-VibeCAD visibility state to an FCStd."""
    with zipfile.ZipFile(path, "r") as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]

    rewritten = []
    for info, payload in entries:
        if info.filename not in {"Document.xml", "GuiDocument.xml"}:
            rewritten.append((info, payload))
            continue

        root = ET.fromstring(payload)
        object_tag = "Object" if info.filename == "Document.xml" else "ViewProvider"
        for name, visible in values.items():
            owners = root.findall(f".//{object_tag}[@name='{name}']")
            if not owners:
                raise AssertionError(
                    f"{info.filename} has no saved object named {name}"
                )
            # Document.xml lists each identity once in <Objects> and again
            # with its serialized properties in <ObjectData>. Resolve the
            # property-bearing record explicitly instead of relying on the
            # first matching tag.
            values_found = [
                value
                for owner in owners
                if (
                    value := owner.find(
                        "./Properties/Property[@name='Visibility']/Bool"
                    )
                )
                is not None
            ]
            if not values_found:
                raise AssertionError(
                    f"{info.filename} has no Visibility value for {name}"
                )
            for value in values_found:
                value.set("value", "true" if visible else "false")
        rewritten.append((info, ET.tostring(root, encoding="utf-8")))

    with zipfile.ZipFile(path, "w") as archive:
        for info, payload in rewritten:
            archive.writestr(info, payload)


class TestModelTreeBrowser(unittest.TestCase):
    """The browser is simple; the native Body is the one rendered solid."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")

        self.tree_parameters = App.ParamGet(TREE_PARAMETER_PATH)
        self.previous_browser_preference = self.tree_parameters.GetBool(
            "OrganizeModelByType",
            True,
        )
        self.tree_parameters.SetBool("OrganizeModelByType", True)

        self.document = App.newDocument("ModelTreeBrowser")
        self.document.Label = "Model Browser Test"
        Gui.activateView("Gui::View3DInventor", True)

        self.component = self.document.addObject(
            "App::Part",
            "BrowserComponent",
        )
        self.component.Label = "Browser Component"

        self.design_inputs = self.document.addObject(
            "App::VarSet",
            "DesignInputs",
        )
        self.design_inputs.Label = "Design Inputs"
        self.component.addObject(self.design_inputs)

        self.design_notes = self.document.addObject(
            "App::DocumentObjectGroup",
            "DesignNotes",
        )
        self.design_notes.Label = "Design Notes"
        self.component.addObject(self.design_notes)
        self.manufacturing_note = self.document.addObject(
            "App::FeaturePython",
            "ManufacturingNote",
        )
        self.manufacturing_note.Label = "Manufacturing Note"
        self.design_notes.addObject(self.manufacturing_note)

        self.sketch_body = self.document.addObject(
            "PartDesign::Body",
            "SketchBody",
        )
        self.sketch_body.Label = "Sketch Body"
        self.component.addObject(self.sketch_body)
        self.profile_alpha = self.sketch_body.newObject(
            "Sketcher::SketchObject",
            "ProfileAlpha",
        )
        self.profile_alpha.Label = "Profile Alpha"
        self.profile_alpha.addGeometry(
            Part.LineSegment(
                App.Vector(0, 0, 0),
                App.Vector(5, 0, 0),
            ),
            False,
        )

        self.feature_body = self.document.addObject(
            "PartDesign::Body",
            "FeatureBody",
        )
        self.feature_body.Label = "Feature Body"
        self.component.addObject(self.feature_body)
        self.profile_beta = self.feature_body.newObject(
            "Sketcher::SketchObject",
            "ProfileBeta",
        )
        self.profile_beta.Label = "Profile Beta"
        self.profile_beta.addGeometry(
            Part.LineSegment(
                App.Vector(0, 0, 0),
                App.Vector(3, 0, 0),
            ),
            False,
        )
        self.feature = self.feature_body.newObject(
            "PartDesign::Feature",
            "ExtrudeFeature",
        )
        self.feature.Label = "Extrude Feature"
        self.feature.Shape = Part.makeBox(3, 4, 5)
        _tag_timeline_role(self.feature, "operation")
        self.feature_body.Tip = self.feature
        self.reference = self.feature_body.newObject(
            "PartDesign::SubShapeBinder",
            "BladeReference",
        )
        self.reference.Label = "Blade Reference"
        self.feature_body.Tip = self.feature

        # Loose Part geometry is legacy data. The organized browser must not
        # advertise it as a second class of rendered solid.
        self.loose_geometry = self.document.addObject(
            "Part::Feature",
            "LegacyLooseGeometry",
        )
        self.loose_geometry.Label = "Legacy Loose Geometry"
        self.loose_geometry.Shape = Part.makeCylinder(1, 2)

        self.release_metadata = self.document.addObject(
            "App::FeaturePython",
            "ReleaseMetadata",
        )
        self.release_metadata.Label = "Release Metadata"

        self.component_operation = self.document.addObject(
            "Part::Feature",
            "ComponentOperation",
        )
        self.component_operation.Label = "Component Operation"
        self.component_operation.Shape = Part.makeBox(1, 1, 1)
        _tag_timeline_role(self.component_operation, "operation")
        self.component.addObject(self.component_operation)

        self.root_operation = self.document.addObject(
            "Part::Feature",
            "RootOperation",
        )
        self.root_operation.Label = "Root Operation"
        self.root_operation.Shape = Part.makeBox(1, 2, 1)
        _tag_timeline_role(self.root_operation, "operation")

        self.internal_state = self.document.addObject(
            "Part::Feature",
            "InternalOperationState",
        )
        self.internal_state.Label = "Internal Operation State"
        self.internal_state.Shape = Part.makeBox(1, 1, 2)
        _tag_timeline_role(self.internal_state, "internal")

        model_id = "browser-body-backed-publication"
        self.vibe_model_id = model_id
        self.vibe_component = self.document.addObject(
            "App::Part",
            "VibeProgram",
        )
        self.vibe_component.Label = "Vibe Program"
        _tag_scripted_object(
            self.vibe_component,
            role="model",
            model_id=model_id,
        )
        self.vibe_body = self.document.addObject(
            "PartDesign::Body",
            "VibeCandidateBody",
        )
        self.vibe_body.Label = "Utility Blade 38755A29"
        self.vibe_component.addObject(self.vibe_body)
        _tag_scripted_object(
            self.vibe_body,
            role="implementation",
            model_id=model_id,
            output_key="UtilityBlade",
        )
        self.vibe_sketch = self.vibe_body.newObject(
            "Sketcher::SketchObject",
            "VibeBladeProfile",
        )
        self.vibe_sketch.Label = "Blade Profile"
        self.vibe_sketch.addGeometry(
            [
                Part.LineSegment(
                    App.Vector(0, 0, 0),
                    App.Vector(6, 0, 0),
                ),
                Part.LineSegment(
                    App.Vector(6, 0, 0),
                    App.Vector(6, 2, 0),
                ),
                Part.LineSegment(
                    App.Vector(6, 2, 0),
                    App.Vector(0, 2, 0),
                ),
                Part.LineSegment(
                    App.Vector(0, 2, 0),
                    App.Vector(0, 0, 0),
                ),
            ],
            False,
        )
        self.vibe_sketch.ViewObject.HideDependent = True
        self.vibe_prior_result = self.vibe_body.newObject(
            "PartDesign::Feature",
            "VibePriorResult",
        )
        self.vibe_prior_result.Label = "Rough Blade"
        self.vibe_prior_result.addProperty(
            "App::PropertyLink",
            "Profile",
        )
        self.vibe_prior_result.Profile = self.vibe_sketch
        self.vibe_prior_result.Shape = Part.makeBox(4, 2, 1)
        self.vibe_result = self.vibe_body.newObject(
            "PartDesign::Feature",
            "VibeResult",
        )
        self.vibe_result.Label = "Finished Blade"
        self.vibe_result.addProperty(
            "App::PropertyLink",
            "PreviousResult",
        )
        self.vibe_result.PreviousResult = self.vibe_prior_result
        self.vibe_result.Shape = Part.makeBox(6, 2, 1)
        self.vibe_body.Tip = self.vibe_result

        from VibeCADVibeScriptDomainPublication import (
            PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
            PROP_PARTDESIGN_HISTORY_PRESENTATION,
        )

        self.vibe_body.addProperty(
            "App::PropertyString",
            PROP_PARTDESIGN_HISTORY_PRESENTATION,
            "VibeCAD Publication",
        )
        setattr(
            self.vibe_body,
            PROP_PARTDESIGN_HISTORY_PRESENTATION,
            PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
        )

        self.vibe_output = self.document.addObject(
            "App::Link",
            "VibeUtilityBlade",
        )
        self.vibe_output.Label = self.vibe_body.Label
        self.vibe_output.LinkedObject = (
            self.vibe_component,
            f"{self.vibe_body.Name}.",
        )
        self.vibe_output.LinkTransform = True
        _tag_scripted_object(
            self.vibe_output,
            role="publication",
            model_id=model_id,
            output_key="UtilityBlade",
        )

        self.document.openTransaction("Publish browser VibeScript history")
        self.vibe_operation = self.document.addObject(
            "PartDesign::DesignScriptOperation",
            "VibeProgramOperation",
        )
        self.vibe_operation.Label = "Vibe Program Operation"
        edit = PartDesign.beginDesignOperationEdit(self.vibe_operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            self.vibe_component.Name,
            model_id,
            "accepted",
            [],
            [],
            [],
            [],
            ["UtilityBlade"],
            ["solid"],
        )
        self.assertEqual(PartDesign.finalizeDesignOperationEdit(edit), [])
        _tag_scripted_object(
            self.vibe_operation,
            role="implementation",
            model_id=model_id,
        )
        _tag_timeline_role(self.vibe_operation, "operation")
        for property_name, command_name in (
            ("VibeCADTimelineEditCommand", "VibeCAD_EditScriptedModel"),
            ("VibeCADTimelineDeleteCommand", "VibeCAD_DeleteScriptedModel"),
        ):
            if property_name not in self.vibe_operation.PropertiesList:
                self.vibe_operation.addProperty(
                    "App::PropertyString",
                    property_name,
                    "Timeline",
                )
            setattr(self.vibe_operation, property_name, command_name)
        self.document.commitTransaction()

        self.profile_alpha.Visibility = False
        self.profile_beta.Visibility = False
        self.reference.Visibility = False
        self.vibe_sketch.Visibility = False
        self.vibe_body.Visibility = True
        self.vibe_prior_result.Visibility = False
        self.vibe_result.Visibility = True
        self.vibe_output.Visibility = False
        self.document.UndoMode = True
        self.document.recompute()

        self.assertIsNotNone(
            _wait_until(self._browser_ready),
            "Simplified model browser did not become ready",
        )

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except Exception:
                pass
        if (
            getattr(self, "document", None) is not None
            and App.getDocument(self.document.Name) is not None
        ):
            App.closeDocument(self.document.Name)
        self.tree_parameters.SetBool(
            "OrganizeModelByType",
            self.previous_browser_preference,
        )
        _event_step()

    def _tree_and_document_item(self):
        for tree in Gui.getMainWindow().findChildren(QtGui.QTreeWidget):
            # A standard Tree View and the Combo View may both retain a model
            # tree.  Synthetic pointer gestures must target the tree a human
            # can actually interact with, not an equally populated hidden
            # instance.
            if not tree.isVisible() or not tree.viewport().isVisible():
                continue
            matches = [
                tree.topLevelItem(index)
                for index in range(tree.topLevelItemCount())
                if not tree.topLevelItem(index).isHidden()
                and tree.topLevelItem(index).text(0) == self.document.Label
            ]
            if len(matches) == 1:
                return tree, matches[0]
        return None, None

    def _browser_ready(self):
        tree, document_item = self._tree_and_document_item()
        component = _child(document_item, "Browser Component")
        bodies = _child(component, "Bodies", BROWSER_FOLDER_TYPE)
        sketches = _child(component, "Sketches", BROWSER_FOLDER_TYPE)
        references = _child(component, "References", BROWSER_FOLDER_TYPE)
        if (
            tree is None
            or bodies is None
            or sketches is None
            or references is None
        ):
            return None
        return tree, document_item

    def _snapshot(self):
        _tree, document_item = self._tree_and_document_item()
        return _snapshot(document_item) if document_item is not None else None

    def _component_item(self, label):
        _tree, document_item = self._tree_and_document_item()
        return _child(document_item, label)

    def _vibe_items(self):
        tree, document_item = self._tree_and_document_item()
        component = _child(document_item, "Vibe Program")
        bodies = _child(component, "Bodies", BROWSER_FOLDER_TYPE)
        sketches = _child(component, "Sketches", BROWSER_FOLDER_TYPE)
        body = _child(bodies, self.vibe_body.Label)
        sketch = _child(sketches, self.vibe_sketch.Label)
        return tree, component, bodies, body, sketch

    def _toggle_vibe_item(self, item_name):
        tree, document_item = self._tree_and_document_item()
        component = _child(document_item, "Vibe Program")
        folder = _child(
            component,
            "Bodies" if item_name == "body" else "Sketches",
            BROWSER_FOLDER_TYPE,
        )
        item = _child(
            folder,
            self.vibe_body.Label
            if item_name == "body"
            else self.vibe_sketch.Label,
        )
        self.assertIsNotNone(tree)
        self.assertIsNotNone(item)
        self._toggle_tree_item(tree, item)

    @staticmethod
    def _toggle_tree_item(tree, item):
        tree.clearSelection()
        tree.setCurrentItem(item)
        item.setSelected(True)
        tree.setFocus()
        _press_space(tree)

    def test_browser_uses_primary_categories_and_only_needed_fallbacks(self):
        _tree, document_item = self._tree_and_document_item()
        component = _child(document_item, "Browser Component")
        self.assertIsNotNone(component)

        self.assertEqual(
            {
                item.text(0)
                for item in _visible_children(component)
                if item.type() == BROWSER_FOLDER_TYPE
            },
            {
                "Parameters",
                "Bodies",
                "Sketches",
                "Design History",
                "References",
                "Groups",
            },
        )

        parameters = _child(component, "Parameters", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            [item.text(0) for item in _visible_children(parameters)],
            [self.design_inputs.Label],
        )

        bodies = _child(component, "Bodies", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            [item.text(0) for item in _visible_children(bodies)],
            ["Sketch Body", "Feature Body"],
        )
        sketch_body, feature_body = _visible_children(bodies)
        self.assertEqual(_visible_children(sketch_body), [])
        self.assertEqual(
            [item.text(0) for item in _visible_children(feature_body)],
            [self.feature.Label],
        )

        sketches = _child(component, "Sketches", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            [item.text(0) for item in _visible_children(sketches)],
            ["Profile Alpha", "Profile Beta"],
        )

        references = _child(component, "References", BROWSER_FOLDER_TYPE)
        reference_labels = {
            item.text(0) for item in _visible_children(references)
        }
        self.assertEqual(
            reference_labels,
            {
                "Origin",
                "Sketch Body Origin",
                "Feature Body Origin",
                "Blade Reference",
            },
        )

        groups = _child(component, "Groups", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            [item.text(0) for item in _visible_children(groups)],
            [self.design_notes.Label],
        )
        design_notes = _child(groups, self.design_notes.Label)
        self.assertEqual(
            [item.text(0) for item in _visible_children(design_notes)],
            [self.manufacturing_note.Label],
        )

        operations = _child(component, "Design History", BROWSER_FOLDER_TYPE)
        self.assertEqual(
            [item.text(0) for item in _visible_children(operations)],
            [self.component_operation.Label],
        )

        visible_labels = {item.text(0) for item in _visible_walk(document_item)}
        self.assertNotIn("Features", visible_labels)
        self.assertIn(self.feature.Label, visible_labels)
        self.assertNotIn(self.internal_state.Label, visible_labels)

        root_operations = _child(
            document_item,
            "Design History",
            BROWSER_FOLDER_TYPE,
        )
        self.assertEqual(
            [item.text(0) for item in _visible_children(root_operations)],
            [self.root_operation.Label],
        )

        # Real loose legacy geometry remains reachable, but no feature owned by
        # a Body is duplicated in this fallback.
        root_geometry = _child(
            document_item,
            "Geometry",
            BROWSER_FOLDER_TYPE,
        )
        self.assertIsNotNone(root_geometry)
        self.assertEqual(
            [item.text(0) for item in _visible_children(root_geometry)],
            [self.loose_geometry.Label],
        )

        # There are no loose reference objects, so no empty document-level
        # References folder is created.
        self.assertIsNone(
            _child(document_item, "References", BROWSER_FOLDER_TYPE)
        )
        self.assertIsNone(
            _child(document_item, "Parameters", BROWSER_FOLDER_TYPE)
        )

        root_other = _child(document_item, "Other", BROWSER_FOLDER_TYPE)
        self.assertIsNotNone(root_other)
        self.assertEqual(
            [item.text(0) for item in _visible_children(root_other)],
            [self.release_metadata.Label],
        )

        # A component that needs no fallback categories remains compact.
        vibe_component = _child(document_item, self.vibe_component.Label)
        self.assertEqual(
            {
                item.text(0)
                for item in _visible_children(vibe_component)
                if item.type() == BROWSER_FOLDER_TYPE
            },
            {
                "Bodies",
                "Design History",
                "Published Outputs",
                "Sketches",
                "References",
            },
        )

        for item in _visible_walk(document_item):
            if item.type() == BROWSER_FOLDER_TYPE:
                self.assertFalse(item.icon(0).isNull(), item.text(0))

    def test_vibescript_program_roles_are_clear_and_keep_exact_identity(self):
        def role_items():
            tree, document_item = self._tree_and_document_item()
            component = _child(document_item, self.vibe_component.Label)
            bodies = _child(component, "Bodies", BROWSER_FOLDER_TYPE)
            history = _child(component, "Design History", BROWSER_FOLDER_TYPE)
            published = _child(
                component,
                "Published Outputs",
                BROWSER_FOLDER_TYPE,
            )
            body = _child(bodies, self.vibe_body.Label)
            operation = _child(history, VIBESCRIPT_HISTORY_LABEL)
            output = _child(published, self.vibe_body.Label)
            values = (
                tree,
                document_item,
                component,
                bodies,
                history,
                published,
                body,
                operation,
                output,
            )
            return values if all(value is not None for value in values) else None

        observed = _wait_until(role_items)
        self.assertIsNotNone(observed, self._snapshot())
        (
            tree,
            _document_item,
            component,
            bodies,
            _history,
            _published,
            body,
            operation,
            output,
        ) = observed

        folder_labels = [
            item.text(0)
            for item in _visible_children(component)
            if item.type() == BROWSER_FOLDER_TYPE
        ]
        self.assertEqual(
            folder_labels[:3],
            ["Bodies", "Design History", "Published Outputs"],
        )
        self.assertTrue(bodies.isExpanded())
        self.assertEqual(operation.text(0), VIBESCRIPT_HISTORY_LABEL)
        self.assertEqual(
            [
                child.text(0)
                for child in _visible_children(operation)
                if child.type() == BROWSER_DETAIL_TYPE
            ],
            ["Produces UtilityBlade"],
        )
        self.assertEqual(
            self.vibe_operation.ViewObject.ToggleVisibility,
            "NoToggleVisibility",
        )
        self.assertNotEqual(body.icon(0).cacheKey(), output.icon(0).cacheKey())
        self.assertNotEqual(
            operation.icon(0).cacheKey(),
            body.icon(0).cacheKey(),
        )
        manual_component = self._component_item(self.component.Label)
        manual_bodies = _child(
            manual_component,
            "Bodies",
            BROWSER_FOLDER_TYPE,
        )
        manual_body = _child(manual_bodies, self.feature_body.Label)
        self.assertIsNotNone(manual_body)
        self.assertNotEqual(
            _icon_png(body.icon(0)),
            _icon_png(manual_body.icon(0)),
            "A generated Body must retain its VibeCAD provenance badge",
        )

        operation_visibility = self.vibe_operation.Visibility
        body_visibility = self.vibe_body.Visibility
        output_visibility = self.vibe_output.Visibility
        self._toggle_tree_item(tree, operation)
        self.assertEqual(self.vibe_operation.Visibility, operation_visibility)
        self.assertEqual(self.vibe_body.Visibility, body_visibility)
        self.assertEqual(self.vibe_output.Visibility, output_visibility)

        def select_role_item(index, expected):
            items = role_items()
            if items is None:
                return None
            current_tree = items[0]
            item = items[index]
            Gui.Selection.clearSelection()
            current_tree.clearSelection()
            current_tree.setCurrentItem(item)
            item.setSelected(True)
            current_tree.setFocus()
            # The model browser preserves native App::Part containment in the
            # raw subname selection. The public resolved selection must still
            # identify the exact semantic object represented by the row.
            return Gui.Selection.getSelection(self.document.Name) == [expected]

        for index, expected in (
            (6, self.vibe_body),
            (7, self.vibe_operation),
            (8, self.vibe_output),
        ):
            self.assertIsNotNone(
                _wait_until(lambda: select_role_item(index, expected)),
                self._snapshot(),
            )

        # The role label is presentation-only. Exact saved identities remain
        # available through the selected object's Property inspector.
        self.assertEqual(self.vibe_operation.Name, "VibeProgramOperation")
        self.assertEqual(self.vibe_operation.Label, "Vibe Program Operation")
        self.assertEqual(self.vibe_operation.ProgramId, self.vibe_model_id)

    def test_vibescript_multi_output_history_identifies_every_body(self):
        output_keys = ["UtilityBlade", "FixtureClamp", "FixturePin"]
        created_bodies = []
        created_publications = []

        self.document.openTransaction("Publish multiple VibeScript outputs")
        for index, output_key in enumerate(output_keys[1:], start=1):
            body = self.document.addObject(
                "PartDesign::Body",
                f"Vibe{output_key}Body",
            )
            body.Label = output_key
            self.vibe_component.addObject(body)
            _tag_scripted_object(
                body,
                role="implementation",
                model_id=self.vibe_model_id,
                output_key=output_key,
            )
            result = body.newObject(
                "PartDesign::Feature",
                f"Vibe{output_key}Result",
            )
            result.Shape = Part.makeBox(2 + index, 2, 1)
            body.Tip = result
            body.Visibility = True
            created_bodies.append(body)

            publication = self.document.addObject(
                "App::Link",
                f"Vibe{output_key}",
            )
            publication.Label = body.Label
            publication.LinkedObject = (
                self.vibe_component,
                f"{body.Name}.",
            )
            publication.LinkTransform = True
            publication.Visibility = False
            _tag_scripted_object(
                publication,
                role="publication",
                model_id=self.vibe_model_id,
                output_key=output_key,
            )
            created_publications.append(publication)

        edit = PartDesign.beginDesignOperationEdit(self.vibe_operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            self.vibe_component.Name,
            self.vibe_model_id,
            "accepted-multi-output",
            [],
            [],
            [],
            [],
            output_keys,
            ["solid"] * len(output_keys),
        )
        self.assertEqual(PartDesign.finalizeDesignOperationEdit(edit), [])
        self.document.recompute()
        self.document.commitTransaction()

        def multi_output_snapshot():
            _tree, document_item = self._tree_and_document_item()
            component = _child(document_item, self.vibe_component.Label)
            bodies = _child(component, "Bodies", BROWSER_FOLDER_TYPE)
            history = _child(component, "Design History", BROWSER_FOLDER_TYPE)
            published = _child(
                component,
                "Published Outputs",
                BROWSER_FOLDER_TYPE,
            )
            operation = _child(history, VIBESCRIPT_HISTORY_LABEL)
            values = (component, bodies, history, published, operation)
            if not all(value is not None for value in values):
                return None
            return {
                "details": tuple(
                    child.text(0)
                    for child in _visible_children(operation)
                    if child.type() == BROWSER_DETAIL_TYPE
                ),
                "bodies": tuple(
                    child.text(0) for child in _visible_children(bodies)
                ),
                "publications": tuple(
                    child.text(0) for child in _visible_children(published)
                ),
                "history_count": sum(
                    child.text(0) == VIBESCRIPT_HISTORY_LABEL
                    for child in _visible_walk(component)
                ),
                "operation_parent": operation.parent().text(0),
            }

        observed = _wait_until(multi_output_snapshot)
        self.assertIsNotNone(observed, self._snapshot())
        self.assertEqual(
            observed["details"],
            tuple(f"Produces {output_key}" for output_key in output_keys),
        )
        for body in created_bodies:
            self.assertIn(body.Label, observed["bodies"])
        for body, publication in zip(created_bodies, created_publications):
            self.assertNotEqual(publication.Label, body.Label)
            self.assertIn(body.Label, observed["publications"])
        self.assertEqual(observed["history_count"], 1)
        self.assertEqual(observed["operation_parent"], "Design History")

    def test_vibescript_role_hierarchy_survives_recompute_and_undo_redo(self):
        def role_snapshot():
            _tree, document_item = self._tree_and_document_item()
            component = _child(document_item, self.vibe_component.Label)
            if component is None:
                return None
            folders = tuple(
                _child(component, label, BROWSER_FOLDER_TYPE)
                for label in ("Bodies", "Design History", "Published Outputs")
            )
            if any(folder is None for folder in folders):
                return None
            return (
                component.text(0),
                component.type(),
                tuple(_snapshot(folder) for folder in folders),
            )

        original = _wait_until(role_snapshot)
        self.assertIsNotNone(original, self._snapshot())

        for obj in (
            self.vibe_component,
            self.vibe_body,
            self.vibe_operation,
            self.vibe_output,
        ):
            obj.touch()
        self.document.recompute()
        self.assertEqual(_wait_until(role_snapshot), original)

        self.document.openTransaction("Rename VibeScript presentation objects")
        self.vibe_body.Label = "Renamed Utility Blade"
        self.vibe_output.Label = "Renamed Utility Blade"
        self.vibe_operation.Label = "Internal Program Operation Rename"
        self.document.commitTransaction()
        self.document.recompute()

        renamed = _wait_until(
            lambda: (
                snapshot
                if (
                    (snapshot := role_snapshot()) is not None
                    and snapshot != original
                    and VIBESCRIPT_HISTORY_LABEL in _snapshot_labels(snapshot)
                    and "Internal Program Operation Rename"
                    not in _snapshot_labels(snapshot)
                )
                else None
            )
        )
        self.assertIsNotNone(renamed, self._snapshot())

        self.document.undo()
        self.assertEqual(
            _wait_until(
                lambda: (
                    snapshot
                    if (snapshot := role_snapshot()) == original
                    else None
                )
            ),
            original,
        )

        self.document.redo()
        self.assertEqual(
            _wait_until(
                lambda: (
                    snapshot
                    if (snapshot := role_snapshot()) == renamed
                    else None
                )
            ),
            renamed,
            self._snapshot(),
        )

    def test_native_design_history_keeps_editing_without_a_fake_eye(self):
        self.document.openTransaction("Create native Design operation")
        generator = self.document.addObject(
            "PartDesign::Feature",
            "NativeHistoryGenerator",
        )
        generator.Shape = Part.makeBox(4, 3, 2)
        self.document.classifyProvisionalTimelineInternalObject(generator)
        operation = self.document.addObject(
            "PartDesign::DesignGeneratedOperation",
            "NativeHistoryOperation",
        )
        operation.Label = "Native Generated Build"
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Generator = generator
        operation.GeneratorKind = "model-tree-regression"
        operation.OutputLabel = "Native Generated Body"
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        self.document.recompute()
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        self.assertEqual(len(bodies), 1)

        def native_history_item():
            _tree, document_item = self._tree_and_document_item()
            history = _child(
                document_item,
                "Design History",
                BROWSER_FOLDER_TYPE,
            )
            item = _child(history, operation.Label)
            values = (history, item)
            return values if all(value is not None for value in values) else None

        observed = _wait_until(native_history_item)
        self.assertIsNotNone(observed, self._snapshot())
        self.assertEqual(
            operation.ViewObject.ToggleVisibility,
            "NoToggleVisibility",
        )

        entered = bool(Gui.activeDocument().setEdit(operation.Name))
        try:
            self.assertTrue(entered or Gui.Control.activeDialog())
            self.assertIsNotNone(Gui.activeDocument().getInEdit())
        finally:
            if Gui.activeDocument() and Gui.activeDocument().getInEdit():
                Gui.activeDocument().resetEdit()
            if Gui.Control.activeDialog():
                try:
                    Gui.Control.activeTaskDialog().reject()
                except Exception:
                    Gui.Control.closeDialog()
            _event_step(50)

    @unittest.skipIf(Fem is None, "Requires FEM")
    def test_analyze_folder_preserves_study_membership_without_duplicates(self):
        analysis = self.document.addObject(
            "Fem::FemAnalysis",
            "StructuralAnalysis",
        )
        analysis.Label = "Structural Analysis"
        _tag_timeline_role(analysis, "operation")

        material = self.document.addObject(
            "App::MaterialObjectPython",
            "StructuralMaterial",
        )
        material.Label = "PETG Material"
        _tag_timeline_role(material, "resource")
        analysis.addObject(material)

        constraint = self.document.addObject(
            "Fem::ConstraintFixed",
            "FixedSupport",
        )
        constraint.Label = "Fixed Support"
        _tag_timeline_role(constraint, "resource")
        analysis.addObject(constraint)

        solver = self.document.addObject(
            "Fem::FemSolverObjectPython",
            "CalculiXSolver",
        )
        solver.Label = "CalculiX Solver"
        _tag_timeline_role(solver, "resource")
        analysis.addObject(solver)

        loose_solver = self.document.addObject(
            "Fem::FemSolverObjectPython",
            "UnassignedSolver",
        )
        loose_solver.Label = "Unassigned Solver"
        self.document.recompute()

        def analyze_items():
            _tree, document_item = self._tree_and_document_item()
            analyze = _child(document_item, "Analyze", BROWSER_FOLDER_TYPE)
            study = _child(analyze, analysis.Label)
            values = (document_item, analyze, study)
            return values if all(value is not None for value in values) else None

        observed = _wait_until(analyze_items)
        self.assertIsNotNone(observed, self._snapshot())
        document_item, analyze, study = observed
        self.assertEqual(
            [item.text(0) for item in _visible_children(study)],
            [material.Label, constraint.Label, solver.Label],
        )
        self.assertIsNotNone(_child(analyze, loose_solver.Label))

        operations = _child(document_item, "Design History", BROWSER_FOLDER_TYPE)
        other = _child(document_item, "Other", BROWSER_FOLDER_TYPE)
        for label in (
            analysis.Label,
            material.Label,
            constraint.Label,
            solver.Label,
            loose_solver.Label,
        ):
            self.assertFalse(_snapshot_has_label(_snapshot(operations), label))
            self.assertFalse(_snapshot_has_label(_snapshot(other), label))

    @unittest.skipIf(TechDraw is None, "Requires TechDraw")
    def test_drawings_folder_preserves_page_view_ownership_without_duplicates(self):
        page = self.document.addObject("TechDraw::DrawPage", "DrawingPage")
        page.Label = "Manufacturing Drawing"
        _tag_timeline_role(page, "operation")

        template = self.document.addObject(
            "TechDraw::DrawSVGTemplate",
            "DrawingTemplate",
        )
        template.Label = "A3 Template"
        _tag_timeline_role(template, "resource")
        page.Template = template

        view = self.document.addObject("TechDraw::DrawViewPart", "DrawingView")
        view.Label = "Base Front"
        view.Source = [self.root_operation]
        _tag_timeline_role(view, "operation")
        page.addView(view)

        dimension = self.document.addObject(
            "TechDraw::DrawViewDimension",
            "DrawingDimension",
        )
        dimension.Label = "Overall Width"
        dimension.Type = "Distance"
        dimension.References2D = [(view, "Edge1")]
        _tag_timeline_role(dimension, "operation")
        page.addView(dimension)

        annotation = self.document.addObject(
            "TechDraw::DrawRichAnno",
            "DrawingAnnotation",
        )
        annotation.Label = "General Notes"
        page.addView(annotation)
        self.document.recompute()

        def drawing_items():
            _tree, document_item = self._tree_and_document_item()
            drawings = _child(document_item, "Drawings", BROWSER_FOLDER_TYPE)
            page_item = _child(drawings, page.Label)
            view_item = _child(page_item, view.Label)
            values = (document_item, drawings, page_item, view_item)
            return values if all(value is not None for value in values) else None

        observed = _wait_until(drawing_items)
        self.assertIsNotNone(observed, self._snapshot())
        document_item, drawings, page_item, view_item = observed
        self.assertFalse(drawings.icon(0).isNull())

        previous_visibility_icon = self.tree_parameters.GetBool(
            "VisibilityIcon",
            True,
        )
        try:
            self.tree_parameters.SetBool("VisibilityIcon", False)
            _event_step()
            _tree, current_document_item = self._tree_and_document_item()
            current_drawings = _child(
                current_document_item,
                "Drawings",
                BROWSER_FOLDER_TYPE,
            )
            self.assertIsNotNone(current_drawings)
            expected_icon = Gui.getIcon("preferences-techdraw")
            self.assertIsNotNone(expected_icon)
            self.assertFalse(expected_icon.isNull())
            self.assertEqual(
                _icon_png(current_drawings.icon(0)),
                _icon_png(expected_icon),
            )
        finally:
            self.tree_parameters.SetBool(
                "VisibilityIcon",
                previous_visibility_icon,
            )
            _event_step()

        self.assertIsNotNone(_child(page_item, template.Label))
        self.assertIsNotNone(_child(page_item, annotation.Label))
        self.assertIsNotNone(_child(view_item, dimension.Label))

        operations = _child(document_item, "Design History", BROWSER_FOLDER_TYPE)
        other = _child(document_item, "Other", BROWSER_FOLDER_TYPE)
        for label in (
            page.Label,
            template.Label,
            view.Label,
            dimension.Label,
            annotation.Label,
        ):
            self.assertFalse(_snapshot_has_label(_snapshot(operations), label))
            self.assertFalse(_snapshot_has_label(_snapshot(other), label))

    def test_vibecad_outputs_are_badged_and_not_classified_as_references(self):
        model_id = "browser-target-backed-publication"
        target = self.document.addObject(
            "Part::Feature",
            "VibeGeneratedHousingTarget",
        )
        target.Label = "Generated Housing Target"
        target.Shape = Part.makeCylinder(3, 8)
        self.vibe_component.addObject(target)
        _tag_scripted_object(
            target,
            role="publication_target",
            model_id=model_id,
            output_key="GeneratedHousing",
        )

        output = self.document.addObject(
            "App::Link",
            "VibeGeneratedHousing",
        )
        output.Label = "Generated Housing"
        output.LinkedObject = (
            self.vibe_component,
            f"{target.Name}.",
        )
        output.LinkTransform = True
        _tag_scripted_object(
            output,
            role="publication",
            model_id=model_id,
            output_key="GeneratedHousing",
        )
        self.document.recompute()

        def generated_output_items():
            _tree, document_item = self._tree_and_document_item()
            component = _child(document_item, self.vibe_component.Label)
            category = _child(
                component,
                "Published Outputs",
                BROWSER_FOLDER_TYPE,
            )
            generated = _child(category, output.Label)
            references = _child(
                component,
                "References",
                BROWSER_FOLDER_TYPE,
            )
            values = (document_item, component, category, generated, references)
            return values if all(value is not None for value in values) else None

        observed = _wait_until(generated_output_items)
        self.assertIsNotNone(observed, self._snapshot())
        document_item, vibe_component, category, generated, references = observed
        self.assertIsNone(_child(references, output.Label))
        self.assertFalse(category.icon(0).isNull())
        self.assertIn("Created by VibeCAD", generated.toolTip(0))
        self.assertIn("Created by VibeCAD", vibe_component.toolTip(0))

        manual_component = _child(document_item, self.component.Label)
        self.assertNotIn("Created by VibeCAD", manual_component.toolTip(0))
        self.assertNotEqual(
            vibe_component.icon(0).cacheKey(),
            manual_component.icon(0).cacheKey(),
        )

    def test_assembly_structure_is_visible_without_history_duplication_folders(self):
        assembly = self.document.addObject(
            "Assembly::AssemblyObject",
            "BrowserAssembly",
        )
        assembly.Type = "Assembly"
        assembly.Label = "Drive Assembly"
        joints = assembly.newObject("Assembly::JointGroup", "BrowserJoints")
        joints.Label = "Joints"
        occurrence = assembly.newObject("App::Link", "BrowserOccurrence")
        occurrence.Label = "Rotor"
        occurrence.LinkedObject = self.loose_geometry
        joint = joints.newObject("App::FeaturePython", "BrowserJoint")
        joint.Label = "Rotor Bearing"
        motion = assembly.newObject("App::FeaturePython", "BrowserMotion")
        motion.Label = "Rotor Drive"
        simulations = assembly.newObject(
            "Assembly::SimulationGroup",
            "BrowserSimulations",
        )
        simulations.Label = "Simulations"
        simulation = simulations.newObject(
            "App::FeaturePython",
            "BrowserSimulation",
        )
        simulation.Label = "Run Cycle"
        exploded_views = assembly.newObject(
            "Assembly::ViewGroup",
            "BrowserExplodedViews",
        )
        exploded_views.Label = "Exploded Views"
        exploded_view = exploded_views.newObject(
            "App::FeaturePython",
            "BrowserExplodedView",
        )
        exploded_view.Label = "Service View"
        bom_group = assembly.newObject(
            "Assembly::BomGroup",
            "BrowserBoms",
        )
        bom_group.Label = "Bills of Materials"
        bom = bom_group.newObject("Assembly::BomObject", "BrowserBom")
        bom.Label = "Production BOM"
        bom.autoGenerate = False
        bom.columnsNames = ["Index", "Name", "Quantity"]
        for address, value in (
            ("A1", "Index"),
            ("B1", "Name"),
            ("C1", "Quantity"),
            ("A2", "1"),
            ("B2", "Ring Housing"),
            ("C2", "1"),
            ("A3", "2"),
            ("B3", "Planet Gear"),
            ("C3", "4"),
        ):
            bom.set(address, value)
        for obj, output_type in (
            (occurrence, "component_link"),
            (joint, "joint"),
            (motion, "motion"),
            (simulation, "simulation"),
            (exploded_view, "exploded_view"),
            (bom, "bom"),
        ):
            obj.addProperty(
                "App::PropertyString",
                "VibeCADVibeScriptOutputType",
                "VibeCAD Publication",
            )
            obj.VibeCADVibeScriptOutputType = output_type
            obj.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "VibeCAD History",
            )
            obj.VibeCADTimelineRole = "operation"
        self.document.recompute()

        def assembly_items():
            snapshot = self._snapshot()
            item = _snapshot_child(snapshot, "Drive Assembly")
            components = _snapshot_child(item, "Components", BROWSER_FOLDER_TYPE)
            motions = _snapshot_child(item, "Motions", BROWSER_FOLDER_TYPE)
            joint_group = _snapshot_child(item, "Joints")
            simulation_group = _snapshot_child(item, "Simulations")
            exploded_group = _snapshot_child(item, "Exploded Views")
            bills_group = _snapshot_child(item, "Bills of Materials")
            rotor = _snapshot_child(components, "Rotor")
            drive = _snapshot_child(motions, "Rotor Drive")
            bearing = _snapshot_child(joint_group, "Rotor Bearing")
            cycle = _snapshot_child(simulation_group, "Run Cycle")
            service_view = _snapshot_child(exploded_group, "Service View")
            production_bom = _snapshot_child(bills_group, "Production BOM")
            housing_row = _snapshot_child(
                production_bom,
                "1. Ring Housing  ×1",
                BROWSER_DETAIL_TYPE,
            )
            planet_row = _snapshot_child(
                production_bom,
                "2. Planet Gear  ×4",
                BROWSER_DETAIL_TYPE,
            )
            values = (
                snapshot,
                item,
                components,
                motions,
                joint_group,
                simulation_group,
                exploded_group,
                bills_group,
                rotor,
                drive,
                bearing,
                cycle,
                service_view,
                production_bom,
                housing_row,
                planet_row,
            )
            return values if all(value is not None for value in values) else None

        observed = _wait_until(assembly_items)
        self.assertIsNotNone(observed, self._snapshot())
        (
            _snapshot_value,
            item,
            components,
            motions,
            joint_group,
            simulation_group,
            exploded_group,
            bills_group,
            rotor,
            drive,
            bearing,
            cycle,
            service_view,
            production_bom,
            housing_row,
            planet_row,
        ) = observed
        self.assertIsNone(_snapshot_child(item, "Groups", BROWSER_FOLDER_TYPE))
        self.assertEqual(housing_row[2], ())
        self.assertEqual(planet_row[2], ())
        self.assertTrue(
            all(
                value is not None
                for value in (
                    components,
                    motions,
                    joint_group,
                    simulation_group,
                    exploded_group,
                    bills_group,
                    rotor,
                    drive,
                    bearing,
                    cycle,
                    service_view,
                    production_bom,
                )
            )
        )

    def test_ordinary_and_incomplete_root_links_are_not_publications(self):
        linked_objects = (
            (
                "OrdinarySketchLink",
                "Ordinary Sketch Link",
                self.profile_beta,
            ),
            (
                "OrdinaryReferenceLink",
                "Ordinary Reference Link",
                self.reference,
            ),
            (
                "OrdinaryHistoryLink",
                "Ordinary History Link",
                self.feature,
            ),
        )
        ordinary_links = []
        for name, label, target in linked_objects:
            link = self.document.addObject("App::Link", name)
            link.Label = label
            link.LinkedObject = target
            ordinary_links.append(link)

        # A role string without the complete persisted engine/model/output
        # identity is not a publication contract. Treating it as one would
        # make a damaged or unrelated Link capable of hiding native objects.
        incomplete = self.document.addObject(
            "App::Link",
            "IncompletePublicationMetadata",
        )
        incomplete.Label = "Incomplete Publication Metadata"
        incomplete.LinkedObject = self.profile_alpha
        for name, value in (
            ("VibeCADScriptedRole", "publication"),
            ("VibeCADScriptedEngine", "vibescript:partdesign"),
        ):
            incomplete.addProperty(
                "App::PropertyString",
                name,
                "VibeCAD Publication",
            )
            setattr(incomplete, name, value)

        # Complete metadata still needs one unambiguous persisted counterpart.
        # A lone publication Link must remain visible even when its native
        # target happens to live under a component.
        unpaired = self.document.addObject(
            "App::Link",
            "UnpairedPublication",
        )
        unpaired.Label = "Unpaired Publication"
        unpaired.LinkedObject = self.profile_alpha
        _tag_scripted_object(
            unpaired,
            role="publication",
            model_id="unpaired-browser-contract",
            output_key="MissingImplementation",
        )

        # Even complete properties are not deterministic if two objects claim
        # the same identity. Both links and their native target must remain
        # visible so the conflict can be diagnosed instead of guessed away.
        ambiguous_links = []
        for suffix in ("A", "B"):
            link = self.document.addObject(
                "App::Link",
                f"AmbiguousPublication{suffix}",
            )
            link.Label = f"Ambiguous Publication {suffix}"
            link.LinkedObject = self.profile_beta
            _tag_scripted_object(
                link,
                role="publication",
                model_id="ambiguous-browser-contract",
                output_key="DuplicateOutput",
            )
            ambiguous_links.append(link)

        self.document.recompute()

        expected_root_references = {
            link.Label
            for link in (
                *ordinary_links,
                incomplete,
                unpaired,
                *ambiguous_links,
            )
        }

        def projection_state():
            document = self._snapshot()
            component = _snapshot_child(document, self.component.Label)
            root_references = _snapshot_child(
                document,
                "References",
                BROWSER_FOLDER_TYPE,
            )
            sketches = _snapshot_child(
                component,
                "Sketches",
                BROWSER_FOLDER_TYPE,
            )
            references = _snapshot_child(
                component,
                "References",
                BROWSER_FOLDER_TYPE,
            )
            if (
                root_references is None
                or sketches is None
                or references is None
            ):
                return None
            return (
                set(_snapshot_labels(root_references)),
                set(_snapshot_labels(sketches)),
                set(_snapshot_labels(references)),
            )

        state = _wait_until(projection_state)
        self.assertIsNotNone(state, self._snapshot())
        root_reference_labels, sketch_labels, reference_labels = state
        self.assertTrue(
            expected_root_references.issubset(root_reference_labels),
            root_reference_labels,
        )
        self.assertIn(self.profile_alpha.Label, sketch_labels)
        self.assertIn(self.profile_beta.Label, sketch_labels)
        self.assertIn(self.reference.Label, reference_labels)

        Gui.activeView().setActiveObject("pdbody", self.feature_body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.feature)
        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline)
        self.assertTrue(
            _wait_until(
                lambda: any(
                    timeline.item(row).data(QtCore.Qt.UserRole)
                    == self.feature.Name
                    for row in range(timeline.count())
                )
            ),
            "An ordinary Link to a history operation must not remove that "
            "operation from the native Body timeline.",
        )

    def test_virtual_component_occurrence_selects_its_root_object_identity(self):
        occurrence = self.document.addObject(
            "App::Link",
            "VirtualMountedOccurrence",
        )
        occurrence.Label = "Mounted Blade Occurrence"
        occurrence.LinkedObject = self.vibe_body
        occurrence.addProperty(
            "App::PropertyString",
            "VibeCADVibeScriptOutputType",
            "VibeCAD Publication",
        )
        occurrence.VibeCADVibeScriptOutputType = "component_link"
        occurrence.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "VibeCAD History",
        )
        occurrence.VibeCADTimelineRole = "internal"
        self.vibe_component.addProperty(
            "App::PropertyStringList",
            "VibeCADPartDesignComponentOccurrenceNames",
            "VibeCAD Publication",
        )
        self.vibe_component.VibeCADPartDesignComponentOccurrenceNames = [
            occurrence.Name
        ]
        self.document.recompute()

        def occurrence_item():
            tree, document_item = self._tree_and_document_item()
            component = _child(document_item, self.vibe_component.Label)
            components = _child(component, "Components", BROWSER_FOLDER_TYPE)
            item = _child(components, occurrence.Label)
            return (
                tree,
                document_item,
                component,
                components,
                item,
            ) if tree is not None and item is not None else None

        observed = _wait_until(occurrence_item)
        self.assertIsNotNone(observed, self._snapshot())
        self.assertNotIn(occurrence, self.vibe_component.Group)

        def select_live_occurrence_item():
            observed_item = occurrence_item()
            if observed_item is None:
                return False
            tree, document_item, component, components, item = observed_item
            try:
                Gui.Selection.clearSelection()
                for ancestor in (document_item, component, components):
                    if not ancestor.isExpanded():
                        tree.expandItem(ancestor)
                        return False
                tree.scrollToItem(item)
                tree.setFocus()
                position = tree.visualItemRect(item).center()
                global_position = tree.viewport().mapToGlobal(position)
                for event_type, buttons in (
                    (QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
                    (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton),
                ):
                    event = QtGui.QMouseEvent(
                        event_type,
                        QtCore.QPointF(position),
                        QtCore.QPointF(global_position),
                        QtCore.Qt.LeftButton,
                        buttons,
                        QtCore.Qt.NoModifier,
                    )
                    QtGui.QApplication.sendEvent(tree.viewport(), event)
            except RuntimeError:
                # The projected browser can replace rows while processing a
                # queued document update. Reacquire that row before retrying.
                return False
            selections = Gui.Selection.getSelectionEx(
                self.document.Name,
                Gui.Selection.ResolveMode.NoResolve,
            )
            return (
                len(selections) == 1
                and selections[0].Object == occurrence
                and not selections[0].SubElementNames
            )

        self.assertTrue(
            _wait_until(select_live_occurrence_item),
            self._snapshot(),
        )

        selections = Gui.Selection.getSelectionEx(
            self.document.Name,
            Gui.Selection.ResolveMode.NoResolve,
        )
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0].Object, occurrence)
        self.assertEqual(tuple(selections[0].SubElementNames), ())

    def test_folder_context_action_survives_browser_rebuild(self):
        tree, document_item = self._tree_and_document_item()
        component = _child(document_item, "Browser Component")
        sketches = _child(component, "Sketches", BROWSER_FOLDER_TYPE)
        self.assertIsNotNone(tree)
        self.assertIsNotNone(sketches)

        self.profile_alpha.Visibility = True
        self.profile_beta.Visibility = True
        self.assertTrue(self.profile_alpha.Visibility)
        self.assertTrue(self.profile_beta.Visibility)

        action_state = {}
        rebuild_timer = QtCore.QElapsedTimer()

        def force_browser_rebuild():
            try:
                note = self.document.addObject(
                    "App::FeaturePython",
                    "ContextMenuRebuildNote",
                )
                note.Label = "Context Menu Rebuild Note"
                self.design_notes.addObject(note)
                # Selecting through the public API synchronously flushes the
                # tree's queued structural update before resolving the path.
                # This makes the lifetime check deterministic even inside the
                # native menu's nested event loop.
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(note)
            except Exception as error:
                action_state["error"] = repr(error)

        def trigger_hide_all_after_rebuild():
            popup = QtGui.QApplication.activePopupWidget()
            if popup is None:
                action_state["error"] = "No active browser context menu"
                return

            _, current_document_item = self._tree_and_document_item()
            current_component = _child(
                current_document_item,
                "Browser Component",
            )
            current_sketches = _child(
                current_component,
                "Sketches",
                BROWSER_FOLDER_TYPE,
            )
            # Never invoke a Qt method through the stale Python wrapper: in
            # release builds a deleted QTreeWidgetItem may be reclaimed before
            # Shiboken notices, turning a test probe into the very UAF we are
            # preventing.  A rebuilt tree necessarily exposes a different
            # wrapper identity, which proves replacement without dereferencing
            # the retired item.
            if current_sketches is None or current_sketches is sketches:
                if rebuild_timer.elapsed() < 3000:
                    QtCore.QTimer.singleShot(
                        10,
                        trigger_hide_all_after_rebuild,
                    )
                    return
                action_state["error"] = (
                    "Browser folder was not replaced while its menu was open"
                )
                popup.close()
                return

            action_state["replaced"] = True
            try:
                hide_action = next(
                    (
                        action
                        for action in popup.actions()
                        if action.text().replace("&", "") == "Hide All"
                    ),
                    None,
                )
                if hide_action is None:
                    action_state["error"] = (
                        "Browser context menu omitted Hide All"
                    )
                else:
                    hide_action.trigger()
                    action_state["hide"] = True
            except Exception as error:
                action_state["error"] = repr(error)
            finally:
                popup.close()

        tree.scrollToItem(sketches)
        _event_step()
        viewport_position = tree.visualItemRect(sketches).center()
        rebuild_timer.start()
        QtCore.QTimer.singleShot(0, force_browser_rebuild)
        QtCore.QTimer.singleShot(10, trigger_hide_all_after_rebuild)
        event = QtGui.QContextMenuEvent(
            QtGui.QContextMenuEvent.Mouse,
            viewport_position,
            tree.viewport().mapToGlobal(viewport_position),
        )
        # QAbstractScrollArea receives mouse context-menu events through its
        # viewport. Sending this directly to the QTreeWidget uses frame-relative
        # coordinates and can miss the target item entirely.
        QtGui.QApplication.sendEvent(tree.viewport(), event)

        # Some Qt platform plugins return from the synthetic context-menu
        # event before dispatching zero-delay timers.  Drive the event loop
        # until the native popup callback has either exercised the rebuilt
        # folder or reported a concrete error.
        self.assertIsNotNone(
            _wait_until(lambda: action_state if action_state else None),
            action_state,
        )
        self.assertNotIn("error", action_state, action_state)
        self.assertTrue(action_state.get("replaced"), action_state)
        self.assertTrue(action_state.get("hide"), action_state)
        self.assertTrue(
            _wait_until(
                lambda: (
                    not self.profile_alpha.Visibility
                    and not self.profile_beta.Visibility
                )
            )
        )

    def test_publication_is_secondary_and_body_tip_is_the_only_solid(self):
        def browser_state():
            document = self._snapshot()
            component = _snapshot_child(document, "Vibe Program")
            bodies = _snapshot_child(
                component,
                "Bodies",
                BROWSER_FOLDER_TYPE,
            )
            published = _snapshot_child(
                component,
                "Published Outputs",
                BROWSER_FOLDER_TYPE,
            )
            body = _snapshot_child(bodies, self.vibe_body.Label)
            output = _snapshot_child(published, self.vibe_output.Label)
            if (
                component is None
                or bodies is None
                or published is None
                or body is None
                or output is None
            ):
                return None
            return (
                [child[0] for child in bodies[2]],
                _snapshot_labels(component),
                len(body[2]),
                [child[0] for child in published[2]],
            )

        state = _wait_until(browser_state)
        self.assertIsNotNone(state, _wait_until(self._snapshot))
        self.assertEqual(
            state[0],
            [self.vibe_body.Label],
        )
        self.assertEqual(state[2], 0)
        self.assertEqual(state[3], [self.vibe_output.Label])

        visible_labels = list(state[1])
        self.assertNotIn(self.vibe_result.Label, visible_labels)
        self.assertEqual(
            visible_labels.count(self.vibe_body.Label),
            2,
        )
        self.assertFalse(self.vibe_output.Visibility)
        self.assertIs(self.vibe_output.getLinkedObject(), self.vibe_body)

        triangles, _lines = _primitive_counts(self.vibe_body)
        self.assertGreater(triangles, 0)
        self.assertTrue(self.vibe_body.Visibility)
        self.assertFalse(self.vibe_prior_result.Visibility)
        self.assertTrue(self.vibe_result.Visibility)

        self.document.ShowHidden = True
        _event_step()
        component = _snapshot_child(self._snapshot(), "Vibe Program")
        visible_labels = list(_snapshot_labels(component))
        self.assertEqual(
            visible_labels.count(self.vibe_body.Label),
            2,
        )
        self.document.ShowHidden = False

    def test_body_eye_hides_solid_but_sketch_eye_remains_independent(self):
        self._toggle_vibe_item("body")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_result.Visibility
                    and not self.vibe_output.Visibility
                    and _primitive_counts(self.vibe_result)[0] == 0
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_result.Visibility,
                self.vibe_output.Visibility,
                _primitive_counts(self.vibe_body),
            ),
        )

        # Hiding a Body must not make its sketch eye meaningless.
        self._toggle_vibe_item("sketch")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and self.vibe_sketch.Visibility
                    and not self.vibe_prior_result.Visibility
                    and not self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_result)[0] == 0
                    and _primitive_counts(self.vibe_sketch)[1] > 0
                    and _is_in_active_scene(self.vibe_sketch)
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_sketch.Visibility,
                self.vibe_result.Visibility,
                _primitive_counts(self.vibe_body),
            ),
        )

        # A full view-provider update used to remove the physically mounted
        # Body container because its logical eye is off. The independently
        # visible sketch must survive touch/recompute/update cycles.
        self.vibe_body.touch()
        self.document.recompute()
        self.vibe_body.ViewObject.update()
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and self.vibe_sketch.Visibility
                    and not self.vibe_prior_result.Visibility
                    and not self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_result)[0] == 0
                    and _primitive_counts(self.vibe_sketch)[1] > 0
                    and _is_in_active_scene(self.vibe_sketch)
                )
            )
        )

        # Showing the Body restores only its Tip and leaves the sketch state
        # alone.
        self._toggle_vibe_item("body")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and self.vibe_sketch.Visibility
                    and not self.vibe_prior_result.Visibility
                    and self.vibe_result.Visibility
                    and not self.vibe_output.Visibility
                    and _primitive_counts(self.vibe_body)[0] > 0
                )
            )
        )

    def test_legacy_tip_display_mode_uses_one_tip_child_and_never_gates_sketches(self):
        expected_result_primitives = _primitive_counts(self.vibe_result)
        self.assertGreater(expected_result_primitives[0], 0)

        # Older documents may persist DisplayModeBody="Tip". VibeCAD keeps
        # that public property readable, but it must not reactivate the Body's
        # copied Shape branch alongside the actual Tip child.
        self.vibe_body.ViewObject.DisplayModeBody = "Tip"
        self.vibe_sketch.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_result)
                    == expected_result_primitives
                    and _primitive_counts(self.vibe_sketch)[1] > 0
                    and _primitive_counts(self.vibe_body)
                    == tuple(
                        result_count + sketch_count
                        for result_count, sketch_count in zip(
                            _primitive_counts(self.vibe_result),
                            _primitive_counts(self.vibe_sketch),
                        )
                    )
                )
            ),
            (
                self.vibe_body.ViewObject.DisplayModeBody,
                _primitive_counts(self.vibe_body),
                _primitive_counts(self.vibe_result),
                _primitive_counts(self.vibe_sketch),
            ),
        )

        self.vibe_body.Visibility = False
        self.vibe_body.touch()
        self.document.recompute()
        self.vibe_body.ViewObject.update()
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_result)[0] == 0
                    and self.vibe_sketch.Visibility
                    and _primitive_counts(self.vibe_sketch)[1] > 0
                    and _primitive_counts(self.vibe_body)
                    == _primitive_counts(self.vibe_sketch)
                    and _is_in_active_scene(self.vibe_sketch)
                )
            ),
            (
                self.vibe_body.ViewObject.DisplayModeBody,
                _primitive_counts(self.vibe_body),
                _primitive_counts(self.vibe_result),
                _primitive_counts(self.vibe_sketch),
                _is_in_active_scene(self.vibe_sketch),
            ),
        )

        self.vibe_body.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_result.Visibility
                    and self.vibe_sketch.Visibility
                    and _primitive_counts(self.vibe_result)
                    == expected_result_primitives
                    and _primitive_counts(self.vibe_body)
                    == tuple(
                        result_count + sketch_count
                        for result_count, sketch_count in zip(
                            _primitive_counts(self.vibe_result),
                            _primitive_counts(self.vibe_sketch),
                        )
                    )
                )
            )
        )

    def test_timeline_history_visibility_routes_to_body_final_result(self):
        Gui.activeView().setActiveObject("pdbody", self.vibe_body)
        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline)

        def prior_item():
            for row in range(timeline.count()):
                item = timeline.item(row)
                if item.data(QtCore.Qt.UserRole) == self.vibe_prior_result.Name:
                    return item
            return None

        item = _wait_until(prior_item)
        self.assertIsNotNone(item)
        timeline.clearSelection()
        timeline.setCurrentItem(item)
        item.setSelected(True)
        timeline.setFocus()
        self.assertIsNotNone(
            _wait_until(
                lambda: Gui.Selection.getSelection()
                == [self.vibe_prior_result]
            )
        )

        expected_triangles = _primitive_counts(self.vibe_result)[0]
        self.assertGreater(expected_triangles, 0)

        # Explicit Show on a history operation shows the Body's current state;
        # it cannot expose that operation as a second persistent solid.
        Gui.runCommand("Std_ShowSelection")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_body)[0]
                    == expected_triangles
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_prior_result.Visibility,
                self.vibe_result.Visibility,
                _primitive_counts(self.vibe_body),
                expected_triangles,
            ),
        )

        # Std_ToggleVisibility is the command bound to Space. It uses the same
        # selection path and therefore toggles the Body, never the selected
        # historical result.
        Gui.runCommand("Std_ToggleVisibility")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and not self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_body)[0] == 0
                )
            )
        )
        Gui.runCommand("Std_ToggleVisibility")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_body)[0]
                    == expected_triangles
                )
            )
        )

    def test_history_visibility_routing_does_not_depend_on_typed_browser(self):
        self.tree_parameters.SetBool("OrganizeModelByType", False)
        _event_step()

        self.vibe_body.Visibility = False
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and not self.vibe_result.Visibility
                )
            )
        )

        # The legacy dependency tree selects this feature through its owning
        # component/Body path. It must still control the Body's current result,
        # not expose the historical solid by itself.
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.vibe_prior_result)
        Gui.runCommand("Std_ShowSelection")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and self.vibe_result.Visibility
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_prior_result.Visibility,
                self.vibe_result.Visibility,
            ),
        )

        Gui.runCommand("Std_HideSelection")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and not self.vibe_result.Visibility
                )
            )
        )

    def test_publication_visibility_routing_does_not_depend_on_typed_browser(self):
        self.tree_parameters.SetBool("OrganizeModelByType", False)
        _event_step()

        self.vibe_body.Visibility = False
        self.vibe_output.Visibility = False
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_result.Visibility
                    and not self.vibe_output.Visibility
                )
            )
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.vibe_output)
        Gui.runCommand("Std_ShowSelection")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and self.vibe_result.Visibility
                    and not self.vibe_output.Visibility
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_result.Visibility,
                self.vibe_output.Visibility,
            ),
        )

        Gui.runCommand("Std_HideSelection")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_result.Visibility
                    and not self.vibe_output.Visibility
                )
            )
        )

    def test_multiple_history_selections_toggle_the_body_once(self):
        self.vibe_body.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and self.vibe_result.Visibility
                )
            )
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.vibe_prior_result)
        Gui.Selection.addSelection(self.vibe_result)
        self.assertEqual(
            Gui.Selection.getSelection(),
            [self.vibe_prior_result, self.vibe_result],
        )

        # Both rows resolve to one semantic visibility target. Space must
        # mutate that Body once; two independent toggles would cancel out.
        Gui.runCommand("Std_ToggleVisibility")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and not self.vibe_result.Visibility
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_prior_result.Visibility,
                self.vibe_result.Visibility,
            ),
        )

    def test_selected_bodies_toggle_while_a_feature_task_is_open(self):
        other = self.document.addObject("PartDesign::Body", "OtherToggleBody")
        other.Label = "Other Toggle Body"
        other_tip = other.newObject("PartDesign::Feature", "OtherToggleResult")
        other_tip.Shape = Part.makeBox(4, 4, 4)
        other.Tip = other_tip
        other.Visibility = True
        other_tip.Visibility = True
        self.document.recompute()
        self.assertIsNotNone(
            _wait_until(
                lambda: other.Visibility and other_tip.Visibility
            )
        )

        Gui.activeView().setActiveObject("pdbody", self.vibe_body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.vibe_body)
        Gui.Selection.addSelection(other)
        entered = bool(Gui.activeDocument().setEdit(self.vibe_sketch.Name))
        self.assertTrue(entered or Gui.Control.activeDialog())
        try:
            self.assertTrue(
                self.document.HasPendingTransaction
                or Gui.activeDocument().getInEdit() is not None
            )

            other.ViewObject.hide()
            self.assertIsNotNone(
                _wait_until(
                    lambda: (
                        not other.Visibility
                        and not other_tip.Visibility
                    )
                ),
                (other.Visibility, other_tip.Visibility),
            )

            self.vibe_body.ViewObject.hide()
            self.assertIsNotNone(
                _wait_until(
                    lambda: (
                        not self.vibe_body.Visibility
                        and not self.vibe_result.Visibility
                    )
                ),
                (
                    self.vibe_body.Visibility,
                    self.vibe_result.Visibility,
                ),
            )

            self.vibe_body.ViewObject.show()
            other.ViewObject.show()
            self.assertIsNotNone(
                _wait_until(
                    lambda: (
                        self.vibe_body.Visibility
                        and self.vibe_result.Visibility
                        and other.Visibility
                        and other_tip.Visibility
                    )
                )
            )
        finally:
            if Gui.activeDocument() and Gui.activeDocument().getInEdit():
                Gui.activeDocument().resetEdit()
            if Gui.Control.activeDialog():
                try:
                    Gui.Control.activeTaskDialog().reject()
                except Exception:
                    Gui.Control.closeDialog()
            _event_step(50)

    def test_tip_change_and_undo_replace_the_sole_rendered_result(self):
        self.document.openTransaction("Move Body Tip")
        self.vibe_body.Tip = self.vibe_prior_result
        self.document.recompute()
        self.document.commitTransaction()

        prior_triangles = _primitive_counts(self.vibe_prior_result)[0]
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Tip is self.vibe_prior_result
                    and self.vibe_body.Visibility
                    and self.vibe_prior_result.Visibility
                    and not self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_body)[0]
                    == prior_triangles
                )
            )
        )

        self.document.undo()
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Tip is self.vibe_result
                    and self.vibe_body.Visibility
                    and not self.vibe_prior_result.Visibility
                    and self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_body)[0] > 0
                )
            )
        )

    def test_consumed_sketch_edit_allows_preview_then_restores_tip(self):
        Gui.activateWorkbench("PartDesignWorkbench")
        Gui.activeView().setActiveObject("pdbody", self.vibe_body)
        self.assertTrue(self.vibe_body.Visibility)
        self.assertTrue(self.vibe_result.Visibility)
        self.assertFalse(self.vibe_prior_result.Visibility)

        def enter_consumed_sketch():
            Gui.activeDocument().setEdit(self.vibe_sketch.Name)
            self.assertIsNotNone(
                _wait_until(
                    lambda: Gui.activeDocument().getInEdit() is not None
                )
            )
            self.assertIs(
                Gui.activeDocument().getInEdit().Object,
                self.vibe_sketch,
            )
            self.assertIsNotNone(
                _wait_until(lambda: not self.vibe_result.Visibility),
                "Sketcher TempoVis must be allowed to hide the consumed result",
            )

        def assert_tip_restored():
            self.assertIsNotNone(
                _wait_until(
                    lambda: Gui.activeDocument().getInEdit() is None
                )
            )
            self.assertIsNotNone(
                _wait_until(
                    lambda: (
                        self.vibe_body.Visibility
                        and not self.vibe_prior_result.Visibility
                        and self.vibe_result.Visibility
                        and _primitive_counts(self.vibe_body)[0] > 0
                    )
                )
            )

        enter_consumed_sketch()
        Gui.Control.activeTaskDialog().reject()
        assert_tip_restored()

        enter_consumed_sketch()
        Gui.Control.activeTaskDialog().accept()
        assert_tip_restored()

    def test_link_occurrence_and_definition_visibility_are_independent(self):
        assembly = self.document.addObject(
            "App::Part",
            "VisibilityOccurrenceAssembly",
        )
        first = assembly.newObject("App::Link", "FirstOccurrence")
        first.LinkedObject = self.vibe_body
        first.LinkTransform = True
        second = assembly.newObject("App::Link", "SecondOccurrence")
        second.LinkedObject = self.vibe_body
        second.LinkTransform = True
        first.Visibility = True
        second.Visibility = True
        self.vibe_body.Visibility = True
        self.vibe_result.Visibility = True
        self.document.recompute()

        def direct_occurrence_path(occurrence):
            matches = [
                subpath
                for subpath in assembly.getSubObjects()
                if subpath.partition(".")[0] == occurrence.Name
            ]
            self.assertEqual(len(matches), 1)
            resolved, parent, element_name, subelement = assembly.resolve(matches[0])
            self.assertIs(resolved, occurrence)
            self.assertIs(parent, assembly)
            self.assertEqual(element_name, occurrence.Name)
            self.assertEqual(subelement, "")
            return matches[0], parent, element_name

        first_path, first_parent, first_element = direct_occurrence_path(first)
        _second_path, second_parent, second_element = direct_occurrence_path(second)
        # App::Part is a structural parent, not an element-visibility
        # provider.  Its -1 result deliberately sends the standard visibility
        # command to the resolved App::Link occurrence instead of changing
        # the shared Body.
        self.assertEqual(first_parent.isElementVisible(first_element), -1)
        self.assertEqual(second_parent.isElementVisible(second_element), -1)
        self.assertTrue(first.Visibility)
        self.assertTrue(second.Visibility)
        self.assertTrue(self.vibe_body.Visibility)
        self.assertTrue(self.vibe_result.Visibility)

        # A reusable definition is not an implicit occurrence at its authoring
        # origin. Hiding that definition must not disable either independently
        # visible App::Link occurrence that consumes its final Body shape.
        self.vibe_body.Visibility = False
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_result.Visibility
                    and first.Visibility
                    and second.Visibility
                    and _primitive_counts(first)[0] > 0
                    and _primitive_counts(second)[0] > 0
                    and _is_in_active_scene(first)
                    and _is_in_active_scene(second)
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_result.Visibility,
                first.Visibility,
                second.Visibility,
                _primitive_counts(first),
                _primitive_counts(second),
                _is_in_active_scene(first),
                _is_in_active_scene(second),
                getattr(first, "ShowElement", None),
                tuple(getattr(obj, "Name", "") for obj in first.ElementList),
                tuple(first.VisibilityList),
            ),
        )
        self.vibe_body.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: self.vibe_body.Visibility and self.vibe_result.Visibility
            )
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            self.document.Name,
            assembly.Name,
            first_path,
        )
        raw_selection = Gui.Selection.getSelectionEx(
            self.document.Name,
            Gui.Selection.ResolveMode.NoResolve,
        )
        self.assertEqual(len(raw_selection), 1)
        self.assertIs(raw_selection[0].Object, assembly)
        self.assertEqual(raw_selection[0].SubElementNames, (first_path,))

        Gui.runCommand("Std_HideSelection")

        self.assertEqual(first_parent.isElementVisible(first_element), -1)
        self.assertEqual(second_parent.isElementVisible(second_element), -1)
        self.assertFalse(first.Visibility)
        self.assertTrue(second.Visibility)
        self.assertTrue(self.vibe_body.Visibility)
        self.assertTrue(self.vibe_result.Visibility)

        Gui.runCommand("Std_ShowSelection")

        self.assertTrue(first.Visibility)
        self.assertTrue(second.Visibility)
        self.assertTrue(self.vibe_body.Visibility)
        self.assertTrue(self.vibe_result.Visibility)

    def test_component_and_owned_body_visibility_stay_together(self):
        component = self.document.addObject(
            "PartDesign::Component",
            "ImportedScrew",
        )
        component.Label = "91251A051"
        body = self.document.addObject(
            "PartDesign::Body",
            "ImportedScrewBody",
        )
        body.Label = "91251A051 Body"
        component.addObject(body)
        solid = body.newObject(
            "PartDesign::Feature",
            "ImportedScrewGeometry",
        )
        solid.Label = "91251A051 Geometry"
        solid.Shape = Part.makeCylinder(2, 8)
        body.Tip = solid
        self.document.recompute()
        _event_step()

        component.Visibility = True
        body.Visibility = True
        solid.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    component.Visibility
                    and body.Visibility
                    and solid.Visibility
                )
            )
        )

        component.Visibility = False
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not component.Visibility
                    and not body.Visibility
                    and not solid.Visibility
                )
            ),
            (component.Visibility, body.Visibility, solid.Visibility),
        )

        component.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    component.Visibility
                    and body.Visibility
                    and solid.Visibility
                )
            ),
            (component.Visibility, body.Visibility, solid.Visibility),
        )

        body.Visibility = False
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not body.Visibility
                    and not solid.Visibility
                    and not component.Visibility
                )
            ),
            (component.Visibility, body.Visibility, solid.Visibility),
        )

    def test_standard_visibility_commands_use_the_same_body_contract(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.vibe_body)
        Gui.runCommand("Std_HideSelection")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_body.Visibility
                    and not self.vibe_result.Visibility
                    and not self.vibe_output.Visibility
                )
            )
        )

        self.vibe_sketch.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    _primitive_counts(self.vibe_result)[0] == 0
                    and _primitive_counts(self.vibe_sketch)[1] > 0
                    and _is_in_active_scene(self.vibe_sketch)
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_result.Visibility,
                self.vibe_sketch.Visibility,
                _primitive_counts(self.vibe_body),
                _primitive_counts(self.vibe_sketch),
                _is_in_active_scene(self.vibe_sketch),
            ),
        )

        Gui.runCommand("Std_ShowSelection")
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.vibe_body.Visibility
                    and self.vibe_result.Visibility
                    and self.vibe_sketch.Visibility
                    and not self.vibe_output.Visibility
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_result.Visibility,
                self.vibe_sketch.Visibility,
                _primitive_counts(self.vibe_body),
                _primitive_counts(self.vibe_sketch),
                _is_in_active_scene(self.vibe_sketch),
            ),
        )

    def test_native_body_visibility_survives_save_reopen_without_repair(self):
        """Native Body presentation restores without VibeScript migration."""

        self.feature_body.Visibility = False
        self.profile_beta.Visibility = True
        self.reference.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.feature_body.Visibility
                    and not self.feature.Visibility
                    and self.profile_beta.Visibility
                    and self.reference.Visibility
                    and _primitive_counts(self.feature)[0] == 0
                    and _primitive_counts(self.profile_beta)[1] > 0
                    and _is_in_active_scene(self.profile_beta)
                    and _is_in_active_scene(self.reference)
                )
            )
        )

        with tempfile.TemporaryDirectory(
            prefix="vibecad_native_body_visibility_",
        ) as temporary_directory:
            path = os.path.join(temporary_directory, "native_body.FCStd")
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)
            self.feature_body = self.document.getObject("FeatureBody")
            self.profile_beta = self.document.getObject("ProfileBeta")
            self.feature = self.document.getObject("ExtrudeFeature")
            self.reference = self.document.getObject("BladeReference")
            self.assertIsNotNone(_wait_until(self._browser_ready))
            _event_step(100)

            # Deliberately do not call
            # restore_partdesign_history_presentation(): this contract belongs
            # to the native Body view provider for every FCStd document.
            self.assertFalse(self.feature_body.Visibility)
            self.assertFalse(self.feature.Visibility)
            self.assertTrue(self.profile_beta.Visibility)
            self.assertTrue(self.reference.Visibility)
            self.assertEqual(_primitive_counts(self.feature)[0], 0)
            self.assertGreater(_primitive_counts(self.profile_beta)[1], 0)
            self.assertTrue(_is_in_active_scene(self.profile_beta))
            self.assertTrue(_is_in_active_scene(self.reference))

    def test_document_restore_repairs_stale_native_history_visibility(self):
        """A pre-VibeCAD file cannot reopen with two cumulative solids drawn."""

        Gui.activateWorkbench("PartDesignWorkbench")
        final = self.feature_body.newObject(
            "PartDesign::Feature",
            "RestoredFinalFeature",
        )
        final.Label = "Restored Final Feature"
        final.Shape = Part.makeBox(4, 5, 6)
        self.feature_body.Tip = final
        self.feature_body.Visibility = True
        self.profile_beta.Visibility = True
        self.document.recompute()

        with tempfile.TemporaryDirectory(
            prefix="vibecad_native_history_restore_",
        ) as temporary_directory:
            path = os.path.join(temporary_directory, "stale_history.FCStd")
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)

            # Model a valid native document written by an older presentation:
            # both cumulative results were persisted visible. Change both App
            # and Gui state so the test reaches the full-document restore
            # boundary rather than being repaired by ordinary VP/App syncing.
            _rewrite_saved_visibility(
                path,
                {
                    "FeatureBody": True,
                    "ProfileBeta": True,
                    "ExtrudeFeature": True,
                    "RestoredFinalFeature": True,
                },
            )

            self.document = App.openDocument(path)
            self.feature_body = self.document.getObject("FeatureBody")
            self.profile_beta = self.document.getObject("ProfileBeta")
            self.feature = self.document.getObject("ExtrudeFeature")
            final = self.document.getObject("RestoredFinalFeature")
            self.assertIsNotNone(_wait_until(self._browser_ready))
            self.assertIsNotNone(
                _wait_until(
                    lambda: (
                        _primitive_counts(final)[0] > 0
                        and _primitive_counts(self.profile_beta)[1] > 0
                        and _is_in_active_scene(final)
                        and _is_in_active_scene(self.profile_beta)
                    )
                ),
                (
                    _primitive_counts(final),
                    _primitive_counts(self.profile_beta),
                    _is_in_active_scene(final),
                    _is_in_active_scene(self.profile_beta),
                ),
            )

            self.assertIs(self.feature_body.Tip, final)
            self.assertTrue(self.feature_body.Visibility)
            self.assertFalse(self.feature.Visibility)
            self.assertTrue(final.Visibility)
            self.assertTrue(self.profile_beta.Visibility)
            self.assertEqual(_primitive_counts(self.feature)[0], 0)
            self.assertGreater(_primitive_counts(final)[0], 0)
            self.assertGreater(_primitive_counts(self.profile_beta)[1], 0)
            self.assertTrue(_is_in_active_scene(self.profile_beta))

    def test_hidden_body_and_visible_sketch_survive_save_reopen(self):
        self.vibe_body.Visibility = False
        self.vibe_sketch.Visibility = True
        self.vibe_output.Visibility = False
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.vibe_result.Visibility
                    and _primitive_counts(self.vibe_result)[0] == 0
                    and _primitive_counts(self.vibe_sketch)[1] > 0
                    and _is_in_active_scene(self.vibe_sketch)
                )
            ),
            (
                self.vibe_body.Visibility,
                self.vibe_result.Visibility,
                self.vibe_sketch.Visibility,
                _primitive_counts(self.vibe_body),
                _primitive_counts(self.vibe_sketch),
                _is_in_active_scene(self.vibe_sketch),
            ),
        )

        _tree, document_item = self._tree_and_document_item()
        pre_save_component = _child(document_item, self.vibe_component.Label)
        pre_save_roles = _snapshot(pre_save_component)

        with tempfile.TemporaryDirectory(
            prefix="vibecad_body_visibility_",
        ) as temporary_directory:
            path = os.path.join(temporary_directory, "body.FCStd")
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)

            self.vibe_body = self.document.getObject("VibeCandidateBody")
            self.vibe_result = self.document.getObject("VibeResult")
            self.vibe_sketch = self.document.getObject("VibeBladeProfile")
            self.vibe_output = self.document.getObject("VibeUtilityBlade")
            self.vibe_component = self.document.getObject("VibeProgram")
            self.vibe_operation = self.document.getObject("VibeProgramOperation")
            self.assertIsNotNone(_wait_until(self._browser_ready))

            def restored_role_snapshot():
                _tree, document_item = self._tree_and_document_item()
                component = _child(document_item, self.vibe_component.Label)
                bodies = _child(component, "Bodies", BROWSER_FOLDER_TYPE)
                history = _child(component, "Design History", BROWSER_FOLDER_TYPE)
                published = _child(
                    component,
                    "Published Outputs",
                    BROWSER_FOLDER_TYPE,
                )
                body = _child(bodies, self.vibe_body.Label)
                operation = _child(history, VIBESCRIPT_HISTORY_LABEL)
                output = _child(published, self.vibe_body.Label)
                values = (
                    component,
                    bodies,
                    history,
                    published,
                    body,
                    operation,
                    output,
                )
                return (
                    _snapshot(component)
                    if all(value is not None for value in values)
                    else None
                )

            restored = _wait_until(restored_role_snapshot)
            self.assertIsNotNone(restored, self._snapshot())
            self.assertEqual(restored, pre_save_roles)

            from VibeCADVibeScriptDomainPublication import (
                restore_partdesign_history_presentation,
            )

            restore_partdesign_history_presentation(self.document)
            _event_step(100)
            self.assertFalse(self.vibe_body.Visibility)
            self.assertFalse(self.vibe_result.Visibility)
            self.assertTrue(self.vibe_sketch.Visibility)
            self.assertFalse(self.vibe_output.Visibility)
            self.assertEqual(
                _primitive_counts(self.vibe_result)[0],
                0,
            )
            self.assertGreater(_primitive_counts(self.vibe_sketch)[1], 0)
            self.assertTrue(_is_in_active_scene(self.vibe_sketch))

            self._toggle_vibe_item("body")
            self.assertIsNotNone(
                _wait_until(
                    lambda: (
                        self.vibe_body.Visibility
                        and self.vibe_result.Visibility
                        and self.vibe_sketch.Visibility
                        and not self.vibe_output.Visibility
                    )
                )
            )

    def test_legacy_publication_migrates_to_native_body_renderer(self):
        from VibeCADVibeScriptDomainPublication import (
            _LEGACY_PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
            PROP_PARTDESIGN_HISTORY_PRESENTATION,
            restore_partdesign_history_presentation,
        )

        setattr(
            self.vibe_body,
            PROP_PARTDESIGN_HISTORY_PRESENTATION,
            _LEGACY_PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
        )
        self.vibe_body.Visibility = True  # old scene-container state
        self.vibe_result.Visibility = False
        self.vibe_sketch.Visibility = True
        self.vibe_output.Visibility = True

        restored = restore_partdesign_history_presentation(self.document)

        self.assertEqual(restored["migrated_bodies"], [self.vibe_body.Name])
        self.assertTrue(self.vibe_body.Visibility)
        self.assertTrue(self.vibe_result.Visibility)
        self.assertTrue(self.vibe_sketch.Visibility)
        self.assertFalse(self.vibe_output.Visibility)
        self.assertIs(self.vibe_output.getLinkedObject(), self.vibe_body)

    def test_publication_without_body_gets_native_body_and_result(self):
        from VibeCADVibeScriptDomainPublication import (
            PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
            PROP_PARTDESIGN_HISTORY_PRESENTATION,
            restore_partdesign_history_presentation,
        )

        model_id = "legacy-publication-only"
        root = self.document.addObject("App::Part", "LegacyProgram")
        root.Label = "Legacy Program"
        _tag_scripted_object(root, role="model", model_id=model_id)
        target = self.document.addObject(
            "Part::Feature",
            "LegacyPublishedSource",
        )
        target.Shape = Part.makeBox(7, 3, 2)
        root.addObject(target)
        _tag_scripted_object(
            target,
            role="publication_target",
            model_id=model_id,
            output_key="LegacySolid",
        )
        target.Visibility = False
        stable = self.document.addObject("App::Link", "LegacySolid")
        stable.Label = "Legacy Solid"
        stable.LinkedObject = (root, f"{target.Name}.")
        stable.LinkTransform = True
        _tag_scripted_object(
            stable,
            role="publication",
            model_id=model_id,
            output_key="LegacySolid",
        )
        stable.Visibility = True
        self.document.recompute()

        stable_identity = id(stable)
        restored = restore_partdesign_history_presentation(self.document)
        self.document.recompute()

        self.assertEqual(len(restored["migrated_bodies"]), 1)
        body = self.document.getObject(restored["migrated_bodies"][0])
        self.assertIsNotNone(body)
        self.assertEqual(body.TypeId, "PartDesign::Body")
        self.assertIn(
            body.Label,
            {stable.Label, f"{stable.Label} Body"},
        )
        self.assertIsNotNone(body.Tip)
        self.assertEqual(body.Tip.Label, "Result")
        self.assertTrue(body.Tip.Shape.isValid())
        self.assertGreater(body.Tip.Shape.Volume, 0)
        self.assertTrue(body.Visibility)
        self.assertTrue(body.Tip.Visibility)
        self.assertFalse(stable.Visibility)
        self.assertIs(stable.getLinkedObject(), body)
        self.assertEqual(id(self.document.getObject(stable.Name)), stable_identity)
        self.assertEqual(
            getattr(body, PROP_PARTDESIGN_HISTORY_PRESENTATION),
            PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
        )

        def migrated_tree():
            snapshot = self._snapshot()
            component = _snapshot_child(snapshot, root.Label)
            bodies = _snapshot_child(
                component,
                "Bodies",
                BROWSER_FOLDER_TYPE,
            )
            body_item = _snapshot_child(bodies, body.Label)
            labels = _snapshot_labels(component)
            publication_count = labels.count(stable.Label)
            expected_count = 1 if body.Label == stable.Label else 0
            return bool(
                body_item is not None
                and publication_count == expected_count
            )

        self.assertIsNotNone(_wait_until(migrated_tree), self._snapshot())

    def test_real_task_preserves_independent_sketch_and_datum_visibility(self):
        """Feature preview state cannot consume independent browser objects."""

        Gui.activateWorkbench("PartDesignWorkbench")
        Gui.activeView().setActiveObject("pdbody", self.feature_body)
        datum = self.feature_body.newObject(
            "PartDesign::Plane",
            "IndependentTaskPlane",
        )
        datum.Label = "Independent Task Plane"
        self.feature_body.Tip = self.feature
        self.feature_body.Visibility = True
        self.profile_beta.Visibility = True
        datum.Visibility = True
        self.document.recompute()

        independent_visibility = (
            self.profile_beta.Visibility,
            datum.Visibility,
        )
        self.assertEqual(independent_visibility, (True, True))
        original_tip = self.feature_body.Tip

        def open_chamfer_task():
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(
                self.document.Name,
                self.feature.Name,
                "Edge1",
            )
            Gui.runCommand("PartDesign_Chamfer", 0)
            self.assertTrue(Gui.Control.activeDialog())
            operation = self.document.ActiveObject
            self.assertEqual(operation.TypeId, "PartDesign::DesignChamfer")
            self.assertNotIn(operation, self.feature_body.Group)

        open_chamfer_task()
        Gui.Control.activeTaskDialog().reject()
        self.assertIsNotNone(
            _wait_until(lambda: not Gui.Control.activeDialog())
        )
        self.assertIs(self.feature_body.Tip, original_tip)
        self.assertEqual(
            (self.profile_beta.Visibility, datum.Visibility),
            independent_visibility,
        )
        self.assertFalse(self.document.HasPendingTransaction)

        open_chamfer_task()
        Gui.Control.activeTaskDialog().accept()
        self.assertIsNotNone(
            _wait_until(lambda: not Gui.Control.activeDialog())
        )
        publication = self.feature_body.Tip
        self.assertIsNotNone(publication)
        self.assertIsNot(publication, original_tip)
        self.assertEqual(
            publication.TypeId,
            "PartDesign::DesignBodyPublication",
        )
        accepted_state = publication.CurrentState
        self.assertEqual(
            accepted_state.TypeId,
            "PartDesign::DesignBodyState",
        )
        accepted_operation = accepted_state.Operation
        self.assertEqual(
            accepted_operation.TypeId,
            "PartDesign::DesignChamfer",
        )
        self.assertNotIn(accepted_operation, self.feature_body.Group)
        self.assertEqual(
            (self.profile_beta.Visibility, datum.Visibility),
            independent_visibility,
        )
        self.assertTrue(self.feature_body.Visibility)
        self.assertTrue(publication.Visibility)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_chamfer_requires_selection_and_cancel_is_safe(self):
        Gui.activateWorkbench("PartDesignWorkbench")
        Gui.activeView().setActiveObject("pdbody", self.feature_body)
        Gui.Selection.clearSelection()

        original_tip = self.feature_body.Tip
        original_group = tuple(self.feature_body.Group)
        original_names = tuple(obj.Name for obj in self.document.Objects)

        # No selection is rejected before a transaction or temporary feature
        # exists. Dismiss the synchronous native warning in the test event loop.
        def dismiss_warning():
            for widget in QtGui.QApplication.topLevelWidgets():
                if isinstance(widget, QtGui.QMessageBox) and widget.isVisible():
                    widget.accept()

        warning_timer = QtCore.QTimer()
        warning_timer.timeout.connect(dismiss_warning)
        warning_timer.start(10)
        try:
            Gui.runCommand("PartDesign_Chamfer", 0)
        finally:
            warning_timer.stop()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(self.feature_body.Tip, original_tip)
        self.assertEqual(tuple(self.feature_body.Group), original_group)
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )

        # A real selected edge opens the native task. Cancelling exercises the
        # transaction rollback path that previously left TreeWidget with a
        # dangling child and crashed.
        for _attempt in range(25):
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(
                self.document.Name,
                self.feature.Name,
                "Edge1",
            )
            Gui.runCommand("PartDesign_Chamfer", 0)
            self.assertTrue(Gui.Control.activeDialog())
            temporary = self.document.ActiveObject
            self.assertIsNotNone(temporary)
            self.assertEqual(temporary.TypeId, "PartDesign::DesignChamfer")
            self.assertNotIn(temporary, self.feature_body.Group)
            self.assertFalse(
                _snapshot_has_label(self._snapshot(), temporary.Label),
                self._snapshot(),
            )

            # Reject the actual active task dialog. This avoids accidentally
            # clicking a similarly named Cancel button owned by another panel.
            Gui.Control.activeTaskDialog().reject()
            self.assertIsNotNone(
                _wait_until(lambda: not Gui.Control.activeDialog())
            )
            self.assertEqual(self.feature_body.Tip, original_tip)
            self.assertEqual(tuple(self.feature_body.Group), original_group)
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
            )

        # The transaction abort must leave the native visibility contract
        # usable; this was the exact path that previously crashed TreeWidget.
        self.feature_body.Visibility = False
        self.profile_beta.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    not self.feature_body.Visibility
                    and not self.feature.Visibility
                    and self.profile_beta.Visibility
                    and _primitive_counts(self.feature)[0] == 0
                    and _primitive_counts(self.profile_beta)[1] > 0
                    and _is_in_active_scene(self.profile_beta)
                )
            ),
            (
                self.feature_body.Visibility,
                self.feature.Visibility,
                self.profile_beta.Visibility,
                _primitive_counts(self.feature_body),
                _primitive_counts(self.profile_beta),
                _is_in_active_scene(self.profile_beta),
            ),
        )
        self.feature_body.Visibility = True
        self.assertIsNotNone(
            _wait_until(
                lambda: (
                    self.feature.Visibility
                    and self.profile_beta.Visibility
                    and _primitive_counts(self.feature_body)[0] > 0
                )
            )
        )


@unittest.skipIf(Mesh is None, "Requires Mesh")
class TestMeshGroupBrowser(unittest.TestCase):
    """Mesh history stays reachable through the document's Meshes group."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")

        self.tree_parameters = App.ParamGet(TREE_PARAMETER_PATH)
        self.previous_browser_preference = self.tree_parameters.GetBool(
            "OrganizeModelByType",
            True,
        )
        self.tree_parameters.SetBool("OrganizeModelByType", True)
        self.document = App.newDocument("MeshGroupBrowser")
        self.document.Label = "Mesh Group Browser Test"
        Gui.activateView("Gui::View3DInventor", True)

    def tearDown(self):
        if (
            getattr(self, "document", None) is not None
            and App.getDocument(self.document.Name) is not None
        ):
            App.closeDocument(self.document.Name)
        self.tree_parameters.SetBool(
            "OrganizeModelByType",
            self.previous_browser_preference,
        )
        _event_step()

    def _tree_and_document_item(self):
        for tree in Gui.getMainWindow().findChildren(QtGui.QTreeWidget):
            if not tree.isVisible() or not tree.viewport().isVisible():
                continue
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                if not item.isHidden() and item.text(0) == self.document.Label:
                    return tree, item
        return None, None

    def _snapshot(self):
        _tree, document_item = self._tree_and_document_item()
        return _snapshot(document_item) if document_item is not None else None

    def test_mesh_history_object_is_presented_in_its_meshes_group(self):
        meshes = self.document.addObject(
            "App::DocumentObjectGroup",
            "Meshes",
        )
        meshes.Label = "Meshes"
        meshes.addProperty(
            "App::PropertyString",
            "VibeCADTreeRole",
            "Tree",
        )
        meshes.VibeCADTreeRole = "meshes"

        imported = self.document.addObject("Mesh::Feature", "ImportedMesh")
        imported.Label = "Imported Mesh"
        imported.Mesh = Mesh.Mesh(
            [
                (App.Vector(0, 0, 0), App.Vector(10, 0, 0), App.Vector(0, 10, 0)),
            ]
        )
        _tag_timeline_role(imported, "operation")
        meshes.addObject(imported)
        self.document.recompute()

        def mesh_items():
            _tree, document_item = self._tree_and_document_item()
            mesh_group = _child(document_item, "Meshes")
            mesh_item = _child(mesh_group, imported.Label)
            return (document_item, mesh_group, mesh_item) if mesh_item else None

        observed = _wait_until(mesh_items)
        self.assertIsNotNone(observed, self._snapshot())
        document_item, mesh_group, mesh_item = observed
        self.assertIs(mesh_item.parent(), mesh_group)

        operations = _child(document_item, "Design History", BROWSER_FOLDER_TYPE)
        self.assertTrue(
            operations is None
            or not _snapshot_has_label(_snapshot(operations), imported.Label)
        )
        groups = _child(document_item, "Groups", BROWSER_FOLDER_TYPE)
        self.assertTrue(
            groups is None
            or not _snapshot_has_label(_snapshot(groups), meshes.Label)
        )
        self.assertEqual(
            sum(
                item.text(0) == imported.Label
                for item in _visible_walk(document_item)
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
