# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact document, object, and transaction contracts for Python FEM tasks."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from femtaskpanels import base_femtaskpanel
from femviewprovider import view_base_femobject


_COMMAND_NAME = "VibeCAD_TestFemPythonTaskBoundary"


class _BoundaryTask(base_femtaskpanel._BaseTaskPanel):
    def __init__(self, obj):
        super().__init__(obj)
        self.form = QtGui.QWidget()
        self.form.setWindowTitle("FEM Python task boundary")


class _BoundaryViewProvider(view_base_femobject.VPBaseFemObject):
    def getIcon(self):
        return ""

    def supportsDocumentTimelineEdit(self):
        return True

    def setEdit(self, vobj, mode=0):
        return super().setEdit(
            vobj,
            mode,
            _BoundaryTask,
            hide_mesh=False,
        )


class _BoundaryCommand:
    def __init__(self):
        self.document = None
        self.created_name = ""
        self.transaction_id = 0

    def GetResources(self):
        return {"MenuText": "FEM Python task boundary"}

    def IsActive(self):
        return self.document is not None

    def Activated(self):
        document = self.document
        if (
            document is None
            or App.ActiveDocument is not document
            or App.getDocument(document.Name) is not document
        ):
            raise RuntimeError(
                "The FEM Python task test document is not active"
            )

        document.openTransaction("Create FEM Python task target")
        self.transaction_id = int(document.getBookedTransactionID())
        try:
            obj = document.addObject(
                "App::FeaturePython",
                "FemPythonTaskTarget",
            )
            _BoundaryViewProvider(obj.ViewObject)
            self.created_name = str(obj.Name)
            gui_document = Gui.getDocument(document.Name)
            if not gui_document.setEdit(obj, 0):
                raise RuntimeError("Could not enter the FEM test editor")
        except Exception:
            if (
                self.transaction_id
                and int(document.getBookedTransactionID())
                == self.transaction_id
            ):
                App.closeActiveTransaction(
                    True,
                    self.transaction_id,
                )
            raise


class _SuccessorObserver:
    def __init__(self, document, abort):
        self.document = document
        self.abort = bool(abort)
        self.armed = False
        self.successor_id = 0
        self.error = None

    def slotCloseTransaction(self, abort):
        if (
            not self.armed
            or bool(abort) != self.abort
            or self.successor_id
        ):
            return
        try:
            self.document.openTransaction(
                "FEM Python successor transaction"
            )
            self.successor_id = int(
                self.document.getBookedTransactionID()
            )
            self.document.addObject(
                "App::FeaturePython",
                "FemPythonSuccessor",
            )
        except Exception as error:
            self.error = error


