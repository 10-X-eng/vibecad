# SPDX-License-Identifier: LGPL-2.1-or-later
"""Built ribbon/native connection access with fake auth and real document guards."""

import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui

from SMTests import testRMFGGui, testSheetNativeInspect


class TestRMFGNativeConnection(unittest.TestCase):
    def setUp(self):
        import SheetMetalRMFGGui as Connection
        self.inspection = testSheetNativeInspect.TestSheetNativeInspect()
        self.addCleanup(self.inspection.doCleanups)
        self.inspection.setUp()
        self.context, self.doc = self.inspection.context, self.inspection.model.doc
        self.auth = testRMFGGui.FakeAuth()
        self.controller = Connection.ConnectionController(self.auth)
        self.enterContext(patch.object(Connection, "_controller", self.controller))
        self.enterContext(patch.object(Connection, "_panel", None))
        self.addCleanup(self.close_panel)
        self.previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(self.previous))
        Gui.activateWorkbench("SMWorkbench")
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        self.runtime = build_native_runtime_bindings(self.context, ("sheet_metal.connection",))["sheet_metal.connection"]

    def close_panel(self):
        import SheetMetalRMFGGui as Connection
        if Connection._panel is not None:
            Connection._panel.close()
        self.controller.cancel_login()
        self.inspection.fixture.wait_for(lambda: not self.controller.busy)

    def call(self, operation):
        from VibeCADNativeRegistry import build_native_capability_registry
        implementation = build_native_capability_registry().implementation("sheet_metal.connection")
        return implementation.async_handler(SimpleNamespace(runtime=self.runtime, arguments={"operation": operation}))

    def test_ribbon_and_native_open_the_same_modeless_panel_without_document_changes(self):
        import SheetMetalRMFGGui as Connection
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADRibbonSurface import read_active_ribbon_surface
        self.inspection.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "sheet_metal")
        surface = resolve_native_provider_surface(read_active_ribbon_surface(), build_native_capability_registry())
        self.assertTrue(surface.available, surface.summary())
        self.assertIn("sheet_metal.connection", surface.tool_names)
        before = (self.doc.UndoCount, tuple(self.doc.Objects), self.doc.isTouched())
        Gui.runCommand("SheetMetal_RMFGConnection")
        panel = Connection._panel
        self.assertTrue(panel.isVisible())
        self.assertFalse(panel.isModal())
        result = self.call("show_connection")
        self.assertIs(Connection._panel, panel)
        self.assertTrue(result["requires_user_action"])
        self.inspection.fixture.wait_for(lambda: not self.controller.busy)
        self.assertTrue(panel.grab().save(str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"rmfg-connection.png")))
        self.assertEqual(before, (self.doc.UndoCount, tuple(self.doc.Objects), self.doc.isTouched()))
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_native_status_waits_off_gui_and_returns_only_public_connection_state(self):
        self.auth.connected = True
        future = self.call("status")
        self.inspection.fixture.wait_for(future.done)
        self.assertEqual(future.result(), {"state": "connected"})
        synchronous = self.runtime.execute({"operation": "status"})
        self.assertEqual(synchronous["state"], "connected")
        self.assertFalse(synchronous["pending"])
        future = self.call("status")
        self.assertTrue(future.cancel())
        self.inspection.fixture.wait_for(lambda: not self.controller.busy)
        self.assertTrue(future.cancelled())

    def test_native_connection_refuses_a_replaced_document_before_opening_a_panel(self):
        import SheetMetalRMFGGui as Connection
        other = App.newDocument("DifferentRMFGOwner")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        with self.assertRaisesRegex(RuntimeError, "active"):
            self.call("show_connection")
        self.assertIsNone(Connection._panel)
