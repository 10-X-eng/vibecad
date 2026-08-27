# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeAssemblyIdentity import (
    IDENTITY_KIND_PROPERTY,
    IDENTITY_PROPERTY,
    IDENTITY_SCHEMA,
    IDENTITY_SCHEMA_PROPERTY,
    NativeAssemblyIdentityError,
    assign_persistent_identity,
    connector_persistent_identity,
    read_persistent_identity,
)


class Object(SimpleNamespace):
    def __init__(self):
        super().__init__(PropertiesList=[])
        self.editor_modes = {}

    def addProperty(self, _type_id, name, _group, _description):
        self.PropertiesList.append(name)

    def setEditorMode(self, name, mode):
        self.editor_modes[name] = mode


def test_identity_is_assigned_once_and_survives_transient_renames() -> None:
    obj = Object()
    obj.Name = "Joint001"
    obj.Label = "Hinge"

    first = assign_persistent_identity(
        obj,
        "joint",
        identity_factory=lambda: "12345678-1234-4234-9234-123456789abc",
    )
    obj.Name = "RenamedJoint"
    obj.Label = "Renamed hinge"
    second = assign_persistent_identity(
        obj,
        "joint",
        identity_factory=lambda: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )

    assert first == second == {
        "schema": IDENTITY_SCHEMA,
        "kind": "joint",
        "persistent_id": "12345678-1234-4234-9234-123456789abc",
    }
    assert set(obj.editor_modes) == {
        IDENTITY_PROPERTY,
        IDENTITY_KIND_PROPERTY,
        IDENTITY_SCHEMA_PROPERTY,
    }


def test_partial_or_contradictory_identity_is_rejected_without_repair() -> None:
    partial = Object()
    setattr(partial, IDENTITY_PROPERTY, "12345678-1234-4234-9234-123456789abc")
    with pytest.raises(NativeAssemblyIdentityError, match="kind"):
        assign_persistent_identity(partial, "joint")

    occurrence = Object()
    assign_persistent_identity(
        occurrence,
        "occurrence",
        identity_factory=lambda: "12345678-1234-4234-9234-123456789abc",
    )
    with pytest.raises(NativeAssemblyIdentityError, match="does not match"):
        read_persistent_identity(occurrence, expected_kind="joint")


def test_connector_identity_is_derived_from_joint_and_side() -> None:
    joint = Object()
    assert connector_persistent_identity(joint, 1)["assigned"] is False
    assign_persistent_identity(
        joint,
        "joint",
        identity_factory=lambda: "12345678-1234-4234-9234-123456789abc",
    )

    connector = connector_persistent_identity(joint, 2)

    assert connector["persistent_id"] == (
        "12345678-1234-4234-9234-123456789abc:connector:2"
    )
    assert connector["side"] == 2
