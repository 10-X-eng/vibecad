# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from typing import Any, Final

from Base.Metadata import constmethod, export

from App.Part import Part
from App.DocumentObject import DocumentObject

@export(Include="Mod/Assembly/App/AssemblyObject.h", Namespace="Assembly")
class AssemblyObject(Part):
    """
    This class handles document objects in Assembly

    Author: Ondsel (development@ondsel.com)
    License: LGPL-2.1-or-later
    """

    @constmethod
    def solve(self, enableUndo: bool = False, /) -> int:
        """
        Solve the assembly and update part placements.

        Args:
        enableRedo: Whether the solve save the initial position of parts
        to enable undoing it even without a transaction.
        Defaults to `False` ie the solve cannot be undone if called
        outside of a transaction.

        Returns:
        0 in case of success, otherwise the following codes in this order of
        priority:
        -6 if no parts are fixed.
        -4 if over-constrained,
        -3 if conflicting constraints,
        -5 if malformed constraints
        -1 if solver error,
        -2 if redundant constraints.
        """
        ...

    @constmethod
    def generateSimulation(self, simulationObject: DocumentObject, /) -> int:
        """
        Generate the simulation.

        Args:
        simulationObject: The simulation Object.

        Returns:
        0 in case of success, otherwise the following codes in this order of
        priority:
        -6 if no parts are fixed.
        -4 if over-constrained,
        -3 if conflicting constraints,
        -5 if malformed constraints
        -1 if solver error,
        -2 if redundant constraints.
        """
        ...

    @constmethod
    def startSimulation(self, simulationObject: DocumentObject, /) -> int:
        """Capture inputs and submit a detached native simulation to HostRuntime.

        Call on the GUI/document owner. Existing solved frames remain available
        until finishSimulation adopts a successful, still-current result.
        Returns a request token. Pass it to finish/cancel to address only this job.
        """
        ...

    @constmethod
    def startSimulationPlayback(self, simulationObject: DocumentObject, /) -> int:
        """Prepare playback asynchronously, reusing persisted frames for exact inputs.

        Uses finishSimulation and the asynchronous frame methods. Cached poses
        do not replace the last full native solver or its diagnostics. Request
        startSimulation instead when complete solver state is required.
        Missing, corrupt or stale cached data is regenerated on HostRuntime.
        """
        ...

    @constmethod
    def finishSimulation(self, request: int = 0, /) -> bool:
        """Adopt a ready simulation without waiting; return False while running.

        Raises RuntimeError on cancellation, invalidated inputs or solver failure.
        Call on the same document owner as startSimulation.
        A superseded token raises without consuming another caller's job.
        Omitting the token addresses the current job.
        """
        ...

    @constmethod
    def cancelSimulation(self, request: int = 0, /) -> None:
        """Cancel without waiting. Superseded tokens leave the current job alone."""
        ...

    @constmethod
    def requestSimulationFrame(self, index: int, /) -> int:
        """Prepare an async-generated simulation frame on the native worker pool.

        Returns a request token. A newer request cancels the previous frame.
        Inputs must still match the adopted asynchronous simulation.
        """
        ...

    @constmethod
    def getSimulationFrame(self, index: int, /) -> Any:
        """Return one cached pose without scheduling work or changing the document.

        The result is a list of ``(object_name, placement)`` pairs intended for
        direct ViewProvider presentation on the GUI owner thread.
        """
        ...

    @constmethod
    def finishSimulationFrame(self, request: int, /) -> bool:
        """Apply a ready frame on its document owner without waiting.

        False means preparation is still running. Superseded, cancelled or
        invalidated requests raise without applying their placements.
        """
        ...

    @constmethod
    def takeSimulationFrame(self, request: int, /) -> Any:
        """Consume a ready frame for graphics-only presentation.

        Returns None while preparation is running. A ready result is a list of
        ``(object_name, placement)`` pairs and does not change document properties.
        Superseded, cancelled, or invalidated requests raise.
        """
        ...

    @constmethod
    def cancelSimulationFrame(self, request: int = 0, /) -> None:
        """Cancel one frame token (or the current frame with zero), without waiting."""
        ...

    @constmethod
    def setSimulationPresentation(self, active: bool, /) -> bool:
        """Scope transient component placements on the owner thread.

        Return the previous state, which must be restored in finally. Do not
        process GUI events, generate a simulation, or yield inside the scope.
        Other properties and objects continue to invalidate solved inputs.
        """
        ...

    @constmethod
    def updateForFrame(self, index: int, /) -> None:
        """
        Update entire assembly to frame number specified.

        Args:
            index: index of frame.

        Returns: None
        """
        ...

    @constmethod
    def numberOfFrames(self) -> int:
        """Return Number of frames"""
        ...

    @constmethod
    def updateSolveStatus(self) -> Any:
        """updateSolveStatus()

        Args: None

        Returns: None"""
        ...

    @constmethod
    def getSolverDiagnostics(self) -> dict[str, Any]:
        """Return structured diagnostics from the most recent native solve.

        Includes assembly DoF, grounded components, conflicting/redundant/malformed
        joint sets, and per-joint native constraint residuals.
        """
        ...

    @constmethod
    def undoSolve(self) -> Any:
        """Undo the last solve of the assembly and return part placements to their initial position.

        undoSolve()

        Returns: None"""
        ...

    @constmethod
    def ensureIdentityPlacements(self) -> None:
        """
        Makes sure that LinkGroups or sub-assemblies have identity placements.
        """
        ...

    @constmethod
    def clearUndo(self) -> None:
        """
        Clear the registered undo positions.
        """
        ...

    @constmethod
    def isPartConnected(self, obj: DocumentObject, /) -> bool:
        """
        Check if a part is connected to the ground through joints.
        Returns: True if part is connected to ground.
        """
        ...

    @constmethod
    def isJointConnectingPartToGround(self, joint: DocumentObject, prop_name: str, /) -> Any:
        """
        Check if a joint is connecting a part to the ground.

        Args:
        - joint: document object of the joint to check.
        - prop_name: string 'Part1' or 'Part2' of the joint.

        Returns: True if part is connected to ground.
        """
        ...

    @constmethod
    def isPartGrounded(self, obj: DocumentObject, /) -> Any:
        """
        Check if a part has a grounded joint.

        Args:
        - obj: document object of the part to check.

        Returns: True if part has grounded joint.
        """
        ...

    @constmethod
    def exportAsASMT(self, file_name: str, /) -> None:
        """
        Export the assembly in a text format called ASMT.

        Args:
        - fileName: The name of the file where the ASMT will be exported.
        """
        ...

    @constmethod
    def getDownstreamParts(
        self, start_part: DocumentObject, joint_to_ignore: DocumentObject, /
    ) -> list[DocumentObject]:
        """
        Finds all parts connected to a start_part that are not connected to ground
        when a specific joint is ignored.

        This is used to find the entire rigid group of unconstrained components that
        should be moved together during a pre-solve operation or a drag.

        Args:
            start_part: The App.DocumentObject to begin the search from.
            joint_to_ignore: The App.DocumentObject (a joint) to temporarily
                             suppress during the connectivity check.

        Returns:
            A list of App.DocumentObject instances representing the downstream parts.
        """
        ...
    Joints: Final[list]
    """A list of all joints this assembly has."""
