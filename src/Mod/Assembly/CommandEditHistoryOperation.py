# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact document-history editor dispatch for native Assembly operations."""

import FreeCAD as App
import FreeCADGui as Gui

import UtilsAssembly


COMMAND_NAME = "Assembly_EditHistoryOperation"


def _has_exact_timeline_metadata(operation):
    properties = set(getattr(operation, "PropertiesList", []) or [])
    if {
        "VibeCADTimelineRole",
        "VibeCADTimelineEditCommand",
    } - properties:
        return False
    try:
        return (
            operation.getTypeIdOfProperty("VibeCADTimelineRole")
            == "App::PropertyString"
            and operation.VibeCADTimelineRole == "operation"
            and operation.getTypeIdOfProperty("VibeCADTimelineEditCommand")
            == "App::PropertyString"
            and operation.VibeCADTimelineEditCommand == COMMAND_NAME
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _operation_editor(operation):
    """Return the exact view proxy for one supported Assembly operation."""

    import CommandCreateSimulation
    import CommandCreateView
    import JointObject

    app_proxy = getattr(operation, "Proxy", None)
    view_object = getattr(operation, "ViewObject", None)
    view_proxy = getattr(view_object, "Proxy", None)
    supported_pairs = (
        (
            CommandCreateView.ExplodedView,
            CommandCreateView.ViewProviderExplodedView,
        ),
        (
            CommandCreateSimulation.Simulation,
            CommandCreateSimulation.ViewProviderSimulation,
        ),
        (JointObject.Joint, JointObject.ViewProviderJoint),
    )
    if any(
        type(app_proxy) is app_type and type(view_proxy) is view_type
        for app_type, view_type in supported_pairs
    ):
        return view_proxy
    return None


def _selected_operation():
    document = App.ActiveDocument
    if (
        document is None
        or Gui.Control.activeDialog()
        or document.HasPendingTransaction
        or document.getBookedTransactionID() != 0
    ):
        return None

    selections = Gui.Selection.getSelectionEx(document.Name, 0)
    if len(selections) != 1:
        return None
    selection = selections[0]
    operation = selection.Object
    if (
        selection.SubElementNames
        or getattr(operation, "Document", None) is not document
        or document.getObject(operation.Name) is not operation
        or not UtilsAssembly.isTimelineOperationActive(operation)
        or not _has_exact_timeline_metadata(operation)
        or _operation_editor(operation) is None
    ):
        return None
    return operation


class CommandEditHistoryOperation:
    def GetResources(self):
        return {
            "MenuText": "Edit Assembly History Operation",
            "ToolTip": "Edit the selected Assembly operation from History",
        }

    def IsActive(self):
        return _selected_operation() is not None

    def Activated(self):
        operation = _selected_operation()
        if operation is None:
            return
        editor = _operation_editor(operation)
        try:
            if editor is None or not editor.doubleClicked(operation.ViewObject):
                raise RuntimeError(
                    f"Could not open the editor for {operation.Label!r}"
                )
        except Exception as error:
            App.Console.PrintError(
                f"Could not edit the Assembly history operation: {error}\n"
            )


Gui.addCommand(COMMAND_NAME, CommandEditHistoryOperation())
for _action in Gui.Command.get(COMMAND_NAME).ensureAction():
    _action.setProperty("VibeCADTimelineOperationEditor", True)
