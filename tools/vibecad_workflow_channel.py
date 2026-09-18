# SPDX-License-Identifier: LGPL-2.1-or-later
"""Loopback click channel used by the VibeCAD workflow harness.

This is the same authenticated HTTP surface the visible tour uses:
``GET /v1/status``, ``GET /v1/documents``, ``GET /v1/ui/ribbon``,
``GET /v1/ui/menus``, ``POST /v1/ui/click``, and ``POST /v1/run``.
Clicks stay on that one route. Nothing here drives the OS mouse.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TREE_INSPECT_PYTHON = """
# vibecad.workflow-harness:inspect_tree
doc = App.ActiveDocument
result = {
    "document": None if doc is None else str(doc.Name),
    "objects": []
    if doc is None
    else [
        {
            "name": str(obj.Name),
            "type_id": str(obj.TypeId),
            "label": str(getattr(obj, "Label", "") or ""),
            "geometry_count": int(getattr(obj, "GeometryCount", 0) or 0),
        }
        for obj in list(doc.Objects)
    ],
}
"""

# Same in-process SketchObject.addGeometry + Part.Circle call
# TestConsolidatedPartTools._circle_sketch,
# TestSketcherSolver.CreateCircleSketch, and
# TestDesignProfileRegionsGui._master_sketch already use in this tree.
# Sketcher_CreateRectangle is a real command, but activated() only
# starts DrawSketchHandlerRectangle; the two corners need view clicks.
PLACE_CLOSED_CIRCLE_PYTHON = """
# vibecad.workflow-harness:place_closed_circle
import Part
import PartDesign

doc = App.ActiveDocument
if doc is None:
    raise RuntimeError("No active document")
sketch = next(
    (
        obj
        for obj in list(doc.Objects)
        if obj.isDerivedFrom("Sketcher::SketchObject")
    ),
    None,
)
if sketch is None:
    raise RuntimeError("No Sketcher::SketchObject")
if Gui.editDocument() is not None:
    raise RuntimeError("Sketch is still in edit mode")
if int(sketch.GeometryCount) == 0:
    sketch.addGeometry(
        Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10),
        False,
    )
PartDesign.finalizeDesignDefinition(sketch)
doc.recompute()
if hasattr(doc, "hasPendingTransaction") and doc.hasPendingTransaction():
    doc.commitTransaction()
result = {
    "sketch": str(sketch.Name),
    "geometry_count": int(sketch.GeometryCount),
}
"""

# DocumentRecovery.ui title is "Document Recovery".
# DocumentRecovery.cpp relabels Ok to "Start Recovery".
# The other standard button is Cancel (QDialogButtonBox::Cancel).
DISMISS_DOCUMENT_RECOVERY_PYTHON = """
# vibecad.workflow-harness:dismiss_document_recovery
QtWidgets = None
for module_name in ("PySide6.QtWidgets", "PySide2.QtWidgets", "PySide.QtWidgets"):
    try:
        QtWidgets = __import__(module_name, fromlist=["QDialog"])
        break
    except ImportError:
        continue
if QtWidgets is None:
    raise RuntimeError("Qt widgets are unavailable")

dismissed = []
main = Gui.getMainWindow()
for dialog in list(main.findChildren(QtWidgets.QDialog) or []):
    if not bool(getattr(dialog, "isVisible", lambda: False)()):
        continue
    title = str(dialog.windowTitle() or "")
    if title != "Document Recovery":
        continue
    box = dialog.findChild(QtWidgets.QDialogButtonBox)
    cancel = box.button(QtWidgets.QDialogButtonBox.Cancel) if box is not None else None
    if cancel is None:
        raise RuntimeError("Document Recovery has no Cancel button")
    cancel.click()
    dismissed.append("Cancel")
result = {"dismissed": dismissed, "button": "Cancel"}
"""

# SketcherGui.leaveActiveSketch is the exact-edit finish
# (SketchEditControl.cpp). CmdSketcherLeaveSketch::activated() passes
# getActiveGuiDocument(), which is Application::activeDocument().
# requireExactEditState throws "The exact Sketch document is no longer
# active in edit mode" when activeDocument() != editDocument().
# A queued LeaveSketch click plus Std_New per workflow creates that
# split. This recipe uses the edit document, not the active tab.
LEAVE_ACTIVE_SKETCH_PYTHON = """
# vibecad.workflow-harness:leave_active_sketch
import SketcherGui

