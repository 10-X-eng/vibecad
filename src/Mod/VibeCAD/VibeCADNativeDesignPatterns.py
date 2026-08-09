# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact source and target handling shared by current Design patterns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeTargets import NativeObjectRef, object_reference, resolve_object


@dataclass(frozen=True, slots=True)
class DesignPatternSourceSpec:
    kind: str
    source_ref: NativeObjectRef
    target_refs: tuple[NativeObjectRef, ...] = ()


def _object_ref(document_uid: str, value: Any, *, name: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Design Pattern {name} is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"]))


def pattern_source_from_mapping(
    document_uid: str,
    value: Any,
) -> DesignPatternSourceSpec:
    if not isinstance(value, Mapping) or "kind" not in value:
        raise NativeModelError("A Design Pattern source is invalid.")
    kind = str(value["kind"])
    if kind == "body":
        if set(value) != {"kind", "body"}:
            raise NativeModelError("A Body Pattern source is invalid.")
        return DesignPatternSourceSpec(
            kind,
            _object_ref(document_uid, value["body"], name="source Body"),
        )
    if kind != "feature" or set(value) != {"kind", "operation", "targets"}:
        raise NativeModelError("A Design Pattern source is invalid.")
    raw_targets = value["targets"]
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 16:
        raise NativeModelError(
            "A Feature Pattern requires 1 to 16 exact target Bodies."
        )
    targets = tuple(
        _object_ref(document_uid, target, name="target Body")
        for target in raw_targets
    )
    names = tuple(target.object_name for target in targets)
    if len(names) != len(set(names)):
        raise NativeModelError("A Feature Pattern repeats the same target Body.")
    return DesignPatternSourceSpec(
        kind,
        _object_ref(document_uid, value["operation"], name="source operation"),
        targets,
    )


def _valid_solid_body(body: Any, *, role: str) -> None:
    shape = getattr(body, "Shape", None)
    if (
        shape is None
        or shape.isNull()
        or not shape.isValid()
        or len(shape.Solids) != 1
    ):
        raise NativeModelError(
            f"The exact Design Pattern {role} has no single valid solid state."
        )


def resolve_pattern_source(
    document: Any,
    spec: DesignPatternSourceSpec,
) -> tuple[Any, list[Any], str]:
    if not isinstance(spec, DesignPatternSourceSpec):
        raise TypeError("spec must be a DesignPatternSourceSpec")
    if spec.kind == "body":
        source = resolve_object(
            document,
            spec.source_ref,
            expected_types=("PartDesign::Body",),
        )
        _valid_solid_body(source, role="source Body")
        return source, [], "New Bodies"

    source = resolve_object(
        document,
        spec.source_ref,
        expected_types=("PartDesign::FeatureAddSub",),
    )
    if not bool(getattr(source, "isValid", lambda: False)()):
        raise NativeModelError("The exact Feature Pattern source is invalid.")
    source_result = str(getattr(source, "ResultOperation", "") or "")
    if source_result == "Cut":
        result_mode = "Cut"
    elif source_result in {"New Body", "Join"}:
        result_mode = "Join"
    else:
        raise NativeModelError(
            "A Feature Pattern source must be additive or subtractive."
        )
    targets = [
        resolve_object(
            document,
            target,
            expected_types=("PartDesign::Body",),
        )
        for target in spec.target_refs
    ]
    for target in targets:
        _valid_solid_body(target, role="target Body")
    return source, targets, result_mode


def configure_pattern_source(
    operation: Any,
    edit: Any,
    spec: DesignPatternSourceSpec,
    *,
    generated_copy_count: int,
) -> tuple[Any, list[Any], str]:
    import PartDesign

    document = getattr(operation, "Document", None)
    if document is None:
        raise NativeModelError("A Design Pattern edit has no exact document.")
    source, targets, result_mode = resolve_pattern_source(document, spec)
    if spec.kind == "body":
        PartDesign.setDesignBodyPatternSource(edit, source, generated_copy_count)
    else:
        PartDesign.setDesignFeaturePatternTargets(edit, source, targets)
    return source, targets, result_mode


def pattern_source_summary(
    spec: DesignPatternSourceSpec,
    source: Any,
    targets: list[Any],
) -> dict[str, Any]:
    if spec.kind == "body":
        return {"kind": "body", "body": object_reference(source)}
    return {
        "kind": "feature",
        "operation": object_reference(source),
        "targets": [object_reference(target) for target in targets],
    }
