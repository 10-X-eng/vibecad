# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native CAM job for exact shaped model objects."""

from __future__ import annotations

from typing import Any
import math

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "cam.create_job",
    "description": (
        "Create one native CAM job (Path::FeaturePython) machining exact "
        "shaped model objects. The job gets a stock body enlarged from the "
        "model bounding box by the given margins, plus empty Tools and "
        "Operations groups. Add a tool with cam.add_tool before adding "
        "operations with cam.add_operation."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "CAMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Visible label for the new CAM job.",
            },
            "model_object_names": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {
                    "type": "string",
                    "description": (
                        "Exact internal name of one shaped object to machine."
                    ),
                },
                "description": (
                    "Exact internal names of the shaped model objects this "
                    "job machines; the job clones them, sources are unchanged."
                ),
            },
            "stock_margins_mm": {
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Extra stock in mm added on both the negative "
                            "and positive X sides of the model bounding box."
                        ),
                    },
                    "y": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Extra stock in mm added on both the negative "
                            "and positive Y sides of the model bounding box."
                        ),
                    },
                    "z": {
                        "type": "number",
                        "minimum": 0,
                        "description": (
                            "Extra stock in mm added on both the bottom and "
                            "top Z sides of the model bounding box."
                        ),
                    },
                },
                "required": ["x", "y", "z"],
                "additionalProperties": False,
                "description": (
                    "Stock allowance in mm beyond the model bounding box on "
                    "each axis; 1-2 mm is typical for milling from slightly "
                    "oversized blanks."
                ),
            },
        },
        "required": ["label", "model_object_names", "stock_margins_mm"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    label: str,
    model_object_names: list[str],
    stock_margins_mm: dict[str, Any],
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    clean_names: list[str] = []
    model_preflight: list[dict[str, Any]] = []
    for raw_name in model_object_names or []:
        name = str(raw_name or "").strip()
        if not name:
            return _invalid("model_object_names entries must be non-empty.")
        obj = doc.getObject(name)
        if obj is None:
            return _invalid(f"Object not found by exact internal name: {name}")
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            return _invalid(
                f"Object has no shape geometry to machine: {name}. "
                "CAM jobs need shaped BREP objects."
            )
        if not bool(shape.isValid()) or len(list(shape.Solids)) < 1:
            return _invalid(
                f"Object is not a valid solid model: {name}.",
                model={
                    "object": name,
                    "shape_valid": bool(shape.isValid()),
                    "solid_count": len(list(shape.Solids)),
                    "bounds": domain_runtime.bound_box_summary(shape.BoundBox),
                },
            )
        if name in clean_names:
            return _invalid(f"Duplicate model object: {name}")
        clean_names.append(name)
        model_preflight.append(
            {
                "object": name,
                "shape_valid": True,
                "solid_count": len(list(shape.Solids)),
                "bounds": domain_runtime.bound_box_summary(shape.BoundBox),
            }
        )
    if not clean_names:
        return _invalid("At least one model object is required.")
    try:
        import Path.Main.Job as PathJob
    except ImportError:
        return _invalid(
            "The CAM workbench is not available in this FreeCAD build; "
            "jobs cannot be created."
        )
    if not isinstance(stock_margins_mm, dict):
        return _invalid("stock_margins_mm must be an object with x, y, and z.")
    missing_axes = [axis for axis in ("x", "y", "z") if axis not in stock_margins_mm]
    if missing_axes:
        return _invalid(
            f"stock_margins_mm is missing: {', '.join(missing_axes)}."
        )
    margins = {axis: float(stock_margins_mm[axis]) for axis in ("x", "y", "z")}
    if any(not math.isfinite(value) or value < 0.0 for value in margins.values()):
        return _invalid("Every stock margin must be a finite nonnegative number.")

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import Path.Base.Util as PathUtil
        import Path.Main.Stock as PathStock

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        models = []
        initially_visible_models = []
        for name in clean_names:
            model = active.getObject(name)
            if model is None:
                raise RuntimeError(f"Model object no longer exists: {name}")
            models.append(model)
            view = getattr(model, "ViewObject", None)
            if (
                view is not None
                and hasattr(view, "Visibility")
                and bool(view.Visibility)
            ):
                initially_visible_models.append(model)
        job = PathJob.Create(
            "Job",
            models,
            None,
            createDefaultToolController=False,
            createDefaultStock=True,
        )
        PathUtil.markTimelineReplacedInputs(
            job,
            initially_visible_models,
        )
        job.Label = clean_label
        stock = getattr(job, "Stock", None)
        replaced_stock = None
        stock_is_from_base = (
            stock is not None and type(getattr(stock, "Proxy", None)) is PathStock.StockFromBase
        )
        if not stock_is_from_base:
            if stock is None:
                raise RuntimeError(
                    "Native CAM Job creation did not provide a tracked Stock resource."
                )
            replaced_stock = stock.Name
            stock_replacement = PathUtil.stageTimelineDirectResourceReplacement(
                job,
                stock,
            )
            job.Stock = None
            active.removeObject(stock.Name)
            vector = App.Vector(margins["x"], margins["y"], margins["z"])
            stock = PathStock.CreateFromBase(job, neg=vector, pos=vector)
            job.Stock = stock
            PathUtil.finalizeTimelineDirectResourceReplacement(
                job,
                stock_replacement,
                stock,
            )
        required_stock_properties = [
            "Base",
            "ExtXneg",
            "ExtXpos",
            "ExtYneg",
            "ExtYpos",
            "ExtZneg",
            "ExtZpos",
            "Shape",
        ]
        missing_stock_properties = [
            name for name in required_stock_properties if not hasattr(stock, name)
        ] if stock is not None else required_stock_properties
        if missing_stock_properties:
            raise RuntimeError(
                "Native FromBase stock lacks required properties: "
                + ", ".join(missing_stock_properties)
            )
        stock.ExtXneg = margins["x"]
        stock.ExtXpos = margins["x"]
        stock.ExtYneg = margins["y"]
        stock.ExtYpos = margins["y"]
        stock.ExtZneg = margins["z"]
        stock.ExtZpos = margins["z"]
        active.recompute()
        clone_map = []
        for clone in list(job.Model.Group):
            sources = list(getattr(clone, "Objects", []) or [])
            clone_map.append(
                {
                    "clone": clone.Name,
                    "source": sources[0].Name if len(sources) == 1 else None,
                    "source_count": len(sources),
                    "path_resource": getattr(clone, "PathResource", None),
                    "bounds": domain_runtime.bound_box_summary(clone.Shape.BoundBox),
                }
            )
        model_bounds = PathStock.shapeBoundBox(job.Model.Group)
        actual_margins = {
            "x_negative": float(stock.ExtXneg.Value),
            "x_positive": float(stock.ExtXpos.Value),
            "y_negative": float(stock.ExtYneg.Value),
            "y_positive": float(stock.ExtYpos.Value),
            "z_negative": float(stock.ExtZneg.Value),
            "z_positive": float(stock.ExtZpos.Value),
        }
        return {
            "document": active.Name,
            "job": job.Name,
            "job_label": job.Label,
            "requested_models": clean_names,
            "model_preflight": model_preflight,
            "model_clone_map": clone_map,
            "groups": {
                "model": {
                    "name": job.Model.Name,
                    "members": [obj.Name for obj in job.Model.Group],
                },
                "tools": {
                    "name": job.Tools.Name,
                    "members": [obj.Name for obj in job.Tools.Group],
                },
                "operations": {
                    "name": job.Operations.Name,
                    "members": [obj.Name for obj in job.Operations.Group],
                },
            },
            "stock": {
                "object": stock.Name,
                "type": PathStock.StockType.FromBase,
                "native_proxy": type(stock.Proxy).__name__,
                "replaced_default_stock": replaced_stock,
                "requested_margins_mm": margins,
                "actual_margins_mm": actual_margins,
                "model_bounds": domain_runtime.bound_box_summary(model_bounds),
                "actual_bounds": domain_runtime.bound_box_summary(stock.Shape.BoundBox),
                "shape_valid": bool(stock.Shape.isValid()),
                "solid_count": len(list(stock.Shape.Solids)),
                "base_group": getattr(getattr(stock, "Base", None), "Name", None),
            },
            "coordinate_system": {
                "job_placement": _placement(job.Placement),
                "stock_placement": _placement(stock.Placement),
                "model_clone_placements": {
                    clone.Name: _placement(clone.Placement) for clone in job.Model.Group
                },
            },
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        job = doc.getObject(str(result.get("job") or ""))
        checks: list[dict[str, Any]] = []
        checks.append(_check("job_retained", job is not None))
        if job is None:
            return {"ok": False, "checks": checks}
        stock = getattr(job, "Stock", None)
        checks.append(_check("stock_linked", stock is not None))
        if stock is not None:
            model_bounds = job.Proxy.modelBoundBox(job)
            stock_bounds = stock.Shape.BoundBox
            expected_bounds = {
                "xmin": float(model_bounds.XMin) - margins["x"],
                "xmax": float(model_bounds.XMax) + margins["x"],
                "ymin": float(model_bounds.YMin) - margins["y"],
                "ymax": float(model_bounds.YMax) + margins["y"],
                "zmin": float(model_bounds.ZMin) - margins["z"],
                "zmax": float(model_bounds.ZMax) + margins["z"],
            }
            checks.extend(
                [
                    _check(
                        "stock_type_from_base",
                        type(getattr(stock, "Proxy", None)).__name__ == "StockFromBase",
                    ),
                    _check(
                        "stock_shape_valid",
                        not stock.Shape.isNull()
                        and bool(stock.Shape.isValid())
                        and len(list(stock.Shape.Solids)) == 1,
                    ),
                    _check(
                        "stock_margins_read_back",
                        all(
                            abs(float(getattr(stock, prop).Value) - margins[axis]) <= 1.0e-9
                            for axis, prop in (
                                ("x", "ExtXneg"),
                                ("x", "ExtXpos"),
                                ("y", "ExtYneg"),
                                ("y", "ExtYpos"),
                                ("z", "ExtZneg"),
                                ("z", "ExtZpos"),
                            )
                        ),
                    ),
                    _check(
                        "stock_bounds_match_requested_margins",
                        all(
                            abs(float(getattr(stock_bounds, native_name)) - expected) <= 1.0e-7
                            for native_name, expected in (
                                ("XMin", expected_bounds["xmin"]),
                                ("XMax", expected_bounds["xmax"]),
                                ("YMin", expected_bounds["ymin"]),
                                ("YMax", expected_bounds["ymax"]),
                                ("ZMin", expected_bounds["zmin"]),
                                ("ZMax", expected_bounds["zmax"]),
                            )
                        ),
                        expected=expected_bounds,
                        actual=domain_runtime.bound_box_summary(stock_bounds),
                    ),
                ]
            )
        clone_sources = [
            getattr(job.Proxy.baseObject(job, clone), "Name", None)
            for clone in list(getattr(job.Model, "Group", []) or [])
        ]
        checks.extend(
            [
                _check("model_clone_count", len(clone_sources) == len(clean_names), actual=clone_sources),
                _check("model_clone_sources", clone_sources == clean_names, actual=clone_sources),
                _check("tools_group_empty", len(list(job.Tools.Group)) == 0),
                _check("operations_group_empty", len(list(job.Operations.Group)) == 0),
            ]
        )
        return {"ok": all(item["ok"] for item in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create CAM job: {clean_label}",
        create,
        verify,
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "create_job"},
        next_action=(
            "Add a cutting tool with cam.add_tool, then machining "
            "operations with cam.add_operation."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _placement(value: Any) -> dict[str, Any]:
    quaternion = value.Rotation.Q
    return {
        "position": domain_runtime.vector_values(value.Base),
        "quaternion": [float(component) for component in quaternion],
    }


def _check(name: str, ok: bool, **details: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), **details}
