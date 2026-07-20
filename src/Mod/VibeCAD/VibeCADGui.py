# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD assistant GUI: native dock panel and shared workbench commands.

The panel registers through ``MainWindow.addDockWindow`` (DockWindowManager)
so it gets the same first-class treatment as the Tree and Tasks panels:
the native overlay title bar, overlay-mode eligibility, visibility
persistence, and a View -> Panels entry. No hand-rolled placement code.
"""

from __future__ import annotations

import html
import json
import queue
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADCore import get_service
from VibeCADDebug import list_provider_request_captures
from VibeCADEditState import active_edit_object, active_edit_state
from VibeCADProject import DEFAULT_MODELING_ENGINE
from VibeCADPromptStarters import (
    BUILTIN_PROMPT_STARTERS,
    CATEGORY_ORDER,
    load_custom_prompt_starters,
)
from VibeCADSession import (
    _format_document_delta,
    rebuild_intent_memory,
    run_prompt,
    run_sketch_close_continuation,
)
from VibeCADWorkbenchTools import get_tool_pack


DOCK_NAME = "VibeCADAssistantPanel"
CONTEXT_DEBUG_DOCK_NAME = "VibeCADContextDebugPanel"
MODEL_CODE_DOCK_NAME = "VibeCADScriptedModelPanel"

ICON_MARK = "preferences-vibecad.svg"
ICON_OPEN_ASSISTANT = "vibecad-open-assistant.svg"
ICON_SEND = "vibecad-send.svg"
ICON_STOP = "vibecad-stop.svg"
ICON_ACTIVITY = "vibecad-activity.svg"
ICON_NEW_CONVERSATION = "vibecad-new-conversation.svg"
ICON_PROMPT_STARTERS = "vibecad-prompt-starters.svg"

_commands_registered = False
_preferences_registered = False
_workbench_activation_connected = False
_document_observer_connected = False
_document_observer = None
_gui_document_observer_connected = False
_gui_document_observer = None
_context_debug_startup_scheduled = False
_dock_workbench_manipulator = None
_registered_assistant_widget = None
_registered_context_debug_widget = None
_document_save_conversations: dict[str, dict[str, Any]] = {}
_document_save_references: dict[str, dict[str, Any]] = {}
_pending_question_request: list[dict[str, Any]] = []
_conversation_persist_queue: queue.Queue[tuple[Any, dict[str, Any]]] = queue.Queue()
_conversation_persist_thread: threading.Thread | None = None
_conversation_persist_lock = threading.RLock()

_IDLE_STATUS_TEXT = "Ready. Tell VibeCAD what to make or change."
_PANEL_SPLITTER_PARAMETER = "PanelSplitterState"
_PREFERENCES_PATH = "User parameter:BaseApp/Preferences/VibeCAD"


class _VibeCADDockWorkbenchManipulator:
    """Declare VibeCAD panels through the native workbench dock lifecycle."""

    def modifyDockWindows(self) -> list[dict[str, Any]]:
        try:
            workbench = Gui.activeWorkbench()
            workbench_name = workbench.name() if workbench is not None else ""
        except Exception:
            return []
        if get_tool_pack(workbench_name) is None:
            return []
        docks: list[dict[str, Any]] = [
            {
                "add": DOCK_NAME,
                "area": "right",
                "visible": True,
                "tabbed": True,
            },
            {
                "add": MODEL_CODE_DOCK_NAME,
                "area": "right",
                "visible": False,
                "tabbed": True,
            },
        ]
        if (
            _registered_context_debug_widget is not None
            or _find_context_debug_dock() is not None
        ):
            docks.append(
                {
                    "add": CONTEXT_DEBUG_DOCK_NAME,
                    "area": "bottom",
                    "visible": True,
                    "tabbed": True,
                }
            )
        return docks


def _ensure_dock_workbench_manipulator() -> None:
    global _dock_workbench_manipulator
    if _dock_workbench_manipulator is not None:
        return
    manipulator = _VibeCADDockWorkbenchManipulator()
    Gui.addWorkbenchManipulator(manipulator)
    _dock_workbench_manipulator = manipulator


class _AssistantRunController:
    """Single source of truth for the active GUI-launched provider loop."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._run_id = 0
        self._active = False
        self._cancel_requested = False

    def begin(self) -> int:
        with self._lock:
            self._run_id += 1
            self._active = True
            self._cancel_requested = False
            return self._run_id

    def request_cancel(self) -> bool:
        with self._lock:
            if not self._active:
                return False
            self._cancel_requested = True
            return True

    def finish(self, run_id: int) -> None:
        with self._lock:
            if run_id != self._run_id:
                return
            self._active = False
            self._cancel_requested = False

    def is_cancelled(self, run_id: int) -> bool:
        with self._lock:
            return run_id != self._run_id or self._cancel_requested

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self._run_id,
                "active": self._active,
                "cancel_requested": self._cancel_requested,
            }


class _DocumentThreadCall:
    """One synchronous worker-to-Qt-thread invocation."""

    def __init__(self, operation: Any) -> None:
        self.operation = operation
        self.completed = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None

    def execute(self) -> None:
        try:
            self.result = self.operation()
        except BaseException as exc:
            self.error = exc
        finally:
            self.completed.set()


class _QuestionWaiter:
    """Event-driven bridge between provider worker and the question UI."""

    def __init__(self) -> None:
        self.completed = threading.Event()
        self.answers: list[dict[str, Any]] = []

    def finish(self, answers: list[dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.completed.set()


_assistant_run_controller = _AssistantRunController()
_assistant_run_thread: threading.Thread | None = None
_intent_memory_rebuild_thread: threading.Thread | None = None
_document_thread_invoker: Any | None = None
_pending_question_waiter: _QuestionWaiter | None = None


def _is_intent_memory_rebuild_active() -> bool:
    return bool(
        _intent_memory_rebuild_thread is not None
        and _intent_memory_rebuild_thread.is_alive()
    )


def _ensure_document_thread_invoker() -> Any:
    """Create the queued Qt dispatcher while running on FreeCAD's GUI thread."""
    global _document_thread_invoker
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("The VibeCAD document-thread dispatcher must start on Qt.")
    if _document_thread_invoker is not None:
        return _document_thread_invoker
    from PySide import QtCore

    class _DocumentThreadInvoker(QtCore.QObject):
        requested = QtCore.Signal(object)

        def __init__(self, parent: Any) -> None:
            super().__init__(parent)
            self.requested.connect(self._execute, QtCore.Qt.QueuedConnection)

        @QtCore.Slot(object)
        def _execute(self, request: _DocumentThreadCall) -> None:
            request.execute()

    parent = Gui.getMainWindow()
    if parent is None:
        raise RuntimeError("FreeCAD's main window is unavailable.")
    _document_thread_invoker = _DocumentThreadInvoker(parent)
    return _document_thread_invoker


def _dispatch_to_document_thread(operation: Any) -> Any:
    """Synchronously execute a short GUI/document operation on FreeCAD's thread."""
    if threading.current_thread() is threading.main_thread():
        return operation()
    invoker = _document_thread_invoker
    if invoker is None:
        raise RuntimeError("The VibeCAD document-thread dispatcher is not initialized.")
    request = _DocumentThreadCall(operation)
    invoker.requested.emit(request)
    request.completed.wait()
    if request.error is not None:
        raise request.error
    return request.result


class _SketchCloseContinuationController:
    """Own one exact human-close handoff between provider loops."""

    def __init__(self) -> None:
        self._pending: dict[str, str] | None = None

    def arm(self, event: dict[str, Any]) -> dict[str, str]:
        pending = {
            key: str(event.get(key) or "").strip()
            for key in (
                "document_uid",
                "document_name",
                "sketch_name",
                "sketch_label",
                "owner_body",
            )
        }
        missing = [
            key
            for key in ("document_uid", "document_name", "sketch_name", "owner_body")
            if not pending[key]
        ]
        if missing:
            raise ValueError(
                "Cannot arm sketch continuation without: " + ", ".join(missing) + "."
            )
        pending["type"] = "human_closed_sketch"
        self._pending = pending
        return dict(pending)

    def clear(self) -> None:
        self._pending = None

    def clear_for_document(self, document_uid: str) -> None:
        if self._pending and self._pending.get("document_uid") == document_uid:
            self.clear()

    def consume_reset_edit(self, view_provider: Any) -> dict[str, str] | None:
        pending = self._pending
        if pending is None:
            return None
        obj = getattr(view_provider, "Object", None)
        if obj is None:
            return None
        document = getattr(obj, "Document", None)
        if getattr(obj, "TypeId", "") != "Sketcher::SketchObject":
            return None
        if str(getattr(obj, "Name", "") or "") != pending["sketch_name"]:
            return None
        if str(getattr(document, "Name", "") or "") != pending["document_name"]:
            return None
        if str(getattr(document, "Uid", "") or "") != pending["document_uid"]:
            return None
        self.clear()
        return dict(pending)

    def snapshot(self) -> dict[str, str] | None:
        return dict(self._pending) if self._pending else None


_sketch_close_continuation_controller = _SketchCloseContinuationController()


def _print(message: str) -> None:
    App.Console.PrintMessage(f"{message}\n")


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"{message}\n")


def _icon_path(name: str) -> str:
    return str(Path(__file__).resolve().parent / name)


# ---------------------------------------------------------------------------
# Widget lookup helpers
# ---------------------------------------------------------------------------


def _find_dock():
    try:
        from PySide import QtWidgets
    except Exception:
        return None
    main_window = Gui.getMainWindow()
    if main_window is None:
        return None
    return main_window.findChild(QtWidgets.QDockWidget, DOCK_NAME)


def _find_context_debug_dock():
    try:
        from PySide import QtWidgets
    except Exception:
        return None
    main_window = Gui.getMainWindow()
    if main_window is None:
        return None
    return main_window.findChild(QtWidgets.QDockWidget, CONTEXT_DEBUG_DOCK_NAME)


def _register_dock_content(widget: Any, name: str) -> None:
    """Register panel content without creating or showing a dock window."""
    main_window = Gui.getMainWindow()
    if main_window is None:
        raise RuntimeError("FreeCAD main window is not available.")
    register = getattr(main_window, "registerDockWindow", None)
    if not callable(register):
        raise RuntimeError(
            "FreeCAD main window does not expose "
            "DockWindowManager.registerDockWindow."
        )
    register(widget, name)


def _find_child(widget_type: str, name: str, dock: Any | None = None):
    try:
        from PySide import QtWidgets
    except Exception:
        return None
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return None
    qt_type = getattr(QtWidgets, widget_type, None)
    if qt_type is None:
        return None
    return dock.findChild(qt_type, name)


def _is_assistant_run_active() -> bool:
    return bool(_assistant_run_controller.snapshot()["active"])


def _is_assistant_cancel_requested() -> bool:
    return bool(_assistant_run_controller.snapshot()["cancel_requested"])


# ---------------------------------------------------------------------------
# Exact provider-request debugger
# ---------------------------------------------------------------------------


def _context_debug_settings():
    from VibeCADPreferences import load_debug_settings

    return load_debug_settings()


def _selected_context_debug_path(dock: Any | None = None) -> Path | None:
    if dock is None:
        dock = _find_context_debug_dock()
    selector = _find_child("QComboBox", "VibeContextDebugCapture", dock)
    if selector is None:
        return None
    raw = str(selector.currentData() or "").strip()
    return Path(raw) if raw else None


def _load_selected_context_debug_capture(dock: Any | None = None) -> None:
    if dock is None:
        dock = _find_context_debug_dock()
    if dock is None:
        return
    editor = _find_child("QPlainTextEdit", "VibeContextDebugJson", dock)
    status = _find_child("QLabel", "VibeContextDebugStatus", dock)
    path = _selected_context_debug_path(dock)
    if editor is None or status is None:
        return
    if path is None:
        editor.clear()
        status.setText(
            f"No provider requests captured in "
            f"{_context_debug_settings().resolved_capture_directory}"
        )
        return
    try:
        stat = path.stat()
        signature = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
        if str(editor.property("VibeLoadedCapture") or "") == signature:
            return
        content = path.read_text(encoding="utf-8")
        payload = json.loads(content)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        editor.clear()
        editor.setProperty("VibeLoadedCapture", "")
        status.setText(f"Could not read {path.name}: {exc}")
        return
    editor.setPlainText(content)
    editor.setProperty("VibeLoadedCapture", signature)
    provider = str(payload.get("provider") or "provider").title()
    sdk_call = str(payload.get("sdk_call") or "request")
    turn = payload.get("turn", "?")
    attempt = payload.get("attempt", 1)
    status.setText(
        f"{provider} | turn {turn} | attempt {attempt} | {sdk_call} | "
        f"{stat.st_size:,} bytes | {path}"
    )


def _refresh_context_debug_viewer(dock: Any | None = None) -> None:
    if dock is None:
        dock = _find_context_debug_dock()
    if dock is None:
        return
    selector = _find_child("QComboBox", "VibeContextDebugCapture", dock)
    if selector is None:
        return
    settings = _context_debug_settings()
    paths = list_provider_request_captures(settings.resolved_capture_directory)
    path_texts = [str(path) for path in paths]
    existing = [
        str(selector.itemData(index) or "") for index in range(selector.count())
    ]
    selected = str(selector.currentData() or "")
    if existing != path_texts:
        selector.blockSignals(True)
        selector.clear()
        for path in paths:
            selector.addItem(path.name, str(path))
        if selected in path_texts:
            selector.setCurrentIndex(path_texts.index(selected))
        elif path_texts:
            selector.setCurrentIndex(0)
        selector.blockSignals(False)
    _load_selected_context_debug_capture(dock)


def _open_selected_context_debug_capture() -> None:
    from PySide import QtCore, QtGui

    path = _selected_context_debug_path()
    if path is not None and path.is_file():
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))


def _open_context_debug_capture_folder() -> None:
    from PySide import QtCore, QtGui

    directory = _context_debug_settings().resolved_capture_directory
    directory.mkdir(parents=True, exist_ok=True)
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(directory)))


