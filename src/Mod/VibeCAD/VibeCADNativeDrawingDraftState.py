# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for Draft sources and TechDraw Draft views."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import json
from typing import Any, Mapping
import zipfile

from VibeCADNativeDrawingState import is_drawing_page


MAX_DRAFT_SOURCE_DEPENDENCIES = 256
MAX_DRAFT_SOURCE_STATE_BYTES = 512 * 1024 * 1024
MAX_DRAFT_SYMBOL_BYTES = 32 * 1024 * 1024


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_derived(obj: Any, type_id: str) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    if callable(checker):
        try:
            return bool(checker(type_id))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == type_id


def is_draft_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawViewDraft")


def _is_drawing_view(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawView")


@dataclass(frozen=True, slots=True)
class DraftPropertyFingerprint:
    name: str
    type_id: str
    statuses: tuple[str, ...]
    size_bytes: int
    sha256: str


def _canonical_property_content(
    content: bytes,
    noun: str,
    property_name: str,
) -> tuple[int, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(content), mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("duplicate archive members")
            records = []
            total = 0
            for info in sorted(infos, key=lambda item: item.filename):
                name = str(info.filename)
                if (
                    not name
                    or name.startswith(("/", "\\"))
                    or ".." in name.replace("\\", "/").split("/")
                ):
                    raise ValueError("unsafe archive member")
                data = archive.read(info)
                total += len(data)
                records.append(
                    {
                        "name": name,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
    except (KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        raise ValueError(
            f"The Draft source {noun} property {property_name!r} has invalid persisted content."
        ) from exc
    encoded = json.dumps(
        records,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return total + len(encoded), hashlib.sha256(encoded).hexdigest()


def _part_shape_content(
    value: Any,
    property_name: str,
    noun: str,
) -> tuple[int, str]:
    """Return the stable persisted BREP for one Part shape property.

    ``dumpPropertyContent`` also contains FreeCAD's element-name map.  That
    map is rebuilt while a document is restored and is therefore not a
    stable representation of unchanged geometry.  Draft rendering consumes
    the TopoShape itself, so its canonical BREP is the exact durable input we
    need to authenticate.
    """

    try:
        shape = getattr(value, property_name)
        export = getattr(shape, "exportBrepToString", None)
        if not callable(export):
            raise TypeError("shape has no BREP exporter")
        content = export()
        encoded = content if isinstance(content, bytes) else str(content).encode("utf-8")
    except Exception as exc:
        raise ValueError(
            f"The Draft source {noun} property {property_name!r} has unreadable Part geometry."
        ) from exc
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _persistent_property_digest(
    value: Any,
    noun: str,
) -> tuple[int, str, tuple[DraftPropertyFingerprint, ...]]:
    properties = tuple(sorted(str(name) for name in value.PropertiesList))
    records = []
    total = 0
    for name in properties:
        try:
            statuses = tuple(sorted(str(item) for item in value.getPropertyStatus(name)))
        except Exception as exc:
            raise ValueError(
                f"The Draft source {noun} property status cannot be inspected."
            ) from exc
        if "Transient" in statuses:
            continue
        try:
            content = bytes(value.dumpPropertyContent(name, Compression=9))
            type_id = str(value.getTypeIdOfProperty(name))
        except Exception as exc:
            raise ValueError(
                f"The Draft source {noun} property {name!r} cannot be serialized exactly."
            ) from exc
        if type_id == "Part::PropertyPartShape":
            content_size, content_sha256 = _part_shape_content(value, name, noun)
        else:
            content_size, content_sha256 = _canonical_property_content(
                content,
                noun,
                name,
            )
        total += content_size
        records.append(
            DraftPropertyFingerprint(
                name=name,
                type_id=type_id,
                statuses=statuses,
                size_bytes=content_size,
                sha256=content_sha256,
            )
        )
    encoded = json.dumps(
        [
            {
                "name": record.name,
                "type_id": record.type_id,
                "statuses": record.statuses,
                "bytes": record.size_bytes,
                "sha256": record.sha256,
            }
            for record in records
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        total + len(encoded),
        hashlib.sha256(encoded).hexdigest(),
        tuple(records),
    )


def _source_members(source: Any) -> tuple[Any, ...]:
    document = getattr(source, "Document", None)
    if document is None:
        raise ValueError("A Draft source must belong to the active document.")
    values = (source, *(tuple(getattr(source, "OutListRecursive", ()) or ())))
    unique: dict[tuple[int, str, str], Any] = {}
    for obj in values:
        if obj is None or getattr(obj, "Document", None) is not document:
            raise ValueError("A Draft source dependency left the active document.")
        identity = (
            int(getattr(obj, "ID", -1)),
            str(getattr(obj, "Name", "") or ""),
            str(getattr(obj, "TypeId", "") or ""),
        )
        if identity[0] < 0 or not identity[1] or not identity[2]:
            raise ValueError("A Draft source dependency has no stable identity.")
        unique.setdefault(identity, obj)
    if not 1 <= len(unique) <= MAX_DRAFT_SOURCE_DEPENDENCIES:
        raise ValueError(
            "A Draft source dependency graph must contain between 1 and 256 objects."
        )
    return tuple(unique[key] for key in sorted(unique))


@dataclass(frozen=True, slots=True)
class DraftSourceMemberFingerprint:
    object_id: int
    object_name: str
    type_id: str
    app_bytes: int
    app_sha256: str
    view_bytes: int | None
    view_sha256: str | None
    app_properties: tuple[DraftPropertyFingerprint, ...] = field(
        repr=False,
        compare=False,
    )
    view_properties: tuple[DraftPropertyFingerprint, ...] = field(
        repr=False,
        compare=False,
    )

    def descriptor(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "object_name": self.object_name,
            "type_id": self.type_id,
            "app_bytes": self.app_bytes,
            "app_sha256": self.app_sha256,
            "view_bytes": self.view_bytes,
            "view_sha256": self.view_sha256,
        }


@dataclass(frozen=True, slots=True)
class DraftSourceFingerprint:
    source: Any
    members: tuple[DraftSourceMemberFingerprint, ...]
    state_sha256: str
    total_serialized_bytes: int

    def summary(self) -> dict[str, Any]:
        return {
            "object_name": str(self.source.Name),
            "label": str(getattr(self.source, "Label", "") or ""),
            "type_id": str(self.source.TypeId),
            "state_sha256": self.state_sha256,
            "dependency_count": len(self.members) - 1,
            "serialized_state_bytes": self.total_serialized_bytes,
        }

    def descriptor(self) -> dict[str, Any]:
        return {
            "object_id": int(self.source.ID),
            "object_name": str(self.source.Name),
            "type_id": str(self.source.TypeId),
            "state_sha256": self.state_sha256,
            "members": [member.descriptor() for member in self.members],
        }


def draft_source_fingerprint(source: Any) -> DraftSourceFingerprint:
    """Hash one source and its exact outward dependency/presentation graph."""

    if source is None or is_drawing_page(source) or _is_drawing_view(source):
        raise ValueError("A Draft source must be one non-Drawing model object.")
    records = []
    total = 0
    for obj in _source_members(source):
        app_bytes, app_sha256, app_properties = _persistent_property_digest(
            obj,
            "object state",
        )
        view = getattr(obj, "ViewObject", None)
        if view is None:
            view_bytes = None
            view_sha256 = None
            view_properties = ()
        else:
            view_bytes, view_sha256, view_properties = _persistent_property_digest(
                view,
                "presentation state",
            )
        total += app_bytes + int(view_bytes or 0)
        if total > MAX_DRAFT_SOURCE_STATE_BYTES:
            raise ValueError("The Draft source state exceeds its 512 MiB safety bound.")
        records.append(
            DraftSourceMemberFingerprint(
                object_id=int(obj.ID),
                object_name=str(obj.Name),
                type_id=str(obj.TypeId),
                app_bytes=app_bytes,
                app_sha256=app_sha256,
                view_bytes=view_bytes,
                view_sha256=view_sha256,
                app_properties=app_properties,
                view_properties=view_properties,
            )
        )
    payload = {
        "source": {
            "object_id": int(source.ID),
            "object_name": str(source.Name),
            "type_id": str(source.TypeId),
        },
        "members": [record.descriptor() for record in records],
    }
    return DraftSourceFingerprint(
        source=source,
        members=tuple(records),
        state_sha256=_digest(payload),
        total_serialized_bytes=total,
    )


def drawing_draft_source_state(source: Any) -> dict[str, Any]:
    return draft_source_fingerprint(source).summary()


def _vector(value: Any) -> list[float]:
    return [round(float(getattr(value, name)), 12) for name in ("x", "y", "z")]


def _parent_page(view: Any) -> Any | None:
    finder = getattr(view, "findParentPage", None)
    if callable(finder):
        try:
            page = finder()
            if is_drawing_page(page):
                return page
        except Exception:
            pass
    document = getattr(view, "Document", None)
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        if is_drawing_page(obj) and view in tuple(getattr(obj, "Views", ()) or ()):
            return obj
    return None


def drawing_draft_view_state(view: Any) -> dict[str, Any]:
    if not is_draft_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawViewDraft")
    source = getattr(view, "Source", None)
    source_state = drawing_draft_source_state(source) if source is not None else None
    symbol = str(getattr(view, "Symbol", "") or "")
    symbol_bytes = len(symbol.encode("utf-8"))
    if symbol_bytes > MAX_DRAFT_SYMBOL_BYTES:
        raise ValueError("The Draft view SVG exceeds its 32 MiB safety bound.")
    page = _parent_page(view)
    settings = {
        "page_name": str(getattr(page, "Name", "") or "") if page else None,
        "source": (
            {
                "object_name": source_state["object_name"],
                "state_sha256": source_state["state_sha256"],
            }
            if source_state is not None
            else None
        ),
        "direction": _vector(view.Direction),
        "x_mm": round(float(view.X), 9),
        "y_mm": round(float(view.Y), 9),
        "scale_type": str(view.ScaleType),
        "scale": round(float(view.Scale), 12),
        "style": {
            "line_width": round(float(view.LineWidth), 9),
            "font_size_pt": round(float(view.FontSize), 9),
            "color": [round(float(value), 8) for value in tuple(view.Color)[:3]],
            "line_style": str(view.LineStyle),
            "line_spacing": round(float(view.LineSpacing), 9),
            "override": bool(view.OverrideStyle),
        },
        "svg_bytes": symbol_bytes,
        "svg_sha256": hashlib.sha256(symbol.encode("utf-8")).hexdigest(),
    }
    result = {
        "object_name": str(view.Name),
        "label": str(getattr(view, "Label", "") or ""),
        "type_id": str(view.TypeId),
        **settings,
    }
    result["state_sha256"] = _digest(
        {
            "object_name": result["object_name"],
            "type_id": result["type_id"],
            **settings,
        }
    )
    return result


__all__ = [
    "DraftSourceFingerprint",
    "MAX_DRAFT_SOURCE_DEPENDENCIES",
    "MAX_DRAFT_SOURCE_STATE_BYTES",
    "MAX_DRAFT_SYMBOL_BYTES",
    "draft_source_fingerprint",
    "drawing_draft_source_state",
    "drawing_draft_view_state",
    "is_draft_drawing_view",
]
