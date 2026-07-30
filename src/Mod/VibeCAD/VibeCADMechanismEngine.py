# SPDX-License-Identifier: LGPL-2.1-or-later

"""Pure internal contracts at the shared mechanism-evaluation boundary.

Assembly authoring and future Part Design verification adapters normalize into
these versioned structures before native evaluation.  This module deliberately
does not import FreeCAD or either VibeScript worker.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any, Callable

MECHANISM_SCENARIO_SCHEMA = "vibecad-mechanism-scenario-v1"
MECHANISM_SOLVE_REPORT_SCHEMA = "vibecad-mechanism-solve-report-v1"
MECHANISM_STATIC_CHECK_SCHEMA = "vibecad-mechanism-static-check-v1"
MECHANISM_VERIFICATION_REPORT_SCHEMA = (
    "vibecad-mechanism-verification-report-v1"
)
STATIC_MECHANISM_EVIDENCE_SCHEMA = "vibecad-mechanism-static-evidence-v1"

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SUBELEMENT = re.compile(r"^(Face|Edge|Vertex)[1-9][0-9]*$")
_INTERFACE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_OCCURRENCE_PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*){0,15}$"
)
_OPTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_JOINT_TYPES = frozenset(
    {
        "fixed",
        "revolute",
        "cylindrical",
        "slider",
        "ball",
        "distance",
        "parallel",
        "perpendicular",
        "angle",
        "rack_pinion",
        "screw",
        "gears",
        "belt",
    }
)
_STATIC_REQUIREMENT_TYPES = frozenset({"collision_free", "minimum_clearance"})
_CONTACT_POLICIES = frozenset(
    {"prohibited", "clearance", "allowed", "required", "ignored"}
)
_MAX_CONTRACT_BYTES = 2 * 1024 * 1024
_MAX_CONTRACT_DEPTH = 20
_MAX_COMPONENTS = 64
_MAX_JOINTS = 64
_MAX_MOTIONS = 64
_MAX_OCCURRENCES = 4096


class MechanismContractError(ValueError):
    """An internal scenario or solve report violates the shared contract."""


def _error(path: str, message: str) -> MechanismContractError:
    return MechanismContractError(f"{path}: {message}")


def _json_value(value: Any, *, path: str, depth: int = 0) -> Any:
    if depth > _MAX_CONTRACT_DEPTH:
        raise _error(path, f"exceeds {_MAX_CONTRACT_DEPTH} nested levels")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error(path, "must be finite")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise _error(path, "object keys must be non-empty strings")
            result[key] = _json_value(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            _json_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    raise _error(path, f"contains unsupported value {type(value).__name__}")


def _mapping(
    value: Any,
    *,
    path: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(path, "must be an object")
    fields = set(value)
    if any(not isinstance(field, str) for field in fields):
        raise _error(path, "field names must be strings")
    missing = required - fields
    extra = fields - required - optional
    if missing:
        raise _error(path, f"is missing fields {sorted(missing)}")
    if extra:
        raise _error(path, f"contains unknown fields {sorted(extra)}")
    return dict(value)


def _identifier(value: Any, *, path: str) -> str:
    result = str(value or "")
    if not _IDENTIFIER.fullmatch(result):
        raise _error(path, "must be a stable identifier")
    return result


def _text(value: Any, *, path: str, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise _error(path, "must be a string")
    if len(value) > maximum:
        raise _error(path, f"must contain at most {maximum} characters")
    return value


def _boolean(value: Any, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise _error(path, "must be a boolean")
    return value


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


def _placement(value: Any, *, path: str) -> dict[str, list[float]]:
    raw = _mapping(
        value,
        path=path,
        required=frozenset({"position", "rotation"}),
    )
    rotation = _vector(raw["rotation"], path=f"{path}.rotation", size=4)
    magnitude = math.sqrt(sum(item * item for item in rotation))
    if magnitude <= 1.0e-12:
        raise _error(f"{path}.rotation", "must be a non-zero quaternion")
    return {
        "position": _vector(raw["position"], path=f"{path}.position", size=3),
        "rotation": [item / magnitude for item in rotation],
    }


def _limits(value: Any, *, path: str) -> list[float | None] | None:
    if value is None:
        return None
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 2
    ):
        raise _error(path, "must be null or contain two endpoints")
    result = [
        None if item is None else _number(item, path=f"{path}[{index}]")
        for index, item in enumerate(value)
    ]
    if result == [None, None]:
        raise _error(path, "must define at least one endpoint")
    if result[0] is not None and result[1] is not None and result[0] > result[1]:
        raise _error(path, "minimum must not exceed maximum")
    return result


def _source(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(path, "must be an object")
    kind = str(value.get("kind") or "")
    if kind == "document_object":
        raw = _mapping(
            value,
            path=path,
            required=frozenset(
                {"kind", "document_uid", "object_name"}
            ),
            optional=frozenset({"document_path"}),
        )
        document_uid = _text(
            raw["document_uid"],
            path=f"{path}.document_uid",
        ).strip()
        object_name = _text(
            raw["object_name"],
            path=f"{path}.object_name",
        ).strip()
        result = {
            "kind": kind,
            "document_uid": document_uid,
            "object_name": object_name,
        }
        if not result["document_uid"] or not result["object_name"]:
            raise _error(path, "document identity fields must be non-empty")
        if "document_path" in raw:
            document_path = _text(
                raw["document_path"],
                path=f"{path}.document_path",
                maximum=2048,
            )
            document_parts = document_path.split("/")
            if (
                not document_path
                or document_path != document_path.strip()
                or "\x00" in document_path
                or document_path.startswith("/")
                or _WINDOWS_DRIVE.match(document_path)
                or "\\" in document_path
                or any(part in {"", ".", ".."} for part in document_parts)
                or PurePosixPath(document_path).suffix.casefold() != ".fcstd"
            ):
                raise _error(
                    f"{path}.document_path",
                    "must be a portable relative .FCStd path",
                )
            result["document_path"] = document_path
        return result
    if kind == "standard_fastener":
        raw = _mapping(
            value,
            path=path,
            required=frozenset(
                {
                    "kind",
                    "standard",
                    "nominal_thread",
                    "length_mm",
                    "model_thread",
                    "left_handed",
                    "options",
                }
            ),
        )
        length = raw["length_mm"]
        clean_length = (
            None
            if length is None
            else _number(length, path=f"{path}.length_mm")
        )
        if clean_length is not None and clean_length <= 0.0:
            raise _error(f"{path}.length_mm", "must be positive")
        options = _json_value(raw["options"], path=f"{path}.options")
        if not isinstance(options, dict):
            raise _error(f"{path}.options", "must be an object")
        standard = _text(
            raw["standard"],
            path=f"{path}.standard",
        ).strip()
        nominal_thread = _text(
            raw["nominal_thread"],
            path=f"{path}.nominal_thread",
        ).strip()
        if not standard or not nominal_thread:
            raise _error(path, "fastener identity fields must be non-empty")
        if (
            len(options) > 16
            or any(not _OPTION_NAME.fullmatch(name) for name in options)
            or any(
                not isinstance(item, (str, bool, int, float))
                or (isinstance(item, float) and not math.isfinite(item))
                for item in options.values()
            )
        ):
            raise _error(
                f"{path}.options",
                "must contain at most 16 scalar values",
            )
        return {
            "kind": kind,
            "standard": standard,
            "nominal_thread": nominal_thread,
            "length_mm": clean_length,
            "model_thread": _boolean(
                raw["model_thread"],
                path=f"{path}.model_thread",
            ),
            "left_handed": _boolean(
                raw["left_handed"],
                path=f"{path}.left_handed",
            ),
            "options": options,
        }
    raise _error(path, "kind must be document_object or standard_fastener")


def _connector(
    value: Any,
    *,
    path: str,
    component_ids: set[str],
) -> dict[str, Any]:
    raw = _mapping(
        value,
        path=path,
        required=frozenset(
            {
                "component_id",
                "selection",
                "occurrence_path",
                "anchor",
                "offset",
            }
        ),
    )
    component_id = _identifier(
        raw["component_id"],
        path=f"{path}.component_id",
    )
    if component_id not in component_ids:
        raise _error(f"{path}.component_id", "does not name a scenario component")
    selection = _json_value(raw["selection"], path=f"{path}.selection")
    if not isinstance(selection, dict):
        raise _error(f"{path}.selection", "must be an object")
    selection_type = str(selection.get("type") or "")
    if selection_type == "component_origin":
        if set(selection) != {"type"}:
            raise _error(
                f"{path}.selection",
                "component_origin accepts no additional fields",
            )
    elif selection_type == "exact_subelement":
        if set(selection) != {"type", "subelement"} or not _SUBELEMENT.fullmatch(
            str(selection.get("subelement") or "")
        ):
            raise _error(
                f"{path}.selection",
                "exact_subelement requires one FaceN, EdgeN, or VertexN",
            )
    elif selection_type == "published_interface":
        if set(selection) != {
            "type",
            "interface_name",
        } or not _INTERFACE_NAME.fullmatch(
            str(selection.get("interface_name") or "")
        ):
            raise _error(
                f"{path}.selection",
                "published_interface requires one valid interface_name",
            )
    else:
        raise _error(
            f"{path}.selection",
            "must be component_origin, exact_subelement, or published_interface",
        )
    occurrence_path = raw["occurrence_path"]
    anchor = raw["anchor"]
    if occurrence_path is not None and not _OCCURRENCE_PATH.fullmatch(
        str(occurrence_path)
    ):
        raise _error(
            f"{path}.occurrence_path",
            "must contain 1-16 stable object-name segments",
        )
    if anchor is not None and not _SUBELEMENT.fullmatch(str(anchor)):
        raise _error(
            f"{path}.anchor",
            "must be one exact FaceN, EdgeN, or VertexN",
        )
    if anchor is not None and selection_type != "exact_subelement":
        raise _error(
            f"{path}.anchor",
            "is valid only for an exact_subelement selection",
        )
    selected = str(selection.get("subelement") or "")
    if (
        anchor is not None
        and selected.startswith("Vertex")
        and str(anchor) != selected
    ):
        raise _error(
            f"{path}.anchor",
            "must match the selected vertex",
        )
    if (
        anchor is not None
        and str(anchor) != selected
        and not str(anchor).startswith("Vertex")
    ):
        raise _error(
            f"{path}.anchor",
            "must use the selected element or one VertexN anchor",
        )
    return {
        "component_id": component_id,
        "selection": selection,
        "occurrence_path": (
            None
            if occurrence_path is None
            else _text(
                occurrence_path,
                path=f"{path}.occurrence_path",
            )
        ),
        "anchor": (
            None
            if anchor is None
            else _text(anchor, path=f"{path}.anchor")
        ),
        "offset": _placement(raw["offset"], path=f"{path}.offset"),
    }


def _bounded_contract(value: dict[str, Any], *, path: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error(path, f"is not deterministic JSON: {exc}") from exc
    if len(encoded) > _MAX_CONTRACT_BYTES:
        raise _error(path, f"exceeds {_MAX_CONTRACT_BYTES} encoded bytes")
    return value


def _validate_solved_placement(value: Any, *, path: str) -> dict[str, Any]:
    raw = _mapping(
        value,
        path=path,
        required=frozenset(
            {
                "position_mm",
                "rotation_axis",
                "rotation_angle_degrees",
                "matrix",
            }
        ),
    )
    _vector(raw["position_mm"], path=f"{path}.position_mm", size=3)
    axis = _vector(raw["rotation_axis"], path=f"{path}.rotation_axis", size=3)
    if math.sqrt(sum(item * item for item in axis)) <= 1.0e-12:
        raise _error(f"{path}.rotation_axis", "must be a non-zero vector")
    _number(
        raw["rotation_angle_degrees"],
        path=f"{path}.rotation_angle_degrees",
    )
    matrix = _vector(raw["matrix"], path=f"{path}.matrix", size=16)
    if any(abs(matrix[index] - expected) > 1.0e-9 for index, expected in (
        (12, 0.0),
        (13, 0.0),
        (14, 0.0),
        (15, 1.0),
    )):
        raise _error(path, "matrix must be an affine 4x4 placement")
    return raw


def normalize_mechanism_scenario(value: Any) -> dict[str, Any]:
    """Validate and canonicalize one internal mechanism scenario."""

    raw = _mapping(
        value,
        path="scenario",
        required=frozenset(
            {
                "schema",
                "assembly",
                "components",
                "joints",
                "solve",
                "motions",
                "simulation",
            }
        ),
    )
    if raw["schema"] != MECHANISM_SCENARIO_SCHEMA:
        raise _error(
            "scenario.schema",
            f"must be {MECHANISM_SCENARIO_SCHEMA!r}",
        )
    assembly_raw = _mapping(
        raw["assembly"],
        path="scenario.assembly",
        required=frozenset({"id", "label"}),
    )
    assembly = {
        "id": _identifier(
            assembly_raw["id"],
            path="scenario.assembly.id",
        ),
        "label": _text(
            assembly_raw["label"],
            path="scenario.assembly.label",
        ),
    }

    if (
        not isinstance(raw["components"], Sequence)
        or isinstance(raw["components"], (str, bytes))
        or not 1 <= len(raw["components"]) <= _MAX_COMPONENTS
    ):
        raise _error(
            "scenario.components",
            f"must contain 1-{_MAX_COMPONENTS} components",
        )
    components: list[dict[str, Any]] = []
    component_ids: set[str] = set()
    for index, item in enumerate(raw["components"]):
        path = f"scenario.components[{index}]"
        component_raw = _mapping(
            item,
            path=path,
            required=frozenset(
                {
                    "id",
                    "label",
                    "source",
                    "initial_placement",
                    "grounded",
                    "flexible",
                }
            ),
        )
        component_id = _identifier(component_raw["id"], path=f"{path}.id")
        if component_id in component_ids:
            raise _error(f"{path}.id", "duplicates another component")
        component_ids.add(component_id)
        grounded = _boolean(
            component_raw["grounded"],
            path=f"{path}.grounded",
        )
        flexible = _boolean(
            component_raw["flexible"],
            path=f"{path}.flexible",
        )
        if grounded and flexible:
            raise _error(path, "a flexible component cannot be grounded")
        components.append(
            {
                "id": component_id,
                "label": _text(component_raw["label"], path=f"{path}.label"),
                "source": _source(component_raw["source"], path=f"{path}.source"),
                "initial_placement": _placement(
                    component_raw["initial_placement"],
                    path=f"{path}.initial_placement",
                ),
                "grounded": grounded,
                "flexible": flexible,
            }
        )

    if (
        not isinstance(raw["joints"], Sequence)
        or isinstance(raw["joints"], (str, bytes))
        or len(raw["joints"]) > _MAX_JOINTS
    ):
        raise _error(
            "scenario.joints",
            f"must contain at most {_MAX_JOINTS} joints",
        )
    joints: list[dict[str, Any]] = []
    joint_ids: set[str] = set()
    for index, item in enumerate(raw["joints"]):
        path = f"scenario.joints[{index}]"
        joint_raw = _mapping(
            item,
            path=path,
            required=frozenset(
                {
                    "id",
                    "label",
                    "kind",
                    "connectors",
                    "parameters",
                    "length_limits_mm",
                    "angle_limits_degrees",
                    "suppressed",
                }
            ),
        )
        joint_id = _identifier(joint_raw["id"], path=f"{path}.id")
        if joint_id in joint_ids:
            raise _error(f"{path}.id", "duplicates another joint")
        joint_ids.add(joint_id)
        kind = str(joint_raw["kind"] or "")
        if kind not in _JOINT_TYPES:
            raise _error(f"{path}.kind", "is not a supported native joint kind")
        raw_connectors = joint_raw["connectors"]
        if (
            not isinstance(raw_connectors, Sequence)
            or isinstance(raw_connectors, (str, bytes))
            or len(raw_connectors) != 2
        ):
            raise _error(f"{path}.connectors", "must contain two connectors")
        connectors = [
            _connector(
                connector,
                path=f"{path}.connectors[{connector_index}]",
                component_ids=component_ids,
            )
            for connector_index, connector in enumerate(raw_connectors)
        ]
        if connectors[0]["component_id"] == connectors[1]["component_id"]:
            raise _error(path, "cannot connect one component to itself")
        raw_parameters = _json_value(
            joint_raw["parameters"],
            path=f"{path}.parameters",
        )
        if not isinstance(raw_parameters, dict):
            raise _error(f"{path}.parameters", "must contain only numeric values")
        expected_parameters = {
            "distance": {"distance_mm"},
            "angle": {"angle_degrees"},
            "rack_pinion": {"pitch_radius_mm"},
            "screw": {"thread_pitch_mm"},
            "gears": {"radius1_mm", "radius2_mm"},
            "belt": {"radius1_mm", "radius2_mm"},
        }.get(kind, set())
        if set(raw_parameters) != expected_parameters:
            raise _error(
                f"{path}.parameters",
                f"must contain exactly {sorted(expected_parameters)} for {kind}",
            )
        parameters = {
            name: _number(number, path=f"{path}.parameters.{name}")
            for name, number in raw_parameters.items()
        }
        for name in ("pitch_radius_mm", "thread_pitch_mm"):
            if name in parameters and abs(parameters[name]) <= 1.0e-12:
                raise _error(
                    f"{path}.parameters.{name}",
                    "must be non-zero",
                )
        for name in ("radius1_mm", "radius2_mm"):
            if name in parameters and parameters[name] <= 0.0:
                raise _error(
                    f"{path}.parameters.{name}",
                    "must be positive",
                )
        length_limits = _limits(
            joint_raw["length_limits_mm"],
            path=f"{path}.length_limits_mm",
        )
        angle_limits = _limits(
            joint_raw["angle_limits_degrees"],
            path=f"{path}.angle_limits_degrees",
        )
        if length_limits is not None and kind not in {"slider", "cylindrical"}:
            raise _error(
                f"{path}.length_limits_mm",
                "is valid only for slider or cylindrical joints",
            )
        if angle_limits is not None and kind not in {"revolute", "cylindrical"}:
            raise _error(
                f"{path}.angle_limits_degrees",
                "is valid only for revolute or cylindrical joints",
            )
        joints.append(
            {
                "id": joint_id,
                "label": _text(joint_raw["label"], path=f"{path}.label"),
                "kind": kind,
                "connectors": connectors,
                "parameters": parameters,
                "length_limits_mm": length_limits,
                "angle_limits_degrees": angle_limits,
                "suppressed": _boolean(
                    joint_raw["suppressed"],
                    path=f"{path}.suppressed",
                ),
            }
        )

    solve_raw = _mapping(
        raw["solve"],
        path="scenario.solve",
        required=frozenset({"id", "label", "require_solved"}),
    )
    solve = {
        "id": _identifier(solve_raw["id"], path="scenario.solve.id"),
        "label": _text(solve_raw["label"], path="scenario.solve.label"),
        "require_solved": _boolean(
            solve_raw["require_solved"],
            path="scenario.solve.require_solved",
        ),
    }

    if (
        not isinstance(raw["motions"], Sequence)
        or isinstance(raw["motions"], (str, bytes))
        or len(raw["motions"]) > _MAX_MOTIONS
    ):
        raise _error(
            "scenario.motions",
            f"must contain at most {_MAX_MOTIONS} motions",
        )
    motions: list[dict[str, Any]] = []
    motion_ids: set[str] = set()
    drives: set[tuple[str, str]] = set()
    joints_by_id = {item["id"]: item for item in joints}
    for index, item in enumerate(raw["motions"]):
        path = f"scenario.motions[{index}]"
        motion_raw = _mapping(
            item,
            path=path,
            required=frozenset(
                {"id", "label", "joint_id", "motion_type", "formula"}
            ),
        )
        motion_id = _identifier(motion_raw["id"], path=f"{path}.id")
        if motion_id in motion_ids:
            raise _error(f"{path}.id", "duplicates another motion")
        motion_ids.add(motion_id)
        joint_id = _identifier(
            motion_raw["joint_id"],
            path=f"{path}.joint_id",
        )
        if joint_id not in joint_ids:
            raise _error(f"{path}.joint_id", "does not name a scenario joint")
        joint = joints_by_id[joint_id]
        if joint["suppressed"]:
            raise _error(f"{path}.joint_id", "names a suppressed joint")
        motion_type = str(motion_raw["motion_type"] or "")
        if motion_type not in {"angular", "linear"}:
            raise _error(f"{path}.motion_type", "must be angular or linear")
        allowed_motion_types = {
            "revolute": {"angular"},
            "slider": {"linear"},
            "cylindrical": {"angular", "linear"},
        }.get(str(joint["kind"]), set())
        if motion_type not in allowed_motion_types:
            raise _error(
                f"{path}.motion_type",
                f"is not valid for a {joint['kind']} joint",
            )
        drive = (joint_id, motion_type)
        if drive in drives:
            raise _error(path, "duplicates a joint motion type")
        drives.add(drive)
        motions.append(
            {
                "id": motion_id,
                "label": _text(motion_raw["label"], path=f"{path}.label"),
                "joint_id": joint_id,
                "motion_type": motion_type,
                "formula": _text(
                    motion_raw["formula"],
                    path=f"{path}.formula",
                    maximum=512,
                ),
            }
        )

    simulation = None
    if raw["simulation"] is not None:
        simulation_raw = _mapping(
            raw["simulation"],
            path="scenario.simulation",
            required=frozenset(
                {
                    "id",
                    "label",
                    "motion_ids",
                    "start_time_s",
                    "end_time_s",
                    "time_step_s",
                    "error_tolerance",
                    "frames_per_second",
                }
            ),
        )
        raw_motion_ids = simulation_raw["motion_ids"]
        if (
            not isinstance(raw_motion_ids, Sequence)
            or isinstance(raw_motion_ids, (str, bytes))
        ):
            raise _error("scenario.simulation.motion_ids", "must be an array")
        simulation_motion_ids = [
            _identifier(
                item,
                path=f"scenario.simulation.motion_ids[{index}]",
            )
            for index, item in enumerate(raw_motion_ids)
        ]
        if not simulation_motion_ids:
            raise _error(
                "scenario.simulation.motion_ids",
                "must contain at least one motion",
            )
        if simulation_motion_ids != [item["id"] for item in motions]:
            raise _error(
                "scenario.simulation.motion_ids",
                "must exactly match the ordered scenario motions",
            )
        start = _number(
            simulation_raw["start_time_s"],
            path="scenario.simulation.start_time_s",
        )
        end = _number(
            simulation_raw["end_time_s"],
            path="scenario.simulation.end_time_s",
        )
        step = _number(
            simulation_raw["time_step_s"],
            path="scenario.simulation.time_step_s",
        )
        tolerance = _number(
            simulation_raw["error_tolerance"],
            path="scenario.simulation.error_tolerance",
        )
        frames_per_second = simulation_raw["frames_per_second"]
        if (
            isinstance(frames_per_second, bool)
            or not isinstance(frames_per_second, int)
            or not 1 <= frames_per_second <= 240
        ):
            raise _error(
                "scenario.simulation.frames_per_second",
                "must be an integer from 1 through 240",
            )
        if end <= start or step <= 0.0 or not 1.0e-12 <= tolerance <= 1.0:
            raise _error(
                "scenario.simulation",
                "contains invalid time or tolerance bounds",
            )
        estimated_frames = math.ceil((end - start) / step) + 2
        if (
            estimated_frames > 10_000
            or estimated_frames * len(components) > 100_000
        ):
            raise _error(
                "scenario.simulation",
                "exceeds the bounded native frame or component-pose limit",
            )
        simulation = {
            "id": _identifier(
                simulation_raw["id"],
                path="scenario.simulation.id",
            ),
            "label": _text(
                simulation_raw["label"],
                path="scenario.simulation.label",
            ),
            "motion_ids": simulation_motion_ids,
            "start_time_s": start,
            "end_time_s": end,
            "time_step_s": step,
            "error_tolerance": tolerance,
            "frames_per_second": frames_per_second,
        }
    elif motions:
        raise _error(
            "scenario.simulation",
            "is required when motions are present",
        )

    return _bounded_contract(
        {
            "schema": MECHANISM_SCENARIO_SCHEMA,
            "assembly": assembly,
            "components": components,
            "joints": joints,
            "solve": solve,
            "motions": motions,
            "simulation": simulation,
        },
        path="scenario",
    )


def mechanism_scenario_sha256(value: Any) -> str:
    """Return the deterministic identity of one normalized scenario."""

    clean = normalize_mechanism_scenario(value)
    encoded = json.dumps(
        clean,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_mechanism_scenario(
    scenario: Any,
    evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Run one backend behind the shared scenario/report contract boundary."""

    clean_scenario = normalize_mechanism_scenario(scenario)
    if not callable(evaluator):
        raise _error("evaluator", "must be callable")
    # The backend receives a detached JSON value. It cannot mutate the
    # canonical scenario against which its report is authenticated.
    backend_scenario = _json_value(
        clean_scenario,
        path="backend_scenario",
    )
    expected_hash = mechanism_scenario_sha256(clean_scenario)
    report = evaluator(backend_scenario)
    try:
        observed_hash = mechanism_scenario_sha256(backend_scenario)
    except MechanismContractError as exc:
        raise _error(
            "evaluator",
            "mutated the normalized scenario during evaluation",
        ) from exc
    if observed_hash != expected_hash:
        raise _error(
            "evaluator",
            "mutated the normalized scenario during evaluation",
        )
    return normalize_mechanism_solve_report(clean_scenario, report)


