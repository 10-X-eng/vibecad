# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from FreeCAD import Base

from . import base_fempythonobject


_PropHelper = base_fempythonobject._PropHelper


class SolverOpenFOAM(base_fempythonobject.BaseFemPythonObject):
    Type = "Fem::SolverOpenFOAM"

    def __init__(self, obj):
        super().__init__(obj)
        obj.addExtension("App::SuppressibleExtensionPython")
        for prop in self._properties():
            prop.add_to_object(obj)

    @staticmethod
    def _properties():
        return (
            _PropHelper(
                type="App::PropertyEnumeration",
                name="FlowRegime",
                group="Solver",
                doc="Time behavior",
                value=["steady"],
            ),
            _PropHelper(
                type="App::PropertyEnumeration",
                name="TurbulenceModel",
                group="Solver",
                doc="Momentum transport model",
                value=["laminar"],
            ),
            _PropHelper(
                type="App::PropertyIntegerConstraint",
                name="MaxIterations",
                group="Solver",
                doc="Maximum steady iterations",
                value={"value": 1000, "min": 1, "max": 1_000_000_000},
            ),
            _PropHelper(
                type="App::PropertyIntegerConstraint",
                name="WriteEveryIterations",
                group="Solver",
                doc="Result write interval",
                value={"value": 100, "min": 1, "max": 1_000_000_000},
            ),
            _PropHelper(
                type="App::PropertyFloatConstraint",
                name="PressureTolerance",
                group="Solver",
                doc="Pressure residual tolerance",
                value={"value": 1.0e-6, "min": 1.0e-15, "max": 1.0},
            ),
            _PropHelper(
                type="App::PropertyFloatConstraint",
                name="VelocityTolerance",
                group="Solver",
                doc="Velocity residual tolerance",
                value={"value": 1.0e-5, "min": 1.0e-15, "max": 1.0},
            ),
        )

    def onDocumentRestored(self, obj):
        if not obj.hasExtension("App::SuppressibleExtensionPython"):
            obj.addExtension("App::SuppressibleExtensionPython")
        for prop in self._properties():
            try:
                obj.getPropertyByName(prop.name)
            except Base.PropertyError:
                prop.add_to_object(obj)
