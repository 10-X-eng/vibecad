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
        self._properties: dict[str, object] = {}
        self._layout = FakeLayout(self)
        self.clicked = _Signal()
        if parent is not None:
            parent._children.append(self)

    def objectName(self) -> str:
        return self._object_name

    def setObjectName(self, name: str) -> None:
        self._object_name = name

    def property(self, name: str):
        return self._properties.get(name)

    def setProperty(self, name: str, value) -> None:
        self._properties[name] = value

    def layout(self):
        return self._layout

    def setLayout(self, layout) -> None:
        self._layout = layout

    def setParent(self, parent) -> None:
        if self._parent is not None and self in self._parent._children:
            self._parent._children.remove(self)
        self._parent = parent
        if parent is not None and self not in parent._children:
            parent._children.append(self)

    def deleteLater(self) -> None:
        return None

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

    def removeWidget(self, widget: FakeWidget) -> None:
        if widget in self.widgets:
            self.widgets.remove(widget)
        if widget in self._parent._children:
            self._parent._children.remove(widget)


def _qt_widgets():
    return SimpleNamespace(
        QFrame=FakeWidget,
        QWidget=FakeWidget,
        QHBoxLayout=FakeLayout,
        QToolButton=FakeWidget,
    )


def _gui(window: FakeWidget, registered: dict[str, object] | None = None):
    commands = registered if registered is not None else {}
    return SimpleNamespace(
        getMainWindow=lambda: window,
        addCommand=lambda name, command: commands.__setitem__(name, command),
        runCommand=lambda _name: None,
    )


def _window_with_page(page: FakeWidget) -> FakeWidget:
    window = FakeWidget("MainWindow")
    page.setParent(window)

    def find_child(_kind, name: str):
        if name == ribbon.RIBBON_PAGE_OBJECT_NAME:
            return page
        return None

    window.findChild = find_child  # type: ignore[method-assign]
    return window


def _group_names(page: FakeWidget) -> list[str]:
    return [group.objectName() for group in ribbon._iter_ribbon_groups(page)]


def test_preview_group_is_inserted_on_the_ribbon_page() -> None:
    page = FakeWidget("VibeCADRibbonPage")
    other = FakeWidget("VibeCADRibbonGroup_View")
    page.layout().addWidget(other)
    gui = SimpleNamespace(runCommand=lambda _name: None, addCommand=lambda *_a, **_k: None)
    group = ribbon._append_preview_group(page, _qt_widgets(), gui)
    assert group.objectName() == ribbon.GROUP_OBJECT_NAME
    assert page.layout().widgets[0] is group
    assert [item[1] for item in ribbon.PREVIEW_BUTTONS] == [
        "VibeCAD_ApplyNativePreview",
        "VibeCAD_RejectNativePreview",
    ]


def test_install_does_not_attach_to_qmainwindow() -> None:
    registered: dict[str, object] = {}
    window = FakeWidget("MainWindow")
    window.findChild = lambda *_args, **_kwargs: window  # type: ignore[method-assign]
    gui = _gui(window, registered)
    assert ribbon.install_native_preview_ribbon(
        gui=gui, qt_widgets=_qt_widgets(), remaining_attempts=0
    ) is False
    assert "VibeCAD_ApplyNativePreview" in registered
    assert not any(
        child.objectName() == ribbon.GROUP_OBJECT_NAME for child in window._children
    )


def test_install_registers_commands_and_returns_true() -> None:
    registered: dict[str, object] = {}
    page = FakeWidget("VibeCADRibbonPage")
    window = _window_with_page(page)
    gui = _gui(window, registered)
    assert ribbon.install_native_preview_ribbon(gui=gui, qt_widgets=_qt_widgets()) is True
    assert "VibeCAD_ApplyNativePreview" in registered
    assert "VibeCAD_RejectNativePreview" in registered
    assert _group_names(page).count(ribbon.GROUP_OBJECT_NAME) == 1


