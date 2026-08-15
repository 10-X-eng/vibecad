# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact live Assembly identity without changing human activation state."""

from __future__ import annotations

from typing import Any, Callable


class NativeAssemblyStateError(RuntimeError):
    """The live Assembly interaction state could not be read exactly."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_STATE_FAILED",
            "message": str(self),
        }


def is_assembly_object(obj: Any) -> bool:
    if obj is None:
        return False
    if str(getattr(obj, "TypeId", "") or "") == "Assembly::AssemblyObject":
        return True
    derived = getattr(obj, "isDerivedFrom", None)
    if not callable(derived):
        return False
    try:
        return bool(derived("Assembly::AssemblyObject"))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def assembly_objects(document: Any) -> tuple[Any, ...]:
    return tuple(
        obj
        for obj in list(getattr(document, "Objects", ()) or ())
        if getattr(obj, "Document", None) is document and is_assembly_object(obj)
    )


def _default_gui_document(document: Any) -> Any | None:
    try:
        import FreeCADGui as Gui

        return Gui.getDocument(str(getattr(document, "Name", "") or ""))
    except (ImportError, AttributeError, NameError, RuntimeError) as exc:
        raise NativeAssemblyStateError(
            "The active Assembly view is unavailable on the document thread."
        ) from exc


def _default_timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def read_active_assembly(
    document: Any,
    *,
    gui_document: Any | None = None,
    gui_document_reader: Callable[[Any], Any | None] = _default_gui_document,
    timeline_active: Callable[[Any], bool] = _default_timeline_active,
) -> Any | None:
    """Return the exact human-active Assembly, or ``None``, without mutation."""

    selected_gui_document = (
        gui_document if gui_document is not None else gui_document_reader(document)
    )
    if selected_gui_document is None:
        raise NativeAssemblyStateError(
            "The active document has no readable GUI Assembly view."
        )
    if getattr(selected_gui_document, "Document", None) is not document:
        raise NativeAssemblyStateError(
            "The active Assembly view belongs to another document."
        )
    view = getattr(selected_gui_document, "ActiveView", None)
    if view is None:
        active_view = getattr(selected_gui_document, "activeView", None)
        view = active_view() if callable(active_view) else None
    get_active = getattr(view, "getActiveObject", None)
    if not callable(get_active):
        return None
    active = get_active("assembly")
    if active is None:
        return None
    name = str(getattr(active, "Name", "") or "")
    get_object = getattr(document, "getObject", None)
    if (
        not name
        or not callable(get_object)
        or get_object(name) is not active
        or getattr(active, "Document", None) is not document
        or not is_assembly_object(active)
    ):
        raise NativeAssemblyStateError(
            "The human-active Assembly is not an exact live document object."
        )
    view_object = getattr(active, "ViewObject", None)
    in_edit = getattr(view_object, "isInEditMode", None)
    if not callable(in_edit) or not bool(in_edit()):
        raise NativeAssemblyStateError(
            "The Assembly view reports an inactive edit object as active."
        )
    if not timeline_active(active):
        raise NativeAssemblyStateError(
            "The human-active Assembly is outside the current document history."
        )
    return active


def same_assembly(first: Any | None, second: Any | None) -> bool:
    if first is None or second is None:
        return first is second
    return (
        first is second
        and getattr(first, "Document", None) is getattr(second, "Document", None)
        and str(getattr(first, "Name", "") or "")
        == str(getattr(second, "Name", "") or "")
        and int(getattr(first, "ID", 0) or 0) == int(getattr(second, "ID", 0) or 0)
    )
