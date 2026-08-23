# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persistent preferences for additive external-slicer integrations."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import VibeCADPrint


PREFERENCES_PATH = "User parameter:BaseApp/Preferences/VibeCAD/Print"
EXECUTABLE_KEY = "PrusaSlicerExecutable"
ACTIVE_BACKEND_KEY = "SlicerBackend"
BAMBU_EXECUTABLE_KEY = "BambuStudioExecutable"
PRINTER_PROFILE_KEY = "PrinterProfile"
PRINT_PROFILE_KEY = "PrintProfile"
MATERIAL_PROFILES_KEY = "MaterialProfilesJson"
AUTO_ARRANGE_KEY = "AutoArrange"
ENSURE_ON_BED_KEY = "EnsureOnBed"
HANDOFF_STORAGE_MODE_KEY = "HandoffStorageMode"
HANDOFF_DIRECTORY_KEY = "HandoffDirectory"

SUPPORTED_BACKENDS = ("prusaslicer", "bambustudio", "orcaslicer")
_BACKEND_KEYS = {
    "prusaslicer": (
        EXECUTABLE_KEY,
        PRINTER_PROFILE_KEY,
        PRINT_PROFILE_KEY,
        MATERIAL_PROFILES_KEY,
        AUTO_ARRANGE_KEY,
        ENSURE_ON_BED_KEY,
    ),
    "bambustudio": (
        BAMBU_EXECUTABLE_KEY,
        "BambuStudioPrinterProfile",
        "BambuStudioPrintProfile",
        "BambuStudioMaterialProfilesJson",
        "BambuStudioAutoArrange",
        "BambuStudioEnsureOnBed",
    ),
    "orcaslicer": (
        "OrcaSlicerExecutable",
        "OrcaSlicerPrinterProfile",
        "OrcaSlicerPrintProfile",
        "OrcaSlicerMaterialProfilesJson",
        "OrcaSlicerAutoArrange",
        "OrcaSlicerEnsureOnBed",
    ),
}


@dataclass(frozen=True)
class HandoffStorage:
    """Explicit location policy for automatically generated handoff files."""

    mode: str = "managed"
    directory: str = ""


def preferences() -> Any:
    import FreeCAD

    return FreeCAD.ParamGet(PREFERENCES_PATH)


def _keys(backend_id: str) -> tuple[str, str, str, str, str, str]:
    try:
        return _BACKEND_KEYS[str(backend_id)]
    except KeyError as exc:
        raise ValueError(f"Unsupported slicer backend: {backend_id}") from exc


def active_backend(*, params: Any | None = None) -> str:
    group = params or preferences()
    value = str(group.GetString(ACTIVE_BACKEND_KEY, "prusaslicer") or "prusaslicer")
    return value if value in SUPPORTED_BACKENDS else "prusaslicer"


def set_active_backend(value: str, *, params: Any | None = None) -> None:
    backend_id = str(value or "").strip()
    _keys(backend_id)
    group = params or preferences()
    group.SetString(ACTIVE_BACKEND_KEY, backend_id)


def executable_override(
    *,
    backend_id: str = "prusaslicer",
    params: Any | None = None,
) -> str:
    group = params or preferences()
    executable_key, *_rest = _keys(backend_id)
    return str(group.GetString(executable_key, "") or "").strip()


def set_executable_override(
    value: str,
    *,
    backend_id: str = "prusaslicer",
    params: Any | None = None,
) -> None:
    group = params or preferences()
    executable_key, *_rest = _keys(backend_id)
    group.SetString(executable_key, str(value or "").strip())


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
    *,
    backend_id: str = "prusaslicer",
    params: Any | None = None,
) -> VibeCADPrint.PrintSetup | None:
    """Load a complete setup or None; malformed state never gains defaults."""

    group = params or preferences()
    (
        _executable_key,
        printer_key,
        print_key,
        materials_key,
        arrange_key,
        bed_key,
    ) = _keys(backend_id)
    printer = str(group.GetString(printer_key, "") or "").strip()
    print_profile = str(group.GetString(print_key, "") or "").strip()
    raw_materials = str(group.GetString(materials_key, "") or "").strip()
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
        auto_arrange=bool(group.GetBool(arrange_key, True)),
        ensure_on_bed=bool(group.GetBool(bed_key, True)),
    )


def save_confirmed_setup(
    setup: VibeCADPrint.PrintSetup,
    *,
    backend_id: str = "prusaslicer",
    params: Any | None = None,
) -> None:
    group = params or preferences()
    (
        _executable_key,
        printer_key,
        print_key,
        materials_key,
        arrange_key,
        bed_key,
    ) = _keys(backend_id)
    group.SetString(printer_key, setup.printer_profile)
    group.SetString(print_key, setup.print_profile)
    group.SetString(materials_key, json.dumps(list(setup.material_profiles)))
    group.SetBool(arrange_key, bool(setup.auto_arrange))
    group.SetBool(bed_key, bool(setup.ensure_on_bed))


def clear_confirmed_setup(
    *,
    backend_id: str = "prusaslicer",
    params: Any | None = None,
) -> None:
    group = params or preferences()
    (
        _executable_key,
        printer_key,
        print_key,
        materials_key,
        arrange_key,
        bed_key,
    ) = _keys(backend_id)
    for key in (printer_key, print_key, materials_key):
        group.RemString(key)
    for key in (arrange_key, bed_key):
        group.RemBool(key)
