# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of FEM force, pressure, centrifugal, and gravity loads."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeLoadState import load_kind, load_state
from VibeCADNativeAnalyzeLoadValues import (
    PreparedLoadValues,
    apply_load_values,
    prepare_load_values,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    PreparedGeometryReference,
    analysis_target_still_exact,
    geometry_references_still_exact,
    prepare_analysis_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef, object_identity, resolve_object


@dataclass(frozen=True, slots=True)
class PreparedDirectionReference:
    source: Any
    expected_state_sha256: str
    subelement: str


@dataclass(frozen=True, slots=True)
class PreparedDirectionVector:
    x: float
    y: float
    z: float


PreparedForceDirection = PreparedDirectionReference | PreparedDirectionVector | None


@dataclass(frozen=True, slots=True)
class PreparedLoadCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    references: tuple[PreparedGeometryReference, ...]
    direction: PreparedForceDirection
    axis: PreparedGeometryReference | None
    scope_kind: str | None
    kind: str
    label: str
    values: PreparedLoadValues


def load_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def _derived(obj: Any, type_id: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_id))
    except Exception:
        return False


def _active_source(source: Any) -> None:
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(source))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            f"Direction source {source.Name} is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def prepare_force_direction(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedForceDirection:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("direction must be one typed force-direction object.")
    raw = dict(value)
    kind = str(raw.get("kind", "") or "")
    if kind == "normal" and set(raw) == {"kind"}:
        return None
    if kind == "vector" and set(raw) == {"kind", "x", "y", "z"}:
        components = []
        for axis in ("x", "y", "z"):
            try:
                component = float(raw[axis])
            except (TypeError, ValueError) as exc:
                raise NativeAnalyzeError(
                    f"direction.{axis} must be a finite number."
                ) from exc
            if not math.isfinite(component):
                raise NativeAnalyzeError(f"direction.{axis} must be finite.")
            components.append(component)
        length = math.sqrt(sum(component * component for component in components))
        if length <= 1.0e-15:
            raise NativeAnalyzeError("direction must have non-zero length.")
        normalized = [
            float(format(component / length, ".15g")) for component in components
        ]
        return PreparedDirectionVector(*normalized)
    required = {"kind", "object_name", "expected_state_sha256", "subelement"}
    if kind != "reference" or set(raw) != required:
        raise NativeAnalyzeError(
            "direction must be normal or one exact reference with object_name, "
            "expected_state_sha256, and subelement."
        )
    source = resolve_object(
        document,
        NativeObjectRef(document_uid, str(raw["object_name"])),
    )
    _active_source(source)
    current_sha = mesh_object_state(source)["state_sha256"]
    expected_sha = str(raw["expected_state_sha256"])
    if current_sha != expected_sha:
        raise NativeAnalyzeError(
            "The force-direction source changed after it was read.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "source": {"object_name": str(source.Name)},
                "current_state_sha256": current_sha,
            },
        )
    subelement = str(raw["subelement"] or "")
    if not subelement:
        if not (
            _derived(source, "App::DatumElement")
            or _derived(source, "Part::Datum")
        ):
            raise NativeAnalyzeError(
                "An empty force-direction subelement is valid only for a datum line or plane."
            )
    else:
        if not (
            subelement.startswith("Edge") or subelement.startswith("Face")
        ):
            raise NativeAnalyzeError(
                "A force-direction subelement must be one linear EdgeN or planar FaceN."
            )
        try:
            selected = source.Shape.getElement(subelement)
            type_id = str(selected.Curve.TypeId) if subelement.startswith("Edge") else str(
                selected.Surface.TypeId
            )
        except Exception as exc:
            raise NativeAnalyzeError(
                f"{source.Name}.{subelement} is not valid current direction geometry."
            ) from exc
        expected_type = "Part::GeomLine" if subelement.startswith("Edge") else "Part::GeomPlane"
        if type_id != expected_type:
            expected_word = "linear edge" if subelement.startswith("Edge") else "planar face"
            raise NativeAnalyzeError(
                f"{source.Name}.{subelement} is not a {expected_word}."
            )
    return PreparedDirectionReference(source, expected_sha, subelement)


def direction_still_exact(reference: PreparedForceDirection) -> bool:
    if reference is None or isinstance(reference, PreparedDirectionVector):
        return True
    try:
        _active_source(reference.source)
        if mesh_object_state(reference.source)["state_sha256"] != reference.expected_state_sha256:
            return False
        if reference.subelement:
            reference.source.Shape.getElement(reference.subelement)
        return True
    except Exception:
        return False


