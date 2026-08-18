# SPDX-License-Identifier: LGPL-2.1-or-later

"""Legacy optional Aero workspace for hosts that explicitly provide one.

The native Aero domain presents its commands through the standard VibeCAD
ribbon and does not mount this widget automatically. The module remains
available for compatibility with older hosts that provide
``VibeCADAeroWorkspaceHost``.
"""

from __future__ import annotations

from typing import Any

import AeroConfig

_HOST_NAME = "VibeCADAeroWorkspaceHost"
_WIDGET_NAME = "VibeCADAeroWorkspace"
_VEHICLE_LABELS = (
    ("airplane", "Airplane"),
    ("multirotor", "Multirotor drone"),
    ("tailsitter", "Tailsitter VTOL"),
)

_workspace: AeroWorkspaceWidget | None = None
_dock = None


def show_workspace() -> None:
    widget = _ensure_widget()
    if widget is None:
        return
    widget.reload()
    widget.show()
    host = _ribbon_host()
    if host is not None:
        host.show()
        return
    if _dock is not None:
        _dock.show()
        _dock.setFloating(False)


def hide_workspace() -> None:
    host = _ribbon_host()
    if host is not None:
        host.hide()
    if _dock is not None:
        _dock.hide()


def refresh_workspace() -> None:
    if _workspace is not None:
        _workspace.reload()


def _ensure_widget() -> AeroWorkspaceWidget | None:
    global _workspace, _dock
    try:
        from PySide import QtCore, QtWidgets
    except Exception:
        return None

    if _workspace is not None:
        return _workspace

    widget = AeroWorkspaceWidget()
    host = _ribbon_host()
    if host is not None:
        layout = host.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(host)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(4)
        layout.addWidget(widget)
        _workspace = widget
        return widget

    try:
        import FreeCADGui
    except Exception:
        _workspace = widget
        return widget

    main = FreeCADGui.getMainWindow()
    if main is None:
        _workspace = widget
        return widget
    dock = QtWidgets.QDockWidget("Aero", main)
    dock.setObjectName("VibeCADAeroDock")
    dock.setWidget(widget)
    dock.setFloating(False)
    main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    _dock = dock
    _workspace = widget
    return widget


def _ribbon_host():
    try:
        import FreeCADGui
        from PySide import QtWidgets
    except Exception:
        return None
    main = FreeCADGui.getMainWindow()
    if main is None:
        return None
    return main.findChild(QtWidgets.QWidget, _HOST_NAME)


