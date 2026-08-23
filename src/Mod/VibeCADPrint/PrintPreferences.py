# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persistent preferences for the additive PrusaSlicer integration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import VibeCADPrint


PREFERENCES_PATH = "User parameter:BaseApp/Preferences/VibeCAD/Print"
EXECUTABLE_KEY = "PrusaSlicerExecutable"
PRINTER_PROFILE_KEY = "PrinterProfile"
PRINT_PROFILE_KEY = "PrintProfile"
MATERIAL_PROFILES_KEY = "MaterialProfilesJson"
AUTO_ARRANGE_KEY = "AutoArrange"
ENSURE_ON_BED_KEY = "EnsureOnBed"
HANDOFF_STORAGE_MODE_KEY = "HandoffStorageMode"
HANDOFF_DIRECTORY_KEY = "HandoffDirectory"


@dataclass(frozen=True)
class HandoffStorage:
    """Explicit location policy for automatically generated handoff files."""

    mode: str = "managed"
    directory: str = ""


def preferences() -> Any:
    import FreeCAD

    return FreeCAD.ParamGet(PREFERENCES_PATH)


def executable_override(*, params: Any | None = None) -> str:
    group = params or preferences()
    return str(group.GetString(EXECUTABLE_KEY, "") or "").strip()


def set_executable_override(value: str, *, params: Any | None = None) -> None:
    group = params or preferences()
    group.SetString(EXECUTABLE_KEY, str(value or "").strip())


def load_handoff_storage(*, params: Any | None = None) -> HandoffStorage:
    group = params or preferences()
    mode = str(group.GetString(HANDOFF_STORAGE_MODE_KEY, "managed") or "managed")
    directory = str(group.GetString(HANDOFF_DIRECTORY_KEY, "") or "").strip()
    if mode not in {"managed", "folder"} or (mode == "folder" and not directory):
        return HandoffStorage()
    return HandoffStorage(mode=mode, directory=directory)


def save_handoff_storage(
    storage: HandoffStorage,
    *,
    params: Any | None = None,
) -> None:
    mode = str(storage.mode or "").strip()
    directory = str(storage.directory or "").strip()
    if mode not in {"managed", "folder"}:
        raise ValueError("3MF storage mode must be 'managed' or 'folder'.")
    if mode == "folder" and not directory:
        raise ValueError("Choose a folder for persistent 3MF handoffs.")
    group = params or preferences()
    group.SetString(HANDOFF_STORAGE_MODE_KEY, mode)
    group.SetString(HANDOFF_DIRECTORY_KEY, directory)


def load_confirmed_setup(
    *, params: Any | None = None
) -> VibeCADPrint.PrintSetup | None:
    """Load a complete setup or None; malformed state never gains defaults."""

    group = params or preferences()
    printer = str(group.GetString(PRINTER_PROFILE_KEY, "") or "").strip()
    print_profile = str(group.GetString(PRINT_PROFILE_KEY, "") or "").strip()
    raw_materials = str(group.GetString(MATERIAL_PROFILES_KEY, "") or "").strip()
    if not printer or not print_profile or not raw_materials:
        return None
    try:
        decoded = json.loads(raw_materials)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, list):
        return None
    materials = tuple(str(value or "").strip() for value in decoded)
    if not materials or any(not value for value in materials):
        return None
    return VibeCADPrint.PrintSetup(
        printer_profile=printer,
        print_profile=print_profile,
        material_profiles=materials,
        auto_arrange=bool(group.GetBool(AUTO_ARRANGE_KEY, True)),
        ensure_on_bed=bool(group.GetBool(ENSURE_ON_BED_KEY, True)),
    )


def save_confirmed_setup(
    setup: VibeCADPrint.PrintSetup, *, params: Any | None = None
) -> None:
    group = params or preferences()
    group.SetString(PRINTER_PROFILE_KEY, setup.printer_profile)
    group.SetString(PRINT_PROFILE_KEY, setup.print_profile)
    group.SetString(MATERIAL_PROFILES_KEY, json.dumps(list(setup.material_profiles)))
    group.SetBool(AUTO_ARRANGE_KEY, bool(setup.auto_arrange))
    group.SetBool(ENSURE_ON_BED_KEY, bool(setup.ensure_on_bed))


def clear_confirmed_setup(*, params: Any | None = None) -> None:
    group = params or preferences()
    for key in (PRINTER_PROFILE_KEY, PRINT_PROFILE_KEY, MATERIAL_PROFILES_KEY):
        group.RemString(key)
    for key in (AUTO_ARRANGE_KEY, ENSURE_ON_BED_KEY):
        group.RemBool(key)
