# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   (c) 2009, Yorik van Havre <yorik@uncreated.net>                       *
# *   (c) 2019 Eliud Cabrera Castillo <e.cabrera-castillo@tum.de>           *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
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
# *                                                                         *
# ***************************************************************************
"""Provides the ToDo static class to run commands with a time delay.

The `ToDo` class is used to delay the commit of commands for later execution.
This is necessary when a GUI command needs to manipulate the 3D view
in such a way that a callback would crash `Coin`.

The `ToDo` class essentially calls `QtCore.QTimer.singleShot`
to execute the instructions stored in internal lists.
"""

## @package todo
# \ingroup draftutils
# \brief Provides the ToDo static class to run commands with a time delay.

from dataclasses import dataclass
import sys
import traceback
import PySide.QtCore as QtCore

import FreeCAD as App
import FreeCADGui as Gui

from draftutils.messages import _msg, _wrn, _err, _log
from draftutils.transaction import (
    DocumentReference,
    ObjectReference,
    OwnedDocumentTransaction,
    validate_object_references,
)

__title__ = "FreeCAD Draft Workbench, Todo class"
__author__ = "Yorik van Havre <yorik@uncreated.net>"
__url__ = ["https://www.freecad.org"]

_DEBUG = 0
_DEBUG_inner = 0

## \addtogroup draftutils
# @{


def _capture_object_references(document, objects):
    """Capture declared mutation inputs without interpreting command text."""

    document_reference = DocumentReference.capture(document)
    references = {}
    for item in objects:
        reference = (
            item
            if isinstance(item, ObjectReference)
            else ObjectReference.capture(item)
        )
        if reference.document != document_reference:
            raise ValueError(
                "A delayed Draft action input belongs to another document"
            )
        references[(reference.name, reference.object_id)] = reference
    return tuple(references.values())


@dataclass(frozen=True)
class _DeferredCommit:
    """One delayed command bound to exact document and input identities."""

    document: DocumentReference
    objects: tuple
    name: str
    commands: object

    @classmethod
    def capture(cls, document, name, commands, objects=()):
        return cls(
            DocumentReference.capture(document),
            _capture_object_references(document, objects),
            str(name),
            commands,
        )

    def execute(self):
        document = self.document.resolve()
        if document is None:
            raise RuntimeError(
                "The Draft command document was closed or replaced "
                "before delayed execution"
            )
        validate_object_references(document, self.objects)

        previous_document = App.activeDocument()
        previous_reference = (
            DocumentReference.capture(previous_document)
            if previous_document is not None
            else None
        )
        transaction = OwnedDocumentTransaction(document, self.name)

        def validate_execution_context():
            if self.document.resolve() is not document:
                raise RuntimeError(
                    "The Draft command document was closed or replaced "
                    "during delayed execution"
                )
            if App.activeDocument() is not document:
                raise RuntimeError(
                    "The active document changed during delayed Draft "
                    "execution"
                )
            validate_object_references(document, self.objects)

        try:
            if App.activeDocument() is not document:
                App.setActiveDocument(document.Name)
            if App.activeDocument() is not document:
                raise RuntimeError(
                    "Could not activate the exact Draft command document"
                )

            if isinstance(self.commands, list):
                for command in self.commands:
                    validate_execution_context()
                    Gui.doCommand(command)
                    validate_execution_context()
            else:
                validate_execution_context()
                self.commands()
                validate_execution_context()
        except Exception:
            if not transaction.is_closed:
                transaction.abort()
            raise
        else:
            # A failed exact commit remains a retained commit request. Never
            # reinterpret it as a rollback in the exception path.
            transaction.commit()
        finally:
            previous_live = (
                previous_reference.resolve()
                if previous_reference is not None
                else None
            )
            if (
                previous_live is not None
                and App.activeDocument() is not previous_live
            ):
                App.setActiveDocument(previous_reference.name)