def _copy_context_debug_json() -> None:
    from PySide import QtWidgets

    dock = _find_context_debug_dock()
    editor = _find_child("QPlainTextEdit", "VibeContextDebugJson", dock)
    application = QtWidgets.QApplication.instance()
    if editor is not None and application is not None:
        application.clipboard().setText(editor.toPlainText())


def _build_context_debug_widget():
    from PySide import QtCore, QtGui, QtWidgets

    root = QtWidgets.QWidget()
    root.setObjectName("VibeContextDebugRoot")
    root.setWindowTitle("VibeCAD Context Debug")
    layout = QtWidgets.QVBoxLayout(root)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)

    controls = QtWidgets.QWidget(root)
    controls.setObjectName("VibeContextDebugControls")
    controls_layout = QtWidgets.QHBoxLayout(controls)
    controls_layout.setContentsMargins(0, 0, 0, 0)
    controls_layout.setSpacing(6)

    selector = QtWidgets.QComboBox(controls)
    selector.setObjectName("VibeContextDebugCapture")
    selector.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    selector.currentIndexChanged.connect(
        lambda _index: _load_selected_context_debug_capture()
    )
    controls_layout.addWidget(selector, 1)

    refresh = QtWidgets.QPushButton("Refresh", controls)
    refresh.setObjectName("VibeContextDebugRefresh")
    refresh.clicked.connect(lambda: _refresh_context_debug_viewer())
    controls_layout.addWidget(refresh)

    copy_json = QtWidgets.QPushButton("Copy JSON", controls)
    copy_json.setObjectName("VibeContextDebugCopy")
    copy_json.clicked.connect(_copy_context_debug_json)
    controls_layout.addWidget(copy_json)

    open_file = QtWidgets.QPushButton("Open File", controls)
    open_file.setObjectName("VibeContextDebugOpenFile")
    open_file.clicked.connect(_open_selected_context_debug_capture)
    controls_layout.addWidget(open_file)

    open_folder = QtWidgets.QPushButton("Open Folder", controls)
    open_folder.setObjectName("VibeContextDebugOpenFolder")
    open_folder.clicked.connect(_open_context_debug_capture_folder)
    controls_layout.addWidget(open_folder)
    layout.addWidget(controls)

    editor = QtWidgets.QPlainTextEdit(root)
    editor.setObjectName("VibeContextDebugJson")
    editor.setReadOnly(True)
    editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    editor.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
    layout.addWidget(editor, 1)

    status = QtWidgets.QLabel(root)
    status.setObjectName("VibeContextDebugStatus")
    status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    status.setWordWrap(True)
    layout.addWidget(status)

    timer = QtCore.QTimer(root)
    timer.setObjectName("VibeContextDebugPollTimer")
    timer.setInterval(1000)
    timer.timeout.connect(lambda: _refresh_context_debug_viewer())
    return root


def _sync_context_debug_polling(dock: Any, visible: bool) -> None:
    from PySide import QtCore

    timer = dock.findChild(QtCore.QTimer, "VibeContextDebugPollTimer")
    enabled = bool(_context_debug_settings().context_debug_enabled)
    if not visible or not enabled:
        if timer is not None:
            timer.stop()
        return
    if timer is not None and not timer.isActive():
        timer.start()
    _refresh_context_debug_viewer(dock)


def _bind_context_debug_dock(dock: Any) -> None:
    if bool(dock.property("VibeContextDebugVisibilityBound")):
        _sync_context_debug_polling(dock, bool(dock.isVisible()))
        return
    dock.visibilityChanged.connect(
        lambda visible, current=dock: _sync_context_debug_polling(
            current, bool(visible)
        )
    )
    dock.setProperty("VibeContextDebugVisibilityBound", True)
    _sync_context_debug_polling(dock, bool(dock.isVisible()))


def _register_startup_context_debugger() -> None:
    """Register enabled debugger content for native workbench setup."""
    global _registered_context_debug_widget
    if not _context_debug_settings().context_debug_enabled:
        return
    if _find_context_debug_dock() is not None:
        return
    if _registered_context_debug_widget is not None:
        return
    widget = _build_context_debug_widget()
    widget.setMinimumWidth(480)
    widget.setMinimumHeight(220)
    _register_dock_content(widget, CONTEXT_DEBUG_DOCK_NAME)
    _registered_context_debug_widget = widget


def _register_context_debug_dock(widget: Any) -> Any:
    main_window = Gui.getMainWindow()
    if main_window is None:
        raise RuntimeError("FreeCAD main window is not available.")
    add_dock_window = getattr(main_window, "addDockWindow", None)
    if not callable(add_dock_window):
        raise RuntimeError(
            "FreeCAD main window does not expose DockWindowManager.addDockWindow."
        )
    dock = add_dock_window(widget, CONTEXT_DEBUG_DOCK_NAME, "bottom")
    dock.toggleViewAction().setVisible(True)
    return dock


def show_context_debugger() -> None:
    settings = _context_debug_settings()
    if not settings.context_debug_enabled:
        _warn("Enable the context debugger in VibeCAD Debug preferences first.")
        return
    dock = _find_context_debug_dock()
    if dock is None and _registered_context_debug_widget is not None:
        # Workbench activation will create the registered native dock. Never
        # create a competing QDockWidget while that transition is in flight.
        return
    if dock is None or dock.widget() is None:
        widget = _build_context_debug_widget()
        if dock is not None:
            dock.setWidget(widget)
        else:
            dock = _register_context_debug_dock(widget)
        dock.setMinimumWidth(480)
        dock.setMinimumHeight(220)
    _bind_context_debug_dock(dock)
    dock.show()
    dock.raise_()


def apply_context_debug_preferences() -> None:
    from PySide import QtCore

    settings = _context_debug_settings()
    dock = _find_context_debug_dock()
    if not settings.context_debug_enabled:
        if dock is not None:
            timer = dock.findChild(QtCore.QTimer, "VibeContextDebugPollTimer")
            if timer is not None:
                timer.stop()
            dock.hide()
        return
    show_context_debugger()


def _apply_startup_context_debug_preferences() -> None:
    global _context_debug_startup_scheduled
    if _context_debug_startup_scheduled:
        return

    _context_debug_startup_scheduled = True
    # InitGui runs before workbench activation. Register content only; the
    # native DockWindowManager owns creation, visibility and placement.
    _register_startup_context_debugger()


# ---------------------------------------------------------------------------
# Conversation rendering
# ---------------------------------------------------------------------------


def _scroll_to_end(edit: Any) -> None:
    try:
        from PySide import QtGui

        edit.moveCursor(QtGui.QTextCursor.End)
    except Exception:
        pass


#: Display width (pixels) for inline conversation thumbnails.
TRANSCRIPT_THUMBNAIL_WIDTH = 160
#: Display width (pixels) for chip tooltip previews.
CHIP_PREVIEW_WIDTH = 256
#: Icon edge (pixels) for reference chip thumbnails.
CHIP_ICON_SIZE = 32


def _image_file_uri(raw_path: str) -> str | None:
    """Return a file:// URI for an existing image file, else None."""
    clean = str(raw_path or "").strip()
    if not clean:
        return None
    try:
        path = Path(clean).expanduser()
        if not path.is_file():
            return None
        return path.resolve().as_uri()
    except (OSError, ValueError):
        return None


def _html_body_fragment(document_html: str) -> str:
    lower = document_html.lower()
    start = lower.find("<body")
    if start < 0:
        return document_html
    start = lower.find(">", start)
    if start < 0:
        return document_html
    end = lower.rfind("</body>")
    if end < 0:
        end = len(document_html)
    return document_html[start + 1 : end]


_MARKDOWN_LIST_MARKER_RE = re.compile(r"^\s{0,3}(?:[-+*]\s+|\d{1,9}[.)]\s+)")


def _normalize_markdown_for_qtext(markdown_text: str) -> str:
    """Add the blank lines Qt Markdown needs around lists in chat prose."""
    lines = (
        str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )
    normalized: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            normalized.append(line)
            continue
        is_list = bool(_MARKDOWN_LIST_MARKER_RE.match(line)) if not in_fence else False
        if is_list and normalized:
            previous = normalized[-1]
            previous_is_list = bool(_MARKDOWN_LIST_MARKER_RE.match(previous))
            if previous.strip() and not previous_is_list:
                normalized.append("")
        elif normalized and stripped and not line.startswith((" ", "\t")):
            previous = normalized[-1]
            if _MARKDOWN_LIST_MARKER_RE.match(previous):
                normalized.append("")
        normalized.append(line)
    return "\n".join(normalized)


def _markdown_fragment_html(markdown_text: str) -> str:
    normalized_markdown = _normalize_markdown_for_qtext(markdown_text)
    from PySide import QtGui

    features = (
        QtGui.QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        | QtGui.QTextDocument.MarkdownFeature.MarkdownNoHTML
    )
    fragment = QtGui.QTextDocumentFragment.fromMarkdown(
        normalized_markdown,
        features,
    )
    return _html_body_fragment(fragment.toHtml())


def _split_transcript_role(text: str) -> tuple[str | None, str]:
    raw = str(text or "")
    first, separator, rest = raw.partition("\n")
    if separator and first.endswith(":") and 1 <= len(first) <= 48:
        return first[:-1], rest
    return None, raw


def _transcript_block_html(text: str, image_paths: list[str] | None = None) -> str:
    """Render one conversation turn as markdown-backed HTML plus thumbnails.

    Missing or unreadable image files degrade to text-only output.
    """
    role, body = _split_transcript_role(str(text))
    parts = ['<div style="margin:0 0 10px 0;">']
    if role:
        escaped_role = re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", role)
        body = f"**{escaped_role}:**\n\n{body}"
    parts.append('<div style="display:block; margin:0;">')
    parts.append(_markdown_fragment_html(body))
    parts.append("</div>")
    for raw in image_paths or []:
        uri = _image_file_uri(raw)
        if uri is None:
            continue
        parts.append(
            f'<p style="margin:6px 0 0 0;"><img src="{html.escape(uri, quote=True)}" '
            f'width="{TRANSCRIPT_THUMBNAIL_WIDTH}"/></p>'
        )
    parts.append("</div>")
    return "".join(parts)


def _turn_image_paths(entry: dict[str, Any]) -> list[str]:
    """Extract attached image paths from a persisted conversation turn."""
    metadata = entry.get("metadata")
    if not isinstance(metadata, dict):
        return []
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return []
    paths: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("type") != "image":
            continue
        path = str(attachment.get("path", "")).strip()
        if path:
            paths.append(path)
    return paths


def _append_transcript_block(output: Any, block_html: str) -> None:
    """Append one HTML block, preserving a blank line between turns."""
    if output.toPlainText().strip():
        output.append("")
    output.append(block_html)


def _append_output(text: str, image_paths: list[str] | None = None) -> None:
    output = _find_child("QTextBrowser", "VibeConversation")
    if output is None:
        _print(text)
        return
    _append_transcript_block(output, _transcript_block_html(text, image_paths))
    _scroll_to_end(output)


def _append_thinking(text: str) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    thinking = _find_child("QPlainTextEdit", "VibeThinking")
    if thinking is None:
        return
    current = thinking.toPlainText().strip()
    merged = clean if not current else f"{current}\n\n{clean}"
    thinking.setPlainText(merged)
    _scroll_to_end(thinking)


def _append_live_delta(text: str) -> None:
    delta = str(text or "")
    if not delta:
        return
    thinking = _find_child("QPlainTextEdit", "VibeThinking")
    if thinking is None:
        return
    from PySide import QtGui

    if not bool(thinking.property("VibeStreamingProviderText")):
        current = thinking.toPlainText().rstrip()
        prefix = "VibeCAD is writing:\n"
        thinking.setPlainText(f"{current}\n\n{prefix}" if current else prefix)
        thinking.setProperty("VibeStreamingProviderText", True)
    cursor = thinking.textCursor()
    cursor.movePosition(QtGui.QTextCursor.End)
    cursor.insertText(delta)
    thinking.setTextCursor(cursor)
    _scroll_to_end(thinking)


def _append_reasoning_delta(text: str) -> None:
    delta = str(text or "")
    if not delta:
        return
    thinking = _find_child("QPlainTextEdit", "VibeThinking")
    if thinking is None:
        return
    from PySide import QtGui

    if not bool(thinking.property("VibeStreamingReasoningText")):
        current = thinking.toPlainText().rstrip()
        prefix = "Reasoning:\n"
        thinking.setPlainText(f"{current}\n\n{prefix}" if current else prefix)
        thinking.setProperty("VibeStreamingReasoningText", True)
    cursor = thinking.textCursor()
    cursor.movePosition(QtGui.QTextCursor.End)
    cursor.insertText(delta)
    thinking.setTextCursor(cursor)
    _scroll_to_end(thinking)


def _clear_thinking(dock: Any | None = None) -> None:
    thinking = _find_child("QPlainTextEdit", "VibeThinking", dock)
    if thinking is None:
        return
    thinking.clear()
    thinking.setProperty("VibeStreamingProviderText", False)
    thinking.setProperty("VibeStreamingReasoningText", False)


def _save_panel_splitter_state(splitter: Any) -> None:
    encoded = bytes(splitter.saveState().toBase64()).decode("ascii")
    App.ParamGet(_PREFERENCES_PATH).SetString(_PANEL_SPLITTER_PARAMETER, encoded)


def _restore_panel_splitter_state(splitter: Any) -> bool:
    from PySide import QtCore

    encoded = App.ParamGet(_PREFERENCES_PATH).GetString(
        _PANEL_SPLITTER_PARAMETER,
        "",
    )
    if not encoded:
        return False
    state = QtCore.QByteArray.fromBase64(encoded.encode("ascii"))
    return bool(splitter.restoreState(state))


def _storage_role_for_conversation(role: str) -> str | None:
    return {
        "User": "user",
        "VibeCAD": "assistant",
        "System": "system",
    }.get(str(role))


