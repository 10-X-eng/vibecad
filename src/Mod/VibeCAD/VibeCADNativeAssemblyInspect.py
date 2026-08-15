# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact read-only inspection of a human-selected Assembly link source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeTargets import (
    NativeObjectRef,
    document_uid,
    object_reference,
    resolve_object,
)


NATIVE_ASSEMBLY_INSPECT_FAILED = "NATIVE_ASSEMBLY_INSPECT_FAILED"
MAX_LINKED_SELECTION_SUBELEMENTS = 64


class NativeAssemblyInspectError(RuntimeError):
    """The exact selected Assembly source cannot be read safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": NATIVE_ASSEMBLY_INSPECT_FAILED,
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class _SelectedObject:
    obj: Any
    document_uid: str
    document_name: str
    object_name: str
    object_id: int
    subelements: tuple[str, ...]

    def canonical(self) -> tuple[Any, ...]:
        return (
            self.obj,
            self.document_uid,
            self.document_name,
            self.object_name,
            self.object_id,
            self.subelements,
        )


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == type_id:
        return True
    reader = getattr(obj, "isDerivedFrom", None)
    if not callable(reader):
        return False
    try:
        return bool(reader(type_id))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _live_object(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    name = str(getattr(obj, "Name", "") or "")
    reader = getattr(document, "getObject", None)
    return bool(
        document is not None and name and callable(reader) and reader(name) is obj
    )


def _selection_api() -> Any:
    import FreeCADGui as Gui

    return Gui.Selection


def _selected_objects(selection_api: Any) -> tuple[_SelectedObject, ...]:
    reader = getattr(selection_api, "getSelectionEx", None)
    if not callable(reader):
        raise NativeAssemblyInspectError(
            "The exact global GUI selection is unavailable."
        )
    try:
        entries = tuple(reader() or ())
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyInspectError(
            "The exact global GUI selection is unreadable."
        ) from exc
    result = []
    for entry in entries:
        obj = getattr(entry, "Object", None)
        if not _live_object(obj):
            raise NativeAssemblyInspectError(
                "The human selection contains a stale document object."
            )
        try:
            subelements = tuple(
                str(value)
                for value in tuple(getattr(entry, "SubElementNames", ()) or ())
            )
            if len(subelements) > MAX_LINKED_SELECTION_SUBELEMENTS:
                raise NativeAssemblyInspectError(
                    "The selected Assembly link exceeds the bounded subelement count."
                )
            result.append(
                _SelectedObject(
                    obj=obj,
                    document_uid=document_uid(obj.Document),
                    document_name=str(obj.Document.Name),
                    object_name=str(obj.Name),
                    object_id=int(obj.ID),
                    subelements=subelements,
                )
            )
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            if isinstance(exc, NativeAssemblyInspectError):
                raise
            raise NativeAssemblyInspectError(
                "The human selection has an invalid object identity."
            ) from exc
    return tuple(result)


def _exact_object_summary(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        **object_reference(obj),
        "document_name": str(obj.Document.Name),
        "object_id": int(obj.ID),
    }
    label = str(getattr(obj, "Label", "") or "").strip()
    if label and label != result["object_name"]:
        result["label"] = label[:160]
    return result


def _linked_assembly(link: Any) -> Any:
    reader = getattr(link, "getLinkedAssembly", None)
    if callable(reader):
        try:
            source = reader()
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeAssemblyInspectError(
                "The selected Assembly link source is unreadable."
            ) from exc
    else:
        try:
            source = link.LinkedObject
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeAssemblyInspectError(
                "The selected object has no native linked-Assembly source."
            ) from exc
    if (
        source is link
        or not _live_object(source)
        or not _is_derived(source, "Assembly::AssemblyObject")
        or not _timeline_active(source)
    ):
        raise NativeAssemblyInspectError(
            "The selected Assembly link has no exact active linked Assembly."
        )
    return source


def read_selected_linked_assembly(
    document: Any,
    link_ref: NativeObjectRef,
    *,
    guard: Callable[[], None],
    selection_api: Any | None = None,
) -> dict[str, Any]:
    """Read one selected AssemblyLink exactly without changing GUI state."""

    if not isinstance(link_ref, NativeObjectRef):
        raise TypeError("link_ref must be a NativeObjectRef")
    if not callable(guard):
        raise TypeError("guard must be callable")
    selected_api = selection_api if selection_api is not None else _selection_api()

    guard()
    try:
        link = resolve_object(
            document,
            link_ref,
            expected_types=("Assembly::AssemblyLink",),
        )
    except Exception as exc:
        raise NativeAssemblyInspectError(str(exc)) from exc
    if not _timeline_active(link):
        raise NativeAssemblyInspectError(
            "The selected Assembly link is outside the current History position."
        )

    selection_before = _selected_objects(selected_api)
    if len(selection_before) != 1:
        raise NativeAssemblyInspectError(
            "Select exactly one active Assembly link and retry."
        )
    selected = selection_before[0]
    if selected.obj is not link:
        raise NativeAssemblyInspectError(
            "The exact selected Assembly link changed; read selection and retry."
        )

    document_objects = tuple(getattr(document, "Objects", ()) or ())
    source = _linked_assembly(link)
    source_document = source.Document
    source_objects = tuple(getattr(source_document, "Objects", ()) or ())
    result = {
        "operation": "linked_source",
        "assembly_link": _exact_object_summary(link),
        "linked_assembly": _exact_object_summary(source),
        "source_is_external": source_document is not document,
        "rigid": bool(getattr(link, "Rigid", True)),
        "selected_subelements": list(selected.subelements),
    }

    guard()
    selection_after = _selected_objects(selected_api)
    if tuple(item.canonical() for item in selection_after) != tuple(
        item.canonical() for item in selection_before
    ):
        raise NativeAssemblyInspectError(
            "The human selection changed while reading the linked Assembly source."
        )
    if (
        tuple(getattr(document, "Objects", ()) or ()) != document_objects
        or tuple(getattr(source_document, "Objects", ()) or ()) != source_objects
        or not _live_object(link)
        or not _live_object(source)
        or _linked_assembly(link) is not source
    ):
        raise NativeAssemblyInspectError(
            "The linked Assembly graph changed while it was being read."
        )
    result["selection_unchanged"] = True
    result["active_document_unchanged"] = True
    result["document_graph_unchanged"] = True
    return result
