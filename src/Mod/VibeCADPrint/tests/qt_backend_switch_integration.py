# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live contract for switching exact-profile slicer backends in the panel."""

from __future__ import annotations

from pathlib import Path
import sys
import traceback
from types import SimpleNamespace


TESTS = Path(__file__).resolve().parent
MODULE = TESTS.parent
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from PySide import QtCore, QtWidgets  # noqa: E402

import PrintPanel  # noqa: E402
import VibeCADPrint  # noqa: E402


def _setup(prefix: str) -> VibeCADPrint.PrintSetup:
    return VibeCADPrint.PrintSetup(
        f"{prefix} Printer",
        f"{prefix} Quality",
        (f"{prefix} Material",),
    )


def _backend_data(backend_id: str):
    prefix = "Prusa" if backend_id == "prusaslicer" else "Bambu"
    setup = _setup(prefix)
    installation = VibeCADPrint.SlicerInstallation(
        backend_id=backend_id,
        version="2.9.6" if backend_id == "prusaslicer" else "2.8.2.61",
        gui_command=(backend_id,),
        cli_command=(backend_id,),
        source="test",
        display_name=f"{prefix} Installed",
        tested_version=(2, 9, 6) if backend_id == "prusaslicer" else (2, 8, 2),
    )
    material = VibeCADPrint.MaterialProfile(setup.material_profiles[0])
    quality = VibeCADPrint.PrintProfile(setup.print_profile, (material,))
    printer = VibeCADPrint.PrinterProfile(
        setup.printer_profile,
        extruders=1,
        bed=VibeCADPrint.BedInfo(width=250, height=250, max_print_height=250),
    )
    catalog = VibeCADPrint.ProfileCatalog(printer.name, (quality,))
    return setup, installation, printer, catalog


class _Backend:
    def __init__(self, backend_id: str) -> None:
        self.backend_id = backend_id
        self.display_name = "PrusaSlicer" if backend_id == "prusaslicer" else "Bambu Studio"
        self.setup, self.installation, self.printer, self.catalog = _backend_data(
            backend_id
        )

    def discover(self, _override=""):
        return (self.installation,)

    def query_printers(self, _installation):
        return (self.printer,)

    def query_profiles(self, _installation, _printer):
        return self.catalog


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    panel = None
    try:
        backends = {
            backend_id: _Backend(backend_id)
            for backend_id in ("prusaslicer", "bambustudio")
        }
        state = {
            "active": "prusaslicer",
            "setups": {
                backend_id: backend.setup for backend_id, backend in backends.items()
            },
        }
        PrintPanel._backend_for_id = lambda backend_id: backends[backend_id]
        PrintPanel.PrintPreferences.active_backend = lambda: state["active"]
        PrintPanel.PrintPreferences.set_active_backend = lambda value: state.__setitem__(
            "active", value
        )
        PrintPanel.PrintPreferences.load_confirmed_setup = (
            lambda *, backend_id="prusaslicer": state["setups"].get(backend_id)
        )
        PrintPanel.PrintPreferences.save_confirmed_setup = (
            lambda setup, *, backend_id="prusaslicer": state["setups"].__setitem__(
                backend_id, setup
            )
        )
        PrintPanel.PrintPreferences.executable_override = (
            lambda *, backend_id="prusaslicer": ""
        )
        PrintPanel.PrintPreferences.load_handoff_storage = lambda: SimpleNamespace(
            mode="managed", directory=""
        )
        PrintPanel._active_print_selection = lambda: (
            SimpleNamespace(Name="Fan"),
            (SimpleNamespace(Name="Body", Label="Fan Frame"),),
        )

        panel = PrintPanel.PrintPanelWidget()

        def immediate(_message, operation, callback):
            callback(operation())

        panel._start_job = immediate
        panel.refresh()
        application.processEvents()
        assert panel.slicer_combo.currentData() == "prusaslicer"
        assert panel.printer_combo.currentText() == "Prusa Printer"
        assert panel.object_checkboxes[0].text() == "Fan Frame"

        bambu_index = panel.slicer_combo.findData("bambustudio")
        assert bambu_index >= 0
        panel.slicer_combo.setCurrentIndex(bambu_index)
        application.processEvents()

        assert state["active"] == "bambustudio"
        assert panel.backend.backend_id == "bambustudio"
        assert panel.installation.backend_id == "bambustudio"
        assert panel.printer_combo.currentText() == "Bambu Printer"
        assert panel.print_combo.currentText() == "Bambu Quality"
        assert panel.material_combos[0].currentText() == "Bambu Material"
        assert panel.object_checkboxes[0].isChecked()
        print("VIBECAD_PRINT_BACKEND_SWITCH_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if panel is not None:
            panel.close()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
