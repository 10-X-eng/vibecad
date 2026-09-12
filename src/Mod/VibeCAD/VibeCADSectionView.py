# SPDX-License-Identifier: LGPL-2.1-or-later

"""SolidWorks/Fusion-style 3D section view for VibeCAD.

Cuts the active 3D view with a Front (XZ), Top (XY), or Right (YZ) plane
through the model. Offset slides the plane along its normal; Flip keeps the
opposite half. The GPU clip hides the discarded half without changing model
geometry. Because displayed solids are tessellated shells, a clip plane alone
leaves an open surface. This module also builds the planar cut faces and
draws them as a filled, hatched cap so the remaining half reads as a solid.

The native ``VibeCAD_SectionView`` command owns the View-ribbon action. This
module inspects and applies the active Inventor view's clipping plane; when no
3D view is open it reports inactive and does not create a clip.

The module imports safely outside FreeCAD (guarded imports) so tooling such
as linters and test collectors can load it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import atan2, cos, degrees, floor, hypot, radians, sin
from typing import Any, Iterable, Sequence

try:
    import FreeCAD as App
except ImportError:  # pragma: no cover - only outside FreeCAD (tooling/tests)
    App = None  # type: ignore[assignment]


SECTION_PLANES = ("front", "top", "right")
# Z-up, matching VibeCAD's Top/Front/Right cameras: Top looks along -Z at XY,
# Front looks along -Y at XZ, Right looks along -X at YZ.
_PLANE_NORMALS = {
    "front": (0.0, 1.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "right": (1.0, 0.0, 0.0),
}
_OVERLAY_NAME = "VibeCADSectionPlaneOverlay"
_CAP_OVERLAY_NAME = "VibeCADSectionCapOverlay"
_DRAGGER_NAME = "VibeCADSectionDragger"
SECTION_CAP_OFFSET = 0.05
_HATCH_ANGLE_DEG = 45.0
_DEFAULT_HATCH_SPACING = 2.5
_PLANE_CS = {
    "front": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "top": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
}


@dataclass(frozen=True)
class SectionViewSettings:
    plane: str = "top"
    offset: float = 0.0
    flipped: bool = False
    show_plane: bool = True
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    show_handles: bool = True

    def __post_init__(self) -> None:
        plane = str(self.plane).strip().casefold()
        if plane not in _PLANE_NORMALS:
            raise ValueError("Section plane must be front, top, or right.")
        object.__setattr__(self, "plane", plane)
        object.__setattr__(self, "offset", float(self.offset))
        object.__setattr__(self, "flipped", bool(self.flipped))
        object.__setattr__(self, "show_plane", bool(self.show_plane))
        object.__setattr__(self, "yaw", float(self.yaw))
        object.__setattr__(self, "pitch", float(self.pitch))
        object.__setattr__(self, "roll", float(self.roll))
        object.__setattr__(self, "show_handles", bool(self.show_handles))


@dataclass(frozen=True)
class ModelBounds:
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            (self.xmin + self.xmax) / 2.0,
            (self.ymin + self.ymax) / 2.0,
            (self.zmin + self.zmax) / 2.0,
        )

    def axis_half_extent(self, plane: str) -> float:
        name = str(plane).strip().casefold()
        if name == "front":
            return abs(self.ymax - self.ymin) / 2.0
        if name == "top":
            return abs(self.zmax - self.zmin) / 2.0
        if name == "right":
            return abs(self.xmax - self.xmin) / 2.0
        raise ValueError("Section plane must be front, top, or right.")


@dataclass(frozen=True)
class SectionCapGeometry:
    """Filled cut faces, hatch strokes, and outlines in world coordinates."""

    triangles: tuple[
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ],
        ...,
    ] = ()
    hatch: tuple[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        ...,
    ] = ()
    outlines: tuple[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        ...,
    ] = ()

    def __bool__(self) -> bool:
        return bool(self.triangles or self.hatch or self.outlines)


_settings = SectionViewSettings()
_overlay_node: Any | None = None
_cap_node: Any | None = None
_dragger_node: Any | None = None
_dragger_view: Any | None = None
_dragger_document: Any | None = None
_section_document_observer: Any | None = None
_bounds_view: Any | None = None
_section_bounds: ModelBounds | None = None
_cap_worker: Any | None = None
_cap_dirty = False
_last_dragger_scale: float | None = None
_cap_document_observer: Any | None = None
_dragger_busy = False
_drag_start_settings: SectionViewSettings | None = None
_drag_start_origin: tuple[float, float, float] | None = None
_drag_start_axes: tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
] | None = None
_drag_start_rot_counts: tuple[int, int, int] | None = None
_triad_parts: dict[str, Any] | None = None
_selection_observer: Any | None = None
_poll_timer: Any | None = None
_last_polled_origin: tuple[float, float, float] | None = None
_last_polled_quat: tuple[float, float, float, float] | None = None
_stable_ticks = 0
_DRAGGER_NDC_SIZE = 0.03
_POLL_MS = 50
_STABLE_TICKS = 5
_pending_toggle_generation = 0


def reset_section_view_settings() -> SectionViewSettings:
    global _settings
    _settings = SectionViewSettings()
    return _settings


def current_section_view_settings() -> SectionViewSettings:
    return _settings


def _object_bound_box(obj: Any) -> Any | None:
    for source in (
        getattr(getattr(obj, "Shape", None), "BoundBox", None),
        getattr(obj, "BoundBox", None),
    ):
        box = source
        if box is None:
            continue
        is_valid = getattr(box, "isValid", None)
        try:
            if callable(is_valid) and not bool(is_valid()):
                continue
        except Exception:
            continue
        try:
            xmin = float(box.XMin)
            xmax = float(box.XMax)
            ymin = float(box.YMin)
            ymax = float(box.YMax)
            zmin = float(box.ZMin)
            zmax = float(box.ZMax)
        except Exception:
            continue
        if any(value != value for value in (xmin, xmax, ymin, ymax, zmin, zmax)):
            continue
        return box
    return None


def model_bounds(objects: Any) -> ModelBounds | None:
    """Return the combined BoundBox of ``objects``, or None if empty."""

    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    found = False
    for obj in tuple(objects or ()):
        box = _object_bound_box(obj)
        if box is None:
            continue
        found = True
        xmin = min(xmin, float(box.XMin))
        xmax = max(xmax, float(box.XMax))
        ymin = min(ymin, float(box.YMin))
        ymax = max(ymax, float(box.YMax))
        zmin = min(zmin, float(box.ZMin))
        zmax = max(zmax, float(box.ZMax))
    if not found:
        return None
    return ModelBounds(xmin, xmax, ymin, ymax, zmin, zmax)


def bounds_center(objects: Any) -> tuple[float, float, float] | None:
    """Return the combined BoundBox center of ``objects``, or None if empty."""

    bounds = model_bounds(objects)
    if bounds is None:
        return None
    return bounds.center


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _sub(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _rotate_vector(
    vector: tuple[float, float, float],
    axis: tuple[float, float, float],
    degrees: float,
) -> tuple[float, float, float]:
    if abs(float(degrees)) < 1.0e-12:
        return vector
    angle = radians(float(degrees))
    k = _unit(axis)
    cosine = cos(angle)
    sine = sin(angle)
    kdotv = _dot(k, vector)
    crossed = _cross(k, vector)
    scale = 1.0 - cosine
    return (
        vector[0] * cosine + crossed[0] * sine + k[0] * kdotv * scale,
        vector[1] * cosine + crossed[1] * sine + k[1] * kdotv * scale,
        vector[2] * cosine + crossed[2] * sine + k[2] * kdotv * scale,
    )


def principal_cs(
    plane: str,
    flipped: bool = False,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    name = str(plane).strip().casefold()
    axes = _PLANE_CS.get(name)
    if axes is None:
        raise ValueError("Section plane must be front, top, or right.")
    u_axis, v_axis, normal = axes
    if flipped:
        u_axis = (-u_axis[0], -u_axis[1], -u_axis[2])
        normal = (-normal[0], -normal[1], -normal[2])
    return u_axis, v_axis, normal


def section_cs_axes(
    plane: str,
    flipped: bool = False,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    u_axis, v_axis, normal = principal_cs(plane, flipped)
    if abs(float(pitch)) > 1.0e-12:
        v_axis = _rotate_vector(v_axis, u_axis, pitch)
        normal = _rotate_vector(normal, u_axis, pitch)
    if abs(float(yaw)) > 1.0e-12:
        u_axis = _rotate_vector(u_axis, v_axis, yaw)
        normal = _rotate_vector(normal, v_axis, yaw)
    normal = _unit(normal)
    u_axis = _unit(_cross(v_axis, normal))
    v_axis = _unit(_cross(normal, u_axis))
    if abs(float(roll)) > 1.0e-12:
        u_axis = _rotate_vector(u_axis, normal, roll)
        v_axis = _rotate_vector(v_axis, normal, roll)
    return u_axis, v_axis, normal


def section_plane_normal(
    plane: str,
    flipped: bool = False,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
) -> tuple[float, float, float]:
    if abs(float(yaw)) < 1.0e-12 and abs(float(pitch)) < 1.0e-12:
        _u_axis, _v_axis, normal = principal_cs(plane, flipped)
        return normal
    return section_cs_axes(plane, flipped, yaw, pitch, roll)[2]


def wrap_degrees(value: float) -> float:
    wrapped = float(value)
    while wrapped > 180.0:
        wrapped -= 360.0
    while wrapped < -180.0:
        wrapped += 360.0
    return wrapped


def orientation_from_axes(
    plane: str,
    flipped: bool,
    u_axis: tuple[float, float, float],
    normal: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Return (yaw, pitch, roll) that reproduces ``normal`` and ``u_axis``."""

    u0, v0, n0 = principal_cs(plane, flipped)
    direction = _unit(normal)
    nx = _dot(direction, u0)
    ny = _dot(direction, v0)
    nz = _dot(direction, n0)
    pitch = wrap_degrees(degrees(atan2(-ny, nz)))
    yaw = wrap_degrees(degrees(atan2(nx, hypot(ny, nz))))
    u_expected, v_expected, _n_expected = section_cs_axes(
        plane, flipped, yaw, pitch, 0.0
    )
    roll = wrap_degrees(
        degrees(atan2(_dot(u_axis, v_expected), _dot(u_axis, u_expected)))
    )
    return yaw, pitch, roll


def apply_world_rotation(
    settings: SectionViewSettings,
    origin: tuple[float, float, float],
    center: tuple[float, float, float],
    quat: tuple[float, float, float, float],
) -> SectionViewSettings:
    """Map a datum rotation-ring pose onto yaw, pitch, and roll."""

    normal = _unit(_quat_rotate(quat, (0.0, 0.0, 1.0)))
    u_axis = _unit(_quat_rotate(quat, (1.0, 0.0, 0.0)))
    start_normal = section_plane_normal(
        settings.plane, settings.flipped, settings.yaw, settings.pitch, settings.roll
    )
    if _dot(normal, start_normal) < 0.0:
        normal = (-normal[0], -normal[1], -normal[2])
        u_axis = (-u_axis[0], -u_axis[1], -u_axis[2])
    yaw, pitch, roll = orientation_from_axes(
        settings.plane, settings.flipped, u_axis, normal
    )
    return replace(
        settings,
        yaw=yaw,
        pitch=pitch,
        roll=roll,
        offset=_dot(_sub(origin, center), normal),
    )


def section_offset_label(settings: SectionViewSettings) -> str:
    names = {"front": ("Front", "Y"), "top": ("Top", "Z"), "right": ("Right", "X")}
    plane, axis = names[settings.plane]
    tilted = abs(settings.yaw) > 0.5 or abs(settings.pitch) > 0.5
    if tilted:
        return f"{plane}  ·  offset along cut"
    return f"{plane}  ·  offset along {axis}"


def section_plane_from_view_direction(
    direction: tuple[float, float, float],
) -> tuple[str, bool]:
    """Pick Front/Top/Right parallel to the screen, keeping the far half."""

    look = _unit(direction)
    toward_camera = (-look[0], -look[1], -look[2])
    plane = principal_plane_for_axis(toward_camera)
    principal = section_plane_normal(plane, flipped=False)
    flipped = _dot(principal, look) < 0.0
    return plane, flipped


def initial_section_settings(
    view_direction: tuple[float, float, float] | None = None,
) -> SectionViewSettings:
    if view_direction is None:
        return SectionViewSettings()
    plane, flipped = section_plane_from_view_direction(view_direction)
    return SectionViewSettings(plane=plane, flipped=flipped)


