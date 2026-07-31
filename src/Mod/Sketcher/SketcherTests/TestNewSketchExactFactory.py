# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source contracts for exact standalone Sketch creation."""

from pathlib import Path
import unittest

try:
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    from PySide import QtCore, QtGui

    from SketcherTests.GuiTestCase import SketcherGuiTestCase

    _GUI_RUNTIME_AVAILABLE = True
except ImportError:
    App = None
    Gui = None
    Part = None
    QtCore = None
    QtGui = None
    SketcherGuiTestCase = unittest.TestCase
    _GUI_RUNTIME_AVAILABLE = False


def _source_root():
    candidates = (Path.cwd(), *Path(__file__).resolve().parents)
    return next(
        (
            candidate
            for candidate in candidates
            if (
                candidate / "src/Mod/Sketcher/Gui/Command.cpp"
            ).is_file()
        ),
        None,
    )


class TestNewSketchExactFactorySourceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_root = _source_root()
        if cls.source_root is None:
            raise unittest.SkipTest("The Sketcher source tree is unavailable")
        cls.source = (
            cls.source_root / "src/Mod/Sketcher/Gui/Command.cpp"
        ).read_text(encoding="utf-8")
        cls.validation = (
            cls.source_root
            / "src/Mod/Sketcher/Gui/TaskSketcherValidation.cpp"
        ).read_text(encoding="utf-8")
        cls.edit_task = (
            cls.source_root
            / "src/Mod/Sketcher/Gui/TaskDlgEditSketch.cpp"
        ).read_text(encoding="utf-8")
        cls.factory = cls.source[
            cls.source.index(
                "Sketcher::SketchObject* createStandaloneSketchExact"
            ):
            cls.source.index(
                "\n}  // namespace",
                cls.source.index(
                    "Sketcher::SketchObject* createStandaloneSketchExact"
                ),
            )
        ]
        cls.command = cls.source[
            cls.source.index("void CmdSketcherNewSketch::activated"):
            cls.source.index("bool CmdSketcherNewSketch::isActive")
        ]

    def test_factory_retains_the_exact_document_object_return(self):
        self.assertIn("runDocumentObjectCommand", self.factory)
        self.assertIn(
            "Sketcher::SketchObject::getClassTypeId()",
            self.factory,
        )
        self.assertIn("resolveExactSketchDocument", self.factory)
        self.assertIn("resolveExactStandaloneSketch", self.factory)
        self.assertIn("resolveExactUsableSketchGroup", self.factory)
        self.assertIn("group->hasObject(sketch)", self.factory)
        self.assertIn(
            ".newObject('Sketcher::SketchObject','",
            self.factory,
        )
        self.assertIn(
            ".addObject('Sketcher::SketchObject','",
            self.factory,
        )
        self.assertNotIn("activeDocument()", self.factory)

    def test_command_uses_the_returned_sketch_for_all_setup(self):
        self.assertEqual(
            2,
            self.command.count("createStandaloneSketchExact("),
        )
        self.assertIn("getObjectCmd(sketch)", self.command)
        self.assertIn("resolveExactStandaloneSketch", self.command)
        self.assertIn("Gui.getDocument('%s').setEdit('%s')", self.command)
        self.assertIn("App.getDocument('%s').recompute()", self.command)
        self.assertNotIn("App.activeDocument()", self.command)
        self.assertNotIn("Gui.activeDocument()", self.command)
        self.assertNotIn("getObject(FeatName", self.command)
        self.assertNotIn("FeatName", self.command)

    def test_factory_failure_aborts_the_exact_open_transaction(self):
        self.assertEqual(
            2,
            self.command.count("const int transactionId = openCommand("),
        )
        self.assertGreaterEqual(
            self.command.count("abortCommand(transactionId)"),
            2,
        )
        self.assertGreaterEqual(
            self.command.count("resetTransactionID()"),
            2,
        )
        self.assertGreater(
            self.command.index("openCommand("),
            self.command.index("resolveExactSketchDocument"),
        )

    def test_setup_surface_filters_to_exact_current_document_selection(self):
        helper = self.source[
            self.source.index(
                "bool selectionBelongsToExactSketchDocument"
            ):
            self.source.index(
                "Sketcher::SketchObject* createStandaloneSketchExact"
            )
        ]
        self.assertIn("Gui::ResolveMode::NoResolve", helper)
        self.assertIn("object->getDocument() == document", helper)
        self.assertIn("PartGui::isModelingObjectActive", helper)
        self.assertIn("selectedExactUsableSketches", helper)

        for command_name, end_name, exact_boundary in (
            (
                "void CmdSketcherReorientSketch::activated",
                "bool CmdSketcherReorientSketch::isActive",
                "selectedExactUsableSketches",
            ),
            (
                "void CmdSketcherMapSketch::activated",
                "bool CmdSketcherMapSketch::isActive",
                "captureExactSketchSelection",
            ),
            (
                "void CmdSketcherMirrorSketch::activated",
                "bool CmdSketcherMirrorSketch::isActive",
                "selectedExactUsableSketches",
            ),
            (
                "void CmdSketcherMergeSketches::activated",
                "bool CmdSketcherMergeSketches::isActive",
                "selectedExactUsableSketches",
            ),
        ):
            command = self.source[
                self.source.index(command_name):
                self.source.index(
                    end_name,
                    self.source.index(command_name),
                )
            ]
            self.assertIn(
                exact_boundary,
                command,
                command_name,
            )
            self.assertNotIn(
                "getSelectionEx(nullptr",
                command,
                command_name,
            )

    def test_merge_and_mirror_are_exact_atomic_native_copy_operations(self):
        for command_name, end_name in (
            (
                "void CmdSketcherMirrorSketch::activated",
                "bool CmdSketcherMirrorSketch::isActive",
            ),
            (
                "void CmdSketcherMergeSketches::activated",
                "bool CmdSketcherMergeSketches::isActive",
            ),
        ):
            command = self.source[
                self.source.index(command_name):
                self.source.index(
                    end_name,
                    self.source.index(command_name),
                )
            ]
            self.assertIn("exactSketchObjectIdentity", command)
            self.assertIn("resolveExactUsableStandaloneSketch", command)
            self.assertIn("const int transactionId = openCommand(", command)
            self.assertIn("commitCommand(transactionId)", command)
            self.assertIn("abortCommand(transactionId)", command)
            self.assertIn("markSketchCommandOutputs", command)
            self.assertIn('Gui::cmdAppDocument', command)
            self.assertNotIn("SourceSketches", command)
            self.assertNotIn("App.activeDocument()", command)

    def test_validation_mutations_cannot_join_a_foreign_transaction(self):
        runner = self.validation[
            self.validation.index(
                "bool SketcherValidation::runExactMutation"
            ):
            self.validation.index(
                "void SketcherValidation::setupConnections"
            )
        ]
        self.assertIn("resolveExactSketch()", runner)
        self.assertIn("getBookedTransactionID()", runner)
        self.assertIn("App::NullTransaction", runner)
        self.assertIn("Gui::ExactTransaction", runner)
        self.assertIn("resolveExactSketch(false)", runner)
        self.assertNotIn("openTransaction(", runner)
        self.assertNotIn("commitTransaction(", runner)

        self.assertNotIn(
            "doc->openTransaction(",
            self.validation,
        )
        self.assertNotIn(
            "doc->commitTransaction(",
            self.validation,
        )

    def test_edit_task_resolves_document_and_sketch_by_exact_identity(self):
        self.assertIn(
            "TaskDlgEditSketch::resolveExactGuiDocument",
            self.edit_task,
        )
        self.assertIn(
            "TaskDlgEditSketch::resolveExactSketchView",
            self.edit_task,
        )
        self.assertIn("getObjectByID(exactSketchId)", self.edit_task)
        self.assertIn(
            "document->getObject(exactSketchName.c_str()) != object",
            self.edit_task,
        )
        self.assertIn(
            "restoredDocument->getObjectByID(sketchId)",
            self.edit_task,
        )


