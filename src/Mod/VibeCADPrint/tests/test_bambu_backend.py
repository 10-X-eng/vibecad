# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import zipfile

import pytest

import BambuStudio
import VibeCADPrint


def _write_profile(root: Path, folder: str, data: dict) -> None:
    target = root / "TestVendor" / folder / f"{data['name']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")


def _profile_store(root: Path) -> None:
    _write_profile(
        root,
        "machine",
        {
            "name": "test_machine_end_gcode",
            "instantiation": "false",
            "machine_end_gcode": "M84",
        },
    )
    _write_profile(
        root,
        "machine",
        {
            "type": "machine",
            "name": "test_machine_base",
            "printable_area": ["0x0", "256x0", "256x256", "0x256"],
            "printable_height": "250",
            "machine_start_gcode": "G28",
        },
    )
    _write_profile(
        root,
        "machine",
        {
            "type": "machine",
            "name": "Test Printer 0.4 nozzle",
            "inherits": "test_machine_base",
            "include": ["test_machine_end_gcode"],
            "instantiation": "true",
            "printer_model": "Test Printer",
            "printer_variant": "0.4",
            "nozzle_diameter": ["0.4"],
        },
    )
    _write_profile(
        root,
        "process",
        {
            "type": "process",
            "name": "test_process_base",
            "layer_height": "0.2",
        },
    )
    _write_profile(
        root,
        "process",
        {
            "type": "process",
            "name": "0.20mm Standard @TEST",
            "inherits": "test_process_base",
            "instantiation": "true",
            "compatible_printers": ["Test Printer 0.4 nozzle"],
        },
    )
    _write_profile(
        root,
        "process",
        {
            "type": "process",
            "name": "Wrong printer quality",
            "instantiation": "true",
            "compatible_printers": ["Another Printer"],
        },
    )
    _write_profile(
        root,
        "filament",
        {
            "type": "filament",
            "name": "test_filament_base",
            "filament_type": ["PLA"],
        },
    )
    _write_profile(
        root,
        "filament",
        {
            "type": "filament",
            "name": "Generic PLA @TEST",
            "inherits": "test_filament_base",
            "instantiation": "true",
            "compatible_printers": ["Test Printer 0.4 nozzle"],
        },
    )
    _write_profile(
        root,
        "filament",
        {
            "type": "filament",
            "name": "Wrong printer filament",
            "instantiation": "true",
            "compatible_printers": ["Another Printer"],
        },
    )


def _installation(root: Path) -> VibeCADPrint.SlicerInstallation:
    return VibeCADPrint.SlicerInstallation(
        backend_id="bambustudio",
        version="2.8.2.61",
        gui_command=("bambu-studio",),
        cli_command=("bambu-studio",),
        source="path",
        display_name="Bambu Studio",
        resource_dir=str(root),
        tested_version=(2, 8, 2),
    )


def _source_3mf(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "3D/3dmodel.model",
            '<model><resources><object id="1" name="Frame"/>'
            '<object id="2" name="Rotor"/></resources></model>',
        )


def _prepared_3mf(path: Path, names: tuple[str, ...]) -> None:
    objects = "".join(
        f'<object id="{index}"><metadata key="name" value="{name}"/></object>'
        for index, name in enumerate(names, start=1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/model_settings.config", f"<config>{objects}</config>")
        archive.writestr("Metadata/project_settings.config", "{}")


def test_bambu_catalog_resolves_inheritance_and_exact_compatibility(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    _profile_store(root)
    backend = BambuStudio.BambuStudioBackend()
    installation = _installation(root)

    printers = backend.query_printers(installation)
    catalog = backend.query_profiles(installation, printers[0].name)
    resolved = BambuStudio.resolved_profile(
        installation,
        "machine",
        printers[0].name,
    )

    assert [printer.name for printer in printers] == ["Test Printer 0.4 nozzle"]
    assert printers[0].bed.width == 256
    assert printers[0].bed.height == 256
    assert printers[0].bed.max_print_height == 250
    assert [profile.name for profile in catalog.print_profiles] == [
        "0.20mm Standard @TEST"
    ]
    assert [
        material.name for material in catalog.print_profiles[0].materials
    ] == ["Generic PLA @TEST"]
    assert resolved["machine_start_gcode"] == "G28"
    assert resolved["machine_end_gcode"] == "M84"
    assert resolved["printer_model"] == "Test Printer"


@pytest.mark.parametrize(
    ("auto_arrange", "arrange_value"),
    [(True, "1"), (False, "0")],
)
def test_prepare_bambu_project_uses_full_profiles_and_preserves_named_objects(
    tmp_path: Path,
    auto_arrange: bool,
    arrange_value: str,
) -> None:
    root = tmp_path / "profiles"
    _profile_store(root)
    source = tmp_path / "fan.3mf"
    destination = tmp_path / "fan-bambu.3mf"
    _source_3mf(source)
    setup = VibeCADPrint.PrintSetup(
        "Test Printer 0.4 nozzle",
        "0.20mm Standard @TEST",
        ("Generic PLA @TEST",),
        auto_arrange=auto_arrange,
        ensure_on_bed=True,
    )
    commands = []

    def runner(command, **_kwargs):
        commands.append(tuple(command))
        settings = command[command.index("--load-settings") + 1].split(";")
        machine = json.loads(Path(settings[0]).read_text(encoding="utf-8"))
        process = json.loads(Path(settings[1]).read_text(encoding="utf-8"))
        filament_path = Path(command[command.index("--load-filaments") + 1])
        filament = json.loads(filament_path.read_text(encoding="utf-8"))
        assert machine["printable_area"][-1] == "0x256"
        assert machine["machine_start_gcode"] == "G28"
        assert process["layer_height"] == "0.2"
        assert filament["filament_type"] == ["PLA"]
        output = Path(command[command.index("--export-3mf") + 1])
        _prepared_3mf(output, ("Frame", "Rotor"))
        return subprocess.CompletedProcess(command, 0, "prepared", "")

    actual = BambuStudio.prepare_bambu_project(
        _installation(root),
        source,
        destination,
        setup,
        runner=runner,
    )

    assert actual == destination
    arrange_index = commands[0].index("--arrange")
    assert commands[0][arrange_index + 1] == arrange_value
    assert "--ensure-on-bed" in commands[0]
    assert destination.is_file()
    assert not list(tmp_path.glob("*.partial.3mf"))
    assert not list(tmp_path.glob("*.profile.json"))


def test_prepare_bambu_project_rejects_object_collapse_and_preserves_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    _profile_store(root)
    source = tmp_path / "fan.3mf"
    _source_3mf(source)
    original = source.read_bytes()
    setup = VibeCADPrint.PrintSetup(
        "Test Printer 0.4 nozzle",
        "0.20mm Standard @TEST",
        ("Generic PLA @TEST",),
    )

    def runner(command, **_kwargs):
        output = Path(command[command.index("--export-3mf") + 1])
        _prepared_3mf(output, ("Frame",))
        return subprocess.CompletedProcess(command, 0, "prepared", "")

    with pytest.raises(VibeCADPrint.SlicerError, match="object count"):
        BambuStudio.prepare_bambu_project(
            _installation(root),
            source,
            source,
            setup,
            runner=runner,
        )

    assert source.read_bytes() == original
    assert not list(tmp_path.glob("*.partial.3mf"))
