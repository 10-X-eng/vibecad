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


def test_windows_discovery_uses_file_version_when_gui_help_is_silent(
    tmp_path: Path,
) -> None:
    program_files = tmp_path / "Program Files"
    executable = program_files / "Bambu Studio" / "bambu-studio.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    profiles = executable.parent / "resources" / "profiles"
    profiles.mkdir(parents=True)
    appdata = tmp_path / "Donn\u00fd User" / "AppData" / "Roaming"

    calls = []

    def runner(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    installations = BambuStudio.discover_bambu_installations(
        platform="win32",
        environ={"ProgramFiles": str(program_files), "APPDATA": str(appdata)},
        runner=runner,
        which=lambda _name: None,
        windows_version_reader=lambda _path, _product: "2.7.1.62",
    )

    assert len(installations) == 1
    assert installations[0].version == "2.7.1.62"
    assert installations[0].gui_command == (str(executable),)
    assert installations[0].resource_dir == str(profiles)
    assert installations[0].config_dir == str(appdata / "BambuStudio")
    assert calls
    no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    assert all(call["creationflags"] & no_window for call in calls)


def test_windows_launch_has_no_console_and_preserves_unicode_path(tmp_path: Path) -> None:
    handoff = tmp_path / "M\u00f8del With Spaces.3mf"
    calls = []

    class Process:
        pid = 42

    def popen(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return Process()

    result = BambuStudio.launch_bambu_studio(
        _installation(tmp_path),
        handoff,
        popen=popen,
        platform="win32",
    )

    assert result.command[-1] == str(handoff)
    assert result.process_id == 42
    assert calls[0][0][-1] == str(handoff)
    creationflags = calls[0][1]["creationflags"]
    no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    detached = int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
    assert creationflags & no_window
    assert not creationflags & detached
    assert calls[0][1]["close_fds"] is True
    assert calls[0][1]["start_new_session"] is False


def _source_3mf(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "3D/3dmodel.model",
            '<model><resources><object id="1" name="Frame"/>'
            '<object id="2" name="Rotor"/></resources></model>',
        )


def _prepared_3mf(
    path: Path,
    names: tuple[str, ...],
    *,
    filament_ids: tuple[int, ...] | None = None,
    project_settings: dict | None = None,
) -> None:
    filament_ids = filament_ids or (1,) * len(names)
    objects = "".join(
        f'<object id="{index}"><metadata key="name" value="{name}"/>'
        f'<metadata key="extruder" value="{filament_ids[index - 1]}"/></object>'
        for index, name in enumerate(names, start=1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/model_settings.config", f"<config>{objects}</config>")
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                project_settings
                or {
                    "filament_settings_id": [
                        f"Filament {index}"
                        for index in range(1, max(filament_ids, default=1) + 1)
                    ]
                }
            ),
        )


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


def test_bambu_catalog_includes_compatible_user_filament_without_instantiation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    config = tmp_path / "BambuStudio"
    _profile_store(root)
    user_filament = config / "user" / "123456" / "filament" / "base"
    user_filament.mkdir(parents=True)
    (user_filament / "Custom PETG.json").write_text(
        json.dumps(
            {
                "name": "Custom PETG",
                "from": "User",
                "inherits": "Generic PLA @TEST",
                "compatible_printers": ["Test Printer 0.4 nozzle"],
                "filament_settings_id": ["Custom PETG"],
            }
        ),
        encoding="utf-8",
    )
    (user_filament / "Other Printer PETG.json").write_text(
        json.dumps(
            {
                "name": "Other Printer PETG",
                "from": "User",
                "inherits": "Generic PLA @TEST",
                "compatible_printers": ["Another Printer"],
                "filament_settings_id": ["Other Printer PETG"],
            }
        ),
        encoding="utf-8",
    )
    cached_system_filament = config / "system" / "BBL" / "filament" / "base"
    cached_system_filament.mkdir(parents=True)
    (cached_system_filament / "Cached system base.json").write_text(
        json.dumps(
            {
                "name": "Cached system base",
                "from": "system",
                "inherits": "Generic PLA @TEST",
                "compatible_printers": ["Test Printer 0.4 nozzle"],
            }
        ),
        encoding="utf-8",
    )
    installation = VibeCADPrint.SlicerInstallation(
        backend_id="bambustudio",
        version="2.8.2.61",
        gui_command=("bambu-studio",),
        cli_command=("bambu-studio",),
        source="path",
        display_name="Bambu Studio",
        config_dir=str(config),
        resource_dir=str(root),
        tested_version=(2, 8, 2),
    )

    catalog = BambuStudio.query_compatible_profiles(
        installation,
        "Test Printer 0.4 nozzle",
    )

    assert [
        material.name for material in catalog.print_profiles[0].materials
    ] == ["Custom PETG", "Generic PLA @TEST"]
    assert catalog.print_profiles[0].materials[0].is_user is True


def test_profile_store_uses_its_index_for_exact_name_queries(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    _profile_store(root)
    store = BambuStudio._load_profile_store(_installation(root))

    class NoFullScan:
        def __iter__(self):
            raise AssertionError("exact profile lookup scanned the complete store")

    object.__setattr__(store, "records", NoFullScan())

    records = store.records_for("machine", "Test Printer 0.4 nozzle")
    assert len(records) == 1
    assert records[0].name == "Test Printer 0.4 nozzle"


def test_backend_caches_discovery_and_all_profile_data_until_invalidated(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "profiles"
    _profile_store(root)
    installation = _installation(root)
    calls = {"discover": 0, "store": 0}
    real_load = BambuStudio._load_profile_store

    def discover(_override=""):
        calls["discover"] += 1
        return (installation,)

    def load(actual_installation):
        calls["store"] += 1
        return real_load(actual_installation)

    monkeypatch.setattr(BambuStudio, "discover_bambu_installations", discover)
    monkeypatch.setattr(BambuStudio, "_load_profile_store", load)
    backend = BambuStudio.BambuStudioBackend()

    assert backend.discover() == (installation,)
    assert backend.discover() == (installation,)
    printers = backend.query_printers(installation)
    assert backend.query_printers(installation) is printers
    catalog = backend.query_profiles(installation, printers[0].name)
    assert backend.query_profiles(installation, printers[0].name) is catalog
    assert calls == {"discover": 1, "store": 1}

    backend.invalidate_cache()
    backend.discover()
    backend.query_printers(installation)
    assert calls == {"discover": 2, "store": 2}


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
    working_directories = []

    def runner(command, **kwargs):
        commands.append(tuple(command))
        working_directory = Path(kwargs["cwd"])
        working_directories.append(working_directory)
        assert working_directory.parent == destination.parent
        assert working_directory.name.startswith(".vibecad-slicer-")
        settings = command[command.index("--load-settings") + 1].split(";")
        machine = json.loads(Path(settings[0]).read_text(encoding="utf-8"))
        process = json.loads(Path(settings[1]).read_text(encoding="utf-8"))
        filament_path = Path(command[command.index("--load-filaments") + 1])
        filament = json.loads(filament_path.read_text(encoding="utf-8"))
        assert machine["printable_area"][-1] == "0x256"
        assert machine["machine_start_gcode"] == "G28"
        assert process["layer_height"] == "0.2"
        assert filament["filament_type"] == ["PLA"]
        filament_ids = command[command.index("--load-filament-ids") + 1]
        assert filament_ids == "1,1"
        output = Path(command[command.index("--export-3mf") + 1])
        _prepared_3mf(output, ("Frame", "Rotor"), filament_ids=(1, 1))
        return subprocess.CompletedProcess(command, 0, "prepared", "")

    model_files = (tmp_path / "frame.stl", tmp_path / "rotor.stl")
    for model_file in model_files:
        model_file.write_bytes(b"solid model")
    actual = BambuStudio.prepare_bambu_project(
        _installation(root),
        source,
        destination,
        setup,
        model_files=model_files,
        runner=runner,
    )

    assert actual == destination
    arrange_index = commands[0].index("--arrange")
    assert commands[0][arrange_index + 1] == arrange_value
    assert "--ensure-on-bed" in commands[0]
    assert destination.is_file()
    assert all(not path.exists() for path in working_directories)
    assert not list(tmp_path.glob("*.partial.3mf"))
    assert not list(tmp_path.glob("*.profile.json"))


def test_multi_filament_command_assigns_a_filament_to_every_object(
    tmp_path: Path,
) -> None:
    installation = _installation(tmp_path / "profiles")
    setup = VibeCADPrint.PrintSetup(
        "Dual Printer",
        "Quality",
        ("PLA", "PETG"),
        object_filament_ids=(2, 1, 2),
    )

    command = BambuStudio.build_prepare_project_command(
        installation,
        tmp_path / "input.3mf",
        tmp_path / "output.3mf",
        setup,
        tmp_path / "machine.json",
        tmp_path / "process.json",
        (tmp_path / "pla.json", tmp_path / "petg.json"),
        model_files=(
            tmp_path / "frame.stl",
            tmp_path / "rotor.stl",
            tmp_path / "guard.stl",
        ),
    )

    assert command[command.index("--load-filament-ids") + 1] == "2,1,2"
    assert command[-3:] == (
        str(tmp_path / "frame.stl"),
        str(tmp_path / "rotor.stl"),
        str(tmp_path / "guard.stl"),
    )
    assert str(tmp_path / "input.3mf") not in command


def test_multi_filament_command_preserves_legacy_3mf_input_without_models(
    tmp_path: Path,
) -> None:
    setup = VibeCADPrint.PrintSetup(
        "Dual Printer",
        "Quality",
        ("PLA", "PETG"),
        object_filament_ids=(1, 2),
    )

    command = BambuStudio.build_prepare_project_command(
        _installation(tmp_path / "profiles"),
        tmp_path / "input.3mf",
        tmp_path / "output.3mf",
        setup,
        tmp_path / "machine.json",
        tmp_path / "process.json",
        (tmp_path / "pla.json", tmp_path / "petg.json"),
    )

    assert command[-1] == str(tmp_path / "input.3mf")
    assert "--load-filament-ids" not in command


def test_project_metadata_normalization_preserves_exact_assignments(tmp_path: Path) -> None:
    project = tmp_path / "prepared.3mf"
    _prepared_3mf(
        project,
        ("frame.stl", "rotor.stl"),
        filament_ids=(1, 2),
        project_settings={
            "filament_settings_id": ["ABS-GF", "PC"],
            "filament_colour": ["#F2754E"],
            "filament_map": ["1"],
            "filament_is_support": ["0"],
            "nozzle_diameter": ["0.4", "0.4"],
            "default_nozzle_volume_type": ["Standard", "Standard"],
            "nozzle_volume_type": ["Standard"],
        },
    )
    setup = VibeCADPrint.PrintSetup(
        "Bambu Lab H2D 0.4 nozzle",
        "Quality",
        ("ABS-GF", "PC"),
        object_filament_ids=(1, 2),
    )

    BambuStudio._normalize_project_metadata(
        project,
        source_names=("Fan Frame", "Fan Rotor"),
        setup=setup,
        filament_keys={"filament_is_support"},
    )

    with zipfile.ZipFile(project) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        model = BambuStudio.ET.fromstring(
            archive.read("Metadata/model_settings.config")
        )
    objects = model.findall("./{*}object")
    assert [
        obj.find('./{*}metadata[@key="name"]').attrib["value"] for obj in objects
    ] == ["Fan Frame", "Fan Rotor"]
    assert [
        obj.find('./{*}metadata[@key="extruder"]').attrib["value"]
        for obj in objects
    ] == ["1", "2"]
    assert settings["filament_colour"] == ["#F2754E", "#F2754E"]
    assert settings["filament_map"] == ["1", "1"]
    assert settings["filament_is_support"] == ["0", "0"]
    assert settings["nozzle_volume_type"] == ["Standard", "Standard"]


def test_project_setup_keeps_only_used_filaments_and_remaps_ids() -> None:
    setup = VibeCADPrint.PrintSetup(
        "Dual Printer",
        "Quality",
        ("PLA", "PETG", "Support"),
        object_filament_ids=(3, 1, 3),
    )

    project = BambuStudio._project_setup(setup, object_count=3)

    assert project.material_profiles == ("PLA", "Support")
    assert project.object_filament_ids == (2, 1, 2)


@pytest.mark.parametrize("ids", [(), (1,), (1, 3)])
def test_multi_filament_project_requires_a_valid_id_for_every_object(ids) -> None:
    setup = VibeCADPrint.PrintSetup(
        "Dual Printer",
        "Quality",
        ("PLA", "PETG"),
        object_filament_ids=ids,
    )

    with pytest.raises(VibeCADPrint.SlicerError, match="filament.*each object"):
        BambuStudio._project_setup(setup, object_count=2)


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
