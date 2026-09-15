# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create shared folded/flat state through an owned asynchronous native command."""

import unittest
from types import SimpleNamespace

import SheetMetalOperations as Operations
from SMTests import testSheetNativeEdit


class TestSheetNativeCreation(unittest.TestCase):
    def setUp(self):
        self.fixture = testSheetNativeEdit.TestSheetNativeEdit()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.context, self.model = self.fixture.context, self.fixture.model
        self.model.edit(lambda: self.model.doc.removeObject(self.model.sheet.Name))
        self.source = self.model.doc.BaseBend
        self.source.Visibility = True
        self.face = f"Face{self.model.root}"

    def start(self, *, face=None, revision=None):
        import SheetMetalNativeEdit
        ticket = self.context.state.begin_call(self.model.doc.Uid, "sheet_metal.create")
        future = SheetMetalNativeEdit.start_creation(self.context, ticket, self.source,
            self.face if face is None else face,
            expected_revision=Operations.capture_source_revision(self.source) if revision is None else revision)
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        self.model.sheet = next(obj for obj in self.model.doc.Objects
                                if "PreparedInputHash" in obj.PropertiesList)
        self.fixture.fixture.fixture.view = self.model.sheet.ViewObject.Proxy
        return ticket, future

    def wait(self, future):
        self.fixture.fixture.fixture.wait_for(future.done)
        return future.result()

    def test_native_creation_prepares_both_views_and_one_owned_history_operation(self):
        doc, state = self.model.doc, self.context.state
        undo = doc.UndoCount
        ticket, future = self.start()
        self.assertFalse(future.done())
        self.assertIsNone(state.completed_mutation_receipt(ticket))
        self.assertFalse(doc.HasPendingTransaction)
        result = self.wait(future)
        sheet = self.model.sheet
        self.assertEqual(result["phase"], "ready")
        self.assertTrue(result["assistant_undo_available"])
        self.assertEqual(doc.UndoCount, undo+1)
        self.assertEqual(sheet.SourceFace, (self.source, [self.face]))
        self.model.assert_valid_pair()
        self.assertFalse(self.source.Visibility)
        self.assertEqual(sheet.VibeCADTimelineEditCommand, "SheetMetal_EditParameters")
        self.assertEqual(sheet.VibeCADTimelineRole, "operation")
        receipt = state.completed_mutation_receipt(ticket)
        self.assertEqual(receipt.created[0].object_name, sheet.Name)
        self.assertEqual(receipt.revision_after, state.current_revision(doc.Uid))
        self.fixture.fixture.fixture.wait_for(lambda: sheet.ViewObject.Proxy.ready)
        Operations.switch(sheet, "flat")
        before = state.current_revision(doc.Uid), doc.UndoCount
        Operations.switch(sheet, "folded")
        self.assertEqual(before, (state.current_revision(doc.Uid), doc.UndoCount))
        name = sheet.Name
        self.context.undo_ledger.undo_latest(ticket=state.begin_call(doc.Uid, "native.undo"),
            document=doc, state=state, reauthorize_turn=self.context.guard,
            active_document=self.context.active_document)
        self.model.recompute()
        self.assertIsNone(doc.getObject(name))
        self.assertTrue(self.source.Visibility)

    def test_creation_does_not_claim_a_concurrent_source_label_edit(self):
        import SheetMetalNativeEdit
        ticket, future = self.start()
        self.source.Label = "Changed while the sheet unfolded"
        with self.assertRaises(SheetMetalNativeEdit.NativeSheetEditError) as caught:
            self.wait(future)
        self.assertTrue(caught.exception.failure()["parameters_committed"])
        self.assertIsNotNone(self.model.doc.getObject(self.model.sheet.Name))
        self.assertIsNone(self.context.state.completed_mutation_receipt(ticket))

    def test_registered_creation_binding_uses_the_exact_source_and_shared_state(self):
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        registry = build_native_capability_registry()
        runtime = build_native_runtime_bindings(self.context, ("sheet_metal.create",))["sheet_metal.create"]
        ticket = self.context.state.begin_call(self.model.doc.Uid, "sheet_metal.create")
        self.addCleanup(lambda: self.context.state.cancel_mutation(ticket))
        future = registry.implementation("sheet_metal.create").async_handler(SimpleNamespace(
            runtime=runtime, ticket=ticket, arguments={"operation": "from_source",
                "object_name": self.source.Name, "reference_face": self.face}))
        self.assertFalse(future.done())
        self.model.sheet = next(obj for obj in self.model.doc.Objects
                                if "PreparedInputHash" in obj.PropertiesList)
        self.fixture.fixture.fixture.view = self.model.sheet.ViewObject.Proxy
        result = self.wait(future)
        self.assertEqual(result["object_name"], self.model.sheet.Name)
        self.assertEqual(self.model.sheet.SourceFace, (self.source, [self.face]))
        self.model.assert_valid_pair()
        self.assertTrue(result["assistant_undo_available"])

    def test_stale_source_revision_is_rejected_without_a_new_undo_entry(self):
        revision = Operations.capture_source_revision(self.source)
        self.source.Label = "Changed before creation"
        undo = self.model.doc.UndoCount
        with self.assertRaises(RuntimeError):
            self.start(revision=revision)
        self.assertEqual(self.model.doc.UndoCount, undo)

    def test_invalid_reference_face_aborts_creation_and_preserves_the_source(self):
        undo = self.model.doc.UndoCount
        objects = tuple(self.model.doc.Objects)
        with self.assertRaises(RuntimeError):
            self.start(face="Face99999")
        self.assertEqual(self.model.doc.UndoCount, undo)
        self.assertEqual(tuple(self.model.doc.Objects), objects)
        self.assertTrue(self.source.Visibility)
