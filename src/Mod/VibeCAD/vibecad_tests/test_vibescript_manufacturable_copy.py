# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path


def test_vibescript_copy_does_not_say_manufacturable_solid() -> None:
    source = Path(__file__).resolve().parents[1] / "VibeCADVibeScriptDomainRuntime.py"
    text = source.read_text(encoding="utf-8")
    assert "manufacturable solid" not in text
    assert "accepted Native B-rep geometry and semantic machining faces" in text
