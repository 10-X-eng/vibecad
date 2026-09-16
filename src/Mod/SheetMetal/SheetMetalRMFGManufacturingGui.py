# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI-owned manufacturing state, with detached export, storage and HTTP workers."""

from concurrent.futures import Future
import copy
from functools import partial
import json
from pathlib import Path
import threading
import uuid
import weakref

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

import SheetMetalEditable as Editable
import SheetMetalOperations as Operations
from SheetMetalPresentation import _gui_thread
# Retain the legacy imported entry point for existing callers.
from SheetMetalRMFGExport import start_export, start_prepared_export
from SheetMetalRMFGManufacturing import Backend, configuration, findings, ready_quote
from SheetMetalRMFGSnapshot import QuoteRequest


class _Signals(QtCore.QObject):
    ready = QtCore.Signal(object)
    invalidated = QtCore.Signal()


class _Observer:
    def __init__(self, controller):
        self.owner = weakref.ref(controller)

    def notify(self, *args):
        owner = self.owner()
        if owner is not None and not owner._closed and not owner._notification_pending:
            owner._notification_pending = True
            owner._signals.invalidated.emit()

    slotChangedObject = notify
    slotDeletedObject = notify
    slotRecomputedDocument = notify
    slotUndoDocument = notify
    slotRedoDocument = notify
    slotDeletedDocument = notify


