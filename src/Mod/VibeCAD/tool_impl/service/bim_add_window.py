# SPDX-License-Identifier: LGPL-2.1-or-later

"""Insert one native BIM window or door into an exact host wall."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "bim.add_window",
    "description": (
        "Insert one native BIM window or door into an exact host wall. The "
        "opening is cut through the wall automatically. The window plane is "
        "vertical; rotate it with rotation_z_degrees to match the wall "
        "direction (0 means the plane is parallel to the global X axis). "
        "Position is the bottom-left corner of the opening, so its Z sets "
        "the sill height."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "BIMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "host_wall": {
                "type": "string",
                "description": (
                    "Exact internal name of the wall to cut the opening "
                    "into, from core.inspect scope='domain'."
                ),
            },
            "preset": {
                "type": "string",
                "enum": ["fixed_window", "open_window", "door", "glass_door"],
                "description": (
                    "Opening kind: 'fixed_window' is a non-opening glazed "
                    "window, 'open_window' is a single-pane opening window, "
                    "'door' is a solid door, 'glass_door' is a glazed door. "
                    "Doors get IFC type Door, windows get Window."
                ),
            },
            "width_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Total opening width in mm.",
            },
            "height_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Total opening height in mm.",
            },
            "position": domain_runtime.vector_schema(
                "Global position of the opening's bottom-left corner in mm; "
                "Z is the sill/threshold height."
            ),
            "rotation_z_degrees": {
                "type": "number",
                "description": (
                    "Rotation of the window plane around the global Z axis "
                    "in degrees; 0 keeps the plane parallel to the global X "
                    "axis. Match the host wall's direction."
                ),
            },
            "label": {
                "type": "string",
                "description": (
                    "Visible label for the new window or door, e.g. 'FrontDoor'."
                ),
            },
        },
        "required": [
            "host_wall",
            "preset",
            "width_mm",
            "height_mm",
            "position",
            "rotation_z_degrees",
            "label",
        ],
        "additionalProperties": False,
    },
}

# Preset name plus frame parameters (h1 h2 h3 w1 w2 o1 o2) passed to
# Arch.makeWindowPreset. Frame values follow the BIM workbench test defaults.
_PRESETS = {
    "fixed_window": ("Fixed", 50.0, 50.0, 0.0, 100.0, 50.0, 0.0, 50.0),
    "open_window": ("Open 1-pane", 50.0, 50.0, 0.0, 100.0, 50.0, 0.0, 50.0),
    "door": ("Simple door", 50.0, 50.0, 0.0, 100.0, 40.0, 0.0, 0.0),
    "glass_door": ("Glass door", 50.0, 50.0, 0.0, 100.0, 40.0, 0.0, 0.0),
}


def run(
    service: Any,
    host_wall: str,
    preset: str,
    width_mm: float,
    height_mm: float,
    position: dict[str, Any],
    rotation_z_degrees: float,
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    wall_name = str(host_wall or "").strip()
    if not wall_name:
        return _invalid("host_wall is required.")
    preset_spec = _PRESETS.get(str(preset or ""))
    if preset_spec is None:
        return _invalid(
            "preset must be fixed_window, open_window, door, or glass_door."
        )
    width = float(width_mm)
    height = float(height_mm)
    if width <= 0:
        return _invalid("width_mm must be greater than 0.")
    if height <= 0:
        return _invalid("height_mm must be greater than 0.")
    rotation_z = float(rotation_z_degrees)
    preset_name, h1, h2, h3, w1, w2, o1, o2 = preset_spec
    doc = service._active_document()
    wall = doc.getObject(wall_name) if doc is not None else None
    if wall is None:
        return _invalid(
            f"Host wall not found by exact internal name: {wall_name}",
            candidates=[
                {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
                for obj in list(getattr(doc, "Objects", []) or [])
                if getattr(getattr(obj, "Proxy", None), "Type", "") == "Wall"
            ],
        )
    if getattr(getattr(wall, "Proxy", None), "Type", "") != "Wall":
        return _invalid(
            "host_wall must be a native BIM wall.",
            requested={"name": wall.Name, "label": wall.Label, "type": wall.TypeId},
        )
    wall_health = domain_runtime.shape_health(wall)
    if not wall_health.get("valid_non_null"):
        return _invalid("The host wall does not have a valid native shape.", host_wall=wall_health)
    placement_preflight = _opening_preflight(
        wall,
        width,
        height,
        position,
        rotation_z,
    )
    if not placement_preflight.get("intersects_host"):
        return _invalid(
            "The requested opening envelope does not intersect the host wall; no window was created.",
            opening_preflight=placement_preflight,
            host_wall=wall_health,
        )
    wall_shape_before_native = wall.Shape.copy()
    wall_shape_before = domain_runtime.shape_summary(wall)

    def create() -> dict[str, Any]:
        import Arch
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        native_wall = doc.getObject(wall_name)
        if native_wall is None:
            raise RuntimeError(
                f"Host wall '{wall_name}' not found; use core.inspect scope='domain' "
                "for exact names."
            )
        if getattr(getattr(native_wall, "Proxy", None), "Type", "") != "Wall":
            raise RuntimeError(
                f"Object '{wall_name}' is not a BIM wall; bim.add_window "
                "cuts openings only into walls from bim.create_wall."
            )
        # Stand the preset sketch upright, then rotate to the wall direction.
        rotation = App.Rotation(App.Vector(0, 0, 1), rotation_z).multiply(
            App.Rotation(App.Vector(1, 0, 0), 90)
        )
        placement = App.Placement(domain_runtime.parse_vector(position), rotation)
        window = Arch.makeWindowPreset(
            preset_name,
            width,
            height,
            h1,
            h2,
            h3,
            w1,
            w2,
            o1,
            o2,
            placement=placement,
        )
        if window is None:
            raise RuntimeError("Arch.makeWindowPreset did not create an object.")
        window.Label = clean_label
        window.Hosts = [native_wall]
        doc.recompute()
        wall_shape_after = domain_runtime.shape_summary(native_wall)
        try:
            removed = wall_shape_before_native.cut(native_wall.Shape)
            opening_delta = {
                "removed_volume_mm3": float(getattr(removed, "Volume", 0.0) or 0.0),
                "removed_solids": len(list(getattr(removed, "Solids", []) or [])),
                "removed_faces": len(list(getattr(removed, "Faces", []) or [])),
                "native_stage": "BRepAlgoAPI_Cut",
            }
        except Exception as exc:
            opening_delta = {"native_stage": "BRepAlgoAPI_Cut", "native_error": str(exc)}
        try:
            intersection = wall_shape_before_native.common(window.Shape)
            wall_window_intersection = {
                "volume_mm3": float(getattr(intersection, "Volume", 0.0) or 0.0),
                "faces": len(list(getattr(intersection, "Faces", []) or [])),
                "edges": len(list(getattr(intersection, "Edges", []) or [])),
                "native_stage": "BRepAlgoAPI_Common",
            }
        except Exception as exc:
            wall_window_intersection = {
                "native_stage": "BRepAlgoAPI_Common",
                "native_error": str(exc),
            }
        return {
            "document": doc.Name,
            "feature": window.Name,
            "feature_label": window.Label,
            "feature_type": window.TypeId,
            "ifc_type": getattr(window, "IfcType", None),
            "host_wall": native_wall.Name,
            "preset": preset_name,
            "requested_placement": {
                "position": position,
                "rotation_z_degrees": rotation_z,
            },
            "actual_placement": domain_runtime.placement_summary(window),
            "actual_global_placement": domain_runtime.global_placement_summary(window),
            "opening_preflight": placement_preflight,
            "host_link_readback": [
                getattr(host, "Name", None) for host in list(getattr(window, "Hosts", []) or [])
            ],
            "wall_shape_before": wall_shape_before,
            "wall_shape_after": wall_shape_after,
            "wall_shape_delta": domain_runtime.shape_delta(wall_shape_before, wall_shape_after),
            "opening_delta": opening_delta,
            "wall_window_intersection": wall_window_intersection,
            "shape": domain_runtime.shape_summary(window),
            "feature_state": domain_runtime.feature_state_summary(window),
            "wall_state": domain_runtime.feature_state_summary(native_wall),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        opening = result.get("opening_delta") or {}
        wall_state = result.get("wall_state") or {}
        checks = [
            {
                "name": "host_link",
                "ok": wall_name in list(result.get("host_link_readback") or []),
                "expected": wall_name,
                "actual": result.get("host_link_readback"),
            },
            {
                "name": "opening_changed_host",
                "ok": not opening.get("native_error")
                and (
                    float(opening.get("removed_volume_mm3", 0.0)) > 1.0e-9
                    or int(opening.get("removed_faces", 0)) > 0
                ),
                "actual": opening,
            },
            {
                "name": "host_shape_valid",
                "ok": wall_state.get("shape_valid") is True
                and not wall_state.get("marked_invalid"),
                "actual": wall_state,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Add BIM window: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(
        transaction,
        operation="add_window",
        next_action=(
            "Capture a screenshot to confirm the opening cuts the wall; a "
            "window floating outside the wall means position or rotation is "
            "wrong."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _opening_preflight(
    wall: Any,
    width: float,
    height: float,
    position: dict[str, Any],
    rotation_z: float,
) -> dict[str, Any]:
    import FreeCAD as App
    import Part

    rotation = App.Rotation(App.Vector(0, 0, 1), rotation_z).multiply(
        App.Rotation(App.Vector(1, 0, 0), 90)
    )
    placement = App.Placement(domain_runtime.parse_vector(position), rotation)
    depth = max(float(wall.Shape.BoundBox.DiagonalLength) * 2.0, 1.0)
    envelope = Part.makeBox(width, height, depth)
    envelope.Placement = placement.multiply(
        App.Placement(App.Vector(0.0, 0.0, -depth / 2.0), App.Rotation())
    )
    try:
        common = wall.Shape.common(envelope)
        common_volume = float(getattr(common, "Volume", 0.0) or 0.0)
        common_faces = len(list(getattr(common, "Faces", []) or []))
        common_edges = len(list(getattr(common, "Edges", []) or []))
        return {
            "intersects_host": common_volume > 1.0e-9 or common_faces > 0 or common_edges > 0,
            "envelope_bounds": domain_runtime.bound_box_summary(envelope.BoundBox),
            "host_intersection": {
                "volume_mm3": common_volume,
                "faces": common_faces,
                "edges": common_edges,
            },
            "native_stage": "BRepAlgoAPI_Common",
        }
    except Exception as exc:
        return {
            "intersects_host": False,
            "envelope_bounds": domain_runtime.bound_box_summary(envelope.BoundBox),
            "native_stage": "BRepAlgoAPI_Common",
            "native_error": str(exc),
        }