edit = Gui.editDocument()
if edit is None:
    result = {
        "edit_mode": "closed",
        "left": False,
        "document": None,
        "sketch": None,
    }
else:
    app_doc = edit.Document
    view = edit.getInEdit()
    sketch = getattr(view, "Object", None) if view is not None else None
    if sketch is None or not sketch.isDerivedFrom("Sketcher::SketchObject"):
        raise RuntimeError(
            "The exact requested Sketch is no longer the active edit target."
        )
    native = SketcherGui.leaveActiveSketch(
        str(app_doc.Name),
        str(app_doc.Uid),
        str(sketch.Name),
    )
    result = {
        "edit_mode": native.get("edit_mode"),
        "left": True,
        "document": str(app_doc.Name),
        "sketch": str(sketch.Name),
        "accepted_task_dialog": native.get("accepted_task_dialog"),
    }
if Gui.editDocument() is not None:
    raise RuntimeError("The exact Sketch edit session did not close")
"""

# designProfileOperationActive() requires a reusable sketch or InternalFace*
# (ReferenceSelection.cpp). Edge1 disables Extrude. The live enable path in
# TestDesignProfileRegionsGui is addSelection(sketch, "InternalFace1") plus
# Gui.Command.update() so the QAction isEnabled flag matches isActive().
SELECT_SKETCH_PYTHON = """
# vibecad.workflow-harness:select_sketch
import PartDesign

doc = App.ActiveDocument
if doc is None:
    raise RuntimeError("No active document")
sketch = next(
    (
        obj
        for obj in list(doc.Objects)
        if obj.isDerivedFrom("Sketcher::SketchObject")
    ),
    None,
)
if sketch is None:
    raise RuntimeError("No Sketcher::SketchObject")
if int(sketch.GeometryCount) < 1:
    raise RuntimeError("Sketch has no geometry to select")
PartDesign.finalizeDesignDefinition(sketch)
doc.recompute()
faces = list(getattr(getattr(sketch, "InternalShape", None), "Faces", []) or [])
if not faces:
    raise RuntimeError(
        "Sketch has no InternalFace; closed profile did not produce a filled area"
    )
Gui.Selection.clearSelection()
Gui.Selection.addSelection(sketch, "InternalFace1")
if hasattr(doc, "hasPendingTransaction") and doc.hasPendingTransaction():
    doc.commitTransaction()
if hasattr(Gui, "Command") and hasattr(Gui.Command, "update"):
    Gui.Command.update()
command_active = False
if hasattr(Gui, "isCommandActive"):
    command_active = bool(Gui.isCommandActive("PartDesign_DesignExtrude"))
result = {
    "selected": str(sketch.Name),
    "sub": "InternalFace1",
    "command_active": command_active,
}
"""

# TestDesignProfileRegionsGui._close_task clicks the visible Tasks-dock
# QDialogButtonBox OK, then waits until Gui.Control.activeDialog is gone.
# Kind dialog looks for a QDialog titled OK and finds none: Extrude OK
# is not a modal. After that OK, DesignExtrude.Shape stays null
# (test_final_result_checkbox_renders_unpublished_design_output).
# The solid is BodyResult, type PartDesign::DesignBodyPublication.
ACCEPT_DESIGN_TASK_PYTHON = """
# vibecad.workflow-harness:accept_design_task
QtWidgets = None
QtCore = None
for module_name in ("PySide6", "PySide2", "PySide"):
    try:
        QtWidgets = __import__(
            module_name + ".QtWidgets",
            fromlist=["QDialogButtonBox"],
        )
        QtCore = __import__(
            module_name + ".QtCore",
            fromlist=["QElapsedTimer", "QEventLoop", "QTimer"],
        )
        break
    except ImportError:
        continue
if QtWidgets is None or QtCore is None:
    raise RuntimeError("Qt widgets are unavailable")

def _process_events(wait_ms=20):
    if hasattr(Gui, "updateGui"):
        Gui.updateGui()
    application = QtWidgets.QApplication.instance()
    if application is not None:
        application.processEvents()
    if wait_ms:
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(wait_ms, loop.quit)
        loop.exec()

def _task_button(standard_button):
    _process_events()
    for button_box in Gui.getMainWindow().findChildren(
        QtWidgets.QDialogButtonBox
    ):
        if not button_box.isVisible():
            continue
        button = button_box.button(standard_button)
        if button is not None and button.isVisible() and button.isEnabled():
            return button
    return None

