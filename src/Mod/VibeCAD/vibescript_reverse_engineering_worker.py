# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native worker for production Reverse Engineering VibeScript."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_reverse_engineering_api import ReverseEngineeringDomainAPI


VALIDATION_SCHEMA = "vibecad-vibescript-reverse-engineering-validation-v1"
_EXPORTS = (
    "fit_curve",
    "fit_surface",
    "reconstruct",
    "segment",
    "fit_metrics",
)
_OUTPUT_TYPES = ("curve", "surface", "brep", "mesh", "fit_metrics")
_GEOMETRY_TYPES = frozenset(_OUTPUT_TYPES[:-1])
_MAX_DEFINITION_BYTES = 1_000_000
_MAX_POINTS = 2_000_000
_MAX_FACETS = 5_000_000
_MAX_BREP_FACETS = 100_000
_MAX_METRIC_DISTANCE_TESTS = 1_000_000
_MAX_METRIC_SAMPLES = 4096
_MAX_PUBLISHED_SEGMENTS = 4096
_MAX_PUBLISHED_SEGMENT_MEMBERSHIPS = 100_000
# Matches OCC's Precision::Confusion() and VibeCAD's publication tolerance.
_NATIVE_DISTANCE_RESOLUTION = 1.0e-7
_REFERENCE_KINDS: dict[tuple[str, str], str] = {}


def _default_correction(details: Mapping[str, Any]) -> str:
    """Return one bounded model repair for every reverse-engineering stage."""

    stage = str(details.get("stage") or "")
    path = str(details.get("path") or "")
    output = str(details.get("output") or details.get("output_name") or "")
    location = f" at {path}" if path else (f" {output!r}" if output else "")
    if stage == "result_contract":
        return (
            "Return exactly the declared expected_outputs names, types, and order. Replace "
            "only the mismatched result entry and keep every declaration unchanged."
        )
    if stage == "definition_contract":
        return (
            f"Rebuild only the malformed value{location} with its api.fit_curve, "
            "api.fit_surface, api.reconstruct, api.segment, or api.fit_metrics call; never "
            "construct or mutate a serialized definition."
        )
    if stage == "source_selection":
        return (
            "Copy one exact eligible reference or available artifact_id from current Reverse "
            "Engineering domain context; do not use a label, path, stale id, or guessed name."
        )
    if stage == "source_validation":
        return (
            "Change only the reported source: use ordered point data for curve/reconstruction, "
            "or an eligible point/mesh source for surface fitting, with finite bounded geometry."
        )
    if stage == "curve_fitting":
        return (
            "Keep the ordered source points, then change only curve degree bounds, continuity, "
            "closure, parametrization, or tolerance named by the native failure and retry."
        )
    if stage == "surface_fitting":
        return (
            "Keep the source, make each degree smaller than its pole count, and change only the "
            "reported pole, smoothing, iteration, patch, or nonparallel UV-direction setting."
        )
    if stage == "reconstruction_parameters":
        return (
            "For structured_grid, provide the exact row-major [width,height] whose product equals "
            "the current point count, or use validated source Width/Height unchanged."
        )
    if stage == "native_capability":
        return (
            "Choose a method whose native capability is true in current domain context; use "
            "structured_grid only with ordered grid data and never rely on a hidden fallback."
        )
    if stage == "native_reconstruction":
        return (
            "Keep one reconstruction method and change only its reported neighborhood/scale "
            "parameter or source normals until it returns a nonempty bounded mesh."
        )
    if stage == "triangulation":
        return (
            "Repair only the reported structured-grid cell or source ordering so both triangles "
            "have nonzero area; preserve the intended grid dimensions and diagonal policy."
        )
    if stage == "brep_reconstruction":
        return (
            "Keep output_type='mesh' for dense or non-faceted intent, or reduce/repair the source "
            "until at most 100000 valid facets can form the requested faceted BREP compound."
        )
    if stage == "mesh_validation":
        return (
            "Repair or replace only the referenced mesh so it contains a bounded nonempty topology "
            "before reconstruction metrics or segmentation."
        )
    if stage == "segmentation_source":
        return (
            "Return the exact nested reconstruct/segment mesh under its own declared mesh output "
            "name, then pass that same returned value to api.segment."
        )
    if stage == "segmentation":
        return (
            "Change only minimum_facets or the selected segmentation method/angle so at least one "
            "region remains; do not invent facet groups."
        )
    if stage == "segmentation_selection":
        available = details.get("available_segments")
        suffix = f" 0 through {int(available) - 1}" if type(available) is int and available else ""
        return (
            f"Choose segment='all' or one reported zero-based segment index{suffix}; copy the "
            "current range rather than guessing from an older revision."
        )
    if stage == "segmentation_publication":
        return (
            "Publish one reported segment index, or increase minimum_facets to reduce the exact "
            "group count while keeping the segmentation method unchanged."
        )
    if stage == "metrics_target":
        return (
            "Return the exact geometry value targeted by api.fit_metrics under its own declared "
            "output name; do not reconstruct an equivalent second value."
        )
    if stage == "metrics_evaluation":
        return (
            "Keep the target identity and change only the invalid source geometry or positive "
            "tolerance reported by the distance evaluation."
        )
    if stage == "native_validation":
        return (
            "Change only the producing fit/reconstruction/segmentation settings until the exact "
            "declared BREP or Mesh is native-valid and nonempty."
        )
    if stage in {"artifact_export", "artifact_roundtrip"}:
        return (
            "Keep the validated geometry definition unchanged and retry only after the isolated "
            "worker can write, reimport, and authenticate its bounded BREP/BMS artifact."
        )
    return (
        "Correct only the reported Reverse Engineering source, method parameter, selector, or "
        "declared output and retry the failed working revision; do not recreate the program."
    )


class ReverseEngineeringCandidateError(RuntimeError):
    """A model-correctable reverse-engineering failure with stage details."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
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


def _fail(message: str, *, stage: str, **details: Any) -> ReverseEngineeringCandidateError:
    return ReverseEngineeringCandidateError(
        message,
        details={"stage": stage, **details},
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def native_capabilities() -> dict[str, bool]:
    """Return the current build's native ReverseEngineering algorithm set."""

    import ReverseEngineering

    return {
        name: callable(getattr(ReverseEngineering, name, None))
        for name in (
            "approxCurve",
            "approxSurface",
            "triangulate",
            "poissonReconstruction",
            "regionGrowingSegmentation",
            "featureSegmentation",
        )
    }


