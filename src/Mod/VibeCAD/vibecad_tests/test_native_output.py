# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from VibeCADNativeOutput import (
    NATIVE_OUTPUT_AUTHORIZATION_FAILED,
    NativeOutputError,
    NativeOutputBundleItem,
    NativeOutputRequest,
    authorize_native_output_path,
    publish_authorized_output,
    publish_authorized_output_bundle,
)
from VibeCADNativeOutputGui import request_native_output_authorization


def _request(*, maximum_bytes: int = 1024) -> NativeOutputRequest:
    return NativeOutputRequest(
        purpose="assembly_asmt_export",
        title="Export active Assembly as ASMT",
        suggested_file_name="Assembly.asmt",
        allowed_suffixes=(".asmt",),
        name_filter="ASMT Files (*.asmt)",
        maximum_bytes=maximum_bytes,
    )


def test_authorization_accepts_only_one_exact_regular_output_destination(
    tmp_path: Path,
) -> None:
    request = _request()
    destination = tmp_path / "Result.ASMT"
    authorization = authorize_native_output_path(request, destination)

    assert authorization.request is request
    with pytest.raises(NativeOutputError, match="must use one of"):
        authorize_native_output_path(request, tmp_path / "Result.txt")

    directory = tmp_path / "Directory.asmt"
    directory.mkdir()
    with pytest.raises(NativeOutputError, match="regular file"):
        authorize_native_output_path(request, directory)


def test_authorization_rejects_symlink_destination(tmp_path: Path) -> None:
    request = _request()

    target = tmp_path / "Target.asmt"
    target.write_text("existing", encoding="utf-8")
    link = tmp_path / "Linked.asmt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    with pytest.raises(NativeOutputError, match="regular file"):
        authorize_native_output_path(request, link)


def test_authorized_output_is_private_atomic_bounded_and_one_shot(
    tmp_path: Path,
) -> None:
    request = _request()
    destination = tmp_path / "Assembly.asmt"
    authorization = authorize_native_output_path(request, destination)
    guards = []
    payload = b"OndselSolver\n\x1aAssembly\n"

    artifact = publish_authorized_output(
        request,
        authorization,
        writer=lambda path: Path(path).write_bytes(payload),
        guard=lambda: guards.append(True),
    )

    assert destination.read_bytes() == payload
    assert artifact.file_name == destination.name
    assert artifact.size_bytes == len(destination.read_bytes())
    assert len(artifact.sha256) == 64
    assert artifact.replaced_existing is False
    assert len(guards) == 2
    assert not list(tmp_path.glob(".*.vibecad-*.tmp"))
    with pytest.raises(NativeOutputError, match="already been used"):
        publish_authorized_output(
            request,
            authorization,
            writer=lambda _path: None,
            guard=lambda: None,
        )


def test_destination_drift_fails_before_writer(tmp_path: Path) -> None:
    request = _request()
    destination = tmp_path / "Assembly.asmt"
    destination.write_text("authorized", encoding="utf-8")
    authorization = authorize_native_output_path(request, destination)
    destination.write_text("changed after authorization", encoding="utf-8")
    calls = []

    with pytest.raises(NativeOutputError, match="changed after authorization"):
        publish_authorized_output(
            request,
            authorization,
            writer=lambda _path: calls.append(True),
            guard=lambda: None,
        )

    assert calls == []
    assert destination.read_text(encoding="utf-8") == "changed after authorization"


@pytest.mark.parametrize("failure", ("writer", "validator", "oversized"))
def test_failed_generation_preserves_existing_destination(
    tmp_path: Path,
    failure: str,
) -> None:
    request = _request(maximum_bytes=16)
    destination = tmp_path / "Assembly.asmt"
    destination.write_text("original", encoding="utf-8")
    authorization = authorize_native_output_path(request, destination)

    def writer(path: str) -> None:
        if failure == "writer":
            raise RuntimeError("serializer failed")
        Path(path).write_bytes(
            b"too much generated output" if failure == "oversized" else b"invalid"
        )

    validator = (
        (lambda _path: (_ for _ in ()).throw(RuntimeError("invalid output")))
        if failure == "validator"
        else None
    )
    with pytest.raises(Exception):
        publish_authorized_output(
            request,
            authorization,
            writer=writer,
            guard=lambda: None,
            validator=validator,
        )

    assert destination.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob(".*.vibecad-*.tmp"))


def test_authorization_failure_has_stable_machine_code(tmp_path: Path) -> None:
    with pytest.raises(NativeOutputError) as failure:
        authorize_native_output_path(_request(), tmp_path / "wrong.txt")

    assert failure.value.failure()["error_code"] == NATIVE_OUTPUT_AUTHORIZATION_FAILED


