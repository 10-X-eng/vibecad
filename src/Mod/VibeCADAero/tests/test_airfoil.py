# SPDX-License-Identifier: LGPL-2.1-or-later

"""E63 must load as E63. Silent NACA 0009 substitution is a bug."""

from __future__ import annotations

from pathlib import Path

import pytest

import AeroAirfoil as airfoil


def test_bundled_e63_dat_is_present_and_cambered():
    path = airfoil.bundled_dat_path("e63")
    assert path.is_file()
    coords = airfoil.read_dat(path)
    assert len(coords) >= 40
    ys = [row[1] for row in coords]
    assert max(ys) > 0.05
    assert min(ys) > -0.02
    assert not airfoil.looks_like_naca0009(coords)


def test_load_e63_never_returns_naca0009(monkeypatch):
    naca = airfoil.naca4_coordinates(0, 0, 9)

    class _FakeAirfoil:
        def __init__(self, name):
            self.name = name
            self.coordinates = naca

    fake_asb = type("asb", (), {"Airfoil": _FakeAirfoil})
    monkeypatch.setattr(airfoil, "_try_import_aerosandbox", lambda: fake_asb)

    coords, source = airfoil.load_airfoil_coordinates("e63")
    assert source.startswith("bundled")
    assert not airfoil.looks_like_naca0009(coords)
    assert airfoil.looks_like_naca0009(naca)


def test_unknown_non_naca_name_does_not_become_naca0009(monkeypatch, tmp_path):
    naca = airfoil.naca4_coordinates(0, 0, 9)

    class _FakeAirfoil:
        def __init__(self, name):
            self.name = name
            self.coordinates = naca

    fake_asb = type("asb", (), {"Airfoil": _FakeAirfoil})
    monkeypatch.setattr(airfoil, "_try_import_aerosandbox", lambda: fake_asb)
    monkeypatch.setattr(airfoil, "bundled_dat_path", lambda name: tmp_path / f"{name}.dat")

    with pytest.raises(airfoil.AirfoilLoadError, match="naca0009|NACA 0009|E63|cannot"):
        airfoil.load_airfoil_coordinates("mysteryfoil")


def test_explicit_naca0009_is_allowed():
    coords, source = airfoil.load_airfoil_coordinates("naca0009")
    assert airfoil.looks_like_naca0009(coords)
    assert "naca" in source.lower()


def test_missing_airfoil_error_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(airfoil, "_try_import_aerosandbox", lambda: None)
    monkeypatch.setattr(airfoil, "bundled_dat_path", lambda name: tmp_path / f"{name}.dat")
    with pytest.raises(airfoil.AirfoilLoadError, match="naca2412|install|AeroSandbox|bundled"):
        airfoil.load_airfoil_coordinates("naca2412")
