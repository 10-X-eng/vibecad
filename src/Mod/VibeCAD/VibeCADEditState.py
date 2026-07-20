# SPDX-License-Identifier: LGPL-2.1-or-later

"""One native adapter for FreeCAD's active GUI edit session.

``Gui.Document.getInEdit()`` returns a view provider.  View-provider Python
bindings are not uniform: some expose ``Object`` and others, including the
native Assembly view provider, intentionally do not.  ``getInEditInfo`` is
the native property that returns the underlying ``App::DocumentObject``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActiveEditState:
    """Resolved state for one FreeCAD GUI edit session."""

    active: bool
    document_object: Any | None = None
    view_provider: Any | None = None
    subname: str = ""
    subelement: str = ""
    mode: int | None = None
    error: str = ""


def _document_object(value: Any) -> Any | None:
    """Return a real document object without assuming a view-provider schema."""

    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    if value is None:
        return None
    try:
        provider_object = getattr(value, "Object", None)
    except Exception:
        provider_object = None
    if provider_object is not None:
        value = provider_object
    try:
        name = getattr(value, "Name", None)
        type_id = getattr(value, "TypeId", None)
    except Exception:
        return None
    return value if name is not None and type_id is not None else None


def _optional_text(values: tuple[Any, ...], index: int) -> str:
    if index >= len(values) or values[index] is None:
        return ""
    try:
        return str(values[index])
    except Exception:
        return ""


def _optional_mode(values: tuple[Any, ...]) -> int | None:
    if len(values) < 4 or values[3] is None:
        return None
    try:
        return int(values[3])
    except (TypeError, ValueError):
        return None


def active_edit_state(gui_document: Any | None = None) -> ActiveEditState:
    """Return the active edit session without leaking GUI wrappers to callers.

    ``getInEditInfo`` is authoritative when available.  ``getInEdit()`` is
    retained only as a compatibility and active-session fallback; an opaque
    view provider is reported as active but is never treated as a document
    object.
    """

    errors: list[str] = []
    edit_info_present = False
    if gui_document is None:
        try:
            import FreeCADGui as Gui

            gui_document = getattr(Gui, "ActiveDocument", None)
        except Exception as exc:
            return ActiveEditState(active=False, error=str(exc))
    if gui_document is None:
        return ActiveEditState(active=False)

    try:
        get_info = getattr(gui_document, "getInEditInfo", None)
    except Exception as exc:
        get_info = None
        errors.append(f"getInEditInfo failed: {exc}")
    if get_info is not None:
        try:
            raw_info = get_info() if callable(get_info) else get_info
        except Exception as exc:
            errors.append(f"getInEditInfo failed: {exc}")
        else:
            if raw_info is not None:
                edit_info_present = True
                values = tuple(raw_info) if isinstance(raw_info, (tuple, list)) else (raw_info,)
                document_object = _document_object(values[0] if values else None)
                if document_object is not None:
                    return ActiveEditState(
                        active=True,
                        document_object=document_object,
                        subname=_optional_text(values, 1),
                        subelement=_optional_text(values, 2),
                        mode=_optional_mode(values),
                        error="; ".join(errors),
                    )
                errors.append("getInEditInfo returned no document object")

    try:
        get_view_provider = getattr(gui_document, "getInEdit", None)
    except Exception as exc:
        get_view_provider = None
        errors.append(f"getInEdit failed: {exc}")
    if callable(get_view_provider):
        try:
            view_provider = get_view_provider()
        except Exception as exc:
            errors.append(f"getInEdit failed: {exc}")
        else:
            if isinstance(view_provider, (tuple, list)):
                view_provider = view_provider[0] if view_provider else None
            if view_provider is not None:
                return ActiveEditState(
                    active=True,
                    document_object=_document_object(view_provider),
                    view_provider=view_provider,
                    error="; ".join(errors),
                )

    return ActiveEditState(active=edit_info_present, error="; ".join(errors))


def active_edit_object(gui_document: Any | None = None) -> Any | None:
    """Return only the underlying document object for the active edit session."""

    return active_edit_state(gui_document).document_object