def prepare_centrifugal_axis(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedGeometryReference:
    required = {"object_name", "expected_state_sha256", "subelement"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "axis must contain only object_name, expected_state_sha256, and subelement."
        )
    references = prepare_geometry_references(
        document,
        document_uid,
        [
            {
                "object_name": value["object_name"],
                "expected_state_sha256": value["expected_state_sha256"],
                "subelements": [value["subelement"]],
            }
        ],
        allowed_kinds=frozenset({"Edge"}),
    )
    if len(references) != 1 or len(references[0].subelements) != 1:
        raise NativeAnalyzeError("axis must identify exactly one current linear edge.")
    axis = references[0]
    name = axis.subelements[0]
    try:
        type_id = str(axis.source.Shape.getElement(name).Curve.TypeId)
    except Exception as exc:
        raise NativeAnalyzeError(f"{axis.source.Name}.{name} is not a valid edge.") from exc
    if type_id != "Part::GeomLine":
        raise NativeAnalyzeError(
            f"{axis.source.Name}.{name} is not a linear rotation axis."
        )
    return axis


def prepare_centrifugal_scope(
    document: Any,
    document_uid: str,
    value: Any,
) -> tuple[str, tuple[PreparedGeometryReference, ...]]:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("scope must be one typed centrifugal-scope object.")
    raw = dict(value)
    kind = str(raw.get("kind", "") or "")
    if kind == "all_bodies" and set(raw) == {"kind"}:
        return kind, ()
    if kind == "selected_geometry" and set(raw) == {"kind", "references"}:
        references = prepare_geometry_references(
            document,
            document_uid,
            raw["references"],
            allowed_kinds=frozenset({"Solid", "Face"}),
        )
        if not references:
            raise NativeAnalyzeError(
                "selected_geometry scope requires at least one exact Solid or Face reference."
            )
        return kind, references
    raise NativeAnalyzeError(
        "scope must be all_bodies or selected_geometry with exact references."
    )


def _global_gravity_exists(analysis: Any) -> bool:
    for member in tuple(analysis.Group or ()):
        try:
            if load_kind(member) == "gravity":
                return True
        except NativeAnalyzeError:
            continue
    return False


def prepare_load_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    load: Any,
    references: Any = None,
    direction: Any = None,
    axis: Any = None,
    scope: Any = None,
) -> PreparedLoadCreate:
    target = prepare_analysis_target(document, document_uid, analysis)
    values = prepare_load_values(kind, load)
    prepared_references: tuple[PreparedGeometryReference, ...] = ()
    prepared_direction = None
    prepared_axis = None
    scope_kind = None
    if kind == "force":
        prepared_references = prepare_geometry_references(
            document,
            document_uid,
            references,
            allowed_kinds=frozenset({"Vertex", "Edge", "Face"}),
        )
        prepared_direction = prepare_force_direction(document, document_uid, direction)
    elif kind == "pressure":
        prepared_references = prepare_geometry_references(
            document,
            document_uid,
            references,
            allowed_kinds=frozenset({"Edge", "Face"}),
        )
    elif kind == "centrifugal":
        prepared_axis = prepare_centrifugal_axis(document, document_uid, axis)
        scope_kind, prepared_references = prepare_centrifugal_scope(
            document,
            document_uid,
            scope,
        )
    elif kind == "gravity":
        if _global_gravity_exists(target.analysis):
            raise NativeAnalyzeError(
                "This FEM analysis already contains its one global gravity load."
            )
    else:
        raise NativeAnalyzeError("The requested FEM mechanical-load kind is unavailable.")
    if kind in {"force", "pressure"} and not prepared_references:
        raise NativeAnalyzeError(
            f"A {kind} load requires at least one exact geometry reference."
        )
    return PreparedLoadCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_references,
        prepared_direction,
        prepared_axis,
        scope_kind,
        kind,
        load_label(label),
        values,
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    factories = {
        "force": ("Force", ObjectsFem.makeConstraintForce),
        "pressure": ("Pressure", ObjectsFem.makeConstraintPressure),
        "centrifugal": ("CentrifugalForce", ObjectsFem.makeConstraintCentrif),
        "gravity": ("Gravity", ObjectsFem.makeConstraintSelfWeight),
    }
    try:
        stem, factory = factories[kind]
    except KeyError as exc:
        raise NativeAnalyzeError(
            "The requested FEM mechanical-load kind is unavailable."
        ) from exc
    return factory(document, document.getUniqueObjectName(stem))


