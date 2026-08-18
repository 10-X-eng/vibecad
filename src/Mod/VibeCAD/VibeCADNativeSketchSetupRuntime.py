# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound execution for reusable Sketch setup operations."""

from __future__ import annotations

import re
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeModelDefinitions import (
    configure_reusable_sketch_support,
    reusable_sketch_base_plane_placement,
)
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchRevision import sketch_revision
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_CONSTRAINT_EXPRESSION = re.compile(r"^\.?Constraints\[([0-9]+)\]$")


class NativeSketchSetupError(NativeMutationError):
    def __init__(self, message: str) -> None:
        super().__init__("NATIVE_SKETCH_SETUP_INVALID", message)


def _placement_state(sketch: Any) -> dict[str, list[float]]:
    placement = sketch.Placement
    return {
        "base_mm": [
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
        ],
        "rotation_xyzw": [float(value) for value in placement.Rotation.Q],
    }


def _support_state(sketch: Any) -> list[dict[str, Any]]:
    return [
        {
            "object": object_reference(obj),
            "subelements": [str(name) for name in list(subelements or [])],
        }
        for obj, subelements in list(getattr(sketch, "AttachmentSupport", []) or [])
    ]


def _require_reusable_sketch(document: Any, reference: NativeObjectRef) -> Any:
    import PartDesign

    sketch = resolve_object(
        document,
        reference,
        expected_types=("Sketcher::SketchObject",),
    )
    if sketch.getParentGeoFeatureGroup() is not None:
        raise NativeSketchSetupError(
            "Sketch setup accepts a reusable document-level Sketch, not Body-owned history."
        )
    try:
        PartDesign.validateDesign(sketch)
    except Exception as exc:
        raise NativeSketchSetupError(
            "The exact Sketch is not a valid reusable Design definition."
        ) from exc
    return sketch


def _source_expressions(source: Any) -> tuple[tuple[int, str], ...]:
    result = []
    for raw_path, raw_expression in list(getattr(source, "ExpressionEngine", []) or []):
        path = str(raw_path)
        match = _CONSTRAINT_EXPRESSION.fullmatch(path)
        if match is None:
            raise NativeSketchSetupError(
                "Merge and Mirror require Sketch expressions to target constraints only."
            )
        index = int(match.group(1))
        if not 0 <= index < int(source.ConstraintCount):
            raise NativeSketchSetupError(
                "A source Sketch contains a stale constraint expression."
            )
        result.append((index, str(raw_expression)))
    return tuple(result)


def _require_self_contained(source: Any) -> None:
    if list(getattr(source, "ExternalGeometry", []) or []):
        raise NativeSketchSetupError(
            "Merge and Mirror require self-contained Sketches with no external geometry."
        )
    _source_expressions(source)


def _copy_geometry_and_constraints(
    source: Any,
    output: Any,
    *,
    copy_expressions: bool,
) -> tuple[int, int]:
    geometry_base = int(output.GeometryCount)
    constraint_base = int(output.ConstraintCount)
    geometries = list(source.Geometry)
    created_geometry = tuple(output.addGeometry(geometries, False)) if geometries else ()
    if len(created_geometry) != len(geometries):
        raise NativeSketchSetupError("Sketch geometry copying returned an incomplete result.")
    for source_index, output_index in enumerate(created_geometry):
        if bool(source.getConstruction(source_index)):
            output.setConstruction(int(output_index), True)

    constraints = []
    import Sketcher

    for source_constraint in list(source.Constraints):
        constraint = Sketcher.Constraint()
        constraint.restoreContent(source_constraint.dumpContent(0))
        for field in ("First", "Second", "Third"):
            value = int(getattr(constraint, field))
            if value >= 0:
                setattr(constraint, field, value + geometry_base)
        constraints.append(constraint)
    created_constraints = (
        tuple(output.addConstraint(constraints)) if constraints else ()
    )
    if len(created_constraints) != len(constraints):
        raise NativeSketchSetupError(
            "Sketch constraint copying returned an incomplete result."
        )
    for source_index, output_index in enumerate(created_constraints):
        if bool(source.getVirtualSpace(source_index)):
            output.setVirtualSpace(int(output_index), True)
    if copy_expressions:
        for source_index, expression in _source_expressions(source):
            output.setExpression(
                f"Constraints[{constraint_base + source_index}]",
                expression,
            )
    return geometry_base, constraint_base


