# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached process execution and authorized publication for CAM posts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufacturePostInput import (
    FrozenPostInput,
    MAX_MACHINE_CONFIG_BYTES,
    MAX_POST_SNAPSHOT_BYTES,
    validate_file_identity,
)
from VibeCADNativeOutput import (
    NativeOutputArtifact,
    NativeOutputAuthorization,
    NativeOutputBundleItem,
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output_bundle,
)
from VibeCADScriptedProcess import run_process


MAX_POST_OUTPUTS = 64
MAX_POST_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_POST_TOTAL_OUTPUT_BYTES = 1024 * 1024 * 1024
MAX_POST_RESULT_BYTES = 64 * 1024
POST_TIMEOUT_SECONDS = 600.0
POST_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
_BINARY_OPEN = getattr(os, "O_BINARY", 0)
_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,16}$")


@dataclass(frozen=True, slots=True)
class PreparedPostFile:
    path: Path = field(repr=False, compare=False)
    file_name: str
    suffix: str
    section: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PreparedPostOutput:
    frozen: FrozenPostInput = field(repr=False, compare=False)
    files: tuple[PreparedPostFile, ...]
    total_size_bytes: int


def _error(message: str, code: str) -> None:
    raise NativeManufactureError(message, error_code=code)


def _cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise NativeBackgroundCancelled()


