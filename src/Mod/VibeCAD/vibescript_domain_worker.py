# SPDX-License-Identifier: LGPL-2.1-or-later

"""Windowless worker for workbench-qualified VibeScript v2 programs."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue, create_domain_api
import vibescript_worker_progress as worker_progress

REQUEST_ENV = "VIBECAD_VIBESCRIPT_DOMAIN_REQUEST"
RESULT_ENV = "VIBECAD_VIBESCRIPT_DOMAIN_RESULT"
SCHEMA = "vibecad-vibescript-domain-worker-v2"
MAX_STDOUT_CHARS = 16_000
MAX_DEFINITION_BYTES = 1_000_000
MAX_PART_OUTPUT_SUBELEMENT_DETAILS = 256
PART_OUTPUT_SUBELEMENT_DETAIL_BUDGET = 2_048


class _ObjectView:
    """Bounded immutable document object metadata exposed to source."""

    __slots__ = ("Name", "Label", "TypeId")

    def __init__(self, obj: Any) -> None:
        if isinstance(obj, dict):
            name = obj.get("name")
            label = obj.get("label")
            type_id = obj.get("type_id")
        else:
            name = getattr(obj, "Name", "")
            label = getattr(obj, "Label", "")
            type_id = getattr(obj, "TypeId", "")
        object.__setattr__(self, "Name", str(name or ""))
        object.__setattr__(self, "Label", str(label or ""))
        object.__setattr__(self, "TypeId", str(type_id or ""))

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise TypeError("VibeScript document object views are immutable.")


class _DocumentView:
    """Read-only description of the isolated candidate document."""

    __slots__ = ("_name", "_objects", "_by_name")

    def __init__(self, name: str, objects: list[Any]) -> None:
        self._name = str(name)
        self._objects = tuple(_ObjectView(item) for item in objects)
        self._by_name = {item.Name: item for item in self._objects}

    @property
    def Name(self) -> str:
        return self._name

    @property
    def Objects(self) -> tuple[Any, ...]:
        return self._objects

    def getObject(self, name: str) -> Any:
        return self._by_name.get(str(name))

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, name):
            raise TypeError("The VibeScript document view is immutable.")
        object.__setattr__(self, name, value)


def _immutable_input(value: Any) -> Any:
    """Recursively freeze the already validated JSON input tree."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_input(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _immutable_input(item) for key, item in value.items()}
        )
    raise TypeError(f"Worker input contains unsupported value {type(value).__name__}.")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _resource_limits(request: dict[str, Any]) -> None:
    try:
        import resource
    except ImportError:
        return
    memory = int(request.get("memory_limit_bytes") or 0)
    cpu = int(request.get("cpu_limit_seconds") or 0)
    output = int(request.get("output_limit_bytes") or 0)

    def apply(resource_id: int, limit: int) -> None:
        if limit <= 0:
            return
        soft, hard = resource.getrlimit(resource_id)
        del soft
        applied = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
        resource.setrlimit(resource_id, (applied, hard))

    if sys.platform != "darwin":
        apply(resource.RLIMIT_AS, memory)
    apply(resource.RLIMIT_CPU, cpu)
    apply(resource.RLIMIT_FSIZE, output)
    apply(resource.RLIMIT_NOFILE, 64)


_SAFE_BUILTINS = MappingProxyType(
    {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": print,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "ArithmeticError": ArithmeticError,
        "AssertionError": AssertionError,
        "Exception": Exception,
        "RuntimeError": RuntimeError,
        "TypeError": TypeError,
        "ValueError": ValueError,
    }
)


def _execute_source(
    *,
    source: str,
    document_name: str,
    document_objects: list[dict[str, str]],
    inputs: dict[str, Any],
    api: Any,
    max_operations: int,
    max_seconds: float,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    started = time.monotonic()
    operations = 0
    source_filename = "<vibecad-domain-vibescript>"

    def trace(frame: Any, event: str, _arg: Any):
        nonlocal operations
        if frame.f_code.co_filename == source_filename and event in {"line", "call"}:
            operations += 1
            if operations > max_operations:
                raise RuntimeError(
                    f"VibeScript exceeded its {max_operations} operation budget."
                )
            if time.monotonic() - started > max_seconds:
                raise TimeoutError(
                    f"VibeScript exceeded its {max_seconds:g} second source budget."
                )
        return trace

    namespace: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        "__name__": "__vibecad_domain_program__",
        "doc": _DocumentView(document_name, document_objects),
        "inputs": _immutable_input(inputs),
        "api": api,
    }
    output = io.StringIO()
    previous_trace = sys.gettrace()
    try:
        sys.settrace(trace)
        with redirect_stdout(output):
            exec(
                compile(source, source_filename, "exec"),
                namespace,
                namespace,
            )
    finally:
        sys.settrace(previous_trace)
    result = namespace.get("result")
    if not isinstance(result, dict):
        raise TypeError("Program source must assign a dictionary to result.")
    return (
        result,
        output.getvalue()[-MAX_STDOUT_CHARS:],
        {
            "operations": operations,
            "max_operations": max_operations,
            "elapsed_seconds": time.monotonic() - started,
            "max_seconds": max_seconds,
        },
    )


