# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human editor for document-embedded VibeScript programs."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import threading
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADCore import get_service
from VibeCADModelingSurface import resolve_modeling_surface


DOCK_NAME = "VibeCADScriptedModelPanel"
EDITOR_PREFERENCES = "User parameter:BaseApp/Preferences/VibeCAD/ModelCodeEditor"

_controller: Any | None = None
_registered_widget: Any | None = None
_refresh_retry_pending = False


class _LatestEditorJobRunner:
    """Run one editor metadata/build job at a time and coalesce pending work."""

    def __init__(self, completed_signal: Any):
        self._completed_signal = completed_signal
        self._condition = threading.Condition()
        self._serial = 0
        self._pending: tuple[int, str, Any] | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="VibeCAD model editor jobs",
            daemon=True,
        )
        self._thread.start()

    def submit(self, name: str, work: Any) -> int:
        with self._condition:
            self._serial += 1
            serial = self._serial
            self._pending = (serial, str(name), work)
            self._condition.notify()
            return serial

    def cancel_pending(self) -> None:
        with self._condition:
            self._serial += 1
            self._pending = None
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._serial += 1
            self._pending = None
            self._closed = True
            self._condition.notify()

    def _cancelled(self, serial: int) -> bool:
        with self._condition:
            return self._closed or serial != self._serial

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                serial, _name, work = self._pending
                self._pending = None
            try:
                event = work(lambda: self._cancelled(serial))
            except Exception as exc:
                event = {
                    "event_kind": "editor_job_failure",
                    "result": {
                        "ok": False,
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                }
            if event is not None and not self._cancelled(serial):
                self._completed_signal.emit(event)


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"VibeCAD scripted editor: {message}\n")


def _document_restore_active(doc: Any | None) -> bool:
    is_restoring = getattr(App, "isRestoring", None)
    if callable(is_restoring):
        try:
            if bool(is_restoring()):
                return True
        except Exception:
            pass
    return doc is not None and bool(getattr(doc, "Restoring", False))


def _find_dock() -> Any | None:
    from PySide import QtWidgets

    main = Gui.getMainWindow()
    return main.findChild(QtWidgets.QDockWidget, DOCK_NAME) if main is not None else None


SCRIPTED_ENGINES = {"vibescript"}
_DOMAIN_EDITOR_NEW_TYPES = {
    "assembly": "assembly",
    "cam": "job",
    "fem": "analysis",
    "inspection": "inspection_group",
    "mesh": "mesh",
    "part": "solid",
    "partdesign": "solid",
    "points": "points",
    "robot": "robot",
    "sketcher": "sketch",
    "spreadsheet": "sheet",
    "techdraw": "page",
}


def _new_domain_program_template(domain: str, label: str) -> tuple[str, str] | None:
    output_type = _DOMAIN_EDITOR_NEW_TYPES.get(str(domain or ""))
    if output_type is None:
        return None
    if domain == "partdesign":
        source = (
            "w = inputs['width']\n"
            "d = inputs['depth']\n"
            "h = inputs['height']\n"
            "bottom = api.line([0, 0], [w, 0], name='Bottom')\n"
            "right = api.line([w, 0], [w, d], name='Right')\n"
            "top = api.line([w, d], [0, d], name='Top')\n"
            "left = api.line([0, d], [0, 0], name='Left')\n"
            "profile = api.sketch([bottom, right, top, left], "
            "require_closed_profile=True, label='Base Profile')\n"
            "feature = api.extrude(profile, h, operation='add_material', "
            "label='Base Extrusion')\n"
            f"result = {{'Result': api.body(feature, label={label!r})}}\n"
        )
    elif domain == "part":
        source = "result = {'Result': api.box(10, 10, 10)}\n"
    elif domain == "assembly":
        source = f"result = {{'Result': api.assembly(label={label!r})}}\n"
    elif domain == "sketcher":
        source = (
            f"result = {{'Result': api.sketch(label={label!r}, "
            "geometry=[], constraints=[])}\n"
        )
    elif domain == "mesh":
        source = "result = {'Result': api.mesh(triangles=[])}\n"
    elif domain == "points":
        source = f"result = {{'Result': api.point_cloud([[0, 0, 0]], label={label!r})}}\n"
    else:
        source = f"result = {{'Result': api.output({output_type!r}, label={label!r})}}\n"
    return source, output_type


