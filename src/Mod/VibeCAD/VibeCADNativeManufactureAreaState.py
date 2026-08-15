# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded state for experimental CAM Area domain objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import shape_sha256
from VibeCADNativeManufactureState import (
    candidate_model_state,
    persistent_resource_state,
)
from VibeCADNativeTargets import object_reference


MAX_AREA_SNAPSHOT_ITEMS = 12


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _is_live(document: Any, obj: Any) -> bool:
    name = str(getattr(obj, "Name", "") or "")
    return bool(
        name
        and getattr(obj, "Document", None) is document
        and document.getObject(name) is obj
    )


def _is_usable(document: Any, obj: Any) -> bool:
    if not _is_live(document, obj):
        return False
    try:
        from Path.CommandBoundary import is_timeline_input_usable

        return bool(is_timeline_input_usable(obj, document))
    except Exception:
        return False


def is_feature_area(obj: Any) -> bool:
    derived = getattr(obj, "isDerivedFrom", None)
    return bool(callable(derived) and derived("Path::FeatureArea"))


def is_feature_area_view(obj: Any) -> bool:
    derived = getattr(obj, "isDerivedFrom", None)
    return bool(callable(derived) and derived("Path::FeatureAreaView"))


def _shape_topology(shape: Any) -> dict[str, int]:
    result = {}
    for name, attribute in (
        ("edges", "Edges"),
        ("wires", "Wires"),
        ("faces", "Faces"),
        ("shells", "Shells"),
        ("solids", "Solids"),
    ):
        try:
            result[name] = len(getattr(shape, attribute))
        except Exception:
            continue
    return result


def _linked_workplane(area: Any) -> dict[str, Any] | None:
    if not bool(getattr(area, "WorkPlaneSourceEnabled", False)):
        return None
    try:
        source, raw_subelements = area.WorkPlaneSource
    except Exception:
        return None
    if source is None:
        return None
    subelements = tuple(str(value) for value in raw_subelements or ())
    return {
        "source": object_reference(source),
        "subelement": subelements[0] if len(subelements) == 1 else None,
        "collection": str(getattr(area, "WorkPlaneSourceCollection", "") or ""),
    }