def _vector(value: Any):
    import FreeCAD as App

    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("A vector must be [x, y, z].")
    return App.Vector(float(value[0]), float(value[1]), float(value[2]))


def _sketch_vector(value: Any):
    import FreeCAD as App

    if not isinstance(value, (list, tuple)) or len(value) not in {2, 3}:
        raise ValueError("A sketch point must be [x, y] or [x, y, z].")
    values = [float(item) for item in value]
    return App.Vector(values[0], values[1], values[2] if len(values) == 3 else 0.0)


def _sketch_geometry(payload: dict[str, Any]):
    import Part

    operation = str(payload.get("operation") or "")
    properties = dict(payload.get("properties") or {})
    if operation == "line":
        points = _argument(payload, 0, "points")
        if (
            isinstance(points, list)
            and len(points) == 2
            and isinstance(points[0], list)
        ):
            start, end = points
        else:
            start = _argument(payload, 0, "start")
            end = _argument(payload, 1, "end")
        return Part.LineSegment(_sketch_vector(start), _sketch_vector(end))
    if operation == "circle":
        center = properties.get("center", _argument(payload, 0, "center"))
        radius = properties.get("radius", _argument(payload, 1, "radius"))
        return Part.Circle(
            _sketch_vector(center), _vector([0.0, 0.0, 1.0]), float(radius)
        )
    if operation == "arc":
        points = _argument(payload, 0, "points")
        if (
            isinstance(points, list)
            and len(points) == 3
            and isinstance(points[0], list)
        ):
            start, middle, end = points
        else:
            start = _argument(payload, 0, "start")
            middle = _argument(payload, 1, "middle")
            end = _argument(payload, 2, "end")
        return Part.Arc(
            _sketch_vector(start), _sketch_vector(middle), _sketch_vector(end)
        )
    if operation == "ellipse":
        center = _sketch_vector(
            properties.get("center", _argument(payload, 0, "center"))
        )
        major = float(
            properties.get("major_radius", _argument(payload, 1, "major_radius"))
        )
        minor = float(
            properties.get("minor_radius", _argument(payload, 2, "minor_radius"))
        )
        return Part.Ellipse(center, major, minor)
    if operation == "bspline":
        points = _argument(payload, 0, "points")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError("A sketch B-spline requires at least three points.")
        curve = Part.BSplineCurve()
        curve.interpolate(
            Points=[_sketch_vector(point) for point in points],
            PeriodicFlag=bool(properties.get("closed")),
        )
        return curve
    raise ValueError(f"Unsupported Sketcher geometry operation {operation!r}.")


def _sketch_constraint(payload: dict[str, Any]):
    import Sketcher

    arguments = list(payload.get("arguments") or [])
    properties = dict(payload.get("properties") or {})
    kind = str((arguments.pop(0) if arguments else properties.pop("type", "")) or "")
    if not kind:
        raise ValueError("api.constraint requires a native Sketcher constraint type.")
    values = properties.pop("arguments", arguments)
    if not isinstance(values, list):
        raise ValueError("Sketcher constraint arguments must be an array.")
    return Sketcher.Constraint(kind, *values)


def _build_isolated_sketch(document: Any, payload: dict[str, Any]) -> dict[str, Any]:
    properties = dict(payload.get("properties") or {})
    geometry = properties.get("geometry", _argument(payload, 0, "geometry", []))
    constraints = properties.get(
        "constraints", _argument(payload, 1, "constraints", [])
    )
    if not isinstance(geometry, list) or not isinstance(constraints, list):
        raise ValueError("Sketch geometry and constraints must be arrays.")
    sketch = document.addObject("Sketcher::SketchObject", "CandidateSketch")
    for raw in geometry:
        definition = _payload(raw, serialized=True)
        index = sketch.addGeometry(
            _sketch_geometry(definition),
            bool(dict(definition.get("properties") or {}).get("construction")),
        )
        if index < 0:
            raise RuntimeError("The isolated Sketcher worker rejected geometry.")
    for raw in constraints:
        definition = _payload(raw, serialized=True)
        sketch.addConstraint(_sketch_constraint(definition))
    for path, expression in dict(properties.get("expressions") or {}).items():
        sketch.setExpression(str(path), str(expression))
    document.recompute()
    solver_code = int(sketch.solve())
    document.recompute()
    conflicts = []
    getter = getattr(sketch, "getConflictingConstraints", None)
    if callable(getter):
        conflicts = [int(value) for value in list(getter() or [])]
    shape = getattr(sketch, "Shape", None)
    facts = {
        "solver_code": solver_code,
        "geometry_count": int(getattr(sketch, "GeometryCount", len(geometry))),
        "constraint_count": int(getattr(sketch, "ConstraintCount", len(constraints))),
        "degrees_of_freedom": int(getattr(sketch, "SolverDOF", 0)),
        "fully_constrained": bool(getattr(sketch, "FullyConstrained", False)),
        "conflicting_constraints": conflicts,
        "edge_count": len(list(getattr(shape, "Edges", []) or [])),
        "wire_count": len(list(getattr(shape, "Wires", []) or [])),
        "profile_ready": bool(
            shape is not None
            and len(list(getattr(shape, "Wires", []) or [])) > 0
            and all(wire.isClosed() for wire in list(getattr(shape, "Wires", []) or []))
        ),
    }
    if solver_code != 0 or conflicts:
        raise RuntimeError(
            f"The isolated Sketcher solver rejected the program: {facts}."
        )
    return facts


