# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exact source editors shared by Tree and History, with asynchronous Apply."""

import weakref

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets

import SheetMetalOperations as Operations
import SheetMetalSourceOperations as Sources
import SheetMetalTools


_ICONS = {"base_shape": "SheetMetal_AddBaseShape.svg", "base_from_sketch": "SheetMetal_AddBase.svg",
          "from_solid": "SheetMetal_FromSolid.svg", "add_flange": "SheetMetal_AddWall.svg",
          "fold_from_sketch": "SheetMetal_AddFoldWall.svg"}


def _owner(source):
    revision = Operations.capture_source_revision(source)
    if source.Document is not App.ActiveDocument:
        raise RuntimeError("Activate this source's document before editing")
    return revision


def parameter_fields(values, layout):
    """Use the same dimension/options controls for source creation and editing."""
    fields = {}
    for name in Sources.PARAMETERS[values["operation"]]:
        value = values[name]
        if name in Sources.CHOICES:
            field = QtWidgets.QComboBox()
            field.addItems(Sources.CHOICES[name])
            field.setCurrentText(value)
        elif type(value) is bool:
            field = QtWidgets.QCheckBox()
            field.setChecked(value)
        else:
            field = QtWidgets.QDoubleSpinBox()
            field.setDecimals(6)
            field.setRange(0 if name == "bend_radius" else .000001, 1e9)
            field.setSuffix(" mm")
            if name == "bend_angle":
                field.setMaximum(180)
                field.setSuffix(" °")
            elif name == "k_factor":
                field.setRange(0, 1)
                field.setSuffix("")
            field.setValue(value)
        fields[name] = field
        layout.addRow(name.replace("_", " ").capitalize(), field)
    if values["operation"] == "base_shape":
        def show_shape_options():
            kind = fields["shape_type"].currentText()
            visible = {"bend_radius": kind != "Flat", "height": kind != "Flat",
                       "flange_width": kind in ("Hat", "Box"),
                       "fill_gaps": kind in ("Tub", "Hat", "Box")}
            for name, shown in visible.items():
                field = fields[name]
                field.setVisible(shown)
                layout.labelForField(field).setVisible(shown)
        fields["shape_type"].currentTextChanged.connect(show_shape_options)
        show_shape_options()
    return fields


def field_values(fields):
    values = {}
    for name, field in fields.items():
        if isinstance(field, QtWidgets.QComboBox):
            values[name] = field.currentText()
        elif isinstance(field, QtWidgets.QCheckBox):
            values[name] = field.isChecked()
        else:
            values[name] = field.value()
    return values


class SourcePanel:
    def __init__(self, source):
        self.revision = _owner(source)
        self.source, self.run, self._closed = source, None, False
        self.values = Sources.arguments(source)
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Sheet source dimensions")
        layout = QtWidgets.QFormLayout(self.form)
        self.fields = parameter_fields(self.values, layout)
        self.apply_button = QtWidgets.QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        layout.addRow(self.apply_button)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        layout.addRow(self.message)

    def apply(self):
        try:
            if self._closed:
                raise RuntimeError("This source editor is closed")
            if self.run is not None and not self.run.future.done():
                raise RuntimeError("Wait for the current source update")
            _owner(self.source)
            changes = field_values(self.fields)
            self.run = Sources.update(self.source, changes, expected_revision=self.revision)
            from SheetMetalGui import _retain
            _retain(self.run)
            self.apply_button.setEnabled(False)
            self.message.setText("Updating sheet…")
            reference = weakref.ref(self)
            def finished(future):
                panel = reference()
                if panel is not None and not panel._closed:
                    result = future.result()
                    panel.message.setText("Updated" if result["phase"] == "ready" else
                                          result.get("error", "Update failed"))
                    if result["phase"] in ("superseded", "closed"):
                        panel.message.setText(panel.message.text() + " Reopen the source editor to continue.")
                        panel.apply_button.setEnabled(False)
                        return
                    panel.apply_button.setEnabled(True)
                    try:
                        panel.revision = _owner(panel.source)
                    except (RuntimeError, ValueError):
                        panel.apply_button.setEnabled(False)
            self.run.future.add_done_callback(finished)
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def getStandardButtons(self):
        flag = QtWidgets.QDialogButtonBox.Close
        return flag.value if hasattr(flag, "value") else int(flag)

    def isAllowedAlterDocument(self):
        return True

    def isAllowedAlterSelection(self):
        return True

    def isAllowedAlterView(self):
        return True

    def reject(self):
        if not self._closed:
            self._closed = True
            Gui.Control.closeDialog()
        return True

    def closed(self):
        self._closed = True


def open_editor(source):
    _owner(source)
    if Gui.Control.activeDialog():
        raise RuntimeError("Close the current task panel first")
    Gui.Control.showDialog(SourcePanel(source))


class SourceViewProvider:
    def __init__(self, view):
        view.Proxy = self
        self.attach(view)

    def attach(self, view):
        self.Object = view.Object
        ensure_commands_registered()

    def claimChildren(self):
        if hasattr(self.Object, "BendSketch"):
            return [self.Object.BendSketch] if self.Object.BendSketch else []
        if hasattr(self.Object, "baseObject"):
            return [self.Object.baseObject[0]] if self.Object.baseObject else []
        return []

    def getIcon(self):
        operation = next((name for name, cls in Sources._CLASSES.items()
                          if isinstance(self.Object.Proxy, cls)), "base_shape")
        return str(SheetMetalTools.icons_path) + "/" + _ICONS[operation]

    def doubleClicked(self, view):
        open_editor(view.Object)
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        pass


class _EditCommand:
    def GetResources(self):
        return {"MenuText": "Edit sheet source", "ToolTip": "Edit this sheet source's dimensions",
                "Pixmap": str(SheetMetalTools.icons_path) + "/SheetMetal_AddBaseShape.svg"}

    def IsActive(self):
        try:
            selection = Gui.Selection.getSelection()
            if len(selection) != 1 or Gui.Control.activeDialog():
                return False
            _owner(selection[0])
            Sources.arguments(selection[0])
            return True
        except (RuntimeError, ValueError, AttributeError):
            return False

    def Activated(self):
        try:
            selection = Gui.Selection.getSelection()
            if len(selection) != 1:
                raise ValueError("Select one sheet source")
            open_editor(selection[0])
        except (RuntimeError, ValueError) as error:
            App.Console.PrintError("Sheet Metal: " + str(error) + "\n")


def ensure_commands_registered():
    name = "SheetMetal_EditSource"
    if Gui.Command.get(name) is None:
        Gui.addCommand(name, _EditCommand())
    for action in Gui.Command.get(name).ensureAction():
        action.setProperty("VibeCADTimelineOperationEditor", True)
