# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD-independent bridge contract between Aero evidence and Native authority.

Pass 03 retains the Pass-02 conclusion that VibeCADAero against VibeCAD's now-mature host-owned Native
revision/preview store.  This module does *not* duplicate that store.  It only
captures the host structural revision together with Aero's geometry fingerprint
and decides whether a long-running result is still current enough to attach as
active evidence.

Long-running solver jobs are not CAD mutation previews.  They may run for hours
and must be preserved even if CAD changes.  A stale result is historical evidence,
not failed evidence; it simply may not silently replace the current Aero result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AttachmentState(str, Enum):
    CURRENT = "current"
    STALE_NATIVE_REVISION = "stale_native_revision"
    STALE_GEOMETRY = "stale_geometry"
    STALE_BOTH = "stale_native_and_geometry"


@dataclass(frozen=True)
class AuthoritySnapshot:
    document_uid: str
    native_revision: int
    geometry_revision: str

    def __post_init__(self) -> None:
        if not str(self.document_uid).strip():
            raise ValueError("document_uid is required")
        if type(self.native_revision) is not int or self.native_revision < 0:
            raise ValueError("native_revision must be a non-negative integer")
        if not str(self.geometry_revision).strip():
            raise ValueError("geometry_revision is required")


@dataclass(frozen=True)
class AttachmentDecision:
    state: AttachmentState
    current: bool
    captured_native_revision: int
    current_native_revision: int
    captured_geometry_revision: str
    current_geometry_revision: str

    @property
    def preserve_as_history(self) -> bool:
        return not self.current


def capture_authority(
    state_store: Any,
    *,
    document_uid: str,
    geometry_revision: str,
) -> AuthoritySnapshot:
    """Read VibeCAD's host revision without taking ownership of its state store."""
    getter = getattr(state_store, "current_revision", None)
    if not callable(getter):
        raise TypeError("state_store must expose current_revision(document_uid)")
    return AuthoritySnapshot(
        document_uid=str(document_uid),
        native_revision=int(getter(document_uid)),
        geometry_revision=str(geometry_revision),
    )


def decide_attachment(
    snapshot: AuthoritySnapshot,
    *,
    current_native_revision: int,
    current_geometry_revision: str,
) -> AttachmentDecision:
    native_changed = int(current_native_revision) != snapshot.native_revision
    geometry_changed = str(current_geometry_revision) != snapshot.geometry_revision
    if native_changed and geometry_changed:
        state = AttachmentState.STALE_BOTH
    elif native_changed:
        state = AttachmentState.STALE_NATIVE_REVISION
    elif geometry_changed:
        state = AttachmentState.STALE_GEOMETRY
    else:
        state = AttachmentState.CURRENT
    return AttachmentDecision(
        state=state,
        current=state is AttachmentState.CURRENT,
        captured_native_revision=snapshot.native_revision,
        current_native_revision=int(current_native_revision),
        captured_geometry_revision=snapshot.geometry_revision,
        current_geometry_revision=str(current_geometry_revision),
    )
