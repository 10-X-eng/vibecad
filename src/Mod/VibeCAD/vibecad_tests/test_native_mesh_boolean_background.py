# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path

from VibeCADNativeMeshBooleanSchema import mesh_boolean_capability_definition


SOURCE_ROOT = Path(__file__).resolve().parents[3]


def test_mesh_boolean_contract_requires_background_execution() -> None:
    definition = mesh_boolean_capability_definition()

    assert definition.variants
    assert all(variant.background_required for variant in definition.variants)


def test_human_mesh_boolean_uses_the_shared_background_path() -> None:
    command = (
        SOURCE_ROOT / "Mod" / "Mesh" / "Gui" / "Command.cpp"
    ).read_text(encoding="utf-8")
    body = command.split("void runNativeMeshBoolean(", 1)[1].split(
        "DEF_STD_CMD_A(CmdMeshUnion)", 1
    )[0]

    assert 'callMemberFunction("start_mesh_boolean"' in body
    assert "Gui::WaitCursor" not in body


def test_cached_mesh_boolean_recompute_does_not_repeat_brep_work() -> None:
    header = (
        SOURCE_ROOT / "Mod" / "MeshPart" / "App" / "MeshBoolean.h"
    ).read_text(encoding="utf-8")
    source = (
        SOURCE_ROOT / "Mod" / "MeshPart" / "App" / "MeshBoolean.cpp"
    ).read_text(encoding="utf-8")

    assert "App::PropertyBool UpdateFromSource;" in header
    assert "if (!UpdateFromSource.getValue())" in source
