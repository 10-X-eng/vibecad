# SPDX-License-Identifier: LGPL-2.1-or-later
"""Private Qt interaction tests; no RMFG requests, browser or user credentials."""

import threading
import time
import unittest

from PySide import QtCore, QtWidgets


class FakeAuth:
    def __init__(self):
        self.threads = []
        self.approved = threading.Event()
        self.entered = threading.Event()
        self.begin_release = threading.Event()
        self.begin_release.set()
        self.connected = False
        self.cancelled = 0
        self.begin_count = 0

    def status(self):
        self.threads.append(threading.get_ident())
        return {"state": "connected" if self.connected else "disconnected"}

    def begin(self):
        from SheetMetalRMFGAuth import DeviceAuthorization
        self.threads.append(threading.get_ident())
        self.begin_count += 1
        self.entered.set()
        self.begin_release.wait()
        now = time.monotonic()
        return DeviceAuthorization("test-attempt", "private-device", "ABCD-EFGH",
            "https://www.rmfg.com/activate", "https://www.rmfg.com/activate?user_code=ABCD-EFGH",
            now+600, 5, now)

    def poll(self, attempt):
        self.threads.append(threading.get_ident())
        if self.approved.is_set():
            self.connected = True
            return "connected"
        attempt.next_poll = time.monotonic()+.01
        return "pending"

    def cancel(self, attempt):
        self.threads.append(threading.get_ident())
        self.cancelled += 1

    def disconnect(self):
        self.threads.append(threading.get_ident())
        self.connected = False


class TestRMFGGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from SheetMetalRMFGGui import ConnectionController, ConnectionPanel
        self.auth = FakeAuth()
        self.controller = ConnectionController(self.auth)
        self.opened = []
        self.panel = ConnectionPanel(self.controller,
            open_browser=lambda url: self.opened.append((str(url), threading.get_ident())) or True)
        self.panel.show()
        self.addCleanup(self.cleanup)
        self.wait(lambda: not self.controller.busy)

    def wait(self, condition):
        # Test assertion deadline only; never terminates an application/process.
        deadline = time.monotonic()+5
        while not condition():
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 5)
            if time.monotonic() > deadline:
                self.fail("The fake RMFG operation did not deliver its GUI result")
            time.sleep(.001)
        QtWidgets.QApplication.processEvents()

    def cleanup(self):
        self.auth.begin_release.set()
        self.controller.cancel_login()
        self.wait(lambda: not self.controller.busy)
        self.panel.close()

    def test_browser_approval_is_displayed_on_gui_while_auth_runs_off_gui(self):
        self.panel.connect_button.click()
        self.wait(lambda: bool(self.opened))
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.opened[0][1], threading.get_ident())
        self.assertIn("ABCD-EFGH", self.panel.code.text())
        self.assertTrue(self.panel.cancel_button.isEnabled())
        self.assertNotIn("private-device", str(self.controller.status()))
        self.auth.approved.set()
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.controller.status()["state"], "connected")
        self.assertFalse(self.panel.connect_button.isEnabled())
        self.assertTrue(self.panel.disconnect_button.isEnabled())
        self.assertEqual(self.panel.code.text(), "")
        self.assertTrue(all(value != threading.get_ident() for value in self.auth.threads))

    def test_slow_begin_does_not_block_controls_or_start_a_second_login(self):
        self.auth.begin_release.clear()
        self.panel.connect_button.click()
        self.assertTrue(self.auth.entered.wait(1))
        self.assertFalse(self.controller.begin_login())
        self.panel.setWindowTitle("Still responsive")
        QtWidgets.QApplication.processEvents()
        self.assertEqual(self.panel.windowTitle(), "Still responsive")
        self.assertEqual(self.auth.begin_count, 1)
        self.auth.begin_release.set()
        self.wait(lambda: bool(self.opened))
        summary = self.controller.status()
        summary["state"] = "modified outside controller"
        self.assertNotEqual(self.controller.status()["state"], summary["state"])

    def test_closing_pending_panel_cancels_login_and_keeps_other_windows_open(self):
        other = QtWidgets.QWidget()
        other.show()
        self.addCleanup(other.close)
        self.auth.begin_release.clear()
        self.panel.connect_button.click()
        self.assertTrue(self.auth.entered.wait(1))
        self.panel.close()
        self.auth.begin_release.set()
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.auth.cancelled, 1)
        self.assertEqual(self.opened, [])
        self.assertTrue(other.isVisible())

    def test_disconnect_and_unexpected_errors_do_not_expose_credentials(self):
        self.auth.connected = True
        self.controller.refresh()
        self.wait(lambda: not self.controller.busy)
        self.panel.disconnect_button.click()
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.controller.status()["state"], "disconnected")
        def failed():
            raise RuntimeError("private-access-token")
        self.auth.begin = failed
        self.panel.connect_button.click()
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.controller.status()["state"], "error")
        self.assertNotIn("private-access-token", self.panel.message.text())

    def test_cancel_immediately_hides_the_approval_link(self):
        self.panel.connect_button.click()
        self.wait(lambda: bool(self.opened))
        self.panel.cancel_button.click()
        self.assertEqual(self.controller.status()["state"], "cancelling")
        self.assertEqual(self.panel.code.text(), "")
        self.assertFalse(self.panel.browser_button.isVisible())

    def test_escape_rejection_cancels_an_inflight_browser_request(self):
        self.auth.begin_release.clear()
        self.panel.connect_button.click()
        self.assertTrue(self.auth.entered.wait(1))
        self.panel.reject()
        self.assertTrue(self.panel._closed)
        self.auth.begin_release.set()
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.auth.cancelled, 1)
        self.assertEqual(self.opened, [])