def normalize_mechanism_solve_report(
    scenario: Any,
    value: Any,
) -> dict[str, Any]:
    """Validate native solve evidence against its exact normalized scenario."""

    clean_scenario = normalize_mechanism_scenario(scenario)
    raw = _mapping(
        value,
        path="solve_report",
        required=frozenset(
            {
                "schema",
                "scenario_sha256",
                "status",
                "solver_code",
                "solver_verdict",
                "require_solved",
                "component_count",
                "joint_count",
                "grounded_components",
                "native_diagnostics",
                "component_placements",
                "joint_dependency_issues",
            }
        ),
        optional=frozenset({"component_occurrences"}),
    )
    if raw["schema"] != MECHANISM_SOLVE_REPORT_SCHEMA:
        raise _error(
            "solve_report.schema",
            f"must be {MECHANISM_SOLVE_REPORT_SCHEMA!r}",
        )
    expected_hash = mechanism_scenario_sha256(clean_scenario)
    if raw["scenario_sha256"] != expected_hash:
        raise _error(
            "solve_report.scenario_sha256",
            "does not identify the evaluated scenario",
        )
    status = str(raw["status"] or "")
    if status not in {"solved", "failed"}:
        raise _error("solve_report.status", "must be solved or failed")
    solver_code = raw["solver_code"]
    if isinstance(solver_code, bool) or not isinstance(solver_code, int):
        raise _error("solve_report.solver_code", "must be an integer")
    if status == "solved" and solver_code != 0:
        raise _error(
            "solve_report",
            "cannot report solved with a non-zero native solver code",
        )
    require_solved = _boolean(
        raw["require_solved"],
        path="solve_report.require_solved",
    )
    if require_solved != clean_scenario["solve"]["require_solved"]:
        raise _error(
            "solve_report.require_solved",
            "differs from the scenario",
        )
    component_count = raw["component_count"]
    joint_count = raw["joint_count"]
    if (
        isinstance(component_count, bool)
        or not isinstance(component_count, int)
        or component_count != len(clean_scenario["components"])
    ):
        raise _error(
            "solve_report.component_count",
            "differs from the scenario",
        )
    if (
        isinstance(joint_count, bool)
        or not isinstance(joint_count, int)
        or joint_count != len(clean_scenario["joints"])
    ):
        raise _error("solve_report.joint_count", "differs from the scenario")
    grounded = _json_value(
        raw["grounded_components"],
        path="solve_report.grounded_components",
    )
    expected_grounded = [
        item["id"] for item in clean_scenario["components"] if item["grounded"]
    ]
    if grounded != expected_grounded:
        raise _error(
            "solve_report.grounded_components",
            "differs from the scenario",
        )
    placements = _json_value(
        raw["component_placements"],
        path="solve_report.component_placements",
    )
    expected_component_ids = {
        item["id"] for item in clean_scenario["components"]
    }
    if not isinstance(placements, dict) or set(placements) != expected_component_ids:
        raise _error(
            "solve_report.component_placements",
            "must contain every scenario component exactly once",
        )
    for component_id, placement in placements.items():
        _validate_solved_placement(
            placement,
            path=f"solve_report.component_placements.{component_id}",
        )
    component_occurrences = None
    if "component_occurrences" in raw:
        component_occurrences = _json_value(
            raw["component_occurrences"],
            path="solve_report.component_occurrences",
        )
        if (
            not isinstance(component_occurrences, dict)
            or set(component_occurrences) != expected_component_ids
        ):
            raise _error(
                "solve_report.component_occurrences",
                "must contain every scenario component exactly once",
            )
        occurrence_count = 0
        for component_id, occurrences in component_occurrences.items():
            component_path = (
                f"solve_report.component_occurrences.{component_id}"
            )
            if not isinstance(occurrences, list):
                raise _error(component_path, "must be an array")
            seen_paths: set[str] = set()
            for index, occurrence in enumerate(occurrences):
                occurrence_count += 1
                if occurrence_count > _MAX_OCCURRENCES:
                    raise _error(
                        "solve_report.component_occurrences",
                        f"must contain at most {_MAX_OCCURRENCES} occurrences",
                    )
                path = f"{component_path}[{index}]"
                occurrence_raw = _mapping(
                    occurrence,
                    path=path,
                    required=frozenset(
                        {
                            "occurrence_path",
                            "source_node_id",
                            "source_kind",
                            "source_label",
                            "native_name",
                            "native_type_id",
                            "native_target_mode",
                            "live_occurrence",
                            "local_placement",
                            "global_placement",
                        }
                    ),
                )
                occurrence_path = _text(
                    occurrence_raw["occurrence_path"],
                    path=f"{path}.occurrence_path",
                    maximum=2048,
                )
                if (
                    not _OCCURRENCE_PATH.fullmatch(occurrence_path)
                    or occurrence_path in seen_paths
                ):
                    raise _error(
                        f"{path}.occurrence_path",
                        "must be one unique stable occurrence path",
                    )
                seen_paths.add(occurrence_path)
                source_node_id = _text(
                    occurrence_raw["source_node_id"],
                    path=f"{path}.source_node_id",
                )
                if not re.fullmatch(r"n[0-9]{4}", source_node_id):
                    raise _error(
                        f"{path}.source_node_id",
                        "must identify one captured hierarchy node",
                    )
                source_kind = _text(
                    occurrence_raw["source_kind"],
                    path=f"{path}.source_kind",
                )
                if source_kind not in {"assembly", "part", "shape"}:
                    raise _error(
                        f"{path}.source_kind",
                        "must be assembly, part, or shape",
                    )
                for field, maximum in (
                    ("source_label", 4096),
                    ("native_name", 256),
                    ("native_type_id", 256),
                    ("native_target_mode", 64),
                ):
                    _text(
                        occurrence_raw[field],
                        path=f"{path}.{field}",
                        maximum=maximum,
                    )
                live = _boolean(
                    occurrence_raw["live_occurrence"],
                    path=f"{path}.live_occurrence",
                )
                local = occurrence_raw["local_placement"]
                global_placement = occurrence_raw["global_placement"]
                if live:
                    if local is None or global_placement is None:
                        raise _error(
                            path,
                            "a live occurrence requires local and global placements",
                        )
                    _validate_solved_placement(
                        local,
                        path=f"{path}.local_placement",
                    )
                    _validate_solved_placement(
                        global_placement,
                        path=f"{path}.global_placement",
                    )
                elif local is not None or global_placement is not None:
                    raise _error(
                        path,
                        "a non-live occurrence cannot report native placements",
                    )
    native = _json_value(
        raw["native_diagnostics"],
        path="solve_report.native_diagnostics",
    )
    issues = _json_value(
        raw["joint_dependency_issues"],
        path="solve_report.joint_dependency_issues",
    )
    if not isinstance(native, dict):
        raise _error("solve_report.native_diagnostics", "must be an object")
    if not isinstance(issues, list) or not all(
        isinstance(item, dict) for item in issues
    ):
        raise _error(
            "solve_report.joint_dependency_issues",
            "must be an array of objects",
        )
    result = {
        "schema": MECHANISM_SOLVE_REPORT_SCHEMA,
        "scenario_sha256": expected_hash,
        "status": status,
        "solver_code": solver_code,
        "solver_verdict": _text(
            raw["solver_verdict"],
            path="solve_report.solver_verdict",
        ),
        "require_solved": require_solved,
        "component_count": component_count,
        "joint_count": joint_count,
        "grounded_components": grounded,
        "native_diagnostics": native,
        "component_placements": placements,
        "joint_dependency_issues": issues,
    }
    if component_occurrences is not None:
        result["component_occurrences"] = component_occurrences
    return _bounded_contract(result, path="solve_report")


