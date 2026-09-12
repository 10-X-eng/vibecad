# SPDX-License-Identifier: LGPL-2.1-or-later

"""Section View offset panel, docked with Tasks and VibeCAD Assistant."""

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
_dock: Any | None = None


class SectionViewDialog(QtWidgets.QWidget):
    """Offset control for the plane currently being sectioned."""

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VibeCADSectionViewDialog")
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.plane_label = QtWidgets.QLabel(self)
        self.plane_label.setObjectName("sectionPlaneLabel")
        layout.addWidget(self.plane_label)

        offset_row = QtWidgets.QHBoxLayout()
        self.offset_spin = QtWidgets.QDoubleSpinBox(self)
        self.offset_spin.setObjectName("sectionOffset")
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(1.0)
        self.offset_spin.setRange(-1_000_000.0, 1_000_000.0)
        self.offset_spin.setSuffix(" mm")
        offset_row.addWidget(self.offset_spin)
        self.flip_button = QtWidgets.QPushButton("Flip", self)
        self.flip_button.setObjectName("sectionFlip")
        self.flip_button.setCheckable(True)
        offset_row.addWidget(self.flip_button)
        layout.addLayout(offset_row)

        self.offset_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self.offset_slider.setObjectName("sectionOffsetSlider")
        self.offset_slider.setRange(-1000, 1000)
        layout.addWidget(self.offset_slider)
        self.show_plane_checkbox = QtWidgets.QCheckBox("Show plane", self)
        self.show_plane_checkbox.setObjectName("sectionShowPlane")
        self.show_plane_checkbox.setToolTip("Show the plane guide while keeping the section cut active")
        layout.addWidget(self.show_plane_checkbox)
        self.show_handles_checkbox = QtWidgets.QCheckBox("Show handles", self)
        self.show_handles_checkbox.setObjectName("sectionShowHandles")
        self.show_handles_checkbox.setToolTip("Show the on-canvas translation and rotation handles")
        layout.addWidget(self.show_handles_checkbox)
        layout.addStretch(1)

        self.offset_spin.valueChanged.connect(self._offset_spin_changed)
        self.offset_slider.valueChanged.connect(self._offset_slider_changed)
        self.flip_button.toggled.connect(self._flip_changed)
        self.show_plane_checkbox.toggled.connect(self._show_plane_changed)
        self.show_handles_checkbox.toggled.connect(self._show_handles_changed)
        self.destroyed.connect(_clear_dialog)
        self._load_from_settings()

    def _load_from_settings(self) -> None:
        settings = section.current_section_view_settings()
        self._updating = True
        self.plane_label.setText(section.section_offset_label(settings))
        if _dock is not None:
            try:
                _dock.setWindowTitle(f"Section · {settings.plane.capitalize()}")
            except RuntimeError:
                pass
        self._sync_offset_limits()
        self.offset_spin.setValue(settings.offset)
        self._set_slider_from_offset(settings.offset)
        if self.flip_button.isChecked() != bool(settings.flipped):
            self.flip_button.setChecked(settings.flipped)
        self.show_plane_checkbox.setChecked(settings.show_plane)
        self.show_handles_checkbox.setChecked(settings.show_handles)
        self._updating = False

    def _sync_offset_limits(self) -> None:
        try:
            import FreeCAD as App
        except ImportError:
            return
        document = getattr(App, "ActiveDocument", None)
        # Use the same displayed bounds as the clipping plane. Reading model
        # bounds here can materialize compounds and disagree with the view.
        bounds = section._bounds_for_view(section._active_3d_view(), document)
        if bounds is None:
            return
        settings = section.current_section_view_settings()
        low, high = section.section_offset_range(
            bounds,
            settings.plane,
            yaw=settings.yaw,
            pitch=settings.pitch,
            flipped=settings.flipped,
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

    def _offset_spin_changed(self, value: float) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            self._set_slider_from_offset(value)
            section.configure_section_view(offset=float(value))
        finally:
            self._updating = False

    def _offset_slider_changed(self, value: int) -> None:
        if self._updating:
            return
        offset = self._slider_to_offset(value)
        self._updating = True
        try:
            self.offset_spin.setValue(offset)
            section.configure_section_view(offset=float(offset))
        finally:
            self._updating = False

    def _flip_changed(self, checked: bool) -> None:
        if self._updating:
            return
        section.configure_section_view(flipped=bool(checked))

    def _show_plane_changed(self, checked: bool) -> None:
        if self._updating:
            return
        section.configure_section_view(show_plane=bool(checked))

    def _show_handles_changed(self, checked: bool) -> None:
        if self._updating:
            return
        section.configure_section_view(show_handles=bool(checked))

    def isVisible(self) -> bool:  # noqa: N802
        if _dock is not None:
            try:
                return bool(_dock.isVisible())
            except RuntimeError:
                pass
        return super().isVisible()


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


def _install_section_dock(widget: SectionViewDialog) -> Any | None:
    main = _main_window()
    if main is None or QtWidgets is None or QtCore is None:
        widget.show()
        return None
    dock = QtWidgets.QDockWidget("Section View", main)
    dock.setObjectName("VibeCADSectionViewDock")
    dock.setWidget(widget)
    left = getattr(QtCore.Qt, "LeftDockWidgetArea", None)
    right = getattr(QtCore.Qt, "RightDockWidgetArea", None)
    if left is not None and right is not None:
        dock.setAllowedAreas(left | right)
    dock.setMinimumWidth(260)
    if right is not None:
        main.addDockWidget(right, dock)
    else:
        main.addDockWidget(dock)
    tabify = getattr(main, "tabifyDockWidget", None)
    if callable(tabify):
        for name in ("Std_TaskView", "VibeCADAssistantPanel"):
            sibling = main.findChild(QtWidgets.QDockWidget, name)
            if sibling is None:
                continue
            try:
                tabify(sibling, dock)
            except Exception:
                continue
    dock.show()
    dock.raise_()
    return dock


def show_section_view_dialog() -> SectionViewDialog | None:
    """Show the offset panel in the right sidebar with Tasks and Assistant."""

    global _dialog, _dock
    if QtWidgets is None:
        return None
    if _dialog is not None:
        try:
            _dialog._load_from_settings()
            if _dock is not None:
                _dock.show()
                _dock.raise_()
            else:
                _dialog.show()
            return _dialog
        except RuntimeError:
            _dialog = None
            _dock = None
    dialog = SectionViewDialog(_main_window())
    _dialog = dialog
    _dock = _install_section_dock(dialog)
    return dialog


def close_section_view_dialog() -> None:
    global _dialog, _dock
    dock = _dock
    dialog = _dialog
    _dock = None
    _dialog = None
    if dock is not None:
        try:
            dock.hide()
            dock.deleteLater()
            return
        except RuntimeError:
            pass
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


def refresh_section_view_dialog() -> None:
    if _dialog is None:
        return
    try:
        _dialog._load_from_settings()
    except RuntimeError:
        return
