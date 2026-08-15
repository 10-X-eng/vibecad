# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed argument specs for Robot trajectory feature operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeRobotTrajectory import NativeRobotTrajectoryError
from VibeCADNativeRobotTrajectoryState import MAX_TRAJECTORY_SOURCES
from VibeCADNativeTargets import NativeObjectRef


MIN_EDGE_SEGMENTATION_MM = 0.1
MAX_EDGE_SEGMENTATION_MM = 10_000.0
MAX_DRESS_UP_MOTION_VALUE = 10_000.0
CONTINUITY_MODES = frozenset({"unchanged", "continuous", "discontinuous"})
PLACEMENT_MODES = frozenset(
    {
        "unchanged",
        "replace_orientation",
        "translate",
        "rotate",
        "transform",
    }
)
_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class FeatureTargetSpec:
    mode: str
    target_ref: NativeObjectRef | None
    expected_target_state_sha256: str | None


@dataclass(frozen=True, slots=True)
class PlacementSpec:
    origin_mm: tuple[float, float, float]
    rotation_axis: tuple[float, float, float]
    angle_degrees: float


@dataclass(frozen=True, slots=True)
class EdgeTrajectorySpec:
    target: FeatureTargetSpec
    source_ref: NativeObjectRef
    edges: tuple[str, ...]
    segmentation_mm: float
    use_rotation: bool
    expected_trajectory_setup_state_sha256: str
    expected_source_state_sha256: str


@dataclass(frozen=True, slots=True)
class DressUpTrajectorySpec:
    target: FeatureTargetSpec
    source_ref: NativeObjectRef
    use_speed: bool
    speed_mm_per_s: float
    use_acceleration: bool
    acceleration_mm_per_s2: float
    continuity_mode: str
    placement: PlacementSpec
    placement_mode: str
    expected_trajectory_setup_state_sha256: str
    expected_source_state_sha256: str


@dataclass(frozen=True, slots=True)
class CompoundTrajectorySourceSpec:
    trajectory_ref: NativeObjectRef
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class CompoundTrajectorySpec:
    target: FeatureTargetSpec
    sources: tuple[CompoundTrajectorySourceSpec, ...]
    expected_trajectory_setup_state_sha256: str


def _digest(value: Any, field: str) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeRobotTrajectoryError(
            f"{field} must be one lowercase SHA-256 digest."
        )
    return result


