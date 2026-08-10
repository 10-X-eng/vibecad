# SPDX-License-Identifier: LGPL-2.1-or-later

"""Main-thread human file chooser for Native output authorization."""

from __future__ import annotations

from typing import Any

from VibeCADNativeOutput import (
    NativeOutputAuthorization,
    NativeOutputRequest,
    authorize_native_output_path,
)


def request_native_output_authorization(
    request: NativeOutputRequest,
    *,
    parent: Any | None = None,
) -> NativeOutputAuthorization | None:
    """Ask the human for one exact destination without exposing a path to AI."""

    if not isinstance(request, NativeOutputRequest):
        raise TypeError("request must be a NativeOutputRequest")
    from PySide import QtWidgets

    dialog = QtWidgets.QFileDialog(parent, request.title)
    dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QtWidgets.QFileDialog.FileMode.AnyFile)
    dialog.setNameFilter(request.name_filter)
    dialog.setDefaultSuffix(request.allowed_suffixes[0].lstrip("."))
    dialog.setConfirmOverwrite(True)
    dialog.selectFile(request.suggested_file_name)
    if not dialog.exec():
        return None
    selected = list(dialog.selectedFiles() or ())
    if len(selected) != 1:
        return None
    return authorize_native_output_path(request, str(selected[0]))