class ToDo:
    """A static class that delays execution of functions.

    It calls `QtCore.QTimer.singleShot(0, doTasks)`
    where `doTasks` is a static method which executes
    the commands stored in the list attributes.

    Attributes
    ----------
    itinerary: list of tuples
        Each tuple is of the form `(name, arg)`.
        The `name` is a reference (pointer) to a function,
        and `arg` is the corresponding argument that is passed
        to that function.
        It then tries executing the function with the argument,
        if available, or without it, if not available.
        ::
            name(arg)
            name()

    commitlist: list of tuples
        Each tuple is of the form `(name, command_list)`.
        The `name` is a string identifier or description of the commands
        that will be run, and `command_list` is a list of strings
        that indicate the Python instructions that will be executed,
        or a reference to a single function that will be executed.

        If `command_list` is a list, the program opens a transaction,
        then runs all commands in the list in sequence,
        and finally commits the transaction.
        ::
            command_list = ["command1", "command2", "..."]
            App.activeDocument().openTransaction(name)
            Gui.doCommand("command1")
            Gui.doCommand("command2")
            Gui.doCommand("...")
            App.activeDocument().commitTransaction()

        If `command_list` is a reference to a function
        the function is executed directly.
        ::
            command_list = function
            App.activeDocument().openTransaction(name)
            function()
            App.activeDocument().commitTransaction()

    afteritinerary: list of tuples
        Each tuple is of the form `(name, arg)`.
        This list is used just like `itinerary`.

    Lists
    -----
    The lists contain tuples. Each tuple contains a `name` which is just
    a string to identify the operation, and a `command_list` which is
    a list of strings, each string an individual Python instruction.
    """

    itinerary = []
    commitlist = []
    afteritinerary = []

    @staticmethod
    def doTasks():
        """Execute the commands stored in the lists.

        The lists are `itinerary`, `commitlist` and `afteritinerary`.
        """
        if _DEBUG:
            _msg(
                "Debug: doing delayed tasks.\n"
                "itinerary: {0}\n"
                "commitlist: {1}\n"
                "afteritinerary: {2}\n".format(todo.itinerary, todo.commitlist, todo.afteritinerary)
            )
        try:
            for f, arg in ToDo.itinerary:
                try:
                    if _DEBUG_inner:
                        _msg("Debug: executing.\n" "function: {}\n".format(f))
                    if arg or (arg is False):
                        f(arg)
                    else:
                        f()
                except Exception:
                    _log(traceback.format_exc())
                    _err(traceback.format_exc())
                    wrn = (
                        "ToDo.doTasks, Unexpected error:\n"
                        "{0}\n"
                        "in {1}({2})".format(sys.exc_info()[0], f, arg)
                    )
                    _wrn(wrn)
        except ReferenceError:
            _wrn("Debug: ToDo.doTasks: " "queue contains a deleted object, skipping")
        ToDo.itinerary = []

        if ToDo.commitlist:
            commit_list = ToDo.commitlist
            ToDo.commitlist = []  # Reset immediately to avoid race condition.
            for deferred in commit_list:
                if _DEBUG_inner:
                    deferred_name = (
                        deferred.name
                        if isinstance(deferred, _DeferredCommit)
                        else deferred[0]
                    )
                    _msg(
                        "Debug: committing.\n"
                        "name: {}\n".format(deferred_name)
                    )
                try:
                    if isinstance(deferred, _DeferredCommit):
                        deferred.execute()
                    else:
                        name, func, *entry_inputs = deferred
                        document = App.activeDocument()
                        if document is None:
                            raise RuntimeError(
                                "A delayed Draft command requires a document"
                            )
                        _DeferredCommit.capture(
                            document,
                            name,
                            func,
                            entry_inputs[0] if entry_inputs else (),
                        ).execute()
                except Exception:
                    _log(traceback.format_exc())
                    _err(traceback.format_exc())
                    wrn = (
                        "ToDo.doTasks, Unexpected error:\n"
                        "{0}\n"
                        "in {1}".format(
                            sys.exc_info()[0],
                            getattr(deferred, "commands", deferred),
                        )
                    )
                    _wrn(wrn)
            # Restack Draft screen widgets after creation
            if hasattr(Gui, "Snapper"):
                Gui.Snapper.restack()

        for f, arg in ToDo.afteritinerary:
            try:
                if _DEBUG_inner:
                    _msg("Debug: executing after.\n" "function: {}\n".format(f))
                if arg:
                    f(arg)
                else:
                    f()
            except Exception:
                _log(traceback.format_exc())
                _err(traceback.format_exc())
                wrn = (
                    "ToDo.doTasks, Unexpected error:\n"
                    "{0}\n"
                    "in {1}({2})".format(sys.exc_info()[0], f, arg)
                )
                _wrn(wrn)
        ToDo.afteritinerary = []

    @staticmethod
    def delay(f, arg):
        """Add the function and argument to the itinerary list.

        Schedule geometry manipulation that would crash Coin if done
        in the event callback.

        If the `itinerary` list is empty, it will call
        `QtCore.QTimer.singleShot(0, ToDo.doTasks)`
        to execute the commands in the other lists.

        Finally, it will build the tuple `(f, arg)`
        and append it to the `itinerary` list.

        Parameters
        ----------
        f: function reference
            A reference (pointer) to a Python command
            which can be executed directly.
            ::
                f()

        arg: argument reference
            A reference (pointer) to the argument to the `f` function.
            ::
                f(arg)
        """
        if _DEBUG:
            _msg("Debug: delaying.\n" "function: {}\n".format(f))
        if ToDo.itinerary == []:
            QtCore.QTimer.singleShot(0, ToDo.doTasks)
        ToDo.itinerary.append((f, arg))

    @staticmethod
    def delayCommit(cl, document=None, objects=()):
        """Execute the other lists, and add to the commit list.

        Schedule geometry manipulation that would crash Coin if done
        in the event callback.

        First it calls
        `QtCore.QTimer.singleShot(0, ToDo.doTasks)`
        to execute the commands in all lists.

        Then the `cl` list is assigned as the new commit list.

        Parameters
        ----------
        cl: list of tuples
            Each tuple is of the form `(name, command_list)`.
            The `name` is a string identifier or description of the commands
            that will be run, and `command_list` is a list of strings
            that indicate the Python instructions that will be executed.

            See the attributes of the `ToDo` class for more information.

        document: App::Document, optional
            Exact document captured by the GUI command. If omitted, the
            current active document is captured for compatibility.

        objects: iterable of App::DocumentObject, optional
            Exact inputs shared by legacy two-field entries. New mutating
            call sites should use a three-field entry
            ``(name, commands, inputs)`` so each action declares its own
            stable inputs.
        """
        if _DEBUG:
            _msg("Debug: delaying commit.\n" "commitlist: {}\n".format(cl))
        if document is None:
            document = App.activeDocument()
        if document is None:
            raise RuntimeError("A delayed Draft command requires a document")
        deferred = []
        for entry in cl:
            if len(entry) == 2:
                name, commands = entry
                inputs = objects
            elif len(entry) == 3:
                name, commands, inputs = entry
            else:
                raise ValueError(
                    "A delayed Draft action must contain "
                    "(name, commands) or (name, commands, inputs)"
                )
            deferred.append(
                _DeferredCommit.capture(
                    document,
                    name,
                    commands,
                    inputs,
                )
            )
        QtCore.QTimer.singleShot(0, ToDo.doTasks)
        ToDo.commitlist.extend(deferred)

    @staticmethod
    def delayAfter(f, arg):
        """Add the function and argument to the afteritinerary list.

        Schedule geometry manipulation that would crash Coin if done
        in the event callback.

        Works the same as `delay`.

        If the `afteritinerary` list is empty, it will call
        `QtCore.QTimer.singleShot(0, ToDo.doTasks)`
        to execute the commands in the other lists.

        Finally, it will build the tuple `(f, arg)`
        and append it to the `afteritinerary` list.
        """
        if _DEBUG:
            _msg("Debug: delaying after.\n" "function: {}\n".format(f))
        if ToDo.afteritinerary == []:
            QtCore.QTimer.singleShot(0, ToDo.doTasks)
        ToDo.afteritinerary.append((f, arg))


# Alias for compatibility with v0.18 and earlier
todo = ToDo

## @}
