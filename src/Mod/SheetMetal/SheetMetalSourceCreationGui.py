# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ribbon source creation through the same asynchronous operations as native AI."""

import weakref

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SheetMetalSourceOperations as Sources
from SheetMetalSourceGui import parameter_fields, field_values, _ICONS
import SheetMetalTools


_DEFAULTS = {
    "base_shape": {"shape_type": "L-Shape", "thickness": 1.6, "bend_radius": 2,
                   "width": 50, "length": 70, "height": 25, "flange_width": 8,
                   "origin": "0,0", "fill_gaps": True},
    "base_from_sketch": {"thickness": 1.6, "bend_radius": 2, "length": 25,
                         "bend_side": "Inside", "midplane": False, "reverse": False},
    "from_solid": {"thickness": 1.6, "bend_radius": 2, "invert": False},
    "add_flange": {"length": 20, "bend_radius": 2, "bend_angle": 90, "invert": False,
                   "bend_type": "Material Outside", "length_spec": "Leg"},
    "fold_from_sketch": {"bend_radius": 2, "bend_angle": 90, "invert": False,
                         "invert_bend": False, "k_factor": .42, "position": "middle"},
}
_COMMANDS = {
    "SheetMetal_CreateBaseShape": ("base_shape", "Base Shape"),
    "SheetMetal_CreateFromSketch": ("base_from_sketch", "From Sketch"),
    "SheetMetal_CreateFromSolid": ("from_solid", "From Solid"),
    "SheetMetal_CreateFlange": ("add_flange", "Add Flange"),
    "SheetMetal_CreateFold": ("fold_from_sketch", "Internal Fold"),
}


def _owner(document):
    revision = Sources.capture_revision(document)
    if document is not App.ActiveDocument:
        raise RuntimeError("Activate the original document before creating this sheet")
    return revision


def _selection(operation):
    document = App.ActiveDocument
    _owner(document)
    if operation == "base_shape":
        return document, None, ()
    if operation == "fold_from_sketch":
        document, source, names, _sketch = _fold_selection()
        return document, source, names
    selected = Gui.Selection.getSelectionEx()
    if len(selected) != 1 or selected[0].Object.Document is not document:
        raise ValueError("Select one source in this document")
    source = selected[0].Object
    if operation == "base_from_sketch":
        if not source.isDerivedFrom("Sketcher::SketchObject"):
            raise ValueError("Select an editable sketch")
        return document, source, ()
    names = tuple(selected[0].SubElementNames)
    if not names or any(not name.startswith(("Face", "Edge")) for name in names):
        raise ValueError("Select boundary edges or thickness-side faces on one sheet" if operation == "add_flange" else
                         "Select faces to remove or edges to rip on one solid")
    return document, source, names


def _fold_selection():
    document = App.ActiveDocument
    _owner(document)
    selected = Gui.Selection.getSelectionEx()
    if len(selected) != 2 or any(item.Object.Document is not document for item in selected):
        raise ValueError("Select one sheet skin and one editable straight-line bend sketch in this document")
    sketches = [item for item in selected if item.Object.isDerivedFrom("Sketcher::SketchObject")]
    skins = [item for item in selected if not item.Object.isDerivedFrom("Sketcher::SketchObject")]
    if (len(sketches) != 1 or len(skins) != 1 or len(skins[0].SubElementNames) != 1
            or not skins[0].SubElementNames[0].startswith("Face")):
        raise ValueError("Select one planar sheet skin and one editable straight-line bend sketch")
    return document, skins[0].Object, tuple(skins[0].SubElementNames), sketches[0].Object


