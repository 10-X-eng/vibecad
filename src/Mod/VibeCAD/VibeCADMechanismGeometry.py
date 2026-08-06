# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact geometry evidence for internal mechanism evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import math
import os
import re
from typing import Any, Callable

STATIC_PAIR_EVIDENCE_SCHEMA = "vibecad-mechanism-static-pair-evidence-v1"
STATIC_MECHANISM_EVIDENCE_SCHEMA = "vibecad-mechanism-static-evidence-v1"
DYNAMIC_COLLISION_TRACE_SCHEMA = "vibecad-mechanism-dynamic-collisions-v1"

_COMPONENT_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_DECLARATION_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_INTERFACE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SUBELEMENT = re.compile(r"^(Face|Edge|Vertex)([1-9][0-9]*)$")
_MAX_COMPONENTS = 256
_MAX_PAIRS = (_MAX_COMPONENTS * (_MAX_COMPONENTS - 1)) // 2
_MAX_WITNESSES_PER_PAIR = 8
_MAX_CONTACT_WITNESSES = 4096
_COLLISION_MESH_LINEAR_DEFLECTION_MM = 0.05
_COLLISION_MESH_ANGULAR_DEFLECTION_RADIANS = 0.5


class MechanismGeometryError(ValueError):
    """A static mechanism geometry request cannot be evaluated exactly."""


def _disjoint_surface_job_batches(
    jobs: Sequence[tuple[int, str, str]],
) -> list[list[tuple[int, str, str]]]:
    """Schedule proximity calls without sharing an OCCT shape concurrently.

    ``BRepExtrema_ShapeProximity`` reads triangulations stored on the topology.
    Concurrent calls involving the same shape are not safe: detailed threads
    and imported B-reps can transiently appear to have an untessellated face.
    Each deterministic batch is therefore a matching in the component-pair
    graph. Disjoint pairs still run in parallel.
    """

    batches: list[list[tuple[int, str, str]]] = []
    used_names: list[set[str]] = []
    for job in jobs:
        _candidate_index, first_name, second_name = job
        names = {first_name, second_name}
        for index, occupied in enumerate(used_names):
            if occupied.isdisjoint(names):
                batches[index].append(job)
                occupied.update(names)
                break
        else:
            batches.append([job])
            used_names.append(set(names))
    return batches


def _ensure_complete_collision_triangulation(shape: Any, *, path: str) -> int:
    """Attach a validated triangulation to every face used by proximity.

    ``MeshPart.meshFromShape`` is the fast path and is sufficient for ordinary
    native and imported solids. Some detailed helical B-reps still leave one or
    more OCCT faces without attached triangulation even though the returned mesh
    contains facets. In that exact case, tessellate each face in place and
    validate the complete topology before any simulation pair is evaluated.
    """

    import MeshPart

    collision_mesh = MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=_COLLISION_MESH_LINEAR_DEFLECTION_MM,
        AngularDeflection=_COLLISION_MESH_ANGULAR_DEFLECTION_RADIANS,
        Relative=False,
    )
    triangle_count = int(collision_mesh.CountFacets)
    del collision_mesh
    if triangle_count < 1:
        raise _error(path, "contains no triangles")

    try:
        shape.proximity(shape, 0.0)
        return triangle_count
    except Exception as exc:
        if "requires every face to be tessellated" not in str(exc):
            raise _error(
                path,
                f"could not validate the collision triangulation: {exc}",
            ) from exc

    triangle_count = 0
    for face_index, face in enumerate(list(shape.Faces), start=1):
        try:
            _vertices, facets = face.tessellate(
                _COLLISION_MESH_LINEAR_DEFLECTION_MM,
                False,
            )
        except Exception as exc:
            raise _error(
                f"{path}.Face{face_index}",
                f"could not tessellate the face: {exc}",
            ) from exc
        face_triangle_count = len(facets)
        if face_triangle_count < 1:
            raise _error(
                f"{path}.Face{face_index}",
                "contains no collision triangles",
            )
        triangle_count += face_triangle_count

    try:
        shape.proximity(shape, 0.0)
    except Exception as exc:
        raise _error(
            path,
            f"face-complete collision triangulation validation failed: {exc}",
        ) from exc
    return triangle_count


