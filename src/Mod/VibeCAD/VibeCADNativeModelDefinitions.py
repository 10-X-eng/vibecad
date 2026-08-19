# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reusable Sketch and SubShapeBinder mutation algorithms."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_PROFILE_INTENT_KEYS = (
    "kind",
    "global_axis",
    "sketch_axis",
    "axial",
    "radius",
    "axis",
)


def _profile_intent(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if set(value) != set(_PROFILE_INTENT_KEYS):
        raise NativeModelError("A Sketch profile intent is incomplete.")
    result = {key: str(value[key] or "").strip() for key in _PROFILE_INTENT_KEYS}
    if (
        not all(result.values())
        or result["kind"] != "axisymmetric"
        or result["global_axis"] not in {"X", "Y", "Z"}
        or result["sketch_axis"] not in {"H_Axis", "V_Axis"}
    ):
        raise NativeModelError("A Sketch profile intent is invalid.")
    return result


def _support_summary(value: Any) -> list[dict[str, Any]]:
    result = []
    for obj, subelements in list(value or []):
        result.append(
            {
                "object": object_reference(obj),
                "subelements": [str(name) for name in list(subelements or [])],
            }
        )
    return result


def _resolved_design_reference(
    definition: Any,
    source: Any,
    subelements: list[str],
) -> tuple[Any, list[str]]:
    import PartDesign

    try:
        resolved, canonical = PartDesign.resolveDesignDefinitionSubelementReference(
            definition,
            source,
            subelements,
        )
    except Exception as exc:
        detail = " ".join(str(exc).split())
        if len(detail) > 320:
            detail = detail[:317] + "..."
        message = "The exact Design support is not valid before this definition in History."
        if detail:
            message += " " + detail
        raise NativeModelError(message) from exc
    values = [str(value) for value in list(canonical or [])]
    if resolved is None or getattr(resolved, "Document", None) is not definition.Document:
        raise NativeModelError("A Design reference did not resolve in the exact document.")
    return resolved, values


def create_subshape_binder(
    document: Any,
    *,
    label: str,
    references: list[tuple[NativeObjectRef, list[str]]],
) -> NativeMutationDraft:
    import PartDesign

    sources = [
        (resolve_object(document, reference), list(subelements))
        for reference, subelements in references
    ]
    binder = document.addObject("PartDesign::SubShapeBinder", "Reference")
    if binder is None or binder.TypeId != "PartDesign::SubShapeBinder":
        raise NativeModelError("The Design reference factory returned the wrong object type.")
    binder.Label = label
    PartDesign.initializeDesignDefinition(binder)
    resolved: list[tuple[Any, list[str]]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for source, subelements in sources:
        exact, canonical = _resolved_design_reference(binder, source, subelements)
        key = (str(exact.Name), tuple(canonical))
        if key in seen:
            continue
        seen.add(key)
        resolved.append((exact, canonical))
    if not resolved:
        raise NativeModelError("The Design reference has no unique exact geometry source.")
    binder.Support = resolved
    document.recompute([binder], True, True)
    if binder.Shape.isNull() or not binder.Shape.isValid() or not binder.isValid():
        raise NativeModelError("The exact reference sources produced no valid binder geometry.")
    PartDesign.finalizeDesignDefinition(binder)
    return NativeMutationDraft(
        value={
            "binder": binder,
            "expected_support": _support_summary(binder.Support),
        },
        recompute_targets=(binder,),
        created=(object_identity(binder),),
    )


def verify_subshape_binder(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    binder = draft.value["binder"]
    actual_support = _support_summary(binder.Support)
    if (
        document.getObject(binder.Name) is not binder
        or binder.TypeId != "PartDesign::SubShapeBinder"
        or binder.getParentGeoFeatureGroup() is not None
        or actual_support != draft.value["expected_support"]
        or binder.Shape.isNull()
        or not binder.Shape.isValid()
        or not binder.isValid()
    ):
        raise NativeModelError("The reusable Design reference failed its postcondition.")
    PartDesign.validateDesign(binder)
    return {
        "reference": object_reference(binder),
        "support": actual_support,
        "shape": {
            "solids": len(binder.Shape.Solids),
            "faces": len(binder.Shape.Faces),
            "edges": len(binder.Shape.Edges),
        },
    }


def reusable_sketch_base_plane_placement(
    plane: str,
    offset_mm: float,
    *,
    reverse_normal: bool = False,
) -> Any:
    """Return one explicit global-plane placement for a reusable Sketch."""

    import FreeCAD as App

    normals = {
        "XY": App.Vector(0.0, 0.0, 1.0),
        "XZ": App.Vector(0.0, 1.0, 0.0),
        "YZ": App.Vector(1.0, 0.0, 0.0),
    }
    normal = normals.get(plane)
    if normal is None:
        raise NativeModelError("Sketch base plane must be XY, XZ, or YZ.")
    if bool(reverse_normal):
        normal = normal * -1.0
    rotation = App.Rotation(App.Vector(0.0, 0.0, 1.0), normal)
    return App.Placement(normal * float(offset_mm), rotation)


def configure_reusable_sketch_support(
    document: Any,
    sketch: Any,
    support: Mapping[str, Any],
) -> dict[str, Any]:
    kind = str(support.get("kind") or "")
    if kind == "base_plane":
        plane = str(support["plane"])
        offset = float(support["offset_mm"])
        reverse_normal = bool(support.get("reverse_normal", False))
        sketch.AttachmentSupport = None
        sketch.MapMode = "Deactivated"
        sketch.Placement = reusable_sketch_base_plane_placement(
            plane,
            offset,
            reverse_normal=reverse_normal,
        )
        result = {"kind": kind, "plane": plane, "offset_mm": offset}
        if reverse_normal:
            result["reverse_normal"] = True
        return result

    target = support.get("target")
    if not isinstance(target, Mapping):
        raise NativeModelError("Attached Sketch support requires one exact target.")
    source_ref = NativeObjectRef(
        str(document.Uid),
        str(target.get("object_name") or ""),
    )
    source = resolve_object(document, source_ref)
    requested_subelements: list[str] = []
    if kind == "datum_plane":
        if not source.isDerivedFrom("App::Plane"):
            raise NativeModelError("Datum Sketch support must be an exact plane object.")
    elif kind == "planar_face":
        face_name = str(target.get("subelement") or "")
        try:
            face = source.Shape.getElement(face_name)
        except Exception as exc:
            raise NativeModelError("The exact Sketch support face no longer exists.") from exc
        if face.ShapeType != "Face" or not bool(face.Surface.isPlanar()):
            raise NativeModelError("The exact Sketch support face is not planar.")
        requested_subelements = [face_name]
    else:
        raise NativeModelError("Sketch support kind is unavailable.")

    exact, canonical = _resolved_design_reference(
        sketch,
        source,
        requested_subelements,
    )
    if kind == "planar_face":
        exact_face = exact.Shape.getElement(canonical[0])
        if exact_face.ShapeType != "Face" or not bool(exact_face.Surface.isPlanar()):
            raise NativeModelError("The resolved History support face is not planar.")
    sketch.MapMode = "FlatFace"
    sketch.AttachmentSupport = (exact, canonical)
    return {
        "kind": kind,
        "requested_object": source.Name,
        "resolved_support": _support_summary(sketch.AttachmentSupport),
    }


def create_reusable_sketch(
    document: Any,
    *,
    label: str,
    support: Mapping[str, Any],
    profile_intent: Mapping[str, Any] | None = None,
) -> NativeMutationDraft:
    import PartDesign

    intent = _profile_intent(profile_intent)
    sketch = document.addObject("Sketcher::SketchObject", "Sketch")
    if sketch is None or not sketch.isDerivedFrom("Sketcher::SketchObject"):
        raise NativeModelError("The Sketch factory returned the wrong object type.")
    sketch.Label = label
    PartDesign.initializeDesignDefinition(sketch)
    if intent:
        sketch.addProperty(
            "App::PropertyMap",
            "VibeCADProfileIntent",
            "VibeCAD",
            "Parametric profile coordinates.",
            locked=True,
        )
        sketch.VibeCADProfileIntent = intent
        sketch.setEditorMode("VibeCADProfileIntent", 2)
    support_result = configure_reusable_sketch_support(document, sketch, support)
    document.recompute([sketch], True, True)
    if not sketch.isValid():
        raise NativeModelError("The empty reusable Sketch is not valid on its support.")
    PartDesign.finalizeDesignDefinition(sketch)
    return NativeMutationDraft(
        value={
            "sketch": sketch,
            "support": support_result,
            "profile_intent": intent,
        },
        recompute_targets=(sketch,),
        created=(object_identity(sketch),),
    )


def verify_reusable_sketch(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    import PartDesign

    sketch = draft.value["sketch"]
    profile_intent = dict(draft.value.get("profile_intent") or {})
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", []) or [])
    if (
        document.getObject(sketch.Name) is not sketch
        or not sketch.isDerivedFrom("Sketcher::SketchObject")
        or sketch.getParentGeoFeatureGroup() is not None
        or int(sketch.GeometryCount) != 0
        or not sketch.isValid()
        or not str(getattr(sketch, "VibeCADSketchId", "") or "")
        or str(getattr(sketch, "VibeCADTimelineRole", "") or "") != "operation"
        or operations.count(sketch) != 1
    ):
        raise NativeModelError("The reusable Sketch failed its Design-history postcondition.")
    if profile_intent and dict(getattr(sketch, "VibeCADProfileIntent", {}) or {}) != (
        profile_intent
    ):
        raise NativeModelError("The reusable Sketch lost its profile intent.")
    PartDesign.validateDesign(sketch)
    result = {
        "sketch": object_reference(sketch),
        "support": draft.value["support"],
        "entered_edit_mode": False,
        "geometry_count": 0,
        "next_step": {
            "tool": "sketch.open",
            "arguments": {
                "operation": "open",
                "sketch": {"object_name": str(sketch.Name)},
            },
        },
    }
    if profile_intent:
        result["profile_intent"] = profile_intent
    return result