def _encoded(value: Any) -> bytes:
    try:
        result = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"A Reverse Engineering definition is not bounded JSON: {exc}",
            stage="definition_contract",
            exception_type=type(exc).__name__,
        ) from exc
    if len(result) > _MAX_DEFINITION_BYTES:
        raise _fail(
            f"A Reverse Engineering definition exceeds {_MAX_DEFINITION_BYTES} JSON bytes.",
            stage="definition_contract",
            json_bytes=len(result),
        )
    return result


def _canonical_point_source(payload: Any, *, context: str) -> Any:
    if not isinstance(payload, Mapping) or set(payload) != {
        "kind",
        "reference",
        "artifact_id",
        "points",
    }:
        raise _fail(
            f"{context} has malformed point-source fields.",
            stage="definition_contract",
            path=context,
        )
    kind = payload.get("kind")
    if kind == "document":
        return payload.get("reference")
    if kind == "artifact":
        return {"artifact_id": payload.get("artifact_id")}
    if kind == "inline":
        return payload.get("points")
    raise _fail(
        f"{context}.kind is unsupported.",
        stage="definition_contract",
        path=f"{context}.kind",
    )


def _domain_value(payload: Mapping[str, Any]) -> DomainValue:
    return DomainValue(
        domain=str(payload.get("domain") or ""),
        operation=str(payload.get("operation") or ""),
        output_type=str(payload.get("output_type") or ""),
        arguments=tuple(payload.get("arguments") or []),
        properties=dict(payload.get("properties") or {}),
    )


def _canonical_nested(value: Any, *, context: str) -> DomainValue:
    nested = validate_reverse_definition(
        value,
        require_domain_value=False,
        context=context,
    )
    return _domain_value(nested)


def _canonical_mesh_source(value: Any, *, context: str) -> Any:
    if isinstance(value, Mapping) and set(value) == {"kind", "reference"}:
        if value.get("kind") != "document":
            raise _fail(
                f"{context}.kind must be 'document'.",
                stage="definition_contract",
                path=f"{context}.kind",
            )
        return value.get("reference")
    return _canonical_nested(value, context=context)


