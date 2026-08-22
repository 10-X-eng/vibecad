# SPDX-License-Identifier: LGPL-2.1-or-later
"""Source contract: Sketcher Dimension does not leave a second automatic constraint."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS_CPP = (
    ROOT / "src" / "Mod" / "Sketcher" / "Gui" / "CommandConstraints.cpp"
)


def _method(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_restart_discards_provisional_dimensions_when_abort_is_a_noop() -> None:
    source = CONSTRAINTS_CPP.read_text(encoding="utf-8")
    restart = _method(source, "void restartCommand(const char* cstrName)", "void resetTool()")
    assert "discardProvisionalConstraints()" in restart
    assert restart.find("abortCommand()") < restart.find(
        "discardProvisionalConstraints()"
    )


def test_cancel_and_deactivate_also_drop_uncommitted_dimensions() -> None:
    source = CONSTRAINTS_CPP.read_text(encoding="utf-8")
    deactivated = _method(source, "void deactivated() override", "void registerPressedKey")
    reset = _method(source, "void resetTool()", "};\n\nDEF_STD_CMD_AU(CmdSketcherDimension)")
    assert "discardProvisionalConstraints()" in deactivated
    assert "discardProvisionalConstraints()" in reset
    assert "delConstraint(" in source.split("void discardProvisionalConstraints()", 1)[1].split(
        "void restartCommand", 1
    )[0]


def test_accepted_dimension_is_not_deleted_when_the_tool_restarts() -> None:
    source = CONSTRAINTS_CPP.read_text(encoding="utf-8")
    finalize = _method(
        source, "void finalizeCommand()", "std::vector<SelIdPair>& getSelectionVector"
    )
    accepted = finalize.split("if (!dialogCancelled)", 1)[1]
    assert "cstrIndexes.clear()" in accepted
    assert accepted.find("cstrIndexes.clear()") < accepted.find("resetTool()")
