# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact FEM analysis, material, and geometry targets."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeElementState import (
    element_definition_kind,
    element_definition_state,
    element_definition_still_exact,
)
from VibeCADNativeAnalyzeConstraintState import (
    electromagnetic_constraint_kind,
    electromagnetic_constraint_state,
    electromagnetic_constraint_still_exact,
)
from VibeCADNativeAnalyzeFluidState import (
    fluid_constraint_kind,
    fluid_constraint_state,
    fluid_constraint_still_exact,
)
from VibeCADNativeAnalyzeGeometricalState import (
    geometrical_feature_kind,
    geometrical_feature_state,
    geometrical_feature_still_exact,
)
from VibeCADNativeAnalyzeSupportState import (
    support_condition_kind,
    support_condition_state,
    support_condition_still_exact,
)
from VibeCADNativeAnalyzeConnectionState import (
    connection_kind,
    connection_state,
    connection_still_exact,
)
from VibeCADNativeAnalyzeLoadState import load_kind, load_state, load_still_exact
from VibeCADNativeAnalyzeThermalState import (
    thermal_condition_family,
    thermal_condition_state,
    thermal_condition_still_exact,
)
from VibeCADNativeAnalyzeMeshState import (
    fem_mesh_definition_state,
    fem_mesh_definition_still_exact,
    fem_mesher_kind,
)
from VibeCADNativeAnalyzeState import (
    analysis_state,
    analysis_still_exact,
    material_kind,
    material_state,
    material_still_exact,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_SUBELEMENT = re.compile(r"^(Solid|Face|Edge|Vertex)([1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class PreparedAnalysisTarget:
    analysis: Any
    expected_state_sha256: str
    expected_member_count: int


@dataclass(frozen=True, slots=True)
class PreparedMaterialTarget:
    material: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedGeometryReference:
    source: Any
    expected_state_sha256: str
    subelements: tuple[str, ...]
    shape_kind: str


@dataclass(frozen=True, slots=True)
class PreparedElementDefinitionTarget:
    element: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedElectromagneticConstraintTarget:
    constraint: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedFluidConstraintTarget:
    constraint: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedGeometricalFeatureTarget:
    feature: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedSupportConditionTarget:
    condition: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedConnectionTarget:
    connection: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedLoadTarget:
    load: Any
    kind: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedThermalConditionTarget:
    condition: Any
    family: str
    expected_state_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedFemMeshDefinitionTarget:
    mesh: Any
    kind: str
    expected_state_sha256: str


def prepare_analysis_target(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedAnalysisTarget:
    required = {"object_name", "expected_state_sha256", "expected_member_count"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "analysis must contain only object_name, expected_state_sha256, and expected_member_count."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    analysis = resolve_object(document, reference, expected_types=("Fem::FemAnalysis",))
    state = analysis_state(analysis)
    expected_sha = str(value["expected_state_sha256"])
    expected_count = value["expected_member_count"]
    if type(expected_count) is not int or expected_count < 0:
        raise NativeAnalyzeError(
            "expected_member_count must be a non-negative integer."
        )
    if state["state_sha256"] != expected_sha or state["member_count"] != expected_count:
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "analysis": {"object_name": str(analysis.Name)},
                "current_state_sha256": state["state_sha256"],
                "current_member_count": state["member_count"],
            },
        )
    return PreparedAnalysisTarget(analysis, expected_sha, expected_count)


def analysis_target_still_exact(target: PreparedAnalysisTarget) -> bool:
    return analysis_still_exact(target.analysis, target.expected_state_sha256)


def prepare_material_target(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedMaterialTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "material target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    material = resolve_object(document, reference)
    kind = material_kind(material)
    state = material_state(material)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM material changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "material": {"object_name": str(material.Name)},
                "current_state_sha256": state["state_sha256"],
                "material_kind": kind,
            },
        )
    return PreparedMaterialTarget(material, kind, expected_sha)


def material_target_still_exact(target: PreparedMaterialTarget) -> bool:
    return material_still_exact(target.material, target.expected_state_sha256)


def prepare_element_definition_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedElementDefinitionTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "element target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    element = resolve_object(document, reference)
    kind = element_definition_kind(element)
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = element_definition_state(element)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM element definition changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "element_definition": {"object_name": str(element.Name)},
                "current_state_sha256": state["state_sha256"],
                "element_definition_kind": kind,
            },
        )
    return PreparedElementDefinitionTarget(element, kind, expected_sha)


def element_definition_target_still_exact(
    target: PreparedElementDefinitionTarget,
) -> bool:
    return element_definition_still_exact(
        target.element,
        target.expected_state_sha256,
    )


def prepare_electromagnetic_constraint_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedElectromagneticConstraintTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "constraint target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    constraint = resolve_object(document, reference)
    kind = electromagnetic_constraint_kind(constraint)
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = electromagnetic_constraint_state(constraint)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM electromagnetic constraint changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "electromagnetic_constraint": {"object_name": str(constraint.Name)},
                "current_state_sha256": state["state_sha256"],
                "constraint_kind": kind,
            },
        )
    return PreparedElectromagneticConstraintTarget(constraint, kind, expected_sha)