def principal_plane_for_axis(axis: tuple[float, float, float]) -> str:
    direction = _unit(axis)
    abs_x, abs_y, abs_z = abs(direction[0]), abs(direction[1]), abs(direction[2])
    if abs_z >= abs_x and abs_z >= abs_y:
        return "top"
    if abs_y >= abs_x:
        return "front"
    return "right"


def offset_through_point(
    settings: SectionViewSettings,
    model_center: tuple[float, float, float],
    point: tuple[float, float, float],
) -> float:
    """Offset that puts the current section plane through ``point``."""

    normal = section_plane_normal(
        settings.plane, settings.flipped, settings.yaw, settings.pitch, settings.roll
    )
    return _dot(_sub(point, model_center), normal)


def snap_point_from_shape(shape: Any) -> tuple[float, float, float] | None:
    """Center of a hole, cylinder, circle, vertex, or other picked shape."""

    if shape is None:
        return None
    shape_type = str(getattr(shape, "ShapeType", "") or "")
    if shape_type == "Vertex":
        point = getattr(shape, "Point", None)
        if point is not None:
            try:
                return _vec3(point)
            except Exception:
                return None
    # Cylindrical hole faces: CenterOfMass is the midpoint along the hole.
    # Surface.Center/Location is the cylinder origin and often sits at one end.
    if shape_type == "Face" and snap_axis_from_shape(shape) is not None:
        com = getattr(shape, "CenterOfMass", None)
        if com is not None:
            try:
                return _vec3(com)
            except Exception:
                pass
    for owner_name in ("Curve", "Surface"):
        try:
            owner = getattr(shape, owner_name)
        except Exception:
            owner = None
        if owner is None:
            continue
        for center_name in ("Center", "Location"):
            center = getattr(owner, center_name, None)
            if center is None:
                continue
            try:
                return _vec3(center)
            except Exception:
                continue
    com = getattr(shape, "CenterOfMass", None)
    if com is None:
        return None
    try:
        return _vec3(com)
    except Exception:
        return None


def snap_axis_from_shape(shape: Any) -> tuple[float, float, float] | None:
    """Axis of a hole, cylinder, or circular edge, if the shape has one."""

    if shape is None:
        return None
    for owner_name in ("Curve", "Surface"):
        try:
            owner = getattr(shape, owner_name)
        except Exception:
            owner = None
        if owner is None:
            continue
        axis = getattr(owner, "Axis", None)
        if axis is None:
            continue
        try:
            return _unit(_vec3(axis))
        except Exception:
            continue
    return None


def snap_geometry_from_shape(
    shape: Any,
) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None]:
    """Return (center, axis) for a picked hole or other feature."""

    point = snap_point_from_shape(shape)
    axis = snap_axis_from_shape(shape)
    if axis is not None:
        return point, axis
    faces = tuple(getattr(shape, "Faces", ()) or ())
    best: tuple[float, tuple[float, float, float] | None, tuple[float, float, float]] | None = None
    for face in faces:
        face_axis = snap_axis_from_shape(face)
        if face_axis is None:
            continue
        face_point = snap_point_from_shape(face) or point
        radius = getattr(getattr(face, "Surface", None), "Radius", None)
        key = float(radius) if radius is not None else 1.0e9
        if best is None or key < best[0]:
            best = (key, face_point, face_axis)
    if best is not None:
        return best[1], best[2]
    return point, None


def plane_containing_axis(
    axis: tuple[float, float, float],
    view_direction: tuple[float, float, float] | None = None,
) -> tuple[str, bool]:
    """Principal plane that contains ``axis``, preferring one facing the camera."""

    direction = _unit(axis)
    look = _unit(view_direction) if view_direction is not None else (0.0, 0.0, -1.0)
    toward_camera = (-look[0], -look[1], -look[2])
    ranked: list[tuple[float, float, str, tuple[float, float, float]]] = []
    for plane in SECTION_PLANES:
        normal = section_plane_normal(plane, False)
        contain = 1.0 - abs(_dot(normal, direction))
        facing = abs(_dot(normal, look))
        ranked.append((contain, facing, plane, normal))
    ranked.sort(reverse=True)
    _contain, _facing, plane, normal = ranked[0]
    flipped = _dot(normal, toward_camera) < 0.0
    return plane, flipped


def apply_feature_snap(
    settings: SectionViewSettings,
    model_center: tuple[float, float, float],
    point: tuple[float, float, float],
    axis: tuple[float, float, float] | None = None,
    view_direction: tuple[float, float, float] | None = None,
) -> SectionViewSettings:
    """Snap the section through a feature. Holes are cut along their depth."""

    next_settings = settings
    if axis is not None:
        direction = _unit(axis)
        current_normal = section_plane_normal(
            settings.plane,
            settings.flipped,
            settings.yaw,
            settings.pitch,
            settings.roll,
        )
        if abs(_dot(current_normal, direction)) > 0.5:
            plane, flipped = plane_containing_axis(direction, view_direction)
            next_settings = SectionViewSettings(
                plane=plane,
                flipped=flipped,
                show_plane=settings.show_plane,
                show_handles=settings.show_handles,
            )
    offset = offset_through_point(next_settings, model_center, point)
    return replace(next_settings, offset=offset)


def offset_range_along_normal(
    bounds: ModelBounds,
    normal: tuple[float, float, float],
) -> tuple[float, float]:
    direction = _unit(normal)
    center = bounds.center
    values = []
    for x in (bounds.xmin, bounds.xmax):
        for y in (bounds.ymin, bounds.ymax):
            for z in (bounds.zmin, bounds.zmax):
                values.append(_dot(_sub((x, y, z), center), direction))
    return (min(values), max(values))


def section_offset_range(
    bounds: ModelBounds,
    plane: str,
    yaw: float = 0.0,
    pitch: float = 0.0,
    flipped: bool = False,
) -> tuple[float, float]:
    if abs(float(yaw)) < 1.0e-12 and abs(float(pitch)) < 1.0e-12:
        half = float(bounds.axis_half_extent(plane))
        return (-half, half)
    return offset_range_along_normal(
        bounds, section_plane_normal(plane, flipped, yaw, pitch)
    )


def apply_dragger_translation(
    settings: SectionViewSettings,
    origin: tuple[float, float, float],
    center: tuple[float, float, float],
    translation_counts: tuple[int, int, int],
) -> SectionViewSettings:
    """Map a datum-origin arrow drag onto plane, offset, and cleared tilt."""

    count_x, count_y, count_z = (int(value) for value in translation_counts)
    abs_x, abs_y, abs_z = abs(count_x), abs(count_y), abs(count_z)
    u_axis, v_axis, normal = section_cs_axes(
        settings.plane, settings.flipped, settings.yaw, settings.pitch, settings.roll
    )
    switch_threshold = 20
    if abs_x >= abs_y and abs_x > abs_z and abs_x >= switch_threshold:
        plane = principal_plane_for_axis(u_axis)
        principal = section_plane_normal(plane, settings.flipped)
        return replace(
            settings,
            plane=plane,
            offset=_dot(_sub(origin, center), principal),
            yaw=0.0,
            pitch=0.0,
            roll=0.0,
        )
    if abs_y > abs_z and abs_y >= switch_threshold:
        plane = principal_plane_for_axis(v_axis)
        principal = section_plane_normal(plane, settings.flipped)
        return replace(
            settings,
            plane=plane,
            offset=_dot(_sub(origin, center), principal),
            yaw=0.0,
            pitch=0.0,
            roll=0.0,
        )
    return replace(settings, offset=_dot(_sub(origin, center), normal))


def apply_dragger_rotation(
    settings: SectionViewSettings,
    origin: tuple[float, float, float],
    center: tuple[float, float, float],
    rotation_counts: tuple[int, int, int],
    degrees_per_count: float = 1.0,
) -> SectionViewSettings:
    """Map datum rotation-ring steps onto section tilt."""

    pitch = wrap_degrees(
        settings.pitch + int(rotation_counts[0]) * float(degrees_per_count)
    )
    yaw = wrap_degrees(settings.yaw + int(rotation_counts[1]) * float(degrees_per_count))
    roll = wrap_degrees(
        settings.roll + int(rotation_counts[2]) * float(degrees_per_count)
    )
    normal = section_plane_normal(
        settings.plane, settings.flipped, yaw, pitch, roll
    )
    return replace(
        settings,
        yaw=yaw,
        pitch=pitch,
        roll=roll,
        offset=_dot(_sub(origin, center), normal),
    )


