# SPDX-License-Identifier: LGPL-2.1-or-later

"""Show 3D Print on VibeCAD's ribbon when the native C++ domain is missing.

VibeCAD hides the classic workbench combo. A Python workbench plus a
Preferences page is not enough on an older app build: the 3D Print ribbon
tab is compiled into VibeCADRibbon.cpp. This installer adds that tab, the
print dock, and a Tools menu until a rebuilt app already owns the domain.

The module name is unique on purpose. McMasterInsert also ships InstallUI.py;
a bare `import InstallUI` from InitGui.py would reuse McMaster's cached module
and never add the 3D Print tab.
"""

from __future__ import annotations

from typing import Any

import PrintCommandLoader
import PrintIcons


RIBBON_TABS = "VibeCADRibbonTabs"
RIBBON_PAGE = "VibeCADRibbonPage"
TAB_LABEL = "3D Print"
TAB_DATA = "VibeCADPrintWorkbench"
GROUP_NAME = "VibeCADRibbonGroup_Print"
RIBBON_GROUP_VERSION = 1
MENU_NAME = "3D Print"
# True only when this process inserted the tab. Native C++ builds already own
# the 3D Print domain; overlay hooks must not hide those ribbon groups.
_injected_print_tab = False
BUTTONS = (
    ("Print", "VibeCADPrint_OpenInPrusaSlicer", "open"),
    ("Export 3MF", "VibeCADPrint_Save3MF", "save"),
    ("Setup", "VibeCADPrint_Setup", "setup"),
)


def _log(message: str) -> None:
    try:
        import FreeCAD as App

        App.Console.PrintMessage(f"3D Print: {message}\n")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        import FreeCAD as App

        App.Console.PrintWarning(f"3D Print: {message}\n")
    except Exception:
        pass


def _qt():
    from PySide import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def _gui():
    import FreeCADGui as Gui

    return Gui


def _run_command(gui: Any, command_id: str):
    def _run(_checked=False) -> None:
        runner = getattr(gui, "runCommand", None)
        if callable(runner):
            try:
                runner(command_id, 0)
            except TypeError:
                runner(command_id)

    return _run


def _tab_index(tabs: Any, *, label: str = "", data: str = "") -> int:
    for index in range(tabs.count()):
        if label and tabs.tabText(index) == label:
            return index
        if data and str(tabs.tabData(index) or "") == data:
            return index
    return -1


def _insert_index(tabs: Any) -> int:
    aero = _tab_index(tabs, label="Aero")
    if aero < 0:
        aero = _tab_index(tabs, data="VibeCADAeroWorkbench")
    if aero >= 0:
        return aero + 1
    return tabs.count()


def _is_print_tab(tabs: Any, index: int) -> bool:
    if index < 0 or index >= tabs.count():
        return False
    if tabs.tabText(index) == TAB_LABEL:
        return True
    return str(tabs.tabData(index) or "") in {TAB_DATA, "print"}


def _find_tabs(mw: Any, qt_widgets: Any):
    tabs = mw.findChild(getattr(qt_widgets, "QTabBar", None), RIBBON_TABS)
    if tabs is None:
        tabs = mw.findChild(object, RIBBON_TABS)
    return tabs


def install_ribbon_tab(gui: Any, qt_widgets: Any) -> bool:
    mw = gui.getMainWindow()
    if mw is None:
        return False
    tabs = _find_tabs(mw, qt_widgets)
    if tabs is None:
        return False
    if _tab_index(tabs, label=TAB_LABEL) >= 0 or _tab_index(tabs, data=TAB_DATA) >= 0:
        return True
    insert_at = _insert_index(tabs)
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
    global _injected_print_tab
    _injected_print_tab = True
    _log("ribbon tab installed")
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
    added = False
    for label, command_id, _icon in BUTTONS:
        if label in have:
            continue
        action = menu.addAction(label)
        action.triggered.connect(_run_command(gui, command_id))
        added = True
    if added:
        _log("menu installed")
    return True


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


def _ribbon_page(gui: Any, qt_widgets: Any):
    mw = gui.getMainWindow()
    if mw is None:
        return None
    page = mw.findChild(getattr(qt_widgets, "QWidget", object), RIBBON_PAGE)
    return page or mw.findChild(object, RIBBON_PAGE)


