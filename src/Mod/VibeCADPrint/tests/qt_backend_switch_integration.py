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
    materials = (
        (f"{prefix} Material 1",)
        if prefix in {"Bambu", "Orca"}
        else (f"{prefix} Material",)
    )
    return VibeCADPrint.PrintSetup(
        f"{prefix} Printer",
        f"{prefix} Quality",
        materials,
    )


def _backend_data(backend_id: str):
    prefix = {
        "prusaslicer": "Prusa",
        "bambustudio": "Bambu",
        "orcaslicer": "Orca",
    }[backend_id]
    setup = _setup(prefix)
    installation = VibeCADPrint.SlicerInstallation(
        backend_id=backend_id,
        version={
            "prusaslicer": "2.9.6",
            "bambustudio": "2.8.2.61",
            "orcaslicer": "2.4.2",
        }[backend_id],
        gui_command=(backend_id,),
        cli_command=(backend_id,),
        source="test",
        display_name=f"{prefix} Installed",
        tested_version={
            "prusaslicer": (2, 9, 6),
            "bambustudio": (2, 8, 2),
            "orcaslicer": (2, 4, 2),
        }[backend_id],
    )
    material_names = (
        (f"{prefix} Material 1", f"{prefix} Material 2")
        if prefix in {"Bambu", "Orca"}
        else setup.material_profiles
    )
    materials = tuple(VibeCADPrint.MaterialProfile(name) for name in material_names)
    quality = VibeCADPrint.PrintProfile(setup.print_profile, materials)
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
        self.display_name = {
            "prusaslicer": "PrusaSlicer",
            "bambustudio": "Bambu Studio",
            "orcaslicer": "OrcaSlicer",
        }[backend_id]
        self.capabilities = (
            ("object_filament_assignment",)
            if backend_id in {"bambustudio", "orcaslicer"}
            else ()
        )
        self.setup, self.installation, self.printer, self.catalog = _backend_data(
            backend_id
        )
        self.invalidations = 0

    def invalidate_cache(self):
        self.invalidations += 1

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
            for backend_id in ("prusaslicer", "bambustudio", "orcaslicer")
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
            (
                SimpleNamespace(Name="Body", Label="Fan Frame"),
                SimpleNamespace(Name="Body001", Label="Fan Rotor"),
            ),
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
        assert [combo.currentText() for combo in panel.material_combos] == [
            "Bambu Material 1",
        ]
        assert not panel.add_material_button.isHidden()
        panel.add_material_button.click()
        panel.material_combos[1].setCurrentIndex(
            panel.material_combos[1].findData("Bambu Material 2")
        )
        application.processEvents()
        assert [combo.currentText() for combo in panel.material_combos] == [
            "Bambu Material 1",
            "Bambu Material 2",
        ]
        assert panel.object_checkboxes[0].isChecked()
        assert len(panel.object_filament_combos) == 2
        assert not panel.print_button.isEnabled()
        panel.object_filament_combos[0].setCurrentIndex(
            panel.object_filament_combos[0].findData(1)
        )
        panel.object_filament_combos[1].setCurrentIndex(
            panel.object_filament_combos[1].findData(2)
        )
        application.processEvents()
        assert panel._current_setup(
            require_object_assignments=True
        ).object_filament_ids == (1, 2)
        assert panel.print_button.isEnabled()
        panel._manual_refresh()
        application.processEvents()
        assert [combo.currentData() for combo in panel.object_filament_combos] == [1, 2]

        setup_calls = []
        PrintPanel._find_dock = lambda: SimpleNamespace(widget=lambda: panel)
        PrintPanel.PrintSetupDialog.choose_print_setup = lambda **kwargs: (
            setup_calls.append(kwargs) or SimpleNamespace(accepted=False)
        )
        PrintPanel.open_setup_dialog(parent=panel)
        assert setup_calls[0]["backend"] is panel.backend

        orca_index = panel.slicer_combo.findData("orcaslicer")
        assert orca_index >= 0
        panel.slicer_combo.setCurrentIndex(orca_index)
        application.processEvents()
        assert state["active"] == "orcaslicer"
        assert panel.backend.backend_id == "orcaslicer"
        assert panel.printer_combo.currentText() == "Orca Printer"
        assert panel.print_combo.currentText() == "Orca Quality"
        assert [combo.currentText() for combo in panel.material_combos] == [
            "Orca Material 1",
        ]
        panel.add_material_button.click()
        panel.material_combos[1].setCurrentIndex(
            panel.material_combos[1].findData("Orca Material 2")
        )
        application.processEvents()
        assert len(panel.object_filament_combos) == 2
        assert not panel.print_button.isEnabled()
        panel.object_filament_combos[0].setCurrentIndex(
            panel.object_filament_combos[0].findData(2)
        )
        panel.object_filament_combos[1].setCurrentIndex(
            panel.object_filament_combos[1].findData(1)
        )
        application.processEvents()
        assert panel._current_setup(
            require_object_assignments=True
        ).object_filament_ids == (2, 1)
        assert panel.print_button.isEnabled()
        panel._manual_refresh()
        assert backends["orcaslicer"].invalidations == 1
        assert panel.object_checkboxes[0].isChecked()

        legacy = _Backend("bambustudio")
        legacy.installation = VibeCADPrint.SlicerInstallation(
            backend_id="bambustudio",
            version="2.7.1.62",
            gui_command=("bambu-studio",),
            cli_command=("bambu-studio",),
            source="test",
            display_name="Bambu Studio 2.7.1.62",
            tested_version=(2, 8, 2),
        )
        panel.backend = legacy
        panel.backend_id = "bambustudio"
        panel._clear_profiles()
        panel._installations_loaded((legacy.installation,))
        application.processEvents()
        assert panel.print_button.isEnabled()
        assert "basic 3MF" in panel.status.text()
        handoffs = []
        import PrintCommandLoader

        PrintCommandLoader.command_module = lambda: SimpleNamespace(
            open_selected_in_slicer=lambda **kwargs: handoffs.append(kwargs)
        )
        panel.print_selected()
        assert len(handoffs) == 1
        assert handoffs[0]["backend"] is legacy
        assert handoffs[0]["installation"] is legacy.installation
        assert handoffs[0]["setup"] is None
        print("VIBECAD_PRINT_BACKEND_SWITCH_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if panel is not None:
            panel.close()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
