# SPDX-License-Identifier: LGPL-2.1-or-later

"""CAM stock configuration has one exact human/Native contract."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[2] / "CAM" / "Path" / "Main" / "JobStock.py"


def _service():
    spec = importlib.util.spec_from_file_location(
        "vibecad_test_job_stock",
        _MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("CAM Job stock service could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _placement() -> dict:
    return {
        "origin_mm": {"x": 10.0, "y": -2.0, "z": 4.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 90.0,
        },
    }


def test_stock_union_normalizes_each_authored_stock_kind() -> None:
    service = _service()

    assert service.normalize_stock_specification(
        {
            "kind": "model_bounds",
            "allowance_mm": {
                "x_negative": 1,
                "x_positive": 2,
                "y_negative": 3,
                "y_positive": 4,
                "z_negative": 5,
                "z_positive": 6,
            },
        }
    ) == {
        "kind": "model_bounds",
        "allowance_mm": {
            "x_negative": 1.0,
            "x_positive": 2.0,
            "y_negative": 3.0,
            "y_positive": 4.0,
            "z_negative": 5.0,
            "z_positive": 6.0,
        },
    }
    assert service.normalize_stock_specification(
        {
            "kind": "box",
            "size_mm": {"x": 80, "y": 50, "z": 12},
            "placement": _placement(),
        }
    ) == {
        "kind": "box",
        "size_mm": {"x": 80.0, "y": 50.0, "z": 12.0},
        "placement": _placement(),
    }
    assert service.normalize_stock_specification(
        {"kind": "cylinder", "radius_mm": 25, "height_mm": 40}
    ) == {"kind": "cylinder", "radius_mm": 25.0, "height_mm": 40.0}
    assert service.normalize_stock_specification(
        {
            "kind": "existing_solid",
            "source": {"object_name": "RawCasting"},
        }
    ) == {
        "kind": "existing_solid",
        "source": {"object_name": "RawCasting"},
    }


@pytest.mark.parametrize(
    "stock",
    (
        {"kind": "box", "size_mm": {"x": 1, "y": 2}},
        {"kind": "box", "size_mm": {"x": 1, "y": 2, "z": 0}},
        {"kind": "cylinder", "radius_mm": math.inf, "height_mm": 2},
        {"kind": "model_bounds", "allowance_mm": {}},
        {"kind": "existing_solid", "source": {"object_name": ""}},
        {"kind": "invented"},
    ),
)
def test_stock_union_rejects_incomplete_or_invalid_meanings(stock: dict) -> None:
    with pytest.raises(ValueError):
        _service().normalize_stock_specification(stock)


class _Vector:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


class _Rotation:
    Axis = _Vector(0.0, 0.0, 1.0)
    Angle = math.pi / 2.0


class _Placement:
    Base = _Vector(10.0, -2.0, 4.0)
    Rotation = _Rotation()


class _Quantity:
    def __init__(self, value: float) -> None:
        self.Value = value


class _BoxStock:
    Name = "Stock"
    Label = "Stock"
    StockType = "CreateBox"
    Length = _Quantity(80.0)
    Width = _Quantity(50.0)
    Height = _Quantity(12.0)
    Placement = _Placement()


class _Job:
    Stock = _BoxStock()


def test_stock_state_uses_the_same_authored_terms_as_stock_editing() -> None:
    state = _service().stock_configuration_state(_Job())

    assert state["object_name"] == "Stock"
    assert state["kind"] == "box"
    assert state["size_mm"] == {"x": 80.0, "y": 50.0, "z": 12.0}
    assert state["placement"] == _placement()
    assert len(state["state_sha256"]) == 64