def _schema_requires_document_references(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("x-vibecad-reference") is True:
            return True
        return any(_schema_requires_document_references(item) for item in value.values())
    if isinstance(value, list):
        return any(_schema_requires_document_references(item) for item in value)
    return False


def _build_widget():
    from PySide import QtCore, QtGui, QtWidgets

    class LineNumberArea(QtWidgets.QWidget):
        def __init__(self, editor):
            super().__init__(editor)
            self.editor = editor

        def sizeHint(self):
            return QtCore.QSize(self.editor.line_number_area_width(), 0)

        def paintEvent(self, event):
            self.editor.paint_line_numbers(event)

    class SourceEditor(QtWidgets.QPlainTextEdit):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.number_area = LineNumberArea(self)
            self.blockCountChanged.connect(self.update_line_number_width)
            self.updateRequest.connect(self.update_line_number_area)
            self.cursorPositionChanged.connect(self.highlight_current_line)
            self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            self.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
            self.update_line_number_width()
            self.highlight_current_line()

        def line_number_area_width(self):
            digits = max(2, len(str(max(1, self.blockCount()))))
            return 10 + self.fontMetrics().horizontalAdvance("9") * digits

        def update_line_number_width(self, _count=0):
            self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

        def update_line_number_area(self, rect, dy):
            if dy:
                self.number_area.scroll(0, dy)
            else:
                self.number_area.update(0, rect.y(), self.number_area.width(), rect.height())
            if rect.contains(self.viewport().rect()):
                self.update_line_number_width()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            rect = self.contentsRect()
            self.number_area.setGeometry(
                QtCore.QRect(
                    rect.left(),
                    rect.top(),
                    self.line_number_area_width(),
                    rect.height(),
                )
            )

        def paint_line_numbers(self, event):
            painter = QtGui.QPainter(self.number_area)
            painter.fillRect(event.rect(), self.palette().alternateBase())
            block = self.firstVisibleBlock()
            number = block.blockNumber()
            top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            bottom = top + int(self.blockBoundingRect(block).height())
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    painter.setPen(
                        self.palette().color(QtGui.QPalette.Disabled, QtGui.QPalette.Text)
                    )
                    painter.drawText(
                        0,
                        top,
                        self.number_area.width() - 5,
                        self.fontMetrics().height(),
                        QtCore.Qt.AlignRight,
                        str(number + 1),
                    )
                block = block.next()
                top = bottom
                bottom = top + int(self.blockBoundingRect(block).height())
                number += 1

        def highlight_current_line(self):
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format.setBackground(self.palette().alternateBase())
            selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            self.setExtraSelections([selection])

        def goto_line(self, line: int):
            block = self.document().findBlockByNumber(max(0, line - 1))
            if block.isValid():
                cursor = QtGui.QTextCursor(block)
                self.setTextCursor(cursor)
                self.centerCursor()
                self.setFocus()

        def find_text(self, text: str, *, backwards: bool = False) -> bool:
            if not text:
                return False
            flags = (
                QtGui.QTextDocument.FindBackward if backwards else QtGui.QTextDocument.FindFlags()
            )
            if self.find(text, flags):
                return True
            cursor = self.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End if backwards else QtGui.QTextCursor.Start)
            self.setTextCursor(cursor)
            return bool(self.find(text, flags))

        def replace_current(self, replacement: str) -> bool:
            cursor = self.textCursor()
            if not cursor.hasSelection():
                return False
            cursor.insertText(replacement)
            return True

    class SchemaInputsEditor(QtWidgets.QScrollArea):
        changed = QtCore.Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWidgetResizable(True)
            self.setFrameShape(QtWidgets.QFrame.NoFrame)
            self._content = QtWidgets.QWidget(self)
            self._form = QtWidgets.QFormLayout(self._content)
            self._form.setContentsMargins(8, 8, 8, 8)
            self._form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            self.setWidget(self._content)
            self._schema: dict[str, Any] = {}
            self._values: dict[str, Any] = {}
            self._references: list[dict[str, str]] = []
            self._fields: dict[str, tuple[str, Any]] = {}
            self._optional: dict[str, Any] = {}
            self._loading = False

        def _clear(self):
            while self._form.rowCount():
                self._form.removeRow(0)
            self._fields = {}
            self._optional = {}

        def set_contract(
            self,
            schema: dict[str, Any],
            values: dict[str, Any],
            references: list[dict[str, str]],
        ) -> None:
            self._loading = True
            try:
                self._schema = dict(schema or {})
                self._values = dict(values or {})
                self._references = [dict(item) for item in references]
                self._clear()
                properties = self._schema.get("properties")
                if not isinstance(properties, dict) or not properties:
                    empty = QtWidgets.QLabel("This program has no configurable inputs.")
                    empty.setWordWrap(True)
                    self._form.addRow(empty)
                    return
                required = set(self._schema.get("required") or [])
                for name, raw_schema in properties.items():
                    name = str(name)
                    field_schema = dict(raw_schema or {})
                    description = str(field_schema.get("description") or "")
                    value = self._values.get(name, field_schema.get("default"))
                    kind, widget = self._make_field(name, field_schema, value)
                    if name in required:
                        label = QtWidgets.QLabel(f"{name} *", self._content)
                    else:
                        label = QtWidgets.QCheckBox(name, self._content)
                        label.setToolTip("Enable or omit this optional input.")
                        enabled = name in self._values
                        label.setChecked(enabled)
                        widget.setEnabled(enabled)
                        label.toggled.connect(
                            lambda checked, editor=widget: self._toggle_optional(
                                editor, checked
                            )
                        )
                        self._optional[name] = label
                    if description:
                        label.setToolTip(description)
                        widget.setToolTip(description)
                    self._fields[name] = (kind, widget)
                    self._form.addRow(label, widget)
                self._form.addRow(QtWidgets.QLabel("* required", self._content))
            finally:
                self._loading = False

        def _emit_changed(self, *_args) -> None:
            if not self._loading:
                self.changed.emit()

        def _toggle_optional(self, widget: Any, enabled: bool) -> None:
            widget.setEnabled(enabled)
            self._emit_changed()

        def _make_field(self, name: str, schema: dict[str, Any], value: Any):
            if schema.get("x-vibecad-reference") is True:
                widget = QtWidgets.QComboBox(self._content)
                widget.addItem("Select an object…", None)
                for reference in self._references:
                    text = str(reference.get("label") or reference.get("object_name") or "")
                    object_name = str(reference.get("object_name") or "")
                    if text != object_name:
                        text = f"{text} — {object_name}"
                    document_label = str(reference.get("document_label") or "")
                    if document_label:
                        text = f"{document_label}: {text}"
                    value_reference = {
                        key: str(reference[key])
                        for key in (
                            "document_uid",
                            "object_name",
                            "document_path",
                        )
                        if reference.get(key)
                    }
                    widget.addItem(text, value_reference)
                if isinstance(value, dict):
                    target = (
                        str(value.get("document_uid") or ""),
                        str(value.get("object_name") or ""),
                    )
                    for index in range(widget.count()):
                        candidate = widget.itemData(index)
                        if (
                            isinstance(candidate, dict)
                            and (
                                str(candidate.get("document_uid") or ""),
                                str(candidate.get("object_name") or ""),
                            )
                            == target
                        ):
                            widget.setCurrentIndex(index)
                            break
                widget.currentIndexChanged.connect(self._emit_changed)
                return "reference", widget
            enum = schema.get("enum")
            if isinstance(enum, list) and enum:
                widget = QtWidgets.QComboBox(self._content)
                for item in enum:
                    widget.addItem(str(item), item)
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
                widget.currentIndexChanged.connect(self._emit_changed)
                return "enum", widget
            raw_type = schema.get("type")
            types = list(raw_type) if isinstance(raw_type, list) else [raw_type]
            non_null = [item for item in types if item != "null"]
            if len(non_null) != 1 or "oneOf" in schema:
                return self._json_field(value)
            field_type = non_null[0]
            if field_type == "boolean":
                widget = QtWidgets.QCheckBox(self._content)
                widget.setChecked(bool(value))
                widget.toggled.connect(self._emit_changed)
                return "boolean", widget
            if field_type == "integer":
                minimum = schema.get("minimum")
                maximum = schema.get("maximum")
                if minimum is None and isinstance(schema.get("exclusiveMinimum"), int):
                    minimum = int(schema["exclusiveMinimum"]) + 1
                if maximum is None and isinstance(schema.get("exclusiveMaximum"), int):
                    maximum = int(schema["exclusiveMaximum"]) - 1
                minimum = -2147483647 if minimum is None else minimum
                maximum = 2147483647 if maximum is None else maximum
                if all(
                    isinstance(item, int) and -2147483647 <= item <= 2147483647
                    for item in (minimum, maximum)
                ):
                    widget = QtWidgets.QSpinBox(self._content)
                    widget.setRange(int(minimum), int(maximum))
                    widget.setValue(int(value or 0))
                    widget.valueChanged.connect(self._emit_changed)
                    return "integer", widget
                widget = QtWidgets.QLineEdit(str(value if value is not None else "0"))
                widget.setValidator(
                    QtGui.QRegularExpressionValidator(
                        QtCore.QRegularExpression(r"[+-]?\d+"), widget
                    )
                )
                widget.textEdited.connect(self._emit_changed)
                return "integer_text", widget
            if field_type == "number":
                widget = QtWidgets.QDoubleSpinBox(self._content)
                widget.setDecimals(12)
                minimum = float(schema.get("minimum", -1.0e100))
                maximum = float(schema.get("maximum", 1.0e100))
                if "exclusiveMinimum" in schema:
                    minimum = math.nextafter(
                        float(schema["exclusiveMinimum"]), math.inf
                    )
                if "exclusiveMaximum" in schema:
                    maximum = math.nextafter(
                        float(schema["exclusiveMaximum"]), -math.inf
                    )
                widget.setRange(minimum, maximum)
                multiple = schema.get("multipleOf")
                if isinstance(multiple, (int, float)) and multiple > 0:
                    widget.setSingleStep(float(multiple))
                widget.setValue(float(value or 0.0))
                widget.setKeyboardTracking(False)
                widget.valueChanged.connect(self._emit_changed)
                return "number", widget
            if field_type == "string":
                widget = QtWidgets.QLineEdit(str(value if value is not None else ""), self._content)
                maximum = schema.get("maxLength")
                if isinstance(maximum, int):
                    widget.setMaxLength(maximum)
                widget.textEdited.connect(self._emit_changed)
                return "string", widget
            return self._json_field(value)

        def _json_field(self, value: Any):
            widget = QtWidgets.QLineEdit(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                self._content,
            )
            widget.textEdited.connect(self._emit_changed)
            return "json", widget

        def values(self) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for name, (kind, widget) in self._fields.items():
                optional = self._optional.get(name)
                if optional is not None and not optional.isChecked():
                    continue
                if kind == "reference":
                    value = widget.currentData()
                    if value is not None:
                        result[name] = dict(value)
                elif kind == "enum":
                    result[name] = widget.currentData()
                elif kind == "boolean":
                    result[name] = bool(widget.isChecked())
                elif kind == "integer":
                    result[name] = int(widget.value())
                elif kind == "integer_text":
                    result[name] = int(widget.text())
                elif kind == "number":
                    result[name] = float(widget.value())
                elif kind == "string":
                    result[name] = str(widget.text())
                else:
                    try:
                        result[name] = json.loads(widget.text())
                    except ValueError as exc:
                        raise ValueError(f"Input {name!r} is not valid JSON: {exc}") from exc
            return result

    class ScriptHighlighter(QtGui.QSyntaxHighlighter):
        def __init__(self, document, engine: str):
            super().__init__(document)
            self.engine = engine
            keyword_color = QtGui.QColor("#65b8ff")
            string_color = QtGui.QColor("#82c995")
            number_color = QtGui.QColor("#f0b86e")
            comment_color = QtGui.QColor("#7f8b96")
            self.rules = []
            if engine == "json":
                keywords = ["true", "false", "null"]
            else:
                keywords = [
                    "from",
                    "import",
                    "as",
                    "def",
                    "class",
                    "for",
                    "while",
                    "if",
                    "elif",
                    "else",
                    "return",
                    "assert",
                    "True",
                    "False",
                    "None",
                ]
            for word in keywords:
                expression = QtCore.QRegularExpression(rf"\b{re.escape(word)}\b")
                fmt = QtGui.QTextCharFormat()
                fmt.setForeground(keyword_color)
                fmt.setFontWeight(QtGui.QFont.Bold)
                self.rules.append((expression, fmt))
            patterns = [
                (r"\b(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\b", number_color),
                (r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', string_color),
            ]
            if engine != "json":
                patterns.append((r"#[^\n]*", comment_color))
            for pattern, color in patterns:
                fmt = QtGui.QTextCharFormat()
                fmt.setForeground(color)
                self.rules.append((QtCore.QRegularExpression(pattern), fmt))

        def highlightBlock(self, text):
            for expression, fmt in self.rules:
                iterator = expression.globalMatch(text)
                while iterator.hasNext():
                    match = iterator.next()
                    self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

    class Bridge(QtCore.QObject):
        completed = QtCore.Signal(object)

    class EditorRoot(QtWidgets.QWidget):
        def minimumSizeHint(self):
            return QtCore.QSize(180, 180)

        def sizeHint(self):
            return QtCore.QSize(420, 680)

    root = EditorRoot()
    root.setObjectName("VibeScriptedModelRoot")
    root.setWindowTitle("Model Code Editor")
    layout = QtWidgets.QVBoxLayout(root)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)

    toolbar = QtWidgets.QWidget(root)
    toolbar.setObjectName("VibeScriptedModelToolbar")
    toolbar_layout = QtWidgets.QVBoxLayout(toolbar)
    toolbar_layout.setContentsMargins(0, 0, 0, 0)
    toolbar_layout.setSpacing(6)

    context_label = QtWidgets.QLabel("No active scripted domain", toolbar)
    context_label.setObjectName("VibeScriptedContext")
    context_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    toolbar_layout.addWidget(context_label)

    selector_row = QtWidgets.QWidget(toolbar)
    selector_layout = QtWidgets.QHBoxLayout(selector_row)
    selector_layout.setContentsMargins(0, 0, 0, 0)
    selector_layout.setSpacing(6)
    model_selector = QtWidgets.QComboBox(selector_row)
    model_selector.setObjectName("VibeScriptedModelSelector")
    model_selector.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    selector_layout.addWidget(model_selector, 1)
    toolbar_layout.addWidget(selector_row)

    actions_layout = QtWidgets.QGridLayout()
    actions_layout.setContentsMargins(0, 0, 0, 0)
    actions_layout.setHorizontalSpacing(6)
    actions_layout.setVerticalSpacing(6)
    actions = (
        ("New", "VibeScriptedNew", "Create a new source-backed model"),
        (
            "Save",
            "VibeScriptedSave",
            "Save the current source and inputs in this FreeCAD document",
        ),
        ("Build", "VibeScriptedRender", "Build and validate the current working source"),
    )
    for index, (text, name, tooltip) in enumerate(actions):
        button = QtWidgets.QPushButton(text, toolbar)
        button.setObjectName(name)
        button.setToolTip(tooltip)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        actions_layout.addWidget(button, index // 2, index % 2)
    toolbar_layout.addLayout(actions_layout)

    point_artifact_row = QtWidgets.QWidget(toolbar)
    point_artifact_row.setObjectName("VibeScriptedPointArtifactRow")
    point_artifact_layout = QtWidgets.QHBoxLayout(point_artifact_row)
    point_artifact_layout.setContentsMargins(0, 0, 0, 0)
    point_artifact_layout.setSpacing(6)
    point_artifact_label = QtWidgets.QLabel("Point data", point_artifact_row)
    point_artifact_layout.addWidget(point_artifact_label)
    point_artifact_selector = QtWidgets.QComboBox(point_artifact_row)
    point_artifact_selector.setObjectName("VibeScriptedPointArtifactSelector")
    point_artifact_selector.setToolTip(
        "Human-approved point files available to Points VibeScript programs"
    )
    point_artifact_selector.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
    )
    point_artifact_selector.addItem("No approved point data", "")
    point_artifact_layout.addWidget(point_artifact_selector, 1)
    point_artifact_add = QtWidgets.QPushButton("Add…", point_artifact_row)
    point_artifact_add.setObjectName("VibeScriptedPointArtifactAdd")
    point_artifact_add.setToolTip("Copy a human-selected point file into this VibeCAD project")
    point_artifact_layout.addWidget(point_artifact_add)
    point_artifact_remove = QtWidgets.QPushButton("Remove", point_artifact_row)
    point_artifact_remove.setObjectName("VibeScriptedPointArtifactRemove")
    point_artifact_remove.setToolTip(
        "Remove the selected approval when no program still references it"
    )
    point_artifact_layout.addWidget(point_artifact_remove)
    point_artifact_row.setVisible(False)
    toolbar_layout.addWidget(point_artifact_row)
    layout.addWidget(toolbar)

    tabs = QtWidgets.QTabWidget(root)
    tabs.setObjectName("VibeScriptedTabs")
    source_panel = QtWidgets.QWidget(tabs)
    source_panel.setObjectName("VibeScriptedSourcePanel")
    source_layout = QtWidgets.QVBoxLayout(source_panel)
    source_layout.setContentsMargins(0, 0, 0, 0)
    source_layout.setSpacing(4)
    find_bar = QtWidgets.QWidget(source_panel)
    find_bar.setObjectName("VibeScriptedFindBar")
    find_layout = QtWidgets.QGridLayout(find_bar)
    find_layout.setContentsMargins(0, 0, 0, 0)
    find_layout.setSpacing(4)
    find_text = QtWidgets.QLineEdit(find_bar)
    find_text.setObjectName("VibeScriptedFindText")
    find_text.setPlaceholderText("Find")
    replace_text = QtWidgets.QLineEdit(find_bar)
    replace_text.setObjectName("VibeScriptedReplaceText")
    replace_text.setPlaceholderText("Replace")
    find_previous = QtWidgets.QToolButton(find_bar)
    find_previous.setText("Previous")
    find_previous.setObjectName("VibeScriptedFindPrevious")
    find_next = QtWidgets.QToolButton(find_bar)
    find_next.setText("Next")
    find_next.setObjectName("VibeScriptedFindNext")
    replace_button = QtWidgets.QToolButton(find_bar)
    replace_button.setText("Replace")
    replace_button.setObjectName("VibeScriptedReplace")
    find_close = QtWidgets.QToolButton(find_bar)
    find_close.setText("×")
    find_close.setObjectName("VibeScriptedFindClose")
    find_layout.addWidget(find_text, 0, 0, 1, 2)
    find_layout.addWidget(find_previous, 0, 2)
    find_layout.addWidget(find_next, 0, 3)
    find_layout.addWidget(find_close, 0, 4)
    find_layout.addWidget(replace_text, 1, 0, 1, 2)
    find_layout.addWidget(replace_button, 1, 2)
    find_bar.hide()
    source_layout.addWidget(find_bar)
    source_editor = SourceEditor(source_panel)
    source_editor.setObjectName("VibeScriptedSource")
    source_layout.addWidget(source_editor, 1)
    cursor_status = QtWidgets.QLabel("Ln 1, Col 1", source_panel)
    cursor_status.setObjectName("VibeScriptedCursorStatus")
    cursor_status.setAlignment(QtCore.Qt.AlignRight)
    source_layout.addWidget(cursor_status)
    tabs.addTab(source_panel, "Source")

    inputs_editor = SchemaInputsEditor(tabs)
    inputs_editor.setObjectName("VibeScriptedInputs")
    tabs.addTab(inputs_editor, "Inputs")
    parameters_editor = SourceEditor(tabs)
    parameters_editor.setObjectName("VibeScriptedParameters")
    tabs.addTab(parameters_editor, "Inputs JSON")

    diagnostics = QtWidgets.QTreeWidget()
    diagnostics.setObjectName("VibeScriptedDiagnostics")
    diagnostics.setHeaderLabels(["Severity", "Location", "Message"])
    diagnostics.setRootIsDecorated(False)
    diagnostics.setMinimumHeight(40)

    content_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, root)
    content_splitter.setObjectName("VibeScriptedContentSplitter")
    content_splitter.setChildrenCollapsible(True)
    content_splitter.addWidget(tabs)
    content_splitter.addWidget(diagnostics)
    content_splitter.setStretchFactor(0, 1)
    content_splitter.setStretchFactor(1, 0)
    preferences = App.ParamGet(EDITOR_PREFERENCES)
    encoded_splitter = str(preferences.GetString("ContentSplitterState", "") or "")
    restored_splitter = False
    if encoded_splitter:
        try:
            restored_splitter = bool(
                content_splitter.restoreState(
                    QtCore.QByteArray.fromBase64(encoded_splitter.encode("ascii"))
                )
            )
        except Exception:
            restored_splitter = False
    if not restored_splitter:
        content_splitter.setSizes([520, 120])

    def save_splitter_state(_position=0, _index=0):
        encoded = bytes(content_splitter.saveState().toBase64()).decode("ascii")
        preferences.SetString("ContentSplitterState", encoded)

    content_splitter.splitterMoved.connect(save_splitter_state)
    layout.addWidget(content_splitter, 1)

    status = QtWidgets.QLabel(root)
    status.setObjectName("VibeScriptedStatus")
    status.setWordWrap(True)
    status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    layout.addWidget(status)

    bridge = Bridge(root)
    root._vibecad_source_highlighter = None
    root._vibecad_source_highlighter_engine = ""
    root._vibecad_parameter_highlighter = ScriptHighlighter(parameters_editor.document(), "json")
    root._vibecad_bridge = bridge
    root._vibecad_source_editor_class = SourceEditor

    def update_cursor_status():
        cursor = source_editor.textCursor()
        cursor_status.setText(f"Ln {cursor.blockNumber() + 1}, Col {cursor.positionInBlock() + 1}")

    def show_find(*, replace: bool = False):
        find_bar.show()
        replace_text.setVisible(replace)
        replace_button.setVisible(replace)
        find_text.setFocus()
        find_text.selectAll()

    def find_source(backwards: bool = False):
        source_editor.find_text(find_text.text(), backwards=backwards)

    def replace_source():
        if not source_editor.replace_current(replace_text.text()):
            find_source(False)

    source_editor.cursorPositionChanged.connect(update_cursor_status)
    find_previous.clicked.connect(lambda: find_source(True))
    find_next.clicked.connect(lambda: find_source(False))
    find_text.returnPressed.connect(lambda: find_source(False))
    replace_button.clicked.connect(replace_source)
    find_close.clicked.connect(find_bar.hide)
    find_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Find, root)
    replace_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Replace, root)
    zoom_in_shortcut = QtGui.QShortcut(QtGui.QKeySequence.ZoomIn, root)
    zoom_out_shortcut = QtGui.QShortcut(QtGui.QKeySequence.ZoomOut, root)
    find_shortcut.activated.connect(lambda: show_find(replace=False))
    replace_shortcut.activated.connect(lambda: show_find(replace=True))
    zoom_in_shortcut.activated.connect(source_editor.zoomIn)
    zoom_out_shortcut.activated.connect(source_editor.zoomOut)
    update_cursor_status()
    return root


