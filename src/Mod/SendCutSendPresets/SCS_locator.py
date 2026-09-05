# SPDX-License-Identifier: MIT
"""Locate this workbench directory.

InitGui.py is exec'd by FreeCAD without __file__, so icon/data paths must
come from a normal imported module (this file).
"""

import os

PATH = os.path.dirname(os.path.realpath(__file__))
