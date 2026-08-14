# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated offscreen GUI child for one exact TechDraw Draft view."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping
import xml.etree.ElementTree as ET


_REQUEST_ENV = "VIBECAD_NATIVE_DRAWING_DRAFT_REQUEST"
_PROTOCOL = "vibecad-native-drawing-draft-v1"
_MAX_REQUEST_BYTES = 512 * 1024
_MAX_RESULT_BYTES = 1024 * 1024
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_SYMBOL_BYTES = 32 * 1024 * 1024
_MAX_MEMBERS = 256
_MAX_MEMBER_STATE_BYTES = 512 * 1024 * 1024
_MAX_SVG_ELEMENTS = 200_000
_DRAWABLES = {
    "circle",
    "ellipse",
    "image",
    "line",
    "path",
    "polygon",
    "polyline",
    "rect",
    "text",
}
_FORBIDDEN = {"embed", "foreignObject", "iframe", "object", "script"}


class _ChildFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise _ChildFailure(code, message)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "An input escaped its private workspace.")
    try:
        value = path.lstat()
    except OSError:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Draft rendering input is unavailable.")
    if not stat.S_ISREG(value.st_mode):
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Draft rendering input is not a regular file.")
    descriptor = -1
    data = bytearray()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Draft rendering input changed while opening.")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail("NATIVE_DRAWING_DRAFT_LIMIT", "A Draft rendering input exceeds its safety bound.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Draft rendering input is empty.")
    return bytes(data)


