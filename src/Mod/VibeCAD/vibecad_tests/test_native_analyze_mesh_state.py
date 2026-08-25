# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for stable exact FEM mesh content hashes."""

from __future__ import annotations

from io import BytesIO
import zipfile

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
