# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import VibeCADPrint


PRINTERS_JSON = {
    "printer_models": [
        {
            "id": "MK4S",
            "name": "Original Prusa MK4S",
            "technology": "FFF",
            "vendor_name": "Prusa Research",
            "vendor_id": "PRUSA",
            "variants": [
                {
                    "name": "0.4",
                    "printer_profiles": [
                        {
                            "name": "Original Prusa MK4S 0.4 nozzle",
                            "extruders_cnt": "1",
                            "bed": {
                                "type": "Rectangle",
                                "width": "250",
                                "height": "210",
                                "origin": "[0, 0]",
                                "max_print_height": "220",
                            },
                        }
                    ],
                    "user_printer_profiles": [
                        {
                            "name": "My MK4S",
                            "extruders_cnt": "1",
                            "bed": {
                                "type": "Rectangle",
                                "width": "250",
                                "height": "210",
                                "origin": "[0, 0]",
                                "max_print_height": "220",
                            },
                        }
                    ],
                }
            ],
        },
        {
            "id": "XL",
            "name": "Original Prusa XL",
            "technology": "FFF",
            "vendor_name": "Prusa Research",
            "vendor_id": "PRUSA",
            "variants": [
                {
                    "name": "5T 0.4",
                    "printer_profiles": [
                        {
                            "name": "Original Prusa XL - 5T 0.4 nozzle",
                            "extruders_cnt": 5,
                            "bed": {
                                "type": "Rectangle",
                                "width": 360,
                                "height": 360,
                                "origin": "[0, 0]",
                                "max_print_height": 360,
                            },
                        }
                    ],
                }
            ],
        },
    ]
}


PROFILES_JSON = {
    "printer_profile": "Original Prusa XL - 5T 0.4 nozzle",
    "print_profiles": [
        {
            "name": "0.20mm SPEED @XL 0.4",
            "filament_profiles": ["Generic PLA @XL", "Generic PETG @XL"],
            "user_filament_profiles": ["My PLA @XL"],
        },
        {
            "name": "0.15mm QUALITY @XL 0.4",
            "filament_profiles": ["Generic PLA @XL"],
        },
    ],
    "user_print_profiles": [
        {
            "name": "My Fast XL",
            "filament_profiles": ["Generic PLA @XL"],
        }
    ],
}


def _installation(*, version: str = "2.9.6") -> VibeCADPrint.SlicerInstallation:
    return VibeCADPrint.SlicerInstallation(
        backend_id="prusaslicer",
        version=version,
        gui_command=("prusa-slicer",),
        cli_command=("prusa-slicer",),
        source="path",
        display_name=f"PrusaSlicer {version}",
    )


def test_parse_printer_models_flattens_system_and_user_profiles() -> None:
    profiles = VibeCADPrint.parse_printer_models(PRINTERS_JSON)

    assert [profile.name for profile in profiles] == [
        "Original Prusa MK4S 0.4 nozzle",
        "My MK4S",
        "Original Prusa XL - 5T 0.4 nozzle",
    ]
    custom = profiles[1]
    assert custom.is_user is True
    assert custom.model_name == "Original Prusa MK4S"
    assert custom.variant_name == "0.4"
    assert custom.extruders == 1
    assert custom.bed.width == 250.0
    assert custom.bed.origin == (0.0, 0.0)
    assert profiles[2].extruders == 5


def test_parse_compatible_profiles_preserves_per_print_material_compatibility() -> None:
    catalog = VibeCADPrint.parse_compatible_profiles(PROFILES_JSON)

    assert catalog.printer_profile == "Original Prusa XL - 5T 0.4 nozzle"
    assert [profile.name for profile in catalog.print_profiles] == [
        "0.20mm SPEED @XL 0.4",
        "0.15mm QUALITY @XL 0.4",
        "My Fast XL",
    ]
    speed = catalog.print_profiles[0]
    assert [(item.name, item.is_user) for item in speed.materials] == [
        ("Generic PLA @XL", False),
        ("Generic PETG @XL", False),
        ("My PLA @XL", True),
    ]
    assert catalog.print_profiles[2].is_user is True


def test_valid_query_json_wins_over_nonzero_process_status(tmp_path: Path) -> None:
    def runner(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps(PRINTERS_JSON), encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, "", "query action returned 1")

    result = VibeCADPrint.run_json_query(
        _installation(),
        ("--query-printer-models",),
        runner=runner,
        temporary_directory=tmp_path,
    )

    assert result == PRINTERS_JSON


def test_flatpak_query_reads_stdout_across_private_tmp_boundary(
    tmp_path: Path,
) -> None:
    installation = VibeCADPrint.SlicerInstallation(
        backend_id="prusaslicer",
        version="2.9.6",
        gui_command=("flatpak", "run", "com.prusa3d.PrusaSlicer"),
        cli_command=(
            "flatpak",
            "run",
            "--command=/app/bin/prusa-slicer",
            "com.prusa3d.PrusaSlicer",
        ),
        source="flatpak-user",
        display_name="PrusaSlicer (Flatpak) 2.9.6",
    )

    def runner(command, **kwargs):
        assert "--output" not in command
        return subprocess.CompletedProcess(command, 1, json.dumps(PRINTERS_JSON), "")

    result = VibeCADPrint.run_json_query(
        installation,
        ("--query-printer-models",),
        runner=runner,
        temporary_directory=tmp_path,
    )

    assert result == PRINTERS_JSON