def mechanism_solve_report_sha256(scenario: Any, value: Any) -> str:
    """Return the deterministic identity of an authenticated solve report."""

    clean = normalize_mechanism_solve_report(scenario, value)
    encoded = json.dumps(
        clean,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _positive_number(
    value: Any,
    *,
    path: str,
    maximum: float,
) -> float:
    result = _number(value, path=path)
    if not 0.0 < result <= maximum:
        raise _error(path, f"must be greater than zero and at most {maximum:g}")
    return result


def _nonnegative_number(
    value: Any,
    *,
    path: str,
    maximum: float,
) -> float:
    result = _number(value, path=path)
    if not 0.0 <= result <= maximum:
        raise _error(path, f"must be from zero through {maximum:g}")
    return result


def _static_pair(
    raw: Mapping[str, Any],
    *,
    path: str,
    component_ids: set[str],
) -> tuple[str, str, tuple[str, str]]:
    first = _identifier(raw["first_component"], path=f"{path}.first_component")
    second = _identifier(raw["second_component"], path=f"{path}.second_component")
    if first not in component_ids or second not in component_ids:
        raise _error(path, "names a component outside the mechanism scenario")
    if first == second:
        raise _error(path, "cannot compare a component with itself")
    return first, second, tuple(sorted((first, second)))


def normalize_mechanism_static_check(
    scenario: Any,
    value: Any,
) -> dict[str, Any]:
    """Validate one explicit static verification contract."""

    clean_scenario = normalize_mechanism_scenario(scenario)
    raw = _mapping(
        value,
        path="static_check",
        required=frozenset(
            {
                "schema",
                "id",
                "label",
                "scenario_sha256",
                "requirements",
                "contacts",
            }
        ),
    )
    if raw["schema"] != MECHANISM_STATIC_CHECK_SCHEMA:
        raise _error(
            "static_check.schema",
            f"must be {MECHANISM_STATIC_CHECK_SCHEMA!r}",
        )
    expected_scenario_hash = mechanism_scenario_sha256(clean_scenario)
    if raw["scenario_sha256"] != expected_scenario_hash:
        raise _error(
            "static_check.scenario_sha256",
            "does not identify the normalized mechanism scenario",
        )
    component_ids = {
        str(component["id"]) for component in clean_scenario["components"]
    }
    seen_ids: set[str] = set()
    seen_pairs: dict[tuple[str, str], str] = {}

    raw_requirements = raw["requirements"]
    if (
        not isinstance(raw_requirements, Sequence)
        or isinstance(raw_requirements, (str, bytes))
        or len(raw_requirements) > 64
    ):
        raise _error(
            "static_check.requirements",
            "must contain at most 64 entries",
        )
    requirements: list[dict[str, Any]] = []
    for index, item in enumerate(raw_requirements):
        path = f"static_check.requirements[{index}]"
        if not isinstance(item, Mapping):
            raise _error(path, "must be an object")
        requirement_type = str(item.get("type") or "")
        expected_fields = {
            "id",
            "type",
            "first_component",
            "second_component",
            "tolerance_mm",
        }
        if requirement_type == "minimum_clearance":
            expected_fields.add("minimum_mm")
        requirement_raw = _mapping(
            item,
            path=path,
            required=frozenset(expected_fields),
        )
        if requirement_type not in _STATIC_REQUIREMENT_TYPES:
            raise _error(
                f"{path}.type",
                f"must be one of {sorted(_STATIC_REQUIREMENT_TYPES)}",
            )
        declaration_id = _identifier(
            requirement_raw["id"],
            path=f"{path}.id",
        )
        if declaration_id in seen_ids:
            raise _error(f"{path}.id", "duplicates another declaration")
        seen_ids.add(declaration_id)
        first, second, pair = _static_pair(
            requirement_raw,
            path=path,
            component_ids=component_ids,
        )
        if pair in seen_pairs:
            raise _error(
                path,
                f"duplicates the unordered pair declared by {seen_pairs[pair]}",
            )
        seen_pairs[pair] = declaration_id
        normalized = {
            "id": declaration_id,
            "type": requirement_type,
            "first_component": first,
            "second_component": second,
            "tolerance_mm": _positive_number(
                requirement_raw["tolerance_mm"],
                path=f"{path}.tolerance_mm",
                maximum=1.0e3,
            ),
        }
        if requirement_type == "minimum_clearance":
            normalized["minimum_mm"] = _nonnegative_number(
                requirement_raw["minimum_mm"],
                path=f"{path}.minimum_mm",
                maximum=1.0e6,
            )
        requirements.append(normalized)

    raw_contacts = raw["contacts"]
    if (
        not isinstance(raw_contacts, Sequence)
        or isinstance(raw_contacts, (str, bytes))
        or len(raw_contacts) > 64
    ):
        raise _error(
            "static_check.contacts",
            "must contain at most 64 entries",
        )
    contacts: list[dict[str, Any]] = []
    for index, item in enumerate(raw_contacts):
        path = f"static_check.contacts[{index}]"
        if not isinstance(item, Mapping):
            raise _error(path, "must be an object")
        policy = str(item.get("policy") or "")
        expected_fields = {
            "id",
            "policy",
            "first_component",
            "second_component",
        }
        if policy == "ignored":
            expected_fields.add("reason")
        else:
            expected_fields.add("tolerance_mm")
        if policy == "clearance":
            expected_fields.add("minimum_clearance_mm")
        elif policy in {"allowed", "required"}:
            expected_fields.update({"first_interface", "second_interface"})
        contact_raw = _mapping(
            item,
            path=path,
            required=frozenset(expected_fields),
        )
        if policy not in _CONTACT_POLICIES:
            raise _error(
                f"{path}.policy",
                f"must be one of {sorted(_CONTACT_POLICIES)}",
            )
        declaration_id = _identifier(contact_raw["id"], path=f"{path}.id")
        if declaration_id in seen_ids:
            raise _error(f"{path}.id", "duplicates another declaration")
        seen_ids.add(declaration_id)
        first, second, pair = _static_pair(
            contact_raw,
            path=path,
            component_ids=component_ids,
        )
        if pair in seen_pairs:
            raise _error(
                path,
                f"duplicates the unordered pair declared by {seen_pairs[pair]}",
            )
        seen_pairs[pair] = declaration_id
        normalized = {
            "id": declaration_id,
            "policy": policy,
            "first_component": first,
            "second_component": second,
        }
        if policy == "ignored":
            reason = _text(
                contact_raw["reason"],
                path=f"{path}.reason",
                maximum=256,
            ).strip()
            if not reason:
                raise _error(f"{path}.reason", "must be non-empty")
            normalized["reason"] = reason
        else:
            normalized["tolerance_mm"] = _positive_number(
                contact_raw["tolerance_mm"],
                path=f"{path}.tolerance_mm",
                maximum=1.0e3,
            )
        if policy == "clearance":
            normalized["minimum_clearance_mm"] = _nonnegative_number(
                contact_raw["minimum_clearance_mm"],
                path=f"{path}.minimum_clearance_mm",
                maximum=1.0e6,
            )
        elif policy in {"allowed", "required"}:
            for field in ("first_interface", "second_interface"):
                interface_name = str(contact_raw[field] or "")
                if not _INTERFACE_NAME.fullmatch(interface_name):
                    raise _error(
                        f"{path}.{field}",
                        "must name one published semantic interface",
                    )
                normalized[field] = interface_name
        contacts.append(normalized)

    if not requirements and not contacts:
        raise _error(
            "static_check",
            "must contain at least one requirement or contact policy",
        )
    if not requirements and all(item["policy"] == "ignored" for item in contacts):
        raise _error(
            "static_check",
            "must contain at least one evaluated declaration",
        )
    return _bounded_contract(
        {
            "schema": MECHANISM_STATIC_CHECK_SCHEMA,
            "id": _identifier(raw["id"], path="static_check.id"),
            "label": _text(raw["label"], path="static_check.label"),
            "scenario_sha256": expected_scenario_hash,
            "requirements": requirements,
            "contacts": contacts,
        },
        path="static_check",
    )


def mechanism_static_check_sha256(scenario: Any, value: Any) -> str:
    """Return the deterministic identity of one normalized static check."""

    clean = normalize_mechanism_static_check(scenario, value)
    encoded = json.dumps(
        clean,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _static_declarations(check: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"declaration_kind": "requirement", **dict(item)}
        for item in list(check["requirements"])
    ] + [
        {"declaration_kind": "contact", **dict(item)}
        for item in list(check["contacts"])
    ]


def _normalize_static_geometry_evidence(
    scenario: Mapping[str, Any],
    check: Mapping[str, Any],
    value: Any,
) -> dict[str, Any]:
    raw = _mapping(
        value,
        path="geometry_evidence",
        required=frozenset(
            {
                "schema",
                "geometry_engine",
                "component_count",
                "declaration_count",
                "complete_count",
                "indeterminate_count",
                "declarations",
            }
        ),
    )
    if raw["schema"] != STATIC_MECHANISM_EVIDENCE_SCHEMA:
        raise _error(
            "geometry_evidence.schema",
            f"must be {STATIC_MECHANISM_EVIDENCE_SCHEMA!r}",
        )
    engine = _mapping(
        raw["geometry_engine"],
        path="geometry_evidence.geometry_engine",
        required=frozenset({"name", "version"}),
    )
    clean_engine = {
        "name": _text(
            engine["name"],
            path="geometry_evidence.geometry_engine.name",
            maximum=64,
        ),
        "version": _text(
            engine["version"],
            path="geometry_evidence.geometry_engine.version",
            maximum=64,
        ),
    }
    if not clean_engine["name"] or not clean_engine["version"]:
        raise _error(
            "geometry_evidence.geometry_engine",
            "must identify the exact geometry engine",
        )
    expected_component_count = len(scenario["components"])
    component_count = raw["component_count"]
    if (
        isinstance(component_count, bool)
        or not isinstance(component_count, int)
        or component_count != expected_component_count
    ):
        raise _error(
            "geometry_evidence.component_count",
            "differs from the mechanism scenario",
        )
    expected_declarations = [
        item
        for item in _static_declarations(check)
        if item.get("policy") != "ignored"
    ]
    evidence_items = raw["declarations"]
    if (
        not isinstance(evidence_items, Sequence)
        or isinstance(evidence_items, (str, bytes))
        or len(evidence_items) != len(expected_declarations)
    ):
        raise _error(
            "geometry_evidence.declarations",
            "must exactly match every evaluated declaration",
        )
    clean_items: list[dict[str, Any]] = []
    complete_count = 0
    for index, (item, declaration) in enumerate(
        zip(evidence_items, expected_declarations, strict=True)
    ):
        path = f"geometry_evidence.declarations[{index}]"
        item_raw = _mapping(
            item,
            path=path,
            required=frozenset(
                {
                    "declaration_id",
                    "first_component",
                    "second_component",
                    "tolerance_mm",
                    "first_interface",
                    "second_interface",
                    "status",
                    "error",
                    "body",
                    "interfaces",
                }
            ),
        )
        expected_interfaces = (
            (
                declaration.get("first_interface"),
                declaration.get("second_interface"),
            )
            if declaration.get("policy") in {"allowed", "required"}
            else (None, None)
        )
        expected_identity = (
            declaration["id"],
            declaration["first_component"],
            declaration["second_component"],
            float(declaration["tolerance_mm"]),
            *expected_interfaces,
        )
        observed_identity = (
            item_raw["declaration_id"],
            item_raw["first_component"],
            item_raw["second_component"],
            item_raw["tolerance_mm"],
            item_raw["first_interface"],
            item_raw["second_interface"],
        )
        if observed_identity != expected_identity:
            raise _error(path, "does not match its static declaration")
        status = str(item_raw["status"] or "")
        error = _text(item_raw["error"], path=f"{path}.error", maximum=512)
        if status not in {"complete", "indeterminate"}:
            raise _error(f"{path}.status", "must be complete or indeterminate")
        body = _json_value(item_raw["body"], path=f"{path}.body")
        interfaces = _json_value(
            item_raw["interfaces"],
            path=f"{path}.interfaces",
        )
        if status == "indeterminate":
            if body is not None or interfaces is not None or not error:
                raise _error(
                    path,
                    "indeterminate evidence requires an error and no partial geometry",
                )
        else:
            complete_count += 1
            if error or not isinstance(body, dict):
                raise _error(path, "complete evidence requires one exact body result")
            required_body_fields = {
                "first_component",
                "second_component",
                "minimum_distance_mm",
                "common_volume_mm3",
            }
            if not required_body_fields <= set(body):
                raise _error(f"{path}.body", "is missing exact pair evidence")
            if (
                body["first_component"] != declaration["first_component"]
                or body["second_component"] != declaration["second_component"]
            ):
                raise _error(f"{path}.body", "changed component identity")
            distance = _number(
                body["minimum_distance_mm"],
                path=f"{path}.body.minimum_distance_mm",
            )
            volume = _number(
                body["common_volume_mm3"],
                path=f"{path}.body.common_volume_mm3",
            )
            if distance < 0.0 or volume < 0.0:
                raise _error(f"{path}.body", "contains a negative exact measure")
            if expected_interfaces == (None, None):
                if interfaces is not None:
                    raise _error(path, "invented semantic-interface evidence")
            else:
                if not isinstance(interfaces, dict):
                    raise _error(path, "is missing semantic-interface evidence")
                if (
                    interfaces.get("first_interface") != expected_interfaces[0]
                    or interfaces.get("second_interface") != expected_interfaces[1]
                    or interfaces.get("first_component")
                    != declaration["first_component"]
                    or interfaces.get("second_component")
                    != declaration["second_component"]
                ):
                    raise _error(
                        f"{path}.interfaces",
                        "changed semantic-interface identity",
                    )
                _number(
                    interfaces.get("minimum_distance_mm"),
                    path=f"{path}.interfaces.minimum_distance_mm",
                )
                contact_locus = interfaces.get(
                    "contact_locus_on_interfaces"
                )
                if (
                    contact_locus is not True
                    and contact_locus is not False
                    and contact_locus is not None
                ):
                    raise _error(
                        f"{path}.interfaces.contact_locus_on_interfaces",
                        "must be true, false, or null",
                    )
        clean_items.append(
            {
                "declaration_id": str(item_raw["declaration_id"]),
                "first_component": str(item_raw["first_component"]),
                "second_component": str(item_raw["second_component"]),
                "tolerance_mm": float(item_raw["tolerance_mm"]),
                "first_interface": item_raw["first_interface"],
                "second_interface": item_raw["second_interface"],
                "status": status,
                "error": error,
                "body": body,
                "interfaces": interfaces,
            }
        )
    reported_count = raw["declaration_count"]
    reported_complete = raw["complete_count"]
    reported_indeterminate = raw["indeterminate_count"]
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (
            reported_count,
            reported_complete,
            reported_indeterminate,
        )
    ) or (
        reported_count != len(clean_items)
        or reported_complete != complete_count
        or reported_indeterminate != len(clean_items) - complete_count
    ):
        raise _error(
            "geometry_evidence",
            "summary counts do not match declaration evidence",
        )
    return _bounded_contract(
        {
            "schema": STATIC_MECHANISM_EVIDENCE_SCHEMA,
            "geometry_engine": clean_engine,
            "component_count": component_count,
            "declaration_count": len(clean_items),
            "complete_count": complete_count,
            "indeterminate_count": len(clean_items) - complete_count,
            "declarations": clean_items,
        },
        path="geometry_evidence",
    )