class Controller(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, sheet, backend):
        super().__init__()
        _gui_thread()
        Editable.state_owner(sheet)
        self.sheet, self.backend = sheet, backend
        self.busy, self._closed, self._notification_pending = False, False, False
        self.materials, self.design, self.selections, self.quantity = [], None, {}, 1
        self._cursor = None
        self._jobs, self._jobs_next_offset, self._saved_job = [], None, None
        self._export = self._quote = self._quote_observed_key = None
        self._upload_key = self._quote_key = self._cart_key = None
        self._quote_context = self._quote_request_context = self._cart_context = None
        self._checkout_link = None
        self.message, self._last_error = "Load materials and analyze the folded sheet to begin.", False
        self.future = None
        self._signals = _Signals()
        self._signals.ready.connect(self._receive, QtCore.Qt.QueuedConnection)
        self._signals.invalidated.connect(self._invalidate, QtCore.Qt.QueuedConnection)
        self._observer = _Observer(self)
        App.addDocumentObserver(self._observer)

    def _invalidate(self):
        self._notification_pending = False
        if not self._closed:
            try:
                Editable.state_owner(self.sheet)
            except (RuntimeError, ReferenceError, AttributeError, NameError):
                self.close()
            else:
                self.changed.emit()

    def stale(self):
        if self._export is None:
            return False
        try:
            Editable.get_state_geometry(self.sheet)
            return not self._export.matches_revision(Operations.capture_revision(self.sheet).summary())
        except Exception:
            return True

    def _configuration(self):
        return configuration(self.design or {}, self.materials, self.selections)

    def _token(self):
        if self._export is None:
            return None
        try:
            return QuoteRequest(self._export, self.design["id"], self._configuration(), self.quantity).fingerprint
        except (ValueError, TypeError, KeyError):
            return None

    def status(self):
        _gui_thread()
        stale, token = self.stale(), self._token()
        quote_current = (self._quote is not None and token is not None and token == self._quote_context
                         and self._quote_observed_key == self._quote_key and not stale)
        problem = None
        if quote_current:
            try:
                ready_quote(self._quote, QuoteRequest(self._export, self.design["id"],
                                                      self._configuration(), self.quantity).items())
            except ValueError as error:
                problem = str(error)
        quote = None if self._quote is None else {name: self._quote.get(name) for name in (
            "id", "status", "currency", "amount_total_cents", "amount_subtotal_cents", "expires_at")}
        if quote is not None:
            quote.update(stale=not quote_current, findings=findings(self._quote))
        design = None if self.design is None else {"id": self.design.get("id"), "status": self.design.get("status"),
            "parts": [{name: part.get(name) for name in ("id", "name", "detected_thickness_mm", "instance_count")}
                      for part in self.design.get("parts", [])]}
        revision = None
        if self._jobs or self._saved_job is not None:
            try:
                revision = Operations.capture_revision(self.sheet).summary()
            except (RuntimeError, ReferenceError, AttributeError, NameError):
                pass
        jobs = [{**job, "stale": job["revision"] != revision} for job in self._jobs]
        saved = None if self._saved_job is None else {**copy.deepcopy(self._saved_job),
                                                     "stale": self._saved_job["revision"] != revision}
        return {"busy": self.busy, "closed": self._closed, "stale": stale, "message": self.message, "design": design, "quote": quote,
                "materials": copy.deepcopy(self.materials), "selections": dict(self.selections), "quantity": self.quantity,
                "has_more_materials": self._cursor is not None, "quote_problem": problem,
                "can_quote": not self.busy and not stale and token is not None,
                "can_checkout": not self.busy and not self._last_error and quote_current and problem is None,
                "can_refresh": not self.busy and not stale and (self._upload_key is not None or self._quote_key is not None),
                "jobs": jobs, "jobs_next_offset": self._jobs_next_offset, "saved_job": saved,
                "can_resume_job": not self.busy and saved is not None and not saved["stale"]
                                  and saved["kind"] in ("analyze", "quote"),
                "upload_job": self._upload_key, "quote_job": self._quote_key}

    def _require_idle(self):
        _gui_thread()
        if self._closed or self.busy:
            raise RuntimeError("Wait for the current manufacturing operation to finish")

    def _begin(self, kind, context=None):
        self._require_idle()
        self.busy, self._kind, self._work_context = True, kind, context
        self._last_error, self._checkout_link = False, None
        self.message = {"materials": "Loading RMFG materials…", "analyze": "Analyzing the folded sheet…",
                        "quote": "Requesting a manufacturing quote…", "checkout": "Preparing website checkout…",
                        "jobs": "Reading saved RMFG jobs…", "inspect_job": "Reading the saved result…",
                        "resume_job": "Resuming the saved manufacturing job…"}[kind]
        self.future = Future()
        self.changed.emit()
        return self.future

    def _spawn(self, work):
        signals = self._signals
        def run():
            try:
                outcome = work(), None
            except Exception as error:
                outcome = None, error
            try:
                signals.ready.emit(outcome)
            except RuntimeError:
                pass
        threading.Thread(target=run, name="RMFG-manufacturing", daemon=True).start()

    def _receive(self, outcome):
        _gui_thread()
        self.busy = False
        if self._closed or not self.future.set_running_or_notify_cancel():
            self.changed.emit()
            return
        value, error = outcome
        try:
            if error is not None:
                raise error
            if self._kind not in ("materials", "jobs", "inspect_job", "resume_job") and self.stale():
                raise RuntimeError("The sheet changed; this result belongs to an earlier revision")
            if self._kind in ("quote", "checkout") and self._token() != self._work_context:
                raise RuntimeError("Manufacturing settings changed; request a quote for the current settings")
            if self._kind == "resume_job":
                current = Operations.capture_revision(self.sheet).summary()
                if (current != self._work_context["revision"] or self.selections != self._work_context["selections"]
                        or self.quantity != self._work_context["quantity"]):
                    raise RuntimeError("The sheet or manufacturing settings changed while restoring this job")
                Editable.get_state_geometry(self.sheet)
                self._export = value["export"]
                self._upload_key = self._quote_key = self._quote_observed_key = self._cart_key = None
                self._quote_context = self._quote_request_context = self._cart_context = None
                self._quote, self.selections, self.quantity = None, {}, 1
                if value["kind"] == "analyze":
                    self.design, self._upload_key = value["response"], value["operation_key"]
                else:
                    self.design, self._quote = value["design"], value["response"]
                    self.selections, self.quantity = value["selections"], value["quantity"]
                    self._quote_key = self._quote_observed_key = value["operation_key"]
                    self._quote_context = self._quote_request_context = value["quote_fingerprint"]
                self._saved_job = value["inspection"]
                self.message = "Restored saved " + value["kind"] + ". Check the current materials and quote status."
            elif self._kind == "jobs":
                self._jobs, self._jobs_next_offset = value["jobs"], value["next_offset"]
                self.message = "Select a saved job to inspect its last recorded result."
            elif self._kind == "inspect_job":
                self._saved_job = value
                self.message = "Showing the last locally saved result; resume to read its current remote status."
            elif self._kind == "materials":
                rows = {item["id"]: item for item in self.materials}
                rows.update({item["id"]: item for item in value["data"]})
                self.materials = list(rows.values())
                self._cursor = value.get("next_cursor") if value.get("has_more") else None
                self.message = "Select a material for each analyzed part."
            elif self._kind == "analyze":
                self.design = value["response"]
                self.message = "RMFG analysis: " + str(self.design.get("status", "unknown"))
            elif self._kind == "quote":
                self._quote, self._quote_context = value["response"], self._work_context
                self._quote_observed_key = value["job_key"]
                self.message = "RMFG quote: " + str(self._quote.get("status", "unknown"))
            else:
                self._checkout_link = value["url"]
                self.message = "Checkout is ready for review on RMFG."
        except Exception as error:
            self.message, self._last_error = str(error), True
            self.future.set_exception(error)
        else:
            self.future.set_result(self.status())
        self.changed.emit()

    def load_materials(self, *, more=False):
        future = self._begin("materials")
        self._spawn(partial(self.backend.materials, self._cursor if more else None))
        return future

    def analyze(self):
        future = self._begin("analyze")
        if self._closed or future.cancelled():
            return future
        if self._export is not None and self._upload_key is not None and not self.stale():
            self._spawn(partial(self.backend.analyze, self._export, self._upload_key))
            return future
        try:
            self.message = "Preparing and exporting the folded sheet…"
            self._export_run = start_prepared_export(self.sheet, expected_revision=Operations.capture_revision(self.sheet))
            future.add_done_callback(lambda request, owned=self._export_run:
                                     owned.future.cancel() if request.cancelled() else None)
            self._export_run.future.add_done_callback(self._export_ready)
        except Exception as error:
            self._receive((None, error))
        return future

    def _export_ready(self, future):
        try:
            export = future.result()
            if self._closed or self.future.cancelled():
                self.busy = False
                return
            self._export, self._upload_key = export, uuid.uuid4().hex
            self.design, self._quote, self._quote_key, self.selections = None, None, None, {}
            self._quote_observed_key = None
            self._spawn(partial(self.backend.analyze, export, self._upload_key))
        except Exception as error:
            self._receive((None, error))

    def set_material(self, part_id, material_id):
        _gui_thread()
        if (self.design is None or part_id not in {part["id"] for part in self.design.get("parts", [])}
                or material_id not in {material["id"] for material in self.materials}):
            raise ValueError("Select an analyzed part and a material from the RMFG catalog")
        self.selections[part_id] = material_id
        self.changed.emit()

    def clear_material(self, part_id):
        _gui_thread()
        if self.design is None or part_id not in {part["id"] for part in self.design.get("parts", [])}:
            raise ValueError("Select an analyzed part")
        self.selections.pop(part_id, None)
        self.changed.emit()

    def set_quantity(self, quantity):
        _gui_thread()
        if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
            raise ValueError("Quantity must be 1 to 1000000 completed design units")
        self.quantity = quantity
        self.changed.emit()

    def request_quote(self):
        self._require_idle()
        token = self._token()
        if token is None or self.stale():
            raise RuntimeError("Analyze the current sheet and select its materials first")
        if (self._quote_key is None or self._quote_request_context != token
                or self._quote is not None and self._quote_observed_key == self._quote_key
                and self._quote.get("status") != "processing"):
            self._quote_key, self._quote_request_context = uuid.uuid4().hex, token
        future = self._begin("quote", token)
        self._spawn(partial(self.backend.evaluate, self._export, copy.deepcopy(self.design),
                            copy.deepcopy(self.materials), dict(self.selections), self.quantity, self._quote_key))
        return future

    def refresh(self):
        if self.stale() or (self._upload_key is None and self._quote_key is None):
            raise RuntimeError("Analyze the current sheet before refreshing manufacturing results")
        if self._quote_key is not None:
            if self._token() != self._quote_request_context:
                raise RuntimeError("Settings changed; request a new quote")
            future = self._begin("quote", self._quote_request_context)
            key = self._quote_key
        else:
            future = self._begin("analyze")
            key = self._upload_key
        self._spawn(partial(self.backend.refresh, key))
        return future

    def checkout(self):
        if not self.status()["can_checkout"]:
            raise RuntimeError("Checkout requires a ready quote for the current sheet and settings")
        token = self._token()
        cart_context = token, self._quote_key
        if self._cart_context != cart_context:
            self._cart_key, self._cart_context = uuid.uuid4().hex, cart_context
        future = self._begin("checkout", token)
        self._spawn(partial(self.backend.checkout, self._export, self._configuration(), self.quantity,
                            self._quote_key, self._cart_key))
        return future

    def checkout_link(self):
        if self._closed or self.stale() or (self._token(), self._quote_key) != self._cart_context:
            raise RuntimeError("The checkout no longer matches the current sheet and settings")
        return self._checkout_link

    def list_jobs(self, *, offset=0, limit=20):
        future = self._begin("jobs")
        self._spawn(partial(self.backend.list_jobs, self.sheet.Document.Uid, self.sheet.Name, offset=offset, limit=limit))
        return future

    def inspect_job(self, operation_key):
        future = self._begin("inspect_job")
        self._spawn(partial(self.backend.inspect_job, self.sheet.Document.Uid, self.sheet.Name, operation_key))
        return future

    def resume_job(self, operation_key):
        self._require_idle()
        Editable.get_state_geometry(self.sheet)
        revision = Operations.capture_revision(self.sheet).summary()
        future = self._begin("resume_job", {"revision": revision, "selections": dict(self.selections), "quantity": self.quantity})
        self._spawn(partial(self.backend.resume_job, self.sheet.Document.Uid, self.sheet.Name, operation_key, revision))
        return future

    def close(self):
        if not self._closed:
            self._closed = True
            App.removeDocumentObserver(self._observer)
            if self.future is not None and not self.future.done():
                self.future.cancel()
            for key, controller in tuple(_controllers.items()):
                if controller is self:
                    _controllers.pop(key, None)
                    panel = _panels.get(key)
                    if panel is not None and panel.controller is self:
                        _panels.pop(key, None)
            self.changed.emit()


