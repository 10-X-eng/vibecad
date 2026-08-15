# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Model structure and reusable Sketch setup."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelDefinitions import (
    create_reusable_sketch,
    create_subshape_binder,
    verify_reusable_sketch,
    verify_subshape_binder,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeDesignSeparate import (
    create_design_separate,
    preflight_design_separate,
    prepare_design_separate,
    verify_design_separate,
)
from VibeCADNativeModelObjects import (
    create_body,
    create_component,
    create_design_clone,
    verify_body,
    verify_component,
    verify_design_clone,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchReadiness import sketch_readiness
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef
from VibeCADNativeTargets import resolve_object


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeModelError("A visible Model label must contain 1 to 160 characters.")
    return result


class NativeModelStructureRuntime:
    """Execute only structure capabilities from one frozen Model turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _object_ref(self, value: Any) -> NativeObjectRef:
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeModelError("An exact Model object target is invalid.")
        return NativeObjectRef(
            self._context.document_uid,
            str(value.get("object_name") or ""),
        )

    def _nullable_ref(self, value: Any) -> NativeObjectRef | None:
        return None if value is None else self._object_ref(value)

    def _require_object(
        self,
        reference: NativeObjectRef,
        *expected_types: str,
    ) -> Any:
        self._context.guard()
        return resolve_object(
            self._context.document,
            reference,
            expected_types=tuple(expected_types),
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
                "new_component": frozenset({"label", "parent_component"}),
                "new_body": frozenset({"label", "component"}),
                "sub_shape_binder": frozenset({"label", "references"}),
                "clone": frozenset({"source_body", "label", "output_body_label"}),
                "separate": frozenset(
                    {"label", "source", "destination_component"}
                ),
            },
        )
        if operation == "new_component":
            label = _label(values["label"])
            parent = self._nullable_ref(values["parent_component"])
            if parent is not None:
                self._require_object(parent, "PartDesign::Component")
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Component",
                mutate=lambda document: create_component(
                    document,
                    label=label,
                    parent_ref=parent,
                ),
                verify=verify_component,
            )
        if operation == "new_body":
            label = _label(values["label"])
            component = self._nullable_ref(values["component"])
            if component is not None:
                self._require_object(component, "PartDesign::Component")
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Body",
                mutate=lambda document: create_body(
                    document,
                    label=label,
                    component_ref=component,
                ),
                verify=verify_body,
            )
        if operation == "sub_shape_binder":
            label = _label(values["label"])
            references = self._binder_references(values["references"])
            for reference, _subelements in references:
                self._require_object(reference)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Design Reference",
                mutate=lambda document: create_subshape_binder(
                    document,
                    label=label,
                    references=references,
                ),
                verify=verify_subshape_binder,
            )
        if operation == "clone":
            source = self._object_ref(values["source_body"])
            source_body = self._require_object(source, "PartDesign::Body")
            source_shape = getattr(source_body, "Shape", None)
            if source_shape is None or source_shape.isNull() or not source_shape.isValid():
                raise NativeModelError(
                    "The exact source Body has no valid current History shape."
                )
            label = _label(values["label"])
            output_label = _label(values["output_body_label"])
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Design Clone",
                mutate=lambda document: create_design_clone(
                    document,
                    source_ref=source,
                    label=label,
                    output_body_label=output_label,
                ),
                verify=verify_design_clone,
            )
        label = _label(values["label"])
        spec = prepare_design_separate(
            self._context.document_uid,
            {
                "source": values["source"],
                "destination_component": values["destination_component"],
            },
        )
        self._context.guard()
        prepared = preflight_design_separate(self._context.document, spec)
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Design Separate",
            mutate=lambda document: create_design_separate(
                document,
                label=label,
                prepared=prepared,
            ),
            verify=verify_design_separate,
        )

    def _binder_references(
        self,
        value: Any,
    ) -> list[tuple[NativeObjectRef, list[str]]]:
        if not isinstance(value, list) or not 1 <= len(value) <= 32:
            raise NativeModelError("A Design reference requires 1 to 32 exact sources.")
        result = []
        seen = set()
        for item in value:
            if not isinstance(item, Mapping) or set(item) != {
                "object_name",
                "subelements",
            }:
                raise NativeModelError("A Design reference source is invalid.")
            subelements = item["subelements"]
            if not isinstance(subelements, list) or len(subelements) > 64:
                raise NativeModelError("A Design reference source has invalid subelements.")
            names = [str(name) for name in subelements]
            reference = self._object_ref({"object_name": item["object_name"]})
            key = (reference.object_name, tuple(names))
            if key in seen:
                raise NativeModelError("A Design reference repeats the same exact source.")
            seen.add(key)
            result.append((reference, names))
        return result

    def create_sketch(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"new_sketch": frozenset({"label", "support"})},
        )
        label = _label(values["label"])
        support = values["support"]
        if not isinstance(support, Mapping):
            raise NativeModelError("A reusable Sketch requires explicit support.")
        if str(support.get("kind") or "") in {"datum_plane", "planar_face"}:
            target = support.get("target")
            if not isinstance(target, Mapping):
                raise NativeModelError("Attached Sketch support requires one exact target.")
            self._require_object(
                self._object_ref({"object_name": target.get("object_name")})
            )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Reusable Sketch",
            mutate=lambda document: create_reusable_sketch(
                document,
                label=label,
                support=dict(support),
            ),
            verify=verify_reusable_sketch,
        )

    def validate_sketch(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"validate_sketch": frozenset({"target"})},
        )
        target = self._object_ref(values["target"])
        self._context.guard()
        return sketch_readiness(self._context.document, target)
