# SPDX-License-Identifier: LGPL-2.1-or-later

"""Register Aero commands from their exact source without module-name collisions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


COMMAND_IDS = (
    "VibeCADAero_Analyze",
    "VibeCADAero_Section",
    "VibeCADAero_VLM",
    "VibeCADAero_ExportJSBSim",
    "VibeCADAero_Report",
)
_COMMAND_MODULE_NAME = "_vibecad_aero_commands"


def _registered_commands(gui: Any) -> set[str]:
    list_commands = getattr(gui, "listCommands", None)
    if not callable(list_commands):
        return set()
    return {str(command) for command in list_commands()}


def _load_command_module() -> Any:
    source = Path(__file__).resolve().with_name("Commands.py")
    existing = sys.modules.get(_COMMAND_MODULE_NAME)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and Path(existing_file).resolve() == source:
            return existing

    spec = importlib.util.spec_from_file_location(_COMMAND_MODULE_NAME, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load the Aero command module from {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_COMMAND_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(_COMMAND_MODULE_NAME) is module:
            sys.modules.pop(_COMMAND_MODULE_NAME, None)
        raise
    return module


def ensure_commands_registered(gui: Any | None = None) -> None:
    """Register every Aero command without importing the global ``Commands`` name."""

    if gui is None:
        import FreeCADGui as gui  # type: ignore[no-redef]

    registered = _registered_commands(gui)
    if registered and set(COMMAND_IDS) <= registered:
        return

    module = _load_command_module()
    registered = _registered_commands(gui)
    if not registered or not set(COMMAND_IDS) <= registered:
        module.register_commands(gui=gui)

    list_commands = getattr(gui, "listCommands", None)
    if callable(list_commands):
        missing = set(COMMAND_IDS) - _registered_commands(gui)
        if missing:
            raise RuntimeError(
                "VibeCAD Aero commands failed to register: " + ", ".join(sorted(missing))
            )
