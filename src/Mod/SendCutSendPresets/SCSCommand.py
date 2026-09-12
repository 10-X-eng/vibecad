# SPDX-License-Identifier: MIT
"""SendCutSend bend presets command and task panel for FreeCAD."""

from __future__ import annotations

import json
import os
import re

import FreeCAD as App
import FreeCADGui as Gui

# Qt compatibility: FreeCAD 0.21/1.0 often PySide2; 1.1 may be PySide6
QtCore = None
QtGui = None
_widgets = None
for _mod in ("PySide6", "PySide2", "PySide"):
    try:
        if _mod == "PySide":
            from PySide import QtGui as _QtGui, QtCore as _QtCore  # type: ignore
            QtGui, QtCore = _QtGui, _QtCore
            if hasattr(QtGui, "QWidget"):
                _widgets = QtGui
            break
        else:
            _Qt = __import__(_mod + ".QtCore", fromlist=["QtCore"])
            _QtGui = __import__(_mod + ".QtGui", fromlist=["QtGui"])
            _QtWidgets = __import__(_mod + ".QtWidgets", fromlist=["QtWidgets"])
            QtCore, QtGui, _widgets = _Qt, _QtGui, _QtWidgets
            break
    except ImportError:
        continue
if _widgets is None:
    raise ImportError("No PySide / PySide2 / PySide6 found")

QWidget = _widgets.QWidget
QVBoxLayout = _widgets.QVBoxLayout
QHBoxLayout = _widgets.QHBoxLayout
QFormLayout = _widgets.QFormLayout
QLabel = _widgets.QLabel
QComboBox = _widgets.QComboBox
QPushButton = _widgets.QPushButton
QGroupBox = _widgets.QGroupBox
QMessageBox = _widgets.QMessageBox

from ui_theme import apply_dark_theme
from bend_common import (
    INCH_TO_MM,
    RADIUS_PROP_NAMES,
    KFACTOR_PROP_NAMES,
    find_property,
    inch_quantity,
    material_short_name,
    thickness_thou,
    resolve_wb_dir,
)

WB_DIR = resolve_wb_dir()
DATA_PATH = os.path.join(WB_DIR, "data", "sendcutsend_bends.json")
ICON_PATH = os.path.join(WB_DIR, "resources", "icons", "SCS_Presets.svg")


def load_bend_data():
    with open(DATA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)