def clip_plane_from_settings(
    settings: SectionViewSettings,
    center: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return (origin, clip-normal) for the SolidWorks/Fusion section plane."""

    normal = section_plane_normal(
        settings.plane, settings.flipped, settings.yaw, settings.pitch, settings.roll
    )
    origin = (
        center[0] + normal[0] * settings.offset,
        center[1] + normal[1] * settings.offset,
        center[2] + normal[2] * settings.offset,
    )
    return origin, normal


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = (vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2) ** 0.5
    if length == 0.0:
        raise ValueError("A section-plane axis is degenerate.")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def section_plane_axes(
    normal: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    direction = _unit(normal)
    helper = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (0.0, 1.0, 0.0)
    u_axis = _unit(_cross(direction, helper))
    v_axis = _unit(_cross(direction, u_axis))
    return u_axis, v_axis


def section_plane_corners(
    origin: tuple[float, float, float],
    normal: tuple[float, float, float],
    half_width: float,
    half_height: float,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    u_axis, v_axis = section_plane_axes(normal)
    corners = []
    for u_sign, v_sign in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
        corners.append(
            (
                origin[0] + u_axis[0] * u_sign * half_width + v_axis[0] * v_sign * half_height,
                origin[1] + u_axis[1] * u_sign * half_width + v_axis[1] * v_sign * half_height,
                origin[2] + u_axis[2] * u_sign * half_width + v_axis[2] * v_sign * half_height,
            )
        )
    return (corners[0], corners[1], corners[2], corners[3])


def section_plane_distance(
    origin: tuple[float, float, float],
    normal: tuple[float, float, float],
) -> float:
    """Return ``n · origin`` for a unit or non-unit section-plane normal."""

    return (
        float(origin[0]) * float(normal[0])
        + float(origin[1]) * float(normal[1])
        + float(origin[2]) * float(normal[2])
    )


def hatch_spacing_for_bounds(bounds: ModelBounds | None) -> float:
    """Pick an ANSI-style hatch pitch from the model size, like other CAD apps."""

    if bounds is None:
        return _DEFAULT_HATCH_SPACING
    diagonal = (
        (bounds.xmax - bounds.xmin) ** 2
        + (bounds.ymax - bounds.ymin) ** 2
        + (bounds.zmax - bounds.zmin) ** 2
    ) ** 0.5
    return max(diagonal / 18.0, 0.5)


def _closed_ring(
    loop: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    points = [(float(point[0]), float(point[1])) for point in loop]
    if len(points) < 3:
        return ()
    if points[0] != points[-1]:
        points.append(points[0])
    return tuple(points)


def _point_on_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
    eps: float = 1e-9,
) -> bool:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length <= eps:
        return abs(point[0] - start[0]) <= eps and abs(point[1] - start[1]) <= eps
    cross = (point[0] - start[0]) * dy - (point[1] - start[1]) * dx
    if abs(cross) > eps * length:
        return False
    along = (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    return -eps * length <= along <= length * length + eps * length


def _point_in_ring(point: tuple[float, float], ring: Sequence[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for index in range(len(ring) - 1):
        start = ring[index]
        end = ring[index + 1]
        if _point_on_segment(point, start, end):
            return True
        x1, y1 = start
        x2, y2 = end
        if (y1 > y) is (y2 > y):
            continue
        span = y2 - y1
        if span == 0.0:
            continue
        x_at_y = (x2 - x1) * (y - y1) / span + x1
        if x < x_at_y:
            inside = not inside
    return inside


def _point_in_loops(
    point: tuple[float, float],
    loops: Sequence[Sequence[tuple[float, float]]],
) -> bool:
    inside = False
    for loop in loops:
        ring = _closed_ring(loop)
        if len(ring) < 4:
            continue
        if _point_in_ring(point, ring):
            inside = not inside
    return inside


def _segment_intersection_t(
    start: tuple[float, float],
    end: tuple[float, float],
    other_start: tuple[float, float],
    other_end: tuple[float, float],
) -> float | None:
    dx1 = end[0] - start[0]
    dy1 = end[1] - start[1]
    dx2 = other_end[0] - other_start[0]
    dy2 = other_end[1] - other_start[1]
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-14:
        return None
    sx = other_start[0] - start[0]
    sy = other_start[1] - start[1]
    t = (sx * dy2 - sy * dx2) / denom
    u = (sx * dy1 - sy * dx1) / denom
    if -1e-10 <= t <= 1.0 + 1e-10 and -1e-10 <= u <= 1.0 + 1e-10:
        return max(0.0, min(1.0, t))
    return None


def hatch_segments_for_loops(
    loops: Sequence[Sequence[tuple[float, float]]],
    spacing: float,
    angle_deg: float = _HATCH_ANGLE_DEG,
) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    """Clip 45-degree hatch strokes to planar section loops, including holes."""

    pitch = float(spacing)
    if pitch <= 0.0:
        raise ValueError("Hatch spacing must be positive.")
    rings = tuple(ring for ring in (_closed_ring(loop) for loop in loops) if len(ring) >= 4)
    if not rings:
        return ()
    points = [point for ring in rings for point in ring]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    angle = radians(float(angle_deg))
    dx, dy = cos(angle), sin(angle)
    px, py = -dy, dx
    projections = [point[0] * px + point[1] * py for point in points]
    along = [point[0] * dx + point[1] * dy for point in points]
    t_min = min(projections)
    t_max = max(projections)
    a_min = min(along) - pitch
    a_max = max(along) + pitch
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax <= xmin and ymax <= ymin:
        return ()

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    t = floor(t_min / pitch) * pitch
    while t <= t_max + 1e-9:
        origin = (t * px, t * py)
        line_start = (origin[0] + a_min * dx, origin[1] + a_min * dy)
        line_end = (origin[0] + a_max * dx, origin[1] + a_max * dy)
        hits: list[float] = []
        for ring in rings:
            for index in range(len(ring) - 1):
                hit = _segment_intersection_t(
                    line_start, line_end, ring[index], ring[index + 1]
                )
                if hit is not None:
                    hits.append(hit)
        unique: list[float] = []
        for hit in sorted(hits):
            if not unique or hit - unique[-1] > 1e-9:
                unique.append(hit)
        for left, right in zip(unique, unique[1:]):
            if right - left <= 1e-8:
                continue
            mid_t = (left + right) / 2.0
            midpoint = (
                line_start[0] + (line_end[0] - line_start[0]) * mid_t,
                line_start[1] + (line_end[1] - line_start[1]) * mid_t,
            )
            if not _point_in_loops(midpoint, rings):
                continue
            start = (
                line_start[0] + (line_end[0] - line_start[0]) * left,
                line_start[1] + (line_end[1] - line_start[1]) * left,
            )
            end = (
                line_start[0] + (line_end[0] - line_start[0]) * right,
                line_start[1] + (line_end[1] - line_start[1]) * right,
            )
            segments.append((start, end))
        t += pitch
    return tuple(segments)


def project_point_to_uv(
    point: tuple[float, float, float],
    origin: tuple[float, float, float],
    u_axis: tuple[float, float, float],
    v_axis: tuple[float, float, float],
) -> tuple[float, float]:
    relative = (
        point[0] - origin[0],
        point[1] - origin[1],
        point[2] - origin[2],
    )
    return (
        relative[0] * u_axis[0] + relative[1] * u_axis[1] + relative[2] * u_axis[2],
        relative[0] * v_axis[0] + relative[1] * v_axis[1] + relative[2] * v_axis[2],
    )


def unproject_uv(
    u_value: float,
    v_value: float,
    origin: tuple[float, float, float],
    u_axis: tuple[float, float, float],
    v_axis: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        origin[0] + u_axis[0] * u_value + v_axis[0] * v_value,
        origin[1] + u_axis[1] * u_value + v_axis[1] * v_value,
        origin[2] + u_axis[2] * u_value + v_axis[2] * v_value,
    )


def _offset_point(
    point: tuple[float, float, float],
    delta: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (point[0] + delta[0], point[1] + delta[1], point[2] + delta[2])


def _closed_loop_3d(
    loop: Sequence[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], ...]:
    points = [
        (float(point[0]), float(point[1]), float(point[2])) for point in loop
    ]
    if len(points) < 3:
        return ()
    if points[0] != points[-1]:
        points.append(points[0])
    return tuple(points)


def fan_triangulate_loop(
    loop: Sequence[tuple[float, float, float]],
) -> tuple[
    tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
    ...,
]:
    points = [
        (float(point[0]), float(point[1]), float(point[2])) for point in loop
    ]
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        return ()
    triangles = []
    origin = points[0]
    for index in range(1, len(points) - 1):
        triangles.append((origin, points[index], points[index + 1]))
    return tuple(triangles)


def build_section_cap_geometry(
    loops_3d: Sequence[Sequence[tuple[float, float, float]]],
    origin: tuple[float, float, float],
    normal: tuple[float, float, float],
    spacing: float,
    *,
    triangles: Sequence[
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ]
    ]
    | None = None,
    offset_magnitude: float | None = None,
) -> SectionCapGeometry:
    """Build filled, hatched cut-face geometry from planar section loops."""

    direction = _unit(normal)
    u_axis, v_axis = section_plane_axes(direction)
    magnitude = SECTION_CAP_OFFSET if offset_magnitude is None else float(offset_magnitude)
    offset = (
        -direction[0] * magnitude,
        -direction[1] * magnitude,
        -direction[2] * magnitude,
    )
    loops_uv = []
    outlines: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for loop in loops_3d:
        closed = _closed_loop_3d(loop)
        if len(closed) < 4:
            continue
        loops_uv.append(
            tuple(project_point_to_uv(point, origin, u_axis, v_axis) for point in closed[:-1])
        )
        shifted = [_offset_point(point, offset) for point in closed]
        for start, end in zip(shifted, shifted[1:]):
            outlines.append((start, end))
    hatch_uv = hatch_segments_for_loops(loops_uv, spacing)
    hatch = tuple(
        (
            _offset_point(unproject_uv(start[0], start[1], origin, u_axis, v_axis), offset),
            _offset_point(unproject_uv(end[0], end[1], origin, u_axis, v_axis), offset),
        )
        for start, end in hatch_uv
    )
    if triangles is None:
        filled = fan_triangulate_loop(loops_3d[0]) if len(loops_3d) == 1 else ()
    else:
        filled = tuple(triangles)
    shifted_triangles = tuple(
        tuple(_offset_point(point, offset) for point in triangle)  # type: ignore[misc]
        for triangle in filled
    )
    return SectionCapGeometry(shifted_triangles, hatch, tuple(outlines))


def _is_visible_object(obj: Any) -> bool:
    try:
        if getattr(obj, "Visibility", True) is False:
            return False
    except Exception:
        return False
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return True
    try:
        return getattr(view, "Visibility", True) is not False
    except Exception:
        return True


def _shape_has_solids(shape: Any) -> bool:
    if shape is None:
        return False
    is_null = getattr(shape, "isNull", None)
    if callable(is_null):
        try:
            if bool(is_null()):
                return False
        except Exception:
            return False
    count = getattr(shape, "countElement", None)
    if callable(count):
        try:
            return count("Solid") > 0
        except Exception:
            pass
    solids = getattr(shape, "Solids", None)
    try:
        if solids:
            return True
    except Exception:
        pass
    shape_type = str(getattr(shape, "ShapeType", "") or "")
    return shape_type in {"Solid", "CompSolid"}


def _parent_already_has_solid(obj: Any) -> bool:
    getter = getattr(obj, "getParentGeoFeatureGroup", None)
    if not callable(getter):
        return False
    try:
        parent = getter()
    except Exception:
        return False
    if parent is None or not _is_visible_object(parent):
        return False
    return _shape_has_solids(getattr(parent, "Shape", None))


def iter_sectionable_shapes(objects: Any) -> tuple[Any, ...]:
    """Return visible solid shapes that can produce a hatched section cap."""

    shapes: list[Any] = []
    for obj in tuple(objects or ()):
        if not _is_visible_object(obj):
            continue
        if _parent_already_has_solid(obj):
            continue
        shape = getattr(obj, "Shape", None)
        if not _shape_has_solids(shape):
            continue
        shapes.append(shape)
    return tuple(shapes)


def _vec3(value: Any) -> tuple[float, float, float]:
    nested = getattr(value, "getValue", None)
    if callable(nested):
        try:
            unpacked = nested()
            if unpacked is not None:
                value = unpacked
        except Exception:
            pass
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        pass

    def _component(*names: str) -> float | None:
        for name in names:
            attr = getattr(value, name, None)
            if attr is None:
                continue
            if callable(attr):
                try:
                    attr = attr()
                except Exception:
                    continue
            try:
                return float(attr)
            except Exception:
                continue
        return None

    x_val = _component("x", "X")
    y_val = _component("y", "Y")
    z_val = _component("z", "Z")
    if None not in (x_val, y_val, z_val):
        return (x_val, y_val, z_val)
    raise ValueError("Cannot read an XYZ vector.")


def _discretize_wire(wire: Any, deflection: float = 0.25) -> tuple[tuple[float, float, float], ...]:
    points: list[tuple[float, float, float]] = []
    discretize = getattr(wire, "discretize", None)
    if callable(discretize):
        try:
            raw = discretize(Deflection=deflection)
        except TypeError:
            try:
                raw = discretize(deflection)
            except Exception:
                raw = ()
        except Exception:
            raw = ()
        for item in tuple(raw or ()):
            points.append(_vec3(item))
    if len(points) < 3:
        vertexes = getattr(wire, "OrderedVertexes", None) or getattr(wire, "Vertexes", ())
        points = []
        for vertex in tuple(vertexes or ()):
            point = getattr(vertex, "Point", vertex)
            points.append(_vec3(point))
    return _closed_loop_3d(points)[:-1] if len(points) >= 3 else ()


def _wire_key(wire: Any) -> object:
    hasher = getattr(wire, "hashCode", None)
    if callable(hasher):
        try:
            return hasher()
        except Exception:
            return id(wire)
    return id(wire)


def _faces_from_section_wires(wires: Sequence[Any]) -> tuple[Any, ...]:
    try:
        import Part
    except ImportError:
        return ()
    closed = []
    for wire in tuple(wires or ()):
        is_closed = getattr(wire, "isClosed", None)
        try:
            if callable(is_closed) and not bool(is_closed()):
                continue
        except Exception:
            continue
        closed.append(wire)
    if not closed:
        return ()
    try:
        made = Part.makeFace(closed, "Part::FaceMakerBullseye")
    except Exception:
        made = None
    faces: list[Any] = []
    if made is not None:
        found = getattr(made, "Faces", None)
        faces = list(found) if found else [made]
    if faces:
        return tuple(faces)
    for wire in closed:
        try:
            faces.append(Part.Face(wire))
        except Exception:
            continue
    return tuple(faces)


def _cap_geometry_from_shape(
    shape: Any,
    origin: tuple[float, float, float],
    normal: tuple[float, float, float],
    spacing: float,
) -> SectionCapGeometry | None:
    if App is None:
        return None
    try:
        import Part  # noqa: F401
    except ImportError:
        return None
    direction = _unit(normal)
    distance = section_plane_distance(origin, direction)
    slicer = getattr(shape, "slice", None)
    if not callable(slicer):
        return None
    direction_vec = App.Vector(direction[0], direction[1], direction[2])
    wires = ()
    for candidate in (distance, distance + 1.0e-4, distance - 1.0e-4):
        try:
            found = slicer(direction_vec, candidate)
        except Exception:
            found = ()
        if found:
            wires = found
            break
    faces = _faces_from_section_wires(tuple(wires or ()))
    if not faces:
        return None
    triangles: list[
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ]
    ] = []
    loops: list[tuple[tuple[float, float, float], ...]] = []
    for face in faces:
        try:
            outer = _discretize_wire(face.OuterWire)
        except Exception:
            continue
        if len(outer) < 3:
            continue
        face_loops = [outer]
        try:
            outer_key = _wire_key(face.OuterWire)
            for wire in tuple(getattr(face, "Wires", ()) or ()):
                if _wire_key(wire) == outer_key:
                    continue
                inner = _discretize_wire(wire)
                if len(inner) >= 3:
                    face_loops.append(inner)
        except Exception:
            pass
        loops.extend(face_loops)
        tessellate = getattr(face, "tessellate", None)
        if callable(tessellate):
            try:
                verts, tris = tessellate(0.25)
                for tri in tris:
                    triangles.append(
                        (
                            _vec3(verts[tri[0]]),
                            _vec3(verts[tri[1]]),
                            _vec3(verts[tri[2]]),
                        )
                    )
                continue
            except Exception:
                pass
        triangles.extend(fan_triangulate_loop(outer))
    if not loops:
        return None
    return build_section_cap_geometry(
        loops,
        origin,
        direction,
        spacing,
        triangles=triangles or None,
    )


def section_cap_geometry_from_objects(
    objects: Any,
    origin: tuple[float, float, float],
    normal: tuple[float, float, float],
    spacing: float,
) -> SectionCapGeometry:
    """Slice visible solids on the section plane and hatch the resulting faces."""

    triangles: list[
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ]
    ] = []
    hatch: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    outlines: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for shape in iter_sectionable_shapes(objects):
        try:
            geometry = _cap_geometry_from_shape(shape, origin, normal, spacing)
        except Exception:
            continue
        if geometry is None:
            continue
        triangles.extend(geometry.triangles)
        hatch.extend(geometry.hatch)
        outlines.extend(geometry.outlines)
    return SectionCapGeometry(tuple(triangles), tuple(hatch), tuple(outlines))


def _active_document() -> Any | None:
    if App is None:
        return None
    document = getattr(App, "ActiveDocument", None)
    return document


def _active_3d_view(gui: Any | None = None) -> Any | None:
    if gui is None:
        try:
            import FreeCADGui as Gui
        except ImportError:
            return None
        gui = Gui
    gui_document = getattr(gui, "ActiveDocument", None)
    if gui_document is None:
        active_document = getattr(gui, "activeDocument", None)
        gui_document = active_document() if callable(active_document) else None
    if gui_document is None:
        return None
    active_view = getattr(gui_document, "activeView", None)
    view = (
        active_view()
        if callable(active_view)
        else getattr(gui_document, "ActiveView", None)
    )
    if view is None:
        return None
    if not callable(getattr(view, "toggleClippingPlane", None)):
        return None
    if not callable(getattr(view, "hasClippingPlane", None)):
        return None
    return view


def _document_objects(document: Any | None) -> Iterable[Any]:
    target = document if document is not None else _active_document()
    return getattr(target, "Objects", ()) if target is not None else ()


def section_view_placement(
    document: Any | None = None,
    settings: SectionViewSettings | None = None,
) -> Any:
    """Front/Top/Right plane through the document bounds center, or the origin."""

    if App is None:
        raise RuntimeError("FreeCAD is unavailable.")
    active = settings if settings is not None else _settings
    bounds = model_bounds(_document_objects(document))
    return _placement_from_bounds(active, bounds)


def _placement_from_bounds(settings: SectionViewSettings, bounds: ModelBounds | None) -> Any:
    center = bounds.center if bounds is not None else (0.0, 0.0, 0.0)
    origin, normal = clip_plane_from_settings(settings, center)
    rotation = App.Rotation(App.Vector(0.0, 0.0, -1.0), App.Vector(*normal))
    return App.Placement(App.Vector(*origin), rotation)


def _render_bounds(view: Any) -> ModelBounds | None:
    """Bound displayed model instances, excluding grids and editor decorations.

    Applying the action to each visible path preserves Link/display transforms
    without reading document compound shapes or modifying the scene.
    """
    scene = _scene_from_view(view)
    if scene is None:
        return None
    try:
        from pivy import coin

        box = coin.SbBox3f()
        for type_name in (
            "SoBrepFaceSet", "SoBrepEdgeSet", "SoBrepPointSet",
            "SoFCMeshObjectShape", "SoFCIndexedFaceSet",
        ):
            shape_type = coin.SoType.fromName(type_name)
            if shape_type.isBad():
                continue
            search = coin.SoSearchAction()
            search.setType(shape_type)
            search.setInterest(coin.SoSearchAction.ALL)
            search.setSearchingAll(False)
            search.apply(scene)
            for path in search.getPaths():
                action = coin.SoGetBoundingBoxAction(coin.SbViewportRegion(1, 1))
                action.apply(path)
                instance_box = action.getBoundingBox()
                if not instance_box.isEmpty():
                    box.extendBy(instance_box)
        if box.isEmpty():
            return None
        low, high = _vec3(box.getMin()), _vec3(box.getMax())
        return ModelBounds(low[0], high[0], low[1], high[1], low[2], high[2])
    except (ImportError, AttributeError, TypeError):
        return None


def _bounds_for_view(view: Any, document: Any | None) -> ModelBounds | None:
    if view is not None and view == _bounds_view:
        return _section_bounds
    return model_bounds(_document_objects(document))


def is_section_view_active(view: Any | None = None) -> bool:
    active = view if view is not None else _active_3d_view()
    if active is None:
        return False
    try:
        has_clip = getattr(active, "hasClippingPlane", None)
        if not callable(has_clip):
            return False
        return bool(has_clip())
    except Exception:
        return False


def _coin_vec3(coin: Any, xyz: tuple[float, float, float]) -> Any:
    """SbVec3f from three floats. Star-unpacking hits a Pivy SWIG mismatch."""

    x, y, z = float(xyz[0]), float(xyz[1]), float(xyz[2])
    try:
        return coin.SbVec3f(x, y, z)
    except Exception:
        vec = coin.SbVec3f()
        vec.setValue(x, y, z)
        return vec


def _top_level_nodes(scene: Any) -> tuple[Any, ...]:
    children = getattr(scene, "getChildren", None)
    if not callable(children):
        return ()
    try:
        return tuple(children() or ())
    except Exception:
        return ()


def _update_clip_plane(view: Any, placement: Any) -> bool:
    get_scene = getattr(view, "getSceneGraph", None)
    if not callable(get_scene) or App is None:
        return False
    try:
        from pivy import coin
    except ImportError:
        return False
    try:
        scene = get_scene()
    except Exception:
        return False
    if scene is None:
        return False
    try:
        origin = (
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
        )
        direction = placement.Rotation.multVec(App.Vector(0.0, 0.0, -1.0))
        normal = (float(direction.x), float(direction.y), float(direction.z))
    except Exception:
        return False
    for node in _top_level_nodes(scene):
        if type(node).__name__ == "SoClipPlane":
            node.plane.setValue(
                coin.SbPlane(_coin_vec3(coin, normal), _coin_vec3(coin, origin))
            )
            return True
    return False


def _overlay_size(
    bounds: ModelBounds | None,
    normal: tuple[float, float, float],
) -> tuple[float, float]:
    if bounds is None:
        return (50.0, 50.0)
    u_axis, v_axis = section_plane_axes(normal)
    extents = (
        (bounds.xmax - bounds.xmin, 1.0, 0.0, 0.0),
        (bounds.ymax - bounds.ymin, 0.0, 1.0, 0.0),
        (bounds.zmax - bounds.zmin, 0.0, 0.0, 1.0),
    )
    half_width = 0.0
    half_height = 0.0
    for length, x_dir, y_dir, z_dir in extents:
        half = abs(length) / 2.0
        half_width = max(
            half_width, abs(u_axis[0] * x_dir + u_axis[1] * y_dir + u_axis[2] * z_dir) * half
        )
        half_height = max(
            half_height,
            abs(v_axis[0] * x_dir + v_axis[1] * y_dir + v_axis[2] * z_dir) * half,
        )
    pad = 1.05
    return (max(half_width, 1.0) * pad, max(half_height, 1.0) * pad)


def _detach_scene_node(scene: Any, node: Any) -> None:
    if scene is None or node is None:
        return
    try:
        index = scene.findChild(node)
    except Exception:
        index = -1
    if index >= 0:
        try:
            scene.removeChild(node)
        except Exception:
            return


def _scene_from_view(view: Any) -> Any | None:
    try:
        # FreeCAD wrappers can raise during attribute lookup after their view
        # is deleted, before a bound method can even be obtained.
        get_scene = getattr(view, "getSceneGraph", None)
        if not callable(get_scene):
            return None
        return get_scene()
    except Exception:
        return None


def _remove_overlay(view: Any) -> None:
    global _overlay_node, _cap_node
    if _cap_worker is not None:
        _cap_worker.cancel()
    scene = _scene_from_view(view)
    _detach_scene_node(scene, _overlay_node)
    _detach_scene_node(scene, _cap_node)
    _overlay_node = None
    _cap_node = None


class _SectionDisplaySequence:
    """Read immutable worker geometry in bounded batches during GUI adoption."""

    def __init__(self, handle, kind, size, read):
        self._handle = handle
        self._kind = kind
        self._size = size
        self._read = read

    def __len__(self):
        return self._size

    def __iter__(self):
        for first in range(0, self._size, 256):
            yield from self._read(
                self._handle, self._kind, first, min(256, self._size - first)
            )


class _NativeSectionCapWorker:
    """Capture and adopt on the owner; compute geometry on native workers."""

    def __init__(self, backend):
        self._backend = backend
        self._handle = backend.createSectionFaceController()
        self._generation = 0
        self._steps = None
        self._running = False

    def cancel(self):
        self._generation += 1
        self._running = False
        steps, self._steps = self._steps, None
        self._backend.cancelSectionFaces(self._handle)
        if steps is not None:
            close = getattr(steps, "close", None)
            if close is not None:
                close()

    def _queue(self, generation, callback):
        def run():
            if generation != self._generation:
                return
            try:
                callback()
            except Exception:
                self.cancel()
                raise

        if not self._backend._deferSectionDisplay(run):
            self.cancel()

    def request(self, snapshots, origin, normal, spacing, publish, *, mesh=False):
        from time import perf_counter

        self.cancel()
        generation = self._generation
        self._steps = iter(snapshots)
        self._running = True
        instances = []

        def adopt():
            try:
                next(self._steps)
            except StopIteration:
                self._steps = None
                self._running = False
                return
            self._queue(generation, adopt)

        def completed(handle, error):
            if generation != self._generation:
                return
            self._running = False
            if error:
                # The native worker preserves successful instances when an
                # individual solid cannot be cut, as the geometry API does.
                App.Console.PrintWarning("Section View: " + error + "\n")
            steps = publish(handle)
            if steps is not None:
                self._steps = iter(steps)
                self._running = True
                self._queue(generation, adopt)

        def capture():
            started = perf_counter()
            for _ in range(128):
                try:
                    instance = next(self._steps)
                except StopIteration:
                    self._steps = None
                    request = (self._backend.requestSectionMeshDisplay if mesh
                               else self._backend.requestSectionDisplay)
                    request(
                        self._handle, instances, origin, normal, spacing, completed
                    )
                    return
                if instance is not None:
                    instances.append(instance)
                if perf_counter() - started >= 0.008:
                    break
            self._queue(generation, capture)

        self._queue(generation, capture)


def _invalidate_caps():
    global _cap_dirty
    _cap_dirty = True
    if _cap_worker is not None:
        _cap_worker.cancel()


class _SectionCapDocumentObserver:
    def slotChangedObject(self, obj, property_name):
        if property_name in {"Shape", "Placement", "Visibility", "Group", "LinkedObject"}:
            self._changed(obj)

    def slotCreatedObject(self, obj):
        self._changed(obj)

    def slotDeletedObject(self, obj):
        self._changed(obj)

    @staticmethod
    def _changed(obj):
        if _dragger_document is not None and getattr(obj, "Document", None) is _dragger_document:
            _invalidate_caps()


class _SectionViewDocumentObserver:
    def slotActivateDocument(self, gui_document: Any) -> None:
        if _dragger_node is None:
            return
        if getattr(gui_document, "Document", None) is _dragger_document:
            _start_dragger_poll()
        else:
            _stop_dragger_poll()

    def slotDeletedDocument(self, gui_document: Any) -> None:
        global _dragger_view, _dragger_document
        if getattr(gui_document, "Document", None) is not _dragger_document:
            return
        # Gui's delete notification precedes scene destruction. Pivy wrappers
        # must be released here, before the poll timer can see deleted nodes.
        view = _dragger_view
        _stop_selection_snap()
        _remove_dragger(view)
        _remove_overlay(view)
        _dragger_view = None
        _dragger_document = None
        _close_ui()


def _observe_section_document(view: Any, document: Any | None) -> None:
    global _dragger_view, _dragger_document, _section_document_observer, _cap_document_observer
    import FreeCADGui as Gui

    if _section_document_observer is None:
        observer = _SectionViewDocumentObserver()
        Gui.addDocumentObserver(observer)
        _section_document_observer = observer
    if _cap_document_observer is None:
        observer = _SectionCapDocumentObserver()
        App.addDocumentObserver(observer)
        _cap_document_observer = observer
    _dragger_view = view
    _dragger_document = document if document is not None else _active_document()


def _remove_dragger(view: Any) -> None:
    global _dragger_node, _dragger_busy, _drag_start_settings
    global _dragger_view, _dragger_document, _bounds_view, _section_bounds, _cap_dirty
    global _last_dragger_scale
    global _drag_start_origin, _drag_start_axes, _drag_start_rot_counts, _triad_parts
    _stop_plane_drag()
    _stop_dragger_poll()
    scene = _scene_from_view(view)
    _detach_scene_node(scene, _dragger_node)
    if _dragger_node is not None:
        unref = getattr(_dragger_node, "unref", None)
        if callable(unref):
            unref()
    _dragger_node = None
    _dragger_view = None
    _dragger_document = None
    _bounds_view = None
    _section_bounds = None
    _cap_dirty = False
    _last_dragger_scale = None
    _dragger_busy = False
    _drag_start_settings = None
    _drag_start_origin = None
    _drag_start_axes = None
    _drag_start_rot_counts = None
    _triad_parts = None


def _overlay_insert_index(scene: Any, after_plane: bool = False) -> int:
    index = 0
    if _dragger_node is not None and scene is not None:
        try:
            found = scene.findChild(_dragger_node)
        except Exception:
            found = -1
        if found >= 0:
            index = found + 1
    if after_plane and _overlay_node is not None and scene is not None:
        try:
            found = scene.findChild(_overlay_node)
        except Exception:
            found = -1
        if found >= 0:
            index = found + 1
    return index


def _sync_overlay(
    view: Any,
    document: Any | None,
    settings: SectionViewSettings,
    *,
    rebuild_caps: bool = True,
) -> None:
    _remove_overlay(view)
    try:
        from pivy import coin
    except ImportError:
        return
    scene = _scene_from_view(view)
    if scene is None:
        return
    _sync_plane_guide(view, document, settings)
    bounds = _bounds_for_view(view, document)
    center = bounds.center if bounds is not None else (0.0, 0.0, 0.0)
    origin, normal = clip_plane_from_settings(settings, center)
    if not rebuild_caps:
        return
    _schedule_cap_build(coin, view, document, origin, normal, hatch_spacing_for_bounds(bounds))


def _sync_plane_guide(view, document, settings):
    """Change guide visibility without cancelling or rebuilding cut faces."""
    global _overlay_node
    scene = _scene_from_view(view)
    if scene is None:
        return
    _detach_scene_node(scene, _overlay_node)
    _overlay_node = None
    if settings.show_plane:
        from pivy import coin

        bounds = _bounds_for_view(view, document)
        center = bounds.center if bounds is not None else (0.0, 0.0, 0.0)
        origin, normal = clip_plane_from_settings(settings, center)
        half_width, half_height = _overlay_size(bounds, normal)
        corners = section_plane_corners(origin, normal, half_width, half_height)
        try:
            _install_overlay_node(coin, scene, corners)
        except Exception:
            pass


def _rendered_section_snapshot_steps(view, *, mesh=False):
    """Capture displayed instances on the owner, yielding between providers.

    Only detached native shape references and matrices leave this generator.
    Coin traversal uses the visible scene, including Link snapshots and their
    current display transforms, rather than document Placement properties.
    """
    from pivy import coin

    face_type = coin.SoType.fromName("SoBrepFaceSet")
    sources = {}
    for document in App.listDocuments().values():
        for obj in document.Objects:
            provider = obj.ViewObject
            snapshot = getattr(provider, "getRenderedMeshSnapshot" if mesh else "getRenderedShapeSnapshot", None)
            if callable(snapshot):
                shape = snapshot()
                available = shape is not None if mesh else not shape.isNull()
                if available:
                    search = coin.SoSearchAction()
                    search.setType(face_type)
                    search.setInterest(coin.SoSearchAction.FIRST)
                    search.setSearchingAll(True)
                    search.apply(provider.RootNode)
                    path = search.getPath()
                    if path is not None:
                        sources[path.getTail().getNodeId()] = shape
            yield None

    search = coin.SoSearchAction()
    search.setType(face_type)
    search.setInterest(coin.SoSearchAction.ALL)
    search.setSearchingAll(False)
    search.apply(view.getSceneGraph())
    for path in search.getPaths():
        shape = sources.get(path.getTail().getNodeId())
        if shape is None:
            yield None
            continue
        action = coin.SoGetMatrixAction(coin.SbViewportRegion(1, 1))
        action.apply(path)
        matrix = action.getMatrix().getValue()
        # Coin uses row vectors; FreeCAD's matrix convention is transposed.
        transform = App.Matrix(*(matrix[column][row] for row in range(4) for column in range(4)))
        yield shape, transform


def _schedule_cap_build(coin, view, document, origin, normal, spacing):
    global _cap_worker, _cap_dirty
    import PartGui

    _cap_dirty = False
    owner = document if document is not None else _active_document()
    if owner is None:
        return
    if _cap_worker is None:
        _cap_worker = _NativeSectionCapWorker(PartGui)

    def publish(handle):
        sizes = PartGui.sectionDisplaySizes(handle)
        caps = SectionCapGeometry(*(
            _SectionDisplaySequence(handle, kind, size, PartGui.readSectionDisplay)
            for kind, size in zip(("triangles", "hatch", "outlines"), sizes)
        ))
        scene = _scene_from_view(view)
        if scene is not None:
            return _install_cap_node_steps(coin, scene, caps)

    _cap_worker.request(
        _rendered_section_snapshot_steps(view, mesh=True),
        App.Vector(*origin), App.Vector(*normal), spacing, publish, mesh=True,
    )


def _install_overlay_node(coin: Any, scene: Any, corners: Any) -> None:
    global _overlay_node
    separator = coin.SoSeparator()
    separator.setName(_OVERLAY_NAME)
    light = coin.SoLightModel()
    light.model = coin.SoLightModel.BASE_COLOR
    material = coin.SoMaterial()
    material.diffuseColor.setValue(0.15, 0.62, 0.78)
    material.transparency.setValue(0.72)
    material.emissiveColor.setValue(0.08, 0.35, 0.45)
    coords = coin.SoCoordinate3()
    for index, corner in enumerate(corners):
        coords.point.set1Value(
            index, float(corner[0]), float(corner[1]), float(corner[2])
        )
    faces = coin.SoIndexedFaceSet()
    for index, value in enumerate((0, 1, 2, 3, -1)):
        faces.coordIndex.set1Value(index, value)
    style = coin.SoDrawStyle()
    style.style = coin.SoDrawStyle.LINES
    style.lineWidth = 2
    line_material = coin.SoMaterial()
    line_material.diffuseColor.setValue(0.05, 0.42, 0.58)
    lines = coin.SoIndexedLineSet()
    for index, value in enumerate((0, 1, 2, 3, 0, -1)):
        lines.coordIndex.set1Value(index, value)
    separator.addChild(light)
    separator.addChild(material)
    separator.addChild(coords)
    separator.addChild(faces)
    separator.addChild(style)
    separator.addChild(line_material)
    separator.addChild(lines)
    try:
        scene.insertChild(separator, _overlay_insert_index(scene))
    except Exception:
        return
    _overlay_node = separator


def _write_points(coords: Any, points: Sequence[tuple[float, float, float]]) -> None:
    for index, point in enumerate(points):
        coords.point.set1Value(index, float(point[0]), float(point[1]), float(point[2]))


def _write_indexes(target: Any, values: Sequence[int]) -> None:
    for index, value in enumerate(values):
        target.coordIndex.set1Value(index, int(value))


def _write_points_steps(coords, points):
    for index, point in enumerate(points):
        coords.point.set1Value(index, float(point[0]), float(point[1]), float(point[2]))
        if (index + 1) % 256 == 0:
            yield


def _write_indexes_steps(target, values):
    for index, value in enumerate(values):
        target.coordIndex.set1Value(index, int(value))
        if (index + 1) % 256 == 0:
            yield


def _install_cap_node(coin: Any, scene: Any, caps: SectionCapGeometry) -> None:
    for _ in _install_cap_node_steps(coin, scene, caps):
        pass


def _install_cap_node_steps(coin: Any, scene: Any, caps: SectionCapGeometry) -> None:
    global _cap_node
    separator = coin.SoSeparator()
    separator.setName(_CAP_OVERLAY_NAME)
    light = coin.SoLightModel()
    light.model = coin.SoLightModel.BASE_COLOR
    separator.addChild(light)
    hints = getattr(coin, "SoShapeHints", None)
    if callable(hints):
        shape_hints = hints()
        unknown_order = getattr(coin.SoShapeHints, "UNKNOWN_ORDERING", None)
        if unknown_order is not None:
            shape_hints.vertexOrdering = unknown_order
        unknown_shape = getattr(coin.SoShapeHints, "UNKNOWN_SHAPE_TYPE", None)
        if unknown_shape is not None:
            shape_hints.shapeType = unknown_shape
        unknown_face = getattr(coin.SoShapeHints, "UNKNOWN_FACE_TYPE", None)
        if unknown_face is not None:
            shape_hints.faceType = unknown_face
        separator.addChild(shape_hints)

    if caps.triangles:
        fill = coin.SoMaterial()
        fill.diffuseColor.setValue(0.90, 0.78, 0.45)
        fill.emissiveColor.setValue(0.35, 0.24, 0.08)
        fill.transparency.setValue(0.0)
        points: list[tuple[float, float, float]] = []
        indexes: list[int] = []
        for index, triangle in enumerate(caps.triangles):
            if index and index % 256 == 0:
                yield
            start = len(points)
            points.extend(triangle)
            indexes.extend((start, start + 1, start + 2, -1))
            start = len(points)
            points.extend((triangle[0], triangle[2], triangle[1]))
            indexes.extend((start, start + 1, start + 2, -1))
        coords = coin.SoCoordinate3()
        yield from _write_points_steps(coords, points)
        faces = coin.SoIndexedFaceSet()
        yield from _write_indexes_steps(faces, indexes)
        separator.addChild(fill)
        separator.addChild(coords)
        separator.addChild(faces)

    if caps.hatch:
        hatch_material = coin.SoMaterial()
        hatch_material.diffuseColor.setValue(0.42, 0.26, 0.08)
        hatch_material.emissiveColor.setValue(0.18, 0.10, 0.02)
        style = coin.SoDrawStyle()
        style.style = coin.SoDrawStyle.LINES
        style.lineWidth = 1
        points = []
        indexes = []
        for index, (start_point, end_point) in enumerate(caps.hatch):
            if index and index % 256 == 0:
                yield
            start = len(points)
            points.extend((start_point, end_point))
            indexes.extend((start, start + 1, -1))
        coords = coin.SoCoordinate3()
        yield from _write_points_steps(coords, points)
        lines = coin.SoIndexedLineSet()
        yield from _write_indexes_steps(lines, indexes)
        separator.addChild(hatch_material)
        separator.addChild(style)
        separator.addChild(coords)
        separator.addChild(lines)

    if caps.outlines:
        outline_material = coin.SoMaterial()
        outline_material.diffuseColor.setValue(0.18, 0.10, 0.04)
        outline_material.emissiveColor.setValue(0.08, 0.04, 0.01)
        style = coin.SoDrawStyle()
        style.style = coin.SoDrawStyle.LINES
        style.lineWidth = 2
        points = []
        indexes = []
        for index, (start_point, end_point) in enumerate(caps.outlines):
            if index and index % 256 == 0:
                yield
            start = len(points)
            points.extend((start_point, end_point))
            indexes.extend((start, start + 1, -1))
        coords = coin.SoCoordinate3()
        yield from _write_points_steps(coords, points)
        lines = coin.SoIndexedLineSet()
        yield from _write_indexes_steps(lines, indexes)
        separator.addChild(outline_material)
        separator.addChild(style)
        separator.addChild(coords)
        separator.addChild(lines)

    insert_index = _overlay_insert_index(scene, after_plane=True)
    try:
        scene.insertChild(separator, insert_index)
        touch = getattr(scene, "touch", None)
        if callable(touch):
            touch()
    except Exception:
        return
    _cap_node = separator


def _coin_type_instance(coin: Any, *names: str) -> Any | None:
    type_getter = getattr(coin, "SoType", None)
    from_name = getattr(type_getter, "fromName", None) if type_getter is not None else None
    if not callable(from_name):
        return None
    for name in names:
        try:
            node_type = from_name(name)
        except Exception:
            continue
        is_bad = getattr(node_type, "isBad", None)
        try:
            if callable(is_bad) and bool(is_bad()):
                continue
        except Exception:
            continue
        create = getattr(node_type, "createInstance", None)
        if not callable(create):
            continue
        try:
            instance = create()
        except Exception:
            continue
        if instance is not None:
            return instance
    return None


def _create_transform_dragger(coin: Any) -> Any | None:
    try:
        import FreeCADGui  # noqa: F401
    except ImportError:
        pass
    return _coin_type_instance(
        coin,
        "SoTransformDragger",
        "TransformDragger",
    )


def _field_int(node: Any, name: str) -> int:
    field = getattr(node, name, None)
    getter = getattr(field, "getValue", None)
    if not callable(getter):
        return 0
    try:
        return int(getter())
    except Exception:
        return 0


def _field_vec3(node: Any, name: str) -> tuple[float, float, float] | None:
    field = getattr(node, name, None)
    getter = getattr(field, "getValue", None)
    if not callable(getter):
        return None
    try:
        value = getter()
    except Exception:
        return None
    nested = getattr(value, "getValue", None)
    if callable(nested):
        try:
            value = nested()
        except Exception:
            pass
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        return None


def _hide_dragger_clutter(dragger: Any) -> None:
    for method_name in (
        "hidePlanarTranslationXY",
        "hidePlanarTranslationYZ",
        "hidePlanarTranslationZX",
        "showRotationX",
        "showRotationY",
        "showRotationZ",
    ):
        method = getattr(dragger, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass
    getter = getattr(dragger, "getPart", None)
    if not callable(getter):
        return
    for part_name in (
        "xyPlanarTranslatorSwitch",
        "yzPlanarTranslatorSwitch",
        "zxPlanarTranslatorSwitch",
    ):
        try:
            part = getter(part_name, 0)
        except Exception:
            continue
        if part is None:
            continue
        try:
            part.whichChild = -1
        except Exception:
            continue


def _zero_transform_field(node: Any, name: str, identity_rotation: bool = False) -> None:
    field = getattr(node, name, None)
    setter = getattr(field, "setValue", None)
    if not callable(setter):
        return
    try:
        if identity_rotation:
            setter(0.0, 0.0, 0.0, 1.0)
        else:
            setter(0.0, 0.0, 0.0)
    except Exception:
        try:
            setter(0.0)
        except Exception:
            return


def _apply_pose_fields(
    coin: Any,
    node: Any,
    origin: tuple[float, float, float],
    axes: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> None:
    u_axis, v_axis, normal = axes
    translation = getattr(node, "translation", None)
    setter = getattr(translation, "setValue", None)
    if callable(setter):
        try:
            setter(float(origin[0]), float(origin[1]), float(origin[2]))
        except Exception:
            try:
                setter(_coin_vec3(coin, origin))
            except Exception:
                pass
    rotation = getattr(node, "rotation", None)
    rot_set = getattr(rotation, "setValue", None)
    if not callable(rot_set):
        return
    matrix_type = getattr(coin, "SbMatrix", None)
    rotation_type = getattr(coin, "SbRotation", None)
    if callable(matrix_type) and callable(rotation_type):
        try:
            matrix = matrix_type(
                float(u_axis[0]),
                float(v_axis[0]),
                float(normal[0]),
                0.0,
                float(u_axis[1]),
                float(v_axis[1]),
                float(normal[1]),
                0.0,
                float(u_axis[2]),
                float(v_axis[2]),
                float(normal[2]),
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
            )
            rot_set(rotation_type(matrix))
            return
        except Exception:
            pass
    if callable(rotation_type):
        try:
            rot_set(
                rotation_type(
                    _coin_vec3(coin, (0.0, 0.0, 1.0)),
                    _coin_vec3(coin, normal),
                )
            )
        except Exception:
            return


def _set_dragger_pose(
    coin: Any,
    dragger: Any,
    origin: tuple[float, float, float],
    axes: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> None:
    if _triad_parts is not None:
        pose = _triad_parts.get("pose")
        if pose is not None:
            _apply_pose_fields(coin, pose, origin, axes)
        if not _dragger_busy:
            for key, is_rotation in (
                ("x", False),
                ("y", False),
                ("z", False),
                ("pitch", True),
                ("yaw", True),
            ):
                child = _triad_parts.get(key)
                if child is None:
                    continue
                _zero_transform_field(
                    child,
                    "rotation" if is_rotation else "translation",
                    identity_rotation=is_rotation,
                )
        return
    _apply_pose_fields(coin, dragger, origin, axes)


def _dragger_center(document: Any | None) -> tuple[float, float, float]:
    bounds = (
        _bounds_for_view(_dragger_view, document)
        if document is None or document is _dragger_document
        else model_bounds(_document_objects(document))
    )
    if bounds is None:
        return (0.0, 0.0, 0.0)
    return bounds.center


def _axis_drag_distance(dragger: Any) -> float:
    if dragger is None:
        return 0.0
    value = _field_vec3(dragger, "translation")
    if value is None:
        return 0.0
    return float(value[0])


def _rotation_angle_degrees(dragger: Any) -> float:
    if dragger is None:
        return 0.0
    field = getattr(dragger, "rotation", None)
    getter = getattr(field, "getValue", None)
    if not callable(getter):
        return 0.0
    try:
        rotation = getter()
    except Exception:
        return 0.0
    quat = rotation
    nested = getattr(rotation, "getValue", None)
    if callable(nested):
        try:
            quat = nested()
        except Exception:
            quat = rotation
    try:
        x, y, z, w = (float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3]))
    except Exception:
        return 0.0
    sine = (x * x + y * y + z * z) ** 0.5
    angle = 2.0 * atan2(sine, w)
    return angle * 180.0 / 3.141592653589793


def _triad_origin() -> tuple[float, float, float] | None:
    if _triad_parts is None or _drag_start_origin is None or _drag_start_axes is None:
        return None
    local = (
        _axis_drag_distance(_triad_parts["x"]),
        _axis_drag_distance(_triad_parts["y"]),
        _axis_drag_distance(_triad_parts["z"]),
    )
    u_axis, v_axis, normal = _drag_start_axes
    return (
        _drag_start_origin[0]
        + u_axis[0] * local[0]
        + v_axis[0] * local[1]
        + normal[0] * local[2],
        _drag_start_origin[1]
        + u_axis[1] * local[0]
        + v_axis[1] * local[1]
        + normal[1] * local[2],
        _drag_start_origin[2]
        + u_axis[2] * local[0]
        + v_axis[2] * local[1]
        + normal[2] * local[2],
    )


def _axis_aligned_rotation(coin: Any, axis: str) -> Any:
    rotation = coin.SoRotation()
    if axis == "y":
        rotation.rotation.setValue(_coin_vec3(coin, (0.0, 0.0, 1.0)), radians(90.0))
    elif axis == "z":
        rotation.rotation.setValue(_coin_vec3(coin, (0.0, 1.0, 0.0)), radians(-90.0))
    else:
        rotation.rotation.setValue(_coin_vec3(coin, (1.0, 0.0, 0.0)), 0.0)
    return rotation


def _build_section_triad(coin: Any, scale: float) -> tuple[Any, dict[str, Any]] | None:
    translate_type = _coin_type_instance(coin, "SoTranslate1Dragger", "Translate1Dragger")
    rotate_type = _coin_type_instance(
        coin, "SoRotateCylindricalDragger", "RotateCylindricalDragger"
    )
    if translate_type is None:
        return None
    root = _coin_type_instance(coin, "So3DAnnotation", "SoAnnotation")
    if root is None:
        root = coin.SoSeparator()
    set_name = getattr(root, "setName", None)
    if callable(set_name):
        try:
            set_name(_DRAGGER_NAME)
        except Exception:
            pass
    pose = coin.SoTransform()
    scaled = coin.SoScale()
    try:
        scaled.scaleFactor.setValue(float(scale), float(scale), float(scale))
    except Exception:
        pass
    parts: dict[str, Any] = {"pose": pose, "scale": scaled}
    colors = {
        "x": (0.86, 0.18, 0.18),
        "y": (0.18, 0.72, 0.22),
        "z": (0.18, 0.42, 0.92),
    }
    root.addChild(pose)
    root.addChild(scaled)
    for axis in ("x", "y", "z"):
        group = coin.SoSeparator()
        group.addChild(_axis_aligned_rotation(coin, axis))
        material = coin.SoMaterial()
        material.diffuseColor.setValue(*colors[axis])
        material.emissiveColor.setValue(*tuple(component * 0.35 for component in colors[axis]))
        group.addChild(material)
        arrow = _coin_type_instance(coin, "SoTranslate1Dragger", "Translate1Dragger")
        if arrow is None:
            continue
        group.addChild(arrow)
        root.addChild(group)
        parts[axis] = arrow
    if rotate_type is not None:
        pitch_group = coin.SoSeparator()
        pitch_rot = coin.SoRotation()
        pitch_rot.rotation.setValue(_coin_vec3(coin, (0.0, 0.0, 1.0)), radians(90.0))
        pitch_group.addChild(pitch_rot)
        pitch = _coin_type_instance(coin, "SoRotateCylindricalDragger", "RotateCylindricalDragger")
        if pitch is not None:
            pitch_group.addChild(pitch)
            root.addChild(pitch_group)
            parts["pitch"] = pitch
        yaw = _coin_type_instance(coin, "SoRotateCylindricalDragger", "RotateCylindricalDragger")
        if yaw is not None:
            root.addChild(yaw)
            parts["yaw"] = yaw
    if "x" not in parts or "y" not in parts or "z" not in parts:
        return None
    if "pitch" not in parts:
        parts["pitch"] = None
    if "yaw" not in parts:
        parts["yaw"] = None
    return root, parts


def _view_look_direction(view: Any) -> tuple[float, float, float] | None:
    if view is None:
        try:
            import FreeCADGui as Gui

            view = getattr(getattr(Gui, "ActiveDocument", None), "ActiveView", None)
        except Exception:
            view = None
    if view is None:
        return None
    getter = getattr(view, "getViewDirection", None)
    if callable(getter):
        try:
            return _unit(_vec3(getter()))
        except Exception:
            pass
    camera = _view_camera(view)
    if camera is not None:
        volume_getter = getattr(camera, "getViewVolume", None)
        if callable(volume_getter):
            try:
                volume = volume_getter()
                projection = getattr(volume, "getProjectionDirection", None)
                if callable(projection):
                    return _unit(_vec3(projection()))
            except Exception:
                pass
    getter = getattr(view, "getCameraOrientation", None)
    if callable(getter):
        try:
            rotation = getter()
            quat = getattr(rotation, "Q", None)
            if quat is not None and len(tuple(quat)) >= 4:
                values = tuple(float(part) for part in quat)
                return _unit(_quat_rotate(values, (0.0, 0.0, -1.0)))
            multiply = getattr(rotation, "multVec", None)
            if callable(multiply) and App is not None:
                return _unit(_vec3(multiply(App.Vector(0.0, 0.0, -1.0))))
        except Exception:
            pass
    camera = _view_camera(view)
    if camera is None:
        return None
    orientation = getattr(camera, "orientation", None)
    getter = getattr(orientation, "getValue", None)
    if not callable(getter):
        return None
    try:
        quat = getter()
        nested = getattr(quat, "getValue", None)
        if callable(nested):
            quat = nested()
        return _unit(
            _quat_rotate(
                (float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3])),
                (0.0, 0.0, -1.0),
            )
        )
    except Exception:
        return None


def _view_camera(view: Any) -> Any | None:
    for name in ("getCameraNode", "getCamera"):
        getter = getattr(view, name, None)
        if not callable(getter):
            continue
        try:
            camera = getter()
        except Exception:
            camera = None
        if camera is not None:
            return camera
    get_viewer = getattr(view, "getViewer", None)
    if callable(get_viewer):
        try:
            viewer = get_viewer()
        except Exception:
            viewer = None
        if viewer is not None:
            manager = getattr(viewer, "getSoRenderManager", None)
            if callable(manager):
                try:
                    render = manager()
                    camera = render.getCamera() if render is not None else None
                except Exception:
                    camera = None
                if camera is not None:
                    return camera
    return None


def world_scale_for_ndc(
    view: Any,
    origin: tuple[float, float, float],
    ndc_size: float = _DRAGGER_NDC_SIZE,
) -> float | None:
    """World size that matches Body Transform's screen-relative dragger."""

    camera = _view_camera(view)
    if camera is None:
        return None
    volume_getter = getattr(camera, "getViewVolume", None)
    if not callable(volume_getter):
        return None
    try:
        from pivy import coin
    except ImportError:
        return None
    try:
        volume = volume_getter()
        scale_getter = getattr(volume, "getWorldToScreenScale", None)
        if not callable(scale_getter):
            return None
        return float(scale_getter(_coin_vec3(coin, origin), float(ndc_size) / 2.0))
    except Exception:
        return None


def _set_scale_factor(node: Any, scale: float) -> bool:
    field = getattr(node, "scaleFactor", None)
    if field is None:
        field = getattr(node, "draggerSize", None)
    setter = getattr(field, "setValue", None)
    if not callable(setter):
        return False
    disconnect = getattr(field, "disconnect", None)
    if callable(disconnect):
        try:
            disconnect()
        except Exception:
            pass
    try:
        setter(float(scale), float(scale), float(scale))
        return True
    except TypeError:
        try:
            setter(float(scale))
            return True
        except Exception:
            return False
    except Exception:
        return False


def _autoscale_dragger(view: Any, origin: tuple[float, float, float]) -> None:
    global _last_dragger_scale
    if _dragger_node is None:
        return
    scale = world_scale_for_ndc(view, origin, _DRAGGER_NDC_SIZE)
    if scale is None or scale <= 0.0:
        bounds = _bounds_for_view(view, None)
        if bounds is None:
            scale = 20.0
        else:
            diagonal = (
                (bounds.xmax - bounds.xmin) ** 2
                + (bounds.ymax - bounds.ymin) ** 2
                + (bounds.zmax - bounds.zmin) ** 2
            ) ** 0.5
            scale = max(diagonal * 0.12, 15.0)
    if (_last_dragger_scale is not None
            and abs(scale - _last_dragger_scale) <= 1e-6 * max(abs(scale), 1.0)):
        return
    target = None
    if _triad_parts is not None:
        target = _triad_parts.get("scale")
    if target is None:
        getter = getattr(_dragger_node, "getPart", None)
        if callable(getter):
            try:
                target = getter("scaleNode", 0)
            except Exception:
                target = None
    if target is None:
        target = _dragger_node
    if _set_scale_factor(target, scale):
        _last_dragger_scale = scale


def _field_quat(node: Any) -> tuple[float, float, float, float] | None:
    field = getattr(node, "rotation", None)
    getter = getattr(field, "getValue", None)
    if not callable(getter):
        return None
    try:
        value = getter()
    except Exception:
        return None
    nested = getattr(value, "getValue", None)
    if callable(nested):
        try:
            value = nested()
        except Exception:
            pass
    try:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except Exception:
        return None


def _quat_rotate(
    quat: tuple[float, float, float, float],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z, w = quat
    vx, vy, vz = vector
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def _current_dragger_origin() -> tuple[float, float, float] | None:
    if _triad_parts is not None:
        dragged = _triad_origin()
        if dragged is not None:
            return dragged
        pose = _triad_parts.get("pose")
        if pose is not None:
            return _field_vec3(pose, "translation")
        return None
    if _dragger_node is None:
        return None
    return _field_vec3(_dragger_node, "translation")


def _snapshot_polled_pose() -> None:
    global _last_polled_origin, _last_polled_quat, _stable_ticks
    _last_polled_origin = _current_dragger_origin()
    if _triad_parts is None and _dragger_node is not None:
        _last_polled_quat = _field_quat(_dragger_node)
    else:
        _last_polled_quat = None
    _stable_ticks = 0


def _start_dragger_poll() -> None:
    global _poll_timer
    if _poll_timer is not None:
        return
    try:
        from PySide import QtCore
    except ImportError:
        try:
            from PySide6 import QtCore
        except ImportError:
            return
    timer = QtCore.QTimer()
    timer.setInterval(_POLL_MS)
    timer.timeout.connect(_poll_dragger)
    timer.start()
    _poll_timer = timer


def _stop_dragger_poll() -> None:
    global _poll_timer, _last_polled_origin, _last_polled_quat, _stable_ticks
    timer = _poll_timer
    _poll_timer = None
    _last_polled_origin = None
    _last_polled_quat = None
    _stable_ticks = 0
    if timer is None:
        return
    try:
        timer.stop()
        timer.deleteLater()
    except Exception:
        return


def _end_polled_drag() -> None:
    global _dragger_busy, _drag_start_settings, _drag_start_origin, _drag_start_axes
    global _drag_start_rot_counts, _stable_ticks
    if not _dragger_busy:
        return
    _dragger_busy = False
    _drag_start_settings = None
    _drag_start_origin = None
    _drag_start_axes = None
    _drag_start_rot_counts = None
    _stable_ticks = 0
    clearer = getattr(_dragger_node, "clearIncrementCounts", None)
    if callable(clearer):
        try:
            clearer()
        except Exception:
            pass
    configure_section_view(preview=False, sync_dragger=True)
    _refresh_dialog()


def _poll_dragger() -> None:
    global _dragger_busy, _drag_start_settings, _drag_start_origin, _drag_start_axes
    global _drag_start_rot_counts, _last_polled_origin, _last_polled_quat, _stable_ticks
    if _dragger_node is None:
        return
    view = _active_3d_view()
    # A queued timeout can arrive after deactivation. Do not read the old
    # scene's Pivy nodes when another view (or no view) is active.
    if view is None or view != _dragger_view:
        return
    if (_cap_dirty and not _dragger_busy and view is not None
            and _dragger_document is not None and _dragger_document.isClosable()):
        _sync_overlay(view, _dragger_document, _settings)
    if not _settings.show_handles:
        return
    origin = _current_dragger_origin()
    if origin is not None and view is not None:
        _autoscale_dragger(view, origin)
    if origin is None:
        return
    quat = None
    if _triad_parts is None:
        quat = _field_quat(_dragger_node)
    previous_quat = _last_polled_quat
    moved = False
    quat_changed = False
    if _last_polled_origin is not None:
        delta = _sub(origin, _last_polled_origin)
        if _dot(delta, delta) > 1.0e-6:
            moved = True
    if quat is not None and previous_quat is not None:
        quat_changed = any(
            abs(left - right) > 1.0e-4 for left, right in zip(quat, previous_quat)
        )
        if quat_changed:
            moved = True
    _last_polled_origin = origin
    if quat is not None:
        _last_polled_quat = quat
    if not moved:
        if _dragger_busy:
            _stable_ticks += 1
            if _stable_ticks >= _STABLE_TICKS:
                _end_polled_drag()
        return
    _stable_ticks = 0
    center = _dragger_center(None)
    if not _dragger_busy:
        _dragger_busy = True
        _drag_start_settings = _settings
        _drag_start_origin, _unused = clip_plane_from_settings(_settings, center)
        _drag_start_axes = section_cs_axes(
            _settings.plane,
            _settings.flipped,
            _settings.yaw,
            _settings.pitch,
            _settings.roll,
        )
        if _dragger_node is not None and _triad_parts is None:
            _drag_start_rot_counts = (
                _field_int(_dragger_node, "rotationIncrementCountX"),
                _field_int(_dragger_node, "rotationIncrementCountY"),
                _field_int(_dragger_node, "rotationIncrementCountZ"),
            )
        else:
            _drag_start_rot_counts = (0, 0, 0)
    start = _drag_start_settings if _drag_start_settings is not None else _settings
    start_origin = _drag_start_origin if _drag_start_origin is not None else origin
    start_axes = _drag_start_axes or section_cs_axes(
        start.plane, start.flipped, start.yaw, start.pitch, start.roll
    )
    motion = _sub(origin, start_origin)
    translation_counts = (
        int(round(_dot(motion, start_axes[0]) * 10.0)),
        int(round(_dot(motion, start_axes[1]) * 10.0)),
        int(round(_dot(motion, start_axes[2]) * 10.0)),
    )
    rotation_counts = (0, 0, 0)
    if _triad_parts is not None:
        rotation_counts = (
            int(round(_rotation_angle_degrees(_triad_parts.get("pitch")))),
            int(round(_rotation_angle_degrees(_triad_parts.get("yaw")))),
            0,
        )
    elif _dragger_node is not None:
        start_counts = _drag_start_rot_counts or (0, 0, 0)
        rotation_counts = (
            _field_int(_dragger_node, "rotationIncrementCountX") - start_counts[0],
            _field_int(_dragger_node, "rotationIncrementCountY") - start_counts[1],
            _field_int(_dragger_node, "rotationIncrementCountZ") - start_counts[2],
        )
    origin_moved = _dot(motion, motion) > 1.0
    if any(rotation_counts) and not origin_moved:
        updated = apply_dragger_rotation(start, origin, center, rotation_counts)
    elif quat is not None and quat_changed and not origin_moved:
        updated = apply_world_rotation(start, origin, center, quat)
    else:
        updated = apply_dragger_translation(start, origin, center, translation_counts)
    configure_section_view(
        plane=updated.plane,
        offset=updated.offset,
        flipped=updated.flipped,
        yaw=updated.yaw,
        pitch=updated.pitch,
        roll=updated.roll,
        preview=True,
        sync_dragger=False,
    )
    _refresh_dialog()


_plane_drag_filter = None


def _stop_plane_drag():
    global _plane_drag_filter
    handler, _plane_drag_filter = _plane_drag_filter, None
    if handler is not None:
        try:
            handler.widget.removeEventFilter(handler)
            handler.deleteLater()
        except RuntimeError:
            pass


def _start_plane_drag(view):
    """Handle guide drags on the owning Qt viewport, without Coin callbacks."""
    global _plane_drag_filter
    if _plane_drag_filter is not None or not callable(getattr(view, "graphicsView", None)):
        return
    from PySide import QtCore, QtGui

    class PlaneDragFilter(QtCore.QObject):
        def __init__(self, widget):
            super().__init__(widget)
            self.widget = widget
            self.dragging = False

        def eventFilter(self, watched, event):
            if _active_3d_view() != view or _dragger_view != view:
                self.dragging = False
                return False
            kind = event.type()
            if kind == QtCore.QEvent.MouseButtonPress:
                if (event.button() != QtCore.Qt.LeftButton
                        or event.modifiers() != QtCore.Qt.NoModifier
                        or not _settings.show_plane or _overlay_node is None):
                    return False
                bounds = _bounds_for_view(view, _dragger_document)
                center = bounds.center if bounds else (0, 0, 0)
                origin, normal = clip_plane_from_settings(_settings, center)
                width, height = _overlay_size(bounds, normal)

                def project(point):
                    x, y = view.getPointOnScreen(App.Vector(*point))
                    return QtCore.QPointF(x, watched.height() - 1 - y)

                corners = section_plane_corners(origin, normal, width, height)
                polygon = QtGui.QPolygonF([project(point) for point in corners])
                position = event.position()
                if not polygon.containsPoint(position, QtCore.Qt.OddEvenFill):
                    return False
                # The native gizmo keeps its own arrow/ring interaction when
                # both the guide and a handle lie under the cursor.
                from pivy import coin
                pixel_x, pixel_y = int(position.x()), watched.height() - 1 - int(position.y())
                ray_point = view.getPoint(pixel_x, pixel_y)
                direction = view.getCameraOrientation().multVec(App.Vector(0, 0, -1))
                camera = _view_camera(view)
                if camera is not None:
                    ray_point = ray_point - direction * float(camera.farDistance.getValue())
                pick = coin.SoRayPickAction(coin.SbViewportRegion(watched.width(), watched.height()))
                pick.setRay(_coin_vec3(coin, _vec3(ray_point)), _coin_vec3(coin, _vec3(direction)))
                pick.setPickAll(True)
                pick.apply(_scene_from_view(view))
                if any(point.getPath().containsNode(_dragger_node) for point in pick.getPickedPointList()):
                    return False
                self.start_position = position
                self.start_settings = _settings
                self.normal = normal
                self.center = center
                self.bounds = bounds
                self.axis = project(tuple(origin[i] + normal[i] for i in range(3))) - project(origin)
                # Looking straight at the cut collapses its normal on screen;
                # vertical dragging then uses the view's world-units-per-pixel.
                a = view.getPoint(pixel_x, pixel_y)
                b = view.getPoint(pixel_x, pixel_y + 1)
                # A face-on plane has no projected normal. Map an upward pull
                # to the positive direction of the active section normal, so
                # Flip reverses both the handle and its interaction together.
                self.pixel_scale = (b - a).Length
                self.dragging = True
                return True
            if not self.dragging:
                return False
            if kind == QtCore.QEvent.MouseMove:
                delta = event.position() - self.start_position
                length2 = self.axis.x() ** 2 + self.axis.y() ** 2
                distance = ((delta.x() * self.axis.x() + delta.y() * self.axis.y()) / length2
                            if length2 > 0.25 else -delta.y() * self.pixel_scale)
                offset = self.start_settings.offset + distance
                if self.bounds is not None:
                    low, high = offset_range_along_normal(self.bounds, self.normal)
                    offset = max(low, min(high, offset))
                configure_section_view(offset=offset, view=view, document=_dragger_document,
                                       preview=True, sync_dragger=False)
                _sync_plane_guide(view, _dragger_document, _settings)
                _refresh_dialog()
                return True
            if kind == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                self.dragging = False
                configure_section_view(view=view, document=_dragger_document)
                _refresh_dialog()
                return True
            return False

    widget = view.graphicsView().viewport()
    handler = PlaneDragFilter(widget)
    widget.installEventFilter(handler)
    _plane_drag_filter = handler


def _sync_dragger(
    view: Any,
    document: Any | None,
    settings: SectionViewSettings,
) -> None:
    global _dragger_node, _triad_parts
    if _dragger_busy and _dragger_node is not None:
        return
    try:
        from pivy import coin
    except ImportError:
        return
    scene = _scene_from_view(view)
    if scene is None:
        return
    bounds = _bounds_for_view(view, document)
    center = bounds.center if bounds is not None else (0.0, 0.0, 0.0)
    origin, _normal = clip_plane_from_settings(settings, center)
    axes = section_cs_axes(
        settings.plane, settings.flipped, settings.yaw, settings.pitch, settings.roll
    )
    if _dragger_node is None:
        dragger = _create_transform_dragger(coin)
        if dragger is not None:
            set_name = getattr(dragger, "setName", None)
            if callable(set_name):
                try:
                    set_name(_DRAGGER_NAME)
                except Exception:
                    pass
            increment = getattr(dragger, "translationIncrement", None)
            inc_set = getattr(increment, "setValue", None)
            if callable(inc_set):
                try:
                    inc_set(0.1)
                except Exception:
                    pass
            rot_inc = getattr(dragger, "rotationIncrement", None)
            rot_set = getattr(rot_inc, "setValue", None)
            if callable(rot_set):
                try:
                    rot_set(radians(1.0))
                except Exception:
                    pass
            _hide_dragger_clutter(dragger)
        else:
            diagonal = 50.0
            if bounds is not None:
                diagonal = (
                    (bounds.xmax - bounds.xmin) ** 2
                    + (bounds.ymax - bounds.ymin) ** 2
                    + (bounds.zmax - bounds.zmin) ** 2
                ) ** 0.5
            built = _build_section_triad(coin, max(diagonal / 40.0, 0.8))
            if built is None:
                return
            dragger, _triad_parts = built
        try:
            scene.insertChild(dragger, 0)
        except Exception:
            _triad_parts = None
            return
        # Keep a native reference while the handles are detached. A Python
        # wrapper alone does not keep a Coin node alive after removeChild.
        dragger.ref()
        _dragger_node = dragger
        _observe_section_document(view, document)
        _start_dragger_poll()
    _set_dragger_pose(coin, _dragger_node, origin, axes)
    _autoscale_dragger(view, origin)
    _snapshot_polled_pose()
    _start_plane_drag(view)
    if settings.show_handles:
        if scene.findChild(_dragger_node) < 0:
            scene.insertChild(_dragger_node, 0)
    else:
        _detach_scene_node(scene, _dragger_node)
    touch = getattr(scene, "touch", None)
    if callable(touch):
        try:
            touch()
        except Exception:
            pass


def _refresh_dialog() -> None:
    try:
        import VibeCADSectionViewGui as gui
    except Exception:
        return
    refresh = getattr(gui, "refresh_section_view_dialog", None)
    if callable(refresh):
        refresh()


def _apply_clip(
    view: Any,
    document: Any | None,
    settings: SectionViewSettings,
    *,
    preview: bool = False,
    sync_dragger: bool = True,
) -> None:
    global _bounds_view, _section_bounds
    if _dragger_view is not None and view != _dragger_view:
        # The section editor owns one scene. Transfer it before attaching new
        # nodes, rather than leaving the previous view with dangling wrappers.
        set_section_view(False, view=_dragger_view, document=_dragger_document)
    if view != _bounds_view:
        _section_bounds = _render_bounds(view)
        if _section_bounds is None:
            _section_bounds = model_bounds(_document_objects(document))
        # Keep the section's world-space reference stable throughout a drag;
        # subsequently added cap/plane/gizmo nodes must not affect its bounds.
        _bounds_view = view
    placement = _placement_from_bounds(settings, _section_bounds)
    if is_section_view_active(view):
        if not _update_clip_plane(view, placement):
            view.toggleClippingPlane(toggle=0)
            view.toggleClippingPlane(toggle=1, noManip=True, pla=placement)
    else:
        view.toggleClippingPlane(toggle=1, noManip=True, pla=placement)
    if preview:
        if _cap_worker is not None:
            _cap_worker.cancel()
        return
    _sync_overlay(view, document, settings, rebuild_caps=True)
    if sync_dragger:
        _sync_dragger(view, document, settings)


def _close_ui() -> None:
    try:
        import VibeCADSectionViewGui as gui
    except Exception:
        return
    closer = getattr(gui, "close_section_view_dialog", None)
    if callable(closer):
        closer()


def _show_ui() -> None:
    try:
        import VibeCADSectionViewGui as gui
    except Exception:
        return
    shower = getattr(gui, "show_section_view_dialog", None)
    if callable(shower):
        shower()


def snap_section_to_point(
    point: tuple[float, float, float],
    *,
    axis: tuple[float, float, float] | None = None,
    view: Any | None = None,
    document: Any | None = None,
) -> dict[str, object]:
    """Move the section through ``point``. Holes are sliced along their depth."""

    center = bounds_center(_document_objects(document)) or (0.0, 0.0, 0.0)
    look = _view_look_direction(view if view is not None else _active_3d_view())
    updated = apply_feature_snap(
        current_section_view_settings(),
        center,
        point,
        axis,
        look,
    )
    return configure_section_view(
        plane=updated.plane,
        offset=updated.offset,
        flipped=updated.flipped,
        yaw=updated.yaw,
        pitch=updated.pitch,
        roll=updated.roll,
        view=view,
        document=document,
    )


def _shape_from_selection(document_name: Any, object_name: Any, sub_name: Any) -> Any | None:
    if App is None:
        return None
    try:
        document = App.getDocument(str(document_name)) if document_name else App.ActiveDocument
        obj = document.getObject(str(object_name)) if document is not None else None
    except Exception:
        return None
    if obj is None:
        return None
    shape = getattr(obj, "Shape", None)
    sub = str(sub_name or "")
    if sub and shape is not None:
        getter = getattr(shape, "getElement", None)
        if callable(getter):
            try:
                return getter(sub)
            except Exception:
                return shape
    return shape


def _current_selection_snap_geometry() -> tuple[
    tuple[float, float, float] | None,
    tuple[float, float, float] | None,
]:
    try:
        import FreeCADGui as Gui
    except ImportError:
        return None, None
    selection = getattr(Gui, "Selection", None)
    get_ex = getattr(selection, "getSelectionEx", None)
    if not callable(get_ex):
        return None, None
    try:
        selected = tuple(get_ex() or ())
    except Exception:
        return None, None
    for sel in selected:
        for subshape in tuple(getattr(sel, "SubObjects", ()) or ()):
            point, axis = snap_geometry_from_shape(subshape)
            if point is not None:
                return point, axis
        point, axis = snap_geometry_from_shape(
            getattr(getattr(sel, "Object", None), "Shape", None)
        )
        if point is not None:
            return point, axis
    return None, None


class _SectionSnapObserver:
    def addSelection(self, document: Any, object_name: Any, sub_name: Any, *_args: Any) -> None:
        if not is_section_view_active():
            return
        shape = _shape_from_selection(document, object_name, sub_name)
        point, axis = snap_geometry_from_shape(shape)
        if point is None:
            return
        snap_section_to_point(point, axis=axis)
        _refresh_dialog()

    def setPreselection(self, *_args: Any) -> None:
        return

    def removeSelection(self, *_args: Any) -> None:
        return

    def clearSelection(self, *_args: Any) -> None:
        return


def _start_selection_snap() -> None:
    global _selection_observer
    if _selection_observer is not None:
        return
    try:
        import FreeCADGui as Gui
    except ImportError:
        return
    adder = getattr(getattr(Gui, "Selection", None), "addObserver", None)
    if not callable(adder):
        return
    observer = _SectionSnapObserver()
    try:
        adder(observer)
    except Exception:
        return
    _selection_observer = observer


def _stop_selection_snap() -> None:
    global _selection_observer
    observer = _selection_observer
    _selection_observer = None
    if observer is None:
        return
    try:
        import FreeCADGui as Gui

        remover = getattr(getattr(Gui, "Selection", None), "removeObserver", None)
        if callable(remover):
            remover(observer)
    except Exception:
        return


def set_section_view(
    visible: bool,
    *,
    view: Any | None = None,
    document: Any | None = None,
    show_ui: bool = False,
) -> dict[str, bool]:
    """Enable or disable the active 3D section clip."""

    global _settings
    if type(visible) is not bool:
        raise TypeError("visible must be a boolean")
    active = view if view is not None else _active_3d_view()
    if active is None:
        raise RuntimeError("Section view requires an active 3D view.")
    current = is_section_view_active(active)
    if current == visible:
        if visible and show_ui:
            _show_ui()
        if not visible:
            _stop_selection_snap()
            _remove_overlay(active)
            _remove_dragger(active)
            _close_ui()
        return {"section_view": current}
    toggle = getattr(active, "toggleClippingPlane", None)
    if not callable(toggle):
        raise RuntimeError("The active 3D view cannot toggle a section plane.")
    if visible:
        _apply_clip(active, document, _settings)
        _start_selection_snap()
        if show_ui:
            _show_ui()
    else:
        _stop_selection_snap()
        _remove_overlay(active)
        _remove_dragger(active)
        toggle(toggle=0)
        _close_ui()
    observed = is_section_view_active(active)
    if observed != visible:
        raise RuntimeError("The active 3D view did not reach the requested section state.")
    return {"section_view": observed}


def configure_section_view(
    *,
    plane: str | None = None,
    offset: float | None = None,
    flipped: bool | None = None,
    show_plane: bool | None = None,
    yaw: float | None = None,
    pitch: float | None = None,
    roll: float | None = None,
    view: Any | None = None,
    document: Any | None = None,
    preview: bool = False,
    sync_dragger: bool = True,
    show_handles: bool | None = None,
) -> dict[str, object]:
    """Update the live Front/Top/Right section without changing geometry."""

    global _settings
    updates: dict[str, object] = {}
    if show_handles is not None:
        updates["show_handles"] = show_handles
    if plane is not None:
        updates["plane"] = plane
    if offset is not None:
        updates["offset"] = offset
    if flipped is not None:
        updates["flipped"] = flipped
    if show_plane is not None:
        updates["show_plane"] = show_plane
    if yaw is not None:
        updates["yaw"] = yaw
    if pitch is not None:
        updates["pitch"] = pitch
    if roll is not None:
        updates["roll"] = roll
    _settings = replace(_settings, **updates) if updates else _settings
    active = view if view is not None else _active_3d_view()
    if active is not None and is_section_view_active(active):
        if updates and updates.keys() <= {"show_plane", "show_handles"}:
            if "show_plane" in updates:
                _sync_plane_guide(active, document, _settings)
            if "show_handles" in updates:
                _sync_dragger(active, document, _settings)
        else:
            _apply_clip(
                active,
                document,
                _settings,
                preview=preview,
                sync_dragger=sync_dragger,
            )
    return {
        "plane": _settings.plane,
        "offset": _settings.offset,
        "flipped": _settings.flipped,
        "show_plane": _settings.show_plane,
        "show_handles": _settings.show_handles,
        "yaw": _settings.yaw,
        "pitch": _settings.pitch,
        "roll": _settings.roll,
        "section_view": is_section_view_active(active) if active is not None else False,
    }


def toggle_section_view(
    *,
    view: Any | None = None,
    document: Any | None = None,
    show_ui: bool = True,
) -> dict[str, bool]:
    """Toggle the active 3D section clip and return the resulting state."""

    active = view if view is not None else _active_3d_view()
    if active is None:
        raise RuntimeError("Section view requires an active 3D view.")
    if is_section_view_active(active):
        return set_section_view(False, view=active, document=document)
    global _settings
    _settings = initial_section_settings(_view_look_direction(active))
    return set_section_view(
        True,
        view=active,
        document=document,
        show_ui=show_ui,
    )


def request_section_view_toggle(
    *,
    view: Any | None = None,
    document: Any | None = None,
    show_ui: bool = True,
) -> dict[str, bool]:
    """Toggle after an in-flight camera move reaches its requested orientation."""

    import FreeCADGui as Gui

    active = view if view is not None else _active_3d_view()
    if active is None:
        raise RuntimeError("Section view requires an active 3D view.")
    if is_section_view_active(active):
        return toggle_section_view(view=active, document=document, show_ui=show_ui)

    global _pending_toggle_generation
    _pending_toggle_generation += 1
    generation = _pending_toggle_generation

    def apply_when_camera_settles() -> None:
        if generation != _pending_toggle_generation:
            return
        try:
            animating = bool(active.isAnimating())
        except RuntimeError:
            return
        if animating:
            if not Gui.deferToNextFrame(apply_when_camera_settles):
                raise RuntimeError("The GUI frame dispatcher is shutting down.")
            return
        toggle_section_view(view=active, document=document, show_ui=show_ui)

    if active.isAnimating():
        if not Gui.deferToNextFrame(apply_when_camera_settles):
            raise RuntimeError("The GUI frame dispatcher is shutting down.")
        return {"section_view": False}
    return toggle_section_view(view=active, document=document, show_ui=show_ui)
