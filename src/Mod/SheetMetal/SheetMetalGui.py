# SPDX-License-Identifier: LGPL-2.1-or-later
"""Human controls for the shared asynchronous SheetMetal operations."""

import weakref

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets
from pivy import coin

import SheetMetalEditable as Editable
import SheetMetalOperations as Operations
import SheetMetalTools


_jobs = {}
_PARAMETER_LABELS = {
    "thickness": "Thickness (mm)", "bend_radius": "Inside bend radius (mm)",
    "flange_length": "Flange length (mm)", "bend_angle": "Bend angle (degrees)",
    "width": "Base width (mm)", "height": "Wall height (mm)",
    "flange_width": "Return flange width (mm)",
}


def _retain(run):
    # Closing a panel must not abandon a committed edit or its completion status.
    _jobs[run.id] = run
    run.future.add_done_callback(lambda future, job_id=run.id: _jobs.pop(job_id, None))


def _selected_sheet(*, allow_states=False):
    selection = Gui.Selection.getSelection()
    if len(selection) != 1:
        raise ValueError("Select one editable sheet")
    sheet = selection[0]
    owner = Editable.state_owner if allow_states else Editable._editable_owner
    if owner(sheet) is not App.ActiveDocument:
        raise ValueError("Select the sheet in the active document")
    return sheet


def _source_face():
    selection = Gui.Selection.getSelectionEx()
    if (len(selection) != 1 or len(selection[0].SubElementNames) != 1
            or not selection[0].SubElementNames[0].startswith("Face")):
        raise ValueError("Select one planar stationary face on the source sheet")
    source = selection[0].Object
    if Editable._owner(source) is not App.ActiveDocument:
        raise ValueError("Select a face in the active document")
    if isinstance(getattr(source, "Proxy", None), Editable.PreparedSheetState):
        raise ValueError("This is already an editable sheet")
    return source, selection[0].SubElementNames[0]


def _number(minimum=0, maximum=1e9):
    box = QtWidgets.QDoubleSpinBox()
    box.setDecimals(6)
    box.setRange(minimum, maximum)
    return box


