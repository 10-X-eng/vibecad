# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run only in an isolated FreeCAD GUI process with its own preferences."""
import threading
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from PySide import QtCore, QtWidgets

import VibeCADMCPToolServers as servers
from VibeCADPreferences import VibeCADMCPPreferencesPage


class Heartbeat(QtCore.QObject):
    requested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.seen = False
        self.requested.connect(self.receive, QtCore.Qt.QueuedConnection)

    def receive(self):
        self.seen = True


class TestMCPGuiCleanup(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("VIBECAD_TEST_MCP_PYTHON"), "Set the MCP fixture Python executable")
    def test_preferences_register_test_reload_and_call_real_server(self):
        page = VibeCADMCPPreferencesPage()
        heartbeat = QtCore.QTimer()
        ticks = []
        heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
        heartbeat.start(20)

        def wait_until(predicate):
            deadline = time.monotonic() + 45
            while not predicate() and time.monotonic() < deadline:
                QtWidgets.QApplication.processEvents()
                time.sleep(0.01)
            self.assertTrue(predicate(), "MCP GUI operation did not finish")

        try:
            page.add_tool_server.click()
            page.tool_server_name.setText("gui-fixture")
            page.tool_server_command.setText(os.environ["VIBECAD_TEST_MCP_PYTHON"])
            page.tool_server_args.setText(servers.join_command_arguments([
                str(Path(__file__).with_name("fake_mcp_tool_server.py"))]))
            page.tool_server_env.setPlainText("FAKE_MCP_LIST_DELAY=0.3")
            page.test_tool_server.click()
            self.assertFalse(page.test_tool_server.isEnabled())
            wait_until(lambda: page._tool_server_test_thread is None)
            self.assertIn("connected, 5 tools", page.tool_server_status.text())
            self.assertGreater(len(ticks), 2, "Connection test prevented GUI heartbeats")
            page._save_tool_servers()
            page.loadSettings()
            self.assertEqual(page.tool_server_list.topLevelItem(0).text(0), "gui-fixture")

            results = []
            def call_from_agent_thread():
                try:
                    context = {}
                    servers.attach_external_tool_schemas(context)
                    runner = servers.wrap_tool_runner_with_external_tools(
                        lambda *args: self.fail("External tool was routed to CAD"),
                        context, tool_trace=[])
                    results.append(runner("mcp_gui_fixture.echo", json.dumps({"text": "GUI registration works"}), "gui-test"))
                except Exception as exc:
                    results.append(exc)

            worker = threading.Thread(target=call_from_agent_thread, daemon=True)
            worker.start()
            wait_until(lambda: not worker.is_alive())
            self.assertIsInstance(results[0], dict)
            self.assertTrue(results[0]["ok"], results[0])
            self.assertEqual(results[0]["structured_content"]["echoed"], "GUI registration works")
            page.tool_server_list.setCurrentItem(page.tool_server_list.topLevelItem(0))
            page.remove_tool_server.click()
            page._save_tool_servers()
            self.assertEqual(servers.load_mcp_tool_servers(), [])
        finally:
            heartbeat.stop()
            page.form.close()
            page.form.deleteLater()
            servers.shutdown_mcp_tool_servers()

    def check_cleanup(self, action):
        manager = servers.MCPToolServerManager()
        held, release, exited = threading.Event(), threading.Event(), threading.Event()

        def hold_network_lock():
            with manager._lock:
                held.set()
                # Bound only the synthetic blocked operation, never the GUI
                # process lifetime. A blocking regression fails without hanging.
                release.wait(2)
            exited.set()

        worker = threading.Thread(target=hold_network_lock)
        heartbeat = Heartbeat()
        worker.start()
        try:
            self.assertTrue(held.wait(2))
            heartbeat.requested.emit()
            completion = action(manager)
            QtWidgets.QApplication.processEvents()
            self.assertTrue(heartbeat.seen)
            self.assertFalse(exited.is_set(), "GUI call waited for network cleanup")
            self.assertFalse(completion.done())
        finally:
            release.set()
            worker.join(2)
            manager.shutdown()
        completion.result(2)

    def test_preferences_removal_keeps_gui_events_running(self):
        server = servers.MCPToolServer(name="fixture", command="unused")
        servers.save_mcp_tool_servers([server])
        page = VibeCADMCPPreferencesPage()
        page._tool_server_drafts = []

        def remove(manager):
            completions = []
            original = manager.close_server_async

            def record(name):
                completion = original(name)
                completions.append(completion)
                return completion

            with patch.object(servers, "get_mcp_tool_server_manager", return_value=manager), \
                    patch.object(manager, "close_server_async", side_effect=record):
                page._save_tool_servers()
            self.assertEqual(len(completions), 1)
            return completions[0]

        try:
            self.check_cleanup(remove)
            self.assertEqual(servers.load_mcp_tool_servers(), [])
        finally:
            page.form.close()
            page.form.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_shutdown_keeps_gui_events_running(self):
        def shutdown(manager):
            with patch.object(servers, "_manager", manager):
                return servers.shutdown_mcp_tool_servers_async()

        self.check_cleanup(shutdown)
