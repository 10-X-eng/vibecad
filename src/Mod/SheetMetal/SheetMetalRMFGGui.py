# SPDX-License-Identifier: LGPL-2.1-or-later
"""Responsive application-level RMFG connection panel; no document mutation."""

import threading
import time
import uuid

from PySide import QtCore, QtGui, QtWidgets

from SheetMetalRMFGAuth import RMFGAuth, RMFGAuthError
from SheetMetalRMFGCredentialStore import CredentialStore


class ConnectionController(QtCore.QObject):
    changed = QtCore.Signal()
    _event = QtCore.Signal(object)

    def __init__(self, auth):
        super().__init__()
        self._auth = auth
        self._status = {"state": "unknown"}
        self._approval = None
        self._worker = None
        self._cancel = None
        self._job_id = None
        self._event.connect(self._receive, QtCore.Qt.QueuedConnection)

    @property
    def busy(self):
        return self._worker is not None

    def status(self):
        # Public status is suitable for native tools. Browser code/link stay
        # separately in the panel and never enter provider messages.
        result = dict(self._status)
        if "scopes" in result:
            result["scopes"] = list(result["scopes"])
        return result

    def approval(self):
        return dict(self._approval) if self._approval else None

    def _receive(self, event):
        if event["job_id"] != self._job_id:
            return
        if event["kind"] == "approval":
            # Closing while begin() was in flight must not open a browser
            # from a queued approval notification afterward.
            if self._cancel.is_set():
                return
            self._approval = event["approval"]
            self._status = {"state": "authorizing"}
        else:
            self._status = event["status"]
            self._approval = None
            self._worker = None
            self._cancel = None
        self.changed.emit()

    def _start(self, operation):
        if self.busy:
            return False
        job_id = self._job_id = uuid.uuid4().hex
        cancelled = self._cancel = threading.Event()
        self._approval = None
        self._status = {"state": {"login": "starting", "status": "checking",
                                  "disconnect": "disconnecting"}[operation]}
        auth = self._auth
        def publish(event):
            try:
                self._event.emit({"job_id": job_id, **event})
            except RuntimeError:
                # The application itself may have closed; workers retain no
                # document or widget and never touch a destroyed GUI object.
                pass
        def run():
            try:
                if operation == "status":
                    status = auth.status()
                elif operation == "disconnect":
                    auth.disconnect()
                    status = {"state": "disconnected"}
                else:
                    attempt = auth.begin()
                    publish({"kind": "approval", "approval": {
                        "user_code": attempt.user_code,
                        "verification_uri_complete": attempt.verification_uri_complete}})
                    while True:
                        if cancelled.is_set():
                            auth.cancel(attempt)
                            status = {"state": "disconnected"}
                            break
                        # This is OAuth's server-required polling interval and
                        # code lifetime, not an application/process deadline.
                        delay = max(0, min(attempt.next_poll, attempt.expires_at) - time.monotonic())
                        if cancelled.wait(delay):
                            continue
                        if auth.poll(attempt) == "connected":
                            status = auth.status()
                            break
            except RMFGAuthError as error:
                status = {"state": "error", "code": error.code, "message": str(error)}
            except Exception:
                status = {"state": "error", "code": "connection_failed",
                          "message": "The RMFG connection could not be completed. Try connecting again."}
            publish({"kind": "finished", "status": status})
        # Detached network/credential work must not keep the CAD process alive
        # after the user exits it. Persisted OAuth consumption markers protect
        # the next launch if exit interrupts a token exchange.
        self._worker = threading.Thread(target=run, name="RMFG-connection", daemon=True)
        self.changed.emit()
        self._worker.start()
        return True

    def refresh(self):
        return self._start("status")

    def begin_login(self):
        return self._start("login")

    def disconnect_account(self):
        return self._start("disconnect")

    def cancel_login(self):
        if self._cancel is not None:
            self._cancel.set()
            if self._status["state"] in ("starting", "authorizing"):
                self._approval = None
                self._status = {"state": "cancelling"}
                self.changed.emit()


