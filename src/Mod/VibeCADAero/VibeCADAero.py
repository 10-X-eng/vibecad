# SPDX-License-Identifier: LGPL-2.1-or-later

"""Public Aero helper for ribbon commands and Native ``aero.solve`` tools.

Analyze does not move CAD. Repair is propose/apply. JSBSim and Report use
the last solve. Agent-control should call these functions, not raw exec
that bypasses the stamps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import AeroAirfoil
import AeroConfig
import AeroFlightCard
import AeroJSBSim
import AeroMass
import AeroPreview
import AeroRepair
import AeroResults
import AeroSolvers
import AeroStamp
from AeroSolvers import AeroDependencyError

__all__ = [
    "AeroDependencyError",
    "apply_repairs",
    "export_jsbsim",
    "reject_repairs",
    "flight_card",
    "prepare_jsbsim_payload",
    "propose_repairs",
    "run_analyze",
    "run_section",
    "run_vlm",
    "write_last_report",
    "write_report",
]


def run_analyze(
    doc: Any | None = None,
    *,
    spreadsheet: bool = False,
    markdown: bool = False,
    export_plant: bool = False,
    repair: bool = False,
) -> dict[str, Any]:
    """Run section + 3D + hover and write ``AeroReport``. Does not repair CAD."""

    return _run(
        doc,
        run_section_solve=True,
        run_vlm_solve=True,
        spreadsheet=spreadsheet,
        markdown=markdown,
        export_plant=export_plant,
        repair=repair,
    )


def run_section(doc: Any | None = None) -> dict[str, Any]:
    return _run(doc, run_section_solve=True, run_vlm_solve=False, repair=False)


def run_vlm(doc: Any | None = None) -> dict[str, Any]:
    return _run(doc, run_section_solve=False, run_vlm_solve=True, repair=False)


def prepare_jsbsim_payload(
    doc: Any | None = None,
    results: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve JSBSim input without writing files or mutating the document."""

    document = _require_doc(doc)
    payload = results if results is not None else _results_from_report(document)
    if payload is None:
        return None
    _merge_resolved_plant_geometry(payload, document)
    return payload


