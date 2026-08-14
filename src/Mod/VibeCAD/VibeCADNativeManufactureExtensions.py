# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, reusable boundary for CAM face-extension task-panel state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    PreparedOperationBoundary,
    clear_operation_expressions,
    exact_fields,
    finite_number,
    quantity_mm,
    shape_sha256,
)


@dataclass(frozen=True, slots=True)
class PreparedFeatureExtension:
    public_source: Any
    job_resource: Any
    source_shape_sha256: str
    feature: str
    edges: tuple[str, ...]
    sublink: str
    feature_sha256: str
    edge_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedFeatureExtensions:
    noun: str
    kind: str
    default_length_mm: float | None
    extend_corners: bool | None
    items: tuple[PreparedFeatureExtension, ...]


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


def _positive(value: Any, noun: str) -> float:
    result = finite_number(value, noun, minimum=0.0)
    if result <= 0.0:
        _error(f"{noun} must be greater than zero.")
    return result


def _edge_number(name: str) -> int:
    return int(name[4:])


def _boundary_geometry(
    boundary: PreparedOperationBoundary,
    target: Mapping[str, Any],
    noun: str,
) -> Any:
    if not isinstance(target, Mapping) or set(target) != {
        "object_name",
        "expected_state_sha256",
    }:
        _error(f"Each {noun} extension model requires one exact state target.")
    name = str(target.get("object_name") or "")
    expected = str(target.get("expected_state_sha256") or "")
    matches = [
        item
        for item in boundary.geometry
        if str(item.public_source.Name) == name
        and item.source_state_sha256 == expected
    ]
    if len(matches) != 1:
        _error(
            f"Each {noun} extension must target an exact model already present in "
            f"the {noun} geometry.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return matches[0]


def prepare_feature_extensions(
    boundary: PreparedOperationBoundary,
    request: Mapping[str, Any],
    *,
    noun: str,
    use_outline: bool,
) -> PreparedFeatureExtensions:
    """Freeze the exact extension faces and edges accepted by the human page."""

    import Part

    clean_noun = str(noun or "").strip()
    if not clean_noun:
        raise ValueError("noun must not be empty")
    if not isinstance(use_outline, bool):
        raise TypeError("use_outline must be a bool")
    if not isinstance(request, Mapping):
        _error(f"{clean_noun} extensions must be one closed extension request.")
    kind = str(request.get("kind") or "")
    if kind == "none":
        exact_fields(request, frozenset({"kind"}), f"{clean_noun} extensions")
        return PreparedFeatureExtensions(clean_noun, kind, None, None, ())
    exact_fields(
        request,
        frozenset({"kind", "default_length_mm", "extend_corners", "items"}),
        f"Explicit {clean_noun} extensions",
    )
    if kind != "explicit":
        _error(f"{clean_noun} extensions kind must be none or explicit.")
    length = _positive(
        request["default_length_mm"],
        f"{clean_noun} extension default length",
    )
    corners = _boolean(request["extend_corners"], f"{clean_noun} extend_corners")
    raw_items = request["items"]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 64:
        _error(f"Explicit {clean_noun} extensions require 1 through 64 items.")

    prepared_items = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping) or set(raw) != {"model", "feature", "edges"}:
            _error(f"Each {clean_noun} extension requires model, feature, and edges.")
        geometry = _boundary_geometry(boundary, raw["model"], clean_noun)
        feature = str(raw["feature"] or "")
        if not feature.startswith("Face") or feature not in geometry.subelements:
            _error(
                f"A {clean_noun} extension feature must be an exact selected Face "
                "from the same geometry request."
            )
        raw_edges = raw["edges"]
        if not isinstance(raw_edges, list) or not 1 <= len(raw_edges) <= 64:
            _error(f"Each {clean_noun} extension requires 1 through 64 exact edges.")
        unsorted_edges = tuple(str(value) for value in raw_edges)
        if len(unsorted_edges) != len(set(unsorted_edges)) or any(
            not name.startswith("Edge")
            or not name[4:].isdigit()
            or int(name[4:]) < 1
            for name in unsorted_edges
        ):
            _error(f"{clean_noun} extension edge names must be unique EdgeN values.")
        edges = tuple(sorted(unsorted_edges, key=_edge_number))
        if len(edges) > 1 and not corners:
            _error(
                f"A multi-edge {clean_noun} extension requires "
                "extend_corners=true; otherwise provide one extension item per edge."
            )
        source_shape = geometry.public_source.Shape
        try:
            face = source_shape.getElement(feature)
            edge_shapes = [source_shape.getElement(name) for name in edges]
        except Exception as exc:
            raise NativeManufactureError(
                f"{clean_noun} extension geometry changed after turn start.",
                error_code="NATIVE_MANUFACTURE_TARGET_STALE",
            ) from exc
        allowed_face_edges = (
            tuple(face.OuterWire.Edges) if use_outline else tuple(face.Edges)
        )
        if any(
            not any(edge.isSame(candidate) for candidate in allowed_face_edges)
            for edge in edge_shapes
        ):
            scope = "outer boundary" if use_outline else "selected face"
            _error(
                f"Every {clean_noun} extension edge must belong to the {scope} "
                f"{feature}."
            )
        try:
            groups = Part.sortEdges(edge_shapes)
        except Exception as exc:
            raise NativeManufactureError(
                f"{clean_noun} extension edges could not be connected into a wire.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            ) from exc
        if len(edges) > 1 and (len(groups) != 1 or len(groups[0]) != len(edges)):
            _error(f"Multi-edge {clean_noun} extensions must form one connected wire.")
        sublink = (
            edges[0]
            if len(edges) == 1
            else "Wire(" + ",".join(name[4:] for name in edges) + ")"
        )
        identity = (str(geometry.public_source.Name), feature, sublink)
        if identity in seen:
            _error(f"{clean_noun} extension items must be unique.")
        seen.add(identity)
        prepared_items.append(
            PreparedFeatureExtension(
                public_source=geometry.public_source,
                job_resource=geometry.job_resource,
                source_shape_sha256=geometry.shape_sha256,
                feature=feature,
                edges=edges,
                sublink=sublink,
                feature_sha256=shape_sha256(
                    face,
                    f"{clean_noun} extension {feature}",
                ),
                edge_sha256=tuple(
                    shape_sha256(edge, f"{clean_noun} extension {name}")
                    for name, edge in zip(edges, edge_shapes)
                ),
            )
        )
    return PreparedFeatureExtensions(
        clean_noun,
        kind,
        length,
        corners,
        tuple(prepared_items),
    )


def assert_feature_extensions_current(prepared: PreparedFeatureExtensions) -> None:
    for item in prepared.items:
        if (
            item.public_source.Document is None
            or shape_sha256(
                item.public_source.Shape,
                f"CAM model {item.public_source.Name}",
            )
            != item.source_shape_sha256
        ):
            _error(
                f"{prepared.noun} extension model geometry changed before creation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        try:
            face = item.public_source.Shape.getElement(item.feature)
            edges = tuple(
                item.public_source.Shape.getElement(name) for name in item.edges
            )
        except Exception as exc:
            raise NativeManufactureError(
                f"{prepared.noun} extension geometry changed before creation.",
                error_code="NATIVE_MANUFACTURE_STATE_STALE",
            ) from exc
        if shape_sha256(face, f"{prepared.noun} extension face") != item.feature_sha256:
            _error(
                f"{prepared.noun} extension face changed before creation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        hashes = tuple(
            shape_sha256(edge, f"{prepared.noun} extension {name}")
            for name, edge in zip(item.edges, edges)
        )
        if hashes != item.edge_sha256:
            _error(
                f"{prepared.noun} extension edges changed before creation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )


def apply_feature_extensions(
    operation: Any,
    prepared: PreparedFeatureExtensions,
) -> None:
    """Apply only the accepted task-panel representation to a fresh operation."""

    import Path.Op.FeatureExtension as FeatureExtensions

    assert_feature_extensions_current(prepared)
    if prepared.kind == "none":
        FeatureExtensions.setExtensions(operation, [])
        return
    clear_operation_expressions(operation, ("ExtensionLengthDefault",))
    operation.ExtensionLengthDefault = f"{prepared.default_length_mm} mm"
    operation.ExtensionCorners = prepared.extend_corners
    native_extensions = []
    for item in prepared.items:
        extension = FeatureExtensions.Extension(
            operation,
            item.job_resource,
            item.feature,
            item.sublink,
            operation.ExtensionLengthDefault,
            FeatureExtensions.Extension.DirectionNormal,
        )
        if extension.getWire() is None:
            _error(
                f"{prepared.noun} extension {item.public_source.Name}."
                f"{item.feature}:{item.sublink} cannot produce a valid extension face."
            )
        native_extensions.append(extension)
    FeatureExtensions.setExtensions(operation, native_extensions)


def assert_feature_extension_settings(
    operation: Any,
    prepared: PreparedFeatureExtensions,
    mismatches: dict[str, Any],
) -> None:
    """Append exact extension postcondition failures to ``mismatches``."""

    import Path.Op.FeatureExtension as FeatureExtensions

    actual = tuple(FeatureExtensions.readObjExtensionFeature(operation))
    expected = tuple(
        (str(item.job_resource.Name), item.feature, item.sublink)
        for item in prepared.items
    )
    if actual != expected:
        mismatches["extensions"] = {"expected": expected, "actual": actual}
    if prepared.kind != "explicit":
        return
    length = quantity_mm(operation, "ExtensionLengthDefault")
    if length != prepared.default_length_mm:
        mismatches["extension_length_mm"] = {
            "expected": prepared.default_length_mm,
            "actual": length,
        }
    if bool(operation.ExtensionCorners) != prepared.extend_corners:
        mismatches["extend_corners"] = {
            "expected": prepared.extend_corners,
            "actual": bool(operation.ExtensionCorners),
        }
    getter = getattr(operation, "getExpression", None)
    expression = getter("ExtensionLengthDefault") if callable(getter) else None
    if expression:
        mismatches["ExtensionLengthDefault_expression"] = {
            "expected": None,
            "actual": str(expression),
        }


def feature_extension_result(prepared: PreparedFeatureExtensions) -> dict[str, Any]:
    result = {
        "kind": prepared.kind,
        "count": len(prepared.items),
        "items": [
            {
                "object_name": str(item.public_source.Name),
                "feature": item.feature,
                "edges": list(item.edges),
            }
            for item in prepared.items
        ],
    }
    if prepared.kind == "explicit":
        result.update(
            default_length_mm=prepared.default_length_mm,
            extend_corners=prepared.extend_corners,
        )
    return result