def _payload(value: Any, *, serialized: bool = False) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif (
        serialized
        and isinstance(value, dict)
        and {
            "domain",
            "operation",
            "output_type",
            "arguments",
            "properties",
        }
        <= set(value)
    ):
        payload = dict(value)
    else:
        raise TypeError("Every result value must come from the active domain api.")
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_DEFINITION_BYTES:
        raise ValueError(
            f"One VibeScript output definition exceeds {MAX_DEFINITION_BYTES} bytes."
        )
    return payload


def _argument(
    payload: dict[str, Any], index: int, name: str, default: Any = None
) -> Any:
    arguments = list(payload.get("arguments") or [])
    properties = dict(payload.get("properties") or {})
    if index < len(arguments):
        return arguments[index]
    return properties.get(name, default)


def _shape_from_payload(
    payload: dict[str, Any],
    *,
    diagnostics: dict[str, Any] | None = None,
):
    if str(payload.get("domain") or "") == "part":
        from vibescript_part_worker import build_part_shape

        return build_part_shape(payload, diagnostics=diagnostics)

    import Part

    operation = str(payload.get("operation") or "")
    properties = dict(payload.get("properties") or {})
    if operation == "box":
        return Part.makeBox(
            float(_argument(payload, 0, "length")),
            float(_argument(payload, 1, "width")),
            float(_argument(payload, 2, "height")),
            _vector(properties.get("origin", [0.0, 0.0, 0.0])),
        )
    if operation == "cylinder":
        return Part.makeCylinder(
            float(_argument(payload, 0, "radius")),
            float(_argument(payload, 1, "height")),
            _vector(properties.get("origin", [0.0, 0.0, 0.0])),
            _vector(properties.get("direction", [0.0, 0.0, 1.0])),
        )
    if operation == "sphere":
        return Part.makeSphere(
            float(_argument(payload, 0, "radius")),
            _vector(properties.get("center", [0.0, 0.0, 0.0])),
        )
    if operation == "circle":
        radius = float(
            _argument(
                payload,
                0,
                "radius",
                properties.get("radius_mm"),
            )
        )
        center = _vector(
            properties.get("center", _argument(payload, 1, "center", [0.0, 0.0, 0.0]))
        )
        start = float(properties.get("start_angle", properties.get("first_angle", 0.0)))
        end = float(properties.get("end_angle", properties.get("last_angle", 360.0)))
        edge = Part.makeCircle(radius, center, _vector([0.0, 0.0, 1.0]), start, end)
        wire = Part.Wire([edge])
        if bool(properties.get("make_face")) and abs(end - start) >= 360.0 - 1.0e-9:
            return Part.Face(wire)
        return wire
    if operation == "rectangle":
        length = float(_argument(payload, 0, "length", properties.get("length_mm")))
        height = float(_argument(payload, 1, "height", properties.get("height_mm")))
        corner = _vector(
            properties.get("corner", properties.get("origin", [0.0, 0.0, 0.0]))
        )
        points = [
            corner,
            corner + _vector([length, 0.0, 0.0]),
            corner + _vector([length, height, 0.0]),
            corner + _vector([0.0, height, 0.0]),
            corner,
        ]
        wire = Part.makePolygon(points)
        return Part.Face(wire) if bool(properties.get("make_face")) else wire
    if operation == "bspline":
        points = _argument(payload, 0, "points")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError("api.bspline requires at least three points.")
        curve = Part.BSplineCurve()
        curve.interpolate(
            Points=[_vector(point) for point in points],
            PeriodicFlag=bool(properties.get("closed")),
        )
        wire = Part.Wire([curve.toShape()])
        if bool(properties.get("make_face")):
            return Part.Face(wire)
        return wire
    if operation == "wire":
        points = _argument(payload, 0, "points")
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("api.wire requires at least two points.")
        vectors = [_vector(point) for point in points]
        if bool(properties.get("closed")) and not vectors[0].isEqual(vectors[-1], 1e-9):
            vectors.append(vectors[0])
        return Part.makePolygon(vectors)
    if operation == "face":
        base = _payload(_argument(payload, 0, "wire"), serialized=True)
        return Part.Face(_shape_from_payload(base))
    if operation in {"fuse", "cut", "common"}:
        left = _shape_from_payload(
            _payload(_argument(payload, 0, "left"), serialized=True)
        )
        right = _shape_from_payload(
            _payload(_argument(payload, 1, "right"), serialized=True)
        )
        return getattr(left, operation)(right)
    if operation == "compound":
        values = _argument(payload, 0, "shapes")
        if not isinstance(values, list) or not values:
            raise ValueError("api.compound requires a non-empty shape list.")
        return Part.makeCompound(
            [_shape_from_payload(_payload(item, serialized=True)) for item in values]
        )
    if operation == "extrude":
        base = _shape_from_payload(
            _payload(_argument(payload, 0, "shape"), serialized=True)
        )
        vector = _argument(payload, 1, "vector", properties.get("vector"))
        if isinstance(vector, (int, float)):
            vector = [0.0, 0.0, float(vector)]
        return base.extrude(_vector(vector))
    if operation == "revolve":
        base = _shape_from_payload(
            _payload(_argument(payload, 0, "shape"), serialized=True)
        )
        axis_origin = _vector(properties.get("axis_origin", [0.0, 0.0, 0.0]))
        axis_direction = _vector(properties.get("axis_direction", [0.0, 0.0, 1.0]))
        angle = float(properties.get("angle", 360.0))
        return base.revolve(axis_origin, axis_direction, angle)
    if operation == "loft":
        sections = _argument(payload, 0, "sections")
        if not isinstance(sections, list) or len(sections) < 2:
            raise ValueError("api.loft requires at least two sections.")
        return Part.makeLoft(
            [_shape_from_payload(_payload(item, serialized=True)) for item in sections],
            bool(properties.get("solid")),
            bool(properties.get("ruled")),
            False,
        )
    if operation in {"output", "fill", "blend", "extend", "thicken", "shell"}:
        nested = properties.get("shape")
        if nested is not None:
            return _shape_from_payload(_payload(nested, serialized=True))
    raise ValueError(f"Domain operation {operation!r} has no BREP implementation.")


