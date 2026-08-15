# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from typing import Any

from Base.BaseClass import BaseClass
from Base.Metadata import export, no_args

from Gui import Document
from Part.App.TopoShape import TopoShape
from CAM.App.Command import Command

@export(
    Include="Mod/CAM/PathSimulator/AppGL/CAMSim.h",
    FatherInclude="Base/BaseClassPy.h",
    Namespace="CAMSimulator",
    Constructor=True,
    Delete=True,
)
class CAMSim(BaseClass):
    """
    FreeCAD python wrapper of CAMSimulator

          CAMSimulator.CAMSim():

          Create a path simulator object

    Author: Shai Seger (shaise_at_g-mail)
    License: LGPL-2.1-or-later
    """

    def BeginSimulation(self, stock: TopoShape, resolution: float) -> None:
        """
        Start a simulation process on a box shape stock with given resolution
        """
        ...

    def PrepareShapeMesh(self, shape: TopoShape, resolution: float) -> bytes:
        """Tessellate one detached shape into an opaque in-process simulator mesh."""
        ...

    def BeginPreparedSimulation(
        self,
        stock: TopoShape,
        prepared_mesh: bytes,
        quality: float,
    ) -> None:
        """Start simulation using a mesh prepared away from the GUI thread."""
        ...

    def ResetSimulation(self, document: Document, /) -> None:
        """
        Clear the simulation and all gcode commands
        """
        ...

    def AddTool(self, shape: TopoShape, toolnumber: int, diameter: float, resolution: float) -> Any:
        """
        Set the shape of the tool to be used for simulation
        """
        ...

    def SetBaseShape(self, shape: TopoShape, resolution: float) -> None:
        """
        Set the shape of the base object of the job
        """
        ...

    def SetPreparedBaseShape(self, shape: TopoShape, prepared_mesh: bytes) -> None:
        """Present a base shape using a previously prepared simulator mesh."""
        ...

    def AddCommand(self, command: Command, /) -> Any:
        """
        Add a path command to the simulation.
        """
        ...

    def AddGCode(self, command: str, /) -> None:
        """Add one already serialized G-code command to the simulation."""
        ...