class DynamicCollisionEvaluator:
    """Reuse detached component BREPs for exact collision checks over many poses.

    A simulation changes occurrence placements, not source topology.  Detaching
    each source once avoids repeatedly copying imported models and modeled
    fasteners at every frame while keeping the caller's shapes untouched.
    """

    def __init__(
        self,
        components: Mapping[str, Any],
        *,
        definition_keys: Mapping[str, str] | None = None,
    ) -> None:
        if (
            not isinstance(components, Mapping)
            or not 1 <= len(components) <= _MAX_COMPONENTS
        ):
            raise _error(
                "components",
                f"must contain 1-{_MAX_COMPONENTS} named solid shapes",
            )
        if definition_keys is not None and (
            not isinstance(definition_keys, Mapping)
            or set(definition_keys) != set(components)
            or any(
                not isinstance(value, str) or not value
                for value in definition_keys.values()
            )
        ):
            raise _error(
                "definition_keys",
                "must contain one non-empty authenticated identity per component",
            )
        self._shapes: dict[str, Any] = {}
        self._local_bounds: dict[str, dict[str, list[float]]] = {}
        mesh_definitions: list[tuple[Any, Any, int]] = []
        mesh_definitions_by_key: dict[str, tuple[Any, int]] = {}
        import FreeCAD as App

        for raw_name, source in components.items():
            if not isinstance(raw_name, str) or not _COMPONENT_ID.fullmatch(raw_name):
                raise _error(
                    "components",
                    "component names must be stable identifiers",
                )
            try:
                if (
                    source is None
                    or bool(source.isNull())
                    or not bool(source.isValid())
                    or len(list(getattr(source, "Solids", []) or [])) < 1
                ):
                    raise _error(
                        f"components.{raw_name}",
                        "shape must contain at least one valid solid",
                )
                definition_key = (
                    str(definition_keys[raw_name])
                    if definition_keys is not None
                    else ""
                )
                shared_definition = (
                    mesh_definitions_by_key.get(definition_key)
                    if definition_key
                    else next(
                        (
                            (definition_shape, triangle_count)
                            for definition_source, definition_shape, triangle_count
                            in mesh_definitions
                            if bool(source.isPartner(definition_source))
                            and str(getattr(source, "Orientation", ""))
                            == str(getattr(definition_source, "Orientation", ""))
                        ),
                        None,
                    )
                )
                if shared_definition is None:
                    # Generate one isolated collision mesh per authenticated
                    # BREP definition. Never reuse the display triangulation:
                    # its density is a GUI preference and can be millions of
                    # triangles on detailed imports. MeshPart attaches the
                    # deterministic OCCT triangulation without materializing a
                    # second Python list of every triangle.
                    detached = source.cleaned()
                    detached.Placement = App.Placement()
                    triangle_count = _ensure_complete_collision_triangulation(
                        detached,
                        path=f"components.{raw_name}.collision_mesh",
                    )
                    mesh_definitions.append(
                        (source, detached, triangle_count)
                    )
                    if definition_key:
                        mesh_definitions_by_key[definition_key] = (
                            detached,
                            triangle_count,
                        )
                else:
                    definition_shape, triangle_count = shared_definition
                    # Occurrences need independent top-level placements. Copy
                    # the already-bounded collision triangles instead of
                    # remeshing the same authenticated definition.
                    detached = definition_shape.copy(False, True)
                    detached.Placement = App.Placement()
            except MechanismGeometryError:
                raise
            except Exception as exc:
                raise _error(
                    f"components.{raw_name}",
                    f"could not detach the source shape: {exc}",
                ) from exc
            self._shapes[raw_name] = detached
            self._local_bounds[raw_name] = _bounds(
                detached,
                path=f"components.{raw_name}.bounds",
            )
        self._unique_mesh_definition_count = len(mesh_definitions)
        self._unique_mesh_triangle_count = sum(
            triangle_count
            for _source, _shape, triangle_count in mesh_definitions
        )
        self._component_names = list(self._shapes)
        self._pairs = [
            (first, second)
            for index, first in enumerate(self._component_names)
            for second in self._component_names[index + 1 :]
        ]

    @property
    def component_names(self) -> list[str]:
        return list(self._component_names)

    @property
    def pair_count(self) -> int:
        return len(self._pairs)

    @property
    def collision_mesh_statistics(self) -> dict[str, int | float]:
        return {
            "unique_collision_mesh_count": self._unique_mesh_definition_count,
            "unique_collision_mesh_triangle_count": (
                self._unique_mesh_triangle_count
            ),
            "collision_mesh_angular_deflection_radians": (
                _COLLISION_MESH_ANGULAR_DEFLECTION_RADIANS
            ),
        }

    def precompute_strict_containment(
        self,
        frames: Sequence[Mapping[str, Any]],
        *,
        excluded_pairs: set[tuple[str, str]] | frozenset[tuple[str, str]],
        progress_callback: Callable[[str, str, str, int, int], None]
        | None = None,
    ) -> set[tuple[int, tuple[str, str]]]:
        """Classify every moving vertex path with one classifier per pair.

        Points are transformed into the target component's local coordinates,
        so its exact BREP classifier is loaded once and reused across the entire
        simulation instead of once per frame.
        """

        frame_placements: list[dict[str, Any]] = []
        frame_bounds: list[dict[str, dict[str, list[float]]]] = []
        for frame_offset, frame in enumerate(frames, start=1):
            placements = frame.get("component_placements")
            if not isinstance(placements, Mapping) or set(placements) != set(
                self._component_names
            ):
                raise _error(
                    f"frames[{frame_offset}].component_placements",
                    "must contain exactly every collision component",
                )
            exact_placements: dict[str, Any] = {}
            exact_bounds: dict[str, dict[str, list[float]]] = {}
            for name in self._component_names:
                value = placements[name]
                if not isinstance(value, Mapping) or set(value) != {
                    "position_mm",
                    "rotation_xyzw",
                }:
                    raise _error(
                        f"frames[{frame_offset}].component_placements.{name}",
                        "must contain exactly position_mm and rotation_xyzw",
                    )
                placement = _placement(
                    {
                        "position": value["position_mm"],
                        "rotation": value["rotation_xyzw"],
                    },
                    path=(
                        f"frames[{frame_offset}].component_placements.{name}"
                    ),
                )
                exact_placements[name] = placement
                exact_bounds[name] = _transformed_bounds(
                    self._local_bounds[name],
                    placement,
                )
            frame_placements.append(exact_placements)
            frame_bounds.append(exact_bounds)

        jobs: list[tuple[str, str, list[int]]] = []
        for first_name, second_name in self._pairs:
            if (first_name, second_name) in excluded_pairs:
                continue
            candidate_frames = [
                frame_offset
                for frame_offset, bounds in enumerate(frame_bounds, start=1)
                if bool(
                    _aabb_evidence(
                        bounds[first_name],
                        bounds[second_name],
                    )["overlaps_or_touches"]
                )
            ]
            if candidate_frames:
                jobs.append((first_name, second_name, candidate_frames))

        contained: set[tuple[int, tuple[str, str]]] = set()
        for pair_index, (first_name, second_name, candidate_frames) in enumerate(
            jobs,
            start=1,
        ):
            if progress_callback is not None:
                progress_callback(
                    "started",
                    first_name,
                    second_name,
                    pair_index,
                    len(jobs),
                )
            # Surface intersection is handled by OCCT's triangle BVH below.
            # Only full containment has no intersecting surface, and one strict
            # boundary vertex from the contained solid proves it. Test the first
            # stable vertex in both directions; exhaustive vertex classification
            # is unnecessary and prohibitively expensive on imported B-reps.
            for source_name, target_name in (
                (first_name, second_name),
                (second_name, first_name),
            ):
                source_vertices = list(
                    getattr(self._shapes[source_name], "Vertexes", []) or []
                )
                if not source_vertices:
                    continue
                remaining_frames = [
                    frame_offset
                    for frame_offset in candidate_frames
                    if (frame_offset, (first_name, second_name)) not in contained
                    and _aabb_contains(
                        frame_bounds[frame_offset - 1][target_name],
                        frame_bounds[frame_offset - 1][source_name],
                    )
                ]
                if not remaining_frames:
                    continue
                source_point = source_vertices[0].Point
                points: list[Any] = []
                for frame_offset in remaining_frames:
                    placements = frame_placements[frame_offset - 1]
                    source_placement = placements[source_name]
                    target_inverse = placements[target_name].inverse()
                    points.append(
                        target_inverse.multVec(
                            source_placement.multVec(source_point)
                        )
                    )
                try:
                    classified = list(
                        self._shapes[target_name].classifyInside(
                            points,
                            1.0e-7,
                            False,
                        )
                    )
                except Exception as exc:
                    raise _error(
                        f"pairs.{first_name}__{second_name}.containment",
                        f"exact batched solid classification failed: {exc}",
                    ) from exc
                expected = len(remaining_frames)
                if len(classified) != expected or any(
                    not isinstance(item, bool) for item in classified
                ):
                    raise _error(
                        f"pairs.{first_name}__{second_name}.containment",
                        "exact batched solid classification returned malformed state",
                    )
                for frame_offset, is_inside in zip(
                    remaining_frames,
                    classified,
                    strict=True,
                ):
                    if is_inside:
                        contained.add((frame_offset, (first_name, second_name)))
            if progress_callback is not None:
                progress_callback(
                    "completed",
                    first_name,
                    second_name,
                    pair_index,
                    len(jobs),
                )
        return contained

    def evaluate(self, placements: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """Return every validated interference at one pose."""

        return self.evaluate_with_known_pairs(placements, known_pair_results={})

    def evaluate_with_known_pairs(
        self,
        placements: Mapping[str, Mapping[str, Any]],
        *,
        known_pair_results: Mapping[
            tuple[str, str], Mapping[str, Any] | None
        ],
        excluded_pairs: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset(),
        containment_pairs: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset(),
        pair_progress_callback: Callable[
            [str, str, str, int, int], None
        ]
        | None = None,
    ) -> dict[str, Any]:
        """Evaluate one pose with explicit reuse and exclusion semantics."""

        if not isinstance(placements, Mapping) or set(placements) != set(
            self._component_names
        ):
            raise _error(
                "placements",
                "must contain exactly every collision component",
            )
        bounds: dict[str, dict[str, list[float]]] = {}
        for name in self._component_names:
            value = placements[name]
            if not isinstance(value, Mapping) or set(value) != {
                "position_mm",
                "rotation_xyzw",
            }:
                raise _error(
                    f"placements.{name}",
                    "must contain exactly position_mm and rotation_xyzw",
                )
            shape = self._shapes[name]
            shape.Placement = _placement(
                {
                    "position": value["position_mm"],
                    "rotation": value["rotation_xyzw"],
                },
                path=f"placements.{name}",
            )
            bounds[name] = _bounds(shape, path=f"placements.{name}.bounds")

        collision_results: dict[tuple[str, str], dict[str, Any]] = {}
        candidates: list[tuple[str, str]] = []
        for first_name, second_name in self._pairs:
            pair = (first_name, second_name)
            if pair in excluded_pairs:
                continue
            if pair in known_pair_results:
                known = known_pair_results[pair]
                if known is not None:
                    collision_results[pair] = dict(known)
                continue
            broad_phase = _aabb_evidence(bounds[first_name], bounds[second_name])
            if not bool(broad_phase["overlaps_or_touches"]):
                continue
            candidates.append(pair)

        candidate_count = len(candidates)
        exact_common_count = 0
        surface_jobs: list[tuple[int, str, str]] = []
        containment_collision_count = 0
        for candidate_index, (first_name, second_name) in enumerate(
            candidates,
            start=1,
        ):
            if pair_progress_callback is not None:
                pair_progress_callback(
                    "started",
                    first_name,
                    second_name,
                    candidate_index,
                    candidate_count,
                )
            if (first_name, second_name) in containment_pairs:
                containment_collision_count += 1
                collision_results[(first_name, second_name)] = {
                    "first_component": first_name,
                    "second_component": second_name,
                    # Strict interior containment proves positive common volume,
                    # but deliberately avoids the potentially unbounded full
                    # common-volume boolean. Keep numeric compatibility while
                    # stating clearly that the volume was not measured.
                    "interference_volume_mm3": 0.0,
                    "common_solid_count": 0,
                    "detection": "exact_strict_containment_witness",
                    "interference_volume_measured": False,
                }
                if pair_progress_callback is not None:
                    pair_progress_callback(
                        "completed",
                        first_name,
                        second_name,
                        candidate_index,
                        candidate_count,
                )
                continue
            surface_jobs.append((candidate_index, first_name, second_name))

        def evaluate_surface_job(
            job: tuple[int, str, str],
        ) -> tuple[int, str, str, dict[str, Any]]:
            candidate_index, first_name, second_name = job
            return (
                candidate_index,
                first_name,
                second_name,
                _surface_collision_evidence(
                    self._shapes[first_name],
                    self._shapes[second_name],
                    path=(
                        f"pairs.{first_name}__{second_name}.surface_proximity"
                    ),
                ),
            )

        surface_results = []
        maximum_workers = max(1, min(4, os.cpu_count() or 1))
        for batch in _disjoint_surface_job_batches(surface_jobs):
            worker_count = min(len(batch), maximum_workers)
            if worker_count > 1:
                with ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="vibecad-collision",
                ) as executor:
                    surface_results.extend(
                        executor.map(evaluate_surface_job, batch)
                    )
            else:
                surface_results.extend(
                    evaluate_surface_job(job) for job in batch
                )
        surface_results.sort(key=lambda item: item[0])

        for candidate_index, first_name, second_name, proximity in surface_results:
            if pair_progress_callback is not None:
                pair_progress_callback(
                    "completed",
                    first_name,
                    second_name,
                    candidate_index,
                    candidate_count,
                )
            if not bool(proximity["intersects"]):
                continue
            collision_results[(first_name, second_name)] = {
                "first_component": first_name,
                "second_component": second_name,
                "interference_volume_mm3": 0.0,
                "common_solid_count": 0,
                "detection": "deterministic_collision_mesh_intersection",
                "interference_volume_measured": False,
                "overlapping_face_count_first": int(
                    proximity["overlapping_face_count_first"]
                ),
                "overlapping_face_count_second": int(
                    proximity["overlapping_face_count_second"]
                ),
            }
        collisions = [
            collision_results[pair]
            for pair in self._pairs
            if pair in collision_results
        ]
        return {
            "possible_pair_count": len(self._pairs),
            "excluded_pair_count": len(excluded_pairs),
            "broad_phase_candidate_count": candidate_count,
            "exact_common_count": exact_common_count,
            "surface_proximity_count": len(surface_jobs),
            "containment_collision_count": containment_collision_count,
            "collision_count": len(collisions),
            "collisions": collisions,
        }


