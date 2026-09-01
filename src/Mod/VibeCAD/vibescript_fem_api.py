# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical immutable API for FEM VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = (
    "analysis",
    "solver",
    "material",
    "constraint",
    "load_case",
    "mesh",
    "solve",
)
_OUTPUT_TYPES = (
    "analysis",
    "solver",
    "material",
    "constraint",
    "load_case",
    "mesh",
    "result",
)
_MATRIX_SOLVERS = (
    "default",
    "pastix",
    "pardiso",
    "spooles",
    "iterativescaling",
    "iterativecholesky",
)
_CONSTRAINT_KINDS = ("fixed", "force", "pressure")
_ELEMENT_NODE_COUNTS = {
    "edge2": 2,
    "edge3": 3,
    "triangle3": 3,
    "triangle6": 6,
    "quad4": 4,
    "quad8": 8,
    "tetra4": 4,
    "tetra10": 10,
    "pyramid5": 5,
    "pyramid13": 13,
    "penta6": 6,
    "penta15": 15,
    "hexa8": 8,
    "hexa20": 20,
}
_OBJECT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SUBELEMENT = re.compile(r"(?:Solid|Face|Edge|Vertex)[1-9][0-9]*\Z")
_INTERFACE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")
_MAX_LABEL_CHARS = 256
_MAX_GRAPH_ITEMS = 64
_MAX_NODES = 100_000
_MAX_ELEMENTS = 200_000
_MAX_COORDINATE = 1.0e9
_MISSING = object()


