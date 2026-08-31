# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Model structure and reusable Sketch setup."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADEditState import active_edit_object
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
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)
from VibeCADSurfaceAuthority import enter_edit_mode


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
        normalized = dict(arguments)
        optional_parent = {
            "new_component": "parent_component",
            "new_body": "component",
            "separate": "destination_component",
        }.get(str(normalized.get("operation") or ""))
        if optional_parent is not None:
            normalized.setdefault(optional_parent, None)
        operation, values = strict_variant_arguments(
            normalized,
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
        normalized_arguments = dict(arguments)
        if normalized_arguments.get("operation") == "create_on_base_plane":
            normalized_arguments.setdefault("offset_mm", 0.0)
        operation, values = strict_variant_arguments(
            normalized_arguments,
            {
                "create_on_base_plane": frozenset({"label", "plane", "offset_mm"}),
                "create_on_face": frozenset({"label", "target"}),
                "create_on_datum_plane": frozenset({"label", "target"}),
                "create_revolution": frozenset({"label", "axis"}),
            },
        )
        revolution_axes = {
            "X": (
                "XY",
                "H_Axis",
                False,
                {"axial": "x_mm", "radius": "y_mm >= 0", "axis": "y_mm = 0"},
            ),
            "Y": (
                "XY",
                "V_Axis",
                False,
                {"axial": "y_mm", "radius": "x_mm >= 0", "axis": "x_mm = 0"},
            ),
            "Z": (
                "XZ",
                "V_Axis",
                True,
                {"axial": "y_mm", "radius": "x_mm >= 0", "axis": "x_mm = 0"},
            ),
        }
        revolution_axis = None
        profile_coordinates = None
        profile_intent = None
        if operation == "create_revolution":
            global_axis = str(values["axis"])
            try:
                (
                    plane,
                    revolution_axis,
                    reverse_normal,
                    profile_coordinates,
                ) = revolution_axes[global_axis]
            except KeyError as exc:
                raise NativeModelError(
                    "A revolution Sketch axis must be X, Y, or Z."
                ) from exc
            support: Any = {
                "kind": "base_plane",
                "plane": plane,
                "offset_mm": 0.0,
                "reverse_normal": reverse_normal,
            }
            profile_intent = {
                "kind": "axisymmetric",
                "global_axis": global_axis,
                "sketch_axis": revolution_axis,
                **profile_coordinates,
            }
        elif operation == "create_on_base_plane":
            support = {
                "kind": "base_plane",
                "plane": str(values["plane"]),
                "offset_mm": float(values.get("offset_mm", 0.0)),
            }
        else:
            support = {
                "kind": (
                    "planar_face"
                    if operation == "create_on_face"
                    else "datum_plane"
                ),
                "target": values["target"],
            }
        label = _label(values["label"])
        if not isinstance(support, Mapping):
            raise NativeModelError("A reusable Sketch requires explicit support.")
        support = dict(support)
        if str(support.get("kind") or "") == "base_plane":
            support.setdefault("offset_mm", 0.0)
        if str(support.get("kind") or "") in {"datum_plane", "planar_face"}:
            target = support.get("target")
            if not isinstance(target, Mapping):
                raise NativeModelError("Attached Sketch support requires one exact target.")
            self._require_object(
                self._object_ref({"object_name": target.get("object_name")})
            )
        result = run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Create Native Reusable Sketch",
            mutate=lambda document: create_reusable_sketch(
                document,
                label=label,
                support=support,
                profile_intent=profile_intent,
            ),
            verify=verify_reusable_sketch,
        )
        if revolution_axis is not None:
            sketch = result["sketch"]
            result["revolution_axis"] = {
                "object_name": sketch["object_name"],
                "subelements": [revolution_axis],
            }
            result["global_axis"] = str(values["axis"])
            result["profile_coordinates"] = profile_coordinates
            result["profile_intent"] = profile_intent
        return result

    def open_sketch(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"open": frozenset({"sketch"})},
        )
        return self._open_sketch(values["sketch"], ticket=ticket)

    def _open_sketch(
        self,
        value: Any,
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        reference = self._object_ref(value)
        sketch = self._require_object(reference, "Sketcher::SketchObject")
        if active_edit_object() is not None or bool(self._context.edit_or_task_active()):
            raise NativeModelError("Finish the active task before opening a Sketch.")
        dispatch = self._context.document_thread_dispatch
        if dispatch is None:
            raise NativeModelError("Opening a Sketch requires the document thread.")

        authorization = self._context.state.authorize_mutation(ticket)
        if authorization.duplicate:
            return dict(authorization.prior_verified_result or {})
        self._context.state.begin_mutation_observation(ticket)
        try:
            document = self._context.document

            def activate() -> None:
                import FreeCADGui as Gui
                from PySide import QtCore, QtWidgets

                gui_document = Gui.activeDocument()
                if (
                    gui_document is None
                    or self._context.active_document() is not document
                ):
                    raise NativeModelError("The Sketch document is no longer active.")
                if not enter_edit_mode(gui_document, sketch.Name):
                    raise NativeModelError("Sketcher could not open the exact Sketch.")
                for _index in range(8):
                    Gui.updateGui()
                    QtWidgets.QApplication.processEvents(
                        QtCore.QEventLoop.AllEvents,
                        25,
                    )

            dispatch(activate)
            if self._context.active_document() is not document:
                raise NativeModelError("The Sketch document changed while opening it.")
            if active_edit_object() is not sketch:
                raise NativeModelError("The requested Sketch did not enter edit mode.")
            if str(self._context.active_surface_id() or "") != "sketch.edit":
                raise NativeModelError("Sketch editing did not become available.")
            result = {
                "operation": "open",
                "sketch": object_reference(sketch),
                "edit_mode": "open",
                "next_surface": "sketch.edit",
                "next_turn_required": True,
            }
            revision_after = self._context.state.commit_mutation_observation(ticket)
            changed = (
                (object_identity(sketch),)
                if revision_after > ticket.expected_revision
                else ()
            )
            completion = self._context.state.prepare_mutation_completion(
                ticket,
                result,
                changed=changed,
            )
            receipt = self._context.state.complete_prepared_mutation(completion)
        except Exception:
            self._context.state.cancel_mutation(ticket)
            raise
        return {**result, "receipt": receipt.summary()}

    def validate_sketch(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"validate_sketch": frozenset({"target"})},
        )
        target = self._object_ref(values["target"])
        self._context.guard()
        return sketch_readiness(self._context.document, target)