def evaluate_dynamic_collisions(
    components: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    *,
    definition_keys: Mapping[str, str] | None = None,
    rigid_pairs: Sequence[Sequence[str]] = (),
    excluded_pairs: Sequence[Sequence[str]] = (),
    progress_callback: Callable[[str, int, int], None] | None = None,
    pair_progress_callback: Callable[
        [str, int, int, str, str, int, int], None
    ]
    | None = None,
) -> dict[str, Any]:
    """Evaluate and compact deterministic collisions over a motion trace.

    Frame zero is the native solver's retained input state and is not playable;
    collision analysis therefore covers every solver-output frame only. OCCT's
    native triangle BVH detects intersecting boundaries while exact solid
    classification detects full containment.
    """

    evaluator = DynamicCollisionEvaluator(
        components,
        definition_keys=definition_keys,
    )
    normalized_rigid_pairs = _normalize_rigid_collision_pairs(
        evaluator.component_names,
        rigid_pairs,
        path="rigid_pairs",
    )
    normalized_excluded_pairs = _normalize_collision_pairs(
        evaluator.component_names,
        excluded_pairs,
        path="excluded_pairs",
    )
    known_rigid_results: dict[
        tuple[str, str], Mapping[str, Any] | None
    ] = {}
    if (
        not isinstance(frames, Sequence)
        or isinstance(frames, (str, bytes))
        or len(frames) < 2
    ):
        raise _error("frames", "must contain an input frame and solver output")

    solver_frames: list[Mapping[str, Any]] = []
    nominal_times: dict[int, float | None] = {}
    for expected_index, frame in enumerate(frames):
        path = f"frames[{expected_index}]"
        if not isinstance(frame, Mapping):
            raise _error(path, "must be an object")
        if frame.get("frame_index") != expected_index:
            raise _error(f"{path}.frame_index", "must match trace order")
        nominal_time = frame.get("nominal_time_s")
        if nominal_time is not None:
            nominal_time = _number(nominal_time, path=f"{path}.nominal_time_s")
        nominal_times[expected_index] = nominal_time
        if expected_index > 0:
            solver_frames.append(frame)

    containment_witnesses = evaluator.precompute_strict_containment(
        solver_frames,
        excluded_pairs=normalized_excluded_pairs,
        progress_callback=(
            None
            if pair_progress_callback is None
            else lambda event, first, second, pair_index, pair_total: (
                pair_progress_callback(
                    event,
                    0,
                    len(solver_frames),
                    first,
                    second,
                    pair_index,
                    pair_total,
                )
            )
        ),
    )
    containment_pairs_by_frame: dict[int, set[tuple[str, str]]] = {}
    for frame_index, pair in containment_witnesses:
        containment_pairs_by_frame.setdefault(frame_index, set()).add(pair)

    frame_results: list[dict[str, Any]] = []
    broad_phase_candidates = 0
    exact_common_evaluations = 0
    surface_proximity_evaluations = 0
    containment_collisions = 0
    for expected_index, frame in enumerate(solver_frames, start=1):
        if progress_callback is not None:
            progress_callback("started", expected_index, len(frames) - 1)
        evaluated = evaluator.evaluate_with_known_pairs(
            frame.get("component_placements"),
            known_pair_results=known_rigid_results,
            excluded_pairs=normalized_excluded_pairs,
            containment_pairs=containment_pairs_by_frame.get(
                expected_index,
                frozenset(),
            ),
            pair_progress_callback=(
                None
                if pair_progress_callback is None
                else lambda event, first, second, pair_index, pair_total: (
                    pair_progress_callback(
                        event,
                        expected_index,
                        len(frames) - 1,
                        first,
                        second,
                        pair_index,
                        pair_total,
                    )
                )
            ),
        )
        broad_phase_candidates += int(evaluated["broad_phase_candidate_count"])
        exact_common_evaluations += int(evaluated["exact_common_count"])
        surface_proximity_evaluations += int(
            evaluated["surface_proximity_count"]
        )
        containment_collisions += int(evaluated["containment_collision_count"])
        collisions = list(evaluated["collisions"])
        if expected_index == 1:
            collisions_by_pair = {
                (
                    str(item["first_component"]),
                    str(item["second_component"]),
                ): item
                for item in collisions
            }
            known_rigid_results = {
                pair: collisions_by_pair.get(pair)
                for pair in normalized_rigid_pairs
            }
        frame_results.append(
            {
                "frame_index": expected_index,
                "nominal_time_s": nominal_times[expected_index],
                "collisions": collisions,
            }
        )
        if progress_callback is not None:
            progress_callback("completed", expected_index, len(frames) - 1)

    return {
        "summary": summarize_dynamic_collision_frames(
            evaluator.component_names,
            frame_results,
        ),
        "frames": frame_results,
        "evaluation": {
            "rigid_pair_count": len(normalized_rigid_pairs),
            "excluded_pair_count": len(normalized_excluded_pairs),
            "broad_phase_candidate_count": broad_phase_candidates,
            "exact_common_count": exact_common_evaluations,
            "surface_proximity_count": surface_proximity_evaluations,
            "containment_collision_count": containment_collisions,
            "collision_mesh_linear_deflection_mm": (
                _COLLISION_MESH_LINEAR_DEFLECTION_MM
            ),
            **evaluator.collision_mesh_statistics,
        },
    }


def _normalize_rigid_collision_pairs(
    component_names: Sequence[str],
    pairs: Sequence[Sequence[str]],
    *,
    path: str = "rigid_pairs",
) -> set[tuple[str, str]]:
    """Validate component pairs whose relative pose is fixed by the joint graph."""

    return _normalize_collision_pairs(component_names, pairs, path=path)


