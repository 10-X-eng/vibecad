# SPDX-License-Identifier: LGPL-2.1-or-later

"""Behavior contracts for durable tools exposed only through context menus."""

from pathlib import Path
import re
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui


def _source_checkout_root():
    return next(
        (
            candidate
            for candidate in Path(__file__).resolve().parents
            if (candidate / "CMakeLists.txt").is_file()
            and (candidate / "src/Mod").is_dir()
        ),
        None,
    )


MODEL_CONTEXT_COMMAND_BEHAVIOR = {
    "PartDesign_MoveFeature": "in-place",
    "PartDesign_MoveFeatureInTree": "in-place",
    "PartDesign_MoveTip": "in-place",
    "PartDesign_MultiTransform": "operation",
    "Std_ToggleFreeze": "in-place",
}

SKETCH_CONTEXT_READ_ONLY = {
    "Sketcher_CopyClipboard",
    "Sketcher_SelectConstraints",
    "Sketcher_SelectElementsAssociatedWithConstraints",
    "Sketcher_ViewSection",
    "Sketcher_ViewSketch",
    "Std_ViewFitAll",
}

SKETCH_CONTEXT_DISPATCHERS = {
    "Sketcher_CompConstrainTools",
    "Sketcher_CompDimensionTools",
}

SKETCH_CONTEXT_EDITOR_LIFECYCLE = {
    "Sketcher_LeaveSketch",
}

SKETCH_CONTEXT_IN_PLACE = {
    "Sketcher_BSplineDecreaseDegree",
    "Sketcher_BSplineDecreaseKnotMultiplicity",
    "Sketcher_BSplineIncreaseDegree",
    "Sketcher_BSplineIncreaseKnotMultiplicity",
    "Sketcher_BSplineInsertKnot",
    "Sketcher_ChangeDimensionConstraint",
    "Sketcher_ConstrainBlock",
    "Sketcher_ConstrainCoincidentUnified",
    "Sketcher_ConstrainEqual",
    "Sketcher_ConstrainHorVer",
    "Sketcher_ConstrainHorizontal",
    "Sketcher_ConstrainParallel",
    "Sketcher_ConstrainPerpendicular",
    "Sketcher_ConstrainSymmetric",
    "Sketcher_ConstrainTangent",
    "Sketcher_ConstrainVertical",
    "Sketcher_CreateArc",
    "Sketcher_CreateBSpline",
    "Sketcher_CreateChamfer",
    "Sketcher_CreateCircle",
    "Sketcher_CreateFillet",
    "Sketcher_CreateHexagon",
    "Sketcher_CreatePoint",
    "Sketcher_CreatePolyline",
    "Sketcher_CreateRectangle",
    "Sketcher_Cut",
    "Sketcher_DeleteAllConstraints",
    "Sketcher_DeleteAllGeometry",
    "Sketcher_Dimension",
    "Sketcher_Extend",
    "Sketcher_Intersection",
    "Sketcher_JoinCurves",
    "Sketcher_Offset",
    "Sketcher_Paste",
    "Sketcher_Projection",
    "Sketcher_Rotate",
    "Sketcher_Scale",
    "Sketcher_Symmetry",
    "Sketcher_ToggleActiveConstraint",
    "Sketcher_ToggleConstruction",
    "Sketcher_ToggleDrivingConstraint",
    "Sketcher_Translate",
    "Sketcher_Trimming",
    "Std_Delete",
}

SKETCH_CONTEXT_COMMAND_BEHAVIOR = {
    **{command: "read-only" for command in SKETCH_CONTEXT_READ_ONLY},
    **{command: "dispatcher" for command in SKETCH_CONTEXT_DISPATCHERS},
    **{
        command: "editor-lifecycle"
        for command in SKETCH_CONTEXT_EDITOR_LIFECYCLE
    },
    **{command: "in-place" for command in SKETCH_CONTEXT_IN_PLACE},
}

ASSEMBLY_CONTEXT_COMMAND_BEHAVIOR = {
    "Assembly_LinkSelectLinked": "read-only",
    "Assembly_SelectJointsOfComponent": "read-only",
}

