# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for stable exact FEM mesh content hashes."""

from __future__ import annotations

from io import BytesIO
import zipfile

import VibeCADNativeAnalyzeMeshState as mesh_state
from VibeCADNativeAnalyzeMeshState import _mesh_content_sha256


def _archive(unv: str) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("FemMesh.unv", unv)
        archive.writestr("Persistence.xml", "<Persistence version='1'/>")
    return stream.getvalue()


class _FemMesh:
    NodeCount = 4

    def __init__(self, unv: str, groups: dict[int, tuple[str, str, tuple[int, ...]]]):
        self._payload = _archive(unv)
        self._groups = groups
        self.Groups = tuple(groups)

    def dumpContent(self) -> bytes:
        return self._payload

    def getGroupName(self, group_id: int) -> str:
        return self._groups[group_id][0]

    def getGroupElementType(self, group_id: int) -> str:
        return self._groups[group_id][1]

    def getGroupElements(self, group_id: int) -> tuple[int, ...]:
        return self._groups[group_id][2]


_PREFIX = """    -1
  2411
node records stay exact
    -1
    -1
  2467
"""


def test_mesh_content_hash_ignores_serialized_group_order_and_ids() -> None:
    before = _FemMesh(
        _PREFIX
        + "         1         0         0         0         0         0         0         1\n"
        + "Edge1\n         8         1         0         0\n"
        + "         2         0         0         0         0         0         0         1\n"
        + "Face1\n         8        10         0         0\n    -1\n",
        {
            1: ("Edge1", "Edge", (1,)),
            2: ("Face1", "Face", (10,)),
        },
    )
    reopened = _FemMesh(
        _PREFIX
        + "         7         0         0         0         0         0         0         1\n"
        + "Face1\n         8        10         0         0\n"
        + "         9         0         0         0         0         0         0         1\n"
        + "Edge1\n         8         1         0         0\n    -1\n",
        {
            7: ("Face1", "Face", (110,)),
            9: ("Edge1", "Edge", (101,)),
        },
    )

    assert _mesh_content_sha256(before) == _mesh_content_sha256(reopened)


def test_mesh_content_hash_retains_exact_group_membership() -> None:
    first = _FemMesh(
        _PREFIX
        + "         1         0         0         0         0         0         0         1\n"
        + "Face1\n         8        10         0         0\n    -1\n",
        {1: ("Face1", "Face", (10,))},
    )
    changed = _FemMesh(
        _PREFIX
        + "         8         0         0         0         0         0         0         1\n"
        + "Face1\n         8        11         0         0\n    -1\n",
        {8: ("Face1", "Face", (11,))},
    )

    assert _mesh_content_sha256(first) != _mesh_content_sha256(changed)


class _DefinitionMesh:
    NodeCount = 140_228
    EdgeCount = 0
    FaceCount = 0
    VolumeCount = 78_832

    def __init__(self) -> None:
        self.dump_count = 0

    def dumpContent(self) -> bytes:
        self.dump_count += 1
        raise AssertionError("context capture must not serialize FEM mesh content")


class _Definition:
    Name = "FEMMeshGmsh"
    Label = "Large mesh"
    ID = 42

    def __init__(self) -> None:
        self.FemMesh = _DefinitionMesh()


def _patch_definition_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(mesh_state, "is_live", lambda _document, _obj: True)
    monkeypatch.setattr(mesh_state, "fem_mesher_kind", lambda _obj: "gmsh")
    monkeypatch.setattr(
        mesh_state,
        "_source",
        lambda _obj: (
            {
                "object_name": "Body",
                "state_sha256": "a" * 64,
                "shape_type": "Solid",
                "topology": {"solids": 1},
            },
            {
                "object_name": "Body",
                "object_id": 7,
                "state_sha256": "a" * 64,
                "shape_type": "Solid",
                "topology": {"solids": 1},
            },
        ),
    )
    monkeypatch.setattr(mesh_state, "_settings", lambda _obj, _kind: {"order": 2})
    monkeypatch.setattr(mesh_state, "_native_parameters", lambda _obj: {})
    monkeypatch.setattr(
        mesh_state,
        "concise_object",
        lambda obj: {"object_name": obj.Name, "label": obj.Label},
    )


def test_mesh_definition_context_state_never_serializes_mesh_content(monkeypatch) -> None:
    _patch_definition_dependencies(monkeypatch)
    definition = _Definition()

    state = mesh_state.fem_mesh_definition_context_state(definition)

    assert state["generated"] is True
    assert state["topology"] == {
        "nodes": 140_228,
        "edges": 0,
        "faces": 0,
        "volumes": 78_832,
    }
    assert "mesh_content_sha256" not in state
    assert definition.FemMesh.dump_count == 0


def test_mesh_definition_match_accepts_context_state_without_exact_hash(monkeypatch) -> None:
    _patch_definition_dependencies(monkeypatch)
    definition = _Definition()
    expected = mesh_state.fem_mesh_definition_context_state(definition)["state_sha256"]

    assert mesh_state.fem_mesh_definition_still_exact(definition, expected) is True
    assert definition.FemMesh.dump_count == 0


def test_mesh_definition_match_falls_back_to_legacy_exact_state(monkeypatch) -> None:
    _patch_definition_dependencies(monkeypatch)
    definition = _Definition()
    monkeypatch.setattr(mesh_state, "_mesh_content_sha256", lambda _mesh: "b" * 64)
    legacy = mesh_state.fem_mesh_definition_state(definition)["state_sha256"]

    assert mesh_state.fem_mesh_definition_still_exact(definition, legacy) is True


def test_baked_mesh_context_state_never_serializes_mesh_content(monkeypatch) -> None:
    monkeypatch.setattr(mesh_state, "is_live", lambda _document, _obj: True)
    monkeypatch.setattr(
        mesh_state,
        "concise_object",
        lambda obj: {"object_name": obj.Name, "label": obj.Label},
    )

    class BakedMesh:
        Name = "BakedMesh"
        Label = "Baked mesh"
        ID = 99

        def __init__(self) -> None:
            self.FemMesh = _DefinitionMesh()
            self.Proxy = None
            self.Document = object()

        @staticmethod
        def isDerivedFrom(type_id: str) -> bool:
            return type_id == "Fem::FemMeshObject"

    baked = BakedMesh()

    state = mesh_state.fem_mesh_object_context_state(baked)

    assert state["backend"] == "baked"
    assert state["generated"] is True
    assert "mesh_content_sha256" not in state
    assert baked.FemMesh.dump_count == 0
