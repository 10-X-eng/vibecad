# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact document and guard context shared by one Native assistant turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from VibeCADNativeInput import NativeInputAuthorizer
from VibeCADNativeOutput import NativeOutputAuthorizer
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeTargets import document_uid
from VibeCADNativeUndo import NativeAssistantUndoLedger


class NativeRuntimeContextError(RuntimeError):
    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_RUNTIME_GUARD_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class NativeRuntimeContext:
    """Host-owned authority needed by document-bound capability runtimes."""

    service: Any = field(repr=False, compare=False)
    document: Any = field(repr=False, compare=False)
    state: NativeDocumentStateStore = field(repr=False, compare=False)
    undo_ledger: NativeAssistantUndoLedger = field(repr=False, compare=False)
    reauthorize_turn: Callable[[], Any] = field(repr=False, compare=False)
    active_document: Callable[[], Any] = field(repr=False, compare=False)
    active_surface_id: Callable[[], str] = field(repr=False, compare=False)
    edit_or_task_active: Callable[[], bool] = field(repr=False, compare=False)
    authorize_output: NativeOutputAuthorizer | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    authorize_input: NativeInputAuthorizer | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    background_manager: Any | None = field(default=None, repr=False, compare=False)
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    document_uid: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.state, NativeDocumentStateStore):
            raise TypeError("state must be a NativeDocumentStateStore")
        if not isinstance(self.undo_ledger, NativeAssistantUndoLedger):
            raise TypeError("undo_ledger must be a NativeAssistantUndoLedger")
        callbacks = (
            self.reauthorize_turn,
            self.active_document,
            self.active_surface_id,
            self.edit_or_task_active,
        )
        if not all(callable(value) for value in callbacks):
            raise TypeError("Native runtime guards must be callable")
        if self.authorize_output is not None and not callable(self.authorize_output):
            raise TypeError("Native output authorizer must be callable")
        if self.authorize_input is not None and not callable(self.authorize_input):
            raise TypeError("Native input authorizer must be callable")
        if self.document_thread_dispatch is not None and not callable(
            self.document_thread_dispatch
        ):
            raise TypeError("Native document-thread dispatcher must be callable")
        object.__setattr__(self, "document_uid", document_uid(self.document))

    def guard(
        self,
        *,
        allow_owned_playback: bool = False,
        allow_owned_cam_simulation: bool = False,
    ) -> None:
        self.reauthorize_turn()
        active = self.active_document()
        if (
            active is not self.document
            or document_uid(self.document) != self.document_uid
        ):
            raise NativeRuntimeContextError(
                "The exact Native document is no longer active."
            )
        if not bool(self.edit_or_task_active()):
            return
        if str(self.active_surface_id() or "") == "sketch.edit":
            return
        if allow_owned_playback:
            try:
                from VibeCADNativeAssemblyPlayback import (
                    owns_active_native_assembly_playback,
                )

                if owns_active_native_assembly_playback(self.document):
                    return
            except (ImportError, RuntimeError):
                pass
        if allow_owned_cam_simulation:
            try:
                from Path.Main.Gui.SimulatorGL import (
                    owns_active_prepared_simulation,
                )

                if owns_active_prepared_simulation(self.document):
                    return
            except (ImportError, RuntimeError):
                pass
        raise NativeRuntimeContextError(
            "Finish or close the active task before using this Native operation."
        )