def area_state(area: Any) -> dict[str, Any]:
    document = getattr(area, "Document", None)
    if document is None or not is_feature_area(area) or not _is_usable(document, area):
        _error(
            "The exact CAM Area is not usable at the current History position.",
            "NATIVE_MANUFACTURE_TARGET_STALE",
        )
    if not bool(area.isValid()):
        _error(
            "The exact CAM Area is invalid.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    resource = persistent_resource_state(area)
    result = object_reference(area)
    result.update(
        label=str(area.Label),
        state_sha256=resource["state_sha256"],
        source_names=[
            str(source.Name) for source in tuple(getattr(area, "Sources", ()) or ())
        ][:64],
        source_count=len(tuple(getattr(area, "Sources", ()) or ())),
        workplane=_linked_workplane(area),
    )
    shape = getattr(area, "Shape", None)
    if shape is not None and not shape.isNull():
        result["topology"] = _shape_topology(shape)
    return result


def area_view_state(view: Any) -> dict[str, Any]:
    document = getattr(view, "Document", None)
    if (
        document is None
        or not is_feature_area_view(view)
        or not _is_usable(document, view)
        or not bool(view.isValid())
    ):
        _error(
            "The exact CAM Area view is not usable at the current History position.",
            "NATIVE_MANUFACTURE_TARGET_STALE",
        )
    source = getattr(view, "Source", None)
    if source is None or not is_feature_area(source):
        _error(
            "The CAM Area view has no exact Area source.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    resource = persistent_resource_state(view)
    result = object_reference(view)
    result.update(
        label=str(view.Label),
        state_sha256=resource["state_sha256"],
        source_area=object_reference(source),
        section_index=int(getattr(view, "SectionIndex", 0)),
        section_count=int(getattr(view, "SectionCount", 1)),
    )
    return result


def resolve_area_target(
    document: Any,
    target: Mapping[str, Any],
) -> tuple[Any, Mapping[str, Any]]:
    if not isinstance(target, Mapping) or set(target) != {
        "object_name",
        "expected_state_sha256",
    }:
        _error("An Area target requires object_name and expected_state_sha256.")
    name = target.get("object_name")
    expected = target.get("expected_state_sha256")
    if not isinstance(name, str) or not isinstance(expected, str):
        _error("An Area target identity is invalid.")
    area = document.getObject(name)
    if area is None or not is_feature_area(area):
        _error(
            f"CAM Area {name!r} no longer exists.",
            "NATIVE_MANUFACTURE_TARGET_STALE",
        )
    state = area_state(area)
    if state["state_sha256"] != expected:
        _error(
            f"CAM Area {name!r} changed after it was read.",
            "NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "object_name": name,
                "current_state_sha256": state["state_sha256"],
                "retry_from_current_state": True,
            },
        )
    return area, state


@dataclass(frozen=True, slots=True)
class AreaGeometryTarget:
    kind: str
    model: Any
    model_state: Mapping[str, Any]
    source_shape_sha256: str
    subelement: str | None
    element_type: str | None
    element_sha256: str | None

    @property
    def key(self) -> tuple[str, str | None]:
        return str(self.model.Name), self.subelement


def _resolve_model(
    document: Any,
    target: Mapping[str, Any],
) -> tuple[Any, Mapping[str, Any]]:
    if not isinstance(target, Mapping) or set(target) != {
        "object_name",
        "expected_state_sha256",
    }:
        _error("An Area geometry model requires exact object state.")
    name = target.get("object_name")
    expected = target.get("expected_state_sha256")
    if not isinstance(name, str) or not isinstance(expected, str):
        _error("An Area geometry model identity is invalid.")
    model = document.getObject(name)
    derived = getattr(model, "isDerivedFrom", None)
    if model is None or not callable(derived) or not derived("Part::Feature"):
        _error(
            f"Area source {name!r} is not one current Part feature.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    state = candidate_model_state(model)
    if state["state_sha256"] != expected:
        _error(
            f"Area source {name!r} changed after it was read.",
            "NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "object_name": name,
                "current_state_sha256": state["state_sha256"],
                "retry_from_current_state": True,
            },
        )
    return model, state


def resolve_geometry_target(
    document: Any,
    request: Mapping[str, Any],
    *,
    workplane: bool = False,
) -> AreaGeometryTarget:
    if not isinstance(request, Mapping):
        _error("Area geometry must be one closed geometry target.")
    kind = request.get("kind")
    expected_fields = (
        {"kind", "model"}
        if kind == "whole_shape"
        else {
            "kind",
            "model",
            "name",
        }
    )
    if kind not in {"whole_shape", "subelement"} or set(request) != expected_fields:
        _error("Area geometry must be whole_shape or one FaceN/EdgeN subelement.")
    model, state = _resolve_model(document, request["model"])
    source_hash = shape_sha256(model.Shape, f"Area source {model.Name}")
    if kind == "whole_shape":
        if workplane and len(tuple(getattr(model.Shape, "Shells", ()) or ())) > 0:
            _error(
                "A whole-shape Area workplane must be two-dimensional and contain no shell.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        return AreaGeometryTarget(
            kind="whole_shape",
            model=model,
            model_state=state,
            source_shape_sha256=source_hash,
            subelement=None,
            element_type=None,
            element_sha256=None,
        )

    name = request.get("name")
    if not isinstance(name, str) or not name.startswith(("Face", "Edge")):
        _error("Area subelements must use an exact FaceN or EdgeN name.")
    element_type = "Face" if name.startswith("Face") else "Edge"
    try:
        element = model.Shape.getElement(name)
    except Exception as exc:
        raise NativeManufactureError(
            f"Area geometry {model.Name}.{name} no longer exists.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        ) from exc
    if str(getattr(element, "ShapeType", "")) != element_type:
        _error(
            f"Area geometry {model.Name}.{name} is not a {element_type.lower()}.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    element_hash = shape_sha256(element, f"Area geometry {model.Name}.{name}")
    if workplane:
        import PathCommands

        selected = PathCommands.findShape(
            model.Shape,
            name,
            "Wires" if element_type == "Edge" else None,
        )
        if selected is None or selected.isNull():
            _error(
                f"Area workplane {model.Name}.{name} has no containing geometry.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
    return AreaGeometryTarget(
        kind="subelement",
        model=model,
        model_state=state,
        source_shape_sha256=source_hash,
        subelement=name,
        element_type=element_type,
        element_sha256=element_hash,
    )


def geometry_target_is_current(target: AreaGeometryTarget) -> bool:
    try:
        document = target.model.Document
        current = candidate_model_state(target.model)
        if (
            current["state_sha256"] != target.model_state["state_sha256"]
            or shape_sha256(target.model.Shape, f"Area source {target.model.Name}")
            != target.source_shape_sha256
        ):
            return False
        if target.subelement is not None:
            element = target.model.Shape.getElement(target.subelement)
            if (
                str(element.ShapeType) != target.element_type
                or shape_sha256(
                    element,
                    f"Area geometry {target.model.Name}.{target.subelement}",
                )
                != target.element_sha256
            ):
                return False
        return _is_live(document, target.model)
    except Exception:
        return False


def selected_workplane_shape(target: AreaGeometryTarget) -> Any:
    if target.subelement is None:
        return target.model.Shape
    import PathCommands

    return PathCommands.findShape(
        target.model.Shape,
        target.subelement,
        "Wires" if target.element_type == "Edge" else None,
    )


def area_snapshot(document: Any) -> dict[str, Any]:
    areas = []
    views = []
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        try:
            if is_feature_area(obj):
                areas.append(area_state(obj))
            elif is_feature_area_view(obj):
                views.append(area_view_state(obj))
        except NativeManufactureError:
            continue
    return {
        "area_count": len(areas),
        "areas": areas[:MAX_AREA_SNAPSHOT_ITEMS],
        "areas_truncated": len(areas) > MAX_AREA_SNAPSHOT_ITEMS,
        "area_view_count": len(views),
        "area_views": views[:MAX_AREA_SNAPSHOT_ITEMS],
        "area_views_truncated": len(views) > MAX_AREA_SNAPSHOT_ITEMS,
    }