def _record_conversation_turn(
    role: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    storage_role = _storage_role_for_conversation(role)
    clean = str(text or "").strip()
    if storage_role is None or not clean:
        return
    try:
        service = get_service()
        prepared = service.prepare_conversation_turn(
            storage_role,
            clean,
            metadata=metadata,
        )
    except Exception as exc:
        _warn(f"VibeCAD conversation save failed: {exc}")
        return
    try:
        _ensure_conversation_persist_thread()
    except Exception as exc:
        _warn(f"VibeCAD conversation save failed: {exc}")
        return
    _conversation_persist_queue.put((service, prepared))


def _ensure_conversation_persist_thread() -> None:
    """Start the single ordered transcript writer without blocking Qt."""

    global _conversation_persist_thread
    with _conversation_persist_lock:
        if (
            _conversation_persist_thread is not None
            and _conversation_persist_thread.is_alive()
        ):
            return
        _ensure_document_thread_invoker()
        _conversation_persist_thread = threading.Thread(
            target=_conversation_persist_loop,
            name="VibeCAD-conversation-persistence",
            daemon=True,
        )
        _conversation_persist_thread.start()


def _conversation_persist_loop() -> None:
    while True:
        service, prepared = _conversation_persist_queue.get()
        try:
            history = service.persist_prepared_conversation_turn(prepared)
            _dispatch_to_document_thread(
                lambda: service.accept_persisted_conversation_turn(history, prepared)
            )
        except Exception as exc:
            message = f"VibeCAD conversation save failed: {exc}"
            try:
                _dispatch_to_document_thread(lambda: _warn(message))
            except Exception:
                pass
        finally:
            _conversation_persist_queue.task_done()


def _format_saved_conversation(conversation: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    labels = {
        "user": "User",
        "assistant": "VibeCAD",
        "system": "System",
    }
    for entry in conversation:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        content = str(entry.get("content", "")).strip()
        label = labels.get(role)
        if label is None or not content:
            continue
        lines.append(f"{label}:\n{content}")
    return "\n\n".join(lines)


def _saved_conversation_blocks(conversation: list[dict[str, Any]]) -> list[str]:
    """Render persisted conversation turns as HTML blocks with thumbnails."""
    labels = {
        "user": "User",
        "assistant": "VibeCAD",
        "system": "System",
    }
    blocks: list[str] = []
    for entry in conversation:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        content = str(entry.get("content", "")).strip()
        label = labels.get(role)
        if label is None or not content:
            continue
        blocks.append(
            _transcript_block_html(f"{label}:\n{content}", _turn_image_paths(entry))
        )
    return blocks


def _render_saved_conversation(dock: Any | None = None) -> None:
    if _is_assistant_run_active():
        return
    output = _find_child("QTextBrowser", "VibeConversation", dock)
    if output is None:
        return
    try:
        history = get_service().conversation_history()
    except Exception as exc:
        _warn(f"VibeCAD conversation load failed: {exc}")
        return
    output.clear()
    for block in _saved_conversation_blocks(history.get("conversation", [])):
        _append_transcript_block(output, block)
    output.setProperty("VibeConversationPath", str(history.get("path", "")))
    _scroll_to_end(output)


def _conversation_selector_label(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "New conversation").strip()
    activity_date = str(item.get("updated_at") or "").strip()[:10]
    return f"{title} - {activity_date}" if activity_date else title


def _refresh_conversation_selector(dock: Any | None = None) -> None:
    try:
        from PySide import QtCore
    except Exception:
        return
    selector = _find_child("QComboBox", "VibeConversationSelector", dock)
    if selector is None:
        return
    try:
        catalog = get_service().conversation_catalog()
    except Exception as exc:
        selector.setEnabled(False)
        selector.setToolTip(f"Conversation history is unavailable: {exc}")
        _warn(f"VibeCAD conversation catalog load failed: {exc}")
        return

    active_id = str(catalog.get("active_conversation_id") or "")
    previous_blocked = selector.blockSignals(True)
    selector.clear()
    active_index = -1
    for item in catalog.get("conversations", []):
        if not isinstance(item, dict):
            continue
        conversation_id = str(item.get("id") or "")
        if not conversation_id:
            continue
        selector.addItem(_conversation_selector_label(item), conversation_id)
        index = selector.count() - 1
        turn_count = int(item.get("turn_count") or 0)
        updated_at = str(item.get("updated_at") or "Unknown activity time")
        selector.setItemData(
            index,
            f"{item.get('title') or 'New conversation'}\n"
            f"{turn_count} messages\nLast activity: {updated_at}",
            QtCore.Qt.ToolTipRole,
        )
        if conversation_id == active_id:
            active_index = index
    if active_index >= 0:
        selector.setCurrentIndex(active_index)
        selector.setToolTip(str(selector.itemData(active_index, QtCore.Qt.ToolTipRole)))
    selector.blockSignals(previous_blocked)


def _refresh_modeling_engine_selector(dock: Any | None = None) -> None:
    selector = _find_child("QComboBox", "VibeModelingEngine", dock)
    if selector is None:
        return
    service = get_service()
    try:
        workbench = service.active_workbench_name()
        state = service.modeling_engine_state(workbench)
        pack = get_tool_pack(workbench)
        active = pack is not None and workbench not in {
            "NoneWorkbench",
            "TestWorkbench",
        }
    except Exception as exc:
        selector.setVisible(False)
        _warn(f"VibeCAD modeling-engine state failed: {exc}")
        return
    selected = str(state.get("selected") or DEFAULT_MODELING_ENGINE)
    build123d_enabled = bool(state.get("build123d_preference_enabled"))
    openscad_enabled = bool(state.get("openscad_preference_enabled"))
    vibescript_enabled = bool(state.get("vibescript_preference_enabled"))
    scripted_engines = {"build123d", "openscad", "vibescript"}
    selector.setVisible(active)
    if not selector.isVisible():
        return

    available = set(state.get("available_engines") or [])
    build_state = dict(state.get("build123d") or {})
    openscad_state = dict(state.get("openscad") or {})
    vibescript_state = dict(state.get("vibescript") or {})
    previous_blocked = selector.blockSignals(True)
    try:
        selector.clear()
        selector.addItem("Native", "native")
        if active and workbench == "PartDesignWorkbench" and "build123d" in available:
            selector.addItem("build123d", "build123d")
        elif selected == "build123d" and workbench != "PartDesignWorkbench":
            selector.addItem("build123d (Part Design only)", "")
            item = selector.model().item(selector.count() - 1)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip(
                    "The automatic change to VibeScript is waiting for the "
                    "active edit session or AI run to finish."
                )
        elif workbench == "PartDesignWorkbench" and (selected == "build123d" or build123d_enabled):
            selector.addItem("build123d unavailable", "")
            item = selector.model().item(selector.count() - 1)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip(str(build_state.get("error") or "Runtime unavailable"))
        if active and workbench == "PartDesignWorkbench" and "openscad" in available:
            selector.addItem("OpenSCAD", "openscad")
        elif selected == "openscad" and workbench != "PartDesignWorkbench":
            selector.addItem("OpenSCAD (Part Design only)", "")
            item = selector.model().item(selector.count() - 1)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip(
                    "The automatic change to VibeScript is waiting for the "
                    "active edit session or AI run to finish."
                )
        elif workbench == "PartDesignWorkbench" and (selected == "openscad" or openscad_enabled):
            selector.addItem("OpenSCAD unavailable", "")
            item = selector.model().item(selector.count() - 1)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip(
                    str(openscad_state.get("error") or "Runtime unavailable")
                )
        if "vibescript" in available:
            selector.addItem("VibeScript", "vibescript")
        elif selected == "vibescript" or vibescript_enabled:
            selector.addItem("VibeScript unavailable", "")
            item = selector.model().item(selector.count() - 1)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip(
                    str(vibescript_state.get("error") or "Engine unavailable")
                )
        index = selector.findData(selected)
        if index >= 0:
            selector.setCurrentIndex(index)
        elif selected in scripted_engines:
            unavailable_text = {
                "build123d": (
                    "build123d unavailable"
                    if workbench == "PartDesignWorkbench"
                    else "build123d (Part Design only)"
                ),
                "openscad": (
                    "OpenSCAD unavailable"
                    if workbench == "PartDesignWorkbench"
                    else "OpenSCAD (Part Design only)"
                ),
                "vibescript": "VibeScript unavailable",
            }[selected]
            unavailable_index = selector.findText(unavailable_text)
            if unavailable_index >= 0:
                selector.setCurrentIndex(unavailable_index)
        selector.setToolTip(
            "Global modeling engine for this saved CAD document. The human "
            "controls this setting; AI tools cannot change it."
        )
    finally:
        selector.blockSignals(previous_blocked)


def _modeling_engine_changed(index: int) -> None:
    dock = _find_dock()
    selector = _find_child("QComboBox", "VibeModelingEngine", dock)
    if selector is None or index < 0:
        return
    engine = str(selector.itemData(index) or "").strip()
    if not engine:
        _refresh_modeling_engine_selector(dock)
        return
    if _is_assistant_run_active():
        _refresh_modeling_engine_selector(dock)
        return
    service = get_service()
    try:
        if engine == service.modeling_engine():
            return
        service.set_modeling_engine(engine)
    except Exception as exc:
        _set_status_line(f"Could not select modeling engine: {exc}", dock=dock)
        _refresh_modeling_engine_selector(dock)
        return
    _set_status_line(f"Modeling engine: {engine}", dock=dock)
    _refresh_modeling_engine_selector(dock)
    try:
        from VibeCADScriptedEditor import (
            refresh_scripted_model_editor,
            show_scripted_model_editor,
        )

        if engine in {"build123d", "openscad", "vibescript"}:
            show_scripted_model_editor()
        else:
            refresh_scripted_model_editor()
    except Exception as exc:
        _warn(f"VibeCAD scripted editor engine refresh failed: {exc}")


def apply_modeling_preferences() -> None:
    """Refresh engine availability after the Preferences page is applied."""
    _refresh_modeling_engine_selector(_find_dock())
    try:
        from VibeCADScriptedEditor import refresh_scripted_model_editor

        refresh_scripted_model_editor()
    except Exception as exc:
        _warn(f"VibeCAD scripted editor preference refresh failed: {exc}")


def _clear_conversation_transients(dock: Any) -> None:
    global _pending_question_request
    _pending_question_request = []
    _cancel_question_round()
    _hide_question_panel(dock)
    _sketch_close_continuation_controller.clear()
    get_service().clear_steering_messages()
    _clear_thinking(dock)
    prompt = _find_child("QPlainTextEdit", "VibePrompt", dock)
    if prompt is not None:
        prompt.clear()


def _activate_conversation_from_selector(index: int) -> None:
    dock = _find_dock()
    selector = _find_child("QComboBox", "VibeConversationSelector", dock)
    if dock is None or selector is None or index < 0:
        return
    if _is_assistant_run_active():
        _refresh_conversation_selector(dock)
        return
    conversation_id = str(selector.itemData(index) or "").strip()
    if not conversation_id:
        return
    try:
        catalog = get_service().conversation_catalog()
        if conversation_id == str(catalog.get("active_conversation_id") or ""):
            return
        get_service().activate_conversation(conversation_id)
        _clear_conversation_transients(dock)
        _render_saved_conversation(dock)
        _refresh_conversation_selector(dock)
        _render_assistant_run_state(dock)
    except Exception as exc:
        _warn(f"VibeCAD conversation switch failed: {exc}")
        _set_status_line(f"Could not open conversation: {exc}", dock=dock)
        _refresh_conversation_selector(dock)


def _new_conversation_from_panel() -> None:
    dock = _find_dock()
    if dock is None or _is_assistant_run_active():
        return
    persistence = _document_persistence_state()
    if not persistence.get("enabled"):
        _render_assistant_run_state(
            dock,
            text=str(
                persistence.get("message")
                or "Save this VibeCAD document to enable VibeCAD."
            ),
        )
        return
    try:
        get_service().create_conversation()
        _clear_conversation_transients(dock)
        _render_saved_conversation(dock)
        _refresh_conversation_selector(dock)
        _render_assistant_run_state(dock)
        prompt = _find_child("QPlainTextEdit", "VibePrompt", dock)
        if prompt is not None:
            prompt.setFocus()
    except Exception as exc:
        _warn(f"VibeCAD new conversation failed: {exc}")
        _set_status_line(f"Could not create conversation: {exc}", dock=dock)


def _append_conversation(
    role: str,
    text: str,
    *,
    persist: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    if role == "AI thinking":
        _append_thinking(clean)
        return
    image_paths = _turn_image_paths({"metadata": metadata}) if metadata else []
    _append_output(f"{role}:\n{clean}", image_paths)
    if persist:
        _record_conversation_turn(role, clean, metadata=metadata)


def _pending_questions() -> list[dict[str, Any]]:
    questions = list(_pending_question_request)
    cleaned: list[dict[str, Any]] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        options: list[dict[str, str]] = []
        for option in item.get("options") or []:
            if isinstance(option, dict):
                answer = str(option.get("answer") or option.get("value") or "").strip()
                label = str(option.get("label") or option.get("text") or "").strip()
                if not answer:
                    answer = label
                if not label:
                    label = answer
            else:
                label = str(option).strip()
                answer = label
            if label and answer:
                options.append({"label": label, "answer": answer})
        cleaned.append(
            {
                "id": str(item.get("id") or f"question_{len(cleaned) + 1}"),
                "question": question,
                "default_answer": str(
                    item.get("recommended_answer") or item.get("default_answer") or ""
                ).strip(),
                "why_it_matters": str(
                    item.get("why_it_matters") or item.get("why") or ""
                ).strip(),
                "options": options,
            }
        )
    return cleaned


def _clear_layout(layout: Any) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif child_layout is not None:
            _clear_layout(child_layout)


def _hide_question_panel(dock: Any | None = None) -> None:
    panel = _find_child("QScrollArea", "VibeQuestionPanel", dock)
    if panel is not None:
        panel.setVisible(False)


def _render_questions(dock: Any | None = None) -> None:
    try:
        from PySide import QtCore, QtWidgets
    except Exception:
        return
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return
    panel = _find_child("QScrollArea", "VibeQuestionPanel", dock)
    body = _find_child("QWidget", "VibeQuestionList", dock)
    if panel is None or body is None:
        return
    layout = body.layout()
    if layout is None:
        return
    _clear_layout(layout)
    questions = _pending_questions()
    if not questions:
        panel.setVisible(False)
        return

    header = QtWidgets.QLabel("Design questions", body)
    header.setObjectName("VibeQuestionHeader")
    layout.addWidget(header)

    for index, question in enumerate(questions):
        card = QtWidgets.QWidget(body)
        card.setObjectName("VibeQuestionCard")
        card.setProperty("question_id", question["id"])
        card.setProperty("question_text", question["question"])
        card.setProperty("default_answer", question["default_answer"])
        card.setProperty("options", question["options"])
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(6)

        label = QtWidgets.QLabel(question["question"], card)
        label.setObjectName("VibeQuestionText")
        label.setWordWrap(True)
        card_layout.addWidget(label)

        if question["why_it_matters"]:
            why = QtWidgets.QLabel(question["why_it_matters"], card)
            why.setObjectName("VibeQuestionWhy")
            why.setWordWrap(True)
            card_layout.addWidget(why)

        group = QtWidgets.QButtonGroup(card)
        group.setExclusive(True)
        default_answer = question["default_answer"]
        checked = False
        for option_index, option in enumerate(question["options"]):
            label = str(option.get("label") or "").strip()
            answer = str(option.get("answer") or label).strip()
            radio = QtWidgets.QRadioButton(label, card)
            radio.setObjectName(f"VibeQuestionOption_{index}_{option_index}")
            radio.setProperty("answer_text", answer)
            group.addButton(radio)
            if default_answer and (
                answer.casefold() == default_answer.casefold()
                or label.casefold() == default_answer.casefold()
            ):
                radio.setChecked(True)
                checked = True
            card_layout.addWidget(radio)
        if question["options"] and not checked and group.buttons():
            group.buttons()[0].setChecked(True)

        custom = QtWidgets.QLineEdit(card)
        custom.setObjectName(f"VibeQuestionCustom_{index}")
        custom.setPlaceholderText(
            "Custom answer"
            + (f" (default: {default_answer})" if default_answer else "")
        )
        custom.returnPressed.connect(_submit_question_answers)
        card_layout.addWidget(custom)
        layout.addWidget(card)

    submit = QtWidgets.QPushButton("Submit Answers", body)
    submit.setObjectName("VibeQuestionSubmit")
    submit.clicked.connect(_submit_question_answers)
    layout.addWidget(submit)
    layout.addStretch(1)
    panel.setMinimumHeight(min(280, 72 + len(questions) * 112))
    panel.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    panel.setVisible(True)


def _collect_question_answers(dock: Any | None = None) -> list[dict[str, Any]]:
    try:
        from PySide import QtWidgets
    except Exception:
        return []
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return []
    panel = _find_child("QScrollArea", "VibeQuestionPanel", dock)
    if panel is None:
        return []
    answers: list[dict[str, Any]] = []
    for card in panel.findChildren(QtWidgets.QWidget, "VibeQuestionCard"):
        question_id = str(card.property("question_id") or "").strip()
        question = str(card.property("question_text") or "").strip()
        default_answer = str(card.property("default_answer") or "").strip()
        options = card.property("options") or []
        custom = card.findChild(QtWidgets.QLineEdit)
        custom_answer = str(custom.text() if custom is not None else "").strip()
        selected = ""
        for radio in card.findChildren(QtWidgets.QRadioButton):
            if radio.isChecked():
                selected = str(radio.property("answer_text") or radio.text()).strip()
                break
        answer = custom_answer or selected or default_answer
        if question and answer:
            answers.append(
                {
                    "id": question_id,
                    "question": question,
                    "answer": answer,
                    "source": "custom" if custom_answer else "choice",
                    "options": list(options) if isinstance(options, list) else [],
                    "default_answer": default_answer,
                }
            )
    return answers


def _submit_question_answers() -> None:
    global _pending_question_request, _pending_question_waiter
    dock = _find_dock()
    if dock is None:
        return
    waiter = _pending_question_waiter
    if waiter is None:
        _hide_question_panel(dock)
        return
    answers = _collect_question_answers(dock)
    if not answers:
        _set_status_line("Answer at least one design question.", dock=dock)
        return
    _pending_question_request = []
    _pending_question_waiter = None
    _hide_question_panel(dock)
    waiter.finish(answers)


def _begin_question_round(
    questions: list[dict[str, Any]],
    waiter: _QuestionWaiter,
) -> None:
    global _pending_question_request, _pending_question_waiter
    if _pending_question_waiter is not None:
        raise RuntimeError("Another VibeCAD question round is already active.")
    dock = _find_dock()
    if dock is None:
        raise RuntimeError("The VibeCAD panel is not open.")
    _pending_question_request = list(questions)
    _pending_question_waiter = waiter
    _render_questions(dock)
    _set_status_line("VibeCAD needs design input.", dock=dock)


def _cancel_question_round(waiter: _QuestionWaiter | None = None) -> None:
    global _pending_question_request, _pending_question_waiter
    active = _pending_question_waiter
    if active is None or (waiter is not None and active is not waiter):
        return
    _pending_question_request = []
    _pending_question_waiter = None
    _hide_question_panel()
    active.finish([])


def _request_user_answers(
    questions: list[dict[str, Any]],
    cancellation_check: Any,
) -> list[dict[str, Any]]:
    waiter = _QuestionWaiter()
    _dispatch_to_document_thread(lambda: _begin_question_round(questions, waiter))
    while not waiter.completed.wait(0.1):
        if cancellation_check():
            _dispatch_to_document_thread(lambda: _cancel_question_round(waiter))
            return []
    answers = list(waiter.answers)
    if not answers:
        return []
    lines = [f"{item['question']}\nAnswer: {item['answer']}" for item in answers]
    _dispatch_to_document_thread(
        lambda: _append_conversation(
            "User",
            "\n\n".join(lines),
            persist=True,
            metadata={"source": "model_questions"},
        )
    )
    return answers


# ---------------------------------------------------------------------------
# Status + progress rendering
# ---------------------------------------------------------------------------


def _set_status_line(text: str, *, dock: Any | None = None) -> None:
    label = _find_child("QLabel", "VibeStatusLine", dock)
    if label is None:
        return
    clean = str(text or "").strip()
    label.setText(clean)
    label.setVisible(bool(clean) and clean != _IDLE_STATUS_TEXT)


#: Failure stages where the tool call was rejected before touching the document.
_PRE_EXECUTION_FAILURE_STAGES = frozenset(
    {"schema", "surface", "edit_state", "precondition"}
)

#: Failure stages where the tool call executed and the transaction rolled back.
_ROLLED_BACK_FAILURE_STAGES = frozenset(
    {"native_call", "native_recompute", "postcondition"}
)


def _failure_status_text(failure_stage: Any) -> str:
    """Human-readable failure status derived from a tool failure_stage.

    Missing or unrecognized stages degrade to the generic "blocked" so the
    transcript never breaks on payloads without stage reporting.
    """
    stage = str(failure_stage or "").strip()
    if stage in _PRE_EXECUTION_FAILURE_STAGES:
        return f"rejected before execution ({stage})"
    if stage in _ROLLED_BACK_FAILURE_STAGES:
        return f"failed during execution, rolled back ({stage})"
    if stage == "external_process":
        return "failed in external process, document unchanged"
    return "blocked"


def _format_progress_event(event: dict[str, Any]) -> str:
    name = str(event.get("event", "progress"))
    if name == "context_build_started":
        return "Looking at the current VibeCAD document..."
    if name == "context_build_completed":
        return "I have the document context."
    if name == "provider_subprocess_started":
        return f"{event.get('provider', 'Provider')} process started" + (
            f" | pid {event.get('pid')}" if event.get("pid") else ""
        )
    if name == "provider_waiting":
        return (
            f"Waiting on {event.get('provider', 'provider')} response..."
            f" | idle {float(event.get('idle_seconds', 0) or 0):.1f}s"
            f" | total {float(event.get('elapsed_seconds', 0) or 0):.1f}s"
        )
    if name == "provider_turn_started":
        base = "Thinking about the next CAD move..."
        delta = _format_document_delta(event.get("document_delta"))
        if delta and not delta.startswith("not available"):
            return f"{base} | {delta}"
        return base
    if name == "provider_turn_completed":
        return "CAD step completed."
    if name == "provider_turn_output":
        return f"VibeCAD wrote turn {event.get('turn', '?')}."
    if name == "intent_memory_update_started":
        return f"Updating Intent Memory | {event.get('turn_count', 0)} uncovered turns"
    if name == "intent_memory_update_completed":
        return "Intent Memory updated."
    if name == "intent_memory_update_failed":
        return (
            "Intent Memory update failed; uncovered turns were retained"
            f" | {event.get('error', 'unknown error')}"
        )
    if name == "provider_text_delta":
        return ""
    if name == "provider_turn_failed":
        return (
            f"Provider turn {event.get('turn', '?')} failed: "
            f"{event.get('error', 'unknown error')}"
        )
    if name == "provider_total_timeout":
        return (
            f"Autonomous loop reached {event.get('elapsed_seconds', 0):.1f}s | "
            f"tools: {event.get('tool_count', 0)}"
        )
    if name == "provider_run_cancelled":
        return "Run stopped by user."
    if name == "human_steering_consumed":
        return "Applied your latest correction."
    if name == "document_recompute_waiting":
        elapsed = float(event.get("elapsed_seconds", 0.0) or 0.0)
        return f"Waiting for FreeCAD to finish recomputing... | {elapsed:.1f}s"
    if name == "geometry_worker_started":
        return "Measuring geometry outside the FreeCAD UI process..."
    if name == "tool_workspace_handoff_reached":
        workbench = str(event.get("active_workbench") or "").strip()
        return f"Workspace active: {workbench}" if workbench else "Workspace changed."
    if name == "anthropic_request_started":
        thinking = event.get("thinking")
        if isinstance(thinking, dict) and thinking.get("budget_tokens"):
            thinking_text = f", thinking {thinking['budget_tokens']} tokens"
        elif isinstance(thinking, dict) and thinking.get("type"):
            thinking_text = f", thinking {thinking['type']}"
        else:
            thinking_text = ""
        return (
            f"Anthropic request sent: turn {event.get('turn', '?')}, "
            f"{event.get('message_count', 0)} messages, "
            f"{event.get('tool_count', 0)} tools{thinking_text}"
        )
    if name == "anthropic_stream_retrying":
        return (
            f"Anthropic stream interrupted; retry "
            f"{event.get('next_attempt', '?')}/"
            f"{event.get('max_attempts', 3)}."
        )
    if name == "anthropic_stream_waiting":
        return f"Anthropic stream opened: waiting for turn {event.get('turn', '?')}."
    if name == "anthropic_stream_event":
        stream_type = str(event.get("stream_event_type") or "event")
        if stream_type == "content_block_start":
            block = str(event.get("block_type") or "block")
            tool = event.get("tool_name")
            return f"Anthropic stream: started {block}" + (f" {tool}" if tool else "")
        if stream_type == "content_block_stop":
            return "Anthropic stream: finished content block."
        if stream_type == "message_delta" and event.get("stop_reason"):
            return f"Anthropic stream: stop reason {event['stop_reason']}."
        if stream_type == "message_stop":
            return "Anthropic stream: message complete."
        if event.get("delta_type"):
            return f"Anthropic stream: receiving {event['delta_type']}."
        return f"Anthropic stream: {stream_type}."
    if name == "anthropic_stream_completed":
        return f"Anthropic stream completed: {event.get('event_count', 0)} events."
    if name == "anthropic_response_received":
        counts = event.get("block_counts")
        if isinstance(counts, dict) and counts:
            blocks = ", ".join(
                f"{key}={value}" for key, value in sorted(counts.items())
            )
        else:
            blocks = "no content blocks"
        tools = event.get("tool_names")
        tool_text = ""
        if isinstance(tools, list) and tools:
            joined = ", ".join(str(tool) for tool in tools[:4])
            remaining = int(event.get("tool_name_count", len(tools)) or len(tools))
            suffix = f" +{remaining - 4}" if remaining > 4 else ""
            tool_text = f" | wants {joined}{suffix}"
        return (
            f"Anthropic response: stop={event.get('stop_reason', 'unknown')}; "
            f"{blocks}{tool_text}"
        )
    if name == "provider_web_search_started":
        return f"{event.get('provider', 'Provider')} started web research."
    if name == "provider_web_search_completed":
        query = str(event.get("query") or "").strip()
        return "Web research completed" + (f": {query}" if query else ".")
    if name == "design_review_started":
        return "Independent design review started."
    if name == "design_review_completed":
        verdict = str(event.get("verdict") or "completed")
        count = int(event.get("finding_count", 0) or 0)
        return f"Independent design review: {verdict} | {count} findings."
    if name == "design_review_failed":
        return f"Independent design review failed: {event.get('error', 'unknown error')}"
    if name == "provider_tool_requested":
        arguments = event.get("arguments")
        arg_text = ""
        if isinstance(arguments, dict):
            keys = arguments.get("keys")
            if isinstance(keys, list) and keys:
                arg_text = " | args: " + ", ".join(str(key) for key in keys[:6])
            elif arguments.get("key_count") == 0:
                arg_text = " | args: none"
            elif arguments.get("valid_json") is False:
                arg_text = " | args: invalid JSON"
        tool_kind = "skill" if event.get("tool_kind") == "skill" else "CAD tool"
        return (
            f"{event.get('provider', 'Provider')} requested {tool_kind}: "
            f"{event.get('tool_name', 'unknown')}{arg_text}"
        )
    if name == "provider_tool_result_sent":
        status = (
            "ok"
            if event.get("ok")
            else _failure_status_text(event.get("failure_stage"))
        )
        detail = f" | {event.get('error')}" if event.get("error") else ""
        tool_kind = "skill" if event.get("tool_kind") == "skill" else "CAD tool"
        return (
            f"Provider received {tool_kind} result: "
            f"{event.get('tool_name', 'unknown')} {status}{detail}"
        )
    if name == "tool_call_completed":
        result = (
            event.get("result", {}) if isinstance(event.get("result"), dict) else {}
        )
        status = (
            "ok"
            if event.get("ok")
            else _failure_status_text(result.get("failure_stage"))
        )
        if result.get("title"):
            return f"CAD action {status}: {result['title']}"
        if result.get("error"):
            return f"CAD action {status}: {result['error']}"
        return f"CAD action {status}: {event.get('tool_name', 'unknown')}"
    return name.replace("_", " ")


_PROGRESS_THINKING_EVENTS = {
    "provider_tool_requested",
    "provider_web_search_started",
    "provider_web_search_completed",
    "design_review_started",
    "design_review_completed",
    "design_review_failed",
    "tool_call_completed",
    "provider_turn_failed",
    "human_steering_consumed",
    "anthropic_stream_retrying",
}

_PROGRESS_STATUS_ONLY_EVENTS: set[str] = {
    "document_recompute_waiting",
    "geometry_worker_started",
    "intent_memory_update_started",
    "intent_memory_update_completed",
    "intent_memory_update_failed",
}


def _progress_event_should_update_status(event: dict[str, Any]) -> bool:
    name = str(event.get("event", "progress"))
    return name in _PROGRESS_STATUS_ONLY_EVENTS


def _progress_event_should_append_thinking(event: dict[str, Any]) -> bool:
    return str(event.get("event", "progress")) in _PROGRESS_THINKING_EVENTS


def _handle_progress_event(
    dock: Any,
    event: dict[str, Any],
) -> None:
    event_name = str(event.get("event") or "")
    if event_name in {
        "scripted_model_update_started",
        "scripted_model_update_finished",
    }:
        try:
            from VibeCADScriptedEditor import (
                automated_model_update_finished,
                automated_model_update_started,
            )

            arguments = (
                str(event.get("engine") or ""),
                str(event.get("document_name") or ""),
                str(event.get("model_id") or ""),
            )
            if event_name == "scripted_model_update_started":
                automated_model_update_started(*arguments)
            else:
                automated_model_update_finished(*arguments)
        except Exception as exc:
            _warn(f"VibeCAD scripted editor synchronization failed: {exc}")
        return
    if event.get("event") == "provider_text_delta":
        _append_live_delta(str(event.get("text") or ""))
        return
    if event.get("event") == "provider_reasoning_delta":
        _append_reasoning_delta(str(event.get("text") or ""))
        return
    text = _format_progress_event(event)
    if not text:
        return
    if _progress_event_should_update_status(event):
        _set_status_line(text, dock=dock)
    if _progress_event_should_append_thinking(event):
        _append_thinking(text)


def _set_view_status(summary: dict[str, Any]) -> None:
    status = _find_child("QLabel", "VibeViewStatus")
    if status is None:
        return
    if summary.get("captured"):
        size = summary.get("size") or ["?", "?"]
        text = f"View attached: {size[0]}x{size[1]} | {summary.get('camera_type', 'camera')}"
    elif summary.get("error"):
        text = f"View not attached: {summary['error']}"
    else:
        text = ""
    status.setText(text)
    status.setVisible(bool(text))


def _require_saved_document(dock: Any | None = None) -> bool:
    persistence = _document_persistence_state()
    if persistence.get("enabled"):
        return True
    if dock is None:
        dock = _find_dock()
    message = str(
        persistence.get("message") or "Save this VibeCAD document to enable VibeCAD."
    )
    if dock is not None:
        _render_assistant_run_state(dock, text=message)
    else:
        _set_status_line(message)
    return False


def _capture_view_from_panel() -> None:
    if not _require_saved_document():
        return
    summary = get_service().capture_view_screenshot()
    _set_view_status(summary)
    if summary.get("captured"):
        _append_conversation(
            "AI thinking",
            "Attached viewport screenshot: "
            f"{summary.get('size', ['?', '?'])} {summary.get('camera_type', 'camera')}",
        )
    else:
        _append_conversation(
            "VibeCAD",
            f"Viewport screenshot failed: {summary.get('error', 'unknown error')}",
        )


# ---------------------------------------------------------------------------
# Reference images (user-supplied targets)
# ---------------------------------------------------------------------------


def _chip_thumbnail_icon(path: str) -> Any | None:
    """Build a QIcon thumbnail for a reference chip; None when unavailable."""
    try:
        from PySide import QtCore, QtGui
    except Exception:
        return None
    clean = str(path or "").strip()
    if not clean or not Path(clean).expanduser().is_file():
        return None
    try:
        pixmap = QtGui.QPixmap(clean)
        if pixmap.isNull():
            return None
        scaled = pixmap.scaled(
            CHIP_ICON_SIZE,
            CHIP_ICON_SIZE,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        return QtGui.QIcon(scaled)
    except Exception:
        return None


def _chip_tooltip(name: str, path: str) -> str:
    """Tooltip with a larger inline preview; text-only when the file is gone."""
    uri = _image_file_uri(path)
    text = f"Reference image: {html.escape(name)}<br/>Click to remove."
    if uri is None:
        return f"<p>{text}</p>"
    return (
        f"<p>{text}</p>"
        f'<p><img src="{html.escape(uri, quote=True)}" '
        f'width="{CHIP_PREVIEW_WIDTH}"/></p>'
    )


def _refresh_reference_chips(dock: Any | None = None) -> None:
    """Rebuild the removable reference-image chips row from service state."""
    try:
        from PySide import QtCore, QtWidgets
    except Exception:
        return
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return
    row = _find_child("QWidget", "VibeReferenceChips", dock)
    if row is None:
        return
    layout = row.layout()
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
    if not _document_persistence_state().get("enabled"):
        row.setVisible(False)
        return
    try:
        summary = get_service().reference_images_summary()
    except Exception as exc:
        _warn(f"VibeCAD reference image summary failed: {exc}")
        summary = {"count": 0, "images": []}
    images = summary.get("images") or []
    for entry in images:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "image"))
        reference_id = str(entry.get("id", ""))
        stored_path = str(entry.get("path", ""))
        chip = QtWidgets.QPushButton(f"{name}  \u2715", row)
        chip.setObjectName(f"VibeReferenceChip_{reference_id}")
        chip.setProperty("VibeReferenceId", reference_id)
        icon = _chip_thumbnail_icon(stored_path)
        if icon is not None:
            chip.setIcon(icon)
            chip.setIconSize(QtCore.QSize(CHIP_ICON_SIZE, CHIP_ICON_SIZE))
        chip.setToolTip(_chip_tooltip(name, stored_path))
        chip.clicked.connect(
            lambda checked=False, rid=reference_id: _remove_reference_from_panel(rid)
        )
        layout.addWidget(chip)
    if images:
        layout.addStretch(1)
    row.setVisible(bool(images))


def _remove_reference_from_panel(reference_id: str) -> None:
    try:
        result = get_service().remove_reference_image(reference_id)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    if result.get("ok"):
        removed = result.get("removed") or {}
        _set_status_line(
            f"Removed reference image: {removed.get('name', reference_id)}."
        )
    else:
        _set_status_line(str(result.get("error", "Could not remove reference image.")))
    _refresh_reference_chips()


def _attach_reference_paths(paths: list[str], *, source: str) -> None:
    """Attach each path via the service; report failures without raising."""
    if not _require_saved_document():
        return
    if _is_assistant_run_active():
        _set_status_line("Cannot attach reference images while a run is active.")
        return
    attached = 0
    for path in paths:
        try:
            result = get_service().attach_reference_image(path)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if result.get("ok"):
            attached += 1
            reference = result.get("reference") or {}
            stored_path = str(reference.get("path", "")).strip()
            metadata: dict[str, Any] | None = None
            if stored_path:
                metadata = {
                    "attachments": [
                        {
                            "type": "image",
                            "path": stored_path,
                            "name": str(reference.get("name", "")),
                            "reference_id": str(reference.get("id", "")),
                        }
                    ]
                }
            _append_conversation(
                "System",
                f"Attached reference image: {reference.get('name', path)}",
                persist=True,
                metadata=metadata,
            )
        else:
            _append_conversation(
                "VibeCAD",
                f"Reference image not attached: {result.get('error', 'unknown error')}",
            )
    if attached:
        noun = "image" if attached == 1 else "images"
        _set_status_line(f"Attached {attached} reference {noun} ({source}).")
    _refresh_reference_chips()


def _attach_image_from_panel() -> None:
    try:
        from PySide import QtWidgets
    except Exception:
        return
    if not _require_saved_document():
        return
    if _is_assistant_run_active():
        _set_status_line("Cannot attach reference images while a run is active.")
        return
    dock = _find_dock()
    paths, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
        dock,
        "Attach reference images",
        "",
        "Images (*.png *.jpg *.jpeg *.webp)",
    )
    if not paths:
        return
    _attach_reference_paths([str(path) for path in paths], source="file dialog")


def _paste_clipboard_reference() -> bool:
    """Attach a clipboard image as a reference. True if the clipboard held one."""
    try:
        from PySide import QtWidgets
    except Exception:
        return False
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    clipboard = app.clipboard()
    mime = clipboard.mimeData()
    if mime is None or not mime.hasImage():
        return False
    image = clipboard.image()
    if image is None or image.isNull():
        return False
    if not _require_saved_document():
        return True
    if _is_assistant_run_active():
        _set_status_line("Cannot attach reference images while a run is active.")
        return True
    target = Path(tempfile.gettempdir()) / f"vibecad-paste-{uuid.uuid4().hex[:8]}.png"
    try:
        saved = bool(image.save(str(target), "PNG"))
    except Exception as exc:
        _set_status_line(f"Could not save pasted image: {exc}")
        return True
    if not saved:
        _set_status_line("Could not save pasted image.")
        return True
    _attach_reference_paths([str(target)], source="clipboard")
    try:
        target.unlink()
    except OSError:
        pass
    return True


def _install_prompt_paste_filter(prompt: Any) -> None:
    """Intercept Ctrl+V on the prompt box when the clipboard holds an image."""
    try:
        from PySide import QtCore, QtGui
    except Exception:
        return

    class _PasteFilter(QtCore.QObject):
        def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802 (Qt API)
            try:
                if event.type() == QtCore.QEvent.KeyPress and event.matches(
                    QtGui.QKeySequence.Paste
                ):
                    if _paste_clipboard_reference():
                        return True
            except Exception as exc:
                _warn(f"VibeCAD paste handling failed: {exc}")
            return False

    paste_filter = _PasteFilter(prompt)
    prompt.installEventFilter(paste_filter)
    prompt.setProperty("VibePasteFilterInstalled", True)


def _insert_prompt_starter(prompt: Any, content: str) -> None:
    """Insert editable starter text at the current composer selection."""
    from PySide import QtGui

    if prompt is None or prompt.isReadOnly():
        return
    clean = str(content or "").strip()
    if not clean:
        return

    cursor = prompt.textCursor()
    existing = prompt.toPlainText()
    selection_start = cursor.selectionStart()
    selection_end = cursor.selectionEnd()
    before = existing[:selection_start]
    after = existing[selection_end:]
    if not before or before.endswith("\n\n"):
        prefix = ""
    elif before.endswith("\n"):
        prefix = "\n"
    else:
        prefix = "\n\n"
    if not after or after.startswith("\n\n"):
        suffix = ""
    elif after.startswith("\n"):
        suffix = "\n"
    else:
        suffix = "\n\n"

    inserted_start = selection_start + len(prefix)
    cursor.insertText(f"{prefix}{clean}{suffix}")
    placeholder = re.search(r"\[[^\]\n]+\]", clean)
    if placeholder is not None:
        cursor.setPosition(inserted_start + placeholder.start())
        cursor.setPosition(
            inserted_start + placeholder.end(),
            QtGui.QTextCursor.MoveMode.KeepAnchor,
        )
    prompt.setTextCursor(cursor)
    prompt.setFocus()


def _show_prompt_starter_preferences() -> None:
    Gui.showPreferencesByName("VibeCAD", "Prompt Starters")


def _populate_prompt_starter_menu(menu: Any, prompt: Any) -> None:
    menu.clear()
    starters = list(BUILTIN_PROMPT_STARTERS)
    custom_error = ""
    try:
        starters.extend(load_custom_prompt_starters())
    except Exception as exc:
        custom_error = str(exc)

    for category in CATEGORY_ORDER:
        category_starters = sorted(
            (starter for starter in starters if starter.category == category),
            key=lambda starter: (not starter.builtin, starter.name.casefold()),
        )
        if not category_starters:
            continue
        category_menu = menu.addMenu(category)
        for starter in category_starters:
            action = category_menu.addAction(starter.name)
            action.setToolTip(
                "Built-in prompt starter" if starter.builtin else "Custom prompt starter"
            )
            action.triggered.connect(
                lambda _checked=False, text=starter.content: _insert_prompt_starter(
                    prompt, text
                )
            )

    if custom_error:
        menu.addSeparator()
        error_action = menu.addAction("Custom starters unavailable")
        error_action.setEnabled(False)
        error_action.setToolTip(custom_error)
    menu.addSeparator()
    manage_action = menu.addAction("Manage Prompt Starters...")
    manage_action.triggered.connect(_show_prompt_starter_preferences)


# ---------------------------------------------------------------------------
# Run / stop / steering
# ---------------------------------------------------------------------------


def _document_persistence_state() -> dict[str, Any]:
    try:
        return get_service().document_persistence_state()
    except Exception as exc:
        return {
            "enabled": False,
            "reason": "state_unavailable",
            "message": f"VibeCAD cannot determine the document save state: {exc}",
        }


def rebuild_intent_memory_async() -> dict[str, Any]:
    """Start a non-blocking full Intent Memory rebuild for the active project."""
    global _intent_memory_rebuild_thread
    if _is_assistant_run_active():
        return {"started": False, "error": "Wait for the active CAD run to finish."}
    if (
        _intent_memory_rebuild_thread is not None
        and _intent_memory_rebuild_thread.is_alive()
    ):
        return {"started": False, "error": "Intent Memory rebuild is already running."}
    persistence = _document_persistence_state()
    if not persistence.get("enabled"):
        return {
            "started": False,
            "error": str(
                persistence.get("message")
                or "Save the active document before rebuilding Intent Memory."
            ),
        }
    service = get_service()
    _set_status_line("Rebuilding Intent Memory...")

    def progress(event: dict[str, Any]) -> None:
        copy = dict(event)
        _dispatch_to_document_thread(lambda: _handle_progress_event(_find_dock(), copy))

    def worker() -> None:
        global _intent_memory_rebuild_thread
        try:
            result = rebuild_intent_memory(
                service=service,
                progress_callback=progress,
                document_thread_dispatch=_dispatch_to_document_thread,
            )
        except Exception as exc:
            message = f"Intent Memory rebuild failed; existing memory preserved | {exc}"
        else:
            if result.get("changed"):
                message = (
                    f"Intent Memory rebuilt | {result.get('entry_count', 0)} entries"
                )
            else:
                message = "Intent Memory has no conversation turns to compile."
        finally:
            _intent_memory_rebuild_thread = None
        _dispatch_to_document_thread(lambda: _set_status_line(message))

    _intent_memory_rebuild_thread = threading.Thread(
        target=worker,
        name="VibeCADIntentMemoryRebuild",
        daemon=True,
    )
    _intent_memory_rebuild_thread.start()
    return {"started": True}


def _render_assistant_run_state(dock: Any, text: str | None = None) -> None:
    if dock is None:
        return
    busy = _is_assistant_run_active()
    persistence = _document_persistence_state()
    document_ready = bool(persistence.get("enabled"))
    pending_sketch = _sketch_close_continuation_controller.snapshot()
    dock.setProperty("VibeRunActive", busy)
    dock.setProperty("VibeCancelRequested", _is_assistant_cancel_requested())
    dock.setProperty("VibeDocumentReady", document_ready)

    send_button = _find_child("QPushButton", "VibeSend", dock)
    stop_button = _find_child("QPushButton", "VibeStop", dock)
    prompt_box = _find_child("QPlainTextEdit", "VibePrompt", dock)
    attach_button = _find_child("QPushButton", "VibeAttachView", dock)
    attach_image_button = _find_child("QPushButton", "VibeAttachImage", dock)
    reference_chips = _find_child("QWidget", "VibeReferenceChips", dock)
    conversation_selector = _find_child("QComboBox", "VibeConversationSelector", dock)
    new_conversation = _find_child("QToolButton", "VibeNewConversation", dock)
    prompt_starters = _find_child("QToolButton", "VibePromptStarters", dock)
    engine_selector = _find_child("QComboBox", "VibeModelingEngine", dock)

    if send_button is not None:
        send_button.setEnabled(busy or document_ready)
        send_button.setText("Steer" if busy else "Send")
    if stop_button is not None:
        stop_button.setEnabled(busy)
    if attach_button is not None:
        attach_button.setEnabled(document_ready and not busy)
    if attach_image_button is not None:
        attach_image_button.setEnabled(document_ready and not busy)
    if reference_chips is not None:
        reference_chips.setEnabled(document_ready and not busy)
    if conversation_selector is not None:
        conversation_selector.setEnabled(document_ready and not busy)
    if new_conversation is not None:
        new_conversation.setEnabled(document_ready and not busy)
    if prompt_starters is not None:
        prompt_starters.setEnabled(document_ready and not busy)
    if engine_selector is not None:
        engine_selector.setEnabled(document_ready and not busy)
    if prompt_box is not None:
        prompt_box.setReadOnly(not busy and not document_ready)
        if busy:
            placeholder = "Steer the current CAD run..."
        elif document_ready:
            placeholder = "Message VibeCAD..."
        else:
            placeholder = str(
                persistence.get("message")
                or "Save this VibeCAD document to enable VibeCAD."
            )
        prompt_box.setPlaceholderText(placeholder)
    if busy:
        status_text = text or ""
    elif not document_ready:
        status_text = str(
            persistence.get("message")
            or "Save this VibeCAD document to enable VibeCAD."
        )
    else:
        if text:
            status_text = text
        elif pending_sketch:
            sketch_label = (
                pending_sketch.get("sketch_label")
                or pending_sketch.get("sketch_name")
                or "the sketch"
            )
            status_text = f"Close {sketch_label} to continue automatically."
        else:
            status_text = _IDLE_STATUS_TEXT
    _set_status_line(status_text, dock=dock)


def _stop_prompt_from_panel() -> None:
    dock = _find_dock()
    if dock is None:
        return
    if not _is_assistant_run_active():
        _render_assistant_run_state(dock)
        return
    _assistant_run_controller.request_cancel()
    _cancel_question_round()
    _render_assistant_run_state(
        dock, text="Stopping after the current provider/tool step..."
    )
    _append_conversation("User", "Stop.", persist=True, metadata={"source": "stop"})
    _append_conversation(
        "AI thinking", "Stopping after the current provider/tool step."
    )


def _active_edit_sketch_continuation_event() -> dict[str, str] | None:
    gui_document = getattr(Gui, "ActiveDocument", None)
    edit_object = active_edit_object(gui_document)
    if getattr(edit_object, "TypeId", "") != "Sketcher::SketchObject":
        return None
    document = getattr(edit_object, "Document", None)
    if document is None or getattr(App, "ActiveDocument", None) is not document:
        return None
    parent_getter = getattr(edit_object, "getParentGeoFeatureGroup", None)
    owner = parent_getter() if callable(parent_getter) else None
    if getattr(owner, "TypeId", "") != "PartDesign::Body":
        return None
    event = {
        "document_uid": str(getattr(document, "Uid", "") or "").strip(),
        "document_name": str(getattr(document, "Name", "") or "").strip(),
        "sketch_name": str(getattr(edit_object, "Name", "") or "").strip(),
        "sketch_label": str(
            getattr(edit_object, "Label", getattr(edit_object, "Name", "")) or ""
        ).strip(),
        "owner_body": str(getattr(owner, "Name", "") or "").strip(),
    }
    if not all(
        event[key]
        for key in ("document_uid", "document_name", "sketch_name", "owner_body")
    ):
        return None
    return event


def _arm_sketch_close_continuation() -> dict[str, str] | None:
    event = _active_edit_sketch_continuation_event()
    if event is None:
        _sketch_close_continuation_controller.clear()
        return None
    return _sketch_close_continuation_controller.arm(event)


def _execute_assistant_run(
    dock: Any,
    service: Any,
    *,
    prompt: str | None = None,
    continuation_event: dict[str, Any] | None = None,
) -> None:
    global _assistant_run_thread
    if _is_assistant_run_active():
        _warn("VibeCAD refused to start a second provider loop while one is active.")
        return
    if _is_intent_memory_rebuild_active():
        _render_assistant_run_state(
            dock, text="Wait for the Intent Memory rebuild to finish."
        )
        return
    clean_prompt = str(prompt or "").strip()
    if bool(clean_prompt) == bool(continuation_event):
        raise ValueError(
            "A VibeCAD run requires exactly one user prompt or continuation event."
        )

    _sketch_close_continuation_controller.clear()
    _ensure_document_thread_invoker()
    prefer_online = service.use_online_provider_by_default()
    run_id = _assistant_run_controller.begin()
    _render_assistant_run_state(
        dock,
        text="Sketch closed. Continuing the CAD work..."
        if continuation_event
        else None,
    )
    _clear_thinking(dock)
    displayed_provider_texts: list[str] = []

    def _cancelled() -> bool:
        return _assistant_run_controller.is_cancelled(run_id)

    def _steering_messages() -> list[str]:
        def consume() -> list[str]:
            return [
                str(item.get("text", "")).strip()
                for item in service.consume_steering_messages()
                if str(item.get("text", "")).strip()
            ]

        return _dispatch_to_document_thread(consume)

    def _question_callback(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _request_user_answers(questions, _cancelled)

    def _progress_on_document_thread(event: dict[str, Any]) -> None:
        current_dock = _find_dock() or dock
        if event.get("event") == "provider_turn_output":
            text = str(event.get("text") or "").strip()
            if text:
                displayed_provider_texts.append(text)
                _append_conversation("VibeCAD", text)
        _handle_progress_event(current_dock, event)

    def _progress(event: dict[str, Any]) -> None:
        event_copy = dict(event)
        _dispatch_to_document_thread(lambda: _progress_on_document_thread(event_copy))

    def _complete_run(response: Any | None, failure: BaseException | None) -> None:
        global _assistant_run_thread
        current_dock = _find_dock() or dock
        run_succeeded = False
        terminal_status = ""
        if failure is not None:
            terminal_status = f"The CAD run failed: {failure}"
        elif response is not None:
            final_text = str(response.final_output or "").strip()
            if response.error:
                terminal_status = final_text or str(response.error)
            elif final_text and not displayed_provider_texts:
                _append_conversation(
                    "VibeCAD",
                    final_text,
                )
            memory_update = (
                response.context.get("intent_memory_update")
                if isinstance(response.context, dict)
                else None
            )
            if isinstance(memory_update, dict) and memory_update.get("ok") is False:
                terminal_status = (
                    "Intent Memory update failed; uncovered turns were retained"
                    f" | {memory_update.get('error', 'unknown error')}"
                )
            run_succeeded = response.error is None and not _cancelled()

        _assistant_run_controller.finish(run_id)
        try:
            transition = service.coerce_modeling_engine_for_workbench(
                service.active_workbench_name()
            )
            if transition.get("changed"):
                terminal_status = (
                    f"{transition.get('previous_engine')} is Part Design-only; "
                    "global modeling engine changed to VibeScript."
                )
        except Exception as exc:
            _warn(f"VibeCAD deferred engine transition failed: {exc}")
        _cancel_question_round()
        if run_succeeded:
            try:
                _arm_sketch_close_continuation()
            except Exception as exc:
                _sketch_close_continuation_controller.clear()
                _warn(f"VibeCAD could not arm sketch-close continuation: {exc}")
        else:
            _sketch_close_continuation_controller.clear()
        _clear_thinking(current_dock)
        _refresh_conversation_selector(current_dock)
        _render_assistant_run_state(
            current_dock,
            text=terminal_status or None,
        )
        _refresh_modeling_engine_selector(current_dock)
        try:
            from VibeCADScriptedEditor import refresh_scripted_model_editor

            refresh_scripted_model_editor()
        except Exception as exc:
            _warn(f"VibeCAD scripted editor completion refresh failed: {exc}")
        _refresh_view_status(current_dock)
        _render_questions(current_dock)
        _assistant_run_thread = None

    def _run_in_background() -> None:
        common_arguments = {
            "service": service,
            "prefer_online": prefer_online,
            "progress_callback": _progress,
            "cancellation_check": _cancelled,
            "steering_check": _steering_messages,
            "question_callback": _question_callback,
            "document_thread_dispatch": _dispatch_to_document_thread,
        }
        try:
            if continuation_event is not None:
                response = run_sketch_close_continuation(
                    continuation_event,
                    **common_arguments,
                )
            else:
                response = run_prompt(clean_prompt, **common_arguments)
        except BaseException as exc:
            _dispatch_to_document_thread(
                lambda failure=exc: _complete_run(None, failure)
            )
            return
        _dispatch_to_document_thread(
            lambda result=response: _complete_run(result, None)
        )

    _assistant_run_thread = threading.Thread(
        target=_run_in_background,
        name=f"VibeCAD-provider-{run_id}",
        daemon=True,
    )
    _assistant_run_thread.start()


def _start_sketch_close_continuation(event: dict[str, Any]) -> None:
    if _is_assistant_run_active() or _is_intent_memory_rebuild_active():
        _warn(
            "VibeCAD ignored a sketch-close continuation while another run was active."
        )
        return
    document = getattr(App, "ActiveDocument", None)
    if document is None:
        return
    if str(getattr(document, "Uid", "") or "") != str(event.get("document_uid") or ""):
        return
    if str(getattr(document, "Name", "") or "") != str(
        event.get("document_name") or ""
    ):
        return
    sketch = document.getObject(str(event.get("sketch_name") or ""))
    if sketch is None or getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
        return
    parent_getter = getattr(sketch, "getParentGeoFeatureGroup", None)
    owner = parent_getter() if callable(parent_getter) else None
    if getattr(owner, "TypeId", "") != "PartDesign::Body" or str(
        getattr(owner, "Name", "") or ""
    ) != str(event.get("owner_body") or ""):
        return
    gui_document = getattr(Gui, "ActiveDocument", None)
    if active_edit_state(gui_document).active:
        _warn(
            "VibeCAD did not continue after sketch close because another edit session is active."
        )
        return
    dock = _find_dock()
    if dock is None or not _assistant_panel_is_built(dock):
        _warn(
            "VibeCAD could not continue after sketch close because its panel is unavailable."
        )
        return
    service = get_service()
    persistence = service.document_persistence_state()
    if not persistence.get("enabled"):
        _render_assistant_run_state(
            dock,
            text=str(
                persistence.get("message")
                or "Save this VibeCAD document to enable VibeCAD."
            ),
        )
        return
    _execute_assistant_run(
        dock,
        service,
        continuation_event=event,
    )


def _run_prompt_from_panel() -> None:
    dock = _find_dock()
    if dock is None:
        return
    prompt_box = _find_child("QPlainTextEdit", "VibePrompt", dock)
    if prompt_box is None:
        return

    service = get_service()
    if not _is_assistant_run_active():
        persistence = service.document_persistence_state()
        if not persistence.get("enabled"):
            _render_assistant_run_state(
                dock,
                text=str(
                    persistence.get("message")
                    or "Save this VibeCAD document to enable VibeCAD."
                ),
            )
            return

    prompt = prompt_box.toPlainText().strip()
    if not prompt:
        _set_status_line("Enter a message before sending.", dock=dock)
        return

    if _is_assistant_run_active():
        result = service.queue_steering_message(prompt)
        if result.get("ok"):
            prompt_box.clear()
            _append_conversation(
                "User", prompt, persist=True, metadata={"source": "steering"}
            )
            _append_conversation(
                "AI thinking", "Received. I will apply that to the current CAD run."
            )
        else:
            _append_conversation(
                "VibeCAD",
                result.get("error", "Unable to send correction."),
                persist=True,
                metadata={"source": "steering_error"},
            )
        return

    # The background session persists the prompt after capturing only the
    # active document identity on the GUI thread.
    _append_conversation("User", prompt)
    prompt_box.clear()
    _execute_assistant_run(dock, service, prompt=prompt)


# ---------------------------------------------------------------------------
# View-status refresh
# ---------------------------------------------------------------------------


def _refresh_view_status(dock: Any | None = None) -> None:
    if dock is None:
        dock = _find_dock()
    if dock is None:
        return
    try:
        _set_view_status(get_service().view_screenshot_summary())
    except Exception as exc:
        _warn(f"VibeCAD view-status refresh failed: {exc}")


# ---------------------------------------------------------------------------
# Document observer: conversation persistence across saves
# ---------------------------------------------------------------------------


def _refresh_assistant_for_document_change() -> None:
    try:
        from VibeCADScriptedEditor import refresh_scripted_model_editor

        refresh_scripted_model_editor()
    except Exception as exc:
        _warn(f"VibeCAD scripted editor document refresh failed: {exc}")
    dock = _find_dock()
    if dock is None or not _assistant_panel_is_built(dock):
        return
    _clear_thinking(dock)
    _render_saved_conversation(dock)
    _refresh_conversation_selector(dock)
    _refresh_modeling_engine_selector(dock)
    _refresh_reference_chips(dock)
    _refresh_view_status(dock)
    _render_assistant_run_state(dock)


def _schedule_assistant_document_refresh() -> None:
    try:
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, _refresh_assistant_for_document_change)
    except Exception:
        _refresh_assistant_for_document_change()


def _document_storage_key(doc: Any) -> str:
    uid = str(getattr(doc, "Uid", "") or "").strip()
    if not uid:
        raise RuntimeError("FreeCAD document has no stable Uid.")
    return uid


def _snapshot_active_document_conversation(doc: Any) -> None:
    if doc is None:
        return
    try:
        active_doc = App.ActiveDocument
    except Exception:
        active_doc = None
    if active_doc is not doc and getattr(active_doc, "Name", None) != getattr(
        doc, "Name", None
    ):
        return
    document_key = _document_storage_key(doc)
    _document_save_conversations.pop(document_key, None)
    try:
        history = get_service().conversation_snapshot_for_save(doc)
    except Exception as exc:
        _warn(f"VibeCAD conversation snapshot failed: {exc}")
        history = {"store_path": ""}
    conversation_store_path = str(history.get("store_path") or "").strip()
    if conversation_store_path:
        _document_save_conversations[document_key] = {
            "store_path": conversation_store_path,
        }
    try:
        references = (
            get_service().reference_images_snapshot_for_save(doc).get("references", [])
        )
    except Exception as exc:
        _warn(f"VibeCAD reference snapshot failed: {exc}")
        references = []
    if isinstance(references, list) and references:
        _document_save_references[_document_storage_key(doc)] = {
            "references": [dict(item) for item in references if isinstance(item, dict)],
        }


def _move_saved_document_conversation(doc: Any, filepath: str) -> None:
    document_key = _document_storage_key(doc)
    snapshot = _document_save_conversations.pop(document_key, None) or {}
    reference_snapshot = _document_save_references.pop(document_key, None) or {}
    conversation_store_path = str(snapshot.get("store_path") or "").strip()
    if conversation_store_path:
        try:
            get_service().relocate_conversation_store_for_document_file(
                filepath,
                conversation_store_path,
            )
        except Exception as exc:
            _warn(f"VibeCAD saved-document conversation relocation failed: {exc}")
    references = reference_snapshot.get("references") or []
    if isinstance(references, list) and references:
        try:
            get_service().write_references_for_document_file(filepath, references)
        except Exception as exc:
            _warn(f"VibeCAD saved-document references write failed: {exc}")


class _VibeCADDocumentObserver:
    def slotCreatedDocument(self, doc) -> None:
        _schedule_assistant_document_refresh()

    def slotActivateDocument(self, doc) -> None:
        pending = _sketch_close_continuation_controller.snapshot()
        active_uid = str(getattr(doc, "Uid", "") or "")
        if pending and pending.get("document_uid") != active_uid:
            _sketch_close_continuation_controller.clear()
        _schedule_assistant_document_refresh()

    def slotChangedObject(self, obj, property_name) -> None:
        try:
            from VibeCADVibeScriptDomainPublication import (
                mark_programs_stale_from_source,
            )

            marked = mark_programs_stale_from_source(obj, str(property_name or ""))
        except Exception as exc:
            _warn(f"VibeCAD VibeScript dependency observer failed: {exc}")
            return
        if marked:
            _schedule_assistant_document_refresh()

    def slotStartSaveDocument(self, doc, filepath) -> None:
        _snapshot_active_document_conversation(doc)
        try:
            from VibeCADScriptedEditor import suspend_preview_for_save

            suspend_preview_for_save(doc)
        except Exception as exc:
            _warn(f"VibeCAD preview cleanup before save failed: {exc}")

    def slotFinishSaveDocument(self, doc, filepath) -> None:
        _move_saved_document_conversation(doc, str(filepath))
        try:
            from VibeCADScriptedEditor import restore_preview_after_save

            restore_preview_after_save(doc)
        except Exception as exc:
            _warn(f"VibeCAD preview restore after save failed: {exc}")
        _schedule_assistant_document_refresh()

    def slotDeletedDocument(self, doc) -> None:
        document_key = _document_storage_key(doc)
        _sketch_close_continuation_controller.clear_for_document(document_key)
        _document_save_conversations.pop(document_key, None)
        _document_save_references.pop(document_key, None)
        try:
            from VibeCADScriptedEditor import remove_all_previews

            remove_all_previews(doc)
        except Exception as exc:
            _warn(f"VibeCAD preview cleanup for deleted document failed: {exc}")
        _schedule_assistant_document_refresh()


def _schedule_sketch_close_continuation(event: dict[str, Any]) -> None:
    try:
        from PySide import QtCore
    except Exception as exc:
        _warn(f"VibeCAD cannot schedule sketch-close continuation: {exc}")
        return
    QtCore.QTimer.singleShot(
        0,
        lambda continuation=dict(event): _start_sketch_close_continuation(continuation),
    )


class _VibeCADGuiDocumentObserver:
    def slotResetEdit(self, view_provider) -> None:
        try:
            event = _sketch_close_continuation_controller.consume_reset_edit(
                view_provider
            )
        except Exception as exc:
            _warn(f"VibeCAD sketch-close observer failed: {exc}")
            return
        if event is not None:
            _schedule_sketch_close_continuation(event)


def _connect_document_observer() -> None:
    global _document_observer_connected, _document_observer
    global _gui_document_observer_connected, _gui_document_observer
    if not _document_observer_connected:
        try:
            _document_observer = _VibeCADDocumentObserver()
            App.addDocumentObserver(_document_observer)
            _document_observer_connected = True
        except Exception as exc:
            _warn(f"VibeCAD document observer failed: {exc}")
    if not _gui_document_observer_connected:
        try:
            _gui_document_observer = _VibeCADGuiDocumentObserver()
            Gui.addDocumentObserver(_gui_document_observer)
            _gui_document_observer_connected = True
        except Exception as exc:
            _warn(f"VibeCAD GUI document observer failed: {exc}")


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def _assistant_panel_is_built(dock: Any) -> bool:
    return (
        dock is not None
        and dock.widget() is not None
        and _find_child("QTextBrowser", "VibeConversation", dock) is not None
        and _find_child("QPlainTextEdit", "VibePrompt", dock) is not None
    )


def _build_panel_widget():
    """Build the panel content widget (no dock chrome — that is native now)."""
    from PySide import QtCore, QtGui, QtWidgets

    icon_size = QtCore.QSize(16, 16)

    root = QtWidgets.QWidget()
    root.setObjectName("VibePanelRoot")
    root.setWindowTitle("VibeCAD Assistant")
    layout = QtWidgets.QVBoxLayout(root)
    layout.setContentsMargins(10, 8, 10, 10)
    layout.setSpacing(8)

    splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, root)
    splitter.setObjectName("VibeContentSplitter")
    splitter.setChildrenCollapsible(True)
    layout.addWidget(splitter, 1)

    # --- Conversation ----------------------------------------------------
    conversation_panel = QtWidgets.QWidget(splitter)
    conversation_panel.setObjectName("VibeConversationPanel")
    conversation_layout = QtWidgets.QVBoxLayout(conversation_panel)
    conversation_layout.setContentsMargins(0, 0, 0, 0)
    conversation_layout.setSpacing(6)

    conversation_header = QtWidgets.QWidget(conversation_panel)
    conversation_header.setObjectName("VibeConversationHeader")
    conversation_header_layout = QtWidgets.QHBoxLayout(conversation_header)
    conversation_header_layout.setContentsMargins(0, 0, 0, 0)
    conversation_header_layout.setSpacing(6)

    conversation_selector = QtWidgets.QComboBox(conversation_header)
    conversation_selector.setObjectName("VibeConversationSelector")
    conversation_selector.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Fixed,
    )
    size_adjust_policy = getattr(
        QtWidgets.QComboBox,
        "SizeAdjustPolicy",
        QtWidgets.QComboBox,
    )
    conversation_selector.setSizeAdjustPolicy(
        size_adjust_policy.AdjustToMinimumContentsLengthWithIcon
    )
    conversation_selector.setMinimumContentsLength(18)
    conversation_selector.setToolTip("Open a conversation for this CAD document")
    conversation_selector.currentIndexChanged.connect(
        _activate_conversation_from_selector
    )
    conversation_header_layout.addWidget(conversation_selector, 1)

    engine_selector = QtWidgets.QComboBox(conversation_header)
    engine_selector.setObjectName("VibeModelingEngine")
    engine_selector.setMinimumContentsLength(9)
    engine_selector.setMaximumWidth(138)
    engine_selector.setVisible(False)
    engine_selector.currentIndexChanged.connect(_modeling_engine_changed)
    conversation_header_layout.addWidget(engine_selector)

    new_conversation = QtWidgets.QToolButton(conversation_header)
    new_conversation.setObjectName("VibeNewConversation")
    new_conversation.setIcon(QtGui.QIcon(_icon_path(ICON_NEW_CONVERSATION)))
    new_conversation.setIconSize(icon_size)
    new_conversation.setToolTip("New conversation")
    new_conversation.setAutoRaise(False)
    new_conversation.clicked.connect(_new_conversation_from_panel)
    conversation_header_layout.addWidget(new_conversation)
    conversation_layout.addWidget(conversation_header)

    conversation = QtWidgets.QTextBrowser(conversation_panel)
    conversation.setObjectName("VibeConversation")
    conversation.setReadOnly(True)
    conversation.setOpenExternalLinks(False)
    conversation.setOpenLinks(False)
    conversation.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
    conversation.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    conversation.setFrameShape(QtWidgets.QFrame.NoFrame)
    conversation.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Expanding,
    )
    conversation_layout.addWidget(conversation, 1)
    splitter.addWidget(conversation_panel)

    # --- Live provider stream --------------------------------------------
    thinking = QtWidgets.QPlainTextEdit(splitter)
    thinking.setObjectName("VibeThinking")
    thinking.setReadOnly(True)
    thinking.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    thinking.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    thinking.setFrameShape(QtWidgets.QFrame.NoFrame)
    thinking.setFocusPolicy(QtCore.Qt.NoFocus)
    thinking.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Expanding,
    )
    splitter.addWidget(thinking)

    lower = QtWidgets.QWidget(splitter)
    lower.setObjectName("VibeLowerPanel")
    lower.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Expanding,
    )
    lower_layout = QtWidgets.QVBoxLayout(lower)
    lower_layout.setContentsMargins(0, 0, 0, 0)
    lower_layout.setSpacing(6)
    splitter.addWidget(lower)

    # --- Model questions (hidden unless the current turn needs input) ------
    question_panel = QtWidgets.QScrollArea(lower)
    question_panel.setObjectName("VibeQuestionPanel")
    question_panel.setWidgetResizable(True)
    question_panel.setFrameShape(QtWidgets.QFrame.NoFrame)
    question_panel.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    question_panel.setVisible(False)
    question_body = QtWidgets.QWidget(question_panel)
    question_body.setObjectName("VibeQuestionList")
    question_layout = QtWidgets.QVBoxLayout(question_body)
    question_layout.setContentsMargins(0, 0, 0, 0)
    question_layout.setSpacing(6)
    question_panel.setWidget(question_body)
    lower_layout.addWidget(question_panel)

    # --- Status lines -----------------------------------------------------
    view_status = QtWidgets.QLabel(lower)
    view_status.setObjectName("VibeViewStatus")
    view_status.setVisible(False)
    lower_layout.addWidget(view_status)

    status_line = QtWidgets.QLabel(lower)
    status_line.setObjectName("VibeStatusLine")
    status_line.setWordWrap(True)
    status_line.setVisible(False)
    lower_layout.addWidget(status_line)

    # --- Composer ----------------------------------------------------------
    composer = QtWidgets.QWidget(lower)
    composer.setObjectName("VibeComposer")
    composer_layout = QtWidgets.QVBoxLayout(composer)
    composer_layout.setContentsMargins(0, 0, 0, 0)
    composer_layout.setSpacing(6)

    chips_row = QtWidgets.QWidget(composer)
    chips_row.setObjectName("VibeReferenceChips")
    chips_layout = QtWidgets.QHBoxLayout(chips_row)
    chips_layout.setContentsMargins(0, 0, 0, 0)
    chips_layout.setSpacing(4)
    chips_row.setVisible(False)
    composer_layout.addWidget(chips_row)

    prompt = QtWidgets.QPlainTextEdit(composer)
    prompt.setObjectName("VibePrompt")
    prompt.setPlaceholderText("Message VibeCAD...")
    prompt.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    prompt.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    prompt.setMinimumHeight(56)
    prompt.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Expanding,
    )
    _install_prompt_paste_filter(prompt)
    composer_layout.addWidget(prompt)

    composer_buttons = QtWidgets.QWidget(composer)
    composer_buttons.setObjectName("VibeComposerButtons")
    buttons_layout = QtWidgets.QHBoxLayout(composer_buttons)
    buttons_layout.setContentsMargins(0, 0, 0, 0)
    buttons_layout.setSpacing(6)

    attach_button = QtWidgets.QPushButton("Attach View", composer_buttons)
    attach_button.setObjectName("VibeAttachView")
    attach_button.setIcon(QtGui.QIcon(_icon_path(ICON_OPEN_ASSISTANT)))
    attach_button.setIconSize(icon_size)
    attach_button.clicked.connect(_capture_view_from_panel)

    attach_image_button = QtWidgets.QPushButton("Attach Image", composer_buttons)
    attach_image_button.setObjectName("VibeAttachImage")
    attach_image_button.setIcon(QtGui.QIcon(_icon_path(ICON_OPEN_ASSISTANT)))
    attach_image_button.setIconSize(icon_size)
    attach_image_button.setToolTip(
        "Attach a reference image (a picture of the part you want).\n"
        "You can also paste an image into the message box with Ctrl+V."
    )
    attach_image_button.clicked.connect(_attach_image_from_panel)

    prompt_starters = QtWidgets.QToolButton(composer_buttons)
    prompt_starters.setObjectName("VibePromptStarters")
    prompt_starters.setIcon(QtGui.QIcon(_icon_path(ICON_PROMPT_STARTERS)))
    prompt_starters.setIconSize(icon_size)
    prompt_starters.setToolTip("Insert an editable prompt starter")
    prompt_starters.setPopupMode(
        QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
    )
    prompt_starter_menu = QtWidgets.QMenu(prompt_starters)
    prompt_starter_menu.setObjectName("VibePromptStarterMenu")
    prompt_starter_menu.aboutToShow.connect(
        lambda: _populate_prompt_starter_menu(prompt_starter_menu, prompt)
    )
    prompt_starters.setMenu(prompt_starter_menu)

    send_button = QtWidgets.QPushButton("Send", composer_buttons)
    send_button.setObjectName("VibeSend")
    send_button.setIcon(QtGui.QIcon(_icon_path(ICON_SEND)))
    send_button.setIconSize(icon_size)
    send_button.setDefault(True)
    send_button.clicked.connect(_run_prompt_from_panel)

    stop_button = QtWidgets.QPushButton("Stop", composer_buttons)
    stop_button.setObjectName("VibeStop")
    stop_button.setIcon(QtGui.QIcon(_icon_path(ICON_STOP)))
    stop_button.setIconSize(icon_size)
    stop_button.setEnabled(False)
    stop_button.clicked.connect(_stop_prompt_from_panel)

    buttons_layout.addWidget(prompt_starters)
    buttons_layout.addWidget(attach_button)
    buttons_layout.addWidget(attach_image_button)
    buttons_layout.addStretch(1)
    buttons_layout.addWidget(send_button)
    buttons_layout.addWidget(stop_button)
    composer_layout.addWidget(composer_buttons)

    lower_layout.addWidget(composer, 1)
    if not _restore_panel_splitter_state(splitter):
        splitter.setSizes([480, 120, 220])
    splitter.splitterMoved.connect(
        lambda _position, _index: _save_panel_splitter_state(splitter)
    )
    return root


