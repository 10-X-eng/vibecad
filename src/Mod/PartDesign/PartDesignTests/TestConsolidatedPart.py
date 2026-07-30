# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI integration tests for the consolidated Part Design modeling surface."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
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
    "Standard Components": [
        "VibeCAD_InsertStandardFastener",
        "VibeCAD_EditStandardFastener",
        "VibeCAD_CreateMatchingFastenerHole",
        "VibeCAD_AttachStandardFastener",
    ],
}

MODEL_MENU_ONLY_COMMANDS = {
    "Sketcher_ReorientSketch",
    "Sketcher_MergeSketches",
    "Sketcher_MirrorSketch",
    "PartDesign_Point",
    "PartDesign_Line",
    "PartDesign_Plane",
    "PartDesign_CoordinateSystem",
    "PartDesign_ShapeBinder",
    "Materials_InspectAppearance",
    "Materials_InspectMaterial",
    "Part_BoxSelection",
    "Part_ColorPerFace",
    "Part_EditAttachment",
    "Part_ElementCopy",
    "Part_MakeSolid",
    "Part_PointsFromMesh",
    "Part_RefineShape",
    "Part_ReverseShape",
    "Part_SectionCut",
    "Part_ShapeFromMesh",
    "Part_SimpleCopy",
    "Part_ToleranceSet",
    "Part_TransformedCopy",
    "PartDesign_InvoluteGear",
    "PartDesign_Sprocket",
    "PartDesign_DuplicateSelection",
}

MODEL_OPTIONAL_MENU_COMMANDS = {"PartDesign_WizardShaft"}

STANDARD_TOOLBAR_TITLES = {
    "File",
    "Edit",
    "Clipboard",
    "Workbench",
    "Macro",
    "View",
    "Individual Views",
    "Structure",
    "Help",
}