def export_jsbsim(
    doc: Any | None = None,
    results: dict[str, Any] | None = None,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    document = _require_doc(doc)
    payload = prepare_jsbsim_payload(document, results)
    if payload is None:
        return {
            "ok": False,
            "error": "No AeroReport. Run Analyze before exporting JSBSim.",
            **AeroStamp.stamp(
                state=AeroStamp.STATE_WAITING,
                ceiling=AeroStamp.CEILING_NOT_AIRWORTHY,
                method="jsbsim",
            ),
        }
    written = AeroJSBSim.write_plant(
        payload,
        output_dir=(
            output_dir
            if output_dir is not None
            else AeroJSBSim.default_output_dir(document)
        ),
    )
    AeroResults.write_report(
        document,
        payload,
        jsbsim_path=written["fdm_path"],
        jsbsim_boot_error=written.get("boot_error") or "",
    )
    return {
        "ok": True,
        **written,
        **{k: payload.get(k) for k in ("CL", "CD", "source")},
        **AeroStamp.analysis_stamp(payload.get("source")),
    }


def write_last_report(
    doc: Any | None = None,
    *,
    spreadsheet: bool = True,
    markdown: bool = True,
) -> dict[str, Any]:
    document = _require_doc(doc)
    payload = _results_from_report(document)
    if payload is None:
        return {
            "ok": False,
            "error": "No AeroReport. Run Analyze before writing a report.",
            **AeroStamp.stamp(
                state=AeroStamp.STATE_WAITING,
                ceiling=AeroStamp.CEILING_NOT_AIRWORTHY,
                method="report",
            ),
        }
    AeroResults.write_report(
        document,
        payload,
        spreadsheet=spreadsheet,
        markdown=markdown,
    )
    return {"ok": True, **payload, **AeroStamp.analysis_stamp(payload.get("source"))}


def write_report(doc: Any, payload: dict[str, Any], **kwargs: Any) -> Any:
    return AeroResults.write_report(doc, payload, **kwargs)


def propose_repairs(
    doc: Any | None = None,
    *,
    native_revision: str | None = None,
) -> dict[str, Any]:
    document = _require_doc(doc)
    payload = _results_from_report(document)
    if payload is None:
        return {
            "ok": False,
            "error": "No AeroReport. Run Analyze before proposing repairs.",
            **AeroStamp.stamp(
                state=AeroStamp.STATE_WAITING,
                ceiling=AeroStamp.CEILING_NOT_AIRWORTHY,
                method="repair_preview",
            ),
        }
    cfg = AeroConfig.resolve_geometry(document)
    proposals = AeroRepair.propose_repairs(cfg, payload, document)
    revision = AeroPreview.geometry_revision(document, cfg)
    AeroPreview.write_preview(
        document,
        revision=revision,
        proposals=proposals,
        native_revision=native_revision,
    )
    return {
        "ok": True,
        "revision": revision,
        "native_revision": native_revision,
        "proposals": proposals,
        "count": len(proposals),
        **AeroStamp.stamp(
            state=AeroStamp.STATE_UNQUALIFIED,
            ceiling=AeroStamp.CEILING_NOT_AIRWORTHY,
            method="repair_preview",
        ),
    }


def apply_repairs(
    doc: Any | None = None,
    *,
    native_revision: str | None = None,
    manage_transaction: bool = True,
) -> dict[str, Any]:
    document = _require_doc(doc)
    cfg = AeroConfig.resolve_geometry(document)
    revision = AeroPreview.geometry_revision(document, cfg)
    try:
        proposals = AeroPreview.validate_preview(
            document, revision, native_revision=native_revision
        )
    except AeroPreview.PreviewError as exc:
        return _repair_apply_rejected(exc.reason)

    owns_transaction = False
    if manage_transaction and _transaction_is_active(document):
        return _repair_apply_rejected("transaction_active")
    try:
        if manage_transaction and _can_manage_transaction(document):
            document.openTransaction("Apply Aero Repairs")
            owns_transaction = True
        landed = AeroRepair.apply_repairs(document, cfg, proposals)
        if proposals and not landed:
            raise RuntimeError("The repair preview did not change configuration or CAD.")
        recompute = getattr(document, "recompute", None)
        if callable(recompute):
            outcome = recompute()
            if outcome is False:
                raise RuntimeError("The repaired Aero document failed to recompute.")
        AeroPreview.mark_preview_consumed(
            document,
            revision,
            native_revision=native_revision,
        )
        if owns_transaction:
            document.commitTransaction()
            owns_transaction = False
    except Exception:
        if owns_transaction:
            try:
                document.abortTransaction()
            except Exception:
                return _repair_apply_rejected("rollback_failed")
        return _repair_apply_rejected("apply_failed")
    return {
        "ok": True,
        "landed": landed,
        "count": len(landed),
        "revision_before": revision,
        **AeroStamp.stamp(
            state=AeroStamp.STATE_PASS,
            ceiling=AeroStamp.CEILING_GEOMETRY_APPLIED,
            method="repair_apply",
        ),
    }


def _repair_apply_rejected(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"Repair apply rejected: {reason}",
        **AeroStamp.stamp(
            state=AeroStamp.STATE_REJECTED,
            ceiling=AeroStamp.CEILING_GEOMETRY_APPLIED,
            method="repair_apply",
            extra={"reason": reason},
        ),
    }


def _transaction_is_active(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    try:
        booked_id = int(booked() or 0) if callable(booked) else 0
    except Exception:
        booked_id = 0
    return bool(getattr(document, "HasPendingTransaction", False) or booked_id)


def _can_manage_transaction(document: Any) -> bool:
    return all(
        callable(getattr(document, name, None))
        for name in ("openTransaction", "commitTransaction", "abortTransaction")
    )


def reject_repairs(doc: Any | None = None) -> dict[str, Any]:
    document = _require_doc(doc)
    discarded = AeroPreview.discard_preview(document)
    if discarded is None:
        return {
            "ok": False,
            "error": "No repair preview to reject.",
            **AeroStamp.stamp(
                state=AeroStamp.STATE_REJECTED,
                ceiling=AeroStamp.CEILING_GEOMETRY_APPLIED,
                method="repair_reject",
                extra={"reason": "missing"},
            ),
        }
    return {
        "ok": True,
        "rejected": True,
        **AeroStamp.stamp(
            state=AeroStamp.STATE_REJECTED,
            ceiling=AeroStamp.CEILING_GEOMETRY_APPLIED,
            method="repair_reject",
        ),
    }


def flight_card(doc: Any | None = None) -> dict[str, Any]:
    document = _require_doc(doc)
    cfg = AeroConfig.resolve_geometry(document)
    results = _results_from_report(document)
    mass = AeroMass.measure_document(document, cfg)
    card = AeroFlightCard.build_card(cfg, results, mass)
    return {"ok": True, **card}


def _run(
    doc: Any | None,
    *,
    run_section_solve: bool,
    run_vlm_solve: bool,
    spreadsheet: bool = False,
    markdown: bool = False,
    export_plant: bool = False,
    repair: bool = False,
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
        changes: list[dict[str, Any]] = []
        repair_passes = 0
        if repair:
            for _pass in range(AeroRepair.MAX_REPAIR_PASSES):
                if not payload.get("PitchUnstable") and not _positive_cmalpha(payload):
                    break
                proposed = AeroRepair.propose_repairs(cfg, payload, document)
                landed = AeroRepair.apply_repairs(document, cfg, proposed)
                if not landed:
                    break
                repair_passes += 1
                changes.extend(landed)
                cfg = AeroConfig.resolve_geometry(document)
                payload = AeroSolvers.analyze(
                    cfg,
                    coords=coords,
                    run_section_solve=run_section_solve,
                    run_vlm_solve=run_vlm_solve,
                )
                payload["airfoil_source"] = airfoil_source
        payload["changes"] = changes
        payload["RepairPasses"] = repair_passes
        payload["Corrections"] = [
            str(item.get("sentence") or "") for item in changes if item.get("sentence")
        ]
        payload["user_message"] = AeroRepair.format_user_message(
            changes, payload, repair_passes
        )
        mass = AeroMass.measure_document(document, cfg)
        payload["mass"] = mass
        payload["flight_card"] = AeroFlightCard.build_card(cfg, payload, mass)
        payload.update(AeroStamp.analysis_stamp(payload.get("source")))
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
            "changes": changes,
            "user_message": payload["user_message"],
            "jsbsim_path": jsbsim_path,
            "jsbsim_boot_error": boot,
        }
    except AeroDependencyError as exc:
        return {
            "ok": False,
            "error": str(exc),
            **AeroStamp.stamp(
                state=AeroStamp.STATE_UNAVAILABLE,
                ceiling=AeroStamp.CEILING_NOT_AIRWORTHY,
                method="missing_backend",
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            **AeroStamp.stamp(
                state=AeroStamp.STATE_FAILED,
                ceiling=AeroStamp.CEILING_NOT_AIRWORTHY,
                method="exception",
            ),
        }


def _positive_cmalpha(payload: dict[str, Any]) -> bool:
    value = payload.get("Cmalpha")
    return value is not None and float(value) > 0.0


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
        "boom_length_mm",
        "tail_span_mm",
        "tail_chord_mm",
        "xyz_ref_c",
        "thrust_to_weight",
        "vehicle_type",
        "battery_wh",
        "airframe_density_kg_m3",
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
