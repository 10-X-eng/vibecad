# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import VibeCADPrint


class _Shape:
    def __init__(self, null: bool = False) -> None:
        self._null = null

    def isNull(self) -> bool:
        return self._null


def _object(name: str, *, document=None, printable: bool = True):
    return SimpleNamespace(
        Name=name,
        Label=name,
        Document=document,
        Shape=_Shape(null=not printable),
    )


def test_collect_printable_objects_deduplicates_owners_and_rejects_mixed_selection() -> (
    None
):
    document = object()
    first = _object("First", document=document)
    unsupported = _object("Spreadsheet", document=document, printable=False)

    with pytest.raises(VibeCADPrint.PrintSelectionError) as error:
        VibeCADPrint.collect_printable_objects(
            [first, first, unsupported], active_document=document
        )

    assert "Spreadsheet" in str(error.value)
    assert "First" not in str(error.value)


def test_collect_printable_objects_requires_explicit_selection_and_active_document() -> (
    None
):
    with pytest.raises(VibeCADPrint.PrintSelectionError, match="active document"):
        VibeCADPrint.collect_printable_objects([], active_document=None)
    with pytest.raises(VibeCADPrint.PrintSelectionError, match="Select at least one"):
        VibeCADPrint.collect_printable_objects([], active_document=object())


def test_export_selection_is_atomic_and_keeps_separate_objects(tmp_path: Path) -> None:
    document = object()
    objects = [
        _object("First", document=document),
        _object("Second", document=document),
    ]
    calls = []

    def mesh_export(selected, path):
        calls.append((tuple(selected), path))
        Path(path).write_bytes(b"3MF payload")

    destination = tmp_path / "plate.3mf"
    result = VibeCADPrint.export_selection_3mf(
        objects,
        destination,
        mesh_exporter=mesh_export,
    )

    assert result == destination
    assert destination.read_bytes() == b"3MF payload"
    assert calls[0][0] == tuple(objects)
    assert calls[0][1].endswith(".partial.3mf")
    assert not list(tmp_path.glob("*.partial.3mf"))


def test_export_objects_stl_writes_one_named_model_per_object(tmp_path: Path) -> None:
    document = object()
    objects = [
        _object("Fan Frame", document=document),
        _object("Fan/Frame", document=document),
    ]
    calls = []

    def mesh_export(selected, path):
        calls.append((tuple(selected), Path(path)))
        Path(path).write_bytes(b"solid model")

    result = VibeCADPrint.export_objects_stl(
        objects,
        tmp_path,
        mesh_exporter=mesh_export,
    )

    assert result == (
        tmp_path / "0001-Fan-Frame.stl",
        tmp_path / "0002-Fan-Frame.stl",
    )
    assert [selected for selected, _path in calls] == [
        (objects[0],),
        (objects[1],),
    ]
    assert all(path.read_bytes() == b"solid model" for path in result)


def test_managed_handoff_prunes_only_owned_old_files(tmp_path: Path) -> None:
    for index in range(12):
        path = tmp_path / f"vibecad-print-old-{index:02d}.3mf"
        path.write_text(str(index), encoding="utf-8")
        path.touch()
    unrelated = tmp_path / "keep-me.3mf"
    unrelated.write_text("user", encoding="utf-8")

    destination = VibeCADPrint.managed_handoff_path(
        tmp_path,
        document_label="A Plate / With Spaces",
        object_names=("First", "Second"),
        keep=10,
    )

    assert destination.parent == tmp_path
    assert destination.name.startswith("vibecad-print-A-Plate-With-Spaces-")
    assert unrelated.exists()
    assert len(list(tmp_path.glob("vibecad-print-*.3mf"))) == 10


def test_persistent_handoff_path_never_prunes_user_folder(tmp_path: Path) -> None:
    existing = tmp_path / "previous-print.3mf"
    existing.write_bytes(b"keep")

    destination = VibeCADPrint.persistent_handoff_path(
        tmp_path,
        document_label="Gearbox / Rev B",
        object_names=("Housing", "Cover"),
    )

    assert destination.parent == tmp_path
    assert destination.name.startswith("Gearbox-Rev-B-")
    assert destination.suffix == ".3mf"
    assert existing.read_bytes() == b"keep"
