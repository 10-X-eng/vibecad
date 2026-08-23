# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import sys
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
        def prepare_project(
            self,
            actual_installation,
            source,
            destination,
            actual_setup,
        ):
            events.append(
                (
                    "prepare",
                    actual_installation,
                    source,
                    destination,
                    actual_setup,
                )
            )

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
    monkeypatch.setattr(commands, "_main_window", lambda: "main-window")
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
    monkeypatch.setitem(
        sys.modules,
        "PrintSetupDialog",
        SimpleNamespace(run_with_progress=lambda _parent, _label, operation: operation()),
    )

    assert commands.open_selected_in_prusaslicer(
        installation=installation,
        setup=setup,
    )
    assert events[0][0] == "export"
    assert events[1] == ("prepare", installation, handoff, handoff, setup)
    assert events[2] == ("launch", installation, handoff, setup)
    assert events[3][0] == "status"


def test_active_selection_deduplicates_a_body_and_its_picked_subelement(
    monkeypatch,
) -> None:
    commands = PrintCommandLoader.command_module()
    document = SimpleNamespace(Name="FanDocument")

    class Shape:
        def isNull(self):
            return False

    frame = SimpleNamespace(
        Name="Body",
        Label="120 mm Fan Frame",
        TypeId="PartDesign::Body",
        Document=document,
        Shape=Shape(),
    )
    rotor = SimpleNamespace(
        Name="Body001",
        Label="120 mm Fan Rotor",
        TypeId="PartDesign::Body",
        Document=document,
        Shape=Shape(),
    )
    frame_result = SimpleNamespace(
        Name="BodyResult",
        Label="BodyResult",
        TypeId="PartDesign::DesignBodyPublication",
        Document=document,
        Shape=Shape(),
        getParentGeoFeatureGroup=lambda: frame,
    )
    selection = SimpleNamespace(
        getSelectionEx=lambda: (
            SimpleNamespace(Object=frame, SubElementNames=()),
            SimpleNamespace(Object=rotor, SubElementNames=()),
            SimpleNamespace(Object=frame_result, SubElementNames=("Edge1",)),
        )
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", SimpleNamespace(ActiveDocument=document))
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace(Selection=selection))

    actual_document, objects = commands._active_selection()

    assert actual_document is document
    assert objects == (frame, rotor)


def test_explicit_panel_choices_override_the_live_selection(
    monkeypatch,
    tmp_path,
) -> None:
    commands = PrintCommandLoader.command_module()
    installation = _installation()
    setup = _setup()
    document = SimpleNamespace(Label="Fan")

    class Shape:
        def isNull(self):
            return False

    frame = SimpleNamespace(Name="Body", Document=document, Shape=Shape())
    rotor = SimpleNamespace(Name="Body001", Document=document, Shape=Shape())
    handoff = tmp_path / "fan.3mf"
    exported = []

    class Backend:
        def prepare_project(self, *_args):
            return handoff

        def launch(self, *_args):
            return VibeCADPrint.LaunchResult(("prusa-slicer",), 42)

    monkeypatch.setattr(commands.VibeCADPrint, "PrusaSlicerBackend", Backend)
    monkeypatch.setattr(
        commands,
        "_active_selection",
        lambda: (_ for _ in ()).throw(
            AssertionError("Panel choices unexpectedly reread the live selection")
        ),
    )
    monkeypatch.setattr(
        commands,
        "_handoff_destination",
        lambda _document, _objects: (handoff, False),
    )
    monkeypatch.setattr(
        commands.VibeCADPrint,
        "export_selection_3mf",
        lambda objects, _destination: exported.append(objects),
    )
    monkeypatch.setattr(commands, "_main_window", lambda: None)
    monkeypatch.setattr(commands, "_status", lambda _message: None)
    monkeypatch.setitem(
        sys.modules,
        "PrintSetupDialog",
        SimpleNamespace(run_with_progress=lambda _parent, _label, operation: operation()),
    )

    assert commands.open_selected_in_prusaslicer(
        installation=installation,
        setup=setup,
        selection=(document, (rotor,)),
    )
    assert exported == [(rotor,)]
    assert frame not in exported[0]