class SourceCreatePanel:
    def __init__(self, document, operation, source=None, subelements=(), *, bend_sketch=None):
        if operation not in _DEFAULTS:
            raise ValueError("Choose a supported sheet source operation")
        self.revision = _owner(document)
        if operation != "base_shape" and (source is None or source.Document is not document):
            raise ValueError("Choose an exact source in the original document")
        self.document, self.operation = document, operation
        self.run, self._closed = None, False
        self._inputs = {} if source is None else {"object_name": source.Name}
        if operation in ("from_solid", "add_flange", "fold_from_sketch"):
            self._inputs["subelements"] = list(subelements)
        if operation == "fold_from_sketch":
            if bend_sketch is None or bend_sketch.Document is not document:
                raise ValueError("Choose the bend sketch in the original document")
            self._inputs["sketch_name"] = bend_sketch.Name
        values = {"operation": operation, **_DEFAULTS[operation]}
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Create sheet source")
        layout = QtWidgets.QFormLayout(self.form)
        if source is not None:
            label = QtWidgets.QLabel(f"{source.Label} ({source.Name})")
            label.setTextFormat(QtCore.Qt.PlainText)
            layout.addRow("Source", label)
        self.container = None
        if operation == "base_shape":
            self.container = QtWidgets.QComboBox()
            self.container.addItem("Document root", None)
            for obj in document.Objects:
                if obj.TypeId in ("App::Part", "PartDesign::Body"):
                    self.container.addItem(f"{obj.Label} ({obj.Name})", obj.Name)
            layout.addRow("Container", self.container)
        elif subelements:
            label = QtWidgets.QLabel(", ".join(subelements))
            label.setTextFormat(QtCore.Qt.PlainText)
            label.setWordWrap(True)
            layout.addRow({"add_flange": "Flange boundaries", "fold_from_sketch": "Sheet skin"}.get(
                operation, "Remove faces / rip edges"), label)
        if bend_sketch is not None:
            label = QtWidgets.QLabel(f"{bend_sketch.Label} ({bend_sketch.Name})")
            label.setTextFormat(QtCore.Qt.PlainText)
            layout.addRow("Bend line", label)
        self.fields = parameter_fields(values, layout)
        self.create_button = QtWidgets.QPushButton("Create")
        self.create_button.clicked.connect(self.create)
        layout.addRow(self.create_button)
        self.message = QtWidgets.QLabel(
            "After creation, select a planar stationary face and choose Editable Sheet for linked folded and flat views.")
        self.message.setTextFormat(QtCore.Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addRow(self.message)

    def create(self):
        try:
            if self._closed:
                raise RuntimeError("This creation form is closed")
            if self.run is not None:
                raise RuntimeError("This form already submitted its source; use History to edit it")
            _owner(self.document)
            values = {"operation": self.operation, **self._inputs, **field_values(self.fields)}
            if self.container is not None:
                values["container_name"] = self.container.currentData()
            prepared = Sources.prepare(self.document, values, expected_revision=self.revision)
            self.run = Sources.start(prepared)
            from SheetMetalGui import _retain
            _retain(self.run)
            self.create_button.setEnabled(False)
            for field in self.fields.values():
                field.setEnabled(False)
            if self.container is not None:
                self.container.setEnabled(False)
            self.message.setText("Creating sheet source…")
            reference = weakref.ref(self)
            def finished(future):
                panel = reference()
                if panel is not None and not panel._closed:
                    result = future.result()
                    if result["phase"] == "ready":
                        panel.message.setText(
                            "Source created. Close this form, select a planar stationary face, and choose Editable Sheet for folded and flat views.")
                    else:
                        panel.message.setText(result.get("error", "Creation failed") +
                                              " Any committed source remains editable in History.")
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


class _CreateCommand:
    def __init__(self, operation, label):
        self.operation, self.label = operation, label

    def GetResources(self):
        return {"MenuText": self.label,
                "ToolTip": ("Select a sheet skin and a straight-line bend sketch. For an internal tab, cut relief around its sides first."
                            if self.operation == "fold_from_sketch" else "Create an editable sheet source"),
                "Pixmap": str(SheetMetalTools.icons_path) + "/" + _ICONS[self.operation]}

    def IsActive(self):
        try:
            if Gui.Control.activeDialog():
                return False
            _selection(self.operation)
            return True
        except (RuntimeError, ValueError, AttributeError):
            return False

    def Activated(self):
        try:
            if Gui.Control.activeDialog():
                raise RuntimeError("Close the current task panel first")
            if self.operation == "fold_from_sketch":
                document, source, names, sketch = _fold_selection()
                Gui.Control.showDialog(SourceCreatePanel(document, self.operation, source, names, bend_sketch=sketch))
            else:
                document, source, names = _selection(self.operation)
                Gui.Control.showDialog(SourceCreatePanel(document, self.operation, source, names))
        except (RuntimeError, ValueError) as error:
            App.Console.PrintError("Sheet Metal: " + str(error) + "\n")


def ensure_commands_registered():
    for name, (operation, label) in _COMMANDS.items():
        if Gui.Command.get(name) is None:
            Gui.addCommand(name, _CreateCommand(operation, label))
