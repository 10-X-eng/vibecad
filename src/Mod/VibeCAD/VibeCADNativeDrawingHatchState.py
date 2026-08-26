# SPDX-License-Identifier: LGPL-2.1-or-later

"""Path-free exact state for Drawing image and geometric hatches."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from VibeCADNativeInput import NativeInputError, inspect_native_input_file


MAX_DRAWING_HATCH_FACES = 64
MAX_DRAWING_HATCHES_PER_VIEW = 64
MAX_DRAWING_IMAGE_PATTERN_BYTES = 16 * 1024 * 1024
MAX_DRAWING_PAT_PATTERN_BYTES = 4 * 1024 * 1024
MAX_DRAWING_PATTERNS = 256
_FACE = re.compile(r"^Face(?:0|[1-9][0-9]*)$")
_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IMAGE_SUFFIXES = frozenset({".svg", ".png", ".bmp", ".jpg", ".jpeg"})


class NativeDrawingHatchStateError(RuntimeError):
    """A Drawing hatch or host hatch plan is malformed."""


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived(obj: Any, type_id: str) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(callable(checker) and checker(type_id))
    except Exception:
        return False


def is_drawing_hatch(obj: Any) -> bool:
    return _derived(obj, "TechDraw::DrawHatch") or _derived(
        obj, "TechDraw::DrawGeomHatch"
    )


def drawing_hatch_view_belongs_to_page(view: Any, page: Any) -> bool:
    """Accept direct views and projection-group children on their exact page."""

    finder = getattr(view, "findParentPage", None)
    if callable(finder):
        try:
            return finder() is page
        except Exception:
            pass
    return view in tuple(getattr(page, "Views", ()) or ())


def _finite(value: Any, noun: str, *, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingHatchStateError(
            f"Drawing hatch {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeDrawingHatchStateError(
            f"Drawing hatch {noun} is outside the supported range."
        )
    return round(result, 12)


def _color(value: Any) -> dict[str, float]:
    try:
        channels = tuple(value)
    except TypeError as exc:
        raise NativeDrawingHatchStateError(
            "Drawing hatch color is malformed."
        ) from exc
    if len(channels) < 3:
        raise NativeDrawingHatchStateError("Drawing hatch color is incomplete.")
    return {
        name: _finite(channel, f"color {name}", minimum=0.0, maximum=1.0)
        for name, channel in zip(("red", "green", "blue"), channels[:3], strict=True)
    }


def _offset(value: Any) -> dict[str, float]:
    return {
        "x_mm": _finite(
            getattr(value, "x", None), "offset X", minimum=-1_000_000.0, maximum=1_000_000.0
        ),
        "y_mm": _finite(
            getattr(value, "y", None), "offset Y", minimum=-1_000_000.0, maximum=1_000_000.0
        ),
    }


def _source(hatch: Any) -> tuple[Any, tuple[str, ...]]:
    raw = getattr(hatch, "Source", None)
    if not isinstance(raw, tuple) or len(raw) != 2 or raw[0] is None:
        raise NativeDrawingHatchStateError("Drawing hatch source is malformed.")
    view, raw_faces = raw
    faces = (
        (str(raw_faces),)
        if isinstance(raw_faces, str)
        else tuple(str(value or "") for value in tuple(raw_faces or ()))
    )
    if (
        not 1 <= len(faces) <= MAX_DRAWING_HATCH_FACES
        or len(faces) != len(set(faces))
        or any(_FACE.fullmatch(value) is None for value in faces)
        or getattr(view, "Document", None) is not getattr(hatch, "Document", None)
        or not _derived(view, "TechDraw::DrawViewPart")
    ):
        raise NativeDrawingHatchStateError(
            "Drawing hatch does not have unique live projected FaceN sources."
        )
    return view, faces


def _artifact(
    hatch: Any,
    *,
    source_property: str,
    included_property: str,
    maximum_bytes: int,
    allowed_suffixes: frozenset[str],
) -> dict[str, Any]:
    source = str(getattr(hatch, source_property, "") or "")
    file_name = Path(source).name
    if (
        not file_name
        or len(file_name) > 255
        or Path(file_name).suffix.casefold() not in allowed_suffixes
    ):
        raise NativeDrawingHatchStateError(
            "Drawing hatch pattern file identity is invalid."
        )
    try:
        content = inspect_native_input_file(
            str(getattr(hatch, included_property, "") or ""),
            maximum_bytes=maximum_bytes,
        )
    except NativeInputError as exc:
        raise NativeDrawingHatchStateError(str(exc)) from exc
    if content.get("configured") is not True:
        raise NativeDrawingHatchStateError(
            "Drawing hatch has no embedded pattern content."
        )
    return {
        "file_name": file_name,
        "size_bytes": int(content["size_bytes"]),
        "sha256": str(content["sha256"]),
    }


def _messages(hatch: Any) -> list[str]:
    result = []
    for raw in tuple(getattr(hatch, "State", ()) or ()):
        message = str(raw or "").strip()
        if message:
            result.append(message[:256])
        if len(result) >= 16:
            break
    return result


def drawing_hatch_state(hatch: Any) -> dict[str, Any]:
    """Return one durable hatch without exposing any host filesystem path."""

    if not is_drawing_hatch(hatch):
        raise TypeError("hatch must be a TechDraw Drawing hatch")
    document = getattr(hatch, "Document", None)
    object_name = str(getattr(hatch, "Name", "") or "")
    if document is None or _OBJECT_NAME.fullmatch(object_name) is None:
        raise NativeDrawingHatchStateError("Drawing hatch is not a live object.")
    view, faces = _source(hatch)
    page = view.findParentPage()
    page_name = str(getattr(page, "Name", "") or "") if page else ""
    view_name = str(getattr(view, "Name", "") or "")
    if (
        not page_name
        or page.Document is not document
        or not drawing_hatch_view_belongs_to_page(view, page)
    ):
        raise NativeDrawingHatchStateError(
            "Drawing hatch source view is not attached to a live page."
        )
    geometric = _derived(hatch, "TechDraw::DrawGeomHatch")
    if geometric:
        artifact = _artifact(
            hatch,
            source_property="FilePattern",
            included_property="PatIncluded",
            maximum_bytes=MAX_DRAWING_PAT_PATTERN_BYTES,
            allowed_suffixes=frozenset({".pat"}),
        )
        pattern_name = str(getattr(hatch, "NamePattern", "") or "")
        if not 1 <= len(pattern_name) <= 128:
            raise NativeDrawingHatchStateError(
                "Drawing geometric hatch pattern name is invalid."
            )
        style = {
            "scale": _finite(
                getattr(hatch, "ScalePattern", None),
                "scale",
                minimum=1.0e-12,
                maximum=1000.0,
            ),
            "rotation_degrees": _finite(
                getattr(hatch, "PatternRotation", None),
                "rotation",
                minimum=-360.0,
                maximum=360.0,
            ),
            "offset_mm": _offset(getattr(hatch, "PatternOffset", None)),
            "line_width_mm": _finite(
                getattr(hatch.ViewObject, "WeightPattern", None),
                "line width",
                minimum=0.0,
                maximum=100.0,
            ),
            "color_rgb": _color(getattr(hatch.ViewObject, "ColorPattern", None)),
        }
        pattern = {**artifact, "pattern_name": pattern_name}
        kind = "geometric"
    else:
        artifact = _artifact(
            hatch,
            source_property="HatchPattern",
            included_property="SvgIncluded",
            maximum_bytes=MAX_DRAWING_IMAGE_PATTERN_BYTES,
            allowed_suffixes=_IMAGE_SUFFIXES,
        )
        suffix = Path(artifact["file_name"]).suffix.casefold()
        style = {
            "scale": _finite(
                getattr(hatch.ViewObject, "HatchScale", None),
                "scale",
                minimum=1.0e-12,
                maximum=1000.0,
            ),
            "rotation_degrees": _finite(
                getattr(hatch.ViewObject, "HatchRotation", None),
                "rotation",
                minimum=-360.0,
                maximum=360.0,
            ),
            "offset_mm": _offset(getattr(hatch.ViewObject, "HatchOffset", None)),
            "color_rgb": _color(getattr(hatch.ViewObject, "HatchColor", None)),
        }
        pattern = {
            **artifact,
            "pattern_kind": "svg" if suffix == ".svg" else "bitmap",
        }
        kind = "image"
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    timeline_usable = bool(not callable(checker) or checker(hatch))
    messages = _messages(hatch)
    valid = bool(hatch.isValid())
    exact = {
        "object_name": object_name,
        "label": str(getattr(hatch, "Label", "") or "")[:160],
        "type_id": str(getattr(hatch, "TypeId", "") or ""),
        "kind": kind,
        "page_name": page_name,
        "source_view_name": view_name,
        "faces": list(faces),
        "pattern": pattern,
        "style": style,
        "timeline_usable": timeline_usable,
        "valid": valid,
    }
    return {
        **exact,
        "state_messages": messages,
        "hatch_state_sha256": _digest(exact),
    }


def drawing_hatch_inventory_state(view: Any) -> dict[str, Any]:
    """Return every exact hatch sourced from one projected view."""

    document = getattr(view, "Document", None)
    if document is None or not _derived(view, "TechDraw::DrawViewPart"):
        raise TypeError("view must be a live TechDraw::DrawViewPart")
    hatches = tuple(
        obj
        for obj in tuple(document.Objects)
        if is_drawing_hatch(obj)
        and isinstance(getattr(obj, "Source", None), tuple)
        and getattr(obj, "Source", (None,))[0] is view
    )
    if len(hatches) > MAX_DRAWING_HATCHES_PER_VIEW:
        raise NativeDrawingHatchStateError(
            "Drawing view exceeds the supported 64-hatch inventory."
        )
    items = [drawing_hatch_state(hatch) for hatch in hatches]
    exact = {
        "view_name": str(view.Name),
        "hatch_count": len(items),
        "hatches": items,
    }
    return {**exact, "inventory_state_sha256": _digest(exact)}


def normalize_drawing_hatch_plan(raw: Any, *, kind: str) -> dict[str, Any]:
    """Validate the small C++ preflight result at the Python boundary."""

    expected = {
        "view_name",
        "page_name",
        "faces",
        "pattern_file_name",
        "style",
    }
    if kind == "image":
        expected.add("pattern_kind")
    elif kind == "geometric":
        expected.add("pattern_name")
    else:
        raise ValueError("kind must be image or geometric")
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise NativeDrawingHatchStateError(
            "TechDraw returned a malformed hatch plan."
        )
    faces = tuple(str(value or "") for value in tuple(raw["faces"] or ()))
    if (
        not 1 <= len(faces) <= MAX_DRAWING_HATCH_FACES
        or len(faces) != len(set(faces))
        or any(_FACE.fullmatch(value) is None for value in faces)
    ):
        raise NativeDrawingHatchStateError(
            "TechDraw returned malformed hatch faces."
        )
    style_raw = raw["style"]
    style_fields = {"scale", "rotation_degrees", "offset_mm", "color_rgb"}
    if kind == "geometric":
        style_fields.add("line_width_mm")
    if not isinstance(style_raw, Mapping) or set(style_raw) != style_fields:
        raise NativeDrawingHatchStateError(
            "TechDraw returned malformed hatch style."
        )
    offset = style_raw["offset_mm"]
    color = style_raw["color_rgb"]
    if not isinstance(offset, Mapping) or set(offset) != {"x_mm", "y_mm"}:
        raise NativeDrawingHatchStateError(
            "TechDraw returned malformed hatch offset."
        )
    if not isinstance(color, Mapping) or set(color) != {"red", "green", "blue"}:
        raise NativeDrawingHatchStateError(
            "TechDraw returned malformed hatch color."
        )
    style = {
        "scale": _finite(style_raw["scale"], "scale", minimum=1.0e-12, maximum=1000.0),
        "rotation_degrees": _finite(
            style_raw["rotation_degrees"], "rotation", minimum=-360.0, maximum=360.0
        ),
        "offset_mm": {
            "x_mm": _finite(offset["x_mm"], "offset X", minimum=-1_000_000.0, maximum=1_000_000.0),
            "y_mm": _finite(offset["y_mm"], "offset Y", minimum=-1_000_000.0, maximum=1_000_000.0),
        },
        "color_rgb": {
            name: _finite(color[name], f"color {name}", minimum=0.0, maximum=1.0)
            for name in ("red", "green", "blue")
        },
    }
    if kind == "geometric":
        style["line_width_mm"] = _finite(
            style_raw["line_width_mm"], "line width", minimum=0.0, maximum=100.0
        )
    file_name = str(raw["pattern_file_name"] or "")
    view_name = str(raw["view_name"] or "")
    page_name = str(raw["page_name"] or "")
    if (
        not file_name
        or len(file_name) > 255
        or _OBJECT_NAME.fullmatch(view_name) is None
        or _OBJECT_NAME.fullmatch(page_name) is None
    ):
        raise NativeDrawingHatchStateError(
            "TechDraw returned invalid hatch identities."
        )
    result = {
        "view_name": view_name,
        "page_name": page_name,
        "faces": list(faces),
        "pattern_file_name": file_name,
        "style": style,
    }
    discriminator = str(raw["pattern_kind" if kind == "image" else "pattern_name"] or "")
    if not discriminator or len(discriminator) > 128:
        raise NativeDrawingHatchStateError(
            "TechDraw returned an invalid hatch pattern discriminator."
        )
    result["pattern_kind" if kind == "image" else "pattern_name"] = discriminator
    return result