def _write_private(path: Path, data: bytes, maximum: int) -> None:
    if not data or len(data) > maximum:
        _fail("NATIVE_DRAWING_DRAFT_LIMIT", "A Draft rendering output exceeds its safety bound.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _request() -> tuple[dict[str, Any], Path, Path, str]:
    path_text = str(os.environ.get(_REQUEST_ENV) or "").strip()
    if not path_text:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft rendering request is unavailable.")
    path = Path(path_text)
    root = path.parent.resolve()
    data = _read_regular(path, root=root, maximum=_MAX_REQUEST_BYTES)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft rendering request is unreadable.")
    required = {
        "protocol",
        "workspace",
        "snapshot",
        "snapshot_bytes",
        "snapshot_sha256",
        "page",
        "source",
        "render",
        "symbol",
        "result",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft rendering request is malformed.")
    if (
        str(value["protocol"]) != _PROTOCOL
        or Path(str(value["workspace"])).resolve() != root
        or str(value["snapshot"]) != "document.FCStd"
        or str(value["symbol"]) != "outputs/draft-view.svg"
        or str(value["result"]) != "result.json"
    ):
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft rendering request identity is invalid.")
    return dict(value), root, root / "result.json", hashlib.sha256(data).hexdigest()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object_descriptor(value: Any) -> dict[str, Any]:
    required = {"object_id", "object_name", "type_id", "state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Drawing object descriptor is malformed.")
    result = dict(value)
    if (
        type(result["object_id"]) is not int
        or int(result["object_id"]) < 0
        or not str(result["object_name"] or "")
        or not str(result["type_id"] or "")
        or len(str(result["state_sha256"] or "")) != 64
    ):
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Drawing object descriptor is invalid.")
    return result


def _member_descriptor(value: Any) -> dict[str, Any]:
    required = {
        "object_id",
        "object_name",
        "type_id",
        "app_bytes",
        "app_sha256",
        "view_bytes",
        "view_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Draft source member is malformed.")
    result = dict(value)
    view_bytes = result["view_bytes"]
    view_sha = result["view_sha256"]
    if (
        type(result["object_id"]) is not int
        or int(result["object_id"]) < 0
        or not str(result["object_name"] or "")
        or not str(result["type_id"] or "")
        or type(result["app_bytes"]) is not int
        or not 0 < int(result["app_bytes"]) <= _MAX_MEMBER_STATE_BYTES
        or len(str(result["app_sha256"] or "")) != 64
        or (
            (view_bytes is None or view_sha is None)
            and not (view_bytes is None and view_sha is None)
        )
        or (
            view_bytes is not None
            and (
                type(view_bytes) is not int
                or not 0 < int(view_bytes) <= _MAX_MEMBER_STATE_BYTES
                or len(str(view_sha or "")) != 64
            )
        )
    ):
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "A Draft source member is invalid.")
    return result


def _source_descriptor(value: Any) -> dict[str, Any]:
    required = {"object_id", "object_name", "type_id", "state_sha256", "members"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft source descriptor is malformed.")
    identity = _object_descriptor({name: value[name] for name in required if name != "members"})
    members = value["members"]
    if not isinstance(members, list) or not 1 <= len(members) <= _MAX_MEMBERS:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft source member graph is invalid.")
    result = dict(identity)
    result["members"] = [_member_descriptor(member) for member in members]
    return result


def _render_descriptor(value: Any) -> dict[str, Any]:
    required = {
        "direction",
        "scale",
        "line_width",
        "font_size",
        "color_rgb",
        "line_style",
        "line_spacing",
        "override",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft rendering settings are malformed.")
    result = dict(value)
    direction = result["direction"]
    color = result["color_rgb"]
    numbers = [result[name] for name in ("scale", "line_width", "font_size", "line_spacing")]
    if (
        not isinstance(direction, list)
        or len(direction) != 3
        or any(type(item) not in {int, float} or not math.isfinite(float(item)) for item in direction)
        or math.sqrt(sum(float(item) ** 2 for item in direction)) <= 1.0e-12
        or any(type(item) not in {int, float} or not math.isfinite(float(item)) or float(item) <= 0 for item in numbers)
        or not isinstance(color, list)
        or len(color) != 3
        or any(type(item) is not int or not 0 <= item <= 255 for item in color)
        or str(result["line_style"]) not in {"Solid", "Dashed", "Dashdot", "Dot"}
        or type(result["override"]) is not bool
    ):
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The Draft rendering settings are invalid.")
    return result


def _source_members(source: Any) -> tuple[Any, ...]:
    values = (source, *(tuple(getattr(source, "OutListRecursive", ()) or ())))
    unique = {
        (int(obj.ID), str(obj.Name), str(obj.TypeId)): obj
        for obj in values
        if obj is not None
    }
    if not 1 <= len(unique) <= _MAX_MEMBERS:
        _fail("NATIVE_DRAWING_DRAFT_SOURCE_INVALID", "The restored Draft source graph is out of bounds.")
    return tuple(unique[key] for key in sorted(unique))


def _authenticate_source(source: Any, expected: Mapping[str, Any]) -> None:
    members = []
    total = 0
    for obj in _source_members(source):
        app = bytes(obj.dumpContent(9))
        view = getattr(obj, "ViewObject", None)
        view_data = bytes(view.dumpContent(9)) if view is not None else None
        total += len(app) + len(view_data or b"")
        if total > _MAX_MEMBER_STATE_BYTES:
            _fail("NATIVE_DRAWING_DRAFT_LIMIT", "The restored Draft source state is too large.")
        members.append(
            (
                int(obj.ID),
                str(obj.Name),
                str(obj.TypeId),
                view_data is not None,
            )
        )
    wanted = [
        (
            int(member["object_id"]),
            str(member["object_name"]),
            str(member["type_id"]),
            member["view_bytes"] is not None,
        )
        for member in expected["members"]
    ]
    if members != wanted:
        _fail(
            "NATIVE_DRAWING_DRAFT_SOURCE_STALE",
            "The authenticated snapshot restored a different Draft source graph.",
        )


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _validate_svg(data: bytes) -> tuple[int, int]:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _fail("NATIVE_DRAWING_DRAFT_OUTPUT_INVALID", "The generated Draft SVG contains declarations.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        _fail("NATIVE_DRAWING_DRAFT_OUTPUT_INVALID", "The generated Draft SVG is not valid XML.")
    if _local_name(root.tag) != "svg":
        _fail("NATIVE_DRAWING_DRAFT_OUTPUT_INVALID", "The generated Draft output is not an SVG document.")
    count = 0
    drawables = 0
    for element in root.iter():
        count += 1
        if count > _MAX_SVG_ELEMENTS:
            _fail("NATIVE_DRAWING_DRAFT_LIMIT", "The generated Draft SVG has too many elements.")
        name = _local_name(element.tag)
        if name in _FORBIDDEN:
            _fail("NATIVE_DRAWING_DRAFT_OUTPUT_INVALID", "The generated Draft SVG contains active content.")
        drawables += int(name in _DRAWABLES)
        for attribute, raw in element.attrib.items():
            value = str(raw).strip()
            attr = _local_name(attribute).lower()
            if attr == "href" and value and not (
                value.startswith("#") or value.startswith("data:image/")
            ):
                _fail("NATIVE_DRAWING_DRAFT_OUTPUT_INVALID", "The generated Draft SVG references an external resource.")
            lowered = value.lower().replace(" ", "")
            if "url(" in lowered and "url(#" not in lowered:
                _fail("NATIVE_DRAWING_DRAFT_OUTPUT_INVALID", "The generated Draft SVG contains an external URL.")
    if drawables < 1:
        _fail("NATIVE_DRAWING_DRAFT_RENDER_FAILED", "The exact Draft source produced no drawable SVG geometry.")
    return count, drawables


def _execute(request: Mapping[str, Any], root: Path, request_sha256: str) -> dict[str, Any]:
    import FreeCAD as App

    snapshot = root / str(request["snapshot"])
    data = _read_regular(snapshot, root=root, maximum=_MAX_SNAPSHOT_BYTES)
    if (
        type(request["snapshot_bytes"]) is not int
        or int(request["snapshot_bytes"]) != len(data)
        or str(request["snapshot_sha256"]) != _sha(data)
    ):
        _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The exact Drawing snapshot failed authentication.")
    page_descriptor = _object_descriptor(request["page"])
    source_descriptor = _source_descriptor(request["source"])
    render = _render_descriptor(request["render"])
    document = App.openDocument(str(snapshot), True)
    try:
        page = document.getObject(str(page_descriptor["object_name"]))
        source = document.getObject(str(source_descriptor["object_name"]))
        if (
            page is None
            or source is None
            or int(page.ID) != int(page_descriptor["object_id"])
            or str(page.TypeId) != str(page_descriptor["type_id"])
            or int(source.ID) != int(source_descriptor["object_id"])
            or str(source.TypeId) != str(source_descriptor["type_id"])
        ):
            _fail("NATIVE_DRAWING_DRAFT_CHILD_INVALID", "The exact Drawing objects were not restored.")
        _authenticate_source(source, source_descriptor)
        import Draft

        direction = App.Vector(*(float(value) for value in render["direction"]))
        color = "#" + "".join(f"{int(value):02x}" for value in render["color_rgb"]) + "ff"
        body = Draft.get_svg(
            source,
            scale=float(render["scale"]),
            linewidth=float(render["line_width"]),
            fontsize=float(render["font_size"]),
            direction=direction,
            linestyle=str(render["line_style"]),
            color=color,
            linespacing=float(render["line_spacing"]),
            techdraw=True,
            override=bool(render["override"]),
        )
        symbol = (
            '<svg\n\txmlns="http://www.w3.org/2000/svg" version="1.1"\n'
            '\txmlns:freecad="https://www.freecad.org/wiki/index.php?title=Svg_Namespace">\n'
            + str(body)
            + "\n</svg>"
        ).encode("utf-8")
        if len(symbol) > _MAX_SYMBOL_BYTES:
            _fail("NATIVE_DRAWING_DRAFT_LIMIT", "The generated Draft SVG exceeds 32 MiB.")
        element_count, drawable_count = _validate_svg(symbol)
        outputs = root / "outputs"
        outputs.mkdir(mode=0o700)
        symbol_path = outputs / "draft-view.svg"
        _write_private(symbol_path, symbol, _MAX_SYMBOL_BYTES)
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "page_name": str(page.Name),
            "source_name": str(source.Name),
            "source_state_sha256": str(source_descriptor["state_sha256"]),
            "symbol": {
                "artifact": "outputs/draft-view.svg",
                "artifact_bytes": len(symbol),
                "artifact_sha256": _sha(symbol),
                "element_count": element_count,
                "drawable_count": drawable_count,
            },
        }
    finally:
        App.closeDocument(document.Name)


def _main() -> int:
    import FreeCADGui as Gui

    Gui.showMainWindow()
    result_path: Path | None = None
    request_sha256 = ""
    try:
        request, root, result_path, request_sha256 = _request()
        result = _execute(request, root, request_sha256)
        _write_private(
            result_path,
            json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            _MAX_RESULT_BYTES,
        )
        return 0
    except _ChildFailure as exc:
        failure = {
            "ok": False,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "error_code": exc.code,
            "message": str(exc)[:320],
        }
    except Exception:
        failure = {
            "ok": False,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "error_code": "NATIVE_DRAWING_DRAFT_EXECUTION_FAILED",
            "message": "The isolated Drawing Draft process failed.",
        }
    if result_path is not None and not result_path.exists():
        try:
            _write_private(
                result_path,
                json.dumps(failure, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                _MAX_RESULT_BYTES,
            )
        except Exception:
            pass
    return 1


if __name__ in {"__main__", "child"}:
    # This one-shot worker has already fsynced every authenticated output. A
    # direct process exit deliberately skips Qt/MDI destruction: the isolated
    # offscreen GUI owns no user state, and FreeCADCmd teardown can otherwise
    # dereference Python after interpreter finalization.
    os._exit(_main())
