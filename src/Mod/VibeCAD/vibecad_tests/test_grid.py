# SPDX-License-Identifier: LGPL-2.1-or-later

"""Pure regression coverage for VibeCAD's adaptive viewport grid."""

from __future__ import annotations

import pytest

import VibeCADGrid as grid


class _WorkingPlane:
    position = (0.0, 0.0, 0.0)
    axis = (0.0, 0.0, 1.0)


class _Grid:
    @staticmethod
    def _get_wp() -> _WorkingPlane:
        return _WorkingPlane()


class _OrthographicView:
    @staticmethod
    def getSize() -> tuple[int, int]:
        return (800, 600)

    @staticmethod
    def projectPointToLine(pixel: tuple[int, int]) -> tuple[tuple[float, ...], ...]:
        x, y = pixel
        point = (0.5 * x, 0.25 * y)
        return ((*point, 10.0), (*point, -10.0))

    @staticmethod
    def getPointOnFocalPlane(pixel: tuple[int, int]) -> tuple[float, float, float]:
        x, y = pixel
        return (0.5 * x, 0.25 * y, 0.0)


def test_metric_spacing_uses_125_engineering_series() -> None:
    assert grid._nice_grid_spacing(0.87, 0) == pytest.approx(1.0)
    assert grid._nice_grid_spacing(2.7, 0) == pytest.approx(2.0)
    assert grid._nice_grid_spacing(4.1, 0) == pytest.approx(5.0)
    assert grid._nice_grid_spacing(87.0, 0) == pytest.approx(100.0)


def test_fractional_imperial_spacing_uses_binary_inches() -> None:
    quarter_inch_mm = 0.25 * grid._MM_PER_INCH
    assert grid._nice_grid_spacing(quarter_inch_mm * 1.08, 2) == pytest.approx(
        quarter_inch_mm
    )


def test_building_units_use_clean_foot_steps() -> None:
    assert grid._nice_grid_spacing(13.0 * grid._MM_PER_INCH, 5) == pytest.approx(
        12.0 * grid._MM_PER_INCH
    )
    assert grid._nice_grid_spacing(58.0 * grid._MM_PER_INCH, 5) == pytest.approx(
        60.0 * grid._MM_PER_INCH
    )


def test_spacing_hysteresis_keeps_current_level_inside_visual_band() -> None:
    current = 10.0
    assert grid._select_grid_spacing(current / 30.0, current, 0) == current
    assert grid._select_grid_spacing(current / 10.0, current, 0) != current
    assert grid._select_grid_spacing(current / 60.0, current, 0) != current


def test_ray_plane_intersection_is_exact() -> None:
    point = grid._ray_plane_intersection(
        (2.0, -3.0, 10.0),
        (2.0, -3.0, -10.0),
        (0.0, 0.0, 2.5),
        (0.0, 0.0, 1.0),
    )
    assert point == pytest.approx((2.0, -3.0, 2.5))


def test_parallel_ray_has_no_grid_plane_intersection() -> None:
    assert (
        grid._ray_plane_intersection(
            (0.0, 0.0, 1.0),
            (10.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        is None
    )


def test_world_units_per_pixel_uses_grid_plane_projection() -> None:
    assert grid._world_units_per_pixel(_OrthographicView(), _Grid()) == pytest.approx(
        0.5
    )


def test_final_subwindow_close_disposes_coin_sensors_without_rescheduling(
    monkeypatch,
) -> None:
    class _Controller:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    controller = _Controller()
    monkeypatch.setattr(grid, "_adaptive_controllers", [controller])

    grid._on_sub_window_activated(None)

    assert controller.disposed is True
    assert grid._adaptive_controllers == []


def test_main_window_close_stops_timer_and_disposes_coin_sensors(monkeypatch) -> None:
    class _Timer:
        stopped = False

        def stop(self) -> None:
            self.stopped = True

    class _Controller:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    timer = _Timer()
    controller = _Controller()
    monkeypatch.setattr(grid, "_maintenance_timer", timer)
    monkeypatch.setattr(grid, "_adaptive_controllers", [controller])
    monkeypatch.setattr(grid, "_close_suspended", False)

    grid._suspend_for_main_window_close()

    assert grid._close_suspended is True
    assert timer.stopped is True
    assert controller.disposed is True
    assert grid._adaptive_controllers == []
