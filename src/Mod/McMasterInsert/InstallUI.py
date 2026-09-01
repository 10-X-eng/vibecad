# SPDX-License-Identifier: LGPL-2.1-or-later
"""Make McMaster commands visible in VibeCAD (ribbon, toolbar, and Tools menu).

VibeCAD hides the classic workbench combo. A Python workbench alone is
invisible. This installer puts Catalog / Import on:
- a permanent main-window toolbar
- Tools → McMaster-Carr
- a McMaster ribbon tab, retried until the native ribbon exists
- a button group on the live ribbon page so it is visible on Model too
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ICON = str(Path(__file__).resolve().parent / "icons" / "mcmaster-workbench.svg")
TOOLBAR_NAME = "McMaster-Carr"
MENU_NAME = "McMaster-Carr"
GROUP_NAME = "VibeCADRibbonGroup_McMaster"
RIBBON_TABS = "VibeCADRibbonTabs"
RIBBON_PAGE = "VibeCADRibbonPage"
TAB_LABEL = "McMaster"
TAB_DATA = "McMasterWorkbench"
RIBBON_GROUP_VERSION = 7
ICON_DIR = Path(__file__).resolve().parent / "icons"
BUTTONS = (
    ("Catalog", "McMaster_BrowseCatalog", "catalog.svg"),
    ("Import", "McMaster_ImportFile", "import.svg"),
)


def _warn(message: str) -> None:
    try:
        import FreeCAD as App

        App.Console.PrintWarning(f"McMaster-Carr: {message}\n")
    except Exception:
        pass


def _qt():
    from PySide import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def _gui():
    import FreeCADGui as Gui

    return Gui


def _icon(qt_gui: Any, filename: str = "mcmaster-workbench.svg"):
    icon_type = getattr(qt_gui, "QIcon", None)
    if icon_type is None:
        return None
    try:
        return icon_type(str(ICON_DIR / filename))
    except Exception:
        return None


def _run_command(gui: Any, command_id: str):
    def _run(_checked=False) -> None:
        runner = getattr(gui, "runCommand", None)
        if callable(runner):
            try:
                runner(command_id, 0)
            except TypeError:
                runner(command_id)

    return _run


def install_toolbar(gui: Any, qt_widgets: Any, qt_gui: Any) -> bool:
    mw = gui.getMainWindow()
    if mw is None:
        return False
    existing = mw.findChild(qt_widgets.QToolBar, "McMasterCarrToolbar")
    if existing is not None:
        existing.show()
        return True
    bar = mw.addToolBar(TOOLBAR_NAME)
    bar.setObjectName("McMasterCarrToolbar")
    bar.setMovable(True)
    for label, command_id, icon_name in BUTTONS:
        action = bar.addAction(label)
        icon = _icon(qt_gui, icon_name)
        if icon is not None:
            action.setIcon(icon)
        action.setToolTip(command_id)
        action.triggered.connect(_run_command(gui, command_id))
    bar.show()
    return True


def install_menu(gui: Any, qt_widgets: Any) -> bool:
    mw = gui.getMainWindow()
    if mw is None:
        return False
    bar = mw.menuBar()
    menu = None
    for action in bar.actions():
        title = (action.text() or "").replace("&", "")
        if title == MENU_NAME:
            menu = action.menu()
            break
    if menu is None:
        menu = bar.addMenu(MENU_NAME)
    have = {(action.text() or "") for action in menu.actions()}
    for label, command_id in (
        ("Browse Catalog", "McMaster_BrowseCatalog"),
        ("Import CAD File", "McMaster_ImportFile"),
        ("Open Cache Folder", "McMaster_OpenCache"),
    ):
        if label in have:
            continue
        action = menu.addAction(label)
        action.triggered.connect(_run_command(gui, command_id))
    return True


def _tab_index(tabs: Any, label: str) -> int:
    for index in range(tabs.count()):
        if tabs.tabText(index) == label:
            return index
    return -1


def install_ribbon_tab(gui: Any, qt_widgets: Any) -> bool:
    mw = gui.getMainWindow()
    if mw is None:
        return False
    tabs = mw.findChild(qt_widgets.QTabBar, RIBBON_TABS)
    if tabs is None:
        return False
    if _tab_index(tabs, TAB_LABEL) >= 0:
        return True
    insert_at = tabs.count()
    blocker = getattr(tabs, "blockSignals", None)
    if callable(blocker):
        blocker(True)
    try:
        tabs.insertTab(insert_at, TAB_LABEL)
        if hasattr(tabs, "setTabData"):
            tabs.setTabData(insert_at, TAB_DATA)
    finally:
        if callable(blocker):
            blocker(False)
    return True


def _is_mcmaster_tab(tabs: Any, index: int) -> bool:
    if index < 0 or index >= tabs.count():
        return False
    if tabs.tabText(index) == TAB_LABEL:
        return True
    return str(tabs.tabData(index) or "") in {TAB_DATA, "mcmaster"}


def _iter_ribbon_groups(root: Any) -> list[Any]:
    groups = []
    find_children = getattr(root, "findChildren", None)
    if find_children is None:
        return groups
    try:
        children = find_children(object)
    except TypeError:
        children = find_children(None)
    except Exception:
        children = []
    for child in children or []:
        name = str(getattr(child, "objectName", lambda: "")() or "")
        if name.startswith("VibeCADRibbonGroup_"):
            groups.append(child)
    return groups


def _remove_overlay_strip(gui: Any, qt_widgets: Any) -> None:
    mw = gui.getMainWindow()
    if mw is None:
        return
    for name in ("McMasterRibbonStrip", "McMasterCarrToolbar"):
        widget = mw.findChild(qt_widgets.QWidget, name)
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def _ribbon_page(gui: Any, qt_widgets: Any):
    mw = gui.getMainWindow()
    if mw is None:
        return None
    return mw.findChild(qt_widgets.QWidget, RIBBON_PAGE)


def _set_mcmaster_page_active(gui: Any, qt_widgets: Any, qt_gui: Any, active: bool) -> None:
    page = _ribbon_page(gui, qt_widgets)
    if page is None:
        return
    if active:
        install_ribbon_group(gui, qt_widgets, qt_gui, page)
        for group in _iter_ribbon_groups(page):
            name = str(group.objectName() or "")
            if name == GROUP_NAME:
                group.show()
            else:
                group.hide()
    else:
        for group in _iter_ribbon_groups(page):
            name = str(group.objectName() or "")
            if name == GROUP_NAME:
                group.hide()


def _ribbon_group_version(group: Any) -> int:
    try:
        return int(group.property("McMasterRibbonVersion") or 0)
    except Exception:
        return 0


def _discard_ribbon_group(page: Any, group: Any) -> None:
    try:
        group.setObjectName(GROUP_NAME + "_old")
    except Exception:
        pass
    layout = getattr(page, "layout", lambda: None)()
    if layout is not None and hasattr(layout, "removeWidget"):
        try:
            layout.removeWidget(group)
        except Exception:
            pass
    try:
        group.hide()
        group.setParent(None)
        group.deleteLater()
    except Exception:
        pass


def install_ribbon_group(gui: Any, qt_widgets: Any, qt_gui: Any, page: Any) -> bool:
    """Match native VibeCAD groups: transparent strip, icon-over-label commands."""
    leftovers = [
        group
        for group in _iter_ribbon_groups(page)
        if str(group.objectName() or "").startswith(GROUP_NAME)
    ]
    reusable = None
    for group in leftovers:
        if (
            group.objectName() == GROUP_NAME
            and _ribbon_group_version(group) >= RIBBON_GROUP_VERSION
            and not (group.styleSheet() or "").strip()
        ):
            reusable = group
        else:
            _discard_ribbon_group(page, group)
    if reusable is not None:
        reusable.show()
        return True

    from PySide import QtCore as _qc

    frame_type = getattr(qt_widgets, "QFrame", None) or qt_widgets.QWidget
    group = frame_type(page)
    group.setObjectName(GROUP_NAME)
    group.setProperty("ribbonGroup", True)
    group.setProperty("McMasterRibbonVersion", RIBBON_GROUP_VERSION)
    group.setStyleSheet("")
    layout = qt_widgets.QHBoxLayout(group)
    layout.setContentsMargins(6, 4, 10, 4)
    layout.setSpacing(4)
    style = getattr(_qc.Qt, "ToolButtonTextUnderIcon", None)
    if style is None:
        style = getattr(getattr(qt_widgets, "Qt", None), "ToolButtonTextUnderIcon", None)
    icon_size = _qc.QSize(24, 24)
    tooltips = {
        "McMaster_BrowseCatalog": "Browse McMaster-Carr and insert 3-D STEP",
        "McMaster_ImportFile": "Import a STEP file you already downloaded",
        "McMaster_OpenCache": "Open the local McMaster CAD cache",
    }
    for label, command_id, icon_name in BUTTONS:
        button = qt_widgets.QToolButton(group)
        button.setText(label)
        icon = _icon(qt_gui, icon_name)
        if icon is not None:
            button.setIcon(icon)
        button.setIconSize(icon_size)
        if style is not None:
            button.setToolButtonStyle(style)
        button.setAutoRaise(True)
        button.setToolTip(tooltips.get(command_id, command_id))
        button.setProperty("VibeCADCommandId", command_id)
        button.setMinimumSize(48, 48)
        button.clicked.connect(_run_command(gui, command_id))
        layout.addWidget(button)
    try:
        group.setSizePolicy(
            qt_widgets.QSizePolicy.Maximum,
            qt_widgets.QSizePolicy.Preferred,
        )
    except Exception:
        pass

    page_layout = page.layout()
    if page_layout is not None:
        if hasattr(page_layout, "insertWidget"):
            page_layout.insertWidget(0, group)
        elif hasattr(page_layout, "addWidget"):
            page_layout.addWidget(group)
        try:
            page_layout.setAlignment(group, getattr(_qc.Qt, "AlignLeft"))
        except Exception:
            pass
    group.show()
    return True


def _on_tab_changed(index: int, tabs: Any, gui: Any, qt_widgets: Any, qt_gui: Any, qt_core: Any) -> None:
    mcmaster = _is_mcmaster_tab(tabs, index)
    if mcmaster:
        try:
            gui.activateWorkbench("McMasterWorkbench")
        except Exception:
            pass

    def _apply() -> None:
        _set_mcmaster_page_active(gui, qt_widgets, qt_gui, mcmaster)

    timer = getattr(qt_core, "QTimer", None)
    if timer is not None and hasattr(timer, "singleShot"):
        timer.singleShot(0, _apply)
        timer.singleShot(80, _apply)
        timer.singleShot(250, _apply)
    else:
        _apply()


def hook_ribbon_tab_changes(gui: Any, qt_widgets: Any, qt_gui: Any, qt_core: Any) -> bool:
    mw = gui.getMainWindow()
    if mw is None:
        return False
    tabs = mw.findChild(qt_widgets.QTabBar, RIBBON_TABS)
    if tabs is None:
        return False
    _remove_overlay_strip(gui, qt_widgets)
    if not getattr(tabs, "_mcmasterTabHooked", False):
        tabs.currentChanged.connect(
            lambda i, bar=tabs: _on_tab_changed(i, bar, gui, qt_widgets, qt_gui, qt_core)
        )
        try:
            setattr(tabs, "_mcmasterTabHooked", True)
        except Exception:
            pass
    if _is_mcmaster_tab(tabs, tabs.currentIndex()):
        _on_tab_changed(tabs.currentIndex(), tabs, gui, qt_widgets, qt_gui, qt_core)
    else:
        _set_mcmaster_page_active(gui, qt_widgets, qt_gui, False)
    return True


def install_once() -> dict[str, bool]:
    from Commands import register

    gui = _gui()
    QtCore, QtGui, QtWidgets = _qt()
    register(gui)
    _remove_overlay_strip(gui, QtWidgets)
    return {
        "menu": install_menu(gui, QtWidgets),
        "ribbon_tab": install_ribbon_tab(gui, QtWidgets),
        "ribbon_hook": hook_ribbon_tab_changes(gui, QtWidgets, QtGui, QtCore),
    }


_retry_count = 0
_timer = None


def install_with_retry(max_tries: int = 40, interval_ms: int = 500) -> None:
    """Keep trying until VibeCAD's native ribbon/main window exists."""

    global _retry_count, _timer
    from PySide import QtCore

    def _tick() -> None:
        global _retry_count, _timer
        _retry_count += 1
        try:
            result = install_once()
        except Exception as exc:
            _warn(f"install attempt {_retry_count} failed: {exc}")
            result = {}
        if result.get("ribbon_hook") and result.get("ribbon_tab") and result.get("menu"):
            if _timer is not None:
                _timer.stop()
            return
        if _retry_count >= max_tries:
            if _timer is not None:
                _timer.stop()
            _warn(
                "could not fully hook VibeCAD's ribbon. "
                "Use menu McMaster-Carr → Browse Catalog, or run Macro InsertMcMaster."
            )

    if _timer is None:
        _timer = QtCore.QTimer()
        _timer.setInterval(interval_ms)
        _timer.timeout.connect(_tick)
    _retry_count = 0
    _tick()
    if _retry_count < max_tries:
        _timer.start()
