# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import sys


MODULE = Path(__file__).resolve().parents[1]
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))
