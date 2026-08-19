# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Design feature creation and application."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import NativeArgumentError, strict_variant_arguments
from VibeCADNativeDesignPrimitives import (
    create_design_primitive,
    primitive_argument_fields,
    primitive_native_parameters,
)
from VibeCADNativeDesignProfiles import (
    create_prepared_design_profile,
    preflight_design_profile,
    prepare_design_profile,
    profile_argument_fields,
)
from VibeCADNativeDesignResults import (
    placement_from_mapping,
    resolve_design_result,
    result_spec_from_mapping,
    verify_design_operation,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_OUTER_FIELDS = {
    "primitive": frozenset({"label", "placement", "result", "definition"}),
    "profile": frozenset({"label", "profile", "result", "definition"}),
}
_PRIMITIVE_BASE_FIELDS = frozenset({"label", "placement", "result"})
_PROFILE_BASE_FIELDS = frozenset({"label", "profile", "result"})


def _typed_definition(
    value: Any,
    fields: Mapping[str, frozenset[str]],
    base_fields: frozenset[str],
    *,
    helix_parameters: bool = False,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise NativeArgumentError("A Native feature definition must be an object.")
    nested = dict(value)
    kind = str(nested.pop("kind", "") or "").strip()
    nested_fields = {
        operation: frozenset(
            "parameters"
            if helix_parameters
            and operation == "design_helix"
            and name == "definition"
            else name
            for name in operation_fields - base_fields
        )
        for operation, operation_fields in fields.items()
    }
    operation, selected = strict_variant_arguments(
        {"operation": f"design_{kind}", **nested},
        nested_fields,
    )
    if helix_parameters and operation == "design_helix":
        selected["definition"] = selected.pop("parameters")
    return operation, selected


class NativeModelFeatureRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def profile_destination_component(
        self,
        profile: Mapping[str, Any],
    ) -> dict[str, str] | None:
        """Return the Component already owning a reusable profile, if any."""
        object_name = str(profile.get("object_name") or "").strip()
        source = self._context.document.getObject(object_name) if object_name else None
        current = source
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if str(getattr(current, "TypeId", "")) == "PartDesign::Component":
                return {"object_name": str(current.Name)}
            parent_getter = getattr(current, "getParentGeoFeatureGroup", None)
            current = parent_getter() if callable(parent_getter) else None
        return None

    def profile_global_axes(
        self,
        profile: Mapping[str, Any],
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Return the reusable Sketch's horizontal and vertical global directions."""
        object_name = str(profile.get("object_name") or "").strip()
        sketch = self._context.document.getObject(object_name) if object_name else None
        if sketch is None:
            raise NativeModelError(f"Sketch '{object_name}' does not exist.")
        derived = getattr(sketch, "isDerivedFrom", None)
        if not callable(derived) or not derived("Sketcher::SketchObject"):
            raise NativeModelError(f"'{object_name}' is not a Sketch.")
        placement_getter = getattr(sketch, "getGlobalPlacement", None)
        placement = placement_getter() if callable(placement_getter) else None
        rotation = getattr(placement, "Rotation", None)
        matrix_getter = getattr(rotation, "toMatrix", None)
        if not callable(matrix_getter):
            raise NativeModelError(f"Sketch '{object_name}' has no global placement.")
        matrix = matrix_getter()
        try:
            horizontal = tuple(
                float(getattr(matrix, name))
                for name in ("A11", "A21", "A31")
            )
            vertical = tuple(
                float(getattr(matrix, name))
                for name in ("A12", "A22", "A32")
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise NativeModelError(
                f"Sketch '{object_name}' has an invalid global placement."
            ) from exc
        return horizontal, vertical

    def mutate_feature(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        outer_operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        primitive_fields = primitive_argument_fields()
        profile_fields = profile_argument_fields()
        if outer_operation == "profile":
            operation, specific = _typed_definition(
                values["definition"],
                profile_fields,
                _PROFILE_BASE_FIELDS,
                helix_parameters=True,
            )
            operation_values = {
                "label": values["label"],
                "profile": values["profile"],
                "result": values["result"],
                **specific,
            }
        else:
            operation, specific = _typed_definition(
                values["definition"],
                primitive_fields,
                _PRIMITIVE_BASE_FIELDS,
            )
            operation_values = {
                "label": values["label"],
                "placement": values["placement"],
                "result": values["result"],
                **specific,
            }
        label = str(operation_values["label"] or "").strip()
        if not label or len(label) > 160:
            raise NativeModelError("A visible Model label must contain 1 to 160 characters.")
        result_spec = result_spec_from_mapping(
            self._context.document_uid,
            operation_values["result"],
        )
        if operation in profile_fields:
            prepared_profile = prepare_design_profile(
                self._context.document_uid,
                operation,
                operation_values,
            )
            placement = None
            native_parameters = None
        else:
            prepared_profile = None
            placement = placement_from_mapping(operation_values["placement"])
            native_parameters = primitive_native_parameters(operation, operation_values)
        self._context.guard()
        resolve_design_result(self._context.document, result_spec)
        if prepared_profile is not None:
            preflight_design_profile(
                self._context.document,
                prepared_profile,
                result_spec,
            )

        def mutate(document: Any):
            if prepared_profile is not None:
                return create_prepared_design_profile(
                    document,
                    prepared=prepared_profile,
                    label=label,
                    result_spec=result_spec,
                )
            if placement is None or native_parameters is None:
                raise AssertionError("A primitive Design operation was not prepared.")
            return create_design_primitive(
                document,
                operation=operation,
                label=label,
                native_parameters=native_parameters,
                placement=placement,
                result_spec=result_spec,
            )

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=(
                prepared_profile.transaction_name
                if prepared_profile is not None
                else "Create Native Design Primitive"
            ),
            mutate=mutate,
            verify=verify_design_operation,
        )