class Panel(QtWidgets.QDialog):
    def __init__(self, controller, *, parent=None, open_browser=None):
        super().__init__(parent)
        self.controller, self._closed = controller, False
        self._open_browser = open_browser or (lambda url: QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)))
        self.setWindowTitle("RMFG — " + controller.sheet.Label)
        self.resize(760, 570)
        layout = QtWidgets.QVBoxLayout(self)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        account = QtWidgets.QHBoxLayout()
        connect = QtWidgets.QPushButton("RMFG account")
        from SheetMetalRMFGGui import show_connection
        connect.clicked.connect(show_connection)
        account.addWidget(connect)
        self.materials_button = QtWidgets.QPushButton("Load materials")
        self.materials_button.clicked.connect(lambda: self._action(controller.load_materials))
        account.addWidget(self.materials_button)
        self.more_button = QtWidgets.QPushButton("More materials")
        self.more_button.clicked.connect(lambda: self._action(partial(controller.load_materials, more=True)))
        account.addWidget(self.more_button)
        account.addStretch()
        layout.addLayout(account)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Part", "Detected thickness", "RMFG material"])
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Completed design units"))
        self.quantity = QtWidgets.QSpinBox()
        self.quantity.setRange(1, 1_000_000)
        self.quantity.valueChanged.connect(controller.set_quantity)
        row.addWidget(self.quantity)
        row.addStretch()
        self.price = QtWidgets.QLabel()
        row.addWidget(self.price)
        layout.addLayout(row)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.report, "Current quote")
        saved = QtWidgets.QWidget()
        saved_layout = QtWidgets.QVBoxLayout(saved)
        saved_buttons = QtWidgets.QHBoxLayout()
        self.load_jobs_button = QtWidgets.QPushButton("Load saved jobs")
        self.more_jobs_button = QtWidgets.QPushButton("Older jobs")
        self.jobs = QtWidgets.QComboBox()
        self.jobs.setMinimumContentsLength(20)
        self.jobs.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.inspect_job_button = QtWidgets.QPushButton("View saved result")
        self.resume_job_button = QtWidgets.QPushButton("Resume saved job")
        self.load_jobs_button.clicked.connect(lambda: self._action(controller.list_jobs))
        self.more_jobs_button.clicked.connect(lambda: self._action(partial(controller.list_jobs,
            offset=controller.status()["jobs_next_offset"])))
        self.inspect_job_button.clicked.connect(lambda: self._action(partial(controller.inspect_job, self.jobs.currentData())))
        self.resume_job_button.clicked.connect(lambda: self._action(partial(controller.resume_job,
            controller.status()["saved_job"]["operation_key"])))
        for button in (self.load_jobs_button, self.more_jobs_button):
            saved_buttons.addWidget(button)
        saved_buttons.addWidget(self.jobs, 1)
        saved_buttons.addWidget(self.inspect_job_button)
        saved_layout.addLayout(saved_buttons)
        self.saved_report = QtWidgets.QPlainTextEdit()
        self.saved_report.setReadOnly(True)
        saved_layout.addWidget(self.saved_report)
        saved_layout.addWidget(self.resume_job_button)
        self.tabs.addTab(saved, "Saved RMFG jobs")
        layout.addWidget(self.tabs)
        self._job_rows = None
        buttons = QtWidgets.QHBoxLayout()
        self.analyze_button = QtWidgets.QPushButton("Analyze folded sheet")
        self.quote_button = QtWidgets.QPushButton("Get quote")
        self.refresh_button = QtWidgets.QPushButton("Refresh result")
        self.checkout_button = QtWidgets.QPushButton("Review checkout")
        close = QtWidgets.QPushButton("Close")
        for button, action in ((self.analyze_button, controller.analyze), (self.quote_button, controller.request_quote),
                               (self.refresh_button, controller.refresh), (self.checkout_button, self._checkout)):
            button.clicked.connect(lambda checked=False, action=action: self._action(action))
            buttons.addWidget(button)
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._rows = None
        controller.changed.connect(self._refresh)
        self._refresh()
        if not controller.busy and not controller.materials:
            self._action(controller.load_materials)

    def _action(self, action):
        try:
            return action()
        except Exception as error:
            self.message.setText(str(error))

    def _checkout(self):
        future = self.controller.checkout()
        def ready(result):
            if self._closed or result.cancelled() or result.exception() is not None:
                return
            try:
                link = self.controller.checkout_link()
                if link:
                    self._open_browser(link)
            except Exception as error:
                self.message.setText(str(error))
        future.add_done_callback(ready)

    def _refresh(self):
        if self._closed:
            return
        if self.controller._closed:
            self.close()
            return
        state = self.controller.status()
        message = state["message"]
        if state["stale"]:
            message = "Sheet changed — analyze its current folded geometry before quoting or checkout."
        elif state["quote"] and state["quote"]["stale"]:
            message = "The displayed quote belongs to earlier manufacturing settings."
        self.message.setText(message)
        self.analyze_button.setEnabled(not state["busy"])
        self.materials_button.setEnabled(not state["busy"])
        self.more_button.setEnabled(not state["busy"] and state["has_more_materials"])
        self.quote_button.setEnabled(state["can_quote"])
        self.refresh_button.setEnabled(state["can_refresh"])
        self.checkout_button.setEnabled(state["can_checkout"])
        parts = (state["design"] or {}).get("parts", [])
        signature = json.dumps([parts, state["materials"]], sort_keys=True)
        if signature != self._rows:
            self._rows = signature
            self.table.setRowCount(len(parts))
            for row, part in enumerate(parts):
                for column, text in enumerate((part.get("name") or part["id"],
                        "—" if part.get("detected_thickness_mm") is None else f'{part["detected_thickness_mm"]:g} mm')):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    self.table.setItem(row, column, item)
                combo = QtWidgets.QComboBox()
                combo.addItem("Select material…", None)
                for material in state["materials"]:
                    combo.addItem(f'{material["material"]} — {material["thickness_mm"]:g} mm', material["id"])
                combo.currentIndexChanged.connect(lambda index, combo=combo, part_id=part["id"]:
                    self._action(partial(self.controller.set_material, part_id, combo.itemData(index)))
                    if combo.itemData(index) is not None else self._action(partial(self.controller.clear_material, part_id)))
                self.table.setCellWidget(row, 2, combo)
        for row, part in enumerate(parts):
            combo = self.table.cellWidget(row, 2)
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(state["selections"].get(part["id"]))))
            combo.blockSignals(False)
        self.quantity.blockSignals(True)
        self.quantity.setValue(state["quantity"])
        self.quantity.blockSignals(False)
        quote = state["quote"]
        self.price.setText("")
        lines = []
        if quote:
            amount = quote.get("amount_total_cents")
            if type(amount) is int:
                self.price.setText(f'Parts estimate: ${amount/100:,.2f} USD')
            lines.append("Quote: " + str(quote["status"]))
            lines.extend(f'{item["severity"]}: {item["message"]}' for item in quote["findings"])
            if state["quote_problem"]:
                lines.append(state["quote_problem"])
            lines.append("Shipping, tax and the final total are reviewed on RMFG before payment.")
        self.report.setPlainText("\n".join(lines))
        self.load_jobs_button.setEnabled(not state["busy"])
        self.more_jobs_button.setEnabled(not state["busy"] and state["jobs_next_offset"] is not None)
        signature = json.dumps(state["jobs"], sort_keys=True)
        if signature != self._job_rows:
            selected = self.jobs.currentData()
            self.jobs.clear()
            for job in state["jobs"]:
                label = f'{job["kind"].capitalize()} — {job["remote_id"] or job["operation_key"][:12]} — {job["status"]}'
                if job["stale"]:
                    label += " (earlier revision)"
                self.jobs.addItem(label, job["operation_key"])
            if selected is not None and self.jobs.findData(selected) >= 0:
                self.jobs.setCurrentIndex(self.jobs.findData(selected))
            self._job_rows = signature
        self.inspect_job_button.setEnabled(not state["busy"] and self.jobs.currentData() is not None)
        self.resume_job_button.setEnabled(state["can_resume_job"])
        detail = state["saved_job"]
        lines = []
        if detail is not None:
            lines.append("Earlier revision — view only. Analyze the current sheet to request a new quote."
                         if detail["stale"] else "Current sheet revision — this job can be resumed when its type supports it.")
            lines.append(f'{detail["kind"].capitalize()}: {detail["remote_id"] or "no confirmed remote ID"}')
            lines.append("Last saved status: " + str(detail["status"]))
            if "quantity" in detail:
                lines.append("Completed design units: " + str(detail["quantity"]))
            response = detail.get("response") or {}
            amount = response.get("amount_total_cents")
            if type(amount) is int:
                lines.append(f'Saved parts estimate: ${amount / 100:,.2f} USD')
            lines.extend(item["severity"] + ": " + item["message"] for item in response.get("findings", []))
        self.saved_report.setPlainText("\n".join(lines))

    def closeEvent(self, event):
        self._closed = True
        self.controller.close()
        super().closeEvent(event)

    def reject(self):
        self._closed = True
        self.controller.close()
        super().reject()


