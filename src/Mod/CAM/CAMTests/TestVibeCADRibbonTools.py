# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD behavior contracts for native Manufacture commands."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

SHIPPED_CAM_COMMANDS = {
    "Setup": (
        "CAM_Job",
        "CAM_Sanity",
        "CAM_PostTools",
        "CAM_Post",
        "CAM_PostSelected",
    ),
    "Tools": (
        "CAM_SimTools",
        "CAM_SimulatorGL",
        "CAM_Simulator",
        "CAM_Inspect",
        "CAM_SelectLoop",
        "CAM_OpActiveToggle",
        "CAM_ToolBitDock",
    ),
    "Operations": (
        "CAM_Profile",
        "CAM_Pocket_Shape",
        "CAM_MillFacing",
        "CAM_Helix",
        "CAM_Adaptive",
        "CAM_Slot",
        "CAM_DrillingTools",
        "CAM_Drilling",
        "CAM_ThreadMilling",
        "CAM_EngraveTools",
        "CAM_Engrave",
        "CAM_Deburr",
        "CAM_Vcarve",
        "CAM_Pocket3D",
    ),
    "Modify": (
        "CAM_OperationCopy",
        "CAM_Array",
        "CAM_SimpleCopy",
        "CAM_DressupTools",
        "CAM_DressupArray",
        "CAM_DressupAxisMap",
        "CAM_DressupPathBoundary",
        "CAM_DressupDogbone",
        "CAM_DressupDragKnife",
        "CAM_DressupLeadInOut",
        "CAM_DressupMirror",
        "CAM_DressupRampEntry",
        "CAM_DressupTag",
        "CAM_DressupZCorrect",
    ),
}

SHIPPED_CAM_TOOLBARS = {
    "Project Setup": (
        "CAM_Job",
        "CAM_Sanity",
        "CAM_PostTools",
    ),
    "Tool Commands": (
        "CAM_SimTools",
        "CAM_Inspect",
        "CAM_SelectLoop",
        "CAM_OpActiveToggle",
        "CAM_ToolBitDock",
    ),
    "New Operations": (
        "CAM_Profile",
        "CAM_Pocket_Shape",
        "CAM_MillFacing",
        "CAM_Helix",
        "CAM_Adaptive",
        "CAM_Slot",
        "CAM_DrillingTools",
        "CAM_EngraveTools",
        "CAM_Pocket3D",
    ),
    "Path Modification": (
        "CAM_OperationCopy",
        "CAM_Array",
        "CAM_SimpleCopy",
        "CAM_DressupTools",
    ),
}

CAM_COMPOSITE_ACTIONS = {
    "CAM_PostTools": (
        "CAM_Post",
        "CAM_PostSelected",
    ),
    "CAM_SimTools": (
        "CAM_SimulatorGL",
        "CAM_Simulator",
    ),
    "CAM_DrillingTools": (
        "CAM_Drilling",
        "CAM_ThreadMilling",
    ),
    "CAM_EngraveTools": (
        "CAM_Engrave",
        "CAM_Deburr",
        "CAM_Vcarve",
    ),
    "CAM_DressupTools": (
        "CAM_DressupArray",
        "CAM_DressupAxisMap",
        "CAM_DressupPathBoundary",
        "CAM_DressupDogbone",
        "CAM_DressupDragKnife",
        "CAM_DressupLeadInOut",
        "CAM_DressupMirror",
        "CAM_DressupRampEntry",
        "CAM_DressupTag",
        "CAM_DressupZCorrect",
    ),
}

DEFAULT_CAM_MENU_ADDITIONS = {
    "CAM_ExportTemplate",
    "CAM_ToolBitLibraryOpen",
    "CAM_Comment",
    "CAM_Stop",
    "CAM_Custom",
    "CAM_Probe",
    "CAM_PropertyBag",
}

CAM_CONTEXT_ONLY_COMMANDS = {
    "CAM_SetStartPoint",
    "CAM_ToolBitSave",
    "CAM_ToolBitSaveAs",
}

COMMAND_TIMELINE_BEHAVIOR = {
    "CAM_Job": frozenset({"operation", "resource", "replacement"}),
    "CAM_Sanity": frozenset({"read-only"}),
    "CAM_PostTools": frozenset({"read-only"}),
    "CAM_Post": frozenset({"read-only"}),
    "CAM_PostSelected": frozenset({"read-only"}),
    "CAM_SimTools": frozenset({"read-only"}),
    "CAM_SimulatorGL": frozenset({"read-only"}),
    "CAM_Simulator": frozenset({"operation", "source-preserving"}),
    "CAM_Inspect": frozenset({"read-only"}),
    "CAM_SelectLoop": frozenset({"read-only"}),
    "CAM_OpActiveToggle": frozenset({"in-place"}),
    "CAM_ToolBitDock": frozenset({"resource", "source-preserving"}),
    "CAM_Profile": frozenset({"operation", "source-preserving"}),
    "CAM_Pocket_Shape": frozenset({"operation", "source-preserving"}),
    "CAM_MillFacing": frozenset({"operation", "source-preserving"}),
    "CAM_Helix": frozenset({"operation", "source-preserving"}),
    "CAM_Adaptive": frozenset({"operation", "source-preserving"}),
    "CAM_Slot": frozenset({"operation", "source-preserving"}),
    "CAM_DrillingTools": frozenset({"read-only"}),
    "CAM_Drilling": frozenset({"operation", "source-preserving"}),
    "CAM_ThreadMilling": frozenset({"operation", "source-preserving"}),
    "CAM_EngraveTools": frozenset({"read-only"}),
    "CAM_Engrave": frozenset({"operation", "source-preserving"}),
    "CAM_Deburr": frozenset({"operation", "source-preserving"}),
    "CAM_Vcarve": frozenset({"operation", "source-preserving"}),
    "CAM_Pocket3D": frozenset({"operation", "source-preserving"}),
    "CAM_OperationCopy": frozenset({"operation", "source-preserving"}),
    "CAM_Array": frozenset({"operation", "source-preserving"}),
    "CAM_SimpleCopy": frozenset({"operation", "source-preserving"}),
    "CAM_DressupTools": frozenset({"read-only"}),
    "CAM_DressupArray": frozenset({"operation", "replacement"}),
    "CAM_DressupAxisMap": frozenset({"operation", "replacement"}),
    "CAM_DressupPathBoundary": frozenset({"operation", "replacement"}),
    "CAM_DressupDogbone": frozenset({"operation", "replacement"}),
    "CAM_DressupDragKnife": frozenset({"operation", "replacement"}),
    "CAM_DressupLeadInOut": frozenset({"operation", "replacement"}),
    "CAM_DressupMirror": frozenset({"operation", "replacement"}),
    "CAM_DressupRampEntry": frozenset({"operation", "replacement"}),
    "CAM_DressupTag": frozenset({"operation", "replacement"}),
    "CAM_DressupZCorrect": frozenset({"operation", "replacement"}),
    "CAM_ExportTemplate": frozenset({"read-only"}),
    "CAM_ToolBitLibraryOpen": frozenset({"read-only"}),
    "CAM_Comment": frozenset({"operation", "source-preserving"}),
    "CAM_Stop": frozenset({"operation", "source-preserving"}),
    "CAM_Custom": frozenset({"operation", "source-preserving"}),
    "CAM_Probe": frozenset({"operation", "source-preserving"}),
    "CAM_PropertyBag": frozenset({"operation", "source-preserving"}),
    "CAM_PathShapeTC": frozenset({"operation", "source-preserving"}),
    "CAM_Area": frozenset({"operation", "source-preserving"}),
    "CAM_Area_Workplane": frozenset({"in-place"}),
    "CAM_3dTools": frozenset({"read-only"}),
    "CAM_Surface": frozenset({"operation", "source-preserving"}),
    "CAM_Waterline": frozenset({"operation", "source-preserving"}),
    "CAM_RotarySurface": frozenset({"operation", "source-preserving"}),
    "CAM_Camotics": frozenset({"read-only"}),
    "CAM_SetStartPoint": frozenset({"in-place"}),
    "CAM_ToolBitSave": frozenset({"read-only", "external"}),
    "CAM_ToolBitSaveAs": frozenset({"read-only", "external"}),
}

