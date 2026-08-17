# SPDX-License-Identifier: LGPL-2.1-or-later

"""Load section coordinates. Never silently substitute NACA 0009 for E63."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable


class AirfoilLoadError(RuntimeError):
    """Raised when coordinates cannot be loaded without a silent fallback."""


def bundled_dat_path(name: str) -> Path:
    key = _normalize_name(name)
    if key in {"e63", "epplere63", "eppler63"}:
        key = "e63"
    return Path(__file__).resolve().parent / "data" / f"{key}.dat"


def read_dat(path: Path) -> list[list[float]]:
    coords: list[list[float]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            continue
        try:
            coords.append([float(parts[0]), float(parts[1])])
        except ValueError:
            continue
    if len(coords) < 8:
        raise AirfoilLoadError(f"Airfoil file {path} does not contain enough coordinates.")
    return coords


def looks_like_naca0009(coords: Iterable[Iterable[float]]) -> bool:
    ys = [float(row[1]) for row in coords]
    if not ys:
        return False
    ymax = max(ys)
    ymin = min(ys)
    symmetric = abs(ymax + ymin) < 0.008
    nine_percent = 0.038 < ymax < 0.055
    return symmetric and nine_percent


def naca4_coordinates(m_percent: float, p_tenths: float, t_percent: float, n: int = 81) -> list[list[float]]:
    """Analytic NACA 4-digit section. Used only for explicit NACA names."""

    m = m_percent / 100.0
    p = p_tenths / 10.0
    t = t_percent / 100.0
    xs = [0.5 * (1.0 - math.cos(math.pi * i / (n - 1))) for i in range(n)]
    upper: list[list[float]] = []
    lower: list[list[float]] = []
    for x in xs:
        yt = (
            5.0
            * t
            * (
                0.2969 * math.sqrt(max(x, 0.0))
                - 0.1260 * x
                - 0.3516 * x * x
                + 0.2843 * x**3
                - 0.1015 * x**4
            )
        )
        if p > 0.0 and x < p:
            yc = m * (x / (p * p)) * (2.0 * p - x)
            dyc = 2.0 * m * (p - x) / (p * p)
        elif p > 0.0:
            yc = m * ((1.0 - x) / ((1.0 - p) ** 2)) * (1.0 + x - 2.0 * p)
            dyc = 2.0 * m * (p - x) / ((1.0 - p) ** 2)
        else:
            yc = 0.0
            dyc = 0.0
        theta = math.atan(dyc)
        upper.append([x - yt * math.sin(theta), yc + yt * math.cos(theta)])
        lower.append([x + yt * math.sin(theta), yc - yt * math.cos(theta)])
    return upper[::-1] + lower[1:]


def load_airfoil_coordinates(name: str) -> tuple[list[list[float]], str]:
    """Return ``(coords, source)``. E63 comes from the bundled UIUC dat."""

    raw = str(name or "").strip()
    if not raw:
        raise AirfoilLoadError("Airfoil name is empty.")
    key = _normalize_name(raw)

    if key in {"e63", "epplere63", "eppler63"}:
        path = bundled_dat_path("e63")
        if path.is_file():
            coords = read_dat(path)
            if looks_like_naca0009(coords):
                raise AirfoilLoadError(
                    "Bundled e63.dat looks like NACA 0009; refusing to use it."
                )
            return coords, "bundled:e63"

    naca = _parse_naca4(key)
    if naca is not None:
        return naca4_coordinates(*naca), f"naca4:{key}"

    asb = _try_import_aerosandbox()
    if asb is not None:
        try:
            foil = asb.Airfoil(raw)
            coords = _as_coord_list(getattr(foil, "coordinates", None))
            if looks_like_naca0009(coords) and naca is None:
                raise AirfoilLoadError(
                    f"AeroSandbox returned NACA 0009 coordinates for {raw!r}. "
                    "That silent fallback is disabled. Use a bundled .dat or a "
                    "real UIUC/Airfoil name. For E63, ship data/e63.dat."
                )
            return coords, f"aerosandbox:{raw}"
        except AirfoilLoadError:
            raise
        except Exception as exc:
            asb_error = exc
        else:
            asb_error = None
    else:
        asb_error = None

    path = bundled_dat_path(key)
    if path.is_file():
        return read_dat(path), f"bundled:{key}"

    extra = f" AeroSandbox error: {asb_error}." if asb_error else ""
    raise AirfoilLoadError(
        f"Cannot load airfoil {raw!r}.{extra} "
        "Install aerosandbox into VibeCAD's bundled Python or add a Selig "
        f".dat under Mod/VibeCADAero/data/{key}.dat. "
        "E63 is bundled; other non-NACA names are not silently replaced with NACA 0009."
    )


def _try_import_aerosandbox() -> Any | None:
    try:
        import aerosandbox as asb  # type: ignore
    except Exception:
        return None
    return asb


def _normalize_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _parse_naca4(key: str) -> tuple[float, float, float] | None:
    if not key.startswith("naca") or len(key) != 8 or not key[4:].isdigit():
        return None
    digits = key[4:]
    return float(digits[0]), float(digits[1]), float(digits[2:])


def _as_coord_list(value: Any) -> list[list[float]]:
    if value is None:
        raise AirfoilLoadError("Airfoil object has no coordinates.")
    rows = []
    for row in value:
        rows.append([float(row[0]), float(row[1])])
    if len(rows) < 8:
        raise AirfoilLoadError("Airfoil coordinates are too short.")
    return rows
