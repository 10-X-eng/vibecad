#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""In-process Qt interaction driver for a running FreeCAD GUI.

This file is executed by the FreeCAD GUI binary as a Python startup file. Use
tools/gui_interaction_harness.py to launch it with an isolated user profile and
timeout.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from collections import Counter
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide import QtCore, QtWidgets


RISKY_WORDS = (
    "about",
    "activate",
    "add",
    "adds",
    "addon",
    "annot",
    "calculator",
    "clarify",
    "close",
    "converter",
    "create",
    "creates",
    "crear",
    "debug",
    "delete",
    "deshacer",
    "dependency",
    "datum",
    "datums",
    "dialog",
    "documentation",
    "donate",
    "done",
    "dimension",
    "exit",
    "export",
    "forum",
    "float",
    "fullscreen",
    "group",
    "grupo",
    "hecho",
    "help",
    "homepage",
    "image",
    "import",
    "internet",
    "light",
    "load",
    "macro",
    "mass",
    "material",
    "measure",
    "new",
    "nueva",
    "nuevo",
    "online",
    "open",
    "overlay",
    "part",
    "parte",
    "preferences",
    "print",
    "project",
    "python",
    "quit",
    "recent",
    "recompute",
    "save",
    "select",
    "selection",
    "sponsor",
    "start page",
    "style",
    "strict",
    "task",
    "theme",
    "texture",
    "undo",
    "url",
    "variable",
    "web",
    "website",
    "what's this",
    "whats this",
    "workbench",
)

RISKY_PHRASES = (
    "bill of materials",
    "coordinate system",
    "close dock window",
    "bottom panel toggle",
    "fit all",
    "freecad dark",
    "freecad light",
    "mass properties",
    "notificationarea",
    "switches between workbenches",
    "set style",
    "textos de cotas",
)


def clean_text(value):
    if value is None:
        return ""
    return str(value).replace("&", "").strip()


def class_name(obj):
    try:
        return obj.metaObject().className()
    except Exception:
        return type(obj).__name__


def text_for(obj):
    for attr in ("text", "windowTitle", "toolTip", "objectName"):
        try:
            value = getattr(obj, attr)()
        except Exception:
            continue
        value = clean_text(value)
        if value:
            return value
    return ""


def action_id(action):
    return {
        "kind": "action",
        "text": clean_text(action.text()),
        "object_name": action.objectName(),
        "tool_tip": clean_text(action.toolTip()),
        "enabled": action.isEnabled(),
        "visible": action.isVisible(),
        "checkable": action.isCheckable(),
        "checked": action.isChecked(),
    }


def widget_id(widget):
    try:
        geometry = widget.geometry()
        return {
            "kind": "widget",
            "class": class_name(widget),
            "object_name": widget.objectName(),
            "text": text_for(widget),
            "enabled": widget.isEnabled(),
            "visible": widget.isVisible(),
            "geometry": [geometry.x(), geometry.y(), geometry.width(), geometry.height()],
        }
    except RuntimeError as exc:
        return {
            "kind": "widget",
            "class": type(widget).__name__,
            "object_name": "",
            "text": "",
            "enabled": False,
            "visible": False,
            "geometry": [0, 0, 0, 0],
            "deleted": True,
            "error": repr(exc),
        }


def is_risky(label):
    lower = label.lower()
    return any(word in lower for word in RISKY_WORDS) or any(
        phrase in lower for phrase in RISKY_PHRASES
    )


def model_count(model, method):
    try:
        return method()
    except TypeError:
        return method(QtCore.QModelIndex())


def is_file_or_native_dialog(widget):
    widget_class = class_name(widget)
    title = text_for(widget).lower()
    object_name = clean_text(widget.objectName()).lower()
    combined = " ".join([widget_class.lower(), title, object_name])
    return (
        isinstance(widget, QtWidgets.QFileDialog)
        or "filedialog" in combined
        or "file dialog" in combined
        or "open document" in combined
        or "save freecad document" in combined
        or "files of type" in combined
    )


class Recorder:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.output_dir / "events.jsonl"
        self.summary_path = self.output_dir / "summary.json"
        self.events = []
        self.started = time.time()
        self._stream = self.events_path.open("w", encoding="utf-8")

    def record(self, event):
        event.setdefault("elapsed_s", round(time.time() - self.started, 3))
        self.events.append(event)
        self._stream.write(json.dumps(event, sort_keys=True) + "\n")
        self._stream.flush()

    def close(self, extra):
        counts = Counter(event.get("status", "unknown") for event in self.events)
        kinds = Counter(event.get("target", {}).get("kind", "meta") for event in self.events)
        summary = {
            "freecad_version": App.Version(),
            "event_count": len(self.events),
            "status_counts": dict(counts),
            "target_kind_counts": dict(kinds),
            "events_path": str(self.events_path),
            **extra,
        }
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        self._stream.close()


