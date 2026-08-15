# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Dogbone dress-up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureDressupSupport import (
    MAX_DRESSUP_COMMANDS,
    PreparedDressupBase,
    assert_dressup_preflight_current,
    command_path_sha256,
    cutting_command_count,
    dressup_error,
    preflight_dressup_base,
    publish_dressup_replacement,
    verify_dressup_envelope,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import exact_fields, finite_number
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_DOGBONE_INPUT_COMMANDS = 50_000
MAX_DOGBONE_PROFILE_RUNS = 512
MAX_DOGBONE_CANDIDATES = 10_000
MAX_DOGBONE_GROUPS = 2_048
MAX_DOGBONE_CLOSED_PROFILE_SEARCH_WORK = 2_000_000
MAX_DOGBONE_NESTED_BONE_COMPARISONS = 2_000_000
_STYLE_NAMES = {
    "dogbone": "Dogbone",
    "t_bone_horizontal": "T-bone horizontal",
    "t_bone_vertical": "T-bone vertical",
    "t_bone_long_edge": "T-bone long edge",
    "t_bone_short_edge": "T-bone short edge",
}
_SIDE_NAMES = {"left": "Left", "right": "Right"}
_INCISION_NAMES = {
    "adaptive": "adaptive",
    "fixed": "fixed",
    "custom": "custom",
}
_INCISION_FIELDS = {
    "adaptive": frozenset({"kind", "maximum_length_mm"}),
    "fixed": frozenset({"kind"}),
    "custom": frozenset({"kind", "length_mm"}),
}
_LOCATION_FIELDS = frozenset({"x_mm", "y_mm"})


@dataclass(frozen=True, slots=True)
class DogboneDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    style: Any
    side: Any
    incision: Mapping[str, Any]
    only_closed_profiles: Any
    disabled_bone_locations_mm: Any


@dataclass(frozen=True, slots=True)
class BoneGroup:
    x_mm: float
    y_mm: float
    indices: tuple[int, ...]
    z_levels_mm: tuple[float, ...]
    lengths_mm: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PreparedDogboneDressup:
    base: PreparedDressupBase
    style: str
    native_style: str
    side: str
    native_side: str
    incision: str
    native_incision: str
    custom_length_mm: float
    only_closed_profiles: bool
    disabled_locations_mm: tuple[tuple[float, float], ...]
    bone_blacklist: tuple[int, ...]
    bone_groups: tuple[BoneGroup, ...]
    candidate_bone_count: int
    enabled_bone_count: int
    inserted_command_count: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def _normalize_incision(value: Any) -> tuple[str, str, float]:
    if not isinstance(value, Mapping):
        dressup_error("CAM Dogbone incision must be one closed incision request.")
    kind = str(value.get("kind") or "")
    fields = _INCISION_FIELDS.get(kind)
    if fields is None:
        dressup_error("CAM Dogbone incision.kind must be adaptive, fixed, or custom.")
    item = exact_fields(value, fields, f"CAM Dogbone {kind} incision")
    if kind == "adaptive":
        custom = finite_number(
            item["maximum_length_mm"],
            "CAM Dogbone maximum_length_mm",
            minimum=0.0,
            maximum=1_000_000.0,
        )
    elif kind == "custom":
        custom = finite_number(
            item["length_mm"],
            "CAM Dogbone length_mm",
            minimum=0.0,
            maximum=1_000_000.0,
        )
        if custom <= 0.0:
            dressup_error("CAM Dogbone custom length_mm must be greater than zero.")
    else:
        custom = 0.0
    return kind, _INCISION_NAMES[kind], custom