@unittest.skipIf(not App.GuiUp, "FEM Python task tests require the GUI")
class TestFemPythonTaskBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.command = _BoundaryCommand()
        Gui.addCommand(_COMMAND_NAME, cls.command)

    def setUp(self):
        Gui.activateWorkbench("FemWorkbench")
        self.documents = []
        self.owner = self._new_document("FemPythonTaskOwner")
        self.other = self._new_document("FemPythonTaskOther")
        App.setActiveDocument(self.owner.Name)
        self._process_events()
        self.command.document = self.owner
        self.command.created_name = ""
        self.command.transaction_id = 0

    def tearDown(self):
        self.command.document = None
        for document in self.documents:
            gui_document = Gui.getDocument(document.Name)
            if (
                gui_document is not None
                and Gui.Control.activeDialog(gui_document)
            ):
                task = Gui.Control.activeTaskDialog(gui_document)
                if task is not None:
                    task.reject()
                    self._process_events()
            booked = int(document.getBookedTransactionID())
            if booked:
                App.closeActiveTransaction(True, booked)
        for document in reversed(self.documents):
            if document.Name in App.listDocuments():
                App.closeDocument(document.Name)
        self.documents = []
        self._process_events()

    def _new_document(self, name):
        document = App.newDocument(name)
        self.documents.append(document)
        return document

    @staticmethod
    def _process_events(rounds=4):
        for _ in range(rounds):
            Gui.updateGui()
            application = QtGui.QApplication.instance()
            if application is not None:
                application.processEvents(
                    QtCore.QEventLoop.AllEvents,
                    25,
                )

    def _open_task(self):
        App.setActiveDocument(self.owner.Name)
        Gui.runCommand(_COMMAND_NAME, 0)
        self._process_events()
        gui_document = Gui.getDocument(self.owner.Name)
        self.assertTrue(Gui.Control.activeDialog(gui_document))
        self.assertNotEqual(
            int(self.owner.getBookedTransactionID()),
            0,
        )
        self.assertTrue(
            Gui.Control.ownsCommandTransaction(
                gui_document,
                int(self.owner.getBookedTransactionID()),
            )
        )
        return gui_document

    def test_dialog_stays_bound_to_its_owner_when_active_document_changes(
        self,
    ):
        gui_document = self._open_task()
        other_gui_document = Gui.getDocument(self.other.Name)

        App.setActiveDocument(self.other.Name)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog(gui_document))
        self.assertFalse(
            Gui.Control.activeDialog(other_gui_document)
        )

        Gui.Control.activeTaskDialog(gui_document).accept()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog(gui_document))
        self.assertEqual(
            int(self.owner.getBookedTransactionID()),
            0,
        )
        self.assertIsNotNone(
            self.owner.getObject(self.command.created_name)
        )
        self.assertEqual(
            int(self.other.getBookedTransactionID()),
            0,
        )

    def test_accept_does_not_close_a_synchronous_successor(self):
        gui_document = self._open_task()
        observer = _SuccessorObserver(self.owner, abort=False)
        App.addDocumentObserver(observer)
        try:
            observer.armed = True
            Gui.Control.activeTaskDialog(gui_document).accept()
            self._process_events()

            self.assertIsNone(observer.error)
            self.assertNotEqual(observer.successor_id, 0)
            self.assertEqual(
                int(self.owner.getBookedTransactionID()),
                observer.successor_id,
            )
            self.assertIsNotNone(
                self.owner.getObject("FemPythonSuccessor")
            )
        finally:
            observer.armed = False
            App.removeDocumentObserver(observer)

    def test_cancel_does_not_abort_a_synchronous_successor(self):
        gui_document = self._open_task()
        created_name = self.command.created_name
        observer = _SuccessorObserver(self.owner, abort=True)
        App.addDocumentObserver(observer)
        try:
            observer.armed = True
            Gui.Control.activeTaskDialog(gui_document).reject()
            self._process_events()

            self.assertIsNone(observer.error)
            self.assertNotEqual(observer.successor_id, 0)
            self.assertEqual(
                int(self.owner.getBookedTransactionID()),
                observer.successor_id,
            )
            self.assertIsNone(self.owner.getObject(created_name))
            self.assertIsNotNone(
                self.owner.getObject("FemPythonSuccessor")
            )
        finally:
            observer.armed = False
            App.removeDocumentObserver(observer)

    def test_target_identity_never_rebinds_to_reused_name(self):
        obj = self.owner.addObject(
            "App::FeaturePython",
            "FemIdentityTarget",
        )
        panel = _BoundaryTask(obj)
        name = str(obj.Name)
        object_id = int(obj.ID)

        self.owner.removeObject(name)
        replacement = self.owner.addObject(
            "App::FeaturePython",
            name,
        )
        self.assertNotEqual(int(replacement.ID), object_id)

        self.owner.openTransaction("Unrelated successor")
        successor = int(self.owner.getBookedTransactionID())
        with self.assertRaisesRegex(
            RuntimeError,
            "captured object",
        ):
            panel.accept()
        self.assertEqual(
            int(self.owner.getBookedTransactionID()),
            successor,
        )

    def test_target_identity_never_rebinds_to_reused_document_name(
        self,
    ):
        old_document = self.owner
        obj = old_document.addObject(
            "App::FeaturePython",
            "FemIdentityTarget",
        )
        panel = _BoundaryTask(obj)
        document_name = str(old_document.Name)
        document_uid = str(old_document.Uid)

        self.documents.remove(old_document)
        App.closeDocument(document_name)
        replacement = self._new_document(document_name)
        self.owner = replacement
        self.assertNotEqual(str(replacement.Uid), document_uid)

        replacement.openTransaction("Unrelated successor")
        successor = int(replacement.getBookedTransactionID())
        with self.assertRaisesRegex(
            RuntimeError,
            "document is no longer available",
        ):
            panel.accept()
        self.assertEqual(
            int(replacement.getBookedTransactionID()),
            successor,
        )


if __name__ == "__main__":
    unittest.main()
