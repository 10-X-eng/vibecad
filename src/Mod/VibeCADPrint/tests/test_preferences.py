# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import PrintPreferences
import VibeCADPrint


class _Params:
    def __init__(self) -> None:
        self.values = {}

    def SetString(self, key, value) -> None:
        self.values[key] = str(value)

    def GetString(self, key, default="") -> str:
        return str(self.values.get(key, default))

    def SetBool(self, key, value) -> None:
        self.values[key] = bool(value)

    def GetBool(self, key, default=False) -> bool:
        return bool(self.values.get(key, default))

    def RemString(self, key) -> None:
        self.values.pop(key, None)

    def RemBool(self, key) -> None:
        self.values.pop(key, None)


def _setup() -> VibeCADPrint.PrintSetup:
    return VibeCADPrint.PrintSetup(
        printer_profile="Original Prusa XL - 5T 0.4 nozzle",
        print_profile="0.20mm SPEED @XL 0.4",
        material_profiles=("Generic PLA @XL", "Generic PETG @XL"),
        auto_arrange=False,
        ensure_on_bed=True,
    )


def test_confirmed_setup_round_trips_exact_names_and_placement() -> None:
    params = _Params()

    PrintPreferences.save_confirmed_setup(_setup(), params=params)

    assert PrintPreferences.load_confirmed_setup(params=params) == _setup()
    assert params.values["MaterialProfilesJson"] == (
        '["Generic PLA @XL", "Generic PETG @XL"]'
    )


def test_incomplete_or_invalid_persisted_setup_is_not_guessed() -> None:
    params = _Params()
    params.values.update(
        {
            "PrinterProfile": "Printer",
            "PrintProfile": "Print",
            "MaterialProfilesJson": "not JSON",
        }
    )

    assert PrintPreferences.load_confirmed_setup(params=params) is None


def test_placement_defaults_are_on_only_after_an_explicit_complete_setup() -> None:
    params = _Params()
    params.values.update(
        {
            "PrinterProfile": "Printer",
            "PrintProfile": "Print",
            "MaterialProfilesJson": '["Material"]',
        }
    )

    setup = PrintPreferences.load_confirmed_setup(params=params)

    assert setup is not None
    assert setup.auto_arrange is True
    assert setup.ensure_on_bed is True


def test_executable_override_and_clear_are_additive_preferences() -> None:
    params = _Params()

    PrintPreferences.set_executable_override(
        " /opt/Prusa Slicer/prusa-slicer ", params=params
    )
    PrintPreferences.save_confirmed_setup(_setup(), params=params)
    PrintPreferences.clear_confirmed_setup(params=params)

    assert PrintPreferences.executable_override(params=params) == (
        "/opt/Prusa Slicer/prusa-slicer"
    )
    assert PrintPreferences.load_confirmed_setup(params=params) is None
    assert params.values == {"PrusaSlicerExecutable": "/opt/Prusa Slicer/prusa-slicer"}
