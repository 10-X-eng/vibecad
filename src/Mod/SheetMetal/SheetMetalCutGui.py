# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tree and native History editor for parameterized sheet cuts."""

import os
import weakref

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets

import SheetMetalCutHistory as History
import SheetMetalOperations as Operations
import SheetMetalTools
from SheetMetalPresentation import SheetViewProvider, _gui_thread
from SheetMetalGui import _number, _retain


class CutViewProvider(SheetViewProvider):
    def getIcon(self):
        return os.path.join(SheetMetalTools.icons_path, "SheetMetal_AddCutout.svg")

    def getTreeViewDetails(self):
        _gui_thread()
        if self._closed or not self._alive():
            return []
        step = self._object
        return [
            {"key": "representation:folded", "label": "Folded", "secondary_text": "3D sheet",
             "icon": os.path.join(SheetMetalTools.icons_path, "SheetMetal_AddWall.svg")},
            {"key": "representation:flat", "label": "Flat", "secondary_text": "Developed sheet",
             "icon": os.path.join(SheetMetalTools.icons_path, "SheetMetal_Unfold.svg")},
            {"key": "cut", "label": "Hole settings", "secondary_text": f"Radius {float(step.Radius):g} mm",
             "tooltip": "Edit this cut in the shared feature history", "icon": self.getIcon()},
        ]

    def treeViewDetailsAffectedBy(self, name):
        return name in ("Radius", "CenterU", "CenterV", "BaseSheet", "Suppressed")

    def activateTreeViewDetail(self, key):
        _gui_thread()
        step = self._object
        if History._active(step) is not App.ActiveDocument:
            raise RuntimeError("Activate this cut's document before editing")
        if key in ("representation:folded", "representation:flat"):
            self.switch(key.split(":", 1)[1])
            return True
        if key == "cut":
            open_editor(step)
            return True
        return False

    def finishRestoring(self):
        super().finishRestoring()
        ensure_commands_registered()


def open_editor(step):
    _gui_thread()
    if not isinstance(getattr(step, "Proxy", None), History.CircleCutFeature):
        raise ValueError("Select one circular sheet cut")
    if Gui.Control.activeDialog():
        raise RuntimeError("Close the current task panel first")
    if History._active(step) is not App.ActiveDocument:
        raise RuntimeError("Activate this cut's document before editing")
    revision = Operations.capture_revision(step)
    Gui.Control.showDialog(CutPanel(step, revision))


