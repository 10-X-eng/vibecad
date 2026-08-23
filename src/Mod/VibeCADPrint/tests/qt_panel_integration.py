# SPDX-License-Identifier: LGPL-2.1-or-later

"""Offscreen integration check for the persistent 3D Print panel."""

from __future__ import annotations

from pathlib import Path
import sys


TESTS = Path(__file__).resolve().parent
MODULE = TESTS.parent
REPO = MODULE.parents[2]
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from PySide import QtWidgets  # noqa: E402

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
    saved: dict[str, object] = {}
    PrintPanel.PrintPreferences.load_confirmed_setup = lambda: setup
    PrintPanel.PrintPreferences.load_handoff_storage = lambda: storage
    PrintPanel.PrintPreferences.executable_override = lambda: ""
    PrintPanel.PrintPreferences.save_confirmed_setup = lambda value: saved.setdefault(
        "setup", value
    )
    PrintPanel.PrintPreferences.save_handoff_storage = lambda value: saved.setdefault(
        "storage", value
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
    assert panel.folder_storage.isChecked()
    assert panel.folder_edit.text() == storage.directory
    assert panel._save()
    assert saved == {"setup": setup, "storage": storage}
    assert panel.findChild(QtWidgets.QScrollArea, "VibeCADPrintPanelScroll") is not None

    panel.close()


if __name__ == "__main__":
    main()
