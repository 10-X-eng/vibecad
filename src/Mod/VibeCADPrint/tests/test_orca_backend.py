# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import subprocess

import OrcaSlicer
import VibeCADPrint


def test_orca_flatpak_discovery_uses_its_own_profile_and_config_roots(
    tmp_path: Path,
) -> None:
    location = tmp_path / "flatpak-orca"
    profiles = location / "files/share/OrcaSlicer/profiles"
    profiles.mkdir(parents=True)
    probe_directories = []

    def runner(command, **kwargs):
        if "--show-location" in command:
            return subprocess.CompletedProcess(command, 0, str(location), "")
        probe_directories.append(Path(kwargs["cwd"]))
        return subprocess.CompletedProcess(command, 0, "OrcaSlicer-2.4.2:\n", "")

    installations = OrcaSlicer.discover_orca_installations(
        platform="linux",
        environ={"HOME": "/home/test"},
        runner=runner,
        which=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None,
    )

    assert {value.source for value in installations} == {
        "flatpak-user",
        "flatpak-system",
    }
    assert all(value.backend_id == "orcaslicer" for value in installations)
    assert all(value.version == "2.4.2" for value in installations)
    assert all(value.resource_dir == str(profiles) for value in installations)
    assert all("com.orcaslicer.OrcaSlicer" in value.gui_command for value in installations)
    assert all(value.tested_version == (2, 4, 2) for value in installations)
    assert installations[0].config_dir == str(
        Path("/home/test")
        / ".var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer"
    )
    assert probe_directories
    assert all(not path.exists() for path in probe_directories)


def test_orca_discovery_has_native_macos_and_windows_candidates() -> None:
    mac = OrcaSlicer._candidate_specs(
        "",
        platform="darwin",
        environ={"HOME": "/Users/test"},
    )
    windows = OrcaSlicer._candidate_specs(
        "",
        platform="win32",
        environ={
            "APPDATA": r"C:\Users\test\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\test\AppData\Local",
            "ProgramFiles": r"C:\Program Files",
        },
    )

    assert any("/Applications/OrcaSlicer.app/" in value.gui_command[0] for value in mac)
    assert any(value.gui_command[0].lower().endswith("orcaslicer.exe") for value in windows)
    assert any(value.gui_command[0].lower().endswith("orca-slicer.exe") for value in windows)


def test_windows_discovery_uses_metadata_without_launching_orca(
    tmp_path: Path,
) -> None:
    program_files = tmp_path / "Program Files"
    executable = program_files / "OrcaSlicer" / "orca-slicer.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    profiles = executable.parent / "resources" / "profiles"
    profiles.mkdir(parents=True)
    appdata = tmp_path / "Donn\u00fd User" / "AppData" / "Roaming"

    calls = []

    def runner(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    installations = OrcaSlicer.discover_orca_installations(
        platform="win32",
        environ={"ProgramFiles": str(program_files), "APPDATA": str(appdata)},
        runner=runner,
        which=lambda _name: None,
        windows_version_reader=lambda _path, _product: "2.4.2",
    )

    assert len(installations) == 1
    assert installations[0].version == "2.4.2"
    assert installations[0].gui_command == (str(executable),)
    assert installations[0].resource_dir == str(profiles)
    assert installations[0].config_dir == str(appdata / "OrcaSlicer")
    assert calls == []


def test_orca_project_command_keeps_explicit_placement_and_profiles(
    tmp_path: Path,
) -> None:
    installation = VibeCADPrint.SlicerInstallation(
        backend_id="orcaslicer",
        version="2.4.2",
        gui_command=("orca-slicer",),
        cli_command=("orca-slicer",),
        source="path",
        display_name="OrcaSlicer 2.4.2",
        tested_version=(2, 4, 2),
    )
    setup = VibeCADPrint.PrintSetup(
        "Printer",
        "Quality",
        ("PLA", "PETG"),
        auto_arrange=False,
        ensure_on_bed=True,
        object_filament_ids=(1, 2),
    )
    command = OrcaSlicer.build_prepare_project_command(
        installation,
        tmp_path / "input.3mf",
        tmp_path / "output.3mf",
        setup,
        tmp_path / "machine.json",
        tmp_path / "process.json",
        (tmp_path / "pla.json", tmp_path / "petg.json"),
        model_files=(tmp_path / "frame.stl", tmp_path / "rotor.stl"),
    )

    assert command[:3] == ("orca-slicer", "--debug", "2")
    assert command[command.index("--arrange") + 1] == "0"
    assert "--ensure-on-bed" in command
    assert command[command.index("--load-settings") + 1].endswith(
        "machine.json;" + str(tmp_path / "process.json")
    )
    assert command[command.index("--load-filaments") + 1].endswith(
        "pla.json;" + str(tmp_path / "petg.json")
    )
    assert command[command.index("--load-filament-ids") + 1] == "1,2"
    assert command[-2:] == (
        str(tmp_path / "frame.stl"),
        str(tmp_path / "rotor.stl"),
    )


def test_orca_backend_delegates_profile_project_work_without_bambu_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    expected = tmp_path / "prepared.3mf"

    def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(OrcaSlicer.BambuStudio, "prepare_bambu_project", prepare)
    backend = OrcaSlicer.OrcaSlicerBackend()
    installation = VibeCADPrint.SlicerInstallation(
        backend_id="orcaslicer",
        version="2.4.2",
        gui_command=("orca-slicer",),
        cli_command=("orca-slicer",),
        source="path",
        display_name="OrcaSlicer 2.4.2",
        tested_version=(2, 4, 2),
    )
    setup = VibeCADPrint.PrintSetup("Printer", "Quality", ("PLA",))

    actual = backend.prepare_project(
        installation,
        tmp_path / "source.3mf",
        expected,
        setup,
    )

    assert actual == expected
    assert backend.backend_id == "orcaslicer"
    assert backend.display_name == "OrcaSlicer"
    assert calls[0][0] == (
        installation,
        tmp_path / "source.3mf",
        expected,
        setup,
    )
