"""Make the reference overlay testable directly with ``pytest -q``.

The reconciliation package is not installed as a Python package.  Tests must
therefore add the reference module directory explicitly rather than depending
on an undocumented caller-side PYTHONPATH.
"""
from pathlib import Path
import sys

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))