def _ribbon_group_version(group: Any) -> int:
    try:
        return int(group.property("PrintRibbonVersion") or 0)
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
        ):
            reusable = group
        else:
            _discard_ribbon_group(page, group)
    if reusable is not None:
        reusable.show()
        return True

    from PySide import QtCore as qt_core

    frame_type = getattr(qt_widgets, "QFrame", None) or qt_widgets.QWidget
    group = frame_type(page)
    group.setObjectName(GROUP_NAME)
    group.setProperty("ribbonGroup", True)
    group.setProperty("PrintRibbonVersion", RIBBON_GROUP_VERSION)
    layout = qt_widgets.QHBoxLayout(group)
    layout.setContentsMargins(6, 4, 10, 4)
    layout.setSpacing(4)
    style = getattr(qt_core.Qt, "ToolButtonTextUnderIcon", None)
    icon_size = qt_core.QSize(24, 24)
    for label, command_id, icon_name in BUTTONS:
        button = qt_widgets.QToolButton(group)
        button.setText(label)
        try:
            icon = qt_gui.QIcon(PrintIcons.icon_path(icon_name))
            button.setIcon(icon)
        except Exception:
            pass
        button.setIconSize(icon_size)
        if style is not None:
            button.setToolButtonStyle(style)
        button.setAutoRaise(True)
        button.setToolTip(command_id)
        button.setProperty("VibeCADCommandId", command_id)
        button.setMinimumSize(48, 48)
        button.clicked.connect(_run_command(gui, command_id))
        layout.addWidget(button)
    page_layout = page.layout()
    if page_layout is not None:
        if hasattr(page_layout, "insertWidget"):
            page_layout.insertWidget(0, group)
        elif hasattr(page_layout, "addWidget"):
            page_layout.addWidget(group)
    group.show()
    _log("ribbon group installed")
    return True


def _set_print_page_active(gui: Any, qt_widgets: Any, qt_gui: Any, active: bool) -> None:
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
        try:
            import PrintPanel

            PrintPanel.show_panel(refresh=True)
        except Exception as exc:
            _warn(f"could not show print panel: {exc}")
        return
    for group in _iter_ribbon_groups(page):
        name = str(group.objectName() or "")
        if name == GROUP_NAME:
            group.hide()
    try:
        import PrintPanel

        PrintPanel.hide_panel()
    except Exception:
        pass


def _on_tab_changed(index: int, tabs: Any, gui: Any, qt_widgets: Any, qt_gui: Any, qt_core: Any) -> None:
    printing = _is_print_tab(tabs, index)
    if printing:
        try:
            gui.activateWorkbench(TAB_DATA)
        except Exception:
            pass

    def _apply() -> None:
        _set_print_page_active(gui, qt_widgets, qt_gui, printing)

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
    tabs = _find_tabs(mw, qt_widgets)
    if tabs is None:
        return False
    if not _injected_print_tab:
        return True
    if not getattr(tabs, "_vibecadPrintTabHooked", False):
        tabs.currentChanged.connect(
            lambda i, bar=tabs: _on_tab_changed(i, bar, gui, qt_widgets, qt_gui, qt_core)
        )
        try:
            setattr(tabs, "_vibecadPrintTabHooked", True)
        except Exception:
            pass
        _log("ribbon tab change hooked")
    if _is_print_tab(tabs, tabs.currentIndex()):
        _on_tab_changed(tabs.currentIndex(), tabs, gui, qt_widgets, qt_gui, qt_core)
    else:
        _set_print_page_active(gui, qt_widgets, qt_gui, False)
    return True


def install_once() -> dict[str, bool]:
    gui = _gui()
    qt_core, qt_gui, qt_widgets = _qt()
    try:
        PrintCommandLoader.ensure_commands_registered(gui)
    except Exception as exc:
        _warn(f"command registration deferred: {exc}")
    try:
        import PrintPanel

        PrintPanel.ensure_panel_registered()
    except Exception as exc:
        _warn(f"print panel not ready: {exc}")
    return {
        "menu": install_menu(gui, qt_widgets),
        "ribbon_tab": install_ribbon_tab(gui, qt_widgets),
        "ribbon_hook": hook_ribbon_tab_changes(gui, qt_widgets, qt_gui, qt_core),
    }


_retry_count = 0
_timer = None


def install_with_retry(max_tries: int = 40, interval_ms: int = 500) -> None:
    """Keep trying until VibeCAD's native ribbon and main window exist."""

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
            _log("UI ready")
            return
        if _retry_count >= max_tries:
            if _timer is not None:
                _timer.stop()
            _warn("could not fully hook VibeCAD's ribbon. Use menu 3D Print → Setup.")

    if _timer is None:
        _timer = QtCore.QTimer()
        _timer.setInterval(interval_ms)
        _timer.timeout.connect(_tick)
    _retry_count = 0
    _tick()
    if _retry_count < max_tries:
        _timer.start()