if not Gui.Control.activeDialog():
    raise RuntimeError("The Design operation task panel did not open")
button = _task_button(QtWidgets.QDialogButtonBox.Ok)
if button is None:
    raise RuntimeError("No visible task QDialogButtonBox OK")
button.click()
timer = QtCore.QElapsedTimer()
timer.start()
finished = False
while timer.elapsed() < 5000:
    _process_events()
    if not Gui.Control.activeDialog():
        finished = True
        break
if not finished:
    raise RuntimeError("The task did not finish accepting or cancelling")
doc = App.ActiveDocument
if doc is not None:
    doc.recompute()
publication = next(
    (
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(obj.TypeId) == "PartDesign::DesignBodyPublication"
    ),
    None,
)
if publication is None:
    raise RuntimeError("No PartDesign::DesignBodyPublication after task OK")
result = {
    "accepted": True,
    "button": "Ok",
    "publication": str(publication.Name),
    "type_id": str(publication.TypeId),
}
"""

# Import.export is the in-process exporter Std_Export calls after
# FileDialog::getSaveFileName (src/Mod/Import/App/AppImportPy.cpp).
# WriterStep::write only throws on IFSelect RetError/RetFail/RetStop.
# Live 6714cd65: Import.export returned, then os.path.getsize raised
# FileNotFoundError on the Windows temp path. TopoShape.exportStep
# (TopoShapePy / TopoShape.cpp) is the STEP write Part.export uses.
# Live after Extrude task OK: DesignExtrude.Shape is still null. The
# solid with Faces is BodyResult (PartDesign::DesignBodyPublication).
EXPORT_STEP_PYTHON = """
# vibecad.workflow-harness:export_step
import Import
import os

doc = App.ActiveDocument
if doc is None:
    raise RuntimeError("No active document")
doc.recompute()

def _faces(obj):
    shape = getattr(obj, "Shape", None)
    is_null = getattr(shape, "isNull", None)
    if shape is None or (callable(is_null) and bool(is_null())):
        return []
    return list(getattr(shape, "Faces", []) or [])

with_faces = [
    (obj, _faces(obj))
    for obj in list(doc.Objects)
]
with_faces = [(obj, faces) for obj, faces in with_faces if faces]
solid = next(
    (
        obj
        for obj, _ignored in with_faces
        if str(obj.TypeId) == "PartDesign::DesignBodyPublication"
    ),
    None,
)
if solid is None and with_faces:
    solid = with_faces[0][0]
if solid is None:
    raise RuntimeError("No object with Faces to export")
shape = solid.Shape
path = __EXPORT_PATH__
parent = os.path.dirname(path)
if parent:
    os.makedirs(parent, exist_ok=True)
writer = "Import.export"
Import.export([solid], path)
if not os.path.isfile(path) or os.path.getsize(path) < 1:
    exporter = getattr(shape, "exportStep", None)
    if not callable(exporter):
        raise RuntimeError("Shape.exportStep is unavailable")
    exporter(path)
    writer = "Shape.exportStep"
if not os.path.isfile(path):
    raise RuntimeError("STEP was not written at " + path)
size = os.path.getsize(path)
if size < 1:
    raise RuntimeError("STEP at " + path + " is empty")