def _new_reusable_sketch(document: Any, label: str) -> Any:
    import PartDesign

    sketch = document.addObject("Sketcher::SketchObject", "Sketch")
    if sketch is None or not sketch.isDerivedFrom("Sketcher::SketchObject"):
        raise NativeSketchSetupError("The reusable Sketch factory returned no Sketch.")
    sketch.Label = label
    PartDesign.initializeDesignDefinition(sketch)
    return sketch


def _finalize_reusable_sketch(document: Any, sketch: Any) -> None:
    import PartDesign

    document.recompute([sketch], True, True)
    if not sketch.isValid():
        raise NativeSketchSetupError("The resulting reusable Sketch is invalid.")
    PartDesign.finalizeDesignDefinition(sketch)
    PartDesign.validateDesign(sketch)


def _verify_changed_sketch(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    sketch = draft.value["sketch"]
    if (
        document.getObject(sketch.Name) is not sketch
        or sketch_revision(sketch) != draft.value["revision"]
        or not sketch.isValid()
    ):
        raise NativeSketchSetupError("The configured Sketch failed its postcondition.")
    return {
        "sketch": object_reference(sketch),
        "map_mode": str(sketch.MapMode or "Deactivated"),
        "support": _support_state(sketch),
        "placement": _placement_state(sketch),
        "revision": draft.value["revision"],
        "entered_edit_mode": False,
    }


def _verify_created_sketches(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    outputs = tuple(draft.value["outputs"])
    expected = tuple(draft.value["revisions"])
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", []) or [])
    for sketch, revision in zip(outputs, expected, strict=True):
        if (
            document.getObject(sketch.Name) is not sketch
            or sketch.getParentGeoFeatureGroup() is not None
            or sketch_revision(sketch) != revision
            or operations.count(sketch) != 1
            or str(getattr(sketch, "VibeCADTimelineRole", "") or "")
            != "operation"
            or not sketch.isValid()
        ):
            raise NativeSketchSetupError(
                "A created reusable Sketch failed its Design-history postcondition."
            )
        PartDesign.validateDesign(sketch)
    return {
        "sketches": [object_reference(sketch) for sketch in outputs],
        "revisions": list(expected),
        "entered_edit_mode": False,
        "next_step": {
            "tool": "sketch.open",
        },
    }


class NativeSketchSetupRuntime:
    """Execute setup mutations without opening or closing Sketch edit mode."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context
        self._document = context.document
        self._document_uid = context.document_uid

    def _object_ref(self, value: Mapping[str, Any]) -> NativeObjectRef:
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeSketchSetupError("An exact Sketch target is invalid.")
        return NativeObjectRef(self._document_uid, str(value["object_name"]))

    def _sources(self, values: list[Any], *, minimum: int) -> tuple[Any, ...]:
        if not isinstance(values, list) or not minimum <= len(values) <= 16:
            raise NativeSketchSetupError(
                f"This operation requires {minimum} to 16 exact Sketch sources."
            )
        sketches = tuple(
            _require_reusable_sketch(self._document, self._object_ref(value))
            for value in values
        )
        if len({sketch.Name for sketch in sketches}) != len(sketches):
            raise NativeSketchSetupError("A Sketch source is repeated.")
        for sketch in sketches:
            _require_self_contained(sketch)
        return sketches

    def _map(self, values: Mapping[str, Any], ticket: NativeCallTicket) -> dict[str, Any]:
        sketch = _require_reusable_sketch(
            self._document,
            self._object_ref(values["target"]),
        )
        support = values["support"]
        if not isinstance(support, Mapping):
            raise NativeSketchSetupError("Sketch support must be one exact support object.")

        def mutate(document: Any) -> NativeMutationDraft:
            configure_reusable_sketch_support(document, sketch, support)
            value = {"sketch": sketch, "revision": ""}
            return NativeMutationDraft(
                value=value,
                recompute_targets=(sketch,),
                changed=(object_identity(sketch),),
                after_recompute=lambda _document: value.update(
                    revision=sketch_revision(sketch)
                ),
            )

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Map Native Reusable Sketch",
            mutate=mutate,
            verify=_verify_changed_sketch,
        )

    def _reorient(
        self,
        values: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        sketch = _require_reusable_sketch(
            self._document,
            self._object_ref(values["target"]),
        )
        placement = reusable_sketch_base_plane_placement(
            str(values["plane"]),
            float(values["offset_mm"]),
            reverse_normal=bool(values["reverse_normal"]),
        )

        def mutate(_document: Any) -> NativeMutationDraft:
            sketch.AttachmentSupport = None
            sketch.MapMode = "Deactivated"
            sketch.Placement = placement
            value = {"sketch": sketch, "revision": ""}
            return NativeMutationDraft(
                value=value,
                recompute_targets=(sketch,),
                changed=(object_identity(sketch),),
                after_recompute=lambda _document: value.update(
                    revision=sketch_revision(sketch)
                ),
            )

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Reorient Native Reusable Sketch",
            mutate=mutate,
            verify=_verify_changed_sketch,
        )

    def _merge(self, values: Mapping[str, Any], ticket: NativeCallTicket) -> dict[str, Any]:
        sources = self._sources(values["sources"], minimum=2)
        label = str(values["label"]).strip()

        def mutate(document: Any) -> NativeMutationDraft:
            output = _new_reusable_sketch(document, label)
            output.Placement = sources[0].Placement
            for source in sources:
                _copy_geometry_and_constraints(
                    source,
                    output,
                    copy_expressions=True,
                )
            _finalize_reusable_sketch(document, output)
            return NativeMutationDraft(
                value={"outputs": (output,), "revisions": (sketch_revision(output),)},
                recompute_targets=(output,),
                created=(object_identity(output),),
            )

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Merge Native Reusable Sketches",
            mutate=mutate,
            verify=_verify_created_sketches,
        )

    def _mirror(self, values: Mapping[str, Any], ticket: NativeCallTicket) -> dict[str, Any]:
        sources = self._sources(values["sources"], minimum=1)
        reference = str(values["reference"])
        label_prefix = str(values["label_prefix"]).strip()
        mirror_reference = {
            "x_axis": (-1, 0),
            "y_axis": (-2, 0),
            "origin": (-1, 1),
        }[reference]

        def mutate(document: Any) -> NativeMutationDraft:
            outputs = []
            for index, source in enumerate(sources, 1):
                label = label_prefix if len(sources) == 1 else f"{label_prefix} {index}"
                output = _new_reusable_sketch(document, label)
                output.Placement = source.Placement
                _copy_geometry_and_constraints(
                    source,
                    output,
                    copy_expressions=False,
                )
                original_geometry = list(range(int(source.GeometryCount)))
                if original_geometry:
                    mirrored = tuple(
                        output.addSymmetric(original_geometry, *mirror_reference)
                    )
                    if len(mirrored) != len(original_geometry):
                        raise NativeSketchSetupError(
                            "Mirror Sketch returned incomplete mirrored geometry."
                        )
                    output.delGeometries(original_geometry, True)
                if int(output.GeometryCount) != int(source.GeometryCount):
                    raise NativeSketchSetupError(
                        "Mirror Sketch produced the wrong geometry count."
                    )
                if int(output.ConstraintCount) != int(source.ConstraintCount):
                    raise NativeSketchSetupError(
                        "Mirror Sketch produced the wrong constraint count."
                    )
                for constraint_index, expression in _source_expressions(source):
                    output.setExpression(
                        f"Constraints[{constraint_index}]",
                        expression,
                    )
                _finalize_reusable_sketch(document, output)
                outputs.append(output)
            return NativeMutationDraft(
                value={
                    "outputs": tuple(outputs),
                    "revisions": tuple(sketch_revision(output) for output in outputs),
                },
                recompute_targets=tuple(outputs),
                created=tuple(object_identity(output) for output in outputs),
            )

        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Mirror Native Reusable Sketches",
            mutate=mutate,
            verify=_verify_created_sketches,
        )

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "map_sketch": frozenset({"target", "support"}),
                "reorient_sketch": frozenset(
                    {"target", "plane", "offset_mm", "reverse_normal"}
                ),
                "merge_sketches": frozenset({"sources", "label"}),
                "mirror_sketch": frozenset(
                    {"sources", "reference", "label_prefix"}
                ),
            },
        )
        if operation == "map_sketch":
            return self._map(values, ticket)
        if operation == "reorient_sketch":
            return self._reorient(values, ticket)
        if operation == "merge_sketches":
            return self._merge(values, ticket)
        return self._mirror(values, ticket)
