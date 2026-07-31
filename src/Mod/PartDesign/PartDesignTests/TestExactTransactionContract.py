"""VibeCAD contracts for exact native modeling transaction ownership."""

from pathlib import Path
import unittest


def source_root():
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "src/Gui/ExactTransaction.cpp").exists():
            return candidate
    return None


class TestExactTransactionContract(unittest.TestCase):
    def setUp(self):
        self.root = source_root()
        if self.root is None:
            self.skipTest("VibeCAD source checkout is unavailable")

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def test_direct_app_close_reports_exact_identity_before_legacy_close(self):
        application = self.read("src/App/Application.h")
        close = self.read("src/App/AutoTransaction.cpp")
        dialog = self.read("src/Gui/TaskView/TaskDialog.cpp")

        self.assertIn("signalExactTransactionClosed", application)
        exact_signal = close.index(
            "signalExactTransactionClosed(id, abort, docsToPoke);"
        )
        legacy_scope_end = close.index("return true;", exact_signal)
        self.assertLess(exact_signal, legacy_scope_end)
        self.assertIn(
            "signalExactTransactionClosed.connect",
            dialog,
        )
        self.assertIn(
            "recordCommandTransactionCompletion",
            dialog,
        )

    def test_shared_owner_is_successor_safe_and_retains_failed_close(self):
        header = self.read("src/Gui/ExactTransaction.h")
        owner = self.read("src/Gui/ExactTransaction.cpp")

        for public_surface in (
            "bool commit() noexcept;",
            "bool abort() noexcept;",
            "bool retry() noexcept;",
            "bool ownsCurrentTransaction() const noexcept;",
        ):
            self.assertIn(public_surface, header)
        self.assertIn("transactionIsActive", owner)
        self.assertIn("detachedEverywhere", owner)
        self.assertIn("pendingStates().insert_or_assign", owner)
        self.assertIn("signalTransactionLockChanged.connect", owner)
        self.assertIn("signalBecameStable.connect", owner)
        self.assertIn("item.document->lockTransaction();", owner)
        self.assertIn("item.document->unlockTransaction();", owner)

    def test_rollback_presentation_waits_for_confirmed_exact_abort(self):
        dialog = self.read("src/Gui/TaskView/TaskDialog.cpp")
        close_check = dialog.index(
            "if (!finishCommandTransaction(*state, false))"
        )
        retention = dialog.index(
            "retainPendingInteractionRollback(*state, restorePresentation);",
            close_check,
        )
        visibility_restore = dialog.index(
            "for (const auto& visibility : state->visibility)",
            retention,
        )
        self.assertLess(close_check, retention)
        self.assertLess(retention, visibility_restore)
        self.assertIn(
            "retryPendingInteractionRollback",
            dialog,
        )

    def test_pending_edit_close_retries_without_reinvoking_task_callback(self):
        document = self.read("src/Gui/Document.cpp")
        retry = document[
            document.index("void Document::retryPendingEditTransaction()") :
            document.index("void Document::resetIfEditing()")
        ]
        self.assertIn("finishEditTransaction(transactionId, commit)", retry)
        self.assertNotIn("accept()", retry)
        self.assertNotIn("reject()", retry)
        self.assertIn("armPendingEditTransactionRetry();", document)
        self.assertIn("signalTransactionLockChanged.connect", document)
        self.assertIn("signalBecameStable.connect", document)

    def test_closing_editor_transfers_failed_abort_without_gui_pointer(self):
        document = self.read("src/Gui/Document.cpp")
        destructor = document[
            document.index("Document::~Document()") :
            document.index(
                "// 3D viewer handling",
                document.index("Document::~Document()"),
            )
        ]
        self.assertIn("discardOwnedEditCommandInteraction(this)", destructor)
        self.assertIn("retainDetachedExactAbort", destructor)
        retained = document[
            document.index("void retainDetachedExactAbort(") :
            document.index("// Pimpl class")
        ]
        self.assertIn("signalTransactionLockChanged.connect", retained)
        self.assertIn("signalBecameStable.connect", retained)
        self.assertIn("signalDeletedDocument.connect", retained)
        self.assertNotIn("Gui::Document*", retained)

    def test_native_owners_use_one_exact_transaction_implementation(self):
        owners = (
            "src/Mod/Part/Gui/ModelingSelection.cpp",
            "src/Mod/Measure/Gui/TaskMeasure.cpp",
            "src/Mod/Measure/Gui/TaskMassProperties.cpp",
            "src/Mod/Inspection/Gui/VisualInspection.cpp",
            "src/Mod/Part/Gui/CrossSections.cpp",
        )
        for relative in owners:
            source = self.read(relative)
            self.assertIn("Gui::ExactTransaction", source, relative)

    def test_tree_cross_document_drop_uses_one_exact_transaction(self):
        tree = self.read("src/Gui/Tree.cpp")
        drop_start = tree.index("bool TreeWidget::dropInDocument(")
        drop_end = tree.index(
            "bool TreeWidget::dropInObject(",
            drop_start,
        )
        drop = tree[drop_start:drop_end]

        self.assertIn(
            "std::vector<App::Document*> transactionDocuments",
            drop,
        )
        self.assertIn(
            "std::make_unique<ExactTransaction>",
            drop,
        )
        self.assertIn(
            "prepareTimelineExport({obj}, true)",
            drop,
        )
        self.assertIn(
            "copyTimelineObjects(",
            drop,
        )
        self.assertIn("adoptTimelineImport(imported);", drop)
        self.assertIn("transaction->abort()", drop)
        self.assertIn("transaction->commit()", drop)
        self.assertIn("deleteTimelineExportSource(sourcePlan)", drop)
        self.assertIn(
            "TaskDialog::beginCommandInvocation()",
            drop,
        )
        self.assertIn(
            "TaskDialog::endCommandInvocation(success)",
            drop,
        )
        self.assertNotIn(
            '<< (da == Qt::CopyAction ? "copyObject(" : "moveObject(")',
            drop,
            "The legacy replay command omits semantic closure and exact "
            "cross-document deletion",
        )

    def test_hole_overlay_defers_updates_during_transaction_replay(self):
        view = self.read("src/Mod/PartDesign/Gui/ViewProviderHole.cpp")
        update_start = view.index("void ViewProviderHole::updateOverlay()")
        transaction_guard = view.index(
            "document->isPerformingTransaction()",
            update_start,
        )
        recompute_guard = view.index(
            "document->testStatus(App::Document::Recomputing)",
            update_start,
        )
        restore_exemption = view.index(
            "!documentRestoreFinished",
            update_start,
        )
        restore_guard = view.index(
            "document->testStatus(App::Document::Restoring)",
            update_start,
        )
        first_scene_update = view.index(
            "updateDesignHoleOverlays(hole);",
            update_start,
        )
        self.assertLess(transaction_guard, first_scene_update)
        self.assertLess(recompute_guard, first_scene_update)
        self.assertLess(restore_exemption, restore_guard)
        self.assertLess(restore_guard, first_scene_update)

    def test_hole_normal_reports_empty_profile_as_no_overlay(self):
        view = self.read("src/Mod/PartDesign/Gui/ViewProviderHole.cpp")
        normal_start = view.index("ViewProviderHole::getHoleNormal(")
        normal_end = view.index(
            "ViewProviderHole::getHoleOrigin(",
            normal_start,
        )
        normal = view[normal_start:normal_end]

        profile_read = normal.index("pcHole->getProfileShape()")
        base_catch = normal.index(
            "catch (const Base::Exception&)",
            profile_read,
        )
        occ_catch = normal.index(
            "catch (const Standard_Failure&)",
            base_catch,
        )
        self.assertLess(normal.index("try {"), profile_read)
        self.assertLess(
            base_catch,
            normal.index("return std::nullopt;", base_catch),
        )
        self.assertLess(
            occ_catch,
            normal.index("return std::nullopt;", occ_catch),
        )

    def test_hole_overlay_refreshes_after_document_becomes_stable(self):
        view = self.read("src/Mod/PartDesign/Gui/ViewProviderHole.cpp")
        stable_connect = view.index("signalBecameStable.connect")
        stable_refresh = view.index("this->updateOverlay();", stable_connect)
        restore_connect = view.index("signalFinishRestoreDocument.connect")
        restore_recorded = view.index(
            ".documentRestoreFinished = true;",
            restore_connect,
        )
        restore_refresh = view.index(
            "this->updateOverlay();",
            restore_recorded,
        )
        self.assertLess(stable_connect, stable_refresh)
        self.assertLess(restore_connect, restore_recorded)
        self.assertLess(restore_recorded, restore_refresh)


if __name__ == "__main__":
    unittest.main()