result = {
    "exported_path": path,
    "bytes": size,
    "object": str(solid.Name),
    "type_id": str(solid.TypeId),
    "writer": writer,
    "face_count": len(_faces(solid)),
}
"""


def workflow_run_python(recipe_id: str, *, export_path: str = "") -> str:
    recipes = {
        "place_closed_circle": PLACE_CLOSED_CIRCLE_PYTHON,
        "select_sketch": SELECT_SKETCH_PYTHON,
        "leave_active_sketch": LEAVE_ACTIVE_SKETCH_PYTHON,
        "dismiss_document_recovery": DISMISS_DOCUMENT_RECOVERY_PYTHON,
        "accept_design_task": ACCEPT_DESIGN_TASK_PYTHON,
        "export_step": EXPORT_STEP_PYTHON.replace(
            "__EXPORT_PATH__", json.dumps(str(export_path))
        ),
        "inspect_tree": TREE_INSPECT_PYTHON,
    }
    try:
        return recipes[str(recipe_id or "").strip()]
    except KeyError as exc:
        raise ValueError(f"Unknown workflow run recipe {recipe_id!r}.") from exc


def require_loopback_url(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    if (
        parsed.scheme.lower() != "http"
        or host not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "The workflow harness only talks to an authenticated loopback "
            "http://127.0.0.1:<port> agent."
        )
    return str(base_url).rstrip("/")


def click_input_method(kind: str) -> str:
    return {
        "ribbon": "qt_in_process_mouse_click",
        "menu": "qt_in_process_menu_popup",
        "action": "qt_in_process_action_trigger",
        "command": "qt_in_process_action_trigger",
        "button": "qt_in_process_action_trigger",
        "dialog": "qt_in_process_dialog_button",
    }.get(str(kind or "").strip().lower(), "")


class AgentClickChannel:
    """HTTP client for the existing loopback ``/v1/ui/click`` path."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = require_loopback_url(base_url)
        self.token = str(token or "").strip()
        if len(self.token) < 40:
            raise ValueError("The checkout-scoped bearer token is invalid.")
        self.timeout_seconds = float(timeout_seconds)

    def request(self, method: str, route: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        data = None
        if method == "POST":
            headers["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}{route}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = request.urlopen(http_request, timeout=self.timeout_seconds)
            try:
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                response.close()
        except error.HTTPError as exc:
            try:
                raw = exc.read() if hasattr(exc, "read") else b""
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}
            finally:
                exc.close()
            if isinstance(payload, dict) and payload:
                return payload
            return {
                "ok": False,
                "failure_code": "HTTP_ERROR",
                "error": f"Agent control HTTP {exc.code}.",
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "failure_code": "INVALID_RESPONSE",
                "error": "Agent control did not return a JSON object.",
            }
        return payload

    def status(self) -> dict[str, Any]:
        return self.request("GET", "/v1/status")

    def documents(self) -> dict[str, Any]:
        return self.request("GET", "/v1/documents")

    def click(
        self,
        kind: str,
        text: str,
        *,
        expected_process_id: int | None = None,
        expected_index: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "text": text}
        if expected_process_id is not None:
            body["expected_process_id"] = expected_process_id
        if expected_index is not None:
            body["expected_index"] = expected_index
        return self.request("POST", "/v1/ui/click", body)

    def inspect_tree(self) -> dict[str, Any]:
        return self.request("POST", "/v1/run", {"python": TREE_INSPECT_PYTHON, "recompute": False})

    def run(self, python: str, *, recompute: bool = True) -> dict[str, Any]:
        return self.request(
            "POST",
            "/v1/run",
            {"python": python, "recompute": bool(recompute)},
        )


class FakeAgentState:
    """In-memory GUI/document state for CI when no display is available."""

    def __init__(self, export_dir: str, *, recovery_dialog: bool = False) -> None:
        self.export_dir = export_dir
        self.documents: list[dict[str, Any]] = []
        self.active_index = -1
        self.selected_ribbon = "Model"
        self.exported_path = ""
        self.click_count = 0
        self.orientation_dialog = False
        self.sketch_edit = False
        self.recovery_dialog = bool(recovery_dialog)
        self.selection: list[dict[str, str]] = []

    def active_document(self) -> dict[str, Any] | None:
        if self.active_index < 0 or self.active_index >= len(self.documents):
            return None
        return self.documents[self.active_index]

    def document_summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for index, document in enumerate(self.documents):
            summaries.append(
                {
                    "document": document["name"],
                    "label": document["name"],
                    "path": "",
                    "active": index == self.active_index,
                    "object_count": len(document["objects"]),
                    "modified": True,
                }
            )
        return summaries

    def tree_result(self) -> dict[str, Any]:
        document = self.active_document()
        if document is None:
            return {"document": None, "objects": []}
        return {
            "document": document["name"],
            "objects": [
                {
                    "name": obj["name"],
                    "type_id": obj["type_id"],
                    "label": obj["label"],
                    "geometry_count": int(obj.get("geometry_count") or 0),
                }
                for obj in document["objects"]
            ],
        }

    def run_python(self, source: str) -> dict[str, Any]:
        document = self.active_document()
        if "vibecad.workflow-harness:place_closed_circle" in source:
            if document is None:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No active document",
                }
            sketch = next(
                (
                    obj
                    for obj in document["objects"]
                    if obj["type_id"] == "Sketcher::SketchObject"
                ),
                None,
            )
            if sketch is None:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No Sketcher::SketchObject",
                }
            if self.sketch_edit:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "Sketch is still in edit mode",
                }
            sketch["closed_profile"] = True
            sketch["geometry_count"] = max(int(sketch.get("geometry_count") or 0), 1)
            # Adding geometry resolves a leftover tree selection to an edge.
            # Edge1 is the live disable path in TestDesignProfileRegionsGui.
            self.selection = [{"name": sketch["name"], "sub": "Edge1"}]
            return {
                "ok": True,
                "result": {
                    "sketch": sketch["name"],
                    "geometry_count": sketch["geometry_count"],
                },
            }
        if "vibecad.workflow-harness:leave_active_sketch" in source:
            if not self.sketch_edit:
                return {
                    "ok": True,
                    "result": {
                        "edit_mode": "closed",
                        "left": False,
                        "document": None,
                        "sketch": None,
                    },
                }
            document = self.active_document()
            sketch = next(
                (
                    obj
                    for obj in (document or {}).get("objects") or []
                    if obj["type_id"] == "Sketcher::SketchObject"
                ),
                None,
            )
            self.sketch_edit = False
            self.selected_ribbon = "Model"
            return {
                "ok": True,
                "result": {
                    "edit_mode": "closed",
                    "left": True,
                    "document": None if document is None else document["name"],
                    "sketch": None if sketch is None else sketch["name"],
                    "accepted_task_dialog": True,
                },
            }
        if "vibecad.workflow-harness:dismiss_document_recovery" in source:
            dismissed = []
            if self.recovery_dialog:
                self.recovery_dialog = False
                dismissed.append("Cancel")
            return {
                "ok": True,
                "result": {"dismissed": dismissed, "button": "Cancel"},
            }
        if "vibecad.workflow-harness:select_sketch" in source:
            if document is None:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No active document",
                }
            sketch = next(
                (
                    obj
                    for obj in document["objects"]
                    if obj["type_id"] == "Sketcher::SketchObject"
                ),
                None,
            )
            if sketch is None:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No Sketcher::SketchObject",
                }
            if int(sketch.get("geometry_count") or 0) < 1:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "Sketch has no geometry to select",
                }
            if "InternalFace1" in source:
                self.selection = [{"name": sketch["name"], "sub": "InternalFace1"}]
                command_active = True
                sub = "InternalFace1"
            else:
                # Name-pair addSelection after a circle is the live miss.
                self.selection = [{"name": sketch["name"], "sub": "Edge1"}]
                command_active = False
                sub = "Edge1"
            return {
                "ok": True,
                "result": {
                    "selected": sketch["name"],
                    "sub": sub,
                    "command_active": command_active,
                },
            }
        if "vibecad.workflow-harness:accept_design_task" in source:
            if document is None:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No active document",
                }
            if not any(bool(obj.get("design_task")) for obj in document["objects"]):
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "The Design operation task panel did not open",
                }
            if "QDialogButtonBox" not in source:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No visible task QDialogButtonBox OK",
                }
            for obj in document["objects"]:
                obj["design_task"] = False
            publication = next(
                (
                    obj
                    for obj in document["objects"]
                    if obj["type_id"] == "PartDesign::DesignBodyPublication"
                ),
                None,
            )
            if publication is None:
                publication = {
                    "name": "BodyResult",
                    "type_id": "PartDesign::DesignBodyPublication",
                    "label": "BodyResult",
                    "shape_null": False,
                    "face_count": 3,
                }
                document["objects"].append(publication)
            return {
                "ok": True,
                "result": {
                    "accepted": True,
                    "button": "Ok",
                    "publication": publication["name"],
                    "type_id": publication["type_id"],
                },
            }
        if "vibecad.workflow-harness:export_step" in source:
            if document is None:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No active document",
                }

            def _has_faces(obj: dict[str, Any]) -> bool:
                return (
                    int(obj.get("face_count") or 0) > 0
                    and not bool(obj.get("shape_null"))
                )

            solid = next(
                (
                    obj
                    for obj in document["objects"]
                    if obj["type_id"] == "PartDesign::DesignBodyPublication"
                    and _has_faces(obj)
                ),
                None,
            )
            if solid is None:
                solid = next(
                    (obj for obj in document["objects"] if _has_faces(obj)),
                    None,
                )
            if solid is None:
                return {
                    "ok": False,
                    "failure_code": "SCRIPT_FAILED",
                    "error": "No object with Faces to export",
                }
            path = Path(self.export_dir) / f"{document['name']}.step"
            path.write_text(
                "ISO-10303-21; /* fake STEP from workflow harness */\n",
                encoding="utf-8",
            )
            self.exported_path = str(path)
            return {
                "ok": True,
                "result": {
                    "exported_path": self.exported_path,
                    "bytes": path.stat().st_size,
                    "object": solid["name"],
                    "type_id": solid["type_id"],
                    "writer": "Import.export",
                    "face_count": int(solid.get("face_count") or 0),
                },
                "exported_path": self.exported_path,
            }
        return {
            "ok": True,
            "result": self.tree_result(),
            "exported_path": self.exported_path,
        }

    def click_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        kind = str(body.get("kind") or "").strip().lower().replace("-", "_")
        if kind in {"tab", "ribbon_tab"}:
            kind = "ribbon"
        if kind in {"command", "button"}:
            kind = "action"
        text = str(body.get("text") or "").strip()
        self.click_count += 1
        if kind not in {"ribbon", "menu", "action", "dialog"}:
            return {
                "ok": False,
                "failure_code": "UI_TARGET_KIND_INVALID",
                "error": "kind must be 'ribbon', 'menu', 'action', or 'dialog'.",
            }
        if not text:
            return {
                "ok": False,
                "failure_code": "UI_TARGET_TEXT_REQUIRED",
                "error": "text must name one visible semantic UI target.",
            }

        details = {
            "target_kind": kind,
            "target_text": text,
            "target_index": 0,
            "click_queued": False,
            "focus_restored": True,
            "active_window_unchanged": True,
            "popup_restored": True,
            "active_action_restored": True,
            "interaction_restored": True,
            "input_method": click_input_method(kind),
            "physical_cursor_control": "none",
            "physical_cursor_unchanged": True,
            "semantic_verified": True,
        }

        if kind == "ribbon":
            if text not in {"Model", "Sketch", "Aero"}:
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": f"Expected exactly one ribbon tab named {text!r}; found 0.",
                }
            if text == "Model" and self.sketch_edit:
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_DISABLED",
                    "error": f"Ribbon tab {text!r} is disabled.",
                    **details,
                    "semantic_verified": False,
                }
            self.selected_ribbon = text
            details["selected_after"] = text
            return {"ok": True, **details}

        if kind == "menu":
            if text != "File":
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": f"Expected exactly one top-level menu named {text!r}; found 0.",
                }
            details.update(
                {
                    "menu_visible": True,
                    "menu_open_after": False,
                    "preview_duration_milliseconds": 240,
                }
            )
            return {"ok": True, **details}

        if kind == "dialog":
            return self._click_dialog(text, details)

        return self._click_action(text, details)

    def _click_action(self, text: str, details: dict[str, Any]) -> dict[str, Any]:
        if text in {"Std_New", "New"}:
            name = f"Unnamed{len(self.documents) + 1}"
            self.documents.append({"name": name, "objects": []})
            self.active_index = len(self.documents) - 1
            self.selection = []
            details["object_name"] = "Std_New"
            # Creating a document moves focus. The live agent reports that
            # as UI_CLICK_NOT_APPLIED; the harness must still pass on the
            # new document.
            details["focus_restored"] = False
            details["interaction_restored"] = False
            details["semantic_verified"] = False
            return {
                "ok": False,
                "failure_code": "UI_CLICK_NOT_APPLIED",
                "error": f"Qt click did not activate action target {text!r}.",
                **details,
            }

        document = self.active_document()
        if document is None:
            return {
                "ok": False,
                "failure_code": "UI_CLICK_NOT_APPLIED",
                "error": f"Qt click did not activate action target {text!r}.",
                **details,
                "semantic_verified": False,
            }

        type_ids = [str(obj["type_id"]) for obj in document["objects"]]
        if text in {"PartDesign_NewBody", "New Body"}:
            document["objects"].append(
                {
                    "name": "Body",
                    "type_id": "PartDesign::Body",
                    "label": "Body",
                }
            )
            details["object_name"] = "PartDesign_NewBody"
            return {"ok": True, **details}

        if text in {
            "PartDesign_NewSketch",
            "Sketcher_NewSketch",
            "Create Sketch",
            "Sketch",
        }:
            if "PartDesign::Body" not in type_ids:
                return {
                    "ok": False,
                    "failure_code": "UI_CLICK_NOT_APPLIED",
                    "error": "Sketch has no Body to own it.",
                    **details,
                    "semantic_verified": False,
                }
            command_name = (
                "PartDesign_NewSketch"
                if text == "PartDesign_NewSketch"
                else "Sketcher_NewSketch"
            )
            self.orientation_dialog = True
            details["object_name"] = command_name
            details["click_queued"] = True
            return {"ok": True, **details}

        if text in {
            "Sketcher_CreateRectangle",
            "Sketcher_CompCreateRectangles",
            "Rectangle",
        }:
            # Real sketch.edit Geometry control. activated() only starts
            # DrawSketchHandlerRectangle; it does not add a closed profile.
            if not self.sketch_edit:
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": f"Expected exactly one action named {text!r}; found 0.",
                }
            details["object_name"] = "Sketcher_CreateRectangle"
            details["click_queued"] = True
            return {"ok": True, **details}

        if text in {"Sketcher_LeaveSketch", "Leave Sketch"}:
            if not self.sketch_edit:
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": f"Expected exactly one action named {text!r}; found 0.",
                }
            self.sketch_edit = False
            self.selected_ribbon = "Model"
            details["object_name"] = "Sketcher_LeaveSketch"
            details["click_queued"] = True
            return {"ok": True, **details}

        if text in {"PartDesign_Pad", "Pad"}:
            # Command.cpp still registers PartDesign_Pad, but createAction()
            # runs only when a command is addTo()'d. The Model ribbon and
            # sketch.edit Finish group never surface it, so findChildren
            # reports found 0 before and after leaving the sketch.
            return {
                "ok": False,
                "failure_code": "UI_TARGET_NOT_UNIQUE",
                "error": f"Expected exactly one action named {text!r}; found 0.",
            }

        if text in {"PartDesign_DesignExtrude", "Extrude"}:
            if self.sketch_edit or not self._design_extrude_active():
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_DISABLED",
                    "failure_stage": "precondition",
                    "error": f"Action {text!r} is disabled or hidden.",
                    "action_pick": "disabled",
                    "action_match_count": 1,
                    "command_active": False,
                    **details,
                    "semantic_verified": False,
                }
            if "Sketcher::SketchObject" not in type_ids:
                return {
                    "ok": False,
                    "failure_code": "UI_CLICK_NOT_APPLIED",
                    "error": "Extrude has no sketch to consume.",
                    **details,
                    "semantic_verified": False,
                }
            has_closed_profile = any(
                str(obj.get("type_id") or "") == "Sketcher::SketchObject"
                and bool(obj.get("closed_profile"))
                for obj in document["objects"]
            )
            details["object_name"] = "PartDesign_DesignExtrude"
            details["click_queued"] = True
            if not has_closed_profile:
                # Live queues the trigger, then FeatureSketchBased throws
                # "Linked shape object is empty" and Command.cpp aborts
                # after setEdit finds no task panel. No DesignExtrude object.
                details["error"] = "Linked shape object is empty"
                return {"ok": True, **details}
            document["objects"].append(
                {
                    "name": "Extrude",
                    "type_id": "PartDesign::DesignExtrude",
                    "label": "Extrude",
                    "shape_null": True,
                    "face_count": 0,
                    "design_task": True,
                }
            )
            return {"ok": True, **details}

        if text in {"Std_Export", "Export"}:
            path = Path(self.export_dir) / f"{document['name']}.step"
            path.write_text("ISO-10303-21; /* fake STEP from workflow harness */\n", encoding="utf-8")
            self.exported_path = str(path)
            details["object_name"] = "Std_Export"
            details["exported_path"] = self.exported_path
            return {"ok": True, **details}

        return {
            "ok": False,
            "failure_code": "UI_TARGET_NOT_UNIQUE",
            "error": f"Expected exactly one action named {text!r}; found 0.",
        }

    def _design_extrude_active(self) -> bool:
        document = self.active_document()
        if document is None:
            return False
        sketch = next(
            (
                obj
                for obj in document["objects"]
                if obj["type_id"] == "Sketcher::SketchObject"
            ),
            None,
        )
        if sketch is None:
            return False
        for item in self.selection:
            if item.get("name") != sketch["name"]:
                continue
            sub = str(item.get("sub") or "")
            if sub.startswith("Edge"):
                return False
            if sub.startswith("InternalFace") or sub == "":
                return True
        return False

    def _click_dialog(self, text: str, details: dict[str, Any]) -> dict[str, Any]:
        visible_titles = []
        if self.recovery_dialog:
            visible_titles.append("Document Recovery")
        if self.orientation_dialog:
            visible_titles.append("Choose Orientation")
        if text in {"Cancel"}:
            if not self.recovery_dialog:
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": f"Expected exactly one visible dialog named {text!r}; found 0.",
                }
            self.recovery_dialog = False
            details["object_name"] = "Document Recovery"
            return {"ok": True, **details}
        if text in {"OK", "Ok"}:
            # Live dialog kind treats OK as matching every visible QDialog.
            if len(visible_titles) != 1:
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": (
                        f"Expected exactly one visible dialog named {text!r}; "
                        f"found {len(visible_titles)}."
                    ),
                }
            if visible_titles[0] == "Document Recovery":
                return {
                    "ok": False,
                    "failure_code": "UI_CLICK_NOT_APPLIED",
                    "error": "Document Recovery OK is Start Recovery.",
                    **details,
                    "semantic_verified": False,
                }
        if text not in {"OK", "Ok", "Choose Orientation"}:
            return {
                "ok": False,
                "failure_code": "UI_TARGET_NOT_UNIQUE",
                "error": f"Expected exactly one visible dialog named {text!r}; found 0.",
            }
        if not self.orientation_dialog:
            return {
                "ok": False,
                "failure_code": "UI_TARGET_NOT_UNIQUE",
                "error": f"Expected exactly one visible dialog named {text!r}; found 0.",
            }
        document = self.active_document()
        if document is None:
            return {
                "ok": False,
                "failure_code": "UI_CLICK_NOT_APPLIED",
                "error": f"Qt click did not activate dialog target {text!r}.",
                **details,
                "semantic_verified": False,
            }
        document["objects"].append(
                {
                    "name": "Sketch",
                    "type_id": "Sketcher::SketchObject",
                    "label": "Sketch",
                    "closed_profile": False,
                    "geometry_count": 0,
                }
        )
        self.selection = [{"name": "Sketch", "sub": ""}]
        self.orientation_dialog = False
        self.sketch_edit = True
        self.selected_ribbon = "Sketch"
        details["object_name"] = "Choose Orientation"
        return {"ok": True, **details}


