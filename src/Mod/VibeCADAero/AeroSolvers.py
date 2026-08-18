# SPDX-License-Identifier: LGPL-2.1-or-later

"""Voider aero stack: NeuralFoil, AeroSandbox VLM/AeroBuildup, momentum hover."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Callable

CoeffFn = Callable[..., dict[str, Any]]
SectionFn = Callable[..., dict[str, Any]]


RHO = 1.225
MU = 1.81e-5
G = 9.80665
NEURALFOIL_MODEL = "large"
VLM_SPANWISE = 8
VLM_CHORDWISE = 6
VLM_ALPHA_STEP_DEG = 2.0


class AeroDependencyError(RuntimeError):
    """Optional solver package is missing from the bundled interpreter."""


def bundled_python_command(package: str) -> str:
    executable = Path(sys.executable)
    name = executable.name.lower()
    if name in {"freecad.exe", "freecadcmd.exe", "vibecad.exe", "vibecadcmd.exe"}:
        python = executable.with_name("python.exe")
    elif name.endswith(".exe"):
        python = executable.with_name("python.exe") if name != "python.exe" else executable
    else:
        python = executable
    if python.name.lower() != "python.exe" and sys.platform.startswith("win"):
        python = Path(r"<VibeCAD>\bin\python.exe")
    if not sys.platform.startswith("win") and python.name.lower() != "python.exe":
        hint = f'"{python}" -m pip install {package}'
    else:
        display = python if python.is_absolute() else Path(r"<VibeCAD>\bin\python.exe")
        hint = f'"{display}" -m pip install {package}'
    if "python.exe" not in hint:
        hint = f'"<VibeCAD>\\bin\\python.exe" -m pip install {package}'
    return hint


def require_backend(package: str, available: bool | None = None) -> None:
    if available is None:
        available = _module_available(package)
    if available:
        return
    command = bundled_python_command(package)
    raise AeroDependencyError(
        f"{package} is not installed. Install it into VibeCAD's bundled Python:\n"
        f"  {command}\n"
        "See Mod/VibeCADAero/requirements-aero.txt. Do not vendor wheels into git."
    )


def biplane_area(span_m: float, chord_m: float) -> float:
    return 2.0 * float(span_m) * float(chord_m)


def loaf_speed(mass_kg: float, area_m2: float, cl: float) -> float:
    if cl <= 0.0 or area_m2 <= 0.0:
        raise ValueError("Loaf speed needs positive CL and reference area.")
    return math.sqrt(2.0 * float(mass_kg) * G / (RHO * float(area_m2) * float(cl)))


def reynolds(speed_mps: float, chord_m: float) -> float:
    return RHO * float(speed_mps) * float(chord_m) / MU


def hover_power(
    mass_kg: float,
    *,
    n_props: int = 2,
    prop_diameter_m: float = 0.178,
    figure_of_merit: float = 0.55,
    thrust_to_weight: float = 1.9,
) -> dict[str, Any]:
    weight = float(mass_kg) * G
    thrust = float(thrust_to_weight) * weight
    disk = float(n_props) * math.pi * (float(prop_diameter_m) / 2.0) ** 2
    induced = math.sqrt(thrust / (2.0 * RHO * disk))
    ideal = thrust * induced
    return {
        "P_hover": ideal / float(figure_of_merit),
        "source": "momentum-theory",
        "T_W": float(thrust_to_weight),
        "figure_of_merit": float(figure_of_merit),
        "n_props": int(n_props),
        "prop_diameter_m": float(prop_diameter_m),
        "thrust_N": thrust,
    }


def cruise_power(drag_n: float, speed_mps: float, prop_eta: float = 0.65) -> dict[str, Any]:
    return {
        "P_cruise": float(drag_n) * float(speed_mps) / float(prop_eta),
        "prop_eta": float(prop_eta),
    }


def pick_force_coefficients(
    aerobuildup: dict[str, Any] | None = None,
    vlm: dict[str, Any] | None = None,
    neuralfoil: dict[str, Any] | None = None,
) -> dict[str, Any]:
    for source, data in (
        ("AeroBuildup", aerobuildup),
        ("VLM", vlm),
        ("NeuralFoil", neuralfoil),
    ):
        if not data:
            continue
        cl = _coeff(data, "CL")
        if cl is None:
            continue
        return {
            "CL": cl,
            "CD": _coeff(data, "CD") or 0.0,
            "CM": _coeff(data, "CM", "Cm") or 0.0,
            "source": source,
            "analysis_confidence": _coeff(data, "analysis_confidence"),
        }
    raise AeroDependencyError(
        "No aerodynamic coefficients available. Install neuralfoil and/or "
        f"aerosandbox:\n  {bundled_python_command('neuralfoil')}\n"
        f"  {bundled_python_command('aerosandbox')}"
    )


def vlm_stability_derivatives(
    alpha1_deg: float,
    cl1: float,
    cm1: float,
    alpha2_deg: float,
    cl2: float,
    cm2: float,
) -> dict[str, float]:
    d_rad = math.radians(float(alpha2_deg) - float(alpha1_deg))
    if abs(d_rad) < 1e-12:
        raise ValueError("VLM alpha step must be non-zero.")
    return {
        "CLalpha": (float(cl2) - float(cl1)) / d_rad,
        "Cmalpha": (float(cm2) - float(cm1)) / d_rad,
    }


def pitch_unstable(cmalpha: float | None) -> bool:
    return cmalpha is not None and float(cmalpha) > 0.0


def run_section(
    cfg: dict[str, Any],
    coords: list[list[float]],
    *,
    neuralfoil_fn: SectionFn | None = None,
) -> dict[str, Any]:
    fn = neuralfoil_fn or _neuralfoil_backend()
    seed_speed = loaf_speed(cfg["mass_kg"], cfg["reference_area_m2"], 1.0)
    seed_re = reynolds(seed_speed, cfg["chord_m"])
    raw = fn(coords, cfg["alpha_deg"], seed_re, NEURALFOIL_MODEL)
    cl = float(_coeff(raw, "CL") or 0.0)
    if cl <= 0.0:
        raise AeroDependencyError("NeuralFoil returned a non-positive CL.")
    speed = loaf_speed(cfg["mass_kg"], cfg["reference_area_m2"], cl)
    re = reynolds(speed, cfg["chord_m"])
    refined = fn(coords, cfg["alpha_deg"], re, NEURALFOIL_MODEL)
    cl = float(_coeff(refined, "CL") or cl)
    cd = float(_coeff(refined, "CD") or 0.0)
    cm = float(_coeff(refined, "CM", "Cm") or 0.0)
    speed = loaf_speed(cfg["mass_kg"], cfg["reference_area_m2"], cl)
    re = reynolds(speed, cfg["chord_m"])
    return {
        "CL": cl,
        "CD": cd,
        "CM": cm,
        "analysis_confidence": _coeff(refined, "analysis_confidence"),
        "source": "NeuralFoil",
        "V_loaf": speed,
        "Re": re,
        "alpha_deg": float(cfg["alpha_deg"]),
    }


def analyze(
    cfg: dict[str, Any],
    *,
    coords: list[list[float]] | None = None,
    neuralfoil_fn: SectionFn | None = None,
    aerobuildup_fn: CoeffFn | None = None,
    vlm_fn: CoeffFn | None = None,
    run_section_solve: bool = True,
    run_vlm_solve: bool = True,
) -> dict[str, Any]:
    section = None
    if run_section_solve and (neuralfoil_fn is not None or _module_available("neuralfoil")):
        if coords is None:
            raise AirfoilRequiredError("Section analysis needs airfoil coordinates.")
        section = run_section(cfg, coords, neuralfoil_fn=neuralfoil_fn)

    seed_cl = (section or {}).get("CL") or 1.0
    speed = loaf_speed(cfg["mass_kg"], cfg["reference_area_m2"], float(seed_cl))

    buildup = None
    vlm = None
    derivatives = {"CLalpha": None, "Cmalpha": None}
    if run_vlm_solve and (
        aerobuildup_fn is not None
        or vlm_fn is not None
        or _module_available("aerosandbox")
    ):
        if coords is None and aerobuildup_fn is None and vlm_fn is None:
            raise AirfoilRequiredError("3D analysis needs airfoil coordinates.")
        buildup, vlm, derivatives = run_three_d(
            cfg,
            coords or [],
            speed_mps=speed,
            aerobuildup_fn=aerobuildup_fn,
            vlm_fn=vlm_fn,
        )

    if section is None and buildup is None and vlm is None:
        require_backend("neuralfoil", available=False)

    picked = pick_force_coefficients(aerobuildup=buildup, vlm=vlm, neuralfoil=section)
    speed = loaf_speed(cfg["mass_kg"], cfg["reference_area_m2"], picked["CL"])
    re = reynolds(speed, cfg["chord_m"])
    q = 0.5 * RHO * speed * speed
    drag = q * cfg["reference_area_m2"] * picked["CD"]
    hover = hover_power(
        cfg["mass_kg"],
        n_props=int(cfg.get("n_props", 2)),
        prop_diameter_m=float(cfg.get("prop_diameter_m", 0.178)),
        figure_of_merit=float(cfg.get("figure_of_merit", 0.55)),
        thrust_to_weight=float(cfg.get("thrust_to_weight", 1.9)),
    )
    cruise = cruise_power(drag, speed, prop_eta=float(cfg.get("cruise_prop_eta", 0.65)))
    cmalpha = derivatives.get("Cmalpha")
    payload = {
        **cfg,
        **picked,
        "CLalpha": derivatives.get("CLalpha"),
        "Cmalpha": cmalpha,
        "Re": re,
        "V_loaf": speed,
        "P_hover": hover["P_hover"],
        "P_cruise": cruise["P_cruise"],
        "hover": hover,
        "cruise": cruise,
        "PitchUnstable": pitch_unstable(cmalpha),
        "section": section,
        "aerobuildup": buildup,
        "vlm": vlm,
        "xyz_ref": list(cfg.get("xyz_ref") or [0.25 * cfg["chord_m"], 0.0, cfg["gap_m"] / 2.0]),
        "mass_kg": cfg["mass_kg"],
        "span_m": cfg["span_m"],
        "chord_m": cfg["chord_m"],
        "reference_area_m2": cfg["reference_area_m2"],
        "alpha_deg": float(cfg["alpha_deg"]),
    }
    return payload


def run_three_d(
    cfg: dict[str, Any],
    coords: list[list[float]],
    *,
    speed_mps: float,
    aerobuildup_fn: CoeffFn | None = None,
    vlm_fn: CoeffFn | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    airplane = None
    if aerobuildup_fn is None or vlm_fn is None:
        if _module_available("aerosandbox"):
            airplane = build_airplane(cfg, coords)

    buildup = None
    if aerobuildup_fn is not None:
        buildup = _normalize_coeffs(aerobuildup_fn(airplane, speed_mps, cfg["alpha_deg"]))
    elif airplane is not None:
        try:
            buildup = _normalize_coeffs(
                _aerobuildup_backend()(airplane, speed_mps, cfg["alpha_deg"])
            )
        except Exception:
            buildup = None

    vlm = None
    vlm2 = None
    runner = vlm_fn or (_vlm_backend() if airplane is not None else None)
    if runner is not None:
        vlm = _normalize_coeffs(runner(airplane, speed_mps, cfg["alpha_deg"]))
        vlm2 = _normalize_coeffs(
            runner(airplane, speed_mps, float(cfg["alpha_deg"]) + VLM_ALPHA_STEP_DEG)
        )

    derivatives: dict[str, Any] = {"CLalpha": None, "Cmalpha": None}
    if vlm and vlm2:
        derivatives = vlm_stability_derivatives(
            cfg["alpha_deg"],
            vlm["CL"],
            vlm["CM"],
            float(cfg["alpha_deg"]) + VLM_ALPHA_STEP_DEG,
            vlm2["CL"],
            vlm2["CM"],
        )
    return buildup, vlm, derivatives


def build_airplane(cfg: dict[str, Any], coords: list[list[float]]) -> Any:
    require_backend("aerosandbox")
    import aerosandbox as asb  # type: ignore

    foil = asb.Airfoil(coordinates=_as_array(coords))
    span = float(cfg["span_m"])
    chord = float(cfg["chord_m"])
    gap = float(cfg["gap_m"])
    stagger = float(cfg["stagger_m"])
    decalage = float(cfg["decalage_deg"])
    lower = asb.Wing(
        name="Lower Wing",
        symmetric=True,
        xsecs=[
            asb.WingXSec(xyz_le=[0.0, 0.0, 0.0], chord=chord, twist=0.0, airfoil=foil),
            asb.WingXSec(xyz_le=[0.0, span / 2.0, 0.0], chord=chord, twist=0.0, airfoil=foil),
        ],
    )
    upper = asb.Wing(
        name="Upper Wing",
        symmetric=True,
        xsecs=[
            asb.WingXSec(
                xyz_le=[-stagger, 0.0, gap],
                chord=chord,
                twist=decalage,
                airfoil=foil,
            ),
            asb.WingXSec(
                xyz_le=[-stagger, span / 2.0, gap],
                chord=chord,
                twist=decalage,
                airfoil=foil,
            ),
        ],
    )
    boom_length = float(cfg.get("boom_length_m") or max(0.25, 3.5 * chord))
    tail_span = float(cfg.get("tail_span_m") or 0.30 * span)
    tail_chord = float(cfg.get("tail_chord_m") or 0.60 * chord)
    boom = asb.Fuselage(
        name="Boom",
        xsecs=[
            asb.FuselageXSec(xyz_c=[-0.02, 0.0, gap / 2.0], radius=0.004),
            asb.FuselageXSec(
                xyz_c=[boom_length, 0.0, gap / 2.0],
                radius=0.004,
            ),
        ],
    )
    h_tail = asb.Wing(
        name="h_tail",
        symmetric=True,
        xsecs=[
            asb.WingXSec(
                xyz_le=[boom_length, 0.0, gap / 2.0],
                chord=tail_chord,
                twist=0.0,
                airfoil=foil,
            ),
            asb.WingXSec(
                xyz_le=[boom_length, tail_span / 2.0, gap / 2.0],
                chord=tail_chord,
                twist=0.0,
                airfoil=foil,
            ),
        ],
    )
    return asb.Airplane(
        name="VibeCADAero",
        xyz_ref=list(cfg.get("xyz_ref") or [0.25 * chord, 0.0, gap / 2.0]),
        wings=[lower, upper, h_tail],
        fuselages=[boom],
    )


class AirfoilRequiredError(RuntimeError):
    pass


def _neuralfoil_backend() -> SectionFn:
    require_backend("neuralfoil")
    import neuralfoil as nf  # type: ignore

    def _run(coords, alpha, Re, model_size="large"):
        return nf.get_aero_from_coordinates(
            _as_array(coords),
            alpha=alpha,
            Re=Re,
            model_size=model_size,
        )

    return _run


def _aerobuildup_backend() -> CoeffFn:
    require_backend("aerosandbox")
    import aerosandbox as asb  # type: ignore

    def _run(airplane, speed_mps, alpha_deg):
        op = asb.OperatingPoint(velocity=speed_mps, alpha=alpha_deg)
        return asb.AeroBuildup(airplane=airplane, op_point=op).run()

    return _run


def _vlm_backend() -> CoeffFn:
    require_backend("aerosandbox")
    import aerosandbox as asb  # type: ignore

    def _run(airplane, speed_mps, alpha_deg):
        op = asb.OperatingPoint(velocity=speed_mps, alpha=alpha_deg)
        return asb.VortexLatticeMethod(
            airplane=airplane,
            op_point=op,
            spanwise_resolution=VLM_SPANWISE,
            chordwise_resolution=VLM_CHORDWISE,
        ).run()

    return _run


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return False
    return True


def _coeff(data: Any, *names: str) -> float | None:
    for name in names:
        value = None
        if isinstance(data, dict) and name in data:
            value = data[name]
        elif hasattr(data, name):
            value = getattr(data, name)
        if value is None:
            continue
        try:
            return float(_first(value))
        except (TypeError, ValueError):
            continue
    return None


def _first(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    try:
        import numpy as np  # type: ignore

        array = np.asarray(value)
        if array.shape == ():
            return array.item()
        if array.size:
            return array.reshape(-1)[0]
    except Exception:
        pass
    return value


def _normalize_coeffs(data: Any) -> dict[str, Any]:
    return {
        "CL": _coeff(data, "CL"),
        "CD": _coeff(data, "CD"),
        "CM": _coeff(data, "CM", "Cm"),
        "analysis_confidence": _coeff(data, "analysis_confidence"),
    }


def _as_array(coords: list[list[float]]) -> Any:
    try:
        import numpy as np  # type: ignore

        return np.asarray(coords, dtype=float)
    except Exception:
        return coords