def _normalize_locations(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or len(value) > 256:
        dressup_error(
            "CAM Dogbone disabled_bone_locations_mm must contain zero through 256 locations."
        )
    result = []
    keys = set()
    for index, raw in enumerate(value):
        item = exact_fields(
            raw,
            _LOCATION_FIELDS,
            f"CAM Dogbone disabled location {index}",
        )
        location = (
            finite_number(item["x_mm"], f"CAM Dogbone location {index} x_mm"),
            finite_number(item["y_mm"], f"CAM Dogbone location {index} y_mm"),
        )
        key = tuple(round(component, 4) for component in location)
        if key in keys:
            dressup_error(
                "CAM Dogbone disabled locations must name distinct XY corner groups."
            )
        result.append(key)
        keys.add(key)
    return tuple(result)


def _bone_groups(bones: tuple[Any, ...]) -> tuple[BoneGroup, ...]:
    grouped: dict[tuple[float, float], dict[str, Any]] = {}
    for index, bone in enumerate(bones):
        position = bone.position()
        key = (round(float(position.x), 4), round(float(position.y), 4))
        value = grouped.setdefault(
            key,
            {"indices": [], "z": [], "lengths": []},
        )
        value["indices"].append(index)
        value["z"].append(round(float(position.z), 4))
        value["lengths"].append(round(float(bone.length), 7))
    return tuple(
        BoneGroup(
            x_mm=key[0],
            y_mm=key[1],
            indices=tuple(value["indices"]),
            z_levels_mm=tuple(sorted(set(value["z"]))),
            lengths_mm=tuple(sorted(value["lengths"])),
        )
        for key, value in sorted(
            grouped.items(),
            key=lambda item: item[1]["indices"][0],
        )
    )


def _available_locations(groups: tuple[BoneGroup, ...]) -> list[dict[str, Any]]:
    return [
        {
            "x_mm": group.x_mm,
            "y_mm": group.y_mm,
            "cutting_depth_count": len(group.z_levels_mm),
        }
        for group in groups[:128]
    ]


def _resolve_blacklist(
    groups: tuple[BoneGroup, ...],
    requested: tuple[tuple[float, float], ...],
) -> tuple[int, ...]:
    by_location = {(group.x_mm, group.y_mm): group for group in groups}
    missing = [location for location in requested if location not in by_location]
    if missing:
        dressup_error(
            "CAM Dogbone disabled_bone_locations_mm includes a corner not generated "
            "by the exact requested settings.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "missing_locations_mm": [list(value) for value in missing],
                "available_bone_locations_mm": _available_locations(groups),
                "available_locations_truncated": len(groups) > 128,
            },
        )
    return tuple(
        index
        for location in requested
        for index in by_location[location].indices
    )


def _preflight_input_work(base: PreparedDressupBase, only_closed: bool) -> None:
    commands = tuple(base.base.Path.Commands or ())
    if len(commands) > MAX_DOGBONE_INPUT_COMMANDS:
        dressup_error(
            f"CAM Dogbone base has {len(commands)} commands; its interactive limit is "
            f"{MAX_DOGBONE_INPUT_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    if not only_closed:
        return
    try:
        import Path
        import Path.Base.Language as PathLanguage
        import PathScripts.PathUtils as PathUtils

        instructions = tuple(
            PathLanguage.Maneuver.FromPath(
                PathUtils.getPathWithPlacement(base.base)
            ).instr
        )
        cutting = tuple(
            instruction.isMove()
            and not instruction.isRapid()
            and not instruction.isPlunge()
            for instruction in instructions
        )
        run_bounds = []
        run_start = None
        for index, value in enumerate(cutting):
            if value and run_start is None:
                run_start = index
            if run_start is not None and (
                index == len(cutting) - 1 or not cutting[index + 1]
            ):
                run_bounds.append((run_start, index))
                run_start = None
        runs = len(run_bounds)
        search_work = 0
        for start, end in run_bounds:
            if Path.Geom.pointsCoincide(
                instructions[start].positionBegin(),
                instructions[end].positionEnd(),
            ):
                continue
            length = end - start + 1
            search_work += length * max(0, length - 1) // 2
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM Dogbone cutting profiles could not be inspected.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc
    if runs > MAX_DOGBONE_PROFILE_RUNS:
        dressup_error(
            f"CAM Dogbone found {runs} cutting-profile runs; closed-profile filtering "
            f"is limited to {MAX_DOGBONE_PROFILE_RUNS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    if search_work > MAX_DOGBONE_CLOSED_PROFILE_SEARCH_WORK:
        dressup_error(
            "CAM Dogbone closed-profile discovery would require too many corner "
            "comparisons for an interactive Native call.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={
                "estimated_corner_comparisons": search_work,
                "maximum_corner_comparisons": (
                    MAX_DOGBONE_CLOSED_PROFILE_SEARCH_WORK
                ),
            },
        )


def _preflight_nested_work(base: PreparedDressupBase) -> None:
    if not hasattr(base.base, "BoneBlacklist"):
        return
    existing = tuple(getattr(base.base.Proxy, "bones", ()) or ())
    upper_bound = len(existing) * len(tuple(base.base.Path.Commands or ()))
    if upper_bound > MAX_DOGBONE_NESTED_BONE_COMPARISONS:
        dressup_error(
            "CAM Dogbone-on-Dogbone duplicate detection is too large for an "
            "interactive Native call.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={
                "existing_bone_count": len(existing),
                "base_command_count": len(tuple(base.base.Path.Commands or ())),
                "maximum_duplicate_comparisons": (
                    MAX_DOGBONE_NESTED_BONE_COMPARISONS
                ),
            },
        )