_panels = {}
_controllers = {}


def manufacturing_controller(sheet):
    """One GUI-owned configuration shared by native requests and the panel."""
    _gui_thread()
    Editable.state_owner(sheet)
    key = id(sheet.Document), sheet.Name
    controller = _controllers.get(key)
    if controller is None or controller._closed or controller.sheet is not sheet:
        from SheetMetalRMFGGui import connection_controller
        from SheetMetalRMFGClient import RMFGClient
        auth = connection_controller()._auth
        backend = Backend(Path(App.ConfigGet("UserAppData"))/"rmfg"/"jobs.sqlite", RMFGClient(auth.access_token))
        controller = Controller(sheet, backend)
        _controllers[key] = controller
    return controller


def open_checkout(controller):
    """Open only a current private checkout link; never return it to an agent."""
    _gui_thread()
    link = controller.checkout_link()
    if not link:
        raise RuntimeError("Prepare checkout for the current quote first")
    return QtGui.QDesktopServices.openUrl(QtCore.QUrl(link))


def show_manufacturing(sheet):
    _gui_thread()
    controller = manufacturing_controller(sheet)
    key = id(sheet.Document), sheet.Name
    panel = _panels.get(key)
    if panel is None or panel._closed or panel.controller is not controller:
        panel = Panel(controller, parent=Gui.getMainWindow())
        _panels[key] = panel
    panel.show()
    panel.raise_()
    return panel


class _ManufactureCommand:
    def GetResources(self):
        from SheetMetalTools import icons_path
        return {"MenuText": "Manufacture with RMFG", "ToolTip": "Analyze the selected shared sheet, choose materials and review an RMFG quote",
                "Pixmap": str(icons_path) + "/SMLogo.svg"}

    def _sheet(self):
        selected = Gui.Selection.getSelection()
        if len(selected) != 1:
            raise RuntimeError("Select one shared sheet state in the Tree")
        sheet = selected[0]
        if Editable.state_owner(sheet) is not App.ActiveDocument:
            raise RuntimeError("Activate the sheet's document first")
        Operations.capture_revision(sheet)
        return sheet

    def IsActive(self):
        try:
            self._sheet()
            return True
        except (RuntimeError, ValueError, AttributeError, ReferenceError):
            return False

    def Activated(self):
        try:
            show_manufacturing(self._sheet())
        except (RuntimeError, ValueError) as error:
            App.Console.PrintError(str(error) + "\n")


def ensure_commands_registered():
    if Gui.Command.get("SheetMetal_RMFGManufacture") is None:
        Gui.addCommand("SheetMetal_RMFGManufacture", _ManufactureCommand())
