# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exercise nested browser icons after an unrelated incremental rebuild.

Run inside the GUI executable (not ordinary pytest). Uses a temporary document.
"""

import sys
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import ObjectsFem
from PySide import QtCore, QtWidgets


def _run():
    document = App.newDocument("TreeIconRegression")
    timer = QtCore.QTimer()
    started = time.monotonic()
    phase = 0
    settled = None

    def finish(ok):
        timer.stop()
        App.closeDocument(document.Name)
        print("VIBECAD_TREE_ICONS_GUI_" + ("OK" if ok else "FAILED"), flush=True)
        QtWidgets.QApplication.instance().exit(0 if ok else 1)

    def rows():
        found = {}
        for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
            if not tree.isVisible():
                continue
            iterator = QtWidgets.QTreeWidgetItemIterator(tree)
            while iterator.value():
                item = iterator.value()
                if item.text(0) in labels:
                    # Read immediately: never retain Qt items across event turns.
                    found[item.text(0)] = not item.icon(0).isNull()
                iterator += 1
        return found

    def tick():
        nonlocal phase, settled
        try:
            assert time.monotonic() - started < 30, "Tree did not settle"
            if any((document.Restoring, document.Recomputing, document.RecomputePending,
                    document.CooperativeMutationActive, document.PresentationUpdateActive)):
                settled = None
                return
            if settled is None:
                settled = time.monotonic()
                return
            if time.monotonic() - settled < 1:
                return
            current = rows()
            if len(current) != len(labels):
                return
            if phase == 0:
                # Force status for the initial population; the regression is
                # rebuilding previously correct nested rows for an unrelated edit.
                for obj in members:
                    obj.ViewObject.signalChangeIcon()
                phase = 1
                settled = None
                return
            if phase == 1:
                assert all(current.values()), ("Initial icons", current)
                document.addObject("App::DocumentObjectGroup", "IconRefreshTrigger")
                phase = 2
                settled = None
                return
            assert all(current.values()), ("Rebuilt nested icons", current)
            assert support.ViewObject.ToggleVisibility == "CanToggleVisibility"
            assert solver.ViewObject.ToggleVisibility == "NoToggleVisibility"
            finish(True)
        except Exception:
            traceback.print_exc(file=sys.__stderr__)
            finish(False)

    try:
        Gui.activateWorkbench("FemWorkbench")
        study = ObjectsFem.makeAnalysis(document)
        support = ObjectsFem.makeConstraintFixed(document)
        force = ObjectsFem.makeConstraintForce(document)
        solver = ObjectsFem.makeSolverCalculiX(document)
        members = (support, force, solver)
        for obj in members:
            study.addObject(obj)
        labels = {obj.Label for obj in members}
        timer.timeout.connect(tick)
        timer.start(50)
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        finish(False)


QtCore.QTimer.singleShot(1000, _run)
