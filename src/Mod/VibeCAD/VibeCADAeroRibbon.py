# SPDX-License-Identifier: LGPL-2.1-or-later

"""Install the Aero ribbon tab from VibeCAD startup without replacing Model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

AERO_TAB_LABEL = "Aero"
AERO_GROUP_OBJECT_NAME = "VibeCADRibbonGroup_Aero"
AERO_TAB_DATA = "VibeCADAeroWorkbench"
PARAMETERS_TAB_LABEL = "Parameters"
RIBBON_TABS_OBJECT_NAME = "VibeCADRibbonTabs"
RIBBON_PAGE_OBJECT_NAME = "VibeCADRibbonPage"
RIBBON_GROUP_PREFIX = "VibeCADRibbonGroup_"
AERO_BUTTONS = (
    ("Analyze", "VibeCADAero_Analyze"),
    ("Section", "VibeCADAero_Section"),
    ("3D VLM", "VibeCADAero_VLM"),
    ("JSBSim", "VibeCADAero_ExportJSBSim"),
)
_INSTALLED_PROPERTY = "_vibecadAeroRibbonInstalled"


def _analyze_icon_path() -> str:
    try:
        import AeroIcons

        return AeroIcons.aero_icon_path()
    except Exception:
        return str(
            Path(__file__).resolve().parent.parent
            / "VibeCADAero"
            / "icons"
            / "vibecad-aero-analyze.svg"
        )


def _ensure_aero_commands(gui: Any | None = None) -> None:
    import sys

    if gui is None:
        import FreeCADGui as gui  # type: ignore[no-redef]
    aero_dir = Path(__file__).resolve().parent.parent / "VibeCADAero"
    if not aero_dir.is_dir():
        raise RuntimeError(f"VibeCAD Aero module directory is missing: {aero_dir}")
    if str(aero_dir) not in sys.path:
        sys.path.insert(0, str(aero_dir))
    import AeroCommandLoader

    AeroCommandLoader.ensure_commands_registered(gui=gui)


def _tab_index(tabs: Any, label: str) -> int:
    for index in range(tabs.count()):
        if tabs.tabText(index) == label:
            return index
    return -1


def _is_aero_tab(tabs: Any, index: int) -> bool:
    if index < 0 or index >= tabs.count():
        return False
    if tabs.tabText(index) == AERO_TAB_LABEL:
        return True
    data = str(tabs.tabData(index) or "")
    return data in {AERO_TAB_DATA, "aero"}


def _iter_ribbon_groups(root: Any) -> list[Any]:
    groups: list[Any] = []
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
        if name.startswith(RIBBON_GROUP_PREFIX):
            groups.append(child)
    return groups


def _unhide_stock_groups(root: Any) -> None:
    for group in _iter_ribbon_groups(root):
        show = getattr(group, "show", None)
        if callable(show):
            show()


def _append_aero_group(
    page: Any,
    qt_widgets: Any,
    qt_gui: Any,
    gui: Any,
) -> Any:
    existing = [
        group
        for group in _iter_ribbon_groups(page)
        if group.objectName() == AERO_GROUP_OBJECT_NAME
    ]
    if existing:
        return existing[0]

    frame_type = getattr(qt_widgets, "QFrame", None) or qt_widgets.QWidget
    group = frame_type(page) if page is not None else frame_type()
    group.setObjectName(AERO_GROUP_OBJECT_NAME)
    layout_type = getattr(qt_widgets, "QHBoxLayout", None)
    if layout_type is not None:
        try:
            layout = layout_type(group)
        except TypeError:
            layout = layout_type()
            add_to_group = getattr(group, "setLayout", None)
            if callable(add_to_group):
                add_to_group(layout)
    else:
        layout = getattr(group, "layout", lambda: None)()

    icon_type = getattr(qt_gui, "QIcon", None)
    analyze_icon = None
    if icon_type is not None:
        try:
            analyze_icon = icon_type(_analyze_icon_path())
        except Exception:
            analyze_icon = _analyze_icon_path()

    style = getattr(getattr(qt_widgets, "Qt", None), "ToolButtonTextUnderIcon", None)
    if style is None:
        qt_core_qt = getattr(qt_gui, "Qt", None)
        style = getattr(qt_core_qt, "ToolButtonTextUnderIcon", None)

    for label, command_id in AERO_BUTTONS:
        button = qt_widgets.QToolButton(group)
        button.setText(label)
        if label == "Analyze" and analyze_icon is not None:
            set_icon = getattr(button, "setIcon", None)
            if callable(set_icon):
                set_icon(analyze_icon)
        set_style = getattr(button, "setToolButtonStyle", None)
        if callable(set_style) and style is not None:
            set_style(style)
        set_raise = getattr(button, "setAutoRaise", None)
        if callable(set_raise):
            set_raise(True)
        set_tip = getattr(button, "setToolTip", None)
        if callable(set_tip):
            set_tip(command_id)

        def _run(_checked=False, command=command_id) -> None:
            runner = getattr(gui, "runCommand", None)
            if callable(runner):
                runner(command)

        clicked = getattr(button, "clicked", None)
        if clicked is not None and hasattr(clicked, "connect"):
            clicked.connect(_run)
        if layout is not None and hasattr(layout, "addWidget"):
            layout.addWidget(button)

    page_layout = getattr(page, "layout", lambda: None)()
    if page_layout is not None and hasattr(page_layout, "insertWidget"):
        page_layout.insertWidget(0, group)
    elif page_layout is not None and hasattr(page_layout, "addWidget"):
        page_layout.addWidget(group)
    show = getattr(group, "show", None)
    if callable(show):
        show()
    return group


def _apply_aero_page(gui: Any, qt_widgets: Any, qt_gui: Any) -> None:
    main_window = gui.getMainWindow()
    if main_window is None:
        return
    _unhide_stock_groups(main_window)
    page = main_window.findChild(qt_widgets.QWidget, RIBBON_PAGE_OBJECT_NAME)
    if page is None:
        page = main_window
    _append_aero_group(page, qt_widgets, qt_gui, gui)
    _unhide_stock_groups(main_window)


def _on_tab_changed(
    index: int,
    tabs: Any,
    gui: Any,
    qt_widgets: Any,
    qt_gui: Any,
    qt_core: Any,
) -> None:
    if not _is_aero_tab(tabs, index):
        return
    apply = lambda: _apply_aero_page(gui, qt_widgets, qt_gui)
    timer = getattr(qt_core, "QTimer", None)
    if timer is not None and hasattr(timer, "singleShot"):
        timer.singleShot(0, apply)
    else:
        apply()


def install_aero_ribbon_tab(
    *,
    gui: Any | None = None,
    qt_widgets: Any | None = None,
    qt_gui: Any | None = None,
    qt_core: Any | None = None,
) -> bool:
    """Insert Aero after Parameters and keep Model groups visible when it is selected.

    Does not activate VibeCADAeroWorkbench and does not change the current tab.
    """

    if gui is None:
        import FreeCADGui as gui  # type: ignore[no-redef]
    if qt_widgets is None or qt_core is None:
        from PySide import QtCore as _qt_core
        from PySide import QtGui as _qt_gui
        from PySide import QtWidgets as _qt_widgets

        qt_widgets = qt_widgets or _qt_widgets
        qt_gui = qt_gui or _qt_gui
        qt_core = qt_core or _qt_core
    if qt_gui is None:
        try:
            from PySide import QtGui as _qt_gui

            qt_gui = _qt_gui
        except Exception:
            qt_gui = SimpleNamespaceMissing()

    main_window = gui.getMainWindow() if gui is not None else None
    if main_window is None:
        return False
    tabs = main_window.findChild(qt_widgets.QTabBar, RIBBON_TABS_OBJECT_NAME)
    if tabs is None:
        return False

    _ensure_aero_commands(gui)

    already = getattr(tabs, _INSTALLED_PROPERTY, False)
    aero_index = _tab_index(tabs, AERO_TAB_LABEL)
    if aero_index < 0:
        parameters_index = _tab_index(tabs, PARAMETERS_TAB_LABEL)
        insert_at = parameters_index + 1 if parameters_index >= 0 else tabs.count()
        blocker = getattr(tabs, "blockSignals", None)
        if callable(blocker):
            blocker(True)
        try:
            tabs.insertTab(insert_at, AERO_TAB_LABEL)
            if hasattr(tabs, "setTabData"):
                tabs.setTabData(insert_at, AERO_TAB_DATA)
        finally:
            if callable(blocker):
                blocker(False)

    if not already:
        tabs.currentChanged.connect(
            lambda index, bar=tabs: _on_tab_changed(
                index, bar, gui, qt_widgets, qt_gui, qt_core
            )
        )
        try:
            setattr(tabs, _INSTALLED_PROPERTY, True)
        except Exception:
            pass
    return True


class SimpleNamespaceMissing:
    QIcon = None
    Qt = None
