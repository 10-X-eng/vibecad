# SPDX-License-Identifier: LGPL-2.1-or-later

"""Public Aero helper for workbench commands and agent-control ``/v1/run``.

Example from Grok Bot (no SendKeys)::

    import VibeCADAero
    result = VibeCADAero.run_analyze(App.ActiveDocument)
"""

from __future__ import annotations

from typing import Any

import AeroAirfoil
import AeroConfig
import AeroJSBSim
import AeroResults
import AeroSolvers
from AeroSolvers import AeroDependencyError

__all__ = [
    "AeroDependencyError",
    "export_jsbsim",
    "run_analyze",
    "run_section",
    "run_vlm",
    "write_report",
]


def run_analyze(
    doc: Any | None = None,
    *,
    spreadsheet: bool = False,
    markdown: bool = False,
    export_plant: bool = False,
) -> dict[str, Any]:
    """Run section + 3D + hover and write ``AeroReport`` onto ``doc``."""

    return _run(
        doc,
        run_section_solve=True,
        run_vlm_solve=True,
        spreadsheet=spreadsheet,
        markdown=markdown,
        export_plant=export_plant,
    )


def run_section(doc: Any | None = None) -> dict[str, Any]:
    return _run(doc, run_section_solve=True, run_vlm_solve=False)


def run_vlm(doc: Any | None = None) -> dict[str, Any]:
    return _run(doc, run_section_solve=False, run_vlm_solve=True)


def export_jsbsim(doc: Any | None = None, results: dict[str, Any] | None = None) -> dict[str, Any]:
    document = _require_doc(doc)
    payload = results or _results_from_report(document)
    if payload is None:
        solved = run_analyze(document)
        if not solved.get("ok"):
            return solved
        payload = solved
    else:
        _merge_resolved_plant_geometry(payload, document)
    written = AeroJSBSim.write_plant(
        payload,
        output_dir=AeroJSBSim.default_output_dir(document),
    )
    AeroResults.write_report(
        document,
        payload,
        jsbsim_path=written["fdm_path"],
        jsbsim_boot_error=written.get("boot_error") or "",
    )
    return {"ok": True, **written, **{k: payload.get(k) for k in ("CL", "CD", "source")}}


def write_report(doc: Any, payload: dict[str, Any], **kwargs: Any) -> Any:
    return AeroResults.write_report(doc, payload, **kwargs)


def _run(
    doc: Any | None,
    *,
    run_section_solve: bool,
    run_vlm_solve: bool,
    spreadsheet: bool = False,
    markdown: bool = False,
    export_plant: bool = False,
) -> dict[str, Any]:
    try:
        document = _require_doc(doc)
        cfg = AeroConfig.resolve_geometry(document)
        _ensure_aeroconfig(document, cfg)
        coords, airfoil_source = AeroAirfoil.load_airfoil_coordinates(cfg["airfoil"])
        payload = AeroSolvers.analyze(
            cfg,
            coords=coords,
            run_section_solve=run_section_solve,
            run_vlm_solve=run_vlm_solve,
        )
        payload["airfoil_source"] = airfoil_source
        jsbsim_path = None
        boot = ""
        if export_plant:
            written = AeroJSBSim.write_plant(
                payload,
                output_dir=AeroJSBSim.default_output_dir(document),
            )
            jsbsim_path = written["fdm_path"]
            boot = written.get("boot_error") or ""
            payload["jsbsim"] = written
        AeroResults.write_report(
            document,
            payload,
            spreadsheet=spreadsheet,
            markdown=markdown,
            jsbsim_path=jsbsim_path,
            jsbsim_boot_error=boot or "",
        )
        return {
            "ok": True,
            **payload,
            "jsbsim_path": jsbsim_path,
            "jsbsim_boot_error": boot,
        }
    except AeroDependencyError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _require_doc(doc: Any | None) -> Any:
    if doc is not None:
        return doc
    try:
        import FreeCAD

        active = FreeCAD.ActiveDocument
        if active is not None:
            return active
        return FreeCAD.newDocument("Aero")
    except Exception as exc:
        raise AeroDependencyError(
            "No document is available. Open a document or pass one to "
            f"VibeCADAero.run_analyze(doc). ({exc})"
        ) from exc


