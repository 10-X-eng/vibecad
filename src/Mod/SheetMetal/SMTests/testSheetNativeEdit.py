# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native async completion must own geometry without absorbing other edits."""

import unittest
from unittest.mock import patch

import FreeCAD as App

import SheetMetalHistoryOperations as Shared
from SMTests import testSheetNativeInspect


class TestSheetNativeEdit(unittest.TestCase):
    def setUp(self):
        self.fixture = testSheetNativeInspect.TestSheetNativeInspect()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model, self.sheet = self.fixture.model, self.fixture.sheet
        self.context = self.fixture.context
        self.context.state.begin_native_authority(self.model.doc.Uid)
        self.context.undo_ledger.begin_run("native-sheet-edit-test")

    def prepared(self, radius=4):
        return Shared.prepare(self.sheet, {
            "operation": "add_circle", "center": list(self.model.bend_pick()[2]), "radius": radius},
            expected_revision=Shared.capture_revision(self.sheet))

    def start(self, prepared=None):
        import SheetMetalNativeEdit
        prepared = self.prepared() if prepared is None else prepared
        ticket = self.context.state.begin_call(self.model.doc.Uid, "sheet_metal.edit")
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        future = SheetMetalNativeEdit.start(self.context, ticket, prepared)
        return ticket, future

    def wait(self, future):
        self.fixture.fixture.wait_for(future.done)
        return future.result()

    def close_other(self, document):
        self.model.settle(document)
        App.closeDocument(document.Name)

    def assert_no_receipt(self, ticket):
        self.assertIsNone(self.context.state.completed_mutation_receipt(ticket))
        self.assertFalse(self.context.undo_ledger.available(self.model.doc, self.context.state)["available"])

    def test_ready_receipt_and_assistant_undo_follow_async_geometry(self):
        doc, state = self.model.doc, self.context.state
        before, undo = self.sheet.Shape.Volume, doc.UndoCount
        ticket, future = self.start()
        commit_revision = state.current_revision(doc.Uid)
        self.assertFalse(future.done())
        self.assert_no_receipt(ticket)
        self.assertFalse(doc.HasPendingTransaction)
        result = self.wait(future)
        self.assertEqual(result["phase"], "ready")
        receipt = state.completed_mutation_receipt(ticket)
        self.assertGreater(receipt.revision_after, commit_revision)
        self.assertEqual(receipt.revision_after, state.current_revision(doc.Uid))
        self.assertEqual(result["receipt"], receipt.summary())
        self.assertTrue(result["assistant_undo_available"])
        self.assertTrue(self.context.undo_ledger.available(doc, state)["available"])
        step = doc.getObject(result["object_name"])
        self.assertLess(step.Shape.Volume, before)
        self.assertTrue(step.FlatShape.isValid())
        self.assertEqual(doc.UndoCount, undo+1)
        self.assertEqual(receipt.created[0].object_name, step.Name)
        undo_ticket = state.begin_call(doc.Uid, "native.undo")
        self.context.undo_ledger.undo_latest(ticket=undo_ticket, document=doc, state=state,
            reauthorize_turn=self.context.guard, active_document=lambda: App.ActiveDocument)
        self.model.recompute()
        self.assertIsNone(doc.getObject(result["object_name"]))
        self.assertAlmostEqual(self.sheet.Shape.Volume, before, places=6)

    def test_missing_folded_region_can_be_repaired_before_any_mutation(self):
        from VibeCADNativeSheetMetalEditRuntime import (
            NativeSheetMetalEditRuntime, NativeSheetMetalEditRequestError)
        region, folded, _ = self.model.bend_pick()
        runtime = NativeSheetMetalEditRuntime(self.context)
        arguments = {"operation": "add_circle", "object_name": self.sheet.Name,
                     "center": list(folded), "radius": 4, "representation": "folded"}
        before = self.model.doc.UndoCount, self.sheet.PreparedInputHash, self.sheet.Shape.Volume
        ticket = self.context.state.begin_call(self.model.doc.Uid, "sheet_metal.edit")
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        with self.assertRaises(NativeSheetMetalEditRequestError) as caught:
            runtime.execute_async(arguments, ticket=ticket)
        self.assertIn("list_regions", caught.exception.failure()["repair"])
        self.assertEqual((self.model.doc.UndoCount, self.sheet.PreparedInputHash, self.sheet.Shape.Volume), before)
        listed = self.fixture.call("list_regions", target=self.fixture.target())
        self.assertIn(region, [entry["region"] for entry in listed["items"]])
        repaired = runtime.execute_async({**arguments, "region": region}, ticket=ticket)
        result = self.wait(repaired)
        self.assertEqual(result["phase"], "ready")
        hole = self.model.doc.getObject(result["object_name"])
        self.assertLess(hole.Shape.Volume, before[2])
        self.assertTrue(hole.FlatShape.isValid())
        volume = hole.Shape.Volume
        operation_id = Shared.inspect(hole)["cuts"][0]["id"]
        update_ticket = self.context.state.begin_call(self.model.doc.Uid, "sheet_metal.edit")
        self.addCleanup(lambda: self.context.state.cancel_mutation(update_ticket))
        resized = runtime.execute_async({"operation": "update_circle", "object_name": hole.Name,
            "operation_id": operation_id, "radius": 5, "representation": "folded"}, ticket=update_ticket)
        self.assertEqual(self.wait(resized)["phase"], "ready")
        self.assertLess(hole.Shape.Volume, volume)

    def test_unrelated_label_edit_and_aba_are_not_claimed_by_the_receipt(self):
        import SheetMetalNativeEdit
        original = self.sheet.Label
        ticket, future = self.start()
        self.sheet.Label = "A user edit during geometry"
        self.sheet.Label = original
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError) as caught:
            self.wait(future)
        self.assertTrue(caught.exception.failure()["parameters_committed"])
        self.assert_no_receipt(ticket)

    def test_nested_gui_edit_inside_geometry_notification_is_not_owned(self):
        import SheetMetalNativeEdit
        changed = []
        sheet = self.sheet
        class Observer:
            def slotChangedObjectWithOrigin(self, obj, name, origin):
                if obj.Document is sheet.Document and name == "Shape" and origin and not changed:
                    changed.append(True)
                    sheet.Label = "User edit from a GUI callback"
        observer = Observer()
        App.addDocumentObserver(observer)
        self.addCleanup(lambda: App.removeDocumentObserver(observer))
        ticket, future = self.start()
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError):
            self.wait(future)
        self.assertTrue(changed)
        self.assert_no_receipt(ticket)

    def test_other_document_and_presentation_changes_do_not_invalidate_geometry(self):
        other = App.newDocument("OtherNativeSheetEdit")
        self.addCleanup(lambda: self.close_other(other))
        obj = other.addObject("Part::Feature", "Unrelated")
        App.setActiveDocument(self.model.doc.Name)
        ticket, future = self.start()
        obj.Label = "Another document's edit"
        self.sheet.Visibility = not self.sheet.Visibility
        result = self.wait(future)
        self.assertEqual(result["phase"], "ready")
        self.assertIsNotNone(self.context.state.completed_mutation_receipt(ticket))

    def test_closing_other_document_finishes_sheet_projection(self):
        other = App.newDocument("OtherSheetProjection")
        other_name = other.Name
        self.addCleanup(lambda: self.close_other(App.getDocument(other_name))
                        if other_name in App.listDocuments() else None)
        obj = other.addObject("Part::Feature", "Unrelated")
        App.setActiveDocument(self.model.doc.Name)
        ticket, future = self.start()
        obj.Label = "Another document's edit"
        self.sheet.Visibility = not self.sheet.Visibility
        result = self.wait(future)
        self.assertEqual(result["phase"], "ready")
        self.assertIsNotNone(self.context.state.completed_mutation_receipt(ticket))

        # Closing a second document can interrupt a sliced Tree refresh after
        # its object queue has drained. The surviving sheet still needs the
        # remaining projection work and must become closable without an edit.
        self.close_other(other)
        self.model.settle()
        self.assertFalse(self.model.doc.PresentationUpdateActive)
        self.assertTrue(self.model.doc.isClosable())

    def test_lost_active_document_leaves_parameters_without_an_assistant_receipt(self):
        import SheetMetalNativeEdit
        ticket, future = self.start()
        other = App.newDocument("ChangedNativeSheetTab")
        self.addCleanup(lambda: self.close_other(other))
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError):
            self.wait(future)
        App.setActiveDocument(self.model.doc.Name)
        self.assert_no_receipt(ticket)

    def test_failed_geometry_preserves_the_exact_cut_for_repair(self):
        import SheetMetalNativeEdit
        undo = self.model.doc.UndoCount
        ticket, future = self.start(self.prepared(radius=10000))
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError) as caught:
            self.wait(future)
        failure = caught.exception.failure()
        self.assertTrue(failure["parameters_committed"])
        self.assertEqual(self.model.doc.UndoCount, undo+1)
        step = self.model.doc.getObject(failure["object_name"])
        self.assertIsNotNone(step)
        self.assertEqual(step.VibeCADTimelineEditCommand, "SheetMetal_EditHistoryCut")
        self.assert_no_receipt(ticket)

    def test_queue_failure_does_not_issue_a_receipt_or_discard_the_cut(self):
        import SheetMetalNativeEdit
        with patch.object(SheetMetalNativeEdit, "_queue_tracked", side_effect=RuntimeError("queue unavailable")):
            ticket, future = self.start()
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError) as caught:
            self.wait(future)
        self.assertTrue(caught.exception.failure()["parameters_committed"])
        self.assertIsNotNone(self.model.doc.getObject(caught.exception.failure()["object_name"]))
        self.assert_no_receipt(ticket)
        self.model.recompute()

    def test_stale_prepared_parameters_are_rejected_before_a_transaction(self):
        import SheetMetalNativeEdit
        prepared = self.prepared()
        self.sheet.Label = "Changed before the tool started"
        undo = self.model.doc.UndoCount
        with self.assertRaises(RuntimeError):
            self.start(prepared)
        self.assertEqual(self.model.doc.UndoCount, undo)

    def test_material_dimensions_and_suppression_finalize_the_same_native_chain(self):
        import SheetMetalCutHistory as History
        _, future = self.start()
        step = self.model.doc.getObject(self.wait(future)["object_name"])
        requests = (
            {"operation": "set_parameters", "changes": {"thickness": 2, "flange_length": 36}},
            {"operation": "set_material", "material": "Steel", "k_factor": .35},
            {"operation": "update_circle", "operation_id": step.OperationId, "radius": 3},
            {"operation": "set_suppressed", "operation_id": step.OperationId, "suppressed": True},
            {"operation": "set_suppressed", "operation_id": step.OperationId, "suppressed": False},
        )
        for arguments in requests:
            with self.subTest(operation=arguments["operation"]):
                prepared = Shared.prepare(step, arguments, expected_revision=Shared.capture_revision(step))
                ticket, future = self.start(prepared)
                result = self.wait(future)
                self.assertEqual(result["object_name"], step.Name)
                self.assertTrue(result["assistant_undo_available"])
                self.assertIsNotNone(self.context.state.completed_mutation_receipt(ticket))
        self.assertEqual(self.sheet.Material, "Steel")
        self.assertEqual(self.sheet.KFactor, .35)
        self.assertAlmostEqual(History.get_prepared(step).mapping.thickness, 2)
        self.assertAlmostEqual(float(step.Radius), 3)
        self.assertFalse(step.Suppressed)

    def test_completed_ticket_replay_never_recomputes_or_creates_another_undo(self):
        import SheetMetalNativeEdit
        prepared = self.prepared()
        ticket, future = self.start(prepared)
        result = self.wait(future)
        before = (self.model.doc.UndoCount, self.context.state.current_revision(self.model.doc.Uid))
        with patch.object(SheetMetalNativeEdit, "_queue_tracked", side_effect=AssertionError("replayed geometry")):
            repeated = SheetMetalNativeEdit.start(self.context, ticket, prepared).result()
        self.assertEqual(repeated["job_id"], result["job_id"])
        self.assertEqual(repeated["revision"], result["revision"])
        self.assertEqual(before, (self.model.doc.UndoCount,
                                  self.context.state.current_revision(self.model.doc.Uid)))

    def test_cancelled_client_detaches_after_geometry_without_publishing_a_receipt(self):
        import SheetMetalNativeEdit
        original = SheetMetalNativeEdit._Completion.close
        with patch.object(SheetMetalNativeEdit._Completion, "close", autospec=True,
                          side_effect=original) as close:
            ticket, future = self.start()
            self.assertTrue(future.cancel())
            self.fixture.fixture.wait_for(lambda: close.call_count == 1)
        self.assertTrue(future.cancelled())
        self.assert_no_receipt(ticket)
        self.assertFalse(self.model.doc.RecomputePending)

    def test_unexplained_revision_change_is_rejected_even_without_a_property_event(self):
        import SheetMetalNativeEdit
        ticket, future = self.start()
        self.context.state.note_structural_change(self.model.doc.Uid)
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError):
            self.wait(future)
        self.assert_no_receipt(ticket)

    def test_cancellation_cannot_race_receipt_publication_once_completion_starts(self):
        import SheetMetalNativeEdit
        original = SheetMetalNativeEdit._Completion._verify_completion
        attempts = []
        def verify(completion, result):
            original(completion, result)
            attempts.append(completion.future.cancel())
        with patch.object(SheetMetalNativeEdit._Completion, "_verify_completion",
                          autospec=True, side_effect=verify):
            ticket, future = self.start()
            result = self.wait(future)
        self.assertEqual(attempts, [False])
        self.assertEqual(result["phase"], "ready")
        self.assertIsNotNone(self.context.state.completed_mutation_receipt(ticket))