class CutPanel:
    def __init__(self, step, revision):
        self.step, self.revision, self.run = step, revision, None
        self._closed = False
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Sheet hole")
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.addWidget(QtWidgets.QLabel(step.Label))
        layout.addWidget(QtWidgets.QLabel("Developed hole radius (mm)"))
        self.radius = _number(.000001)
        self.radius.setValue(float(step.Radius))
        layout.addWidget(self.radius)
        self.apply_button = QtWidgets.QPushButton("Apply radius")
        self.apply_button.clicked.connect(self.apply)
        layout.addWidget(self.apply_button)
        self.reload_button = QtWidgets.QPushButton("Reload current cut")
        self.reload_button.clicked.connect(self.reload)
        layout.addWidget(self.reload_button)
        self.message = QtWidgets.QLabel("Edits update both folded and flat states.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addStretch()

    def getStandardButtons(self):
        value = QtWidgets.QDialogButtonBox.Close
        return value.value if hasattr(value, "value") else int(value)

    def apply(self):
        try:
            if self._closed or History._active(self.step) is not App.ActiveDocument:
                raise RuntimeError("Activate this cut's document before editing")
            self.run = History.start_radius_edit(self.step, self.radius.value(),
                                                  expected_revision=self.revision)
            _retain(self.run)
            self.apply_button.setEnabled(False)
            self.reload_button.setEnabled(False)
            self.message.setText("Preparing sheet geometry…")
            reference = weakref.ref(self)
            def completed(future):
                panel = reference()
                if panel is None or panel._closed:
                    return
                result = future.result()
                panel.reload_button.setEnabled(True)
                if result["phase"] == "ready":
                    panel.revision = result["revision"]
                    panel.apply_button.setEnabled(True)
                elif result["phase"] == "failed":
                    # The same committed edit remains repairable. Superseding
                    # edits instead require explicit reload of current values.
                    try:
                        panel.revision = Operations.capture_revision(panel.step)
                        panel.apply_button.setEnabled(True)
                    except RuntimeError:
                        pass
                panel.message.setText("Ready" if result["phase"] == "ready"
                                      else result.get("error", result["phase"]))
            self.run.future.add_done_callback(completed)
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def reload(self):
        try:
            if self._closed or History._active(self.step) is not App.ActiveDocument:
                raise RuntimeError("Activate this cut's document before editing")
            self.revision = Operations.capture_revision(self.step)
            self.radius.setValue(float(self.step.Radius))
            self.apply_button.setEnabled(True)
            self.message.setText("Loaded current cut")
        except (RuntimeError, ValueError) as error:
            self.message.setText(str(error))

    def closed(self):
        self._closed = True

    def reject(self):
        self.closed()
        Gui.Control.closeDialog()
        return True


class _EditCut:
    def GetResources(self):
        return {"MenuText": "Edit sheet hole", "ToolTip": "Edit the shared folded/flat hole",
                "Pixmap": os.path.join(SheetMetalTools.icons_path, "SheetMetal_AddCutout.svg")}

    def IsActive(self):
        selected = Gui.Selection.getSelection()
        if Gui.Control.activeDialog() or len(selected) != 1:
            return False
        step = selected[0]
        try:
            if not isinstance(getattr(step, "Proxy", None), History.CircleCutFeature):
                return False
            return History._active(step) is App.ActiveDocument and bool(Operations.capture_revision(step))
        except (RuntimeError, ValueError):
            return False

    def Activated(self):
        try:
            selected = Gui.Selection.getSelection()
            if len(selected) != 1:
                raise ValueError("Select one sheet cut")
            open_editor(selected[0])
        except (RuntimeError, ValueError) as error:
            App.Console.PrintError("Sheet Metal: " + str(error) + "\n")


def ensure_commands_registered():
    for name, command in (("SheetMetal_EditHistoryCut", _EditCut),
                          ("SheetMetal_EditHistoryProfile", _EditProfile)):
        if Gui.Command.get(name) is None:
            Gui.addCommand(name, command())
        for action in Gui.Command.get(name).ensureAction():
            action.setProperty("VibeCADTimelineOperationEditor", True)


class ProfileCutViewProvider(CutViewProvider):
    def getTreeViewDetails(self):
        _gui_thread()
        if self._closed or not self._alive():
            return []
        try:
            label = History.get_profile(self._object).Label
        except (RuntimeError, ValueError):
            label = "Missing sketch"
        return [
            {"key": "representation:folded", "label": "Folded", "secondary_text": "3D sheet",
             "icon": os.path.join(SheetMetalTools.icons_path, "SheetMetal_AddWall.svg")},
            {"key": "representation:flat", "label": "Flat", "secondary_text": "Developed sheet",
             "icon": os.path.join(SheetMetalTools.icons_path, "SheetMetal_Unfold.svg")},
            {"key": "profile", "label": "Edit sketch", "secondary_text": label,
             "tooltip": "Edit the native sketch shared by both representations", "icon": self.getIcon()},
        ]

    def treeViewDetailsAffectedBy(self, name):
        return name in ("BaseSheet", "Suppressed") or name.startswith("CutProfile_")

    def activateTreeViewDetail(self, key):
        if key == "profile":
            open_profile_editor(self._object)
            return True
        if key in ("representation:folded", "representation:flat"):
            return super().activateTreeViewDetail(key)
        return False


def open_profile_editor(step):
    _gui_thread()
    document = History._active(step)
    if document is not App.ActiveDocument:
        raise RuntimeError("Activate this cut's document before editing")
    if Gui.Control.activeDialog():
        raise RuntimeError("Close the current task panel first")
    Operations.capture_revision(step)
    profile = History.get_profile(step)
    if not document.isObjectUsableAtCurrentTimelinePosition(profile):
        raise RuntimeError("The sketch is not active at the current History position")
    if not Gui.getDocument(document.Name).setEdit(profile.Name):
        raise RuntimeError("The native sketch editor could not open this profile")


class _EditProfile:
    def GetResources(self):
        return {"MenuText": "Edit sheet cut sketch", "ToolTip": "Edit the shared folded/flat cut sketch",
                "Pixmap": os.path.join(SheetMetalTools.icons_path, "SheetMetal_AddCutout.svg")}

    def IsActive(self):
        selected = Gui.Selection.getSelection()
        if Gui.Control.activeDialog() or len(selected) != 1:
            return False
        try:
            step = selected[0]
            if History._active(step) is not App.ActiveDocument:
                return False
            History.get_profile(step)
            return bool(Operations.capture_revision(step))
        except (RuntimeError, ValueError):
            return False

    def Activated(self):
        try:
            selected = Gui.Selection.getSelection()
            if len(selected) != 1:
                raise ValueError("Select one sketch cut")
            open_profile_editor(selected[0])
        except (RuntimeError, ValueError) as error:
            App.Console.PrintError("Sheet Metal: " + str(error) + "\n")
