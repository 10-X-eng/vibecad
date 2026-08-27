# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persisted, rename-independent identities for Native Assembly graph objects."""

from __future__ import annotations

import re
from typing import Any, Callable
import uuid


IDENTITY_SCHEMA = "vibecad-assembly-identity-v1"
IDENTITY_PROPERTY = "VibeCADAssemblyPersistentIdentity"
IDENTITY_KIND_PROPERTY = "VibeCADAssemblyIdentityKind"
IDENTITY_SCHEMA_PROPERTY = "VibeCADAssemblyIdentitySchema"
IDENTITY_GROUP = "VibeCAD Assembly Identity"
IDENTITY_KINDS = frozenset({
    "assembly",
    "joint_group",
    "occurrence",
    "joint",
    "interface",
})
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class NativeAssemblyIdentityError(RuntimeError):
    """A persisted Assembly identity is missing, malformed, or contradictory."""


def _kind(value: Any) -> str:
    kind = str(value or "").strip()
    if kind not in IDENTITY_KINDS:
        raise NativeAssemblyIdentityError(f"Unknown Assembly identity kind: {kind!r}")
    return kind


def _uuid(value: Any) -> str:
    identity = str(value or "").strip().lower()
    if not _UUID.fullmatch(identity):
        raise NativeAssemblyIdentityError("Assembly persistent identity is not a UUID.")
    return identity


def read_persistent_identity(obj: Any, *, expected_kind: str | None = None) -> dict[str, str] | None:
    """Read a complete identity; return None only when no identity fields exist."""

    values = (
        getattr(obj, IDENTITY_PROPERTY, None),
        getattr(obj, IDENTITY_KIND_PROPERTY, None),
        getattr(obj, IDENTITY_SCHEMA_PROPERTY, None),
    )
    if all(value in (None, "") for value in values):
        return None
    identity = _uuid(values[0])
    kind = _kind(values[1])
    if str(values[2] or "") != IDENTITY_SCHEMA:
        raise NativeAssemblyIdentityError("Assembly persistent identity schema is invalid.")
    if expected_kind is not None and kind != _kind(expected_kind):
        raise NativeAssemblyIdentityError(
            f"Assembly identity kind {kind!r} does not match {expected_kind!r}."
        )
    return {"schema": IDENTITY_SCHEMA, "kind": kind, "persistent_id": identity}


def assign_persistent_identity(
    obj: Any,
    kind: str,
    *,
    identity_factory: Callable[[], Any] = uuid.uuid4,
) -> dict[str, str]:
    """Assign once inside an owning mutation; never repair or replace silently."""

    expected_kind = _kind(kind)
    existing = read_persistent_identity(obj, expected_kind=expected_kind)
    if existing is not None:
        return existing
    add_property = getattr(obj, "addProperty", None)
    if not callable(add_property):
        raise NativeAssemblyIdentityError(
            "The Native Assembly object cannot persist an identity property."
        )
    identity = _uuid(str(identity_factory()))
    definitions = (
        (IDENTITY_PROPERTY, "Stable identity across rename, reorder, save, and reopen"),
        (IDENTITY_KIND_PROPERTY, "Stable Assembly graph identity kind"),
        (IDENTITY_SCHEMA_PROPERTY, "Stable Assembly identity schema"),
    )
    properties = set(getattr(obj, "PropertiesList", ()) or ())
    for name, description in definitions:
        if name not in properties:
            add_property("App::PropertyString", name, IDENTITY_GROUP, description)
    setattr(obj, IDENTITY_PROPERTY, identity)
    setattr(obj, IDENTITY_KIND_PROPERTY, expected_kind)
    setattr(obj, IDENTITY_SCHEMA_PROPERTY, IDENTITY_SCHEMA)
    editor_mode = getattr(obj, "setEditorMode", None)
    if callable(editor_mode):
        for name, _description in definitions:
            editor_mode(name, 1)
    return read_persistent_identity(obj, expected_kind=expected_kind)  # type: ignore[return-value]


def connector_persistent_identity(joint: Any, side: int) -> dict[str, Any]:
    """Derive connector identity from the persisted joint and explicit side."""

    if side not in {1, 2}:
        raise NativeAssemblyIdentityError("Assembly connector side must be 1 or 2.")
    joint_identity = read_persistent_identity(joint, expected_kind="joint")
    if joint_identity is None:
        return {"schema": IDENTITY_SCHEMA, "kind": "connector", "assigned": False, "side": side}
    return {
        "schema": IDENTITY_SCHEMA,
        "kind": "connector",
        "persistent_id": f"{joint_identity['persistent_id']}:connector:{side}",
        "joint_persistent_id": joint_identity["persistent_id"],
        "side": side,
    }
