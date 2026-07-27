# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI integration tests for the consolidated Part Design modeling surface."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui


RETAINED_PART_COMMANDS = {
    "Materials_InspectAppearance",
    "Materials_InspectMaterial",
    "Part_Boolean",
    "Part_BooleanFragments",
    "Part_BoxSelection",
    "Part_Builder",
    "Part_CheckGeometry",
    "Part_ColorPerFace",
    "Part_Common",
    "Part_CompCompoundTools",
    "Part_CompJoinFeatures",
    "Part_CompOffset",
    "Part_CompSplitFeatures",
    "Part_Compound",
    "Part_CompoundFilter",
    "Part_CrossSections",
    "Part_Cut",
    "Part_Defeaturing",
    "Part_EditAttachment",
    "Part_ElementCopy",
    "Part_ExplodeCompound",
    "Part_Extrude",
    "Part_Fuse",
    "Part_JoinConnect",
    "Part_JoinCutout",
    "Part_JoinEmbed",
    "Part_Loft",
    "Part_MakeFace",
    "Part_MakeSolid",
    "Part_Mirror",
    "Part_Offset",
    "Part_Offset2D",
    "Part_PointsFromMesh",
    "Part_Primitives",
    "Part_ProjectionOnSurface",
    "Part_RefineShape",
    "Part_ReverseShape",
    "Part_Revolve",
    "Part_RuledSurface",
    "Part_Scale",
    "Part_Section",
    "Part_SectionCut",
    "Part_ShapeFromMesh",
    "Part_SimpleCopy",
    "Part_Slice",
    "Part_SliceApart",
    "Part_Sweep",
    "Part_ToleranceSet",
    "Part_TransformedCopy",
    "Part_Tube",
    "Part_XOR",
}

RETIRED_REDUNDANT_COMMANDS = {
    # Part Design owns the stronger Body-native versions.
    "Part_Box",
    "Part_Cylinder",
    "Part_Sphere",
    "Part_Cone",
    "Part_Torus",
    "Part_Fillet",
    "Part_Chamfer",
    "Part_Thickness",
    "Part_DatumPoint",
    "Part_DatumLine",
    "Part_DatumPlane",
    "Part_CoordinateSystem",
    # Part owns the stronger general BREP boolean implementation.
    "PartDesign_Boolean",
}

# The generic Sketcher command remains registered for macro compatibility, while the
# consolidated UI uses the Body-aware Part Design workflow.
LEGACY_COMPATIBILITY_COMMANDS = {"Sketcher_NewSketch"}

MODEL_TOOLBARS = {
    "Part Design Helper Features": [
        "PartDesign_Body",
        "PartDesign_CompSketches",
        "Sketcher_ValidateSketch",
        "Part_CheckGeometry",
        "PartDesign_SubShapeBinder",
        "PartDesign_Clone",
    ],
    "Create and Remove Material": [
        "PartDesign_Pad",
        "PartDesign_Revolution",
        "PartDesign_AdditiveLoft",
        "PartDesign_AdditivePipe",
        "PartDesign_AdditiveHelix",
        "PartDesign_CompPrimitiveAdditive",
        "PartDesign_Pocket",
        "PartDesign_Hole",
        "PartDesign_Groove",
        "PartDesign_SubtractiveLoft",
        "PartDesign_SubtractivePipe",
        "PartDesign_SubtractiveHelix",
        "PartDesign_CompPrimitiveSubtractive",
    ],
    "Finish Shape": [
        "PartDesign_Fillet",
        "PartDesign_Chamfer",
        "PartDesign_Draft",
        "PartDesign_Thickness",
    ],
    "Transform Features": [
        "PartDesign_Mirrored",
        "PartDesign_LinearPattern",
        "PartDesign_PolarPattern",
        "PartDesign_MultiTransform",
    ],
    "Standalone and Surface Geometry": [
        "Part_Tube",
        "Part_Primitives",
        "Part_Builder",
        "Part_Extrude",
        "Part_Revolve",
        "Part_Mirror",
        "Part_Scale",
        "Part_MakeFace",
        "Part_RuledSurface",
        "Part_Loft",
        "Part_Sweep",
        "Part_Section",
        "Part_CrossSections",
        "Part_CompOffset",
        "Part_ProjectionOnSurface",
    ],
    "Boolean, Split, and Repair": [
        "Part_CompCompoundTools",
        "Part_Boolean",
        "Part_Cut",
        "Part_Fuse",
        "Part_Common",
        "Part_CompJoinFeatures",
        "Part_CompSplitFeatures",
        "Part_Defeaturing",
    ],
}