class AeroWorkspaceWidget:
    """Built lazily so InitGui can import this module without constructing Qt."""

    def __new__(cls, *args, **kwargs):
        from PySide import QtWidgets

        class _Widget(QtWidgets.QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setObjectName(_WIDGET_NAME)
                self._writing = False
                self._build()

            def _build(self) -> None:
                from PySide import QtCore, QtWidgets

                root = QtWidgets.QHBoxLayout(self)
                root.setContentsMargins(0, 0, 0, 0)
                root.setSpacing(12)

                vehicle = QtWidgets.QGroupBox("Vehicle", self)
                form = QtWidgets.QGridLayout(vehicle)
                form.setContentsMargins(8, 6, 8, 6)
                form.setHorizontalSpacing(8)
                form.setVerticalSpacing(4)

                self.vehicle_type = QtWidgets.QComboBox(vehicle)
                for value, label in _VEHICLE_LABELS:
                    self.vehicle_type.addItem(label, value)
                self.airfoil = QtWidgets.QLineEdit(vehicle)
                self.airfoil.setText("e63")
                self.span = self._spin(vehicle, 1.0, 20000.0, 1.0)
                self.chord = self._spin(vehicle, 1.0, 5000.0, 0.5)
                self.auw = self._spin(vehicle, 1.0, 50000.0, 0.1)
                self.alpha = self._spin(vehicle, -20.0, 30.0, 0.1)
                self.gap = self._spin(vehicle, 0.0, 10.0, 0.05)
                self.stagger = self._spin(vehicle, 0.0, 10.0, 0.05)
                self.decalage = self._spin(vehicle, -20.0, 20.0, 0.1)
                self.n_props = self._spin(vehicle, 1.0, 16.0, 1.0)
                self.n_props.setDecimals(0)
                self.prop_diameter = self._spin(vehicle, 10.0, 2000.0, 1.0)
                self.thrust_to_weight = self._spin(vehicle, 0.05, 10.0, 0.05)

                fields = (
                    (0, 0, "Type", self.vehicle_type),
                    (0, 1, "Airfoil", self.airfoil),
                    (1, 0, "Span mm", self.span),
                    (1, 1, "Chord mm", self.chord),
                    (2, 0, "AUW g", self.auw),
                    (2, 1, "Alpha deg", self.alpha),
                    (3, 0, "Gap / c", self.gap),
                    (3, 1, "Stagger / c", self.stagger),
                    (4, 0, "Decalage deg", self.decalage),
                    (4, 1, "Prop count", self.n_props),
                    (5, 0, "Prop Ø mm", self.prop_diameter),
                    (5, 1, "T/W", self.thrust_to_weight),
                )
                for row, col, label, widget in fields:
                    form.addWidget(QtWidgets.QLabel(label, vehicle), row, col * 2)
                    form.addWidget(widget, row, col * 2 + 1)

                self.vehicle_type.currentIndexChanged.connect(self._on_edit)
                self.airfoil.editingFinished.connect(self._on_edit)
                for spin in (
                    self.span,
                    self.chord,
                    self.auw,
                    self.alpha,
                    self.gap,
                    self.stagger,
                    self.decalage,
                    self.n_props,
                    self.prop_diameter,
                    self.thrust_to_weight,
                ):
                    spin.valueChanged.connect(self._on_edit)

                results = QtWidgets.QGroupBox("Results", self)
                grid = QtWidgets.QGridLayout(results)
                grid.setContentsMargins(8, 6, 8, 6)
                grid.setHorizontalSpacing(10)
                grid.setVerticalSpacing(2)
                self._result_labels: dict[str, Any] = {}
                items = (
                    ("CL", "CL"),
                    ("CD", "CD"),
                    ("CM", "CM"),
                    ("CLalpha", "CLα / rad"),
                    ("Cmalpha", "Cmα / rad"),
                    ("Re", "Re"),
                    ("V_loaf", "V_loaf m/s"),
                    ("P_hover", "P_hover W (momentum-theory)"),
                    ("P_cruise", "P_cruise W"),
                    ("PitchUnstable", "Pitch"),
                    ("source", "Source"),
                    ("jsbsim", "JSBSim"),
                )
                for index, (key, title) in enumerate(items):
                    row, col = divmod(index, 2)
                    caption = QtWidgets.QLabel(title, results)
                    value = QtWidgets.QLabel("—", results)
                    value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                    value.setObjectName(f"VibeCADAeroResult_{key}")
                    grid.addWidget(caption, row, col * 2)
                    grid.addWidget(value, row, col * 2 + 1)
                    self._result_labels[key] = value

                root.addWidget(vehicle, 1)
                root.addWidget(results, 1)
                self.reload()

            def _spin(self, parent, minimum: float, maximum: float, step: float):
                from PySide import QtWidgets

                box = QtWidgets.QDoubleSpinBox(parent)
                box.setRange(minimum, maximum)
                box.setSingleStep(step)
                box.setDecimals(3)
                box.setKeyboardTracking(False)
                return box

            def _on_edit(self, *_args) -> None:
                if self._writing:
                    return
                self._write_config()
                self._update_biplane_enabled()

            def _update_biplane_enabled(self) -> None:
                vehicle = str(self.vehicle_type.currentData() or "tailsitter")
                biplane = vehicle != "multirotor"
                for widget in (self.gap, self.stagger, self.decalage):
                    widget.setEnabled(biplane)

            def values(self) -> dict[str, Any]:
                airfoil = str(self.airfoil.text() or "").strip() or "e63"
                return {
                    "vehicle_type": str(self.vehicle_type.currentData() or "tailsitter"),
                    "airfoil": airfoil,
                    "span_mm": float(self.span.value()),
                    "chord_mm": float(self.chord.value()),
                    "gap_c": float(self.gap.value()),
                    "stagger_c": float(self.stagger.value()),
                    "decalage_deg": float(self.decalage.value()),
                    "auw_g": float(self.auw.value()),
                    "alpha_deg": float(self.alpha.value()),
                    "n_props": float(self.n_props.value()),
                    "prop_diameter_mm": float(self.prop_diameter.value()),
                    "thrust_to_weight": float(self.thrust_to_weight.value()),
                }

            def _write_config(self) -> None:
                doc = _active_document()
                if doc is None:
                    return
                AeroConfig.write_config(doc, self.values())

            def reload(self) -> None:
                self._writing = True
                try:
                    doc = _active_document()
                    cfg = AeroConfig.resolve_geometry(doc)
                    self._apply_config(cfg)
                    self._apply_results(doc)
                    self._update_biplane_enabled()
                finally:
                    self._writing = False

            def _apply_config(self, cfg: dict[str, Any]) -> None:
                vehicle = AeroConfig.normalize_vehicle_type(cfg.get("vehicle_type"))
                index = self.vehicle_type.findData(vehicle)
                if index >= 0:
                    self.vehicle_type.setCurrentIndex(index)
                self.airfoil.setText(str(cfg.get("airfoil") or "e63"))
                self.span.setValue(float(cfg.get("span_mm") or 500.0))
                self.chord.setValue(float(cfg.get("chord_mm") or 90.0))
                self.auw.setValue(float(cfg.get("auw_g") or 149.6))
                self.alpha.setValue(float(cfg.get("alpha_deg") or 4.0))
                self.gap.setValue(float(cfg.get("gap_c") or 1.4))
                self.stagger.setValue(float(cfg.get("stagger_c") or 1.15))
                self.decalage.setValue(float(cfg.get("decalage_deg") or 2.0))
                self.n_props.setValue(float(cfg.get("n_props") or 2.0))
                self.prop_diameter.setValue(float(cfg.get("prop_diameter_mm") or 178.0))
                self.thrust_to_weight.setValue(float(cfg.get("thrust_to_weight") or 1.9))

            def _apply_results(self, doc: Any) -> None:
                report = None
                if doc is not None:
                    getter = getattr(doc, "getObject", None)
                    report = getter("AeroReport") if callable(getter) else None
                if report is None or getattr(report, "CL", None) is None:
                    for key, label in self._result_labels.items():
                        if key == "jsbsim":
                            path = ""
                            if doc is not None:
                                path = str(getattr(doc, "JSBSimPlantPath", "") or "")
                            label.setText(path or "—")
                        else:
                            label.setText("—")
                    return
                self._result_labels["CL"].setText(_fmt(getattr(report, "CL", None)))
                self._result_labels["CD"].setText(_fmt(getattr(report, "CD", None)))
                self._result_labels["CM"].setText(_fmt(getattr(report, "CM", None)))
                self._result_labels["CLalpha"].setText(_fmt(getattr(report, "CLalpha", None)))
                self._result_labels["Cmalpha"].setText(_fmt(getattr(report, "Cmalpha", None)))
                self._result_labels["Re"].setText(_fmt(getattr(report, "Re", None), digits=0))
                self._result_labels["V_loaf"].setText(_fmt(getattr(report, "V_loaf", None)))
                self._result_labels["P_hover"].setText(_fmt(getattr(report, "P_hover", None)))
                self._result_labels["P_cruise"].setText(_fmt(getattr(report, "P_cruise", None)))
                unstable = bool(getattr(report, "PitchUnstable", False))
                self._result_labels["PitchUnstable"].setText(
                    "UNSTABLE (Cmα > 0)" if unstable else "stable"
                )
                self._result_labels["source"].setText(
                    str(getattr(report, "Source", "") or "—")
                )
                path = str(getattr(report, "JSBSimPlantPath", "") or "")
                if not path and doc is not None:
                    path = str(getattr(doc, "JSBSimPlantPath", "") or "")
                boot = str(getattr(report, "JSBSimBootError", "") or "")
                if path and boot:
                    self._result_labels["jsbsim"].setText(f"{path} (boot: {boot})")
                elif path:
                    self._result_labels["jsbsim"].setText(f"{path} (loaded)")
                else:
                    self._result_labels["jsbsim"].setText("—")

        instance = _Widget(*args, **kwargs)
        instance.__class__.__name__ = "AeroWorkspaceWidget"
        return instance


def _active_document() -> Any | None:
    try:
        import FreeCAD

        return FreeCAD.ActiveDocument
    except Exception:
        return None


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if digits <= 0:
        return f"{number:.0f}"
    return f"{number:.{digits}f}"
