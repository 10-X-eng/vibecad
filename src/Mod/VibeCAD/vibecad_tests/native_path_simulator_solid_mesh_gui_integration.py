# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled gate for exact solid retained-stock PathSimulator output."""

from __future__ import annotations

import traceback

import FreeCAD as App
import Part
import Path
import PathSimulator
from PySide import QtCore, QtWidgets


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    try:
        stock = Part.makeBox(20.0, 15.0, 6.0)
        cutter = Part.makeCylinder(2.0, 12.0)
        simulator = PathSimulator.PathSim()
        simulator.BeginSimulation(stock, 1.0)
        simulator.SetToolShape(cutter, 0.5)

        position = App.Placement(App.Vector(0.0, 0.0, 6.0), App.Rotation())
        for command in (
            Path.Command("G0", {"X": 5.0, "Y": 5.0, "Z": 7.0}),
            Path.Command("G1", {"Z": 2.0}),
            Path.Command("G1", {"X": 15.0}),
            Path.Command("G1", {"Y": 10.0}),
            Path.Command("G1", {"X": 5.0}),
        ):
            position = simulator.ApplyCommand(position, command)

        preview = simulator.GetCombinedResultMesh()
        assert preview.CountFacets > 0
        retained = simulator.GetRemainingStockShape()
        assert not retained.isNull()
        assert retained.isValid()
        assert len(retained.Solids) >= 1
        bounds = retained.BoundBox
        assert abs(float(bounds.XLength) - 20.0) <= 1.0e-6
        assert abs(float(bounds.YLength) - 15.0) <= 1.0e-6
        assert abs(float(bounds.ZLength) - 6.0) <= 1.0e-6
        assert 0.0 < float(retained.Volume) < float(stock.Volume)
        assert all(solid.isValid() and solid.Volume > 0.0 for solid in retained.Solids)

        print(
            "VIBECAD_NATIVE_PATH_SIMULATOR_REMAINING_STOCK_GUI_OK "
            f"solids={len(retained.Solids)} valid=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
