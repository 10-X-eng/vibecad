# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Text geometry creation in the human-opened Sketch."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryValues import (
    MIN_SKETCH_GEOMETRY_LENGTH_MM,
    same_sketch_point,
    sketch_point_2d,
)
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    sketch_geometry_result,
    verify_sketch_append,
)
from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)
from VibeCADNativeTargets import object_identity


MAX_NATIVE_SKETCH_TEXT_CHARACTERS = 64
MAX_NATIVE_SKETCH_TEXT_GEOMETRY = 512
_FONT_SUFFIXES = frozenset({".otf", ".ttc", ".ttf"})
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "text",
        "font_name",
        "handle_start_mm",
        "handle_end_mm",
        "sizing_mode",
    }
)


@dataclass(frozen=True, slots=True)
class SketchTextSpec:
    target: ActiveSketchTargetSpec
    text: str
    font_name: str
    handle_start_mm: tuple[float, float]
    handle_end_mm: tuple[float, float]
    sizing_mode: str


@dataclass(frozen=True, slots=True)
class FontFileIdentity:
    path: str
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class PreparedSketchText:
    insertion: PreparedSketchInsertion
    spec: SketchTextSpec
    resolved_font_name: str
    font_file: FontFileIdentity


def _required_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise NativeSketchError(
            f"Sketch Text {field} must contain 1 through {maximum} characters."
        )
    return value


def prepare_sketch_text(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchTextSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError("A Sketch Text definition has incorrect fields.")
    text = _required_text(
        value["text"],
        field="text",
        maximum=MAX_NATIVE_SKETCH_TEXT_CHARACTERS,
    )
    if not text.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in text
    ):
        raise NativeSketchError(
            "Sketch Text text must contain visible single-line characters."
        )
    font_name = _required_text(value["font_name"], field="font_name", maximum=128)
    if font_name != font_name.strip():
        raise NativeSketchError("Sketch Text font_name must not have outer whitespace.")
    start = sketch_point_2d(value["handle_start_mm"], "Text handle_start_mm")
    end = sketch_point_2d(value["handle_end_mm"], "Text handle_end_mm")
    if (
        (end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2
        <= MIN_SKETCH_GEOMETRY_LENGTH_MM**2
    ):
        raise NativeSketchError("Sketch Text handle endpoints must be distinct.")
    sizing_mode = value["sizing_mode"]
    if sizing_mode not in {"width", "height"}:
        raise NativeSketchError("Sketch Text sizing_mode must be width or height.")
    return SketchTextSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        text,
        font_name,
        start,
        end,
        sizing_mode,
    )


def _font_roots() -> tuple[Path, ...]:
    import FreeCAD as App

    roots = [Path(str(App.getResourceDir())) / "Mod/TechDraw/Resources/fonts"]
    if sys.platform.startswith("win"):
        roots.append(Path("C:/Windows/Fonts"))
    elif sys.platform == "darwin":
        roots.extend(
            (
                Path("/System/Library/Fonts"),
                Path("/Library/Fonts"),
                Path.home() / "Library/Fonts",
            )
        )
    else:
        roots.extend(
            (
                Path("/usr/share/fonts"),
                Path("/usr/local/share/fonts"),
                Path.home() / ".fonts",
            )
        )
    return tuple(roots)


def _available_font_files() -> dict[str, Path]:
    catalog: dict[str, Path] = {}
    for root in _font_roots():
        try:
            candidates = sorted(
                (
                    path
                    for path in root.rglob("*")
                    if path.suffix.lower() in _FONT_SUFFIXES and path.is_file()
                ),
                key=lambda path: str(path).casefold(),
            )
        except OSError:
            continue
        for path in candidates:
            catalog[path.stem] = path
    return catalog


def _font_identity(path: Path) -> FontFileIdentity:
    try:
        resolved = path.resolve(strict=True)
        details = resolved.stat()
    except OSError as exc:
        raise NativeSketchError("The selected Sketch Text font is unavailable.") from exc
    if (
        resolved.suffix.lower() not in _FONT_SUFFIXES
        or not stat.S_ISREG(details.st_mode)
        or not os.access(resolved, os.R_OK)
    ):
        raise NativeSketchError("The selected Sketch Text font is not a readable font file.")
    return FontFileIdentity(
        str(resolved),
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(details.st_mtime_ns),
    )


