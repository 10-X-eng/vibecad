# SPDX-License-Identifier: LGPL-2.1-or-later

"""Add Apply / Reject Native preview buttons to the live ribbon without a C++ rebuild."""

from __future__ import annotations

from typing import Any

GROUP_OBJECT_NAME = "VibeCADRibbonGroup_NativePreview"
RIBBON_PAGE_OBJECT_NAME = "VibeCADRibbonPage"
RIBBON_GROUP_PREFIX = "VibeCADRibbonGroup_"
PREVIEW_BUTTONS = (
    ("Apply preview", "VibeCAD_ApplyNativePreview"),
    ("Reject preview", "VibeCAD_RejectNativePreview"),
)


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


def _append_preview_group(
    page: Any,
    qt_widgets: Any,
    gui: Any,
) -> Any:
    existing = [
        group
        for group in _iter_ribbon_groups(page)
        if group.objectName() == GROUP_OBJECT_NAME
    ]
    if existing:
        return existing[0]

    frame_type = getattr(qt_widgets, "QFrame", None) or qt_widgets.QWidget
    group = frame_type(page) if page is not None else frame_type()
    group.setObjectName(GROUP_OBJECT_NAME)
    layout_type = getattr(qt_widgets, "QHBoxLayout", None)
    if layout_type is not None:
        try:
            layout = layout_type(group)
        except TypeError:
            layout = layout_type()
            set_layout = getattr(group, "setLayout", None)
            if callable(set_layout):
                set_layout(layout)
    else:
        layout = getattr(group, "layout", lambda: None)()

    for label, command_id in PREVIEW_BUTTONS:
        button = qt_widgets.QToolButton(group)
        button.setText(label)
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


def _ribbon_page(main_window: Any, qt_widgets: Any) -> Any | None:
    if main_window is None:
        return None
    finder = getattr(main_window, "findChild", None)
    if not callable(finder):
        return None
    try:
        page = finder(qt_widgets.QWidget, RIBBON_PAGE_OBJECT_NAME)
    except TypeError:
        page = finder(RIBBON_PAGE_OBJECT_NAME)
    except Exception:
        return None
    if page is main_window:
        return None
    return page


def install_native_preview_ribbon(
    *,
    gui: Any | None = None,
    qt_widgets: Any | None = None,
    qt_core: Any | None = None,
    remaining_attempts: int = 40,
) -> bool:
    """Put Apply/Reject on VibeCADRibbonPage. Never patch QMainWindow."""

    if gui is None:
        import FreeCADGui as gui  # type: ignore[no-redef]
    if qt_widgets is None:
        from PySide import QtWidgets as qt_widgets  # type: ignore[no-redef]
    if qt_core is None and remaining_attempts > 0:
        try:
            from PySide import QtCore as qt_core  # type: ignore[no-redef]
        except Exception:
            qt_core = None

    from VibeCADNativePreviewCommands import register_preview_commands

    register_preview_commands(gui)
    main_window = gui.getMainWindow() if gui is not None else None
    page = _ribbon_page(main_window, qt_widgets)
    if page is None:
        if remaining_attempts <= 0:
            return False
        timer = getattr(qt_core, "QTimer", None)
        if timer is None or not hasattr(timer, "singleShot"):
            return False
        timer.singleShot(
            250,
            lambda: install_native_preview_ribbon(
                gui=gui,
                qt_widgets=qt_widgets,
                qt_core=qt_core,
                remaining_attempts=remaining_attempts - 1,
            ),
        )
        return False
    _append_preview_group(page, qt_widgets, gui)
    return True
