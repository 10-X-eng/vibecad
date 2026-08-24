# SPDX-License-Identifier: LGPL-2.1-or-later

"""Offscreen integration check for the persistent 3D Print panel."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace


TESTS = Path(__file__).resolve().parent
MODULE = TESTS.parent
REPO = MODULE.parents[2]
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from PySide import QtCore, QtWidgets  # noqa: E402

import PrintPanel  # noqa: E402
import PrintPreferences  # noqa: E402
import VibeCADPrint  # noqa: E402


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    styles = REPO / "src" / "Gui" / "Stylesheets"
    app.setStyleSheet(
        (styles / "defaults.qss").read_text(encoding="utf-8")
        + "\n"
        + (styles / "VibeDark.qss").read_text(encoding="utf-8")
    )

    setup = VibeCADPrint.PrintSetup(
        printer_profile="Prusa CORE One 0.4 nozzle",
        print_profile="0.20mm STRUCTURAL @COREONE 0.4",
        material_profiles=("Prusament PLA @COREONE",),
        auto_arrange=True,
        ensure_on_bed=True,
    )
    storage = PrintPreferences.HandoffStorage("folder", "/tmp/print-projects")
    saved: dict[str, object] = {"setup": setup, "storage": storage}
    PrintPanel.PrintPreferences.load_confirmed_setup = (
        lambda **_kwargs: saved["setup"]
    )
    PrintPanel.PrintPreferences.load_handoff_storage = lambda: saved["storage"]
    PrintPanel.PrintPreferences.executable_override = lambda **_kwargs: ""
    PrintPanel.PrintPreferences.save_confirmed_setup = (
        lambda value, **_kwargs: saved.__setitem__("setup", value)
    )
    PrintPanel.PrintPreferences.save_handoff_storage = lambda value: saved.__setitem__(
        "storage", value
    )
    PrintPanel._active_print_selection = lambda: (
        SimpleNamespace(Name="FanDocument"),
        (
            SimpleNamespace(Name="Body", Label="120 mm Fan Frame"),
            SimpleNamespace(Name="Body001", Label="120 mm Fan Rotor"),
        ),
    )

    installation = VibeCADPrint.SlicerInstallation(
        backend_id="prusaslicer",
        version="2.9.6",
        gui_command=("prusa-slicer",),
        cli_command=("prusa-slicer",),
        source="path",
        display_name="PrusaSlicer 2.9.6",
    )
    printer = VibeCADPrint.PrinterProfile(
        name=setup.printer_profile,
        extruders=1,
        bed=VibeCADPrint.BedInfo(
            kind="Rectangle",
            width=250,
            height=220,
            max_print_height=270,
        ),
    )
    material = VibeCADPrint.MaterialProfile(name=setup.material_profiles[0])
    profile = VibeCADPrint.PrintProfile(
        name=setup.print_profile,
        materials=(material,),
    )
    catalog = VibeCADPrint.ProfileCatalog(
        printer_profile=printer.name,
        print_profiles=(profile,),
    )

    class Backend:
        backend_id = "prusaslicer"
        display_name = "PrusaSlicer"

        def discover(self, _override=""):
            return (installation,)

        def query_printers(self, _installation):
            return (printer,)

        def query_profiles(self, _installation, _printer):
            return catalog

    panel = PrintPanel.PrintPanelWidget()
    panel.backend = Backend()

    def immediate(_message, operation, callback):
        callback(operation())

    panel._start_job = immediate
    panel.resize(390, 720)
    panel.show()
    panel.refresh()
    app.processEvents()

    assert panel.printer_combo.currentData() == printer
    assert panel.print_combo.currentData() == profile
    assert len(panel.material_combos) == 1
    assert panel.material_combos[0].currentData() == material.name
    assert panel.output_location.text() == storage.directory
    assert not panel.output_location.isVisible()
    assert panel.selection_group.title() == "Objects to be sent"
    assert [choice.text() for choice in panel.object_checkboxes] == [
        "120 mm Fan Frame",
        "120 mm Fan Rotor",
    ]
    assert all(choice.isChecked() for choice in panel.object_checkboxes)
    panel.object_checkboxes[0].setChecked(False)
    app.processEvents()
    _document, chosen = panel._checked_print_selection()
    assert [obj.Label for obj in chosen] == ["120 mm Fan Rotor"]
    assert "1 of 2 objects will be sent" in panel.selection_summary.text()
    panel.object_checkboxes[0].setChecked(True)
    assert panel.print_button.text() == "Print"
    assert panel.setup_button.text().startswith("Setup")
    assert panel.export_button.text() == "Export 3MF…"
    assert not panel.apply_button.isVisible()
    assert panel._save()
    assert saved == {"setup": setup, "storage": storage}
    scroll = panel.findChild(QtWidgets.QScrollArea, "VibeCADPrintPanelScroll")
    assert scroll is not None
    assert scroll.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
    assert scroll.horizontalScrollBar().maximum() == 0

    panel.auto_arrange.setChecked(False)
    app.processEvents()
    assert saved["setup"].auto_arrange is False

    panel.close()

    remembered = PrintPanel.PrintPanelWidget()
    remembered.backend = Backend()
    remembered._start_job = immediate
    remembered.resize(320, 720)
    remembered.show()
    remembered.refresh()
    app.processEvents()

    assert remembered.printer_combo.currentData() == printer
    assert remembered.print_combo.currentData() == profile
    assert remembered.material_combos[0].currentData() == material.name
    assert not remembered.auto_arrange.isChecked()
    assert [choice.text() for choice in remembered.object_checkboxes] == [
        "120 mm Fan Frame",
        "120 mm Fan Rotor",
    ]
    screenshot = os.environ.get("VIBECAD_PRINT_PANEL_SCREENSHOT")
    if screenshot:
        assert remembered.grab().save(screenshot)
    remembered.close()


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    try:
        main()
        print("VIBECAD_PRINT_PANEL_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
