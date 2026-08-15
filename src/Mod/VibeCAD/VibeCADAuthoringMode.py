# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-owned Native/VibeScript authoring-mode state.

Unsaved documents retain their choice only in this process. Saved documents
read and write their project manifest through callbacks supplied by the project
store. This module has no FreeCAD, GUI, provider, or tool-dispatch dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


AUTHORING_MODES = frozenset({"native", "vibescript"})
DEFAULT_AUTHORING_MODE = "vibescript"

ModeReader = Callable[[Path], str]
ModeWriter = Callable[[Path, str], None]


def normalize_authoring_mode(value: Any) -> str:
    mode = str(value or DEFAULT_AUTHORING_MODE).strip().lower()
    if mode not in AUTHORING_MODES:
        raise RuntimeError(
            f"VibeCAD project selects unsupported authoring mode {mode!r}; "
            f"choose one of: {sorted(AUTHORING_MODES)}."
        )
    return mode


@dataclass(frozen=True, slots=True)
class AuthoringModeScope:
    document_identity: str
    manifest_path: Path
    saved: bool

    @classmethod
    def from_project_scope(
        cls,
        scope: dict[str, Any],
        *,
        session_id: str,
    ) -> "AuthoringModeScope":
        document = scope.get("document")
        if not isinstance(document, dict):
            document = {}
        identity = str(
            document.get("uid")
            or document.get("document")
            or scope.get("project_id")
            or session_id
        ).strip()
        raw_manifest_path = str(scope.get("manifest_path") or "").strip()
        if not identity or not raw_manifest_path:
            raise RuntimeError("VibeCAD authoring mode has no document scope.")
        manifest_path = Path(raw_manifest_path).expanduser()
        return cls(
            document_identity=identity,
            manifest_path=manifest_path,
            saved=bool(scope.get("document_saved") or document.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class AuthoringModeSelection:
    mode: str
    persistence: str

    def summary(self) -> dict[str, str]:
        return {"mode": self.mode, "persistence": self.persistence}


class AuthoringModeStore:
    """Own session choices and delegate saved-project persistence exactly."""

    def __init__(self) -> None:
        self._session_modes: dict[str, str] = {}

    def current(
        self,
        scope: AuthoringModeScope,
        read_mode: ModeReader,
    ) -> AuthoringModeSelection:
        if not isinstance(scope, AuthoringModeScope):
            raise TypeError("scope must be an AuthoringModeScope")
        pending = self._session_modes.get(scope.document_identity)
        if pending is not None:
            return AuthoringModeSelection(
                pending,
                "project_pending" if scope.saved else "session",
            )
        if not scope.saved:
            return AuthoringModeSelection(DEFAULT_AUTHORING_MODE, "session")
        return AuthoringModeSelection(
            normalize_authoring_mode(read_mode(scope.manifest_path)),
            "project",
        )

    def select(
        self,
        scope: AuthoringModeScope,
        mode: str,
        write_mode: ModeWriter,
    ) -> AuthoringModeSelection:
        if not isinstance(scope, AuthoringModeScope):
            raise TypeError("scope must be an AuthoringModeScope")
        selected = normalize_authoring_mode(mode)
        if scope.saved:
            write_mode(scope.manifest_path, selected)
            self._session_modes.pop(scope.document_identity, None)
            return AuthoringModeSelection(selected, "project")
        self._session_modes[scope.document_identity] = selected
        return AuthoringModeSelection(selected, "session")

    def persist_after_save(
        self,
        scope: AuthoringModeScope,
        read_mode: ModeReader,
        write_mode: ModeWriter,
    ) -> AuthoringModeSelection:
        if not isinstance(scope, AuthoringModeScope):
            raise TypeError("scope must be an AuthoringModeScope")
        if not scope.saved:
            raise RuntimeError("Cannot persist authoring mode before document save.")
        pending = self._session_modes.get(scope.document_identity)
        if pending is not None:
            write_mode(scope.manifest_path, pending)
            self._session_modes.pop(scope.document_identity, None)
            return AuthoringModeSelection(pending, "project")
        return AuthoringModeSelection(
            normalize_authoring_mode(read_mode(scope.manifest_path)),
            "project_unchanged",
        )

    def discard_document(self, document_identity: str) -> None:
        self._session_modes.pop(str(document_identity or "").strip(), None)

    @property
    def session_document_count(self) -> int:
        return len(self._session_modes)
