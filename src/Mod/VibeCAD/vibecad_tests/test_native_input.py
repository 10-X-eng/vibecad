# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from VibeCADNativeInput import (
    NATIVE_INPUT_AUTHORIZATION_FAILED,
    NATIVE_INPUT_FAILED,
    NativeInputError,
    NativeInputRequest,
    authorize_native_input_path,
    inspect_native_input_file,
)
from VibeCADNativeInputGui import request_native_input_authorization


def _request(*, maximum_bytes: int = 1024) -> NativeInputRequest:
    return NativeInputRequest(
        purpose="test_input",
        title="Select test input",
        allowed_suffixes=(".csv",),
        name_filter="CSV Files (*.csv)",
        maximum_bytes=maximum_bytes,
    )


def test_human_input_grant_is_exact_one_shot_and_path_free(tmp_path: Path) -> None:
    source = tmp_path / "robot.csv"
    source.write_bytes(b"axis,data\n")
    request = _request()

    authorization = authorize_native_input_path(request, source)
    artifact = authorization.claim(request)

    assert artifact.read_bytes() == b"axis,data\n"
    assert artifact.summary() == {
        "file_name": "robot.csv",
        "size_bytes": 10,
        "sha256": "b93cc7d54b3197e677b96e5f84e5135f4753aea0e6b61e56e50430522872bfcc",
    }
    assert str(tmp_path) not in repr(artifact)
    assert str(tmp_path) not in repr(authorization)
    with pytest.raises(NativeInputError, match="already been used"):
        authorization.claim(request)


def test_human_input_reads_binary_control_bytes_without_text_translation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "binary.csv"
    content = b"before\x1aafter\r\n\x00\xff"
    source.write_bytes(content)
    request = _request()

    artifact = authorize_native_input_path(request, source).claim(request)

    assert artifact.read_bytes() == content


def test_input_authorization_rejects_wrong_kind_suffix_and_size(
    tmp_path: Path,
) -> None:
    source = tmp_path / "robot.txt"
    source.write_bytes(b"1234")
    with pytest.raises(NativeInputError) as suffix:
        authorize_native_input_path(_request(), source)
    assert suffix.value.code == NATIVE_INPUT_AUTHORIZATION_FAILED

    directory = tmp_path / "directory.csv"
    directory.mkdir()
    with pytest.raises(NativeInputError, match="regular file"):
        authorize_native_input_path(_request(), directory)

    large = tmp_path / "large.csv"
    large.write_bytes(b"12345")
    with pytest.raises(NativeInputError, match="byte bound"):
        authorize_native_input_path(_request(maximum_bytes=4), large)

    unsafe_name = tmp_path / "unsafe\nname.csv"
    with pytest.raises(NativeInputError, match="valid file name"):
        authorize_native_input_path(_request(), unsafe_name)


def test_input_artifact_refuses_content_drift(tmp_path: Path) -> None:
    source = tmp_path / "robot.csv"
    source.write_bytes(b"before")
    request = _request()
    artifact = authorize_native_input_path(request, source).claim(request)

    source.write_bytes(b"after!")

    with pytest.raises(NativeInputError) as changed:
        artifact.verify_unchanged()
    assert changed.value.code == NATIVE_INPUT_FAILED


def test_document_file_inspection_is_content_stable_and_path_free(
    tmp_path: Path,
) -> None:
    source = tmp_path / "definition.csv"
    source.write_bytes(b"definition")

    summary = inspect_native_input_file(source, maximum_bytes=128)

    assert summary == {
        "configured": True,
        "size_bytes": 10,
        "sha256": "4c4ed1afbfdaa1e4c3bf7bbb82d730cecb7e384da91eea4f3cc093fd545524d6",
    }
    assert str(tmp_path) not in str(summary)
    assert inspect_native_input_file("", maximum_bytes=128) == {"configured": False}


def test_gui_input_authorizer_uses_one_host_owned_existing_file_dialog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "chosen.csv"
    source.write_bytes(b"chosen")
    observed: dict[str, object] = {}

    class _Dialog:
        AcceptMode = SimpleNamespace(AcceptOpen="accept-open")
        FileMode = SimpleNamespace(ExistingFile="existing-file")

        def __init__(self, parent, title):
            observed.update(parent=parent, title=title)

        def setAcceptMode(self, value):
            observed["accept_mode"] = value

        def setFileMode(self, value):
            observed["file_mode"] = value

        def setNameFilter(self, value):
            observed["name_filter"] = value

        def exec(self):
            return True

        def selectedFiles(self):
            return [str(source)]

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtWidgets=SimpleNamespace(QFileDialog=_Dialog)),
    )
    request = _request()
    authorization = request_native_input_authorization(request, parent="owner")

    assert authorization is not None
    assert authorization.claim(request).read_bytes() == b"chosen"
    assert observed == {
        "parent": "owner",
        "title": request.title,
        "accept_mode": "accept-open",
        "file_mode": "existing-file",
        "name_filter": request.name_filter,
    }
