# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared human and provider presentation of OpenFOAM fields."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeResultState import openfoam_flow_summary_state


FLOW_PRESENTATION_FIELDS = {
    "pressure": "Pressure",
    "velocity": "Velocity",
    "turbulent_kinetic_energy": "Turbulent Kinetic Energy",
    "specific_dissipation_rate": "Specific Dissipation Rate",
    "turbulent_kinematic_viscosity": "Turbulent Kinematic Viscosity",
}


def present_flow_result(result: Any, field: str, *, visible: bool = True) -> dict[str, Any]:
    """Show one normalized OpenFOAM field on its existing result pipeline."""

    document = getattr(result, "Document", None)
    if document is None or document.getObject(str(getattr(result, "Name", ""))) is not result:
        raise NativeAnalyzeError("The OpenFOAM result is no longer live.")
    if openfoam_flow_summary_state(result) is None:
        raise NativeAnalyzeError(
            "The selected object is not a completed OpenFOAM result.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    native_field = FLOW_PRESENTATION_FIELDS.get(str(field))
    if native_field is None or type(visible) is not bool:
        raise NativeAnalyzeError(
            "Flow presentation requires a published flow field and a boolean visibility."
        )
    view = getattr(result, "ViewObject", None)
    if view is None:
        raise NativeAnalyzeError("The OpenFOAM result has no presentation object.")
    available = tuple(view.getEnumerationsOfProperty("Field") or ())
    if native_field not in available:
        raise NativeAnalyzeError(
            f"The OpenFOAM result does not contain {native_field}."
        )
    previous = {
        "field": str(getattr(view, "Field", "")),
        "component": str(getattr(view, "Component", "")),
        "visible": bool(getattr(view, "Visibility", False)),
    }
    try:
        view.Field = native_field
        if native_field == "Velocity":
            components = tuple(view.getEnumerationsOfProperty("Component") or ())
            if "Magnitude" in components:
                view.Component = "Magnitude"
        view.Visibility = visible
        current = {
            "field": str(view.Field),
            "component": str(view.Component),
            "visible": bool(view.Visibility),
        }
        if current["field"] != native_field or current["visible"] is not visible:
            raise RuntimeError("presentation values were not retained")
        return {
            "changed": current != previous,
            "previous_presentation": previous,
            "presentation": current,
            "result_name": str(result.Name),
        }
    except Exception as exc:
        try:
            view.Field = previous["field"]
            if previous["component"] in tuple(
                view.getEnumerationsOfProperty("Component") or ()
            ):
                view.Component = previous["component"]
            view.Visibility = previous["visible"]
        except Exception:
            pass
        if isinstance(exc, NativeAnalyzeError):
            raise
        raise NativeAnalyzeError(
            f"The OpenFOAM result presentation could not be applied: {exc}",
            error_code="NATIVE_ANALYZE_PRESENTATION_FAILED",
        ) from exc