FEM_CONTEXT_COMMAND_BEHAVIOR = {
    "FEM_MeshClear": "in-place",
    "FEM_MeshClearGroups": "in-place",
    "FEM_MeshDisplayInfo": "read-only",
}


class TestContextMenuSurfaceContract(unittest.TestCase):
    """Every dynamically exposed command has one explicit tracking behavior."""

    ROOT = _source_checkout_root()

    @classmethod
    def setUpClass(cls):
        if cls.ROOT is None:
            raise unittest.SkipTest("VibeCAD source checkout is unavailable")

    @classmethod
    def _source(cls, relative):
        return (cls.ROOT / relative).read_text(encoding="utf-8")

    @classmethod
    def _commands_between(cls, relative, start, end, prefixes):
        source = cls._source(relative)
        begin = source.index(start)
        section = source[begin : source.index(end, begin)]
        prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
        return set(
            re.findall(
                rf'"((?:{prefix_pattern})_[A-Za-z0-9_]+)"',
                section,
            )
        )

    def test_model_context_commands_are_exhaustively_classified(self):
        discovered = self._commands_between(
            "src/Mod/PartDesign/Gui/Workbench.cpp",
            "void Workbench::setupContextMenu",
            "void Workbench::activated",
            ("PartDesign", "Std"),
        )
        self.assertEqual(discovered, set(MODEL_CONTEXT_COMMAND_BEHAVIOR))

    def test_sketch_viewport_context_commands_are_exhaustively_classified(
        self,
    ):
        discovered = self._commands_between(
            "src/Mod/Sketcher/Gui/ViewProviderSketch.cpp",
            "void ViewProviderSketch::generateContextMenu",
            "void ViewProviderSketch::preselectToSelection",
            ("Sketcher", "Std"),
        )
        self.assertEqual(discovered, set(SKETCH_CONTEXT_COMMAND_BEHAVIOR))
        self.assertEqual(
            len(SKETCH_CONTEXT_COMMAND_BEHAVIOR),
            len(SKETCH_CONTEXT_READ_ONLY)
            + len(SKETCH_CONTEXT_DISPATCHERS)
            + len(SKETCH_CONTEXT_EDITOR_LIFECYCLE)
            + len(SKETCH_CONTEXT_IN_PLACE),
        )

    def test_assembly_context_commands_are_exhaustively_classified(self):
        workbench = self._commands_between(
            "src/Mod/Assembly/InitGui.py",
            "    def ContextMenu",
            "    def setWatchers",
            ("Assembly",),
        )
        link_provider = self._commands_between(
            "src/Mod/Assembly/Gui/ViewProviderAssemblyLink.cpp",
            "void ViewProviderAssemblyLink::setupContextMenu",
            "    Q_UNUSED(receiver)",
            ("Assembly",),
        )
        self.assertEqual(
            workbench | link_provider,
            set(ASSEMBLY_CONTEXT_COMMAND_BEHAVIOR),
        )

    def test_direct_context_mutations_use_exact_tracked_transactions(self):
        assembly = self._source(
            "src/Mod/Assembly/Gui/ViewProviderAssemblyLink.cpp"
        )
        self.assertIn("resolveAssemblyLink(identity)", assembly)
        self.assertIn("getBookedTransactionID()", assembly)
        self.assertIn("hasPendingTransaction()", assembly)
        self.assertIn("openDocumentCommand(", assembly)
        self.assertIn("commitCommand(transactionId)", assembly)
        self.assertIn("abortCommand(transactionId)", assembly)
        self.assertIn("timeline->Operations.getValues()", assembly)
        self.assertIn("expectedDeletedGroundJointIds", assembly)
        self.assertIn(
            "deleted an unrelated document-history operation",
            assembly,
        )

        binder = self._source(
            "src/Mod/PartDesign/Gui/ViewProviderShapeBinder.cpp"
        )
        synchronize = binder[binder.index(
            "void ViewProviderSubShapeBinder::updatePlacement"
        ) :]
        self.assertIn(
            "resolveBinder<PartDesign::SubShapeBinder>(identity)",
            synchronize,
        )
        self.assertIn("getBookedTransactionID()", synchronize)
        self.assertIn("hasPendingTransaction()", synchronize)
        self.assertIn("openDocumentCommand(", synchronize)
        self.assertIn("commitCommand(transactionId)", synchronize)
        self.assertIn("abortCommand(transactionId)", synchronize)
        self.assertIn("timeline->Operations.getValues()", synchronize)
        self.assertIn(".ViewObject.doubleClicked()", synchronize)

        binder_header = self._source(
            "src/Mod/PartDesign/Gui/ViewProviderShapeBinder.h"
        )
        subshape_start = binder_header.index(
            "class PartDesignGuiExport ViewProviderSubShapeBinder"
        )
        subshape = binder_header[subshape_start:]
        self.assertIn("getTransactionText() const override", subshape)
        self.assertIn("return nullptr;", subshape)
        self.assertIn(
            "supportsDocumentTimelineEdit() const noexcept override",
            subshape,
        )
        self.assertIn("return false;", subshape)

    def test_fem_context_commands_are_exhaustively_classified(self):
        discovered = self._commands_between(
            "src/Mod/Fem/Gui/Workbench.cpp",
            "void Workbench::setupContextMenu",
            "Gui::ToolBarItem* Workbench::setupToolBars",
            ("FEM",),
        )
        self.assertEqual(discovered, set(FEM_CONTEXT_COMMAND_BEHAVIOR))

    def test_fastener_context_menu_reuses_the_registered_command_surface(self):
        source = self._source("src/Mod/Fasteners/InitGui.py")
        self.assertIn('FastenerBase.FSGetCommands("command")', source)
        self.assertIn('FastenerBase.FSGetCommands("screws")', source)
        self.assertIn(
            "FreeCAD.Qt.translate(\"Workbench\", \"Fasteners\"), self.list",
            source,
        )

    def test_native_metadata_adoption_reasserts_internal_presentation(self):
        for relative in (
            "src/Mod/Part/Gui/ModelingSelection.cpp",
            "src/Mod/PartDesign/App/FeatureMultiTransform.cpp",
            "src/Mod/Sketcher/Gui/Command.cpp",
            "src/Mod/Fem/Gui/Command.cpp",
            "src/Mod/Fem/Gui/TaskCreateElementSet.cpp",
        ):
            source = self._source(relative)
            self.assertIn(
                "setStatus(App::Property::Hidden, true)",
                source,
                relative,
            )
            self.assertIn(
                "setStatus(App::Property::LockDynamic, true)",
                source,
                relative,
            )
            self.assertIn(
                "setStatus(App::Property::NoRecompute, true)",
                source,
                relative,
            )

    def test_multitransform_conversion_stages_identity_before_reparenting(self):
        command = self._source("src/Mod/PartDesign/Gui/Command.cpp")
        conversion = command[
            command.index(
                "// Create a MultiTransform feature and move the "
                "Transformed feature inside it"
            ):
            command.index(
                "// Add the MultiTransform into the Body",
                command.index(
                    "// Create a MultiTransform feature and move the "
                    "Transformed feature inside it"
                ),
            )
        ]
        self.assertLess(
            conversion.index("stageExistingOperationResources"),
            conversion.index('"Transformations = ["'),
        )

        task = self._source(
            "src/Mod/PartDesign/Gui/TaskMultiTransformParameters.cpp"
        )
        finalize = task[
            task.index(
                "void TaskDlgMultiTransformParameters::"
                "finalizeAcceptedFeature"
            ):
            task.index(
                '#include "moc_TaskMultiTransformParameters.cpp"'
            )
        ]
        self.assertIn(
            "isProvisionallyEnrolledByCurrentTransaction",
            finalize,
        )
        self.assertIn("synchronizeTimelineResources()", finalize)
        self.assertIn("finalizeProvisionalOperationBlock(", finalize)

    def test_measure_and_inspection_context_actions_are_transient(self):
        measure = self._source("src/Mod/Measure/Gui/TaskMeasure.cpp")
        self.assertIn("menu->addAction(autoSaveAction)", measure)
        self.assertIn(
            "menu->addAction(newMeasurementBehaviourAction)",
            measure,
        )
        inspection = self._source(
            "src/Mod/Inspection/Gui/ViewProviderInspection.cpp"
        )
        self.assertIn("InspectionContextAnnotation", inspection)
        self.assertIn("InspectionContextLeaveInfoMode", inspection)