def _register_native_dock(widget) -> Any:
    """Register through DockWindowManager for a native dock."""
    main_window = Gui.getMainWindow()
    if main_window is None:
        raise RuntimeError("FreeCAD main window is not available.")
    add_dock_window = getattr(main_window, "addDockWindow", None)
    if not callable(add_dock_window):
        raise RuntimeError(
            "FreeCAD main window does not expose DockWindowManager.addDockWindow."
        )
    dock = add_dock_window(widget, DOCK_NAME, "right")
    dock.toggleViewAction().setVisible(True)
    return dock


def register_startup_assistant() -> Any:
    """Register assistant content for native workbench-owned dock creation."""
    global _registered_assistant_widget
    dock = _find_dock()
    if dock is not None:
        return dock
    if _registered_assistant_widget is not None:
        return _registered_assistant_widget
    widget = _build_panel_widget()
    widget.setMinimumWidth(300)
    _register_dock_content(widget, DOCK_NAME)
    _registered_assistant_widget = widget
    return widget


def _show_panel(text: str = "") -> None:
    try:
        from PySide import QtWidgets  # noqa: F401 - availability probe
    except Exception:
        _print(text or "VibeCAD assistant panel requires Qt.")
        return

    dock = _find_dock()
    if dock is None or not _assistant_panel_is_built(dock):
        if dock is None and _registered_assistant_widget is not None:
            _warn(
                "VibeCAD assistant content is registered but the active "
                "workbench has not created its dock window."
            )
            return
        widget = _build_panel_widget()
        if dock is not None:
            # Replace incomplete panel content without replacing the native dock.
            old = dock.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
            dock.setWidget(widget)
        else:
            try:
                dock = _register_native_dock(widget)
            except Exception as exc:
                message = f"VibeCAD assistant panel could not open: {exc}"
                _warn(message)
                _print(message)
                return
            dock.setMinimumWidth(300)

    dock.toggleViewAction().setVisible(True)
    dock.show()
    dock.raise_()

    if text:
        output = _find_child("QTextBrowser", "VibeConversation", dock)
        if output is not None:
            output.clear()
            _append_transcript_block(output, _transcript_block_html(text))
            _scroll_to_end(output)
    else:
        _render_saved_conversation(dock)
    _refresh_conversation_selector(dock)
    _refresh_modeling_engine_selector(dock)
    _refresh_view_status(dock)
    _refresh_reference_chips(dock)
    _render_questions(dock)
    _render_assistant_run_state(dock)