def electromagnetic_constraint_target_still_exact(
    target: PreparedElectromagneticConstraintTarget,
) -> bool:
    return electromagnetic_constraint_still_exact(
        target.constraint,
        target.expected_state_sha256,
    )


def prepare_fluid_constraint_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedFluidConstraintTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "fluid constraint target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    constraint = resolve_object(document, reference)
    kind = fluid_constraint_kind(constraint)
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = fluid_constraint_state(constraint)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM fluid constraint changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "fluid_constraint": {"object_name": str(constraint.Name)},
                "current_state_sha256": state["state_sha256"],
                "constraint_kind": kind,
            },
        )
    return PreparedFluidConstraintTarget(constraint, kind, expected_sha)


def fluid_constraint_target_still_exact(
    target: PreparedFluidConstraintTarget,
) -> bool:
    return fluid_constraint_still_exact(
        target.constraint,
        target.expected_state_sha256,
    )


def prepare_geometrical_feature_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedGeometricalFeatureTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "geometrical feature target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    feature = resolve_object(document, reference)
    kind = geometrical_feature_kind(feature)
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = geometrical_feature_state(feature)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM geometrical feature changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "geometrical_feature": {"object_name": str(feature.Name)},
                "current_state_sha256": state["state_sha256"],
                "feature_kind": kind,
            },
        )
    return PreparedGeometricalFeatureTarget(feature, kind, expected_sha)


def geometrical_feature_target_still_exact(
    target: PreparedGeometricalFeatureTarget,
) -> bool:
    return geometrical_feature_still_exact(
        target.feature,
        target.expected_state_sha256,
    )


def prepare_support_condition_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedSupportConditionTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "support-condition target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    condition = resolve_object(document, reference)
    kind = support_condition_kind(condition)
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = support_condition_state(condition)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM support condition changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "support_condition": {"object_name": str(condition.Name)},
                "current_state_sha256": state["state_sha256"],
                "condition_kind": kind,
            },
        )
    return PreparedSupportConditionTarget(condition, kind, expected_sha)


def support_condition_target_still_exact(
    target: PreparedSupportConditionTarget,
) -> bool:
    return support_condition_still_exact(
        target.condition,
        target.expected_state_sha256,
    )


def prepare_connection_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedConnectionTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "connection target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    connection = resolve_object(document, reference)
    kind = connection_kind(connection)
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = connection_state(connection)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM connection changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "connection": {"object_name": str(connection.Name)},
                "current_state_sha256": state["state_sha256"],
                "connection_kind": kind,
            },
        )
    return PreparedConnectionTarget(connection, kind, expected_sha)


def connection_target_still_exact(target: PreparedConnectionTarget) -> bool:
    return connection_still_exact(
        target.connection,
        target.expected_state_sha256,
    )


def prepare_load_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedLoadTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "load target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    load = resolve_object(document, reference)
    kind = load_kind(load)
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = load_state(load)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM mechanical load changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "load": {"object_name": str(load.Name)},
                "current_state_sha256": state["state_sha256"],
                "load_kind": kind,
            },
        )
    return PreparedLoadTarget(load, kind, expected_sha)


def load_target_still_exact(target: PreparedLoadTarget) -> bool:
    return load_still_exact(target.load, target.expected_state_sha256)


def prepare_thermal_condition_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_family: str | None = None,
) -> PreparedThermalConditionTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "thermal target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    condition = resolve_object(document, reference)
    family = thermal_condition_family(condition)
    if expected_family is not None and family != expected_family:
        raise NativeAnalyzeError(
            f"The exact target is {family}; this operation requires {expected_family}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = thermal_condition_state(condition)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM thermal condition changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "thermal_condition": {"object_name": str(condition.Name)},
                "current_state_sha256": state["state_sha256"],
                "thermal_family": family,
                "thermal_mode": state["thermal_mode"],
            },
        )
    return PreparedThermalConditionTarget(condition, family, expected_sha)


def thermal_condition_target_still_exact(
    target: PreparedThermalConditionTarget,
) -> bool:
    return thermal_condition_still_exact(
        target.condition,
        target.expected_state_sha256,
    )


def prepare_fem_mesh_definition_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kind: str | None = None,
) -> PreparedFemMeshDefinitionTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "mesh target must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(value["object_name"]))
    mesh = resolve_object(document, reference)
    kind = fem_mesher_kind(mesh)
    if kind == "netgen_legacy":
        kind = "netgen"
    if expected_kind is not None and kind != expected_kind:
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation requires {expected_kind}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    state = fem_mesh_definition_state(mesh)
    expected_sha = str(value["expected_state_sha256"])
    if state["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM mesh definition changed after the provider read its state.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "mesh_definition": {"object_name": str(mesh.Name)},
                "current_state_sha256": state["state_sha256"],
                "mesher": kind,
                "generated": state["generated"],
                "topology": state["topology"],
            },
        )
    return PreparedFemMeshDefinitionTarget(mesh, kind, expected_sha)


def fem_mesh_definition_target_still_exact(
    target: PreparedFemMeshDefinitionTarget,
) -> bool:
    return fem_mesh_definition_still_exact(
        target.mesh,
        target.expected_state_sha256,
    )