class _FakeHandler(BaseHTTPRequestHandler):
    server: "FakeAgentServer"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.token}"
        return self.headers.get("Authorization") == expected

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write_json(401, {"ok": False, "failure_code": "UNAUTHORIZED"})
            return
        state = self.server.state
        if self.path == "/v1/status":
            self._write_json(
                200,
                {
                    "ok": True,
                    "channel": "vibecad-agent-control",
                    "gui_up": True,
                    "documents": state.document_summaries(),
                    "exported_path": state.exported_path,
                },
            )
            return
        if self.path == "/v1/documents":
            documents = state.document_summaries()
            self._write_json(
                200,
                {
                    "ok": True,
                    "document_count": len(documents),
                    "documents": documents,
                },
            )
            return
        if self.path == "/v1/ui/ribbon":
            self._write_json(
                200,
                {
                    "ok": True,
                    "selected_text": state.selected_ribbon,
                    "tabs": [
                        {
                            "text": "Model",
                            "index": 0,
                            "enabled": not state.sketch_edit,
                        },
                        {"text": "Sketch", "index": 1, "enabled": True},
                    ],
                },
            )
            return
        if self.path == "/v1/ui/menus":
            self._write_json(
                200,
                {
                    "ok": True,
                    "menus": [
                        {
                            "text": "File",
                            "index": 0,
                            "enabled": True,
                            "visible": True,
                            "menu_visible": False,
                        }
                    ],
                },
            )
            return
        self._write_json(404, {"ok": False, "failure_code": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write_json(401, {"ok": False, "failure_code": "UNAUTHORIZED"})
            return
        body = self._read_json()
        state = self.server.state
        if self.path == "/v1/ui/click":
            self._write_json(200, state.click_payload(body))
            return
        if self.path == "/v1/run":
            self._write_json(200, state.run_python(str(body.get("python") or "")))
            return
        self._write_json(404, {"ok": False, "failure_code": "NOT_FOUND"})


class FakeAgentServer(ThreadingHTTPServer):
    def __init__(self, token: str, state: FakeAgentState) -> None:
        super().__init__(("127.0.0.1", 0), _FakeHandler)
        self.token = token
        self.state = state


def start_fake_channel(
    export_dir: str,
    token: str,
    *,
    recovery_dialog: bool = False,
) -> tuple[FakeAgentServer, str, FakeAgentState]:
    state = FakeAgentState(export_dir, recovery_dialog=recovery_dialog)
    server = FakeAgentServer(token, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}", state