class SCSPresetsPanel(QWidget):
    """Task-panel style dialog for SendCutSend bend presets."""

    def __init__(self, parent=None):
        super(SCSPresetsPanel, self).__init__(parent)
        self.data = load_bend_data()
        self.materials = {m["name"]: m["thicknesses"] for m in self.data["materials"]}
        self._build_ui()
        self._populate_materials()
        self._on_material_changed(0)

    def _build_ui(self):
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.material_combo = QComboBox()
        self.thickness_combo = QComboBox()
        form.addRow("Material:", self.material_combo)
        form.addRow("Thickness:", self.thickness_combo)
        root.addLayout(form)

        group = QGroupBox("Bend parameters (90°)")
        grid = QFormLayout(group)
        self.lbl_radius = QLabel("-")
        self.lbl_k = QLabel("-")
        self.lbl_bd = QLabel("-")
        self.lbl_relief = QLabel("-")
        self.lbl_min_flange = QLabel("-")
        self.lbl_die = QLabel("-")
        self.lbl_corner = QLabel("-")
        grid.addRow("Bend radius:", self.lbl_radius)
        grid.addRow("K-factor:", self.lbl_k)
        grid.addRow("Bend deduction:", self.lbl_bd)
        grid.addRow("Bend relief depth:", self.lbl_relief)
        grid.addRow("Min flange:", self.lbl_min_flange)
        grid.addRow("Die width:", self.lbl_die)
        grid.addRow("Min corner relief:", self.lbl_corner)
        root.addWidget(group)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        btn_row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply to bends")
        self.btn_sheet = QPushButton("Create material sheet")
        self.btn_defaults = QPushButton("Set SheetMetal defaults")
        self.btn_all = QPushButton("Apply all (bends + sheet + defaults)")
        btn_row.addWidget(self.btn_apply)
        btn_row.addWidget(self.btn_sheet)
        btn_row.addWidget(self.btn_defaults)
        root.addLayout(btn_row)
        root.addWidget(self.btn_all)

        self.material_combo.currentIndexChanged.connect(self._on_material_changed)
        self.thickness_combo.currentIndexChanged.connect(self._on_thickness_changed)
        self.btn_apply.clicked.connect(self.apply_to_selection)
        self.btn_sheet.clicked.connect(self.create_material_sheet)
        self.btn_defaults.clicked.connect(self.set_sheetmetal_defaults)
        self.btn_all.clicked.connect(self.apply_all)

    def _populate_materials(self):
        self.material_combo.blockSignals(True)
        self.material_combo.clear()
        for name in self.materials:
            self.material_combo.addItem(name)
        self.material_combo.blockSignals(False)

    def _on_material_changed(self, _index=None):
        name = self.material_combo.currentText()
        thicknesses = self.materials.get(name, [])
        self.thickness_combo.blockSignals(True)
        self.thickness_combo.clear()
        for row in thicknesses:
            t = row["t"]
            self.thickness_combo.addItem(f'{t:.3f}"', row)
        self.thickness_combo.blockSignals(False)
        self._on_thickness_changed()

    def _current_entry(self):
        idx = self.thickness_combo.currentIndex()
        if idx < 0:
            return None
        return self.thickness_combo.itemData(idx)

    def _fmt_dual(self, value_in: float) -> str:
        mm = value_in * INCH_TO_MM
        return f'{value_in:.4f}"  ({mm:.3f} mm)'

    def _on_thickness_changed(self, _index=None):
        entry = self._current_entry()
        if not entry:
            for lbl in (
                self.lbl_radius,
                self.lbl_k,
                self.lbl_bd,
                self.lbl_relief,
                self.lbl_min_flange,
                self.lbl_die,
                self.lbl_corner,
            ):
                lbl.setText("-")
            return
        self.lbl_radius.setText(self._fmt_dual(entry["r"]))
        self.lbl_k.setText(f'{entry["k"]:.2f}')
        self.lbl_bd.setText(self._fmt_dual(entry["bd"]))
        self.lbl_relief.setText(self._fmt_dual(entry["relief"]))
        self.lbl_min_flange.setText(self._fmt_dual(entry["min_flange"]))
        self.lbl_die.setText(self._fmt_dual(entry["die"]))
        self.lbl_corner.setText(self._fmt_dual(entry["min_corner_relief"]))

    def _set_status(self, text: str):
        self.status.setText(text)
        App.Console.PrintMessage(f"[SCS Presets] {text}\n")


    def apply_to_selection(self):
        entry = self._current_entry()
        if not entry:
            self._set_status("No thickness selected.")
            return

        radius_q = inch_quantity(entry["r"])
        k = float(entry["k"])
        updated = 0
        skipped = []

        from bend_actions import collect_bend_targets, check_min_flange_lengths
        targets = collect_bend_targets()

        if not targets:
            self._set_status(
                "Nothing to update. Select a SheetMetal Bend/Wall (or its Body), "
                "or create one first."
            )
            return

        for obj in targets:
            r_prop = find_property(obj, RADIUS_PROP_NAMES)
            k_prop = find_property(obj, KFACTOR_PROP_NAMES)
            changed = False
            try:
                if r_prop:
                    setattr(obj, r_prop, radius_q)
                    changed = True
                if k_prop:
                    # SheetMetal uses App::PropertyFloat "kfactor"
                    try:
                        setattr(obj, k_prop, float(k))
                    except Exception:
                        try:
                            obj.setExpression(k_prop, None)
                        except Exception:
                            pass
                        setattr(obj, k_prop, float(k))
                    changed = True
            except Exception as exc:
                skipped.append(f"{obj.Label}: {exc}")
                continue
            if changed:
                updated += 1
                obj.touch()
            else:
                skipped.append(f"{obj.Label}: no radius/kfactor properties")

        if updated and App.ActiveDocument is not None:
            App.ActiveDocument.recompute()

        from bend_actions import check_min_flange_lengths
        _ok, flange_warnings = check_min_flange_lengths(
            entry, targets=targets, warn_dialog=True
        )

        # Also push unfold + new-feature defaults so flat pattern matches
        # (set silently then report one combined status)
        try:
            param = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
            param.SetString("defaultRadius", f'{entry["r"]} in')
            param.SetFloat("defaultKFactor", k)
            param.SetFloat("manualKFactor", k)
            param.SetString("kFactorStandard", "ansi")
            prefs_ok = True
        except Exception:
            prefs_ok = False

        mat_name = self.material_combo.currentText()
        short = material_short_name(mat_name)
        thou = thickness_thou(entry["t"])
        sheet_name = f"material_SCS_{short}_{thou}"
        from bend_actions import sync_unfold_features
        unfold_n = sync_unfold_features(sheet_name, k)

        msg = f"Applied radius={entry['r']} in, k={k} to {updated} object(s)."
        if prefs_ok:
            msg += f" Prefs: defaultKFactor/manualKFactor={k}."
        if unfold_n:
            msg += f" Updated {unfold_n} Unfold feature(s) to K={k} / {sheet_name}."
        elif App.ActiveDocument and not any(
            hasattr(o, "MaterialSheet") for o in App.ActiveDocument.Objects
        ):
            msg += (
                f" Create material sheet '{sheet_name}' then re-apply to sync Unfold."
            )
        if flange_warnings:
            msg += " MIN FLANGE WARN: " + "; ".join(flange_warnings)
        if skipped:
            msg += " Warnings: " + "; ".join(skipped)
        self._set_status(msg)

    def create_material_sheet(self):
        entry = self._current_entry()
        mat_name = self.material_combo.currentText()
        if not entry:
            self._set_status("No thickness selected.")
            return
        doc = App.ActiveDocument
        if doc is None:
            self._set_status("No active document. Create or open a document first.")
            return

        short = material_short_name(mat_name)
        thou = thickness_thou(entry["t"])
        sheet_name = f"material_SCS_{short}_{thou}"

        # Reuse existing object with same name if present
        existing = doc.getObject(sheet_name)
        if existing is not None:
            sheet = existing
        else:
            sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
        sheet.Label = sheet_name

        r_over_t = entry["r"] / entry["t"] if entry["t"] else 0.0
        k = float(entry["k"])

        # SheetMetal material sheet convention:
        # A1/B1 headers, A2/B2 r/t and k, A3/B3 fallback angle/k, options block
        sheet.set("A1", "Radius / Thickness")
        sheet.set("B1", "K-factor (ANSI)")
        sheet.set("A2", f"{r_over_t:.6f}")
        sheet.set("B2", f"{k:.6f}")
        sheet.set("A3", "99")
        sheet.set("B3", "0.5")
        sheet.set("A5", "Options")
        sheet.set("A6", "K-factor standard")
        sheet.set("B6", "ansi")
        # Extra reference cells for the chosen SCS preset
        sheet.set("A8", "Material")
        sheet.set("B8", mat_name)
        sheet.set("A9", "Thickness (in)")
        sheet.set("B9", f'{entry["t"]:.4f}')
        sheet.set("A10", "Bend radius (in)")
        sheet.set("B10", f'{entry["r"]:.4f}')
        sheet.set("A11", "Bend deduction (in)")
        sheet.set("B11", f'{entry["bd"]:.4f}')
        sheet.set("A12", "Source")
        sheet.set("B12", "SendCutSend bending calculator")

        doc.recompute()
        from bend_actions import sync_unfold_features
        n = sync_unfold_features(sheet_name, k)
        self._set_status(
            f"Created/updated '{sheet_name}'. Synced K={k} onto {n} Unfold object(s). "
            f"Material Definition Sheet should be '{sheet_name}' (not Manual)."
        )



    def apply_all(self):
        """One-shot: bends + material sheet + prefs (including Unfold manual K)."""
        self.apply_to_selection()
        self.create_material_sheet()
        self._set_status(
            self.status.text()
            + " Tip: reopen the Unfold task panel so the material list refreshes."
        )

    def set_sheetmetal_defaults(self):
        entry = self._current_entry()
        if not entry:
            self._set_status("No thickness selected.")
            return

        try:
            param = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        except Exception as exc:
            self._set_status(f"Could not open SheetMetal preferences: {exc}")
            return

        radius_str = f'{entry["r"]} in'
        k = float(entry["k"])
        set_msgs = []

        # SheetMetal reads these exact preference keys (see SheetMetalCmd /
        # UnfoldGUI). ParamGet.Set* never fails on unknown keys, so write the
        # real names only -- do not "probe" with fake keys.
        param.SetString("defaultRadius", radius_str)
        set_msgs.append(f"defaultRadius={radius_str}")

        param.SetFloat("defaultKFactor", k)
        set_msgs.append(f"defaultKFactor={k}")

        # Unfold task panel "Manual K-Factor" uses this, not defaultKFactor
        param.SetFloat("manualKFactor", k)
        set_msgs.append(f"manualKFactor={k}")

        param.SetString("kFactorStandard", "ansi")
        set_msgs.append("kFactorStandard=ansi")

        self._set_status("SheetMetal defaults updated: " + "; ".join(set_msgs))