_BREP_OUTPUT_TYPES = {
    "solid",
    "shell",
    "face",
    "wire",
    "compound",
    "surface",
    "fill",
    "blend",
    "extension",
    "loft",
    "brep",
    "curve",
}

_DRAFT_SHAPE_OUTPUT_TYPES = {"wire", "circle", "rectangle", "bspline", "array"}


def _shape_facts(shape: Any, *, max_subelements: int) -> dict[str, Any]:
    from vibescript_part_worker import part_shape_facts

    return part_shape_facts(shape, max_subelements=max_subelements)


def _serialize_output(
    root: Path,
    index: int,
    expected: dict[str, str],
    value: Any,
    *,
    max_shape_subelements: int,
) -> dict[str, Any]:
    payload = _payload(value)
    output_type = str(payload.get("output_type") or "")
    if output_type != expected["type"]:
        raise ValueError(
            f"Output {expected['name']!r} returned type {output_type!r}; "
            f"expected {expected['type']!r}."
        )
    item: dict[str, Any] = {
        "name": expected["name"],
        "type": output_type,
        "definition": payload,
    }
    if output_type in _BREP_OUTPUT_TYPES or output_type in _DRAFT_SHAPE_OUTPUT_TYPES:
        operation_diagnostics: dict[str, Any] = {}
        shape = _shape_from_payload(payload, diagnostics=operation_diagnostics)
        facts = _shape_facts(shape, max_subelements=max_shape_subelements)
        if facts["null"] or not facts["valid"]:
            raise ValueError(f"Output {expected['name']!r} is not a valid BREP shape.")
        relative = Path("outputs") / f"output-{index:03d}.brep"
        target = root / relative
        shape.exportBrep(str(target))
        if not target.is_file() or target.stat().st_size <= 0:
            raise RuntimeError(f"Could not export output {expected['name']!r}.")
        item["artifact_kind"] = "brep"
        item["artifact_path"] = str(relative)
        item["facts"] = facts
        if operation_diagnostics:
            item["operation_diagnostics"] = operation_diagnostics
    elif output_type == "mesh":
        triangles = dict(payload.get("properties") or {}).get("triangles", [])
        if not isinstance(triangles, list):
            raise ValueError("Mesh triangles must be an array.")
        item["artifact_kind"] = "mesh_json"
        item["mesh"] = {"triangles": triangles, "facet_count": len(triangles)}
    elif output_type == "points":
        points = dict(payload.get("properties") or {}).get("points", [])
        if not isinstance(points, list):
            raise ValueError("Point output points must be an array.")
        item["points"] = points
        item["facts"] = {"count": len(points)}
    elif output_type == "solver_diagnostics":
        properties = dict(payload.get("properties") or {})
        item["diagnostics"] = {
            "status": str(properties.get("status") or "solved"),
            "grounded_component": properties.get("grounded_component"),
            "joint_count": len(list(properties.get("joints") or [])),
            "messages": list(properties.get("messages") or []),
        }
    return item


