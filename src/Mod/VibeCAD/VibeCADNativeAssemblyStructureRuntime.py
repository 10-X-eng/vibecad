# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly structure operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyComponents import (
    AssemblySourceRef,
    CreatePartSpec,
    InsertComponentSpec,
    NativeAssemblyComponentError,
    create_part,
    insert_component,
    preflight_create_part,
    preflight_insert_component,
    verify_created_part,
    verify_inserted_component,
)
from VibeCADNativeAssemblyBom import (
    AssemblyBomCreateSpec,
    NativeAssemblyBomError,
    create_assembly_bom,
    preflight_create_assembly_bom,
    verify_created_assembly_bom,
)
from VibeCADNativeAssemblyStructure import (
    AssemblyCreateSpec,
    NativeAssemblyStructureError,
    create_assembly,
    preflight_create_assembly,
    verify_created_assembly,
)
from VibeCADNativeAssemblySolve import (
    AssemblySolveSpec,
    NativeAssemblySolveError,
    apply_assembly_solve,
    preflight_assembly_solve,
    verify_assembly_solve,
)
from VibeCADNativeAssemblySimulation import (
    AssemblySimulationCreateSpec,
    AssemblySimulationMotionSpec,
    NativeAssemblySimulationError,
    create_assembly_simulation,
    preflight_create_assembly_simulation,
    verify_created_assembly_simulation,
)
from VibeCADNativeAssemblyView import (
    AssemblyViewCreateSpec,
    AssemblyViewMoveSpec,
    NativeAssemblyViewError,
    create_assembly_view,
    preflight_create_assembly_view,
    verify_created_assembly_view,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeDesignResults import placement_from_mapping
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeAssemblyStructureError(
            "An Assembly label must contain 1 to 160 characters."
        )
    return result


def _expected_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
        raise NativeAssemblyStructureError(
            "expected_assembly_count must be an integer from 0 through 10000."
        )
    return value


def _expected_component_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000:
        raise NativeAssemblyComponentError(
            "expected_component_count must be an integer from 0 through 100000."
        )
    return value


def _expected_joint_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 256:
        raise NativeAssemblySolveError(
            f"{field} must be an integer from 0 through 256."
        )
    return value


def _solver_state_sha256(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeAssemblySolveError(
            "expected_solver_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _view_state_sha256(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeAssemblyViewError(
            "expected_view_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyComponentError(
            f"{field} must be one exact object reference."
        )
    return NativeObjectRef(document_uid, str(value.get("object_name") or ""))


def _source_ref(value: Any) -> AssemblySourceRef:
    required = {"document_uid", "document_name", "object_name", "object_id"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAssemblyComponentError(
            "source must be one exact source reference from current Assemble state."
        )
    object_id = value["object_id"]
    if (
        isinstance(object_id, bool)
        or not isinstance(object_id, int)
        or not 1 <= object_id <= 2_147_483_647
    ):
        raise NativeAssemblyComponentError("source.object_id is invalid.")
    parts = {
        name: str(value[name] or "").strip()
        for name in ("document_uid", "document_name", "object_name")
    }
    if any(not item or len(item) > 128 for item in parts.values()):
        raise NativeAssemblyComponentError("source identity text is invalid.")
    return AssemblySourceRef(
        parts["document_uid"],
        parts["document_name"],
        parts["object_name"],
        object_id,
    )


def _bom_object_ref(document_uid: str, value: Any) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyBomError(
            "assembly must be one exact object reference."
        )
    try:
        return NativeObjectRef(document_uid, str(value.get("object_name") or ""))
    except Exception as exc:
        raise NativeAssemblyBomError(
            "assembly must be one exact object reference."
        ) from exc


def _bom_columns(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise NativeAssemblyBomError("columns must be one ordered list.")
    return tuple(value)


def _placement(value: Any) -> Any:
    try:
        return placement_from_mapping(value)
    except (NativeModelError, KeyError, TypeError, ValueError) as exc:
        raise NativeAssemblyComponentError(
            "placement must contain a finite origin and non-zero axis rotation."
        ) from exc


def _view_placement(value: Any) -> Any:
    try:
        return placement_from_mapping(value)
    except (NativeModelError, KeyError, TypeError, ValueError) as exc:
        raise NativeAssemblyViewError(
            "A normal exploded-view transform must contain a finite origin and a finite rotation with a non-zero axis."
        ) from exc


def _view_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyViewError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _view_moves(document_uid: str, value: Any) -> tuple[AssemblyViewMoveSpec, ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 256:
        raise NativeAssemblyViewError(
            "moves must contain 1 through 256 ordered exploded-view moves."
        )
    result = []
    total_targets = 0
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise NativeAssemblyViewError(
                f"moves[{index}] must be one exact move object."
            )
        kind = str(raw.get("kind") or "")
        required = (
            {"kind", "targets", "transform"}
            if kind == "normal"
            else {"kind", "targets", "radial_distance_mm"}
            if kind == "radial"
            else set()
        )
        if not required or set(raw) != required:
            raise NativeAssemblyViewError(
                f"moves[{index}] must be exactly one normal or radial move."
            )
        targets = raw["targets"]
        if not isinstance(targets, (list, tuple)) or not 1 <= len(targets) <= 256:
            raise NativeAssemblyViewError(
                f"moves[{index}].targets must contain 1 through 256 exact object references."
            )
        target_refs = tuple(
            _object_ref(document_uid, target, f"moves[{index}].targets")
            for target in targets
        )
        if len({target.object_name for target in target_refs}) != len(target_refs):
            raise NativeAssemblyViewError(
                f"moves[{index}].targets contains a duplicate object."
            )
        total_targets += len(target_refs)
        if total_targets > 16_384:
            raise NativeAssemblyViewError(
                "moves may contain at most 16384 target references."
            )
        if kind == "normal":
            result.append(
                AssemblyViewMoveSpec(
                    kind="normal",
                    target_refs=target_refs,
                    movement_transform=_view_placement(raw["transform"]),
                )
            )
            continue
        distance = raw["radial_distance_mm"]
        if (
            isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or not 0.0 < float(distance) <= 1_000_000.0
        ):
            raise NativeAssemblyViewError(
                f"moves[{index}].radial_distance_mm must be greater than zero and at most 1000000."
            )
        result.append(
            AssemblyViewMoveSpec(
                kind="radial",
                target_refs=target_refs,
                radial_distance_mm=float(distance),
            )
        )
    return tuple(result)


def _simulation_state_sha256(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeAssemblySimulationError(
            "expected_simulation_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _simulation_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblySimulationError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _simulation_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeAssemblySimulationError(f"{field} must be a finite number.")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise NativeAssemblySimulationError(f"{field} must be a finite number.")
    return result


def _simulation_object_ref(
    document_uid: str,
    value: Any,
    field: str,
) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblySimulationError(
            f"{field} must be one exact object reference."
        )
    try:
        return NativeObjectRef(document_uid, str(value.get("object_name") or ""))
    except Exception as exc:
        raise NativeAssemblySimulationError(str(exc)) from exc


def _simulation_motions(
    document_uid: str,
    value: Any,
) -> tuple[AssemblySimulationMotionSpec, ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 256:
        raise NativeAssemblySimulationError(
            "motions must contain 1 through 256 ordered Assembly motions."
        )
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "joint",
            "motion_type",
            "formula",
        }:
            raise NativeAssemblySimulationError(
                f"motions[{index}] must contain exactly joint, motion_type, and formula."
            )
        motion_type = raw["motion_type"]
        formula = raw["formula"]
        if motion_type not in {"angular", "linear"}:
            raise NativeAssemblySimulationError(
                f"motions[{index}].motion_type must be angular or linear."
            )
        if not isinstance(formula, str) or not 1 <= len(formula) <= 512:
            raise NativeAssemblySimulationError(
                f"motions[{index}].formula must contain 1 through 512 characters."
            )
        result.append(
            AssemblySimulationMotionSpec(
                joint_ref=_simulation_object_ref(
                    document_uid,
                    raw["joint"],
                    f"motions[{index}].joint",
                ),
                motion_type=motion_type,
                formula=formula,
            )
        )
    return tuple(result)


class NativeAssemblyStructureRuntime:
    """Execute only structure operations from one frozen Assemble turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _parent_ref(self, value: Any) -> NativeObjectRef | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeAssemblyStructureError(
                "parent_assembly must be null or one exact Assembly reference."
            )
        return NativeObjectRef(
            self._context.document_uid,
            str(value.get("object_name") or ""),
        )

    def mutate_structure(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create_assembly": frozenset(
                    {
                        "label",
                        "parent_assembly",
                        "expected_assembly_count",
                    }
                ),
                "insert_component": frozenset(
                    {
                        "assembly",
                        "source",
                        "label",
                        "placement",
                        "rigid",
                        "expected_component_count",
                    }
                ),
                "create_part": frozenset(
                    {
                        "assembly",
                        "label",
                        "placement",
                        "expected_component_count",
                    }
                ),
                "solve_assembly": frozenset(
                    {
                        "assembly",
                        "expected_solver_state_sha256",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                    }
                ),
                "create_view": frozenset(
                    {
                        "assembly",
                        "label",
                        "parts_as_single_solid",
                        "moves",
                        "expected_view_state_sha256",
                        "expected_component_count",
                        "expected_target_count",
                        "expected_view_count",
                    }
                ),
                "create_simulation": frozenset(
                    {
                        "assembly",
                        "label",
                        "time_start_seconds",
                        "time_end_seconds",
                        "output_time_step_seconds",
                        "global_error_tolerance",
                        "frames_per_second",
                        "motions",
                        "expected_simulation_state_sha256",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_eligible_joint_count",
                        "expected_simulation_count",
                    }
                ),
                "create_bom": frozenset(
                    {
                        "assembly",
                        "label",
                        "columns",
                        "detail_subassemblies",
                        "detail_parts",
                        "only_parts",
                        "expected_bom_state_sha256",
                        "expected_component_count",
                        "expected_bom_count",
                    }
                ),
            },
        )
        if operation == "create_assembly":
            spec = AssemblyCreateSpec(
                label=_label(values["label"]),
                parent_ref=self._parent_ref(values["parent_assembly"]),
                expected_assembly_count=_expected_count(
                    values["expected_assembly_count"]
                ),
            )
            self._context.guard()
            preflight_create_assembly(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly",
                mutate=lambda document: create_assembly(document, spec),
                verify=verify_created_assembly,
            )
        if operation == "create_bom":
            bom_spec = AssemblyBomCreateSpec(
                assembly_ref=_bom_object_ref(
                    self._context.document_uid,
                    values["assembly"],
                ),
                label=values["label"],
                columns=_bom_columns(values["columns"]),
                detail_subassemblies=values["detail_subassemblies"],
                detail_parts=values["detail_parts"],
                only_parts=values["only_parts"],
                expected_bom_state_sha256=values[
                    "expected_bom_state_sha256"
                ],
                expected_component_count=values["expected_component_count"],
                expected_bom_count=values["expected_bom_count"],
            )
            self._context.guard()
            preflight_create_assembly_bom(self._context.document, bom_spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly BOM",
                mutate=lambda document: create_assembly_bom(document, bom_spec),
                verify=verify_created_assembly_bom,
            )
        assembly_ref = _object_ref(
            self._context.document_uid,
            values["assembly"],
            "assembly",
        )
        expected_components = _expected_component_count(
            values["expected_component_count"]
        )
        if operation == "insert_component":
            rigid = values["rigid"]
            if rigid is not None and not isinstance(rigid, bool):
                raise NativeAssemblyComponentError(
                    "rigid must be true, false, or null."
                )
            insert_spec = InsertComponentSpec(
                assembly_ref=assembly_ref,
                source_ref=_source_ref(values["source"]),
                label=_label(values["label"]),
                placement=_placement(values["placement"]),
                rigid=rigid,
                expected_component_count=expected_components,
            )
            self._context.guard()
            preflight_insert_component(self._context.document, insert_spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Insert Native Assembly Component",
                mutate=lambda document: insert_component(document, insert_spec),
                verify=verify_inserted_component,
            )
        if operation == "create_part":
            part_spec = CreatePartSpec(
                assembly_ref=assembly_ref,
                label=_label(values["label"]),
                placement=_placement(values["placement"]),
                expected_component_count=expected_components,
            )
            self._context.guard()
            preflight_create_part(self._context.document, part_spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Part",
                mutate=lambda document: create_part(document, part_spec),
                verify=verify_created_part,
            )
        if operation == "solve_assembly":
            solve_spec = AssemblySolveSpec(
                assembly_ref=assembly_ref,
                expected_solver_state_sha256=_solver_state_sha256(
                    values["expected_solver_state_sha256"]
                ),
                expected_component_count=expected_components,
                expected_grounded_count=_expected_joint_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                ),
                expected_joint_count=_expected_joint_count(
                    values["expected_joint_count"],
                    "expected_joint_count",
                ),
            )
            self._context.guard()
            preflight_assembly_solve(self._context.document, solve_spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Solve Native Assembly",
                mutate=lambda document: apply_assembly_solve(document, solve_spec),
                verify=verify_assembly_solve,
            )
        if operation == "create_view":
            parts_as_single_solid = values["parts_as_single_solid"]
            if type(parts_as_single_solid) is not bool:
                raise NativeAssemblyViewError(
                    "parts_as_single_solid must be true or false."
                )
            view_spec = AssemblyViewCreateSpec(
                assembly_ref=assembly_ref,
                label=_label(values["label"]),
                parts_as_single_solid=parts_as_single_solid,
                moves=_view_moves(
                    self._context.document_uid,
                    values["moves"],
                ),
                expected_view_state_sha256=_view_state_sha256(
                    values["expected_view_state_sha256"]
                ),
                expected_component_count=_view_count(
                    values["expected_component_count"],
                    "expected_component_count",
                    100_000,
                ),
                expected_target_count=_view_count(
                    values["expected_target_count"],
                    "expected_target_count",
                    4_096,
                ),
                expected_view_count=_view_count(
                    values["expected_view_count"],
                    "expected_view_count",
                    1_024,
                ),
            )
            self._context.guard()
            preflight_create_assembly_view(self._context.document, view_spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Exploded View",
                mutate=lambda document: create_assembly_view(document, view_spec),
                verify=verify_created_assembly_view,
            )
        if operation == "create_simulation":
            frames_per_second = values["frames_per_second"]
            if type(frames_per_second) is not int:
                raise NativeAssemblySimulationError(
                    "frames_per_second must be an integer."
                )
            simulation_spec = AssemblySimulationCreateSpec(
                assembly_ref=assembly_ref,
                label=_label(values["label"]),
                time_start_seconds=_simulation_number(
                    values["time_start_seconds"],
                    "time_start_seconds",
                ),
                time_end_seconds=_simulation_number(
                    values["time_end_seconds"],
                    "time_end_seconds",
                ),
                output_time_step_seconds=_simulation_number(
                    values["output_time_step_seconds"],
                    "output_time_step_seconds",
                ),
                global_error_tolerance=_simulation_number(
                    values["global_error_tolerance"],
                    "global_error_tolerance",
                ),
                frames_per_second=frames_per_second,
                motions=_simulation_motions(
                    self._context.document_uid,
                    values["motions"],
                ),
                expected_simulation_state_sha256=_simulation_state_sha256(
                    values["expected_simulation_state_sha256"]
                ),
                expected_component_count=_simulation_count(
                    values["expected_component_count"],
                    "expected_component_count",
                    100_000,
                ),
                expected_grounded_count=_simulation_count(
                    values["expected_grounded_count"],
                    "expected_grounded_count",
                    256,
                ),
                expected_eligible_joint_count=_simulation_count(
                    values["expected_eligible_joint_count"],
                    "expected_eligible_joint_count",
                    256,
                ),
                expected_simulation_count=_simulation_count(
                    values["expected_simulation_count"],
                    "expected_simulation_count",
                    1_024,
                ),
            )
            self._context.guard()
            preflight_create_assembly_simulation(
                self._context.document,
                simulation_spec,
            )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Simulation",
                mutate=lambda document: create_assembly_simulation(
                    document,
                    simulation_spec,
                ),
                verify=verify_created_assembly_simulation,
            )
        raise NativeAssemblyStructureError(
            "The Assembly structure operation is not implemented."
        )