def test_gui_chooser_returns_only_the_exact_human_selected_grant(
    monkeypatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "HumanChoice.asmt"
    created = []

    class _Dialog:
        AcceptMode = SimpleNamespace(AcceptSave="save")
        FileMode = SimpleNamespace(AnyFile="file")

        def __init__(self, parent, title) -> None:
            self.parent = parent
            self.title = title
            created.append(self)

        def setAcceptMode(self, value) -> None:
            self.accept_mode = value

        def setFileMode(self, value) -> None:
            self.file_mode = value

        def setNameFilter(self, value) -> None:
            self.name_filter = value

        def setDefaultSuffix(self, value) -> None:
            self.default_suffix = value

        def setConfirmOverwrite(self, value) -> None:
            self.confirm_overwrite = value

        def selectFile(self, value) -> None:
            self.selected_default = value

        def exec(self) -> bool:
            return True

        def selectedFiles(self) -> list[str]:
            return [str(destination)]

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtWidgets=SimpleNamespace(QFileDialog=_Dialog)),
    )
    parent = object()
    request = _request()
    authorization = request_native_output_authorization(request, parent=parent)

    assert authorization is not None and authorization.request is request
    assert len(created) == 1
    dialog = created[0]
    assert dialog.parent is parent
    assert dialog.title == request.title
    assert dialog.accept_mode == "save"
    assert dialog.file_mode == "file"
    assert dialog.name_filter == request.name_filter
    assert dialog.default_suffix == "asmt"
    assert dialog.confirm_overwrite is True
    assert dialog.selected_default == request.suggested_file_name


def _bundle_item(
    tmp_path: Path,
    file_name: str,
    content: bytes,
) -> NativeOutputBundleItem:
    request = NativeOutputRequest(
        purpose="cam_post",
        title="Save CAM program",
        suggested_file_name=file_name,
        allowed_suffixes=(".nc",),
        name_filter="NC program (*.nc)",
        maximum_bytes=1024,
    )
    authorization = authorize_native_output_path(request, tmp_path / file_name)
    return NativeOutputBundleItem(
        request=request,
        authorization=authorization,
        writer=lambda path: Path(path).write_bytes(content),
        temporary_suffix=".nc",
    )


def test_authorized_output_bundle_publishes_complete_validated_set(
    tmp_path: Path,
) -> None:
    first = _bundle_item(tmp_path, "setup.nc", b"G21\nG0 X0\n")
    second_path = tmp_path / "finish.nc"
    second_path.write_bytes(b"original\n")
    second = _bundle_item(tmp_path, "finish.nc", b"G1 X10\nM30\n")
    guards = []

    artifacts = publish_authorized_output_bundle(
        (first, second),
        guard=lambda: guards.append(True),
    )

    assert (tmp_path / "setup.nc").read_bytes() == b"G21\nG0 X0\n"
    assert second_path.read_bytes() == b"G1 X10\nM30\n"
    assert [artifact.file_name for artifact in artifacts] == ["setup.nc", "finish.nc"]
    assert [artifact.replaced_existing for artifact in artifacts] == [False, True]
    assert len(guards) == 2
    assert not list(tmp_path.glob(".*.vibecad-*"))


def test_authorized_output_bundle_rejects_duplicate_destinations_before_writing(
    tmp_path: Path,
) -> None:
    request_a = _request()
    request_b = _request()
    destination = tmp_path / "Assembly.asmt"
    calls = []
    items = (
        NativeOutputBundleItem(
            request_a,
            authorize_native_output_path(request_a, destination),
            lambda _path: calls.append("a"),
        ),
        NativeOutputBundleItem(
            request_b,
            authorize_native_output_path(request_b, destination),
            lambda _path: calls.append("b"),
        ),
    )

    with pytest.raises(NativeOutputError, match="same destination"):
        publish_authorized_output_bundle(items, guard=lambda: None)

    assert calls == []
    assert not destination.exists()


def test_authorized_output_bundle_restores_every_original_on_publish_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "setup.nc"
    second_path = tmp_path / "finish.nc"
    first_path.write_bytes(b"first original\n")
    second_path.write_bytes(b"second original\n")
    first = _bundle_item(tmp_path, "setup.nc", b"first replacement\n")
    second = _bundle_item(tmp_path, "finish.nc", b"second replacement\n")

    import VibeCADNativeOutput as output_module

    real_replace = output_module.os.replace
    publish_count = 0

    def failing_replace(source, destination) -> None:
        nonlocal publish_count
        source_path = Path(source)
        destination_path = Path(destination)
        if ".vibecad-" in source_path.name and destination_path.suffix == ".nc":
            publish_count += 1
            if publish_count == 2:
                raise OSError("injected second publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(output_module.os, "replace", failing_replace)
    with pytest.raises(NativeOutputError, match="could not be generated and published"):
        publish_authorized_output_bundle((first, second), guard=lambda: None)

    assert first_path.read_bytes() == b"first original\n"
    assert second_path.read_bytes() == b"second original\n"
    assert not list(tmp_path.glob(".*.vibecad-*"))