class SheetPanel:
    def __init__(self, sheet, mode, history=False):
        if mode not in ("materials", "parameters", "cuts"):
            raise ValueError("Choose materials, parameters, or cuts")
        self.history = bool(history)
        self._operations = Operations
        if self.history:
            import SheetMetalHistoryOperations
            self._operations = SheetMetalHistoryOperations
        owner = self._operations.owner if self.history else Editable._editable_owner
        self.sheet, self.document, self.mode = sheet, owner(sheet), mode
        self._cut_entries = {}
        self._closed, self.run, self.pick, self._event = False, None, None, None
        self.preparation_run = None
        self._view = None
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle({"materials": "Sheet material", "parameters": "Sheet dimensions",
                                  "cuts": "Sheet cuts and reliefs"}[mode])
        layout = QtWidgets.QVBoxLayout(self.form)
        self.sheet_label = QtWidgets.QLabel(sheet.Label)
        layout.addWidget(self.sheet_label)
        views = QtWidgets.QHBoxLayout()
        self.folded_button = QtWidgets.QPushButton("Folded")
        self.flat_button = QtWidgets.QPushButton("Flat")
        self.folded_button.clicked.connect(lambda: self.switch("folded"))
        self.flat_button.clicked.connect(lambda: self.switch("flat"))
        views.addWidget(self.folded_button)
        views.addWidget(self.flat_button)
        layout.addLayout(views)
        self.fields = QtWidgets.QWidget()
        self.fields_layout = QtWidgets.QFormLayout(self.fields)
        layout.addWidget(self.fields)
        self.parameters = {}
        if mode == "materials":
            self.material = QtWidgets.QLineEdit()
            self.k_factor = _number(0, 1)
            self.fields_layout.addRow("Material", self.material)
            self.fields_layout.addRow("ANSI K-factor", self.k_factor)
        elif mode == "cuts":
            self.pick_button = QtWidgets.QPushButton("Pick hole center on sheet")
            self.pick_button.clicked.connect(self.arm_pick)
            self.fields_layout.addRow(self.pick_button)
            self.position = QtWidgets.QLabel("Pick in either folded or flat view")
            self.position.setWordWrap(True)
            self.fields_layout.addRow(self.position)
            self.radius = _number(.000001)
            self.radius.setValue(5)
            self.fields_layout.addRow("Hole radius (mm)", self.radius)
            self.operations = QtWidgets.QComboBox()
            self.fields_layout.addRow("Existing cut", self.operations)
            self.resize_button = QtWidgets.QPushButton("Change selected hole radius")
            self.resize_button.clicked.connect(self.resize_cut)
            self.fields_layout.addRow(self.resize_button)
            self.remove_button = QtWidgets.QPushButton("Remove selected cut")
            self.remove_button.clicked.connect(self.remove_cut)
            self.fields_layout.addRow(self.remove_button)
            self.profiles = QtWidgets.QComboBox()
            self.fields_layout.addRow("Closed sketch", self.profiles)
            self.profile_button = QtWidgets.QPushButton("Cut from sketch")
            self.profile_button.clicked.connect(self.add_profile)
            self.fields_layout.addRow(self.profile_button)
            if self.history:
                self.edit_profile_button = QtWidgets.QPushButton("Edit selected sketch")
                self.edit_profile_button.clicked.connect(self.edit_profile)
                self.fields_layout.addRow(self.edit_profile_button)
                self.replace_profile_button = QtWidgets.QPushButton("Replace cut sketch")
                self.replace_profile_button.clicked.connect(self.replace_profile)
                self.fields_layout.addRow(self.replace_profile_button)
                self.operations.currentIndexChanged.connect(self._update_cut_buttons)
        self.apply_button = QtWidgets.QPushButton("Add hole" if mode == "cuts" else "Apply")
        self.apply_button.clicked.connect(self.apply)
        if self.history and mode == "cuts":
            self.fields_layout.insertRow(3, self.apply_button)
        else:
            layout.addWidget(self.apply_button)
        self.refresh_button = QtWidgets.QPushButton("Reload current sheet")
        self.refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_button)
        self.prepare_button = QtWidgets.QPushButton("Prepare for editing")
        self.prepare_button.setToolTip("Prepare a reopened sheet for cuts without changing its dimensions or History")
        self.prepare_button.clicked.connect(self.prepare_for_editing)
        layout.addWidget(self.prepare_button)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.refresh()

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
            self.close()
            Gui.Control.closeDialog()
        return True

    def closed(self):
        self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            self._disarm()
            if self.preparation_run is not None:
                self.preparation_run.future.cancel()

    def _check_owner(self):
        if self._closed:
            raise RuntimeError("The sheet panel is closed")
        owner = self._operations.owner if self.history else Editable._editable_owner
        if owner(self.sheet) is not App.ActiveDocument:
            raise RuntimeError("Return to this sheet's document before editing")

    def switch(self, representation):
        try:
            self._check_owner()
            self._operations.switch(self.sheet, representation)
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def refresh(self):
        try:
            self._check_owner()
            state = self._operations.inspect(self.sheet)
            self.revision = state["revision"]
            self.prepare_button.setVisible(not state["prepared"])
            self.pick = None
            if self.mode == "materials":
                self.material.setText(state["material"])
                self.k_factor.setValue(state["k_factor"])
            elif self.mode == "parameters":
                for name, descriptor in state["parameters"].items():
                    if name not in self.parameters:
                        box = _number(-180 if name == "bend_angle" else 0,
                                      180 if name == "bend_angle" else 1e9)
                        self.parameters[name] = box
                        self.fields_layout.addRow(_PARAMETER_LABELS[name], box)
                    self.fields_layout.labelForField(self.parameters[name]).setText(
                        descriptor.get("label", _PARAMETER_LABELS[name]))
                    self.parameters[name].setValue(descriptor["value"])
                for name, box in self.parameters.items():
                    box.setEnabled(state["parameters"].get(name, {}).get("editable", False))
            else:
                self.position.setText("Pick in either folded or flat view")
                self.operations.clear()
                entries = state.get("cuts", state["definition"]["operations"])
                self._cut_entries = {entry["id"]: entry for entry in entries}
                for entry in entries:
                    label = (f"Hole · radius {entry['radius']:g} mm" if entry["kind"] == "circle"
                             else "Sketch cut")
                    if entry.get("suppressed"):
                        label += " (suppressed)"
                    self.operations.addItem(f"{self.operations.count()+1}. {label}", entry["id"])
                self.profiles.clear()
                for obj in self.document.Objects:
                    if obj.isDerivedFrom("Sketcher::SketchObject"):
                        try:
                            if self.history:
                                self._operations.check_profile_input(self.sheet, obj)
                            else:
                                Editable._check_profile_owner(self.sheet, obj)
                        except (RuntimeError, ValueError):
                            continue
                        self.profiles.addItem(obj.Label, obj.Name)
                if self.history:
                    self._update_cut_buttons()
            self.message.setText("Ready" if state["prepared"] else state.get("error", "Geometry not prepared"))
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def prepare_for_editing(self):
        try:
            self._check_owner()
            if any(run is not None and not run.future.done()
                   for run in (self.run, self.preparation_run)):
                raise RuntimeError("Wait for this sheet operation to finish")
            from SheetMetalPreparation import start_preparation
            self._disarm()
            self.preparation_run = start_preparation(self.sheet, expected_revision=self.revision)
            for control in (self.fields, self.apply_button, self.refresh_button, self.prepare_button):
                control.setEnabled(False)
            self.message.setText("Preparing sheet for editing…")
            reference = weakref.ref(self)

            def completed(future):
                panel = reference()
                if panel is None or panel._closed:
                    return
                for control in (panel.fields, panel.apply_button, panel.refresh_button, panel.prepare_button):
                    control.setEnabled(True)
                try:
                    future.result()
                    panel.refresh()
                except Exception as error:
                    panel.message.setText(str(error))

            self.preparation_run.future.add_done_callback(completed)
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def _submit(self, arguments):
        try:
            self._check_owner()
            if self.run is not None and not self.run.future.done():
                raise RuntimeError("Wait for this edit to finish")
            if self.preparation_run is not None and not self.preparation_run.future.done():
                raise RuntimeError("Wait for sheet preparation to finish")
            prepared = self._operations.prepare(self.sheet, arguments, expected_revision=self.revision)
            self._disarm()
            self.run = self._operations.start(prepared)
            _retain(self.run)
            if self.history and self.run._sheet is not self.sheet:
                self.sheet = self.run._sheet
                self.sheet_label.setText(self.sheet.Label)
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(self.sheet)
            self.fields.setEnabled(False)
            self.apply_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.prepare_button.setEnabled(False)
            self.message.setText("Preparing folded and flat geometry…")
            reference = weakref.ref(self)
            def completed(future):
                panel = reference()
                if panel is not None and not panel._closed:
                    panel.fields.setEnabled(True)
                    panel.apply_button.setEnabled(True)
                    panel.refresh_button.setEnabled(True)
                    panel.prepare_button.setEnabled(True)
                    result = future.result()
                    if not panel.history or result["phase"] in ("ready", "failed"):
                        panel.refresh()
                    if result["phase"] != "ready":
                        panel.message.setText(result.get("error", result["phase"]))
            self.run.future.add_done_callback(completed)
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def apply(self):
        if self.mode == "materials":
            self._submit({"operation": "set_material", "material": self.material.text(),
                          "k_factor": self.k_factor.value()})
        elif self.mode == "parameters":
            self._submit({"operation": "set_parameters", "changes": {
                name: box.value() for name, box in self.parameters.items() if box.isEnabled()}})
        else:
            try:
                self._check_owner()
                self.sheet.ViewObject.Proxy.validate_pick(self.pick)
                self._submit({"operation": "add_circle", "center": list(self.pick.flat),
                              "radius": self.radius.value(), "representation": "flat",
                              "region": self.pick.region})
            except (RuntimeError, ValueError, TypeError) as error:
                self.message.setText(str(error) if self.pick else "Pick the hole center on the sheet first")

    def resize_cut(self):
        self._submit({"operation": "update_circle", "operation_id": self.operations.currentData(),
                      "radius": self.radius.value()})

    def remove_cut(self):
        if self.history:
            entry = self._cut_entries.get(self.operations.currentData(), {})
            if entry.get("native"):
                self._submit({"operation": "set_suppressed", "operation_id": entry["id"],
                              "suppressed": not entry["suppressed"]})
                return
        self._submit({"operation": "remove_operation", "operation_id": self.operations.currentData()})

    def add_profile(self):
        self._submit({"operation": "add_profile", "profile": {
            "document_uid": str(self.document.Uid), "object_name": self.profiles.currentData()}})

    def _update_cut_buttons(self):
        entry = self._cut_entries.get(self.operations.currentData(), {})
        native, suppressed = entry.get("native", False), entry.get("suppressed", False)
        self.resize_button.setEnabled(entry.get("kind") == "circle" and not suppressed)
        self.remove_button.setEnabled(bool(entry))
        self.remove_button.setText(("Restore selected cut" if suppressed else "Suppress selected cut")
                                   if native else "Remove selected cut")
        self.edit_profile_button.setEnabled(native and entry.get("kind") == "profile" and not suppressed)
        self.replace_profile_button.setEnabled(native and entry.get("kind") == "profile" and not suppressed)

    def replace_profile(self):
        self._submit({"operation": "replace_profile", "operation_id": self.operations.currentData(),
                      "profile": {"document_uid": str(self.document.Uid),
                                  "object_name": self.profiles.currentData()}})

    def edit_profile(self):
        try:
            self._check_owner()
            self._operations._check_revision(self.sheet, self.revision)
            entry = self._cut_entries.get(self.operations.currentData(), {})
            if not entry.get("native") or entry.get("kind") != "profile":
                raise ValueError("Select one native sketch cut")
            step = self.document.getObject(entry["object_name"])
            import SheetMetalCutGui
            # Keep the repair controls open when the linked sketch is missing.
            SheetMetalCutGui.History.get_profile(step)
            self.reject()
            SheetMetalCutGui.open_profile_editor(step)
        except (RuntimeError, ValueError) as error:
            if self._closed:
                App.Console.PrintError("Sheet Metal: " + str(error) + "\n")
            else:
                self.message.setText(str(error))

    def use_pick(self, pick):
        self._check_owner()
        self.sheet.ViewObject.Proxy.validate_pick(pick)
        self.pick = pick
        self.position.setText("Flat center: " + ", ".join(f"{value:.3f}" for value in pick.flat) + " mm")
        self._disarm()

    def arm_pick(self):
        try:
            self._check_owner()
            self._disarm()
            self._view = Gui.getDocument(self.document.Name).activeView()
            self._event = self._view.addEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(),
                                                         self._mouse_event)
            self.message.setText("Click the hole center on the sheet; then choose Add hole")
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def _mouse_event(self, callback):
        event = callback.getEvent()
        if event.getState() != coin.SoButtonEvent.DOWN or event.getButton() != coin.SoMouseButtonEvent.BUTTON1:
            return
        try:
            self._check_owner()
            pick = self.sheet.ViewObject.Proxy.pick_screen(self._view, event.getPosition().getValue())
            callback.setHandled()
            self.use_pick(pick)
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def _disarm(self):
        if self._event is not None:
            try:
                self._view.removeEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(), self._event)
            except (RuntimeError, ReferenceError):
                pass
            self._event, self._view = None, None