def _shape_kind_and_index(source: Any, name: str) -> tuple[str, int]:
    match = _SUBELEMENT.fullmatch(name)
    if match is None:
        raise NativeAnalyzeError(
            f"{name or 'A FEM reference'} must be a SolidN, FaceN, EdgeN, or VertexN subelement."
        )
    kind = str(match.group(1))
    index = int(match.group(2))
    shape = source.Shape
    collection_name = "Vertexes" if kind == "Vertex" else f"{kind}s"
    collection = getattr(shape, collection_name, ())
    if index > len(collection):
        raise NativeAnalyzeError(
            f"{name} does not exist on exact geometry source {source.Name}."
        )
    try:
        selected = shape.getElement(name)
        if (
            selected.isNull()
            or not selected.isValid()
            or str(selected.ShapeType) != kind
        ):
            raise ValueError
    except Exception as exc:
        raise NativeAnalyzeError(
            f"{name} is not a valid current subelement of {source.Name}."
        ) from exc
    return kind, index


def prepare_geometry_references(
    document: Any,
    document_uid: str,
    values: Any,
    *,
    allowed_kinds: frozenset[str] | None = None,
    allow_mixed_kinds: bool = False,
) -> tuple[PreparedGeometryReference, ...]:
    if not isinstance(values, list) or len(values) > 64:
        raise NativeAnalyzeError(
            "references must be a list of at most 64 exact geometry targets."
        )
    required = {"object_name", "expected_state_sha256", "subelements"}
    prepared = []
    object_names = set()
    common_kind = None
    for value in values:
        if not isinstance(value, Mapping) or set(value) != required:
            raise NativeAnalyzeError(
                "Every reference must contain object_name, expected_state_sha256, and subelements."
            )
        reference = NativeObjectRef(document_uid, str(value["object_name"]))
        if reference.object_name in object_names:
            raise NativeAnalyzeError("references must not repeat one geometry object.")
        object_names.add(reference.object_name)
        source = resolve_object(document, reference)
        shape = getattr(source, "Shape", None)
        try:
            usable = shape is not None and not shape.isNull() and shape.isValid()
        except Exception:
            usable = False
        if not usable:
            raise NativeAnalyzeError(
                f"FEM reference {reference.object_name} has no valid shape."
            )
        try:
            import PartGui

            active = bool(PartGui.isModelingObjectActive(source))
        except Exception:
            active = False
        if not active:
            raise NativeAnalyzeError(
                f"FEM reference {reference.object_name} is not active at current History.",
                error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
            )
        state = mesh_object_state(source)
        expected_sha = str(value["expected_state_sha256"])
        if state.get("state_sha256") != expected_sha:
            raise NativeAnalyzeError(
                f"FEM reference {reference.object_name} changed after it was read.",
                error_code="NATIVE_ANALYZE_STATE_STALE",
                repair={
                    "source": {"object_name": reference.object_name},
                    "current_state_sha256": state.get("state_sha256"),
                    "current_topology": state.get("topology"),
                },
            )
        raw_subelements = value["subelements"]
        if (
            not isinstance(raw_subelements, list)
            or not 1 <= len(raw_subelements) <= 256
        ):
            raise NativeAnalyzeError(
                "Each reference must contain 1 to 256 unique subelements."
            )
        names = tuple(str(name) for name in raw_subelements)
        if len(names) != len(set(names)):
            raise NativeAnalyzeError("A FEM reference must not repeat a subelement.")
        kinds = {_shape_kind_and_index(source, name)[0] for name in names}
        if len(kinds) != 1 and not allow_mixed_kinds:
            raise NativeAnalyzeError("Each FEM reference must use one subelement type.")
        invalid_kinds = kinds - allowed_kinds if allowed_kinds is not None else set()
        if invalid_kinds:
            expected = " or ".join(sorted(allowed_kinds))
            raise NativeAnalyzeError(
                f"{reference.object_name} uses {' or '.join(sorted(invalid_kinds))} references; "
                f"this FEM operation requires {expected} references."
            )
        kind = next(iter(kinds)) if len(kinds) == 1 else "Mixed"
        if not allow_mixed_kinds:
            if common_kind is None:
                common_kind = kind
            elif kind != common_kind:
                raise NativeAnalyzeError(
                    "All FEM references must use the same subelement type, matching the human editor."
                )
        prepared.append(PreparedGeometryReference(source, expected_sha, names, kind))
    return tuple(prepared)


def geometry_references_still_exact(
    references: tuple[PreparedGeometryReference, ...],
) -> bool:
    for reference in references:
        source = reference.source
        try:
            import PartGui

            if (
                not PartGui.isModelingObjectActive(source)
                or mesh_object_state(source).get("state_sha256")
                != reference.expected_state_sha256
            ):
                return False
            for name in reference.subelements:
                _shape_kind_and_index(source, name)
        except Exception:
            return False
    return True


def reference_value(
    references: tuple[PreparedGeometryReference, ...],
) -> list[tuple[Any, tuple[str, ...]]]:
    return [(reference.source, reference.subelements) for reference in references]
