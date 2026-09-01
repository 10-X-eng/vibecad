# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native worker for production FEM VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_fem_api import FEMDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-fem-validation-v1"
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
_OPERATION_OUTPUT = {
    "analysis": "analysis",
    "solver": "solver",
    "material": "material",
    "constraint": "constraint",
    "load_case": "load_case",
    "mesh": "mesh",
    "solve": "result",
}
_ELEMENT_METHOD = {
    "edge2": "edge",
    "edge3": "edge",
    "triangle3": "face",
    "triangle6": "face",
    "quad4": "face",
    "quad8": "face",
    "tetra4": "volume",
    "tetra10": "volume",
    "pyramid5": "volume",
    "pyramid13": "volume",
    "penta6": "volume",
    "penta15": "volume",
    "hexa8": "volume",
    "hexa20": "volume",
}
_RESULT_FLOAT_LISTS = (
    "CriticalStrainRatio",
    "DisplacementLengths",
    "MassFlowRate",
    "MaxShear",
    "MohrCoulomb",
    "NetworkPressure",
    "NodeStrainXX",
    "NodeStrainXY",
    "NodeStrainXZ",
    "NodeStrainYY",
    "NodeStrainYZ",
    "NodeStrainZZ",
    "NodeStressXX",
    "NodeStressXY",
    "NodeStressXZ",
    "NodeStressYY",
    "NodeStressYZ",
    "NodeStressZZ",
    "Peeq",
    "PrincipalMax",
    "PrincipalMed",
    "PrincipalMin",
    "ReinforcementRatio_x",
    "ReinforcementRatio_y",
    "ReinforcementRatio_z",
    "Stats",
    "Temperature",
    "UserDefined",
    "vonMises",
)
_RESULT_VECTOR_LISTS = (
    "DisplacementVectors",
    "HeatFlux",
    "PS1Vector",
    "PS2Vector",
    "PS3Vector",
)
_REFERENCE_OPTIONAL_FIELDS = frozenset(
    {
        "label",
        "type_id",
        "shape_type",
        "facts",
        "source_kind",
        "source_program_id",
        "source_program_domain",
        "source_revision",
        "transient_topology",
        "requires_semantic_interfaces",
        "published_interfaces",
        "reference_contract_sha256",
    }
)
_MAX_DEFINITION_BYTES = 1_000_000
_MAX_NATIVE_READBACK_BYTES = 256 * 1024 * 1024
_MAX_REFERENCES = 128
_MAX_NODES = 100_000
_MAX_ELEMENTS = 500_000
_MAX_RESULT_VALUES = 10_000_000
_SUBELEMENT = re.compile(r"(Solid|Face|Edge|Vertex)([1-9][0-9]*)\Z")
_REFERENCES: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded model repair for every FEM failure stage."""

    stage = str(details.get("stage") or "")
    path = str(details.get("path") or "")
    output = str(details.get("output") or "")
    location = f" at {path}" if path else (f" {output!r}" if output else "")
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, types, and order. Replace "
            "only the mismatched result entry and keep every declaration unchanged."
        )
    if stage == "definition_contract":
        return (
            f"Rebuild only the malformed value{location} with the matching FEM api operation; "
            "never construct or mutate serialized definitions."
        )
    if stage == "source_validation":
        return (
            "Change only the named api argument and preserve exact returned graph values, stable "
            "document references, units, semantic selectors, and expected output declarations."
        )
    if stage == "reference_resolution":
        return (
            "Copy the exact current document_uid/object_name reference from FEM domain context; "
            "do not use a label, filesystem path, stale object name, or another document."
        )
    if stage == "semantic_selection":
        return (
            "Use one reported available SolidN/FaceN/EdgeN/VertexN on stable native topology, or "
            "copy an available published_interface name for regenerating VibeScript geometry."
        )
    if stage == "material_assignment":
        return (
            "For one material omit assignments so it covers the entire mesh. For multiple materials, "
            "assign disjoint SolidN/FaceN/EdgeN regions and leave at most one material unassigned as "
            "the explicit remainder; eliminate every overlap or uncovered element."
        )
    if stage == "native_graph":
        return (
            "Correct only the reported solver, material, constraint, or analysis property and keep "
            "all exact returned graph identities unchanged before retrying."
        )
    if stage == "native_mesh_readback":
        return (
            "Correct only the reported inline node/connectivity or Gmsh sizing/order so the native "
            "mesh is finite, non-empty, bounded, and reconstructs without topology drift."
        )
    if stage == "mesh_constraint_mapping":
        return (
            "Keep the load unchanged and correct only the mesh or semantic selection so every selected "
            "boundary is represented by compatible mesh nodes/faces with full reported coverage."
        )
    if stage == "graph_membership":
        return (
            "Return every solver, material, constraint, load case, mesh, and analysis consumed by a "
            "derived value under its own declared stable output name, then reuse that exact value."
        )
    if stage == "output_identity":
        return (
            "Remove only the duplicate definition or make it deliberately different; every declared FEM "
            "output must have one unique graph identity."
        )
    if stage == "output_evaluation":
        return (
            "Return the missing prerequisite before its analysis/result and preserve expected_outputs order."
        )
    if stage == "external_capability":
        capability = str(details.get("capability") or "requested external executable")
        return (
            f"Keep the FEM graph unchanged and retry only when the isolated worker can resolve {capability!r}; "
            "otherwise select inline meshing or validate_only as the explicit non-executing alternative."
        )
    if stage == "external_mesher":
        return (
            "Correct only Gmsh source validity, maximum/minimum size, or order using the reported mesher "
            "diagnostic, then retry the retained failed revision."
        )
    if stage in {"solver_prerequisites", "solver_input"}:
        return (
            "Correct only the reported missing material assignment, boundary condition, element geometry, "
            "mesh coverage, or solver option; regenerate and inspect the authenticated input deck before execution."
        )
    if stage == "external_solver":
        return (
            "Use the bounded CalculiX exit/stderr evidence to correct only the input graph or solver option, "
            "then retry; never claim numerical results from the failed run."
        )
    if stage == "solver_result_readback":
        return (
            "Keep the accepted input graph and correct only the reported analysis/result cardinality or native "
            "solver output issue; do not treat malformed/non-finite arrays as solved evidence."
        )
    return (
        "Correct only the reported FEM reference, selector, assignment, mesh, graph member, solver option, "
        "or execution choice and retry the failed working revision; do not recreate the program."
    )


class FEMCandidateError(RuntimeError):
    """A model-correctable FEM failure with structured diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.details = dict(details or {})
        if not str(self.details.get("correction") or "").strip():
            changes = self.details.get("required_changes")
            correction = (
                next(
                    (str(item).strip() for item in changes if str(item).strip()),
                    "",
                )
                if isinstance(changes, list)
                else ""
            )
            self.details["correction"] = correction or _default_correction(
                self.details
            )
        super().__init__(message)


def _fail(message: str, *, stage: str, **details: Any) -> FEMCandidateError:
    return FEMCandidateError(message, details={"stage": stage, **details})


def _sha256_file(path: Path, *, stage: str = "reference_resolution") -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _fail(
            f"A FEM artifact could not be authenticated: {exc}",
            stage=stage,
            exception_type=type(exc).__name__,
        ) from exc
    return digest.hexdigest()


def _encoded(
    value: Any,
    *,
    limit: int = _MAX_DEFINITION_BYTES,
    label: str = "definition",
) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A FEM {label} is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(payload) > limit:
        raise _fail(
            f"A FEM {label} exceeds {limit} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(payload),
        )
    return payload