class ScriptedEditorController:
    def __init__(self, dock: Any):
        from PySide import QtCore, QtWidgets

        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.dock = dock
        self.root = dock.widget()
        self.engine = "vibescript"
        self.domain = ""
        self.document_name = ""
        self.document_uid = ""
        self.model_id = ""
        self.working_revision = ""
        self.accepted_revision = ""
        self.model: dict[str, Any] = {}
        self.source_files: dict[str, str] = {}
        self.current_source_file = "model.py"
        self.loading = False
        self.generation = 0
        self.active_vibescript_candidate: dict[str, Any] | None = None
        self.dirty = False
        self.busy = False
        self.reference_options: list[dict[str, str]] = []
        self.editor_active = False
        self.point_artifact_generation = 0
        self.point_artifact_busy = False
        self.point_artifact_project_root = ""
        self.point_artifact_loaded_root = ""
        self.jobs = _LatestEditorJobRunner(self.root._vibecad_bridge.completed)
        self.point_jobs = _LatestEditorJobRunner(self.root._vibecad_bridge.completed)
        self._connect()

    def child(self, kind: Any, name: str):
        return self.root.findChild(kind, name)

    @property
    def source(self):
        return self.child(self.QtWidgets.QPlainTextEdit, "VibeScriptedSource")

    @property
    def parameters(self):
        return self.child(self.QtWidgets.QPlainTextEdit, "VibeScriptedParameters")

    @property
    def inputs(self):
        return self.child(self.QtWidgets.QScrollArea, "VibeScriptedInputs")

    @property
    def tabs(self):
        return self.child(self.QtWidgets.QTabWidget, "VibeScriptedTabs")

    @property
    def context_label(self):
        return self.child(self.QtWidgets.QLabel, "VibeScriptedContext")

    @property
    def selector(self):
        return self.child(self.QtWidgets.QComboBox, "VibeScriptedModelSelector")

    @property
    def status(self):
        return self.child(self.QtWidgets.QLabel, "VibeScriptedStatus")

    @property
    def point_artifact_selector(self):
        return self.child(self.QtWidgets.QComboBox, "VibeScriptedPointArtifactSelector")

    @property
    def point_artifact_row(self):
        return self.child(self.QtWidgets.QWidget, "VibeScriptedPointArtifactRow")

    @property
    def diagnostics(self):
        return self.child(self.QtWidgets.QTreeWidget, "VibeScriptedDiagnostics")

    def button(self, name: str):
        return self.child(self.QtWidgets.QPushButton, name)

    def _connect(self):
        self.dock.visibilityChanged.connect(self._visibility_changed)
        self.dock.destroyed.connect(lambda _obj=None: self.jobs.close())
        self.dock.destroyed.connect(lambda _obj=None: self.point_jobs.close())
        self.selector.currentIndexChanged.connect(self._select_model)
        self.source.textChanged.connect(self._source_changed)
        self.parameters.textChanged.connect(self._parameters_changed)
        self.inputs.changed.connect(self._schema_inputs_changed)
        self.root._vibecad_bridge.completed.connect(self._preview_completed)
        self.button("VibeScriptedNew").clicked.connect(self.new_model)
        self.button("VibeScriptedSave").clicked.connect(self.save)
        self.button("VibeScriptedRender").clicked.connect(self.render)
        self.button("VibeScriptedPointArtifactAdd").clicked.connect(self.add_point_artifact)
        self.button("VibeScriptedPointArtifactRemove").clicked.connect(self.remove_point_artifact)
        self.point_artifact_selector.currentIndexChanged.connect(
            lambda _index: self._update_actions()
        )
        self.diagnostics.itemActivated.connect(self._diagnostic_activated)

    def _visibility_changed(self, visible: bool):
        if visible:
            self.activate()
        else:
            self.deactivate()

    def activate(self, preferred_model_id: str = ""):
        if self.editor_active:
            self.refresh(preferred_model_id)
            return
        self.editor_active = True
        self.refresh(preferred_model_id)

    def deactivate(self):
        self.editor_active = False
        self.jobs.cancel_pending()
        self.point_jobs.cancel_pending()
        self.busy = False
        self.point_artifact_busy = False

    def automated_update_started(self, engine: str, document_name: str, model_id: str):
        if (
            not self.editor_active
            or engine != self.engine
            or model_id != self.model_id
            or document_name != str(getattr(App.ActiveDocument, "Name", "") or "")
        ):
            return
        self._cancel_preview(restore_accepted=True)
        self.status.setText(f"AI is updating {self.model.get('label') or model_id}...")
        self._update_actions()

    def automated_update_finished(
        self,
        engine: str,
        document_name: str,
        model_id: str,
    ):
        if (
            not self.editor_active
            or engine != self.engine
            or model_id != self.model_id
            or document_name != str(getattr(App.ActiveDocument, "Name", "") or "")
        ):
            return
        self._cancel_preview(restore_accepted=True)
        self.refresh(model_id)

    def _clear_model_fields(self):
        self.model_id = ""
        self.working_revision = ""
        self.accepted_revision = ""
        self.model = {}
        self.source_files = {}
        self.current_source_file = "model.py"
        self.active_vibescript_candidate = None
        self.reference_options = []
        self._set_dirty(False)

    def _clear_editors(self):
        self.loading = True
        self.source.clear()
        self.parameters.clear()
        self.inputs.set_contract({}, {}, [])
        self.loading = False

    def _set_dirty(self, dirty: bool) -> None:
        dirty = bool(dirty)
        if dirty == self.dirty:
            return
        self.dirty = dirty
        if self.tabs is not None and self.tabs.count():
            self.tabs.setTabText(0, "Source *" if self.dirty else "Source")
        self._update_actions()

    def _capture_reference_options(self, doc: Any | None) -> list[dict[str, str]]:
        if doc is None:
            return []
        if self.domain == "assembly":
            from VibeCADComponentCatalog import open_component_candidates

            return [
                {
                    **dict(candidate["reference"]),
                    "label": str(candidate.get("label") or ""),
                    "document_label": str(
                        candidate.get("document_label") or ""
                    ),
                }
                for candidate in open_component_candidates(doc)
            ]
        document_uid = str(getattr(doc, "Uid", "") or "")
        return [
            {
                "document_uid": document_uid,
                "object_name": str(getattr(obj, "Name", "") or ""),
                "label": str(getattr(obj, "Label", "") or ""),
            }
            for obj in list(getattr(doc, "Objects", []) or [])[:10_000]
            if str(getattr(obj, "Name", "") or "")
        ]

    def _vibescript_program_objects(self, doc: Any | None = None) -> list[Any]:
        if self.engine != "vibescript" or not self.model_id:
            return []
        if doc is None:
            doc = App.ActiveDocument
        if doc is None or str(getattr(doc, "Uid", "") or "") != self.document_uid:
            return []
        import VibeCADVibeScriptDomains as domain_contracts

        return [
            obj
            for obj in list(getattr(doc, "Objects", []) or [])
            if str(
                getattr(obj, domain_contracts.PROP_PROGRAM_ID, "") or ""
            )
            == self.model_id
            and str(
                getattr(obj, domain_contracts.PROP_PROGRAM_DOMAIN, "") or ""
            )
            == self.domain
        ]

    def _vibescript_contract_owner(self, doc: Any | None = None) -> Any | None:
        import VibeCADVibeScriptDomains as domain_contracts

        objects = self._vibescript_program_objects(doc)
        if not objects:
            return None
        expected_outputs = list(self.model.get("expected_outputs") or [])
        first_output = (
            str(expected_outputs[0].get("name") or "")
            if expected_outputs and isinstance(expected_outputs[0], dict)
            else ""
        )
        return min(
            objects,
            key=lambda obj: (
                not bool(
                    str(
                        getattr(
                            obj,
                            domain_contracts.PROP_PROGRAM_CONTRACT,
                            "",
                        )
                        or ""
                    )
                ),
                bool(
                    str(
                        getattr(
                            obj,
                            domain_contracts.PROP_PROGRAM_OUTPUT,
                            "",
                        )
                        or ""
                    )
                ),
                str(
                    getattr(
                        obj,
                        domain_contracts.PROP_PROGRAM_OUTPUT,
                        "",
                    )
                    or ""
                )
                != first_output,
                str(getattr(obj, "Name", "") or ""),
            ),
        )

    @staticmethod
    def _set_hidden_string_property(
        obj: Any,
        name: str,
        value: str,
        description: str,
    ) -> None:
        properties = set(getattr(obj, "PropertiesList", []) or [])
        if name not in properties:
            obj.addProperty("App::PropertyString", name, "VibeCAD", description)
        setattr(obj, name, value)
        setter = getattr(obj, "setEditorMode", None)
        if callable(setter):
            setter(name, 2)

    def _persist_vibescript_draft(self) -> bool:
        if (
            self.engine != "vibescript"
            or not self.model_id
            or not self.editor_active
        ):
            return False
        doc = App.ActiveDocument
        owner = self._vibescript_contract_owner(doc)
        if owner is None:
            self.status.setText("This script has no document object to save into.")
            return False
        import VibeCADVibeScriptDomains as domain_contracts

        try:
            encoded = domain_contracts.encode_editor_draft(
                program_id=self.model_id,
                domain=self.domain,
                base_revision=self.working_revision,
                source=self.source.toPlainText(),
                input_schema=dict(self.model.get("input_schema") or {}),
                inputs_json=self.parameters.toPlainText(),
                expected_outputs=list(self.model.get("expected_outputs") or []),
            )
            for obj in self._vibescript_program_objects(doc):
                value = encoded if obj is owner else ""
                if (
                    obj is owner
                    or domain_contracts.PROP_PROGRAM_EDITOR_DRAFT
                    in set(getattr(obj, "PropertiesList", []) or [])
                ):
                    self._set_hidden_string_property(
                        obj,
                        domain_contracts.PROP_PROGRAM_EDITOR_DRAFT,
                        value,
                        "Unbuilt VibeScript editor source and inputs.",
                    )
            return True
        except Exception as exc:
            self.status.setText(f"Could not save the VibeScript draft in this document: {exc}")
            return False

    def _clear_vibescript_draft(self, doc: Any | None = None) -> None:
        import VibeCADVibeScriptDomains as domain_contracts

        for obj in self._vibescript_program_objects(doc):
            if domain_contracts.PROP_PROGRAM_EDITOR_DRAFT not in set(
                getattr(obj, "PropertiesList", []) or []
            ):
                continue
            setattr(obj, domain_contracts.PROP_PROGRAM_EDITOR_DRAFT, "")

    def _embed_loaded_vibescript_contract(self) -> None:
        if self.engine != "vibescript" or not self.accepted_revision:
            return
        owner = self._vibescript_contract_owner()
        if owner is None:
            return
        accepted = self.model.get("accepted_contract")
        if not isinstance(accepted, dict):
            if self.accepted_revision != self.working_revision:
                return
            accepted = {
                "source": self.model.get("source"),
                "input_schema": self.model.get("input_schema"),
                "inputs": self.model.get("parameters"),
                "expected_outputs": self.model.get("expected_outputs"),
            }
        import VibeCADVibeScriptDomains as domain_contracts

        pack = domain_contracts.get_vibescript_pack(
            get_service().active_workbench_name()
        )
        if pack is None or pack.domain != self.domain:
            return
        try:
            encoded = domain_contracts.encode_document_program_contract(
                pack,
                program_id=self.model_id,
                label=str(self.model.get("label") or self.model_id),
                revision=self.accepted_revision,
                source=str(accepted.get("source") or ""),
                input_schema=dict(accepted.get("input_schema") or {}),
                inputs=dict(accepted.get("inputs") or {}),
                expected_outputs=list(accepted.get("expected_outputs") or []),
            )
            for obj in self._vibescript_program_objects():
                self._set_hidden_string_property(
                    obj,
                    domain_contracts.PROP_PROGRAM_LABEL,
                    str(self.model.get("label") or self.model_id),
                    "Stable VibeScript program label.",
                )
                value = encoded if obj is owner else ""
                if (
                    obj is owner
                    or domain_contracts.PROP_PROGRAM_CONTRACT
                    in set(getattr(obj, "PropertiesList", []) or [])
                ):
                    self._set_hidden_string_property(
                        obj,
                        domain_contracts.PROP_PROGRAM_CONTRACT,
                        value,
                        "Portable accepted VibeScript source, inputs, and output contract.",
                    )
        except Exception as exc:
            _warn(f"Could not embed the accepted VibeScript contract: {exc}")

    def _deselect_model(self, *, update_selector: bool):
        self._cancel_preview(restore_accepted=True)
        self._clear_model_fields()
        self._clear_editors()
        if update_selector and self.selector.count():
            self.loading = True
            none_index = self.selector.findData("")
            self.selector.setCurrentIndex(max(0, none_index))
            self.loading = False
        self.status.setText("No scripted model selected.")
        self.diagnostics.clear()
        self._update_actions()

    def refresh(self, preferred_model_id: str = ""):
        if not self.editor_active:
            return
        service = get_service()
        next_engine = service.modeling_engine()
        workbench = service.active_workbench_name()
        resolution = resolve_modeling_surface(workbench, next_engine)
        next_domain = str(resolution.domain or "")
        active_document = getattr(service, "_active_document", None)
        doc = active_document() if callable(active_document) else App.ActiveDocument
        next_document_name = str(getattr(doc, "Name", "") or "")
        next_document_uid = str(getattr(doc, "Uid", "") or "")
        context_changed = (
            next_engine != self.engine
            or next_domain != self.domain
            or next_document_uid != self.document_uid
        )
        if context_changed and self.dirty:
            self.context_label.setText(
                f"Unsaved {self.engine} edits retained — return to their document and domain"
            )
            self.status.setText(
                "The active document or workbench changed. "
                "The editor retained your unbuilt changes and will not replace them."
            )
            self._update_actions()
            return
        if context_changed:
            self.jobs.cancel_pending()
            self.point_jobs.cancel_pending()
            self.point_artifact_generation += 1
            self.point_artifact_busy = False
            self.point_artifact_project_root = ""
            self.point_artifact_loaded_root = ""
            self._cancel_preview(restore_accepted=True)
            self._clear_model_fields()
        self.engine = next_engine
        self.domain = next_domain
        self.document_name = next_document_name
        self.document_uid = next_document_uid
        scripted = self.engine in SCRIPTED_ENGINES and resolution.available
        self.root.setEnabled(scripted)
        self.context_label.setText(
            (
                f"{self.domain} · {next_document_name or 'no document'}"
                if scripted
                else "No active scripted domain"
            )
        )
        points_active = bool(scripted and self.domain == "points")
        self.point_artifact_row.setVisible(points_active)
        if points_active and not self.point_artifact_busy:
            self._start_point_artifact_refresh()
        else:
            self.point_artifact_generation += 1
            self.point_artifact_busy = False
            self.point_artifact_project_root = ""
            self.point_artifact_loaded_root = ""
            self._set_point_artifact_items([], "Point data is available in Points.")
        self.button("VibeScriptedRender").setToolTip(
            "Validate the current source and inputs, then update the model"
        )
        if not scripted:
            self._clear_model_fields()
            self.loading = True
            self.selector.clear()
            self.selector.addItem("None", "")
            self.source.clear()
            self.parameters.clear()
            self.inputs.set_contract({}, {}, [])
            self.source_files = {}
            self.loading = False
            self.status.setText(
                resolution.unavailable_reason
                or "The active workbench has no VibeScript authoring domain."
            )
            return
        self._start_vibescript_model_refresh(preferred_model_id)

    def _set_point_artifact_items(
        self,
        artifacts: list[dict[str, Any]],
        empty_text: str = "No approved point data",
        preferred_artifact_id: str = "",
    ) -> None:
        selector = self.point_artifact_selector
        previous = str(selector.currentData() or "")
        target = preferred_artifact_id or previous
        selector.blockSignals(True)
        try:
            selector.clear()
            selector.addItem(empty_text, "")
            for artifact in artifacts:
                artifact_id = str(artifact.get("artifact_id") or "")
                if not artifact_id:
                    continue
                title = str(artifact.get("label") or artifact.get("name") or "Point data")
                selector.addItem(f"{title} — {artifact_id}", artifact_id)
                index = selector.count() - 1
                selector.setItemData(
                    index,
                    "\n".join(
                        (
                            f"Stable ID: {artifact_id}",
                            f"Original name: {artifact.get('name') or ''}",
                            f"Format: {artifact.get('format') or ''}",
                            f"Bytes: {int(artifact.get('size_bytes') or 0)}",
                            f"Available: {'yes' if artifact.get('available') else 'no'}",
                        )
                    ),
                    self.QtCore.Qt.ToolTipRole,
                )
            index = selector.findData(target) if target else 0
            selector.setCurrentIndex(index if index >= 0 else 0)
        finally:
            selector.blockSignals(False)
        self._update_actions()

    def _point_artifact_root_snapshot(self) -> str:
        snapshot = get_service().project_scope_snapshot()
        return str(snapshot.get("root") or "").strip()

    def _start_point_artifact_refresh(self) -> None:
        project_root = self._point_artifact_root_snapshot()
        if (
            project_root
            and project_root == self.point_artifact_loaded_root
            and not self.point_artifact_busy
        ):
            return
        self.point_artifact_project_root = project_root
        self.point_artifact_generation += 1
        generation = self.point_artifact_generation
        if not project_root:
            self.point_artifact_busy = False
            self.point_artifact_loaded_root = ""
            self._set_point_artifact_items(
                [], "Save or initialize this project to approve point data."
            )
            return
        self.point_artifact_busy = True
        self._set_point_artifact_items([], "Loading approved point data…")
        service = get_service()

        def work(cancelled):
            try:
                result = service.point_artifacts(project_root=project_root)
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            if cancelled():
                return None
            return {
                "event_kind": "point_artifact_list",
                "engine": "vibescript",
                "domain": "points",
                "artifact_generation": generation,
                "result": result,
            }

        self.point_jobs.submit("point artifact list", work)

    def add_point_artifact(self) -> None:
        if self.engine != "vibescript" or self.domain != "points" or self.point_artifact_busy:
            return
        project_root = self.point_artifact_project_root or self._point_artifact_root_snapshot()
        if not project_root:
            self.status.setText("Save or initialize this project before approving point data.")
            return
        selected, _selected_filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.root,
            "Approve point data",
            "",
            "Point data (*.asc *.xyz *.pcd *.ply *.e57)",
        )
        if not selected:
            return
        self._approve_point_artifact_path(selected, project_root)

    def _approve_point_artifact_path(
        self,
        selected: str,
        project_root: str = "",
    ) -> None:
        if (
            self.engine != "vibescript"
            or self.domain != "points"
            or self.point_artifact_busy
            or not selected
        ):
            return
        project_root = project_root or self.point_artifact_project_root
        if not project_root:
            self.status.setText("Save or initialize this project before approving point data.")
            return
        self.point_artifact_generation += 1
        generation = self.point_artifact_generation
        self.point_artifact_busy = True
        self.status.setText("Copying and authenticating point data in the background…")
        self._update_actions()
        service = get_service()

        def work(cancelled):
            try:
                approved = service.approve_point_artifact(
                    selected,
                    label=Path(selected).stem,
                    project_root=project_root,
                )
                summary = service.point_artifacts(project_root=project_root)
                result = {
                    "ok": True,
                    "artifact": dict(approved.get("artifact") or {}),
                    "summary": summary,
                }
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            if cancelled():
                return None
            return {
                "event_kind": "point_artifact_approved",
                "engine": "vibescript",
                "domain": "points",
                "artifact_generation": generation,
                "result": result,
            }

        self.point_jobs.submit("point artifact approval", work)

    def remove_point_artifact(self) -> None:
        artifact_id = str(self.point_artifact_selector.currentData() or "")
        if (
            self.engine != "vibescript"
            or self.domain != "points"
            or self.point_artifact_busy
            or not artifact_id
        ):
            return
        answer = self.QtWidgets.QMessageBox.question(
            self.root,
            "Remove approved point data",
            "Remove this project-local point-data approval?\n\n"
            f"{artifact_id}\n\n"
            "Removal is rejected while a working or accepted program references it.",
            self.QtWidgets.QMessageBox.Yes | self.QtWidgets.QMessageBox.No,
            self.QtWidgets.QMessageBox.No,
        )
        if answer != self.QtWidgets.QMessageBox.Yes:
            return
        project_root = self.point_artifact_project_root or self._point_artifact_root_snapshot()
        if not project_root:
            self.status.setText("The active project has no point-artifact root.")
            return
        self._remove_point_artifact_id(artifact_id, project_root)

    def _remove_point_artifact_id(
        self,
        artifact_id: str,
        project_root: str = "",
    ) -> None:
        if (
            self.engine != "vibescript"
            or self.domain != "points"
            or self.point_artifact_busy
            or not artifact_id
        ):
            return
        project_root = project_root or self.point_artifact_project_root
        if not project_root:
            self.status.setText("The active project has no point-artifact root.")
            return
        self.point_artifact_generation += 1
        generation = self.point_artifact_generation
        self.point_artifact_busy = True
        self.status.setText("Removing the unreferenced point-data approval…")
        self._update_actions()
        service = get_service()

        def work(cancelled):
            try:
                removed = service.remove_point_artifact(
                    artifact_id,
                    project_root=project_root,
                )
                summary = service.point_artifacts(project_root=project_root)
                result = {
                    "ok": True,
                    "removed": removed,
                    "summary": summary,
                }
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            if cancelled():
                return None
            return {
                "event_kind": "point_artifact_removed",
                "engine": "vibescript",
                "domain": "points",
                "artifact_generation": generation,
                "artifact_id": artifact_id,
                "result": result,
            }

        self.point_jobs.submit("point artifact removal", work)

    def _apply_model_list(self, models: list[dict[str, Any]], preferred_model_id: str = "") -> None:
        target = preferred_model_id or self.model_id
        summaries = {
            str(item.get("model_id") or item.get("program_id") or ""): item
            for item in models
            if isinstance(item, dict)
        }
        self.loading = True
        self.selector.clear()
        self.selector.addItem("None", "")
        for item in models:
            label = str(item.get("label") or item.get("model_id"))
            state = str(item.get("state") or "")
            if self.engine == "vibescript":
                state = {
                    "accepted": "built",
                    "accepted_current": "built",
                    "accepted_document": "built",
                    "working_candidate": "needs build",
                    "working_candidate_not_accepted": "needs build",
                    "reconfiguration_required": "needs update",
                    "live_outputs_only": "built",
                }.get(state, state)
            suffix = f"  [{state}]" if state else ""
            self.selector.addItem(
                f"{label}{suffix}",
                str(item.get("model_id") or ""),
            )
        index = self.selector.findData(target) if target else 0
        if index < 0:
            index = 0
        if index >= 0:
            self.selector.setCurrentIndex(index)
        self.loading = False
        if index > 0:
            selected_id = str(self.selector.itemData(index) or "")
            summary = summaries.get(selected_id, {})
            summary_revision = str(summary.get("working_revision") or "")
            if (
                selected_id != self.model_id
                or not self.model
                or summary_revision != self.working_revision
            ):
                self._load_model(selected_id)
        else:
            self._deselect_model(update_selector=False)
        self._update_actions()

    def _start_vibescript_model_refresh(self, preferred_model_id: str = "") -> None:
        """Load only the VibeScript editor index; never capture domain geometry."""

        service = get_service()
        active_domain = self.domain
        import VibeCADVibeScriptDomains as domain_contracts

        snapshot = domain_contracts.domain_program_index_snapshot(service, active_domain)
        self.generation += 1
        generation = self.generation
        self.status.setText("Loading VibeScript models...")
        self.busy = True
        self._update_actions()

        def work(cancelled):
            try:
                completed = domain_contracts.complete_domain_program_index(snapshot)
                if cancelled():
                    return None
                models = [
                    {
                        **item,
                        "model_id": str(item.get("program_id") or ""),
                    }
                    for item in list(completed.get("programs") or [])
                ]
                event_kind = "vibescript_domain_program_list"
                result = {"ok": True, "models": models}
            except Exception as exc:
                event_kind = "vibescript_domain_program_list"
                result = {
                    "ok": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            return {
                "event_kind": event_kind,
                "engine": "vibescript",
                "generation": generation,
                "preferred_model_id": preferred_model_id,
                "result": result,
            }

        self.jobs.submit("program index", work)

    def _select_model(self, index: int):
        if self.loading or index < 0:
            return
        model_id = str(self.selector.itemData(index) or "")
        if self.dirty and model_id != self.model_id:
            answer = self.QtWidgets.QMessageBox.warning(
                self.root,
                "Discard unbuilt changes?",
                "This program has source or input changes that have not been built. "
                "Discard them and switch programs?",
                self.QtWidgets.QMessageBox.Discard | self.QtWidgets.QMessageBox.Cancel,
                self.QtWidgets.QMessageBox.Cancel,
            )
            if answer != self.QtWidgets.QMessageBox.Discard:
                self.loading = True
                previous = self.selector.findData(self.model_id)
                self.selector.setCurrentIndex(previous if previous >= 0 else 0)
                self.loading = False
                return
        if not model_id:
            self._deselect_model(update_selector=False)
            return
        self._load_model(model_id)

    def _load_model(self, model_id: str):
        if not model_id:
            return
        if self.model_id and model_id != self.model_id:
            self._cancel_preview(restore_accepted=True)
        self._start_vibescript_model_inspection(model_id)

    def _apply_loaded_model(self, model_id: str, result: dict[str, Any]) -> None:
        previous_engine = str(getattr(self.root, "_vibecad_source_highlighter_engine", ""))
        self.model = dict(result["model"])
        self.model_id = model_id
        self.working_revision = str(self.model.get("working_revision") or "")
        self.accepted_revision = str(self.model.get("accepted_revision") or "")
        editor_draft = self.model.get("editor_draft")
        restore_draft = bool(
            isinstance(editor_draft, dict)
            and str(editor_draft.get("base_revision") or "")
            == self.working_revision
        )
        main_name = "model.py"
        source_files = self.model.get("source_files")
        if not isinstance(source_files, dict):
            source_files = {main_name: str(self.model.get("source") or "")}
        self.source_files = {str(path): str(content) for path, content in source_files.items()}
        if restore_draft:
            self.source_files[main_name] = str(editor_draft.get("source") or "")
            self.model["input_schema"] = dict(
                editor_draft.get("input_schema") or {}
            )
            self.model["expected_outputs"] = list(
                editor_draft.get("expected_outputs") or []
            )
        self.current_source_file = (
            main_name
            if main_name in self.source_files
            else next(iter(self.source_files), main_name)
        )
        input_values = dict(self.model.get("parameters") or {})
        input_schema = dict(self.model.get("input_schema") or {})
        parameters_text = json.dumps(input_values, indent=2, sort_keys=True)
        if restore_draft:
            parameters_text = str(editor_draft.get("inputs_json") or "")
            try:
                draft_values = json.loads(parameters_text or "{}")
            except ValueError:
                draft_values = None
            if isinstance(draft_values, dict):
                input_values = draft_values
        if not input_schema and input_values:
            inferred_properties: dict[str, dict[str, str]] = {}
            for name, value in input_values.items():
                inferred_type = (
                    "boolean"
                    if isinstance(value, bool)
                    else "integer"
                    if isinstance(value, int)
                    else "number"
                    if isinstance(value, float)
                    else "string"
                    if isinstance(value, str)
                    else "array"
                    if isinstance(value, list)
                    else "object"
                )
                inferred_properties[str(name)] = {"type": inferred_type}
            input_schema = {
                "type": "object",
                "properties": inferred_properties,
                "required": list(inferred_properties),
                "additionalProperties": False,
            }
        if _schema_requires_document_references(input_schema):
            doc = get_service()._active_document()
            self.reference_options = self._capture_reference_options(doc)
        else:
            self.reference_options = []
        self.loading = True
        source_text = self.source_files.get(self.current_source_file, "")
        if self.source.toPlainText() != source_text:
            self.source.setPlainText(source_text)
        if self.parameters.toPlainText() != parameters_text:
            self.parameters.setPlainText(parameters_text)
        self.inputs.set_contract(input_schema, input_values, self.reference_options)
        self.loading = False
        if previous_engine != self.engine:
            self._install_highlighter()
        self.active_vibescript_candidate = None
        self._set_dirty(False)
        self._embed_loaded_vibescript_contract()
        if restore_draft:
            self.status.setText(
                "Loaded saved source and inputs from this document. "
                "Press Build to update the model."
            )
        else:
            self.status.setText(
                (
                    "The model is built from the saved source and inputs."
                    if self.working_revision == self.accepted_revision
                    else "The saved source and inputs need to be built."
                )
            )
        self.diagnostics.clear()
        latest = self.model.get("latest_attempt") or {}
        failure = latest.get("failure") if isinstance(latest, dict) else None
        if isinstance(failure, dict):
            self._populate_diagnostics(failure)
        self._update_actions()

    def _install_highlighter(self):
        if (
            getattr(self.root, "_vibecad_source_highlighter", None) is not None
            and str(getattr(self.root, "_vibecad_source_highlighter_engine", "")) == self.engine
        ):
            return
        old = getattr(self.root, "_vibecad_source_highlighter", None)
        if old is not None:
            old.setDocument(None)
        # Reuse the highlighter class already attached to the parameters editor.
        highlighter_class = type(self.root._vibecad_parameter_highlighter)
        self.root._vibecad_source_highlighter = highlighter_class(
            self.source.document(), self.engine
        )
        self.root._vibecad_source_highlighter_engine = self.engine

    def _source_changed(self):
        if self.loading or not self.editor_active or not self.model_id:
            return
        first_change = not self.dirty
        if first_change or self.busy:
            self._invalidate_preview_for_edit()
        self._set_dirty(True)
        if first_change:
            self.status.setText("Source modified. Press Save or Build.")

    def _parameters_changed(self):
        if self.loading or not self.editor_active or not self.model_id:
            return
        first_change = not self.dirty
        if first_change or self.busy:
            self._invalidate_preview_for_edit()
        try:
            values = json.loads(self.parameters.toPlainText() or "{}")
        except ValueError:
            values = None
        if isinstance(values, dict):
            schema = dict(self.model.get("input_schema") or {})
            if self.engine == "vibescript":
                import VibeCADVibeScriptDomains as domain_contracts

                schema = domain_contracts.synchronize_input_schema(
                    schema,
                    values,
                )
                self.model["input_schema"] = schema
            if schema:
                self.loading = True
                try:
                    self.inputs.set_contract(
                        schema,
                        values,
                        self.reference_options,
                    )
                finally:
                    self.loading = False
        self._set_dirty(True)
        if first_change:
            self.status.setText("Inputs modified. Press Save or Build.")

    def _schema_inputs_changed(self):
        if self.loading or not self.editor_active or not self.model_id:
            return
        try:
            values = self.inputs.values()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.loading = True
        try:
            self.parameters.setPlainText(json.dumps(values, indent=2, sort_keys=True))
        finally:
            self.loading = False
        self._invalidate_preview_for_edit()
        self._set_dirty(True)
        self.status.setText("Inputs modified. Press Save or Build.")

    def _start_vibescript_model_inspection(self, model_id: str) -> None:
        """Inspect one VibeScript program away from the GUI thread."""

        active_domain = self.domain
        from VibeCADVibeScriptDomainRuntime import capture_editor_inspection_state

        try:
            captured = capture_editor_inspection_state(get_service(), active_domain, model_id)
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        self.generation += 1
        generation = self.generation
        self.status.setText("Loading VibeScript source and model metadata...")
        self.busy = True
        self._update_actions()

        def work(cancelled):
            try:
                from VibeCADVibeScriptDomainRuntime import complete_inspection

                result = complete_inspection(captured)
                if cancelled():
                    return None
                if result.get("ok") is True:
                    program = dict(result.get("program") or {})
                    result = {
                        "ok": True,
                        "model": {
                            **program,
                            "model_id": str(program.get("program_id") or ""),
                            "parameters": dict(program.get("inputs") or {}),
                            "latest_attempt": dict(program.get("latest_candidate") or {}),
                        },
                    }
                event_kind = "vibescript_domain_program_inspection"
            except Exception as exc:
                event_kind = "vibescript_domain_program_inspection"
                result = {
                    "ok": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            return {
                "event_kind": event_kind,
                "engine": "vibescript",
                "generation": generation,
                "model_id": model_id,
                "result": result,
            }

        self.jobs.submit("program inspection", work)

    def _parse_parameters(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.parameters.toPlainText() or "{}")
        except ValueError as exc:
            self.status.setText(f"Inputs JSON is not valid: {exc}")
            return None
        if not isinstance(value, dict):
            self.status.setText("Inputs must be a JSON object.")
            return None
        schema = dict(self.model.get("input_schema") or {})
        if self.engine == "vibescript":
            import VibeCADVibeScriptDomains as domain_contracts

            schema = domain_contracts.synchronize_input_schema(schema, value)
            self.model["input_schema"] = schema
        if schema:
            self.loading = True
            try:
                self.inputs.set_contract(schema, value, self.reference_options)
            finally:
                self.loading = False
        return value

    def _invalidate_preview_for_edit(self):
        self.jobs.cancel_pending()
        self.busy = False
        self._cancel_preview(restore_accepted=True)
        self.active_vibescript_candidate = None

    def _save_vibescript_draft(self, *, show_status: bool) -> bool:
        if self.engine != "vibescript" or not self.model_id:
            return False
        self.source_files[self.current_source_file] = self.source.toPlainText()
        if not self._persist_vibescript_draft():
            return False
        doc = App.ActiveDocument
        if doc is None:
            self.status.setText("There is no FreeCAD document to save.")
            return False
        try:
            if str(getattr(doc, "FileName", "") or ""):
                doc.save()
            else:
                Gui.runCommand("Std_Save")
                if not str(getattr(doc, "FileName", "") or ""):
                    self.status.setText("Save was cancelled.")
                    return False
        except Exception as exc:
            self.status.setText(f"Could not save the FreeCAD document: {exc}")
            return False
        self._set_dirty(False)
        if show_status:
            self.status.setText(
                "Saved source and inputs in the FreeCAD file. "
                "Press Build to update the model."
            )
        return True

    def save(self) -> None:
        self._save_vibescript_draft(show_status=True)

    def render(self):
        if not self.editor_active or not self.model_id or self.engine != "vibescript":
            return
        if not self._save_vibescript_draft(show_status=False):
            return
        parameters = self._parse_parameters()
        if parameters is None:
            return
        self.source_files[self.current_source_file] = self.source.toPlainText()
        self._start_vibescript_build(
            {
                "program_id": self.model_id,
                "expected_revision": self.working_revision,
                "source": self.source_files.get("model.py", self.source.toPlainText()),
                "input_schema": dict(self.model.get("input_schema") or {}),
                "inputs": parameters,
                "expected_outputs": list(self.model.get("expected_outputs") or []),
            }
        )

    def _start_vibescript_build(self, arguments: dict[str, Any]) -> None:
        from VibeCADGui import (
            _dispatch_to_document_thread,
            _ensure_document_thread_invoker,
        )
        from VibeCADSession import build_domain_vibescript_editor_candidate

        _ensure_document_thread_invoker()
        self.generation += 1
        generation = self.generation
        domain = self.domain
        self.active_vibescript_candidate = None
        self.status.setText("Building and validating the model...")
        self.busy = True
        self._update_actions()

        def work(cancelled):
            result = build_domain_vibescript_editor_candidate(
                get_service(),
                f"vibescript.{domain}.reconfigure_program",
                arguments,
                document_thread_dispatch=_dispatch_to_document_thread,
                cancellation_check=cancelled,
            )
            if cancelled():
                return None
            return {
                "event_kind": "vibescript_editor_candidate",
                "engine": "vibescript",
                "domain": domain,
                "generation": generation,
                "result": result,
            }

        self.jobs.submit("VibeScript candidate build", work)

    def _preview_completed(self, event: dict[str, Any]):
        event_engine = str(event.get("engine") or "")
        event_kind = str(event.get("event_kind") or "")
        if event_kind == "editor_job_failure":
            self.busy = False
            self._show_failure(dict(event.get("result") or {}))
            return
        if event_kind == "vibescript_editor_candidate":
            if (
                not self.editor_active
                or self.engine != "vibescript"
                or event_engine != "vibescript"
                or str(event.get("domain") or "") != self.domain
                or int(event.get("generation") or 0) != self.generation
            ):
                return
            self.busy = False
            result = event.get("result")
            if not isinstance(result, dict) or result.get("ok") is not True:
                adopted = bool(
                    isinstance(result, dict)
                    and self._adopt_failed_vibescript_revision(result)
                )
                self._show_failure(
                    result
                    if isinstance(result, dict)
                    else {"error": "The VibeScript build returned no structured result."}
                )
                if adopted:
                    self.status.setText(
                        f"{str(result.get('error') or 'Build failed.')} "
                        "The source and inputs remain editable; correct them and press "
                        "Build again."
                    )
                return
            candidate = result.get("_editor_candidate")
            if not isinstance(candidate, dict):
                self._show_failure(
                    {"error": "The VibeScript build returned no validated model result."}
                )
                return
            self.active_vibescript_candidate = candidate
            prepared_candidate = candidate.get("prepared")
            if isinstance(prepared_candidate, dict):
                self.model["source"] = str(prepared_candidate.get("source") or "")
                self.model["parameters"] = dict(prepared_candidate.get("inputs") or {})
                self.model["input_schema"] = dict(
                    prepared_candidate.get("input_schema") or {}
                )
                self.model["expected_outputs"] = list(
                    prepared_candidate.get("expected_outputs") or []
                )
            self.working_revision = str(result.get("working_revision") or "")
            self.model["working_revision"] = self.working_revision
            self.diagnostics.clear()
            self._start_vibescript_apply()
            return
        if event_kind == "vibescript_editor_apply":
            if (
                not self.editor_active
                or self.engine != "vibescript"
                or event_engine != "vibescript"
                or int(event.get("generation") or 0) != self.generation
            ):
                return
            self.busy = False
            result = event.get("result")
            if not isinstance(result, dict) or result.get("ok") is not True:
                self._set_dirty(True)
                if self._persist_vibescript_draft():
                    self._set_dirty(False)
                self._show_failure(
                    result
                    if isinstance(result, dict)
                    else {"error": "The VibeScript apply returned no structured result."}
                )
                return
            revision = str(result.get("accepted_revision") or "")
            model_id = str(result.get("program_id") or self.model_id)
            self.active_vibescript_candidate = None
            self.accepted_revision = revision
            self._clear_vibescript_draft()
            self._set_dirty(False)
            self.model = {}
            try:
                doc = App.ActiveDocument
                if doc is not None and str(getattr(doc, "FileName", "") or ""):
                    doc.save()
            except Exception as exc:
                self.status.setText(
                    f"Model updated, but the FreeCAD file could not be saved: {exc}"
                )
            else:
                self.status.setText(
                    f"Model updated and saved ({revision[:10]})."
                )
            self.refresh(model_id)
            return
        if event_kind in {
            "point_artifact_list",
            "point_artifact_approved",
            "point_artifact_removed",
        }:
            if (
                not self.editor_active
                or self.engine != "vibescript"
                or self.domain != "points"
                or event_engine != "vibescript"
                or str(event.get("domain") or "") != "points"
                or int(event.get("artifact_generation") or 0) != self.point_artifact_generation
            ):
                return
            self.point_artifact_busy = False
            self.point_artifact_loaded_root = self.point_artifact_project_root
            result = event.get("result")
            if not isinstance(result, dict) or result.get("ok") is not True:
                if event_kind == "point_artifact_list":
                    self._set_point_artifact_items([], "Could not load approved point data.")
                self.status.setText(
                    str(
                        result.get("error")
                        if isinstance(result, dict)
                        else "Point-artifact operation returned no structured result."
                    )
                )
                self._update_actions()
                return
            summary = result if event_kind == "point_artifact_list" else result.get("summary")
            if not isinstance(summary, dict) or summary.get("ok") is not True:
                self.status.setText(
                    "Point data changed, but its approved-artifact summary could not be read."
                )
                self._update_actions()
                return
            preferred_artifact_id = ""
            if event_kind == "point_artifact_approved":
                artifact = result.get("artifact")
                if isinstance(artifact, dict):
                    preferred_artifact_id = str(artifact.get("artifact_id") or "")
            self._set_point_artifact_items(
                list(summary.get("artifacts") or []),
                preferred_artifact_id=preferred_artifact_id,
            )
            if event_kind == "point_artifact_approved":
                self.status.setText(
                    "Approved point data with stable reference "
                    f"{{'artifact_id': '{preferred_artifact_id}'}}."
                )
            elif event_kind == "point_artifact_removed":
                self.status.setText(
                    f"Removed point-data approval {str(event.get('artifact_id') or '')}."
                )
            return
        if event_kind in {
            "vibescript_domain_program_list",
            "vibescript_domain_program_inspection",
            "vibescript_revert",
        }:
            if (
                not self.editor_active
                or self.engine != "vibescript"
                or event_engine != "vibescript"
                or int(event.get("generation") or 0) != self.generation
            ):
                return
            self.busy = False
            result = event.get("result")
            if not isinstance(result, dict) or result.get("ok") is not True:
                self._show_failure(
                    result
                    if isinstance(result, dict)
                    else {"error": "VibeScript returned no structured result."}
                )
                return
            if event_kind == "vibescript_domain_program_list":
                self._apply_model_list(
                    list(result.get("models") or []),
                    str(event.get("preferred_model_id") or ""),
                )
            elif event_kind == "vibescript_domain_program_inspection":
                self._apply_loaded_model(str(event.get("model_id") or ""), result)
            else:
                self.status.setText(
                    f"Restored accepted revision {str(result.get('working_revision') or '')[:10]}."
                )
                self.refresh(str(result.get("model_id") or self.model_id))
            return
        if bool(event.get("direct_commit")):
            if (
                not self.editor_active
                or int(event.get("generation") or 0) != self.generation
                or event_engine != "vibescript"
                or self.engine != "vibescript"
            ):
                return
            self.busy = False
            self.button("VibeScriptedRender").setEnabled(True)
            result = event.get("result")
            if not isinstance(result, dict) or result.get("ok") is not True:
                self._show_failure(
                    result
                    if isinstance(result, dict)
                    else {"error": "VibeScript returned no structured result."}
                )
                return
            model = result.get("model")
            if isinstance(model, dict):
                model_id = str(model.get("model_id") or "")
                revision = str(model.get("revision") or "")
            else:
                model_id = str(result.get("program_id") or "")
                revision = str(result.get("accepted_revision") or "")
            if not model_id or not revision:
                self._show_failure(
                    {"error": "VibeScript accepted a result without stable program metadata."}
                )
                return
            self.accepted_revision = revision
            if model_id == self.model_id:
                self._clear_vibescript_draft()
            self._set_dirty(False)
            self.model = {}
            self.diagnostics.clear()
            self.status.setText(
                f"Model built from saved source and inputs ({revision[:10]})."
            )
            self.refresh(model_id)
            return

    def _start_vibescript_apply(self) -> None:
        from VibeCADGui import (
            _dispatch_to_document_thread,
            _ensure_document_thread_invoker,
        )
        from VibeCADSession import apply_domain_vibescript_editor_candidate

        candidate = self.active_vibescript_candidate
        if candidate is None:
            return
        _ensure_document_thread_invoker()
        self.generation += 1
        generation = self.generation
        self.status.setText("Updating the model from the validated build...")
        self.busy = True
        self._update_actions()

        def work(cancelled):
            result = apply_domain_vibescript_editor_candidate(
                get_service(),
                candidate,
                document_thread_dispatch=_dispatch_to_document_thread,
                cancellation_check=cancelled,
            )
            if cancelled():
                return None
            return {
                "event_kind": "vibescript_editor_apply",
                "engine": "vibescript",
                "generation": generation,
                "result": result,
            }

        self.jobs.submit("VibeScript candidate apply", work)

    def _start_vibescript_operation(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Run VibeScript through the same non-blocking lifecycle as AI tools."""

        from VibeCADGui import (
            _dispatch_to_document_thread,
            _ensure_document_thread_invoker,
        )
        from VibeCADSession import run_domain_vibescript_operation

        _ensure_document_thread_invoker()
        self.generation += 1
        generation = self.generation
        self.status.setText("Building VibeScript model in the isolated worker...")
        self.busy = True
        self.button("VibeScriptedRender").setEnabled(False)

        def work(cancelled):
            result = run_domain_vibescript_operation(
                get_service(),
                tool_name,
                arguments,
                document_thread_dispatch=_dispatch_to_document_thread,
                cancellation_check=cancelled,
            )
            if cancelled():
                return None
            return {
                "generation": generation,
                "engine": "vibescript",
                "direct_commit": True,
                "result": result,
            }

        self.jobs.submit("VibeScript lifecycle operation", work)

    def new_model(self):
        name, accepted = self.QtWidgets.QInputDialog.getText(
            self.root, "New VibeScript model", "Model name"
        )
        if not accepted or not name.strip():
            return
        import VibeCADVibeScriptDomains as domain_contracts

        pack = domain_contracts.get_vibescript_pack(get_service().active_workbench_name())
        if pack is None:
            self.status.setText("No active VibeScript domain is available.")
            return
        template = _new_domain_program_template(self.domain, name.strip())
        if template is None:
            self.status.setText(
                f"Create {pack.title} programs through its domain tools; "
                "the editor has no safe empty template for this output type."
            )
            return
        source, output_type = template
        if self.domain == "partdesign":
            properties = {
                key: {"type": "number", "exclusiveMinimum": 0}
                for key in ("width", "depth", "height")
            }
            inputs = {"width": 40.0, "depth": 30.0, "height": 12.0}
        else:
            properties = {}
            inputs = {}
        arguments = {
            "program_name": name.strip(),
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
            "inputs": inputs,
            "expected_outputs": [{"name": "Result", "type": output_type}],
        }
        self._start_vibescript_operation(
            f"vibescript.{self.domain}.create_program", arguments
        )

    def _cancel_preview(self, *, restore_accepted: bool) -> None:
        self.generation += 1

    def _adopt_failed_vibescript_revision(
        self,
        payload: dict[str, Any],
    ) -> bool:
        failed = payload.get("failed_candidate")
        if not isinstance(failed, dict):
            return False
        if str(failed.get("program_id") or "") != self.model_id:
            return False
        revision = str(failed.get("revision") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", revision):
            return False
        self.working_revision = revision
        self.model["working_revision"] = revision
        self.model["source"] = self.source.toPlainText()
        try:
            values = json.loads(self.parameters.toPlainText() or "{}")
        except ValueError:
            values = None
        if isinstance(values, dict):
            self.model["parameters"] = values
        self.active_vibescript_candidate = None
        self._set_dirty(True)
        if self._persist_vibescript_draft():
            self._set_dirty(False)
        return True

    def _show_failure(self, payload: dict[str, Any]):
        self.busy = False
        self.status.setText(str(payload.get("error") or "Scripted model operation failed."))
        self._populate_diagnostics(payload)
        self._update_actions()

    def _populate_diagnostics(self, payload: dict[str, Any]):
        self.diagnostics.clear()
        observed = payload.get("observed") if isinstance(payload, dict) else None
        diagnostics = observed.get("diagnostics") if isinstance(observed, dict) else None
        if not isinstance(diagnostics, list):
            diagnostics = []
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict):
                continue
            line = diagnostic.get("line")
            location = (
                f"{diagnostic.get('file') or 'model'}:{line}"
                if line
                else str(diagnostic.get("file") or "")
            )
            item = self.QtWidgets.QTreeWidgetItem(
                [
                    str(diagnostic.get("severity") or "error"),
                    location,
                    str(diagnostic.get("message") or ""),
                ]
            )
            item.setData(0, self.QtCore.Qt.UserRole, int(line or 0))
            item.setData(0, int(self.QtCore.Qt.UserRole) + 1, str(diagnostic.get("file") or ""))
            self.diagnostics.addTopLevelItem(item)
        if not diagnostics and payload.get("error"):
            self.diagnostics.addTopLevelItem(
                self.QtWidgets.QTreeWidgetItem(["error", "", str(payload["error"])])
            )
        self.diagnostics.resizeColumnToContents(0)
        self.diagnostics.resizeColumnToContents(1)

    def _diagnostic_activated(self, item: Any, _column: int):
        line = int(item.data(0, self.QtCore.Qt.UserRole) or 0)
        if line and hasattr(self.source, "goto_line"):
            self.source.goto_line(line)

    def _update_actions(self):
        active_doc = App.ActiveDocument
        live_document_uid = str(getattr(active_doc, "Uid", "") or "")
        scripted = bool(
            self.editor_active
            and self.engine in SCRIPTED_ENGINES
            and live_document_uid == self.document_uid
        )
        ready = scripted and not self.busy
        points_active = bool(scripted and self.domain == "points")
        new_supported = self.domain in _DOMAIN_EDITOR_NEW_TYPES
        self.button("VibeScriptedNew").setEnabled(ready and new_supported)
        self.button("VibeScriptedSave").setEnabled(
            bool(ready and self.model_id)
        )
        self.button("VibeScriptedRender").setEnabled(bool(ready and self.model_id))
        self.button("VibeScriptedPointArtifactAdd").setEnabled(
            bool(
                points_active
                and not self.busy
                and self.point_artifact_project_root
                and not self.point_artifact_busy
            )
        )
        self.button("VibeScriptedPointArtifactRemove").setEnabled(
            bool(
                points_active
                and not self.busy
                and self.point_artifact_project_root
                and not self.point_artifact_busy
                and self.point_artifact_selector.currentData()
            )
        )


def _register_dock(widget: Any) -> Any:
    main = Gui.getMainWindow()
    if main is None:
        raise RuntimeError("FreeCAD main window is unavailable.")
    add_dock_window = getattr(main, "addDockWindow", None)
    if not callable(add_dock_window):
        raise RuntimeError("FreeCAD DockWindowManager is unavailable.")
    dock = add_dock_window(widget, DOCK_NAME, "right")
    dock.toggleViewAction().setVisible(True)
    return dock


def _register_dock_content(widget: Any) -> None:
    main = Gui.getMainWindow()
    if main is None:
        raise RuntimeError("FreeCAD main window is unavailable.")
    register = getattr(main, "registerDockWindow", None)
    if not callable(register):
        raise RuntimeError("FreeCAD DockWindowManager registration is unavailable.")
    register(widget, DOCK_NAME)


def show_scripted_model_editor(preferred_model_id: str = "") -> None:
    global _controller
    dock = _find_dock()
    if dock is None and _registered_widget is not None:
        raise RuntimeError(
            "The Model Code Editor is registered but the active workbench has "
            "not created its dock window."
        )
    if dock is None or dock.widget() is None:
        widget = _build_widget()
        if dock is None:
            dock = _register_dock(widget)
        else:
            dock.setWidget(widget)
        _controller = ScriptedEditorController(dock)
    elif _controller is None or _controller.dock is not dock:
        _controller = ScriptedEditorController(dock)
    dock.show()
    dock.raise_()
    if not _controller.editor_active:
        _controller.activate(preferred_model_id)
    elif preferred_model_id:
        _controller.refresh(preferred_model_id)


def ensure_scripted_model_editor_registered() -> Any:
    """Register native dock content once so View > Panels can reopen it."""
    global _controller, _registered_widget
    dock = _find_dock()
    if dock is None:
        if _registered_widget is None:
            widget = _build_widget()
            _register_dock_content(widget)
            _registered_widget = widget
        return _registered_widget
    if dock.widget() is None:
        widget = _build_widget()
        dock.setWidget(widget)
        dock.hide()
        _controller = ScriptedEditorController(dock)
    elif _controller is None or _controller.dock is not dock:
        _controller = ScriptedEditorController(dock)
        if dock.isVisible():
            _controller.activate()
    dock.toggleViewAction().setVisible(True)
    return dock


def refresh_scripted_model_editor() -> None:
    global _controller, _refresh_retry_pending
    doc = App.ActiveDocument
    if _document_restore_active(doc) or (
        doc is not None and bool(getattr(doc, "Recomputing", False))
    ):
        if not _refresh_retry_pending:
            from PySide import QtCore

            _refresh_retry_pending = True

            def retry() -> None:
                global _refresh_retry_pending
                _refresh_retry_pending = False
                refresh_scripted_model_editor()

            QtCore.QTimer.singleShot(100, retry)
        return
    _refresh_retry_pending = False
    dock = _find_dock()
    if dock is not None and (_controller is None or _controller.dock is not dock):
        _controller = ScriptedEditorController(dock)
        if dock.isVisible():
            _controller.activate()
            return
    if _controller is not None:
        _controller.refresh()


def automated_model_update_started(engine: str, document_name: str, model_id: str) -> None:
    if _controller is not None:
        _controller.automated_update_started(engine, document_name, model_id)


def automated_model_update_finished(engine: str, document_name: str, model_id: str) -> None:
    if _controller is not None:
        _controller.automated_update_finished(engine, document_name, model_id)