class SCSPresetsDialog:
    """Reusable window for the presets panel.

    FreeCAD keeps this wrapper alive; after Close the QWidget is only hidden.
    A second toolbar click must call show() again (raise_/activate alone is not
    enough). If Qt deleted the C++ object, recreate it.
    """

    def __init__(self):
        self.dialog = None
        self.panel = None

    def _dialog_alive(self):
        if self.dialog is None:
            return False
        try:
            _ = self.dialog.isVisible()
            return True
        except RuntimeError:
            # wrap around deleted C++ QWidget
            self.dialog = None
            self.panel = None
            return False

    def _on_destroyed(self, *_args):
        self.dialog = None
        self.panel = None

    def show(self):
        if self._dialog_alive():
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return

        self.dialog = QWidget()
        # Top-level window so Close hides/destroys cleanly (not an orphan child)
        try:
            self.dialog.setWindowFlags(
                QtCore.Qt.Window
                | QtCore.Qt.WindowCloseButtonHint
                | QtCore.Qt.WindowTitleHint
            )
        except Exception:
            pass
        try:
            self.dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        except Exception:
            pass
        self.dialog.setWindowTitle("SendCutSend Bend Presets")
        try:
            self.dialog.destroyed.connect(self._on_destroyed)
        except Exception:
            pass

        layout = QVBoxLayout(self.dialog)
        self.panel = SCSPresetsPanel()
        layout.addWidget(self.panel)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.dialog.close)
        layout.addWidget(close_btn)
        apply_dark_theme(self.dialog)
        self.dialog.resize(520, 460)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()


_dialog_singleton = None


def show_presets_dialog():
    global _dialog_singleton
    if _dialog_singleton is None:
        _dialog_singleton = SCSPresetsDialog()
    _dialog_singleton.show()


class SCS_ShowPresetsCommand:
    """FreeCAD command to open the SendCutSend presets panel."""

    def GetResources(self):
        return {
            "Pixmap": ICON_PATH,
            "MenuText": "SendCutSend Presets",
            "Accel": "S, C",
            "ToolTip": "Browse SendCutSend bend presets and apply to SheetMetal",
        }

    def Activated(self):
        show_presets_dialog()

    def IsActive(self):
        return True


if App.GuiUp:
    Gui.addCommand("SCS_ShowPresets", SCS_ShowPresetsCommand())
