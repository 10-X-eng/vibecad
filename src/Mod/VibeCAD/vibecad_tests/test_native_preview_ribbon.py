# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import VibeCADNativePreviewRibbon as ribbon


class _Signal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        self._slots.append(slot)


class FakeWidget:
    def __init__(self, object_name: str = "", parent=None) -> None:
        self._object_name = object_name
        self._parent = parent
        self._children: list[FakeWidget] = []
        self._layout = FakeLayout(self)
        self.clicked = _Signal()
        if parent is not None:
            parent._children.append(self)

    def objectName(self) -> str:
        return self._object_name

    def setObjectName(self, name: str) -> None:
        self._object_name = name

    def layout(self):
        return self._layout

    def setLayout(self, layout) -> None:
        self._layout = layout

    def show(self) -> None:
        return None

    def setText(self, _text: str) -> None:
        return None

    def setAutoRaise(self, _value: bool) -> None:
        return None

    def setToolTip(self, _text: str) -> None:
        return None

    def findChildren(self, _kind, name: str | None = None):
        found: list[FakeWidget] = []
        for child in self._children:
            if name is None or child.objectName() == name:
                found.append(child)
            found.extend(child.findChildren(_kind, name))
        return found


class FakeLayout:
    def __init__(self, parent: FakeWidget) -> None:
        self._parent = parent
        self.widgets: list[FakeWidget] = []

    def addWidget(self, widget: FakeWidget) -> None:
        self.widgets.append(widget)
        if widget not in self._parent._children:
            self._parent._children.append(widget)

    def insertWidget(self, index: int, widget: FakeWidget) -> None:
        self.widgets.insert(index, widget)
        if widget not in self._parent._children:
            self._parent._children.append(widget)


def test_preview_group_is_inserted_on_the_ribbon_page() -> None:
    page = FakeWidget("VibeCADRibbonPage")
    other = FakeWidget("VibeCADRibbonGroup_View")
    page.layout().addWidget(other)
    qt_widgets = SimpleNamespace(QFrame=FakeWidget, QWidget=FakeWidget, QHBoxLayout=FakeLayout, QToolButton=FakeWidget)
    gui = SimpleNamespace(runCommand=lambda _name: None, addCommand=lambda *_a, **_k: None)
    group = ribbon._append_preview_group(page, qt_widgets, gui)
    assert group.objectName() == ribbon.GROUP_OBJECT_NAME
    assert page.layout().widgets[0] is group
    assert [item[1] for item in ribbon.PREVIEW_BUTTONS] == [
        "VibeCAD_ApplyNativePreview",
        "VibeCAD_RejectNativePreview",
    ]


def test_install_registers_commands_and_returns_true() -> None:
    registered: dict[str, object] = {}
    page = FakeWidget("VibeCADRibbonPage")
    window = FakeWidget("MainWindow")
    window._children.append(page)

    def find_child(_kind, name: str):
        if name == "VibeCADRibbonPage":
            return page
        return None

    window.findChild = find_child  # type: ignore[method-assign]
    gui = SimpleNamespace(
        getMainWindow=lambda: window,
        addCommand=lambda name, command: registered.__setitem__(name, command),
        runCommand=lambda _name: None,
    )
    qt_widgets = SimpleNamespace(
        QFrame=FakeWidget,
        QWidget=FakeWidget,
        QHBoxLayout=FakeLayout,
        QToolButton=FakeWidget,
    )
    assert ribbon.install_native_preview_ribbon(gui=gui, qt_widgets=qt_widgets) is True
    assert "VibeCAD_ApplyNativePreview" in registered
    assert "VibeCAD_RejectNativePreview" in registered
    assert any(
        child.objectName() == ribbon.GROUP_OBJECT_NAME for child in page._children
    )


def test_initgui_schedules_native_preview_ribbon() -> None:
    source = Path(__file__).resolve().parents[1] / "InitGui.py"
    text = source.read_text(encoding="utf-8")
    assert "VibeCADNativePreviewRibbon.install_native_preview_ribbon" in text
    assert "QtCore.QTimer.singleShot(0, _setup_native_preview_ribbon)" in text
