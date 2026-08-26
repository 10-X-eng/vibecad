# SPDX-License-Identifier: LGPL-2.1-or-later
"""Transition contract for moving Aero CAD repairs onto host Native authority.

Current upstream already owns generic preview/apply/reject, structural revisions,
and preservation of user-explicit intent.  Aero should not recreate those
mechanisms.  Until `aero.*` mutations are registered directly on the host Native
surface, this bridge shows the minimum data that `/v1/aero` must thread through
its existing Aero repair preview: host revision + geometry fingerprint + the
user-explicit intent fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def user_explicit_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    selected = []
    for row in rows:
        if str(row.get("kind") or "") != "user_explicit":
            continue
        selected.append({str(k): row[k] for k in sorted(row) if k not in {"updated_at", "last_used_at"}})
    return _stable_hash(selected)


@dataclass(frozen=True)
class RepairAuthoritySnapshot:
    document_uid: str
    native_revision: int
    geometry_revision: str
    user_explicit_sha256: str


def capture(*, document_uid: str, native_revision: int, geometry_revision: str, intent_rows: Iterable[Mapping[str, Any]] = ()) -> RepairAuthoritySnapshot:
    if type(native_revision) is not int or native_revision < 0:
        raise ValueError("native_revision must be a non-negative integer")
    return RepairAuthoritySnapshot(
        str(document_uid), int(native_revision), str(geometry_revision), user_explicit_fingerprint(intent_rows)
    )


def validate_apply(snapshot: RepairAuthoritySnapshot, *, current_native_revision: int, current_geometry_revision: str, current_intent_rows: Iterable[Mapping[str, Any]] = ()) -> None:
    if int(current_native_revision) != snapshot.native_revision:
        raise ValueError("native_revision_stale")
    if str(current_geometry_revision) != snapshot.geometry_revision:
        raise ValueError("geometry_revision_stale")
    if user_explicit_fingerprint(current_intent_rows) != snapshot.user_explicit_sha256:
        raise ValueError("user_explicit_intent_changed")
