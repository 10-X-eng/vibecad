# SPDX-License-Identifier: LGPL-2.1-or-later

"""Aero ribbon fallback for builds without the native Aero domain."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace

import VibeCADAeroRibbon


REPO = Path(__file__).resolve().parents[4]
STOCK_GROUP_TITLES = (
    "View",
    "Structure",
    "Solids",
    "Finish",
    "Transform",
    "Geometry",
    "Modify",
    "Inspect",
    "Fasteners",
    "Surface",
    "Connect",
)
AERO_COMMANDS = (
    ("Analyze", "VibeCADAero_Analyze"),
    ("Section", "VibeCADAero_Section"),
    ("3D VLM", "VibeCADAero_VLM"),
    ("JSBSim", "VibeCADAero_ExportJSBSim"),
    ("Report", "VibeCADAero_Report"),
    ("Propose", "VibeCADAero_ProposeRepairs"),
    ("Apply", "VibeCADAero_ApplyRepairs"),
    ("Card", "VibeCADAero_FlightCard"),
)


class _Signal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        if slot not in self._slots:
            self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self._slots):
            slot(*args)


class FakeWidget:
    def __init__(self, object_name: str = "", parent=None) -> None:
        self._object_name = object_name
        self._parent = parent
        self._visible = True
        self._children: list[FakeWidget] = []
        self._layout = FakeLayout(self)
        if parent is not None:
            parent._children.append(self)

    def objectName(self) -> str:
        return self._object_name

    def setObjectName(self, name: str) -> None:
        self._object_name = name

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def isVisible(self) -> bool:
        return self._visible

    def layout(self):
        return self._layout

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
            widget._parent = self._parent

    def insertWidget(self, index: int, widget: FakeWidget) -> None:
        self.widgets.insert(index, widget)
        if widget not in self._parent._children:
            self._parent._children.append(widget)
            widget._parent = self._parent

    def count(self) -> int:
        return len(self.widgets)


class FakeButton(FakeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__("QToolButton", parent)
        self._text = ""
        self._icon = ""
        self.clicked = _Signal()

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setIcon(self, icon) -> None:
        self._icon = icon

    def setToolButtonStyle(self, _style) -> None:
        pass

    def setAutoRaise(self, _value) -> None:
        pass

    def setToolTip(self, _text) -> None:
        pass


class FakeIcon:
    def __init__(self, path: str = "") -> None:
        self.path = path


class FakeTabBar(FakeWidget):
    def __init__(self) -> None:
        super().__init__("VibeCADRibbonTabs")
        self._tabs = [
            ("Model", "PartDesignWorkbench"),
            ("Assemble", "AssemblyWorkbench"),
            ("Mesh", "MeshWorkbench"),
            ("Analyze", "FemWorkbench"),
            ("Manufacture", "CAMWorkbench"),
            ("Drawing", "TechDrawWorkbench"),
            ("Parameters", "SpreadsheetWorkbench"),
        ]
        self._current = 0
        self.currentChanged = _Signal()
        self._blocked = False

    def count(self) -> int:
        return len(self._tabs)

    def tabText(self, index: int) -> str:
        return self._tabs[index][0]

    def tabData(self, index: int) -> str:
        return self._tabs[index][1]

    def setTabData(self, index: int, data: str) -> None:
        label, _previous = self._tabs[index]
        self._tabs[index] = (label, data)

    def insertTab(self, index: int, text: str) -> int:
        self._tabs.insert(index, (text, ""))
        return index

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, index: int) -> None:
        self._current = index
        if not self._blocked:
            self.currentChanged.emit(index)

    def blockSignals(self, blocked: bool) -> None:
        self._blocked = blocked


class FakeMainWindow(FakeWidget):
    def __init__(self, tabs: FakeTabBar, page: FakeWidget, groups: list[FakeWidget]) -> None:
        super().__init__("MainWindow")
        self._tabs = tabs
        self._page = page
        self._children.extend([tabs, page, *groups])

    def findChild(self, _kind, name: str):
        if name == "VibeCADRibbonTabs":
            return self._tabs
        if name == "VibeCADRibbonPage":
            return self._page
        return None


def _stock_groups() -> list[FakeWidget]:
    groups = []
    for title in STOCK_GROUP_TITLES:
        group = FakeWidget(f"VibeCADRibbonGroup_{title}")
        groups.append(group)
    return groups


def _qt(tabs: FakeTabBar, page: FakeWidget, groups: list[FakeWidget]):
    main_window = FakeMainWindow(tabs, page, groups)
    registered_commands: dict[str, object] = {}
    gui = SimpleNamespace(
        getMainWindow=lambda: main_window,
        activateWorkbench=lambda _name: (_ for _ in ()).throw(
            AssertionError("Aero install must not activate a workbench")
        ),
        addCommand=lambda name, command: registered_commands.__setitem__(name, command),
        listCommands=lambda: list(registered_commands),
        runCommand=lambda _name: None,
    )
    qt_widgets = SimpleNamespace(
        QApplication=SimpleNamespace(instance=lambda: object()),
        QWidget=FakeWidget,
        QFrame=FakeWidget,
        QToolButton=FakeButton,
        QTabBar=FakeTabBar,
        QHBoxLayout=FakeLayout,
        QVBoxLayout=FakeLayout,
    )
    qt_gui = SimpleNamespace(QIcon=FakeIcon)
    qt_core = SimpleNamespace(
        QTimer=SimpleNamespace(singleShot=lambda _delay, callback: callback()),
        Qt=SimpleNamespace(ToolButtonTextUnderIcon=1),
    )
    return gui, qt_widgets, qt_gui, qt_core, main_window


def test_aero_ribbon_module_does_not_import_optional_solvers() -> None:
    tree = ast.parse(
        (REPO / "src/Mod/VibeCAD/VibeCADAeroRibbon.py").read_text(encoding="utf-8")
    )
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"neuralfoil", "aerosandbox", "jsbsim"})


def test_cmake_installs_aero_ribbon_module() -> None:
    cmake = (REPO / "src/Mod/VibeCAD/CMakeLists.txt").read_text(encoding="utf-8")
    assert "VibeCADAeroRibbon.py" in cmake
    assert "test_aero_ribbon_install.py" in cmake


def test_initgui_schedules_aero_ribbon_next_to_agent_control() -> None:
    source = (REPO / "src/Mod/VibeCAD/InitGui.py").read_text(encoding="utf-8")
    assert "def _setup_aero_ribbon" in source
    assert "VibeCADAeroRibbon.install_aero_ribbon_tab" in source
    agent = source.index("QtCore.QTimer.singleShot(0, _setup_agent_control)")
    aero = source.index("QtCore.QTimer.singleShot(0, _setup_aero_ribbon)")
    assert agent < aero
    assert "activateWorkbench" not in source[source.index("def _setup_aero_ribbon") :]


def test_command_loader_ignores_cached_generic_commands_module(monkeypatch) -> None:
    registered: dict[str, object] = {}
    gui = SimpleNamespace(
        addCommand=lambda name, command: registered.__setitem__(name, command),
        listCommands=lambda: list(registered),
    )
    generic_commands = SimpleNamespace(owner="another workbench")
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.setitem(sys.modules, "Commands", generic_commands)
    monkeypatch.delitem(sys.modules, "AeroCommandLoader", raising=False)
    monkeypatch.delitem(sys.modules, "_vibecad_aero_commands", raising=False)

    VibeCADAeroRibbon._ensure_aero_commands()

    assert {
        "VibeCADAero_Analyze",
        "VibeCADAero_Section",
        "VibeCADAero_VLM",
        "VibeCADAero_ExportJSBSim",
        "VibeCADAero_Report",
    } <= set(registered)
    assert sys.modules["Commands"] is generic_commands


def test_aero_group_is_inserted_before_existing_model_groups() -> None:
    page = FakeWidget("VibeCADRibbonPage")
    model_group = FakeWidget("VibeCADRibbonGroup_View")
    page.layout().addWidget(model_group)
    tabs = FakeTabBar()
    groups = [model_group]
    gui, qt_widgets, qt_gui, _qt_core, _window = _qt(tabs, page, groups)

    group = VibeCADAeroRibbon._append_aero_group(
        page,
        qt_widgets,
        qt_gui,
        gui,
    )

    assert page.layout().widgets[0] is group
    assert page.layout().widgets[1] is model_group


def test_installer_inserts_aero_after_parameters_without_activating() -> None:
    tabs = FakeTabBar()
    page = FakeWidget("VibeCADRibbonPage")
    groups = _stock_groups()
    gui, qt_widgets, qt_gui, qt_core, _window = _qt(tabs, page, groups)

    installed = VibeCADAeroRibbon.install_aero_ribbon_tab(
        gui=gui,
        qt_widgets=qt_widgets,
        qt_gui=qt_gui,
        qt_core=qt_core,
    )

    assert installed is True
    assert tabs.currentIndex() == 0
    assert [tabs.tabText(index) for index in range(tabs.count())][-2:] == [
        "Parameters",
        "Aero",
    ]
    assert tabs.tabText(tabs.count() - 1) == "Aero"
    parameters = next(
        index for index in range(tabs.count()) if tabs.tabText(index) == "Parameters"
    )
    assert tabs.tabText(parameters + 1) == "Aero"


def test_existing_native_aero_tab_does_not_install_python_page_patch() -> None:
    tabs = FakeTabBar()
    tabs._tabs.append(("Aero", "VibeCADAeroWorkbench"))
    page = FakeWidget("VibeCADRibbonPage")
    groups = _stock_groups()
    gui, qt_widgets, qt_gui, qt_core, window = _qt(tabs, page, groups)

    installed = VibeCADAeroRibbon.install_aero_ribbon_tab(
        gui=gui,
        qt_widgets=qt_widgets,
        qt_gui=qt_gui,
        qt_core=qt_core,
    )

    assert installed is True
    assert tabs.currentChanged._slots == []
    assert not [
        widget
        for widget in window.findChildren(FakeWidget)
        if widget.objectName() == "VibeCADRibbonGroup_Aero"
    ]


def test_selecting_aero_unhides_stock_groups_and_appends_aero() -> None:
    tabs = FakeTabBar()
    page = FakeWidget("VibeCADRibbonPage")
    groups = _stock_groups()
    for group in groups:
        group.hide()
    gui, qt_widgets, qt_gui, qt_core, window = _qt(tabs, page, groups)

    VibeCADAeroRibbon.install_aero_ribbon_tab(
        gui=gui,
        qt_widgets=qt_widgets,
        qt_gui=qt_gui,
        qt_core=qt_core,
    )
    aero_index = next(
        index for index in range(tabs.count()) if tabs.tabText(index) == "Aero"
    )
    tabs.setCurrentIndex(aero_index)

    for title in STOCK_GROUP_TITLES:
        matches = [
            widget
            for widget in window.findChildren(FakeWidget)
            if widget.objectName() == f"VibeCADRibbonGroup_{title}"
        ]
        assert matches
        assert all(widget.isVisible() for widget in matches)

    aero_groups = [
        widget
        for widget in window.findChildren(FakeWidget)
        if widget.objectName() == "VibeCADRibbonGroup_Aero"
    ]
    assert len(aero_groups) == 1
    buttons = [child for child in aero_groups[0]._children if isinstance(child, FakeButton)]
    assert [button.text() for button in buttons] == [label for label, _command in AERO_COMMANDS]
    analyze = buttons[0]
    assert "vibecad-aero-analyze.svg" in str(getattr(analyze._icon, "path", analyze._icon))


def test_installer_is_idempotent_and_does_not_duplicate_aero_group() -> None:
    tabs = FakeTabBar()
    page = FakeWidget("VibeCADRibbonPage")
    groups = _stock_groups()
    gui, qt_widgets, qt_gui, qt_core, window = _qt(tabs, page, groups)

    VibeCADAeroRibbon.install_aero_ribbon_tab(
        gui=gui,
        qt_widgets=qt_widgets,
        qt_gui=qt_gui,
        qt_core=qt_core,
    )
    VibeCADAeroRibbon.install_aero_ribbon_tab(
        gui=gui,
        qt_widgets=qt_widgets,
        qt_gui=qt_gui,
        qt_core=qt_core,
    )
    aero_index = next(
        index for index in range(tabs.count()) if tabs.tabText(index) == "Aero"
    )
    tabs.setCurrentIndex(aero_index)
    tabs.setCurrentIndex(aero_index)

    assert sum(1 for index in range(tabs.count()) if tabs.tabText(index) == "Aero") == 1
    assert (
        len(
            [
                widget
                for widget in window.findChildren(FakeWidget)
                if widget.objectName() == "VibeCADRibbonGroup_Aero"
            ]
        )
        == 1
    )
