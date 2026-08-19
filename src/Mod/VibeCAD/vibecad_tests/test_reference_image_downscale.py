# SPDX-License-Identifier: LGPL-2.1-or-later

"""downscale_reference_image must honor max_edge even for compact files."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import VibeCADCore as core


class _FakeBuffer:
    def __init__(self) -> None:
        self._data = b""

    def open(self, *_args, **_kwargs) -> bool:
        return True

    def data(self) -> bytes:
        return self._data

    def close(self) -> None:
        return None


class _FakeImage:
    def __init__(self, width: int, height: int, *, null: bool = False) -> None:
        self._width = width
        self._height = height
        self._null = null

    def isNull(self) -> bool:
        return self._null

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def scaled(self, width: int, height: int, *_args, **_kwargs) -> _FakeImage:
        scale = min(width / self._width, height / self._height)
        return _FakeImage(int(self._width * scale), int(self._height * scale))

    def save(self, buffer: _FakeBuffer, _format: str, _quality: int) -> bool:
        buffer._data = f"scaled:{self._width}x{self._height}".encode("ascii")
        return True


class _FakeQtGui:
    def __init__(self, image: _FakeImage) -> None:
        self._image = image

    def QImage(self, path: str) -> _FakeImage:
        try:
            payload = Path(path).read_bytes()
        except OSError:
            return self._image
        if payload.startswith(b"scaled:"):
            width_text, height_text = (
                payload.decode("ascii").split(":", 1)[1].split("x", 1)
            )
            return _FakeImage(int(width_text), int(height_text))
        return self._image


def _fake_qt(image: _FakeImage) -> tuple[SimpleNamespace, _FakeQtGui]:
    qt_core = SimpleNamespace(
        QBuffer=_FakeBuffer,
        QIODevice=SimpleNamespace(WriteOnly=1),
        Qt=SimpleNamespace(KeepAspectRatio=1, SmoothTransformation=2),
    )
    return qt_core, _FakeQtGui(image)


def test_compact_file_with_large_edge_is_downscaled(tmp_path, monkeypatch) -> None:
    path = tmp_path / "wide.png"
    path.write_bytes(b"compact-source")
    image = _FakeImage(3200, 400)
    monkeypatch.setattr(core, "_load_qt_modules", lambda: _fake_qt(image))

    result = core.downscale_reference_image(path, max_edge=1568, max_bytes=2_000_000)

    assert result["downscaled"] is True
    assert result.get("error") is None
    assert path.read_bytes().startswith(b"scaled:")
    width, height = result["image_size"]
    assert max(width, height) <= 1568


def test_compact_already_small_edge_is_not_reencoded(tmp_path, monkeypatch) -> None:
    original = b"already-small"
    path = tmp_path / "small.png"
    path.write_bytes(original)
    image = _FakeImage(800, 600)
    monkeypatch.setattr(core, "_load_qt_modules", lambda: _fake_qt(image))

    result = core.downscale_reference_image(path, max_edge=1568, max_bytes=2_000_000)

    assert result["downscaled"] is False
    assert path.read_bytes() == original
    assert result["image_size"] == [800, 600]