class _Command:
    def __init__(self, mode, label, icon, tooltip):
        self.mode, self.label, self.icon, self.tooltip = mode, label, icon, tooltip

    def GetResources(self):
        return {"MenuText": self.label, "ToolTip": self.tooltip,
                "Pixmap": str(SheetMetalTools.icons_path) + "/" + self.icon}

    def IsActive(self):
        try:
            if Gui.Control.activeDialog():
                return False
            if self.mode == "create":
                source, _ = _source_face()
                Operations.capture_source_revision(source)
            else:
                sheet = _selected_sheet(allow_states=True)
                if self.mode in ("flat", "folded"):
                    return bool(sheet.ViewObject.Proxy.ready)
                import SheetMetalHistoryOperations
                SheetMetalHistoryOperations.capture_revision(sheet)
            return True
        except (RuntimeError, ValueError, AttributeError):
            return False

    def Activated(self):
        try:
            if Gui.Control.activeDialog():
                raise RuntimeError("Close the current task panel first")
            if self.mode == "create":
                source, face = _source_face()
                run = Operations.start_creation(source, face,
                    expected_revision=Operations.capture_source_revision(source))
                _retain(run)
                sheet = source.Document.getObject(run.status()["object_name"])
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(sheet)
                def finished(future):
                    result = future.result()
                    if result["phase"] != "ready":
                        App.Console.PrintError("Sheet Metal: " + result.get("error", result["phase"]) + "\n")
                run.future.add_done_callback(finished)
            elif self.mode in ("folded", "flat"):
                Operations.switch(_selected_sheet(allow_states=True), self.mode)
            else:
                Gui.Control.showDialog(SheetPanel(_selected_sheet(allow_states=True), self.mode, True))
        except (RuntimeError, ValueError) as error:
            App.Console.PrintError("Sheet Metal: " + str(error) + "\n")


