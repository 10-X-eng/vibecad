# SPDX-License-Identifier: MIT
"""Bend presets for SheetMetal: SendCutSend library + user-defined customs."""

from __future__ import annotations

import json
import os

import FreeCAD as App
import FreeCADGui as Gui

from bend_common import resolve_wb_dir
from ui_theme import apply_dark_theme
from bend_actions import (
    apply_entry_to_bends,
    create_material_sheet_for_entry,
    set_sheetmetal_defaults_for_entry,
)

# Qt compatibility (same strategy as SCSCommand; no import of SCSCommand)
QtCore = None
_widgets = None
for _mod in ("PySide6", "PySide2", "PySide"):
    try:
        if _mod == "PySide":
            from PySide import QtGui as _QtGui, QtCore as _QtCore  # type: ignore
            QtCore = _QtCore
            if hasattr(_QtGui, "QWidget"):
                _widgets = _QtGui
            break
        else:
            _Qt = __import__(_mod + ".QtCore", fromlist=["QtCore"])
            _QtWidgets = __import__(_mod + ".QtWidgets", fromlist=["QtWidgets"])
            QtCore, _widgets = _Qt, _QtWidgets
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
QLineEdit = _widgets.QLineEdit
QDoubleSpinBox = _widgets.QDoubleSpinBox
QMessageBox = _widgets.QMessageBox

WB_DIR = resolve_wb_dir()
ICON_PATH = os.path.join(WB_DIR, "resources", "icons", "SCS_Presets.svg")


def custom_data_path():
    """Writable JSON outside the Mod folder so updates do not wipe user presets."""
    root = os.path.join(App.getUserAppDataDir(), "SendCutSendPresets")
    # FreeCAD 1.1 often uses a versioned app data dir already in getUserAppDataDir()
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:
        pass
    return os.path.join(root, "custom_bends.json")