def _placement_matrix(placement: Any) -> list[float]:
    matrix = placement.toMatrix()
    return [
        float(getattr(matrix, name))
        for name in (
            "A11",
            "A12",
            "A13",
            "A14",
            "A21",
            "A22",
            "A23",
            "A24",
            "A31",
            "A32",
            "A33",
            "A34",
            "A41",
            "A42",
            "A43",
            "A44",
        )
    ]


def _assembly_worker_validation(
    document: Any,
    raw_result: dict[str, Any],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build and solve an isolated native assembly from validated definitions."""

    import FreeCAD as App
    import JointObject
    import Part

    definitions = {name: _payload(value) for name, value in raw_result.items()}
    assembly_names = [
        name
        for name, payload in definitions.items()
        if payload.get("output_type") == "assembly"
    ]
    if len(assembly_names) != 1:
        raise ValueError("An Assembly program must return exactly one assembly output.")
    assembly = document.addObject("Assembly::AssemblyObject", "CandidateAssembly")
    if assembly is None:
        raise RuntimeError("The native Assembly::AssemblyObject type is unavailable.")
    assembly.Type = "Assembly"
    joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
    components: dict[str, Any] = {}
    for index, (name, payload) in enumerate(definitions.items()):
        if payload.get("output_type") != "component_link":
            continue
        properties = dict(payload.get("properties") or {})
        source = document.addObject("Part::Feature", f"CandidateSource{index}")
        source.Shape = Part.makeBox(1.0, 1.0, 1.0)
        component = assembly.newObject("App::Link", f"CandidateComponent{index}")
        component.LinkedObject = source
        placement = properties.get("placement") or properties.get("position")
        if isinstance(placement, (list, tuple)) and len(placement) == 3:
            component.Placement = App.Placement(
                App.Vector(*(float(value) for value in placement)), App.Rotation()
            )
        components[name] = component
        if bool(properties.get("grounded")):
            grounded = joint_group.newObject("App::FeaturePython", f"Ground{name}")
            JointObject.GroundedJoint(grounded, component)
    joint_count = 0
    for name, payload in definitions.items():
        if payload.get("output_type") != "joint":
            continue
        properties = dict(payload.get("properties") or {})
        kind = str(properties.get("type") or "revolute").lower()
        native_types = {
            "fixed": "Fixed",
            "revolute": "Revolute",
            "cylindrical": "Cylindrical",
            "slider": "Slider",
            "ball": "Ball",
            "distance": "Distance",
        }
        native_type = native_types.get(kind)
        if native_type not in list(JointObject.JointTypes):
            raise ValueError(f"Unsupported native assembly joint type {kind!r}.")
        references = []
        for key in ("reference1", "reference2"):
            reference = properties.get(key)
            if not isinstance(reference, dict):
                raise ValueError(f"Assembly joint {name!r} requires {key}.")
            component_name = str(reference.get("component_output") or "")
            component = components.get(component_name)
            if component is None:
                raise ValueError(
                    f"Assembly joint {name!r} refers to missing component "
                    f"output {component_name!r}."
                )
            element = str(reference.get("element") or "")
            references.append([component, [element, element]])
        joint = joint_group.newObject(
            "App::FeaturePython", f"CandidateJoint{joint_count}"
        )
        JointObject.Joint(joint, JointObject.JointTypes.index(native_type))
        joint.Proxy.setJointConnectors(joint, references)
        joint_count += 1
    document.recompute()
    solver_code = int(assembly.solve(False))
    document.recompute()
    component_placements = {
        name: _placement_matrix(component.Placement)
        for name, component in components.items()
    }
    for item in outputs:
        if item["name"] in component_placements:
            item["solved_placement_matrix"] = component_placements[item["name"]]
        if item["type"] == "solver_diagnostics":
            item["diagnostics"] = {
                "status": "solved" if solver_code == 0 else "failed",
                "solver_code": solver_code,
                "joint_count": joint_count,
                "component_count": len(components),
                "grounded_components": [
                    name
                    for name, payload in definitions.items()
                    if payload.get("output_type") == "component_link"
                    and bool(dict(payload.get("properties") or {}).get("grounded"))
                ],
            }
    if solver_code != 0:
        raise RuntimeError(
            f"The isolated native Assembly solver returned {solver_code}."
        )
    return {
        "solver_code": solver_code,
        "status": "solved",
        "joint_count": joint_count,
        "component_count": len(components),
        "component_placements": component_placements,
    }


def _validate_and_build_mesh_workbench(
    result: dict[str, Any],
    expected_outputs: list[dict[str, Any]],
    root: Path,
    *,
    max_shape_subelements: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    """Run native Mesh graphs and MeshPart conversions under one Mesh surface."""

    from vibescript_mesh_worker import validate_and_build_meshes
    from vibescript_meshpart_worker import validate_and_convert_meshpart

    native_operations = {
        "mesh",
        "from_object",
        "transform",
        "union",
        "difference",
        "intersection",
        "repair",
        "diagnostics",
    }
    conversion_operations = {"mesh_from_shape", "shape_from_mesh"}
    native_indices: list[int] = []
    conversion_indices: list[int] = []
    for index, declaration in enumerate(expected_outputs):
        name = str(declaration.get("name") or "")
        value = result.get(name)
        if not isinstance(value, DomainValue):
            raise TypeError(
                f"Mesh result {name!r} must be returned by the active Mesh api."
            )
        payload = value.to_payload()
        operation = str(payload.get("operation") or "")
        if payload.get("domain") != "mesh":
            raise ValueError(
                f"Mesh result {name!r} belongs to domain "
                f"{payload.get('domain')!r}, not 'mesh'."
            )
        if operation in native_operations:
            native_indices.append(index)
        elif operation in conversion_operations:
            conversion_indices.append(index)
        else:
            raise ValueError(
                f"Mesh result {name!r} uses unsupported operation {operation!r}."
            )

    outputs_by_name: dict[str, dict[str, Any]] = {}
    mesh_validation = None
    if native_indices:
        declarations = [expected_outputs[index] for index in native_indices]
        names = [str(item["name"]) for item in declarations]
        native_outputs, mesh_validation = validate_and_build_meshes(
            {name: result[name] for name in names},
            declarations,
            root,
            output_indices=native_indices,
        )
        outputs_by_name.update(
            (str(item["name"]), item) for item in native_outputs
        )

    meshpart_validation = None
    if conversion_indices:
        declarations = [expected_outputs[index] for index in conversion_indices]
        names = [str(item["name"]) for item in declarations]
        converted_outputs, meshpart_validation = validate_and_convert_meshpart(
            {name: result[name] for name in names},
            declarations,
            root,
            max_shape_subelements=max_shape_subelements,
            definition_domain="mesh",
            output_indices=conversion_indices,
        )
        outputs_by_name.update(
            (str(item["name"]), item) for item in converted_outputs
        )

    expected_names = [str(item["name"]) for item in expected_outputs]
    if set(outputs_by_name) != set(expected_names):
        raise RuntimeError("Mesh validation did not produce every declared output.")
    return (
        [outputs_by_name[name] for name in expected_names],
        mesh_validation,
        meshpart_validation,
    )


def _run(request: dict[str, Any], root: Path) -> dict[str, Any]:
    import FreeCAD as App

    if request.get("schema") != SCHEMA:
        raise ValueError(
            f"Unsupported domain worker schema: {request.get('schema')!r}."
        )
    domain = str(request.get("domain") or "")
    worker_progress.configure(root / "progress.json", domain)
    worker_progress.set_phase("reference_setup")
    source = str(request.get("source") or "")
    inputs = request.get("inputs")
    expected_outputs = request.get("expected_outputs")
    exports = request.get("api_exports")
    output_types = request.get("output_types")
    compatibility_methods = request.get("compatibility_methods", [])
    if not isinstance(inputs, dict):
        raise TypeError("inputs must be an object.")
    if not isinstance(expected_outputs, list) or not expected_outputs:
        raise TypeError("expected_outputs must be a non-empty array.")
    if not isinstance(exports, list) or not isinstance(output_types, list):
        raise TypeError("The domain API contract is missing.")
    if not isinstance(compatibility_methods, list) or any(
        not isinstance(item, str) for item in compatibility_methods
    ):
        raise TypeError("compatibility_methods must be an array of strings.")
    if domain in {
        "partdesign",
        "part",
        "assembly",
        "sketcher",
        "draft",
        "surface",
        "mesh",
        "meshpart",
        "points",
        "reverse_engineering",
        "inspection",
        "fem",
        "cam",
        "techdraw",
        "robot",
    }:
        references = request.get("document_references", [])
        if not isinstance(references, list):
            raise TypeError("document_references must be an array.")
        if domain == "partdesign":
            from vibescript_partdesign_worker import configure_partdesign_references
            from vibescript_component_worker import configure_component_references

            configure_partdesign_references(root, references)
            configure_component_references(references)
        elif domain == "assembly":
            from vibescript_assembly_worker import configure_assembly_references

            configure_assembly_references(root, references)
        elif domain == "robot":
            from vibescript_component_worker import configure_component_references

            configure_component_references(references)
        elif domain == "part":
            from vibescript_part_worker import configure_part_references

            configure_part_references(root, references)
        elif domain == "sketcher":
            from vibescript_sketcher_worker import configure_sketcher_references

            configure_sketcher_references(root, references)
        elif domain == "draft":
            from vibescript_draft_worker import configure_draft_references

            configure_draft_references(root, references)
        elif domain == "surface":
            from vibescript_surface_worker import configure_surface_references

            configure_surface_references(root, references)
        elif domain == "mesh":
            from vibescript_mesh_worker import configure_mesh_references
            from vibescript_meshpart_worker import configure_meshpart_references

            configure_mesh_references(
                root,
                [
                    item
                    for item in references
                    if str(item.get("artifact_kind") or "") == "mesh_bms"
                ],
            )
            configure_meshpart_references(root, references)
        elif domain == "meshpart":
            from vibescript_meshpart_worker import configure_meshpart_references

            configure_meshpart_references(root, references)
        elif domain == "points":
            from vibescript_points_worker import configure_points_sources

            artifacts = request.get("point_artifacts", [])
            if not isinstance(artifacts, list):
                raise TypeError("point_artifacts must be an array.")
            configure_points_sources(root, references, artifacts)
        elif domain == "reverse_engineering":
            from vibescript_reverse_engineering_worker import (
                configure_reverse_references,
            )

            artifacts = request.get("point_artifacts", [])
            if not isinstance(artifacts, list):
                raise TypeError("point_artifacts must be an array.")
            configure_reverse_references(root, references, artifacts)
        elif domain == "inspection":
            from vibescript_inspection_worker import (
                configure_inspection_references,
            )

            configure_inspection_references(root, references)
        elif domain == "fem":
            from vibescript_fem_worker import configure_fem_references

            configure_fem_references(root, references)
        elif domain == "cam":
            from vibescript_cam_worker import configure_cam_references

            configure_cam_references(root, references)
        else:
            from vibescript_techdraw_worker import configure_techdraw_references

            configure_techdraw_references(root, references)
    output_directory = root / "outputs"
    output_directory.mkdir(parents=True, exist_ok=False)
    document = App.newDocument(
        "VibeScriptDomainCandidate", "VibeScript Domain Candidate", True, True
    )
    try:
        api = create_domain_api(
            domain,
            exports,
            output_types,
            compatibility_methods=compatibility_methods,
        )
        worker_progress.set_phase("source_execution")
        result, stdout, budget = _execute_source(
            source=source,
            document_name=str(request.get("document_name") or "VibeScriptDocument"),
            document_objects=list(request.get("document_objects") or []),
            inputs=inputs,
            api=api,
            max_operations=int(request.get("max_operations") or 200_000),
            max_seconds=float(request.get("max_seconds") or 300.0),
        )
        expected_names = [str(item.get("name") or "") for item in expected_outputs]
        if list(result) != expected_names:
            raise ValueError(
                "result keys must exactly match expected_outputs in declared order: "
                f"expected {expected_names}, received {list(result)}."
            )
        shape_detail_limit = max(
            16,
            min(
                MAX_PART_OUTPUT_SUBELEMENT_DETAILS,
                PART_OUTPUT_SUBELEMENT_DETAIL_BUDGET // max(1, len(expected_outputs)),
            ),
        )
        draft_validation = None
        surface_validation = None
        spreadsheet_validation = None
        material_validation = None
        mesh_validation = None
        meshpart_validation = None
        points_validation = None
        reverse_engineering_validation = None
        inspection_validation = None
        robot_validation = None
        fem_validation = None
        cam_validation = None
        techdraw_validation = None
        partdesign_validation = None
        partdesign_native_history = None
        worker_progress.set_phase("native_build")
        if domain == "partdesign":
            from vibescript_partdesign_worker import (
                export_partdesign_native_history,
                validate_and_build_partdesign,
            )

            outputs, partdesign_validation = validate_and_build_partdesign(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
                max_shape_subelements=shape_detail_limit,
                object_name_prefix=(
                    f"VibePD_{str(request.get('program_id') or '')[:12]}_"
                ),
            )
            partdesign_native_history = export_partdesign_native_history(
                document,
                outputs,
                root,
            )
        elif domain == "draft":
            from vibescript_draft_worker import validate_and_build_draft

            outputs, draft_validation = validate_and_build_draft(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
                max_shape_subelements=shape_detail_limit,
            )
        elif domain == "surface":
            from vibescript_surface_worker import validate_and_build_surface

            outputs, surface_validation = validate_and_build_surface(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
                max_shape_subelements=shape_detail_limit,
            )
        elif domain == "spreadsheet":
            from vibescript_spreadsheet_worker import validate_and_build_spreadsheets

            outputs, spreadsheet_validation = validate_and_build_spreadsheets(
                document,
                result,
                [dict(item) for item in expected_outputs],
            )
        elif domain == "material":
            from vibescript_material_worker import validate_and_resolve_materials

            targets = request.get("material_targets")
            if not isinstance(targets, list):
                raise TypeError("material_targets must be an array.")
            outputs, material_validation = validate_and_resolve_materials(
                result,
                [dict(item) for item in expected_outputs],
                document_uid=str(request.get("document_uid") or ""),
                material_targets=targets,
            )
        elif domain == "mesh":
            (
                outputs,
                mesh_validation,
                meshpart_validation,
            ) = _validate_and_build_mesh_workbench(
                result,
                [dict(item) for item in expected_outputs],
                root,
                max_shape_subelements=shape_detail_limit,
            )
        elif domain == "meshpart":
            from vibescript_meshpart_worker import validate_and_convert_meshpart

            outputs, meshpart_validation = validate_and_convert_meshpart(
                result,
                [dict(item) for item in expected_outputs],
                root,
                max_shape_subelements=shape_detail_limit,
            )
        elif domain == "points":
            from vibescript_points_worker import validate_and_process_points

            outputs, points_validation = validate_and_process_points(
                result,
                [dict(item) for item in expected_outputs],
                root,
                document,
            )
        elif domain == "reverse_engineering":
            from vibescript_reverse_engineering_worker import (
                validate_and_build_reverse_engineering,
            )

            outputs, reverse_engineering_validation = (
                validate_and_build_reverse_engineering(
                    result,
                    [dict(item) for item in expected_outputs],
                    root,
                    document,
                    max_shape_subelements=shape_detail_limit,
                )
            )
        elif domain == "inspection":
            from vibescript_inspection_worker import validate_and_build_inspection

            outputs, inspection_validation = validate_and_build_inspection(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
            )
        elif domain == "robot":
            from vibescript_robot_worker import validate_and_build_robot

            outputs, robot_validation = validate_and_build_robot(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
            )
        elif domain == "fem":
            from vibescript_fem_worker import validate_and_build_fem

            outputs, fem_validation = validate_and_build_fem(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
            )
        elif domain == "cam":
            from vibescript_cam_worker import validate_and_build_cam

            outputs, cam_validation = validate_and_build_cam(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
            )
        elif domain == "techdraw":
            from vibescript_techdraw_worker import validate_and_build_techdraw

            outputs, techdraw_validation = validate_and_build_techdraw(
                document,
                result,
                [dict(item) for item in expected_outputs],
                root,
            )
        else:
            outputs = [
                _serialize_output(
                    root,
                    index,
                    dict(expected),
                    result[expected["name"]],
                    max_shape_subelements=shape_detail_limit,
                )
                for index, expected in enumerate(expected_outputs)
            ]
        response = {
            "ok": True,
            "schema": SCHEMA,
            "domain": domain,
            "outputs": outputs,
            "stdout": stdout,
            "budget": budget,
        }
        if domain == "partdesign":
            response["partdesign_validation"] = partdesign_validation
            response["partdesign_native_history"] = partdesign_native_history
        elif domain == "assembly":
            from vibescript_assembly_worker import validate_and_solve_assembly

            response["assembly_validation"] = validate_and_solve_assembly(
                document,
                result,
                outputs,
                root,
            )
            response["assembly_members"] = [
                item for item in outputs if item.get("internal") is True
            ]
            response["outputs"] = [
                item for item in outputs if item.get("internal") is not True
            ]
        elif domain == "sketcher":
            from vibescript_sketcher_worker import validate_and_solve_sketch

            response["sketch_validation"] = validate_and_solve_sketch(
                document,
                result,
                outputs,
            )
        elif domain == "draft":
            response["draft_validation"] = draft_validation
        elif domain == "surface":
            response["surface_validation"] = surface_validation
        elif domain == "spreadsheet":
            response["spreadsheet_validation"] = spreadsheet_validation
        elif domain == "material":
            response["material_validation"] = material_validation
        elif domain == "mesh":
            if mesh_validation is not None:
                response["mesh_validation"] = mesh_validation
            if meshpart_validation is not None:
                response["meshpart_validation"] = meshpart_validation
        elif domain == "meshpart":
            response["meshpart_validation"] = meshpart_validation
        elif domain == "points":
            response["points_validation"] = points_validation
        elif domain == "reverse_engineering":
            response["reverse_engineering_validation"] = reverse_engineering_validation
        elif domain == "inspection":
            response["inspection_validation"] = inspection_validation
        elif domain == "robot":
            response["robot_validation"] = robot_validation
        elif domain == "fem":
            response["fem_validation"] = fem_validation
        elif domain == "cam":
            response["cam_validation"] = cam_validation
        elif domain == "techdraw":
            response["techdraw_validation"] = techdraw_validation
        worker_progress.finish()
        response["worker_progress"] = json.loads(
            (root / "progress.json").read_text(encoding="utf-8")
        )
        return response
    finally:
        App.closeDocument(document.Name)


def main() -> int:
    result_path = Path(os.environ[RESULT_ENV]).resolve()
    try:
        request_path = Path(os.environ[REQUEST_ENV]).resolve()
        root = request_path.parent
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise TypeError("Domain worker request must be an object.")
        _resource_limits(request)
        payload = _run(request, root)
    except BaseException as exc:
        worker_progress.failed(exc)
        payload = {
            "ok": False,
            "exception_type": exc.__class__.__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=40),
        }
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            payload["details"] = details
    _write_json(result_path, payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
