# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly joint operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyGrounding import (
    GroundingSpec,
    GroundingTargetSpec,
    MAX_GROUNDING_TARGETS,
    NativeAssemblyGroundingError,
    apply_grounding,
    prepared_grounding_result,
    preflight_grounding,
    verify_grounding,
)
from VibeCADNativeAssemblyMotionJointRuntime import (
    MOTION_JOINT_OPERATIONS,
    execute_motion_joint,
)
from VibeCADNativeAssemblyJointIntent import expand_joint_intent
from VibeCADNativeAssemblyRelationJointRuntime import (
    RELATION_JOINT_OPERATIONS,
    execute_relation_joint,
)
from VibeCADNativeAssemblyJointSchema import (
    _legacy_joint_capability_definition,
    assembly_ground_capability_definition,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


_INTENT_VARIANTS = {
    variant.operation: (
        frozenset(variant.parameters.get("required", ())),
        frozenset(variant.parameters["properties"]),
    )
    for definition in (
        assembly_ground_capability_definition(),
        _legacy_joint_capability_definition(),
    )
    for variant in definition.variants
}

_FOCUSED_JOINT_OPERATIONS = {
    "joint_type": {
        "fixed": "create_fixed",
        "revolute": "create_revolute",
        "cylindrical": "create_cylindrical",
        "slider": "create_slider",
        "ball": "create_ball",
    },
    "relation_type": {
        "distance": "create_distance",
        "parallel": "create_parallel",
        "perpendicular": "create_perpendicular",
        "angle": "create_angle",
    },
    "coupling_type": {
        "rack_pinion": "create_rack_pinion",
        "screw": "create_screw",
        "belt": "create_belt",
        "gears": "create_gears",
    },
}

_GENERIC_COUPLING_SIDES = {
    "first_joint": "first_joint",
    "first_component": "first_component",
    "second_joint": "second_joint",
    "second_component": "second_component",
}
_PROVIDER_COUPLING_SIDES = {
    "rack_pinion": {
        "slider_joint": "first_joint",
        "rack_component": "first_component",
        "revolute_joint": "second_joint",
        "pinion_component": "second_component",
    },
    "screw": {
        "slider_joint": "first_joint",
        "slider_component": "first_component",
        "revolute_joint": "second_joint",
        "revolute_component": "second_component",
    },
    "belt": _GENERIC_COUPLING_SIDES,
    "gears": _GENERIC_COUPLING_SIDES,
}

_PROVIDER_OPERATIONS = {
    "distance": (
        "create_distance",
        frozenset({"first", "second", "distance_mm"}),
        frozenset({"first", "second", "distance_mm", "reverse", "label"}),
    ),
    "parallel": (
        "create_parallel",
        frozenset({"first", "second"}),
        frozenset({"first", "second", "reverse", "label"}),
    ),
    "perpendicular": (
        "create_perpendicular",
        frozenset({"first", "second"}),
        frozenset({"first", "second", "label"}),
    ),
    "angle": (
        "create_angle",
        frozenset({"first", "second", "angle_degrees"}),
        frozenset({"first", "second", "angle_degrees", "label"}),
    ),
    "rack_pinion": (
        "create_rack_pinion",
        frozenset(_PROVIDER_COUPLING_SIDES["rack_pinion"])
        | frozenset({"pinion_pitch_radius_mm"}),
        frozenset(_PROVIDER_COUPLING_SIDES["rack_pinion"])
        | frozenset({"pinion_pitch_radius_mm", "label"}),
    ),
    "screw": (
        "create_screw",
        frozenset(_PROVIDER_COUPLING_SIDES["screw"]) | frozenset({"lead_mm"}),
        frozenset(_PROVIDER_COUPLING_SIDES["screw"])
        | frozenset({"lead_mm", "label"}),
    ),
    "belt": (
        "create_belt",
        frozenset(_PROVIDER_COUPLING_SIDES["belt"])
        | frozenset({"first_pulley_radius_mm", "second_pulley_radius_mm"}),
        frozenset(_PROVIDER_COUPLING_SIDES["belt"])
        | frozenset(
            {"first_pulley_radius_mm", "second_pulley_radius_mm", "label"}
        ),
    ),
    "gears": (
        "create_gears",
        frozenset(_PROVIDER_COUPLING_SIDES["gears"])
        | frozenset({"first_pitch_radius_mm", "second_pitch_radius_mm"}),
        frozenset(_PROVIDER_COUPLING_SIDES["gears"])
        | frozenset(
            {"first_pitch_radius_mm", "second_pitch_radius_mm", "label"}
        ),
    ),
}

_PROVIDER_VALUE_FIELDS = {
    "rack_pinion": {"pinion_pitch_radius_mm": "pitch_radius_mm"},
    "screw": {"lead_mm": "thread_pitch_mm"},
    "belt": {
        "first_pulley_radius_mm": "radius1_mm",
        "second_pulley_radius_mm": "radius2_mm",
    },
    "gears": {
        "first_pitch_radius_mm": "radius1_mm",
        "second_pitch_radius_mm": "radius2_mm",
    },
}

_PROVIDER_RELATIONS = {
    "distance": "create_distance",
    "parallel": "create_parallel",
    "perpendicular": "create_perpendicular",
    "angle": "create_angle",
}


def _legacy_endpoint(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not {
        "component",
        "connector_type",
        "connector",
    } <= set(value) or not set(value) <= {
        "component",
        "connector_type",
        "connector",
        "offset",
    }:
        raise NativeArgumentError(f"{field} must be an assembly.connectors endpoint.")
    connector_type = value["connector_type"]
    if connector_type not in {"element", "interface"}:
        raise NativeArgumentError(f"{field}.connector_type is unavailable.")
    result = {"component": value["component"], connector_type: value["connector"]}
    if "offset" in value:
        result["offset"] = value["offset"]
    return result


def _intent_values(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeArgumentError("Native capability arguments must be an object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "").strip()
    provider_contract = _PROVIDER_OPERATIONS.get(operation)
    if provider_contract is not None:
        legacy_operation, required, allowed = provider_contract
        if not required <= set(values) or not set(values) <= allowed:
            raise NativeArgumentError(
                "Native capability arguments do not match the selected operation."
            )
        if operation in {"distance", "parallel", "perpendicular", "angle"}:
            for endpoint_field in ("first", "second"):
                values[endpoint_field] = _legacy_endpoint(
                    values[endpoint_field], endpoint_field
                )
        if operation in {"rack_pinion", "screw", "belt", "gears"}:
            for provider_field, runtime_field in _PROVIDER_COUPLING_SIDES[
                operation
            ].items():
                reference = values.pop(provider_field)
                if (
                    not isinstance(reference, Mapping)
                    or set(reference) != {"object_name"}
                    or not isinstance(reference["object_name"], str)
                ):
                    raise NativeArgumentError(
                        f"{provider_field} must be one exact object reference."
                    )
                values[runtime_field] = reference["object_name"]
        for provider_field, runtime_field in _PROVIDER_VALUE_FIELDS.get(
            operation, {}
        ).items():
            values[runtime_field] = values.pop(provider_field)
        return legacy_operation, values
    if operation == "create":
        relation = values.pop("relation", None)
        if relation is not None:
            operation = (
                _PROVIDER_RELATIONS.get(relation, "")
                if isinstance(relation, str)
                else ""
            )
            if not operation:
                raise NativeArgumentError("relation is unavailable.")
        else:
            type_fields = [
                field for field in _FOCUSED_JOINT_OPERATIONS if field in values
            ]
            if len(type_fields) != 1:
                raise NativeArgumentError(
                    "Joint creation requires one joint_type, relation_type, or "
                    "coupling_type."
                )
            type_field = type_fields[0]
            type_value = values.pop(type_field)
            operation = _FOCUSED_JOINT_OPERATIONS[type_field].get(type_value, "")
            if not operation:
                raise NativeArgumentError(f"{type_field} is unavailable.")
        for endpoint_field in ("first", "second"):
            if endpoint_field in values:
                values[endpoint_field] = _legacy_endpoint(
                    values[endpoint_field], endpoint_field
                )
        if operation in {"create_fixed", "create_ball"} and values.get("limits") == {}:
            values.pop("limits")
    contract = _INTENT_VARIANTS.get(operation)
    if contract is None:
        raise NativeArgumentError("Native capability operation is unavailable.")
    required, allowed = contract
    if not required <= set(values) or not set(values) <= allowed:
        raise NativeArgumentError(
            "Native capability arguments do not match the selected operation."
        )
    return operation, values


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000:
        raise NativeAssemblyGroundingError(
            f"{field} must be an integer from 0 through 100000."
        )
    return value


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAssemblyGroundingError(f"{field} must be true or false.")
    return value


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyGroundingError(
            f"{field} must be one exact current-document object reference."
        )
    return NativeObjectRef(document_uid, str(value.get("object_name") or ""))


def _targets(document_uid: str, value: Any) -> tuple[GroundingTargetSpec, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_GROUNDING_TARGETS
    ):
        raise NativeAssemblyGroundingError(
            f"targets must contain 1 to {MAX_GROUNDING_TARGETS} exact components."
        )
    result = []
    names: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "component",
            "expected_grounded",
        }:
            raise NativeAssemblyGroundingError(
                f"targets[{index}] must contain component and expected_grounded."
            )
        component = _object_ref(
            document_uid,
            item["component"],
            f"targets[{index}].component",
        )
        if component.object_name in names:
            raise NativeAssemblyGroundingError(
                "targets cannot repeat an exact component."
            )
        names.add(component.object_name)
        result.append(
            GroundingTargetSpec(
                component_ref=component,
                expected_grounded=_bool(
                    item["expected_grounded"],
                    f"targets[{index}].expected_grounded",
                ),
            )
        )
    return tuple(result)


class NativeAssemblyJointRuntime:
    """Execute only joint operations authorized for one frozen Assemble turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_joint(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, intent = _intent_values(arguments)
        self._context.guard()
        values = expand_joint_intent(
            self._context.document,
            self._context.document_uid,
            operation,
            intent,
        )
        if operation in MOTION_JOINT_OPERATIONS:
            return execute_motion_joint(
                self._context,
                ticket,
                operation,
                values,
            )
        if operation in RELATION_JOINT_OPERATIONS:
            return execute_relation_joint(
                self._context,
                ticket,
                operation,
                values,
            )
        spec = GroundingSpec(
            assembly_ref=_object_ref(
                self._context.document_uid,
                values["assembly"],
                "assembly",
            ),
            targets=_targets(self._context.document_uid, values["targets"]),
            grounded=_bool(values["grounded"], "grounded"),
            expected_component_count=_count(
                values["expected_component_count"],
                "expected_component_count",
            ),
            expected_grounded_count=_count(
                values["expected_grounded_count"],
                "expected_grounded_count",
            ),
        )
        self._context.guard()
        prepared = preflight_grounding(self._context.document, spec)
        if all(
            (joint is not None) is spec.grounded
            for joint in prepared.existing_joints
        ):
            return prepared_grounding_result(prepared, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=(
                "Ground Native Assembly Components"
                if spec.grounded
                else "Unground Native Assembly Components"
            ),
            mutate=lambda document: apply_grounding(document, spec),
            verify=verify_grounding,
        )