def direction_value(reference: PreparedForceDirection) -> Any:
    if reference is None or isinstance(reference, PreparedDirectionVector):
        return None
    names = (reference.subelement,) if reference.subelement else ()
    return reference.source, names


def _visible_reference(reference: PreparedDirectionReference) -> dict[str, str]:
    return {
        "object_name": str(reference.source.Name),
        "subelement": reference.subelement,
    }


def expected_load_definition(prepared: PreparedLoadCreate) -> dict[str, Any]:
    values = prepared.values.normalized()
    if prepared.kind == "force":
        if prepared.direction is None:
            direction = {"kind": "normal", "reversed": values["reversed"]}
        elif isinstance(prepared.direction, PreparedDirectionVector):
            direction = {
                "kind": "vector",
                "x": prepared.direction.x,
                "y": prepared.direction.y,
                "z": prepared.direction.z,
            }
        else:
            direction = {
                "kind": "reference",
                **_visible_reference(prepared.direction),
                "reversed": values["reversed"],
            }
        return {"force_n": values["force_n"], "direction": direction}
    if prepared.kind == "centrifugal":
        assert prepared.axis is not None
        return {
            **values,
            "axis": {
                "object_name": str(prepared.axis.source.Name),
                "subelement": prepared.axis.subelements[0],
            },
            "scope": (
                {
                    "kind": "selected_geometry",
                    "references": [
                        {
                            "object_name": str(reference.source.Name),
                            "subelements": list(reference.subelements),
                        }
                        for reference in prepared.references
                    ],
                }
                if prepared.scope_kind == "selected_geometry"
                else {"kind": "all_bodies"}
            ),
        }
    return values


def create_load(document: Any, prepared: PreparedLoadCreate) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedLoadCreate):
        raise TypeError("prepared must be a PreparedLoadCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after load preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Load reference geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.axis is not None and not geometry_references_still_exact((prepared.axis,)):
        raise NativeAnalyzeError(
            "Centrifugal axis geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not direction_still_exact(prepared.direction):
        raise NativeAnalyzeError(
            "Force-direction geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.kind == "gravity" and _global_gravity_exists(prepared.analysis.analysis):
        raise NativeAnalyzeError(
            "This FEM analysis already contains its one global gravity load."
        )
    load = _factory(document, prepared.kind)
    if load is None or load_kind(load) != prepared.kind:
        raise NativeAnalyzeError("The FEM load factory returned the wrong object type.")
    load.Label = prepared.label
    apply_load_values(load, prepared.values)
    load.References = reference_value(prepared.references)
    if prepared.kind == "force":
        load.Direction = direction_value(prepared.direction)
        if isinstance(prepared.direction, PreparedDirectionVector):
            import FreeCAD

            load.CustomDirection = FreeCAD.Vector(
                prepared.direction.x,
                prepared.direction.y,
                prepared.direction.z,
            )
            load.UseCustomDirection = True
        else:
            load.UseCustomDirection = False
    elif prepared.kind == "centrifugal":
        assert prepared.axis is not None
        load.RotationAxis = reference_value((prepared.axis,))
    prepared.analysis.analysis.addObject(load)
    if load not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError("The FEM load was not added to its analysis.")
    publish_operation(document, prepared.boundary, load)
    return NativeMutationDraft(
        value={"load": load, "prepared": prepared},
        recompute_targets=(load, prepared.analysis.analysis),
        created=(object_identity(load),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def _direction_usable(load: Any, kind: str) -> bool:
    if kind != "force":
        return True
    vector = load.DirectionVector
    length = math.sqrt(sum(float(vector[index]) ** 2 for index in range(3)))
    return math.isfinite(length) and length > 1.0e-12


def verify_load_create(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    load = draft.value["load"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, load)
    state = load_state(load)
    checks = {
        "live object": is_live(document, load),
        "load kind": load_kind(load) == prepared.kind,
        "label": str(load.Label) == prepared.label,
        "solver values": state["definition"] == expected_load_definition(prepared),
        "geometry references": references_match(load, prepared.references),
        "analysis append order": tuple(analysis.Group or ())
        == (*prepared.members_before, load),
        "current geometry": geometry_references_still_exact(prepared.references),
        "current direction": direction_still_exact(prepared.direction),
        "usable force direction": _direction_usable(load, prepared.kind),
        "native validity": bool(load.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The new FEM load failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError("The FEM analysis did not record its new load.")
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_load": state,
    }