class GuiInteractor:
    def __init__(self, config, recorder):
        self.config = config
        self.recorder = recorder
        self.app = QtWidgets.QApplication.instance()
        self.main_window = Gui.getMainWindow()
        self.max_interactions = int(config.get("max_interactions", 500))
        self.max_targets = int(config.get("max_targets", 0))
        self.allow_risky = bool(config.get("allow_risky", False))
        self.mode = config.get("mode", "exercise")
        self.interactions = 0
        self.targets_seen = 0

    def target_budget_exhausted(self):
        return self.max_targets > 0 and self.targets_seen >= self.max_targets

    def process_events(self, ms=50):
        deadline = time.time() + (ms / 1000.0)
        while time.time() < deadline:
            self.app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 25)
            time.sleep(0.01)

    def close_modals(self):
        closed = 0
        for widget in list(self.app.topLevelWidgets()):
            try:
                visible = widget.isVisible()
            except RuntimeError:
                self.recorder.record({"status": "skipped_deleted", "target": widget_id(widget)})
                continue
            if widget is self.main_window or not visible:
                continue
            if isinstance(widget, QtWidgets.QMenu):
                widget.close()
                closed += 1
                continue
            if widget.isModal() or isinstance(widget, QtWidgets.QDialog) or is_file_or_native_dialog(widget):
                status = "file_dialog_closed" if is_file_or_native_dialog(widget) else "modal"
                self.recorder.record({"status": status, "target": widget_id(widget)})
                if hasattr(widget, "reject"):
                    QtCore.QTimer.singleShot(0, widget.reject)
                else:
                    widget.close()
                closed += 1
        if closed:
            self.process_events(100)
        return closed

    def close_documents(self):
        for document in list(App.listDocuments().values()):
            try:
                App.closeDocument(document.Name)
            except Exception as exc:
                self.recorder.record(
                    {
                        "status": "workflow_cleanup_error",
                        "document": document.Name,
                        "error": repr(exc),
                    }
                )

    def require_task_widget(self, expected):
        classes = []
        for widget in self.app.allWidgets():
            try:
                if widget.isVisible():
                    classes.append(class_name(widget))
            except RuntimeError:
                continue
        if not any(expected in widget_class for widget_class in classes):
            raise RuntimeError(f"Expected visible task widget class containing {expected!r}")

    def workflow_event(self, name, status, **extra):
        event = {"status": status, "workflow": name}
        event.update(extra)
        self.recorder.record(event)

    def run_workflow(self, name, callback):
        self.workflow_event(name, "workflow_started")
        try:
            callback()
            self.close_modals()
            self.workflow_event(name, "workflow_pass")
        except Exception as exc:
            self.workflow_event(
                name,
                "workflow_fail",
                error=repr(exc),
                traceback=traceback.format_exc(),
            )
        finally:
            try:
                if Gui.ActiveDocument is not None:
                    Gui.ActiveDocument.resetEdit()
            except Exception:
                pass
            self.close_modals()
            self.close_documents()

    def workflow_switch_workbench(self):
        self.close_documents()
        App.newDocument("GuiWorkflowSwitch")
        for workbench in ("PartDesignWorkbench", "SketcherWorkbench", "TechDrawWorkbench"):
            if not self.switch_workbench(workbench):
                raise RuntimeError(f"Could not activate {workbench}")

    def workflow_create_body(self):
        self.close_documents()
        App.newDocument("GuiWorkflowCreateBody")
        self.activate_or_raise("PartDesignWorkbench")
        body = App.ActiveDocument.addObject("PartDesign::Body", "Body")
        App.ActiveDocument.recompute()
        if body is None or App.ActiveDocument.getObject("Body") is None:
            raise RuntimeError("PartDesign body was not created")
        self.workflow_event("create_body", "workflow_detail", object=body.Name, type_id=body.TypeId)

    def workflow_reopen_document(self):
        self.close_documents()
        path = Path("data/examples/PartDesignExample.FCStd").resolve()
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        Gui.ActiveDocument = Gui.getDocument(document.Name)
        self.process_events(500)
        first_count = len(document.Objects)
        App.closeDocument(document.Name)
        self.process_events(250)
        reopened = App.openDocument(str(path))
        App.setActiveDocument(reopened.Name)
        Gui.ActiveDocument = Gui.getDocument(reopened.Name)
        self.process_events(500)
        if len(reopened.Objects) != first_count:
            raise RuntimeError(f"Reopened object count changed: {first_count} -> {len(reopened.Objects)}")
        self.workflow_event("reopen_document", "workflow_detail", file=str(path), object_count=first_count)

    def close_active_task(self, workflow, method):
        dialog = None
        try:
            dialog = Gui.Control.activeDialog()
        except Exception:
            dialog = None
        if dialog is not None and hasattr(dialog, method):
            getattr(dialog, method)()
            self.workflow_event(workflow, "workflow_detail", close_method=f"activeDialog.{method}")
        else:
            Gui.ActiveDocument.resetEdit()
            self.workflow_event(workflow, "workflow_detail", close_method="resetEdit")
        self.process_events(300)

    def workflow_sketcher_cancel(self):
        self.close_documents()
        document = App.openDocument(str(Path("data/examples/PartDesignExample.FCStd").resolve()))
        App.setActiveDocument(document.Name)
        Gui.ActiveDocument = Gui.getDocument(document.Name)
        self.activate_or_raise("SketcherWorkbench")
        Gui.ActiveDocument.setEdit("Sketch")
        self.process_events(700)
        self.require_task_widget("SketcherGui::TaskSketcherMessages")
        self.close_active_task("sketcher_cancel_task", "reject")

    def workflow_partdesign_accept(self):
        self.close_documents()
        document = App.openDocument(str(Path("data/examples/PartDesignExample.FCStd").resolve()))
        App.setActiveDocument(document.Name)
        Gui.ActiveDocument = Gui.getDocument(document.Name)
        self.activate_or_raise("PartDesignWorkbench")
        Gui.ActiveDocument.setEdit("Pad")
        self.process_events(700)
        self.require_task_widget("PartDesignGui::TaskPadParameters")
        self.close_active_task("partdesign_accept_task", "accept")

    def activate_or_raise(self, workbench):
        if not self.switch_workbench(workbench):
            raise RuntimeError(f"Could not activate {workbench}")

    def run_workflows(self):
        App.TestEnvironment = True
        workflows = [
            ("switch_workbench", self.workflow_switch_workbench),
            ("create_body", self.workflow_create_body),
            ("reopen_document", self.workflow_reopen_document),
            ("sketcher_cancel_task", self.workflow_sketcher_cancel),
            ("partdesign_accept_task", self.workflow_partdesign_accept),
        ]
        self.recorder.record(
            {
                "status": "started",
                "mode": "workflows",
                "version": App.Version(),
                "workflow_count": len(workflows),
            }
        )
        for name, callback in workflows:
            self.run_workflow(name, callback)
        del App.TestEnvironment
        self.recorder.record({"status": "finished", "mode": "workflows"})

    def all_actions(self):
        seen = set()
        actions = []
        for owner in [self.main_window, self.main_window.menuBar(), *self.main_window.findChildren(QtWidgets.QToolBar)]:
            if owner is None:
                continue
            for action in owner.actions():
                if id(action) not in seen:
                    seen.add(id(action))
                    actions.append(action)
                menu = action.menu()
                if menu is not None:
                    for sub_action in menu.actions():
                        if id(sub_action) not in seen:
                            seen.add(id(sub_action))
                            actions.append(sub_action)
        return actions

    def exercise_action(self, action):
        self.targets_seen += 1
        target = action_id(action)
        label = " ".join([target["text"], target["object_name"], target["tool_tip"]]).strip()
        if not label:
            self.recorder.record({"status": "skipped_empty", "target": target})
            return
        if action.isSeparator() or action.menu() is not None:
            self.recorder.record({"status": "discovered", "target": target})
            return
        if not action.isEnabled() or not action.isVisible():
            self.recorder.record({"status": "skipped_disabled", "target": target})
            return
        if not self.allow_risky and is_risky(label):
            self.recorder.record({"status": "skipped_risky", "target": target})
            return
        if self.close_modals():
            self.recorder.record({"status": "skipped_unmanaged_dialog_state", "target": target})
            return
        if self.mode == "survey":
            self.recorder.record({"status": "discovered", "target": target})
            return
        try:
            self.recorder.record({"status": "attempting", "target": target})
            action.trigger()
            self.interactions += 1
            self.process_events(120)
            self.close_modals()
            self.recorder.record({"status": "triggered", "target": target})
        except Exception as exc:
            self.recorder.record(
                {
                    "status": "exception",
                    "target": target,
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    def exercise_widget(self, widget):
        self.targets_seen += 1
        target = widget_id(widget)
        if target.get("deleted"):
            self.recorder.record({"status": "skipped_deleted", "target": target})
            return
        label = " ".join([target["text"], target["object_name"], target["class"]]).strip()
        try:
            enabled = widget.isEnabled()
            visible = widget.isVisible()
        except RuntimeError:
            self.recorder.record({"status": "skipped_deleted", "target": widget_id(widget)})
            return
        if not enabled or not visible:
            self.recorder.record({"status": "skipped_disabled", "target": target})
            return
        if target["geometry"][2] <= 0 or target["geometry"][3] <= 0:
            self.recorder.record({"status": "skipped_no_geometry", "target": target})
            return
        if isinstance(widget, QtWidgets.QToolButton) and widget.menu() is not None:
            self.recorder.record({"status": "skipped_menu_button", "target": target})
            return
        if not self.allow_risky and is_risky(label):
            self.recorder.record({"status": "skipped_risky", "target": target})
            return
        if isinstance(widget, (QtWidgets.QComboBox, QtWidgets.QAbstractItemView)):
            self.recorder.record({"status": "skipped_conservative_widget", "target": target})
            return
        if self.close_modals():
            self.recorder.record({"status": "skipped_unmanaged_dialog_state", "target": target})
            return
        if self.mode == "survey":
            self.recorder.record({"status": "discovered", "target": target})
            return
        try:
            status = "unsupported"
            if isinstance(widget, QtWidgets.QAbstractButton):
                widget.click()
                status = "clicked"
            elif isinstance(widget, QtWidgets.QLineEdit) and not widget.isReadOnly():
                widget.setText("FreeCAD GUI harness")
                status = "edited"
            elif isinstance(widget, QtWidgets.QAbstractSpinBox) and not widget.isReadOnly():
                widget.stepUp()
                status = "changed"
            elif isinstance(widget, QtWidgets.QAbstractSlider):
                widget.setValue(min(widget.maximum(), widget.value() + widget.singleStep()))
                status = "changed"
            elif isinstance(widget, QtWidgets.QTabBar) and widget.count() > 1:
                widget.setCurrentIndex((widget.currentIndex() + 1) % widget.count())
                status = "changed"
            if status == "unsupported":
                self.recorder.record({"status": "discovered", "target": target})
            else:
                self.interactions += 1
                self.process_events(120)
                self.close_modals()
                self.recorder.record({"status": status, "target": target})
        except Exception as exc:
            self.recorder.record(
                {
                    "status": "exception",
                    "target": target,
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    def switch_workbench(self, name):
        try:
            previous = Gui.activeWorkbench().name()
            if previous != name:
                Gui.activateWorkbench(name)
                Gui.updateGui()
                self.process_events(250)
                self.close_modals()
            self.recorder.record({"status": "workbench_active", "workbench": name})
            return True
        except Exception as exc:
            self.recorder.record({"status": "workbench_exception", "workbench": name, "error": repr(exc)})
            return False

    def run(self):
        if self.mode == "workflows":
            self.run_workflows()
            return

        App.TestEnvironment = True
        if App.ActiveDocument is None:
            App.newDocument("GuiInteractionHarness")
        self.process_events(500)
        workbenches = sorted(Gui.listWorkbenches().keys())
        max_workbenches = int(self.config.get("max_workbenches", 0))
        if max_workbenches > 0:
            workbenches = workbenches[:max_workbenches]
        self.recorder.record(
            {
                "status": "started",
                "version": App.Version(),
                "workbench_count": len(workbenches),
                "widget_count": len(self.app.allWidgets()),
            }
        )
        for workbench in workbenches:
            if self.interactions >= self.max_interactions:
                break
            if not self.switch_workbench(workbench):
                continue
            for action in self.all_actions():
                if self.interactions >= self.max_interactions or self.target_budget_exhausted():
                    break
                self.exercise_action(action)
            for widget in list(self.app.allWidgets()):
                if self.interactions >= self.max_interactions or self.target_budget_exhausted():
                    break
                self.exercise_widget(widget)
            if self.target_budget_exhausted():
                break
        self.close_modals()
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)
        del App.TestEnvironment
        self.recorder.record(
            {
                "status": "finished",
                "interactions": self.interactions,
                "targets_seen": self.targets_seen,
                "widget_count": len(self.app.allWidgets()),
            }
        )


def main():
    config_path = os.environ["FREECAD_GUI_HARNESS_CONFIG"]
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    recorder = Recorder(config["output_dir"])
    try:
        GuiInteractor(config, recorder).run()
        recorder.close({"result": "ok"})
    except Exception as exc:
        recorder.record({"status": "fatal", "error": repr(exc), "traceback": traceback.format_exc()})
        recorder.close({"result": "fatal", "error": repr(exc)})
    finally:
        QtCore.QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)


QtCore.QTimer.singleShot(1500, main)
