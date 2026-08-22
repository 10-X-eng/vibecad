# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADLiveAcceptanceOracle import (
    AssemblyExpectations,
    LiveAcceptanceError,
    copy_linked_document_dependencies,
    validate_assembly_input_snapshot,
    validate_assembly_snapshot,
)


def test_live_artifact_copies_exact_linked_document_dependencies(tmp_path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    main_file = source / "main.FCStd"
    linked_file = source / "linked.FCStd"
    main_file.write_bytes(b"main")
    linked_file.write_bytes(b"linked")
    main = SimpleNamespace(FileName=str(main_file))
    linked = SimpleNamespace(FileName=str(linked_file))
    main.getDependentDocuments = lambda: [linked, main]

    copied = copy_linked_document_dependencies(main, output)

    assert copied == (output / "linked.FCStd",)
    assert copied[0].read_bytes() == b"linked"


def _snapshot(*, components: int = 2) -> dict:
    return {
        "kind": "assembly",
        "assembly_count": 1,
        "assemblies": [
            {
                "object_name": "DriveAssembly",
                "label": "Drive assembly",
                "counts": {
                    "components": components,
                    "joints": 1,
                    "grounded": 1,
                },
                "bom_state": {
                    "available": True,
                    "bom_count": 1,
                    "boms": [
                        {
                            "object_name": "BillOfMaterials",
                            "label": "Drive BOM",
                            "row_count": 2,
                        }
                    ],
                },
                "solver_health": {
                    "status": "solved",
                    "remaining_degrees_of_freedom": 1,
                },
            }
        ],
    }


def test_assembly_oracle_returns_exact_evidence() -> None:
    evidence = validate_assembly_snapshot(
        _snapshot(),
        AssemblyExpectations(
            assemblies=1,
            components=2,
            joints=1,
            grounded=1,
            boms=1,
            remaining_degrees_of_freedom=1,
        ),
    )

    assert evidence == {
        "assembly": {
            "object_name": "DriveAssembly",
            "label": "Drive assembly",
        },
        "counts": {
            "assemblies": 1,
            "components": 2,
            "joints": 1,
            "grounded": 1,
            "boms": 1,
            "bom_rows": [2],
            "remaining_degrees_of_freedom": 1,
        },
        "solver_status": "solved",
    }


def test_assembly_oracle_identifies_the_exact_failed_count() -> None:
    with pytest.raises(
        LiveAcceptanceError,
        match=r"Assembly components: expected 2, found 3\.",
    ):
        validate_assembly_snapshot(
            _snapshot(components=3),
            AssemblyExpectations(assemblies=1, components=2),
        )


def test_assembly_input_oracle_requires_a_source_only_document() -> None:
    assert validate_assembly_input_snapshot(
        {
            "kind": "assembly",
            "assembly_count": 0,
            "assembly_owned_object_count": 0,
            "assemblies": [],
        }
    ) == {"assembly_count": 0, "assembly_owned_object_count": 0}

    with pytest.raises(
        LiveAcceptanceError,
        match=r"Assembly input must be source-only: expected 0 Assemblies, found 1\.",
    ):
        validate_assembly_input_snapshot(_snapshot())

    with pytest.raises(
        LiveAcceptanceError,
        match=r"Assembly input contains 2 Assembly-owned objects\.",
    ):
        validate_assembly_input_snapshot(
            {
                "kind": "assembly",
                "assembly_count": 0,
                "assembly_owned_object_count": 2,
                "assemblies": [],
            }
        )


def test_assembly_input_oracle_can_require_one_existing_assembly() -> None:
    evidence = validate_assembly_input_snapshot(
        _snapshot(),
        allow_existing=True,
    )

    assert evidence["assembly"]["object_name"] == "DriveAssembly"
    assert evidence["counts"]["assemblies"] == 1
    assert evidence["counts"]["components"] == 2