def _hash_file(path: Path, maximum: int) -> tuple[int, str]:
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeManufactureError(
            "An isolated postprocessor output file is unavailable.",
            error_code="NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "An isolated postprocessor output is not a regular file.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | _BINARY_OPEN
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NativeManufactureError(
            "An isolated postprocessor file could not be opened safely.",
            error_code="NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        ) from exc
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            _error(
                "An isolated postprocessor file changed before it was opened.",
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                _error(
                    "An isolated postprocessor output exceeds its safety bound.",
                    "NATIVE_MANUFACTURE_POST_LIMIT",
                )
            digest.update(chunk)
    finally:
        os.close(descriptor)
    if size <= 0:
        _error(
            "An isolated postprocessor output is empty.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )
    return size, digest.hexdigest()


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY_OPEN,
        0o600,
    )
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _request(frozen: FrozenPostInput, output_directory: Path, result_path: Path) -> bytes:
    value = {
        "workspace": str(frozen.workspace_path),
        "snapshot": str(frozen.snapshot_path),
        "snapshot_sha256": frozen.snapshot_sha256,
        "snapshot_size": frozen.snapshot_size,
        "job_name": frozen.job_name,
        "job_state_sha256": str(frozen.job_before["state_sha256"]),
        "operation_variant": frozen.operation_variant,
        "selected_operations": [
            dict(value) for value in frozen.selected_operation_targets
        ],
        "use_machine_flow": frozen.use_machine_flow,
        "machine_name": frozen.machine_name,
        "machine_config": (
            str(frozen.machine_config_path)
            if frozen.machine_config_path is not None
            else None
        ),
        "machine_config_sha256": frozen.machine_config_sha256,
        "postprocessor_name": frozen.postprocessor_name,
        "postprocessor_source": str(frozen.postprocessor_source.path),
        "postprocessor_sha256": frozen.postprocessor_source.sha256,
        "child_sha256": frozen.child_script.sha256,
        "configured_output": frozen.configured_output,
        "output_directory": str(output_directory),
        "result": str(result_path),
    }
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_POST_RESULT_BYTES:
        _error(
            "The frozen postprocessor request exceeds its metadata bound.",
            "NATIVE_MANUFACTURE_POST_LIMIT",
        )
    return encoded


def _read_result(path: Path) -> dict[str, Any]:
    try:
        value = path.lstat()
        if not stat.S_ISREG(value.st_mode):
            raise OSError("result is not a regular file")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | _BINARY_OPEN,
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or int(opened.st_dev) != int(value.st_dev)
                or int(opened.st_ino) != int(value.st_ino)
            ):
                raise OSError("result identity changed")
            data = bytearray()
            while True:
                chunk = os.read(descriptor, 16 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_POST_RESULT_BYTES:
                    raise ValueError("result exceeds metadata bound")
        finally:
            os.close(descriptor)
        if not data:
            raise ValueError("result is empty")
        result = json.loads(bytes(data).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeManufactureError(
            "The isolated postprocessor returned unreadable result metadata.",
            error_code="NATIVE_MANUFACTURE_POST_EXECUTION_FAILED",
        ) from exc
    except ValueError as exc:
        raise NativeManufactureError(
            "The isolated postprocessor result metadata exceeds its safety bound.",
            error_code="NATIVE_MANUFACTURE_POST_LIMIT",
        ) from exc
    if not isinstance(result, Mapping) or type(result.get("ok")) is not bool:
        _error(
            "The isolated postprocessor returned malformed result metadata.",
            "NATIVE_MANUFACTURE_POST_EXECUTION_FAILED",
        )
    if not result["ok"]:
        code = str(result.get("error_code") or "NATIVE_MANUFACTURE_POST_EXECUTION_FAILED")
        message = str(result.get("message") or "The isolated postprocessor failed.")
        raise NativeManufactureError(message[:320], error_code=code[:80])
    return dict(result)


def _validate_frozen_files(frozen: FrozenPostInput) -> None:
    validate_file_identity(frozen.freecadcmd, executable=True)
    validate_file_identity(frozen.child_script)
    validate_file_identity(frozen.postprocessor_source)
    size, digest = _hash_file(frozen.snapshot_path, MAX_POST_SNAPSHOT_BYTES)
    if size != frozen.snapshot_size or digest != frozen.snapshot_sha256:
        _error(
            "The private CAM snapshot changed before isolated execution "
            f"(expected {frozen.snapshot_size} bytes/"
            f"{frozen.snapshot_sha256[:12]}, read {size} bytes/{digest[:12]}).",
            "NATIVE_MANUFACTURE_POST_SNAPSHOT_INVALID",
        )
    if frozen.machine_config_path is not None:
        size, digest = _hash_file(frozen.machine_config_path, MAX_MACHINE_CONFIG_BYTES)
        if digest != frozen.machine_config_sha256:
            _error(
                "The frozen machine configuration changed before isolated execution.",
                "NATIVE_MANUFACTURE_POST_MACHINE_INVALID",
            )


def prepare_post(
    frozen: FrozenPostInput,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedPostOutput:
    """Run the fixed child and authenticate every private output file."""

    if not isinstance(frozen, FrozenPostInput):
        raise TypeError("frozen must be a FrozenPostInput")
    _cancel(cancelled)
    progress(5, "Validating frozen CAM Job and postprocessor")
    _validate_frozen_files(frozen)
    output_directory = frozen.workspace_path / "outputs"
    output_directory.mkdir(mode=0o700)
    request_path = frozen.workspace_path / "request.json"
    result_path = frozen.workspace_path / "result.json"
    _write_private(request_path, _request(frozen, output_directory, result_path))
    _cancel(cancelled)
    progress(10, "Running configured postprocessor in isolated FreeCADCmd")
    environment = dict(os.environ)
    environment["VIBECAD_NATIVE_POST_REQUEST"] = str(request_path)
    process = run_process(
        [str(frozen.freecadcmd.path), str(frozen.child_script.path)],
        cwd=frozen.workspace_path,
        environment=environment,
        cancellation_check=cancelled,
        timeout_seconds=POST_TIMEOUT_SECONDS,
        memory_limit_bytes=POST_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated FreeCADCmd postprocessor could not start.",
            "NATIVE_MANUFACTURE_POST_EXECUTION_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "The isolated CAM postprocessor exceeded its ten-minute limit.",
            "NATIVE_MANUFACTURE_POST_LIMIT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "The isolated CAM postprocessor exceeded its 2 GiB memory limit.",
            "NATIVE_MANUFACTURE_POST_LIMIT",
        )
    _cancel(cancelled)
    if not result_path.is_file():
        _error(
            "The isolated CAM postprocessor returned no authenticated result.",
            "NATIVE_MANUFACTURE_POST_EXECUTION_FAILED",
        )
    result = _read_result(result_path)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated CAM postprocessor exited unsuccessfully.",
            "NATIVE_MANUFACTURE_POST_EXECUTION_FAILED",
        )
    if (
        str(result.get("postprocessor") or "") != frozen.postprocessor_name
        or type(result.get("machine_configured")) is not bool
        or bool(result["machine_configured"]) != frozen.use_machine_flow
        or str(result.get("operation_variant") or "") != frozen.operation_variant
        or int(result.get("selected_operation_count", -1))
        != len(frozen.selected_operation_names)
    ):
        _error(
            "The isolated postprocessor result does not match its frozen configuration.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )
    progress(85, "Authenticating isolated CAM program outputs")
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_POST_OUTPUTS:
        _error(
            "The isolated postprocessor returned an invalid output count.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )
    files = []
    total = 0
    names = set()
    expected_fields = {
        "file_name",
        "suffix",
        "section",
        "relative_path",
        "size_bytes",
        "sha256",
    }
    for item in outputs:
        if not isinstance(item, Mapping) or set(item) != expected_fields:
            _error(
                "The isolated postprocessor returned malformed output metadata.",
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            )
        relative = str(item["relative_path"] or "")
        if Path(relative).name != relative or relative in {"", ".", ".."}:
            _error(
                "The isolated postprocessor returned an invalid private output name.",
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            )
        path = output_directory / relative
        size, digest = _hash_file(path, MAX_POST_OUTPUT_BYTES)
        if size != int(item["size_bytes"]) or digest != str(item["sha256"]):
            _error(
                "An isolated postprocessor output does not match its authenticated metadata.",
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            )
        file_name = str(item["file_name"] or "")
        suffix = str(item["suffix"] or "")
        if (
            Path(file_name).name != file_name
            or file_name in {"", ".", ".."}
            or len(file_name) > 255
            or any(ord(value) < 32 for value in file_name)
            or not _SAFE_SUFFIX.fullmatch(suffix)
            or Path(file_name).suffix.casefold() != suffix
        ):
            _error(
                "The isolated postprocessor returned an invalid suggested file name.",
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            )
        folded = os.path.normcase(file_name)
        if folded in names:
            _error(
                "The isolated postprocessor returned duplicate output names.",
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            )
        names.add(folded)
        total += size
        if total > MAX_POST_TOTAL_OUTPUT_BYTES:
            _error(
                "The isolated postprocessor exceeded its total output bound.",
                "NATIVE_MANUFACTURE_POST_LIMIT",
            )
        files.append(
            PreparedPostFile(
                path=path,
                file_name=file_name,
                suffix=suffix,
                section=str(item["section"])[:160],
                size_bytes=size,
                sha256=digest,
            )
        )
    if total != int(result.get("total_size_bytes", -1)) or len(files) != int(
        result.get("output_count", -1)
    ):
        _error(
            "The isolated postprocessor result totals are inconsistent.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )
    progress(89, "Prepared human-authorized CAM program outputs")
    return PreparedPostOutput(frozen=frozen, files=tuple(files), total_size_bytes=total)


def output_requests(prepared: PreparedPostOutput) -> tuple[NativeOutputRequest, ...]:
    if not isinstance(prepared, PreparedPostOutput):
        raise TypeError("prepared must be a PreparedPostOutput")
    return tuple(
        NativeOutputRequest(
            purpose=f"cam_post_{index + 1}",
            title=(
                f"Save CAM Program {index + 1} of {len(prepared.files)}"
                if len(prepared.files) > 1
                else "Save CAM Program"
            ),
            suggested_file_name=value.file_name,
            allowed_suffixes=(value.suffix,),
            name_filter=f"CAM Program (*{value.suffix})",
            maximum_bytes=MAX_POST_OUTPUT_BYTES,
        )
        for index, value in enumerate(prepared.files)
    )


def _copy_exact(source: PreparedPostFile, destination: str) -> None:
    source_size, source_digest = _hash_file(source.path, MAX_POST_OUTPUT_BYTES)
    if source_size != source.size_bytes or source_digest != source.sha256:
        _error(
            "A private CAM program changed before publication.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )
    input_descriptor = os.open(
        source.path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | _BINARY_OPEN,
    )
    output_descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_TRUNC | _BINARY_OPEN,
    )
    try:
        while True:
            chunk = os.read(input_descriptor, 1024 * 1024)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                offset += os.write(output_descriptor, chunk[offset:])
        os.fsync(output_descriptor)
    finally:
        os.close(input_descriptor)
        os.close(output_descriptor)


def _validate_published(source: PreparedPostFile, path: Path) -> None:
    size, digest = _hash_file(path, MAX_POST_OUTPUT_BYTES)
    if size != source.size_bytes or digest != source.sha256:
        _error(
            "A staged CAM program failed exact byte validation.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )


def publish_post(
    prepared: PreparedPostOutput,
    requests: tuple[NativeOutputRequest, ...],
    authorizations: tuple[NativeOutputAuthorization, ...],
    *,
    guard: Callable[[], None],
) -> tuple[NativeOutputArtifact, ...]:
    if requests != output_requests(prepared) or len(authorizations) != len(requests):
        raise TypeError("Every CAM post output requires one human authorization")
    items = []
    for request, authorization, source in zip(
        requests,
        authorizations,
        prepared.files,
        strict=True,
    ):
        items.append(
            NativeOutputBundleItem(
                request=request,
                authorization=authorization,
                writer=lambda path, value=source: _copy_exact(value, path),
                validator=lambda path, value=source: _validate_published(value, path),
                temporary_suffix=source.suffix,
            )
        )
    try:
        return publish_authorized_output_bundle(tuple(items), guard=guard)
    except NativeOutputError as exc:
        raise NativeManufactureError(str(exc), error_code=exc.code) from exc
