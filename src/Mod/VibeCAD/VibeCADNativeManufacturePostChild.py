# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fixed FreeCADCmd child for isolated, bounded CAM post generation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


_REQUEST_ENV = "VIBECAD_NATIVE_POST_REQUEST"
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_OPERATIONS = 64
_MAX_OUTPUTS = 64
_MAX_OUTPUT_BYTES = 256 * 1024 * 1024
_MAX_TOTAL_OUTPUT_BYTES = 1024 * 1024 * 1024
_BINARY_OPEN = getattr(os, "O_BINARY", 0)
_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,16}$")


class _ChildFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise _ChildFailure(code, message)


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _regular(path: Path) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError:
        _fail("NATIVE_MANUFACTURE_POST_CHILD_INVALID", "A frozen input file is unavailable.")
    if not stat.S_ISREG(value.st_mode):
        _fail("NATIVE_MANUFACTURE_POST_CHILD_INVALID", "A frozen input is not a regular file.")
    return value


def _hash(path: Path, maximum: int) -> tuple[int, str]:
    value = _regular(path)
    size = 0
    digest = hashlib.sha256()
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | _BINARY_OPEN,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            _fail(
                "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                "A frozen input changed before it was opened.",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                _fail(
                    "NATIVE_MANUFACTURE_POST_LIMIT",
                    "A frozen input exceeds its postprocessing safety bound.",
                )
            digest.update(chunk)
    except _ChildFailure:
        raise
    except OSError:
        _fail("NATIVE_MANUFACTURE_POST_CHILD_INVALID", "A frozen input could not be read.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if size <= 0:
        _fail("NATIVE_MANUFACTURE_POST_CHILD_INVALID", "A frozen input is empty.")
    return size, digest.hexdigest()


def _read_request(path: Path) -> dict[str, Any]:
    value = _regular(path)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | _BINARY_OPEN,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            raise OSError("request identity changed")
        data = bytearray()
        while True:
            chunk = os.read(descriptor, 16 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > _MAX_REQUEST_BYTES:
                raise ValueError("request exceeds metadata bound")
        if not data:
            raise ValueError("request is empty")
        request = json.loads(bytes(data).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("NATIVE_MANUFACTURE_POST_CHILD_INVALID", "The frozen post request is unreadable.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    required = {
        "workspace",
        "snapshot",
        "snapshot_sha256",
        "snapshot_size",
        "job_name",
        "job_state_sha256",
        "operation_variant",
        "selected_operations",
        "use_machine_flow",
        "machine_name",
        "machine_config",
        "machine_config_sha256",
        "postprocessor_name",
        "postprocessor_source",
        "postprocessor_sha256",
        "child_sha256",
        "configured_output",
        "output_directory",
        "result",
    }
    if not isinstance(request, Mapping) or set(request) != required:
        _fail("NATIVE_MANUFACTURE_POST_CHILD_INVALID", "The frozen post request is malformed.")
    return dict(request)


def _read_source(path: Path, maximum: int) -> tuple[bytes, str]:
    value = _regular(path)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | _BINARY_OPEN,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            _fail(
                "NATIVE_MANUFACTURE_POST_PROCESSOR_CHANGED",
                "The configured postprocessor changed before it was opened.",
            )
        data = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail(
                    "NATIVE_MANUFACTURE_POST_LIMIT",
                    "The configured postprocessor source exceeds its safety bound.",
                )
    except _ChildFailure:
        raise
    except OSError:
        _fail(
            "NATIVE_MANUFACTURE_POST_PROCESSOR_CHANGED",
            "The configured postprocessor could not be read safely.",
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail(
            "NATIVE_MANUFACTURE_POST_PROCESSOR_CHANGED",
            "The configured postprocessor source is empty.",
        )
    encoded = bytes(data)
    return encoded, hashlib.sha256(encoded).hexdigest()


def _output_bytes(gcode: str) -> bytes:
    if "\0" in gcode:
        _fail(
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            "The configured postprocessor returned a NUL character.",
        )
    if gcode.startswith("\n\n"):
        normalized = gcode[2:]
    elif "\r" in gcode or os.linesep == "\n":
        normalized = gcode
    else:
        normalized = gcode.replace("\n", os.linesep)
    try:
        return normalized.encode("utf-8")
    except UnicodeEncodeError:
        _fail(
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            "The configured postprocessor returned non-UTF-8-compatible text.",
        )
    raise AssertionError("unreachable")


def _safe_file_name(value: str) -> tuple[str, str]:
    name = Path(str(value or "")).name
    suffix = Path(name).suffix.casefold()
    if (
        not name
        or name in {".", ".."}
        or len(name) > 255
        or any(ord(character) < 32 for character in name)
        or not _SAFE_EXTENSION.fullmatch(suffix)
    ):
        _fail(
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
            "The configured output naming pattern produced an invalid file name.",
        )
    return name, suffix


def _generate(request: Mapping[str, Any]) -> dict[str, Any]:
    import FreeCAD
    from Machine.models.machine import MachineFactory
    from Path.Post.Processor import PostProcessorFactory, WrapperPost
    from Path.Post.Utils import FilenameGenerator
    from VibeCADNativeManufactureState import (
        job_state,
        operation_active_state,
        operation_state,
    )

    workspace = Path(str(request["workspace"])).resolve(strict=True)
    snapshot = Path(str(request["snapshot"])).resolve(strict=True)
    output_directory = Path(str(request["output_directory"])).resolve(strict=True)
    result_path = Path(str(request["result"])).resolve(strict=False)
    if not all(
        _inside(path, workspace)
        for path in (snapshot, output_directory, result_path)
    ):
        _fail(
            "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
            "The isolated post request escaped its private workspace.",
        )
    snapshot_size, snapshot_sha = _hash(snapshot, 4 * 1024 * 1024 * 1024)
    if (
        snapshot_size != int(request["snapshot_size"])
        or snapshot_sha != str(request["snapshot_sha256"])
    ):
        _fail(
            "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
            "The private CAM document snapshot changed before processing.",
        )
    child_path = Path(__file__).resolve(strict=True)
    _child_size, child_sha = _hash(child_path, 16 * 1024 * 1024)
    if child_sha != str(request["child_sha256"]):
        _fail(
            "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
            "The fixed CAM post child changed before execution.",
        )
    source = Path(str(request["postprocessor_source"])).resolve(strict=True)
    source_bytes, source_sha = _read_source(source, 16 * 1024 * 1024)
    if source_sha != str(request["postprocessor_sha256"]):
        _fail(
            "NATIVE_MANUFACTURE_POST_PROCESSOR_CHANGED",
            "The configured postprocessor changed before isolated execution.",
        )
    post_name = str(request["postprocessor_name"] or "")
    if not PostProcessorFactory.is_modern_post_processor_source(
        source_bytes,
        post_name,
        module_path=str(source),
    ):
        _fail(
            "NATIVE_MANUFACTURE_POST_PROCESSOR_UNSUPPORTED",
            "The configured postprocessor is not a modern class-based processor.",
        )

    use_machine = bool(request["use_machine_flow"])
    if use_machine:
        machine_path = Path(str(request["machine_config"])).resolve(strict=True)
        if not _inside(machine_path, workspace):
            _fail(
                "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                "The frozen machine configuration escaped the private workspace.",
            )
        _machine_size, machine_sha = _hash(machine_path, 16 * 1024 * 1024)
        if machine_sha != str(request["machine_config_sha256"]):
            _fail(
                "NATIVE_MANUFACTURE_POST_MACHINE_INVALID",
                "The frozen machine configuration changed before processing.",
            )
        MachineFactory.set_config_directory(machine_path.parent)
    elif request["machine_config"] is not None or request["machine_config_sha256"] is not None:
        _fail(
            "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
            "A non-machine post request contained machine configuration data.",
        )

    document = None
    try:
        document = FreeCAD.openDocument(str(snapshot))
        job = document.getObject(str(request["job_name"] or ""))
        if job is None:
            _fail(
                "NATIVE_MANUFACTURE_POST_SNAPSHOT_INVALID",
                "The exact CAM Job is missing from the private snapshot.",
            )
        current = job_state(job)
        if current.get("state_sha256") != str(request["job_state_sha256"]):
            _fail(
                "NATIVE_MANUFACTURE_POST_SNAPSHOT_INVALID",
                "The exact CAM Job did not survive private snapshot restoration.",
            )
        operation_variant = str(request["operation_variant"] or "")
        selected_values = request["selected_operations"]
        if operation_variant not in {"complete_job", "selected_operations"}:
            _fail(
                "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                "The isolated post request has an invalid operation variant.",
            )
        if not isinstance(selected_values, list) or (
            operation_variant == "complete_job" and selected_values
        ):
            _fail(
                "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                "The isolated complete-Job request contains selected operations.",
            )
        if operation_variant == "selected_operations" and not (
            1 <= len(selected_values) <= _MAX_OPERATIONS
        ):
            _fail(
                "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                "The isolated selected-operation request has an invalid target count.",
            )
        group = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
        positions = {id(operation): index for index, operation in enumerate(group)}
        if len(positions) != len(group):
            _fail(
                "NATIVE_MANUFACTURE_POST_SNAPSHOT_INVALID",
                "The private snapshot contains duplicate CAM operation identities.",
            )
        selected_operations = []
        selected_positions = []
        for value in selected_values:
            if not isinstance(value, Mapping) or set(value) != {
                "object_name",
                "expected_state_sha256",
            }:
                _fail(
                    "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                    "A frozen selected CAM operation target is malformed.",
                )
            name = str(value.get("object_name") or "")
            expected = str(value.get("expected_state_sha256") or "")
            operation = document.getObject(name) if name else None
            if operation is None or id(operation) not in positions:
                _fail(
                    "NATIVE_MANUFACTURE_POST_SNAPSHOT_INVALID",
                    "A selected CAM operation is not a direct member of the restored Job.",
                )
            if (
                operation_state(operation).get("state_sha256") != expected
                or not operation_active_state(operation)
                or not tuple(
                    getattr(getattr(operation, "Path", None), "Commands", ()) or ()
                )
            ):
                _fail(
                    "NATIVE_MANUFACTURE_POST_SNAPSHOT_INVALID",
                    "A selected CAM operation did not survive private snapshot restoration.",
                )
            selected_operations.append(operation)
            selected_positions.append(positions[id(operation)])
        if len(set(selected_positions)) != len(selected_positions) or tuple(
            selected_positions
        ) != tuple(sorted(selected_positions)):
            _fail(
                "NATIVE_MANUFACTURE_POST_SNAPSHOT_INVALID",
                "Selected CAM operations are not distinct and in current Job order.",
            )
        if use_machine and str(getattr(job, "Machine", "") or "") != str(
            request["machine_name"] or ""
        ):
            _fail(
                "NATIVE_MANUFACTURE_POST_MACHINE_INVALID",
                "The private snapshot does not retain the exact configured machine.",
            )
        processor = PostProcessorFactory.get_post_processor_from_source(
            job,
            post_name,
            str(source),
            source_bytes,
            operations=(
                selected_operations
                if operation_variant == "selected_operations"
                else None
            ),
        )
        if processor is None or isinstance(processor, WrapperPost):
            _fail(
                "NATIVE_MANUFACTURE_POST_PROCESSOR_UNSUPPORTED",
                "The configured postprocessor cannot run in the isolated Native pipeline.",
            )
        processor.remote_post = lambda _sections: None
        if use_machine:
            processor._dialog_handled = True
            post_data = processor.export2()
            extension = processor.get_file_extension()
        else:
            post_data = processor.export()
            extension = None
        if not post_data or not isinstance(post_data, (list, tuple)):
            _fail(
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
                "The configured postprocessor returned no program output.",
            )
        if len(post_data) > _MAX_OUTPUTS:
            _fail(
                "NATIVE_MANUFACTURE_POST_LIMIT",
                "The configured postprocessor returned more than 64 output sections.",
            )
        generator = FilenameGenerator(
            job=job,
            file_extension=extension,
            output_file=(str(request["configured_output"]) or None),
        )
        generated_names = generator.generate_filenames()
        outputs = []
        total = 0
        seen_names = set()
        for index, item in enumerate(post_data):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                _fail(
                    "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
                    "The configured postprocessor returned a malformed output section.",
                )
            section, gcode = item
            if gcode is None:
                continue
            if not isinstance(gcode, str):
                _fail(
                    "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
                    "The configured postprocessor returned non-text program output.",
                )
            data = _output_bytes(gcode)
            if not data:
                _fail(
                    "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
                    "The configured postprocessor returned an empty output section.",
                )
            if len(data) > _MAX_OUTPUT_BYTES:
                _fail(
                    "NATIVE_MANUFACTURE_POST_LIMIT",
                    "One postprocessor output exceeds the 256 MiB safety bound.",
                )
            total += len(data)
            if total > _MAX_TOTAL_OUTPUT_BYTES:
                _fail(
                    "NATIVE_MANUFACTURE_POST_LIMIT",
                    "Postprocessor output exceeds the 1 GiB total safety bound.",
                )
            subpart = "" if str(section) == "allitems" else str(section or "")
            if len(subpart) > 160 or any(ord(character) < 32 for character in subpart):
                _fail(
                    "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
                    "The configured postprocessor returned an invalid section name.",
                )
            generator.set_subpartname(subpart)
            file_name, suffix = _safe_file_name(next(generated_names))
            folded_name = os.path.normcase(file_name)
            if folded_name in seen_names:
                _fail(
                    "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
                    "The configured output naming pattern produced duplicate file names.",
                )
            seen_names.add(folded_name)
            private_name = f"output-{index:02d}.bin"
            private_path = output_directory / private_name
            descriptor = os.open(
                private_path,
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
            outputs.append(
                {
                    "file_name": file_name,
                    "suffix": suffix,
                    "section": subpart[:160],
                    "relative_path": private_name,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        if not outputs:
            _fail(
                "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
                "The configured postprocessor produced no publishable output file.",
            )
        return {
            "ok": True,
            "postprocessor": post_name,
            "machine_configured": use_machine,
            "operation_variant": operation_variant,
            "selected_operation_count": len(selected_operations),
            "outputs": outputs,
            "output_count": len(outputs),
            "total_size_bytes": total,
        }
    finally:
        if document is not None:
            try:
                FreeCAD.closeDocument(document.Name)
            except Exception:
                pass


def _write_result(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_REQUEST_BYTES:
        encoded = json.dumps(
            {
                "ok": False,
                "error_code": "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                "message": "The isolated post result exceeded its metadata bound.",
            },
            separators=(",", ":"),
        ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY_OPEN,
        0o600,
    )
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _main() -> None:
    request_path = Path(str(os.environ.get(_REQUEST_ENV, ""))).resolve(strict=True)
    request = None
    result_path = None
    try:
        request = _read_request(request_path)
        workspace = Path(str(request["workspace"])).resolve(strict=True)
        result_path = Path(str(request["result"])).resolve(strict=False)
        if not _inside(request_path, workspace) or not _inside(result_path, workspace):
            _fail(
                "NATIVE_MANUFACTURE_POST_CHILD_INVALID",
                "The isolated post control files escaped their private workspace.",
            )
        result = _generate(request)
    except _ChildFailure as exc:
        result = {"ok": False, "error_code": exc.code, "message": str(exc)}
    except Exception:
        result = {
            "ok": False,
            "error_code": "NATIVE_MANUFACTURE_POST_EXECUTION_FAILED",
            "message": "The configured postprocessor failed in the isolated process.",
        }
    if result_path is not None:
        _write_result(result_path, result)


if os.environ.get(_REQUEST_ENV):
    _main()
