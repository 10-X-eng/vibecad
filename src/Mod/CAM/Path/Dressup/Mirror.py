# SPDX-License-Identifier: LGPL-2.1-or-later

"""Headless CAM Mirror path generation shared by human and Native mode."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import Constants
import Part
import Path
import Path.Base.Util as PathUtil
import Path.Dressup.Utils as PathDressup
import PathScripts.PathUtils as PathUtils
from PySide.QtCore import QT_TRANSLATE_NOOP


MAX_MIRROR_INPUT_COMMANDS = 50_000
MAX_MIRROR_OUTPUT_COMMANDS = 100_000
MAX_MIRROR_OFFSET_MM = 1_000_000.0
MIRROR_AXES = frozenset(("X", "Y", "XY"))
_EPSILON = 1.0e-9


@dataclass(frozen=True, slots=True)
class MirrorDefinition:
    axis: str
    offset_mm: tuple[float, float, float]
    keep_base_path: bool
    center_model: Any | None = None
    reference_object: Any | None = None
    reference_subelement: str = ""


def _finite(value: Any, noun: str) -> float:
    result = float(getattr(value, "Value", value))
    if not math.isfinite(result):
        raise ValueError(f"{noun} must be finite")
    return result


def _vector(value: Any, noun: str) -> tuple[float, float, float]:
    try:
        components = tuple(value)
    except TypeError:
        components = tuple(getattr(value, name) for name in ("x", "y", "z"))
    if len(components) != 3:
        raise ValueError(f"{noun} must contain exactly three coordinates")
    result = tuple(_finite(component, f"{noun} coordinate") for component in components)
    if any(abs(component) > MAX_MIRROR_OFFSET_MM for component in result):
        raise ValueError(
            f"{noun} coordinates must be within {MAX_MIRROR_OFFSET_MM:g} mm"
        )
    return result


def _copy_command(command: Any) -> Any:
    return Path.Command(str(command.Name), dict(command.Parameters))


def _path_with_job_center(owner: Any, commands=()) -> Any:
    result = Path.Path(list(commands)) if commands else Path.Path()
    job = None
    for candidate in (owner, getattr(owner, "Base", None)):
        if candidate is None:
            continue
        try:
            job = PathUtils.findParentJob(candidate) or PathUtil.timelineParentJob(candidate)
        except (AttributeError, TypeError):
            job = None
        if job is not None:
            break
    if job is not None:
        result.Center = job.Path.Center
    return result


def _global_shape(source: Any, subelement: str = "") -> Any:
    if source is None or getattr(source, "Document", None) is None:
        raise ValueError("Mirror geometry source must be one live document object")
    try:
        shape = (
            Part.getShape(
                source,
                subelement,
                needSubElement=True,
                transform=True,
            )
            if subelement
            else Part.getShape(source, transform=True)
        )
    except Exception as exc:
        noun = f"{source.Name}.{subelement}" if subelement else str(source.Name)
        raise ValueError(f"Mirror geometry {noun} could not be resolved") from exc
    if shape is None or shape.isNull() or not shape.isValid():
        raise ValueError("Mirror geometry must have one valid current shape")
    return shape


def _center_offsets(model: Any, axis: str) -> tuple[float, float]:
    bounds = _global_shape(model).BoundBox
    values = (bounds.XMin, bounds.XMax, bounds.YMin, bounds.YMax)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("Mirror model bounds must be finite")
    return (
        float(bounds.XMin + bounds.XMax) if axis in ("Y", "XY") else 0.0,
        float(bounds.YMin + bounds.YMax) if axis in ("X", "XY") else 0.0,
    )


def _reference_axis_and_offset(
    source: Any,
    subelement: str,
) -> tuple[str, float, float]:
    name = str(subelement or "").strip()
    if not name or not name.startswith(("Edge", "Face")):
        raise ValueError("Mirror reference must name one exact EdgeN or FaceN subelement")
    bounds = _global_shape(source, name).BoundBox
    values = (
        bounds.XMin,
        bounds.XLength,
        bounds.YMin,
        bounds.YLength,
    )
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("Mirror reference bounds must be finite")
    fixed_x = abs(float(bounds.XLength)) <= _EPSILON
    fixed_y = abs(float(bounds.YLength)) <= _EPSILON
    if fixed_x == fixed_y:
        raise ValueError(
            "Mirror reference must be axis-aligned in XY with exactly one fixed X or Y coordinate"
        )
    if fixed_x:
        return "Y", 2.0 * float(bounds.XMin), 0.0
    return "X", 0.0, 2.0 * float(bounds.YMin)


def normalize_definition(definition: MirrorDefinition) -> MirrorDefinition:
    if not isinstance(definition, MirrorDefinition):
        raise TypeError("Mirror definition must be a MirrorDefinition")
    axis = str(definition.axis or "")
    reference_name = str(definition.reference_subelement or "").strip()
    has_reference = definition.reference_object is not None or bool(reference_name)
    if has_reference:
        if definition.reference_object is None or not reference_name:
            raise ValueError("Mirror reference requires both object and subelement")
        if definition.center_model is not None:
            raise ValueError("Mirror reference and model-center placement are mutually exclusive")
        axis = "Reference"
    elif axis not in MIRROR_AXES:
        raise ValueError("Mirror axis must be X, Y, or XY")
    if not isinstance(definition.keep_base_path, bool):
        raise TypeError("Mirror keep_base_path must be a boolean")
    return MirrorDefinition(
        axis=axis,
        offset_mm=_vector(definition.offset_mm, "Mirror offset"),
        keep_base_path=definition.keep_base_path,
        center_model=definition.center_model,
        reference_object=definition.reference_object,
        reference_subelement=reference_name,
    )


def generatePathWithMetadata(base: Any, definition: MirrorDefinition):
    """Generate one mirrored path without document or source-path mutation."""

    normalized = normalize_definition(definition)
    if base is None or not base.isDerivedFrom("Path::Feature"):
        raise ValueError("Mirror base must be one Path feature")
    placed = PathUtils.getPathWithPlacement(base)
    source = tuple(getattr(placed, "Commands", ()) or ())
    if not source:
        raise ValueError("Mirror base path is empty")
    if len(source) > MAX_MIRROR_INPUT_COMMANDS:
        raise ValueError(
            f"Mirror base has {len(source)} commands; its interactive limit is "
            f"{MAX_MIRROR_INPUT_COMMANDS}"
        )
    output_count = len(source) * (2 if normalized.keep_base_path else 1)
    if output_count > MAX_MIRROR_OUTPUT_COMMANDS:
        raise ValueError(
            f"Mirror would generate {output_count} commands; the safety limit is "
            f"{MAX_MIRROR_OUTPUT_COMMANDS}"
        )

    offset_x, offset_y, offset_z = normalized.offset_mm
    if normalized.axis == "Reference":
        axis, reference_x, reference_y = _reference_axis_and_offset(
            normalized.reference_object,
            normalized.reference_subelement,
        )
        offset_x += reference_x
        offset_y += reference_y
    else:
        axis = normalized.axis
        if normalized.center_model is not None:
            center_x, center_y = _center_offsets(normalized.center_model, axis)
            offset_x += center_x
            offset_y += center_y

    transformed = []
    move_count = 0
    arc_direction_swap_count = 0
    for original in source:
        command = _copy_command(original)
        if command.Name in Constants.GCODE_MOVE_ALL:
            move_count += 1
            if command.x is not None:
                if axis in ("Y", "XY"):
                    command.x = -command.x
                command.x += offset_x
            if command.y is not None:
                if axis in ("X", "XY"):
                    command.y = -command.y
                command.y += offset_y
            if command.z is not None:
                command.z += offset_z
            if command.i is not None and axis in ("Y", "XY"):
                command.i = -command.i
            if command.j is not None and axis in ("X", "XY"):
                command.j = -command.j
            if axis != "XY" and command.Name in Constants.GCODE_MOVE_ARC:
                command.Name = (
                    "G2" if command.Name in Constants.GCODE_MOVE_CCW else "G3"
                )
                arc_direction_swap_count += 1
        transformed.append(command)

    commands = (
        [_copy_command(command) for command in source] + transformed
        if normalized.keep_base_path
        else transformed
    )
    path = _path_with_job_center(base, commands)
    return path, {
        "source_command_count": len(source),
        "mirrored_command_count": len(transformed),
        "output_command_count": len(commands),
        "move_command_count": move_count,
        "arc_direction_swap_count": arc_direction_swap_count,
        "resolved_axis": axis,
        "resolved_offset_mm": (offset_x, offset_y, offset_z),
    }


class ObjectDressup:
    """Parametric Mirror proxy backed by detached task-free generation."""

    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base path for mirroring"),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "MirrorAxis",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The mirroring axis"),
        )
        obj.addProperty(
            "App::PropertyVectorDistance",
            "Offset",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Offset for the mirroring axis "),
        )
        obj.addProperty(
            "App::PropertyBool",
            "CenterModel",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Mirroring at the center of base model"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "KeepBasePath",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Add path from base operation"),
        )
        obj.addProperty(
            "App::PropertyLinkSubGlobal",
            "Reference",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property", "Define the reference edge or plane for mirroring"
            ),
        )
        obj.addProperty(
            "App::PropertyLink",
            "CenterModelReference",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Exact model used for durable center-based mirroring",
            ),
        )
        obj.MirrorAxis = ("X", "Y", "XY", "Reference", "None")
        obj.Proxy = self
        self.setEditorModes(obj)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDocumentRestored(self, obj):
        if not hasattr(obj, "CenterModelReference"):
            obj.addProperty(
                "App::PropertyLink",
                "CenterModelReference",
                "Path",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Exact model used for durable center-based mirroring",
                ),
            )
        self.setEditorModes(obj)

    def onChanged(self, obj, prop):
        if prop == "MirrorAxis":
            self.setEditorModes(obj)
        elif prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    @staticmethod
    def setEditorModes(obj):
        obj.setEditorMode(
            "CenterModel",
            2 if obj.MirrorAxis in ("None", "Reference") else 0,
        )
        obj.setEditorMode("KeepBasePath", 2 if obj.MirrorAxis == "None" else 0)
        obj.setEditorMode("Reference", 0 if obj.MirrorAxis == "Reference" else 2)
        obj.setEditorMode("CenterModelReference", 2)

    @staticmethod
    def _implicit_model(obj):
        base_op = PathDressup.baseOp(obj)
        candidates = getattr(base_op, "Base", ())
        if (
            isinstance(candidates, (list, tuple))
            and candidates
            and isinstance(candidates[0], (list, tuple))
            and candidates[0]
            and candidates[0][0].isDerivedFrom("Part::Feature")
        ):
            return candidates[0][0]
        job = PathUtils.findParentJob(obj)
        models = tuple(getattr(getattr(job, "Model", None), "Group", ()) or ())
        if not models:
            raise ValueError("Mirror CenterModel requires one Job model")
        try:
            return job.Proxy.baseObject(job, models[0])
        except Exception:
            return models[0]

    def execute(self, obj):
        if not PathUtil.activeForOp(obj):
            obj.Path = _path_with_job_center(obj)
            return
        base = getattr(obj, "Base", None)
        if (
            base is None
            or not base.isDerivedFrom("Path::Feature")
            or not tuple(getattr(getattr(base, "Path", None), "Commands", ()) or ())
        ):
            obj.Path = _path_with_job_center(obj)
            Path.Log.warning("Mirror dress-up requires one nonempty Path base")
            return
        if obj.MirrorAxis == "None":
            placed = PathUtils.getPathWithPlacement(base)
            obj.Path = _path_with_job_center(base, tuple(placed.Commands or ()))
            return

        reference_object = None
        reference_name = ""
        if obj.MirrorAxis == "Reference":
            reference = obj.Reference
            if reference and len(reference) == 2:
                reference_object = reference[0]
                names = tuple(reference[1] or ())
                if len(names) == 1:
                    reference_name = str(names[0])
        try:
            path, metadata = generatePathWithMetadata(
                base,
                MirrorDefinition(
                    axis=str(obj.MirrorAxis),
                    offset_mm=(obj.Offset.x, obj.Offset.y, obj.Offset.z),
                    keep_base_path=bool(obj.KeepBasePath),
                    center_model=(
                        (
                            obj.CenterModelReference
                            if obj.CenterModelReference is not None
                            else self._implicit_model(obj)
                        )
                        if obj.CenterModel and obj.MirrorAxis != "Reference"
                        else None
                    ),
                    reference_object=reference_object,
                    reference_subelement=reference_name,
                ),
            )
        except (ArithmeticError, AttributeError, TypeError, ValueError) as exc:
            obj.Path = _path_with_job_center(obj)
            Path.Log.warning(f"Mirror dress-up could not generate its path: {exc}")
            return
        self.lastGenerationStats = metadata
        obj.Path = path