def _ensure_aeroconfig(doc: Any, cfg: dict[str, Any]) -> Any | None:
    # Never persist a one-shot bbox inference; a wild loft would lock later runs.
    if cfg.get("geometry_source") == "inferred":
        return None
    adder = getattr(doc, "addObject", None)
    if not callable(adder):
        return None
    obj = None
    getter = getattr(doc, "getObject", None)
    if callable(getter):
        obj = getter("AeroConfig")
    if obj is None:
        try:
            obj = adder("App::FeaturePython", "AeroConfig")
        except Exception:
            return None
    for key in (
        "span_mm",
        "chord_mm",
        "gap_c",
        "stagger_c",
        "decalage_deg",
        "auw_g",
        "airfoil",
        "alpha_deg",
        "n_props",
        "prop_diameter_mm",
        "thrust_to_weight",
        "vehicle_type",
    ):
        if not hasattr(obj, key):
            try:
                typ = (
                    "App::PropertyString"
                    if key in ("airfoil", "vehicle_type")
                    else "App::PropertyFloat"
                )
                obj.addProperty(typ, key, "Aero", key)
            except Exception:
                setattr(obj, key, cfg.get(key))
                continue
        try:
            setattr(obj, key, cfg.get(key))
        except Exception:
            pass
    return obj


_PLANT_GEOMETRY_KEYS = (
    "reference_area_m2",
    "span_m",
    "chord_m",
    "mass_kg",
    "xyz_ref",
    "alpha_deg",
    "span_mm",
    "chord_mm",
)


def _results_from_report(doc: Any) -> dict[str, Any] | None:
    getter = getattr(doc, "getObject", None)
    obj = getter("AeroReport") if callable(getter) else None
    if obj is None:
        return None
    if getattr(obj, "CL", None) is None:
        return None
    payload = {
        "CL": obj.CL,
        "CD": getattr(obj, "CD", 0.0),
        "CM": getattr(obj, "CM", 0.0),
        "CLalpha": getattr(obj, "CLalpha", 0.0),
        "Cmalpha": getattr(obj, "Cmalpha", 0.0),
        "Re": getattr(obj, "Re", 0.0),
        "V_loaf": getattr(obj, "V_loaf", 0.0),
        "P_hover": getattr(obj, "P_hover", 0.0),
        "P_cruise": getattr(obj, "P_cruise", 0.0),
        "source": getattr(obj, "Source", ""),
        "airfoil": getattr(obj, "Airfoil", "e63"),
        "geometry_source": getattr(obj, "GeometrySource", ""),
        "PitchUnstable": getattr(obj, "PitchUnstable", False),
        "hover": {"source": getattr(obj, "HoverSource", "momentum-theory")},
        "span_mm": getattr(obj, "span_mm", None),
        "chord_mm": getattr(obj, "chord_mm", None),
        "span_m": getattr(obj, "span_m", None),
        "chord_m": getattr(obj, "chord_m", None),
        "reference_area_m2": getattr(obj, "reference_area_m2", None),
        "mass_kg": getattr(obj, "mass_kg", None),
        "alpha_deg": getattr(obj, "alpha_deg", None),
        "xyz_ref": _xyz_ref_from_report(obj),
    }
    if payload["span_m"] is None and payload["span_mm"] is not None:
        payload["span_m"] = float(payload["span_mm"]) / 1000.0
    if payload["chord_m"] is None and payload["chord_mm"] is not None:
        payload["chord_m"] = float(payload["chord_mm"]) / 1000.0
    if payload["reference_area_m2"] is None and payload["span_m"] and payload["chord_m"]:
        payload["reference_area_m2"] = 2.0 * float(payload["span_m"]) * float(payload["chord_m"])
    _merge_resolved_plant_geometry(payload, doc)
    return payload


def _xyz_ref_from_report(obj: Any) -> list[float] | None:
    raw = getattr(obj, "xyz_ref", None)
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return [float(raw[0]), float(raw[1]), float(raw[2])]
    if raw is not None and all(hasattr(raw, axis) for axis in ("x", "y", "z")):
        return [float(raw.x), float(raw.y), float(raw.z)]
    x = getattr(obj, "xyz_ref_x", None)
    y = getattr(obj, "xyz_ref_y", None)
    z = getattr(obj, "xyz_ref_z", None)
    if x is None or y is None or z is None:
        return None
    return [float(x), float(y), float(z)]


def _missing_plant_value(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, (list, tuple)) and len(value) < 3:
        return True
    return False


def _merge_resolved_plant_geometry(payload: dict[str, Any], doc: Any) -> None:
    missing = [key for key in _PLANT_GEOMETRY_KEYS if _missing_plant_value(payload.get(key))]
    if not missing:
        return
    try:
        cfg = AeroConfig.resolve_geometry(doc)
    except Exception:
        return
    for key in missing:
        value = cfg.get(key)
        if not _missing_plant_value(value):
            payload[key] = value
    if payload.get("reference_area_m2") is None and payload.get("span_m") and payload.get("chord_m"):
        payload["reference_area_m2"] = 2.0 * float(payload["span_m"]) * float(payload["chord_m"])
