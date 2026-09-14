# SPDX-License-Identifier: LGPL-2.1-or-later

import sys
from types import SimpleNamespace

import pytest
import VibeCADGui as gui


@pytest.mark.parametrize("accept", [False, True])
def test_switch_confirmation_explains_ai_and_source_preservation(monkeypatch, accept):
    class Message:
        Warning, AcceptRole, Cancel = range(3)

        def __init__(self, parent):
            self.buttons = []
            messages.append(self)

        def setIcon(self, value): pass
        def setWindowTitle(self, value): self.title = value
        def setText(self, value): self.text = value
        def setInformativeText(self, value): self.explanation = value
        def setDefaultButton(self, value): self.default = value
        def setEscapeButton(self, value): self.escape = value
        def addButton(self, value, *args):
            self.buttons.append(value)
            return value
        def exec(self): pass
        def clickedButton(self): return self.buttons[0] if accept else self.Cancel

    messages = []
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(
        QtWidgets=SimpleNamespace(QMessageBox=Message),
    ))
    monkeypatch.setattr(gui.Gui, "getMainWindow", lambda: None, raising=False)
    assert gui._confirm_take_manual_control() is accept
    message = messages[0]
    assert message.buttons == ["Switch to Native", Message.Cancel]
    assert "You and the AI" in message.explanation
    assert "code is kept" in message.explanation
    assert "not written back" in message.explanation
    assert "Save a copy" in message.explanation
    assert "epoch" not in message.explanation
    assert message.default == Message.Cancel
    assert message.escape == Message.Cancel