def _resolve_font(requested: str) -> tuple[str, FontFileIdentity]:
    catalog = _available_font_files()
    if not catalog:
        raise NativeSketchError("No Sketch Text fonts are installed.")
    if requested.casefold() == "default":
        folded = {name.casefold(): name for name in catalog}
        for preferred in ("osifont-lgpl3fe", "DejaVu Sans", "Arial"):
            canonical = folded.get(preferred.casefold())
            if canonical is not None:
                return canonical, _font_identity(catalog[canonical])
        canonical = min(catalog, key=lambda value: (value.casefold(), value))
        return canonical, _font_identity(catalog[canonical])
    if requested in catalog:
        return requested, _font_identity(catalog[requested])
    matches = [name for name in catalog if name.casefold() == requested.casefold()]
    if len(matches) == 1:
        canonical = matches[0]
        return canonical, _font_identity(catalog[canonical])
    raise NativeSketchError(
        f"Sketch Text font_name {requested!r} is not installed; use 'default' or an installed font name."
    )


def preflight_sketch_text(
    context: NativeRuntimeContext,
    spec: SketchTextSpec,
) -> PreparedSketchText:
    if not isinstance(spec, SketchTextSpec):
        raise TypeError("spec must be a SketchTextSpec")
    insertion = preflight_sketch_insertion(context, spec.target)
    font_name, font_file = _resolve_font(spec.font_name)
    return PreparedSketchText(insertion, spec, font_name, font_file)


def _require_unchanged_font(expected: FontFileIdentity) -> None:
    if _font_identity(Path(expected.path)) != expected:
        raise NativeSketchError("The selected Sketch Text font changed after preflight.")


def _exact_index(raw: Any, expected: int, label: str) -> int:
    result = int(raw)
    if result != expected:
        raise NativeSketchError(f"Sketcher returned an unexpected Text {label} index.")
    return result


def _constraint_elements(constraint: Any) -> tuple[tuple[int, int], ...]:
    raw_elements = getattr(constraint, "Elements", None)
    if not isinstance(raw_elements, (list, tuple)):
        raise NativeSketchError("Sketch Text constraint elements are unavailable.")
    try:
        return tuple((int(value[0]), int(value[1])) for value in raw_elements)
    except (IndexError, TypeError, ValueError) as exc:
        raise NativeSketchError("Sketch Text constraint elements are malformed.") from exc


def _records_digest(records: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        records,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_constraint(sketch: Any, index: int) -> Any:
    try:
        constraint = sketch.Constraints[index]
    except (IndexError, KeyError, TypeError) as exc:
        raise NativeSketchError("Sketch Text constraint is unavailable.") from exc
    if str(getattr(constraint, "Type", "")) != "Text":
        raise NativeSketchError("Sketch Text constraint type changed.")
    return constraint


def create_sketch_text(
    document: Any,
    prepared: PreparedSketchText,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchText):
        raise TypeError("prepared must be a PreparedSketchText")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after Text preflight",
    )
    _require_unchanged_font(prepared.font_file)
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count

    import FreeCAD as App
    import Part
    import Sketcher

    handle_index = _exact_index(
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(*spec.handle_start_mm, 0.0),
                App.Vector(*spec.handle_end_mm, 0.0),
            ),
            True,
        ),
        base_geometry,
        "handle",
    )
    constraint_index = _exact_index(
        sketch.addConstraint(
            Sketcher.Constraint(
                "Text",
                [handle_index, 0],
                spec.text,
                prepared.font_file.path,
                spec.sizing_mode == "height",
            )
        ),
        base_constraint,
        "constraint",
    )
    sketch.setTextAndFont(
        constraint_index,
        spec.text,
        prepared.font_file.path,
        spec.sizing_mode == "height",
        False,
    )
    geometry_count = int(sketch.GeometryCount)
    constraint_count = int(sketch.ConstraintCount)
    generated_count = geometry_count - base_geometry - 1
    if not 1 <= generated_count <= MAX_NATIVE_SKETCH_TEXT_GEOMETRY:
        raise NativeSketchError(
            "Sketch Text must generate between 1 and "
            f"{MAX_NATIVE_SKETCH_TEXT_GEOMETRY} curve elements."
        )
    if constraint_count != base_constraint + 1:
        raise NativeSketchError("Sketch Text changed an unexpected constraint count.")
    generated_indices = tuple(range(base_geometry + 1, geometry_count))
    expected_elements = ((handle_index, 0),) + tuple(
        (index, 0) for index in generated_indices
    )
    constraint = _text_constraint(sketch, constraint_index)
    if _constraint_elements(constraint) != expected_elements:
        raise NativeSketchError("Sketch Text generated an unexpected element group.")
    records = [serialize_sketch_geometry(sketch, index) for index in generated_indices]
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "handle_index": handle_index,
            "constraint_index": constraint_index,
            "generated_indices": generated_indices,
            "generated_sha256": _records_digest(records),
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _verify_constraint_metadata(
    constraint: Any,
    prepared: PreparedSketchText,
    expected_elements: tuple[tuple[int, int], ...],
) -> None:
    spec = prepared.spec
    if (
        _constraint_elements(constraint) != expected_elements
        or str(getattr(constraint, "Text", "")) != spec.text
        or str(getattr(constraint, "Font", "")) != prepared.resolved_font_name
        or bool(getattr(constraint, "IsTextHeight", True))
        != (spec.sizing_mode == "height")
        or not bool(getattr(constraint, "Driving", False))
        or not bool(getattr(constraint, "IsActive", False))
        or bool(getattr(constraint, "InVirtualSpace", False))
    ):
        raise NativeSketchError("Sketch Text constraint metadata changed.")


