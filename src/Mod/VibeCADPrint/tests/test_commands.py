# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import PrintCommandLoader
import VibeCADPrint


def _installation() -> VibeCADPrint.SlicerInstallation:
    return VibeCADPrint.SlicerInstallation(
        backend_id="prusaslicer",
        version="2.9.6",
        gui_command=("prusa-slicer",),
        cli_command=("prusa-slicer",),
        source="path",
        display_name="PrusaSlicer 2.9.6",
    )


def _setup() -> VibeCADPrint.PrintSetup:
    return VibeCADPrint.PrintSetup(
        printer_profile="Printer",
        print_profile="Quality",
        material_profiles=("Material",),
    )


def test_panel_validated_print_does_not_reopen_or_revalidate_setup(
    monkeypatch,
    tmp_path,
) -> None:
    commands = PrintCommandLoader.command_module()
    installation = _installation()
    setup = _setup()
    handoff = tmp_path / "part.3mf"
    events = []

    class Backend:
        def launch(self, actual_installation, actual_handoff, actual_setup):
            events.append(
                ("launch", actual_installation, actual_handoff, actual_setup)
            )
            return VibeCADPrint.LaunchResult(("prusa-slicer",), 42)

    monkeypatch.setattr(commands.VibeCADPrint, "PrusaSlicerBackend", Backend)
    monkeypatch.setattr(
        commands,
        "_active_selection",
        lambda: (SimpleNamespace(Label="Part"), (SimpleNamespace(Name="Body"),)),
    )
    monkeypatch.setattr(
        commands,
        "_handoff_destination",
        lambda _document, _objects: (handoff, False),
    )
    monkeypatch.setattr(
        commands.VibeCADPrint,
        "export_selection_3mf",
        lambda objects, destination: events.append(("export", objects, destination)),
    )
    monkeypatch.setattr(
        commands,
        "_resolve_handoff_configuration",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Print unexpectedly returned to Setup")
        ),
    )
    monkeypatch.setattr(commands, "_status", lambda message: events.append(("status", message)))

    assert commands.open_selected_in_prusaslicer(
        installation=installation,
        setup=setup,
    )
    assert events[0][0] == "export"
    assert events[1] == ("launch", installation, handoff, setup)
    assert events[2][0] == "status"
