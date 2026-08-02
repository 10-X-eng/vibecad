# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared semantic-history support for native Python Part Design tools."""

import FreeCAD as App

if App.GuiUp:
    import FreeCADGui as Gui


def _ensure_property(obj, type_id, name, description):
    if name in obj.PropertiesList:
        actual_type = obj.getTypeIdOfProperty(name)
        if actual_type != type_id:
            raise TypeError(
                f"{obj.Name}.{name} must be {type_id}, not {actual_type}"
            )
    else:
        obj.addProperty(
            type_id,
            name,
            "Timeline",
            description,
            attr=16,
            hidden=True,
            locked=True,
        )

    # Dynamic-property attributes and live property statuses are distinct.
    # Imported/copied objects can also retain the type while losing either
    # part of the internal-storage contract. Normalize both before native
    # timeline publication validates the metadata.
    obj.setPropertyStatus(
        name,
        ("Hidden", "LockDynamic", "NoRecompute"),
    )
    obj.setEditorMode(name, 2)


def mark_operation(obj):
    """Publish *obj* as one user-visible semantic history operation."""

    if obj is None or obj.Document is None:
        raise ValueError("A live Part Design operation is required")

    _ensure_property(
        obj,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    if "VibeCADTimelineOwner" in obj.PropertiesList:
        if (
            obj.getTypeIdOfProperty("VibeCADTimelineOwner")
            != "App::PropertyLinkHidden"
            or obj.VibeCADTimelineOwner is not None
        ):
            raise TypeError(
                "A Part Design operation cannot retain resource-owner metadata"
            )
        _ensure_property(
            obj,
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Semantic history operation which owns this implementation object",
        )
    obj.VibeCADTimelineRole = "operation"
    return obj


def mark_resource(obj, owner):
    """Publish *obj* as an implementation resource owned by *owner*."""

    if (
        obj is None
        or owner is None
        or obj is owner
        or obj.Document is None
        or obj.Document is not owner.Document
    ):
        raise ValueError(
            "A Part Design resource and distinct owner must share a document"
        )
    if "VibeCADTimelineReplacedInputs" in obj.PropertiesList:
        raise TypeError(
            "A Part Design resource cannot carry replaced-input metadata"
        )

    mark_operation(owner)
    _ensure_property(
        obj,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    _ensure_property(
        obj,
        "App::PropertyLinkHidden",
        "VibeCADTimelineOwner",
        "Semantic history operation which owns this implementation object",
    )
    current_owner = obj.VibeCADTimelineOwner
    if current_owner is not None and current_owner is not owner:
        raise ValueError(
            f"{obj.Name} is already owned by {current_owner.Name}"
        )
    obj.VibeCADTimelineOwner = owner
    obj.VibeCADTimelineRole = "resource"
    return obj


def can_start_task(document):
    """Return whether a new native task can own *document* exclusively."""

    if not App.GuiUp or document is None or App.ActiveDocument is not document:
        return False
    try:
        gui_document = Gui.getDocument(document.Name)
    except (NameError, RuntimeError):
        return False
    return (
        gui_document is not None
        and not Gui.Control.activeDialog()
        and int(document.getBookedTransactionID()) == 0
        and not document.HasPendingTransaction
    )


def open_task_command(document, label):
    """Open and return the exact transaction owned by a Python task launch."""

    if not can_start_task(document):
        raise RuntimeError(
            "Another operation already owns the Part Design document"
        )
    gui_document = Gui.getDocument(document.Name)
    transaction_id = int(gui_document.openCommand(label))
    if (
        transaction_id == 0
        or int(document.getBookedTransactionID()) != transaction_id
    ):
        raise RuntimeError("Could not open the Part Design task transaction")
    return gui_document, transaction_id


def abort_task_command(document, transaction_id):
    """Abort only the exact failed task-launch transaction, if still owned."""

    transaction_id = int(transaction_id or 0)
    if (
        document is None
        or transaction_id == 0
        or int(document.getBookedTransactionID()) != transaction_id
    ):
        return False
    App.closeActiveTransaction(True, transaction_id)
    return int(document.getBookedTransactionID()) != transaction_id
