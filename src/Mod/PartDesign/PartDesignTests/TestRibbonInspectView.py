# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD contracts for shared inspection and viewport ribbon tools."""

import hashlib
from pathlib import Path
import re
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
from PySide import QtCore, QtGui
from pivy import coin

SHIPPED_RIBBON_DOMAINS = (
    ("Model", "PartDesignWorkbench"),
    ("Assemble", "AssemblyWorkbench"),
    ("Mesh", "MeshWorkbench"),
    ("Analyze", "FemWorkbench"),
    ("Manufacture", "CAMWorkbench"),
    ("Drawing", "TechDrawWorkbench"),
    ("Parameters", "SpreadsheetWorkbench"),
)

INSPECTION_COMMANDS = (
    "Std_Measure",
    "Std_MassProperties",
    "Inspection_VisualInspection",
    "Inspection_InspectElement",
    "Part_CheckGeometry",
)

VIEW_COMMANDS = (
    "Std_ViewFitAll",
    "Std_ViewIsometric",
    "VibeCAD_ToggleGrid",
)

SHARED_RIBBON_TIMELINE_BEHAVIOR = {
    "Std_ViewFitAll": frozenset({"read-only"}),
    "Std_ViewIsometric": frozenset({"read-only"}),
    "VibeCAD_ToggleGrid": frozenset({"read-only"}),
    "Std_Measure": frozenset({"operation", "source-preserving"}),
    "Std_MassProperties": frozenset({"operation", "source-preserving"}),
    "Inspection_VisualInspection": frozenset({"operation", "replacement"}),
    "Inspection_InspectElement": frozenset({"read-only"}),
    "Part_CheckGeometry": frozenset({"read-only"}),
}

SKETCH_SETUP_COMMANDS = (
    "Sketcher_NewSketch",
    "Sketcher_EditSketch",
    "Sketcher_MapSketch",
    "Sketcher_ReorientSketch",
    "Sketcher_ValidateSketch",
    "Sketcher_MergeSketches",
    "Sketcher_MirrorSketch",
)

SKETCH_EDIT_GROUPS = {
    "Finish": (
        "Sketcher_LeaveSketch",
        "Sketcher_CancelSketch",
        "Sketcher_ViewSketch",
        "Sketcher_ViewSection",
    ),
    "Geometry": (
        "Sketcher_CreatePoint",
        "Sketcher_CompLine",
        "Sketcher_CompCreateArc",
        "Sketcher_CompCreateConic",
        "Sketcher_CompCreateRectangles",
        "Sketcher_CompCreateRegularPolygon",
        "Sketcher_CompSlot",
        "Sketcher_CompCreateBSpline",
        "Sketcher_CreateText",
        "Sketcher_ToggleConstruction",
    ),
    "Constraints": (
        "Sketcher_CompDimensionTools",
        "Sketcher_ConstrainCoincidentUnified",
        "Sketcher_CompHorVer",
        "Sketcher_ConstrainParallel",
        "Sketcher_ConstrainPerpendicular",
        "Sketcher_ConstrainTangent",
        "Sketcher_ConstrainEqual",
        "Sketcher_ConstrainSymmetric",
        "Sketcher_ConstrainBlock",
        "Sketcher_ConstrainGroup",
        "Sketcher_CompToggleConstraints",
    ),
    "Modify": (
        "Sketcher_CompCreateFillets",
        "Sketcher_CompCurveEdition",
        "Sketcher_CompExternal",
        "Sketcher_CarbonCopy",
        "Sketcher_Translate",
        "Sketcher_Rotate",
        "Sketcher_Scale",
        "Sketcher_Offset",
        "Sketcher_Symmetry",
        "Sketcher_RemoveAxesAlignment",
    ),
    "B-Spline": (
        "Sketcher_BSplineConvertToNURBS",
        "Sketcher_BSplineIncreaseDegree",
        "Sketcher_BSplineDecreaseDegree",
        "Sketcher_CompModifyKnotMultiplicity",
        "Sketcher_BSplineInsertKnot",
        "Sketcher_JoinCurves",
    ),
    "Visual": (
        "Sketcher_SelectConstraints",
        "Sketcher_SelectElementsAssociatedWithConstraints",
        "Sketcher_ArcOverlay",
        "Sketcher_CompBSplineShowHideGeometryInformation",
        "Sketcher_RestoreInternalAlignmentGeometry",
        "Sketcher_SwitchVirtualSpace",
    ),
}


def _function_body(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"Unterminated function: {signature}")


def _ribbon_source_path():
    here = Path(__file__).resolve()
    candidates = []
    for parent in here.parents:
        candidates.extend(
            (
                parent / "src" / "Gui" / "VibeCADRibbon.cpp",
                parent / "Gui" / "VibeCADRibbon.cpp",
            )
        )
    return next((path for path in candidates if path.is_file()), None)