def validate_reverse_definition(
    value: Any,
    *,
    expected_output_type: str | None = None,
    require_domain_value: bool = True,
    context: str = "result",
) -> dict[str, Any]:
    """Rebuild a graph through the provider API and require byte-exact semantics."""

    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping) and not require_domain_value:
        payload = dict(value)
    else:
        raise _fail(
            f"{context} must be a value returned by the Reverse Engineering api.",
            stage="definition_contract",
            path=context,
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields:
        raise _fail(
            f"{context} has malformed Reverse Engineering fields.",
            stage="definition_contract",
            path=context,
            missing=sorted(fields - set(payload)),
            unexpected=sorted(set(payload) - fields),
        )
    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if payload.get("domain") != "reverse_engineering" or operation not in _EXPORTS:
        raise _fail(
            f"{context} is not a supported Reverse Engineering value.",
            stage="definition_contract",
            path=context,
            domain=payload.get("domain"),
            operation=operation,
        )
    expected_by_operation = {
        "fit_curve": {"curve"},
        "fit_surface": {"surface"},
        "reconstruct": {"mesh", "brep"},
        "segment": {"mesh"},
        "fit_metrics": {"fit_metrics"},
    }
    if output_type not in expected_by_operation[operation]:
        raise _fail(
            f"{context} api.{operation} cannot return {output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    if expected_output_type is not None and output_type != expected_output_type:
        raise _fail(
            f"{context} returned type {output_type!r}; expected {expected_output_type!r}.",
            stage="definition_contract",
            path=f"{context}.output_type",
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or len(arguments) != 1:
        raise _fail(
            f"{context} must contain exactly one source or target argument.",
            stage="definition_contract",
            path=f"{context}.arguments",
        )
    if not isinstance(properties, dict):
        raise _fail(
            f"{context}.properties must be an object.",
            stage="definition_contract",
            path=f"{context}.properties",
        )
    _encoded(payload)
    api = ReverseEngineeringDomainAPI(_EXPORTS, _OUTPUT_TYPES)
    try:
        kwargs = dict(properties)
        if operation in {"fit_curve", "fit_surface", "reconstruct"}:
            source = _canonical_point_source(
                arguments[0],
                context=f"{context}.arguments[0]",
            )
            if operation == "reconstruct":
                kwargs["output_type"] = output_type
        elif operation == "segment":
            source = _canonical_mesh_source(
                arguments[0],
                context=f"{context}.arguments[0]",
            )
        else:
            source = _canonical_nested(
                arguments[0],
                context=f"{context}.arguments[0]",
            )
        rebuilt = getattr(api, operation)(source, **kwargs).to_payload()
    except ReverseEngineeringCandidateError:
        raise
    except (TypeError, ValueError) as exc:
        raise _fail(
            str(exc),
            stage="definition_contract",
            path=context,
            operation=operation,
            exception_type=type(exc).__name__,
        ) from exc
    if rebuilt != payload:
        raise _fail(
            f"{context} is not the canonical result of api.{operation}.",
            stage="definition_contract",
            path=context,
            required_changes=[
                f"Recreate this value with api.{operation}; do not edit serialized fields."
            ],
        )
    return payload


def configure_reverse_references(
    root: Path,
    entries: list[dict[str, Any]],
    approved_artifacts: list[dict[str, Any]],
) -> None:
    """Authenticate point and mesh snapshots using their production loaders."""

    point_entries = [
        entry for entry in entries if entry.get("artifact_kind") == "points_asc"
    ]
    mesh_entries = [
        entry for entry in entries if entry.get("artifact_kind") == "mesh_bms"
    ]
    if len(point_entries) + len(mesh_entries) != len(entries):
        raise _fail(
            "Reverse Engineering references must be Points ASC or Mesh BMS snapshots.",
            stage="source_selection",
            reference_count=len(entries),
        )
    from vibescript_meshpart_worker import configure_meshpart_references
    from vibescript_points_worker import configure_points_sources

    _REFERENCE_KINDS.clear()
    for entry in entries:
        key = (
            str(entry.get("document_uid") or ""),
            str(entry.get("object_name") or ""),
        )
        if not all(key) or key in _REFERENCE_KINDS:
            raise _fail(
                "Reverse Engineering references must have unique stable identities.",
                stage="source_selection",
                document_uid=key[0],
                object_name=key[1],
            )
        _REFERENCE_KINDS[key] = str(entry["artifact_kind"])
    try:
        configure_points_sources(root, point_entries, approved_artifacts)
        configure_meshpart_references(root, mesh_entries)
    except Exception as exc:
        details = getattr(exc, "details", None)
        raise _fail(
            f"Could not authenticate a Reverse Engineering source: {exc}",
            stage=str(
                details.get("stage")
                if isinstance(details, Mapping)
                else "source_selection"
            ),
            source_error=dict(details) if isinstance(details, Mapping) else None,
        ) from exc


def _point_state(
    source: Mapping[str, Any],
    document: Any,
    *,
    allow_mesh_vertices: bool,
) -> dict[str, Any]:
    from vibescript_points_worker import _load_source

    if source.get("kind") == "document":
        reference = source.get("reference")
        if not isinstance(reference, Mapping):
            raise _fail(
                "Document point source has no stable reference.",
                stage="source_selection",
            )
        key = (
            str(reference.get("document_uid") or ""),
            str(reference.get("object_name") or ""),
        )
        reference_kind = _REFERENCE_KINDS.get(key)
        if reference_kind == "mesh_bms":
            if not allow_mesh_vertices:
                raise _fail(
                    "This operation requires point data and does not accept Mesh vertices.",
                    stage="source_validation",
                    source_kind="mesh_bms",
                    required_changes=[
                        "Use a Points::Feature, approved point artifact, or inline points."
                    ],
                )
            from vibescript_meshpart_worker import detached_reference_mesh

            try:
                mesh, metadata = detached_reference_mesh(reference)
            except Exception as exc:
                details = getattr(exc, "details", None)
                raise _fail(
                    f"Could not resolve the authenticated Mesh source: {exc}",
                    stage=str(
                        details.get("stage")
                        if isinstance(details, dict)
                        else "source_selection"
                    ),
                    source_error=details,
                ) from exc
            points, _facets = mesh.Topology
            if not 2 <= len(points) <= _MAX_POINTS:
                raise _fail(
                    f"Mesh source contains {len(points)} vertices; expected 2-{_MAX_POINTS}.",
                    stage="source_validation",
                )
            values = [(float(point.x), float(point.y), float(point.z)) for point in points]
            return {
                "points": values,
                "attributes": {},
                "structured": None,
                "source": {
                    "kind": "document_mesh_vertices",
                    **metadata,
                },
                "invalid_points_removed": 0,
            }
        if reference_kind != "points_asc":
            raise _fail(
                "Document source was not staged as authenticated Points or Mesh data.",
                stage="source_selection",
                document_uid=key[0],
                object_name=key[1],
            )
    try:
        state = _load_source(
            source,
            {"invalid_points": "reject", "preserve_attributes": True},
            document,
        )
    except Exception as exc:
        if isinstance(exc, ReverseEngineeringCandidateError):
            raise
        details = getattr(exc, "details", None)
        raise _fail(
            f"Could not resolve the authenticated point source: {exc}",
            stage=str(details.get("stage") if isinstance(details, dict) else "source_selection"),
            source_error=details,
        ) from exc
    if not 2 <= len(state["points"]) <= _MAX_POINTS:
        raise _fail(
            f"Point source contains {len(state['points'])} points; expected 2-{_MAX_POINTS}.",
            stage="source_validation",
        )
    return state


def _triangle_area_squared(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return sum(value * value for value in cross) * 0.25


def _structured_triangles(
    points: Sequence[Sequence[float]],
    structured: Mapping[str, Any] | None,
    parameters: Mapping[str, Any],
) -> tuple[list[list[tuple[float, float, float]]], dict[str, Any]]:
    grid_size = parameters.get("grid_size")
    if grid_size is None and structured is not None:
        grid_size = [structured.get("width"), structured.get("height")]
    if not isinstance(grid_size, (list, tuple)) or len(grid_size) != 2:
        raise _fail(
            "structured_grid reconstruction requires parameters.grid_size or a "
            "source with validated Width and Height.",
            stage="reconstruction_parameters",
            required_changes=["Set parameters={'grid_size':[width,height]}."],
        )
    width, height = (int(grid_size[0]), int(grid_size[1]))
    if width < 2 or height < 2 or width * height != len(points):
        raise _fail(
            f"Grid {width} x {height} does not match {len(points)} source points.",
            stage="reconstruction_parameters",
            grid_size=[width, height],
            point_count=len(points),
        )
    diagonal = str(parameters.get("diagonal") or "shortest")
    triangles: list[list[tuple[float, float, float]]] = []
    forward_cells = 0
    backward_cells = 0
    for row in range(height - 1):
        for column in range(width - 1):
            a_index = row * width + column
            a = tuple(points[a_index])
            b = tuple(points[a_index + 1])
            c = tuple(points[a_index + width])
            d = tuple(points[a_index + width + 1])
            use_forward = diagonal == "forward"
            if diagonal == "shortest":
                ad = sum((a[index] - d[index]) ** 2 for index in range(3))
                bc = sum((b[index] - c[index]) ** 2 for index in range(3))
                use_forward = ad <= bc
            cell = ([a, b, d], [a, d, c]) if use_forward else ([a, b, c], [b, d, c])
            for triangle in cell:
                if _triangle_area_squared(*triangle) <= 1.0e-24:
                    raise _fail(
                        f"Grid cell ({column}, {row}) contains a degenerate triangle.",
                        stage="triangulation",
                        cell=[column, row],
                    )
                triangles.append(triangle)
            if use_forward:
                forward_cells += 1
            else:
                backward_cells += 1
    return triangles, {
        "grid_size": [width, height],
        "diagonal": diagonal,
        "forward_cells": forward_cells,
        "backward_cells": backward_cells,
    }


def _points_kernel(points: Sequence[Sequence[float]]):
    import FreeCAD as App
    import Points

    return Points.Points([App.Vector(*point) for point in points])


def _native_reconstruction(
    state: Mapping[str, Any],
    method: str,
    parameters: Mapping[str, Any],
    capabilities: Mapping[str, bool],
) -> tuple[Any, dict[str, Any]]:
    import ReverseEngineering

    points = list(state["points"])
    if method == "structured_grid":
        import Mesh

        triangles, trace = _structured_triangles(
            points,
            state.get("structured"),
            parameters,
        )
        return Mesh.Mesh(triangles), trace
    required = "triangulate" if method == "greedy" else "poissonReconstruction"
    if not capabilities.get(required):
        raise _fail(
            f"This FreeCAD build does not provide ReverseEngineering.{required}.",
            stage="native_capability",
            method=method,
            capability=required,
            available=[name for name, enabled in capabilities.items() if enabled],
            required_changes=[
                "Use method='structured_grid' with a structured cloud, or install a "
                "FreeCAD build compiled with PCL surface support."
            ],
        )
    kernel = _points_kernel(points)
    normals = list(dict(state.get("attributes") or {}).get("normals") or [])
    normal_vectors = None
    if normals:
        import FreeCAD as App

        normal_vectors = [App.Vector(*normal) for normal in normals]
    if method == "greedy":
        kwargs = {
            "Points": kernel,
            "SearchRadius": float(parameters["search_radius"]),
            "Mu": float(parameters["mu"]),
            "KSearch": int(parameters["k_search"]),
        }
        if normal_vectors is not None:
            kwargs["Normals"] = normal_vectors
        native_function = "ReverseEngineering.triangulate"
    else:
        kwargs = {
            "Points": kernel,
            "KSearch": int(parameters["k_search"]),
            "OctreeDepth": int(parameters["octree_depth"]),
            "SolverDivide": int(parameters["solver_divide"]),
            "SamplesPerNode": float(parameters["samples_per_node"]),
        }
        if normal_vectors is not None:
            kwargs["Normals"] = normal_vectors
        native_function = "ReverseEngineering.poissonReconstruction"
    try:
        mesh = (
            ReverseEngineering.triangulate(**kwargs)
            if method == "greedy"
            else ReverseEngineering.poissonReconstruction(**kwargs)
        )
    except Exception as exc:
        raise _fail(
            f"Native {native_function} failed: {type(exc).__name__}: {exc}",
            stage="native_reconstruction",
            method=method,
            native_function=native_function,
            parameters=dict(parameters),
            used_source_normals=normal_vectors is not None,
            exception_type=type(exc).__name__,
        ) from exc
    if mesh is None or not 1 <= int(mesh.CountFacets) <= _MAX_FACETS:
        raise _fail(
            f"Native {method} reconstruction returned no bounded mesh.",
            stage="native_reconstruction",
            facets=int(getattr(mesh, "CountFacets", 0) or 0),
        )
    return mesh, {
        "method": method,
        "used_source_normals": normal_vectors is not None,
        "parameters": dict(parameters),
    }


def _mesh_to_brep(mesh: Any) -> Any:
    facet_count = int(mesh.CountFacets)
    if not 1 <= facet_count <= _MAX_BREP_FACETS:
        raise _fail(
            f"BREP reconstruction supports 1-{_MAX_BREP_FACETS} facets; received {facet_count}.",
            stage="brep_reconstruction",
        )
    import FreeCAD as App
    import Part

    points, facets = mesh.Topology
    faces = []
    for facet_index, facet in enumerate(facets):
        try:
            vectors = [App.Vector(points[index]) for index in facet]
            wire = Part.makePolygon([*vectors, vectors[0]])
            face = Part.Face(wire)
        except Exception as exc:
            raise _fail(
                f"Facet {facet_index} BREP conversion failed: "
                f"{type(exc).__name__}: {exc}",
                stage="brep_reconstruction",
                facet_index=facet_index,
                exception_type=type(exc).__name__,
            ) from exc
        if face.isNull() or not face.isValid():
            raise _fail(
                f"Facet {facet_index} could not be converted to a valid BREP face.",
                stage="brep_reconstruction",
                facet_index=facet_index,
            )
        faces.append(face)
    shape = Part.makeCompound(faces)
    if shape.isNull() or not shape.isValid():
        raise _fail(
            "Triangulated BREP compound is invalid.",
            stage="brep_reconstruction",
        )
    return shape


def _mesh_topology(mesh: Any) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    raw_points, raw_facets = mesh.Topology
    points = [(float(point.x), float(point.y), float(point.z)) for point in raw_points]
    facets = [tuple(int(index) for index in facet) for facet in raw_facets]
    if not 1 <= len(facets) <= _MAX_FACETS:
        raise _fail(
            f"Mesh contains {len(facets)} facets; expected 1-{_MAX_FACETS}.",
            stage="mesh_validation",
        )
    return points, facets


def mesh_fingerprint(mesh: Any) -> str:
    points, facets = _mesh_topology(mesh)
    digest = hashlib.sha256()
    digest.update(struct.pack("<QQ", len(points), len(facets)))
    for point in points:
        digest.update(struct.pack("<ddd", *point))
    for facet in facets:
        digest.update(struct.pack("<QQQ", *facet))
    return digest.hexdigest()


def _facet_normal(
    points: Sequence[Sequence[float]], facet: Sequence[int]
) -> tuple[float, float, float]:
    a, b, c = (points[index] for index in facet)
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    length = math.sqrt(sum(value * value for value in cross))
    if length <= 1.0e-20:
        return (0.0, 0.0, 0.0)
    return tuple(value / length for value in cross)


def _facet_adjacency(facets: Sequence[Sequence[int]]) -> list[set[int]]:
    by_edge: dict[tuple[int, int], list[int]] = defaultdict(list)
    for facet_index, facet in enumerate(facets):
        for first, second in (
            (facet[0], facet[1]),
            (facet[1], facet[2]),
            (facet[2], facet[0]),
        ):
            by_edge[tuple(sorted((first, second)))].append(facet_index)
    adjacency = [set() for _facet in facets]
    for memberships in by_edge.values():
        for first in memberships:
            adjacency[first].update(second for second in memberships if second != first)
    return adjacency


def _portable_segments(mesh: Any, method: str, parameters: Mapping[str, Any]) -> list[list[int]]:
    points, facets = _mesh_topology(mesh)
    adjacency = _facet_adjacency(facets)
    normals = [_facet_normal(points, facet) for facet in facets]
    cosine_limit = (
        math.cos(math.radians(float(parameters["angle_degrees"])))
        if method == "normal_regions"
        else -1.0
    )
    unvisited = set(range(len(facets)))
    segments = []
    while unvisited:
        seed = min(unvisited)
        unvisited.remove(seed)
        queue = deque([seed])
        segment = []
        while queue:
            current = queue.popleft()
            segment.append(current)
            for candidate in sorted(adjacency[current]):
                if candidate not in unvisited:
                    continue
                if method == "normal_regions":
                    dot = sum(
                        normals[current][axis] * normals[candidate][axis]
                        for axis in range(3)
                    )
                    if dot < cosine_limit:
                        continue
                unvisited.remove(candidate)
                queue.append(candidate)
        segments.append(sorted(segment))
    minimum = int(parameters["minimum_facets"])
    segments = [segment for segment in segments if len(segment) >= minimum]
    return sorted(segments, key=lambda item: (-len(item), item[0]))


def _native_point_segments(
    mesh: Any,
    method: str,
    parameters: Mapping[str, Any],
    capabilities: Mapping[str, bool],
) -> list[list[int]]:
    native_name = (
        "regionGrowingSegmentation"
        if method == "native_region_growing"
        else "featureSegmentation"
    )
    if not capabilities.get(native_name):
        raise _fail(
            f"This FreeCAD build does not provide ReverseEngineering.{native_name}.",
            stage="native_capability",
            method=method,
            capability=native_name,
            available=[name for name, enabled in capabilities.items() if enabled],
            required_changes=[
                "Use connected_components or normal_regions, or install a FreeCAD "
                "build compiled with PCL segmentation support."
            ],
        )
    import ReverseEngineering

    points, facets = _mesh_topology(mesh)
    kernel = _points_kernel(points)
    try:
        raw_segments = getattr(ReverseEngineering, native_name)(
            Points=kernel,
            KSearch=int(parameters["k_search"]),
        )
    except Exception as exc:
        raise _fail(
            f"Native ReverseEngineering.{native_name} failed: "
            f"{type(exc).__name__}: {exc}",
            stage="segmentation",
            method=method,
            native_function=f"ReverseEngineering.{native_name}",
            k_search=int(parameters["k_search"]),
            exception_type=type(exc).__name__,
        ) from exc
    point_to_segment = {}
    for segment_index, raw in enumerate(raw_segments):
        for point_index in raw:
            point_to_segment.setdefault(int(point_index), segment_index)
    facet_segments: dict[int, list[int]] = defaultdict(list)
    for facet_index, facet in enumerate(facets):
        votes: dict[int, int] = defaultdict(int)
        for point_index in facet:
            if point_index in point_to_segment:
                votes[point_to_segment[point_index]] += 1
        if votes:
            segment_index = min(votes, key=lambda item: (-votes[item], item))
            facet_segments[segment_index].append(facet_index)
    minimum = int(parameters["minimum_facets"])
    segments = [
        sorted(segment)
        for segment in facet_segments.values()
        if len(segment) >= minimum
    ]
    return sorted(segments, key=lambda item: (-len(item), item[0]))


def _select_segments(
    mesh: Any,
    segments: Sequence[Sequence[int]],
    selector: str | int,
) -> tuple[Any, list[list[int]], dict[str, Any]]:
    if not segments:
        raise _fail(
            "Segmentation produced no segment meeting minimum_facets.",
            stage="segmentation",
        )
    if selector == "all":
        selected = [list(segment) for segment in segments]
        selected_segment: str | int = "all"
    else:
        index = int(selector)
        if index >= len(segments):
            raise _fail(
                f"Requested segment {index} is outside the available range "
                f"0-{len(segments) - 1}.",
                stage="segmentation_selection",
                requested_segment=index,
                available_segments=len(segments),
            )
        selected = [list(segments[index])]
        selected_segment = index
    membership_count = sum(len(segment) for segment in selected)
    if (
        len(selected) > _MAX_PUBLISHED_SEGMENTS
        or membership_count > _MAX_PUBLISHED_SEGMENT_MEMBERSHIPS
    ):
        raise _fail(
            "Selected segmentation groups exceed the bounded publication contract.",
            stage="segmentation_publication",
            selected_segments=len(selected),
            selected_memberships=membership_count,
            required_changes=[
                "Publish one segment index, or increase minimum_facets to reduce groups."
            ],
        )
    points, facets = _mesh_topology(mesh)
    triangles = []
    published_segments = []
    for segment in selected:
        first = len(triangles)
        triangles.extend(
            [points[point_index] for point_index in facets[facet_index]]
            for facet_index in segment
        )
        published_segments.append(list(range(first, len(triangles))))
    import Mesh

    result = Mesh.Mesh(triangles)
    for segment in published_segments:
        result.addSegment(segment)
    return result, published_segments, {
        "selected_segment": selected_segment,
        "retained_facets": len(triangles),
    }


def _sample_indices(count: int, maximum: int) -> list[int]:
    if count <= maximum:
        return list(range(count))
    if maximum <= 1:
        return [0]
    return sorted(
        {
            int(round(index * (count - 1) / (maximum - 1)))
            for index in range(maximum)
        }
    )


def _point_triangle_distance_squared(
    point: Sequence[float],
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
) -> float:
    """Squared point-triangle distance from Real-Time Collision Detection."""

    def subtract(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
        return tuple(left[index] - right[index] for index in range(3))

    def dot(left: Sequence[float], right: Sequence[float]) -> float:
        return sum(left[index] * right[index] for index in range(3))

    ab = subtract(b, a)
    ac = subtract(c, a)
    ap = subtract(point, a)
    d1, d2 = dot(ab, ap), dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return dot(ap, ap)
    bp = subtract(point, b)
    d3, d4 = dot(ab, bp), dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return dot(bp, bp)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        ratio = d1 / (d1 - d3)
        projection = tuple(a[index] + ratio * ab[index] for index in range(3))
        delta = subtract(point, projection)
        return dot(delta, delta)
    cp = subtract(point, c)
    d5, d6 = dot(ab, cp), dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return dot(cp, cp)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        ratio = d2 / (d2 - d6)
        projection = tuple(a[index] + ratio * ac[index] for index in range(3))
        delta = subtract(point, projection)
        return dot(delta, delta)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        edge = subtract(c, b)
        ratio = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        projection = tuple(b[index] + ratio * edge[index] for index in range(3))
        delta = subtract(point, projection)
        return dot(delta, delta)
    denominator = 1.0 / (va + vb + vc)
    v = vb * denominator
    w = vc * denominator
    projection = tuple(a[index] + ab[index] * v + ac[index] * w for index in range(3))
    delta = subtract(point, projection)
    return dot(delta, delta)


def compute_fit_metrics(
    geometry: Any,
    geometry_kind: str,
    source_points: Sequence[Sequence[float]],
    *,
    tolerance: float,
    source_facet_count: int = 0,
    retained_facets: int = 0,
    segment_count: int = 0,
) -> dict[str, Any]:
    """Compute deterministic bounded native deviation and coverage metrics."""

    source_count = len(source_points)
    if source_count <= 0:
        raise ValueError("Fit metrics require at least one source point.")
    if geometry_kind == "mesh":
        mesh_points, facets = _mesh_topology(geometry)
        maximum_samples = min(
            _MAX_METRIC_SAMPLES,
            max(1, _MAX_METRIC_DISTANCE_TESTS // max(1, len(facets))),
        )
        indices = _sample_indices(source_count, maximum_samples)
        distances = []
        triangles = [
            (mesh_points[facet[0]], mesh_points[facet[1]], mesh_points[facet[2]])
            for facet in facets
        ]
        for index in indices:
            point = source_points[index]
            squared = min(
                _point_triangle_distance_squared(point, *triangle)
                for triangle in triangles
            )
            distance = math.sqrt(max(0.0, squared))
            distances.append(
                0.0 if distance <= _NATIVE_DISTANCE_RESOLUTION else distance
            )
    else:
        import FreeCAD as App
        import Part

        # OCC cannot distinguish distances below Precision::Confusion().
        # Canonicalizing that interval prevents the same authenticated BREP
        # from acquiring different fit metrics when it is imported in the
        # isolated worker and again in the host process.
        native_resolution = max(
            _NATIVE_DISTANCE_RESOLUTION,
            float(Part.Precision.confusion()),
        )
        indices = _sample_indices(source_count, _MAX_METRIC_SAMPLES)
        distances = []
        for index in indices:
            vertex = Part.Vertex(App.Vector(*source_points[index]))
            distance = float(vertex.distToShape(geometry)[0])
            if not math.isfinite(distance) or distance < 0.0:
                raise ValueError("Native OCC distance evaluation returned an invalid value.")
            if distance <= native_resolution:
                distance = 0.0
            distances.append(distance)
    evaluated = len(distances)
    mean = sum(distances) / evaluated
    rms = math.sqrt(sum(value * value for value in distances) / evaluated)
    within = sum(value <= tolerance for value in distances)
    return {
        "schema": VALIDATION_SCHEMA,
        "source_point_count": source_count,
        "evaluated_point_count": evaluated,
        "sampled": evaluated < source_count,
        "minimum_distance": min(distances),
        "mean_distance": mean,
        "rms_distance": rms,
        "maximum_distance": max(distances),
        "tolerance": float(tolerance),
        "within_tolerance_count": within,
        "within_tolerance_fraction": within / evaluated,
        "source_facet_count": int(source_facet_count),
        "retained_facets": int(retained_facets),
        "segment_count": int(segment_count),
    }


def _record_fit_metrics(
    record: Mapping[str, Any],
    *,
    tolerance: float,
) -> dict[str, Any]:
    """Measure the exact geometry representation retained by one worker record."""

    geometry = record["geometry"]
    geometry_kind = str(record["geometry_kind"])
    trace = dict(record["operation_trace"])
    if geometry_kind == "mesh":
        source_facet_count = int(trace.get("source_facets") or 0)
        retained_facets = int(
            trace.get("retained_facets") or int(geometry.CountFacets)
        )
        segment_count = int(trace.get("segment_count") or geometry.countSegments())
    else:
        source_facet_count = 0
        retained_facets = 0
        segment_count = 0
    try:
        return compute_fit_metrics(
            geometry,
            geometry_kind,
            list(record["source_state"]["points"]),
            tolerance=tolerance,
            source_facet_count=source_facet_count,
            retained_facets=retained_facets,
            segment_count=segment_count,
        )
    except ReverseEngineeringCandidateError:
        raise
    except Exception as exc:
        raise _fail(
            f"Native fit-metric evaluation failed: {type(exc).__name__}: {exc}",
            stage="metrics_evaluation",
            target_operation=str(record.get("operation") or ""),
            target_output_type=str(record.get("output_type") or ""),
            tolerance=float(tolerance),
            exception_type=type(exc).__name__,
        ) from exc


def _geometry_record(
    payload: Mapping[str, Any],
    document: Any,
    capabilities: Mapping[str, bool],
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = hashlib.sha256(_encoded(payload)).hexdigest()
    if key in cache:
        return cache[key]
    operation = str(payload["operation"])
    output_type = str(payload["output_type"])
    properties = dict(payload["properties"])
    label = str(properties.get("label") or "")
    if operation == "fit_curve":
        state = _point_state(payload["arguments"][0], document, allow_mesh_vertices=False)
        import ReverseEngineering

        parameter_names = {
            "chord_length": "ChordLength",
            "centripetal": "Centripetal",
            "uniform": "Uniform",
        }
        continuity = {"c0": 0, "c1": 1, "c2": 2}[properties["continuity"]]
        try:
            curve = ReverseEngineering.approxCurve(
                Points=list(state["points"]),
                ParametrizationType=parameter_names[properties["parametrization"]],
                Closed=bool(properties["closed"]),
                MinDegree=int(properties["min_degree"]),
                MaxDegree=int(properties["max_degree"]),
                Continuity=continuity,
                Tolerance=float(properties["tolerance"]),
            )
            geometry = curve.toShape()
        except Exception as exc:
            raise _fail(
                f"Native ReverseEngineering.approxCurve failed: {type(exc).__name__}: {exc}",
                stage="curve_fitting",
                exception_type=type(exc).__name__,
            ) from exc
        geometry_kind = "brep"
        trace = {
            "operation": operation,
            "native_function": "ReverseEngineering.approxCurve",
            "input_points": len(state["points"]),
        }
    elif operation == "fit_surface":
        state = _point_state(payload["arguments"][0], document, allow_mesh_vertices=True)
        import FreeCAD as App
        import ReverseEngineering

        kwargs = {
            "Points": list(state["points"]),
            "UDegree": int(properties["u_degree"]),
            "VDegree": int(properties["v_degree"]),
            "NbUPoles": int(properties["u_poles"]),
            "NbVPoles": int(properties["v_poles"]),
            "Smooth": bool(properties["smooth"]),
            "Weight": float(properties["smoothing_weight"]),
            "Grad": float(properties["gradient_weight"]),
            "Bend": float(properties["bending_weight"]),
            "Curv": float(properties["curvature_weight"]),
            "Iterations": int(properties["iterations"]),
            "Correction": bool(properties["correction"]),
            "PatchFactor": float(properties["patch_factor"]),
        }
        if properties.get("uv_directions") is not None:
            kwargs["UVDirs"] = tuple(
                App.Vector(*direction) for direction in properties["uv_directions"]
            )
        try:
            surface = ReverseEngineering.approxSurface(**kwargs)
            geometry = surface.toShape()
        except Exception as exc:
            raise _fail(
                f"Native ReverseEngineering.approxSurface failed: {type(exc).__name__}: {exc}",
                stage="surface_fitting",
                exception_type=type(exc).__name__,
            ) from exc
        geometry_kind = "brep"
        trace = {
            "operation": operation,
            "native_function": "ReverseEngineering.approxSurface",
            "input_points": len(state["points"]),
        }
    elif operation == "reconstruct":
        state = _point_state(payload["arguments"][0], document, allow_mesh_vertices=False)
        method = str(properties["method"])
        mesh, method_trace = _native_reconstruction(
            state,
            method,
            dict(properties["parameters"]),
            capabilities,
        )
        if output_type == "brep":
            geometry = _mesh_to_brep(mesh)
            geometry_kind = "brep"
        else:
            geometry = mesh
            geometry_kind = "mesh"
        trace = {
            "operation": operation,
            "method": method,
            "input_points": len(state["points"]),
            "output_type": output_type,
            "method_trace": method_trace,
        }
    elif operation == "segment":
        source_argument = payload["arguments"][0]
        if (
            isinstance(source_argument, Mapping)
            and source_argument.get("kind") == "document"
        ):
            from vibescript_meshpart_worker import detached_reference_mesh

            try:
                source_mesh, source_metadata = detached_reference_mesh(
                    source_argument.get("reference")
                )
            except Exception as exc:
                details = getattr(exc, "details", None)
                raise _fail(
                    f"Could not resolve the authenticated segmentation Mesh: {exc}",
                    stage=str(
                        details.get("stage")
                        if isinstance(details, dict)
                        else "source_selection"
                    ),
                    source_error=details,
                ) from exc
            source_identity = {
                "kind": "document_mesh",
                **source_metadata,
            }
        else:
            nested = validate_reverse_definition(
                source_argument,
                expected_output_type="mesh",
                require_domain_value=False,
                context="segment.source",
            )
            source_record = _geometry_record(nested, document, capabilities, cache)
            source_mesh = source_record["geometry"]
            source_identity = {
                "kind": "nested_mesh",
                "operation": nested["operation"],
                "fingerprint": mesh_fingerprint(source_mesh),
            }
        source_points, source_facets = _mesh_topology(source_mesh)
        method = str(properties["method"])
        parameters = dict(properties["parameters"])
        if method in {"connected_components", "normal_regions"}:
            segments = _portable_segments(source_mesh, method, parameters)
        else:
            segments = _native_point_segments(
                source_mesh,
                method,
                parameters,
                capabilities,
            )
        geometry, published_segments, selection = _select_segments(
            source_mesh,
            segments,
            parameters["segment"],
        )
        state = {
            "points": source_points,
            "source": source_identity,
        }
        geometry_kind = "mesh"
        trace = {
            "operation": operation,
            "method": method,
            "source_facets": len(source_facets),
            "segment_count": len(segments),
            "segment_sizes": [len(segment) for segment in segments[:256]],
            "segment_sizes_truncated": len(segments) > 256,
            "published_segments": published_segments,
            **selection,
        }
    else:
        raise _fail(
            f"Operation {operation!r} is not a geometry operation.",
            stage="definition_contract",
        )
    if geometry_kind == "brep":
        if geometry is None or geometry.isNull() or not geometry.isValid():
            raise _fail(
                f"api.{operation} produced an invalid BREP.",
                stage="native_validation",
            )
    else:
        if geometry is None or not 1 <= int(geometry.CountFacets) <= _MAX_FACETS:
            raise _fail(
                f"api.{operation} produced no bounded native Mesh.",
                stage="native_validation",
            )
    record = {
        "definition_key": key,
        "definition": dict(payload),
        "operation": operation,
        "output_type": output_type,
        "label": label,
        "geometry_kind": geometry_kind,
        "geometry": geometry,
        "source_state": state,
        "operation_trace": trace,
    }
    record["fit_metrics"] = _record_fit_metrics(record, tolerance=0.1)
    cache[key] = record
    return record


def validate_and_build_reverse_engineering(
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    root: Path,
    document: Any,
    *,
    max_shape_subelements: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build, validate, and export all Reverse Engineering outputs."""

    expected_names = [str(item["name"]) for item in expected_outputs]
    if list(raw_result) != expected_names:
        raise _fail(
            "Reverse Engineering result keys must exactly match expected_outputs "
            "in declared order.",
            stage="result_contract",
            expected=expected_names,
            received=list(raw_result),
        )
    capabilities = native_capabilities()
    definitions = {}
    for expected in expected_outputs:
        name = str(expected["name"])
        definitions[name] = validate_reverse_definition(
            raw_result[name],
            expected_output_type=str(expected["type"]),
            context=f"result.{name}",
        )
    cache: dict[str, dict[str, Any]] = {}
    geometry_by_key = {}
    for name, payload in definitions.items():
        if payload["output_type"] == "fit_metrics":
            continue
        geometry_by_key[hashlib.sha256(_encoded(payload)).hexdigest()] = (
            name,
            _geometry_record(payload, document, capabilities, cache),
        )
    for name, payload in definitions.items():
        if payload["operation"] != "segment":
            continue
        source = payload["arguments"][0]
        if isinstance(source, Mapping) and source.get("kind") == "document":
            continue
        source_key = hashlib.sha256(_encoded(source)).hexdigest()
        if source_key not in geometry_by_key:
            raise _fail(
                f"Segment output {name!r} consumes a reconstructed mesh that is not "
                "returned as a declared program output.",
                stage="segmentation_source",
                output=name,
                required_changes=[
                    "Return the exact source mesh under its own expected output name."
                ],
            )
    artifacts_by_name: dict[str, dict[str, Any]] = {}
    for index, expected in enumerate(expected_outputs):
        name = str(expected["name"])
        payload = definitions[name]
        if payload["output_type"] == "fit_metrics":
            continue
        record = geometry_by_key[hashlib.sha256(_encoded(payload)).hexdigest()][1]
        geometry = record["geometry"]
        if record["geometry_kind"] == "brep":
            import Part
            from vibescript_part_worker import part_shape_facts

            relative = Path("outputs") / f"output-{index:03d}.brep"
            target_path = root / relative
            try:
                geometry.exportBrep(str(target_path))
            except Exception as exc:
                raise _fail(
                    f"Could not export Reverse Engineering output {name!r}: "
                    f"{type(exc).__name__}: {exc}",
                    stage="artifact_export",
                    output_name=name,
                    exception_type=type(exc).__name__,
                ) from exc
            if not target_path.is_file() or target_path.stat().st_size <= 0:
                raise _fail(
                    f"Could not export Reverse Engineering output {name!r}.",
                    stage="artifact_export",
                )
            canonical = Part.Shape()
            try:
                canonical.importBrep(str(target_path))
            except Exception as exc:
                raise _fail(
                    f"Could not reimport Reverse Engineering output {name!r}: "
                    f"{type(exc).__name__}: {exc}",
                    stage="artifact_roundtrip",
                    output_name=name,
                    exception_type=type(exc).__name__,
                ) from exc
            if canonical.isNull() or not canonical.isValid():
                raise _fail(
                    f"Could not reimport Reverse Engineering output {name!r}.",
                    stage="artifact_roundtrip",
                )
            record["geometry"] = canonical
            artifact_digest = _sha256_file(target_path)
            facts = part_shape_facts(
                canonical,
                max_subelements=max_shape_subelements,
            )
            artifacts_by_name[name] = {
                "artifact_kind": "brep",
                "artifact_path": str(relative),
                "artifact_sha256": artifact_digest,
                "geometry_fingerprint": artifact_digest,
                "facts": facts,
            }
        else:
            import Mesh
            from vibescript_mesh_worker import mesh_diagnostics

            relative = Path("outputs") / f"output-{index:03d}.bms"
            target_path = root / relative
            try:
                geometry.write(str(target_path))
            except Exception as exc:
                raise _fail(
                    f"Could not export Reverse Engineering mesh {name!r}: "
                    f"{type(exc).__name__}: {exc}",
                    stage="artifact_export",
                    output_name=name,
                    exception_type=type(exc).__name__,
                ) from exc
            if not target_path.is_file() or target_path.stat().st_size <= 0:
                raise _fail(
                    f"Could not export Reverse Engineering mesh {name!r}.",
                    stage="artifact_export",
                )
            try:
                canonical = Mesh.Mesh(str(target_path))
            except Exception as exc:
                raise _fail(
                    f"Could not reimport Reverse Engineering mesh {name!r}: "
                    f"{type(exc).__name__}: {exc}",
                    stage="artifact_roundtrip",
                    output_name=name,
                    exception_type=type(exc).__name__,
                ) from exc
            if not 1 <= int(canonical.CountFacets) <= _MAX_FACETS:
                raise _fail(
                    f"Could not reimport Reverse Engineering mesh {name!r}.",
                    stage="artifact_roundtrip",
                )
            record["geometry"] = canonical
            artifact_digest = _sha256_file(target_path)
            artifacts_by_name[name] = {
                "artifact_kind": "mesh_bms",
                "artifact_path": str(relative),
                "artifact_sha256": artifact_digest,
                "geometry_fingerprint": mesh_fingerprint(canonical),
                "facts": mesh_diagnostics(canonical),
            }
        record["fit_metrics"] = _record_fit_metrics(record, tolerance=0.1)
    outputs = []
    summaries = []
    for expected in expected_outputs:
        name = str(expected["name"])
        payload = definitions[name]
        output_type = str(payload["output_type"])
        item: dict[str, Any] = {
            "name": name,
            "type": output_type,
            "definition": payload,
        }
        if output_type == "fit_metrics":
            target = dict(payload["arguments"][0])
            target_key = hashlib.sha256(_encoded(target)).hexdigest()
            match = geometry_by_key.get(target_key)
            if match is None:
                raise _fail(
                    f"Fit metrics output {name!r} targets geometry that is not returned "
                    "as a declared program output.",
                    stage="metrics_target",
                    output=name,
                    required_changes=[
                        "Return the exact target value under its own expected output name."
                    ],
                )
            target_name, record = match
            metrics = _record_fit_metrics(
                record,
                tolerance=float(payload["properties"]["tolerance"]),
            )
            reverse_data = {
                "schema": VALIDATION_SCHEMA,
                "operation": "fit_metrics",
                "label": str(payload["properties"].get("label") or name),
                "target_output": target_name,
                "target_operation": record["operation"],
                "target_output_type": record["output_type"],
                "fit_metrics": metrics,
            }
            item["reverse_data"] = reverse_data
            summary = {
                "name": name,
                "type": output_type,
                "operation": "fit_metrics",
                "target_output": target_name,
                "geometry_fingerprint": "",
                "fit_metrics": metrics,
            }
        else:
            record = geometry_by_key[hashlib.sha256(_encoded(payload)).hexdigest()][1]
            artifact = artifacts_by_name[name]
            item.update(
                {
                    key: value
                    for key, value in artifact.items()
                    if key != "geometry_fingerprint"
                }
            )
            artifact_digest = str(artifact["artifact_sha256"])
            fingerprint = str(artifact["geometry_fingerprint"])
            facts = dict(artifact["facts"])
            reverse_data = {
                "schema": VALIDATION_SCHEMA,
                "operation": record["operation"],
                "label": record["label"] or name,
                "source": dict(record["source_state"].get("source") or {}),
                "artifact_sha256": artifact_digest,
                "geometry_fingerprint": fingerprint,
                "operation_trace": dict(record["operation_trace"]),
                "fit_metrics": dict(record["fit_metrics"]),
                "facts": facts,
            }
            item["reverse_data"] = reverse_data
            summary = {
                "name": name,
                "type": output_type,
                "operation": record["operation"],
                "target_output": "",
                "geometry_fingerprint": fingerprint,
                "fit_metrics": dict(record["fit_metrics"]),
            }
        _encoded(item["reverse_data"])
        outputs.append(item)
        summaries.append(summary)
    validation = {
        "schema": VALIDATION_SCHEMA,
        "output_count": len(outputs),
        "native_capabilities": dict(capabilities),
        "outputs": summaries,
    }
    _encoded(validation)
    return outputs, validation