@unittest.skipIf(not App.GuiUp, "Context-menu tracking tests require the GUI")
class TestContextMenuTracking(unittest.TestCase):
    """Context-only model mutations obey the same history contract as the ribbon."""

    def setUp(self):
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("ContextMenuTracking")
        self.document.UndoMode = True

    def tearDown(self):
        Gui.Selection.clearSelection()
        document_names = {"ContextMenuTracking"}
        try:
            document_names.add(self.document.Name)
        except (AttributeError, RuntimeError):
            pass
        for document_name in document_names:
            if document_name in App.listDocuments():
                App.closeDocument(document_name)

    def _structural_body(self, name):
        self.document.openTransaction("Create structural Body fixture")
        try:
            body = self.document.addObject("PartDesign::Body", name)
            self.document.classifyProvisionalTimelineInternalObject(body)
            self.document.commitTransaction()
        except Exception:
            self.document.abortTransaction()
            raise
        return body

    def _body_history(self):
        body = self._structural_body("Body")
        first = body.newObject("PartDesign::Feature", "First")
        first.Shape = Part.makeBox(10, 10, 10)
        second = body.newObject("PartDesign::Feature", "Second")
        second.Shape = Part.makeBox(12, 10, 10)
        body.Tip = second
        self.document.recompute()
        self.assertTrue(body.isValid())
        self.assertTrue(first.isValid())
        self.assertTrue(second.isValid())
        return body, first, second

    @staticmethod
    def _select(obj):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)

    @staticmethod
    def _choose_dialog_item(index, tip_answer=None):
        state = {}

        def answer_tip_question():
            dialog = QtGui.QApplication.activeModalWidget()
            if not isinstance(dialog, QtGui.QMessageBox):
                state["tip_error"] = "Expected a move-tip question"
                if dialog is not None:
                    dialog.reject()
                return
            button = dialog.button(tip_answer)
            if button is None:
                state["tip_error"] = "Move-tip question omitted the requested answer"
                dialog.reject()
                return
            button.click()
            state["tip_answered"] = True

        def choose():
            dialog = QtGui.QApplication.activeModalWidget()
            combo = (
                dialog.findChild(QtGui.QComboBox)
                if dialog is not None
                else None
            )
            if dialog is None or combo is None:
                state["error"] = "Expected a list-selection dialog"
                if dialog is not None:
                    dialog.reject()
                return
            state["items"] = [
                combo.itemText(item)
                for item in range(combo.count())
            ]
            combo.setCurrentIndex(index)
            dialog.accept()
            state["accepted"] = True
            if tip_answer is not None:
                QtCore.QTimer.singleShot(30, answer_tip_question)

        QtCore.QTimer.singleShot(30, choose)
        return state

    @staticmethod
    def _operation_names(document):
        return tuple(obj.Name for obj in document.VibeCADTimeline.Operations)

    def _preexisting_timeline_pair(self, prefix):
        owner = self.document.addObject(
            "PartDesign::Feature", f"{prefix}Owner"
        )
        resource = self.document.addObject(
            "PartDesign::Feature", f"{prefix}Resource"
        )
        for obj in (owner, resource):
            obj.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
            )
            obj.setEditorMode("VibeCADTimelineRole", 0)
        owner.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
        )
        owner.setEditorMode("VibeCADTimelineOwner", 0)
        resource.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
        )
        resource.setEditorMode("VibeCADTimelineOwner", 0)
        return owner, resource

    def _save_and_reopen(self, filename):
        with tempfile.TemporaryDirectory(
            prefix="vibecad-context-menu-tracking-"
        ) as directory:
            path = str(Path(directory) / filename)
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)

    def test_set_tip_is_one_owned_in_place_transaction(self):
        body, first, second = self._body_history()
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        operations = tuple(obj.Name for obj in timeline.Operations)
        end_position = timeline.Position
        first_position = operations.index(first.Name) + 1
        self.assertLess(first_position, end_position)
        self.document.clearUndos()

        self._select(first)
        Gui.runCommand("PartDesign_MoveTip", 0)
        self.document.recompute()

        self.assertIs(body.Tip, first)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.UndoCount, 1)
        self.assertEqual(
            tuple(obj.Name for obj in timeline.Operations),
            operations,
        )
        self.assertEqual(timeline.Position, first_position)

        self.document.undo()
        self.document.recompute()
        self.assertIs(body.Tip, second)
        self.assertEqual(timeline.Position, end_position)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.redo()
        self.document.recompute()
        self.assertIs(body.Tip, first)
        self.assertEqual(timeline.Position, first_position)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_set_tip_refuses_a_caller_owned_transaction(self):
        body, first, second = self._body_history()
        self._select(first)
        self.document.openTransaction("Caller transaction")
        transaction = self.document.getBookedTransactionID()
        self.assertGreater(transaction, 0)

        Gui.runCommand("PartDesign_MoveTip", 0)

        self.assertIs(body.Tip, second)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction,
        )
        self.document.abortTransaction()
        self.assertFalse(self.document.HasPendingTransaction)

    def test_set_tip_survives_save_and_reopen(self):
        body, first, _second = self._body_history()
        self._select(first)
        Gui.runCommand("PartDesign_MoveTip", 0)
        self.assertIs(body.Tip, first)
        expected_position = self.document.VibeCADTimeline.Position

        with tempfile.TemporaryDirectory(
            prefix="vibecad-context-menu-tracking-"
        ) as directory:
            path = str(Path(directory) / "set-tip.FCStd")
            self.document.saveAs(path)
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(path)
            reopened_body = self.document.getObject("Body")
            reopened_first = self.document.getObject("First")
            self.assertIsNotNone(reopened_body)
            self.assertIsNotNone(reopened_first)
            self.assertIs(reopened_body.Tip, reopened_first)
            self.assertEqual(
                self.document.VibeCADTimeline.Position,
                expected_position,
            )
            self.assertFalse(self.document.HasPendingTransaction)

    def test_block_reorder_moves_recursive_resources_as_one_undo_step(self):
        owner = self.document.addObject("PartDesign::Feature", "Owner")
        owner.Shape = Part.makeBox(2, 2, 2)
        resource = self.document.addObject(
            "PartDesign::Feature", "OwnerResource"
        )
        resource.Shape = Part.makeBox(1, 1, 1)
        nested = self.document.addObject(
            "PartDesign::Feature", "NestedResource"
        )
        nested.Shape = Part.makeBox(0.5, 0.5, 0.5)
        target = self.document.addObject("PartDesign::Feature", "Target")
        target.Shape = Part.makeBox(3, 3, 3)
        for obj in (resource, nested):
            obj.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
            )
            obj.VibeCADTimelineRole = "resource"
            obj.addProperty(
                "App::PropertyLinkHidden",
                "VibeCADTimelineOwner",
            )
        resource.VibeCADTimelineOwner = owner
        nested.VibeCADTimelineOwner = resource
        self.document.recompute()
        before = self._operation_names(self.document)
        self.assertEqual(
            before,
            (owner.Name, resource.Name, nested.Name, target.Name),
        )
        self.document.clearUndos()

        with self.assertRaises(RuntimeError):
            self.document.reorderTimelineOperationBlocksAfter(
                [nested],
                target,
            )
        self.assertEqual(self._operation_names(self.document), before)

        self.document.openTransaction("Move semantic block")
        changed = self.document.reorderTimelineOperationBlocksAfter(
            [nested],
            target,
        )
        self.assertTrue(changed)
        self.document.commitTransaction()

        self.assertEqual(
            self._operation_names(self.document),
            (target.Name, owner.Name, resource.Name, nested.Name),
        )
        self.assertEqual(self.document.UndoCount, 1)
        self.document.undo()
        self.assertEqual(self._operation_names(self.document), before)
        self.document.redo()
        self.assertEqual(
            self._operation_names(self.document),
            (target.Name, owner.Name, resource.Name, nested.Name),
        )

    def test_existing_timeline_metadata_is_rehidden_across_domains(self):
        import PartDesignTimeline
        import UtilsAssembly
        from CompoundTools import Explode
        from femcommands import manager as fem_manager

        markers = (
            (
                "Assembly",
                UtilsAssembly.markTimelineOperation,
                UtilsAssembly.markTimelineResource,
            ),
            (
                "PartDesign",
                PartDesignTimeline.mark_operation,
                PartDesignTimeline.mark_resource,
            ),
            (
                "PartExplode",
                Explode._mark_timeline_operation,
                Explode._mark_timeline_resource,
            ),
        )
        for prefix, mark_operation, mark_resource in markers:
            owner, resource = self._preexisting_timeline_pair(prefix)
            mark_operation(owner)
            mark_resource(resource, owner)
            for obj, properties in (
                (
                    owner,
                    ("VibeCADTimelineRole", "VibeCADTimelineOwner"),
                ),
                (
                    resource,
                    ("VibeCADTimelineRole", "VibeCADTimelineOwner"),
                ),
            ):
                for property_name in properties:
                    self.assertTrue(
                        {
                            "Hidden",
                            "LockDynamic",
                            "NoRecompute",
                        }.issubset(
                            obj.getPropertyStatus(property_name)
                        ),
                        (
                            f"{prefix} left {property_name} outside "
                            "the canonical internal-storage contract"
                        ),
                    )

        fem_operation = self.document.addObject(
            "PartDesign::Feature", "FemImportedOperation"
        )
        fem_input = self.document.addObject(
            "PartDesign::Feature", "FemImportedInput"
        )
        for type_id, property_name in (
            ("App::PropertyString", "VibeCADTimelineRole"),
            (
                "App::PropertyLinkListHidden",
                "VibeCADTimelineReplacedInputs",
            ),
            ("App::PropertyLinkHidden", "VibeCADTimelineOwner"),
        ):
            fem_operation.addProperty(type_id, property_name)
            fem_operation.setEditorMode(property_name, 0)
        fem_manager._mark_timeline_replaced_inputs(
            fem_operation,
            [fem_input],
        )
        for property_name in (
            "VibeCADTimelineRole",
            "VibeCADTimelineReplacedInputs",
            "VibeCADTimelineOwner",
        ):
            self.assertTrue(
                {
                    "Hidden",
                    "LockDynamic",
                    "NoRecompute",
                }.issubset(
                    fem_operation.getPropertyStatus(property_name)
                ),
                (
                    f"FEM left {property_name} outside the canonical "
                    "internal-storage contract"
                ),
            )

    def test_block_reorder_rejects_transitive_dependency_through_helper(self):
        consumer = self.document.addObject(
            "PartDesign::Feature", "Consumer"
        )
        consumer.Shape = Part.makeBox(2, 2, 2)
        helper = self.document.addObject(
            "App::DocumentObjectGroup", "InternalHelper"
        )
        target = self.document.addObject("PartDesign::Feature", "Target")
        target.Shape = Part.makeBox(3, 3, 3)
        later = self.document.addObject("PartDesign::Feature", "Later")
        later.Shape = Part.makeBox(4, 4, 4)
        consumer.addProperty("App::PropertyLink", "HelperDependency")
        consumer.HelperDependency = helper
        helper.addProperty("App::PropertyLink", "LaterDependency")
        helper.LaterDependency = later
        self.document.recompute()
        before = self._operation_names(self.document)
        self.assertEqual(
            before,
            (consumer.Name, target.Name, later.Name),
        )

        self.document.openTransaction("Reject forward dependency")
        with self.assertRaises(RuntimeError):
            self.document.reorderTimelineOperationBlocksAfter(
                [consumer],
                target,
            )
        self.assertEqual(self._operation_names(self.document), before)
        self.document.abortTransaction()
        self.assertFalse(self.document.HasPendingTransaction)

    def test_move_object_to_body_keeps_body_and_global_order_atomic(self):
        source = self._structural_body("Body")
        first = source.newObject("PartDesign::Feature", "First")
        first.Shape = Part.makeBox(10, 10, 10)
        source.Tip = first
        target = self._structural_body("TargetBody")
        target_result = target.newObject(
            "PartDesign::Feature", "TargetResult"
        )
        target_result.Shape = Part.makeBox(8, 8, 8)
        target.Tip = target_result
        self.document.recompute()
        before = self._operation_names(self.document)
        self.document.clearUndos()

        state = self._choose_dialog_item(0)
        self._select(first)
        Gui.runCommand("PartDesign_MoveFeature", 0)
        self.document.recompute()

        self.assertNotIn("error", state)
        self.assertTrue(state.get("accepted"))
        self.assertFalse(source.hasObject(first))
        self.assertTrue(target.hasObject(first))
        self.assertEqual(
            [obj.Name for obj in target.Group],
            [target_result.Name, first.Name],
        )
        operations = self._operation_names(self.document)
        self.assertEqual(
            operations.index(first.Name),
            operations.index(target_result.Name) + 1,
        )
        self.assertEqual(set(operations), set(before))
        self.assertEqual(self.document.UndoCount, 1)

        self.document.undo()
        self.document.recompute()
        self.assertTrue(source.hasObject(first))
        self.assertFalse(target.hasObject(first))
        self.assertEqual(self._operation_names(self.document), before)
        self.document.redo()
        self.document.recompute()
        self.assertFalse(source.hasObject(first))
        self.assertTrue(target.hasObject(first))
        expected = self._operation_names(self.document)
        source_name = source.Name
        target_name = target.Name
        first_name = first.Name
        self._save_and_reopen("move-between-bodies.FCStd")
        reopened_source = self.document.getObject(source_name)
        reopened_target = self.document.getObject(target_name)
        reopened_first = self.document.getObject(first_name)
        self.assertFalse(reopened_source.hasObject(reopened_first))
        self.assertTrue(reopened_target.hasObject(reopened_first))
        self.assertEqual(self._operation_names(self.document), expected)

    def test_move_object_refuses_to_strand_a_dependent_source_feature(self):
        source, first, second = self._body_history()
        target = self._structural_body("TargetBody")
        before = self._operation_names(self.document)
        self.document.clearUndos()
        state = {}

        def dismiss_warning():
            dialog = QtGui.QApplication.activeModalWidget()
            if not isinstance(dialog, QtGui.QMessageBox):
                state["error"] = "Expected a dependency warning"
                if dialog is not None:
                    dialog.reject()
                return
            state["title"] = dialog.windowTitle()
            state["text"] = dialog.text()
            dialog.accept()

        QtCore.QTimer.singleShot(30, dismiss_warning)
        self._select(first)
        Gui.runCommand("PartDesign_MoveFeature", 0)
        self.document.recompute()

        self.assertNotIn("error", state)
        self.assertIn("depends", state.get("text", ""))
        self.assertTrue(source.hasObject(first))
        self.assertTrue(source.hasObject(second))
        self.assertFalse(target.hasObject(first))
        self.assertEqual(self._operation_names(self.document), before)
        self.assertEqual(self.document.UndoCount, 0)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_move_feature_after_keeps_body_and_global_order_atomic(self):
        body, first, second = self._body_history()
        body.Tip = first
        self.document.recompute()
        before = self._operation_names(self.document)
        self.document.clearUndos()

        state = self._choose_dialog_item(
            1,
            QtGui.QMessageBox.Yes,
        )
        self._select(first)
        Gui.runCommand("PartDesign_MoveFeatureInTree", 0)
        self.document.recompute()

        self.assertNotIn("error", state)
        self.assertNotIn("tip_error", state)
        self.assertTrue(state.get("accepted"))
        self.assertTrue(state.get("tip_answered"))
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [second.Name, first.Name],
        )
        self.assertIs(body.Tip, first)
        expected = self._operation_names(self.document)
        body_name = body.Name
        first_name = first.Name
        second_name = second.Name
        self._save_and_reopen("move-inside-body.FCStd")
        reopened_body = self.document.getObject(body_name)
        self.assertEqual(
            [obj.Name for obj in reopened_body.Group],
            [second_name, first_name],
        )
        self.assertEqual(self._operation_names(self.document), expected)

    def test_move_feature_targets_duplicate_labels_by_exact_identity(self):
        body, first, second = self._body_history()
        third = body.newObject("PartDesign::Feature", "Third")
        third.Shape = Part.makeBox(14, 10, 10)
        document_preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Document"
        )
        previous_duplicate_labels = document_preferences.GetBool(
            "DuplicateLabels",
            False,
        )
        document_preferences.SetBool("DuplicateLabels", True)
        try:
            second.Label = "Repeated operation"
            third.Label = "Repeated operation"
        finally:
            document_preferences.SetBool(
                "DuplicateLabels",
                previous_duplicate_labels,
            )
        body.Tip = third
        self.document.recompute()
        self.document.clearUndos()

        state = self._choose_dialog_item(1)
        self._select(first)
        Gui.runCommand("PartDesign_MoveFeatureInTree", 0)
        self.document.recompute()

        self.assertNotIn("error", state)
        self.assertTrue(state.get("accepted"))
        self.assertEqual(
            state.get("items"),
            [
                "Beginning of the body",
                f"Repeated operation — {second.Name}",
                f"Repeated operation — {third.Name}",
            ],
        )
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [second.Name, first.Name, third.Name],
        )
        self.assertEqual(self.document.UndoCount, 1)
        self.document.undo()
        self.document.recompute()
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [first.Name, second.Name, third.Name],
        )
        self.document.redo()
        self.document.recompute()
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [second.Name, first.Name, third.Name],
        )

    def test_move_complete_semantic_block_to_body_beginning(self):
        body = self._structural_body("Body")
        first = body.newObject("PartDesign::Feature", "First")
        first.Shape = Part.makeBox(4, 4, 4)
        second = body.newObject("PartDesign::Feature", "Second")
        second.Shape = Part.makeBox(5, 5, 5)
        resource = body.newObject(
            "PartDesign::Feature", "SecondResource"
        )
        resource.Shape = Part.makeBox(1, 1, 1)
        resource.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
        )
        resource.VibeCADTimelineRole = "resource"
        resource.addProperty(
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
        )
        resource.VibeCADTimelineOwner = second
        body.Tip = first
        self.document.recompute()
        before = self._operation_names(self.document)
        self.assertEqual(
            before,
            (first.Name, second.Name, resource.Name),
        )
        self.document.clearUndos()

        state = self._choose_dialog_item(0)
        self._select(second)
        Gui.runCommand("PartDesign_MoveFeatureInTree", 0)
        self.document.recompute()

        self.assertNotIn("error", state)
        self.assertTrue(state.get("accepted"))
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [second.Name, resource.Name, first.Name],
        )
        self.assertEqual(
            self._operation_names(self.document),
            (second.Name, resource.Name, first.Name),
        )
        self.assertEqual(self.document.UndoCount, 1)
        self.document.undo()
        self.document.recompute()
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [first.Name, second.Name, resource.Name],
        )
        self.assertEqual(self._operation_names(self.document), before)
        self.document.redo()
        self.document.recompute()
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [second.Name, resource.Name, first.Name],
        )
        expected = self._operation_names(self.document)
        self.assertEqual(
            expected,
            (second.Name, resource.Name, first.Name),
        )

        body_name = body.Name
        first_name = first.Name
        second_name = second.Name
        resource_name = resource.Name
        self._save_and_reopen("move-semantic-block.FCStd")
        reopened_body = self.document.getObject(body_name)
        self.assertEqual(
            [obj.Name for obj in reopened_body.Group],
            [second_name, resource_name, first_name],
        )
        self.assertEqual(self._operation_names(self.document), expected)

    def test_move_commands_refuse_a_caller_owned_transaction(self):
        body, first, second = self._body_history()
        target = self._structural_body("TargetBody")
        self.document.recompute()
        before = self._operation_names(self.document)
        self.document.openTransaction("Caller transaction")
        transaction = self.document.getBookedTransactionID()

        self._select(first)
        Gui.runCommand("PartDesign_MoveFeature", 0)
        Gui.runCommand("PartDesign_MoveFeatureInTree", 0)

        self.assertTrue(body.hasObject(first))
        self.assertFalse(target.hasObject(first))
        self.assertEqual(
            [obj.Name for obj in body.Group],
            [first.Name, second.Name],
        )
        self.assertEqual(self._operation_names(self.document), before)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction,
        )
        self.document.abortTransaction()
