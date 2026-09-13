# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact stock configuration for CAM Job editors and automation."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


_ALLOWANCE_FIELDS = (
    "x_negative",
    "x_positive",
    "y_negative",
    "y_positive",
    "z_negative",
    "z_positive",
)
_TARGET_FIELDS = ("object_name",)
_KINDS = ("model_bounds", "box", "cylinder", "existing_solid")
_MAX_DISTANCE_MM = 1_000_000.0


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any, fields: tuple[str, ...], noun: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError(f"{noun} must contain exactly {', '.join(fields)}")
    return value


def _number(value: Any, noun: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{noun} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{noun} must be a finite number") from exc
    if not math.isfinite(result) or abs(result) > _MAX_DISTANCE_MM:
        raise ValueError(f"{noun} must be a finite CAM distance in millimeters")
    if positive and result <= 0.0:
        raise ValueError(f"{noun} must be greater than zero")
    return round(result, 9)


def _vector(value: Any, noun: str, *, bounded_axis: bool = False) -> dict[str, float]:
    raw = _mapping(value, ("x", "y", "z"), noun)
    result = {
        axis: _number(raw[axis], f"{noun}.{axis}")
        for axis in ("x", "y", "z")
    }
    if bounded_axis and any(abs(component) > 1.0 for component in result.values()):
        raise ValueError(f"{noun} components must be from -1 through 1")
    if bounded_axis and math.sqrt(sum(value * value for value in result.values())) < 1e-12:
        raise ValueError(f"{noun} must be non-zero")
    return result


def _placement(value: Any) -> dict[str, Any]:
    raw = _mapping(value, ("origin_mm", "rotation"), "placement")
    rotation = _mapping(
        raw["rotation"],
        ("axis", "angle_degrees"),
        "placement.rotation",
    )
    return {
        "origin_mm": _vector(raw["origin_mm"], "placement.origin_mm"),
        "rotation": {
            "axis": _vector(
                rotation["axis"],
                "placement.rotation.axis",
                bounded_axis=True,
            ),
            "angle_degrees": _number(
                rotation["angle_degrees"],
                "placement.rotation.angle_degrees",
            ),
        },
    }


def _target(value: Any) -> dict[str, str]:
    raw = _mapping(value, _TARGET_FIELDS, "existing stock source")
    name = str(raw["object_name"] or "")
    if not name or len(name) > 128 or not (
        name[0].isalpha() or name[0] == "_"
    ) or any(not (character.isalnum() or character == "_") for character in name):
        raise ValueError("existing stock source object_name is invalid")
    return {"object_name": name}


def normalize_stock_specification(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one closed stock meaning without touching the document."""

    if not isinstance(value, Mapping):
        raise ValueError("stock must be an object")
    kind = str(value.get("kind") or "")
    if kind not in _KINDS:
        raise ValueError("stock kind must be model_bounds, box, cylinder, or existing_solid")
    common = {"kind"}
    if "placement" in value:
        common.add("placement")
    result: dict[str, Any] = {"kind": kind}
    if kind == "model_bounds":
        if set(value) != common | {"allowance_mm"}:
            raise ValueError("model_bounds stock requires only allowance_mm and optional placement")
        allowance = _mapping(
            value["allowance_mm"],
            _ALLOWANCE_FIELDS,
            "allowance_mm",
        )
        result["allowance_mm"] = {
            name: _number(allowance[name], f"allowance_mm.{name}")
            for name in _ALLOWANCE_FIELDS
        }
    elif kind == "box":
        if set(value) != common | {"size_mm"}:
            raise ValueError("box stock requires only size_mm and optional placement")
        size = _mapping(value["size_mm"], ("x", "y", "z"), "size_mm")
        result["size_mm"] = {
            axis: _number(size[axis], f"size_mm.{axis}", positive=True)
            for axis in ("x", "y", "z")
        }
    elif kind == "cylinder":
        if set(value) != common | {"radius_mm", "height_mm"}:
            raise ValueError(
                "cylinder stock requires only radius_mm, height_mm, and optional placement"
            )
        result["radius_mm"] = _number(
            value["radius_mm"],
            "radius_mm",
            positive=True,
        )
        result["height_mm"] = _number(
            value["height_mm"],
            "height_mm",
            positive=True,
        )
    else:
        if set(value) != common | {"source"}:
            raise ValueError(
                "existing_solid stock requires only source and optional placement"
            )
        result["source"] = _target(value["source"])
    if "placement" in value:
        result["placement"] = _placement(value["placement"])
    return result


def _quantity_mm(value: Any) -> float:
    reader = getattr(value, "getValueAs", None)
    raw = reader("mm") if callable(reader) else getattr(value, "Value", value)
    return round(float(raw), 9)


def _placement_state(value: Any) -> dict[str, Any]:
    placement = getattr(value, "Placement", None)
    if placement is None:
        raise ValueError("CAM stock has no placement")
    base = placement.Base
    rotation = placement.Rotation
    axis = rotation.Axis
    return {
        "origin_mm": {
            name: round(float(getattr(base, name)), 9)
            for name in ("x", "y", "z")
        },
        "rotation": {
            "axis": {
                name: round(float(getattr(axis, name)), 9)
                for name in ("x", "y", "z")
            },
            "angle_degrees": round(math.degrees(float(rotation.Angle)), 9),
        },
    }


def _stock_kind(stock: Any) -> str:
    stock_type = str(getattr(stock, "StockType", "") or "")
    return {
        "FromBase": "model_bounds",
        "CreateBox": "box",
        "CreateCylinder": "cylinder",
    }.get(stock_type, "existing_solid")


def stock_configuration_state(job: Any) -> dict[str, Any]:
    """Return exact authored stock values in the edit contract's vocabulary."""

    stock = getattr(job, "Stock", None)
    if stock is None:
        return {"present": False, "state_sha256": _digest({"present": False})}
    kind = _stock_kind(stock)
    result: dict[str, Any] = {
        "present": True,
        "object_name": str(getattr(stock, "Name", "") or ""),
        "label": str(getattr(stock, "Label", "") or ""),
        "kind": kind,
        "placement": _placement_state(stock),
    }
    if kind == "model_bounds":
        result["allowance_mm"] = {
            "x_negative": _quantity_mm(stock.ExtXneg),
            "x_positive": _quantity_mm(stock.ExtXpos),
            "y_negative": _quantity_mm(stock.ExtYneg),
            "y_positive": _quantity_mm(stock.ExtYpos),
            "z_negative": _quantity_mm(stock.ExtZneg),
            "z_positive": _quantity_mm(stock.ExtZpos),
        }
    elif kind == "box":
        result["size_mm"] = {
            "x": _quantity_mm(stock.Length),
            "y": _quantity_mm(stock.Width),
            "z": _quantity_mm(stock.Height),
        }
    elif kind == "cylinder":
        result.update(
            radius_mm=_quantity_mm(stock.Radius),
            height_mm=_quantity_mm(stock.Height),
        )
    else:
        sources = tuple(getattr(stock, "Objects", ()) or ())
        if not sources:
            source = getattr(stock, "Source", None)
            sources = (source,) if source is not None else ()
        if len(sources) == 1:
            source = sources[0]
            result["source"] = {
                "object_name": str(getattr(source, "Name", "") or ""),
                "label": str(getattr(source, "Label", "") or ""),
            }
        artifact = str(getattr(stock, "ArtifactSHA256", "") or "")
        if artifact:
            result["artifact_sha256"] = artifact
    result["state_sha256"] = _digest(result)
    return result


def _placement_value(value: Mapping[str, Any]) -> Any:
    import FreeCAD

    origin = value["origin_mm"]
    rotation = value["rotation"]
    axis = rotation["axis"]
    return FreeCAD.Placement(
        FreeCAD.Vector(origin["x"], origin["y"], origin["z"]),
        FreeCAD.Rotation(
            FreeCAD.Vector(axis["x"], axis["y"], axis["z"]),
            rotation["angle_degrees"],
        ),
    )


def _validate_model_bounds(job: Any, allowance: Mapping[str, float]) -> None:
    import Path.Main.Stock as PathStock

    bounds = PathStock.shapeBoundBox(job.Model.Group)
    if bounds is None:
        raise ValueError("model_bounds stock requires bounded Job models")
    sizes = (
        bounds.XLength + allowance["x_negative"] + allowance["x_positive"],
        bounds.YLength + allowance["y_negative"] + allowance["y_positive"],
        bounds.ZLength + allowance["z_negative"] + allowance["z_positive"],
    )
    if any(size <= 0.001 for size in sizes):
        raise ValueError("model_bounds allowances must leave positive stock dimensions")


def _validate_existing_source(job: Any, source: Any) -> None:
    import Path.Main.Stock as PathStock

    if source not in PathStock.existingSolidCandidates(job):
        raise ValueError("existing stock source is not an available solid in this document")


def validate_stock_configuration(
    job: Any,
    specification: Mapping[str, Any],
    *,
    source: Any | None = None,
) -> dict[str, Any]:
    """Validate stock values and their exact Job-dependent prerequisites."""

    stock = normalize_stock_specification(specification)
    if stock["kind"] == "model_bounds":
        _validate_model_bounds(job, stock["allowance_mm"])
    elif stock["kind"] == "existing_solid":
        if source is None:
            raise ValueError("existing_solid stock requires its resolved source")
        _validate_existing_source(job, source)
    return stock


def _create_stock(job: Any, stock: Mapping[str, Any], source: Any | None) -> Any:
    import FreeCAD
    import Path.Main.Job as PathJob
    import Path.Main.Stock as PathStock

    placement = (
        _placement_value(stock["placement"])
        if "placement" in stock
        else None
    )
    kind = stock["kind"]
    if kind == "model_bounds":
        allowance = stock["allowance_mm"]
        return PathStock.CreateFromBase(
            job,
            FreeCAD.Vector(
                allowance["x_negative"],
                allowance["y_negative"],
                allowance["z_negative"],
            ),
            FreeCAD.Vector(
                allowance["x_positive"],
                allowance["y_positive"],
                allowance["z_positive"],
            ),
            placement,
        )
    if kind == "box":
        size = stock["size_mm"]
        return PathStock.CreateBox(
            job,
            FreeCAD.Vector(size["x"], size["y"], size["z"]),
            placement,
        )
    if kind == "cylinder":
        return PathStock.CreateCylinder(
            job,
            stock["radius_mm"],
            stock["height_mm"],
            placement,
        )
    clone = PathJob.createResourceClone(
        job,
        source,
        "Stock",
        "Stock",
    )
    clone.ViewObject.Visibility = True
    PathStock.SetupStockObject(clone, PathStock.StockType.Unknown)
    if placement is not None:
        clone.Placement = placement
    executor = getattr(getattr(clone, "Proxy", None), "execute", None)
    if callable(executor):
        executor(clone)
    return clone


def replace_stock(job: Any, create_stock: Any, timeline_edit: Any) -> Any:
    """Replace one Job's stock and reconcile its durable resource identity."""

    import Path.Base.Util as PathUtil
    import Path.Main.Stock as PathStock

    if not callable(create_stock):
        raise TypeError("CAM stock replacement requires one stock factory")
    old_stock = getattr(job, "Stock", None)
    if old_stock is not None:
        old_identity = (str(old_stock.Name), int(old_stock.ID))
        job.Document.removeObject(old_stock.Name)
        stock = create_stock()
        PathUtil._recordTimelineResourceGraphReplacementIdentity(
            job,
            timeline_edit,
            old_identity,
            stock,
        )
    else:
        stock = create_stock()
        PathUtil.recordTimelineResourceGraphAddition(job, timeline_edit, (stock,))
    job.Stock = stock
    PathStock.ApplyStockViewDefaults(stock)
    return stock


def apply_stock_configuration(
    job: Any,
    specification: Mapping[str, Any],
    *,
    source: Any | None = None,
    timeline_edit: Any | None = None,
) -> dict[str, Any]:
    """Validate then apply one exact stock configuration to one CAM Job."""

    import Path.Base.Util as PathUtil

    stock = validate_stock_configuration(job, specification, source=source)

    current = getattr(job, "Stock", None)
    current_kind = _stock_kind(current) if current is not None else None
    same_existing = bool(
        current_kind == "existing_solid"
        and source is not None
        and tuple(getattr(current, "Objects", ()) or ()) == (source,)
    )
    if current_kind == stock["kind"] and (
        stock["kind"] != "existing_solid" or same_existing
    ):
        if stock["kind"] == "model_bounds":
            values = stock["allowance_mm"]
            for name, prop in (
                ("x_negative", "ExtXneg"),
                ("x_positive", "ExtXpos"),
                ("y_negative", "ExtYneg"),
                ("y_positive", "ExtYpos"),
                ("z_negative", "ExtZneg"),
                ("z_positive", "ExtZpos"),
            ):
                setattr(current, prop, values[name])
        elif stock["kind"] == "box":
            current.Length = stock["size_mm"]["x"]
            current.Width = stock["size_mm"]["y"]
            current.Height = stock["size_mm"]["z"]
        elif stock["kind"] == "cylinder":
            current.Radius = stock["radius_mm"]
            current.Height = stock["height_mm"]
        if "placement" in stock:
            current.Placement = _placement_value(stock["placement"])
        executor = getattr(getattr(current, "Proxy", None), "execute", None)
        if callable(executor):
            executor(current)
        return stock_configuration_state(job)

    owned_edit = timeline_edit is None
    edit = timeline_edit or PathUtil.stageTimelineResourceGraphEdit(job)
    replace_stock(
        job,
        lambda: _create_stock(job, stock, source),
        edit,
    )
    if owned_edit:
        PathUtil.finalizeTimelineResourceGraphEdit(job, edit)
    return stock_configuration_state(job)