def _reference(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeRobotTrajectoryError(
            f"The exact Robot trajectory {field} reference is malformed."
        )
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _target(
    document_uid: str,
    mode: Any,
    target: Any,
    expected_digest: Any,
) -> FeatureTargetSpec:
    clean_mode = str(mode or "")
    if clean_mode not in {"create", "edit"}:
        raise NativeRobotTrajectoryError(
            "A trajectory feature mode must be create or edit."
        )
    if clean_mode == "create":
        if target is not None or expected_digest is not None:
            raise NativeRobotTrajectoryError(
                "Trajectory feature creation cannot name an edit target."
            )
        return FeatureTargetSpec(clean_mode, None, None)
    if target is None or expected_digest is None:
        raise NativeRobotTrajectoryError(
            "Trajectory feature editing requires one exact frozen target."
        )
    return FeatureTargetSpec(
        clean_mode,
        _reference(document_uid, target, "target"),
        _digest(expected_digest, "expected_target_state_sha256"),
    )


def _number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeRobotTrajectoryError(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise NativeRobotTrajectoryError(f"{field} is outside its supported range.")
    return 0.0 if result == 0.0 else result


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise NativeRobotTrajectoryError(f"{field} must be true or false.")
    return value


def _vector(value: Any, field: str, *, maximum: float) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeRobotTrajectoryError(f"{field} must be one exact XYZ vector.")
    return tuple(
        _number(value[axis], field, minimum=-maximum, maximum=maximum) for axis in "xyz"
    )


def _placement(value: Any) -> PlacementSpec:
    if not isinstance(value, Mapping) or set(value) != {"origin_mm", "rotation"}:
        raise NativeRobotTrajectoryError("Trajectory dress-up placement is malformed.")
    rotation = value["rotation"]
    if not isinstance(rotation, Mapping) or set(rotation) != {
        "axis",
        "angle_degrees",
    }:
        raise NativeRobotTrajectoryError("Trajectory dress-up rotation is malformed.")
    axis = _vector(rotation["axis"], "Dress-up rotation axis", maximum=1.0)
    magnitude = math.sqrt(sum(component * component for component in axis))
    if magnitude < 1.0e-12:
        raise NativeRobotTrajectoryError(
            "Trajectory dress-up rotation axis must be nonzero."
        )
    return PlacementSpec(
        _vector(value["origin_mm"], "Dress-up translation", maximum=1_000_000.0),
        tuple(component / magnitude for component in axis),
        _number(
            rotation["angle_degrees"],
            "Dress-up rotation angle",
            minimum=-360.0,
            maximum=360.0,
        ),
    )


def prepare_edge_trajectory_spec(
    document_uid: str,
    values: Mapping[str, Any],
) -> EdgeTrajectorySpec:
    fields = {
        "mode",
        "target",
        "source",
        "edges",
        "segmentation_mm",
        "use_rotation",
        "expected_trajectory_setup_state_sha256",
        "expected_target_state_sha256",
        "expected_source_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeRobotTrajectoryError(
            "Edge-to-trajectory operation has incorrect fields."
        )
    raw_edges = values["edges"]
    if not isinstance(raw_edges, list):
        raise NativeRobotTrajectoryError("Edge-to-trajectory requires an edge list.")
    edges = tuple(str(value) for value in raw_edges)
    if (
        not 1 <= len(edges) <= MAX_TRAJECTORY_SOURCES
        or len(edges) != len(set(edges))
        or any(_EDGE_NAME.fullmatch(edge) is None for edge in edges)
    ):
        raise NativeRobotTrajectoryError(
            "Edge-to-trajectory requires 1 through 64 unique exact EdgeN names."
        )
    return EdgeTrajectorySpec(
        _target(
            document_uid,
            values["mode"],
            values["target"],
            values["expected_target_state_sha256"],
        ),
        _reference(document_uid, values["source"], "source"),
        edges,
        _number(
            values["segmentation_mm"],
            "Edge segmentation_mm",
            minimum=MIN_EDGE_SEGMENTATION_MM,
            maximum=MAX_EDGE_SEGMENTATION_MM,
        ),
        _boolean(values["use_rotation"], "Edge orientation use"),
        _digest(
            values["expected_trajectory_setup_state_sha256"],
            "expected_trajectory_setup_state_sha256",
        ),
        _digest(
            values["expected_source_state_sha256"],
            "expected_source_state_sha256",
        ),
    )


def prepare_dress_up_trajectory_spec(
    document_uid: str,
    values: Mapping[str, Any],
) -> DressUpTrajectorySpec:
    fields = {
        "mode",
        "target",
        "source",
        "use_speed",
        "speed_mm_per_s",
        "use_acceleration",
        "acceleration_mm_per_s2",
        "continuity_mode",
        "placement",
        "placement_mode",
        "expected_trajectory_setup_state_sha256",
        "expected_target_state_sha256",
        "expected_source_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeRobotTrajectoryError(
            "Trajectory dress-up operation has incorrect fields."
        )
    continuity = str(values["continuity_mode"] or "")
    placement_mode = str(values["placement_mode"] or "")
    if continuity not in CONTINUITY_MODES:
        raise NativeRobotTrajectoryError(
            "Trajectory dress-up continuity_mode is unsupported."
        )
    if placement_mode not in PLACEMENT_MODES:
        raise NativeRobotTrajectoryError(
            "Trajectory dress-up placement_mode is unsupported."
        )
    return DressUpTrajectorySpec(
        _target(
            document_uid,
            values["mode"],
            values["target"],
            values["expected_target_state_sha256"],
        ),
        _reference(document_uid, values["source"], "source"),
        _boolean(values["use_speed"], "Dress-up speed use"),
        _number(
            values["speed_mm_per_s"],
            "Dress-up speed_mm_per_s",
            minimum=0.0,
            maximum=MAX_DRESS_UP_MOTION_VALUE,
        ),
        _boolean(values["use_acceleration"], "Dress-up acceleration use"),
        _number(
            values["acceleration_mm_per_s2"],
            "Dress-up acceleration_mm_per_s2",
            minimum=0.0,
            maximum=MAX_DRESS_UP_MOTION_VALUE,
        ),
        continuity,
        _placement(values["placement"]),
        placement_mode,
        _digest(
            values["expected_trajectory_setup_state_sha256"],
            "expected_trajectory_setup_state_sha256",
        ),
        _digest(
            values["expected_source_state_sha256"],
            "expected_source_state_sha256",
        ),
    )


def prepare_compound_trajectory_spec(
    document_uid: str,
    values: Mapping[str, Any],
) -> CompoundTrajectorySpec:
    fields = {
        "mode",
        "target",
        "sources",
        "expected_trajectory_setup_state_sha256",
        "expected_target_state_sha256",
    }
    if not isinstance(values, Mapping) or set(values) != fields:
        raise NativeRobotTrajectoryError(
            "Trajectory compound operation has incorrect fields."
        )
    raw_sources = values["sources"]
    if (
        not isinstance(raw_sources, list)
        or not 1 <= len(raw_sources) <= MAX_TRAJECTORY_SOURCES
    ):
        raise NativeRobotTrajectoryError(
            "Trajectory compound requires 1 through 64 ordered sources."
        )
    sources = []
    seen = set()
    for raw in raw_sources:
        if not isinstance(raw, Mapping) or set(raw) != {
            "trajectory",
            "expected_state_sha256",
        }:
            raise NativeRobotTrajectoryError(
                "A trajectory compound source is malformed."
            )
        reference = _reference(document_uid, raw["trajectory"], "source")
        if reference.object_name in seen:
            raise NativeRobotTrajectoryError(
                "Trajectory compound sources must be unique."
            )
        seen.add(reference.object_name)
        sources.append(
            CompoundTrajectorySourceSpec(
                reference,
                _digest(
                    raw["expected_state_sha256"],
                    "source expected_state_sha256",
                ),
            )
        )
    return CompoundTrajectorySpec(
        _target(
            document_uid,
            values["mode"],
            values["target"],
            values["expected_target_state_sha256"],
        ),
        tuple(sources),
        _digest(
            values["expected_trajectory_setup_state_sha256"],
            "expected_trajectory_setup_state_sha256",
        ),
    )