def _enabled_bone_count(
    base: Any,
    bones: tuple[Any, ...],
    blacklist: tuple[int, ...],
) -> int:
    nested = hasattr(base, "BoneBlacklist")
    return sum(
        1
        for index, bone in enumerate(bones)
        if index not in blacklist
        and not (
            nested
            and base.Proxy.includesBoneAt(bone.position())
        )
    )


def preflight_dogbone_dressup(
    document: Any,
    spec: DogboneDressupSpec,
) -> PreparedDogboneDressup:
    """Freeze one exact base and prepare its complete Dogbone path."""

    if not isinstance(spec, DogboneDressupSpec):
        raise TypeError("spec must be a DogboneDressupSpec")
    style = str(spec.style or "")
    side = str(spec.side or "")
    if style not in _STYLE_NAMES:
        dressup_error(
            "CAM Dogbone style must be dogbone or one shipped T-bone orientation."
        )
    if side not in _SIDE_NAMES:
        dressup_error("CAM Dogbone side must be left or right.")
    incision, native_incision, custom = _normalize_incision(spec.incision)
    if not isinstance(spec.only_closed_profiles, bool):
        dressup_error("CAM Dogbone only_closed_profiles must be true or false.")
    requested_locations = _normalize_locations(spec.disabled_bone_locations_mm)
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Dogbone dress-up",
    )
    try:
        diameter = float(base.controller.Tool.Diameter.Value)
    except Exception as exc:
        raise NativeManufactureError(
            "CAM Dogbone requires a controller with a readable cutter diameter.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if diameter <= 0.0:
        dressup_error(
            "CAM Dogbone requires a controller with a positive cutter diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    _preflight_input_work(base, spec.only_closed_profiles)
    _preflight_nested_work(base)
    try:
        import Path.Dressup.DogboneII as Dogbone
        import PathScripts.PathUtils as PathUtils

        _preview_path, preview_bones = Dogbone.generatePathWithMetadata(
            base.base,
            side=_SIDE_NAMES[side],
            style=_STYLE_NAMES[style],
            incision=native_incision,
            custom_length=custom,
            only_closed_profiles=spec.only_closed_profiles,
        )
        groups = _bone_groups(preview_bones)
        if len(preview_bones) > MAX_DOGBONE_CANDIDATES or len(groups) > MAX_DOGBONE_GROUPS:
            dressup_error(
                "CAM Dogbone produced too many interactive corner candidates.",
                "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
                repair={
                    "candidate_bone_count": len(preview_bones),
                    "bone_group_count": len(groups),
                    "maximum_candidate_bones": MAX_DOGBONE_CANDIDATES,
                    "maximum_bone_groups": MAX_DOGBONE_GROUPS,
                },
            )
        if not preview_bones:
            dressup_error(
                "CAM Dogbone found no eligible corner on the requested side and profiles.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        blacklist = _resolve_blacklist(groups, requested_locations)
        expected_path, expected_bones = Dogbone.generatePathWithMetadata(
            base.base,
            side=_SIDE_NAMES[side],
            style=_STYLE_NAMES[style],
            incision=native_incision,
            custom_length=custom,
            only_closed_profiles=spec.only_closed_profiles,
            bone_blacklist=blacklist,
        )
        commands = tuple(expected_path.Commands or ())
        placed_base_count = len(
            tuple(PathUtils.getPathWithPlacement(base.base).Commands or ())
        )
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Dogbone toolpath could not be prepared.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    if _bone_groups(expected_bones) != groups:
        dressup_error(
            "CAM Dogbone candidate numbering changed while applying the blacklist.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    enabled = _enabled_bone_count(base.base, expected_bones, blacklist)
    inserted = len(commands) - placed_base_count
    cutting = cutting_command_count(commands)
    if enabled <= 0 or inserted <= 0:
        dressup_error(
            "CAM Dogbone settings disable or duplicate every eligible relief.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "available_bone_locations_mm": _available_locations(groups),
                "available_locations_truncated": len(groups) > 128,
            },
        )
    if len(commands) > MAX_DRESSUP_COMMANDS:
        dressup_error(
            f"CAM Dogbone would generate {len(commands)} commands; the safety limit is "
            f"{MAX_DRESSUP_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    if cutting <= 0:
        dressup_error(
            "CAM Dogbone did not retain a usable cutting path.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    return PreparedDogboneDressup(
        base=base,
        style=style,
        native_style=_STYLE_NAMES[style],
        side=side,
        native_side=_SIDE_NAMES[side],
        incision=incision,
        native_incision=native_incision,
        custom_length_mm=custom,
        only_closed_profiles=spec.only_closed_profiles,
        disabled_locations_mm=requested_locations,
        bone_blacklist=blacklist,
        bone_groups=groups,
        candidate_bone_count=len(expected_bones),
        enabled_bone_count=enabled,
        inserted_command_count=inserted,
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=command_path_sha256(
            commands,
            "CAM Dogbone dress-up",
        ),
    )


def create_dogbone_dressup(
    document: Any,
    *,
    prepared: PreparedDogboneDressup,
) -> NativeMutationDraft:
    """Create and configure one Dogbone replacement in the owned transaction."""

    if not isinstance(prepared, PreparedDogboneDressup):
        raise TypeError("prepared must be a PreparedDogboneDressup")
    assert_dressup_preflight_current(document, prepared.base)
    base = prepared.base
    try:
        import Path.Dressup.Gui.DogboneII as DogboneGui

        operation = DogboneGui.CreateInTransaction(
            base.base,
            hide_base=False,
        )
        operation.Label = base.label
        operation.Style = prepared.native_style
        operation.Side = prepared.native_side
        operation.Incision = prepared.native_incision
        operation.Custom = prepared.custom_length_mm
        operation.OnlyClosedProfiles = prepared.only_closed_profiles
        operation.BoneBlacklist = list(prepared.bone_blacklist)
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Dogbone factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "operation": operation},
        recompute_targets=(operation,),
        created=(object_identity(operation),),
        changed=(object_identity(base.job),),
        replaced=(object_identity(base.base),),
    )


def verify_created_dogbone_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact relief generation and replacement History state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedDogboneDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Dogbone dress-up")
    base = prepared.base

    import Path.Dressup.DogboneII as Dogbone
    import Path.Dressup.Gui.DogboneII as DogboneGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=Dogbone.Proxy,
        view_proxy_type=DogboneGui.ViewProviderDressup,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    expected_center = tuple(round(float(value), 9) for value in base.job.Path.Center)
    actual_center = tuple(round(float(value), 9) for value in operation.Path.Center)
    actual_groups = _bone_groups(tuple(operation.Proxy.bones or ()))
    if (
        str(operation.Style) != prepared.native_style
        or str(operation.Side) != prepared.native_side
        or str(operation.Incision) != prepared.native_incision
        or round(float(operation.Custom.Value), 9) != prepared.custom_length_mm
        or bool(operation.OnlyClosedProfiles) is not prepared.only_closed_profiles
        or tuple(int(index) for index in operation.BoneBlacklist)
        != prepared.bone_blacklist
        or actual_groups != prepared.bone_groups
        or actual_center != expected_center
    ):
        dressup_error(
            "The created CAM Dogbone did not retain its exact relief settings or "
            "corner catalog.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "dogbone_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "style": prepared.style,
        "side": prepared.side,
        "incision": prepared.incision,
        "custom_length_mm": prepared.custom_length_mm,
        "only_closed_profiles": prepared.only_closed_profiles,
        "bone_group_count": len(prepared.bone_groups),
        "candidate_bone_count": prepared.candidate_bone_count,
        "disabled_bone_group_count": len(prepared.disabled_locations_mm),
        "enabled_bone_count": prepared.enabled_bone_count,
        "inserted_command_count": prepared.inserted_command_count,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_center_mm": list(actual_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