def show_assistant_for_active_workbench() -> None:
    _show_panel()


# ---------------------------------------------------------------------------
# Workbench activation
# ---------------------------------------------------------------------------


def _on_workbench_activated(workbench_name: str) -> None:
    clean_workbench = str(workbench_name)
    if get_tool_pack(clean_workbench) is None:
        return
    transition: dict[str, Any] = {"changed": False}
    if _is_assistant_run_active():
        transition = {
            "changed": False,
            "deferred": True,
            "reason": "An AI run is active.",
        }
    else:
        try:
            transition = get_service().coerce_modeling_engine_for_workbench(clean_workbench)
        except Exception as exc:
            _warn(f"VibeCAD global engine transition failed: {exc}")
    try:
        from PySide import QtCore
    except Exception:
        return

    def refresh_or_open() -> None:
        dock = _find_dock()
        if dock is not None:
            context_debug_dock = _find_context_debug_dock()
            if context_debug_dock is not None:
                _bind_context_debug_dock(context_debug_dock)
            if _assistant_panel_is_built(dock):
                _refresh_view_status(dock)
                _refresh_modeling_engine_selector(dock)
                if transition.get("changed"):
                    _set_status_line(
                        f"{transition.get('previous_engine')} is Part Design-only; "
                        "global modeling engine changed to VibeScript.",
                        dock=dock,
                    )
                try:
                    from VibeCADScriptedEditor import refresh_scripted_model_editor

                    refresh_scripted_model_editor()
                except Exception as exc:
                    _warn(f"VibeCAD scripted editor workbench refresh failed: {exc}")
            return
        if _registered_assistant_widget is not None:
            return
        _show_panel()

    QtCore.QTimer.singleShot(0, refresh_or_open)