def test_fallback_installer_is_idempotent() -> None:
    page = FakeWidget(ribbon.RIBBON_PAGE_OBJECT_NAME)
    window = _window_with_page(page)
    gui = _gui(window)
    assert ribbon.install_native_preview_ribbon(gui=gui, qt_widgets=_qt_widgets()) is True
    assert ribbon.install_native_preview_ribbon(gui=gui, qt_widgets=_qt_widgets()) is True
    assert _group_names(page).count(ribbon.GROUP_OBJECT_NAME) == 1


def test_compiled_preview_group_owns_presentation() -> None:
    registered: dict[str, object] = {}
    page = FakeWidget(ribbon.RIBBON_PAGE_OBJECT_NAME)
    native = FakeWidget(ribbon.NATIVE_GROUP_OBJECT_NAME, page)
    page.layout().addWidget(native)
    window = _window_with_page(page)
    gui = _gui(window, registered)

    assert ribbon.install_native_preview_ribbon(gui=gui, qt_widgets=_qt_widgets()) is True
    assert page.property(ribbon.NATIVE_PRESENTATION_PROPERTY) is True
    assert _group_names(page).count(ribbon.NATIVE_GROUP_OBJECT_NAME) == 1
    assert ribbon.GROUP_OBJECT_NAME not in _group_names(page)
    assert set(registered) >= {
        "VibeCAD_ApplyNativePreview",
        "VibeCAD_RejectNativePreview",
    }


def test_explicit_native_capability_suppresses_fallback() -> None:
    page = FakeWidget(ribbon.RIBBON_PAGE_OBJECT_NAME)
    page.setProperty(ribbon.NATIVE_PRESENTATION_PROPERTY, True)
    window = _window_with_page(page)

    assert ribbon.install_native_preview_ribbon(
        gui=_gui(window), qt_widgets=_qt_widgets()
    ) is True
    assert ribbon.GROUP_OBJECT_NAME not in _group_names(page)


def test_native_preview_retires_legacy_fallback() -> None:
    page = FakeWidget(ribbon.RIBBON_PAGE_OBJECT_NAME)
    window = _window_with_page(page)
    gui = _gui(window)
    assert ribbon.install_native_preview_ribbon(gui=gui, qt_widgets=_qt_widgets()) is True
    assert _group_names(page).count(ribbon.GROUP_OBJECT_NAME) == 1

    native = FakeWidget(ribbon.NATIVE_GROUP_OBJECT_NAME, page)
    page.layout().addWidget(native)
    assert ribbon.install_native_preview_ribbon(gui=gui, qt_widgets=_qt_widgets()) is True
    assert _group_names(page).count(ribbon.NATIVE_GROUP_OBJECT_NAME) == 1
    assert ribbon.GROUP_OBJECT_NAME not in _group_names(page)


def test_cpp_ribbon_is_the_single_native_preview_authority() -> None:
    source = Path(__file__).resolve().parents[4] / "src" / "Gui" / "VibeCADRibbon.cpp"
    text = source.read_text(encoding="utf-8")
    assert "VibeCAD_ApplyNativePreview" in text
    assert "VibeCAD_RejectNativePreview" in text
    assert text.count('addGroup(QObject::tr("Preview")') == 1
    assert 'manifestGroups.push_back(groupManifestRecord(title, entries))' in text


def test_cpp_ribbon_refreshes_when_python_commands_register() -> None:
    source = Path(__file__).resolve().parents[4] / "src" / "Gui" / "VibeCADRibbon.cpp"
    text = source.read_text(encoding="utf-8")
    assert "commandManager().signalChanged.connect" in text
    signal_hook = text.split("commandManager().signalChanged.connect", 1)[1]
    assert "d->scheduleRefresh();" in signal_hook


def test_initgui_schedules_native_preview_ribbon() -> None:
    source = Path(__file__).resolve().parents[1] / "InitGui.py"
    text = source.read_text(encoding="utf-8")
    assert "VibeCADNativePreviewRibbon.install_native_preview_ribbon" in text
    assert "QtCore.QTimer.singleShot(0, _setup_native_preview_ribbon)" in text
