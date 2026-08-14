# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state and targets for Elmer equation resources."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzePropertyState import bounded_fem_properties
from VibeCADNativeAnalyzeSolverState import solver_kind
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeSnapshot import concise_object
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_KINDS = {
    "Fem::EquationElmerElasticity": "elasticity",
    "Fem::EquationElmerDeformation": "deformation",
    "Fem::EquationElmerElectrostatic": "electrostatic",
    "Fem::EquationElmerElectricforce": "electric_force",
    "Fem::EquationElmerMagnetodynamic": "magnetodynamic",
    "Fem::EquationElmerMagnetodynamic2D": "magnetodynamic_2d",
    "Fem::EquationElmerStaticCurrent": "static_current",
    "Fem::EquationElmerFlow": "flow",
    "Fem::EquationElmerFlux": "flux",
    "Fem::EquationElmerHeat": "heat",
}
_EXCLUDED_PROPERTIES = frozenset(
    {
        "ExpressionEngine",
        "Label",
        "Label2",
        "Placement",
        "Proxy",
        "State",
        "VibeCADTimelineOwner",
        "VibeCADTimelineRole",
    }
)


@dataclass(frozen=True, slots=True)
class PreparedEquationTarget:
    equation: Any
    kind: str
    solver: Any
    expected_state_sha256: str


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def equation_kind(obj: Any) -> str:
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    kind = _KINDS.get(proxy_type)
    if kind is None:
        raise NativeAnalyzeError(
            "The exact target is not a supported Elmer equation.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    return kind


def _owner_solver(document: Any, equation: Any) -> Any:
    owners = []
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        try:
            if solver_kind(obj) == "elmer" and equation in tuple(obj.Group or ()):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError("The Elmer equation must belong to exactly one solver.")
    return owners[0]


def equation_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The Elmer equation is no longer live.")
    kind = equation_kind(obj)
    solver = _owner_solver(document, obj)
    settings = bounded_fem_properties(obj, excluded_names=_EXCLUDED_PROPERTIES)
    role = str(getattr(obj, "VibeCADTimelineRole", "") or "")
    owner = getattr(obj, "VibeCADTimelineOwner", None)
    owner_identity = [str(owner.Name), int(owner.ID)] if is_live(document, owner) else None
    result = {
        **concise_object(obj),
        "equation_kind": kind,
        "solver": str(solver.Name),
        "priority": int(getattr(obj, "Priority", 0)),
        "settings": settings,
    }
    if role:
        result["timeline_role"] = role
    if owner_identity is not None:
        result["timeline_owner"] = owner_identity[0]
    result["state_sha256"] = _digest(
        {
            "object_name": str(obj.Name),
            "object_id": int(obj.ID),
            "label": str(obj.Label),
            "proxy_type": str(getattr(obj.Proxy, "Type", "") or ""),
            "solver": [str(solver.Name), int(solver.ID)],
            "settings": settings,
            "timeline_role": role,
            "timeline_owner": owner_identity,
        }
    )
    return result


def prepare_equation_target(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedEquationTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "equation target must contain only object_name and expected_state_sha256."
        )
    equation = resolve_object(
        document,
        NativeObjectRef(document_uid, str(value["object_name"])),
    )
    state = equation_state(equation)
    expected = str(value["expected_state_sha256"] or "")
    if state["state_sha256"] != expected:
        raise NativeAnalyzeError(
            "The exact Elmer equation changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "equation": {"object_name": str(equation.Name)},
                "current_state_sha256": state["state_sha256"],
            },
        )
    return PreparedEquationTarget(equation, state["equation_kind"], _owner_solver(document, equation), expected)