def _static_result(
    declaration: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    *,
    solved: bool,
) -> dict[str, Any]:
    kind = str(declaration["declaration_kind"])
    assertion = str(declaration.get("type") or declaration.get("policy") or "")
    policy = str(declaration.get("policy") or "")
    tolerance = declaration.get("tolerance_mm")
    minimum_clearance = (
        declaration.get("minimum_mm")
        if assertion == "minimum_clearance"
        else declaration.get("minimum_clearance_mm")
        if assertion == "clearance"
        else None
    )
    base = {
        "id": str(declaration["id"]),
        "declaration_kind": kind,
        "assertion": assertion,
        "first_component": str(declaration["first_component"]),
        "second_component": str(declaration["second_component"]),
        "tolerance_mm": tolerance,
        "minimum_clearance_mm": minimum_clearance,
        "first_interface": declaration.get("first_interface"),
        "second_interface": declaration.get("second_interface"),
    }
    if policy == "ignored":
        return {
            **base,
            "verdict": "ignored",
            "reason_code": "explicitly_ignored",
            "message": str(declaration["reason"]),
            "evidence": None,
        }
    if evidence is None:
        raise _error(
            f"static_check.{declaration['id']}",
            "has no exact geometry evidence",
        )
    if not solved:
        return {
            **base,
            "verdict": "indeterminate",
            "reason_code": "assembly_not_solved",
            "message": "The native Assembly graph was not solved cleanly.",
            "evidence": dict(evidence),
        }
    if evidence["status"] != "complete":
        return {
            **base,
            "verdict": "indeterminate",
            "reason_code": "geometry_evaluation_failed",
            "message": str(evidence["error"]),
            "evidence": dict(evidence),
        }
    body = dict(evidence["body"])
    distance = float(body["minimum_distance_mm"])
    common_volume = float(body["common_volume_mm3"])
    tolerance_value = float(tolerance)
    if common_volume > 0.0:
        return {
            **base,
            "verdict": "fail",
            "reason_code": "positive_volume_overlap",
            "message": (
                f"{base['first_component']} and {base['second_component']} "
                f"overlap by {common_volume:.9g} mm3."
            ),
            "evidence": dict(evidence),
        }
    if assertion in {"collision_free", "prohibited"}:
        if distance <= tolerance_value:
            return {
                **base,
                "verdict": "fail",
                "reason_code": "prohibited_contact",
                "message": (
                    f"{base['first_component']} and {base['second_component']} "
                    f"are {distance:.9g} mm apart, within the declared "
                    f"{tolerance_value:.9g} mm contact tolerance."
                ),
                "evidence": dict(evidence),
            }
        verdict = "pass"
        reason_code = "separated_beyond_tolerance"
        message = (
            f"Exact separation is {distance:.9g} mm, greater than the declared "
            f"{tolerance_value:.9g} mm tolerance."
        )
    elif assertion in {"minimum_clearance", "clearance"}:
        minimum = float(minimum_clearance)
        if distance + tolerance_value < minimum:
            return {
                **base,
                "verdict": "fail",
                "reason_code": "insufficient_clearance",
                "message": (
                    f"Exact separation is {distance:.9g} mm; the declared minimum "
                    f"is {minimum:.9g} mm with {tolerance_value:.9g} mm tolerance."
                ),
                "evidence": dict(evidence),
            }
        verdict = "pass"
        reason_code = "minimum_clearance_satisfied"
        message = (
            f"Exact separation is {distance:.9g} mm; the declared minimum is "
            f"{minimum:.9g} mm with {tolerance_value:.9g} mm tolerance."
        )
    elif assertion in {"allowed", "required"}:
        interfaces = evidence.get("interfaces")
        if not isinstance(interfaces, Mapping):
            return {
                **base,
                "verdict": "indeterminate",
                "reason_code": "missing_interface_evidence",
                "message": "Exact semantic-interface evidence is unavailable.",
                "evidence": dict(evidence),
            }
        if distance > tolerance_value:
            if assertion == "required":
                return {
                    **base,
                    "verdict": "fail",
                    "reason_code": "required_contact_absent",
                    "message": (
                        f"Required interfaces are separated; component distance is "
                        f"{distance:.9g} mm, beyond {tolerance_value:.9g} mm."
                    ),
                    "evidence": dict(evidence),
                }
            verdict = "pass"
            reason_code = "allowed_pair_separated"
            message = (
                f"The allowed-contact pair is separated by {distance:.9g} mm."
            )
        else:
            locus = interfaces.get("contact_locus_on_interfaces")
            if locus is False:
                return {
                    **base,
                    "verdict": "fail",
                    "reason_code": "contact_outside_declared_interfaces",
                    "message": (
                        "Contact is not confined to the two declared semantic "
                        "interfaces."
                    ),
                    "evidence": dict(evidence),
                }
            if locus is None:
                return {
                    **base,
                    "verdict": "indeterminate",
                    "reason_code": "interface_contact_not_proven",
                    "message": (
                        "The geometry kernel did not prove that all contact is "
                        "confined to the declared interfaces."
                    ),
                    "evidence": dict(evidence),
                }
            verdict = "pass"
            reason_code = (
                "required_interface_contact"
                if assertion == "required"
                else "allowed_interface_contact"
            )
            message = (
                "Exact contact is confined to the two declared semantic interfaces."
            )
    else:
        raise _error(
            f"static_check.{declaration['id']}",
            f"has unsupported assertion {assertion!r}",
        )
    return {
        **base,
        "verdict": verdict,
        "reason_code": reason_code,
        "message": message,
        "evidence": dict(evidence),
    }


