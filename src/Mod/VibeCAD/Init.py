# SPDX-License-Identifier: LGPL-2.1-or-later

"""FreeCAD init script for the application-wide VibeCAD AI subsystem.

VibeCAD does not register a standalone workbench. Its application initializer
registers panels and commands once, while the active workbench selects the
exact modeling surface made available to the assistant.
"""
