# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real ribbon/native RMFG access with private CAD and a fake remote service."""

from concurrent.futures import Future
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui

from SMTests import testRMFGManufacturingGui


class TestRMFGNativeManufacturing(unittest.TestCase):
    def setUp(self):
        import SheetMetalRMFGManufacturingGui as Manufacturing
        from VibeCADCore import get_service
        from VibeCADNativeRuntimeContext import NativeRuntimeContext
        from VibeCADNativeUndo import NativeAssistantUndoLedger
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        self.fixture = testRMFGManufacturingGui.TestRMFGManufacturingGui()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.controller, self.panel = self.fixture.controller, self.fixture.panel
        self.sheet, self.doc = self.fixture.model.sheet, self.fixture.model.doc
        self.domain, self.client = self.fixture.domain, self.fixture.client
        self.gui = Manufacturing
        key = id(self.doc), self.sheet.Name
        self.enterContext(patch.object(Manufacturing, "_controllers", {key: self.controller}))
        self.enterContext(patch.object(Manufacturing, "_panels", {key: self.panel}))
        service = get_service()
        self.context = NativeRuntimeContext(service=service, document=self.doc,
            state=service.native_document_state_store(), undo_ledger=NativeAssistantUndoLedger(),
            reauthorize_turn=lambda: None, active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "sheet_metal", edit_or_task_active=lambda: False)
        self.previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(self.previous))
        self.addCleanup(Gui.Selection.clearSelection)
        Gui.activateWorkbench("SMWorkbench")
        self.runtime = build_native_runtime_bindings(self.context, ("sheet_metal.manufacturing",))["sheet_metal.manufacturing"]

    def call(self, operation, **values):
        from VibeCADNativeRegistry import build_native_capability_registry
        implementation = build_native_capability_registry().implementation("sheet_metal.manufacturing")
        return implementation.async_handler(SimpleNamespace(runtime=self.runtime, arguments={
            "operation": operation, "object_name": self.sheet.Name, **values}))

    def finish(self, result):
        if isinstance(result, Future):
            self.fixture.fixture.wait_for(result.done)
            return result.result()
        return result

    def test_ribbon_and_native_share_one_panel_and_live_surface_discovery(self):
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADRibbonSurface import read_active_ribbon_surface
        self.fixture.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "sheet_metal")
        surface = resolve_native_provider_surface(read_active_ribbon_surface(), build_native_capability_registry())
        self.assertTrue(surface.available, surface.summary())
        self.assertIn("sheet_metal.manufacturing", surface.tool_names)
        before = self.doc.UndoCount, tuple(self.doc.Objects), self.doc.isTouched()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sheet)
        Gui.runCommand("SheetMetal_RMFGManufacture")
        result = self.call("show_panel")
        self.assertTrue(result["panel_open"])
        self.assertIs(self.gui.manufacturing_controller(self.sheet), self.controller)
        self.assertIs(self.gui.show_manufacturing(self.sheet), self.panel)
        Gui.updateGui()
        Gui.getMainWindow().grab().save(str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"rmfg-ribbon.png"))
        self.assertEqual(before, (self.doc.UndoCount, tuple(self.doc.Objects), self.doc.isTouched()))

    def test_native_analysis_configuration_and_quote_use_the_panel_state_without_mcp(self):
        self.fixture.fixture.view.switch("flat")
        before = self.doc.UndoCount, self.sheet.PreparedInputHash
        self.finish(self.call("analyze"))
        self.call("set_material", part_id="part_1", material_id="steel_1")
        self.call("set_quantity", quantity=10)
        result = self.finish(self.call("quote"))
        self.assertEqual(result["quote"]["status"], "ready")
        self.assertTrue(self.panel.checkout_button.isEnabled())
        self.assertEqual(self.panel.quantity.value(), 10)
        self.assertEqual(self.fixture.fixture.view.mode, "flat")
        with patch.object(self.gui, "open_checkout", return_value=True) as browser:
            result = self.finish(self.call("checkout"))
        browser.assert_called_once_with(self.controller)
        self.assertTrue(result["browser_opened"])
        self.assertNotIn("private-link", str(result))
        self.assertEqual(before, (self.doc.UndoCount, self.sheet.PreparedInputHash))

    def test_native_completion_rejects_document_edits_while_worker_is_running(self):
        self.fixture.analyze()
        entered, release = threading.Event(), threading.Event()
        def quote(*args, **kwargs):
            entered.set()
            release.wait()
            return self.domain.quote()
        self.client.create_quote.side_effect = quote
        future = self.call("quote")
        try:
            self.fixture.fixture.wait_for(entered.is_set)
            self.sheet.Label = "Changed during quote"
        finally:
            release.set()
        self.fixture.fixture.wait_for(future.done)
        with self.assertRaises(RuntimeError):
            future.result()
        self.assertFalse(self.controller.status()["can_checkout"])

    def test_other_document_cannot_redirect_the_target_or_start_remote_work(self):
        other = App.newDocument("OtherManufacturingDocument")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        with self.assertRaisesRegex(RuntimeError, "active"):
            self.call("analyze")
        self.client.analyze.assert_not_called()

    def test_cancelled_native_checkout_cannot_open_a_browser_after_response(self):
        self.fixture.analyze()
        self.finish(self.call("quote"))
        entered, release = threading.Event(), threading.Event()
        def cart(*args, **kwargs):
            entered.set()
            release.wait()
            return {"id": "cart_1", "status": "open", "cart_url": "https://www.rmfg.com/cart/private-link"}
        self.client.create_checkout.side_effect = cart
        with patch.object(self.gui, "open_checkout") as browser:
            future = self.call("checkout")
            try:
                self.fixture.fixture.wait_for(entered.is_set)
                self.assertTrue(future.cancel())
            finally:
                release.set()
            self.fixture.wait()
            browser.assert_not_called()
        self.assertTrue(future.cancelled())

    def test_factory_reuses_the_current_sheet_and_replaces_a_closed_controller(self):
        self.panel.close()
        with patch.object(self.gui, "Backend", return_value=self.domain.backend):
            controller = self.gui.manufacturing_controller(self.sheet)
            self.addCleanup(controller.close)
            self.assertIsNot(controller, self.controller)
            self.assertIs(self.gui.manufacturing_controller(self.sheet), controller)
            panel = self.gui.show_manufacturing(self.sheet)
            self.addCleanup(panel.close)
            self.assertIs(panel.controller, controller)
            self.fixture.fixture.wait_for(lambda: not controller.busy)


    def dispatcher(self):
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeDispatch import NativeTurnDispatcher
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        from VibeCADNativeTurn import NativeTurnSnapshot
        from VibeCADRibbonSurface import read_active_ribbon_surface
        self.fixture.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "sheet_metal")
        registry = build_native_capability_registry()
        surface = resolve_native_provider_surface(read_active_ribbon_surface(), registry)
        self.assertTrue(surface.available, surface.summary())
        return NativeTurnDispatcher(document=self.doc, state=self.context.state, registry=registry,
            turn=NativeTurnSnapshot.from_provider_surface(surface),
            runtimes=build_native_runtime_bindings(self.context, surface.tool_names),
            reauthorize_turn=self.context.guard, active_document=self.context.active_document)

    def dispatched(self, dispatcher, operation, call_id, **values):
        return dispatcher.call_async("sheet_metal.manufacturing", json.dumps({
            "operation": operation, "object_name": self.sheet.Name, **values}), call_id,
            document_dispatch=lambda work: work())

    def test_real_dispatcher_reports_completion_and_replays_without_reuploading(self):
        dispatcher = self.dispatcher()
        before = self.doc.UndoCount, self.context.state.current_revision(self.doc.Uid)
        pending = self.dispatched(dispatcher, "analyze", "analysis")
        self.assertIsInstance(pending, Future)
        self.assertFalse(pending.done())
        self.assertEqual(self.dispatched(dispatcher, "analyze", "analysis")["error_code"], "NATIVE_CALL_IN_PROGRESS")
        result = self.finish(pending)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.dispatched(dispatcher, "analyze", "analysis"), result)
        self.assertEqual(self.client.analyze.call_count, 1)
        self.assertTrue(self.finish(self.dispatched(dispatcher, "set_material", "material",
            part_id="part_1", material_id="steel_1"))["ok"])
        self.assertTrue(self.finish(self.dispatched(dispatcher, "set_quantity", "quantity", quantity=10))["ok"])
        quote = self.finish(self.dispatched(dispatcher, "quote", "quote"))
        self.assertTrue(quote["ok"], quote)
        self.assertTrue(quote["can_checkout"])
        self.assertEqual(before, (self.doc.UndoCount, self.context.state.current_revision(self.doc.Uid)))

    def test_dispatcher_rejects_external_edit_and_requires_a_fresh_turn(self):
        self.fixture.analyze()
        dispatcher = self.dispatcher()
        entered, release = threading.Event(), threading.Event()
        def quote(*args, **kwargs):
            entered.set()
            release.wait()
            return self.domain.quote()
        self.client.create_quote.side_effect = quote
        pending = self.dispatched(dispatcher, "quote", "stale-quote")
        try:
            self.fixture.fixture.wait_for(entered.is_set)
            self.sheet.Label = "Independent edit"
        finally:
            release.set()
        result = self.finish(pending)
        self.assertFalse(result["ok"], result)
        again = self.finish(self.dispatched(dispatcher, "status", "unrefreshed-status"))
        self.assertEqual(again["error_code"], "NATIVE_REVISION_CONFLICT")


    def test_native_saved_job_flow_restores_the_same_quote_after_panel_close(self):
        self.fixture.analyze()
        quoted = self.finish(self.call("quote"))
        key = quoted["quote_job"]
        self.panel.close()
        self.client.reset_mock()
        self.client.design.return_value = self.domain.design
        with patch.object(self.gui, "Backend", return_value=self.domain.backend):
            self.finish(self.call("load_materials", more=False))
            listed = self.finish(self.call("list_jobs", offset=0, limit=20))
            self.assertIn(key, {job["operation_key"] for job in listed["jobs"]})
            inspected = self.finish(self.call("inspect_job", job_key=key))
            self.assertFalse(inspected["saved_job"]["stale"])
            restored = self.finish(self.call("resume_job", job_key=key))
            self.assertEqual(restored["quote_job"], key)
            self.assertEqual(restored["quantity"], 10)
            self.assertTrue(restored["can_checkout"])
            self.addCleanup(self.gui.manufacturing_controller(self.sheet).close)
        self.client.analyze.assert_not_called()
        self.client.create_quote.assert_not_called()
