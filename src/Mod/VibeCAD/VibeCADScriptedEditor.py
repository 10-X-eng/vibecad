# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human source editor and live-preview controller for scripted PartDesign models."""

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
PREVIEW_MARKER = "VibeCADTransientScriptedPreview"
PREVIEW_MODEL_ID = "VibeCADPreviewModelId"
PREVIEW_REVISION = "VibeCADPreviewRevision"
EDITOR_PREFERENCES = "User parameter:BaseApp/Preferences/VibeCAD/ModelCodeEditor"

_controller: Any | None = None
_registered_widget: Any | None = None
_preview_containers: dict[tuple[str, str], str] = {}
_hidden_accepted: dict[tuple[str, str], list[str]] = {}
_save_preview_restore: dict[str, tuple[str, str, str]] = {}
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


def _document_key(doc: Any) -> str:
    return str(getattr(doc, "Uid", "") or getattr(doc, "Name", "") or "")


def _find_dock() -> Any | None:
    from PySide import QtWidgets

    main = Gui.getMainWindow()
    return main.findChild(QtWidgets.QDockWidget, DOCK_NAME) if main is not None else None


SCRIPTED_ENGINES = {"build123d", "openscad", "vibescript"}
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


def _engine_api(engine: str):
    if engine == "build123d":
        import VibeCADBuild123d as api

        return api
    if engine == "openscad":
        import VibeCADOpenSCAD as api

        return api
    raise RuntimeError("The selected scripted engine has no direct editor runtime.")


def _model_source_path(engine: str, model: dict[str, Any]) -> Path | None:
    if engine == "vibescript":
        return None
    directory = str(model.get("artifact_directory") or "").strip()
    if not directory:
        return None
    return Path(directory) / ("model.scad" if engine == "openscad" else "model.py")