def _connect_workbench_activation() -> None:
    global _workbench_activation_connected
    if _workbench_activation_connected:
        return
    try:
        main_window = Gui.getMainWindow()
        main_window.workbenchActivated.connect(_on_workbench_activated)
        _workbench_activation_connected = True
    except Exception as exc:
        _warn(f"VibeCAD AI assistant could not watch workbench activation: {exc}")


def _wrap_workbench_activation(workbench: Any) -> None:
    if getattr(workbench, "__VibeCADActivatedWrapped__", False):
        return
    original = getattr(workbench, "Activated", None)

    def _activated_with_vibecad(*args: Any, **kwargs: Any) -> Any:
        result = None
        if callable(original):
            result = original(*args, **kwargs)
        try:
            active = get_service().active_workbench_name()
            if active:
                _on_workbench_activated(active)
        except Exception as exc:
            _warn(f"VibeCAD assistant could not open after workbench activation: {exc}")
        return result

    setattr(workbench, "__VibeCADOriginalActivated__", original)
    setattr(workbench, "Activated", _activated_with_vibecad)
    setattr(workbench, "__VibeCADActivatedWrapped__", True)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class _BaseCommand:
    name = "VibeCAD"
    menu_text = "VibeCAD"
    tooltip = "VibeCAD AI command"
    pixmap = ICON_MARK

    def GetResources(self) -> dict[str, Any]:
        return {
            "Pixmap": self.pixmap,
            "MenuText": self.menu_text,
            "ToolTip": self.tooltip,
        }

    def IsActive(self) -> bool:
        return True