def test_invalid_query_reports_stdout_stderr_and_status(tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, "bad out", "bad err")

    with pytest.raises(VibeCADPrint.SlicerQueryError) as error:
        VibeCADPrint.run_json_query(
            _installation(),
            ("--query-printer-models",),
            runner=runner,
            temporary_directory=tmp_path,
        )

    assert "status 2" in str(error.value)
    assert "bad out" in str(error.value)
    assert "bad err" in str(error.value)


@pytest.mark.parametrize(
    ("auto_arrange", "ensure_on_bed", "expected", "unexpected"),
    [
        (True, True, "--ensure-on-bed", "--dont-arrange"),
        (True, False, "--no-ensure-on-bed", "--dont-arrange"),
        (False, True, "--dont-arrange", "--no-ensure-on-bed"),
        (False, False, "--dont-arrange", "--ensure-on-bed"),
    ],
)
def test_launch_command_maps_explicit_placement_choices(
    auto_arrange: bool,
    ensure_on_bed: bool,
    expected: str,
    unexpected: str,
) -> None:
    setup = VibeCADPrint.PrintSetup(
        printer_profile="Original Prusa XL - 5T 0.4 nozzle",
        print_profile="0.20mm SPEED @XL 0.4",
        material_profiles=("Generic PLA @XL",) * 5,
        auto_arrange=auto_arrange,
        ensure_on_bed=ensure_on_bed,
    )

    command = VibeCADPrint.build_launch_command(
        _installation(), Path("/tmp/Plate With Spaces.3mf"), setup
    )

    assert expected in command
    assert unexpected not in command
    assert command[-1] == "/tmp/Plate With Spaces.3mf"
    material_index = command.index("--material-profile")
    assert command[material_index + 1] == ";".join(("Generic PLA @XL",) * 5)


def test_basic_launch_does_not_invent_profiles() -> None:
    command = VibeCADPrint.build_launch_command(
        _installation(version="2.7.2"), Path("/tmp/part.3mf"), None
    )

    assert command == ("prusa-slicer", "/tmp/part.3mf")


def test_validate_setup_requires_exact_compatible_names_and_each_extruder() -> None:
    printer = VibeCADPrint.parse_printer_models(PRINTERS_JSON)[2]
    catalog = VibeCADPrint.parse_compatible_profiles(PROFILES_JSON)
    setup = VibeCADPrint.PrintSetup(
        printer_profile=printer.name,
        print_profile="0.20mm SPEED @XL 0.4",
        material_profiles=("Generic PLA @XL",) * 5,
    )

    assert VibeCADPrint.validate_setup(setup, printer, catalog) == ()
    missing = VibeCADPrint.PrintSetup(
        printer_profile=printer.name,
        print_profile=setup.print_profile,
        material_profiles=("Generic PLA @XL",) * 4,
    )
    assert VibeCADPrint.validate_setup(missing, printer, catalog) == (
        "Select one material profile for each of the printer's 5 extruders.",
    )
    incompatible = VibeCADPrint.PrintSetup(
        printer_profile=printer.name,
        print_profile=setup.print_profile,
        material_profiles=("Generic ABS @XL",) * 5,
    )
    assert (
        "Generic ABS @XL"
        in VibeCADPrint.validate_setup(incompatible, printer, catalog)[0]
    )


def test_default_candidates_cover_all_supported_platforms() -> None:
    windows = VibeCADPrint.default_candidate_specs(
        platform="win32",
        environ={"ProgramFiles": r"C:\Program Files"},
    )
    macos = VibeCADPrint.default_candidate_specs(platform="darwin", environ={})
    linux = VibeCADPrint.default_candidate_specs(platform="linux", environ={})

    assert any(
        spec.gui_command[-1].endswith(r"Prusa3D\PrusaSlicer\prusa-slicer.exe")
        for spec in windows
    )
    assert any(
        "PrusaSlicer.app/Contents/MacOS/PrusaSlicer" in spec.gui_command[-1]
        for spec in macos
    )
    assert any(spec.gui_command == ("prusa-slicer",) for spec in linux)
    assert any(
        spec.gui_command[-1] == "com.prusa3d.PrusaSlicer"
        and "--command=/app/bin/prusa-slicer" in spec.cli_command
        for spec in linux
    )


def test_preferred_installation_honors_explicit_then_uses_newest() -> None:
    old = _installation(version="2.7.2")
    current = _installation(version="2.9.6")
    newer = _installation(version="2.10.0")

    assert VibeCADPrint.preferred_installation([old, newer, current]) is newer
    assert (
        VibeCADPrint.preferred_installation(
            [old, newer, current], explicit_gui_command=old.gui_command
        )
        is old
    )