def _schema_requires_document_references(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("x-vibecad-reference") is True:
            return True
        return any(_schema_requires_document_references(item) for item in value.values())
    if isinstance(value, list):
        return any(_schema_requires_document_references(item) for item in value)
    return False


def _add_string_property(obj: Any, name: str) -> None:
    if name not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", name, "VibeCAD Preview")


def _set_shaded_display(obj: Any) -> None:
    view = getattr(obj, "ViewObject", None)
    if view is None:
        # Headless sessions have no view providers; skip display styling.
        return
    modes = list(view.listDisplayModes())
    if "Shaded" not in modes:
        raise RuntimeError(
            f"Preview object {obj.Name} cannot use Shaded display mode. Available modes: {modes}"
        )
    view.DisplayMode = "Shaded"


def _accepted_objects(doc: Any, engine: str, model_id: str) -> list[Any]:
    property_name = {
        "build123d": "VibeCADBuild123dModelId",
        "openscad": "VibeCADOpenSCADModelId",
    }[engine]
    return [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if property_name in list(getattr(obj, "PropertiesList", []) or [])
        and str(getattr(obj, property_name, "") or "") == model_id
    ]


def _accepted_output_features(doc: Any, model: dict[str, Any]) -> list[Any]:
    """Resolve only manifest-owned output features, never their duplicate Bodies."""
    features: list[Any] = []
    seen: set[str] = set()
    outputs = model.get("outputs")
    if not isinstance(outputs, dict):
        return features
    for item in outputs.values():
        if not isinstance(item, dict):
            continue
        name = str(item.get("feature") or item.get("object") or "").strip()
        if not name or name in seen:
            continue
        obj = doc.getObject(name)
        shape = getattr(obj, "Shape", None) if obj is not None else None
        if shape is None or shape.isNull():
            continue
        seen.add(name)
        features.append(obj)
    return features


def _json_merge_patch(before: Any, after: Any) -> Any:
    """Return an RFC 7396-style patch that transforms before into after."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return after
    patch: dict[str, Any] = {}
    for key in before.keys() - after.keys():
        patch[key] = None
    for key, value in after.items():
        if key not in before:
            patch[key] = value
            continue
        old_value = before[key]
        if isinstance(old_value, dict) and isinstance(value, dict):
            nested = _json_merge_patch(old_value, value)
            if nested:
                patch[key] = nested
        elif old_value != value:
            patch[key] = value
    return patch


def _restore_accepted_visibility(doc: Any, model_id: str) -> None:
    key = (_document_key(doc), model_id)
    names = _hidden_accepted.pop(key, [])
    for name in names:
        obj = doc.getObject(name)
        if obj is not None:
            try:
                obj.ViewObject.Visibility = True
            except Exception as exc:
                _warn(f"Could not restore visibility for {name}: {exc}")


def _remove_preview_container(doc: Any, container_name: str) -> None:
    container = doc.getObject(container_name)
    if container is None:
        return
    child_names = [str(child.Name) for child in list(getattr(container, "Group", []) or [])]
    for child_name in child_names:
        if doc.getObject(child_name) is not None:
            doc.removeObject(child_name)
    if doc.getObject(container_name) is not None:
        doc.removeObject(container_name)


def remove_preview(doc: Any, model_id: str, *, restore_accepted: bool = True) -> None:
    key = (_document_key(doc), model_id)
    object_name = _preview_containers.pop(key, "")
    container = doc.getObject(object_name) if object_name else None
    if container is not None:
        _remove_preview_container(doc, object_name)
    if restore_accepted:
        _restore_accepted_visibility(doc, model_id)


def remove_all_previews(doc: Any | None = None) -> list[dict[str, str]]:
    targets = (
        [doc] if doc is not None else list(getattr(App, "listDocuments", lambda: {})().values())
    )
    removed: list[dict[str, str]] = []
    for current in targets:
        if current is None:
            continue
        previews: list[tuple[str, str]] = []
        for obj in list(getattr(current, "Objects", []) or []):
            if PREVIEW_MARKER not in list(getattr(obj, "PropertiesList", []) or []):
                continue
            previews.append(
                (
                    str(obj.Name),
                    str(getattr(obj, PREVIEW_MODEL_ID, "") or ""),
                )
            )
        for object_name, model_id in previews:
            removed.append({"document": current.Name, "model_id": model_id})
            _remove_preview_container(current, object_name)
            _preview_containers.pop((_document_key(current), model_id), None)
            _restore_accepted_visibility(current, model_id)
    return removed


def _show_preview(
    engine: str,
    prepared: dict[str, Any],
    imported: list[dict[str, Any]],
    *,
    frame: bool = True,
) -> None:
    doc = App.ActiveDocument
    if doc is None or doc.Name != prepared["document_name"]:
        return
    model_id = prepared["model_id"]
    remove_preview(doc, model_id, restore_accepted=True)
    hidden: list[str] = []
    for obj in _accepted_objects(doc, engine, model_id):
        try:
            if obj.ViewObject.Visibility:
                hidden.append(obj.Name)
                obj.ViewObject.Visibility = False
        except Exception as exc:
            _warn(f"Could not hide accepted object {obj.Name}: {exc}")
    _hidden_accepted[(_document_key(doc), model_id)] = hidden
    container = doc.addObject("App::Part", f"VibeCADPreview_{model_id[:8]}")
    container.Label = f"Preview - {prepared['model_name']}"
    for prop in (PREVIEW_MARKER, PREVIEW_MODEL_ID, PREVIEW_REVISION):
        _add_string_property(container, prop)
    setattr(container, PREVIEW_MARKER, "true")
    setattr(container, PREVIEW_MODEL_ID, model_id)
    setattr(container, PREVIEW_REVISION, prepared["revision"])
    for index, item in enumerate(imported, start=1):
        feature = doc.addObject("Part::Feature", f"VibeCADPreviewShape_{model_id[:8]}_{index:03d}")
        feature.Label = f"Preview - {item.get('key') or index}"
        feature.Shape = item["shape"]
        container.addObject(feature)
        try:
            _set_shaded_display(feature)
            feature.ViewObject.ShapeColor = (0.18, 0.68, 0.86)
            feature.ViewObject.LineColor = (0.75, 0.92, 1.0)
            feature.ViewObject.Transparency = 18
        except Exception as exc:
            _warn(f"Could not style preview object {feature.Name}: {exc}")
    _preview_containers[(_document_key(doc), model_id)] = container.Name
    if frame:
        try:
            Gui.activeDocument().activeView().fitAll()
        except Exception as exc:
            _warn(f"Could not frame scripted preview: {exc}")


def _read_scad_project(entry: Path) -> dict[str, str]:
    """Read the main source and project-local include/use graph."""
    root = entry.parent.resolve()
    queue = [entry.resolve()]
    copied: set[Path] = set()
    source_files: dict[str, str] = {}
    include_pattern = re.compile(r"\b(?:include|use)\s*<([^>]+)>")
    while queue:
        source_path = queue.pop(0)
        if source_path in copied:
            continue
        copied.add(source_path)
        try:
            relative = source_path.relative_to(root)
        except ValueError:
            continue
        text = source_path.read_text(encoding="utf-8")
        project_name = "model.scad" if source_path == entry.resolve() else relative.as_posix()
        source_files[project_name] = text
        for match in include_pattern.finditer(text):
            include_path = (source_path.parent / match.group(1)).resolve()
            if root == include_path or root in include_path.parents:
                if include_path.is_file():
                    queue.append(include_path)
    return dict(sorted(source_files.items()))


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
                    widget.addItem(text, dict(reference))
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
            if engine == "openscad":
                keywords = [
                    "module",
                    "function",
                    "include",
                    "use",
                    "for",
                    "if",
                    "else",
                    "let",
                    "each",
                    "true",
                    "false",
                    "undef",
                ]
            elif engine == "json":
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
                patterns.append(
                    (r"//[^\n]*" if engine == "openscad" else r"#[^\n]*", comment_color)
                )
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
    fidelity_selector = QtWidgets.QComboBox(selector_row)
    fidelity_selector.setObjectName("VibeScriptedFidelitySelector")
    fidelity_selector.setToolTip("OpenSCAD geometry fidelity")
    fidelity_selector.addItem("Exact BREP", "exact_brep")
    fidelity_selector.addItem("Faceted BREP", "faceted_brep")
    selector_layout.addWidget(fidelity_selector)
    toolbar_layout.addWidget(selector_row)

    actions_layout = QtWidgets.QGridLayout()
    actions_layout.setContentsMargins(0, 0, 0, 0)
    actions_layout.setHorizontalSpacing(6)
    actions_layout.setVerticalSpacing(6)
    actions = (
        ("New", "VibeScriptedNew", "Create a new source-backed model"),
        ("Build", "VibeScriptedRender", "Build and validate the current working source"),
        ("Apply", "VibeScriptedAccept", "Publish the current validated candidate"),
        (
            "Revert",
            "VibeScriptedRevert",
            "Restore the last accepted source and geometry",
        ),
        ("Import", "VibeScriptedImport", "Import an OpenSCAD source project"),
        ("Export", "VibeScriptedExport", "Export accepted scripted geometry"),
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
    file_selector = QtWidgets.QComboBox(source_panel)
    file_selector.setObjectName("VibeScriptedFileSelector")
    file_selector.setToolTip("OpenSCAD project source file")
    source_layout.addWidget(file_selector)
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

    watcher = QtCore.QFileSystemWatcher(root)
    watcher.setObjectName("VibeScriptedSourceWatcher")
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
        self.engine = "native"
        self.domain = ""
        self.document_name = ""
        self.document_uid = ""
        self.model_id = ""
        self.working_revision = ""
        self.accepted_revision = ""
        self.model: dict[str, Any] = {}
        self.source_path: Path | None = None
        self.source_files: dict[str, str] = {}
        self.current_source_file = "model.scad"
        self.loading = False
        self.generation = 0
        self.active_prepared: dict[str, Any] | None = None
        self.active_execution: dict[str, Any] | None = None
        self.active_imported: list[dict[str, Any]] | None = None
        self.active_engine = ""
        self.preview_revision = ""
        self.active_vibescript_candidate: dict[str, Any] | None = None
        self.dirty = False
        self.busy = False
        self.reference_options: list[dict[str, str]] = []
        self.editor_active = False
        self.point_artifact_generation = 0
        self.point_artifact_busy = False
        self.point_artifact_project_root = ""
        self.point_artifact_loaded_root = ""
        self.external_source_generation = 0
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
    def fidelity_selector(self):
        return self.child(self.QtWidgets.QComboBox, "VibeScriptedFidelitySelector")

    @property
    def file_selector(self):
        return self.child(self.QtWidgets.QComboBox, "VibeScriptedFileSelector")

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

    @property
    def watcher(self):
        return self.root.findChild(self.QtCore.QFileSystemWatcher, "VibeScriptedSourceWatcher")

    def button(self, name: str):
        return self.child(self.QtWidgets.QPushButton, name)

    def _connect(self):
        self.dock.visibilityChanged.connect(self._visibility_changed)
        self.dock.destroyed.connect(lambda _obj=None: self.jobs.close())
        self.dock.destroyed.connect(lambda _obj=None: self.point_jobs.close())
        self.selector.currentIndexChanged.connect(self._select_model)
        self.fidelity_selector.currentIndexChanged.connect(self._fidelity_changed)
        self.file_selector.currentIndexChanged.connect(self._select_source_file)
        self.source.textChanged.connect(self._source_changed)
        self.parameters.textChanged.connect(self._parameters_changed)
        self.inputs.changed.connect(self._schema_inputs_changed)
        self.watcher.fileChanged.connect(self._external_file_changed)
        self.root._vibecad_bridge.completed.connect(self._preview_completed)
        self.button("VibeScriptedNew").clicked.connect(self.new_model)
        self.button("VibeScriptedImport").clicked.connect(self.import_model)
        self.button("VibeScriptedRender").clicked.connect(self.render)
        self.button("VibeScriptedAccept").clicked.connect(self.accept)
        self.button("VibeScriptedRevert").clicked.connect(self.revert)
        self.button("VibeScriptedExport").clicked.connect(self.export)
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

    def activate(self):
        if self.editor_active:
            self.refresh()
            return
        self.editor_active = True
        self.refresh()

    def deactivate(self):
        self.editor_active = False
        self.jobs.cancel_pending()
        self.point_jobs.cancel_pending()
        self.busy = False
        self.point_artifact_busy = False
        self._clear_source_watch()

    def automated_update_started(self, engine: str, document_name: str, model_id: str):
        if (
            not self.editor_active
            or engine != self.engine
            or model_id != self.model_id
            or document_name != str(getattr(App.ActiveDocument, "Name", "") or "")
        ):
            return
        self._clear_source_watch()
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

    def _clear_source_watch(self):
        for path in list(self.watcher.files()):
            self.watcher.removePath(path)

    def _clear_model_fields(self):
        self.model_id = ""
        self.working_revision = ""
        self.accepted_revision = ""
        self.model = {}
        self.source_path = None
        self.source_files = {}
        self.current_source_file = "model.scad"
        self.active_vibescript_candidate = None
        self.reference_options = []
        self._set_dirty(False)

    def _clear_editors(self):
        self.loading = True
        self.source.clear()
        self.parameters.clear()
        self.inputs.set_contract({}, {}, [])
        self.file_selector.clear()
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

    def _deselect_model(self, *, update_selector: bool):
        self._cancel_preview(restore_accepted=True)
        self._clear_source_watch()
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
                "The active document, workbench, or modeling engine changed. "
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
            self._clear_source_watch()
            self._clear_model_fields()
        self.engine = next_engine
        self.domain = next_domain
        self.document_name = next_document_name
        self.document_uid = next_document_uid
        scripted = self.engine in SCRIPTED_ENGINES and resolution.available
        self.root.setEnabled(scripted)
        self.context_label.setText(
            (
                f"{self.engine} · {self.domain} · {next_document_name or 'no document'}"
                if scripted
                else "No active scripted domain"
            )
        )
        points_active = bool(scripted and self.engine == "vibescript" and self.domain == "points")
        self.point_artifact_row.setVisible(points_active)
        if points_active and not self.point_artifact_busy:
            self._start_point_artifact_refresh()
        else:
            self.point_artifact_generation += 1
            self.point_artifact_busy = False
            self.point_artifact_project_root = ""
            self.point_artifact_loaded_root = ""
            self._set_point_artifact_items([], "Point data is available in Points.")
        self.button("VibeScriptedImport").setVisible(self.engine == "openscad")
        self.button("VibeScriptedExport").setVisible(self.engine != "vibescript")
        self.file_selector.setVisible(self.engine == "openscad")
        self.fidelity_selector.setVisible(self.engine == "openscad")
        if not scripted:
            self._clear_source_watch()
            self._clear_model_fields()
            self.loading = True
            self.selector.clear()
            self.selector.addItem("None", "")
            self.source.clear()
            self.parameters.clear()
            self.inputs.set_contract({}, {}, [])
            self.file_selector.clear()
            self.source_files = {}
            self.loading = False
            self.status.setText(
                resolution.unavailable_reason
                or "Select a scripted global modeling engine for this workbench."
            )
            return
        if self.engine == "vibescript":
            self._start_vibescript_model_refresh(preferred_model_id)
            return
        root = str(service.project_scope_snapshot().get("root") or "").strip()
        self._start_direct_model_refresh(doc, root, preferred_model_id)

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
            self.selector.addItem(f"{label}  [{state}]", str(item.get("model_id") or ""))
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

    def _start_direct_model_refresh(
        self,
        doc: Any | None,
        project_root: str,
        preferred_model_id: str = "",
    ) -> None:
        """Load direct-engine artifacts without filesystem I/O on the GUI thread."""

        api = _engine_api(self.engine)
        snapshot = (
            api.editor_model_index_snapshot(doc)
            if doc is not None
            else {"native_models": []}
        )
        self.generation += 1
        generation = self.generation
        engine = self.engine
        self.status.setText(f"Loading {engine} models...")
        self.busy = True
        self._update_actions()

        def work(cancelled):
            try:
                models = (
                    api.complete_editor_model_index(snapshot, project_root)
                    if project_root
                    else []
                )
                result = {"ok": True, "models": models}
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            if cancelled():
                return None
            return {
                "event_kind": "direct_model_list",
                "engine": engine,
                "generation": generation,
                "preferred_model_id": preferred_model_id,
                "result": result,
            }

        self.jobs.submit(f"{engine} model index", work)

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
        if self.engine == "vibescript":
            self._start_vibescript_model_inspection(model_id)
            return
        self._start_direct_model_inspection(model_id)

    def _start_direct_model_inspection(self, model_id: str) -> None:
        api = _engine_api(self.engine)
        snapshot = api.editor_model_inspection_snapshot(get_service(), model_id)
        if snapshot.get("ok") is not True:
            self._show_failure(snapshot)
            return
        self.generation += 1
        generation = self.generation
        engine = self.engine
        self.status.setText(f"Loading {engine} source and model metadata...")
        self.busy = True
        self._update_actions()

        def work(cancelled):
            try:
                result = api.complete_editor_model_inspection(snapshot)
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                }
            if cancelled():
                return None
            return {
                "event_kind": "direct_model_inspection",
                "engine": engine,
                "generation": generation,
                "model_id": model_id,
                "result": result,
            }

        self.jobs.submit(f"{engine} model inspection", work)

    def _apply_loaded_model(self, model_id: str, result: dict[str, Any]) -> None:
        previous_engine = str(getattr(self.root, "_vibecad_source_highlighter_engine", ""))
        self.model = dict(result["model"])
        self.model_id = model_id
        self.working_revision = str(self.model.get("working_revision") or "")
        self.accepted_revision = str(self.model.get("accepted_revision") or "")
        self.source_path = _model_source_path(self.engine, self.model)
        main_name = "model.scad" if self.engine == "openscad" else "model.py"
        source_files = self.model.get("source_files")
        if not isinstance(source_files, dict):
            source_files = {main_name: str(self.model.get("source") or "")}
        self.source_files = {str(path): str(content) for path, content in source_files.items()}
        self.current_source_file = (
            main_name
            if main_name in self.source_files
            else next(iter(self.source_files), main_name)
        )
        input_values = dict(self.model.get("parameters") or {})
        input_schema = dict(self.model.get("input_schema") or {})
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
        self.file_selector.clear()
        for path in sorted(self.source_files, key=lambda value: (value != main_name, value)):
            self.file_selector.addItem(path, path)
        selected_file = self.file_selector.findData(self.current_source_file)
        if selected_file >= 0:
            self.file_selector.setCurrentIndex(selected_file)
        source_text = self.source_files.get(self.current_source_file, "")
        if self.source.toPlainText() != source_text:
            self.source.setPlainText(source_text)
        parameters_text = json.dumps(input_values, indent=2, sort_keys=True)
        if self.parameters.toPlainText() != parameters_text:
            self.parameters.setPlainText(parameters_text)
        self.inputs.set_contract(input_schema, input_values, self.reference_options)
        if self.engine == "openscad":
            mode_index = self.fidelity_selector.findData(
                str(self.model.get("conversion_mode") or "")
            )
            if mode_index < 0:
                self.loading = False
                self.status.setText("OpenSCAD model has no valid conversion mode.")
                self._update_actions()
                return
            self.fidelity_selector.setCurrentIndex(mode_index)
        self.loading = False
        if previous_engine != self.engine:
            self._install_highlighter()
        self._watch_source()
        self.active_vibescript_candidate = None
        self._set_dirty(False)
        fidelity = str(self.model.get("fidelity") or "not built")
        conversion = str(self.model.get("conversion_mode") or "")
        self.status.setText(
            f"{self.engine} | working {self.working_revision[:10]} | "
            f"accepted {self.accepted_revision[:10] or 'none'} | "
            f"{conversion + ' | ' if conversion else ''}{fidelity}"
        )
        self.diagnostics.clear()
        latest = self.model.get("latest_attempt") or {}
        failure = latest.get("failure") if isinstance(latest, dict) else None
        if isinstance(failure, dict):
            self._populate_diagnostics(failure)
        self._update_actions()

    def _select_source_file(self, index: int):
        if self.loading or index < 0:
            return
        if self.current_source_file:
            self.source_files[self.current_source_file] = self.source.toPlainText()
        target = str(self.file_selector.itemData(index) or "")
        if not target or target == self.current_source_file:
            return
        self.current_source_file = target
        self.loading = True
        self.source.setPlainText(self.source_files.get(target, ""))
        self.loading = False
        self._install_highlighter()

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

    def _watch_source(self):
        self._clear_source_watch()
        if not self.editor_active or not self.model_id or self.source_path is None:
            return
        directory = self.source_path.parent
        for name in self.source_files:
            path = directory / name
            if path.is_file():
                self.watcher.addPath(str(path))

    def _source_changed(self):
        if self.loading or not self.editor_active or not self.model_id:
            return
        first_change = not self.dirty
        if first_change or self.busy or self.active_prepared is not None:
            self._invalidate_preview_for_edit()
        self._set_dirty(True)
        if first_change:
            self.status.setText("Source modified. Press Build to validate it.")

    def _parameters_changed(self):
        if self.loading or not self.editor_active or not self.model_id:
            return
        first_change = not self.dirty
        if first_change or self.busy or self.active_prepared is not None:
            self._invalidate_preview_for_edit()
        self._set_dirty(True)
        if first_change:
            self.status.setText("Inputs modified. Press Build to validate them.")

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
        self.status.setText("Inputs modified. Press Build to validate them.")

    def _fidelity_changed(self, _index: int):
        if self.loading or not self.editor_active or self.engine != "openscad" or not self.model_id:
            return
        self._invalidate_preview_for_edit()
        self._set_dirty(True)
        self.status.setText("OpenSCAD fidelity changed. Press Build to validate it.")

    def _conversion_mode(self) -> str:
        mode = str(self.fidelity_selector.currentData() or "")
        if mode not in {"exact_brep", "faceted_brep"}:
            raise RuntimeError("Select Exact BREP or Faceted BREP before rendering.")
        return mode

    def _external_file_changed(self, path: str):
        if not self.editor_active or not self.model_id:
            return
        if self.source_path is None:
            return
        self.external_source_generation += 1
        external_generation = self.external_source_generation
        source_root = self.source_path.parent
        engine = self.engine
        model_id = self.model_id
        document_uid = self.document_uid

        def work(cancelled):
            source_path = Path(path)
            try:
                if not source_path.is_file():
                    return None
                relative = source_path.resolve().relative_to(source_root.resolve()).as_posix()
                content = source_path.read_text(encoding="utf-8")
                result = {"ok": True, "relative": relative, "content": content}
            except (OSError, ValueError) as exc:
                result = {"ok": False, "error": str(exc)}
            if cancelled():
                return None
            return {
                "event_kind": "external_source_loaded",
                "engine": engine,
                "model_id": model_id,
                "document_uid": document_uid,
                "external_generation": external_generation,
                "result": result,
            }

        self.point_jobs.submit("external source reload", work)

    def _apply_external_source_change(self, result: dict[str, Any]) -> None:
        if result.get("ok") is not True:
            self.status.setText(
                f"Could not reload external source change: "
                f"{result.get('error') or 'unknown error'}"
            )
            return
        relative = str(result.get("relative") or "")
        content = str(result.get("content") or "")
        previous = self.source_files.get(relative)
        self.source_files[relative] = content
        if content == previous:
            self._watch_source()
            return
        self._invalidate_preview_for_edit()
        if relative == self.current_source_file and content != self.source.toPlainText():
            cursor_position = self.source.textCursor().position()
            self.loading = True
            self.source.setPlainText(content)
            cursor = self.source.textCursor()
            cursor.setPosition(min(cursor_position, len(content)))
            self.source.setTextCursor(cursor)
            self.loading = False
        self.status.setText(f"External source updated in {relative}. Press Build to validate it.")
        self._set_dirty(True)
        self._watch_source()
        self._update_actions()

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
        if schema:
            self.inputs.set_contract(schema, value, self.reference_options)
        return value

    def _invalidate_preview_for_edit(self):
        self.jobs.cancel_pending()
        self.busy = False
        self._cancel_preview(restore_accepted=True)
        self.active_vibescript_candidate = None

    def render(self):
        if not self.editor_active or not self.model_id or self.engine not in SCRIPTED_ENGINES:
            return
        parameters = self._parse_parameters()
        if parameters is None:
            return
        self.source_files[self.current_source_file] = self.source.toPlainText()
        if self.engine == "vibescript":
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
            return
        api = _engine_api(self.engine)
        try:
            if self.engine == "openscad":
                source_stage = api.stage_editor_files(
                    get_service(),
                    self.model_id,
                    self.working_revision,
                    self.source_files,
                    self._conversion_mode(),
                )
            else:
                source_stage = api.stage_editor_source(
                    get_service(),
                    self.model_id,
                    self.working_revision,
                    self.source_files.get("model.py", self.source.toPlainText()),
                )
            self.working_revision = str(source_stage["working_revision"])
            if self.engine == "openscad":
                self.model["conversion_mode"] = str(source_stage["conversion_mode"])
            current_parameters = self.model.get("parameters") or {}
            if parameters != current_parameters:
                patch = _json_merge_patch(current_parameters, parameters)
                operation = f"{self.engine}.set_parameters"
                arguments = {
                    "model_id": self.model_id,
                    "expected_revision": self.working_revision,
                    "patch": patch,
                }
                prepared = api.prepare_execution(
                    get_service(),
                    operation,
                    arguments,
                )
            else:
                prepared = api.prepare_execution(
                    get_service(),
                    f"{self.engine}.editor_rebuild",
                    {
                        "model_id": self.model_id,
                        "expected_revision": self.working_revision,
                    },
                )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        self.working_revision = str(prepared["revision"])
        self.model["parameters"] = parameters
        self.generation += 1
        generation = self.generation
        engine = self.engine
        self.status.setText(f"Building {self.engine} candidate {self.working_revision[:10]}...")
        self.busy = True
        self.button("VibeScriptedRender").setEnabled(False)

        def work(cancelled):
            execution = api.execute_prepared(
                prepared,
                cancellation_check=cancelled,
            )
            if cancelled():
                return None
            return {
                "generation": generation,
                "engine": engine,
                "prepared": prepared,
                "execution": execution,
            }

        self.jobs.submit("scripted candidate build", work)

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
        self.status.setText("Building and validating candidate in the isolated worker...")
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
        if event_kind == "external_source_loaded":
            if (
                not self.editor_active
                or event_engine != self.engine
                or str(event.get("model_id") or "") != self.model_id
                or str(event.get("document_uid") or "") != self.document_uid
                or int(event.get("external_generation") or 0)
                != self.external_source_generation
            ):
                return
            self._apply_external_source_change(dict(event.get("result") or {}))
            return
        if event_kind in {"direct_model_list", "direct_model_inspection"}:
            if (
                not self.editor_active
                or event_engine != self.engine
                or self.engine not in {"build123d", "openscad"}
                or int(event.get("generation") or 0) != self.generation
            ):
                return
            self.busy = False
            result = event.get("result")
            if not isinstance(result, dict) or result.get("ok") is not True:
                self._show_failure(
                    result
                    if isinstance(result, dict)
                    else {"error": "The scripted editor returned no structured result."}
                )
                return
            if event_kind == "direct_model_list":
                self._apply_model_list(
                    list(result.get("models") or []),
                    str(event.get("preferred_model_id") or ""),
                )
            else:
                self._apply_loaded_model(str(event.get("model_id") or ""), result)
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
                self._show_failure(
                    result
                    if isinstance(result, dict)
                    else {"error": "The VibeScript build returned no structured result."}
                )
                return
            candidate = result.get("_editor_candidate")
            if not isinstance(candidate, dict):
                self._show_failure(
                    {"error": "The VibeScript build returned no validated editor candidate."}
                )
                return
            self.active_vibescript_candidate = candidate
            prepared_candidate = candidate.get("prepared")
            if isinstance(prepared_candidate, dict):
                self.model["source"] = str(prepared_candidate.get("source") or "")
                self.model["parameters"] = dict(prepared_candidate.get("inputs") or {})
            self.working_revision = str(result.get("working_revision") or "")
            self.preview_revision = self.working_revision
            self._set_dirty(False)
            self.diagnostics.clear()
            self.status.setText(
                f"Build passed | candidate {self.working_revision[:10]} | "
                "accepted geometry is unchanged. Press Apply to publish it."
            )
            self._update_actions()
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
            self.preview_revision = ""
            self.status.setText(
                f"Applied VibeScript revision {revision[:10]} to stable native outputs."
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
            self.preview_revision = ""
            self.diagnostics.clear()
            self.status.setText(
                f"Accepted VibeScript revision {revision[:10]} | native typed outputs"
            )
            self.refresh(model_id)
            return
        prepared = event["prepared"]
        if (
            not self.editor_active
            or int(event.get("generation") or 0) != self.generation
            or event_engine != self.engine
            or str(prepared.get("model_id") or "") != self.model_id
            or str(prepared.get("document_name") or "")
            != str(getattr(App.ActiveDocument, "Name", "") or "")
        ):
            _engine_api(event_engine).cleanup_prepared(prepared)
            return
        self.button("VibeScriptedRender").setEnabled(True)
        self.busy = False
        execution = event["execution"]
        api = _engine_api(event_engine)
        if not execution.get("ok"):
            try:
                api.record_failed_attempt(prepared, execution)
            except Exception as exc:
                _warn(f"Could not record failed preview: {exc}")
            self._show_failure(execution)
            api.cleanup_prepared(prepared)
            return
        try:
            imported = api.import_validated_outputs(prepared, execution)
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            api.cleanup_prepared(prepared)
            return
        if self.active_prepared is not None:
            _engine_api(self.active_engine).cleanup_prepared(self.active_prepared)
        self.active_prepared = prepared
        self.active_execution = execution
        self.active_imported = imported
        self.active_engine = event_engine
        self.preview_revision = str(prepared["revision"])
        _show_preview(event_engine, prepared, imported)
        fidelity = str(
            execution.get("fidelity")
            or ("exact_brep" if event_engine == "build123d" else "unknown")
        )
        self.status.setText(
            f"Live preview ready | revision {self.preview_revision[:10]} | {fidelity}. "
            "Accepted document geometry is unchanged."
        )
        self._set_dirty(False)
        self.diagnostics.clear()
        self._update_actions()

    def accept(self):
        if self.engine == "vibescript":
            if self.active_vibescript_candidate is None:
                self.status.setText("Build this revision successfully before applying it.")
                return
            self._start_vibescript_apply()
            return
        if (
            self.active_prepared is None
            or self.active_execution is None
            or self.active_imported is None
            or self.preview_revision != self.working_revision
        ):
            self.status.setText("The current working revision has no valid preview to accept.")
            return
        self.generation += 1
        self._clear_source_watch()
        api = _engine_api(self.active_engine)
        doc = App.ActiveDocument
        if doc is not None:
            remove_preview(doc, self.model_id, restore_accepted=True)
        try:
            result = api.commit_outputs(
                get_service(),
                self.active_prepared,
                self.active_execution,
                self.active_imported,
            )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            self._watch_source()
            return
        api.cleanup_prepared(self.active_prepared)
        self.active_prepared = None
        self.active_execution = None
        self.active_imported = None
        self.active_engine = ""
        self.accepted_revision = self.working_revision
        self.preview_revision = ""
        self.status.setText(
            f"Accepted {self.engine} revision {self.accepted_revision[:10]} | "
            f"{result.get('fidelity') or 'exact_brep'}"
        )
        self.refresh(self.model_id)

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
        self.status.setText("Applying validated candidate to stable native outputs...")
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

    def revert(self):
        if not self.model_id:
            return
        if self.dirty:
            answer = self.QtWidgets.QMessageBox.warning(
                self.root,
                "Discard unbuilt changes?",
                "Revert this editor to the last accepted source and inputs?",
                self.QtWidgets.QMessageBox.Discard | self.QtWidgets.QMessageBox.Cancel,
                self.QtWidgets.QMessageBox.Cancel,
            )
            if answer != self.QtWidgets.QMessageBox.Discard:
                return
        self.generation += 1
        self.active_vibescript_candidate = None
        self._clear_source_watch()
        if self.engine == "vibescript":
            accepted = self.model.get("accepted_contract")
            if not isinstance(accepted, dict):
                self.status.setText("This program has no accepted revision to restore.")
                self._update_actions()
                return
            self._start_vibescript_operation(
                f"vibescript.{self.domain}.reconfigure_program",
                {
                    "program_id": self.model_id,
                    "expected_revision": self.working_revision,
                    "source": str(accepted.get("source") or ""),
                    "input_schema": dict(accepted.get("input_schema") or {}),
                    "inputs": dict(accepted.get("inputs") or {}),
                    "expected_outputs": list(accepted.get("expected_outputs") or []),
                },
            )
            return
        api = _engine_api(self.engine)
        try:
            result = api.revert_working_to_accepted(get_service(), self.model_id)
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            self._watch_source()
            return
        if self.active_prepared is not None:
            api.cleanup_prepared(self.active_prepared)
        self.active_prepared = None
        self.active_execution = None
        self.active_imported = None
        self.active_engine = ""
        self.preview_revision = ""
        doc = App.ActiveDocument
        if doc is not None:
            remove_preview(doc, self.model_id, restore_accepted=True)
        self.status.setText(f"Restored accepted revision {result['working_revision'][:10]}.")
        self.refresh(self.model_id)

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
            self.root, f"New {self.engine} model", "Model name"
        )
        if not accepted or not name.strip():
            return
        if self.engine == "openscad":
            source = (
                "width = 40;\n"
                "depth = 30;\n"
                "height = 12;\n\n"
                "cube([width, depth, height], center = true);\n"
            )
            arguments = {
                "model_name": name.strip(),
                "source": source,
                "parameters": {},
                "conversion_mode": self._conversion_mode(),
            }
        elif self.engine == "vibescript":
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
            self._start_vibescript_operation(f"vibescript.{self.domain}.create_program", arguments)
            return
        else:
            source = (
                "from build123d import Box\n\n"
                "width = params.get('width', 40.0)\n"
                "depth = params.get('depth', 30.0)\n"
                "height = params.get('height', 12.0)\n"
                "result = {'Part': Box(width, depth, height)}\n"
            )
            arguments = {
                "model_name": name.strip(),
                "source": source,
                "parameters": {"width": 40.0, "depth": 30.0, "height": 12.0},
                "input_objects": {},
                "expected_outputs": ["Part"],
            }
        try:
            prepared = _engine_api(self.engine).prepare_execution(
                get_service(), f"{self.engine}.create_model", arguments
            )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        self.refresh(prepared["model_id"])
        self.working_revision = prepared["revision"]
        self._start_prepared_preview(prepared)

    def _start_prepared_preview(self, prepared: dict[str, Any]):
        engine = self.engine
        if engine == "vibescript":
            raise RuntimeError("VibeScript must use the isolated editor operation lifecycle.")
        api = _engine_api(engine)
        self.generation += 1
        generation = self.generation
        self.status.setText(f"Rendering {self.engine} preview {prepared['revision'][:10]}...")

        def work(cancelled):
            execution = api.execute_prepared(prepared, cancellation_check=cancelled)
            if cancelled():
                api.cleanup_prepared(prepared)
                return None
            return {
                "generation": generation,
                "engine": engine,
                "prepared": prepared,
                "execution": execution,
            }

        self.jobs.submit("scripted preview", work)

    def import_model(self):
        if self.engine != "openscad":
            return
        selected, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.root, "Import OpenSCAD source", str(Path.home()), "OpenSCAD (*.scad)"
        )
        if not selected:
            return
        entry = Path(selected)
        try:
            source_files = _read_scad_project(entry)
            source = source_files["model.scad"]
            prepared = _engine_api("openscad").prepare_execution(
                get_service(),
                "openscad.create_model",
                {
                    "model_name": entry.stem,
                    "source": source,
                    "source_files": source_files,
                    "parameters": {},
                    "conversion_mode": self._conversion_mode(),
                },
            )
        except Exception as exc:
            payload = getattr(exc, "payload", None)
            self._show_failure(payload if isinstance(payload, dict) else {"error": str(exc)})
            return
        self.refresh(prepared["model_id"])
        self.working_revision = prepared["revision"]
        self._start_prepared_preview(prepared)

    def export(self):
        if not self.model_id or not self.accepted_revision:
            self.status.setText("Accept a valid model revision before exporting it.")
            return
        doc = App.ActiveDocument
        if doc is None:
            return
        shaped = _accepted_output_features(doc, self.model)
        if not shaped:
            self.status.setText("The accepted model has no shaped output to export.")
            return
        selected, selected_filter = self.QtWidgets.QFileDialog.getSaveFileName(
            self.root,
            "Export accepted scripted model",
            str(Path.home() / f"{self.model.get('label') or 'model'}.step"),
            "STEP (*.step *.stp);;STL (*.stl);;3MF (*.3mf)",
        )
        if not selected:
            return
        suffix = Path(selected).suffix.lower()
        fidelity = str(self.model.get("fidelity") or "")
        if suffix in {".step", ".stp"} and fidelity in {"faceted_brep", "mixed"}:
            answer = self.QtWidgets.QMessageBox.warning(
                self.root,
                "Faceted STEP export",
                "This accepted model contains tessellated surfaces. The STEP file "
                "will be valid but will not contain fully analytic manufacturing geometry.",
                self.QtWidgets.QMessageBox.Ok | self.QtWidgets.QMessageBox.Cancel,
                self.QtWidgets.QMessageBox.Cancel,
            )
            if answer != self.QtWidgets.QMessageBox.Ok:
                return
        try:
            if suffix in {".step", ".stp"}:
                import Part

                Part.export(shaped, selected)
            elif suffix == ".stl":
                import Mesh

                Mesh.export(shaped, selected)
            elif suffix == ".3mf":
                import Mesh

                Mesh.export(shaped, selected)
            else:
                raise RuntimeError(f"Unsupported export extension: {suffix}")
        except Exception as exc:
            self.status.setText(f"Export failed: {exc}")
            return
        self.status.setText(f"Exported accepted revision to {selected}")

    def _cancel_preview(self, *, restore_accepted: bool) -> None:
        self.generation += 1
        active_model_id = self.model_id
        document_key = self.document_uid or self.document_name
        had_live_preview = bool(
            self.active_prepared is not None
            or _preview_containers.get((document_key, active_model_id))
            or _hidden_accepted.get((document_key, active_model_id))
        )
        prepared_document_name = ""
        if self.active_prepared is not None:
            prepared_document_name = str(self.active_prepared.get("document_name") or "")
        if self.active_prepared is not None and self.active_engine:
            _engine_api(self.active_engine).cleanup_prepared(self.active_prepared)
        self.active_prepared = None
        self.active_execution = None
        self.active_imported = None
        self.active_engine = ""
        self.preview_revision = ""
        doc = None
        if prepared_document_name:
            doc = dict(App.listDocuments()).get(prepared_document_name)
        if doc is None:
            doc = App.ActiveDocument
        if had_live_preview and doc is not None and active_model_id:
            remove_preview(doc, active_model_id, restore_accepted=restore_accepted)

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
        diagnostic_file = str(item.data(0, int(self.QtCore.Qt.UserRole) + 1) or "")
        if diagnostic_file and self.engine == "openscad":
            candidate = Path(diagnostic_file).name
            matches = [
                path
                for path in self.source_files
                if path == diagnostic_file.replace("\\", "/") or Path(path).name == candidate
            ]
            if len(matches) == 1:
                index = self.file_selector.findData(matches[0])
                if index >= 0:
                    self.file_selector.setCurrentIndex(index)
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
        points_active = bool(scripted and self.engine == "vibescript" and self.domain == "points")
        new_supported = self.engine != "vibescript" or self.domain in _DOMAIN_EDITOR_NEW_TYPES
        self.button("VibeScriptedNew").setEnabled(ready and new_supported)
        self.button("VibeScriptedImport").setEnabled(ready and self.engine == "openscad")
        self.button("VibeScriptedRender").setEnabled(bool(ready and self.model_id))
        applicable = (
            self.active_vibescript_candidate is not None
            if self.engine == "vibescript"
            else bool(self.active_prepared)
        )
        self.button("VibeScriptedAccept").setEnabled(
            ready
            and applicable
            and not self.dirty
            and self.preview_revision == self.working_revision
        )
        self.button("VibeScriptedRevert").setEnabled(
            bool(ready and self.model_id and self.accepted_revision)
        )
        self.button("VibeScriptedExport").setEnabled(
            bool(ready and self.model_id and self.accepted_revision and self.engine != "vibescript")
        )
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


def show_scripted_model_editor() -> None:
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
        _controller.activate()


def ensure_scripted_model_editor_registered() -> Any:
    """Register native dock content once so View > Panels can reopen it."""
    global _controller, _registered_widget
    try:
        remove_all_previews()
    except Exception as exc:
        _warn(f"Could not remove stale transient previews: {exc}")
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


def active_preview_snapshot() -> dict[str, Any] | None:
    if _controller is None or _controller.active_prepared is None:
        return None
    return {
        "engine": _controller.engine,
        "model_id": _controller.model_id,
        "working_revision": _controller.working_revision,
    }


def automated_model_update_started(engine: str, document_name: str, model_id: str) -> None:
    if _controller is not None:
        _controller.automated_update_started(engine, document_name, model_id)


def automated_model_update_finished(engine: str, document_name: str, model_id: str) -> None:
    if _controller is not None:
        _controller.automated_update_finished(engine, document_name, model_id)


def suspend_preview_for_save(doc: Any) -> list[dict[str, str]]:
    document_key = _document_key(doc)
    _save_preview_restore.pop(document_key, None)
    if (
        _controller is not None
        and _controller.active_prepared is not None
        and _controller.active_imported is not None
        and _controller.active_engine
        and _controller.preview_revision
    ):
        prepared = _controller.active_prepared
        model_id = str(prepared.get("model_id") or "")
        preview_name = _preview_containers.get((document_key, model_id), "")
        if (
            str(prepared.get("document_name") or "") == str(doc.Name)
            and preview_name
            and doc.getObject(preview_name) is not None
        ):
            _save_preview_restore[document_key] = (
                _controller.active_engine,
                model_id,
                _controller.preview_revision,
            )
    return remove_all_previews(doc)


def restore_preview_after_save(doc: Any) -> bool:
    pending = _save_preview_restore.pop(_document_key(doc), None)
    if pending is None or _controller is None:
        return False
    engine, model_id, revision = pending
    prepared = _controller.active_prepared
    if (
        prepared is None
        or _controller.active_imported is None
        or _controller.active_engine != engine
        or _controller.preview_revision != revision
        or str(prepared.get("model_id") or "") != model_id
        or str(prepared.get("document_name") or "") != str(doc.Name)
        or str(prepared.get("revision") or "") != revision
    ):
        return False
    _show_preview(
        engine,
        prepared,
        _controller.active_imported,
        frame=False,
    )
    return True
