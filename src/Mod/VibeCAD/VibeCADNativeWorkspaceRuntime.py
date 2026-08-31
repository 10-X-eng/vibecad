# SPDX-License-Identifier: LGPL-2.1-or-later

"""Main-thread runtime for changing the available kind of CAD work."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeWorkspaceSchema import (
    NATIVE_SURFACE_BY_WORKSPACE,
)
from VibeCADSurfaceAuthority import activate_workbench


WORKBENCH_BY_NATIVE_WORKSPACE = {
    "modeling": "PartDesignWorkbench",
    "sketching": "SketcherWorkbench",
    "assembly": "AssemblyWorkbench",
    "mesh": "MeshWorkbench",
    "analysis": "FemWorkbench",
    "manufacturing": "CAMWorkbench",
    "drawing": "TechDrawWorkbench",
    "parameters": "SpreadsheetWorkbench",
    "aerodynamics": "VibeCADAeroWorkbench",
}


class NativeWorkspaceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        current_surface: str = "",
    ) -> None:
        super().__init__(str(message))
        self.code = str(code)
        self.current_surface = str(current_surface)

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": self.code,
            "message": str(self),
        }
        if self.current_surface:
            result["current_surface"] = self.current_surface
        return result


class NativeWorkspaceRuntime:
    """Activate the requested CAD workspace on the document thread."""

    def __init__(
        self,
        context: NativeRuntimeContext,
        *,
        activate_workbench: Callable[[str], Any] | None = None,
    ) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        if activate_workbench is not None and not callable(activate_workbench):
            raise TypeError("activate_workbench must be callable")
        self._context = context
        self._activate_workbench = activate_workbench

    def _activate(self, workbench: str) -> None:
        if self._activate_workbench is not None:
            self._activate_workbench(workbench)
            return
        import FreeCADGui as Gui
        from PySide import QtCore, QtWidgets

        activate_workbench(workbench)
        for _index in range(8):
            Gui.updateGui()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )

    def switch(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"switch": frozenset({"workspace"})},
        )
        workspace = str(values["workspace"])
        next_surface = NATIVE_SURFACE_BY_WORKSPACE.get(workspace)
        workbench = WORKBENCH_BY_NATIVE_WORKSPACE.get(workspace)
        if workbench is None:
            raise NativeWorkspaceError(
                "NATIVE_WORKSPACE_INVALID",
                "The requested CAD workspace is unavailable.",
            )

        self._context.guard()
        previous_surface = str(self._context.active_surface_id() or "")
        if previous_surface == next_surface:
            raise NativeWorkspaceError(
                "NATIVE_WORKSPACE_UNCHANGED",
                f"The {workspace} workspace is already active.",
                current_surface=previous_surface,
            )
        dispatcher = self._context.document_thread_dispatch
        if dispatcher is None:
            raise NativeWorkspaceError(
                "NATIVE_WORKSPACE_UNAVAILABLE",
                "CAD workspace switching requires the VibeCAD document thread.",
                current_surface=previous_surface,
            )
        try:
            dispatcher(lambda: self._activate(workbench))
        except Exception as exc:
            raise NativeWorkspaceError(
                "NATIVE_WORKSPACE_UNAVAILABLE",
                f"VibeCAD could not activate the {workspace} workspace.",
                current_surface=previous_surface,
            ) from exc

        if self._context.active_document() is not self._context.document:
            raise NativeWorkspaceError(
                "NATIVE_DOCUMENT_CHANGED",
                "The active document changed while switching CAD work.",
            )
        observed_surface = str(self._context.active_surface_id() or "")
        if observed_surface != next_surface:
            raise NativeWorkspaceError(
                "NATIVE_WORKSPACE_MISMATCH",
                "VibeCAD did not activate the requested CAD workspace.",
                current_surface=observed_surface,
            )
        return {
            "workspace": workspace,
            "next_turn_required": True,
        }