COMPOSITE_ACTION_TARGETS = {
    "PartDesign_CompSketches": [
        "PartDesign_NewSketch",
        "Sketcher_MapSketch",
        "Sketcher_EditSketch",
    ],
    "PartDesign_CompPrimitiveAdditive": [
        "PartDesign_AdditiveBox",
        "PartDesign_AdditiveCylinder",
        "PartDesign_AdditiveSphere",
        "PartDesign_AdditiveCone",
        "PartDesign_AdditiveEllipsoid",
        "PartDesign_AdditiveTorus",
        "PartDesign_AdditivePrism",
        "PartDesign_AdditiveWedge",
    ],
    "PartDesign_CompPrimitiveSubtractive": [
        "PartDesign_SubtractiveBox",
        "PartDesign_SubtractiveCylinder",
        "PartDesign_SubtractiveSphere",
        "PartDesign_SubtractiveCone",
        "PartDesign_SubtractiveEllipsoid",
        "PartDesign_SubtractiveTorus",
        "PartDesign_SubtractivePrism",
        "PartDesign_SubtractiveWedge",
    ],
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

MODEL_COMMAND_TIMELINE_BEHAVIOR = {
    "PartDesign_Body": frozenset({"structural"}),
    "PartDesign_CompSketches": frozenset({"read-only"}),
    "PartDesign_NewSketch": frozenset({"operation", "standalone"}),
    "Sketcher_MapSketch": frozenset({"in-place"}),
    "Sketcher_EditSketch": frozenset({"in-place"}),
    "Sketcher_ValidateSketch": frozenset({"in-place"}),
    "Part_CheckGeometry": frozenset({"read-only"}),
    "PartDesign_SubShapeBinder": frozenset({"operation", "source-preserving"}),
    "PartDesign_Clone": frozenset({"operation", "source-preserving"}),
    "PartDesign_Pad": frozenset({"operation", "replacement"}),
    "PartDesign_Revolution": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveLoft": frozenset({"operation", "replacement"}),
    "PartDesign_AdditivePipe": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveHelix": frozenset({"operation", "replacement"}),
    "PartDesign_CompPrimitiveAdditive": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveBox": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveCylinder": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveSphere": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveCone": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveEllipsoid": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveTorus": frozenset({"operation", "replacement"}),
    "PartDesign_AdditivePrism": frozenset({"operation", "replacement"}),
    "PartDesign_AdditiveWedge": frozenset({"operation", "replacement"}),
    "PartDesign_Pocket": frozenset({"operation", "replacement"}),
    "PartDesign_Hole": frozenset({"operation", "replacement"}),
    "PartDesign_Groove": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveLoft": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractivePipe": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveHelix": frozenset({"operation", "replacement"}),
    "PartDesign_CompPrimitiveSubtractive": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveBox": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveCylinder": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveSphere": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveCone": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveEllipsoid": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveTorus": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractivePrism": frozenset({"operation", "replacement"}),
    "PartDesign_SubtractiveWedge": frozenset({"operation", "replacement"}),
    "PartDesign_Fillet": frozenset({"operation", "replacement"}),
    "PartDesign_Chamfer": frozenset({"operation", "replacement"}),
    "PartDesign_Draft": frozenset({"operation", "replacement"}),
    "PartDesign_Thickness": frozenset({"operation", "replacement"}),
    "PartDesign_Mirrored": frozenset({"operation", "replacement"}),
    "PartDesign_LinearPattern": frozenset({"operation", "replacement"}),
    "PartDesign_PolarPattern": frozenset({"operation", "replacement"}),
    "PartDesign_MultiTransform": frozenset({"operation", "replacement"}),
    "Part_Tube": frozenset({"operation", "standalone"}),
    "Part_Primitives": frozenset({"operation", "standalone"}),
    "Part_Builder": frozenset({"operation", "source-preserving"}),
    "Part_Extrude": frozenset({"operation", "replacement"}),
    "Part_Revolve": frozenset({"operation", "replacement"}),
    "Part_Mirror": frozenset({"operation", "replacement"}),
    "Part_Scale": frozenset({"operation", "replacement"}),
    "Part_MakeFace": frozenset({"operation", "replacement"}),
    "Part_RuledSurface": frozenset({"operation", "source-preserving"}),
    "Part_Loft": frozenset({"operation", "replacement"}),
    "Part_Sweep": frozenset({"operation", "replacement"}),
    "Part_Section": frozenset({"operation", "replacement"}),
    "Part_CrossSections": frozenset({"operation", "source-preserving"}),
    "Part_CompOffset": frozenset({"read-only"}),
    "Part_Offset": frozenset({"operation", "replacement"}),
    "Part_Offset2D": frozenset({"operation", "replacement"}),
    "Part_ProjectionOnSurface": frozenset({"operation", "source-preserving"}),
    "Part_CompCompoundTools": frozenset({"read-only"}),
    "Part_Compound": frozenset({"operation", "replacement"}),
    "Part_ExplodeCompound": frozenset({"operation", "replacement"}),
    "Part_CompoundFilter": frozenset({"operation", "replacement"}),
    "Part_Boolean": frozenset({"operation", "replacement"}),
    "Part_Cut": frozenset({"operation", "replacement"}),
    "Part_Fuse": frozenset({"operation", "replacement"}),
    "Part_Common": frozenset({"operation", "replacement"}),
    "Part_CompJoinFeatures": frozenset({"read-only"}),
    "Part_JoinConnect": frozenset({"operation", "replacement"}),
    "Part_JoinEmbed": frozenset({"operation", "replacement"}),
    "Part_JoinCutout": frozenset({"operation", "replacement"}),
    "Part_CompSplitFeatures": frozenset({"read-only"}),
    "Part_BooleanFragments": frozenset({"operation", "replacement"}),
    "Part_SliceApart": frozenset({"operation", "replacement"}),
    "Part_Slice": frozenset({"operation", "replacement"}),
    "Part_XOR": frozenset({"operation", "replacement"}),
    "Part_Defeaturing": frozenset({"operation", "replacement"}),
    "VibeCAD_InsertStandardFastener": frozenset({"operation", "standalone"}),
    "VibeCAD_EditStandardFastener": frozenset({"in-place"}),
    "VibeCAD_CreateMatchingFastenerHole": frozenset(
        {"operation", "body-history-step"}
    ),
    "VibeCAD_AttachStandardFastener": frozenset({"in-place"}),
    "Sketcher_ReorientSketch": frozenset({"in-place"}),
    "Sketcher_MergeSketches": frozenset({"operation", "source-preserving"}),
    "Sketcher_MirrorSketch": frozenset({"operation", "source-preserving"}),
    "PartDesign_Point": frozenset({"operation", "source-preserving"}),
    "PartDesign_Line": frozenset({"operation", "source-preserving"}),
    "PartDesign_Plane": frozenset({"operation", "source-preserving"}),
    "PartDesign_CoordinateSystem": frozenset({"operation", "source-preserving"}),
    "PartDesign_ShapeBinder": frozenset({"operation", "source-preserving"}),
    "Materials_InspectAppearance": frozenset({"read-only"}),
    "Materials_InspectMaterial": frozenset({"read-only"}),
    "Part_BoxSelection": frozenset({"read-only"}),
    "Part_ColorPerFace": frozenset({"in-place"}),
    "Part_EditAttachment": frozenset({"in-place"}),
    "Part_ElementCopy": frozenset({"operation", "source-preserving"}),
    "Part_MakeSolid": frozenset({"operation", "replacement"}),
    "Part_PointsFromMesh": frozenset({"operation", "source-preserving"}),
    "Part_RefineShape": frozenset({"operation", "replacement"}),
    "Part_ReverseShape": frozenset({"operation", "replacement"}),
    "Part_SectionCut": frozenset({"operation", "replacement"}),
    "Part_ShapeFromMesh": frozenset({"operation", "replacement"}),
    "Part_SimpleCopy": frozenset({"operation", "source-preserving"}),
    "Part_ToleranceSet": frozenset({"operation", "replacement"}),
    "Part_TransformedCopy": frozenset({"operation", "source-preserving"}),
    "PartDesign_InvoluteGear": frozenset({"operation", "standalone"}),
    "PartDesign_Sprocket": frozenset({"operation", "standalone"}),
    "PartDesign_DuplicateSelection": frozenset({"operation", "source-preserving"}),
    "PartDesign_WizardShaft": frozenset({"operation", "standalone"}),
}

# These controls are injected by VibeCADRibbon into every CAD domain.  They
# are intentionally audited separately from the Model workbench's own action
# graph, so the workbench matrix neither omits nor claims ownership of them.
SHARED_RIBBON_TIMELINE_BEHAVIOR = {
    "Std_ViewFitAll": frozenset({"read-only"}),
    "Std_ViewIsometric": frozenset({"read-only"}),
    "VibeCAD_ToggleGrid": frozenset({"read-only"}),
    # TestRibbonInspectView exercises each task's Save Result action and the
    # resulting durable, source-linked Measure::Result history operation.
    "Std_Measure": frozenset({"operation", "source-preserving"}),
    "Std_MassProperties": frozenset({"operation", "source-preserving"}),
    "Inspection_VisualInspection": frozenset({"operation", "replacement"}),
    "Inspection_InspectElement": frozenset({"read-only"}),
    "Part_CheckGeometry": frozenset({"read-only"}),
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


def _timeline_object_names():
    main_window = Gui.getMainWindow()
    if main_window is None:
        return set()
    timeline = main_window.findChild(
        QtGui.QListWidget, "VibeCADFeatureTimelineItems"
    )
    if timeline is None:
        return set()
    return {
        item.data(QtCore.Qt.UserRole)
        for item in (
            timeline.item(index) for index in range(timeline.count())
        )
        if item.data(QtCore.Qt.UserRole)
    }


def _action_command_id(action):
    for property_name in (
        "VibeCADCommandId",
        "CommandName",
        "FreeCADCommandGroupChildId",
    ):
        value = action.property(property_name)
        if value is None:
            continue
        if isinstance(value, QtCore.QByteArray):
            command_id = bytes(value).decode("utf-8")
        else:
            command_id = str(value)
        command_id = command_id.strip()
        if command_id:
            return command_id
    value = action.data()
    if value is not None:
        if isinstance(value, QtCore.QByteArray):
            command_id = bytes(value).decode("utf-8")
        else:
            command_id = str(value)
        command_id = command_id.strip()
        if command_id:
            return command_id
    return action.objectName().strip()


def _collect_named_menu_commands(menu_title):
    menu_action = next(
        (
            action
            for action in Gui.getMainWindow().menuBar().actions()
            if action.text().replace("&", "") == menu_title
        ),
        None,
    )
    if menu_action is None or menu_action.menu() is None:
        return None

    commands = set()

    def collect(menu):
        for action in menu.actions():
            if action.isSeparator():
                continue
            if action.menu() is not None:
                collect(action.menu())
                continue
            command_id = _action_command_id(action)
            if command_id:
                commands.add(command_id)

    collect(menu_action.menu())
    return commands


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

    def test_model_command_timeline_matrix_is_exhaustive_and_disjoint(self):
        expected_toolbars = {
            title: tuple(commands)
            for title, commands in MODEL_TOOLBARS.items()
        }
        all_toolbar_items = Gui.activeWorkbench().getToolbarItems()
        live_toolbar_items = {
            title: tuple(
                command
                for command in commands
                if command != "Separator"
            )
            for title, commands in all_toolbar_items.items()
            if title not in STANDARD_TOOLBAR_TITLES
        }
        self.assertEqual(live_toolbar_items, expected_toolbars)
        top_level_commands = {
            command
            for commands in live_toolbar_items.values()
            for command in commands
        }
        live_composites = {}
        for command_name in sorted(top_level_commands):
            command = Gui.Command.get(command_name)
            self.assertIsNotNone(command, command_name)
            child_ids = tuple(
                _action_command_id(action)
                for action in command.getAction()
                if not action.isSeparator()
            )
            if len(child_ids) > 1:
                live_composites[command_name] = child_ids

        self.assertEqual(
            live_composites,
            {
                parent: tuple(children)
                for parent, children in COMPOSITE_ACTION_TARGETS.items()
            },
        )
        live_menu = set()
        for menu_title in ("Sketch", "Part Design"):
            menu_commands = _collect_named_menu_commands(menu_title)
            self.assertIsNotNone(menu_commands, menu_title)
            live_menu.update(menu_commands)
        edit_commands = _collect_named_menu_commands("Edit")
        self.assertIsNotNone(edit_commands)
        live_menu.update(
            edit_commands & {"PartDesign_DuplicateSelection"}
        )

        surfaced_commands = top_level_commands | live_menu
        surfaced_commands.update(
            child for children in live_composites.values() for child in children
        )
        optional_registered = (
            MODEL_OPTIONAL_MENU_COMMANDS & set(Gui.listCommands())
        )
        expected_surface = (
            set(MODEL_COMMAND_TIMELINE_BEHAVIOR)
            - MODEL_OPTIONAL_MENU_COMMANDS
            | optional_registered
        )
        expected_menu = expected_surface - set(COMPOSITE_ACTION_TARGETS)
        self.assertEqual(live_menu, expected_menu)
        self.assertEqual(surfaced_commands, expected_surface)
        self.assertEqual(len(expected_surface), 111 + len(optional_registered))
        composite_children = {
            child
            for children in COMPOSITE_ACTION_TARGETS.values()
            for child in children
        }
        self.assertFalse(
            (expected_surface - composite_children)
            - set(Gui.listCommands())
        )
        self.assertEqual(len(MODEL_COMMAND_TIMELINE_BEHAVIOR), 112)
        self.assertEqual(
            set(MODEL_COMMAND_TIMELINE_BEHAVIOR),
            expected_surface
            | MODEL_OPTIONAL_MENU_COMMANDS,
        )

        primary_behaviors = {
            "structural",
            "standalone",
            "source-preserving",
            "replacement",
            "body-history-step",
            "in-place",
            "read-only",
        }
        operation_behaviors = {
            "standalone",
            "source-preserving",
            "replacement",
            "body-history-step",
        }
        for command, behaviors in MODEL_COMMAND_TIMELINE_BEHAVIOR.items():
            with self.subTest(command=command):
                primary = behaviors & primary_behaviors
                self.assertEqual(len(primary), 1)
                self.assertFalse(behaviors - primary_behaviors - {"operation"})
                self.assertEqual(
                    "operation" in behaviors,
                    bool(primary & operation_behaviors),
                )

        self.assertEqual(
            set(SHARED_RIBBON_TIMELINE_BEHAVIOR),
            {
                "Std_ViewFitAll",
                "Std_ViewIsometric",
                "VibeCAD_ToggleGrid",
                "Std_Measure",
                "Std_MassProperties",
                "Inspection_VisualInspection",
                "Inspection_InspectElement",
                "Part_CheckGeometry",
            },
        )
        for command, behaviors in SHARED_RIBBON_TIMELINE_BEHAVIOR.items():
            with self.subTest(shared_command=command):
                primary = behaviors & primary_behaviors
                self.assertEqual(len(primary), 1)
                self.assertEqual(
                    "operation" in behaviors,
                    bool(primary & operation_behaviors),
                )

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
                self.assertEqual(action.objectName(), target_name)
                target = Gui.Command.get(target_name)
                if target is None:
                    self.assertFalse(
                        action.icon().pixmap(24, 24).isNull(),
                        target_name,
                    )
                else:
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
        for menu_title in ("Sketch", "Part Design"):
            commands = _collect_named_menu_commands(menu_title)
            self.assertIsNotNone(commands, menu_title)
            menu_commands.update(commands)
        expected_menu_commands = (
            MODEL_MENU_ONLY_COMMANDS
            - {"PartDesign_DuplicateSelection"}
        )
        self.assertEqual(
            expected_menu_commands - menu_commands,
            set(),
        )
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
        expected_names = {left.Name, right.Name, result.Name}
        timeline_names = None
        for _attempt in range(20):
            Gui.updateGui()
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(50, loop.quit)
            loop.exec()
            timeline_names = _timeline_object_names()
            if expected_names.issubset(timeline_names):
                break
        self.assertTrue(
            expected_names.issubset(timeline_names),
            (timeline_names, expected_names),
        )

        bodies_children = _tree_child_labels(
            "Bodies", {"Consolidated Model"}
        )
        self.assertIsNotNone(bodies_children, _tree_labels())
        self.assertIn("Consolidated Model", bodies_children)

        body_children = _tree_child_labels("Consolidated Model")
        self.assertIsNotNone(body_children, _tree_labels())
        self.assertEqual(body_children, set())
        self.assertNotIn("Features", body_children)

        reference_children = _tree_child_labels("References", {"Origin"})
        self.assertIsNotNone(reference_children, _tree_labels())
        self.assertIn("Origin", reference_children)
        self.assertTrue(
            expected_features.isdisjoint(set(_tree_labels())),
            _tree_labels(),
        )

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
        profile.Shape = Part.makeBox(4, 4, 2)
        result = self.document.addObject("Part::Feature", "TransactionResult")
        result.addProperty("App::PropertyLink", "Base")
        result.Base = profile
        result.Shape = profile.Shape.copy()
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

    def test_transaction_uses_dependency_body_without_changing_active_body(self):
        selected_body = self.document.addObject(
            "PartDesign::Body", "SelectedTransactionBody"
        )
        selected_feature = self.document.addObject(
            "Part::Box", "SelectedTransactionFeature"
        )
        selected_feature.Length = 4
        selected_feature.Width = 4
        selected_feature.Height = 4
        selected_body.addObject(selected_feature)
        self.document.recompute()

        Gui.activeView().setActiveObject("pdbody", self.body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(selected_feature)

        self.document.openTransaction("Create result for selected Body")
        result = self.document.addObject("Part::Feature", "SelectedBodyResult")
        result.addProperty("App::PropertyLink", "Base")
        result.Base = selected_feature
        result.Shape = selected_feature.Shape.copy()
        self.document.commitTransaction()

        self.assertEqual(result.getParentGeoFeatureGroup(), selected_body)
        self.assertEqual(selected_body.Tip, result)
        self.assertEqual(
            Gui.activeView().getActiveObject("pdbody"),
            self.body,
        )

    def test_pending_adoption_is_isolated_by_transaction_and_object_identity(self):
        self.document.openTransaction("First document Part result")
        first_result = self.document.addObject("Part::Feature", "QueuedFirst")
        first_result.Shape = Part.makeBox(4, 4, 4)

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
        other_result = other.addObject("Part::Feature", "QueuedOther")
        other_result.Shape = Part.makeBox(3, 3, 3)

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
        replacement.Shape = Part.makeBox(2, 2, 2)
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
