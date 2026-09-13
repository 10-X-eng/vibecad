# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression tests for exact CAM simulation verification boundaries."""

import unittest
from unittest.mock import patch

import FreeCAD

from Path.Main import Simulation
from Path.Main.Gui import SimulatorGL


class _DeletedJob:
    @property
    def Document(self):
        raise ReferenceError("deleted object")


class TestPathSimulation(unittest.TestCase):
    def test_simulator_endpoint_accepts_native_float_storage(self):
        geometric = FreeCAD.Vector(47.9840033, 14.5093051, 1.0)
        simulated = FreeCAD.Vector(47.984004974365234, 14.509305000305176, 1.0)

        self.assertTrue(Simulation._simulator_endpoint_matches(simulated, geometric))

    def test_simulator_endpoint_rejects_geometric_divergence(self):
        geometric = FreeCAD.Vector(47.9840033, 14.5093051, 1.0)
        simulated = FreeCAD.Vector(47.9850033, 14.5093051, 1.0)

        self.assertFalse(Simulation._simulator_endpoint_matches(simulated, geometric))

    def test_stale_prepared_simulation_is_not_active(self):
        stale = type("StaleSimulation", (), {"job": _DeletedJob()})()

        with patch.object(SimulatorGL, "_active_native_prepared_simulation", stale):
            self.assertFalse(SimulatorGL.owns_active_prepared_simulation(object()))


if __name__ == "__main__":
    unittest.main()
