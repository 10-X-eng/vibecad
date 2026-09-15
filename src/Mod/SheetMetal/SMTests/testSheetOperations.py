# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared, revision-bound sheet operations with native asynchronous completion."""

import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui

from SMTests import testPresentation


class TestSheetOperations(unittest.TestCase):
    def setUp(self):
        import SheetMetalOperations as Operations
        self.operations = Operations
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.sheet = self.model.sheet

    def request(self, **values):
        return self.operations.prepare(self.sheet, values,
                                       expected_revision=self.operations.capture_revision(self.sheet))

    def wait(self, run):
        self.fixture.wait_for(run.future.done)
        return run.future.result()

    def circle(self, radius=5):
        _, _, flat = self.model.bend_pick()
        return self.request(operation="add_circle", center=list(flat), radius=radius)

    def test_shared_edit_is_async_one_undo_and_has_a_current_result(self):
        before = self.sheet.Shape.Volume
        undo = self.model.doc.UndoCount
        run = self.operations.start(self.circle())
        self.assertFalse(run.future.done())
        result = self.wait(run)
        self.assertEqual(result["phase"], "ready")
        self.assertEqual(self.model.doc.UndoCount, undo+1)
        self.assertLess(self.sheet.Shape.Volume, before)
        self.model.assert_valid_pair()
        self.assertEqual(result["revision"], self.operations.capture_revision(self.sheet).summary())
        self.model.doc.undo()
        self.model.recompute()
        self.assertAlmostEqual(self.sheet.Shape.Volume, before, places=6)

    def test_aba_change_rejects_a_prepared_edit_before_mutating(self):
        prepared = self.circle()
        original = self.sheet.Material
        self.sheet.Material = "temporary"
        self.sheet.Material = original
        self.model.recompute()
        self.assertEqual(self.sheet.PreparedInputHash, prepared.revision.input_hash)
        undo = self.model.doc.UndoCount
        with self.assertRaises(RuntimeError):
            self.operations.start(prepared)
        self.assertEqual(self.model.doc.UndoCount, undo)

    def test_settings_and_upstream_dimensions_share_async_completion(self):
        run = self.operations.start(self.request(
            operation="set_material", material="Aluminum", k_factor=.36))
        self.assertEqual(self.wait(run)["phase"], "ready")
        run = self.operations.start(self.request(operation="set_parameters", changes={
            "thickness": 2.0, "bend_radius": 3.0, "flange_length": 42.0}))
        self.assertEqual(self.wait(run)["phase"], "ready")
        self.assertEqual(self.sheet.Material, "Aluminum")
        self.assertEqual(self.sheet.KFactor, .36)
        self.assertEqual(self.model.doc.BaseBend.Thickness.Value, 2.0)
        self.assertEqual(self.model.doc.BaseBend.Radius.Value, 3.0)
        self.assertEqual(self.model.doc.BaseBend.Length.Value, 42.0)
        self.model.assert_valid_pair()

    def test_failed_geometry_is_reported_and_operation_remains_removable(self):
        run = self.operations.start(self.circle(10000))
        result = self.wait(run)
        self.assertEqual(result["phase"], "failed")
        self.assertTrue(result["error"])
        self.assertIn("Invalid", self.sheet.State)
        removal = self.request(operation="remove_operation", operation_id=result["operation_id"])
        repaired = self.wait(self.operations.start(removal))
        self.assertEqual(repaired["phase"], "ready")
        self.model.assert_valid_pair()

    def test_invalid_allowance_and_source_dimensions_can_be_repaired(self):
        import SheetMetalEditable as Editable
        self.sheet.KFactor = 2
        self.model.recompute()
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(self.sheet)
        result = self.wait(self.operations.start(self.request(operation="set_material", k_factor=.42)))
        self.assertEqual(result["phase"], "ready")
        self.model.doc.BaseBend.Length = 0
        self.model.recompute()
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(self.sheet)
        result = self.wait(self.operations.start(self.request(
            operation="set_parameters", changes={"flange_length": 30})))
        self.assertEqual(result["phase"], "ready")
        self.model.assert_valid_pair()

    def test_invalid_parameters_fail_before_the_transaction(self):
        before = self.model.doc.UndoCount, self.sheet.Definition, self.sheet.Material
        for values in (
                {"operation": "add_circle", "center": [0, 0, 0], "radius": -1},
                {"operation": "set_material", "material": "x", "k_factor": 2},
                {"operation": "set_parameters", "changes": {"thickness": 0}},
                {"operation": "set_parameters", "changes": {"Visibility": False}}):
            with self.subTest(values=values), self.assertRaises((RuntimeError, ValueError)):
                self.request(**values)
        self.assertEqual(before, (self.model.doc.UndoCount, self.sheet.Definition, self.sheet.Material))

    def test_missing_profile_fails_cleanly_before_mutation(self):
        with self.assertRaises((RuntimeError, ValueError)):
            self.request(operation="add_profile", profile={
                "document_uid": str(self.model.doc.Uid), "object_name": "MissingProfile"})

    def test_legacy_circle_feature_and_non_numeric_source_metadata_remain_editable(self):
        # Old circle-only documents need not contain the later profile summary.
        self.sheet.removeProperty("ProfileSources")
        source = self.model.doc.BaseBend
        source.addProperty("App::PropertyString", "Angle", "Metadata")
        source.Angle = "not a bend parameter"
        self.model.recompute()
        result = self.wait(self.operations.start(self.circle()))
        self.assertEqual(result["phase"], "ready")
        self.model.assert_valid_pair()

    def test_arguments_are_frozen_and_foreign_revision_is_rejected(self):
        arguments = {"operation": "set_material", "material": "Steel"}
        revision = self.operations.capture_revision(self.sheet).summary()
        prepared = self.operations.prepare(self.sheet, arguments, expected_revision=revision)
        arguments["material"] = "Changed after preparation"
        self.assertEqual(self.wait(self.operations.start(prepared))["phase"], "ready")
        self.assertEqual(self.sheet.Material, "Steel")
        other = testPresentation.TestPresentation()
        self.addCleanup(other.doCleanups)
        other.setUp()
        with self.assertRaises(RuntimeError):
            self.operations.prepare(other.fixture.sheet, arguments,
                                    expected_revision=self.operations.capture_revision(self.sheet))

    def test_native_assistant_runner_uses_the_same_edit_and_records_its_target(self):
        from VibeCADCore import get_service
        from VibeCADNativeImmediate import run_immediate_mutation
        from VibeCADNativeRuntimeContext import NativeRuntimeContext
        from VibeCADNativeUndo import NativeAssistantUndoLedger
        prepared = self.circle()
        service = get_service()
        state = service.native_document_state_store()
        uid = str(self.model.doc.Uid)
        state.begin_native_authority(uid)
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("sheet-operation-test")
        context = NativeRuntimeContext(
            service=service, document=self.model.doc, state=state, undo_ledger=ledger,
            reauthorize_turn=lambda: None, active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "sheet_metal", edit_or_task_active=lambda: False)
        ticket = state.begin_call(uid, "sheet_metal.edit")
        def assistant_runner(**arguments):
            document = arguments.pop("document")
            self.assertIs(document, context.document)
            return run_immediate_mutation(context, ticket=ticket, **arguments)
        run = self.operations.start(prepared, transaction_runner=assistant_runner)
        result = self.wait(run)
        self.assertEqual(result["phase"], "ready")
        self.assertEqual(result["receipt"]["changed"], [{
            "document_uid": uid, "object_name": self.sheet.Name, "type_id": self.sheet.TypeId}])
        # The committed-parameter receipt is valid, but the native adapter
        # still has to finalize Undo eligibility after async geometry changes.
        self.assertNotIn("assistant_undo_available", result)

    def test_native_sketch_profile_uses_the_shared_operation_path(self):
        import Part
        import SheetMetalEditable as Editable
        _, _, flat = self.model.bend_pick()
        profile = self.model.edit(lambda: Editable.create_cut_sketch(self.sheet))
        center = Editable.profile_coordinates(self.sheet, profile, flat)
        self.model.edit(lambda: profile.addGeometry(
            Part.Circle(center, App.Vector(0, 0, 1), 5), False))
        run = self.operations.start(self.request(operation="add_profile", profile={
            "document_uid": str(self.model.doc.Uid), "object_name": profile.Name}))
        self.assertEqual(self.wait(run)["phase"], "ready")
        self.assertIn(profile, self.sheet.ProfileSources)
        self.model.assert_valid_pair()

    def test_reopen_requires_a_new_document_session_token(self):
        token = self.operations.capture_revision(self.sheet).summary()
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"operations.FCStd")
            self.model.doc.saveAs(filename)
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(filename)
            self.model.settle()
            self.sheet = self.model.sheet = self.model.doc.getObject("EditableSheet")
            self.fixture.view = self.sheet.ViewObject.Proxy
            current = self.operations.capture_revision(self.sheet).summary()
            self.assertNotEqual(token["document_session"], current["document_session"])
            with self.assertRaises(RuntimeError):
                self.operations.prepare(self.sheet, {"operation": "set_material", "material": "Steel"},
                                        expected_revision=token)

    def test_view_toggle_does_not_change_the_native_revision_or_undo(self):
        before = self.operations.capture_revision(self.sheet)
        undo = self.model.doc.UndoCount
        self.operations.switch(self.sheet, "flat")
        self.operations.switch(self.sheet, "folded")
        self.assertEqual(before, self.operations.capture_revision(self.sheet))
        self.assertEqual(undo, self.model.doc.UndoCount)

    def test_inspection_exposes_shared_geometry_without_document_changes(self):
        before = self.operations.capture_revision(self.sheet)
        undo = self.model.doc.UndoCount
        result = self.operations.inspect(self.sheet)
        self.assertTrue(result["prepared"])
        self.assertEqual(result["revision"], before.summary())
        self.assertTrue(any(region["kind"] == "bend" for region in result["regions"]))
        self.assertEqual(result["parameters"]["thickness"]["value"], 1.6)
        self.assertEqual(result["definition"]["operations"], [])
        self.assertEqual(before, self.operations.capture_revision(self.sheet))
        self.assertEqual(undo, self.model.doc.UndoCount)

    def test_geometry_worker_keeps_gui_available_and_detects_a_superseding_edit(self):
        import SheetMetalEditGeometry
        started, release, responsive = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = SheetMetalEditGeometry.SheetGeometry.prepare
        worker_threads = []
        def delayed(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            started.set()
            release.wait()
            return original(*args, **kwargs)
        with patch.object(SheetMetalEditGeometry.SheetGeometry, "prepare", side_effect=delayed):
            run = self.operations.start(self.circle())
            self.fixture.wait_for(started.is_set)
            Gui.deferToNextFrame(responsive.set)
            self.fixture.wait_for(responsive.is_set)
            self.sheet.Material = "Later user edit"
            release.set()
            self.assertEqual(self.wait(run)["phase"], "superseded")
        self.assertTrue(all(thread != threading.get_ident() for thread in worker_threads))
        self.assertEqual(self.sheet.Material, "Later user edit")
