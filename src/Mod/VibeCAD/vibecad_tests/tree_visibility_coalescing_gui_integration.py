# SPDX-License-Identifier: LGPL-2.1-or-later

"""Prove one browser-folder refresh for a burst of visibility changes."""

from __future__ import annotations

import sys
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets
from PySide6 import QtTest


def _first_populated_browser_folder(tree: QtWidgets.QTreeWidget):
    pending = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    retained = list(pending)
    while pending:
        parent = pending.pop()
        for index in range(parent.childCount()):
            child = parent.child(index)
            retained.append(child)
            if child.type() == 1002 and child.childCount() > 0:
                return child, retained
            pending.append(child)
    raise AssertionError("The model browser did not create a populated folder.")


def _instrumented_tree(main_window):
    tree_dock = main_window.findChild(QtWidgets.QDockWidget, "Std_TreeView")
    assert tree_dock is not None
    for tree in tree_dock.findChildren(QtWidgets.QTreeWidget):
        if tree.property("VibeCADBrowserFolderStatusUpdateCount") is not None:
            return tree
    raise AssertionError("The Tree does not expose its browser-folder refresh counter.")


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    tree_preferences = None
    original_organize_by_type = True
    original_status_timeout = 100

    def finish(code: int) -> None:
        if tree_preferences is not None:
            tree_preferences.SetBool("OrganizeModelByType", original_organize_by_type)
            tree_preferences.SetInt("StatusTimeout", original_status_timeout)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(code)

    try:
        tree_preferences = App.ParamGet("User parameter:BaseApp/Preferences/TreeView")
        original_organize_by_type = tree_preferences.GetBool(
            "OrganizeModelByType", True
        )
        original_status_timeout = tree_preferences.GetInt("StatusTimeout", 100)
        tree_preferences.SetBool("OrganizeModelByType", True)
        tree_preferences.SetInt("StatusTimeout", 25)
        document = App.newDocument("TreeVisibilityCoalescing")
        objects = [
            document.addObject("App::FeaturePython", f"VisibleObject{index:03d}")
            for index in range(153)
        ]
        document.recompute()
        assert set(App.listDocuments()) == {
            document.Name
        }, "The visibility coalescing test requires one isolated document."

        def exercise() -> None:
            try:
                tree = _instrumented_tree(Gui.getMainWindow())
                counter_name = "VibeCADBrowserFolderStatusUpdateCount"
                before = int(tree.property(counter_name))
                folder, retained_tree_items = _first_populated_browser_folder(tree)
                visible_brush_style = folder.foreground(0).style()

                def wait_for_one_refresh(previous: int, continuation) -> None:
                    deadline = time.monotonic() + 2.0
                    settled_since = None

                    def poll() -> None:
                        nonlocal settled_since
                        try:
                            count = int(tree.property(counter_name)) - previous
                            if count == 1:
                                settled_since = settled_since or time.monotonic()
                                if time.monotonic() - settled_since >= 0.1:
                                    continuation()
                                    return
                            else:
                                settled_since = None
                                assert count == 0, (
                                    "Expected one coalesced browser-folder refresh, "
                                    f"got {count}."
                                )
                            assert (
                                time.monotonic() < deadline
                            ), "The coalesced browser-folder refresh did not run."
                            QtCore.QTimer.singleShot(10, poll)
                        except Exception:
                            traceback.print_exc(file=sys.__stderr__)
                            finish(1)

                    poll()

                def restore_after_direct_change() -> None:
                    try:
                        assert retained_tree_items
                        assert all(not obj.ViewObject.Visibility for obj in objects)
                        assert (
                            folder.foreground(0).style() != visible_brush_style
                        ), "The coalesced refresh did not update the folder's hidden state."
                        restore_before = int(tree.property(counter_name))
                        for obj in objects:
                            obj.ViewObject.Visibility = True
                        assert int(tree.property(counter_name)) == restore_before
                        wait_for_one_refresh(restore_before, exercise_selection_hide)
                    except Exception:
                        traceback.print_exc(file=sys.__stderr__)
                        finish(1)

                def exercise_selection_hide() -> None:
                    try:
                        Gui.Selection.clearSelection()
                        for obj in objects:
                            Gui.Selection.addSelection(obj)
                        selection_before = int(tree.property(counter_name))
                        Gui.Selection.setVisible(False)
                        assert (
                            int(tree.property(counter_name)) == selection_before
                        ), "Std Hide traversed browser folders synchronously per selection."
                        Gui.Selection.clearSelection()
                        wait_for_one_refresh(
                            selection_before, restore_after_selection_hide
                        )
                    except Exception:
                        traceback.print_exc(file=sys.__stderr__)
                        finish(1)

                def restore_after_selection_hide() -> None:
                    try:
                        restore_before = int(tree.property(counter_name))
                        for obj in objects:
                            obj.ViewObject.Visibility = True
                        assert int(tree.property(counter_name)) == restore_before
                        wait_for_one_refresh(restore_before, exercise_folder_toggle)
                    except Exception:
                        traceback.print_exc(file=sys.__stderr__)
                        finish(1)

                def exercise_folder_toggle() -> None:
                    try:
                        current_folder, current_tree_items = (
                            _first_populated_browser_folder(tree)
                        )
                        assert current_tree_items
                        tree.clearSelection()
                        tree.setCurrentItem(current_folder)
                        current_folder.setSelected(True)
                        tree.setFocus()
                        folder_before = int(tree.property(counter_name))
                        QtTest.QTest.keyClick(tree, QtCore.Qt.Key_Space)
                        assert (
                            int(tree.property(counter_name)) == folder_before
                        ), "A folder visibility toggle traversed browser folders synchronously."
                        wait_for_one_refresh(folder_before, verify_complete)
                    except Exception:
                        traceback.print_exc(file=sys.__stderr__)
                        finish(1)

                def verify_complete() -> None:
                    try:
                        assert all(not obj.ViewObject.Visibility for obj in objects)
                        print("VIBECAD_TREE_VISIBILITY_COALESCING_OK", flush=True)
                        finish(0)
                    except Exception:
                        traceback.print_exc(file=sys.__stderr__)
                        finish(1)

                for obj in objects:
                    obj.ViewObject.Visibility = False
                assert (
                    int(tree.property(counter_name)) == before
                ), "A visibility notification traversed browser folders synchronously."
                wait_for_one_refresh(before, restore_after_direct_change)
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        QtCore.QTimer.singleShot(1000, exercise)
        QtCore.QTimer.singleShot(10000, lambda: finish(1))
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
