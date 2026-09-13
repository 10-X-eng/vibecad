# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact document, object, subelement, and GUI-selection references."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeState import NativeObjectIdentity


MAX_SELECTION_OBJECTS = 32
MAX_SELECTION_SUBELEMENTS = 64
MAX_TARGET_CANDIDATES = 16
MAX_NATIVE_ID_CHARACTERS = 128
_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUBELEMENT_NAME = re.compile(r"^(Face|Edge|Vertex)[1-9][0-9]*$")


class NativeTargetError(RuntimeError):
    """An exact Native target is missing, stale, or belongs elsewhere."""

    def __init__(
        self,
        message: str,
        *,
        exact_target: Mapping[str, Any] | None = None,
        actual_type: str | None = None,
        accepted_types: tuple[str, ...] = (),
        candidates: tuple[Mapping[str, Any], ...] = (),
    ):
        super().__init__(str(message).strip())
        self.exact_target = dict(exact_target) if exact_target is not None else None
        self.actual_type = str(actual_type or "").strip() or None
        self.accepted_types = tuple(str(value) for value in accepted_types if str(value))
        self.candidates = tuple(dict(value) for value in candidates)

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": "NATIVE_TARGET_INVALID",
            "message": str(self),
        }
        if self.exact_target is not None:
            result["exact_target"] = self.exact_target
        if self.actual_type is not None:
            result["actual_type"] = self.actual_type
        if self.accepted_types:
            result["accepted_types"] = list(self.accepted_types)
        if self.candidates:
            result["candidates"] = [dict(value) for value in self.candidates]
        return result


@dataclass(frozen=True, slots=True)
class NativeObjectRef:
    document_uid: str
    object_name: str

    def __post_init__(self) -> None:
        uid = str(self.document_uid or "").strip()
        name = str(self.object_name or "").strip()
        if not uid or len(uid) > MAX_NATIVE_ID_CHARACTERS:
            raise NativeTargetError("An exact target requires a document UID.")
        if (
            not name
            or len(name) > MAX_NATIVE_ID_CHARACTERS
            or not _OBJECT_NAME.fullmatch(name)
        ):
            raise NativeTargetError("An exact target requires a valid internal object name.")
        object.__setattr__(self, "document_uid", uid)
        object.__setattr__(self, "object_name", name)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NativeObjectRef":
        if not isinstance(value, Mapping):
            raise NativeTargetError("An exact object target must be an object.")
        return cls(
            str(value.get("document_uid") or "").strip(),
            str(value.get("object_name") or "").strip(),
        )

    def summary(self) -> dict[str, str]:
        return {
            "document_uid": self.document_uid,
            "object_name": self.object_name,
        }


@dataclass(frozen=True, slots=True)
class NativeElementRef:
    object: NativeObjectRef
    subelement: str

    def __post_init__(self) -> None:
        name = str(self.subelement or "").strip()
        if not _SUBELEMENT_NAME.fullmatch(name):
            raise NativeTargetError(
                "A geometric target must use an exact FaceN, EdgeN, or VertexN name."
            )
        object.__setattr__(self, "subelement", name)

    def summary(self) -> dict[str, str]:
        return {**self.object.summary(), "subelement": self.subelement}


def document_uid(document: Any) -> str:
    uid = str(getattr(document, "Uid", "") or "").strip()
    if not uid:
        raise NativeTargetError("The Native target document has no stable UID.")
    return uid


def object_identity(obj: Any) -> NativeObjectIdentity:
    document = getattr(obj, "Document", None)
    return NativeObjectIdentity(
        document_uid(document),
        str(getattr(obj, "Name", "") or ""),
        str(getattr(obj, "TypeId", "") or ""),
    )


def object_reference(obj: Any) -> dict[str, str]:
    identity = object_identity(obj)
    return {
        "document_uid": identity.document_uid,
        "object_name": identity.object_name,
        "type_id": identity.type_id,
    }