def load_custom_data():
    path = custom_data_path()
    if not os.path.isfile(path):
        return {"source": "user", "units": "inch", "materials": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if "materials" not in data:
            data["materials"] = []
        return data
    except Exception:
        return {"source": "user", "units": "inch", "materials": []}


def save_custom_data(data):
    path = custom_data_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return path


def load_scs_data():
    """Bundled SendCutSend public bend table."""
    path = os.path.join(WB_DIR, "data", "sendcutsend_bends.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if "materials" not in data:
        data["materials"] = []
    return data


def _spin(decimals=4, minimum=0.0, maximum=10.0, step=0.001):
    w = QDoubleSpinBox()
    w.setDecimals(decimals)
    w.setMinimum(minimum)
    w.setMaximum(maximum)
    w.setSingleStep(step)
    w.setMaximumWidth(140)
    return w


class CustomPresetsPanel(QWidget):
    """SendCutSend library + custom bend presets (one SheetMetal button)."""

    def __init__(self, parent=None):
        super(CustomPresetsPanel, self).__init__(parent)
        self.custom_data = load_custom_data()
        try:
            self.scs_data = load_scs_data()
        except Exception:
            self.scs_data = {"materials": []}
        self.data = self.custom_data  # active library map source
        self._build_ui()
        self._on_source_changed()

    def _build_ui(self):
        root = QVBoxLayout(self)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Preset source:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("SendCutSend library", "scs")
        self.source_combo.addItem("My custom", "custom")
        src_row.addWidget(self.source_combo, 1)
        root.addLayout(src_row)

        self.path_lbl = QLabel("")
        self.path_lbl.setWordWrap(True)
        root.addWidget(self.path_lbl)

        pick = QGroupBox("Presets")
        pick_form = QFormLayout(pick)
        self.material_combo = QComboBox()
        self.thickness_combo = QComboBox()
        pick_form.addRow("Material:", self.material_combo)
        pick_form.addRow("Thickness:", self.thickness_combo)
        root.addWidget(pick)

        self.edit_group = QGroupBox("Edit / new preset (inches)")
        form = QFormLayout(self.edit_group)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Shop Mild Steel")
        self.spin_t = _spin()
        self.spin_r = _spin()
        self.spin_k = _spin(decimals=3, minimum=0.0, maximum=1.0, step=0.01)
        self.spin_bd = _spin()
        self.spin_relief = _spin()
        self.spin_die = _spin()
        self.spin_flange = _spin()
        self.spin_corner = _spin()
        form.addRow("Material name:", self.name_edit)
        form.addRow("Thickness:", self.spin_t)
        form.addRow("Bend radius:", self.spin_r)
        form.addRow("K-factor:", self.spin_k)
        form.addRow("Bend deduction @ 90:", self.spin_bd)
        form.addRow("Bend relief depth:", self.spin_relief)
        form.addRow("Die width:", self.spin_die)
        form.addRow("Min flange:", self.spin_flange)
        form.addRow("Min corner relief:", self.spin_corner)
        root.addWidget(self.edit_group)

        mm = QLabel("Values are inches (mm shown in status when you save).")
        root.addWidget(mm)

        self.save_row_widget = QWidget()
        row_save = QHBoxLayout(self.save_row_widget)
        row_save.setContentsMargins(0, 0, 0, 0)
        self.btn_save = QPushButton("Save preset")
        self.btn_delete = QPushButton("Delete selected")
        self.btn_load_fields = QPushButton("Load into editor")
        row_save.addWidget(self.btn_save)
        row_save.addWidget(self.btn_load_fields)
        row_save.addWidget(self.btn_delete)
        root.addWidget(self.save_row_widget)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply to bends")
        self.btn_sheet = QPushButton("Create material sheet")
        self.btn_defaults = QPushButton("Set SheetMetal defaults")
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_sheet)
        row.addWidget(self.btn_defaults)
        root.addLayout(row)
        self.btn_all = QPushButton("Apply all (bends + sheet + defaults)")
        root.addWidget(self.btn_all)

        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.material_combo.currentIndexChanged.connect(self._on_material_changed)
        self.thickness_combo.currentIndexChanged.connect(self._on_thickness_changed)
        self.btn_save.clicked.connect(self.save_preset)
        self.btn_delete.clicked.connect(self.delete_preset)
        self.btn_load_fields.clicked.connect(self.load_selected_into_editor)
        self.btn_apply.clicked.connect(self.apply_to_selection)
        self.btn_sheet.clicked.connect(self.create_material_sheet)
        self.btn_defaults.clicked.connect(self.set_sheetmetal_defaults)
        self.btn_all.clicked.connect(self.apply_all)

    def _set_status(self, text):
        self.status.setText(text)
        App.Console.PrintMessage("[Bend Presets] %s\n" % text)

    def _source_key(self):
        idx = self.source_combo.currentIndex()
        if idx < 0:
            return "custom"
        key = self.source_combo.itemData(idx)
        return key if key else "custom"

    def _sheet_prefix(self):
        return "SCS" if self._source_key() == "scs" else "Custom"

    def _source_note(self):
        if self._source_key() == "scs":
            return "SendCutSend bending calculator"
        return "User custom bend preset"

    def _on_source_changed(self, *_a):
        key = self._source_key()
        if key == "scs":
            self.data = self.scs_data
            self.path_lbl.setText(
                "SendCutSend public bend table (read-only). Switch to My custom to save your own."
            )
            self.edit_group.setVisible(False)
            self.save_row_widget.setVisible(False)
        else:
            self.custom_data = load_custom_data()
            self.data = self.custom_data
            self.path_lbl.setText("Saved in: %s" % custom_data_path())
            self.edit_group.setVisible(True)
            self.save_row_widget.setVisible(True)
        self._reload_combos()

    def _materials_map(self):
        return {m["name"]: m.get("thicknesses", []) for m in self.data.get("materials", [])}

    def _reload_combos(self, prefer_mat=None, prefer_t=None):
        mats = self._materials_map()
        self.material_combo.blockSignals(True)
        self.material_combo.clear()
        for name in mats:
            self.material_combo.addItem(name)
        self.material_combo.blockSignals(False)
        if prefer_mat and prefer_mat in mats:
            self.material_combo.setCurrentText(prefer_mat)
        self._on_material_changed()
        if prefer_t is not None:
            for i in range(self.thickness_combo.count()):
                row = self.thickness_combo.itemData(i)
                if row and abs(float(row["t"]) - float(prefer_t)) < 1e-9:
                    self.thickness_combo.setCurrentIndex(i)
                    break

    def _on_material_changed(self, *_a):
        name = self.material_combo.currentText()
        rows = self._materials_map().get(name, [])
        self.thickness_combo.blockSignals(True)
        self.thickness_combo.clear()
        for row in rows:
            self.thickness_combo.addItem('%.3f"' % row["t"], row)
        self.thickness_combo.blockSignals(False)
        self._on_thickness_changed()

    def _on_thickness_changed(self, *_a):
        pass

    def _current_entry(self):
        idx = self.thickness_combo.currentIndex()
        if idx < 0:
            return None
        return self.thickness_combo.itemData(idx)

    def _entry_from_editor(self):
        return {
            "t": float(self.spin_t.value()),
            "r": float(self.spin_r.value()),
            "k": float(self.spin_k.value()),
            "bd": float(self.spin_bd.value()),
            "relief": float(self.spin_relief.value()),
            "die": float(self.spin_die.value()),
            "min_flange": float(self.spin_flange.value()),
            "min_corner_relief": float(self.spin_corner.value()),
        }

    def load_selected_into_editor(self):
        entry = self._current_entry()
        name = self.material_combo.currentText()
        if not entry:
            self._set_status("No saved thickness selected.")
            return
        self.name_edit.setText(name)
        self.spin_t.setValue(float(entry["t"]))
        self.spin_r.setValue(float(entry["r"]))
        self.spin_k.setValue(float(entry["k"]))
        self.spin_bd.setValue(float(entry.get("bd", 0)))
        self.spin_relief.setValue(float(entry.get("relief", 0)))
        self.spin_die.setValue(float(entry.get("die", 0)))
        self.spin_flange.setValue(float(entry.get("min_flange", 0)))
        self.spin_corner.setValue(float(entry.get("min_corner_relief", 0)))
        self._set_status("Loaded %s @ %.3f in into editor." % (name, entry["t"]))

    def save_preset(self):
        name = self.name_edit.text().strip()
        if not name:
            self._set_status("Enter a material name before saving.")
            return
        entry = self._entry_from_editor()
        if entry["t"] <= 0:
            self._set_status("Thickness must be > 0.")
            return
        if entry["r"] < 0 or entry["k"] < 0:
            self._set_status("Radius and K-factor cannot be negative.")
            return

        if self._source_key() == "scs":
            self._set_status("SendCutSend library is read-only — switch to My custom to save.")
            return

        materials = self.custom_data.setdefault("materials", [])
        mat = None
        for m in materials:
            if m.get("name") == name:
                mat = m
                break
        if mat is None:
            mat = {"name": name, "thicknesses": []}
            materials.append(mat)

        rows = mat.setdefault("thicknesses", [])
        replaced = False
        for i, row in enumerate(rows):
            if abs(float(row["t"]) - entry["t"]) < 1e-9:
                rows[i] = entry
                replaced = True
                break
        if not replaced:
            rows.append(entry)
            rows.sort(key=lambda r: float(r["t"]))

        path = save_custom_data(self.custom_data)
        self.data = self.custom_data
        self._reload_combos(prefer_mat=name, prefer_t=entry["t"])
        self._set_status(
            "Saved %s @ %.3f in (k=%.3f, r=%.4f). File: %s"
            % (name, entry["t"], entry["k"], entry["r"], path)
        )

    def delete_preset(self):
        name = self.material_combo.currentText()
        entry = self._current_entry()
        if not name or not entry:
            self._set_status("Nothing selected to delete.")
            return
        if self._source_key() == "scs":
            self._set_status("SendCutSend library is read-only.")
            return
        materials = self.custom_data.get("materials", [])
        for m in materials:
            if m.get("name") != name:
                continue
            rows = m.get("thicknesses", [])
            m["thicknesses"] = [
                r for r in rows if abs(float(r["t"]) - float(entry["t"])) >= 1e-9
            ]
            if not m["thicknesses"]:
                self.custom_data["materials"] = [
                    x for x in materials if x.get("name") != name
                ]
            break
        save_custom_data(self.custom_data)
        self.custom_data = load_custom_data()
        self.data = self.custom_data
        self._reload_combos()
        self._set_status("Deleted %s @ %.3f in." % (name, entry["t"]))

    def _active_mat_and_entry(self):
        """Prefer editor values (custom mode) if name filled; else combo selection."""
        if self._source_key() == "custom":
            name = self.name_edit.text().strip()
            if name and self.spin_t.value() > 0:
                return name, self._entry_from_editor()
        name = self.material_combo.currentText()
        entry = self._current_entry()
        return name, entry

    def apply_to_selection(self):
        name, entry = self._active_mat_and_entry()
        if not entry:
            self._set_status("Select a saved preset or fill the editor.")
            return
        msg = apply_entry_to_bends(
            entry,
            mat_name=name or None,
            sheet_prefix=self._sheet_prefix(),
            log=self._set_status,
        )
        self._set_status(msg)

    def create_material_sheet(self):
        name, entry = self._active_mat_and_entry()
        if not name or not entry:
            self._set_status("Select a saved preset or fill the editor (with name).")
            return
        msg = create_material_sheet_for_entry(
            name,
            entry,
            prefix=self._sheet_prefix(),
            source=self._source_note(),
            log=self._set_status,
        )
        self._set_status(msg)

    def set_sheetmetal_defaults(self):
        _name, entry = self._active_mat_and_entry()
        if not entry:
            self._set_status("Select a saved preset or fill the editor.")
            return
        self._set_status(set_sheetmetal_defaults_for_entry(entry))

    def apply_all(self):
        # Sheet first so Unfold features can be pointed at it, then bends + sync
        self.create_material_sheet()
        self.apply_to_selection()
        self._set_status(
            self.status.text()
            + " Tip: recompute Unfold (or tweak & recompute) if the flat pattern is stale."
        )


class CustomPresetsDialog:
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
        self.dialog.setWindowTitle("Bend Presets (SCS + Custom)")
        try:
            self.dialog.destroyed.connect(self._on_destroyed)
        except Exception:
            pass

        layout = QVBoxLayout(self.dialog)
        self.panel = CustomPresetsPanel()
        layout.addWidget(self.panel)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.dialog.close)
        layout.addWidget(close_btn)
        apply_dark_theme(self.dialog)
        self.dialog.resize(540, 620)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()


_custom_dialog = None


def show_custom_presets_dialog():
    global _custom_dialog
    if _custom_dialog is None:
        _custom_dialog = CustomPresetsDialog()
    _custom_dialog.show()


class SCS_ShowCustomPresetsCommand:
    def GetResources(self):
        icon = os.path.join(WB_DIR, "resources", "icons", "SCS_Custom.svg")
        if not os.path.isfile(icon):
            icon = ICON_PATH
        return {
            "Pixmap": icon,
            "MenuText": "Bend Presets (SCS + Custom)",
            "Accel": "S, U",
            "ToolTip": "SendCutSend library + your custom bend presets for SheetMetal",
        }

    def Activated(self):
        show_custom_presets_dialog()

    def IsActive(self):
        return True


if App.GuiUp:
    Gui.addCommand("SCS_ShowCustomPresets", SCS_ShowCustomPresetsCommand())