def _build_mechanism_verification_report(
    scenario: Mapping[str, Any],
    solve_report: Mapping[str, Any],
    check: Mapping[str, Any],
    geometry_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_by_id = {
        str(item["declaration_id"]): item
        for item in geometry_evidence["declarations"]
    }
    results = [
        _static_result(
            declaration,
            evidence_by_id.get(str(declaration["id"])),
            solved=str(solve_report["status"]) == "solved",
        )
        for declaration in _static_declarations(check)
    ]
    failed = [item for item in results if item["verdict"] == "fail"]
    indeterminate = [
        item for item in results if item["verdict"] == "indeterminate"
    ]
    ignored = [item for item in results if item["verdict"] == "ignored"]
    passed = [item for item in results if item["verdict"] == "pass"]
    verdict = "fail" if failed else "indeterminate" if indeterminate else "pass"
    first_failure = (
        dict(failed[0])
        if failed
        else dict(indeterminate[0])
        if indeterminate
        else None
    )
    return _bounded_contract(
        {
            "schema": MECHANISM_VERIFICATION_REPORT_SCHEMA,
            "scenario_sha256": mechanism_scenario_sha256(scenario),
            "solve_report_sha256": mechanism_solve_report_sha256(
                scenario,
                solve_report,
            ),
            "static_check_sha256": mechanism_static_check_sha256(
                scenario,
                check,
            ),
            "verification_id": str(check["id"]),
            "label": str(check["label"]),
            "verdict": verdict,
            "scope": {
                "analysis": "static_solved_state",
                "geometry_authority": "exact_brep_occt",
                "pair_selection": "explicit_only",
                "tolerance_source": "declared_per_pair",
                "motion_certified": False,
            },
            "summary": {
                "declaration_count": len(results),
                "pass_count": len(passed),
                "fail_count": len(failed),
                "indeterminate_count": len(indeterminate),
                "ignored_count": len(ignored),
            },
            "results": results,
            "first_failure": first_failure,
            "geometry_evidence": dict(geometry_evidence),
        },
        path="verification_report",
    )


def evaluate_static_mechanism_check(
    scenario: Any,
    solve_report: Any,
    check: Any,
    geometry_evidence: Any,
) -> dict[str, Any]:
    """Apply explicit static requirements to exact OCCT geometry evidence."""

    clean_scenario = normalize_mechanism_scenario(scenario)
    clean_solve = normalize_mechanism_solve_report(clean_scenario, solve_report)
    clean_check = normalize_mechanism_static_check(clean_scenario, check)
    clean_evidence = _normalize_static_geometry_evidence(
        clean_scenario,
        clean_check,
        geometry_evidence,
    )
    return _build_mechanism_verification_report(
        clean_scenario,
        clean_solve,
        clean_check,
        clean_evidence,
    )


def normalize_mechanism_verification_report(
    scenario: Any,
    solve_report: Any,
    check: Any,
    value: Any,
) -> dict[str, Any]:
    """Authenticate a persisted static report against all declared inputs."""

    clean_scenario = normalize_mechanism_scenario(scenario)
    clean_solve = normalize_mechanism_solve_report(clean_scenario, solve_report)
    clean_check = normalize_mechanism_static_check(clean_scenario, check)
    raw = _mapping(
        value,
        path="verification_report",
        required=frozenset(
            {
                "schema",
                "scenario_sha256",
                "solve_report_sha256",
                "static_check_sha256",
                "verification_id",
                "label",
                "verdict",
                "scope",
                "summary",
                "results",
                "first_failure",
                "geometry_evidence",
            }
        ),
    )
    if raw["schema"] != MECHANISM_VERIFICATION_REPORT_SCHEMA:
        raise _error(
            "verification_report.schema",
            f"must be {MECHANISM_VERIFICATION_REPORT_SCHEMA!r}",
        )
    clean_evidence = _normalize_static_geometry_evidence(
        clean_scenario,
        clean_check,
        raw["geometry_evidence"],
    )
    expected = _build_mechanism_verification_report(
        clean_scenario,
        clean_solve,
        clean_check,
        clean_evidence,
    )
    observed = _json_value(raw, path="verification_report")
    if observed != expected:
        raise _error(
            "verification_report",
            "does not match the deterministic report for its authenticated evidence",
        )
    return expected
