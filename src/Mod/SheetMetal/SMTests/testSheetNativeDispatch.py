# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real geometry through the native tool dispatcher and completion receipts.

The isolated provider snapshot contains the edit contract under test. Live
SheetMetal ribbon coverage is verified separately when its full inventory lands.
"""

from concurrent.futures import Future
import json
import unittest

from SMTests import testSheetNativeEdit


class TestSheetNativeDispatch(unittest.TestCase):
    def setUp(self):
        from VibeCADNativeCapabilityRegistry import NativeProviderSurface
        from VibeCADNativeDispatch import NativeTurnDispatcher
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        from VibeCADNativeSurface import NativeSurfaceSnapshot
        from VibeCADNativeTurn import NativeTurnSnapshot
        self.fixture = testSheetNativeEdit.TestSheetNativeEdit()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.context, self.model, self.sheet = self.fixture.context, self.fixture.model, self.fixture.sheet
        registry = build_native_capability_registry()
        definition = registry.definition("sheet_metal.edit")
        schema = definition.provider_schema(tuple(variant.operation for variant in definition.variants))
        surface = NativeProviderSurface(snapshot=NativeSurfaceSnapshot(
            surface_id="sheet_metal", revision=1, manifest_sha256="a"*64,
            command_ids=("SheetMetal_EditCuts",), available_command_ids=("SheetMetal_EditCuts",),
            unavailable_command_ids=()), available=True, unavailable_reason="",
            tool_names=(definition.name,), schemas=(schema,), human_only_action_ids=(),
            missing_definition_names=(), missing_implementation_names=(), incomplete_definition_names=())
        self.debug = []
        self.dispatcher = NativeTurnDispatcher(document=self.model.doc, state=self.context.state,
            registry=registry, turn=NativeTurnSnapshot.from_provider_surface(surface),
            runtimes=build_native_runtime_bindings(self.context, (definition.name,)),
            reauthorize_turn=self.context.guard, active_document=self.context.active_document,
            debug_sink=self.debug.append)

    def call(self, arguments, call_id):
        return self.dispatcher.call_async("sheet_metal.edit", json.dumps(arguments), call_id,
                                          document_dispatch=lambda work: work())

    def arguments(self):
        return {"operation": "add_circle", "object_name": self.sheet.Name,
                "center": list(self.model.bend_pick()[2]), "radius": 4}

    def wait(self, result):
        if isinstance(result, Future):
            self.fixture.fixture.fixture.wait_for(result.done)
            return result.result()
        return result

    def test_native_dispatch_tracks_two_async_edits_and_exact_receipts(self):
        undo = self.model.doc.UndoCount
        arguments = self.arguments()
        pending = self.call(arguments, "first-cut")
        self.assertIsInstance(pending, Future)
        self.assertFalse(pending.done())
        duplicate = self.call(arguments, "first-cut")
        self.assertEqual(duplicate["error_code"], "NATIVE_CALL_IN_PROGRESS")
        first = self.wait(pending)
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["assistant_undo_available"])
        step = self.model.doc.getObject(first["object_name"])
        before = step.Shape.Volume
        second = self.wait(self.call({"operation": "update_circle", "object_name": step.Name,
            "operation_id": step.OperationId, "radius": 3}, "resize-cut"))
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["assistant_undo_available"])
        self.assertGreater(step.Shape.Volume, before)
        self.assertEqual(second["receipt"]["revision_before"], first["receipt"]["revision_after"])
        self.assertEqual(self.model.doc.UndoCount, undo+2)
        self.assertEqual(self.wait(self.call(arguments, "first-cut")), first)

    def test_failed_async_edit_does_not_absorb_external_revision_into_the_turn(self):
        pending = self.call(self.arguments(), "interrupted-cut")
        self.sheet.Label = "Independent user edit"
        failed = self.wait(pending)
        self.assertFalse(failed["ok"], failed)
        self.assertTrue(failed["parameters_committed"])
        self.assertIsNotNone(self.model.doc.getObject(failed["object_name"]))
        self.assertNotIn("receipt", failed)
        again = self.wait(self.call(self.arguments(), "unrefreshed-retry"))
        self.assertFalse(again["ok"], again)
        self.assertEqual(again["error_code"], "NATIVE_REVISION_CONFLICT")

    def test_invalid_provider_arguments_never_open_an_undo_transaction(self):
        undo = self.model.doc.UndoCount
        result = self.wait(self.call({**self.arguments(), "radius": -1}, "invalid-radius"))
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.model.doc.UndoCount, undo)

    def test_domain_preflight_failure_reports_unchanged_parameters_and_repair(self):
        undo = self.model.doc.UndoCount
        result = self.wait(self.call({"operation": "update_circle", "object_name": self.sheet.Name,
            "operation_id": "missing-cut", "radius": 3}, "missing-cut"))
        self.assertFalse(result["ok"], result)
        self.assertFalse(result["parameters_committed"])
        self.assertIn("no longer exists", result["error"])
        self.assertIn("sheet_metal.inspect", result["repair"])
        self.assertEqual(self.model.doc.UndoCount, undo)

    def test_live_sheet_surface_resolves_all_registered_operations(self):
        import FreeCADGui as Gui
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADRibbonSurface import read_active_ribbon_surface
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("SMWorkbench")
        self.fixture.fixture.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "sheet_metal")
        surface = resolve_native_provider_surface(read_active_ribbon_surface(), build_native_capability_registry())
        self.assertTrue(surface.available)
        self.assertEqual(surface.missing_definition_names, ())
        self.assertEqual(surface.missing_implementation_names, ())
        self.assertEqual(surface.incomplete_definition_names, ())
        self.assertEqual(surface.human_only_action_ids, ())