def _matches_expected_type(obj: Any, expected_types: tuple[str, ...]) -> bool:
    if not expected_types:
        return True
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id in expected_types:
        return True
    is_derived = getattr(obj, "isDerivedFrom", None)
    if not callable(is_derived):
        return False
    for expected in expected_types:
        try:
            if is_derived(expected):
                return True
        except Exception:
            continue
    return False


def _target_candidates(
    document: Any,
    expected_types: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    if not expected_types:
        return ()
    return tuple(
        object_reference(candidate)
        for candidate in tuple(getattr(document, "Objects", ()) or ())
        if _matches_expected_type(candidate, expected_types)
    )[:MAX_TARGET_CANDIDATES]


def resolve_object(
    document: Any,
    target: NativeObjectRef | Mapping[str, Any],
    *,
    expected_types: tuple[str, ...] = (),
) -> Any:
    reference = (
        target if isinstance(target, NativeObjectRef) else NativeObjectRef.from_mapping(target)
    )
    if document_uid(document) != reference.document_uid:
        raise NativeTargetError(
            "The exact object target belongs to another document.",
            exact_target=reference.summary(),
        )
    get_object = getattr(document, "getObject", None)
    obj = get_object(reference.object_name) if callable(get_object) else None
    if obj is None or getattr(obj, "Document", None) is not document:
        raise NativeTargetError(
            "The exact object target no longer exists.",
            exact_target=reference.summary(),
            accepted_types=tuple(expected_types),
            candidates=_target_candidates(document, tuple(expected_types)),
        )
    if not _matches_expected_type(obj, tuple(expected_types)):
        actual_type = str(getattr(obj, "TypeId", "") or "") or "unknown"
        accepted = tuple(str(value) for value in expected_types)
        raise NativeTargetError(
            f"The exact object target has type {actual_type!r}; this operation "
            f"accepts {', '.join(accepted)}.",
            exact_target=reference.summary(),
            actual_type=actual_type,
            accepted_types=accepted,
            candidates=_target_candidates(document, accepted),
        )
    return obj


def resolve_element(document: Any, target: NativeElementRef) -> tuple[Any, Any]:
    if not isinstance(target, NativeElementRef):
        raise TypeError("target must be a NativeElementRef")
    obj = resolve_object(document, target.object)
    shape = getattr(obj, "Shape", None)
    get_element = getattr(shape, "getElement", None)
    if not callable(get_element):
        raise NativeTargetError(
            "The exact object target has no selectable shape.",
            exact_target=target.summary(),
        )
    try:
        element = get_element(target.subelement)
    except Exception as exc:
        raise NativeTargetError(
            "The exact geometric subelement no longer exists.",
            exact_target=target.summary(),
        ) from exc
    return obj, element


def read_current_selection(document: Any, selection_api: Any | None = None) -> dict[str, Any]:
    uid = document_uid(document)
    if selection_api is None:
        import FreeCAD as App

        if App.GuiUp:
            import FreeCADGui as Gui

            selection_api = Gui.Selection
    if selection_api is None:
        return {
            "document_uid": uid,
            "selected_count": 0,
            "items": [],
        }
    selected = list(selection_api.getSelectionEx(str(document.Name)) or [])
    items = []
    for entry in selected[:MAX_SELECTION_OBJECTS]:
        obj = getattr(entry, "Object", None)
        if obj is None or getattr(obj, "Document", None) is not document:
            continue
        raw_subelements = [
            str(name)
            for name in list(getattr(entry, "SubElementNames", []) or [])
            if str(name)
        ]
        subelements = raw_subelements[:MAX_SELECTION_SUBELEMENTS]
        item: dict[str, Any] = {"object": object_reference(obj)}
        if subelements:
            item["subelements"] = subelements
        if len(raw_subelements) > len(subelements):
            item["subelements_truncated"] = True
        items.append(item)
    result: dict[str, Any] = {
        "document_uid": uid,
        "selected_count": len(selected),
        "items": items,
    }
    if len(selected) > len(items):
        result["truncated"] = True
    return result
