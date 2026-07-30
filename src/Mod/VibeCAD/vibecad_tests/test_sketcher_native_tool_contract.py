# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[3]
SKETCHER_GUI = SOURCE_ROOT / "Mod" / "Sketcher" / "Gui"


def _function_body(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start)
    return source[start:end]


def test_reorient_cancel_does_not_detach_the_sketch():
    command = (SKETCHER_GUI / "Command.cpp").read_text(encoding="utf-8")
    reorient = _function_body(
        command,
        "void CmdSketcherReorientSketch::activated",
        "bool CmdSketcherReorientSketch::isActive",
    )

    cancel_guard = reorient.index("if (Dlg.exec() != QDialog::Accepted)")
    transaction = reorient.index("const int transactionId = openCommand(")
    detach = reorient.index('Gui::cmdAppObjectArgs(sketch, "AttachmentSupport = None")')

    assert "AttachmentSupport.setValue(nullptr)" not in reorient
    assert "openCommand(\n        document," in reorient
    assert "document->getBookedTransactionID() != transactionId" in reorient
    assert "abortCommand(transactionId)" in reorient
    assert cancel_guard < transaction < detach


def test_validation_markers_survive_view_provider_teardown():
    validation = (SKETCHER_GUI / "TaskSketcherValidation.cpp").read_text(
        encoding="utf-8"
    )
    show_points = _function_body(
        validation,
        "void SketcherValidation::showPoints",
        "void SketcherValidation::hidePoints",
    )
    hide_points = _function_body(
        validation,
        "void SketcherValidation::hidePoints",
        "void SketcherValidation::onFindDegeneratedClicked",
    )

    assert "if (!vp || !vp->getRoot())" in show_points
    assert "coincidenceRoot->ref()" in show_points
    assert "vp && vp->getRoot()" in hide_points
    assert "findChild(coincidenceRoot) >= 0" in hide_points
    assert "coincidenceRoot->unref()" in hide_points
    assert "setAutoCloseOnDeletedDocument(true)" in validation


def test_stopping_an_interactive_handler_rolls_back_only_its_live_transaction():
    handler = (SKETCHER_GUI / "DrawSketchHandler.cpp").read_text(
        encoding="utf-8"
    )
    deactivate = _function_body(
        handler,
        "void DrawSketchHandler::deactivate()",
        "void DrawSketchHandler::preActivated()",
    )
    commit = _function_body(
        handler,
        "void DrawSketchHandler::commitCommand()",
        "void DrawSketchHandler::abortCommand()",
    )
    abort = _function_body(
        handler,
        "void DrawSketchHandler::abortCommand()",
        "int DrawSketchHandler::seekAutoConstraint",
    )

    assert deactivate.index("abortCommand();") < deactivate.index(
        "Gui::ToolHandler::deactivate();"
    )
    assert commit.index("currentTransactionID = App::NullTransaction;") < (
        commit.index("Gui::Command::commitCommand(transactionId);")
    )
    assert abort.index("currentTransactionID = App::NullTransaction;") < (
        abort.index("Gui::Command::abortCommand(transactionId);")
    )