def _definition_key(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _inflate(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_inflate(item) for item in value)
    if not isinstance(value, Mapping):
        return value
    if set(value) == {
        "domain",
        "operation",
        "output_type",
        "arguments",
        "properties",
    }:
        return DomainValue(
            domain=str(value.get("domain") or ""),
            operation=str(value.get("operation") or ""),
            output_type=str(value.get("output_type") or ""),
            arguments=tuple(_inflate(item) for item in list(value.get("arguments") or [])),
            properties={
                str(name): _inflate(item)
                for name, item in dict(value.get("properties") or {}).items()
            },
        )
    return {str(name): _inflate(item) for name, item in value.items()}


def validate_fem_definition(
    value: Any,
    *,
    expected_output_type: str | None = None,
    require_domain_value: bool = True,
    context: str = "definition",
) -> dict[str, Any]:
    """Replay one untrusted definition through the exact provider API."""

    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif not require_domain_value and isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be returned by the active FEM api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields or payload.get("domain") != "fem":
        raise _fail(
            f"{context} has malformed FEM definition fields.",
            stage="definition_contract",
            path=context,
        )
    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if operation not in _OPERATION_OUTPUT or output_type != _OPERATION_OUTPUT[operation]:
        raise _fail(
            f"{context} has unsupported operation/type {operation!r}/{output_type!r}.",
            stage="definition_contract",
            path=context,
        )
    if expected_output_type is not None and output_type != expected_output_type:
        raise _fail(
            f"{context} must publish {expected_output_type!r}, not {output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or not isinstance(properties, Mapping):
        raise _fail(
            f"{context} arguments/properties must be an array and object.",
            stage="definition_contract",
            path=context,
        )
    properties = dict(properties)
    api = FEMDomainAPI(_EXPORTS, _OUTPUT_TYPES)
    try:
        if operation == "solver":
            required = {
                "analysis_type",
                "matrix_solver",
                "geometrical_nonlinearity",
                "material_nonlinearity",
                "reduced_integration",
                "label",
            }
            if arguments or set(properties) != required:
                raise ValueError("solver fields are malformed")
            rebuilt = api.solver(
                matrix_solver=properties["matrix_solver"],
                geometrical_nonlinearity=properties["geometrical_nonlinearity"],
                material_nonlinearity=properties["material_nonlinearity"],
                reduced_integration=properties["reduced_integration"],
                label=properties["label"],
            )
        elif operation == "material":
            required = {
                "name",
                "youngs_modulus_mpa",
                "poisson_ratio",
                "density_kg_m3",
                "thermal_expansion_per_k",
                "label",
            }
            if arguments or set(properties) != required | {"assignments"}:
                raise ValueError("material fields are malformed")
            rebuilt = api.material(
                **{
                    **properties,
                    "assignments": properties.get("assignments"),
                }
            )
        elif operation == "constraint":
            required = {"kind", "magnitude", "direction", "reversed", "label"}
            if len(arguments) != 2 or set(properties) != required:
                raise ValueError("constraint fields are malformed")
            rebuilt = api.constraint(
                properties["kind"],
                arguments[0],
                arguments[1],
                magnitude=properties["magnitude"],
                direction=properties["direction"],
                reversed=properties["reversed"],
                label=properties["label"],
            )
        elif operation == "load_case":
            if len(arguments) != 1 or set(properties) != {"label"}:
                raise ValueError("load_case fields are malformed")
            if not isinstance(arguments[0], list):
                raise ValueError("load_case constraints must be an array")
            constraints = [
                _inflate(
                    validate_fem_definition(
                        item,
                        expected_output_type="constraint",
                        require_domain_value=False,
                        context=f"{context}.arguments[0][{index}]",
                    )
                )
                for index, item in enumerate(arguments[0])
            ]
            rebuilt = api.load_case(constraints, label=properties["label"])
        elif operation == "mesh":
            required = {
                "method",
                "nodes",
                "elements",
                "element_type",
                "maximum_size",
                "minimum_size",
                "order",
                "label",
            }
            if len(arguments) != 1 or set(properties) != required:
                raise ValueError("mesh fields are malformed")
            rebuilt = api.mesh(arguments[0], **properties)
        elif operation == "analysis":
            if len(arguments) != 4 or set(properties) != {"label"}:
                raise ValueError("analysis fields are malformed")
            solver = _inflate(
                validate_fem_definition(
                    arguments[0],
                    expected_output_type="solver",
                    require_domain_value=False,
                    context=f"{context}.arguments[0]",
                )
            )
            if not isinstance(arguments[1], list) or not isinstance(arguments[2], list):
                raise ValueError("analysis materials/load_cases must be arrays")
            materials = [
                _inflate(
                    validate_fem_definition(
                        item,
                        expected_output_type="material",
                        require_domain_value=False,
                        context=f"{context}.arguments[1][{index}]",
                    )
                )
                for index, item in enumerate(arguments[1])
            ]
            load_cases = [
                _inflate(
                    validate_fem_definition(
                        item,
                        expected_output_type="load_case",
                        require_domain_value=False,
                        context=f"{context}.arguments[2][{index}]",
                    )
                )
                for index, item in enumerate(arguments[2])
            ]
            mesh = _inflate(
                validate_fem_definition(
                    arguments[3],
                    expected_output_type="mesh",
                    require_domain_value=False,
                    context=f"{context}.arguments[3]",
                )
            )
            rebuilt = api.analysis(
                solver,
                materials,
                load_cases,
                mesh,
                label=properties["label"],
            )
        else:
            if len(arguments) != 1 or set(properties) != {"execution", "label"}:
                raise ValueError("solve fields are malformed")
            analysis = _inflate(
                validate_fem_definition(
                    arguments[0],
                    expected_output_type="analysis",
                    require_domain_value=False,
                    context=f"{context}.arguments[0]",
                )
            )
            rebuilt = api.solve(
                analysis,
                execution=properties["execution"],
                label=properties["label"],
            )
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"{context} is invalid: {exc}",
            stage="definition_contract",
            path=context,
            operation=operation,
        ) from exc
    canonical = rebuilt.to_payload()
    if canonical != payload:
        raise _fail(
            f"{context} is not the canonical api.{operation} representation.",
            stage="definition_contract",
            path=context,
        )
    _encoded(canonical)
    return canonical


def _bounded_artifact_path(root: Path, relative: Any, *, context: str) -> Path:
    resolved_root = Path(root).resolve()
    if not isinstance(relative, str) or not relative:
        raise _fail(
            f"{context} has no artifact path.",
            stage="reference_resolution",
            path=context,
        )
    path = (resolved_root / relative).resolve()
    if resolved_root not in path.parents or not path.is_file() or path.is_symlink():
        raise _fail(
            f"{context} artifact is missing, symlinked, or outside staging.",
            stage="reference_resolution",
            path=context,
        )
    if not 1 <= path.stat().st_size <= 256 * 1024 * 1024:
        raise _fail(
            f"{context} artifact has an invalid size.",
            stage="reference_resolution",
            path=context,
        )
    return path


def _reference_key(entry: Mapping[str, Any], *, context: str) -> tuple[str, str]:
    values = []
    for name in ("document_uid", "object_name"):
        raw = entry.get(name)
        if (
            not isinstance(raw, str)
            or not raw
            or raw != raw.strip()
            or len(raw) > 256
            or "\0" in raw
        ):
            raise _fail(
                f"{context}.{name} is invalid.",
                stage="reference_resolution",
                path=f"{context}.{name}",
            )
        values.append(raw)
    return values[0], values[1]


def configure_fem_references(
    root: Path,
    document_references: list[dict[str, Any]],
) -> None:
    """Authenticate and import exact detached BREP inputs for FEM."""

    if len(document_references) > _MAX_REFERENCES:
        raise _fail(
            f"FEM accepts at most {_MAX_REFERENCES} document references.",
            stage="reference_resolution",
        )
    import Part

    references: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(document_references):
        context = f"document_references[{index}]"
        if not isinstance(entry, dict):
            raise _fail(
                f"{context} must be an object.",
                stage="reference_resolution",
                path=context,
            )
        required = {
            "document_uid",
            "object_name",
            "artifact_kind",
            "artifact_path",
            "brep_sha256",
        }
        if not required <= set(entry) or set(entry) - required - _REFERENCE_OPTIONAL_FIELDS:
            raise _fail(
                f"{context} has malformed fields.",
                stage="reference_resolution",
                path=context,
            )
        if entry.get("artifact_kind") != "brep":
            raise _fail(
                f"{context} must contain a BREP shape snapshot.",
                stage="reference_resolution",
                path=context,
            )
        key = _reference_key(entry, context=context)
        if key in references:
            raise _fail(
                f"{context} duplicates document object {key[1]!r}.",
                stage="reference_resolution",
                path=context,
                object_name=key[1],
            )
        path = _bounded_artifact_path(root, entry.get("artifact_path"), context=context)
        digest = entry.get("brep_sha256")
        if not isinstance(digest, str) or _sha256_file(path) != digest:
            raise _fail(
                f"{context} SHA-256 does not match its descriptor.",
                stage="reference_resolution",
                path=context,
            )
        shape = Part.Shape()
        try:
            shape.importBrep(str(path))
        except Exception as exc:
            raise _fail(
                f"{context} BREP import failed: {exc}",
                stage="reference_resolution",
                path=context,
                exception_type=type(exc).__name__,
            ) from exc
        if shape.isNull() or not shape.isValid():
            raise _fail(
                f"{context} contains an invalid BREP.",
                stage="reference_resolution",
                path=context,
            )
        expected_shape_type = str(entry.get("shape_type") or "")
        if expected_shape_type and str(shape.ShapeType) != expected_shape_type:
            raise _fail(
                f"{context} changed shape type during transfer.",
                stage="reference_resolution",
                path=context,
            )
        interfaces = entry.get("published_interfaces") or {}
        if not isinstance(interfaces, Mapping) or len(interfaces) > 64:
            raise _fail(
                f"{context}.published_interfaces is invalid.",
                stage="reference_resolution",
                path=f"{context}.published_interfaces",
            )
        references[key] = MappingProxyType(
            {
                "shape": shape,
                "artifact_sha256": digest,
                "label": str(entry.get("label") or ""),
                "source_type_id": str(entry.get("type_id") or ""),
                "source_kind": str(entry.get("source_kind") or "shape"),
                "source_revision": str(entry.get("source_revision") or ""),
                "transient_topology": bool(entry.get("transient_topology")),
                "requires_semantic_interfaces": bool(
                    entry.get("requires_semantic_interfaces")
                ),
                "published_interfaces": {
                    str(name): dict(value)
                    for name, value in interfaces.items()
                    if isinstance(name, str) and isinstance(value, Mapping)
                },
            }
        )
    global _REFERENCES
    _REFERENCES = MappingProxyType(references)


def _source_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _fail(
            f"{context} must contain exactly document_uid and object_name.",
            stage="reference_resolution",
            path=context,
        )
    key = _reference_key(value, context=context)
    if key not in _REFERENCES:
        raise _fail(
            f"{context} refers to unauthenticated object {key[1]!r}.",
            stage="reference_resolution",
            path=context,
            object_name=key[1],
        )
    return key


def _source_identity(key: tuple[str, str]) -> dict[str, Any]:
    source = _REFERENCES[key]
    return {
        "document_uid": key[0],
        "object_name": key[1],
        "artifact_sha256": str(source["artifact_sha256"]),
        "source_type_id": str(source["source_type_id"]),
        "source_kind": str(source["source_kind"]),
        "source_revision": str(source["source_revision"]),
    }


def _source_object(
    document: Any,
    key: tuple[str, str],
    cache: dict[tuple[str, str], Any],
) -> Any:
    existing = cache.get(key)
    if existing is not None:
        return existing
    source = _REFERENCES[key]
    obj = document.addObject("Part::Feature", f"FEMSource{len(cache):03d}")
    obj.Shape = source["shape"].copy()
    obj.Label = str(source["label"] or key[1])
    cache[key] = obj
    return obj


def _validate_subelements(shape: Any, names: Sequence[Any], *, context: str) -> list[str]:
    if not names or len(names) > 64:
        raise _fail(
            f"{context} must resolve to 1-64 subelements.",
            stage="semantic_selection",
            path=context,
        )
    result = []
    for index, raw in enumerate(names):
        if not isinstance(raw, str):
            raise _fail(
                f"{context}[{index}] is not a subelement name.",
                stage="semantic_selection",
                path=f"{context}[{index}]",
            )
        match = _SUBELEMENT.fullmatch(raw)
        if match is None:
            raise _fail(
                f"{context}[{index}] must be FaceN, EdgeN, or VertexN.",
                stage="semantic_selection",
                path=f"{context}[{index}]",
            )
        collection = {
            "Solid": list(shape.Solids),
            "Face": list(shape.Faces),
            "Edge": list(shape.Edges),
            "Vertex": list(shape.Vertexes),
        }[match.group(1)]
        subindex = int(match.group(2))
        if subindex > len(collection):
            raise _fail(
                f"{context}[{index}] requests {raw}, but the snapshot has only "
                f"{len(collection)} {match.group(1)} elements.",
                stage="semantic_selection",
                path=f"{context}[{index}]",
                requested=raw,
                available_count=len(collection),
                available_subelements=[
                    f"{match.group(1)}{offset}"
                    for offset in range(1, min(len(collection), 64) + 1)
                ],
                available_subelements_truncated=len(collection) > 64,
            )
        result.append(raw)
    if len(result) != len(set(result)):
        raise _fail(
            f"{context} contains duplicate subelements.",
            stage="semantic_selection",
            path=context,
        )
    return result


def _resolve_selection(
    key: tuple[str, str],
    selector: Mapping[str, Any],
    *,
    context: str,
) -> list[str]:
    source = _REFERENCES[key]
    kind = str(selector.get("type") or "")
    if kind == "subelement":
        if bool(source["transient_topology"]) or bool(
            source["requires_semantic_interfaces"]
        ):
            raise _fail(
                f"{context} cannot use an exact subelement on regenerating source "
                f"{key[1]!r}; use a published_interface selector.",
                stage="semantic_selection",
                path=context,
                object_name=key[1],
            )
        return _validate_subelements(
            source["shape"], [selector.get("name")], context=context
        )
    if kind == "published_interface":
        interface_name = str(selector.get("interface_name") or "")
        interface = source["published_interfaces"].get(interface_name)
        if not isinstance(interface, Mapping):
            raise _fail(
                f"{context} requests unknown interface {interface_name!r} on "
                f"{key[1]!r}.",
                stage="semantic_selection",
                path=context,
                available_interfaces=sorted(source["published_interfaces"]),
            )
        return _validate_subelements(
            source["shape"],
            list(interface.get("subelements") or []),
            context=f"{context}.{interface_name}",
        )
    raise _fail(
        f"{context} has unsupported selection type {kind!r}.",
        stage="semantic_selection",
        path=context,
    )


def _mesh_topology(fem_mesh: Any) -> dict[str, Any]:
    nodes_mapping = dict(fem_mesh.Nodes)
    if not 1 <= len(nodes_mapping) <= _MAX_NODES:
        raise _fail(
            f"Native FEM mesh contains {len(nodes_mapping)} nodes; the limit is "
            f"1-{_MAX_NODES}.",
            stage="native_mesh_readback",
        )
    nodes = [
        [int(node_id), float(point.x), float(point.y), float(point.z)]
        for node_id, point in sorted(nodes_mapping.items())
    ]
    if any(not math.isfinite(value) for node in nodes for value in node[1:]):
        raise _fail("Native FEM mesh contains non-finite nodes.", stage="native_mesh_readback")
    elements: dict[str, list[list[int]]] = {}
    total = 0
    for name, identifiers in (
        ("edges", list(fem_mesh.Edges)),
        ("faces", list(fem_mesh.Faces)),
        ("volumes", list(fem_mesh.Volumes)),
    ):
        records = []
        for element_id in sorted(int(item) for item in identifiers):
            connectivity = [int(item) for item in fem_mesh.getElementNodes(element_id)]
            if not connectivity or any(node not in nodes_mapping for node in connectivity):
                raise _fail(
                    f"Native FEM element {element_id} has invalid connectivity.",
                    stage="native_mesh_readback",
                )
            records.append([element_id, *connectivity])
        elements[name] = records
        total += len(records)
    if not 1 <= total <= _MAX_ELEMENTS:
        raise _fail(
            f"Native FEM mesh contains {total} elements; the limit is 1-{_MAX_ELEMENTS}.",
            stage="native_mesh_readback",
        )
    mins = [min(node[axis] for node in nodes) for axis in (1, 2, 3)]
    maxs = [max(node[axis] for node in nodes) for axis in (1, 2, 3)]
    return {
        "nodes": nodes,
        "elements": elements,
        "facts": {
            "node_count": len(nodes),
            "edge_count": len(elements["edges"]),
            "face_count": len(elements["faces"]),
            "volume_count": len(elements["volumes"]),
            "element_count": total,
            "bounds_mm": {"min": mins, "max": maxs},
        },
    }


def build_fem_mesh(topology: Mapping[str, Any]) -> Any:
    """Rebuild a native FemMesh from authenticated bounded topology."""

    import Fem

    nodes = topology.get("nodes")
    elements = topology.get("elements")
    if not isinstance(nodes, list) or not isinstance(elements, Mapping):
        raise ValueError("FEM mesh topology must contain nodes and elements.")
    fem_mesh = Fem.FemMesh()
    node_ids: set[int] = set()
    for index, row in enumerate(nodes):
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError(f"FEM mesh nodes[{index}] is malformed.")
        node_id = row[0]
        if type(node_id) is not int or node_id <= 0 or node_id in node_ids:
            raise ValueError(f"FEM mesh nodes[{index}] has an invalid id.")
        coordinates = [float(item) for item in row[1:]]
        if any(not math.isfinite(item) for item in coordinates):
            raise ValueError(f"FEM mesh nodes[{index}] is not finite.")
        fem_mesh.addNode(*coordinates, node_id)
        node_ids.add(node_id)
    element_ids: set[int] = set()
    for category, method_name in (
        ("edges", "addEdge"),
        ("faces", "addFace"),
        ("volumes", "addVolume"),
    ):
        records = elements.get(category)
        if not isinstance(records, list):
            raise ValueError(f"FEM mesh elements.{category} must be an array.")
        method = getattr(fem_mesh, method_name)
        for index, row in enumerate(records):
            if not isinstance(row, list) or len(row) < 3:
                raise ValueError(f"FEM mesh elements.{category}[{index}] is malformed.")
            element_id = row[0]
            connectivity = row[1:]
            if (
                type(element_id) is not int
                or element_id <= 0
                or element_id in element_ids
                or any(type(item) is not int or item not in node_ids for item in connectivity)
            ):
                raise ValueError(f"FEM mesh elements.{category}[{index}] is invalid.")
            method(connectivity, element_id)
            element_ids.add(element_id)
    if not node_ids or not element_ids:
        raise ValueError("FEM mesh topology is empty.")
    observed = _mesh_topology(fem_mesh)
    if observed != dict(topology):
        raise ValueError("FEM mesh changed during native reconstruction.")
    return fem_mesh


def _build_solver(document: Any, definition: Mapping[str, Any], index: int) -> dict[str, Any]:
    import ObjectsFem

    properties = dict(definition["properties"])
    try:
        obj = ObjectsFem.makeSolverCalculiXCcxTools(
            document, f"FEMSolver{index:03d}"
        )
        obj.AnalysisType = properties["analysis_type"]
        obj.MatrixSolverType = properties["matrix_solver"]
        obj.GeometricalNonlinearity = bool(properties["geometrical_nonlinearity"])
        obj.MaterialNonlinearity = bool(properties["material_nonlinearity"])
        obj.ReducedIntegration = bool(properties["reduced_integration"])
        obj.SplitInputWriter = False
    except Exception as exc:
        raise _fail(
            f"Native FEM solver construction failed: {exc}",
            stage="native_graph",
            operation="solver",
            exception_type=type(exc).__name__,
        ) from exc
    return {
        "object": obj,
        "data": {
            "native_type": str(obj.TypeId),
            "analysis_type": str(obj.AnalysisType),
            "matrix_solver": str(obj.MatrixSolverType),
            "geometrical_nonlinearity": bool(obj.GeometricalNonlinearity),
            "material_nonlinearity": bool(obj.MaterialNonlinearity),
            "reduced_integration": bool(obj.ReducedIntegration),
        },
    }


def _build_material(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    source_objects: dict[tuple[str, str], Any],
) -> dict[str, Any]:
    import ObjectsFem

    properties = dict(definition["properties"])
    try:
        obj = ObjectsFem.makeMaterialSolid(document, f"FEMMaterial{index:03d}")
    except Exception as exc:
        raise _fail(
            f"Native FEM material construction failed: {exc}",
            stage="native_graph",
            operation="material",
            exception_type=type(exc).__name__,
        ) from exc
    material = {
        "Name": str(properties["name"]),
        "YoungsModulus": f"{float(properties['youngs_modulus_mpa']):.17g} MPa",
        "PoissonRatio": f"{float(properties['poisson_ratio']):.17g}",
        "Density": f"{float(properties['density_kg_m3']):.17g} kg/m^3",
        "ThermalExpansionCoefficient": (
            f"{float(properties['thermal_expansion_per_k']):.17g} 1/K"
        ),
    }
    try:
        obj.Material = material
    except Exception as exc:
        raise _fail(
            f"Native FEM material property application failed: {exc}",
            stage="native_graph",
            operation="material",
            exception_type=type(exc).__name__,
        ) from exc
    references = []
    assignments = []
    for assignment_index, raw in enumerate(properties.get("assignments") or []):
        key = _source_key(
            raw["target"],
            context=f"material[{index}].assignments[{assignment_index}].target",
        )
        resolved = _resolve_selection(
            key,
            raw["selection"],
            context=f"material[{index}].assignments[{assignment_index}].selection",
        )
        target = _source_object(document, key, source_objects)
        references.append((target, resolved))
        assignments.append(
            {
                "target": _source_identity(key),
                "selection": dict(raw["selection"]),
                "resolved_subelements": resolved,
            }
        )
    try:
        obj.References = references
    except Exception as exc:
        raise _fail(
            f"Native FEM material assignment failed: {exc}",
            stage="material_assignment",
            operation="material",
            exception_type=type(exc).__name__,
        ) from exc
    return {
        "object": obj,
        "data": {
            "native_type": str(obj.TypeId),
            "category": str(obj.Category),
            "material": dict(obj.Material),
            "assignments": assignments,
        },
    }


def _build_constraint(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    source_objects: dict[tuple[str, str], Any],
) -> dict[str, Any]:
    import FreeCAD as App
    import ObjectsFem

    properties = dict(definition["properties"])
    kind = str(properties["kind"])
    key = _source_key(definition["arguments"][0], context=f"constraint[{index}].target")
    selection = _resolve_selection(
        key,
        definition["arguments"][1],
        context=f"constraint[{index}].selection",
    )
    if kind == "pressure" and any(not name.startswith("Face") for name in selection):
        raise _fail(
            "Pressure constraints require face selections.",
            stage="semantic_selection",
            constraint_index=index,
            resolved_subelements=selection,
        )
    target = _source_object(document, key, source_objects)
    factory = {
        "fixed": ObjectsFem.makeConstraintFixed,
        "force": ObjectsFem.makeConstraintForce,
        "pressure": ObjectsFem.makeConstraintPressure,
    }[kind]
    try:
        obj = factory(document, f"FEMConstraint{index:03d}")
        obj.References = [(target, selection)]
        if kind == "force":
            obj.Force = f"{float(properties['magnitude']):.17g} N"
            obj.DirectionVector = App.Vector(*properties["direction"])
            obj.Reversed = bool(properties["reversed"])
        elif kind == "pressure":
            obj.Pressure = f"{float(properties['magnitude']):.17g} MPa"
            obj.Reversed = bool(properties["reversed"])
    except Exception as exc:
        raise _fail(
            f"Native FEM {kind} constraint construction failed: {exc}",
            stage="native_graph",
            operation="constraint",
            kind=kind,
            exception_type=type(exc).__name__,
        ) from exc
    return {
        "object": obj,
        "data": {
            "native_type": str(obj.TypeId),
            "kind": kind,
            "target": _source_identity(key),
            "selection": dict(definition["arguments"][1]),
            "resolved_subelements": selection,
            "magnitude": properties["magnitude"],
            "direction": properties["direction"],
            "reversed": bool(properties["reversed"]),
        },
    }


def _build_inline_mesh(definition: Mapping[str, Any]) -> Any:
    import Fem

    properties = dict(definition["properties"])
    fem_mesh = Fem.FemMesh()
    for node_id, coordinates in enumerate(properties["nodes"], start=1):
        fem_mesh.addNode(*(float(item) for item in coordinates), node_id)
    method_name = {
        "edge": "addEdge",
        "face": "addFace",
        "volume": "addVolume",
    }[_ELEMENT_METHOD[str(properties["element_type"])]]
    method = getattr(fem_mesh, method_name)
    for element_id, connectivity in enumerate(properties["elements"], start=1):
        method([int(item) + 1 for item in connectivity], element_id)
    return fem_mesh


def _build_mesh(
    document: Any,
    definition: Mapping[str, Any],
    index: int,
    source_objects: dict[tuple[str, str], Any],
    root: Path,
) -> dict[str, Any]:
    import ObjectsFem

    properties = dict(definition["properties"])
    key = _source_key(definition["arguments"][0], context=f"mesh[{index}].source")
    source = _source_object(document, key, source_objects)
    try:
        obj = ObjectsFem.makeMeshGmsh(document, f"FEMMesh{index:03d}")
        obj.Shape = source
        obj.ElementOrder = "2nd" if int(properties["order"]) == 2 else "1st"
    except Exception as exc:
        raise _fail(
            f"Native FEM mesh object construction failed: {exc}",
            stage="native_mesh_readback",
            exception_type=type(exc).__name__,
        ) from exc
    method = str(properties["method"])
    if method == "inline":
        try:
            obj.FemMesh = _build_inline_mesh(definition)
            obj.ElementDimension = {
                "edge": "1D",
                "face": "2D",
                "volume": "3D",
            }[_ELEMENT_METHOD[str(properties["element_type"])]]
        except Exception as exc:
            if isinstance(exc, FEMCandidateError):
                raise
            raise _fail(
                f"Native inline FEM mesh construction failed: {exc}",
                stage="native_mesh_readback",
                element_type=str(properties["element_type"]),
                exception_type=type(exc).__name__,
            ) from exc
    else:
        gmsh = shutil.which("gmsh")
        if gmsh is None:
            raise _fail(
                "Gmsh execution was requested, but no gmsh executable is available "
                "to the isolated worker.",
                stage="external_capability",
                capability="gmsh",
            )
        working = root / "outputs" / f"gmsh-{index:03d}"
        try:
            working.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise _fail(
                f"Gmsh worker directory creation failed: {exc}",
                stage="external_mesher",
                capability="gmsh",
                exception_type=type(exc).__name__,
            ) from exc
        obj.WorkingDirectory = str(working)
        obj.CharacteristicLengthMax = f"{float(properties['maximum_size']):.17g} mm"
        obj.CharacteristicLengthMin = f"{float(properties['minimum_size']):.17g} mm"
        obj.ElementDimension = "From Shape"
        from femmesh import gmshtools

        try:
            tool = gmshtools.GmshTools(obj)
            generated = bool(tool.run(blocking=True))
        except Exception as exc:
            raise _fail(
                f"Native Gmsh mesh generation raised {type(exc).__name__}: {exc}",
                stage="external_mesher",
                capability="gmsh",
                exception_type=type(exc).__name__,
            ) from exc
        if not generated:
            raise _fail(
                "Native Gmsh mesh generation failed.",
                stage="external_mesher",
                capability="gmsh",
            )
    topology = _mesh_topology(obj.FemMesh)
    return {
        "object": obj,
        "data": {
            "native_type": str(obj.TypeId),
            "method": method,
            "source": _source_identity(key),
            "order": int(properties["order"]),
            **topology,
        },
    }


def _bounded_result_data(obj: Any) -> dict[str, Any]:
    values_used = 0
    result: dict[str, Any] = {
        "node_numbers": [int(item) for item in list(getattr(obj, "NodeNumbers", []))],
        "float_lists": {},
        "vector_lists": {},
        "time": float(getattr(obj, "Time", 0.0) or 0.0),
        "eigenmode": int(getattr(obj, "Eigenmode", 0) or 0),
        "eigenmode_frequency": float(
            getattr(obj, "EigenmodeFrequency", 0.0) or 0.0
        ),
    }
    values_used += len(result["node_numbers"])
    for name in _RESULT_FLOAT_LISTS:
        if not hasattr(obj, name):
            continue
        values = [float(item) for item in list(getattr(obj, name) or [])]
        if any(not math.isfinite(item) for item in values):
            raise _fail(
                f"CalculiX result property {name!r} contains non-finite values.",
                stage="solver_result_readback",
            )
        values_used += len(values)
        result["float_lists"][name] = values
    for name in _RESULT_VECTOR_LISTS:
        if not hasattr(obj, name):
            continue
        values = [
            [float(item.x), float(item.y), float(item.z)]
            for item in list(getattr(obj, name) or [])
        ]
        if any(not math.isfinite(item) for row in values for item in row):
            raise _fail(
                f"CalculiX result property {name!r} contains non-finite vectors.",
                stage="solver_result_readback",
            )
        values_used += len(values) * 3
        result["vector_lists"][name] = values
    if values_used > _MAX_RESULT_VALUES:
        raise _fail(
            f"CalculiX result contains {values_used} scalar values; the limit is "
            f"{_MAX_RESULT_VALUES}.",
            stage="solver_result_readback",
        )
    result["scalar_value_count"] = values_used
    return result


def _validate_constraint_mesh_coverage(
    mesh_obj: Any,
    constraints: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reject constraints whose selected geometry is absent from the FEM mesh."""

    from femmesh import meshtools

    fem_mesh = mesh_obj.FemMesh
    element_table = meshtools.get_femelement_table(fem_mesh)
    mesh_nodes = dict(fem_mesh.Nodes)
    summaries = []
    for record in constraints:
        obj = record["object"]
        data = record["data"]
        selected_node_ids = sorted(
            set(meshtools.get_femnodes_by_references(fem_mesh, obj.References))
        )
        if not selected_node_ids:
            raise _fail(
                f"FEM constraint {obj.Name!r} selection has no nodes in the supplied mesh.",
                stage="mesh_constraint_mapping",
                kind=str(data["kind"]),
                target=str(data["target"]["object_name"]),
                subelements=list(data["resolved_subelements"]),
            )
        mapped_area = 0.0
        reference_area = 0.0
        for target, subelements in obj.References:
            for subelement in subelements:
                if not str(subelement).startswith("Face"):
                    continue
                face = meshtools.sub_shape_at_global_placement(target, subelement)
                face_table = meshtools.get_ref_facenodes_table(
                    fem_mesh, element_table, face
                )
                area_table = meshtools.get_ref_facenodes_areas(
                    mesh_nodes, face_table
                )
                covered = math.fsum(float(value) for _node, value in area_table)
                if not face_table or not area_table or not math.isclose(
                    covered,
                    float(face.Area),
                    rel_tol=0.01,
                    abs_tol=1.0e-8,
                ):
                    raise _fail(
                        f"FEM constraint {obj.Name!r} face {subelement} is not fully "
                        "covered by compatible FEM boundary elements.",
                        stage="mesh_constraint_mapping",
                        kind=str(data["kind"]),
                        subelement=str(subelement),
                        reference_area_mm2=float(face.Area),
                        mapped_area_mm2=covered,
                    )
                mapped_area += covered
                reference_area += float(face.Area)
        summaries.append(
            {
                "kind": str(data["kind"]),
                "node_count": len(selected_node_ids),
                "mapped_area_mm2": mapped_area,
                "reference_area_mm2": reference_area,
            }
        )
    return {"constraint_count": len(summaries), "constraints": summaries}


def _validate_material_mesh_coverage(
    mesh_obj: Any,
    materials: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Prove deterministic, disjoint material coverage of every active element."""

    from femmesh import meshtools

    fem_mesh = mesh_obj.FemMesh
    element_table = meshtools.get_femelement_table(fem_mesh)
    all_elements = set(int(value) for value in element_table)
    if not all_elements:
        raise _fail(
            "FEM material assignment cannot inspect an empty active element table.",
            stage="material_assignment",
        )
    if len(materials) == 1:
        name, record = materials[0]
        if list(record["object"].References):
            raise _fail(
                f"Single FEM material {name!r} must omit assignments because FreeCAD "
                "applies it globally and ignores its References.",
                stage="material_assignment",
                output=name,
                active_element_count=len(all_elements),
            )
        return {
            "active_element_count": len(all_elements),
            "default_material_output": name,
            "materials": [
                {
                    "output": name,
                    "assignment_mode": "global",
                    "element_count": len(all_elements),
                }
            ],
            "overlap_count": 0,
            "unassigned_count": 0,
        }

    owner_by_element: dict[int, str] = {}
    default_materials = []
    summaries = []
    for name, record in materials:
        references = list(record["object"].References)
        if not references:
            default_materials.append(name)
            summaries.append(
                {
                    "output": name,
                    "assignment_mode": "remainder",
                    "element_count": 0,
                }
            )
            continue
        selected = set(
            int(value)
            for value in meshtools.get_femelements_by_references(
                fem_mesh,
                element_table,
                references,
            )
        )
        selected &= all_elements
        if not selected:
            raise _fail(
                f"FEM material {name!r} assignments map to no active mesh elements.",
                stage="material_assignment",
                output=name,
                assignments=record["data"].get("assignments"),
            )
        overlap = sorted(value for value in selected if value in owner_by_element)
        if overlap:
            raise _fail(
                f"FEM material {name!r} overlaps an earlier material on "
                f"{len(overlap)} active elements.",
                stage="material_assignment",
                output=name,
                overlap_count=len(overlap),
                first_overlap_elements=overlap[:64],
                conflicting_outputs=sorted(
                    {owner_by_element[value] for value in overlap}
                ),
            )
        for element_id in selected:
            owner_by_element[element_id] = name
        summaries.append(
            {
                "output": name,
                "assignment_mode": "explicit",
                "element_count": len(selected),
            }
        )
    if len(default_materials) > 1:
        raise _fail(
            "Multiple FEM materials omit assignments, so the remainder is ambiguous.",
            stage="material_assignment",
            default_material_outputs=default_materials,
        )
    remainder = all_elements - set(owner_by_element)
    if default_materials:
        default_name = default_materials[0]
        if not remainder:
            raise _fail(
                f"Default FEM material {default_name!r} has no remaining active elements.",
                stage="material_assignment",
                output=default_name,
            )
        for summary in summaries:
            if summary["output"] == default_name:
                summary["element_count"] = len(remainder)
                break
    elif remainder:
        raise _fail(
            f"FEM material assignments leave {len(remainder)} active elements uncovered.",
            stage="material_assignment",
            unassigned_count=len(remainder),
            first_unassigned_elements=sorted(remainder)[:64],
        )
    return {
        "active_element_count": len(all_elements),
        "default_material_output": default_materials[0] if default_materials else "",
        "materials": summaries,
        "overlap_count": 0,
        "unassigned_count": 0,
    }


def _solve_analysis(
    document: Any,
    analysis: Any,
    solver: Any,
    mesh: Any,
    definition: Mapping[str, Any],
    index: int,
    root: Path,
) -> dict[str, Any]:
    import ObjectsFem
    from femtools import ccxtools

    execution = str(definition["properties"]["execution"])
    analysis_type = str(solver.AnalysisType)
    working = root / "outputs" / f"calculix-{index:03d}"
    try:
        working.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise _fail(
            f"CalculiX worker directory creation failed: {exc}",
            stage="solver_input",
            exception_type=type(exc).__name__,
        ) from exc
    try:
        fea = ccxtools.FemToolsCcx(
            analysis,
            solver,
            test_mode=execution == "validate_only",
        )
        fea.update_objects()
        fea.setup_working_dir(str(working))
        prerequisite_error = str(fea.check_prerequisites() or "")
    except Exception as exc:
        raise _fail(
            f"CalculiX prerequisite inspection failed: {exc}",
            stage="solver_prerequisites",
            analysis_type=analysis_type,
            exception_type=type(exc).__name__,
        ) from exc
    if prerequisite_error:
        raise _fail(
            f"CalculiX prerequisites are not satisfied: {prerequisite_error}",
            stage="solver_prerequisites",
        )
    try:
        fea.write_inp_file()
    except Exception as exc:
        raise _fail(
            f"CalculiX input-deck generation failed: {exc}",
            stage="solver_input",
            analysis_type=analysis_type,
            exception_type=type(exc).__name__,
        ) from exc
    input_path = Path(str(fea.inp_file_name or "")).resolve()
    if working.resolve() not in input_path.parents or not input_path.is_file():
        raise _fail(
            "CalculiX did not produce a bounded input deck in worker staging.",
            stage="solver_input",
        )
    input_size = int(input_path.stat().st_size)
    if not 1 <= input_size <= 256 * 1024 * 1024:
        raise _fail(
            f"CalculiX input deck has invalid size {input_size}.",
            stage="solver_input",
        )
    result_obj = None
    status = "input_validated"
    solver_executed = False
    existing_result_names = {
        str(getattr(obj, "Name", ""))
        for obj in list(getattr(analysis, "Group", ()) or ())
        if str(getattr(obj, "TypeId", "")) == "Fem::FemResultObjectPython"
    }
    if execution == "calculix":
        ccx = shutil.which("ccx")
        if ccx is None:
            raise _fail(
                "CalculiX execution was requested, but no ccx executable is available "
                "to the isolated worker.",
                stage="external_capability",
                capability="calculix",
            )
        fea.ccx_binary = ccx
        try:
            return_code = int(fea.start_ccx())
        except Exception as exc:
            raise _fail(
                f"CalculiX execution failed to start or wait: {exc}",
                stage="external_solver",
                exception_type=type(exc).__name__,
            ) from exc
        solver_executed = True
        if return_code != 0:
            raise _fail(
                f"CalculiX exited with code {return_code}.",
                stage="external_solver",
                return_code=return_code,
                stderr=str(getattr(fea, "ccx_stderr", "") or "")[-8192:],
            )
        try:
            fea.load_results()
        except Exception as exc:
            raise _fail(
                f"CalculiX result import failed: {exc}",
                stage="solver_result_readback",
                exception_type=type(exc).__name__,
            ) from exc
        candidates = [
            obj
            for obj in list(analysis.Group)
            if str(getattr(obj, "TypeId", "")) == "Fem::FemResultObjectPython"
        ]
        new_candidates = [
            obj
            for obj in candidates
            if str(getattr(obj, "Name", "")) not in existing_result_names
        ]
        if len(new_candidates) != 1:
            raise _fail(
                f"CalculiX produced {len(new_candidates)} new mechanical result "
                "objects; this FEM contract requires exactly one per result set.",
                stage="solver_result_readback",
            )
        result_obj = new_candidates[0]
        status = "solved"
    if result_obj is None:
        result_obj = ObjectsFem.makeResultMechanical(document, f"FEMResult{index:03d}")
        result_obj.Mesh = mesh
        # FreeCAD initializes an unsolved result with the sentinel ``[0]``.
        # It also initializes ``Stats`` with 26 zeroes. Neither is solver output,
        # so validate-only publication must normalize every result field to empty.
        result_obj.NodeNumbers = []
        for property_name in (*_RESULT_FLOAT_LISTS, *_RESULT_VECTOR_LISTS):
            if hasattr(result_obj, property_name):
                setattr(result_obj, property_name, [])
        analysis.addObject(result_obj)
    result_values = _bounded_result_data(result_obj)
    return {
        "object": result_obj,
        "data": {
            "native_type": str(result_obj.TypeId),
            "status": status,
            "execution": execution,
            "solver_executed": solver_executed,
            "input_deck": {
                "artifact_kind": "calculix_input",
                "artifact_path": str(input_path.relative_to(root.resolve())),
                "artifact_sha256": _sha256_file(input_path, stage="solver_input"),
                "artifact_bytes": input_size,
            },
            "result_values": result_values,
        },
    }


def _require_output(
    output_by_key: Mapping[str, tuple[str, str]],
    records: Mapping[str, Mapping[str, Any]],
    definition: Mapping[str, Any],
    expected_type: str,
    *,
    context: str,
) -> tuple[str, Mapping[str, Any]]:
    key = _definition_key(definition)
    output = output_by_key.get(key)
    record = records.get(key)
    if output is None or output[1] != expected_type or record is None:
        raise _fail(
            f"{context} must reference a returned {expected_type} output.",
            stage="graph_membership",
            path=context,
        )
    return output[0], record


def validate_and_build_fem(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build and validate independent native FEM study graphs in the worker."""

    expected_names = [str(item["name"]) for item in expected_outputs]
    if list(raw_result) != expected_names:
        raise _fail(
            "FEM result keys must exactly match expected_outputs in declared order.",
            stage="result_contract",
            expected=expected_names,
            received=list(raw_result),
        )
    definitions: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    output_by_key: dict[str, tuple[str, str]] = {}
    counts = {output_type: 0 for output_type in _OUTPUT_TYPES}
    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        definition = validate_fem_definition(
            raw_result[name],
            expected_output_type=output_type,
            context=f"result.{name}",
        )
        key = _definition_key(definition)
        if key in output_by_key:
            raise _fail(
                f"Outputs {output_by_key[key][0]!r} and {name!r} return duplicate "
                "FEM definitions.",
                stage="output_identity",
                output=name,
            )
        definitions[name] = definition
        keys[name] = key
        output_by_key[key] = (name, output_type)
        counts[output_type] += 1
    for output_type in ("analysis", "solver", "mesh", "result"):
        if counts[output_type] < 1:
            raise _fail(
                f"A FEM program must return at least one {output_type} "
                f"output; received {counts[output_type]}.",
                stage="graph_membership",
            )
    for output_type in ("material", "constraint", "load_case"):
        if counts[output_type] < 1:
            raise _fail(
                f"A FEM program must return at least one {output_type} output.",
                stage="graph_membership",
            )

    source_objects: dict[tuple[str, str], Any] = {}
    records: dict[str, dict[str, Any]] = {}
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        definition = definitions[name]
        operation = str(definition["operation"])
        if operation == "solver":
            records[keys[name]] = _build_solver(document, definition, index)
        elif operation == "material":
            records[keys[name]] = _build_material(
                document, definition, index, source_objects
            )
        elif operation == "constraint":
            records[keys[name]] = _build_constraint(
                document, definition, index, source_objects
            )
        elif operation == "mesh":
            records[keys[name]] = _build_mesh(
                document, definition, index, source_objects, root
            )

    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        definition = definitions[name]
        if definition["operation"] != "load_case":
            continue
        group = document.addObject("App::DocumentObjectGroup", f"FEMLoadCase{index:03d}")
        member_names = []
        for member_index, member_definition in enumerate(definition["arguments"][0]):
            output_name, record = _require_output(
                output_by_key,
                records,
                member_definition,
                "constraint",
                context=f"result.{name}.constraints[{member_index}]",
            )
            member_names.append(output_name)
        group.addProperty(
            "App::PropertyLinkList",
            "VibeCADConstraints",
            "VibeCAD",
            "Exact constraints assigned to this load case.",
        )
        group.VibeCADConstraints = [
            records[keys[member_name]]["object"] for member_name in member_names
        ]
        records[keys[name]] = {
            "object": group,
            "data": {
                "native_type": str(group.TypeId),
                "constraint_outputs": member_names,
            },
        }

    import ObjectsFem

    analysis_names = [
        name
        for name, definition in definitions.items()
        if definition["operation"] == "analysis"
    ]
    solve_names = [
        name
        for name, definition in definitions.items()
        if definition["operation"] == "solve"
    ]
    if not analysis_names or not solve_names:
        raise _fail(
            "A FEM program must return at least one analysis and result graph.",
            stage="graph_membership",
        )

    claimed_resources: dict[str, str] = {}
    claimed_constraints: dict[str, str] = {}

    def claim_resource(output_name: str, analysis_name: str, *, kind: str) -> None:
        previous = claimed_resources.get(output_name)
        if previous is not None:
            raise _fail(
                f"{kind.title()} output {output_name!r} belongs to both "
                f"{previous!r} and {analysis_name!r}.",
                stage="graph_membership",
                output=output_name,
            )
        claimed_resources[output_name] = analysis_name

    analysis_contexts: dict[str, dict[str, Any]] = {}
    for analysis_index, analysis_name in enumerate(analysis_names):
        analysis_definition = definitions[analysis_name]
        analysis_object_name = (
            "FEMAnalysis"
            if analysis_index == 0
            else f"FEMAnalysis{analysis_index:03d}"
        )
        analysis_obj = ObjectsFem.makeAnalysis(document, analysis_object_name)
        solver_name, solver_record = _require_output(
            output_by_key,
            records,
            analysis_definition["arguments"][0],
            "solver",
            context=f"result.{analysis_name}.solver",
        )
        claim_resource(solver_name, analysis_name, kind="solver")
        material_names = []
        material_records = []
        for member_index, member_definition in enumerate(
            analysis_definition["arguments"][1]
        ):
            output_name, record = _require_output(
                output_by_key,
                records,
                member_definition,
                "material",
                context=f"result.{analysis_name}.materials[{member_index}]",
            )
            claim_resource(output_name, analysis_name, kind="material")
            material_names.append(output_name)
            material_records.append(record)
        load_case_names = []
        load_case_records = []
        constraint_records: dict[str, Mapping[str, Any]] = {}
        for member_index, member_definition in enumerate(
            analysis_definition["arguments"][2]
        ):
            output_name, record = _require_output(
                output_by_key,
                records,
                member_definition,
                "load_case",
                context=f"result.{analysis_name}.load_cases[{member_index}]",
            )
            claim_resource(output_name, analysis_name, kind="load case")
            load_case_names.append(output_name)
            load_case_records.append(record)
            for constraint_name in record["data"]["constraint_outputs"]:
                constraint_owner = claimed_constraints.get(constraint_name)
                if constraint_owner not in {None, analysis_name}:
                    raise _fail(
                        f"Constraint output {constraint_name!r} belongs to both "
                        f"{constraint_owner!r} and {analysis_name!r}.",
                        stage="graph_membership",
                        output=constraint_name,
                    )
                claimed_constraints[constraint_name] = analysis_name
                constraint_records[constraint_name] = records[keys[constraint_name]]
        mesh_name, mesh_record = _require_output(
            output_by_key,
            records,
            analysis_definition["arguments"][3],
            "mesh",
            context=f"result.{analysis_name}.mesh",
        )
        claim_resource(mesh_name, analysis_name, kind="mesh")
        for record in (
            solver_record,
            *material_records,
            *constraint_records.values(),
            *load_case_records,
            mesh_record,
        ):
            analysis_obj.addObject(record["object"])
        analysis_record = {
            "object": analysis_obj,
            "data": {
                "native_type": str(analysis_obj.TypeId),
                "solver_output": solver_name,
                "material_outputs": material_names,
                "constraint_outputs": sorted(constraint_records),
                "load_case_outputs": load_case_names,
                "mesh_output": mesh_name,
            },
        }
        records[keys[analysis_name]] = analysis_record
        analysis_contexts[analysis_name] = {
            "analysis_definition": analysis_definition,
            "analysis_record": analysis_record,
            "solver_record": solver_record,
            "mesh_record": mesh_record,
            "material_names": material_names,
            "material_records": material_records,
            "constraint_records": constraint_records,
            "mesh_name": mesh_name,
        }

    result_names_by_analysis: dict[str, list[str]] = {
        name: [] for name in analysis_names
    }
    solver_executed = False
    for solve_name in solve_names:
        solve_definition = definitions[solve_name]
        linked_analysis_name, linked_analysis = _require_output(
            output_by_key,
            records,
            solve_definition["arguments"][0],
            "analysis",
            context=f"result.{solve_name}.analysis",
        )
        context = analysis_contexts.get(linked_analysis_name)
        if context is None:
            raise _fail(
                f"Result {solve_name!r} references an unavailable analysis.",
                stage="graph_membership",
                output=solve_name,
            )
        mesh_mapping = _validate_constraint_mesh_coverage(
            context["mesh_record"]["object"],
            list(context["constraint_records"].values()),
        )
        material_mapping = _validate_material_mesh_coverage(
            context["mesh_record"]["object"],
            list(
                zip(
                    context["material_names"],
                    context["material_records"],
                )
            ),
        )
        context["analysis_record"]["data"]["material_mesh_mapping"] = material_mapping
        result_record = _solve_analysis(
            document,
            linked_analysis["object"],
            context["solver_record"]["object"],
            context["mesh_record"]["object"],
            solve_definition,
            expected_outputs.index(
                next(item for item in expected_outputs if item["name"] == solve_name)
            ),
            root,
        )
        result_record["data"]["mesh_constraint_mapping"] = mesh_mapping
        result_record["data"]["material_mesh_mapping"] = material_mapping
        result_record["data"]["analysis_output"] = linked_analysis_name
        result_record["data"]["mesh_output"] = context["mesh_name"]
        records[keys[solve_name]] = result_record
        result_names_by_analysis[linked_analysis_name].append(solve_name)
        solver_executed = solver_executed or bool(
            result_record["data"]["solver_executed"]
        )

    missing_results = [
        analysis_name
        for analysis_name, result_names in result_names_by_analysis.items()
        if not result_names
    ]
    if missing_results:
        raise _fail(
            "Every FEM analysis must own at least one result set.",
            stage="graph_membership",
            analyses=missing_results,
        )

    outputs = []
    summaries = []
    for expected in expected_outputs:
        name = str(expected["name"])
        output_type = str(expected["type"])
        record = records.get(keys[name])
        if record is None:
            raise _fail(
                f"FEM output {name!r} was not evaluated.",
                stage="output_evaluation",
                output=name,
            )
        data = dict(record["data"])
        _encoded(
            data,
            limit=_MAX_NATIVE_READBACK_BYTES,
            label="native readback",
        )
        item = {
            "name": name,
            "type": output_type,
            "definition": definitions[name],
            "fem_data": data,
        }
        outputs.append(item)
        summaries.append(
            {
                "name": name,
                "type": output_type,
                "operation": str(definitions[name]["operation"]),
                "definition_sha256": keys[name],
                "native_type": str(data["native_type"]),
                "status": str(data.get("status") or "validated"),
            }
        )
    validation = {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "outputs": summaries,
        "analysis_outputs": analysis_names,
        "result_outputs": solve_names,
        "analysis_output": analysis_names[0] if len(analysis_names) == 1 else "",
        "result_output": solve_names[0] if len(solve_names) == 1 else "",
        "solver_executed": solver_executed,
    }
    _encoded(validation)
    return outputs, validation