@unittest.skipUnless(
    _GUI_RUNTIME_AVAILABLE,
    "The FreeCAD GUI runtime is unavailable",
)
class TestNewSketchExactFactoryRuntime(SketcherGuiTestCase):
    def setUp(self):
        super().setUp()
        Gui.activateWorkbench("SketcherWorkbench")
        self.doc = self.new_document("ExactStandaloneSketchFactory")
        self.doc.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self.group = self.doc.addObject(
            "App::DocumentObjectGroup",
            "SketchGroup",
        )
        self.doc.recompute()
        self.flush_gui(80)

    def _accept_orientation_dialog(self):
        attempts = [0]

        def accept():
            attempts[0] += 1
            modal = QtGui.QApplication.activeModalWidget()
            if modal is None:
                if attempts[0] < 200:
                    QtCore.QTimer.singleShot(5, accept)
                return
            modal.accept()

        QtCore.QTimer.singleShot(0, accept)

    def _start_sketch_with_group_context(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.group)
        self._accept_orientation_dialog()
        Gui.runCommand("Sketcher_NewSketch", 0)
        self.flush_gui(80)
        sketches = [
            obj
            for obj in self.doc.Objects
            if obj.isDerivedFrom("Sketcher::SketchObject")
        ]
        self.assertEqual(1, len(sketches))
        self.assertIsNotNone(Gui.activeDocument().getInEdit())
        self.assertFalse(self.group.hasObject(sketches[0]))
        return sketches[0]

    def test_group_context_does_not_own_result_and_cancel_rolls_it_back(self):
        original_objects = tuple(self.doc.Objects)
        original_operations = tuple(
            next(
                (
                    obj
                    for obj in self.doc.Objects
                    if obj.TypeId == "App::DocumentTimeline"
                ),
                (),
            ).Operations
        ) if any(
            obj.TypeId == "App::DocumentTimeline"
            for obj in self.doc.Objects
        ) else ()
        original_undo_count = self.doc.UndoCount

        self._start_sketch_with_group_context()
        Gui.runCommand("Sketcher_CancelSketch", 0)
        self.flush_gui(80)

        self.assertEqual(original_objects, tuple(self.doc.Objects))
        self.assertEqual(original_undo_count, self.doc.UndoCount)
        self.assertFalse(self.doc.HasPendingTransaction)
        timeline = next(
            (
                obj
                for obj in self.doc.Objects
                if obj.TypeId == "App::DocumentTimeline"
            ),
            None,
        )
        self.assertEqual(
            original_operations,
            tuple(timeline.Operations) if timeline else (),
        )

    def test_group_context_result_accepts_as_one_global_history_operation(self):
        original_undo_count = self.doc.UndoCount
        sketch = self._start_sketch_with_group_context()
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(0, 0, 0),
                App.Vector(6, 3, 0),
            ),
            False,
        )

        Gui.runCommand("Sketcher_LeaveSketch", 0)
        self.flush_gui(80)

        self.assertIs(self.doc.getObject(sketch.Name), sketch)
        self.assertFalse(self.group.hasObject(sketch))
        timeline = next(
            obj
            for obj in self.doc.Objects
            if obj.TypeId == "App::DocumentTimeline"
        )
        self.assertIn(sketch, timeline.Operations)
        self.assertEqual(original_undo_count + 1, self.doc.UndoCount)
        self.assertFalse(self.doc.HasPendingTransaction)


if __name__ == "__main__":
    unittest.main()