class AskAICommand(_BaseCommand):
    menu_text = "Ask AI"
    tooltip = "Ask VibeCAD in the current workbench context"
    pixmap = ICON_SEND

    def IsActive(self) -> bool:
        return bool(_document_persistence_state().get("enabled"))

    def Activated(self) -> None:
        if not _require_saved_document():
            _show_panel()
            return
        service = get_service()
        response = run_prompt("Summarize the current VibeCAD context.", service=service)
        _show_panel(f"[{response.provider}] {response.final_output}")


class ExplainSelectionCommand(_BaseCommand):
    menu_text = "Explain Selection"
    tooltip = "Explain the current selection using VibeCAD context tools"
    pixmap = ICON_ACTIVITY

    def Activated(self) -> None:
        selection = get_service().selection_summary()
        _show_panel(f"Selection context:\n{selection}")


class OpenAssistantCommand(_BaseCommand):
    menu_text = "VibeCAD Assistant"
    tooltip = "Open the VibeCAD assistant panel for the active workbench"
    pixmap = ICON_OPEN_ASSISTANT

    def Activated(self) -> None:
        _show_panel()


class OpenPreferencesCommand(_BaseCommand):
    menu_text = "VibeCAD Preferences"
    tooltip = "Open VibeCAD preferences"
    pixmap = ICON_MARK

    def Activated(self) -> None:
        ensure_preferences_registered()
        try:
            Gui.showPreferencesByName("VibeCAD", "VibeCAD")
        except Exception as exc:
            _show_panel(f"VibeCAD preferences could not be opened: {exc}")