PART_MENU_COMMANDS = RETAINED_PART_COMMANDS - {
    "Part_CompCompoundTools",
    "Part_CompJoinFeatures",
    "Part_CompOffset",
    "Part_CompSplitFeatures",
}

COMPOSITE_ACTION_TARGETS = {
    "Part_CompCompoundTools": [
        "Part_Compound",
        "Part_ExplodeCompound",
        "Part_CompoundFilter",
    ],
    "Part_CompJoinFeatures": ["Part_JoinConnect", "Part_JoinEmbed", "Part_JoinCutout"],
    "Part_CompOffset": ["Part_Offset", "Part_Offset2D"],
    "Part_CompSplitFeatures": [
        "Part_BooleanFragments",
        "Part_SliceApart",
        "Part_Slice",
        "Part_XOR",
    ],
}

CANONICAL_COMMAND_LABELS = {
    "PartDesign_Pad": "Extrude — Add Material",
    "PartDesign_Pocket": "Extrude — Remove Material",
    "PartDesign_Revolution": "Revolve — Add Material",
    "PartDesign_Groove": "Revolve — Remove Material",
    "PartDesign_AdditiveLoft": "Loft — Add Material",
    "PartDesign_SubtractiveLoft": "Loft — Remove Material",
    "PartDesign_AdditivePipe": "Sweep — Add Material",
    "PartDesign_SubtractivePipe": "Sweep — Remove Material",
    "PartDesign_AdditiveHelix": "Helix Sweep — Add Material",
    "PartDesign_SubtractiveHelix": "Helix Sweep — Remove Material",
    "Part_Extrude": "Extrude Standalone Shape",
    "Part_Revolve": "Revolve Standalone Shape",
    "Part_Loft": "Loft Standalone Shape",
    "Part_Sweep": "Sweep Standalone Shape",
    "Part_Mirror": "Mirror Standalone Shape",
}


def _find_child_labels(parent, label):
    if parent.isHidden():
        return None
    if parent.text(0) == label:
        return {
            parent.child(index).text(0)
            for index in range(parent.childCount())
            if not parent.child(index).isHidden()
        }
    for index in range(parent.childCount()):
        if parent.child(index).isHidden():
            continue
        result = _find_child_labels(parent.child(index), label)
        if result is not None:
            return result
    return None


def _tree_child_labels(label, expected=None):
    last_result = None
    for _attempt in range(20):
        Gui.updateGui()
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(50, loop.quit)
        loop.exec()
        main_window = Gui.getMainWindow()
        if main_window is None:
            continue
        for tree in main_window.findChildren(QtGui.QTreeWidget):
            try:
                for index in range(tree.topLevelItemCount()):
                    result = _find_child_labels(tree.topLevelItem(index), label)
                    if result is not None:
                        last_result = result
                        if expected is None or expected.issubset(result):
                            return result
            except RuntimeError:
                # The model tree can replace item wrappers during a pending refresh.
                continue
    return last_result


def _tree_labels():
    labels = []

    def collect(item):
        if item.isHidden():
            return
        labels.append(item.text(0))
        for index in range(item.childCount()):
            collect(item.child(index))

    main_window = Gui.getMainWindow()
    if main_window is None:
        return labels
    for tree in main_window.findChildren(QtGui.QTreeWidget):
        try:
            for index in range(tree.topLevelItemCount()):
                collect(tree.topLevelItem(index))
        except RuntimeError:
            continue
    return labels