def ensure_commands_registered():
    for name, mode, label, icon, tooltip in (
        ("CreateEditable", "create", "Editable Sheet", "SheetMetal_Unfold.svg",
         "Create linked folded and flat views from a selected stationary source face"),
        ("EditParameters", "parameters", "Sheet Dimensions", "SheetMetal_AddWall.svg",
         "Edit the source thickness, flange length, bend radius, and angle"),
        ("EditCuts", "cuts", "Holes and Cutouts", "SheetMetal_AddCutout.svg",
         "Add holes in either view, edit hole sizes, or cut using a closed sketch"),
        ("EditMaterial", "materials", "Sheet Material", "SheetMetal_Unfold.svg",
         "Set material and ANSI bend allowance for both representations"),
        ("ViewFolded", "folded", "Folded", "SheetMetal_AddWall.svg", "Show the cached folded sheet"),
        ("ViewFlat", "flat", "Flat", "SheetMetal_Unfold.svg", "Show the cached flat sheet"),
    ):
        command = "SheetMetal_" + name
        if Gui.Command.get(command) is None:
            Gui.addCommand(command, _Command(mode, label, icon, tooltip))
    for action in Gui.Command.get("SheetMetal_EditParameters").ensureAction():
        action.setProperty("VibeCADTimelineOperationEditor", True)