class ConnectionPanel(QtWidgets.QDialog):
    def __init__(self, controller, *, parent=None, open_browser=None):
        super().__init__(parent)
        self.setWindowTitle("RMFG connection")
        self.setModal(False)
        self.controller = controller
        self._open_browser = open_browser or (lambda url: QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)))
        self._opened_code = None
        self._closed = False
        layout = QtWidgets.QVBoxLayout(self)
        description = QtWidgets.QLabel(
            "Connect your RMFG account to check manufacturability, get quotes, and open website checkout.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.message = QtWidgets.QLabel()
        self.message.setTextFormat(QtCore.Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.code = QtWidgets.QLabel()
        self.code.setTextFormat(QtCore.Qt.PlainText)
        self.code.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.code)
        self.browser_button = QtWidgets.QPushButton("Open approval page")
        self.browser_button.clicked.connect(self.open_approval)
        layout.addWidget(self.browser_button)
        row = QtWidgets.QHBoxLayout()
        self.connect_button = QtWidgets.QPushButton("Connect in browser")
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        self.cancel_button = QtWidgets.QPushButton("Cancel sign-in")
        self.connect_button.clicked.connect(controller.begin_login)
        self.disconnect_button.clicked.connect(controller.disconnect_account)
        self.cancel_button.clicked.connect(controller.cancel_login)
        for button in (self.connect_button, self.disconnect_button, self.cancel_button):
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        controller.changed.connect(self._refresh)
        self._refresh()
        controller.refresh()

    def _refresh(self):
        if self._closed:
            return
        status, approval = self.controller.status(), self.controller.approval()
        state = status["state"]
        messages = {"unknown": "Checking connection…", "checking": "Checking connection…",
            "starting": "Starting browser sign-in…", "authorizing": "Approve this connection in your browser.",
            "connected": "Connected to RMFG.", "disconnected": "Not connected to RMFG.",
            "disconnecting": "Disconnecting…", "cancelling": "Cancelling sign-in…",
            "reconnect": "Reconnect in the browser to continue."}
        self.message.setText(status.get("message", messages.get(state, "Connect to RMFG to continue.")))
        self.connect_button.setEnabled(not self.controller.busy and state != "connected")
        self.disconnect_button.setEnabled(not self.controller.busy and state == "connected")
        self.cancel_button.setEnabled(self.controller.busy and state in ("starting", "authorizing"))
        self.browser_button.setVisible(bool(approval))
        self.code.setText("Approval code: " + approval["user_code"] if approval else "")
        if approval and self._opened_code != approval["user_code"]:
            self._opened_code = approval["user_code"]
            self.open_approval()
        elif not approval:
            self._opened_code = None

    def open_approval(self):
        if self._closed:
            return
        approval = self.controller.approval()
        if approval and not self._open_browser(approval["verification_uri_complete"]):
            self.message.setText("The browser could not be opened. Try Open approval page again.")

    def closeEvent(self, event):
        self._closed = True
        self.controller.cancel_login()
        super().closeEvent(event)

    def reject(self):
        self._closed = True
        self.controller.cancel_login()
        super().reject()


_controller = None
_panel = None


def connection_controller():
    global _controller
    if _controller is None:
        import FreeCAD as App
        _controller = ConnectionController(RMFGAuth(CredentialStore(App.ConfigGet("UserAppData"))))
    return _controller


def show_connection():
    global _panel
    import FreeCADGui as Gui
    if _panel is None or _panel._closed:
        _panel = ConnectionPanel(connection_controller(), parent=Gui.getMainWindow())
    _panel.show()
    _panel.raise_()
    _panel.activateWindow()
    return _panel


class _ConnectionCommand:
    def GetResources(self):
        from SheetMetalTools import icons_path
        return {"MenuText": "RMFG connection", "ToolTip": "Connect your RMFG account for manufacturing quotes",
                "Pixmap": str(icons_path) + "/SMLogo.svg"}

    def IsActive(self):
        return True

    def Activated(self):
        show_connection()


def ensure_commands_registered():
    import FreeCADGui as Gui
    if Gui.Command.get("SheetMetal_RMFGConnection") is None:
        Gui.addCommand("SheetMetal_RMFGConnection", _ConnectionCommand())