def verify_sketch_text(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSketchText = draft.value["prepared"]
    spec = prepared.spec
    base_geometry = spec.target.expected_geometry_count
    base_constraint = spec.target.expected_constraint_count
    handle_index = int(draft.value["handle_index"])
    constraint_index = int(draft.value["constraint_index"])
    generated_indices = tuple(int(value) for value in draft.value["generated_indices"])
    if handle_index != base_geometry or constraint_index != base_constraint:
        raise NativeSketchError("Sketch Text append indices changed.")
    if generated_indices != tuple(
        range(base_geometry + 1, base_geometry + 1 + len(generated_indices))
    ):
        raise NativeSketchError("Sketch Text curve indices changed.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=1 + len(generated_indices),
        constraints_added=1,
    )
    handle = serialize_sketch_geometry(sketch, handle_index)
    if (
        handle.get("type_id") != "Part::GeomLineSegment"
        or handle.get("kind") != "line"
        or handle.get("construction") is not True
        or bool(handle.get("blocked"))
        or not same_sketch_point(handle.get("start_mm"), spec.handle_start_mm)
        or not same_sketch_point(handle.get("end_mm"), spec.handle_end_mm)
    ):
        raise NativeSketchError("Sketch Text handle differs from its definition.")

    generated = [
        serialize_sketch_geometry(sketch, index) for index in generated_indices
    ]
    if any(
        record.get("index") != index
        or not str(record.get("type_id", "")).startswith("Part::Geom")
        or bool(record.get("construction"))
        or bool(record.get("blocked"))
        for index, record in zip(generated_indices, generated, strict=True)
    ):
        raise NativeSketchError("Sketch Text generated curve state changed.")
    digest = _records_digest(generated)
    if digest != draft.value["generated_sha256"]:
        raise NativeSketchError("Sketch Text generated curves changed after recompute.")

    expected_elements = ((handle_index, 0),) + tuple(
        (index, 0) for index in generated_indices
    )
    constraint = _text_constraint(sketch, constraint_index)
    _verify_constraint_metadata(constraint, prepared, expected_elements)
    constraint_record = serialize_sketch_constraint(sketch, constraint_index)
    if (
        constraint_record.get("text") != spec.text
        or constraint_record.get("font_name") != prepared.resolved_font_name
        or constraint_record.get("sizing_mode") != spec.sizing_mode
        or constraint_record.get("element_count") != len(expected_elements)
    ):
        raise NativeSketchError("Sketch Text serialized constraint state changed.")

    kind_counts = dict(sorted(Counter(record["kind"] for record in generated).items()))
    return sketch_geometry_result(
        sketch,
        {
            "text": spec.text,
            "font_name": prepared.resolved_font_name,
            "sizing_mode": spec.sizing_mode,
            "handle": handle,
            "text_constraint": {
                "index": constraint_index,
                "type": "Text",
                "handle_index": handle_index,
                "element_count": len(expected_elements),
            },
            "generated_geometry": {
                "count": len(generated_indices),
                "first_index": generated_indices[0],
                "last_index": generated_indices[-1],
                "kind_counts": kind_counts,
                "sha256": digest,
                "construction": False,
            },
        },
    )
