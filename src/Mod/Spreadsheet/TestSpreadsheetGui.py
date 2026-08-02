# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2021 Chris Hennes <chennes@pioneerlibrarysystem.org>    *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU General Public License (GPL)            *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful,            *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with FreeCAD; if not, write to the Free Software        *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# ***************************************************************************/

import unittest
import FreeCAD

from PySide import QtCore, QtGui
import FreeCADGui

from SpreadsheetTests.TestVibeCADRibbonTools import (
    TestVibeCADSpreadsheetRibbonTools,
)

# ----------------------------------------------------------------------------------
# define the functions to test the FreeCAD Spreadsheet GUI
# ----------------------------------------------------------------------------------


class SpreadsheetGuiCases(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument()
        self.sheet = self.doc.addObject("Spreadsheet::Sheet", "Spreadsheet")
        self.view_provider = self.sheet.ViewObject

    def getTableView(self):
        return self.view_provider.getView()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def injectSimpleData(self):
        """A utility function to initialize a blank sheet with some known data"""
        self.sheet.clearAll()
        self.sheet.set("A1", "1")
        self.sheet.set("A2", "2")
        self.sheet.set("A3", "3")
        self.sheet.set("A4", "4")
        self.sheet.set("B1", "5")
        self.sheet.set("B2", "6")
        self.sheet.set("B3", "7")
        self.sheet.set("B4", "8")
        self.sheet.set("C1", "9")
        self.sheet.set("C2", "10")
        self.sheet.set("C3", "11")
        self.sheet.set("C4", "12")
        self.sheet.set("D1", "13")
        self.sheet.set("D2", "14")
        self.sheet.set("D3", "15")
        self.sheet.set("D4", "16")
        self.doc.recompute()

    def testCopySingleCell(self):
        self.injectSimpleData()
        self.view_provider.doubleClicked()
        view = self.getTableView()
        sheet_widget = FreeCADGui.getMainWindow().findChild(
            QtGui.QLineEdit,
            "cellContent",
        )
        self.assertIsNotNone(sheet_widget)
        view.setCurrentIndex("A1")
        view.select(
            "A1",
            QtCore.QItemSelectionModel.ClearAndSelect.value,
        )
        QtGui.QApplication.setActiveWindow(FreeCADGui.getMainWindow())
        FreeCADGui.getMainWindow().activateWindow()
        FreeCADGui.getMainWindow().raise_()
        QtGui.QApplication.processEvents()
        sheet_widget.setFocus(QtCore.Qt.OtherFocusReason)
        self.assertIs(QtGui.QApplication.focusWidget(), sheet_widget)
        self.assertEqual(view.selectedRanges(), ["A1:A1"])
        self.assertTrue(FreeCADGui.isCommandActive("Std_Copy"))
        FreeCAD.Gui.runCommand("Std_Copy", 0)
        self.assertTrue(QtGui.QApplication.clipboard().mimeData().hasText())
        view.setCurrentIndex("E5")
        view.select(
            "E5",
            QtCore.QItemSelectionModel.ClearAndSelect.value,
        )
        FreeCADGui.updateGui()
        QtGui.QApplication.processEvents()
        self.assertEqual(view.selectedRanges(), ["E5:E5"])
        self.assertTrue(FreeCADGui.isCommandActive("Std_Paste"))
        undo_before = self.doc.UndoCount
        FreeCAD.Gui.runCommand("Std_Paste", 0)
        self.assertEqual(self.doc.UndoCount, undo_before + 1)
        self.doc.recompute()
        self.assertEqual(self.sheet.get("A1"), self.sheet.get("E5"))