class FEMAPIError(ValueError):
    """A source error carrying one exact repair target for the operating model."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        parameter: str,
        reason: str,
    ) -> None:
        self.details = {
            "stage": "source_validation",
            "operation": operation,
            "parameter": parameter,
            "reason": reason,
            "correction": (
                f"Correct api.{operation} parameter {parameter!r}: it {reason}. "
                "Change only the failing source expression, then retry against the "
                "failed working revision."
            ),
        }
        super().__init__(message)


def _error(
    operation: str,
    parameter: str,
    reason: str,
    value: Any = _MISSING,
) -> FEMAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return FEMAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float,
    maximum: float,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number", value)
    clean = float(value)
    if not math.isfinite(clean):
        raise _error(operation, parameter, "must be finite", value)
    if clean < minimum or (strict_minimum and clean == minimum):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    if clean > maximum:
        raise _error(operation, parameter, f"must be at most {maximum:g}", value)
    return clean


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LABEL_CHARS or "\0" in value:
        raise _error(
            operation,
            "label",
            f"must be a string of at most {_MAX_LABEL_CHARS} characters without nulls",
            value,
        )
    return value


def _boolean(operation: str, parameter: str, value: Any) -> bool:
    if type(value) is not bool:
        raise _error(operation, parameter, "must be true or false", value)
    return value


def _choice(
    operation: str,
    parameter: str,
    value: Any,
    choices: Sequence[str],
) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _error(operation, parameter, f"must be one of {list(choices)!r}", value)
    return value


def _reference(operation: str, parameter: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            operation,
            parameter,
            "must contain exactly document_uid and object_name",
            value,
        )
    document_uid = value.get("document_uid")
    object_name = value.get("object_name")
    if (
        not isinstance(document_uid, str)
        or not document_uid
        or document_uid != document_uid.strip()
        or len(document_uid) > 256
        or "\0" in document_uid
    ):
        raise _error(
            operation,
            f"{parameter}.document_uid",
            "must be a non-empty string of at most 256 characters without surrounding whitespace",
            document_uid,
        )
    if (
        not isinstance(object_name, str)
        or len(object_name) > 128
        or _OBJECT_NAME.fullmatch(object_name) is None
    ):
        raise _error(
            operation,
            f"{parameter}.object_name",
            "must be an exact FreeCAD internal object name",
            object_name,
        )
    return {"document_uid": document_uid, "object_name": object_name}


def _selection(
    operation: str,
    value: Any,
    *,
    parameter: str = "selection",
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _error(
            operation,
            parameter,
            "must be a subelement or published_interface selector object",
            value,
        )
    kind = value.get("type")
    if kind == "subelement" and set(value) == {"type", "name"}:
        name = value.get("name")
        if not isinstance(name, str) or _SUBELEMENT.fullmatch(name) is None:
            raise _error(
                operation,
                f"{parameter}.name",
                "must be an exact SolidN, FaceN, EdgeN, or VertexN name",
                name,
            )
        return {"type": "subelement", "name": name}
    if kind == "published_interface" and set(value) == {
        "type",
        "interface_name",
    }:
        name = value.get("interface_name")
        if not isinstance(name, str) or _INTERFACE_NAME.fullmatch(name) is None:
            raise _error(
                operation,
                f"{parameter}.interface_name",
                "must be a 1-128 character stable interface identifier",
                name,
            )
        return {"type": "published_interface", "interface_name": name}
    raise _error(
        operation,
        parameter,
        "must contain exactly type/name or type/interface_name",
        value,
    )


def _material_assignments(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            "material",
            "assignments",
            "must be a sequence of target/selection objects",
            value,
        )
    if len(value) > _MAX_GRAPH_ITEMS:
        raise _error(
            "material",
            "assignments",
            f"must contain at most {_MAX_GRAPH_ITEMS} values",
        )
    result = []
    keys = []
    for index, raw in enumerate(value):
        parameter = f"assignments[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != {"target", "selection"}:
            raise _error(
                "material",
                parameter,
                "must contain exactly target and selection",
                raw,
            )
        assignment = {
            "target": _reference(
                "material", f"{parameter}.target", raw["target"]
            ),
            "selection": _selection(
                "material",
                raw["selection"],
                parameter=f"{parameter}.selection",
            ),
        }
        key = json.dumps(
            assignment,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if key in keys:
            raise _error(
                "material",
                parameter,
                "duplicates an earlier assignment",
            )
        keys.append(key)
        result.append(assignment)
    return tuple(result)


def _vector(operation: str, parameter: str, value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must contain exactly three numbers", value)
    result = tuple(
        _number(
            operation,
            f"{parameter}[{index}]",
            item,
            minimum=-_MAX_COORDINATE,
            maximum=_MAX_COORDINATE,
        )
        for index, item in enumerate(value)
    )
    return result  # type: ignore[return-value]


def _unit_vector(operation: str, parameter: str, value: Any) -> tuple[float, float, float]:
    vector = _vector(operation, parameter, value)
    length = math.sqrt(sum(item * item for item in vector))
    if length <= 1.0e-12:
        raise _error(operation, parameter, "must have non-zero magnitude", value)
    return tuple(item / length for item in vector)  # type: ignore[return-value]


def _domain_value(
    operation: str,
    parameter: str,
    value: Any,
    output_types: set[str],
) -> DomainValue:
    if not isinstance(value, DomainValue) or value.domain != "fem":
        raise _error(
            operation,
            parameter,
            "must be a value returned by this FEM api",
            type(value).__name__,
        )
    if value.output_type not in output_types:
        raise _error(
            operation,
            parameter,
            f"must have type {sorted(output_types)!r}",
            value.output_type,
        )
    return value


def _definition_key(value: DomainValue) -> str:
    return json.dumps(
        value.to_payload(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _domain_sequence(
    operation: str,
    parameter: str,
    value: Any,
    output_types: set[str],
    *,
    minimum: int = 1,
    maximum: int = _MAX_GRAPH_ITEMS,
) -> tuple[DomainValue, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(operation, parameter, "must be a sequence of FEM api values", value)
    if not minimum <= len(value) <= maximum:
        raise _error(
            operation,
            parameter,
            f"must contain {minimum}-{maximum} values",
        )
    result = tuple(
        _domain_value(operation, f"{parameter}[{index}]", item, output_types)
        for index, item in enumerate(value)
    )
    keys = [_definition_key(item) for item in result]
    if len(keys) != len(set(keys)):
        raise _error(operation, parameter, "cannot contain duplicate definitions")
    return result


def _inline_mesh(
    nodes: Any,
    elements: Any,
    element_type: Any,
) -> tuple[tuple[tuple[float, float, float], ...], tuple[tuple[int, ...], ...], str]:
    clean_type = _choice(
        "mesh",
        "element_type",
        element_type,
        tuple(_ELEMENT_NODE_COUNTS),
    )
    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
        raise _error("mesh", "nodes", "must be a sequence of [x, y, z] coordinates")
    if not 1 <= len(nodes) <= _MAX_NODES:
        raise _error("mesh", "nodes", f"must contain 1-{_MAX_NODES} nodes")
    clean_nodes = tuple(
        _vector("mesh", f"nodes[{index}]", node)
        for index, node in enumerate(nodes)
    )
    if isinstance(elements, (str, bytes)) or not isinstance(elements, Sequence):
        raise _error("mesh", "elements", "must be a sequence of node-index arrays")
    if not 1 <= len(elements) <= _MAX_ELEMENTS:
        raise _error("mesh", "elements", f"must contain 1-{_MAX_ELEMENTS} elements")
    width = _ELEMENT_NODE_COUNTS[clean_type]
    clean_elements = []
    for element_index, element in enumerate(elements):
        parameter = f"elements[{element_index}]"
        if not isinstance(element, (list, tuple)) or len(element) != width:
            raise _error(
                "mesh",
                parameter,
                f"must contain exactly {width} zero-based node indices for {clean_type}",
                element,
            )
        connectivity = []
        for node_offset, value in enumerate(element):
            if type(value) is not int or not 0 <= value < len(clean_nodes):
                raise _error(
                    "mesh",
                    f"{parameter}[{node_offset}]",
                    f"must be an integer from 0 through {len(clean_nodes) - 1}",
                    value,
                )
            connectivity.append(value)
        if len(connectivity) != len(set(connectivity)):
            raise _error("mesh", parameter, "cannot repeat a node index", element)
        clean_elements.append(tuple(connectivity))
    return clean_nodes, tuple(clean_elements), clean_type


class FEMDomainAPI:
    """Exact native FEM study-graph, meshing, and CalculiX API."""

    __slots__ = ()

    domain = "fem"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared_exports = tuple(dict.fromkeys(str(item) for item in exports))
        declared_outputs = tuple(dict.fromkeys(str(item) for item in output_types))
        if declared_exports != _EXPORTS:
            raise RuntimeError(
                "FEM pack exports do not match the production runtime contract: "
                f"expected {_EXPORTS!r}, received {declared_exports!r}."
            )
        if declared_outputs != _OUTPUT_TYPES:
            raise RuntimeError(
                "FEM pack must publish exactly analysis, solver, material, constraint, "
                "load_case, mesh, and result."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="fem",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def solver(
        self,
        *,
        matrix_solver: str = "default",
        geometrical_nonlinearity: bool = False,
        material_nonlinearity: bool = False,
        reduced_integration: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Define one native CalculiX solver object for one independent study."""

        return self._value(
            "solver",
            "solver",
            analysis_type="static",
            matrix_solver=_choice(
                "solver", "matrix_solver", matrix_solver, _MATRIX_SOLVERS
            ),
            geometrical_nonlinearity=_boolean(
                "solver", "geometrical_nonlinearity", geometrical_nonlinearity
            ),
            material_nonlinearity=_boolean(
                "solver", "material_nonlinearity", material_nonlinearity
            ),
            reduced_integration=_boolean(
                "solver", "reduced_integration", reduced_integration
            ),
            label=_label("solver", label),
        )

    def material(
        self,
        *,
        name: str,
        youngs_modulus_mpa: float,
        poisson_ratio: float,
        density_kg_m3: float,
        thermal_expansion_per_k: float = 0.0,
        assignments: Sequence[Mapping[str, Any]] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Define one isotropic solid material and optional multi-material regions."""

        if (
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or len(name) > 128
            or "\0" in name
        ):
            raise _error(
                "material",
                "name",
                "must be a non-empty trimmed string of at most 128 characters",
                name,
            )
        return self._value(
            "material",
            "material",
            name=name,
            youngs_modulus_mpa=_number(
                "material",
                "youngs_modulus_mpa",
                youngs_modulus_mpa,
                minimum=0.0,
                maximum=1.0e12,
                strict_minimum=True,
            ),
            poisson_ratio=_number(
                "material",
                "poisson_ratio",
                poisson_ratio,
                minimum=-0.999999,
                maximum=0.499999,
            ),
            density_kg_m3=_number(
                "material",
                "density_kg_m3",
                density_kg_m3,
                minimum=0.0,
                maximum=1.0e12,
                strict_minimum=True,
            ),
            thermal_expansion_per_k=_number(
                "material",
                "thermal_expansion_per_k",
                thermal_expansion_per_k,
                minimum=-1.0,
                maximum=1.0,
            ),
            assignments=_material_assignments(assignments),
            label=_label("material", label),
        )

    def constraint(
        self,
        kind: str,
        target: Mapping[str, str],
        selection: Mapping[str, str],
        *,
        magnitude: float | None = None,
        direction: Sequence[float] | None = None,
        reversed: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Define one fixed, directed-force, or pressure constraint."""

        clean_kind = _choice("constraint", "kind", kind, _CONSTRAINT_KINDS)
        clean_reversed = _boolean("constraint", "reversed", reversed)
        if clean_kind == "fixed":
            if magnitude is not None or direction is not None or clean_reversed:
                raise _error(
                    "constraint",
                    "parameters",
                    "fixed constraints cannot set magnitude, direction, or reversed",
                )
            clean_magnitude = None
            clean_direction = None
        elif clean_kind == "force":
            if magnitude is None or direction is None:
                raise _error(
                    "constraint",
                    "parameters",
                    "force constraints require magnitude and direction",
                )
            clean_magnitude = _number(
                "constraint",
                "magnitude",
                magnitude,
                minimum=0.0,
                maximum=1.0e18,
                strict_minimum=True,
            )
            clean_direction = _unit_vector("constraint", "direction", direction)
        else:
            if magnitude is None or direction is not None:
                raise _error(
                    "constraint",
                    "parameters",
                    "pressure constraints require magnitude and cannot set direction",
                )
            clean_magnitude = _number(
                "constraint",
                "magnitude",
                magnitude,
                minimum=0.0,
                maximum=1.0e12,
                strict_minimum=True,
            )
            clean_direction = None
        return self._value(
            "constraint",
            "constraint",
            _reference("constraint", "target", target),
            _selection("constraint", selection),
            kind=clean_kind,
            magnitude=clean_magnitude,
            direction=clean_direction,
            reversed=clean_reversed,
            label=_label("constraint", label),
        )

    def load_case(
        self,
        constraints: Sequence[DomainValue],
        *,
        label: str = "",
    ) -> DomainValue:
        """Group one or more exact returned constraints into a native load case."""

        return self._value(
            "load_case",
            "load_case",
            _domain_sequence(
                "load_case", "constraints", constraints, {"constraint"}
            ),
            label=_label("load_case", label),
        )

    def mesh(
        self,
        source: Mapping[str, str],
        *,
        method: str,
        nodes: Sequence[Sequence[float]] | None = None,
        elements: Sequence[Sequence[int]] | None = None,
        element_type: str | None = None,
        maximum_size: float | None = None,
        minimum_size: float = 0.0,
        order: int = 1,
        label: str = "",
    ) -> DomainValue:
        """Build one native FEM mesh for one study from inline connectivity or worker-side Gmsh."""

        clean_method = _choice("mesh", "method", method, ("inline", "gmsh"))
        if type(order) is not int or order not in {1, 2}:
            raise _error("mesh", "order", "must be 1 or 2", order)
        if clean_method == "inline":
            if maximum_size is not None or minimum_size != 0.0:
                raise _error(
                    "mesh",
                    "sizing",
                    "inline meshes cannot set maximum_size or minimum_size",
                )
            clean_nodes, clean_elements, clean_element_type = _inline_mesh(
                nodes, elements, element_type
            )
            if order != (2 if clean_element_type in {
                "edge3",
                "triangle6",
                "quad8",
                "tetra10",
                "pyramid13",
                "penta15",
                "hexa20",
            } else 1):
                raise _error(
                    "mesh",
                    "order",
                    f"must match the polynomial order encoded by {clean_element_type}",
                    order,
                )
            clean_maximum = None
            clean_minimum = 0.0
        else:
            if nodes is not None or elements is not None or element_type is not None:
                raise _error(
                    "mesh",
                    "connectivity",
                    "gmsh cannot receive inline nodes, elements, or element_type",
                )
            clean_nodes = None
            clean_elements = None
            clean_element_type = None
            if maximum_size is None:
                raise _error("mesh", "maximum_size", "is required for gmsh")
            clean_maximum = _number(
                "mesh",
                "maximum_size",
                maximum_size,
                minimum=0.0,
                maximum=_MAX_COORDINATE,
                strict_minimum=True,
            )
            clean_minimum = _number(
                "mesh",
                "minimum_size",
                minimum_size,
                minimum=0.0,
                maximum=clean_maximum,
            )
        return self._value(
            "mesh",
            "mesh",
            _reference("mesh", "source", source),
            method=clean_method,
            nodes=clean_nodes,
            elements=clean_elements,
            element_type=clean_element_type,
            maximum_size=clean_maximum,
            minimum_size=clean_minimum,
            order=order,
            label=_label("mesh", label),
        )

    def analysis(
        self,
        solver: DomainValue,
        materials: Sequence[DomainValue],
        load_cases: Sequence[DomainValue],
        mesh: DomainValue,
        *,
        label: str = "",
    ) -> DomainValue:
        """Assemble one exact native FEM analysis graph; several may coexist independently."""

        return self._value(
            "analysis",
            "analysis",
            _domain_value("analysis", "solver", solver, {"solver"}),
            _domain_sequence("analysis", "materials", materials, {"material"}),
            _domain_sequence("analysis", "load_cases", load_cases, {"load_case"}),
            _domain_value("analysis", "mesh", mesh, {"mesh"}),
            label=_label("analysis", label),
        )

    def solve(
        self,
        analysis: DomainValue,
        *,
        execution: str,
        label: str = "",
    ) -> DomainValue:
        """Create one result set for an analysis by validating or executing CalculiX in the worker."""

        return self._value(
            "solve",
            "result",
            _domain_value("solve", "analysis", analysis, {"analysis"}),
            execution=_choice(
                "solve", "execution", execution, ("validate_only", "calculix")
            ),
            label=_label("solve", label),
        )
