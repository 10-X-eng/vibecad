# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Path Boundary dress-up."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeManufactureDressupSupport import (
    MAX_DRESSUP_COMMANDS,
    PreparedDressupBase,
    assert_dressup_preflight_current,
    command_path_sha256,
    cutting_command_count,
    dressup_error,
    normalize_exact_target,
    preflight_dressup_base,
    publish_dressup_replacement,
    verify_dressup_envelope,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    exact_fields,
    finite_number,
)
from VibeCADNativeManufactureState import (
    candidate_model_state,
    persistent_resource_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_BOUNDARY_EDGES = 10_000
MAX_BOUNDARY_FACES = 10_000
MAX_BOUNDARY_BREP_BYTES = 16 * 1024 * 1024
MAX_BOUNDARY_INTERSECTION_WORK = 5_000_000
_EPSILON = 1.0e-9
_MODEL_BOUNDS_FIELDS = frozenset(
    {
        "kind",
        "x_negative_mm",
        "x_positive_mm",
        "y_negative_mm",
        "y_positive_mm",
        "z_negative_mm",
        "z_positive_mm",
    }
)
_BOX_FIELDS = frozenset(
    {"kind", "length_mm", "width_mm", "height_mm", "placement"}
)
_CYLINDER_FIELDS = frozenset(
    {"kind", "radius_mm", "height_mm", "placement"}
)
_EXISTING_SOLID_FIELDS = frozenset({"kind", "source"})
_PLACEMENT_FIELDS = frozenset(
    {"origin_mm", "rotation_axis", "rotation_degrees"}
)
_VECTOR_FIELDS = frozenset({"x", "y", "z"})


@dataclass(frozen=True, slots=True)
class PathBoundaryDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    boundary: Mapping[str, Any]
    inside: Any
    offset_mm: Any
    retract_threshold_mm: Any
    rest_machining_pass: Any


@dataclass(frozen=True, slots=True)
class BoundaryPlacement:
    origin_mm: tuple[float, float, float]
    axis: tuple[float, float, float]
    angle_degrees: float
    quaternion: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PreparedPathBoundaryDressup:
    base: PreparedDressupBase
    kind: str
    settings: Mapping[str, Any]
    placement: BoundaryPlacement | None
    source: Any | None
    source_state_before: Mapping[str, Any] | None
    source_candidate_state_sha256: str | None
    source_geometry: tuple[tuple[Any, ...], ...] | None
    model_resource_states: tuple[tuple[Any, Mapping[str, Any]], ...]
    inside: bool
    offset_mm: float
    retract_threshold_mm: float
    rest_machining_pass: bool
    stock_geometry: tuple[tuple[Any, ...], ...]
    effective_boundary_geometry: tuple[tuple[Any, ...], ...]
    boundary_edge_count: int
    boundary_face_count: int
    intersection_work: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def _nonnegative(value: Any, noun: str) -> float:
    return finite_number(value, noun, minimum=0.0, maximum=1_000_000.0)


def _positive(value: Any, noun: str) -> float:
    result = _nonnegative(value, noun)
    if result < 0.001:
        dressup_error(f"{noun} must be at least 0.001 mm.")
    return result


def _vector(value: Any, noun: str, *, direction: bool = False) -> tuple[float, float, float]:
    item = exact_fields(value, _VECTOR_FIELDS, noun)
    limit = 1.0 if direction else 1_000_000.0
    result = tuple(
        finite_number(
            item[axis],
            f"{noun}.{axis}",
            minimum=-limit,
            maximum=limit,
        )
        for axis in ("x", "y", "z")
    )
    if direction:
        magnitude = math.sqrt(sum(component * component for component in result))
        if magnitude <= _EPSILON:
            dressup_error(f"{noun} must be one nonzero direction.")
        result = tuple(round(component / magnitude, 12) for component in result)
    return result


def _prepare_placement(value: Any, noun: str) -> BoundaryPlacement:
    item = exact_fields(value, _PLACEMENT_FIELDS, noun)
    origin = _vector(item["origin_mm"], f"{noun}.origin_mm")
    axis = _vector(
        item["rotation_axis"],
        f"{noun}.rotation_axis",
        direction=True,
    )
    angle = finite_number(
        item["rotation_degrees"],
        f"{noun}.rotation_degrees",
        minimum=-360_000.0,
        maximum=360_000.0,
    )
    try:
        import FreeCAD as App

        quaternion = tuple(
            round(float(component), 12)
            for component in App.Rotation(App.Vector(*axis), angle).Q
        )
    except Exception as exc:
        raise NativeManufactureError(
            f"{noun} could not be converted to one valid placement.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    return BoundaryPlacement(origin, axis, angle, quaternion)


def _placement_value(prepared: BoundaryPlacement) -> Any:
    import FreeCAD as App

    return App.Placement(
        App.Vector(*prepared.origin_mm),
        App.Rotation(App.Vector(*prepared.axis), prepared.angle_degrees),
    )


def _placement_signature(value: Any) -> tuple[tuple[float, ...], tuple[float, ...]]:
    return (
        tuple(round(float(getattr(value.Base, axis)), 9) for axis in ("x", "y", "z")),
        tuple(round(float(component), 12) for component in value.Rotation.Q),
    )


def _boundary_label(value: Any) -> bool:
    label = str(value or "")
    suffix = label[len("Boundary") :] if label.startswith("Boundary") else ""
    return label == "Boundary" or (len(suffix) >= 3 and suffix.isdigit())


def _shape_parts(shape: Any) -> tuple[Any, ...]:
    if isinstance(shape, (list, tuple)):
        return tuple(shape)
    return (shape,)


def _shape_geometry_signature(shape: Any) -> tuple[tuple[Any, ...], ...]:
    """Canonical measurements for transient OCC offset results.

    BRep serialization can reorder equivalent offset subshapes between calls.
    Durable Stock still uses an exact BRep hash, while this transient shape is
    compared through complete bounds plus aggregate geometric measurements.
    The exact generated command hash separately proves clipping equivalence.
    """

    result = []
    for part in _shape_parts(shape):
        bounds = part.BoundBox
        result.append(
            (
                str(part.ShapeType),
                len(tuple(part.Vertexes or ())),
                len(tuple(part.Edges or ())),
                len(tuple(part.Faces or ())),
                round(float(getattr(part, "Length", 0.0)), 7),
                round(float(getattr(part, "Area", 0.0)), 7),
                round(float(getattr(part, "Volume", 0.0)), 7),
                *(round(float(getattr(bounds, name)), 7) for name in (
                    "XMin",
                    "YMin",
                    "ZMin",
                    "XMax",
                    "YMax",
                    "ZMax",
                )),
            )
        )
    return tuple(sorted(result))


def _persistent_invariants(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in state.items()
        if name not in {"shape_sha256", "state_sha256"}
    }


def _boundary_metrics(shape: Any, noun: str) -> tuple[int, int, int]:
    edges = 0
    faces = 0
    encoded_size = 0
    for part in _shape_parts(shape):
        if part is None or part.isNull() or not part.isValid():
            dressup_error(
                f"{noun} does not produce one valid boundary solid.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        edges += len(tuple(part.Edges or ()))
        faces += len(tuple(part.Faces or ()))
        export = part.exportBrepToString()
        encoded_size += len(export if isinstance(export, bytes) else str(export).encode("utf-8"))
    if edges <= 0 or faces <= 0:
        dressup_error(
            f"{noun} must contain bounded faces and edges.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if (
        edges > MAX_BOUNDARY_EDGES
        or faces > MAX_BOUNDARY_FACES
        or encoded_size > MAX_BOUNDARY_BREP_BYTES
    ):
        dressup_error(
            f"{noun} is too complex for an interactive Path Boundary operation.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={
                "edge_count": edges,
                "face_count": faces,
                "brep_bytes": encoded_size,
                "maximum_edges": MAX_BOUNDARY_EDGES,
                "maximum_faces": MAX_BOUNDARY_FACES,
                "maximum_brep_bytes": MAX_BOUNDARY_BREP_BYTES,
            },
        )
    return edges, faces, encoded_size


def _model_bounds_shape(job: Any, settings: Mapping[str, Any]) -> Any:
    import FreeCAD as App
    import Part
    import Path.Main.Stock as PathStock

    bounds = PathStock.shapeBoundBox(list(job.Model.Group or ()))
    if bounds is None or not bounds.isValid():
        dressup_error(
            "CAM Path Boundary model_bounds requires a Job with valid model bounds.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    x_negative = float(settings["x_negative_mm"])
    x_positive = float(settings["x_positive_mm"])
    y_negative = float(settings["y_negative_mm"])
    y_positive = float(settings["y_positive_mm"])
    z_negative = float(settings["z_negative_mm"])
    z_positive = float(settings["z_positive_mm"])
    dimensions = (
        float(bounds.XLength) + x_negative + x_positive,
        float(bounds.YLength) + y_negative + y_positive,
        float(bounds.ZLength) + z_negative + z_positive,
    )
    if any(value < 0.001 for value in dimensions):
        dressup_error(
            "CAM Path Boundary model_bounds extensions must produce three positive dimensions.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    shape = Part.makeBox(
        *dimensions,
        App.Vector(-x_negative, -y_negative, -z_negative),
    )
    shape.Placement = App.Placement(
        App.Vector(bounds.XMin, bounds.YMin, bounds.ZMin),
        App.Rotation(),
    )
    return shape


def _definition_shape(
    prepared_base: PreparedDressupBase,
    kind: str,
    settings: Mapping[str, Any],
    placement: BoundaryPlacement | None,
    source: Any | None,
) -> Any:
    import Part

    if kind == "model_bounds":
        return _model_bounds_shape(prepared_base.job, settings)
    if kind == "box":
        shape = Part.makeBox(
            float(settings["length_mm"]),
            float(settings["width_mm"]),
            float(settings["height_mm"]),
        )
        shape.Placement = _placement_value(placement)
        return shape
    if kind == "cylinder":
        shape = Part.makeCylinder(
            float(settings["radius_mm"]),
            float(settings["height_mm"]),
        )
        shape.Placement = _placement_value(placement)
        return shape
    if kind == "existing_solid" and source is not None:
        return source.Shape.copy()
    raise RuntimeError("Unsupported prepared CAM Path Boundary definition")


def _prepare_definition(
    prepared_base: PreparedDressupBase,
    request: Any,
) -> tuple[
    str,
    Mapping[str, Any],
    BoundaryPlacement | None,
    Any | None,
    Mapping[str, Any] | None,
    str | None,
    str | None,
]:
    if not isinstance(request, Mapping):
        dressup_error("CAM Path Boundary boundary must be one closed boundary request.")
    kind = str(request.get("kind") or "")
    if kind == "model_bounds":
        item = exact_fields(request, _MODEL_BOUNDS_FIELDS, "CAM Path Boundary model_bounds")
        settings = {
            name: _nonnegative(item[name], f"CAM Path Boundary {name}")
            for name in _MODEL_BOUNDS_FIELDS
            if name != "kind"
        }
        return kind, settings, None, None, None, None, None
    if kind == "box":
        item = exact_fields(request, _BOX_FIELDS, "CAM Path Boundary box")
        settings = {
            "length_mm": _positive(item["length_mm"], "CAM Path Boundary length_mm"),
            "width_mm": _positive(item["width_mm"], "CAM Path Boundary width_mm"),
            "height_mm": _positive(item["height_mm"], "CAM Path Boundary height_mm"),
        }
        placement = _prepare_placement(item["placement"], "CAM Path Boundary placement")
        return kind, settings, placement, None, None, None, None
    if kind == "cylinder":
        item = exact_fields(request, _CYLINDER_FIELDS, "CAM Path Boundary cylinder")
        settings = {
            "radius_mm": _positive(item["radius_mm"], "CAM Path Boundary radius_mm"),
            "height_mm": _positive(item["height_mm"], "CAM Path Boundary height_mm"),
        }
        placement = _prepare_placement(item["placement"], "CAM Path Boundary placement")
        return kind, settings, placement, None, None, None, None
    if kind == "existing_solid":
        item = exact_fields(
            request,
            _EXISTING_SOLID_FIELDS,
            "CAM Path Boundary existing_solid",
        )
        target = normalize_exact_target(
            item["source"],
            "CAM Path Boundary existing_solid source",
        )
        source = prepared_base.job.Document.getObject(target["object_name"])
        try:
            import Path.Main.Stock as PathStock

            candidates = PathStock.existingSolidCandidates(prepared_base.base)
            candidate_state = candidate_model_state(source)
        except Exception as exc:
            raise NativeManufactureError(
                "The exact Path Boundary existing solid could not be validated.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            ) from exc
        if source not in candidates:
            dressup_error(
                "CAM Path Boundary existing_solid must target one solid offered by the "
                "human Boundary editor.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                repair={
                    "available_object_names": [str(value.Name) for value in candidates]
                },
            )
        if candidate_state.get("state_sha256") != target["expected_state_sha256"]:
            dressup_error(
                f"CAM Path Boundary source {target['object_name']!r} changed after turn start.",
                "NATIVE_MANUFACTURE_STATE_STALE",
                repair={
                    "object_name": target["object_name"],
                    "current_state_sha256": candidate_state.get("state_sha256"),
                },
            )
        source_before = _persistent_invariants(persistent_resource_state(source))
        source_geometry = _shape_geometry_signature(source.Shape)
        return (
            kind,
            {},
            None,
            source,
            source_before,
            str(candidate_state["state_sha256"]),
            source_geometry,
        )
    dressup_error(
        "CAM Path Boundary boundary.kind must be model_bounds, box, cylinder, "
        "or existing_solid."
    )


def preflight_path_boundary_dressup(
    document: Any,
    spec: PathBoundaryDressupSpec,
) -> PreparedPathBoundaryDressup:
    """Freeze one exact base and prepare its complete boundary-clipped path."""

    if not isinstance(spec, PathBoundaryDressupSpec):
        raise TypeError("spec must be a PathBoundaryDressupSpec")
    if not isinstance(spec.inside, bool):
        dressup_error("CAM Path Boundary inside must be true or false.")
    if not isinstance(spec.rest_machining_pass, bool):
        dressup_error("CAM Path Boundary rest_machining_pass must be true or false.")
    offset = _nonnegative(spec.offset_mm, "CAM Path Boundary offset_mm")
    retract = _nonnegative(
        spec.retract_threshold_mm,
        "CAM Path Boundary retract_threshold_mm",
    )
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Path Boundary dress-up",
    )
    (
        kind,
        settings,
        placement,
        source,
        source_before,
        source_candidate_hash,
        source_geometry,
    ) = _prepare_definition(base, spec.boundary)
    model_states = (
        tuple(
            (resource, persistent_resource_state(resource))
            for resource in tuple(base.job.Model.Group or ())
        )
        if kind == "model_bounds"
        else ()
    )
    try:
        import Path.Dressup.Boundary as PathBoundary

        stock_shape = _definition_shape(base, kind, settings, placement, source)
        _boundary_metrics(stock_shape, "CAM Path Boundary stock")
        effective_shape = PathBoundary.offsetBoundaryShape(
            stock_shape,
            spec.inside,
            offset,
        )
        edges, faces, _ = _boundary_metrics(
            effective_shape,
            "CAM Path Boundary effective boundary",
        )
        moving_commands = sum(
            1
            for command in tuple(base.base.Path.Commands or ())
            if str(getattr(command, "Name", "")) in PathBoundary.Path.Geom.CmdMoveAll
        )
        intersection_work = moving_commands * max(1, edges)
        if intersection_work > MAX_BOUNDARY_INTERSECTION_WORK:
            dressup_error(
                "CAM Path Boundary clipping exceeds the interactive intersection-work limit.",
                "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
                repair={
                    "moving_command_count": moving_commands,
                    "boundary_edge_count": edges,
                    "intersection_work": intersection_work,
                    "maximum_intersection_work": MAX_BOUNDARY_INTERSECTION_WORK,
                },
            )
        expected_path = PathBoundary.createBoundaryPath(
            base.base,
            effective_shape,
            spec.inside,
            retract,
        )
        commands = tuple(expected_path.Commands or ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Path Boundary toolpath could not be prepared.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    cutting = cutting_command_count(commands)
    if len(commands) > MAX_DRESSUP_COMMANDS:
        dressup_error(
            f"CAM Path Boundary would generate {len(commands)} commands; the safety "
            f"limit is {MAX_DRESSUP_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    if cutting <= 0:
        dressup_error(
            "CAM Path Boundary excludes every cutting command; adjust the boundary, "
            "placement, offset, or inside setting.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    return PreparedPathBoundaryDressup(
        base=base,
        kind=kind,
        settings=settings,
        placement=placement,
        source=source,
        source_state_before=source_before,
        source_candidate_state_sha256=source_candidate_hash,
        source_geometry=source_geometry,
        model_resource_states=model_states,
        inside=spec.inside,
        offset_mm=offset,
        retract_threshold_mm=retract,
        rest_machining_pass=spec.rest_machining_pass,
        stock_geometry=_shape_geometry_signature(stock_shape),
        effective_boundary_geometry=_shape_geometry_signature(effective_shape),
        boundary_edge_count=edges,
        boundary_face_count=faces,
        intersection_work=intersection_work,
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=command_path_sha256(
            commands,
            "CAM Path Boundary dress-up",
        ),
    )


def _assert_preflight_current(
    document: Any,
    prepared: PreparedPathBoundaryDressup,
) -> None:
    assert_dressup_preflight_current(document, prepared.base)
    if any(
        persistent_resource_state(resource) != state
        for resource, state in prepared.model_resource_states
    ):
        dressup_error(
            "The CAM Path Boundary Job model changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if prepared.source is not None:
        try:
            import Path.Main.Stock as PathStock

            current_candidate = candidate_model_state(prepared.source)
            candidates = PathStock.existingSolidCandidates(prepared.base.base)
            current_geometry = _shape_geometry_signature(prepared.source.Shape)
        except Exception as exc:
            raise NativeManufactureError(
                "The exact Path Boundary source could not be revalidated.",
                error_code="NATIVE_MANUFACTURE_STATE_STALE",
            ) from exc
        if (
            prepared.source not in candidates
            or current_candidate.get("state_sha256")
            != prepared.source_candidate_state_sha256
            or current_geometry != prepared.source_geometry
            or _persistent_invariants(persistent_resource_state(prepared.source))
            != prepared.source_state_before
        ):
            dressup_error(
                "The CAM Path Boundary existing solid changed after preflight.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )


def _discard_default_stock(operation: Any) -> None:
    stock = operation.Stock
    operation.Stock = None
    if stock is not None:
        operation.Document.removeObject(stock.Name)


def _create_stock(prepared: PreparedPathBoundaryDressup, operation: Any) -> Any:
    import FreeCAD as App
    import Path.Base.Util as PathUtil
    import Path.Main.Job as PathJob
    import Path.Main.Stock as PathStock

    if prepared.kind == "model_bounds":
        stock = operation.Stock
        for property_name, setting_name in (
            ("ExtXneg", "x_negative_mm"),
            ("ExtXpos", "x_positive_mm"),
            ("ExtYneg", "y_negative_mm"),
            ("ExtYpos", "y_positive_mm"),
            ("ExtZneg", "z_negative_mm"),
            ("ExtZpos", "z_positive_mm"),
        ):
            setattr(stock, property_name, prepared.settings[setting_name])
        stock.Proxy.execute(stock)
    elif prepared.kind == "box":
        _discard_default_stock(operation)
        stock = PathStock.CreateBox(
            prepared.base.job,
            App.Vector(
                prepared.settings["length_mm"],
                prepared.settings["width_mm"],
                prepared.settings["height_mm"],
            ),
            _placement_value(prepared.placement),
        )
        stock.Proxy.execute(stock)
    elif prepared.kind == "cylinder":
        _discard_default_stock(operation)
        stock = PathStock.CreateCylinder(
            prepared.base.job,
            prepared.settings["radius_mm"],
            prepared.settings["height_mm"],
            _placement_value(prepared.placement),
        )
        stock.Proxy.execute(stock)
    elif prepared.kind == "existing_solid":
        _discard_default_stock(operation)
        stock = PathJob.createResourceClone(
            operation,
            prepared.source,
            "Stock",
            "Stock",
            recompute=False,
        )
        PathStock.SetupStockObject(stock, PathStock.StockType.Unknown)
    else:
        raise RuntimeError("Unsupported prepared CAM Path Boundary stock")

    PathUtil.markTimelineResource(stock, operation)
    operation.Proxy.promoteStockToBoundary(stock)
    operation.Stock = stock
    PathStock.ApplyStockViewDefaults(stock)
    stock.ViewObject.Visibility = False
    return stock


def create_path_boundary_dressup(
    document: Any,
    *,
    prepared: PreparedPathBoundaryDressup,
) -> NativeMutationDraft:
    """Create and configure one Path Boundary replacement in the owned transaction."""

    if not isinstance(prepared, PreparedPathBoundaryDressup):
        raise TypeError("prepared must be a PreparedPathBoundaryDressup")
    _assert_preflight_current(document, prepared)
    base = prepared.base
    try:
        import Path.Dressup.Gui.Boundary as BoundaryGui

        operation = BoundaryGui.CreateInTransaction(
            base.base,
            hide_base=False,
        )
        operation.Label = base.label
        stock = _create_stock(prepared, operation)
        operation.Inside = prepared.inside
        operation.Offset = prepared.offset_mm
        operation.RetractThreshold = prepared.retract_threshold_mm
        operation.RestMachiningPass = prepared.rest_machining_pass
        publish_dressup_replacement(
            document,
            base,
            operation,
            (stock,),
        )
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Path Boundary factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "operation": operation, "stock": stock},
        recompute_targets=(stock, operation),
        created=(object_identity(operation), object_identity(stock)),
        changed=(object_identity(base.job),),
        replaced=(object_identity(base.base),),
    )


def _verify_stock(
    prepared: PreparedPathBoundaryDressup,
    operation: Any,
    stock: Any,
) -> Mapping[str, Any]:
    import Path.Dressup.Boundary as PathBoundary
    import Path.Main.Stock as PathStock

    if (
        operation.Stock is not stock
        or not bool(getattr(stock, "IsBoundary", False))
        or not _boundary_label(stock.Label)
        or bool(stock.ViewObject.Visibility)
    ):
        dressup_error(
            "The created CAM Path Boundary lost its exact owned boundary resource.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "operation_stock_name": str(
                    getattr(getattr(operation, "Stock", None), "Name", "")
                ),
                "expected_stock_name": str(getattr(stock, "Name", "")),
                "is_boundary": bool(getattr(stock, "IsBoundary", False)),
                "label": str(getattr(stock, "Label", "")),
                "visible": bool(
                    getattr(getattr(stock, "ViewObject", None), "Visibility", False)
                ),
            },
        )
    if prepared.kind == "model_bounds":
        valid = (
            isinstance(stock.Proxy, PathStock.StockFromBase)
            and stock.Base is prepared.base.job.Model
            and all(
                round(float(getattr(stock, property_name).Value), 9)
                == prepared.settings[setting_name]
                for property_name, setting_name in (
                    ("ExtXneg", "x_negative_mm"),
                    ("ExtXpos", "x_positive_mm"),
                    ("ExtYneg", "y_negative_mm"),
                    ("ExtYpos", "y_positive_mm"),
                    ("ExtZneg", "z_negative_mm"),
                    ("ExtZpos", "z_positive_mm"),
                )
            )
        )
    elif prepared.kind == "box":
        valid = (
            isinstance(stock.Proxy, PathStock.StockCreateBox)
            and round(float(stock.Length.Value), 9) == prepared.settings["length_mm"]
            and round(float(stock.Width.Value), 9) == prepared.settings["width_mm"]
            and round(float(stock.Height.Value), 9) == prepared.settings["height_mm"]
            and _placement_signature(stock.Placement)
            == (prepared.placement.origin_mm, prepared.placement.quaternion)
        )
    elif prepared.kind == "cylinder":
        valid = (
            isinstance(stock.Proxy, PathStock.StockCreateCylinder)
            and round(float(stock.Radius.Value), 9) == prepared.settings["radius_mm"]
            and round(float(stock.Height.Value), 9) == prepared.settings["height_mm"]
            and _placement_signature(stock.Placement)
            == (prepared.placement.origin_mm, prepared.placement.quaternion)
        )
    else:
        valid = (
            str(getattr(stock, "PathResource", "") or "") == "Stock"
            and tuple(getattr(stock, "Objects", ()) or ()) == (prepared.source,)
            and PathStock.StockType.FromStock(stock) == PathStock.StockType.Unknown
        )
    if not valid:
        dressup_error(
            "The CAM Path Boundary stock does not match its exact requested definition.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    actual_stock_geometry = _shape_geometry_signature(stock.Shape)
    effective_shape = PathBoundary.offsetBoundaryShape(
        stock.Shape,
        prepared.inside,
        prepared.offset_mm,
    )
    actual_effective_geometry = _shape_geometry_signature(effective_shape)
    if (
        actual_stock_geometry != prepared.stock_geometry
        or actual_effective_geometry != prepared.effective_boundary_geometry
    ):
        dressup_error(
            "The CAM Path Boundary resource did not retain its exact prepared geometry.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "expected_stock_geometry": prepared.stock_geometry,
                "actual_stock_geometry": actual_stock_geometry,
                "expected_effective_boundary_geometry": prepared.effective_boundary_geometry,
                "actual_effective_boundary_geometry": actual_effective_geometry,
            },
        )
    return persistent_resource_state(stock)


def verify_created_path_boundary_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact clipping, owned Stock, and replacement History state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    stock = value.get("stock")
    if (
        not isinstance(prepared, PreparedPathBoundaryDressup)
        or operation is None
        or stock is None
    ):
        raise TypeError("draft must contain one exact prepared CAM Path Boundary dress-up")
    base = prepared.base

    import Path.Dressup.Boundary as PathBoundary
    import Path.Dressup.Gui.Boundary as BoundaryGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=PathBoundary.DressupPathBoundary,
        view_proxy_type=BoundaryGui.DressupPathBoundaryViewProvider,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
        owned_resources=(stock,),
        created_objects=(operation, stock),
    )
    if (
        bool(operation.Inside) is not prepared.inside
        or round(float(operation.Offset.Value), 9) != prepared.offset_mm
        or round(float(operation.RetractThreshold.Value), 9)
        != prepared.retract_threshold_mm
        or bool(operation.RestMachiningPass) is not prepared.rest_machining_pass
    ):
        dressup_error(
            "The created CAM Path Boundary did not retain its exact clipping settings.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    stock_state = _verify_stock(prepared, operation, stock)
    if prepared.source is not None:
        source_after = _persistent_invariants(
            persistent_resource_state(prepared.source)
        )
        source_geometry_after = _shape_geometry_signature(prepared.source.Shape)
        if (
            source_after != prepared.source_state_before
            or source_geometry_after != prepared.source_geometry
        ):
            dressup_error(
                "CAM Path Boundary creation changed its retained public source solid.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
                repair={
                    "expected_source_state": prepared.source_state_before,
                    "actual_source_state": source_after,
                    "expected_source_geometry": prepared.source_geometry,
                    "actual_source_geometry": source_geometry_after,
                },
            )
    if any(
        persistent_resource_state(resource) != prior
        for resource, prior in prepared.model_resource_states
    ):
        dressup_error(
            "CAM Path Boundary creation changed its retained Job model resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "path_boundary_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "boundary_object_name": str(stock.Name),
        "boundary_kind": prepared.kind,
        "inside": prepared.inside,
        "offset_mm": prepared.offset_mm,
        "retract_threshold_mm": prepared.retract_threshold_mm,
        "rest_machining_pass": prepared.rest_machining_pass,
        "boundary_edge_count": prepared.boundary_edge_count,
        "boundary_face_count": prepared.boundary_face_count,
        "intersection_work": prepared.intersection_work,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "boundary_state_sha256": stock_state.get("state_sha256"),
        "boundary_shape_sha256": stock_state.get("shape_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