class TestConsolidatedPartWorkbench(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("ConsolidatedPartWorkbench")
        Gui.activateView("Gui::View3DInventor", True)
        self.body = self.document.addObject("PartDesign::Body", "ModelBody")
        self.body.Label = "Consolidated Model"
        Gui.activeView().setActiveObject("pdbody", self.body)

    def tearDown(self):
        Gui.Selection.clearSelection()
        if App.getDocument("ConsolidatedPartWorkbench") is not None:
            App.closeDocument("ConsolidatedPartWorkbench")

    def test_part_commands_are_loaded_without_part_workbench(self):
        self.assertIn("PartDesignWorkbench", Gui.listWorkbenches())
        self.assertNotIn("PartWorkbench", Gui.listWorkbenches())
        expected = (
            RETAINED_PART_COMMANDS
            | RETIRED_REDUNDANT_COMMANDS
            | LEGACY_COMPATIBILITY_COMMANDS
        )
        self.assertEqual(expected - set(Gui.listCommands()), set())

    def test_part_command_icons_and_toolbars_render(self):
        for command_name, expected_label in CANONICAL_COMMAND_LABELS.items():
            self.assertEqual(
                Gui.Command.get(command_name).getInfo()["menuText"], expected_label
            )

        for command_name in sorted(RETAINED_PART_COMMANDS):
            command = Gui.Command.get(command_name)
            self.assertIsNotNone(command, command_name)
            actions = command.getAction()
            self.assertTrue(actions, command_name)
            for action in actions:
                self.assertFalse(action.icon().isNull(), command_name)
                self.assertFalse(action.icon().pixmap(24, 24).isNull(), command_name)

        tolerance_info = Gui.Command.get("Part_ToleranceSet").getInfo()
        self.assertEqual(tolerance_info["pixmap"], "Part_ToleranceSet.svg")

        for command_name, target_commands in COMPOSITE_ACTION_TARGETS.items():
            actions = Gui.Command.get(command_name).getAction()
            self.assertEqual(len(actions), len(target_commands))
            for action, target_name in zip(actions, target_commands):
                target = Gui.Command.get(target_name)
                self.assertEqual(
                    action.text().replace("&", ""),
                    target.getInfo()["menuText"].replace("&", ""),
                )
                self.assertEqual(
                    action.icon().pixmap(24, 24).toImage(),
                    target.getAction()[0].icon().pixmap(24, 24).toImage(),
                    target_name,
                )

        toolbars = {
            toolbar.windowTitle(): toolbar
            for toolbar in Gui.getMainWindow().findChildren(QtGui.QToolBar)
        }
        surfaced_toolbar_commands = set()
        for title, expected_commands in MODEL_TOOLBARS.items():
            self.assertIn(title, toolbars)
            actual_commands = [
                action.data()
                for action in toolbars[title].actions()
                if not action.isSeparator()
            ]
            self.assertEqual(actual_commands, expected_commands, title)
            surfaced_toolbar_commands.update(actual_commands)
            for action in toolbars[title].actions():
                if not action.isSeparator():
                    self.assertFalse(
                        action.icon().pixmap(24, 24).isNull(), action.data()
                    )

        main_window = Gui.getMainWindow()
        menu_bar = main_window.menuBar()
        menu_actions = menu_bar.actions()
        model_menu_action = next(
            (
                action
                for action in menu_actions
                if action.text().replace("&", "") == "Part Design"
            ),
            None,
        )
        self.assertIsNotNone(model_menu_action)
        model_menu = model_menu_action.menu()
        self.assertIsNotNone(model_menu)

        menu_commands = set()

        def collect(menu):
            for action in menu.actions():
                if action.isSeparator():
                    continue
                if action.menu() is not None:
                    collect(action.menu())
                elif action.objectName():
                    menu_commands.add(action.objectName())

        collect(model_menu)
        self.assertTrue(PART_MENU_COMMANDS.issubset(menu_commands))
        surfaced_commands = menu_commands | surfaced_toolbar_commands
        self.assertEqual(RETIRED_REDUNDANT_COMMANDS & surfaced_commands, set())
        self.assertFalse(
            any(
                action.text().replace("&", "") == "Part Tools"
                for action in menu_actions
            )
        )

    def test_part_command_graph_stays_directly_owned_by_body(self):
        Gui.runCommand("Part_Box", 0)
        left = self.document.ActiveObject
        left.Label = "Part Input A"

        Gui.runCommand("Part_Box", 0)
        right = self.document.ActiveObject
        right.Label = "Part Input B"
        right.Placement.Base.x = 0.5

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(left)
        Gui.Selection.addSelection(right)
        Gui.runCommand("Part_Fuse", 0)
        result = self.document.ActiveObject
        result.Label = "Part Union Result"
        self.document.recompute()

        self.assertEqual(self.body.Tip, result)
        self.assertEqual(list(self.body.Group)[-3:], [left, right, result])
        self.assertEqual(left.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(right.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(result.getParentGeoFeatureGroup(), self.body)
        self.assertTrue(result.ViewObject.ShowInTree)
        self.assertTrue(
            {left, right, result}.issubset(set(self.body.ViewObject.claimChildren()))
        )

        QtGui.QApplication.processEvents()
        expected_features = {"Part Input A", "Part Input B", "Part Union Result"}
        body_children = _tree_child_labels(
            "Consolidated Model", {"Origin", "Features"}
        )
        self.assertIsNotNone(body_children, _tree_labels())
        self.assertTrue(
            {"Origin", "Features"}.issubset(body_children),
            (body_children, _tree_labels()),
        )
        feature_children = _tree_child_labels("Features", expected_features)
        self.assertIsNotNone(feature_children, _tree_labels())
        self.assertTrue(
            expected_features.issubset(feature_children),
            (feature_children, _tree_labels()),
        )
        nested_labels = _tree_child_labels("Part Union Result")
        self.assertIsNotNone(nested_labels)
        self.assertNotIn("Part Input A", nested_labels)
        self.assertNotIn("Part Input B", nested_labels)

    def test_existing_group_ownership_is_not_stolen(self):
        import PartDesignGui

        legacy_group = self.document.addObject(
            "App::DocumentObjectGroup", "LegacyGroup"
        )
        legacy_shape = self.document.addObject("Part::Feature", "LegacyShape")
        legacy_group.addObject(legacy_shape)
        QtGui.QApplication.processEvents()
        PartDesignGui.adoptPartResult(legacy_shape)

        self.assertIn(legacy_shape, legacy_group.Group)
        self.assertNotIn(legacy_shape, self.body.Group)

    def test_pending_transaction_never_steals_a_late_group_member(self):
        container = self.document.addObject("App::Part", "ForeignPart")
        self.document.openTransaction("Create grouped Part feature")
        grouped = self.document.addObject("Part::Feature", "GroupedFeature")

        # Exercise the event-loop race that used to adopt a half-constructed
        # result before its command established final ownership.
        QtGui.QApplication.processEvents()
        self.assertNotIn(grouped, self.body.Group)
        container.addObject(grouped)
        self.document.commitTransaction()
        QtGui.QApplication.processEvents()

        self.assertEqual(grouped.getParentGeoFeatureGroup(), container)
        self.assertNotIn(grouped, self.body.Group)

    def test_external_dependencies_keep_their_geo_feature_group(self):
        import PartDesignGui

        external_part = self.document.addObject("App::Part", "ExternalPart")
        dependency = self.document.addObject("Part::Box", "ExternalDependency")
        external_part.addObject(dependency)
        self.document.recompute()
        result = self.document.addObject("Part::Feature", "BodyResult")
        result.addProperty("App::PropertyLink", "Base")
        result.Base = dependency
        result.Shape = dependency.Shape.copy()

        adopted = PartDesignGui.adoptPartResult(result)

        self.assertEqual(dependency.getParentGeoFeatureGroup(), external_part)
        self.assertIsNone(adopted)
        self.assertIsNone(result.getParentGeoFeatureGroup())
        self.assertNotIn(dependency, self.body.Group)

    def test_origin_reference_is_not_treated_as_a_foreign_modeling_operand(self):
        import PartDesignGui

        source = self.document.addObject("Part::Box", "MirrorSource")
        self.body.addObject(source)
        mirror_plane = self.body.Origin.OriginFeatures[5]
        origin_owner = mirror_plane.getParentGeoFeatureGroup()

        result = self.document.addObject("Part::Mirroring", "OriginReferencedMirror")
        result.Source = source
        result.MirrorPlane = (mirror_plane, "")
        self.document.recompute()

        adopted = PartDesignGui.adoptPartResult(result)
        self.document.recompute()

        self.assertEqual(adopted, self.body)
        self.assertEqual(result.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(mirror_plane.getParentGeoFeatureGroup(), origin_owner)
        self.assertNotIn(mirror_plane, self.body.Group)
        self.assertFalse(result.Shape.isNull())
        self.assertEqual(result.getStatusString(), "Valid")

    def test_transaction_adopts_complete_unowned_graph_with_undo_redo(self):
        self.document.openTransaction("Create complete Part graph")
        profile = self.document.addObject("Part::Feature", "TransactionProfile")
        result = self.document.addObject("Part::Feature", "TransactionResult")
        result.addProperty("App::PropertyLink", "Base")
        result.Base = profile
        self.document.commitTransaction()

        self.assertEqual(profile.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(result.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(self.body.Group[-2:], [profile, result])

        self.document.undo()
        self.assertIsNone(self.document.getObject("TransactionProfile"))
        self.assertIsNone(self.document.getObject("TransactionResult"))
        self.document.redo()
        profile = self.document.getObject("TransactionProfile")
        result = self.document.getObject("TransactionResult")
        self.assertEqual(profile.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(result.getParentGeoFeatureGroup(), self.body)

    def test_pending_adoption_is_isolated_by_transaction_and_object_identity(self):
        self.document.openTransaction("First document Part result")
        first_result = self.document.addObject("Part::Feature", "QueuedFirst")

        other = App.newDocument("ConsolidatedPartOtherTransaction")
        self.addCleanup(
            lambda: (
                App.closeDocument("ConsolidatedPartOtherTransaction")
                if App.getDocument("ConsolidatedPartOtherTransaction") is not None
                else None
            )
        )
        Gui.activateView("Gui::View3DInventor", True)
        other_body = other.addObject("PartDesign::Body", "OtherBody")
        Gui.activeView().setActiveObject("pdbody", other_body)
        other.openTransaction("Second document Part result")
        other.addObject("Part::Feature", "QueuedOther")

        # Committing one independent transaction must neither adopt nor discard a result queued by
        # the other transaction.
        self.document.commitTransaction()
        self.assertEqual(first_result.getParentGeoFeatureGroup(), self.body)
        other_result = other.getObject("QueuedOther")
        self.assertIsNotNone(other_result)
        self.assertIsNone(other_result.getParentGeoFeatureGroup())

        # Aborting the second transaction must remove only its queue entry. Reusing the same object
        # name in a later transaction exercises ID-based lookup and stale-entry isolation.
        other.abortTransaction()
        self.assertIsNone(other.getObject("QueuedOther"))
        other.openTransaction("Replacement Part result")
        replacement = other.addObject("Part::Feature", "QueuedOther")
        other.commitTransaction()
        self.assertEqual(replacement.getParentGeoFeatureGroup(), other_body)

    def test_cross_body_boolean_keeps_each_operand_owner(self):
        from BOPTools.BOPFeatures import BOPFeatures

        left = self.document.addObject("Part::Box", "ActiveBodyOperand")
        self.body.addObject(left)
        other_body = self.document.addObject("PartDesign::Body", "OtherBody")
        right = self.document.addObject("Part::Box", "OtherBodyOperand")
        right.Placement.Base.x = 0.5
        other_body.addObject(right)
        self.document.recompute()

        result = BOPFeatures(self.document).make_fuse([left.Name, right.Name])
        self.document.recompute()
        QtGui.QApplication.processEvents()

        self.assertEqual(left.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(right.getParentGeoFeatureGroup(), other_body)
        self.assertIsNone(result.getParentGeoFeatureGroup())

    def test_legacy_dynamic_part_links_migrate_without_data_loss(self):
        from PartLinkScope import migrate_many_to_global, migrate_to_global

        target = self.document.addObject("App::FeaturePython", "LegacyLinkTarget")
        legacy = self.document.addObject("Part::FeaturePython", "LegacyLinkFeature")
        legacy.addProperty("App::PropertyLink", "Base", "Join", "First input")
        legacy.addProperty("App::PropertyLinkList", "Shapes", "Join", "All inputs")
        legacy.Base = target
        legacy.Shapes = [target]
        legacy.setEditorMode("Base", 2)
        legacy.setPropertyStatus("Base", "NoRecompute")
        expected_editor_mode = legacy.getEditorMode("Base")
        expected_property_status = legacy.getPropertyStatus("Base")

        migrate_many_to_global(legacy, "Base", "Shapes")

        self.assertEqual(legacy.getTypeIdOfProperty("Base"), "App::PropertyLinkGlobal")
        self.assertEqual(
            legacy.getTypeIdOfProperty("Shapes"), "App::PropertyLinkListGlobal"
        )
        self.assertEqual(legacy.Base, target)
        self.assertEqual(legacy.Shapes, [target])
        self.assertEqual(legacy.getGroupOfProperty("Base"), "Join")
        self.assertEqual(legacy.getDocumentationOfProperty("Base"), "First input")
        self.assertEqual(legacy.getEditorMode("Base"), expected_editor_mode)
        self.assertEqual(legacy.getPropertyStatus("Base"), expected_property_status)
        self.assertFalse(migrate_to_global(legacy, "Base"))
