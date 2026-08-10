# SPDX-License-Identifier: LGPL-2.1-or-later

"""Main-thread human file chooser for Native input authorization."""

from __future__ import annotations

from typing import Any

from VibeCADNativeInput import (
    NativeInputAuthorization,
    NativeInputRequest,
    authorize_native_input_path,
)


def request_native_input_authorization(
    request: NativeInputRequest,
    *,
    parent: Any | None = None,
) -> NativeInputAuthorization | None:
    """Ask the human for one exact existing file without exposing its path to AI."""

    if not isinstance(request, NativeInputRequest):
        raise TypeError("request must be a NativeInputRequest")
    from PySide import QtWidgets

    dialog = QtWidgets.QFileDialog(parent, request.title)
    dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptOpen)
    dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFile)
    dialog.setNameFilter(request.name_filter)
    if not dialog.exec():
        return None
    selected = list(dialog.selectedFiles() or ())
    if len(selected) != 1:
        return None
    return authorize_native_input_path(request, str(selected[0]))