class TestRibbonInspectView(unittest.TestCase):
    """Inspection tools must own previews/results and leave model state exact."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")

        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("RibbonInspectView")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)

        self.body = self.document.addObject("PartDesign::Body", "ProductBody")
        self.body.Label = "Product Body"
        self.tip = self.body.newObject("PartDesign::Feature", "CurrentResult")
        self.tip.Label = "Current Result"
        self.tip.Shape = Part.makeBox(10, 12, 14)
        self.body.Tip = self.tip
        self.document.recompute()
        self._process_events()

    def tearDown(self):
        if App.GuiUp and Gui.activeDocument() is not None:
            if Gui.Control.activeDialog():
                try:
                    Gui.Control.activeTaskDialog().reject()
                except (AttributeError, RuntimeError):
                    Gui.Control.closeDialog()
                self._process_events()

            try:
                viewer = Gui.activeDocument().activeView().getViewer()
                if viewer.isRedirectedToSceneGraph():
                    self._send_inspection_escape()
            except (AttributeError, RuntimeError):
                pass

        Gui.Selection.clearSelection()
        if App.getDocument("RibbonInspectView") is not None:
            App.closeDocument("RibbonInspectView")
        self._process_events()

    @staticmethod
    def _process_events(wait_ms=50):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        if wait_ms:
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()

    def _wait_until(self, predicate, timeout_ms=5000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            self._process_events(20)
            try:
                if predicate():
                    return True
            except RuntimeError:
                # Workbench and document changes may replace short-lived Qt
                # wrappers while the persistent shell widgets refresh.
                pass
        return False

    def _new_body(self, name, offset):
        body = self.document.addObject("PartDesign::Body", name)
        feature = body.newObject("PartDesign::Feature", f"{name}Result")
        feature.Label = f"{name} Result"
        feature.Shape = Part.makeBox(6, 7, 8, App.Vector(offset, 0, 0))
        body.Tip = feature
        self.document.recompute()
        return body, feature

    @staticmethod
    def _ribbon_group(title):
        return Gui.getMainWindow().findChild(
            QtGui.QFrame,
            "VibeCADRibbonGroup_"
            + "".join(character if character.isalnum() else "_" for character in title),
        )

    def _ribbon_group_actions(self, title):
        group = self._ribbon_group(title)
        self.assertIsNotNone(group, title)
        menu_button = group.findChild(
            QtGui.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        self.assertIsNotNone(menu_button, title)
        self.assertIsNotNone(menu_button.menu(), title)
        actions = [
            action
            for action in menu_button.menu().actions()
            if not action.isSeparator()
        ]
        by_command = {
            str(action.property("VibeCADCommandId")): action for action in actions
        }
        self.assertEqual(
            len(by_command),
            len(actions),
            f"{title} contains a duplicate command action",
        )
        return by_command

    def _ribbon_group_commands(self, title):
        return tuple(self._ribbon_group_actions(title))

    def _document_snapshot(self):
        def shape_fingerprint(obj):
            if not hasattr(obj, "Shape") or obj.Shape.isNull():
                return None
            brep = obj.Shape.exportBrepToString()
            return hashlib.sha256(brep.encode("utf-8")).hexdigest()

        return (
            tuple(
                (
                    obj.Name,
                    obj.TypeId,
                    shape_fingerprint(obj),
                    bool(obj.ViewObject.Visibility),
                )
                for obj in self.document.Objects
                if getattr(obj, "ViewObject", None) is not None
            ),
            tuple(obj.Name for obj in self.body.Group),
            self.body.Tip.Name if self.body.Tip is not None else None,
            bool(self.document.HasPendingTransaction),
            tuple(self.document.UndoNames),
            int(self.document.UndoCount),
            int(self.document.RedoCount),
        )

    @staticmethod
    def _selection_snapshot():
        return tuple(
            (item.ObjectName, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        )

    def _task_button(self, *standards):
        self._process_events()
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            for standard in standards:
                button = button_box.button(standard)
                if button is not None and button.isVisible() and button.isEnabled():
                    return button
        return None

    def _close_task(self, command_name):
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        button = self._task_button(
            QtGui.QDialogButtonBox.Abort,
            QtGui.QDialogButtonBox.Cancel,
            QtGui.QDialogButtonBox.Close,
        )
        self.assertIsNotNone(
            button, f"{command_name} has no usable close/cancel button"
        )
        button.click()
        self._process_events(100)
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _run_geometry_check(self):
        Gui.runCommand("Part_CheckGeometry")
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        run_button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(run_button)
        run_button.click()
        self._process_events(250)

        result_view = next(
            (
                tree
                for tree in Gui.getMainWindow().findChildren(QtGui.QTreeView)
                if tree.isVisible()
                and tree.model() is not None
                and tree.model().headerData(
                    0, QtCore.Qt.Horizontal, QtCore.Qt.DisplayRole
                )
                == "Name"
                and tree.model().rowCount() > 0
            ),
            None,
        )
        self.assertIsNotNone(result_view)
        return str(result_view.model().index(0, 0).data())

    def _send_inspection_escape(self):
        event = coin.SoKeyboardEvent()
        event.setKey(coin.SoKeyboardEvent.ESCAPE)
        event.setState(coin.SoButtonEvent.DOWN)
        Gui.activeDocument().activeView().getViewer().getSoEventManager().processEvent(
            event
        )
        self._process_events(100)

    def _assert_saved_result_follows_document_history(self, result):
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        operations = list(timeline.Operations)
        self.assertIn(result, operations)
        result_index = operations.index(result)
        end_position = len(operations)
        previous = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        finish = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(finish)

        for _ in range(len(operations) + 1):
            if int(timeline.Position) <= result_index:
                break
            previous.click()
            self._process_events(100)
        self.assertLessEqual(int(timeline.Position), result_index)
        self.assertFalse(result.ViewObject.Visibility)

        finish.click()
        self._process_events(150)
        self.assertEqual(int(timeline.Position), end_position)
        self.assertTrue(result.ViewObject.Visibility)

    def test_shared_ribbon_source_and_history_contract_are_exhaustive(self):
        source_path = _ribbon_source_path()
        if source_path is None:
            self.skipTest("VibeCAD ribbon source is not present in this installation")
        source = source_path.read_text(encoding="utf-8")

        domains_start = source.index(
            "constexpr std::array<DomainDefinition, 7> domains"
        )
        domains_end = source.index("}};", domains_start)
        actual_domains = tuple(
            re.findall(
                r'\{"([^"]+)",\s*"([^"]+)"\}',
                source[domains_start:domains_end],
            )
        )
        self.assertEqual(actual_domains, SHIPPED_RIBBON_DOMAINS)

        inspection_source = _function_body(
            source,
            "const std::vector<QString>& sharedInspectionCommands()",
        )
        self.assertEqual(
            tuple(
                re.findall(
                    r'QStringLiteral\("([^"]+)"\)',
                    inspection_source,
                )
            ),
            INSPECTION_COMMANDS,
        )

        setup_source = _function_body(
            source,
            "std::vector<GroupDefinition> sketchSetupGroups()",
        )
        self.assertEqual(
            tuple(re.findall(r'"(Sketcher_[^"]+)"', setup_source)),
            SKETCH_SETUP_COMMANDS,
        )
        edit_source = _function_body(
            source,
            "std::vector<GroupDefinition> sketchGroups()",
        )
        actual_edit_commands = tuple(re.findall(r'"(Sketcher_[^"]+)"', edit_source))
        self.assertEqual(
            actual_edit_commands,
            tuple(
                command
                for commands in SKETCH_EDIT_GROUPS.values()
                for command in commands
            ),
        )

        rebuild_source = _function_body(source, "void rebuildPage()")
        view_initializer = re.search(
            r"CommandEntries viewEntries = resolveUniqueEntries\(\s*"
            r"\{(?P<commands>[^}]*)\}\s*\);",
            rebuild_source,
            re.DOTALL,
        )
        self.assertIsNotNone(view_initializer)
        self.assertEqual(
            tuple(
                re.findall(
                    r'"([^"]+)"',
                    view_initializer.group("commands"),
                )
            ),
            VIEW_COMMANDS,
        )
        self.assertIn(
            "resolveUniqueEntries(sharedInspectionCommands())",
            rebuild_source,
        )
        self.assertIn(
            "if (!inSketchEdit && !inspectionAdded)",
            rebuild_source,
        )
        self.assertIn(
            "if (!inSketchEdit) {\n"
            "                std::erase_if(domainCommands, "
            "isSharedInspectionCommand);",
            rebuild_source,
        )

        self.assertEqual(
            set(SHARED_RIBBON_TIMELINE_BEHAVIOR),
            set(VIEW_COMMANDS + INSPECTION_COMMANDS),
        )
        primary_behaviors = {
            "source-preserving",
            "replacement",
            "read-only",
        }
        operation_behaviors = {"source-preserving", "replacement"}
        for command, behaviors in SHARED_RIBBON_TIMELINE_BEHAVIOR.items():
            with self.subTest(command=command):
                primary = behaviors & primary_behaviors
                self.assertEqual(len(primary), 1)
                self.assertFalse(behaviors - primary_behaviors - {"operation"})
                self.assertEqual(
                    "operation" in behaviors,
                    bool(primary & operation_behaviors),
                )

    def test_shared_groups_are_live_on_every_domain_and_sketch_state(self):
        comparison_body, _comparison_tip = self._new_body(
            "ComparisonBody",
            20,
        )
        inspection = self.document.addObject(
            "Inspection::Feature",
            "SharedRibbonInspection",
        )
        inspection.Actual = self.body
        inspection.Nominals = [comparison_body]
        self.document.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.body, "Face1")
        self._process_events(150)

        for label, workbench in SHIPPED_RIBBON_DOMAINS:
            with self.subTest(page=label):
                Gui.activateWorkbench(workbench)
                self._process_events(200)
                self.assertEqual(Gui.activeWorkbench().name(), workbench)
                self.assertEqual(
                    self._ribbon_group_commands("View"),
                    VIEW_COMMANDS,
                )
                self.assertEqual(
                    self._ribbon_group_commands("Inspect"),
                    INSPECTION_COMMANDS,
                )
                shared_actions = {
                    **self._ribbon_group_actions("View"),
                    **self._ribbon_group_actions("Inspect"),
                }
                for command in VIEW_COMMANDS + INSPECTION_COMMANDS:
                    self.assertIn(command, SHARED_RIBBON_TIMELINE_BEHAVIOR)
                    self.assertTrue(
                        shared_actions[command].isEnabled(),
                        f"{command} ribbon action is disabled on the " f"{label} page",
                    )
                    self.assertTrue(
                        Gui.isCommandActive(command),
                        f"{command} is unusable on the {label} page",
                    )

        Gui.activateWorkbench("SketcherWorkbench")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.body, "Face1")
        self._process_events(200)
        self.assertEqual(
            self._ribbon_group_commands("View"),
            VIEW_COMMANDS,
        )
        self.assertEqual(
            self._ribbon_group_commands("Inspect"),
            INSPECTION_COMMANDS,
        )
        self.assertEqual(
            self._ribbon_group_commands("Sketch"),
            SKETCH_SETUP_COMMANDS,
        )
        sketch_setup_shared_actions = {
            **self._ribbon_group_actions("View"),
            **self._ribbon_group_actions("Inspect"),
        }
        for command in VIEW_COMMANDS + INSPECTION_COMMANDS:
            self.assertTrue(
                sketch_setup_shared_actions[command].isEnabled(),
                command,
            )
            self.assertTrue(Gui.isCommandActive(command), command)

        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "SharedRibbonSketch",
        )
        self.document.recompute()
        Gui.Selection.clearSelection()
        self._process_events()
        self.assertTrue(Gui.isCommandActive("Sketcher_NewSketch"))
        self.assertFalse(Gui.isCommandActive("Sketcher_EditSketch"))
        Gui.Selection.addSelection(sketch)
        self._process_events()
        for command in (
            "Sketcher_NewSketch",
            "Sketcher_EditSketch",
            "Sketcher_ReorientSketch",
            "Sketcher_ValidateSketch",
            "Sketcher_MirrorSketch",
        ):
            self.assertTrue(Gui.isCommandActive(command), command)

        self.assertTrue(Gui.activeDocument().setEdit(sketch.Name))
        self._process_events(200)
        self.assertEqual(
            self._ribbon_group_commands("View"),
            VIEW_COMMANDS,
        )
        for command, action in self._ribbon_group_actions("View").items():
            self.assertTrue(action.isEnabled(), command)
            self.assertTrue(Gui.isCommandActive(command), command)
        self.assertIsNone(self._ribbon_group("Inspect"))
        self.assertIsNone(self._ribbon_group("Sketch"))
        for title, commands in SKETCH_EDIT_GROUPS.items():
            self.assertEqual(
                self._ribbon_group_commands(title),
                commands,
                title,
            )
        for command in SKETCH_SETUP_COMMANDS:
            self.assertFalse(Gui.isCommandActive(command), command)
        Gui.activeDocument().resetEdit()
        self._process_events(150)

    def test_shell_state_survives_ribbons_assistant_visibility_and_reopen(self):
        import VibeCADGrid

        main_window = Gui.getMainWindow()
        tree = main_window.findChild(QtGui.QDockWidget, "Std_TreeView")
        tasks = main_window.findChild(QtGui.QDockWidget, "Std_TaskView")
        browser_host = main_window.findChild(
            QtGui.QWidget,
            "VibeCADModelBrowserHost",
        )
        assistant = main_window.findChild(
            QtGui.QDockWidget,
            "VibeCADAssistantPanel",
        )
        timeline = main_window.findChild(
            QtGui.QWidget,
            "VibeCADFeatureTimeline",
        )
        timeline_items = main_window.findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        document_tabs = main_window.findChild(
            QtGui.QTabBar,
            "VibeCADDocumentTabs",
        )
        self.assertIsNotNone(tree)
        self.assertIsNotNone(tasks)
        self.assertIsNotNone(browser_host)
        self.assertIsNotNone(assistant)
        self.assertIsNotNone(timeline)
        self.assertIsNotNone(timeline_items)
        self.assertIsNotNone(document_tabs)
        self.assertIs(tree.parentWidget(), browser_host)
        self.assertIsNot(tasks.parentWidget(), browser_host)

        tree_action = tree.toggleViewAction()
        assistant_action = assistant.toggleViewAction()
        original_assistant_visible = bool(assistant_action.isChecked())
        self.assertTrue(tree_action.isChecked())
        self.assertFalse(tree_action.isEnabled())
        self.assertFalse(tree_action.isVisible())
        grid_parameters = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Draft")
        original_grid_visible = grid_parameters.GetBool(
            "alwaysShowGrid",
            False,
        )

        def set_dock_visible(dock, action, visible):
            if bool(action.isChecked()) != visible:
                action.trigger()
            self.assertTrue(
                self._wait_until(
                    lambda: bool(action.isChecked()) == visible
                    and bool(dock.isHidden()) != visible
                ),
                (dock.objectName(), visible),
            )

        def tree_contains_active_document():
            for candidate in main_window.findChildren(QtGui.QTreeWidget):
                if not candidate.isVisible() or not candidate.viewport().isVisible():
                    continue
                for index in range(candidate.topLevelItemCount()):
                    item = candidate.topLevelItem(index)
                    if not item.isHidden() and item.text(0) == self.document.Label:
                        return True
            return False

        def timeline_contains_tip():
            return any(
                timeline_items.item(row).data(QtCore.Qt.UserRole) == self.tip.Name
                for row in range(timeline_items.count())
            )

        def document_tabs_contain_active_document():
            return any(
                self.document.Label in document_tabs.tabText(index)
                for index in range(document_tabs.count())
            )

        def assert_shell():
            current_tree = main_window.findChild(
                QtGui.QDockWidget,
                "Std_TreeView",
            )
            current_assistant = main_window.findChild(
                QtGui.QDockWidget,
                "VibeCADAssistantPanel",
            )
            current_timeline = main_window.findChild(
                QtGui.QWidget,
                "VibeCADFeatureTimeline",
            )
            self.assertIs(current_tree, tree)
            self.assertIs(current_assistant, assistant)
            self.assertIs(current_timeline, timeline)
            self.assertTrue(tree_action.isChecked())
            self.assertFalse(tree_action.isEnabled())
            self.assertFalse(tree_action.isVisible())
            self.assertFalse(tree.isHidden())
            self.assertTrue(timeline.isVisible())
            self.assertTrue(document_tabs.isVisible())
            self.assertTrue(document_tabs_contain_active_document())
            self.assertTrue(VibeCADGrid.is_grid_visible())
            self.assertTrue(tree_contains_active_document())

        try:
            set_dock_visible(assistant, assistant_action, True)
            VibeCADGrid.setup()
            VibeCADGrid.toggle_grid(True)
            self.assertTrue(
                self._wait_until(VibeCADGrid.is_grid_visible),
                "The enabled grid did not appear in the active 3D view.",
            )
            self.assertTrue(
                self._wait_until(timeline_contains_tip),
                "The active Body result did not appear in global History.",
            )
            self.assertTrue(
                self._wait_until(tree_contains_active_document),
                "The active document did not appear in the model tree.",
            )
            self.assertTrue(
                self._wait_until(document_tabs_contain_active_document),
                "The active document did not appear in the ribbon tabs.",
            )

            self.document.openTransaction("Cross-ribbon label edit")
            original_label = self.tip.Label
            self.tip.Label = "Cross-ribbon current result"
            self.document.commitTransaction()
            self.assertEqual(self.tip.Label, "Cross-ribbon current result")

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.body, "Face1")
            self._process_events()
            expected_selection = self._selection_snapshot()
            expected_document = self._document_snapshot()

            for _label, workbench in SHIPPED_RIBBON_DOMAINS:
                Gui.activateWorkbench(workbench)
                self._process_events(150)
                self.assertEqual(Gui.activeWorkbench().name(), workbench)
                assert_shell()
                self.assertEqual(
                    self._selection_snapshot(),
                    expected_selection,
                )
                self.assertEqual(
                    self._document_snapshot(),
                    expected_document,
                )

            # Exercise the actual ribbon command which previously caused the
            # tree overlay to disappear.
            set_dock_visible(assistant, assistant_action, False)
            Gui.runCommand("VibeCAD_OpenAssistant")
            self.assertTrue(
                self._wait_until(
                    lambda: assistant_action.isChecked() and not assistant.isHidden()
                )
            )
            assert_shell()

            # The Model browser is permanent chrome. Even direct QAction
            # activation cannot hide it while ribbons and tasks transition.
            tree_action.trigger()
            for _label, workbench in SHIPPED_RIBBON_DOMAINS:
                Gui.activateWorkbench(workbench)
                self._process_events(150)
                assert_shell()
                self.assertEqual(
                    self._document_snapshot(),
                    expected_document,
                )

            set_dock_visible(assistant, assistant_action, False)
            Gui.runCommand("VibeCAD_OpenAssistant")
            self.assertTrue(
                self._wait_until(
                    lambda: assistant_action.isChecked() and not assistant.isHidden()
                )
            )
            self.assertTrue(tree_action.isChecked())
            self.assertFalse(tree.isHidden())

            Gui.activateWorkbench("PartDesignWorkbench")
            self._process_events(150)
            assert_shell()

            self.document.undo()
            self._process_events()
            self.assertEqual(self.tip.Label, original_label)
            self.document.redo()
            self._process_events()
            self.assertEqual(self.tip.Label, "Cross-ribbon current result")

            self.document.openTransaction("Persist Body visibility")
            self.body.Visibility = False
            self.document.commitTransaction()
            self.assertFalse(self.body.Visibility)

            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "RibbonInspectView.FCStd")
                self.document.saveAs(path)
                App.closeDocument(self.document.Name)
                self._process_events(150)

                self.document = App.openDocument(path)
                App.setActiveDocument(self.document.Name)
                Gui.activateView("Gui::View3DInventor", True)
                self.body = self.document.getObject("ProductBody")
                self.tip = self.document.getObject("CurrentResult")
                self.assertIsNotNone(self.body)
                self.assertIsNotNone(self.tip)
                self._process_events(300)

                self.assertFalse(
                    self.body.Visibility,
                    "Body visibility did not survive save and reopen.",
                )
                self.assertEqual(
                    self.tip.Label,
                    "Cross-ribbon current result",
                )
                self.assertTrue(self._wait_until(tree_contains_active_document))
                self.assertTrue(self._wait_until(timeline_contains_tip))
                self.assertTrue(self._wait_until(document_tabs_contain_active_document))
                self.assertTrue(self._wait_until(VibeCADGrid.is_grid_visible))

                for _label, workbench in SHIPPED_RIBBON_DOMAINS:
                    Gui.activateWorkbench(workbench)
                    self._process_events(150)
                    assert_shell()
                    self.assertFalse(self.body.Visibility)
        finally:
            current_assistant = main_window.findChild(
                QtGui.QDockWidget,
                "VibeCADAssistantPanel",
            )
            if current_assistant is not None:
                set_dock_visible(
                    current_assistant,
                    current_assistant.toggleViewAction(),
                    original_assistant_visible,
                )
            VibeCADGrid.toggle_grid(original_grid_visible)
            self._process_events(100)

    def test_shipped_commands_have_strict_usable_enablement(self):
        for command_name in INSPECTION_COMMANDS + VIEW_COMMANDS:
            self.assertIsNotNone(
                Gui.Command.get(command_name),
                command_name,
            )

        self.assertTrue(Gui.isCommandActive("Std_Measure"))
        self.assertTrue(Gui.isCommandActive("Std_MassProperties"))
        self.assertFalse(
            Gui.isCommandActive("Inspection_VisualInspection"),
            "one Body and its Tip are one inspectable result, not two parts",
        )
        self.assertFalse(Gui.isCommandActive("Inspection_InspectElement"))
        self.assertFalse(Gui.isCommandActive("Part_CheckGeometry"))
        for command_name in VIEW_COMMANDS:
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

        Gui.Selection.addSelection(self.body, "Face1")
        self._process_events()
        self.assertTrue(Gui.isCommandActive("Part_CheckGeometry"))

        self._new_body("ComparisonBody", 20)
        self._process_events()
        self.assertTrue(Gui.isCommandActive("Inspection_VisualInspection"))

    def test_empty_body_does_not_enable_geometry_inspection(self):
        self.document.removeObject(self.tip.Name)
        self.document.recompute()
        self._process_events()

        self.assertIsNone(self.body.Tip)
        self.assertFalse(Gui.isCommandActive("Std_Measure"))
        self.assertFalse(Gui.isCommandActive("Std_MassProperties"))
        self.assertFalse(Gui.isCommandActive("Part_CheckGeometry"))
        self.assertFalse(Gui.isCommandActive("Inspection_VisualInspection"))

    def test_measure_maps_body_edge_to_tip_and_cancel_is_exact(self):
        Gui.Selection.addSelection(self.body, "Edge1")
        self._process_events()
        before_document = self._document_snapshot()
        before_selection = self._selection_snapshot()

        Gui.runCommand("Std_Measure")
        self._process_events(200)
        self.assertTrue(Gui.Control.activeDialog())
        previews = [
            obj for obj in self.document.Objects if obj.TypeId.startswith("Measure::")
        ]
        self.assertEqual(len(previews), 1)
        self.assertEqual(
            previews[0].Elements,
            [(self.tip, ("Edge1",))],
            "the measurement must reference the current result, not its Body container",
        )
        self.assertFalse(Gui.isCommandActive("Std_Measure"))

        self._close_task("Std_Measure")
        self.assertEqual(self._document_snapshot(), before_document)
        self.assertEqual(self._selection_snapshot(), before_selection)

    def test_measure_preview_is_locked_and_save_starts_a_fresh_attempt(self):
        Gui.Selection.addSelection(self.body, "Edge1")
        self._process_events()
        before_undo = int(self.document.UndoCount)

        Gui.runCommand("Std_Measure")
        self._process_events(200)
        first_id = int(self.document.getBookedTransactionID())
        self.assertNotEqual(first_id, 0)

        # The Python wrapper deliberately returns None; transaction ownership
        # is observable through the booked ID.
        self.document.openTransaction("Intruder")
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            first_id,
            "a nested caller must not replace the live measurement preview",
        )
        self.document.commitTransaction()
        self.document.abortTransaction()
        self.assertEqual(self.document.getBookedTransactionID(), first_id)

        save = self._task_button(QtGui.QDialogButtonBox.Apply)
        self.assertIsNotNone(save)
        save.click()
        self._process_events(200)
        second_id = int(self.document.getBookedTransactionID())
        self.assertNotEqual(second_id, 0)
        self.assertNotEqual(second_id, first_id)
        measurements = [
            obj
            for obj in self.document.Objects
            if obj.TypeId.startswith("Measure::")
            and obj.Name != "MassPropertiesPreview"
        ]
        self.assertEqual(len(measurements), 1)
        self.assertEqual(self.document.UndoCount, before_undo + 1)

        self._close_task("Std_Measure repeated Save")
        self.assertIsNotNone(self.document.getObject(measurements[0].Name))
        self.assertEqual(self.document.UndoCount, before_undo + 1)
        self._assert_saved_result_follows_document_history(measurements[0])

    def test_measure_preserves_link_occurrence_and_subelement(self):
        link = self.document.addObject("App::Link", "ResultOccurrence")
        link.LinkedObject = self.body
        link.LinkPlacement = App.Placement(
            App.Vector(25, 3, 2),
            App.Rotation(),
        )
        self.document.recompute()
        Gui.Selection.addSelection(link, "Edge1")
        self._process_events()
        before_document = self._document_snapshot()
        before_selection = self._selection_snapshot()

        Gui.runCommand("Std_Measure")
        self._process_events(200)
        previews = [
            obj for obj in self.document.Objects if obj.TypeId.startswith("Measure::")
        ]
        self.assertEqual(len(previews), 1)
        self.assertEqual(
            previews[0].Elements,
            [(link, ("Edge1",))],
            "a linked occurrence must not collapse to its shared definition",
        )

        self._close_task("Std_Measure link occurrence")
        self.assertEqual(self._document_snapshot(), before_document)
        self.assertEqual(self._selection_snapshot(), before_selection)

    def test_mass_properties_close_removes_preview_without_model_change(self):
        Gui.Selection.addSelection(self.body)
        self._process_events()
        before_document = self._document_snapshot()
        before_selection = self._selection_snapshot()

        Gui.runCommand("Std_MassProperties")
        self._process_events(250)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIsNotNone(self.document.getObject("MassPropertiesPreview"))
        self.assertFalse(Gui.isCommandActive("Std_MassProperties"))

        self._close_task("Std_MassProperties")
        self.assertEqual(self._document_snapshot(), before_document)
        self.assertEqual(self._selection_snapshot(), before_selection)

    def test_mass_properties_preview_is_locked_and_save_is_durable(self):
        Gui.Selection.addSelection(self.body)
        self._process_events()
        before_undo = int(self.document.UndoCount)

        Gui.runCommand("Std_MassProperties")
        self._process_events(250)
        first_id = int(self.document.getBookedTransactionID())
        self.assertNotEqual(first_id, 0)
        self.document.openTransaction("Intruder")
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            first_id,
        )
        self.document.commitTransaction()
        self.document.abortTransaction()
        self.assertEqual(self.document.getBookedTransactionID(), first_id)

        save = self._task_button(QtGui.QDialogButtonBox.Apply)
        self.assertIsNotNone(save)
        save.click()
        self._process_events(300)
        second_id = int(self.document.getBookedTransactionID())
        self.assertNotEqual(second_id, 0)
        self.assertNotEqual(second_id, first_id)
        result = self.document.getObject("MassProperties")
        self.assertIsNotNone(result)
        self.assertTrue(self.document.HasPendingTransaction)

        self._close_task("Std_MassProperties saved result")
        self.assertIsNotNone(self.document.getObject(result.Name))
        self.assertIsNone(self.document.getObject("MassPropertiesPreview"))
        self.assertEqual(self.document.UndoCount, before_undo + 1)
        self._assert_saved_result_follows_document_history(result)

    def test_saved_mass_properties_follow_nested_occurrence_after_reopen(self):
        outer = self.document.addObject("App::Part", "OuterOccurrence")
        inner = self.document.addObject("App::Part", "InnerOccurrence")
        inner.addObject(self.body)
        outer.addObject(inner)
        outer.Placement = App.Placement(
            App.Vector(11, 0, 0),
            App.Rotation(),
        )
        inner.Placement = App.Placement(
            App.Vector(0, 7, 0),
            App.Rotation(),
        )
        self.document.recompute()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(outer)
        self._process_events()
        Gui.runCommand("Std_MassProperties")
        self._process_events(250)
        save = self._task_button(QtGui.QDialogButtonBox.Apply)
        self.assertIsNotNone(save)
        save.click()
        self._process_events(300)
        result = self.document.getObject("MassProperties")
        self.assertIsNotNone(result)
        self._close_task("Std_MassProperties nested occurrence")

        self.assertEqual(
            result.getTypeIdOfProperty("MassPropertySources"),
            "App::PropertyLinkSubListGlobal",
        )
        self.assertEqual(
            result.getTypeIdOfProperty("MassPropertyOccurrences"),
            "App::PropertyLinkSubListGlobal",
        )
        self.assertEqual(
            result.getTypeIdOfProperty("MassPropertyOccurrenceDependencies"),
            "App::PropertyLinkListGlobal",
        )
        self.assertEqual(len(result.MassPropertyOccurrences), 1)
        self.assertTrue(result.isValid(), result.getStatusString())
        baseline = App.Vector(result.MassPropertyCenterOfGravity)

        self.document.openTransaction("Move nested occurrence")
        inner.Placement = App.Placement(
            App.Vector(0, 12, 0),
            App.Rotation(),
        )
        self.document.commitTransaction()
        self.document.recompute()
        moved = App.Vector(result.MassPropertyCenterOfGravity)
        self.assertAlmostEqual(moved.x, baseline.x, places=7)
        self.assertAlmostEqual(moved.y - baseline.y, 5.0, places=7)
        self.assertAlmostEqual(moved.z, baseline.z, places=7)
        self.assertTrue(result.isValid(), result.getStatusString())

        self.document.undo()
        self.document.recompute()
        restored = App.Vector(result.MassPropertyCenterOfGravity)
        self.assertLess((restored - baseline).Length, 1.0e-7)
        self.document.redo()
        self.document.recompute()
        redone = App.Vector(result.MassPropertyCenterOfGravity)
        self.assertLess((redone - moved).Length, 1.0e-7)

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "RibbonInspectView.FCStd")
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self._process_events(100)
            self.document = App.openDocument(path)
            App.setActiveDocument(self.document.Name)
            self.body = self.document.getObject("ProductBody")
            self.tip = self.document.getObject("CurrentResult")
            result = self.document.getObject("MassProperties")
            outer = self.document.getObject("OuterOccurrence")
            self.assertIsNotNone(result)
            self.assertIsNotNone(outer)
            self.document.recompute()
            reopened = App.Vector(result.MassPropertyCenterOfGravity)
            self.assertLess((reopened - moved).Length, 1.0e-7)
            self.assertTrue(result.isValid(), result.getStatusString())

            self.document.openTransaction("Move reopened outer occurrence")
            outer.Placement = App.Placement(
                App.Vector(14, 0, 0),
                App.Rotation(),
            )
            self.document.commitTransaction()
            self.document.recompute()
            reopened_moved = App.Vector(result.MassPropertyCenterOfGravity)
            self.assertAlmostEqual(
                reopened_moved.x - reopened.x,
                3.0,
                places=7,
            )
            self.assertAlmostEqual(
                reopened_moved.y,
                reopened.y,
                places=7,
            )
            self.assertAlmostEqual(
                reopened_moved.z,
                reopened.z,
                places=7,
            )
            self.assertTrue(result.isValid(), result.getStatusString())

    def test_saved_mass_properties_keep_repeated_link_occurrences_distinct(self):
        occurrences = self.document.addObject(
            "App::Part",
            "RepeatedOccurrences",
        )
        first = self.document.addObject("App::Link", "FirstOccurrence")
        second = self.document.addObject("App::Link", "SecondOccurrence")
        first.LinkedObject = self.body
        second.LinkedObject = self.body
        first.LinkPlacement = App.Placement(
            App.Vector(0, 0, 0),
            App.Rotation(),
        )
        second.LinkPlacement = App.Placement(
            App.Vector(40, 0, 0),
            App.Rotation(),
        )
        occurrences.addObject(first)
        occurrences.addObject(second)
        self.document.recompute()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrences)
        self._process_events()
        Gui.runCommand("Std_MassProperties")
        self._process_events(250)
        save = self._task_button(QtGui.QDialogButtonBox.Apply)
        self.assertIsNotNone(save)
        save.click()
        self._process_events(300)
        result = self.document.getObject("MassProperties")
        self.assertIsNotNone(result)
        self._close_task("Std_MassProperties repeated occurrences")

        self.assertEqual(len(result.MassPropertySources), 2)
        stored_occurrences = list(result.MassPropertyOccurrences)
        self.assertEqual(len(stored_occurrences), 1)
        self.assertIs(stored_occurrences[0][0], occurrences)
        self.assertEqual(
            tuple(stored_occurrences[0][1]),
            ("FirstOccurrence.", "SecondOccurrence."),
        )
        self.assertTrue(result.isValid(), result.getStatusString())
        baseline = App.Vector(result.MassPropertyCenterOfGravity)
        self.assertAlmostEqual(baseline.x, 25.0, places=7)
        self.assertAlmostEqual(baseline.y, 6.0, places=7)
        self.assertAlmostEqual(baseline.z, 7.0, places=7)

        self.document.openTransaction("Move one repeated occurrence")
        second.LinkPlacement = App.Placement(
            App.Vector(60, 0, 0),
            App.Rotation(),
        )
        self.document.commitTransaction()
        self.document.recompute()
        moved = App.Vector(result.MassPropertyCenterOfGravity)
        self.assertAlmostEqual(moved.x, 35.0, places=7)
        self.assertAlmostEqual(moved.y, baseline.y, places=7)
        self.assertAlmostEqual(moved.z, baseline.z, places=7)
        self.assertTrue(result.isValid(), result.getStatusString())

        self.document.undo()
        self.document.recompute()
        restored = App.Vector(result.MassPropertyCenterOfGravity)
        self.assertLess((restored - baseline).Length, 1.0e-7)
        self.document.redo()
        self.document.recompute()
        redone = App.Vector(result.MassPropertyCenterOfGravity)
        self.assertLess((redone - moved).Length, 1.0e-7)

    def test_mutating_inspection_commands_refuse_caller_transactions(self):
        self._new_body("ComparisonBody", 20)
        self._process_events()

        for command_name in (
            "Std_Measure",
            "Std_MassProperties",
            "Inspection_VisualInspection",
        ):
            with self.subTest(command=command_name):
                self.document.openTransaction(f"Caller owns {command_name}")
                caller_id = int(self.document.getBookedTransactionID())
                self.tip.Label = f"Caller value {command_name}"
                self._process_events()

                self.assertFalse(Gui.isCommandActive(command_name))
                Gui.runCommand(command_name)
                self._process_events(100)
                self.assertFalse(Gui.Control.activeDialog())
                self.assertIsNone(QtGui.QApplication.activeModalWidget())
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    caller_id,
                )
                self.assertEqual(
                    self.tip.Label,
                    f"Caller value {command_name}",
                )

                self.document.abortTransaction()
                self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_visual_inspection_does_not_dismiss_or_touch_nested_caller_state(self):
        self._new_body("ComparisonBody", 20)
        self._process_events()
        observed = {}

        def attempt_accept_with_caller_transaction():
            dialog = QtGui.QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            actual = dialog.findChild(QtGui.QTreeWidget, "treeWidgetActual")
            nominal = dialog.findChild(QtGui.QTreeWidget, "treeWidgetNominal")
            actual.topLevelItem(0).setCheckState(0, QtCore.Qt.Checked)
            nominal.topLevelItem(1).setCheckState(0, QtCore.Qt.Checked)
            actual.itemClicked.emit(actual.topLevelItem(0), 0)
            nominal.itemClicked.emit(nominal.topLevelItem(1), 0)

            self.document.openTransaction("Nested modal caller")
            caller_id = int(self.document.getBookedTransactionID())
            self.tip.Label = "Nested caller value"
            ok = dialog.findChild(QtGui.QDialogButtonBox).button(
                QtGui.QDialogButtonBox.Ok
            )
            ok.click()
            self._process_events(50)
            observed["visible_after_refusal"] = dialog.isVisible()
            observed["transaction_id"] = int(self.document.getBookedTransactionID())
            observed["caller_id"] = caller_id
            observed["label"] = self.tip.Label
            observed["inspection_count"] = sum(
                obj.TypeId == "Inspection::Feature" for obj in self.document.Objects
            )
            self.document.abortTransaction()
            dialog.reject()

        QtCore.QTimer.singleShot(
            100,
            attempt_accept_with_caller_transaction,
        )
        Gui.runCommand("Inspection_VisualInspection")
        self._process_events()

        self.assertTrue(observed["visible_after_refusal"])
        self.assertEqual(observed["transaction_id"], observed["caller_id"])
        self.assertEqual(observed["label"], "Nested caller value")
        self.assertEqual(observed["inspection_count"], 0)

    def test_visual_inspection_lists_each_current_result_once_and_cancel_is_exact(self):
        comparison_body, _ = self._new_body("ComparisonBody", 20)
        occurrence = self.document.addObject("App::Link", "ComparisonOccurrence")
        occurrence.LinkedObject = self.body
        occurrence.LinkPlacement = App.Placement(
            App.Vector(40, 0, 0),
            App.Rotation(),
        )
        self.document.recompute()
        self._process_events()
        self.assertTrue(Gui.isCommandActive("Inspection_VisualInspection"))
        before_document = self._document_snapshot()
        inspected = {}

        def inspect_and_cancel():
            dialog = QtGui.QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            actual = dialog.findChild(QtGui.QTreeWidget, "treeWidgetActual")
            nominal = dialog.findChild(QtGui.QTreeWidget, "treeWidgetNominal")
            self.assertIsNotNone(actual)
            self.assertIsNotNone(nominal)
            inspected["actual"] = [
                actual.topLevelItem(index).data(0, QtCore.Qt.UserRole)
                for index in range(actual.topLevelItemCount())
            ]
            inspected["nominal"] = [
                nominal.topLevelItem(index).data(0, QtCore.Qt.UserRole)
                for index in range(nominal.topLevelItemCount())
            ]
            dialog.reject()

        QtCore.QTimer.singleShot(100, inspect_and_cancel)
        Gui.runCommand("Inspection_VisualInspection")
        self._process_events()

        expected = [self.body.Name, comparison_body.Name, occurrence.Name]
        self.assertCountEqual(inspected["actual"], expected)
        self.assertCountEqual(inspected["nominal"], expected)
        self.assertNotIn(self.tip.Name, inspected["actual"])
        self.assertEqual(self._document_snapshot(), before_document)

    def test_inspect_element_escape_fully_restores_normal_view_mode(self):
        actual_occurrence = self.document.addObject("App::Link", "InspectionOccurrence")
        actual_occurrence.LinkedObject = self.body
        inspection = self.document.addObject("Inspection::Feature", "InspectionResult")
        inspection.Actual = actual_occurrence
        inspection.Nominals = [self.body]
        self.document.recompute()
        self._process_events()
        self.assertGreater(
            len(inspection.Distances),
            0,
            "Inspection must evaluate linked Part geometry before element review",
        )
        self.assertTrue(Gui.isCommandActive("Inspection_InspectElement"))
        before_document = self._document_snapshot()

        Gui.runCommand("Inspection_InspectElement")
        self._process_events()
        viewer = Gui.activeDocument().activeView().getViewer()
        self.assertTrue(viewer.isRedirectedToSceneGraph())
        self.assertFalse(Gui.isCommandActive("Inspection_InspectElement"))

        self._send_inspection_escape()
        self.assertFalse(viewer.isRedirectedToSceneGraph())
        self.assertTrue(
            Gui.isCommandActive("Inspection_InspectElement"),
            "Escape must leave editing mode and restore ordinary selection",
        )
        self.assertEqual(self._document_snapshot(), before_document)

    def test_check_geometry_uses_tip_and_restores_body_face_selection(self):
        Gui.Selection.addSelection(self.body, "Face1")
        self._process_events()
        before_document = self._document_snapshot()
        before_selection = self._selection_snapshot()

        result_name = self._run_geometry_check()
        self.assertIn(self.tip.Name, result_name)
        self.assertNotIn(self.body.Name, result_name)

        self._close_task("Part_CheckGeometry")
        self.assertEqual(self._document_snapshot(), before_document)
        self.assertEqual(self._selection_snapshot(), before_selection)

    def test_check_geometry_keeps_link_occurrence_as_the_report_target(self):
        link = self.document.addObject("App::Link", "CheckOccurrence")
        link.LinkedObject = self.body
        link.LinkPlacement = App.Placement(
            App.Vector(30, 0, 0),
            App.Rotation(),
        )
        self.document.recompute()
        Gui.Selection.addSelection(link, "Face1")
        self._process_events()
        before_document = self._document_snapshot()
        before_selection = self._selection_snapshot()

        result_name = self._run_geometry_check()
        self.assertIn(link.Name, result_name)
        self.assertNotIn(self.tip.Name, result_name)

        self._close_task("Part_CheckGeometry link occurrence")
        self.assertEqual(self._document_snapshot(), before_document)
        self.assertEqual(self._selection_snapshot(), before_selection)

    def test_isometric_and_fit_all_change_only_the_camera(self):
        before_document = self._document_snapshot()
        view = Gui.activeDocument().activeView()

        view.viewFront()
        self._process_events()
        front = tuple(view.getCameraOrientation().Q)
        Gui.runCommand("Std_ViewIsometric")
        self._process_events()
        isometric = tuple(view.getCameraOrientation().Q)
        self.assertNotEqual(isometric, front)

        camera = view.getCameraNode()
        self.assertTrue(hasattr(camera, "height"))
        camera.height = 0.5
        self._process_events()
        before_height = float(camera.height.getValue())
        Gui.runCommand("Std_ViewFitAll")
        self._process_events(200)
        after_height = float(camera.height.getValue())
        self.assertGreater(after_height, before_height)

        self.assertEqual(self._document_snapshot(), before_document)
        self.assertFalse(self.document.HasPendingTransaction)