OPERATION_COMMANDS = SHIPPED_CAM_COMMANDS["Operations"]
OPERATION_LEAF_COMMANDS = tuple(
    command
    for command in OPERATION_COMMANDS
    if command
    not in {
        "CAM_DrillingTools",
        "CAM_EngraveTools",
    }
)
DRESSUP_LEAF_COMMANDS = SHIPPED_CAM_COMMANDS["Modify"][4:]
CAM_CONTEXT_MENU_COMMANDS = {
    "CAM_Job",
    "CAM_Sanity",
    "CAM_ExportTemplate",
    "CAM_Post",
    "CAM_PostSelected",
    "CAM_Inspect",
    "CAM_OpActiveToggle",
    "CAM_OperationCopy",
    "CAM_Array",
    "CAM_SimpleCopy",
    "CAM_SetStartPoint",
    "CAM_ToolBitSave",
    "CAM_ToolBitSaveAs",
    *DRESSUP_LEAF_COMMANDS,
}
TASK_EDITABLE_DRESSUP_COMMANDS = {
    "CAM_DressupAxisMap",
    "CAM_DressupPathBoundary",
    "CAM_DressupDogbone",
    "CAM_DressupDragKnife",
    "CAM_DressupLeadInOut",
    "CAM_DressupTag",
    "CAM_DressupZCorrect",
}
NON_TASK_EDITABLE_DRESSUP_COMMANDS = {
    "CAM_DressupArray",
    "CAM_DressupMirror",
    "CAM_DressupRampEntry",
}
EXPERIMENTAL_COMMANDS = {
    "CAM_PathShapeTC",
    "CAM_Area",
    "CAM_Area_Workplane",
}
ADVANCED_3D_COMMANDS = {
    "CAM_3dTools",
    "CAM_Surface",
    "CAM_Waterline",
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


class TestVibeCADCAMSourceContracts(unittest.TestCase):
    def test_native_path_factories_retain_exact_results_and_inputs(self):
        source_roots = (
            Path(__file__).resolve().parents[2],
            Path(App.getHomePath()).resolve().parents[1] / "src" / "Mod",
        )
        relative_source = Path("CAM") / "Gui" / "Command.cpp"
        source_path = next(
            (root / relative_source for root in source_roots if (root / relative_source).is_file()),
            None,
        )
        if source_path is None:
            self.skipTest("Native CAM Area source is not present in this installation")

        source = source_path.read_text(encoding="utf-8")

        def command_body(command):
            start = source.index(f"void {command}::activated(")
            end = source.index(
                f"\nbool {command}::isActive()",
                start,
            )
            return source[start:end]

        area_body = command_body("CmdPathArea")
        shape_body = command_body("CmdPathShape")
        compound_body = command_body("CmdPathCompound")
        workplane_body = command_body("CmdPathAreaWorkplane")
        self.assertGreaterEqual(
            area_body.count("Gui::Command::runDocumentObjectCommand("),
            3,
        )
        self.assertGreaterEqual(
            shape_body.count("Gui::Command::runDocumentObjectCommand("),
            2,
        )
        self.assertEqual(
            compound_body.count("Gui::Command::runDocumentObjectCommand("),
            1,
        )
        for body in (
            area_body,
            shape_body,
            compound_body,
            workplane_body,
        ):
            self.assertIn("ExactDocumentIdentity", body)
            self.assertIn("ExactObjectIdentity", body)
            self.assertNotIn("ActiveObject", body)
            self.assertNotIn("doCommandEval", body)
            self.assertNotIn("commandDocument->recompute()", body)

        for body in (area_body, shape_body):
            self.assertIn(
                "std::vector<std::optional<ExactObjectIdentity>> " "sourceIdentities",
                body,
            )
            self.assertIn(
                "finalizeProvisionalOperationBlock(result, block)",
                body,
            )
            self.assertNotIn("resourceNames", body)

    def test_python_path_factories_do_not_recover_temporary_globals(self):
        cam_root = Path(__file__).resolve().parents[1]
        relative_paths = (
            "Path/Op/Gui/Array.py",
            "Path/Op/Gui/SimpleCopy.py",
            "Path/Dressup/Gui/Mirror.py",
            "Path/Dressup/Gui/RampEntry.py",
        )
        for relative_path in relative_paths:
            source = (cam_root / relative_path).read_text(encoding="utf-8")
            self.assertIn("runDocumentObjectCommand(", source)
            self.assertNotIn("doCommandEval(", source)
            self.assertNotRegex(
                source,
                r"\b(?:obj|result|dressup)\s*=\s*" r"(?:FreeCADGui|Gui)\.doCommand",
            )


@unittest.skipIf(not App.GuiUp, "VibeCAD Manufacture ribbon tests require the GUI")
class TestVibeCADCAMRibbonTools(unittest.TestCase):
    """Shipped CAM commands must be atomic, undoable, and correctly gated."""

    def setUp(self):
        Gui.activateWorkbench("CAMWorkbench")
        self.document = App.newDocument("VibeCADCAMRibbonTools")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)

        model = self.document.addObject("Part::Feature", "ContractModel")
        model.Shape = Part.makeBox(20, 16, 8)

        import Path.Main.Gui.Job as PathJobGui
        import Path.Op.Custom as PathCustom

        self.job = PathJobGui.Create([model], None, openTaskPanel=False)
        self.assertIsNotNone(self.job)
        self.assertTrue(self.job.Tools.Group)
        self.operation = PathCustom.Create(
            "ContractOperation",
            parentJob=self.job,
        )
        self.job.Proxy.addOperation(self.operation)
        self.operation.Gcode = [
            "G0 X0 Y0 Z5",
            "G0 X0 Y0 Z0",
            "G1 X10 Y0 Z0",
            "G1 X10 Y10 Z0",
            "G1 X0 Y10 Z0",
            "G1 X0 Y0 Z0",
        ]
        if not hasattr(self.operation, "ClearanceHeight"):
            self.operation.addProperty(
                "App::PropertyDistance",
                "ClearanceHeight",
                "Contract",
            )
        if not hasattr(self.operation, "SafeHeight"):
            self.operation.addProperty(
                "App::PropertyDistance",
                "SafeHeight",
                "Contract",
            )
        if not hasattr(self.operation, "StartDepth"):
            self.operation.addProperty(
                "App::PropertyDistance",
                "StartDepth",
                "Contract",
            )
        if not hasattr(self.operation, "StartPoint"):
            self.operation.addProperty(
                "App::PropertyVector",
                "StartPoint",
                "Contract",
            )
        if not hasattr(self.operation, "UseStartPoint"):
            self.operation.addProperty(
                "App::PropertyBool",
                "UseStartPoint",
                "Contract",
            )
        self.operation.ClearanceHeight = 5.0
        self.operation.SafeHeight = 3.0
        self.operation.StartDepth = 0.0
        self.operation.StartPoint = App.Vector()
        self.operation.UseStartPoint = False
        self.operation.Active = True
        self.area = self.document.addObject(
            "Path::FeatureArea",
            "ContractArea",
        )
        self.area.Sources = [model]
        self.document.recompute()
        self.assertTrue(self.operation.isValid())
        Gui.Selection.clearSelection()
        self._process_events()

    def tearDown(self):
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()
        Gui.Selection.clearSelection()
        try:
            current_document = getattr(self, "document", None)
            current_document_name = current_document.Name if current_document is not None else None
        except (ReferenceError, RuntimeError):
            current_document_name = None
        if current_document_name in App.listDocuments():
            App.closeDocument(current_document_name)
        for name in (
            "VibeCADCAMBackgroundTask",
            "VibeCADCAMDeletedTaskDocument",
            "VibeCADCAMRibbonTools",
        ):
            if name in App.listDocuments():
                App.closeDocument(name)
        self._process_events()

    @staticmethod
    def _process_events(wait_ms=30):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        if wait_ms:
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()

    def _move_timeline_to(self, position):
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertGreaterEqual(position, 0)
        self.assertLessEqual(position, len(timeline.Operations))
        operations = list(timeline.Operations)

        # Moving before a semantic operation also moves before every internal
        # resource owned by that operation.  Those resources immediately
        # precede their owner in the raw document order, but are deliberately
        # not independent user-visible timeline steps.
        expected_position = position
        if position < len(operations):
            owner = operations[position]
            while expected_position > 0:
                resource = operations[expected_position - 1]
                if (
                    "VibeCADTimelineOwner" not in resource.PropertiesList
                    or resource.VibeCADTimelineOwner is not owner
                ):
                    break
                expected_position -= 1

        window = Gui.getMainWindow()
        end = window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        previous = window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        self.assertIsNotNone(end)
        self.assertIsNotNone(previous)

        end.click()
        self._process_events(100)
        while int(timeline.Position) > expected_position:
            previous.click()
            self._process_events(50)
        self.assertEqual(
            int(timeline.Position),
            expected_position,
        )

    def _double_click_history_operation(self, operation):
        items = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(items)
        self._process_events(100)
        role = int(QtCore.Qt.UserRole)
        matches = [
            items.item(row)
            for row in range(items.count())
            if items.item(row).data(role) == operation.Name
        ]
        self.assertEqual(len(matches), 1)
        items.itemDoubleClicked.emit(matches[0])
        self._process_events(100)

    def _visible_timeline_names(self):
        items = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(items)
        self._process_events(100)
        role = int(QtCore.Qt.UserRole)
        return {
            items.item(row).data(role) for row in range(items.count()) if items.item(row).data(role)
        }

    def _assert_timeline_replaces(self, operation, inputs):
        self.assertEqual(
            operation.VibeCADTimelineRole,
            "operation",
        )
        self.assertEqual(
            operation.getTypeIdOfProperty("VibeCADTimelineReplacedInputs"),
            "App::PropertyLinkListHidden",
        )
        self.assertEqual(
            list(operation.VibeCADTimelineReplacedInputs),
            list(inputs),
        )
        self.assertEqual(
            operation.getEditorMode("VibeCADTimelineReplacedInputs"),
            ["Hidden"],
        )

    def _assert_timeline_source_preserving(self, operation):
        self.assertEqual(
            operation.VibeCADTimelineRole,
            "operation",
        )
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            operation.PropertiesList,
        )

    def _assert_timeline_resource(self, resource, owner):
        self.assertEqual(
            resource.VibeCADTimelineRole,
            "resource",
        )
        self.assertIs(resource.VibeCADTimelineOwner, owner)
        self.assertEqual(
            resource.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )

    def _select_operation(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.operation)
        self._process_events()

    def _select_job(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.job)
        self._process_events()

    def _select_model(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.document.getObject("ContractModel"))
        self._process_events()

    def _clear_selection(self):
        Gui.Selection.clearSelection()
        self._process_events()

    def _ensure_probe_tool_controller(self):
        """Give the real Probe editor the one tool type it accepts."""
        return self._ensure_tool_controller(
            shape_id="probe.fcstd",
            shape_name="probe",
            name="Contract Probe",
        )

    def _ensure_tool_controller(
        self,
        *,
        shape_id,
        shape_name,
        name,
    ):
        """Give a real operation editor an exact supported tool type."""
        from Path.Tool.toolbit import ToolBit
        import Path.Base.Util as PathUtil
        import Path.Tool.Controller as PathToolController
        import PathScripts.PathUtils as PathUtils
        from VibeCADNativeTransaction import _OwnedDocumentTransaction

        for controller in self.job.Tools.Group:
            tool = getattr(controller, "Tool", None)
            if tool is not None and PathUtils.getToolShapeName(tool) == shape_name:
                return controller

        transaction = _OwnedDocumentTransaction(
            self.document,
            "Add contract CAM tool controller",
        )
        try:
            extension = PathUtil.stageTimelineResourceGraphExtension(self.job)
            toolbit = ToolBit.from_shape_id(shape_id)
            tool = toolbit.attach_to_doc(
                doc=self.document,
                timeline_owner=self.job,
            )
            controller = PathToolController.Create(
                name=name,
                tool=tool,
                toolNumber=max(int(existing.ToolNumber) for existing in self.job.Tools.Group) + 1,
                document=self.document,
                timelineOwner=self.job,
            )
            self.job.Proxy.addToolController(controller)
            self.document.recompute()
            PathUtil.finalizeTimelineResourceGraphExtension(
                self.job,
                extension,
                PathUtil.toolControllerResourceGraph(controller),
            )
        except Exception:
            transaction.abort()
            raise
        transaction.commit()
        self.assertTrue(controller.isValid())
        self.assertTrue(tool.isValid())
        self.assertEqual(PathUtils.getToolShapeName(tool), shape_name)
        return controller

    def _dismiss_task(self, *, accept):
        self.assertTrue(Gui.Control.activeDialog())
        standards = (
            (QtGui.QDialogButtonBox.Ok, QtGui.QDialogButtonBox.Save)
            if accept
            else (
                QtGui.QDialogButtonBox.Cancel,
                QtGui.QDialogButtonBox.Abort,
                QtGui.QDialogButtonBox.Close,
            )
        )
        button = None
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            for standard in standards:
                candidate = button_box.button(standard)
                if candidate is not None and candidate.isVisible() and candidate.isEnabled():
                    button = candidate
                    break
            if button is not None:
                break
        self.assertIsNotNone(button)
        button.click()
        self._process_events(100)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def _set_context_for(self, command_name):
        if command_name in {
            "CAM_Sanity",
            "CAM_Post",
            "CAM_Camotics",
        }:
            self._select_job()
        elif command_name in {"CAM_SelectLoop", "CAM_Area"}:
            self._select_model()
        elif command_name == "CAM_Area_Workplane":
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.area)
            Gui.Selection.addSelection(
                self.document.getObject("ContractModel"),
                "Face1",
            )
            self._process_events()
        elif command_name in {
            "CAM_PostSelected",
            "CAM_Inspect",
            "CAM_OpActiveToggle",
            "CAM_OperationCopy",
            "CAM_Array",
            "CAM_SimpleCopy",
            "CAM_SetStartPoint",
            *DRESSUP_LEAF_COMMANDS,
        }:
            self._select_operation()
        elif command_name in {
            "CAM_ToolBitSave",
            "CAM_ToolBitSaveAs",
        }:
            tool = self.job.Tools.Group[0].Tool
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(tool)
            self._process_events()
        else:
            self._clear_selection()

    @staticmethod
    def _shipped_inventory():
        """Return every active shipped CAM action, including context-only."""
        inventory = {command for group in SHIPPED_CAM_COMMANDS.values() for command in group}
        inventory.update(CAM_CONTEXT_ONLY_COMMANDS)
        import Path

        registered = set(Gui.listCommands())
        if Path.Preferences.experimentalFeaturesEnabled():
            inventory.update(EXPERIMENTAL_COMMANDS)
        if Path.Preferences.advancedOCLFeaturesEnabled() and "CAM_3dTools" in registered:
            inventory.update(ADVANCED_3D_COMMANDS)
            if Path.Preferences.experimentalFeaturesEnabled() and "CAM_RotarySurface" in registered:
                inventory.add("CAM_RotarySurface")
        if Path.Preferences.advancedOCLFeaturesEnabled() and "CAM_Camotics" in registered:
            inventory.add("CAM_Camotics")
        return inventory

    def _live_cam_action_graph(self):
        toolbar_items = {
            title: commands
            for title, commands in (Gui.activeWorkbench().getToolbarItems()).items()
            if any(command.startswith("CAM_") for command in commands)
        }
        toolbar_top_level = {
            command
            for commands in toolbar_items.values()
            for command in commands
            if command != "Separator"
        }

        composites = {}
        for command_name in sorted(toolbar_top_level):
            command = Gui.Command.get(command_name)
            self.assertIsNotNone(command, command_name)
            child_ids = tuple(
                _action_command_id(action)
                for action in command.getAction()
                if not action.isSeparator()
            )
            if len(child_ids) > 1:
                composites[command_name] = child_ids

        menu_action = next(
            (
                action
                for action in Gui.getMainWindow().menuBar().actions()
                if action.text().replace("&", "") == "CAM"
            ),
            None,
        )
        self.assertIsNotNone(menu_action)
        self.assertIsNotNone(menu_action.menu())
        menu_commands = set()

        def collect_menu(menu):
            for action in menu.actions():
                if action.isSeparator():
                    continue
                if action.menu() is not None:
                    collect_menu(action.menu())
                    continue
                command_id = _action_command_id(action)
                if command_id:
                    menu_commands.add(command_id)

        collect_menu(menu_action.menu())
        return toolbar_items, toolbar_top_level, composites, menu_commands

    def _captured_context_menu_calls(self):
        workbench = Gui.activeWorkbench()
        calls = []

        def capture(menu, commands):
            if isinstance(commands, str):
                command_ids = (commands,)
            else:
                command_ids = tuple(commands)
            calls.append((str(menu), command_ids))

        with patch.object(
            workbench,
            "appendContextMenu",
            side_effect=capture,
        ):
            workbench.ContextMenu("Tree")
        return tuple(calls)

    def _expected_live_cam_action_graph(self):
        import Path

        registered = set(Gui.listCommands())
        toolbars = {title: list(commands) for title, commands in SHIPPED_CAM_TOOLBARS.items()}
        composites = dict(CAM_COMPOSITE_ACTIONS)

        if App.ParamGet("User parameter:BaseApp/Preferences/Mod/CAM").GetBool(
            "DefaultSimulatorLegacy", False
        ):
            composites["CAM_SimTools"] = tuple(reversed(composites["CAM_SimTools"]))

        experimental = Path.Preferences.experimentalFeaturesEnabled()
        advanced = Path.Preferences.advancedOCLFeaturesEnabled()
        if experimental:
            toolbars["Helpful Tools"] = [
                "CAM_Area",
                "CAM_Area_Workplane",
            ]

        has_advanced_3d = advanced and "CAM_3dTools" in registered
        if has_advanced_3d:
            toolbars["New Operations"][-1] = "CAM_3dTools"
            advanced_children = [
                "CAM_Pocket3D",
                "CAM_Surface",
                "CAM_Waterline",
            ]
            if experimental and "CAM_RotarySurface" in registered:
                advanced_children.append("CAM_RotarySurface")
            composites["CAM_3dTools"] = tuple(advanced_children)

        has_camotics = advanced and "CAM_Camotics" in registered
        if has_camotics:
            toolbars["Tool Commands"].append("CAM_Camotics")

        default_ribbon = {
            command for commands in SHIPPED_CAM_COMMANDS.values() for command in commands
        }
        default_menu = default_ribbon - set(CAM_COMPOSITE_ACTIONS) | DEFAULT_CAM_MENU_ADDITIONS
        menu_commands = set(default_menu)
        if experimental:
            menu_commands.update(EXPERIMENTAL_COMMANDS)
        if has_advanced_3d:
            menu_commands.update(
                {
                    "CAM_Surface",
                    "CAM_Waterline",
                }
            )
            if "CAM_RotarySurface" in composites["CAM_3dTools"]:
                menu_commands.add("CAM_RotarySurface")
        if has_camotics:
            menu_commands.add("CAM_Camotics")

        return (
            {title: tuple(commands) for title, commands in toolbars.items()},
            composites,
            menu_commands,
        )

    def test_exact_live_menu_toolbar_and_composite_graph_is_registered(self):
        (
            live_toolbars,
            live_top_level,
            live_composites,
            live_menu,
        ) = self._live_cam_action_graph()
        (
            expected_toolbars,
            expected_composites,
            expected_menu,
        ) = self._expected_live_cam_action_graph()

        self.assertEqual(
            {title: tuple(commands) for title, commands in live_toolbars.items()},
            expected_toolbars,
        )
        qt_toolbars = {
            toolbar.windowTitle(): toolbar
            for toolbar in Gui.getMainWindow().findChildren(QtGui.QToolBar)
        }
        for title, expected_commands in expected_toolbars.items():
            with self.subTest(toolbar_action_graph=title):
                self.assertIn(title, qt_toolbars)
                self.assertEqual(
                    tuple(
                        _action_command_id(action)
                        for action in qt_toolbars[title].actions()
                        if not action.isSeparator()
                    ),
                    expected_commands,
                )
        self.assertEqual(live_composites, expected_composites)
        self.assertEqual(live_menu, expected_menu)

        live_surface = set(live_top_level) | set(live_menu)
        live_surface.update(child for children in live_composites.values() for child in children)
        expected_surface = {
            command for commands in expected_toolbars.values() for command in commands
        } | set(expected_menu)
        expected_surface.update(
            child for children in expected_composites.values() for child in children
        )
        self.assertEqual(live_surface, expected_surface)
        self.assertFalse(live_surface - set(Gui.listCommands()))
        self.assertFalse(live_surface - set(COMMAND_TIMELINE_BEHAVIOR))

    def test_exact_dynamic_context_menu_graph_is_classified(self):
        self._select_model()
        self.assertEqual(
            self._captured_context_menu_calls(),
            (
                ("", ("Separator",)),
                ("", ("CAM_Job",)),
                ("", ("Separator",)),
            ),
        )

        self._select_operation()
        operation_calls = self._captured_context_menu_calls()
        self.assertEqual(
            operation_calls,
            (
                ("", ("Separator",)),
                ("Path Dressup", tuple(DRESSUP_LEAF_COMMANDS)),
                (
                    "Path Modification",
                    (
                        "CAM_Array",
                        "CAM_OperationCopy",
                        "CAM_SimpleCopy",
                    ),
                ),
                ("", ("CAM_SetStartPoint",)),
                ("", ("CAM_OpActiveToggle",)),
                ("", ("CAM_Inspect",)),
                ("", ("CAM_Post",)),
                ("", ("CAM_PostSelected",)),
                ("", ("Separator",)),
            ),
        )

        self._select_job()
        self.assertEqual(
            self._captured_context_menu_calls(),
            (
                ("", ("Separator",)),
                (
                    "",
                    (
                        "CAM_OpActiveToggle",
                        "CAM_ExportTemplate",
                        "CAM_Sanity",
                    ),
                ),
                ("", ("CAM_Post",)),
                ("", ("Separator",)),
            ),
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.job.Tools.Group[0].Tool)
        self._process_events()
        self.assertEqual(
            self._captured_context_menu_calls(),
            (
                ("", ("Separator",)),
                (
                    "",
                    (
                        "CAM_ToolBitSave",
                        "CAM_ToolBitSaveAs",
                    ),
                ),
                ("", ("Separator",)),
            ),
        )

        all_commands = {
            command
            for calls in (
                operation_calls,
                self._captured_context_menu_calls(),
            )
            for _menu, commands in calls
            for command in commands
            if command != "Separator"
        }
        # Add the two contexts whose exact calls were asserted above.
        all_commands.update(
            {
                "CAM_Job",
                "CAM_OpActiveToggle",
                "CAM_ExportTemplate",
                "CAM_Sanity",
                "CAM_Post",
            }
        )
        self.assertEqual(all_commands, CAM_CONTEXT_MENU_COMMANDS)
        self.assertFalse(CAM_CONTEXT_MENU_COMMANDS - set(COMMAND_TIMELINE_BEHAVIOR))
        self.assertFalse(CAM_CONTEXT_MENU_COMMANDS - set(Gui.listCommands()))

    def test_shipped_command_timeline_matrix_is_exhaustive(self):
        default_ribbon = {
            command for commands in SHIPPED_CAM_COMMANDS.values() for command in commands
        }
        default_menu = default_ribbon - set(CAM_COMPOSITE_ACTIONS) | DEFAULT_CAM_MENU_ADDITIONS
        default_surface = default_ribbon | default_menu
        conditional_surface = (
            EXPERIMENTAL_COMMANDS | ADVANCED_3D_COMMANDS | {"CAM_RotarySurface", "CAM_Camotics"}
        )
        context_only_surface = set(CAM_CONTEXT_ONLY_COMMANDS)
        self.assertEqual(len(default_ribbon), 40)
        self.assertEqual(len(default_menu), 42)
        self.assertEqual(len(default_surface), 47)
        self.assertEqual(len(conditional_surface), 8)
        self.assertEqual(len(context_only_surface), 3)
        self.assertEqual(
            set(COMMAND_TIMELINE_BEHAVIOR),
            (default_surface | conditional_surface | context_only_surface),
        )
        self.assertEqual(len(COMMAND_TIMELINE_BEHAVIOR), 58)

        primary_behaviors = {
            "read-only",
            "in-place",
            "source-preserving",
            "replacement",
        }
        for command, behaviors in COMMAND_TIMELINE_BEHAVIOR.items():
            with self.subTest(command=command):
                self.assertFalse(
                    behaviors - primary_behaviors - {"operation", "resource", "external"}
                )
                self.assertEqual(
                    len(behaviors & primary_behaviors),
                    1,
                )
                if "operation" in behaviors:
                    self.assertTrue(behaviors & {"source-preserving", "replacement"})
                if "replacement" in behaviors:
                    self.assertIn("operation", behaviors)
        self.assertEqual(
            {
                command
                for command, behaviors in (COMMAND_TIMELINE_BEHAVIOR.items())
                if "external" in behaviors
            },
            {
                "CAM_ToolBitSave",
                "CAM_ToolBitSaveAs",
            },
        )

        self.assertEqual(
            TASK_EDITABLE_DRESSUP_COMMANDS | NON_TASK_EDITABLE_DRESSUP_COMMANDS,
            set(DRESSUP_LEAF_COMMANDS),
        )
        self.assertFalse(TASK_EDITABLE_DRESSUP_COMMANDS & NON_TASK_EDITABLE_DRESSUP_COMMANDS)

    def test_path_operations_share_the_document_history_suppression_contract(
        self,
    ):
        import Path.Base.Util as PathUtil

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(self.operation, timeline.Operations)
        self.assertTrue(self.operation.hasExtension("App::SuppressibleExtension"))
        self.assertTrue(
            self.operation.ViewObject.hasExtension("Gui::ViewProviderSuppressibleExtension")
        )

        original_commands = len(self.operation.Path.Commands)
        self.assertGreater(original_commands, 0)
        self.assertTrue(PathUtil.activeForOp(self.operation))

        self.operation.Suppressed = True
        self.document.recompute()

        self.assertFalse(PathUtil.activeForOp(self.operation))
        diagnostics = self.operation.Proxy.getGenerationDiagnostics(self.operation)
        self.assertEqual(diagnostics["status"], "skipped")
        self.assertEqual(
            diagnostics["error"]["code"],
            "operation_suppressed",
        )

        self.operation.Suppressed = False
        self.document.recompute()

        self.assertTrue(PathUtil.activeForOp(self.operation))
        self.assertEqual(
            len(self.operation.Path.Commands),
            original_commands,
        )

    def test_path_compound_excludes_suppressed_children_without_deleting_them(
        self,
    ):
        import Path

        first = self.document.addObject("Path::Feature", "FirstToolpath")
        first.Path = Path.Path([Path.Command("G1", {"X": 1.0})])
        second = self.document.addObject("Path::Feature", "SecondToolpath")
        second.Path = Path.Path([Path.Command("G1", {"X": 2.0})])
        compound = self.document.addObject(
            "Path::FeatureCompound",
            "CombinedToolpaths",
        )
        compound.Group = [first, second]
        self.document.recompute()

        self.assertEqual(
            [command.Parameters["X"] for command in compound.Path.Commands],
            [1.0, 2.0],
        )

        first.Suppressed = True
        self.document.recompute()

        self.assertEqual(compound.Group, [first, second])
        self.assertEqual(
            [command.Parameters["X"] for command in compound.Path.Commands],
            [2.0],
        )

        first.Suppressed = False
        self.document.recompute()

        self.assertEqual(
            [command.Parameters["X"] for command in compound.Path.Commands],
            [1.0, 2.0],
        )

    def test_chained_dressup_suppression_reaches_cam_consumers(self):
        from types import SimpleNamespace

        import Path.Base.Util as PathUtil
        import Path.Dressup.Array as DressupArray
        from Path.Main.Gui.Simulator import PathSimulation
        from Path.Main.Sanity.Sanity import CAMSanity
        from Path.Post.Gui.DlgPostProcess import PostProcessDialog

        first = DressupArray.Create(
            self.operation,
            "ContractDressupFirst",
        )
        second = DressupArray.Create(
            first,
            "ContractDressupSecond",
        )
        self.document.recompute()
        self.assertGreater(len(first.Path.Commands), 0)
        self.assertGreater(len(second.Path.Commands), 0)

        first.Suppressed = True
        self.document.recompute()

        self.assertFalse(PathUtil.activeForOp(first))
        self.assertFalse(PathUtil.activeForOp(second))
        self.assertEqual(len(first.Path.Commands), 0)
        self.assertEqual(len(second.Path.Commands), 0)

        post_dialog = PostProcessDialog.__new__(PostProcessDialog)
        post_dialog._get_operations = lambda: [
            self.operation,
            first,
            second,
        ]
        self.assertEqual(
            post_dialog._get_active_operations(),
            [self.operation],
        )

        class OperationList:
            def __init__(self):
                self.items = []

            def clear(self):
                self.items.clear()

            def addItem(self, item):
                self.items.append(item)

        form = SimpleNamespace(
            comboJobs=SimpleNamespace(currentIndex=lambda: 0),
            listOperations=OperationList(),
        )
        simulator = PathSimulation.__new__(PathSimulation)
        simulator.taskForm = SimpleNamespace(form=form)
        simulator.jobs = [
            SimpleNamespace(
                Operations=SimpleNamespace(
                    OutList=[self.operation, first, second],
                )
            )
        ]
        simulator.initdone = False
        simulator.onJobChange()
        self.assertEqual(simulator.operations, [self.operation])
        self.assertEqual(len(form.listOperations.items), 1)

        sanity = CAMSanity.__new__(CAMSanity)
        sanity.job = SimpleNamespace(
            CycleTime="00:00:00",
            Path=self.operation.Path,
            Description="",
            Operations=SimpleNamespace(Group=[second]),
        )
        operation_data = sanity._runData()["operations"]
        self.assertEqual(len(operation_data), 1)
        self.assertIn("(INACTIVE)", operation_data[0]["opName"])
        self.assertEqual(operation_data[0]["cycleTime"], "00:00:00")

    def test_cam_timeline_shows_durable_work_and_restores_owned_resources(self):
        import tempfile

        role = int(QtCore.Qt.UserRole)
        timeline = self.document.getObject("VibeCADTimeline")
        resources = [
            self.job.Operations,
            self.job.SetupSheet,
            self.job.Model,
            *self.job.Model.Group,
            self.job.Tools,
            self.job.Stock,
        ]
        for controller in self.job.Tools.Group:
            resources.append(controller)
            tool = controller.Tool
            if tool:
                resources.append(tool)
                if tool.BitBody:
                    resources.append(tool.BitBody)
                    resources.extend(tool.BitBody.Group)
                for visual_resource in getattr(
                    tool.Proxy,
                    "timelineVisualResources",
                    lambda: (),
                )():
                    if visual_resource not in resources:
                        resources.append(visual_resource)

        self.assertEqual(self.job.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.operation.VibeCADTimelineRole,
            "operation",
        )
        for resource in resources:
            with self.subTest(resource=resource.Name):
                self.assertEqual(
                    resource.VibeCADTimelineRole,
                    "resource",
                )
                self.assertIs(
                    resource.VibeCADTimelineOwner,
                    self.job,
                )
                self.assertEqual(
                    resource.getTypeIdOfProperty("VibeCADTimelineOwner"),
                    "App::PropertyLinkHidden",
                )
                self.assertNotIn(
                    self.job,
                    resource.OutList,
                )
                self.assertEqual(
                    resource.getEditorMode("VibeCADTimelineRole"),
                    ["Hidden"],
                )
                self.assertEqual(
                    resource.getEditorMode("VibeCADTimelineOwner"),
                    ["Hidden"],
                )

        timeline_items = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline_items)
        self._process_events(100)
        visible_names = {
            timeline_items.item(row).data(role)
            for row in range(timeline_items.count())
            if timeline_items.item(row).data(role)
        }
        cam_objects = {
            self.job.Name,
            self.operation.Name,
            *(resource.Name for resource in resources),
        }
        self.assertEqual(
            visible_names & cam_objects,
            {self.job.Name, self.operation.Name},
        )

        self.job.Visibility = True
        self.job.Stock.Visibility = True
        job_index = list(timeline.Operations).index(self.job)
        job_block_begin = job_index - len(resources)
        self.assertGreaterEqual(job_block_begin, 0)
        self.assertEqual(
            set(timeline.Operations[job_block_begin:job_index]),
            set(resources),
        )
        previous = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        next_button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(next_button)
        self._move_timeline_to(job_index + 1)
        previous.click()
        self._process_events(100)

        self.assertEqual(
            int(timeline.Position),
            job_block_begin,
        )
        self.assertTrue(self.job.Suppressed)
        self.assertFalse(self.job.Visibility)
        self.assertFalse(self.job.Stock.Visibility)

        next_button.click()
        self._process_events(100)
        self.assertEqual(
            int(timeline.Position),
            job_index + 1,
        )
        self.assertFalse(self.job.Suppressed)
        self.assertTrue(self.job.Visibility)
        self.assertTrue(self.job.Stock.Visibility)

        previous.click()
        self._process_events(100)
        self.assertEqual(
            int(timeline.Position),
            job_block_begin,
        )
        self.assertTrue(self.job.Suppressed)
        self.assertFalse(self.job.Visibility)
        self.assertFalse(self.job.Stock.Visibility)
        expected_position = int(timeline.Position)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-timeline.FCStd"
            document_name = self.document.Name
            job_name = self.job.Name
            operation_name = self.operation.Name
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            self.operation = self.document.getObject(
                operation_name,
            )
            restored_stock = self.job.Stock
            restored_timeline = self.document.getObject(
                "VibeCADTimeline",
            )

            self.assertEqual(
                int(restored_timeline.Position),
                expected_position,
            )
            self.assertIs(
                restored_stock.VibeCADTimelineOwner,
                self.job,
            )
            self.assertEqual(
                restored_stock.getTypeIdOfProperty("VibeCADTimelineOwner"),
                "App::PropertyLinkHidden",
            )
            self.assertNotIn(self.job, restored_stock.OutList)
            self.assertTrue(self.job.Suppressed)
            self.assertFalse(restored_stock.Visibility)
            restored_items = Gui.getMainWindow().findChild(
                QtGui.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
            self.assertIsNotNone(restored_items)
            restored_names = {
                restored_items.item(row).data(role)
                for row in range(restored_items.count())
                if restored_items.item(row).data(role)
            }
            self.assertIn(self.job.Name, restored_names)
            self.assertNotIn(restored_stock.Name, restored_names)

            end = Gui.getMainWindow().findChild(
                QtGui.QToolButton,
                "VibeCADFeatureTimelineEnd",
            )
            self.assertIsNotNone(end)
            end.click()
            self._process_events(100)
            self.assertFalse(self.job.Suppressed)
            self.assertTrue(restored_stock.Visibility)

    def test_history_opens_only_real_cam_task_editors(self):
        import Path.Op.Gui.Base as PathOpGui
        import Path.Op.Gui.Custom as PathCustomGui

        if not isinstance(
            self.operation.ViewObject.Proxy,
            PathOpGui.ViewProvider,
        ):
            provider = PathOpGui.ViewProvider(
                self.operation.ViewObject,
                PathCustomGui.Command.res,
            )
            self.operation.ViewObject.Proxy = provider
            provider.setDeleteObjectsOnReject(False)

        self.assertFalse(Gui.Control.activeDialog())
        self._double_click_history_operation(self.operation)
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task(accept=False)

        self._double_click_history_operation(self.job)
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task(accept=False)

        self._select_operation()
        before = frozenset(self.document.Objects)
        Gui.runCommand("CAM_DressupArray")
        self._process_events(100)
        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 1)
        self._double_click_history_operation(created[0])
        self.assertFalse(Gui.Control.activeDialog())

    def test_cam_history_edit_capability_survives_save_and_reopen(self):
        import tempfile

        self._select_operation()
        before_axis = frozenset(self.document.Objects)
        Gui.runCommand("CAM_DressupAxisMap")
        self._process_events(100)
        self._dismiss_task(accept=True)
        axis_map = [obj for obj in self.document.Objects if obj not in before_axis]
        self.assertEqual(len(axis_map), 1)
        axis_map = axis_map[0]

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(axis_map)
        self._process_events()
        before_array = frozenset(self.document.Objects)
        Gui.runCommand("CAM_DressupArray")
        self._process_events(100)
        array = [obj for obj in self.document.Objects if obj not in before_array]
        self.assertEqual(len(array), 1)
        array = array[0]

        job_name = str(self.job.Name)
        operation_name = str(self.operation.Name)
        axis_map_name = str(axis_map.Name)
        array_name = str(array.Name)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-history-editors.FCStd"
            document_name = self.document.Name
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            self.operation = self.document.getObject(operation_name)
            axis_map = self.document.getObject(axis_map_name)
            array = self.document.getObject(array_name)

            for editable in (self.job, axis_map):
                with self.subTest(editable=editable.Name):
                    self.assertTrue(editable.ViewObject.Proxy.supportsDocumentTimelineEdit())

            self.assertFalse(
                getattr(
                    array.ViewObject.Proxy,
                    "supportsDocumentTimelineEdit",
                    lambda: False,
                )()
            )

            self._double_click_history_operation(axis_map)
            self.assertTrue(Gui.Control.activeDialog())
            self._dismiss_task(accept=False)

            self._double_click_history_operation(array)
            self.assertFalse(Gui.Control.activeDialog())

    def test_accepted_history_edit_is_one_undo_step(self):
        self._clear_selection()
        before_creation = frozenset(self.document.Objects)
        Gui.runCommand("CAM_Profile")
        self._process_events(100)
        self._dismiss_task(accept=True)
        created = [obj for obj in self.document.Objects if obj not in before_creation]
        self.assertEqual(len(created), 1)
        operation = created[0]
        operation_name = str(operation.Name)
        original_num_passes = int(operation.NumPasses)
        edited_num_passes = original_num_passes + 1
        before_undo = int(self.document.UndoCount)

        self._double_click_history_operation(operation)
        self.assertTrue(Gui.Control.activeDialog())
        panel = operation.ViewObject.Proxy.panel
        profile_pages = [page for page in panel.featurePages if hasattr(page.form, "numPasses")]
        self.assertEqual(len(profile_pages), 1)
        num_passes = profile_pages[0].form.numPasses
        num_passes.setValue(edited_num_passes)
        num_passes.editingFinished.emit()
        self._process_events(100)
        self.assertEqual(
            int(operation.NumPasses),
            edited_num_passes,
        )
        self._dismiss_task(accept=True)

        self.assertEqual(
            int(operation.NumPasses),
            edited_num_passes,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )

        self.document.undo()
        self._process_events(100)
        operation = self.document.getObject(operation_name)
        self.assertIsNotNone(operation)
        self.assertEqual(
            int(operation.NumPasses),
            original_num_passes,
        )

    def test_property_bag_history_edits_through_its_real_task_widget(self):
        import tempfile

        self._clear_selection()
        before_creation = frozenset(self.document.Objects)
        before_creation_undo = int(self.document.UndoCount)
        self.assertTrue(Gui.isCommandActive("CAM_PropertyBag"))
        Gui.runCommand("CAM_PropertyBag")
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        created = [obj for obj in self.document.Objects if obj not in before_creation]
        self.assertEqual(len(created), 1)
        bag = created[0]
        self._assert_timeline_source_preserving(bag)
        self.assertTrue(bag.ViewObject.Proxy.supportsDocumentTimelineEdit())
        self._dismiss_task(accept=True)
        self.assertEqual(
            int(self.document.UndoCount),
            before_creation_undo + 1,
        )

        self.document.openTransaction("Seed PropertyBag history editor")
        bag.Proxy.addCustomProperty(
            "App::PropertyString",
            "HistoryNote",
            "Contract",
            "History editing contract",
        )
        bag.HistoryNote = "before"
        self.document.commitTransaction()
        self.document.recompute()
        self._process_events(100)

        before_edit_undo = int(self.document.UndoCount)
        self._double_click_history_operation(bag)
        self.assertTrue(Gui.Control.activeDialog())
        panel = bag.ViewObject.Proxy.taskPanel
        self.assertIsNotNone(panel)
        self.assertEqual(panel.model.rowCount(), 1)
        index = panel.model.index(0, panel.ColumnVal)
        editor = panel.delegate.createEditor(
            panel.form.table.viewport(),
            QtGui.QStyleOptionViewItem(),
            index,
        )
        self.assertIsInstance(editor, QtGui.QLineEdit)
        panel.delegate.setEditorData(editor, index)
        self.assertEqual(editor.text(), "before")
        editor.setText("after")
        panel.delegate.setModelData(
            editor,
            panel.model,
            index,
        )
        self.assertEqual(bag.HistoryNote, "after")
        self._dismiss_task(accept=True)

        self.assertEqual(bag.HistoryNote, "after")
        self.assertEqual(
            int(self.document.UndoCount),
            before_edit_undo + 1,
        )
        self.document.undo()
        self._process_events(100)
        bag = self.document.getObject(bag.Name)
        self.assertIsNotNone(bag)
        self.assertEqual(bag.HistoryNote, "before")

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-property-bag-editor.FCStd"
            document_name = self.document.Name
            bag_name = str(bag.Name)
            job_name = str(self.job.Name)
            operation_name = str(self.operation.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            self.operation = self.document.getObject(operation_name)
            bag = self.document.getObject(bag_name)
            self._assert_timeline_source_preserving(bag)
            self.assertTrue(bag.ViewObject.Proxy.supportsDocumentTimelineEdit())
            self._double_click_history_operation(bag)
            self.assertTrue(Gui.Control.activeDialog())
            self._dismiss_task(accept=False)

    def test_cam_history_editor_bridge_is_exact_mode_zero_and_non_displacing(
        self,
    ):
        import Path.Op.Gui.Base as PathOpGui
        import Path.Op.Gui.Custom as PathCustomGui
        from Path.CommandBoundary import open_timeline_mode_zero_editor

        class RefusingViewProvider:
            def __init__(self, view_object):
                self.modes = []
                view_object.Proxy = self

            def setEdit(self, view_object, mode=0):
                self.modes.append(mode)
                return False

            def unsetEdit(self, view_object, mode=0):
                return False

        refusing = self.document.addObject(
            "Path::FeaturePython",
            "RefusingHistoryEditor",
        )
        refusing_provider = RefusingViewProvider(
            refusing.ViewObject,
        )
        self.assertFalse(open_timeline_mode_zero_editor(refusing))
        self.assertEqual(refusing_provider.modes, [0])
        self.assertIsNone(Gui.getDocument(self.document.Name).getInEdit())

        alias = type(
            "ObjectAlias",
            (),
            {
                "Document": self.document,
                "Name": self.job.Name,
            },
        )()
        self.assertFalse(open_timeline_mode_zero_editor(alias))

        if not isinstance(
            self.operation.ViewObject.Proxy,
            PathOpGui.ViewProvider,
        ):
            operation_provider = PathOpGui.ViewProvider(
                self.operation.ViewObject,
                PathCustomGui.Command.res,
            )
            self.operation.ViewObject.Proxy = operation_provider
            operation_provider.setDeleteObjectsOnReject(False)

        gui_document = Gui.getDocument(self.document.Name)
        self.document.openTransaction(
            "Foreign CAM History edit transaction",
        )
        foreign_transaction = int(self.document.getBookedTransactionID())
        self.assertNotEqual(foreign_transaction, 0)
        self.assertFalse(open_timeline_mode_zero_editor(self.operation))
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            foreign_transaction,
        )
        self.assertIsNone(gui_document.getInEdit())
        App.closeActiveTransaction(True, foreign_transaction)

        self.assertTrue(gui_document.setEdit(self.job.Name, 0))
        self.assertIs(
            gui_document.getInEdit().Object,
            self.job,
        )
        self.assertFalse(open_timeline_mode_zero_editor(self.operation))
        self.assertIs(
            gui_document.getInEdit().Object,
            self.job,
        )
        self._dismiss_task(accept=False)

        self.assertTrue(open_timeline_mode_zero_editor(self.operation))
        self.assertIs(
            gui_document.getInEdit().Object,
            self.operation,
        )
        self._dismiss_task(accept=False)

    def test_job_tracks_only_public_models_it_actually_hides(self):
        import Path.Main.Gui.Job as PathJobGui

        visible = self.document.addObject(
            "Part::Feature",
            "VisibleJobInput",
        )
        visible.Shape = Part.makeBox(8, 7, 6)
        visible.ViewObject.Visibility = True
        self.document.recompute()
        before = frozenset(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        created_job = PathJobGui.Create(
            [visible],
            None,
            openTaskPanel=True,
        )
        self.assertIsNotNone(created_job)
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertFalse(visible.ViewObject.isVisible())
        self._dismiss_task(accept=False)
        self.assertEqual(frozenset(self.document.Objects), before)
        self.assertTrue(visible.ViewObject.Visibility)
        self.assertEqual(int(self.document.UndoCount), before_undo)

        created_job = PathJobGui.Create(
            [visible],
            None,
            openTaskPanel=True,
        )
        job_name = str(created_job.Name)
        self._process_events(100)
        self._dismiss_task(accept=True)
        self._assert_timeline_replaces(created_job, [visible])
        self.assertFalse(visible.ViewObject.Visibility)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events(100)
        self.assertEqual(frozenset(self.document.Objects), before)
        self.assertTrue(visible.ViewObject.Visibility)

        self.document.redo()
        self._process_events(100)
        created_job = self.document.getObject(job_name)
        self.assertIsNotNone(created_job)
        self._assert_timeline_replaces(created_job, [visible])
        self.assertFalse(visible.ViewObject.Visibility)

        timeline = self.document.getObject("VibeCADTimeline")
        job_index = list(timeline.Operations).index(created_job)
        self._move_timeline_to(job_index)
        self.assertTrue(visible.ViewObject.Visibility)
        self.assertTrue(created_job.Suppressed)
        self.assertFalse(created_job.ViewObject.Visibility)

        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-job-replacement.FCStd"
            document_name = self.document.Name
            visible_name = str(visible.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            visible = self.document.getObject(visible_name)
            created_job = self.document.getObject(job_name)
            self.assertTrue(visible.ViewObject.Visibility)
            self.assertTrue(created_job.Suppressed)
            self._assert_timeline_replaces(
                created_job,
                [visible],
            )

            self._move_timeline_to(len(self.document.getObject("VibeCADTimeline").Operations))
            self.assertFalse(visible.ViewObject.Visibility)
            self.assertFalse(created_job.Suppressed)

    def test_job_does_not_publish_a_previously_hidden_model(self):
        hidden = self.document.getObject("ContractModel")
        hidden.ViewObject.Visibility = False
        provider = self.job.ViewObject.Proxy

        provider.setupEditVisibility(self.job)
        provider.syncTimelineReplacedInputs(self.job)
        provider.resetEditVisibility(self.job)

        self._assert_timeline_replaces(self.job, [])
        timeline = self.document.getObject("VibeCADTimeline")
        job_index = list(timeline.Operations).index(self.job)
        self._move_timeline_to(job_index)
        self.assertFalse(hidden.ViewObject.Visibility)

    def test_cam_replaced_input_helper_requires_an_exact_live_contract(self):
        import Path.Base.Util as PathUtil

        result = self.document.addObject(
            "App::FeaturePython",
            "ReplacementContractResult",
        )
        source = self.document.addObject(
            "Part::Feature",
            "ReplacementContractSource",
        )
        source.Shape = Part.makeBox(2, 2, 2)

        PathUtil.markTimelineReplacedInputs(
            result,
            [source, source],
        )
        self._assert_timeline_replaces(
            result,
            [source],
        )
        self.assertTrue(
            PathUtil.shouldRestoreTimelineReplacedInput(
                result,
                source,
            )
        )
        PathUtil.markTimelineReplacedInputs(result, [])
        self.assertFalse(
            PathUtil.shouldRestoreTimelineReplacedInput(
                result,
                source,
            )
        )
        legacy = self.document.addObject(
            "App::FeaturePython",
            "LegacyReplacementResult",
        )
        self.assertTrue(
            PathUtil.shouldRestoreTimelineReplacedInput(
                legacy,
                source,
            )
        )

        with self.assertRaises(ValueError):
            PathUtil.markTimelineReplacedInputs(
                result,
                [result],
            )

        other = App.newDocument("VibeCADCAMBackgroundTask")
        foreign = other.addObject(
            "App::FeaturePython",
            "ForeignReplacementInput",
        )
        with self.assertRaises(ValueError):
            PathUtil.markTimelineReplacedInputs(
                result,
                [foreign],
            )
        App.closeDocument(other.Name)
        App.setActiveDocument(self.document.Name)

        resource = self.document.addObject(
            "App::FeaturePython",
            "OwnedReplacementResource",
        )
        PathUtil.markTimelineResource(
            resource,
            self.job,
        )
        with self.assertRaises(TypeError):
            PathUtil.markTimelineReplacedInputs(
                resource,
                [source],
            )

    def test_cam_resource_helper_requires_exact_live_objects(self):
        import Path.Base.Util as PathUtil

        owner = self.document.addObject(
            "App::FeaturePython",
            "ResourceContractOwner",
        )
        resource = self.document.addObject(
            "App::FeaturePython",
            "ResourceContractObject",
        )
        PathUtil.markTimelineResource(resource, owner)
        self.assertEqual(resource.VibeCADTimelineRole, "resource")
        self.assertIs(resource.VibeCADTimelineOwner, owner)

        with self.assertRaises(ValueError):
            PathUtil.markTimelineResource(None, owner)
        with self.assertRaises(ValueError):
            PathUtil.markTimelineResource(resource, None)
        with self.assertRaises(ValueError):
            PathUtil.markTimelineResource(owner, owner)

        other = App.newDocument("VibeCADCAMResourceContractOther")
        foreign_owner = other.addObject(
            "App::FeaturePython",
            "ForeignResourceOwner",
        )
        foreign_resource = other.addObject(
            "App::FeaturePython",
            "ForeignResource",
        )
        with self.assertRaises(ValueError):
            PathUtil.markTimelineResource(resource, foreign_owner)
        with self.assertRaises(ValueError):
            PathUtil.markTimelineResource(foreign_resource, owner)
        App.closeDocument(other.Name)
        App.setActiveDocument(self.document.Name)

        stale_resource = self.document.addObject(
            "App::FeaturePython",
            "StaleResource",
        )
        stale_resource_name = stale_resource.Name
        self.document.removeObject(stale_resource_name)
        replacement_resource = self.document.addObject(
            "App::FeaturePython",
            stale_resource_name,
        )
        self.assertIs(
            self.document.getObject(stale_resource_name),
            replacement_resource,
        )
        with self.assertRaises(ValueError):
            PathUtil.markTimelineResource(stale_resource, owner)
        self.assertNotIn(
            "VibeCADTimelineRole",
            replacement_resource.PropertiesList,
        )

        stale_owner = self.document.addObject(
            "App::FeaturePython",
            "StaleResourceOwner",
        )
        stale_owner_name = stale_owner.Name
        self.document.removeObject(stale_owner_name)
        replacement_owner = self.document.addObject(
            "App::FeaturePython",
            stale_owner_name,
        )
        fresh_resource = self.document.addObject(
            "App::FeaturePython",
            "FreshResourceForStaleOwner",
        )
        self.assertIs(
            self.document.getObject(stale_owner_name),
            replacement_owner,
        )
        with self.assertRaises(ValueError):
            PathUtil.markTimelineResource(fresh_resource, stale_owner)
        self.assertNotIn(
            "VibeCADTimelineRole",
            fresh_resource.PropertiesList,
        )

    def test_job_model_edits_preserve_add_and_remove_exact_replacements(self):
        import Path.Main.Job as PathJob

        first = self.document.getObject("ContractModel")
        first.ViewObject.Visibility = True
        provider = self.job.ViewObject.Proxy

        provider.setupEditVisibility(self.job)
        provider.syncTimelineReplacedInputs(self.job)
        provider.resetEditVisibility(self.job)
        provider.applyAcceptedReplacementVisibilityTransition(self.job)
        self._assert_timeline_replaces(self.job, [first])
        self.assertFalse(first.ViewObject.Visibility)

        provider.setupEditVisibility(self.job)
        provider.syncTimelineReplacedInputs(self.job)
        self._assert_timeline_replaces(self.job, [first])

        second = self.document.addObject(
            "Part::Feature",
            "AddedVisibleJobInput",
        )
        second.Shape = Part.makeBox(4, 3, 2)
        second.ViewObject.Visibility = True
        second_clone = PathJob.createModelResourceClone(
            self.job,
            second,
        )
        self.job.Model.addObject(second_clone)
        provider.rememberBaseVisibility(
            self.job,
            second_clone,
        )

        third = self.document.addObject(
            "Part::Feature",
            "AddedHiddenJobInput",
        )
        third.Shape = Part.makeBox(3, 2, 1)
        third.ViewObject.Visibility = False
        third_clone = PathJob.createModelResourceClone(
            self.job,
            third,
        )
        self.job.Model.addObject(third_clone)
        provider.rememberBaseVisibility(
            self.job,
            third_clone,
        )

        provider.syncTimelineReplacedInputs(self.job)
        self._assert_timeline_replaces(
            self.job,
            [first, second],
        )
        self.assertTrue(second.ViewObject.Visibility)
        self.assertFalse(second.ViewObject.isVisible())
        self.assertFalse(third.ViewObject.Visibility)

        second_duplicate = PathJob.createModelResourceClone(
            self.job,
            second,
        )
        self.job.Model.addObject(second_duplicate)
        provider.rememberBaseVisibility(
            self.job,
            second_duplicate,
        )
        provider.forgetBaseVisibility(
            self.job,
            second_clone,
            restoreOriginal=True,
        )

        self.job.Proxy.removeBase(
            self.job,
            second_clone,
            True,
        )
        provider.syncTimelineReplacedInputs(self.job)
        self._assert_timeline_replaces(
            self.job,
            [first, second],
        )
        self.assertTrue(second.ViewObject.Visibility)
        self.assertFalse(second.ViewObject.isVisible())

        first_clone = next(
            clone
            for clone in self.job.Model.Group
            if self.job.Proxy.baseObject(
                self.job,
                clone,
            )
            is first
        )
        provider.forgetBaseVisibility(
            self.job,
            first_clone,
            restoreOriginal=True,
        )
        self.job.Proxy.removeBase(
            self.job,
            first_clone,
            True,
        )
        provider.syncTimelineReplacedInputs(self.job)
        provider.resetEditVisibility(self.job)
        provider.applyAcceptedReplacementVisibilityTransition(self.job)

        self._assert_timeline_replaces(
            self.job,
            [second],
        )
        self.assertTrue(first.ViewObject.Visibility)
        self.assertFalse(second.ViewObject.Visibility)
        self.assertFalse(third.ViewObject.Visibility)

    def test_dressup_marker_restores_exact_base_and_survives_reopen(self):
        import tempfile

        self.operation.ViewObject.Visibility = True
        before = frozenset(self.document.Objects)
        self._select_operation()
        Gui.runCommand("CAM_DressupArray")
        self._process_events(100)
        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 1)
        dressup = created[0]
        self._assert_timeline_replaces(
            dressup,
            [self.operation],
        )
        self.assertFalse(self.operation.ViewObject.Visibility)
        self.assertTrue(dressup.ViewObject.Visibility)

        timeline = self.document.getObject("VibeCADTimeline")
        source_index = list(timeline.Operations).index(self.operation)
        dressup_index = list(timeline.Operations).index(dressup)
        self.assertFalse(bool(timeline.VisibilityAtEnd[source_index]))
        self.assertTrue(bool(timeline.VisibilityAtEnd[dressup_index]))
        self._move_timeline_to(dressup_index)
        self.assertTrue(self.operation.ViewObject.Visibility)
        self.assertFalse(dressup.ViewObject.Visibility)
        self.assertTrue(self.operation.Visibility)
        self.assertFalse(dressup.Visibility)
        self.assertTrue(dressup.Suppressed)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-dressup-replacement.FCStd"
            document_name = self.document.Name
            operation_name = str(self.operation.Name)
            dressup_name = str(dressup.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.operation = self.document.getObject(operation_name)
            dressup = self.document.getObject(dressup_name)
            timeline = self.document.getObject("VibeCADTimeline")
            source_index = list(timeline.Operations).index(self.operation)
            dressup_index = list(timeline.Operations).index(dressup)
            self.assertFalse(bool(timeline.VisibilityAtEnd[source_index]))
            self.assertTrue(bool(timeline.VisibilityAtEnd[dressup_index]))
            self.assertTrue(self.operation.ViewObject.Visibility)
            self.assertFalse(dressup.ViewObject.Visibility)
            self.assertTrue(dressup.Suppressed)
            self._assert_timeline_replaces(
                dressup,
                [self.operation],
            )

            self._move_timeline_to(len(self.document.getObject("VibeCADTimeline").Operations))
            self.assertFalse(self.operation.ViewObject.Visibility)
            self.assertTrue(dressup.ViewObject.Visibility)
            self.assertFalse(dressup.Suppressed)

    def test_hidden_dressup_base_stays_hidden_when_the_dressup_is_deleted(self):
        self.operation.ViewObject.Visibility = False
        before = frozenset(self.document.Objects)
        self._select_operation()
        Gui.runCommand("CAM_DressupArray")
        self._process_events(100)
        created = [
            obj
            for obj in self.document.Objects
            if (obj not in before and "VibeCADTimelineReplacedInputs" in obj.PropertiesList)
        ]
        self.assertEqual(len(created), 1)
        dressup = created[0]
        dressup_name = str(dressup.Name)
        self._assert_timeline_replaces(dressup, [])
        self.assertFalse(self.operation.ViewObject.Visibility)

        before_delete_undo = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(dressup)
        self._process_events()
        Gui.runCommand("Std_Delete")
        self._process_events(100)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertIsNone(self.document.getObject(dressup_name))
        self.assertFalse(self.operation.ViewObject.Visibility)
        self.assertIn(
            self.operation,
            self.job.Operations.Group,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_delete_undo + 1,
        )

    def test_task_dressup_restore_keeps_the_saved_marker_state(self):
        import tempfile

        self.operation.ViewObject.Visibility = True
        before = frozenset(self.document.Objects)
        self._select_operation()
        Gui.runCommand("CAM_DressupTag")
        self._process_events(100)
        self._dismiss_task(accept=True)
        created = [
            obj
            for obj in self.document.Objects
            if (obj not in before and "VibeCADTimelineReplacedInputs" in obj.PropertiesList)
        ]
        self.assertEqual(len(created), 1)
        dressup = created[0]
        self._assert_timeline_replaces(
            dressup,
            [self.operation],
        )

        timeline = self.document.getObject("VibeCADTimeline")
        source_index = list(timeline.Operations).index(self.operation)
        dressup_index = list(timeline.Operations).index(dressup)
        self.assertFalse(bool(timeline.VisibilityAtEnd[source_index]))
        self.assertTrue(bool(timeline.VisibilityAtEnd[dressup_index]))
        self._move_timeline_to(dressup_index)
        self.assertTrue(self.operation.ViewObject.Visibility)
        self.assertFalse(dressup.ViewObject.Visibility)
        self.assertTrue(self.operation.Visibility)
        self.assertFalse(dressup.Visibility)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-task-dressup-replacement.FCStd"
            document_name = self.document.Name
            operation_name = str(self.operation.Name)
            dressup_name = str(dressup.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.operation = self.document.getObject(operation_name)
            dressup = self.document.getObject(dressup_name)
            timeline = self.document.getObject("VibeCADTimeline")
            source_index = list(timeline.Operations).index(self.operation)
            dressup_index = list(timeline.Operations).index(dressup)
            self.assertFalse(bool(timeline.VisibilityAtEnd[source_index]))
            self.assertTrue(bool(timeline.VisibilityAtEnd[dressup_index]))
            self.assertTrue(self.operation.ViewObject.Visibility)
            self.assertFalse(dressup.ViewObject.Visibility)
            self.assertTrue(dressup.Suppressed)

            self._move_timeline_to(len(self.document.getObject("VibeCADTimeline").Operations))
            self.assertFalse(self.operation.ViewObject.Visibility)
            self.assertTrue(dressup.ViewObject.Visibility)
            self.assertFalse(dressup.Suppressed)

    def test_every_shipped_dressup_tracks_its_exact_visible_base(self):
        operation_name = str(self.operation.Name)
        commands = (
            "CAM_DressupZCorrect",
            *(command for command in DRESSUP_LEAF_COMMANDS if command != "CAM_DressupZCorrect"),
        )
        for command_name in commands:
            with self.subTest(command=command_name):
                self.operation = self.document.getObject(operation_name)
                self.assertIsNotNone(self.operation)
                self.operation.ViewObject.Visibility = True
                self._select_operation()
                before = frozenset(self.document.Objects)
                before_undo = int(self.document.UndoCount)

                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name)
                self._process_events(100)
                if Gui.Control.activeDialog():
                    self._dismiss_task(accept=True)

                roots = [
                    obj
                    for obj in self.document.Objects
                    if (obj not in before and "VibeCADTimelineReplacedInputs" in obj.PropertiesList)
                ]
                self.assertEqual(
                    len(roots),
                    1,
                    command_name,
                )
                self._assert_timeline_replaces(
                    roots[0],
                    [self.operation],
                )
                supports_history_edit = getattr(
                    roots[0].ViewObject.Proxy,
                    "supportsDocumentTimelineEdit",
                    lambda: False,
                )()
                self.assertEqual(
                    supports_history_edit,
                    command_name in TASK_EDITABLE_DRESSUP_COMMANDS,
                    command_name,
                )
                self._double_click_history_operation(roots[0])
                if command_name in TASK_EDITABLE_DRESSUP_COMMANDS:
                    self.assertTrue(
                        Gui.Control.activeDialog(),
                        command_name,
                    )
                    self._dismiss_task(accept=False)
                else:
                    self.assertFalse(
                        Gui.Control.activeDialog(),
                        command_name,
                    )
                self.assertFalse(self.operation.ViewObject.Visibility)
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo + 1,
                )

                self.document.undo()
                self._process_events(100)
                self.operation = self.document.getObject(operation_name)
                self.assertEqual(
                    frozenset(self.document.Objects),
                    before,
                )
                self.assertTrue(self.operation.ViewObject.Visibility)

    def test_every_shipped_command_has_a_valid_context(self):
        for command_name in sorted(self._shipped_inventory()):
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )

    def test_every_shipped_command_refuses_a_caller_transaction(self):
        for command_name in sorted(self._shipped_inventory()):
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                self.document.openTransaction(f"Caller transaction for {command_name}")
                transaction = int(self.document.getBookedTransactionID())
                self.assertNotEqual(transaction, 0)
                self._process_events()
                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                self.assertEqual(tuple(self.document.Objects), before)
                self.assertEqual(int(self.document.UndoCount), before_undo)
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    transaction,
                )
                App.closeActiveTransaction(True, transaction)
                self.assertFalse(self.document.HasPendingTransaction)

    def test_operation_task_cancel_accept_and_one_undo(self):
        self._clear_selection()
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("CAM_Profile"))
        Gui.runCommand("CAM_Profile")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(int(self.document.UndoCount), before_undo)

        Gui.runCommand("CAM_Profile")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task(accept=True)

        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].isValid())
        self._assert_timeline_source_preserving(created[0])
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)

    def test_closed_operation_task_releases_its_python_ui_graph(self):
        import weakref

        self._clear_selection()
        before = frozenset(self.document.Objects)
        Gui.runCommand("CAM_Profile")
        self._process_events(100)
        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 1)
        panel = created[0].ViewObject.Proxy.panel
        self.assertIsNotNone(panel)
        panel_reference = weakref.ref(panel)
        page_references = [weakref.ref(page) for page in panel.featurePages]
        form_references = [weakref.ref(page.form) for page in panel.featurePages]

        self._dismiss_task(accept=True)
        del panel
        self._process_events(100)

        self.assertIsNone(panel_reference())
        retained_pages = [
            type(reference()).__name__ for reference in page_references if reference() is not None
        ]
        self.assertTrue(
            not retained_pages,
            retained_pages,
        )
        import shiboken6

        for reference in form_references:
            form = reference()
            if form is None:
                continue
            self.assertFalse(shiboken6.isValid(form))
            self.assertFalse(vars(form))

    def test_ui_loader_never_reuses_a_dead_wrapper_type(self):
        import shiboken6

        retired_wrappers = []
        for _index in range(32):
            form = Gui.PySideUic.loadUi(":/panels/PageOpPocketFullEdit.ui")
            self.assertIsInstance(form.frame, QtGui.QFrame)
            self.assertIsInstance(
                form.minTravel,
                QtGui.QCheckBox,
            )
            retired_wrappers.extend(
                (
                    form,
                    form.frame,
                    form.minTravel,
                )
            )
            shiboken6.delete(form)
            self._process_events(0)
            self.assertFalse(shiboken6.isValid(form))

    def test_menu_task_operations_open_real_atomic_editors(self):
        for command_name in ("CAM_Custom", "CAM_Probe"):
            with self.subTest(command=command_name):
                self._clear_selection()
                if command_name == "CAM_Probe":
                    self._ensure_probe_tool_controller()
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name)
                self._process_events(100)
                self.assertTrue(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                created = [obj for obj in self.document.Objects if obj not in before]
                self.assertEqual(len(created), 1, command_name)
                self._assert_timeline_source_preserving(created[0])
                provider = created[0].ViewObject.Proxy
                self.assertTrue(
                    provider.supportsDocumentTimelineEdit(),
                    command_name,
                )
                self._dismiss_task(accept=False)
                self.assertEqual(
                    tuple(self.document.Objects),
                    before,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo,
                    command_name,
                )

                Gui.runCommand(command_name)
                self._process_events(100)
                self.assertTrue(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                self._dismiss_task(accept=True)
                accepted = [obj for obj in self.document.Objects if obj not in before]
                self.assertEqual(len(accepted), 1, command_name)
                self.assertTrue(accepted[0].isValid(), command_name)
                self.assertIn(
                    accepted[0],
                    self.job.Operations.Group,
                    command_name,
                )
                self._assert_timeline_source_preserving(accepted[0])
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo + 1,
                    command_name,
                )
                self.document.undo()
                self._process_events(100)
                self.assertEqual(
                    tuple(self.document.Objects),
                    before,
                    command_name,
                )

    def test_comment_and_stop_are_atomic_source_preserving_operations(self):
        job_name = str(self.job.Name)
        operation_name = str(self.operation.Name)
        for command_name in ("CAM_Comment", "CAM_Stop"):
            with self.subTest(command=command_name):
                self.job = self.document.getObject(job_name)
                self.operation = self.document.getObject(operation_name)
                self._clear_selection()
                before = frozenset(self.document.Objects)
                before_undo = int(self.document.UndoCount)

                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name)
                self._process_events(100)

                created = [obj for obj in self.document.Objects if obj not in before]
                self.assertEqual(len(created), 1, command_name)
                result = created[0]
                self.assertTrue(result.isValid(), command_name)
                self.assertTrue(result.Path.Commands, command_name)
                self.assertIn(
                    result,
                    self.job.Operations.Group,
                    command_name,
                )
                self._assert_timeline_source_preserving(result)
                self.assertFalse(
                    getattr(
                        result.ViewObject.Proxy,
                        "supportsDocumentTimelineEdit",
                        lambda: False,
                    )(),
                    command_name,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo + 1,
                    command_name,
                )

                self.document.undo()
                self._process_events(100)
                self.assertEqual(
                    frozenset(self.document.Objects),
                    before,
                    command_name,
                )

    def test_menu_commands_refuse_a_caller_transaction(self):
        for command_name in (
            "CAM_ExportTemplate",
            "CAM_ToolBitLibraryOpen",
            "CAM_Comment",
            "CAM_Stop",
            "CAM_Custom",
            "CAM_Probe",
            "CAM_PropertyBag",
        ):
            with self.subTest(command=command_name):
                self._set_context_for(command_name)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                self.document.openTransaction(f"Caller transaction for {command_name}")
                transaction = int(self.document.getBookedTransactionID())
                self._process_events()
                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name)
                self._process_events()
                self.assertEqual(
                    tuple(self.document.Objects),
                    before,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    transaction,
                    command_name,
                )
                App.closeActiveTransaction(True, transaction)
                self.assertFalse(self.document.HasPendingTransaction)

    def test_experimental_path_from_shape_is_one_owned_history_step(self):
        import PathScripts.PathUtils as PathUtils

        model = self.document.getObject("ContractModel")
        model_visibility = bool(model.ViewObject.Visibility)
        get_tool_controllers = PathUtils.getToolControllers
        before = frozenset(self.document.Objects)
        before_history = self._visible_timeline_names()
        before_undo = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Edge1")
        self._process_events()

        self.assertTrue(Gui.isCommandActive("CAM_PathShapeTC"))
        Gui.runCommand("CAM_PathShapeTC")
        self._process_events(100)

        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 2)
        operations = [obj for obj in created if obj in self.job.Operations.Group]
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        resource = next(obj for obj in created if obj is not operation)
        self._assert_timeline_source_preserving(operation)
        self._assert_timeline_resource(resource, operation)
        self.assertEqual(list(operation.Sources), [resource])
        self.assertFalse(resource.ViewObject.Visibility)
        self.assertTrue(operation.isValid())
        self.assertEqual(
            self._visible_timeline_names() - before_history,
            {operation.Name},
        )
        self.assertEqual(
            bool(model.ViewObject.Visibility),
            model_visibility,
        )
        self.assertIs(
            PathUtils.getToolControllers,
            get_tool_controllers,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )
        self.assertTrue(operation.Path.Commands)

        timeline = self.document.getObject("VibeCADTimeline")
        operation_index = list(timeline.Operations).index(operation)
        self.assertGreater(operation_index, 0)
        self.assertIs(
            timeline.Operations[operation_index - 1],
            resource,
        )
        previous = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        next_button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(next_button)
        previous.click()
        self._process_events(100)
        self.assertEqual(
            int(timeline.Position),
            operation_index - 1,
        )
        self.assertTrue(operation.Suppressed)
        self.assertFalse(operation.Path.Commands)
        self.assertFalse(operation.ViewObject.Visibility)
        self.assertFalse(resource.ViewObject.Visibility)

        next_button.click()
        self._process_events(100)
        self.assertEqual(
            int(timeline.Position),
            operation_index + 1,
        )
        self.assertFalse(operation.Suppressed)
        self.assertTrue(operation.Path.Commands)
        self.assertTrue(operation.ViewObject.Visibility)
        self.assertFalse(resource.ViewObject.Visibility)

        # The two marker moves are themselves exact, undoable user actions.
        # Unwind them before undoing the operation-creation action.
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 3,
        )
        while int(self.document.UndoCount) > before_undo:
            self.document.undo()
            self._process_events(100)
        self.assertEqual(frozenset(self.document.Objects), before)

    def test_path_area_does_not_adopt_an_unrelated_same_transaction_part(
        self,
    ):
        document = self.document
        model = document.getObject("ContractModel")

        class SameTransactionPartObserver:
            def __init__(self):
                self.injected = False
                self.decoy = None

            def slotOpenTransaction(self, opened_document, _name):
                if self.injected or opened_document.Name != document.Name:
                    return
                self.injected = True
                self.decoy = document.addObject(
                    "Part::Feature",
                    "ContractModel_Face1",
                )
                self.decoy.Shape = Part.makeBox(2.0, 2.0, 2.0)

        before = frozenset(document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        self._process_events()
        observer = SameTransactionPartObserver()
        App.addDocumentObserver(observer)
        try:
            Gui.runCommand("CAM_Area")
            self._process_events(100)
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.decoy)
        self.assertIs(
            document.getObject(observer.decoy.Name),
            observer.decoy,
        )
        created = [obj for obj in document.Objects if obj not in before]
        area = next(obj for obj in created if obj.TypeId == "Path::FeatureArea")
        resources = [
            obj
            for obj in created
            if obj is not area and obj is not observer.decoy and obj.isDerivedFrom("Part::Feature")
        ]
        self.assertEqual(len(resources), 1)
        resource = resources[0]
        self.assertEqual(resource.TypeId, "Part::FeaturePython")
        self.assertEqual(list(area.Sources), [resource])
        self.assertEqual(resource.VibeCADTimelineRole, "resource")
        self.assertIs(resource.VibeCADTimelineOwner, area)
        if "VibeCADTimelineOwner" in observer.decoy.PropertiesList:
            self.assertIsNone(observer.decoy.VibeCADTimelineOwner)
        timeline = document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        area_index = operations.index(area)
        self.assertGreater(area_index, 0)
        self.assertIs(operations[area_index - 1], resource)
        self.assertIsNot(operations[area_index - 1], observer.decoy)

    def test_experimental_area_and_workplane_have_exact_history_semantics(
        self,
    ):
        model = self.document.getObject("ContractModel")
        model_visibility = bool(model.ViewObject.Visibility)
        before_area = frozenset(self.document.Objects)
        before_history = self._visible_timeline_names()
        before_area_undo = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        self._process_events()

        self.assertTrue(Gui.isCommandActive("CAM_Area"))
        Gui.runCommand("CAM_Area")
        self._process_events(100)

        created = [obj for obj in self.document.Objects if obj not in before_area]
        self.assertEqual(len(created), 2)
        areas = [obj for obj in created if obj.TypeId == "Path::FeatureArea"]
        self.assertEqual(len(areas), 1)
        area = areas[0]
        resource = next(obj for obj in created if obj is not area)
        self._assert_timeline_source_preserving(area)
        self._assert_timeline_resource(resource, area)
        self.assertEqual(list(area.Sources), [resource])
        self.assertFalse(resource.ViewObject.Visibility)
        self.assertTrue(area.isValid())
        timeline = self.document.getObject("VibeCADTimeline")
        area_index = list(timeline.Operations).index(area)
        self.assertIs(
            timeline.Operations[area_index - 1],
            resource,
        )
        self.assertEqual(
            self._visible_timeline_names() - before_history,
            {area.Name},
        )
        self.assertEqual(
            bool(model.ViewObject.Visibility),
            model_visibility,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_area_undo + 1,
        )

        before_workplane_objects = tuple(self.document.Objects)
        before_workplane_is_null = area.WorkPlane.isNull()
        before_workplane_undo = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(area)
        Gui.Selection.addSelection(model, "Face1")
        self._process_events()
        self.assertTrue(Gui.isCommandActive("CAM_Area_Workplane"))
        Gui.runCommand("CAM_Area_Workplane")
        self._process_events(100)

        self.assertEqual(
            tuple(self.document.Objects),
            before_workplane_objects,
        )
        self.assertFalse(area.WorkPlane.isNull())
        self.assertTrue(area.WorkPlaneSourceEnabled)
        workplane_source, workplane_subelements = area.WorkPlaneSource
        self.assertIs(workplane_source, model)
        self.assertEqual(
            list(workplane_subelements),
            ["Face1"],
        )
        self.assertEqual(
            area.WorkPlaneSourceCollection,
            "",
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_workplane_undo + 1,
        )
        self.document.undo()
        self._process_events(100)
        area = self.document.getObject(area.Name)
        self.assertEqual(
            area.WorkPlane.isNull(),
            before_workplane_is_null,
        )
        self.assertFalse(area.WorkPlaneSourceEnabled)
        self.assertIsNone(area.WorkPlaneSource)

        self.document.undo()
        self._process_events(100)
        self.assertEqual(
            frozenset(self.document.Objects),
            before_area,
        )

    def test_selected_cam_subshape_is_parametric_and_survives_reopen(self):
        import tempfile

        model = self.document.getObject("ContractModel")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        self._process_events()
        before = frozenset(self.document.Objects)

        self.assertTrue(Gui.isCommandActive("CAM_Area"))
        Gui.runCommand("CAM_Area")
        self._process_events(100)

        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 2)
        area = next(obj for obj in created if obj.TypeId == "Path::FeatureArea")
        resource = next(obj for obj in created if obj is not area)
        self.assertEqual(resource.TypeId, "Part::FeaturePython")
        source, subnames = resource.Source
        self.assertIs(source, model)
        self.assertEqual(list(subnames), ["Face1"])
        self.assertEqual(list(area.Sources), [resource])
        first_area = float(resource.Shape.Area)
        self.assertGreater(first_area, 0.0)

        before_resize_undo = int(self.document.UndoCount)
        self.document.openTransaction("Resize CAM source model")
        model.Shape = Part.makeBox(20, 18, 9)
        self.document.commitTransaction()
        self.document.recompute()
        self._process_events(100)
        resized_area = float(resource.Shape.Area)
        self.assertNotAlmostEqual(resized_area, first_area)
        self.assertTrue(area.isValid())
        self.assertEqual(
            int(self.document.UndoCount),
            before_resize_undo + 1,
        )

        self.document.undo()
        self.document.recompute()
        self._process_events(100)
        resource = self.document.getObject(resource.Name)
        self.assertAlmostEqual(float(resource.Shape.Area), first_area)
        self.document.redo()
        self.document.recompute()
        self._process_events(100)
        resource = self.document.getObject(resource.Name)
        self.assertAlmostEqual(float(resource.Shape.Area), resized_area)

        document_name = str(self.document.Name)
        model_name = str(model.Name)
        area_name = str(area.Name)
        resource_name = str(resource.Name)
        job_name = str(self.job.Name)
        operation_name = str(self.operation.Name)
        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-parametric-subshape.FCStd"
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            self.operation = self.document.getObject(operation_name)
            model = self.document.getObject(model_name)
            area = self.document.getObject(area_name)
            resource = self.document.getObject(resource_name)

            source, subnames = resource.Source
            self.assertIs(source, model)
            self.assertEqual(list(subnames), ["Face1"])
            self.assertEqual(list(area.Sources), [resource])
            self.assertAlmostEqual(
                float(resource.Shape.Area),
                resized_area,
            )

            model.Shape = Part.makeBox(20, 22, 11)
            self.document.recompute()
            self._process_events(100)
            self.assertNotAlmostEqual(
                float(resource.Shape.Area),
                resized_area,
            )
            self.assertTrue(area.isValid())

    def test_cam_subshape_link_rejects_same_name_replacement(self):
        import PathCommands

        source = self.document.addObject(
            "Part::Feature",
            "ExactCAMSubshapeSource",
        )
        source.Shape = Part.makeBox(9, 8, 7)
        resource = PathCommands.createSubshapeResource(
            self.document,
            source,
            "Face1",
            name="ExactCAMSubshapeResource",
        )
        self.document.recompute()
        self.assertTrue(resource.isValid())
        self.assertFalse(resource.Shape.isNull())
        source_name = str(source.Name)
        source_id = int(source.ID)

        self.document.openTransaction(
            "Replace CAM subshape source",
        )
        self.document.removeObject(source_name)
        replacement = self.document.addObject(
            "Part::Feature",
            source_name,
        )
        replacement.Shape = Part.makeBox(3, 3, 3)
        self.assertEqual(replacement.Name, source_name)
        self.document.recompute()

        source_link = resource.Source
        linked_source = source_link[0] if source_link else None
        self.assertIsNot(linked_source, replacement)
        self.assertFalse(resource.isValid())
        self.assertTrue(resource.Shape.isNull())

        self.document.abortTransaction()
        self.document.recompute()
        source = self.document.getObject(source_name)
        resource = self.document.getObject(resource.Name)
        self.assertEqual(int(source.ID), source_id)
        self.assertIs(resource.Source[0], source)
        self.assertTrue(resource.isValid())
        self.assertFalse(resource.Shape.isNull())

    def test_area_workplane_link_is_authoritative_over_legacy_cache(self):
        import PathCommands

        area = self.area
        plane = self.document.addObject(
            "Part::Feature",
            "ExactCAMWorkplaneSource",
        )
        plane.Shape = Part.makeBox(12, 10, 2)
        area.WorkPlane = PathCommands.findShape(
            plane.Shape,
            "Face1",
        )
        area.WorkPlaneSource = (plane, ["Face1"])
        area.WorkPlaneSourceCollection = ""
        area.WorkPlaneSourceEnabled = True
        self.document.recompute()
        self.assertTrue(area.isValid())
        self.assertFalse(area.Shape.isNull())
        self.assertFalse(area.WorkPlane.isNull())
        cached_area = float(area.WorkPlane.Area)
        plane.Shape = Part.makeBox(12, 14, 3)
        self.document.recompute()
        self.assertTrue(area.isValid())
        self.assertNotAlmostEqual(
            float(area.WorkPlane.Area),
            cached_area,
        )
        plane_name = str(plane.Name)
        plane_id = int(plane.ID)

        self.document.openTransaction(
            "Replace authoritative CAM workplane",
        )
        self.document.removeObject(plane_name)
        replacement = self.document.addObject(
            "Part::Feature",
            plane_name,
        )
        replacement.Shape = Part.makeBox(4, 4, 1)
        self.assertEqual(replacement.Name, plane_name)
        self.document.recompute()

        workplane_link = area.WorkPlaneSource
        linked_plane = workplane_link[0] if workplane_link else None
        self.assertIsNot(linked_plane, replacement)
        self.assertTrue(area.WorkPlaneSourceEnabled)
        self.assertFalse(area.WorkPlane.isNull())
        self.assertFalse(area.isValid())
        self.assertTrue(area.Shape.isNull())

        self.document.abortTransaction()
        self.document.recompute()
        plane = self.document.getObject(plane_name)
        area = self.document.getObject(area.Name)
        self.assertEqual(int(plane.ID), plane_id)
        self.assertIs(area.WorkPlaneSource[0], plane)
        self.assertTrue(area.WorkPlaneSourceEnabled)
        self.assertTrue(area.isValid())
        self.assertFalse(area.Shape.isNull())

    def test_every_shipped_operation_is_a_source_preserving_history_step(self):
        job_name = str(self.job.Name)
        source_name = str(self.operation.Name)
        self._ensure_tool_controller(
            shape_id="thread-mill.fcstd",
            shape_name="threadmill",
            name="Contract Thread Mill",
        )
        self._ensure_tool_controller(
            shape_id="v-bit.fcstd",
            shape_name="vbit",
            name="Contract V-Bit",
        )

        for command_name in OPERATION_LEAF_COMMANDS:
            with self.subTest(command=command_name):
                self.job = self.document.getObject(job_name)
                self.operation = self.document.getObject(source_name)
                self.assertIsNotNone(self.job)
                self.assertIsNotNone(self.operation)
                self._clear_selection()

                before = frozenset(self.document.Objects)
                before_path = self.operation.Path.toGCode()
                before_visibility = bool(self.operation.ViewObject.Visibility)
                before_undo = int(self.document.UndoCount)

                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name)
                self._process_events(100)
                self.assertTrue(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                self._dismiss_task(accept=True)

                created = [obj for obj in self.document.Objects if obj not in before]
                self.assertEqual(
                    len(created),
                    1,
                    command_name,
                )
                result = created[0]
                self.assertTrue(result.isValid(), command_name)
                self.assertIn(
                    result,
                    self.job.Operations.Group,
                    command_name,
                )
                self._assert_timeline_source_preserving(result)
                self.assertTrue(
                    result.ViewObject.Proxy.supportsDocumentTimelineEdit(),
                    command_name,
                )
                self.assertEqual(
                    self.operation.Path.toGCode(),
                    before_path,
                    command_name,
                )
                self.assertEqual(
                    bool(self.operation.ViewObject.Visibility),
                    before_visibility,
                    command_name,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo + 1,
                    command_name,
                )

                self.document.undo()
                self._process_events(100)
                self.job = self.document.getObject(job_name)
                self.operation = self.document.getObject(source_name)
                self.assertEqual(
                    frozenset(self.document.Objects),
                    before,
                    command_name,
                )
                self.assertEqual(
                    self.operation.Path.toGCode(),
                    before_path,
                    command_name,
                )
                self.assertEqual(
                    bool(self.operation.ViewObject.Visibility),
                    before_visibility,
                    command_name,
                )

    def test_source_preserving_operation_keeps_hidden_prior_state_on_reopen_and_delete(
        self,
    ):
        import tempfile

        self.operation.ViewObject.Visibility = False
        self._clear_selection()
        before = frozenset(self.document.Objects)
        Gui.runCommand("CAM_Profile")
        self._process_events(100)
        self._dismiss_task(accept=True)

        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 1)
        result = created[0]
        result_name = str(result.Name)
        source_name = str(self.operation.Name)
        self._assert_timeline_source_preserving(result)
        self.assertFalse(self.operation.ViewObject.Visibility)

        timeline = self.document.getObject("VibeCADTimeline")
        result_index = list(timeline.Operations).index(result)
        self._move_timeline_to(result_index)
        self.assertTrue(result.Suppressed)
        self.assertFalse(self.operation.ViewObject.Visibility)
        source_index = list(timeline.Operations).index(self.operation)
        self.assertFalse(bool(timeline.VisibilityAtEnd[source_index]))

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-source-preserving-operation.FCStd"
            document_name = self.document.Name
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.operation = self.document.getObject(source_name)
            result = self.document.getObject(result_name)
            self.job = result.Proxy.getJob(result)

            self.assertTrue(result.Suppressed)
            timeline = self.document.getObject("VibeCADTimeline")
            source_index = list(timeline.Operations).index(self.operation)
            self.assertFalse(bool(timeline.VisibilityAtEnd[source_index]))
            self.assertFalse(self.operation.ViewObject.Visibility)
            self._assert_timeline_source_preserving(result)

            self._move_timeline_to(len(self.document.getObject("VibeCADTimeline").Operations))
            self.assertFalse(result.Suppressed)
            self.assertFalse(self.operation.ViewObject.Visibility)

            self.document.openTransaction("Delete source-preserving CAM operation")
            self.document.removeObject(result_name)
            self.document.commitTransaction()
            self.document.recompute()
            self._process_events(100)

            self.assertIsNone(self.document.getObject(result_name))
            self.assertFalse(self.operation.ViewObject.Visibility)

    def test_feature_python_without_gui_proxy_preserves_saved_visibility(
        self,
    ):
        import tempfile

        hidden = self.document.addObject(
            "App::FeaturePython",
            "HiddenNoGuiProxy",
        )
        visible = self.document.addObject(
            "App::FeaturePython",
            "VisibleNoGuiProxy",
        )
        self.assertIsNone(hidden.ViewObject.Proxy)
        self.assertIsNone(visible.ViewObject.Proxy)
        hidden.Visibility = False
        visible.Visibility = True
        hidden_name = str(hidden.Name)
        visible_name = str(visible.Name)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/feature-python-visibility.FCStd"
            document_name = self.document.Name
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            hidden = self.document.getObject(hidden_name)
            visible = self.document.getObject(visible_name)

            self.assertFalse(hidden.Visibility)
            self.assertFalse(hidden.ViewObject.Visibility)
            self.assertTrue(visible.Visibility)
            self.assertTrue(visible.ViewObject.Visibility)

    def test_existing_operation_edit_uses_an_exact_captured_transaction(self):
        self._clear_selection()
        before_creation = frozenset(self.document.Objects)
        Gui.runCommand("CAM_Profile")
        self._process_events(100)
        self._dismiss_task(accept=True)
        created = [obj for obj in self.document.Objects if obj not in before_creation]
        self.assertEqual(len(created), 1)
        operation = created[0]
        self.assertIsNotNone(operation.ViewObject.Proxy)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        self._process_events()
        before_objects = tuple(self.document.Objects)
        before_active = bool(operation.Active)
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("Std_Edit"))
        Gui.runCommand("Std_Edit")
        self._process_events(100)

        gui_document = Gui.getDocument(self.document.Name)
        task = Gui.Control.activeTaskDialog(gui_document)
        transaction = int(self.document.getBookedTransactionID())
        owner = operation.ViewObject.Proxy.panel.transaction
        self.assertIsNotNone(task)
        self.assertNotEqual(transaction, 0)
        self.assertIs(owner.document, self.document)
        self.assertEqual(owner.transaction_id, transaction)
        self.assertTrue(owner.owns_transaction())
        self.assertEqual(
            Gui.Control.ownsCommandTransaction(
                gui_document,
                transaction,
            ),
            task.ownsCommandTransaction(transaction),
        )

        operation.Active = not before_active
        self.document.recompute()

        other = App.newDocument("VibeCADCAMBackgroundTask")
        other.UndoMode = True
        marker = other.addObject(
            "App::FeaturePython",
            "UntouchedExistingEditMarker",
        )
        other.recompute()
        other_before = tuple(other.Objects)
        other_undo = int(other.UndoCount)
        App.setActiveDocument(other.Name)
        self._process_events()

        task.reject()
        self._process_events(100)

        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(bool(operation.Active), before_active)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertEqual(tuple(other.Objects), other_before)
        self.assertIs(other.getObject(marker.Name), marker)
        self.assertEqual(int(other.UndoCount), other_undo)
        self.assertEqual(int(other.getBookedTransactionID()), 0)

    def test_operation_task_stays_bound_to_its_launch_document(self):
        """Changing the active tab cannot redirect task Accept or Cancel."""

        for accept in (False, True):
            with self.subTest(accept=accept):
                App.setActiveDocument(self.document.Name)
                self._clear_selection()
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)

                Gui.runCommand("CAM_Profile")
                self._process_events(100)
                gui_document = Gui.getDocument(self.document.Name)
                task = Gui.Control.activeTaskDialog(gui_document)
                self.assertIsNotNone(task)
                task_transaction = int(self.document.getBookedTransactionID())
                self.assertNotEqual(task_transaction, 0)
                self.assertTrue(
                    Gui.Control.ownsCommandTransaction(
                        gui_document,
                        task_transaction,
                    )
                )
                self.assertTrue(task.ownsCommandTransaction(task_transaction))

                other = App.newDocument("VibeCADCAMBackgroundTask")
                other.UndoMode = True
                marker = other.addObject(
                    "App::FeaturePython",
                    "UntouchedMarker",
                )
                other.recompute()
                other_before = tuple(other.Objects)
                other_undo = int(other.UndoCount)
                App.setActiveDocument(other.Name)
                self._process_events()

                if accept:
                    task.accept()
                else:
                    task.reject()
                self._process_events(100)

                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                )
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(tuple(other.Objects), other_before)
                self.assertIs(other.getObject(marker.Name), marker)
                self.assertEqual(int(other.UndoCount), other_undo)
                self.assertEqual(
                    int(other.getBookedTransactionID()),
                    0,
                )
                self.assertFalse(Gui.Control.activeDialog(gui_document))

                App.closeDocument(other.Name)
                App.setActiveDocument(self.document.Name)
                self._process_events()

                if accept:
                    created = [obj for obj in self.document.Objects if obj not in before]
                    self.assertEqual(len(created), 1)
                    self.assertTrue(created[0].isValid())
                    self.assertEqual(
                        int(self.document.UndoCount),
                        before_undo + 1,
                    )
                    self.document.undo()
                    self._process_events()
                else:
                    self.assertEqual(
                        tuple(self.document.Objects),
                        before,
                    )
                    self.assertEqual(
                        int(self.document.UndoCount),
                        before_undo,
                    )

    def test_task_transaction_survives_launch_document_deletion(self):
        from Path.CommandBoundary import TaskDocumentTransaction

        launch_document = App.newDocument("VibeCADCAMDeletedTaskDocument")
        launch_document.UndoMode = True
        marker = launch_document.addObject(
            "App::FeaturePython",
            "DeletedTaskMarker",
        )
        transaction = TaskDocumentTransaction(
            marker,
            "Deleted CAM task contract",
        )
        self.assertTrue(transaction.owns_transaction())

        App.closeDocument(launch_document.Name)
        App.setActiveDocument(self.document.Name)
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        self.assertFalse(transaction.is_open())
        self.assertFalse(transaction.abort())
        self.assertFalse(transaction.recompute_after_close())
        transaction.close_dialog()

        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )

    def test_task_transaction_rolls_back_with_undo_initially_disabled(self):
        from Path.CommandBoundary import TaskDocumentTransaction

        marker = self.document.addObject(
            "App::FeaturePython",
            "TaskUndoDisabledMarker",
        )
        marker.addProperty("App::PropertyInteger", "Value")
        marker.Value = 7
        self.document.recompute()
        self.document.UndoMode = False

        transaction = TaskDocumentTransaction(
            marker,
            "Undo-disabled CAM task",
        )
        self.assertEqual(int(self.document.UndoMode), 1)
        self.assertFalse(
            Gui.Control.ownsCommandTransaction(
                Gui.getDocument(self.document.Name),
                int(self.document.getBookedTransactionID()),
            )
        )
        marker.Value = 42
        transaction.abort()

        self.assertEqual(marker.Value, 7)
        self.assertEqual(int(self.document.UndoMode), 0)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.document.UndoMode = True

    def test_ribbon_task_launch_never_borrows_a_caller_transaction(self):
        from Path.CommandBoundary import (
            TaskDocumentTransaction,
            begin_task_launch,
            ensure_task_transaction,
        )

        self.document.openTransaction("Intentional direct Python caller")
        caller = int(self.document.getBookedTransactionID())
        self.assertNotEqual(caller, 0)

        # Public direct Create helpers keep their documented additive path.
        self.assertEqual(
            ensure_task_transaction(
                "Direct Create compatibility",
                self.document,
            ),
            caller,
        )
        with self.assertRaises(RuntimeError):
            TaskDocumentTransaction(
                self.operation,
                "Existing-object editor must not borrow caller",
            )
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            caller,
        )

        # A ribbon/task launch has an explicit owner and cannot infer that the
        # same caller transaction belongs to it.
        with self.assertRaises(RuntimeError):
            begin_task_launch(
                "Ribbon task must refuse caller",
                self.document,
            )
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            caller,
        )
        App.closeActiveTransaction(True, caller)

        # A launch which never reaches a task owner cannot leave a pending
        # provisional transaction behind.
        launch = begin_task_launch(
            "Ribbon task without an editor",
            self.document,
        )
        with self.assertRaises(RuntimeError):
            launch.require_claimed()
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_core_job_factory_atomically_publishes_its_exact_resource_graph(self):
        import Path.Main.Job as PathJob

        model = self.document.addObject(
            "Part::Feature",
            "AtomicJobModel",
        )
        model.Shape = Part.makeBox(8, 7, 6)
        self.document.recompute()
        before_names = {obj.Name for obj in self.document.Objects}
        before_undo = int(self.document.UndoCount)

        self.document.openTransaction(
            "Create exact atomic CAM Job",
        )
        transaction = int(self.document.getBookedTransactionID())
        self.assertNotEqual(transaction, 0)
        job = PathJob.Create(
            "AtomicJob",
            [model],
            createDefaultToolController=False,
        )

        resources = [
            job.Operations,
            job.SetupSheet,
            job.Model,
            *job.Model.Group,
            job.Tools,
            job.Stock,
        ]
        self.assertEqual(len(resources), 6)
        self.assertEqual(len({id(obj) for obj in resources}), 6)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(
            list(timeline.Operations)[-(len(resources) + 1) :],
            [*resources, job],
        )
        self.assertEqual(job.VibeCADTimelineRole, "operation")
        for resource in resources:
            self._assert_timeline_resource(resource, job)
        self.assertNotIn(
            "VibeCADTimelineRole",
            model.PropertiesList,
        )
        job_name = job.Name
        resource_names = [resource.Name for resource in resources]

        App.closeActiveTransaction(False, transaction)
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )
        created_names = {obj.Name for obj in self.document.Objects} - before_names
        self.assertEqual(
            created_names,
            {
                job_name,
                *resource_names,
            },
        )

        self.document.undo()
        self.assertFalse(created_names & {obj.Name for obj in self.document.Objects})
        self.document.redo()
        restored_job = self.document.getObject(job_name)
        self.assertIsNotNone(restored_job)
        restored_timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(
            [obj.Name for obj in restored_timeline.Operations[-(len(resources) + 1) :]],
            [*resource_names, job_name],
        )

    def test_failed_core_job_factory_publishes_before_preserving_exception(self):
        import Path.Main.Job as PathJob

        model = self.document.addObject(
            "Part::Feature",
            "FailedAtomicJobModel",
        )
        model.Shape = Part.makeBox(9, 8, 7)
        self.document.recompute()
        before_names = {obj.Name for obj in self.document.Objects}

        original_setup_stock = PathJob.ObjectJob.setupStock

        def fail_after_stock(proxy, job):
            original_setup_stock(proxy, job)
            raise RuntimeError("forced Job construction failure")

        self.document.openTransaction(
            "Fail exact atomic CAM Job",
        )
        transaction = int(self.document.getBookedTransactionID())
        self.assertNotEqual(transaction, 0)
        with patch.object(
            PathJob.ObjectJob,
            "setupStock",
            fail_after_stock,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "forced Job construction failure",
            ):
                PathJob.Create(
                    "FailedAtomicJob",
                    [model],
                    createDefaultToolController=False,
                )

        job = self.document.getObject("FailedAtomicJob")
        self.assertIsNotNone(job)
        resources = [
            job.Operations,
            job.SetupSheet,
            job.Model,
            *job.Model.Group,
            job.Tools,
            job.Stock,
        ]
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(
            list(timeline.Operations)[-(len(resources) + 1) :],
            [*resources, job],
        )
        for resource in resources:
            self._assert_timeline_resource(resource, job)
        self.assertNotIn(
            "VibeCADTimelineRole",
            model.PropertiesList,
        )

        App.closeActiveTransaction(True, transaction)
        self.assertFalse(
            ({obj.Name for obj in self.document.Objects} - before_names) - {"VibeCADTimeline"}
        )

    def test_job_stock_replacement_reconciles_the_exact_retained_graph(self):
        import Path.Base.Util as PathUtil
        import Path.Main.Stock as PathStock

        timeline = self.document.getObject("VibeCADTimeline")
        old_stock = self.job.Stock
        old_stock_name = old_stock.Name
        old_stock_id = int(old_stock.ID)
        old_resources = [
            candidate
            for candidate in list(timeline.Operations)
            if candidate is not self.job
            and PathUtil._timelineSemanticRoot(
                candidate,
                self.document,
            )
            is self.job
        ]
        old_resource_identities = [(resource.Name, int(resource.ID)) for resource in old_resources]
        replacement_index = old_resources.index(old_stock)
        before_undo = int(self.document.UndoCount)

        self.document.openTransaction(
            "Replace exact CAM Job Stock",
        )
        transaction = int(self.document.getBookedTransactionID())
        token = PathUtil.stageTimelineDirectResourceReplacement(
            self.job,
            old_stock,
        )
        self.document.removeObject(old_stock_name)
        replacement = PathStock.CreateFromBase(self.job)
        self.job.Stock = replacement
        PathUtil.finalizeTimelineDirectResourceReplacement(
            self.job,
            token,
            replacement,
        )
        replacement_name = replacement.Name
        replacement_id = int(replacement.ID)
        App.closeActiveTransaction(False, transaction)

        expected_identities = list(old_resource_identities)
        expected_identities[replacement_index] = (
            replacement_name,
            replacement_id,
        )
        final_resources = [
            candidate
            for candidate in list(timeline.Operations)
            if candidate is not self.job
            and PathUtil._timelineSemanticRoot(
                candidate,
                self.document,
            )
            is self.job
        ]
        self.assertEqual(
            [(resource.Name, int(resource.ID)) for resource in final_resources],
            expected_identities,
        )
        self.assertIs(self.job.Stock, replacement)
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )

        self.document.undo()
        restored_old_stock = self.document.getObject(old_stock_name)
        self.assertIsNotNone(restored_old_stock)
        self.assertEqual(int(restored_old_stock.ID), old_stock_id)
        self.assertIs(self.job.Stock, restored_old_stock)

        self.document.redo()
        restored_replacement = self.document.getObject(replacement_name)
        self.assertIsNotNone(restored_replacement)
        self.assertEqual(
            int(restored_replacement.ID),
            replacement_id,
        )
        self.assertIs(self.job.Stock, restored_replacement)

    def test_failed_staged_stock_replacement_aborts_without_stale_rewrite(self):
        import Path.Base.Util as PathUtil

        timeline = self.document.getObject("VibeCADTimeline")
        old_stock = self.job.Stock
        old_stock_name = old_stock.Name
        old_stock_id = int(old_stock.ID)
        original_history = [(obj.Name, int(obj.ID)) for obj in timeline.Operations]

        self.document.openTransaction(
            "Fail exact CAM Stock replacement",
        )
        transaction = int(self.document.getBookedTransactionID())
        PathUtil.stageTimelineDirectResourceReplacement(
            self.job,
            old_stock,
        )
        self.job.Stock = None
        self.document.removeObject(old_stock_name)
        App.closeActiveTransaction(True, transaction)

        restored_stock = self.document.getObject(old_stock_name)
        self.assertIsNotNone(restored_stock)
        self.assertEqual(int(restored_stock.ID), old_stock_id)
        self.assertIs(self.job.Stock, restored_stock)
        self.assertEqual(
            [(obj.Name, int(obj.ID)) for obj in timeline.Operations],
            original_history,
        )

        # A fresh exact stage proves the failed transaction left no pending
        # reconciliation in the native timeline.
        self.document.openTransaction(
            "Restage exact CAM Stock replacement",
        )
        retry_transaction = int(self.document.getBookedTransactionID())
        PathUtil.stageTimelineDirectResourceReplacement(
            self.job,
            restored_stock,
        )
        App.closeActiveTransaction(True, retry_transaction)
        self.assertEqual(
            [(obj.Name, int(obj.ID)) for obj in timeline.Operations],
            original_history,
        )

    def test_job_graph_edit_places_new_nested_child_before_retained_owner(self):
        import Path.Base.Util as PathUtil

        timeline = self.document.getObject("VibeCADTimeline")
        retained_owner = self.job.Model
        before_undo = int(self.document.UndoCount)

        self.document.openTransaction(
            "Add exact nested CAM Job resource",
        )
        transaction = int(self.document.getBookedTransactionID())
        token = PathUtil.stageTimelineResourceGraphEdit(self.job)
        child = self.document.addObject(
            "App::FeaturePython",
            "NestedJobResource",
        )
        PathUtil.markTimelineResource(child, retained_owner)
        PathUtil.recordTimelineResourceGraphAddition(
            self.job,
            token,
            (child,),
        )
        PathUtil.finalizeTimelineResourceGraphEdit(
            self.job,
            token,
        )
        child_name = child.Name
        owner_name = retained_owner.Name
        App.closeActiveTransaction(False, transaction)

        history_names = [obj.Name for obj in timeline.Operations]
        self.assertEqual(
            history_names.index(child_name) + 1,
            history_names.index(owner_name),
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )

        self.document.undo()
        self.assertIsNone(self.document.getObject(child_name))
        self.document.redo()
        restored_child = self.document.getObject(child_name)
        restored_owner = self.document.getObject(owner_name)
        self.assertIsNotNone(restored_child)
        self.assertIs(
            restored_child.VibeCADTimelineOwner,
            restored_owner,
        )

    def test_operation_activation_requires_a_real_controller_and_tool(self):
        from Path.CommandBoundary import active_jobs

        real_controllers = list(self.job.Tools.Group)
        self.job.Tools.Group = []
        fake = self.document.addObject(
            "App::FeaturePython",
            "FakeToolController",
        )
        fake.addProperty("App::PropertyLink", "Tool")
        fake.Tool = self.document.getObject("ContractModel")
        self.job.Tools.addObject(fake)
        self.document.recompute()

        self.assertEqual(active_jobs(require_tool=True), [])
        self.assertFalse(Gui.isCommandActive("CAM_Profile"))

        self.job.Tools.Group = real_controllers
        self.document.removeObject(fake.Name)
        self.document.recompute()
        self.assertEqual(
            active_jobs(require_tool=True),
            [self.job],
        )
        self.assertTrue(Gui.isCommandActive("CAM_Profile"))

    def test_operation_factory_uses_its_parent_job_document(self):
        import Path.Op.Custom as PathCustom

        other = App.newDocument("VibeCADCAMBackgroundTask")
        other.UndoMode = True
        other_before = tuple(other.Objects)
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)
        App.setActiveDocument(other.Name)
        self._process_events()

        self.document.openTransaction("Create CAM operation in its parent Job document")
        operation = PathCustom.Create(
            "BackgroundTabContractOperation",
            parentJob=self.job,
        )
        self.document.recompute()
        self.document.commitTransaction()
        self._process_events()

        self.assertIs(operation.Document, self.document)
        self.assertIn(operation, self.job.Operations.Group)
        self._assert_timeline_source_preserving(operation)
        self.assertEqual(tuple(other.Objects), other_before)
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )

        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(tuple(other.Objects), other_before)
        App.closeDocument(other.Name)
        App.setActiveDocument(self.document.Name)
        self._process_events()

    def test_dressup_task_cancel_accept_and_one_undo(self):
        self._select_operation()
        before = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("CAM_DressupAxisMap"))
        Gui.runCommand("CAM_DressupAxisMap")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before)
        self.assertEqual(int(self.document.UndoCount), before_undo)

        self._select_operation()
        Gui.runCommand("CAM_DressupAxisMap")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task(accept=True)

        created = [obj for obj in self.document.Objects if obj not in before]
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].isValid())
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before)

    def test_dressup_task_stays_bound_to_its_launch_document(self):
        for accept in (False, True):
            with self.subTest(accept=accept):
                App.setActiveDocument(self.document.Name)
                self._select_operation()
                before = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)

                Gui.runCommand("CAM_DressupAxisMap")
                self._process_events(100)
                gui_document = Gui.getDocument(self.document.Name)
                task = Gui.Control.activeTaskDialog(gui_document)
                self.assertIsNotNone(task)
                task_transaction = int(self.document.getBookedTransactionID())
                self.assertNotEqual(task_transaction, 0)
                self.assertTrue(
                    Gui.Control.ownsCommandTransaction(
                        gui_document,
                        task_transaction,
                    )
                )
                self.assertTrue(task.ownsCommandTransaction(task_transaction))

                other = App.newDocument("VibeCADCAMBackgroundTask")
                other.UndoMode = True
                marker = other.addObject(
                    "App::FeaturePython",
                    "UntouchedDressupMarker",
                )
                other.recompute()
                other_before = tuple(other.Objects)
                other_undo = int(other.UndoCount)
                App.setActiveDocument(other.Name)
                self._process_events()

                if accept:
                    task.accept()
                else:
                    task.reject()
                self._process_events(100)

                self.assertFalse(Gui.Control.activeDialog(gui_document))
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                )
                self.assertEqual(tuple(other.Objects), other_before)
                self.assertIs(other.getObject(marker.Name), marker)
                self.assertEqual(int(other.UndoCount), other_undo)
                self.assertEqual(
                    int(other.getBookedTransactionID()),
                    0,
                )

                App.closeDocument(other.Name)
                App.setActiveDocument(self.document.Name)
                self._process_events()

                if accept:
                    created = [obj for obj in self.document.Objects if obj not in before]
                    self.assertEqual(len(created), 1)
                    self.assertTrue(created[0].isValid())
                    self.assertEqual(
                        int(self.document.UndoCount),
                        before_undo + 1,
                    )
                    self.document.undo()
                    self._process_events()
                else:
                    self.assertEqual(
                        tuple(self.document.Objects),
                        before,
                    )
                    self.assertEqual(
                        int(self.document.UndoCount),
                        before_undo,
                    )

    def _assert_refuses_caller_transaction(self, command_name):
        self._select_operation()
        before_objects = tuple(self.document.Objects)
        before_active = bool(self.operation.Active)
        before_undo = int(self.document.UndoCount)
        self.document.openTransaction(f"Caller transaction for {command_name}")
        transaction = self.document.getBookedTransactionID()
        self.assertNotEqual(transaction, 0)
        self._process_events()
        self.assertFalse(Gui.isCommandActive(command_name))

        Gui.runCommand(command_name)
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(bool(self.operation.Active), before_active)
        self.assertEqual(self.document.getBookedTransactionID(), transaction)
        self.assertEqual(int(self.document.UndoCount), before_undo)

        self.document.abortTransaction()
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_immediate_commands_refuse_caller_owned_transactions(self):
        for command_name in ("CAM_OpActiveToggle", "CAM_OperationCopy"):
            with self.subTest(command=command_name):
                self._assert_refuses_caller_transaction(command_name)

    def test_modify_commands_are_atomic_validated_single_undo_gestures(self):
        commands = (
            "CAM_Array",
            "CAM_SimpleCopy",
            "CAM_DressupArray",
            "CAM_DressupMirror",
            "CAM_DressupRampEntry",
        )
        for command_name in commands:
            with self.subTest(command=command_name):
                self._select_operation()
                before_objects = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                before_visibility = bool(self.operation.ViewObject.Visibility)

                self.assertTrue(Gui.isCommandActive(command_name))
                Gui.runCommand(command_name)
                self._process_events(100)

                created = [obj for obj in self.document.Objects if obj not in before_objects]
                self.assertEqual(len(created), 1)
                self.assertTrue(created[0].isValid())
                self.assertIn(created[0], self.job.Operations.Group)
                if command_name.startswith("CAM_Dressup"):
                    self._assert_timeline_replaces(
                        created[0],
                        [self.operation],
                    )
                else:
                    self._assert_timeline_source_preserving(created[0])
                self.assertFalse(
                    getattr(
                        created[0].ViewObject.Proxy,
                        "supportsDocumentTimelineEdit",
                        lambda: False,
                    )(),
                    command_name,
                )
                self._double_click_history_operation(created[0])
                self.assertFalse(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                self.assertFalse(Gui.Control.activeDialog())
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo + 1,
                )

                self.document.undo()
                self._process_events()
                self.assertEqual(
                    tuple(self.document.Objects),
                    before_objects,
                )
                self.assertEqual(
                    bool(self.operation.ViewObject.Visibility),
                    before_visibility,
                )

    def test_modify_command_validation_failure_aborts_every_change(self):
        import Path.Dressup.Gui.Array as DressupArrayGui
        import Path.Dressup.Gui.Mirror as MirrorGui
        import Path.Dressup.Gui.RampEntry as RampEntryGui
        import Path.Op.Gui.Array as ArrayGui
        import Path.Op.Gui.SimpleCopy as SimpleCopyGui

        commands = (
            ("CAM_Array", ArrayGui, "_validate_array_result"),
            ("CAM_SimpleCopy", SimpleCopyGui, "_validate_copy_result"),
            ("CAM_DressupArray", DressupArrayGui, "_validate_result"),
            ("CAM_DressupMirror", MirrorGui, "_validate_result"),
            ("CAM_DressupRampEntry", RampEntryGui, "_validate_result"),
        )
        for command_name, module, validator in commands:
            with self.subTest(command=command_name):
                self._select_operation()
                before_objects = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)
                before_visibility = bool(self.operation.ViewObject.Visibility)

                with patch.object(
                    module,
                    validator,
                    side_effect=RuntimeError("forced validation failure"),
                ):
                    try:
                        Gui.runCommand(command_name)
                    except RuntimeError:
                        pass
                    self._process_events(100)

                self.assertEqual(
                    tuple(self.document.Objects),
                    before_objects,
                )
                self.assertEqual(
                    bool(self.operation.ViewObject.Visibility),
                    before_visibility,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo,
                )
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    0,
                )

    def test_dressup_delete_restores_its_base_when_another_tab_is_active(self):
        for command_name in (
            "CAM_DressupMirror",
            "CAM_DressupRampEntry",
        ):
            with self.subTest(command=command_name):
                App.setActiveDocument(self.document.Name)
                self._select_operation()
                before = frozenset(self.document.Objects)
                Gui.runCommand(command_name)
                self._process_events(100)

                created = [obj for obj in self.document.Objects if obj not in before]
                self.assertEqual(len(created), 1)
                dressup = created[0]
                dressup_name = str(dressup.Name)
                self.assertIs(dressup.Base, self.operation)
                self.assertFalse(self.operation.ViewObject.Visibility)

                other = App.newDocument("VibeCADCAMBackgroundTask")
                other.UndoMode = True
                marker = other.addObject(
                    "Part::Feature",
                    self.operation.Name,
                )
                marker.Shape = Part.makeBox(1, 1, 1)
                marker.ViewObject.Visibility = False
                other.recompute()
                App.setActiveDocument(other.Name)
                self._process_events()

                self.document.openTransaction(f"Delete {command_name} outside its active tab")
                self.document.removeObject(dressup_name)
                self.document.commitTransaction()
                self.document.recompute()
                self._process_events()

                self.assertIsNone(self.document.getObject(dressup_name))
                self.assertIn(
                    self.operation,
                    self.job.Operations.Group,
                )
                self.assertTrue(self.operation.ViewObject.Visibility)
                self.assertFalse(marker.ViewObject.Visibility)

                App.closeDocument(other.Name)
                App.setActiveDocument(self.document.Name)
                self._process_events()

    def test_holding_tags_cancel_accept_and_validation_are_atomic(self):
        import Path.Dressup.Gui.Tags as TagsGui

        self._select_operation()
        before_objects = tuple(self.document.Objects)
        before_operations = tuple(self.job.Operations.Group)
        before_undo = int(self.document.UndoCount)
        timeline = self.document.getObject("VibeCADTimeline")
        source_index = list(timeline.Operations).index(self.operation)
        self.assertTrue(self.operation.ViewObject.Visibility)
        self.assertTrue(bool(timeline.VisibilityAtEnd[source_index]))

        self.assertTrue(Gui.isCommandActive("CAM_DressupTag"))
        Gui.runCommand("CAM_DressupTag")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(
            tuple(self.job.Operations.Group),
            before_operations,
        )
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertTrue(self.operation.ViewObject.Visibility)

        self._select_operation()
        Gui.runCommand("CAM_DressupTag")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task(accept=True)
        created = [obj for obj in self.document.Objects if obj not in before_objects]
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].isValid())
        self.assertIn(created[0], self.job.Operations.Group)
        self.assertNotIn(self.operation, self.job.Operations.Group)
        self._assert_timeline_replaces(
            created[0],
            [self.operation],
        )
        self.assertFalse(self.operation.Visibility)
        self.assertFalse(self.operation.ViewObject.Visibility)
        self.assertTrue(created[0].Visibility)
        self.assertTrue(created[0].ViewObject.Visibility)
        timeline = self.document.getObject("VibeCADTimeline")
        source_index = list(timeline.Operations).index(self.operation)
        created_index = list(timeline.Operations).index(created[0])
        self.assertFalse(bool(timeline.VisibilityAtEnd[source_index]))
        self.assertTrue(bool(timeline.VisibilityAtEnd[created_index]))
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)
        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(
            tuple(self.job.Operations.Group),
            before_operations,
        )
        timeline = self.document.getObject("VibeCADTimeline")
        source_index = list(timeline.Operations).index(self.operation)
        self.assertTrue(bool(timeline.VisibilityAtEnd[source_index]))
        self.assertTrue(
            self.operation.Visibility,
            "Undo restored the accepted timeline baseline but not the "
            "source operation's persistent visibility",
        )
        self.assertTrue(self.operation.ViewObject.Visibility)

        self._select_operation()
        Gui.runCommand("CAM_DressupTag")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        with patch.object(
            TagsGui,
            "_validate_result",
            side_effect=RuntimeError("forced validation failure"),
        ):
            self.assertFalse(Gui.Control.activeTaskDialog().accept())
            self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(int(self.document.getBookedTransactionID()), 0)
        self.assertTrue(self.operation.ViewObject.Visibility)

    def test_toggle_is_one_validated_undoable_operation(self):
        self._select_operation()
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("CAM_OpActiveToggle"))
        Gui.runCommand("CAM_OpActiveToggle")
        self._process_events()

        self.assertFalse(self.operation.Active)
        self.assertTrue(self.operation.isValid())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events()
        self.assertTrue(self.operation.Active)

    def test_future_and_suppressed_cam_inputs_disable_human_mutators(self):
        import Path.Dressup.Utils as PathDressup
        import Path.Op.Gui.Base as PathOpGui
        from Path.CommandBoundary import (
            active_jobs,
            is_timeline_input_usable,
        )

        commands = (
            "CAM_OpActiveToggle",
            "CAM_OperationCopy",
            "CAM_Array",
            "CAM_SimpleCopy",
            *DRESSUP_LEAF_COMMANDS,
            "CAM_SetStartPoint",
        )
        operation_readers = (
            "CAM_PostSelected",
            "CAM_Inspect",
        )
        job_readers = (
            "CAM_Post",
            "CAM_Sanity",
        )
        self._select_job()
        for command_name in job_readers:
            self.assertTrue(
                Gui.isCommandActive(command_name),
                command_name,
            )
        self._select_operation()
        for command_name in (*commands, *operation_readers):
            self.assertTrue(
                Gui.isCommandActive(command_name),
                command_name,
            )

        timeline = self.document.getObject("VibeCADTimeline")
        operation_index = list(timeline.Operations).index(self.operation)
        self._move_timeline_to(operation_index)
        self._select_operation()
        self.assertIs(
            Gui.Selection.getSelection()[0],
            self.operation,
        )
        self.assertFalse(
            is_timeline_input_usable(
                self.operation,
                self.document,
            )
        )
        self.assertIsNone(PathDressup.selection())
        self.assertFalse(PathOpGui.CommandSetStartPoint().IsActive())
        for command_name in (*commands, *operation_readers):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

        job_index = list(timeline.Operations).index(self.job)
        self._move_timeline_to(job_index)
        self.assertFalse(active_jobs())
        self._select_job()
        for command_name in job_readers:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )
        for command_name in OPERATION_LEAF_COMMANDS:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

        self._move_timeline_to(len(timeline.Operations))
        self.operation.Suppressed = True
        self.document.recompute()
        self._select_operation()
        self.assertFalse(
            is_timeline_input_usable(
                self.operation,
                self.document,
            )
        )
        for command_name in commands:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )
        self.operation.Suppressed = False
        self.document.recompute()

    def test_exact_cam_identity_rejects_same_name_replacement(self):
        from Path.CommandBoundary import ExactDocumentObjectIdentity

        source = self.document.addObject(
            "App::FeaturePython",
            "ExactCAMIdentity",
        )
        self.document.recompute()
        identity = ExactDocumentObjectIdentity(
            source,
            self.document,
        )
        source_name = str(source.Name)
        source_id = int(source.ID)

        self.document.openTransaction(
            "Replace exact CAM identity",
        )
        self.document.removeObject(source_name)
        replacement = self.document.addObject(
            "App::FeaturePython",
            source_name,
        )
        self.assertEqual(replacement.Name, source_name)
        with self.assertRaises(RuntimeError):
            identity.resolve()
        self.document.abortTransaction()
        self.document.recompute()

        restored = self.document.getObject(source_name)
        self.assertIsNotNone(restored)
        self.assertEqual(int(restored.ID), source_id)

    def test_start_point_is_one_bound_in_place_history_update(self):
        import tempfile
        import DraftTools  # noqa: F401
        import Path.Op.Gui.Base as PathOpGui

        self._select_operation()
        command = PathOpGui.CommandSetStartPoint()
        callbacks = []
        timeline = self.document.getObject("VibeCADTimeline")
        timeline_before = tuple(timeline.Operations)
        point_before = App.Vector(self.operation.StartPoint)
        use_before = bool(self.operation.UseStartPoint)
        before_undo = int(self.document.UndoCount)

        with patch.object(
            Gui.Snapper,
            "getPoint",
            side_effect=lambda callback: callbacks.append(callback),
        ):
            command.Activated()
        self.assertEqual(len(callbacks), 1)

        other = App.newDocument("VibeCADCAMBackgroundTask")
        other.UndoMode = True
        marker = other.addObject(
            "App::FeaturePython",
            "UntouchedStartPointMarker",
        )
        other.recompute()
        other_before = tuple(other.Objects)
        other_undo = int(other.UndoCount)
        App.setActiveDocument(other.Name)
        self._process_events()

        callbacks[0](App.Vector(6.25, 7.5, -100.0), None)
        self._process_events(100)
        expected = App.Vector(
            6.25,
            7.5,
            self.operation.ClearanceHeight.Value,
        )
        self.assertTrue(self.operation.StartPoint.isEqual(expected, 1.0e-9))
        self.assertTrue(self.operation.UseStartPoint)
        self.assertEqual(tuple(timeline.Operations), timeline_before)
        self.assertEqual(
            timeline.Operations.count(self.operation),
            1,
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )
        self.assertEqual(tuple(other.Objects), other_before)
        self.assertIs(other.getObject(marker.Name), marker)
        self.assertEqual(int(other.UndoCount), other_undo)

        App.closeDocument(other.Name)
        App.setActiveDocument(self.document.Name)
        self._process_events()
        self.document.undo()
        self._process_events(100)
        self.assertTrue(
            self.operation.StartPoint.isEqual(
                point_before,
                1.0e-9,
            )
        )
        self.assertEqual(
            bool(self.operation.UseStartPoint),
            use_before,
        )
        self.assertEqual(tuple(timeline.Operations), timeline_before)

        self.document.redo()
        self._process_events(100)
        self.assertTrue(self.operation.StartPoint.isEqual(expected, 1.0e-9))
        self.assertTrue(self.operation.UseStartPoint)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-start-point.FCStd"
            document_name = self.document.Name
            job_name = str(self.job.Name)
            operation_name = str(self.operation.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            self.operation = self.document.getObject(operation_name)
            timeline = self.document.getObject("VibeCADTimeline")

            self.assertTrue(
                self.operation.StartPoint.isEqual(
                    expected,
                    1.0e-9,
                )
            )
            self.assertTrue(self.operation.UseStartPoint)
            self.assertEqual(
                list(timeline.Operations).count(self.operation),
                1,
            )

    def test_start_point_cancel_and_failure_leave_no_partial_change(self):
        import DraftTools  # noqa: F401
        import Path.Op.Gui.Base as PathOpGui

        self._select_operation()
        command = PathOpGui.CommandSetStartPoint()
        objects_before = tuple(self.document.Objects)
        timeline = self.document.getObject("VibeCADTimeline")
        timeline_before = tuple(timeline.Operations)
        point_before = App.Vector(self.operation.StartPoint)
        use_before = bool(self.operation.UseStartPoint)
        before_undo = int(self.document.UndoCount)

        with patch.object(
            Gui.Snapper,
            "getPoint",
            return_value=None,
        ):
            command.Activated()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(tuple(timeline.Operations), timeline_before)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertTrue(
            self.operation.StartPoint.isEqual(
                point_before,
                1.0e-9,
            )
        )
        self.assertEqual(
            bool(self.operation.UseStartPoint),
            use_before,
        )

        with (
            patch.object(
                PathOpGui,
                "is_document_object",
                side_effect=(True, False),
            ),
            self.assertRaises(RuntimeError),
        ):
            command._setpoint(
                self.document,
                self.operation,
                App.Vector(8.0, 9.0, 10.0),
            )
        self._process_events(100)
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(tuple(timeline.Operations), timeline_before)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertTrue(
            self.operation.StartPoint.isEqual(
                point_before,
                1.0e-9,
            )
        )
        self.assertEqual(
            bool(self.operation.UseStartPoint),
            use_before,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            0,
        )

    def test_context_tool_saves_are_external_document_readers(self):
        import Path.Tool.toolbit.ui.cmd as ToolBitCommands

        tool = self.job.Tools.Group[0].Tool
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(tool)
        self._process_events()
        objects_before = tuple(self.document.Objects)
        timeline_before = tuple(self.document.getObject("VibeCADTimeline").Operations)
        before_undo = int(self.document.UndoCount)

        for save_as in (False, True):
            with self.subTest(save_as=save_as):
                dialog = Mock()
                dialog.exec_.return_value = (
                    "/tmp/contract-tool.fctb",
                    object(),
                )
                with patch.object(
                    ToolBitCommands,
                    "AssetSaveDialog",
                    return_value=dialog,
                ):
                    command = ToolBitCommands.CommandToolBitSave(
                        save_as,
                    )
                    self.assertTrue(command.IsActive())
                    command.Activated()

                dialog.exec_.assert_called_once_with(tool.Proxy)
                self.assertEqual(
                    tuple(self.document.Objects),
                    objects_before,
                )
                self.assertEqual(
                    tuple(self.document.getObject("VibeCADTimeline").Operations),
                    timeline_before,
                )
                self.assertEqual(
                    int(self.document.UndoCount),
                    before_undo,
                )
                self.assertFalse(self.document.HasPendingTransaction)

    def test_copy_is_one_validated_undoable_operation(self):
        import PathCommands

        self._select_operation()
        before_objects = tuple(self.document.Objects)
        before_history = self._visible_timeline_names()
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("CAM_OperationCopy"))
        with patch.object(
            PathCommands.PathUtils,
            "addToJob",
            side_effect=AssertionError("Copy must insert through its captured Job"),
        ):
            Gui.runCommand("CAM_OperationCopy")
        self._process_events()

        created = [obj for obj in self.document.Objects if obj not in before_objects]
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].isValid())
        self.assertIn(created[0], self.job.Operations.Group)
        self._assert_timeline_source_preserving(created[0])
        self.assertNotIn("CAMOutputs", created[0].PropertiesList)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(created[0], timeline.Operations)
        self.assertEqual(
            self._visible_timeline_names() - before_history,
            {created[0].Name},
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before_objects)

    def test_copy_inserts_at_marker_without_discarding_future_history(self):
        import tempfile
        import Path.Op.Custom as PathCustom

        future = PathCustom.Create(
            "FutureContractOperation",
            parentJob=self.job,
        )
        future.Gcode = list(self.operation.Gcode)
        self.document.recompute()
        self.assertTrue(future.isValid())

        timeline = self.document.getObject("VibeCADTimeline")
        future_index = list(timeline.Operations).index(future)
        self._move_timeline_to(future_index)
        before_objects = tuple(self.document.Objects)
        before_operations = tuple(timeline.Operations)
        before_position = int(timeline.Position)
        before_visibility = tuple(timeline.VisibilityAtEnd)
        before_suppression = tuple(timeline.SuppressionAtEnd)
        before_undo = int(self.document.UndoCount)
        self.assertEqual(before_position, future_index)
        self.assertTrue(future.Suppressed)

        self._select_operation()
        self.assertTrue(Gui.isCommandActive("CAM_OperationCopy"))
        Gui.runCommand("CAM_OperationCopy")
        self._process_events(100)

        created = [obj for obj in self.document.Objects if obj not in before_objects]
        self.assertEqual(len(created), 1)
        copied = created[0]
        copied_name = str(copied.Name)
        expected_operations = (
            *before_operations[:before_position],
            copied,
            *before_operations[before_position:],
        )
        expected_operation_names = tuple(obj.Name for obj in expected_operations)
        expected_visibility = tuple(timeline.VisibilityAtEnd)
        expected_suppression = tuple(timeline.SuppressionAtEnd)
        expected_position = before_position + 1
        self.assertEqual(
            tuple(timeline.Operations),
            expected_operations,
        )
        self.assertEqual(int(timeline.Position), expected_position)
        self.assertTrue(future.Suppressed)
        self.assertIn(copied, self.job.Operations.Group)
        self._assert_timeline_source_preserving(copied)
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events(100)
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(tuple(timeline.Operations), before_operations)
        self.assertEqual(int(timeline.Position), before_position)
        self.assertEqual(
            tuple(timeline.VisibilityAtEnd),
            before_visibility,
        )
        self.assertEqual(
            tuple(timeline.SuppressionAtEnd),
            before_suppression,
        )

        self.document.redo()
        self._process_events(100)
        copied = self.document.getObject(copied_name)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(copied)
        self.assertEqual(
            tuple(obj.Name for obj in timeline.Operations),
            expected_operation_names,
        )
        self.assertEqual(int(timeline.Position), expected_position)
        self.assertEqual(
            tuple(timeline.VisibilityAtEnd),
            expected_visibility,
        )
        self.assertEqual(
            tuple(timeline.SuppressionAtEnd),
            expected_suppression,
        )

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-copy-at-marker.FCStd"
            document_name = str(self.document.Name)
            job_name = str(self.job.Name)
            operation_name = str(self.operation.Name)
            future_name = str(future.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            self.operation = self.document.getObject(operation_name)
            copied = self.document.getObject(copied_name)
            future = self.document.getObject(future_name)
            timeline = self.document.getObject("VibeCADTimeline")

            self.assertIsNotNone(copied)
            self.assertIsNotNone(future)
            self.assertEqual(
                tuple(obj.Name for obj in timeline.Operations),
                expected_operation_names,
            )
            self.assertEqual(
                int(timeline.Position),
                expected_position,
            )
            self.assertEqual(
                tuple(timeline.VisibilityAtEnd),
                expected_visibility,
            )
            self.assertEqual(
                tuple(timeline.SuppressionAtEnd),
                expected_suppression,
            )
            self.assertTrue(future.Suppressed)
            self.assertIn(copied, self.job.Operations.Group)
            self._assert_timeline_source_preserving(copied)

    def test_operation_copy_owns_dressup_chain_as_one_history_step(self):
        import tempfile

        self._select_operation()
        before_dressup = frozenset(self.document.Objects)
        Gui.runCommand("CAM_DressupArray")
        self._process_events(100)
        created_dressups = [obj for obj in self.document.Objects if obj not in before_dressup]
        self.assertEqual(len(created_dressups), 1)
        dressup = created_dressups[0]
        self._assert_timeline_replaces(
            dressup,
            [self.operation],
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(dressup)
        self._process_events()
        before_copy = frozenset(self.document.Objects)
        before_history = self._visible_timeline_names()
        before_undo = int(self.document.UndoCount)
        Gui.runCommand("CAM_OperationCopy")
        self._process_events(100)

        copied_chain = [obj for obj in self.document.Objects if obj not in before_copy]
        self.assertEqual(len(copied_chain), 2)
        copied_roots = [obj for obj in copied_chain if obj in self.job.Operations.Group]
        self.assertEqual(len(copied_roots), 1)
        copied_dressup = copied_roots[0]
        copied_base = copied_dressup.Base
        self.assertIn(copied_base, copied_chain)
        self.assertIsNot(copied_base, self.operation)
        self._assert_timeline_source_preserving(copied_dressup)
        self._assert_timeline_resource(copied_base, copied_dressup)
        self.assertFalse(copied_base.ViewObject.Visibility)
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            copied_base.PropertiesList,
        )
        self.assertNotIn("CAMOutputs", copied_dressup.PropertiesList)
        timeline = self.document.getObject("VibeCADTimeline")
        copied_dressup_index = list(timeline.Operations).index(copied_dressup)
        self.assertGreater(copied_dressup_index, 0)
        self.assertIs(
            timeline.Operations[copied_dressup_index - 1],
            copied_base,
        )
        self.assertEqual(
            self._visible_timeline_names() - before_history,
            {copied_dressup.Name},
        )
        self.assertEqual(
            list(dressup.VibeCADTimelineReplacedInputs),
            [self.operation],
        )
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )

        copied_base.ViewObject.Visibility = True
        copied_dressup.ViewObject.Visibility = True
        self._process_events(100)
        self._move_timeline_to(copied_dressup_index)
        self.assertTrue(copied_dressup.Suppressed)
        self.assertFalse(copied_base.ViewObject.Visibility)
        self.assertFalse(copied_dressup.ViewObject.Visibility)

        self._move_timeline_to(len(timeline.Operations))
        self.assertTrue(copied_base.ViewObject.Visibility)
        self.assertTrue(copied_dressup.ViewObject.Visibility)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-copy-dressup-chain.FCStd"
            document_name = self.document.Name
            job_name = str(self.job.Name)
            operation_name = str(self.operation.Name)
            dressup_name = str(dressup.Name)
            copied_base_name = str(copied_base.Name)
            copied_dressup_name = str(copied_dressup.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            self.operation = self.document.getObject(operation_name)
            dressup = self.document.getObject(dressup_name)
            copied_base = self.document.getObject(copied_base_name)
            copied_dressup = self.document.getObject(copied_dressup_name)

            self._assert_timeline_source_preserving(copied_dressup)
            self._assert_timeline_resource(
                copied_base,
                copied_dressup,
            )
            self.assertNotIn(
                "VibeCADTimelineReplacedInputs",
                copied_base.PropertiesList,
            )
            self.assertEqual(
                self._visible_timeline_names() & {copied_base_name, copied_dressup_name},
                {copied_dressup_name},
            )

            before_delete_undo = int(self.document.UndoCount)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(copied_dressup)
            self._process_events()
            Gui.runCommand("Std_Delete")
            self._process_events(100)

            self.assertFalse(Gui.Control.activeDialog())
            self.assertIsNone(self.document.getObject(copied_base_name))
            self.assertIsNone(self.document.getObject(copied_dressup_name))
            self.assertIsNotNone(self.document.getObject(operation_name))
            self.assertIsNotNone(self.document.getObject(dressup_name))
            self.assertEqual(
                int(self.document.UndoCount),
                before_delete_undo + 1,
            )

            self.document.undo()
            self._process_events(100)
            copied_base = self.document.getObject(copied_base_name)
            copied_dressup = self.document.getObject(copied_dressup_name)
            self.assertIsNotNone(copied_base)
            self.assertIsNotNone(copied_dressup)
            self._assert_timeline_resource(
                copied_base,
                copied_dressup,
            )
            self.assertIn(
                copied_dressup,
                self.document.getObject(job_name).Operations.Group,
            )

    def test_operation_copy_clones_owned_path_shape_source(self):
        import tempfile

        model = self.document.getObject("ContractModel")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Edge1")
        self._process_events()
        Gui.runCommand("CAM_PathShapeTC")
        self._process_events(100)

        source_operation = self.job.Operations.Group[-1]
        source_resource = source_operation.Sources[0]
        self._assert_timeline_resource(
            source_resource,
            source_operation,
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_operation)
        self._process_events()
        before_copy = frozenset(self.document.Objects)
        before_undo = int(self.document.UndoCount)
        self.assertTrue(Gui.isCommandActive("CAM_OperationCopy"))
        Gui.runCommand("CAM_OperationCopy")
        self._process_events(100)

        copied_objects = [obj for obj in self.document.Objects if obj not in before_copy]
        self.assertEqual(len(copied_objects), 2)
        copied_operations = [obj for obj in copied_objects if obj in self.job.Operations.Group]
        self.assertEqual(len(copied_operations), 1)
        copied_operation = copied_operations[0]
        copied_resource = copied_operation.Sources[0]
        self.assertIn(copied_resource, copied_objects)
        self.assertIsNot(copied_resource, source_resource)
        self._assert_timeline_source_preserving(
            copied_operation,
        )
        self._assert_timeline_resource(
            copied_resource,
            copied_operation,
        )
        self.assertFalse(copied_resource.ViewObject.Visibility)
        self.assertEqual(
            int(self.document.UndoCount),
            before_undo + 1,
        )

        timeline = self.document.getObject("VibeCADTimeline")
        copied_index = list(timeline.Operations).index(copied_operation)
        self.assertIs(
            timeline.Operations[copied_index - 1],
            copied_resource,
        )

        source_operation_name = str(source_operation.Name)
        source_resource_name = str(source_resource.Name)
        copied_operation_name = str(copied_operation.Name)
        copied_resource_name = str(copied_resource.Name)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_operation)
        self._process_events()
        Gui.runCommand("Std_Delete")
        self._process_events(100)

        self.assertIsNone(self.document.getObject(source_operation_name))
        self.assertIsNone(self.document.getObject(source_resource_name))
        copied_operation = self.document.getObject(copied_operation_name)
        copied_resource = self.document.getObject(copied_resource_name)
        self.assertIsNotNone(copied_operation)
        self.assertIsNotNone(copied_resource)
        self.assertEqual(
            list(copied_operation.Sources),
            [copied_resource],
        )
        self.assertTrue(copied_operation.isValid())

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-copy-owned-path-shape.FCStd"
            document_name = str(self.document.Name)
            job_name = str(self.job.Name)
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            copied_operation = self.document.getObject(copied_operation_name)
            copied_resource = self.document.getObject(copied_resource_name)
            self.assertEqual(
                list(copied_operation.Sources),
                [copied_resource],
            )
            self._assert_timeline_resource(
                copied_resource,
                copied_operation,
            )
            self.assertIn(
                copied_operation,
                self.job.Operations.Group,
            )
            self.assertTrue(copied_operation.isValid())

    def test_operation_copy_clones_boundary_base_and_stock(self):
        self._select_operation()
        before_dressup = frozenset(self.document.Objects)
        Gui.runCommand("CAM_DressupPathBoundary")
        self._process_events(100)
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task(accept=True)

        dressups = [
            obj
            for obj in self.document.Objects
            if (obj not in before_dressup and "VibeCADTimelineReplacedInputs" in obj.PropertiesList)
        ]
        self.assertEqual(len(dressups), 1)
        source_dressup = dressups[0]
        source_base = source_dressup.Base
        source_stock = source_dressup.Stock
        self._assert_timeline_resource(
            source_stock,
            source_dressup,
        )
        timeline = self.document.getObject("VibeCADTimeline")
        source_dressup_index = list(timeline.Operations).index(source_dressup)
        self.assertIs(
            timeline.Operations[source_dressup_index - 1],
            source_stock,
        )
        previous = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        next_button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(next_button)
        self._move_timeline_to(source_dressup_index + 1)
        previous.click()
        self._process_events(100)
        self.assertEqual(
            int(timeline.Position),
            source_dressup_index - 1,
        )
        self.assertTrue(source_dressup.Suppressed)
        self.assertFalse(source_stock.ViewObject.Visibility)
        next_button.click()
        self._process_events(100)
        self.assertEqual(
            int(timeline.Position),
            source_dressup_index + 1,
        )
        self.assertFalse(source_dressup.Suppressed)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_dressup)
        self._process_events()
        before_copy = frozenset(self.document.Objects)
        Gui.runCommand("CAM_OperationCopy")
        self._process_events(100)

        copied_objects = [obj for obj in self.document.Objects if obj not in before_copy]
        self.assertEqual(len(copied_objects), 3)
        copied_operations = [obj for obj in copied_objects if obj in self.job.Operations.Group]
        self.assertEqual(len(copied_operations), 1)
        copied_dressup = copied_operations[0]
        copied_base = copied_dressup.Base
        copied_stock = copied_dressup.Stock
        self.assertIn(copied_base, copied_objects)
        self.assertIn(copied_stock, copied_objects)
        self.assertIsNot(copied_base, source_base)
        self.assertIsNot(copied_stock, source_stock)
        self._assert_timeline_source_preserving(
            copied_dressup,
        )
        self._assert_timeline_resource(
            copied_base,
            copied_dressup,
        )
        self._assert_timeline_resource(
            copied_stock,
            copied_dressup,
        )
        self.assertFalse(copied_base.ViewObject.Visibility)
        self.assertFalse(copied_stock.ViewObject.Visibility)
        self.assertTrue(copied_dressup.isValid())

        source_dressup_name = str(source_dressup.Name)
        source_stock_name = str(source_stock.Name)
        copied_dressup_name = str(copied_dressup.Name)
        copied_base_name = str(copied_base.Name)
        copied_stock_name = str(copied_stock.Name)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_dressup)
        self._process_events()
        Gui.runCommand("Std_Delete")
        self._process_events(100)

        self.assertIsNone(self.document.getObject(source_dressup_name))
        self.assertIsNone(self.document.getObject(source_stock_name))
        self.assertIsNotNone(self.document.getObject(source_base.Name))
        copied_dressup = self.document.getObject(copied_dressup_name)
        copied_base = self.document.getObject(copied_base_name)
        copied_stock = self.document.getObject(copied_stock_name)
        self.assertIs(copied_dressup.Base, copied_base)
        self.assertIs(copied_dressup.Stock, copied_stock)
        self._assert_timeline_resource(
            copied_base,
            copied_dressup,
        )
        self._assert_timeline_resource(
            copied_stock,
            copied_dressup,
        )
        self.assertTrue(copied_dressup.isValid())

    def test_multi_operation_copy_is_one_owned_history_step(self):
        import tempfile
        import Path.Op.Custom as PathCustom
        import PathCommands

        second = PathCustom.Create(
            "SecondContractOperation",
            parentJob=self.job,
        )
        second.Gcode = list(self.operation.Gcode)
        self.document.recompute()
        self.assertTrue(second.isValid())
        self.assertIn(second, self.job.Operations.Group)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(second)
        Gui.Selection.addSelection(self.operation)
        self._process_events()
        self.assertEqual(
            [selected for selected, _job in (PathCommands._selected_copy_operations())],
            [second, self.operation],
        )
        before_copy = frozenset(self.document.Objects)
        before_history = self._visible_timeline_names()
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("CAM_OperationCopy"))
        Gui.runCommand("CAM_OperationCopy")
        self._process_events(100)

        created = [obj for obj in self.document.Objects if obj not in before_copy]
        self.assertEqual(len(created), 3)
        controllers = [obj for obj in created if "CAMOutputs" in obj.PropertiesList]
        self.assertEqual(len(controllers), 1)
        controller = controllers[0]
        outputs = list(controller.CAMOutputs)
        self.assertEqual(len(outputs), 2)
        self.assertEqual(set(outputs), set(created) - {controller})
        self.assertTrue(
            outputs[0].Name.startswith(self.operation.Name),
        )
        self.assertTrue(
            outputs[1].Name.startswith(second.Name),
        )
        self._assert_timeline_source_preserving(controller)
        self.assertEqual(
            controller.getTypeIdOfProperty("CAMOutputs"),
            "App::PropertyLinkListHidden",
        )
        self.assertEqual(
            controller.CAMOperationKind,
            "Copy CAM operations",
        )
        self.assertFalse(controller.ViewObject.ShowInTree)
        self.assertFalse(
            getattr(
                controller.ViewObject.Proxy,
                "supportsDocumentTimelineEdit",
                lambda: False,
            )()
        )
        for output in outputs:
            self._assert_timeline_resource(output, controller)
            self.assertNotIn(
                "VibeCADTimelineReplacedInputs",
                output.PropertiesList,
            )
            self.assertIn(output, self.job.Operations.Group)

        self.assertEqual(
            self._visible_timeline_names() - before_history,
            {controller.Name},
        )
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        for output in outputs:
            output.ViewObject.Visibility = True
        self._process_events(100)
        timeline = self.document.getObject("VibeCADTimeline")
        controller_index = list(timeline.Operations).index(controller)
        self.assertEqual(
            list(timeline.Operations[controller_index - len(outputs) : controller_index + 1]),
            [*outputs, controller],
        )
        self._move_timeline_to(controller_index)
        for output in outputs:
            self.assertFalse(output.ViewObject.Visibility)

        self._move_timeline_to(len(timeline.Operations))
        for output in outputs:
            self.assertTrue(output.ViewObject.Visibility)

        with tempfile.TemporaryDirectory() as directory:
            filename = f"{directory}/cam-multi-copy.FCStd"
            document_name = self.document.Name
            job_name = str(self.job.Name)
            source_names = {
                str(self.operation.Name),
                str(second.Name),
            }
            controller_name = str(controller.Name)
            output_names = [str(output.Name) for output in outputs]
            self.document.saveAs(filename)
            App.closeDocument(document_name)
            self._process_events(100)

            self.document = App.openDocument(filename)
            App.setActiveDocument(self.document.Name)
            self._process_events(150)
            self.job = self.document.getObject(job_name)
            controller = self.document.getObject(controller_name)
            outputs = [self.document.getObject(name) for name in output_names]
            self.assertEqual(
                list(controller.CAMOutputs),
                outputs,
            )
            self._assert_timeline_source_preserving(controller)
            for output in outputs:
                self._assert_timeline_resource(output, controller)
                self.assertIn(output, self.job.Operations.Group)
            self.assertEqual(
                self._visible_timeline_names() & {controller_name, *output_names},
                {controller_name},
            )

            self._double_click_history_operation(controller)
            self.assertFalse(Gui.Control.activeDialog())

            before_delete_undo = int(self.document.UndoCount)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(controller)
            self._process_events()
            Gui.runCommand("Std_Delete")
            self._process_events(100)

            self.assertFalse(Gui.Control.activeDialog())
            self.assertIsNone(self.document.getObject(controller_name))
            for output_name in output_names:
                self.assertIsNone(self.document.getObject(output_name))
            for source_name in source_names:
                self.assertIsNotNone(self.document.getObject(source_name))
            self.assertEqual(
                int(self.document.UndoCount),
                before_delete_undo + 1,
            )

            self.document.undo()
            self._process_events(100)
            controller = self.document.getObject(controller_name)
            self.assertIsNotNone(controller)
            outputs = [self.document.getObject(name) for name in output_names]
            self.assertEqual(
                list(controller.CAMOutputs),
                outputs,
            )
            for output in outputs:
                self.assertIsNotNone(output)
                self._assert_timeline_resource(
                    output,
                    controller,
                )
                self.assertIn(output, self.job.Operations.Group)

    def test_copy_failure_rolls_back_the_complete_attempt(self):
        import PathCommands

        self._select_operation()
        before_objects = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        with patch.object(
            self.job.Proxy,
            "addOperation",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError):
                PathCommands._CopyOperation().Activated()
            self._process_events()

        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_inspect_preview_is_transient_and_does_not_move_the_tool(self):
        from Path.Main.Gui.Inspect import GCodeEditorDialog

        tool = self.operation.ToolController.Tool
        tool.Placement = App.Placement(
            App.Vector(7.0, 8.0, 9.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 17.0),
        )
        self.document.recompute()
        before_objects = tuple(self.document.Objects)
        before_placement = App.Placement(tool.Placement)
        before_undo = int(self.document.UndoCount)

        dialog = GCodeEditorDialog(self.operation)
        try:
            self.assertTrue(dialog.editor.isReadOnly())
            dialog.editor.setPlainText(self.operation.Path.toGCode())
            dialog.highlightpath()

            self.assertEqual(tuple(self.document.Objects), before_objects)
            self.assertTrue(tool.Placement.isSame(before_placement))
            self.assertEqual(int(self.document.UndoCount), before_undo)
            self.assertFalse(self.document.HasPendingTransaction)
            self.assertEqual(self.document.getBookedTransactionID(), 0)
        finally:
            dialog.cleanup()

        self.assertIsNone(dialog.preview._scene_graph)

    def test_inspect_preview_cleanup_survives_document_close(self):
        from Path.Main.Gui.Inspect import GCodeEditorDialog

        dialog = GCodeEditorDialog(self.operation)
        document_name = self.document.Name
        App.closeDocument(document_name)
        self._process_events()

        # Closing a document tears down its scene graph before a modal dialog
        # necessarily receives its finished signal.  Cleanup must be
        # idempotent and must not touch that destroyed graph.
        dialog.cleanup()
        dialog.cleanup()
        self.assertIsNone(dialog.preview._scene_graph)
        self.assertIsNone(dialog.preview._root)

    def test_simulator_tool_profile_uses_a_shape_copy(self):
        from Path.Main.Gui.SimulatorGL import CAMSimulation

        tool = self.operation.ToolController.Tool
        tool.Placement = App.Placement(
            App.Vector(4.0, 5.0, 6.0),
            App.Rotation(App.Vector(0.0, 1.0, 0.0), 11.0),
        )
        self.document.recompute()
        before_objects = tuple(self.document.Objects)
        before_placement = App.Placement(tool.Placement)
        before_undo = int(self.document.UndoCount)

        profile = CAMSimulation().GetToolProfile(tool, 0.5)

        self.assertGreater(len(profile), 3)
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertTrue(tool.Placement.isSame(before_placement))
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    @staticmethod
    def _toolbit_browser(toolbits):
        class SelectedToolBits:
            @staticmethod
            def get_selected_bits():
                return list(toolbits)

            @staticmethod
            def get_selected_bit_uris():
                return [str(toolbit.get_uri()) for toolbit in toolbits]

            @staticmethod
            def get_tool_no_from_current_library(_toolbit):
                # The All Tools view has no library-assigned numbers.  The
                # picker must allocate distinct numbers from the real Job.
                return None

        return SelectedToolBits()

    def _new_toolbit_picker(self, toolbits):
        from Path.Tool.library.ui.dock import ToolBitLibraryDock

        picker = ToolBitLibraryDock.__new__(ToolBitLibraryDock)
        picker.defaultJob = self.job
        picker.autoClose = False
        picker.form = None
        picker.browser_widget = self._toolbit_browser(toolbits)
        return picker

    def test_toolbit_add_and_double_click_are_atomic_multi_select_gestures(self):
        from Path.Tool.toolbit import ToolBit

        for trigger in ("add", "double-click"):
            with self.subTest(trigger=trigger):
                toolbits = [
                    ToolBit.from_shape_id("endmill.fcstd"),
                    ToolBit.from_shape_id("ballend.fcstd"),
                ]
                picker = self._new_toolbit_picker(toolbits)
                before_objects = tuple(self.document.Objects)
                before_tools = tuple(self.job.Tools.Group)
                before_history = self._visible_timeline_names()
                before_undo = int(self.document.UndoCount)

                if trigger == "add":
                    picker._add_tool_controller_to_doc()
                else:
                    picker._on_doubleclick(toolbits[0])

                added_controllers = [
                    controller
                    for controller in self.job.Tools.Group
                    if controller not in before_tools
                ]
                self.assertEqual(len(added_controllers), 2)
                self.assertEqual(
                    len({int(controller.ToolNumber) for controller in added_controllers}),
                    2,
                )
                for controller in added_controllers:
                    self.assertIs(controller.Document, self.document)
                    self.assertIs(controller.Tool.Document, self.document)
                    self.assertTrue(controller.isValid())
                    self.assertTrue(controller.Tool.isValid())
                    self._assert_timeline_resource(
                        controller,
                        self.job,
                    )
                    self._assert_timeline_resource(
                        controller.Tool,
                        self.job,
                    )
                    if controller.Tool.BitBody:
                        self._assert_timeline_resource(
                            controller.Tool.BitBody,
                            self.job,
                        )
                        for feature in controller.Tool.BitBody.Group:
                            self._assert_timeline_resource(
                                feature,
                                self.job,
                            )
                    for visual_resource in getattr(
                        controller.Tool.Proxy,
                        "timelineVisualResources",
                        lambda: (),
                    )():
                        self._assert_timeline_resource(
                            visual_resource,
                            self.job,
                        )
                for source_asset in toolbits:
                    self.assertIsNone(getattr(source_asset.obj, "Document", None))
                timeline = self.document.getObject("VibeCADTimeline")
                job_index = list(timeline.Operations).index(self.job)
                owned_resources = [
                    candidate
                    for candidate in timeline.Operations
                    if (
                        "VibeCADTimelineOwner" in candidate.PropertiesList
                        and candidate.VibeCADTimelineOwner is self.job
                    )
                ]
                self.assertEqual(
                    list(timeline.Operations[job_index - len(owned_resources) : job_index]),
                    owned_resources,
                )
                self.assertEqual(
                    self._visible_timeline_names(),
                    before_history,
                )
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(self.document.getBookedTransactionID(), 0)
                self.assertEqual(int(self.document.UndoCount), before_undo + 1)

                self.document.undo()
                self._process_events()
                self.assertEqual(tuple(self.document.Objects), before_objects)
                self.assertEqual(tuple(self.job.Tools.Group), before_tools)

    def test_toolbit_multi_select_validation_failure_aborts_every_addition(self):
        from Path.Tool.library.ui.dock import ToolBitLibraryDock
        from Path.Tool.toolbit import ToolBit

        picker = self._new_toolbit_picker(
            [
                ToolBit.from_shape_id("endmill.fcstd"),
                ToolBit.from_shape_id("ballend.fcstd"),
            ]
        )
        before_objects = tuple(self.document.Objects)
        before_tools = tuple(self.job.Tools.Group)
        before_undo = int(self.document.UndoCount)

        with patch.object(
            ToolBitLibraryDock,
            "_validate_tool_controller_addition",
            side_effect=RuntimeError("forced validation failure"),
        ):
            with self.assertRaises(RuntimeError):
                picker._add_tool_controller_to_doc()

        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(tuple(self.job.Tools.Group), before_tools)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        for source_asset in picker.browser_widget.get_selected_bits():
            self.assertIsNone(getattr(source_asset.obj, "Document", None))

    def test_toolbit_library_numbers_never_duplicate_job_numbers(self):
        from Path.Tool.toolbit import ToolBit

        toolbits = [
            ToolBit.from_shape_id("endmill.fcstd"),
            ToolBit.from_shape_id("ballend.fcstd"),
        ]
        picker = self._new_toolbit_picker(toolbits)
        occupied_number = int(self.job.Tools.Group[0].ToolNumber)
        picker.browser_widget.get_tool_no_from_current_library = lambda _toolbit: occupied_number
        before_tools = tuple(self.job.Tools.Group)

        picker._add_tool_controller_to_doc()
        self._process_events()

        controllers = tuple(self.job.Tools.Group)
        numbers = [int(controller.ToolNumber) for controller in controllers]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(len(controllers), len(before_tools) + len(toolbits))

    def test_legacy_simulator_cancel_aborts_every_provisional_object(self):
        before_objects = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        self.assertTrue(Gui.isCommandActive("CAM_Simulator"))
        Gui.runCommand("CAM_Simulator")
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertNotEqual(self.document.getObject("CutTool"), None)
        self.assertNotEqual(self.document.getObject("CutMaterial"), None)
        self._dismiss_task(accept=False)

        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(int(self.document.UndoCount), before_undo)

    def test_legacy_simulator_accept_retains_one_valid_undoable_result(self):
        before_objects = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)

        Gui.runCommand("CAM_Simulator")
        self._process_events(100)
        self._dismiss_task(accept=True)

        created = [obj for obj in self.document.Objects if obj not in before_objects]
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].Name.startswith("CutMaterial"))
        self.assertTrue(created[0].isValid())
        self.assertGreater(created[0].Mesh.CountFacets, 0)
        self._assert_timeline_source_preserving(created[0])
        self.assertEqual(int(self.document.UndoCount), before_undo + 1)

        self.document.undo()
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), before_objects)

    def test_legacy_simulator_validation_failure_aborts_every_change(self):
        from Path.Main.Gui.Simulator import PathSimulation

        before_objects = tuple(self.document.Objects)
        before_undo = int(self.document.UndoCount)
        simulation = PathSimulation()
        simulation.Activate()
        self._process_events(100)

        with patch.object(
            simulation,
            "_validate_retained_result",
            side_effect=RuntimeError("forced validation failure"),
        ):
            with self.assertRaises(RuntimeError):
                simulation.accept()

        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self._dismiss_task(accept=False)
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_post_commands_export_without_a_model_transaction(self):
        import Path.Post.Command as PostCommand

        class FakePostProcessor:
            def export(self):
                return [("allitems", "G0 X0 Y0 Z5\n")]

        for command, selection in (
            (PostCommand.CommandPathPost(), self._select_job),
            (PostCommand.CommandPathPostSelected(), self._select_operation),
        ):
            with self.subTest(command=command.__class__.__name__):
                selection()
                before_objects = tuple(self.document.Objects)
                before_undo = int(self.document.UndoCount)

                with (
                    patch.object(
                        PostCommand,
                        "_resolve_post_processor_name",
                        return_value="contract_post",
                    ),
                    patch.object(
                        PostCommand.PostProcessorFactory,
                        "get_post_processor",
                        return_value=FakePostProcessor(),
                    ),
                    patch.object(
                        PostCommand.Path.Preferences,
                        "showEditorOnPostProcess",
                        return_value=False,
                    ),
                    patch.object(command, "_write_file") as write_file,
                ):
                    command.Activated()

                write_file.assert_called_once()
                self.assertEqual(tuple(self.document.Objects), before_objects)
                self.assertEqual(int(self.document.UndoCount), before_undo)
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_filename_override_does_not_mutate_the_job(self):
        from Path.Post.Utils import FilenameGenerator

        original = self.job.PostProcessorOutputFile
        generator = FilenameGenerator(
            self.job,
            output_file="/tmp/vibecad-contract.tap",
        )
        generated = next(generator.generate_filenames())

        self.assertEqual(
            self.job.PostProcessorOutputFile,
            original,
        )
        self.assertEqual(generated, "/tmp/vibecad-contract.tap")

    def test_toggle_validation_failure_rolls_back_the_complete_attempt(self):
        import PathCommands

        self._select_operation()
        before_active = bool(self.operation.Active)
        before_undo = int(self.document.UndoCount)

        with patch.object(
            PathCommands,
            "_recompute_and_validate",
            side_effect=RuntimeError("forced validation failure"),
        ):
            try:
                Gui.runCommand("CAM_OpActiveToggle")
            except RuntimeError:
                pass
            self._process_events()

        self.assertEqual(bool(self.operation.Active), before_active)
        self.assertEqual(int(self.document.UndoCount), before_undo)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_immediate_commands_are_atomic_when_user_undo_is_disabled(self):
        import PathCommands

        for command_name, force_failure in (
            ("CAM_OpActiveToggle", False),
            ("CAM_OpActiveToggle", True),
            ("CAM_OperationCopy", False),
            ("CAM_OperationCopy", True),
        ):
            with self.subTest(
                command=command_name,
                failure=force_failure,
            ):
                self.document.UndoMode = False
                self._select_operation()
                before_objects = tuple(self.document.Objects)
                before_active = bool(self.operation.Active)

                patch_target = (
                    patch.object(
                        PathCommands,
                        "_recompute_and_validate",
                        side_effect=RuntimeError("forced validation failure"),
                    )
                    if force_failure
                    else patch.object(
                        PathCommands,
                        "_recompute_and_validate",
                        wraps=PathCommands._recompute_and_validate,
                    )
                )
                with patch_target:
                    try:
                        Gui.runCommand(command_name)
                    except RuntimeError:
                        if not force_failure:
                            raise
                self._process_events()

                self.assertEqual(int(self.document.UndoMode), 0)
                self.assertEqual(int(self.document.UndoCount), 0)
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(self.document.getBookedTransactionID(), 0)
                if force_failure:
                    self.assertEqual(tuple(self.document.Objects), before_objects)
                    self.assertEqual(
                        bool(self.operation.Active),
                        before_active,
                    )
                elif command_name == "CAM_OpActiveToggle":
                    self.assertEqual(tuple(self.document.Objects), before_objects)
                    self.assertNotEqual(
                        bool(self.operation.Active),
                        before_active,
                    )
                else:
                    self.assertEqual(
                        len(self.document.Objects),
                        len(before_objects) + 1,
                    )

                # Reset the fixture state without relying on an undo stack that
                # the user deliberately disabled.
                if command_name == "CAM_OpActiveToggle" and not force_failure:
                    self.operation.Active = before_active
                elif command_name == "CAM_OperationCopy" and not force_failure:
                    for obj in tuple(self.document.Objects):
                        if obj not in before_objects:
                            self.document.removeObject(obj.Name)
                self.document.recompute()
                self.document.UndoMode = True

    def test_native_transaction_retains_a_failed_exact_close(self):
        import VibeCADNativeTransaction as native_transaction

        self.document.UndoMode = False
        transaction = native_transaction._OwnedDocumentTransaction(
            self.document,
            "Retained close contract",
        )
        self.operation.Active = False
        transaction_id = transaction.transaction_id

        with patch.object(
            native_transaction.App,
            "closeActiveTransaction",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError):
                transaction.abort()

        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction_id,
        )
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertEqual(int(self.document.UndoMode), 1)
        self.assertIn(
            (self.document.Name, transaction_id),
            native_transaction._retained_transaction_closes,
        )

        transaction._retry_close()
        self.assertTrue(self.operation.Active)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(int(self.document.UndoMode), 0)
        self.assertNotIn(
            (self.document.Name, transaction_id),
            native_transaction._retained_transaction_closes,
        )
        self.document.UndoMode = True

    def test_native_transaction_retains_its_first_requested_outcome(self):
        import VibeCADNativeTransaction as native_transaction

        self.document.UndoMode = False
        transaction = native_transaction._OwnedDocumentTransaction(
            self.document,
            "Retained commit outcome contract",
        )
        self.operation.Active = False
        transaction_id = transaction.transaction_id

        with patch.object(
            native_transaction.App,
            "closeActiveTransaction",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Could not commit document transaction",
            ):
                transaction.commit()
            with self.assertRaisesRegex(
                RuntimeError,
                "outcome is already retained as commit; refusing abort",
            ):
                transaction.abort()

        self.assertEqual(transaction._requested_abort, False)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction_id,
        )
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertIn(
            (self.document.Name, transaction_id),
            native_transaction._retained_transaction_closes,
        )

        transaction._retry_close()
        self.assertFalse(self.operation.Active)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(int(self.document.UndoMode), 0)
        self.assertNotIn(
            (self.document.Name, transaction_id),
            native_transaction._retained_transaction_closes,
        )
        self.document.UndoMode = True

    def test_native_transaction_preserves_a_direct_close_successor(self):
        import VibeCADNativeTransaction as native_transaction

        document = self.document

        class SuccessorObserver:
            def __init__(self):
                self.opened = False
                self.transaction_id = 0
                self.marker = None

            def slotCloseTransaction(self, _aborted):
                if self.opened:
                    return
                self.opened = True
                self.transaction_id = int(App.setActiveTransaction("Successor transaction"))
                self.marker = document.addObject(
                    "App::FeaturePython",
                    "SuccessorMarker",
                )

        observer = SuccessorObserver()
        App.removeDocumentObserver(native_transaction._native_transaction_observer)
        App.addDocumentObserver(observer)
        App.addDocumentObserver(native_transaction._native_transaction_observer)
        try:
            document.UndoMode = False
            transaction = native_transaction._OwnedDocumentTransaction(
                document,
                "Predecessor transaction",
            )
            self.operation.Active = False
            transaction.commit()

            self.assertTrue(observer.opened)
            self.assertNotEqual(observer.transaction_id, 0)
            self.assertEqual(
                document.getBookedTransactionID(),
                observer.transaction_id,
            )
            self.assertTrue(document.HasPendingTransaction)
            self.assertEqual(int(document.UndoMode), 1)
            self.assertIsNotNone(
                document.getObject(observer.marker.Name),
            )

            App.closeActiveTransaction(True, observer.transaction_id)
            self.assertFalse(document.HasPendingTransaction)
            self.assertEqual(document.getBookedTransactionID(), 0)
            self.assertEqual(int(document.UndoMode), 0)
            self.assertIsNone(document.getObject("SuccessorMarker"))
            self.assertFalse(self.operation.Active)
        finally:
            booked = document.getBookedTransactionID()
            if booked:
                App.closeActiveTransaction(True, booked)
            App.removeDocumentObserver(observer)
            App.removeDocumentObserver(native_transaction._native_transaction_observer)
            App.addDocumentObserver(native_transaction._native_transaction_observer)
            document.UndoMode = True

    def test_operation_commands_require_a_real_job_and_tool(self):
        self.assertTrue(Gui.isCommandActive("CAM_Profile"))

        tools = list(self.job.Tools.Group)
        self.job.Tools.Group = []
        self.document.recompute()
        self._process_events()
        self.assertFalse(Gui.isCommandActive("CAM_Profile"))

        self.job.Tools.Group = tools
        self.document.recompute()
        self._process_events()
        self.assertTrue(Gui.isCommandActive("CAM_Profile"))