def _normalize_collision_pairs(
    component_names: Sequence[str],
    pairs: Sequence[Sequence[str]],
    *,
    path: str,
) -> set[tuple[str, str]]:
    """Validate and canonicalize explicit component-pair semantics."""

    if not isinstance(pairs, Sequence) or isinstance(pairs, (str, bytes)):
        raise _error(path, "must be a sequence of component pairs")
    order = {name: index for index, name in enumerate(component_names)}
    normalized: set[tuple[str, str]] = set()
    for index, pair in enumerate(pairs):
        if (
            not isinstance(pair, Sequence)
            or isinstance(pair, (str, bytes))
            or len(pair) != 2
            or pair[0] not in order
            or pair[1] not in order
            or pair[0] == pair[1]
        ):
            raise _error(
                f"{path}[{index}]",
                "must contain two different collision component names",
            )
        first, second = str(pair[0]), str(pair[1])
        if order[first] > order[second]:
            first, second = second, first
        normalized.add((first, second))
    return normalized


def summarize_dynamic_collision_frames(
    component_names: Sequence[str],
    frames: Sequence[Mapping[str, Any]],
    *,
    evaluation_warnings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Validate per-frame collision evidence and derive its compact summary.

    This contains no geometry operations.  It lets the host authenticate the
    trusted external worker's complete result without repeating expensive OCCT
    intersections on the GUI document thread.
    """

    if (
        not isinstance(component_names, Sequence)
        or isinstance(component_names, (str, bytes))
        or not 1 <= len(component_names) <= _MAX_COMPONENTS
    ):
        raise _error(
            "component_names",
            f"must contain 1-{_MAX_COMPONENTS} stable identifiers",
        )
    names: list[str] = []
    for index, name in enumerate(component_names):
        if not isinstance(name, str) or not _COMPONENT_ID.fullmatch(name):
            raise _error(
                f"component_names[{index}]",
                "must be a stable identifier",
            )
        if name in names:
            raise _error("component_names", f"contains duplicate {name!r}")
        names.append(name)
    if (
        not isinstance(frames, Sequence)
        or isinstance(frames, (str, bytes))
        or not frames
    ):
        raise _error("frames", "must contain at least one solver-output frame")
    if (
        not isinstance(evaluation_warnings, Sequence)
        or isinstance(evaluation_warnings, (str, bytes))
        or len(evaluation_warnings) > 64
    ):
        raise _error("evaluation_warnings", "must contain at most 64 warnings")
    warnings: list[dict[str, str]] = []
    for index, warning in enumerate(evaluation_warnings):
        path = f"evaluation_warnings[{index}]"
        if not isinstance(warning, Mapping) or set(warning) != {
            "code",
            "stage",
            "message",
        }:
            raise _error(path, "must contain code, stage, and message")
        normalized_warning = {
            "code": str(warning["code"]),
            "stage": str(warning["stage"]),
            "message": str(warning["message"]),
        }
        if any(
            not value or len(value) > (64 if name != "message" else 2048)
            for name, value in normalized_warning.items()
        ):
            raise _error(path, "contains an empty or oversized value")
        warnings.append(normalized_warning)

    component_order = {name: index for index, name in enumerate(names)}
    pair_states: dict[tuple[str, str], dict[str, Any]] = {}
    active_intervals: dict[tuple[str, str], dict[str, Any]] = {}
    colliding_frame_count = 0

    for offset, frame in enumerate(frames):
        frame_index = offset + 1
        path = f"frames[{offset}]"
        if not isinstance(frame, Mapping) or set(frame) != {
            "frame_index",
            "nominal_time_s",
            "collisions",
        }:
            raise _error(
                path,
                "must contain frame_index, nominal_time_s, and collisions",
            )
        if frame.get("frame_index") != frame_index:
            raise _error(f"{path}.frame_index", "must match solver-output order")
        nominal_time = frame.get("nominal_time_s")
        if nominal_time is not None:
            nominal_time = _number(nominal_time, path=f"{path}.nominal_time_s")
        collisions = frame.get("collisions")
        if not isinstance(collisions, list):
            raise _error(f"{path}.collisions", "must be a list")
        observed_keys: set[tuple[str, str]] = set()
        for collision_index, collision in enumerate(collisions):
            collision_path = f"{path}.collisions[{collision_index}]"
            required_collision_fields = {
                "first_component",
                "second_component",
                "interference_volume_mm3",
                "common_solid_count",
            }
            optional_collision_fields = {
                "detection",
                "interference_volume_measured",
                "overlapping_face_count_first",
                "overlapping_face_count_second",
            }
            if (
                not isinstance(collision, Mapping)
                or not required_collision_fields <= set(collision)
                or set(collision)
                - required_collision_fields
                - optional_collision_fields
            ):
                raise _error(collision_path, "has malformed collision evidence")
            first = collision.get("first_component")
            second = collision.get("second_component")
            if (
                first not in component_order
                or second not in component_order
                or component_order[first] >= component_order[second]
            ):
                raise _error(
                    collision_path,
                    "must name one ordered pair from component_names",
                )
            key = (str(first), str(second))
            if key in observed_keys:
                raise _error(
                    collision_path,
                    "duplicates a component pair in this frame",
                )
            observed_keys.add(key)
            volume = _number(
                collision.get("interference_volume_mm3"),
                path=f"{collision_path}.interference_volume_mm3",
            )
            volume_measured = collision.get("interference_volume_measured", True)
            if not isinstance(volume_measured, bool):
                raise _error(
                    f"{collision_path}.interference_volume_measured",
                    "must be a boolean",
                )
            detection = str(collision.get("detection") or "exact_common_volume")
            if volume_measured and (volume <= 0.0 or detection != "exact_common_volume"):
                raise _error(
                    f"{collision_path}.interference_volume_mm3",
                    "must be positive exact common volume when measured",
                )
            if not volume_measured and (
                volume != 0.0
                or detection
                not in {
                    "exact_strict_containment_witness",
                    "deterministic_collision_mesh_intersection",
                }
            ):
                raise _error(
                    collision_path,
                    "unmeasured volume requires a validated collision witness",
                )
            solid_count = collision.get("common_solid_count")
            if (
                isinstance(solid_count, bool)
                or not isinstance(solid_count, int)
                or (volume_measured and solid_count < 1)
                or (not volume_measured and solid_count != 0)
            ):
                raise _error(
                    f"{collision_path}.common_solid_count",
                    "does not match the collision measurement state",
                )
            overlap_counts = (
                collision.get("overlapping_face_count_first"),
                collision.get("overlapping_face_count_second"),
            )
            if detection == "deterministic_collision_mesh_intersection":
                if any(
                    isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 1
                    for count in overlap_counts
                ):
                    raise _error(
                        collision_path,
                        "collision-mesh evidence requires positive overlap face counts",
                    )
            elif any(count is not None for count in overlap_counts):
                raise _error(
                    collision_path,
                    "overlap face counts apply only to collision-mesh evidence",
                )

            state = pair_states.get(key)
            if state is None:
                state = {
                    "first_component": key[0],
                    "second_component": key[1],
                    "collision_frame_count": 0,
                    "first_collision_frame": frame_index,
                    "first_collision_time_s": nominal_time,
                    "last_collision_frame": frame_index,
                    "last_collision_time_s": nominal_time,
                    "maximum_interference_volume_mm3": volume,
                    "worst_frame": frame_index,
                    "worst_time_s": nominal_time,
                    "interference_volume_complete": volume_measured,
                    "intervals": [],
                }
                pair_states[key] = state
            state["collision_frame_count"] += 1
            state["last_collision_frame"] = frame_index
            state["last_collision_time_s"] = nominal_time
            state["interference_volume_complete"] = bool(
                state["interference_volume_complete"]
            ) and volume_measured
            if volume > float(state["maximum_interference_volume_mm3"]):
                state["maximum_interference_volume_mm3"] = volume
                state["worst_frame"] = frame_index
                state["worst_time_s"] = nominal_time

            interval = active_intervals.get(key)
            if interval is None:
                interval = {
                    "first_frame": frame_index,
                    "first_time_s": nominal_time,
                    "last_frame": frame_index,
                    "last_time_s": nominal_time,
                    "maximum_interference_volume_mm3": volume,
                    "worst_frame": frame_index,
                    "worst_time_s": nominal_time,
                    "interference_volume_complete": volume_measured,
                }
                active_intervals[key] = interval
            else:
                interval["last_frame"] = frame_index
                interval["last_time_s"] = nominal_time
                interval["interference_volume_complete"] = bool(
                    interval["interference_volume_complete"]
                ) and volume_measured
                if volume > float(interval["maximum_interference_volume_mm3"]):
                    interval["maximum_interference_volume_mm3"] = volume
                    interval["worst_frame"] = frame_index
                    interval["worst_time_s"] = nominal_time

        for key in list(active_intervals):
            if key in observed_keys:
                continue
            pair_states[key]["intervals"].append(active_intervals.pop(key))
        colliding_frame_count += int(bool(collisions))

    for key, interval in active_intervals.items():
        pair_states[key]["intervals"].append(interval)

    pairs = list(pair_states.values())
    first_collision = min(
        pairs,
        key=lambda item: (
            int(item["first_collision_frame"]),
            str(item["first_component"]),
            str(item["second_component"]),
        ),
        default=None,
    )
    worst_collision = max(
        pairs,
        key=lambda item: (
            float(item["maximum_interference_volume_mm3"]),
            -int(item["worst_frame"]),
        ),
        default=None,
    )
    first_event = (
        None
        if first_collision is None
        else {
            "first_component": first_collision["first_component"],
            "second_component": first_collision["second_component"],
            "frame_index": first_collision["first_collision_frame"],
            "time_s": first_collision["first_collision_time_s"],
        }
    )
    worst_event = (
        None
        if worst_collision is None
        else {
            "first_component": worst_collision["first_component"],
            "second_component": worst_collision["second_component"],
            "frame_index": worst_collision["worst_frame"],
            "time_s": worst_collision["worst_time_s"],
            "maximum_interference_volume_mm3": worst_collision[
                "maximum_interference_volume_mm3"
            ],
            "interference_volume_complete": worst_collision[
                "interference_volume_complete"
            ],
        }
    )
    interference_volume_complete = all(
        bool(item["interference_volume_complete"]) for item in pairs
    )
    summary = {
        "schema": DYNAMIC_COLLISION_TRACE_SCHEMA,
        "status": "complete" if not warnings else "incomplete",
        "analysis_complete": not warnings,
        "geometry_authority": (
            "brep_derived_occt_collision_mesh_with_exact_containment"
        ),
        "collision_definition": (
            "intersecting_collision_mesh_boundaries_or_strict_brep_containment"
        ),
        "collision_mesh_linear_deflection_mm": (
            _COLLISION_MESH_LINEAR_DEFLECTION_MM
        ),
        "collision_mesh_angular_deflection_radians": (
            _COLLISION_MESH_ANGULAR_DEFLECTION_RADIANS
        ),
        "component_count": len(names),
        "possible_pair_count": (len(names) * (len(names) - 1)) // 2,
        "evaluated_frame_count": len(frames),
        # True means the entire requested trace was evaluated and no collision
        # was found. An interrupted geometry check is deliberately not reported
        # as collision-free merely because its partial evidence is empty.
        "collision_free": not pairs and not warnings,
        "colliding_frame_count": colliding_frame_count,
        "colliding_pair_count": len(pairs),
        "interference_volume_complete": interference_volume_complete,
        "first_collision": first_event,
        "worst_collision": worst_event,
        "pairs": pairs,
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    return summary


def _error(path: str, message: str) -> MechanismGeometryError:
    return MechanismGeometryError(f"{path}: {message}")


def _number(value: Any, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(path, "must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise _error(path, "must be a finite number")
    return result


def _vector(value: Any, *, path: str, size: int) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != size
    ):
        raise _error(path, f"must contain exactly {size} numbers")
    return [
        _number(item, path=f"{path}[{index}]")
        for index, item in enumerate(value)
    ]


def _placement(value: Any, *, path: str) -> Any:
    if not isinstance(value, Mapping) or set(value) != {
        "position",
        "rotation",
    }:
        raise _error(
            path,
            "must contain exactly position and quaternion rotation",
        )
    position = _vector(value["position"], path=f"{path}.position", size=3)
    rotation = _vector(value["rotation"], path=f"{path}.rotation", size=4)
    magnitude = math.sqrt(sum(item * item for item in rotation))
    if magnitude <= 1.0e-12:
        raise _error(f"{path}.rotation", "must be a non-zero quaternion")
    rotation = [item / magnitude for item in rotation]

    import FreeCAD as App

    native = App.Placement()
    native.Base = App.Vector(*position)
    native.Rotation = App.Rotation(*rotation)
    return native


def _placed_shape(
    value: Any,
    *,
    path: str,
    require_solid: bool = True,
) -> Any:
    if not isinstance(value, Mapping) or set(value) != {"shape", "placement"}:
        raise _error(path, "must contain exactly shape and placement")
    shape = value["shape"]
    try:
        if (
            shape is None
            or bool(shape.isNull())
            or not bool(shape.isValid())
            or (
                require_solid
                and len(list(getattr(shape, "Solids", []) or [])) < 1
            )
        ):
            expectation = (
                "at least one valid solid"
                if require_solid
                else "valid topology"
            )
            raise _error(path, f"shape must contain {expectation}")
        result = shape.copy()
        # An App::Link occurrence applies its Placement to the linked topology;
        # it does not multiply the source object's stored Shape placement.
        result.Placement = _placement(
            value["placement"],
            path=f"{path}.placement",
        )
    except MechanismGeometryError:
        raise
    except Exception as exc:
        raise _error(path, f"could not detach and place the shape: {exc}") from exc
    if bool(result.isNull()) or not bool(result.isValid()):
        raise _error(path, "placed shape is null or invalid")
    return result


def _bounds(shape: Any, *, path: str) -> dict[str, list[float]]:
    box = shape.BoundBox
    return {
        "minimum_mm": [
            _number(box.XMin, path=f"{path}.minimum_mm[0]"),
            _number(box.YMin, path=f"{path}.minimum_mm[1]"),
            _number(box.ZMin, path=f"{path}.minimum_mm[2]"),
        ],
        "maximum_mm": [
            _number(box.XMax, path=f"{path}.maximum_mm[0]"),
            _number(box.YMax, path=f"{path}.maximum_mm[1]"),
            _number(box.ZMax, path=f"{path}.maximum_mm[2]"),
        ],
    }


def _transformed_bounds(
    local_bounds: Mapping[str, Sequence[float]],
    placement: Any,
) -> dict[str, list[float]]:
    """Return a conservative world AABB for one placed local AABB."""

    import FreeCAD as App

    minimum = local_bounds["minimum_mm"]
    maximum = local_bounds["maximum_mm"]
    corners = [
        placement.multVec(App.Vector(x, y, z))
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]
    return {
        "minimum_mm": [
            min(float(point.x) for point in corners),
            min(float(point.y) for point in corners),
            min(float(point.z) for point in corners),
        ],
        "maximum_mm": [
            max(float(point.x) for point in corners),
            max(float(point.y) for point in corners),
            max(float(point.z) for point in corners),
        ],
    }


def _aabb_evidence(
    first: Mapping[str, Sequence[float]],
    second: Mapping[str, Sequence[float]],
) -> dict[str, Any]:
    first_minimum = list(first["minimum_mm"])
    first_maximum = list(first["maximum_mm"])
    second_minimum = list(second["minimum_mm"])
    second_maximum = list(second["maximum_mm"])
    gaps = [
        max(
            float(first_minimum[index]) - float(second_maximum[index]),
            float(second_minimum[index]) - float(first_maximum[index]),
            0.0,
        )
        for index in range(3)
    ]
    return {
        "overlaps_or_touches": all(gap == 0.0 for gap in gaps),
        "axis_gap_mm": gaps,
        "distance_mm": math.sqrt(sum(gap * gap for gap in gaps)),
    }


def _aabb_contains(
    outer: Mapping[str, Sequence[float]],
    inner: Mapping[str, Sequence[float]],
) -> bool:
    """Return a necessary condition for complete solid containment."""

    tolerance = 1.0e-7
    outer_minimum = outer["minimum_mm"]
    outer_maximum = outer["maximum_mm"]
    inner_minimum = inner["minimum_mm"]
    inner_maximum = inner["maximum_mm"]
    return all(
        float(outer_minimum[index])
        <= float(inner_minimum[index]) + tolerance
        and float(outer_maximum[index])
        >= float(inner_maximum[index]) - tolerance
        for index in range(3)
    )


def _point(value: Any, *, path: str) -> list[float]:
    try:
        return [
            _number(value.x, path=f"{path}[0]"),
            _number(value.y, path=f"{path}[1]"),
            _number(value.z, path=f"{path}[2]"),
        ]
    except AttributeError as exc:
        raise _error(path, "must be an OCCT witness point") from exc


def _prepared_components(
    components: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, list[float]]]]:
    if (
        not isinstance(components, Mapping)
        or not 1 <= len(components) <= _MAX_COMPONENTS
    ):
        raise _error(
            "components",
            f"must contain 1-{_MAX_COMPONENTS} named solid shapes",
        )
    placed: dict[str, Any] = {}
    bounds: dict[str, dict[str, list[float]]] = {}
    for raw_name, value in components.items():
        if not isinstance(raw_name, str) or not _COMPONENT_ID.fullmatch(raw_name):
            raise _error(
                "components",
                "component names must be stable identifiers",
            )
        shape = _placed_shape(value, path=f"components.{raw_name}")
        placed[raw_name] = shape
        bounds[raw_name] = _bounds(
            shape,
            path=f"components.{raw_name}.bounds",
        )
    return placed, bounds


def _component_pairs(
    pairs: Sequence[Sequence[str]],
    *,
    component_names: set[str],
) -> list[tuple[str, str]]:
    if (
        not isinstance(pairs, Sequence)
        or isinstance(pairs, (str, bytes))
        or not 1 <= len(pairs) <= _MAX_PAIRS
    ):
        raise _error(
            "pairs",
            f"must contain 1-{_MAX_PAIRS} explicit component pairs",
        )
    clean_pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, value in enumerate(pairs):
        path = f"pairs[{index}]"
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or len(value) != 2
        ):
            raise _error(path, "must contain exactly two component names")
        first_name, second_name = value
        if (
            not isinstance(first_name, str)
            or not isinstance(second_name, str)
            or not _COMPONENT_ID.fullmatch(first_name)
            or not _COMPONENT_ID.fullmatch(second_name)
        ):
            raise _error(path, "component names must be stable identifiers")
        if first_name not in component_names or second_name not in component_names:
            raise _error(path, "names an unknown component")
        if first_name == second_name:
            raise _error(path, "cannot compare a component with itself")
        key = tuple(sorted((first_name, second_name)))
        if key in seen_pairs:
            raise _error(path, "duplicates an unordered component pair")
        seen_pairs.add(key)
        clean_pairs.append((first_name, second_name))
    return clean_pairs


def _distance_evidence(
    first_shape: Any,
    second_shape: Any,
    *,
    path: str,
) -> tuple[float, list[Any], list[dict[str, list[float]]]]:
    try:
        distance, raw_witnesses, _supports = first_shape.distToShape(second_shape)
    except Exception as exc:
        raise _error(path, f"exact OCCT distance evaluation failed: {exc}") from exc
    clean_distance = _number(distance, path=f"{path}.minimum_distance_mm")
    if clean_distance < 0.0:
        raise _error(f"{path}.minimum_distance_mm", "must not be negative")
    try:
        raw_witness_list = list(raw_witnesses or [])
        witnesses = []
        for witness_index, pair in enumerate(
            raw_witness_list[:_MAX_WITNESSES_PER_PAIR]
        ):
            if (
                not isinstance(pair, Sequence)
                or isinstance(pair, (str, bytes))
                or len(pair) != 2
            ):
                raise _error(
                    f"{path}.witnesses[{witness_index}]",
                    "must contain two OCCT points",
                )
            witnesses.append(
                {
                    "first_point_mm": _point(
                        pair[0],
                        path=(
                            f"{path}.witnesses[{witness_index}].first_point_mm"
                        ),
                    ),
                    "second_point_mm": _point(
                        pair[1],
                        path=(
                            f"{path}.witnesses[{witness_index}].second_point_mm"
                        ),
                    ),
                }
            )
    except MechanismGeometryError:
        raise
    except Exception as exc:
        raise _error(
            f"{path}.witnesses",
            f"could not normalize exact OCCT witnesses: {exc}",
        ) from exc
    return clean_distance, raw_witness_list, witnesses


def _common_evidence(
    first_shape: Any,
    second_shape: Any,
    *,
    path: str,
    prevalidated: bool = False,
) -> dict[str, Any]:
    try:
        common = first_shape.common(
            second_shape,
            0.0,
            not prevalidated,
        )
        if not bool(common.isNull()) and not bool(common.isValid()):
            raise RuntimeError("OCCT returned an invalid common shape")
        shape_type = "" if bool(common.isNull()) else str(common.ShapeType)
        volume = _number(common.Volume, path=f"{path}.volume_mm3")
        area = _number(common.Area, path=f"{path}.area_mm2")
        length = _number(common.Length, path=f"{path}.length_mm")
        if volume < 0.0 or area < 0.0 or length < 0.0:
            raise RuntimeError("OCCT returned a negative common measure")
        return {
            "shape_type": shape_type,
            "volume_mm3": volume,
            "area_mm2": area,
            "length_mm": length,
            "solid_count": len(list(common.Solids)),
            "face_count": len(list(common.Faces)),
            "edge_count": len(list(common.Edges)),
            "vertex_count": len(list(common.Vertexes)),
        }
    except MechanismGeometryError:
        raise
    except Exception as exc:
        raise _error(path, f"exact OCCT common evaluation failed: {exc}") from exc


def _surface_collision_evidence(
    first_shape: Any,
    second_shape: Any,
    *,
    path: str,
) -> dict[str, Any]:
    """Detect intersecting collision-mesh faces with OCCT's native BVH.

    The meshes are deterministic tessellations of the authoritative B-reps,
    built once by ``DynamicCollisionEvaluator``. This is the native OCCT path
    intended for repeated proximity tests; unlike a solid boolean, it remains
    bounded across a mechanism trace containing detailed imported geometry.
    """

    try:
        result = first_shape.proximity(second_shape, 0.0)
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], list)
            or not isinstance(result[1], list)
        ):
            raise RuntimeError("OCCT returned malformed overlap indices")
        first_faces = [int(index) for index in result[0]]
        second_faces = [int(index) for index in result[1]]
        if bool(first_faces) != bool(second_faces):
            raise RuntimeError("OCCT returned asymmetric overlap indices")
        return {
            "intersects": bool(first_faces),
            "overlapping_face_count_first": len(first_faces),
            "overlapping_face_count_second": len(second_faces),
        }
    except MechanismGeometryError:
        raise
    except Exception as exc:
        raise _error(
            path,
            f"deterministic OCCT collision-mesh evaluation failed: {exc}",
        ) from exc


def _body_pair_evidence(
    first_name: str,
    second_name: str,
    *,
    placed: Mapping[str, Any],
    bounds: Mapping[str, Mapping[str, Sequence[float]]],
    path: str,
) -> tuple[dict[str, Any], list[Any]]:
    first_shape = placed[first_name]
    second_shape = placed[second_name]
    broad_phase = _aabb_evidence(bounds[first_name], bounds[second_name])
    distance, raw_witnesses, witnesses = _distance_evidence(
        first_shape,
        second_shape,
        path=path,
    )
    common_evaluated = bool(broad_phase["overlaps_or_touches"])
    common = {
        "shape_type": "",
        "volume_mm3": 0.0,
        "area_mm2": 0.0,
        "length_mm": 0.0,
        "solid_count": 0,
        "face_count": 0,
        "edge_count": 0,
        "vertex_count": 0,
    }
    if common_evaluated:
        common = _common_evidence(
            first_shape,
            second_shape,
            path=f"{path}.common",
        )
    return (
        {
            "first_component": first_name,
            "second_component": second_name,
            "first_bounds": bounds[first_name],
            "second_bounds": bounds[second_name],
            "broad_phase": broad_phase,
            "minimum_distance_mm": distance,
            "witness_count": len(raw_witnesses),
            "witnesses_truncated": (
                len(raw_witnesses) > _MAX_WITNESSES_PER_PAIR
            ),
            "witnesses": witnesses,
            "common_evaluated": common_evaluated,
            "common_shape_type": common["shape_type"],
            "common_volume_mm3": common["volume_mm3"],
            "common_solid_count": common["solid_count"],
            "common_face_count": common["face_count"],
        },
        raw_witnesses,
    )


def _subelement(shape: Any, name: str, *, path: str) -> Any:
    match = _SUBELEMENT.fullmatch(name)
    if match is None:
        raise _error(path, "must name one FaceN, EdgeN, or VertexN")
    collection_name = {
        "Face": "Faces",
        "Edge": "Edges",
        "Vertex": "Vertexes",
    }[match.group(1)]
    values = list(getattr(shape, collection_name, []) or [])
    index = int(match.group(2)) - 1
    if not 0 <= index < len(values):
        raise _error(
            path,
            f"{name} is outside the source topology ({len(values)} {collection_name})",
        )
    return values[index].copy()


def _prepared_interfaces(
    components: Mapping[str, Mapping[str, Any]],
    interfaces: Mapping[str, Mapping[str, Sequence[str]]],
    requested: set[tuple[str, str]],
) -> dict[tuple[str, str], Any]:
    if not isinstance(interfaces, Mapping):
        raise _error("interfaces", "must be an object keyed by component")
    if any(name not in components for name in interfaces):
        raise _error("interfaces", "contains an unknown component")
    result: dict[tuple[str, str], Any] = {}
    for component_name, interface_name in sorted(requested):
        component_interfaces = interfaces.get(component_name)
        path = f"interfaces.{component_name}.{interface_name}"
        if not isinstance(component_interfaces, Mapping):
            raise _error(path, "is not published by the component")
        if not _INTERFACE_NAME.fullmatch(interface_name):
            raise _error(path, "must be a stable semantic interface name")
        raw_subelements = component_interfaces.get(interface_name)
        if (
            not isinstance(raw_subelements, Sequence)
            or isinstance(raw_subelements, (str, bytes))
            or not 1 <= len(raw_subelements) <= 64
        ):
            raise _error(
                path,
                "must resolve to 1-64 exact contact subelements",
            )
        subelements = [str(item) for item in raw_subelements]
        if len(subelements) != len(set(subelements)):
            raise _error(path, "contains duplicate contact subelements")
        component = components[component_name]
        source_shape = component.get("shape")
        if source_shape is None:
            raise _error(path, "component has no source shape")
        selected = [
            _subelement(
                source_shape,
                subelement,
                path=f"{path}[{index}]",
            )
            for index, subelement in enumerate(subelements)
        ]
        if len(selected) == 1:
            interface_shape = selected[0]
        else:
            try:
                import Part

                interface_shape = Part.makeCompound(selected)
            except Exception as exc:
                raise _error(
                    path,
                    f"could not build the contact-interface compound: {exc}",
                ) from exc
        result[(component_name, interface_name)] = _placed_shape(
            {
                "shape": interface_shape,
                "placement": component.get("placement"),
            },
            path=path,
            require_solid=False,
        )
    return result


def _point_to_shape_distance(point: Any, shape: Any, *, path: str) -> float:
    try:
        import Part

        distance, _witnesses, _supports = Part.Vertex(point).distToShape(shape)
    except Exception as exc:
        raise _error(path, f"could not measure witness-to-interface distance: {exc}") from exc
    result = _number(distance, path=path)
    if result < 0.0:
        raise _error(path, "must not be negative")
    return result


def _section_coverage(
    section: Any,
    interface: Any,
    *,
    tolerance_mm: float,
    path: str,
) -> dict[str, Any]:
    edge_length = _number(section.Length, path=f"{path}.edge_length_mm")
    face_area = _number(section.Area, path=f"{path}.face_area_mm2")
    common = _common_evidence(
        section,
        interface,
        path=f"{path}.common",
    )
    covered_edge_length = min(edge_length, float(common["length_mm"]))
    covered_face_area = min(face_area, float(common["area_mm2"]))
    area_tolerance = tolerance_mm * tolerance_mm
    uncovered_edges = int(covered_edge_length + tolerance_mm < edge_length)
    uncovered_faces = int(covered_face_area + area_tolerance < face_area)

    vertex_distances = [
        _point_to_shape_distance(
            vertex.Point,
            interface,
            path=f"{path}.vertices[{index}].distance_mm",
        )
        for index, vertex in enumerate(list(section.Vertexes))
    ]
    uncovered_vertices = sum(
        distance > tolerance_mm for distance in vertex_distances
    )
    return {
        "edge_length_mm": edge_length,
        "covered_edge_length_mm": covered_edge_length,
        "uncovered_edge_count": uncovered_edges,
        "face_area_mm2": face_area,
        "covered_face_area_mm2": covered_face_area,
        "uncovered_face_count": uncovered_faces,
        "vertex_count": len(vertex_distances),
        "uncovered_vertex_count": uncovered_vertices,
        "maximum_vertex_distance_mm": max(vertex_distances, default=0.0),
        "complete": (
            uncovered_edges == 0
            and uncovered_faces == 0
            and uncovered_vertices == 0
        ),
    }


def _interface_evidence(
    first_shape: Any,
    second_shape: Any,
    first_interface: Any,
    second_interface: Any,
    raw_body_witnesses: Sequence[Any],
    *,
    first_name: str,
    second_name: str,
    first_interface_name: str,
    second_interface_name: str,
    tolerance_mm: float,
    body_distance_mm: float,
    path: str,
) -> dict[str, Any]:
    interface_distance, raw_interface_witnesses, interface_witnesses = (
        _distance_evidence(
            first_interface,
            second_interface,
            path=f"{path}.interface_distance",
        )
    )
    interface_common = _common_evidence(
        first_interface,
        second_interface,
        path=f"{path}.interface_common",
    )

    body_witnesses_complete = len(raw_body_witnesses) <= _MAX_CONTACT_WITNESSES
    body_witness_distances: list[dict[str, float]] = []
    if body_witnesses_complete:
        for index, witness in enumerate(raw_body_witnesses):
            if (
                not isinstance(witness, Sequence)
                or isinstance(witness, (str, bytes))
                or len(witness) != 2
            ):
                raise _error(
                    f"{path}.body_witnesses[{index}]",
                    "must contain two OCCT points",
                )
            body_witness_distances.append(
                {
                    "first_interface_distance_mm": _point_to_shape_distance(
                        witness[0],
                        first_interface,
                        path=(
                            f"{path}.body_witnesses[{index}]."
                            "first_interface_distance_mm"
                        ),
                    ),
                    "second_interface_distance_mm": _point_to_shape_distance(
                        witness[1],
                        second_interface,
                        path=(
                            f"{path}.body_witnesses[{index}]."
                            "second_interface_distance_mm"
                        ),
                    ),
                }
            )
    witnesses_on_interfaces = (
        None
        if not body_witnesses_complete or not body_witness_distances
        else all(
            item["first_interface_distance_mm"] <= tolerance_mm
            and item["second_interface_distance_mm"] <= tolerance_mm
            for item in body_witness_distances
        )
    )

    try:
        section = first_shape.section(second_shape)
        if not bool(section.isNull()) and not bool(section.isValid()):
            raise RuntimeError("OCCT returned an invalid section shape")
    except Exception as exc:
        raise _error(path, f"exact OCCT section evaluation failed: {exc}") from exc
    section_is_null = bool(section.isNull())
    section_shape_type = "" if section_is_null else str(section.ShapeType)
    section_edge_count = 0 if section_is_null else len(list(section.Edges))
    section_face_count = 0 if section_is_null else len(list(section.Faces))
    section_vertex_count = 0 if section_is_null else len(list(section.Vertexes))
    section_has_topology = (
        section_edge_count + section_face_count + section_vertex_count > 0
    )
    first_coverage = (
        None
        if not section_has_topology
        else _section_coverage(
            section,
            first_interface,
            tolerance_mm=tolerance_mm,
            path=f"{path}.section.first_interface",
        )
    )
    second_coverage = (
        None
        if not section_has_topology
        else _section_coverage(
            section,
            second_interface,
            tolerance_mm=tolerance_mm,
            path=f"{path}.section.second_interface",
        )
    )
    section_on_interfaces = (
        None
        if first_coverage is None or second_coverage is None
        else bool(first_coverage["complete"] and second_coverage["complete"])
    )
    contact_locus_on_interfaces = (
        None
        if witnesses_on_interfaces is None
        else bool(
            witnesses_on_interfaces
            and section_on_interfaces is not False
            and interface_distance <= tolerance_mm
        )
    )
    return {
        "first_component": first_name,
        "second_component": second_name,
        "first_interface": first_interface_name,
        "second_interface": second_interface_name,
        "minimum_distance_mm": interface_distance,
        "witness_count": len(raw_interface_witnesses),
        "witnesses_truncated": (
            len(raw_interface_witnesses) > _MAX_WITNESSES_PER_PAIR
        ),
        "witnesses": interface_witnesses,
        "common": interface_common,
        "body_witness_count": len(raw_body_witnesses),
        "body_witnesses_complete": body_witnesses_complete,
        "body_witness_distances": body_witness_distances[
            :_MAX_WITNESSES_PER_PAIR
        ],
        "body_witnesses_on_interfaces": witnesses_on_interfaces,
        "section": {
            "evaluated": True,
            "shape_type": section_shape_type,
            "edge_count": section_edge_count,
            "face_count": section_face_count,
            "vertex_count": section_vertex_count,
            "has_topology": section_has_topology,
            "first_interface_coverage": first_coverage,
            "second_interface_coverage": second_coverage,
            "all_on_interfaces": section_on_interfaces,
        },
        "body_within_tolerance": body_distance_mm <= tolerance_mm,
        "interfaces_within_tolerance": interface_distance <= tolerance_mm,
        "contact_locus_on_interfaces": contact_locus_on_interfaces,
    }


def measure_static_component_pairs(
    components: Mapping[str, Mapping[str, Any]],
    pairs: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Return raw broad-phase and exact OCCT evidence for explicit pairs.

    The function does not apply a clearance tolerance, contact policy, or
    pass/fail interpretation. Source shapes are copied before placements are
    applied and are never mutated.
    """

    placed, bounds = _prepared_components(components)
    clean_pairs = _component_pairs(pairs, component_names=set(placed))
    results = []
    common_evaluated_count = 0
    for index, (first_name, second_name) in enumerate(clean_pairs):
        evidence, _raw_witnesses = _body_pair_evidence(
            first_name,
            second_name,
            placed=placed,
            bounds=bounds,
            path=f"pairs[{index}]",
        )
        common_evaluated_count += int(bool(evidence["common_evaluated"]))
        results.append(evidence)
    return {
        "schema": STATIC_PAIR_EVIDENCE_SCHEMA,
        "component_count": len(placed),
        "pair_count": len(results),
        "common_evaluated_count": common_evaluated_count,
        "broad_phase_rejected_common_count": (
            len(results) - common_evaluated_count
        ),
        "pairs": results,
    }


def measure_static_mechanism_pairs(
    components: Mapping[str, Mapping[str, Any]],
    declarations: Sequence[Mapping[str, Any]],
    *,
    interfaces: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> dict[str, Any]:
    """Measure exact static evidence for normalized mechanism declarations.

    Each declaration contains a stable ``declaration_id``, two component names,
    an explicit ``tolerance_mm``, and either both semantic interface names or
    neither. The returned evidence is raw geometry evidence; policy verdicts
    are assigned by :mod:`VibeCADMechanismEngine`.
    """

    import Part

    placed, bounds = _prepared_components(components)
    if (
        not isinstance(declarations, Sequence)
        or isinstance(declarations, (str, bytes))
        or not 1 <= len(declarations) <= 128
    ):
        raise _error("declarations", "must contain 1-128 explicit pair declarations")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    requested_interfaces: set[tuple[str, str]] = set()
    for index, value in enumerate(declarations):
        path = f"declarations[{index}]"
        if not isinstance(value, Mapping) or set(value) != {
            "declaration_id",
            "first_component",
            "second_component",
            "tolerance_mm",
            "first_interface",
            "second_interface",
        }:
            raise _error(path, "has malformed fields")
        declaration_id = value["declaration_id"]
        if (
            not isinstance(declaration_id, str)
            or not _DECLARATION_ID.fullmatch(declaration_id)
            or declaration_id in seen_ids
        ):
            raise _error(f"{path}.declaration_id", "must be one unique stable identifier")
        seen_ids.add(declaration_id)
        first_name = value["first_component"]
        second_name = value["second_component"]
        _component_pairs(
            [[first_name, second_name]],
            component_names=set(placed),
        )
        pair = tuple(sorted((str(first_name), str(second_name))))
        if pair in seen_pairs:
            raise _error(path, "duplicates an unordered component pair")
        seen_pairs.add(pair)
        tolerance = _number(value["tolerance_mm"], path=f"{path}.tolerance_mm")
        if not 0.0 < tolerance <= 1.0e3:
            raise _error(
                f"{path}.tolerance_mm",
                "must be greater than zero and at most 1000",
            )
        first_interface = value["first_interface"]
        second_interface = value["second_interface"]
        if (first_interface is None) != (second_interface is None):
            raise _error(path, "must name both semantic interfaces or neither")
        if first_interface is not None:
            for component_name, interface_name, field in (
                (str(first_name), first_interface, "first_interface"),
                (str(second_name), second_interface, "second_interface"),
            ):
                if (
                    not isinstance(interface_name, str)
                    or not _INTERFACE_NAME.fullmatch(interface_name)
                ):
                    raise _error(
                        f"{path}.{field}",
                        "must be a stable semantic interface name",
                    )
                requested_interfaces.add((component_name, interface_name))
        normalized.append(
            {
                "declaration_id": declaration_id,
                "first_component": str(first_name),
                "second_component": str(second_name),
                "tolerance_mm": tolerance,
                "first_interface": first_interface,
                "second_interface": second_interface,
            }
        )
    prepared_interfaces = _prepared_interfaces(
        components,
        interfaces or {},
        requested_interfaces,
    )

    results: list[dict[str, Any]] = []
    complete_count = 0
    for index, declaration in enumerate(normalized):
        first_name = declaration["first_component"]
        second_name = declaration["second_component"]
        try:
            body, raw_witnesses = _body_pair_evidence(
                first_name,
                second_name,
                placed=placed,
                bounds=bounds,
                path=f"declarations[{index}].body",
            )
            interface_evidence = None
            first_interface_name = declaration["first_interface"]
            second_interface_name = declaration["second_interface"]
            if first_interface_name is not None:
                interface_evidence = _interface_evidence(
                    placed[first_name],
                    placed[second_name],
                    prepared_interfaces[(first_name, first_interface_name)],
                    prepared_interfaces[(second_name, second_interface_name)],
                    raw_witnesses,
                    first_name=first_name,
                    second_name=second_name,
                    first_interface_name=first_interface_name,
                    second_interface_name=second_interface_name,
                    tolerance_mm=declaration["tolerance_mm"],
                    body_distance_mm=float(body["minimum_distance_mm"]),
                    path=f"declarations[{index}].interfaces",
                )
            results.append(
                {
                    **declaration,
                    "status": "complete",
                    "error": "",
                    "body": body,
                    "interfaces": interface_evidence,
                }
            )
            complete_count += 1
        except Exception as exc:
            results.append(
                {
                    **declaration,
                    "status": "indeterminate",
                    "error": str(exc)[:512],
                    "body": None,
                    "interfaces": None,
                }
            )
    return {
        "schema": STATIC_MECHANISM_EVIDENCE_SCHEMA,
        "geometry_engine": {
            "name": "OpenCASCADE",
            "version": str(getattr(Part, "OCC_VERSION", "") or "unknown"),
        },
        "component_count": len(placed),
        "declaration_count": len(results),
        "complete_count": complete_count,
        "indeterminate_count": len(results) - complete_count,
        "declarations": results,
    }