class OpenScriptedModelCommand(_BaseCommand):
    menu_text = "Model Code Editor"
    tooltip = "Open the build123d, OpenSCAD, and VibeScript model code editor"
    pixmap = ICON_ACTIVITY

    def Activated(self) -> None:
        from VibeCADScriptedEditor import show_scripted_model_editor

        show_scripted_model_editor()


class AuthStatusCommand(_BaseCommand):
    menu_text = "VibeCAD Authentication Status"
    tooltip = "Show VibeCAD authentication status"
    pixmap = ICON_ACTIVITY

    def Activated(self) -> None:
        auth = get_service().auth_state()
        source = f" from {auth.source}" if auth.source else ""
        _show_panel(f"VibeCAD auth status: {auth.status.value}{source}\n{auth.message}")


def ensure_preferences_registered() -> None:
    global _preferences_registered
    if _preferences_registered:
        return
    import VibeCADPreferences

    Gui.addIconPath(str(Path(__file__).resolve().parent))
    Gui.addPreferencePage(VibeCADPreferences.VibeCADPreferencesPage, "VibeCAD")
    Gui.addPreferencePage(
        VibeCADPreferences.VibeCADPromptStartersPreferencesPage, "VibeCAD"
    )
    Gui.addPreferencePage(VibeCADPreferences.VibeCADDebugPreferencesPage, "VibeCAD")
    _preferences_registered = True


def ensure_commands_registered() -> None:
    global _commands_registered
    ensure_preferences_registered()
    _ensure_dock_workbench_manipulator()
    _connect_document_observer()
    _apply_startup_context_debug_preferences()
    if _commands_registered:
        _connect_workbench_activation()
        return
    Gui.addCommand("VibeCAD_AskAI", AskAICommand())
    Gui.addCommand("VibeCAD_ExplainSelection", ExplainSelectionCommand())
    Gui.addCommand("VibeCAD_OpenAssistant", OpenAssistantCommand())
    Gui.addCommand("VibeCAD_OpenPreferences", OpenPreferencesCommand())
    Gui.addCommand("VibeCAD_OpenScriptedModel", OpenScriptedModelCommand())
    Gui.addCommand("VibeCAD_AuthStatus", AuthStatusCommand())
    try:
        from VibeCADScriptedEditor import ensure_scripted_model_editor_registered

        # InitGui runs before startup workbench setup and the one native
        # MainWindow.loadWindowSettings() pass. Register the real dock now,
        # matching the lifecycle of FreeCAD's built-in dock widgets.
        ensure_scripted_model_editor_registered()
    except Exception as exc:
        _warn(f"VibeCAD scripted editor registration failed: {exc}")
    _connect_workbench_activation()
    _commands_registered = True


def register_ai_commands_for_workbench(workbench: Any, _workbench_name: str) -> None:
    """Connect VibeCAD lifecycle handling to an existing workbench."""

    ensure_commands_registered()
    _wrap_workbench_activation(workbench)
