# SPDX-License-Identifier: LGPL-2.1-or-later

"""SolidWorks/Fusion-style Section View dialog for the 3D viewport."""

from __future__ import annotations

from typing import Any

try:
    import FreeCADGui as Gui
    from PySide import QtCore, QtWidgets
except ImportError:  # pragma: no cover - only outside FreeCAD (tooling/tests)
    Gui = None  # type: ignore[assignment]
    QtCore = None  # type: ignore[assignment]
    QtWidgets = None  # type: ignore[assignment]

import VibeCADSectionView as section


_dialog: "SectionViewDialog | None" = None
_PLANE_LABELS = (
    ("front", "Front (XY)"),
    ("top", "Top (XZ)"),
    ("right", "Right (YZ)"),
)


class SectionViewDialog(QtWidgets.QDialog):
    """Non-modal Front/Top/Right section controls, live-previewed in the 3D view."""

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VibeCADSectionViewDialog")
        self.setWindowTitle("Section View")
        self.setModal(False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        plane_group = QtWidgets.QGroupBox("Section plane", self)
        plane_layout = QtWidgets.QVBoxLayout(plane_group)
        self._plane_buttons: dict[str, QtWidgets.QRadioButton] = {}
        for name, label in _PLANE_LABELS:
            button = QtWidgets.QRadioButton(label, plane_group)
            object_name = {
                "front": "planeFront",
                "top": "planeTop",
                "right": "planeRight",
            }[name]
            button.setObjectName(object_name)
            button.toggled.connect(self._plane_changed)
            plane_layout.addWidget(button)
            self._plane_buttons[name] = button
        layout.addWidget(plane_group)

        offset_row = QtWidgets.QHBoxLayout()
        offset_label = QtWidgets.QLabel("Offset", self)
        self.offset_spin = QtWidgets.QDoubleSpinBox(self)
        self.offset_spin.setObjectName("sectionOffset")
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(1.0)
        self.offset_spin.setRange(-1_000_000.0, 1_000_000.0)
        self.offset_spin.setSuffix(" mm")
        self.offset_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self.offset_slider.setObjectName("sectionOffsetSlider")
        self.offset_slider.setRange(-1000, 1000)
        offset_row.addWidget(offset_label)
        offset_row.addWidget(self.offset_spin)
        layout.addLayout(offset_row)
        layout.addWidget(self.offset_slider)

        self.flip_button = QtWidgets.QPushButton("Flip", self)
        self.flip_button.setObjectName("sectionFlip")
        self.flip_button.setCheckable(True)
        layout.addWidget(self.flip_button)

        self.show_plane = QtWidgets.QCheckBox("Show section plane", self)
        self.show_plane.setObjectName("sectionShowPlane")
        self.show_plane.setChecked(True)
        layout.addWidget(self.show_plane)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.offset_spin.valueChanged.connect(self._offset_spin_changed)
        self.offset_slider.valueChanged.connect(self._offset_slider_changed)
        self.flip_button.toggled.connect(self._flip_changed)
        self.show_plane.toggled.connect(self._show_plane_changed)
        self.destroyed.connect(_clear_dialog)
        self._load_from_settings()

    def _load_from_settings(self) -> None:
        settings = section.current_section_view_settings()
        self._updating = True
        self._plane_buttons[settings.plane].setChecked(True)
        self._sync_offset_limits()
        self.offset_spin.setValue(settings.offset)
        self._set_slider_from_offset(settings.offset)
        self.flip_button.setChecked(settings.flipped)
        self.show_plane.setChecked(settings.show_plane)
        self._updating = False

    def _sync_offset_limits(self) -> None:
        try:
            import FreeCAD as App
        except ImportError:
            return
        document = getattr(App, "ActiveDocument", None)
        objects = getattr(document, "Objects", ()) if document is not None else ()
        bounds = section.model_bounds(objects)
        if bounds is None:
            return
        low, high = section.section_offset_range(
            bounds, section.current_section_view_settings().plane
        )
        if high <= low:
            high = low + 1.0
        self.offset_spin.setRange(low, high)
        self.offset_slider.setRange(-1000, 1000)
        self._offset_low = low
        self._offset_high = high

    @property
    def _offset_low(self) -> float:
        return float(self.offset_spin.minimum())

    @_offset_low.setter
    def _offset_low(self, value: float) -> None:
        self.offset_spin.setMinimum(value)

    @property
    def _offset_high(self) -> float:
        return float(self.offset_spin.maximum())

    @_offset_high.setter
    def _offset_high(self, value: float) -> None:
        self.offset_spin.setMaximum(value)

    def _slider_to_offset(self, value: int) -> float:
        low = float(self.offset_spin.minimum())
        high = float(self.offset_spin.maximum())
        span = high - low
        if span == 0.0:
            return 0.0
        return low + (float(value) + 1000.0) * span / 2000.0

    def _set_slider_from_offset(self, offset: float) -> None:
        low = float(self.offset_spin.minimum())
        high = float(self.offset_spin.maximum())
        span = high - low
        if span == 0.0:
            self.offset_slider.setValue(0)
            return
        ratio = (float(offset) - low) / span
        self.offset_slider.setValue(int(round(ratio * 2000.0 - 1000.0)))

    def _plane_changed(self, checked: bool) -> None:
        if self._updating or not checked:
            return
        plane = next(
            name for name, button in self._plane_buttons.items() if button.isChecked()
        )
        self._updating = True
        section.configure_section_view(plane=plane, offset=0.0)
        self._sync_offset_limits()
        self.offset_spin.setValue(0.0)
        self._set_slider_from_offset(0.0)
        self._updating = False

    def _offset_spin_changed(self, value: float) -> None:
        if self._updating:
            return
        self._updating = True
        self._set_slider_from_offset(value)
        section.configure_section_view(offset=float(value))
        self._updating = False

    def _offset_slider_changed(self, value: int) -> None:
        if self._updating:
            return
        offset = self._slider_to_offset(value)
        self._updating = True
        self.offset_spin.setValue(offset)
        section.configure_section_view(offset=float(offset))
        self._updating = False

    def _flip_changed(self, checked: bool) -> None:
        if self._updating:
            return
        section.configure_section_view(flipped=bool(checked))

    def _show_plane_changed(self, checked: bool) -> None:
        if self._updating:
            return
        section.configure_section_view(show_plane=bool(checked))

    def accept(self) -> None:
        """Keep the section cut and close the editor, like SolidWorks OK."""

        super().accept()

    def reject(self) -> None:
        """Cancel the section cut, like SolidWorks/Fusion dismissing without keeping it."""

        global _dialog
        _dialog = None
        view = None
        try:
            if Gui is not None and Gui.ActiveDocument is not None:
                view = Gui.ActiveDocument.ActiveView
        except Exception:
            view = None
        if view is not None and section.is_section_view_active(view):
            section.set_section_view(False, view=view, show_ui=False)
        super().reject()


def _clear_dialog(*_args: Any) -> None:
    global _dialog
    _dialog = None


def _main_window() -> Any | None:
    if Gui is None:
        return None
    getter = getattr(Gui, "getMainWindow", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def show_section_view_dialog() -> SectionViewDialog | None:
    """Show the Front/Top/Right section editor without stealing a modeling task."""

    global _dialog
    if QtWidgets is None:
        return None
    if _dialog is not None:
        try:
            _dialog._load_from_settings()
            _dialog.show()
            _dialog.raise_()
            _dialog.activateWindow()
            return _dialog
        except RuntimeError:
            _dialog = None
    dialog = SectionViewDialog(_main_window())
    _dialog = dialog
    dialog.show()
    return dialog


def close_section_view_dialog() -> None:
    global _dialog
    dialog = _dialog
    _dialog = None
    if dialog is None:
        return
    try:
        dialog.hide()
        dialog.deleteLater()
    except RuntimeError:
        return


def sync_section_view_dialog(visible: bool) -> None:
    if visible:
        show_section_view_dialog()
        return
    close_section_view_dialog()
