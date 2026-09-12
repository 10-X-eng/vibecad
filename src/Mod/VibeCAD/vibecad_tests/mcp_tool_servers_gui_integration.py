# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run only in an isolated FreeCAD GUI process with its own preferences."""
import threading
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
