# SPDX-License-Identifier: MIT
"""Dark UI theme roughly matching FreeCAD's dark stylesheet."""

# Colors tuned to FreeCAD 1.x dark theme (windows / qt)
DARK_QSS = """
QWidget {
    background-color: #333333;
    color: #f0f0f0;
    font-size: 12px;
}
QLabel {
    background-color: transparent;
    color: #f0f0f0;
}
QGroupBox {
    background-color: #2b2b2b;
    border: 1px solid #555555;
    border-radius: 6px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    color: #f0f0f0;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #dddddd;
}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {
    background-color: #1e1e1e;
    color: #ffffff;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 3px 6px;
    min-height: 22px;
    selection-background-color: #3874f2;
    selection-color: #ffffff;
}
QComboBox:hover, QLineEdit:hover, QDoubleSpinBox:hover {
    border: 1px solid #777777;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #1e1e1e;
    color: #ffffff;
    selection-background-color: #3874f2;
    border: 1px solid #555555;
}
QPushButton {
    background-color: #444444;
    color: #ffffff;
    border: 1px solid #666666;
    border-radius: 4px;
    padding: 6px 12px;
    min-height: 24px;
}
QPushButton:hover {
    background-color: #555555;
    border: 1px solid #888888;
}
QPushButton:pressed {
    background-color: #2a2a2a;
}
QScrollArea, QFrame {
    background-color: #333333;
    border: none;
}
QFormLayout {
    background-color: transparent;
}
"""


def apply_dark_theme(widget):
    """Apply dark QSS to a top-level dialog or panel."""
    if widget is None:
        return
    try:
        widget.setStyleSheet(DARK_QSS)
    except Exception:
        pass
