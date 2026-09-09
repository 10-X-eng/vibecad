# SPDX-License-Identifier: LGPL-2.1-or-later
import importlib.util
from pathlib import Path

from PIL import Image
import pytest


def encoder():
    path = Path(__file__).resolve().parents[2] / 'Assembly' / 'AnimationEncoder.py'
    spec = importlib.util.spec_from_file_location('AnimationEncoder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.encode_animation


def frames(tmp_path):
    paths = []
    for index, color in enumerate(('red', 'green', 'blue')):
        path = tmp_path / f'{index}.png'
        Image.new('RGB', (16, 12), color).save(path)
        paths.append(str(path))
    return paths


def test_gif_roundtrip_preserves_every_frame_and_timing(tmp_path):
    paths = frames(tmp_path)
    destination = tmp_path / 'motion.gif'
    encoder()(paths, destination, 10, (16, 12))
    with Image.open(destination) as result:
        assert result.n_frames == 3
        assert result.info['loop'] == 0
        for index, color in enumerate(((255, 0, 0), (0, 128, 0), (0, 0, 255))):
            result.seek(index)
            assert result.info['duration'] == 100
            assert result.convert('RGB').getpixel((4, 4)) == color


def test_encoder_checks_cancellation_between_frames(tmp_path):
    calls = []
    def check():
        calls.append(True)
        if len(calls) == 3:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        encoder()(frames(tmp_path), tmp_path / 'motion.gif', 10, (16, 12), check=check)
    assert len(calls) == 3


@pytest.mark.parametrize('suffix', ['.gif', '.mp4', '.avi'])
def test_rejects_wrong_frame_dimensions(tmp_path, suffix):
    with pytest.raises(ValueError, match='dimensions'):
        encoder()(frames(tmp_path), tmp_path / ('motion' + suffix), 10, (18, 12))


def test_video_roundtrip_has_all_frames(tmp_path):
    import cv2
    destination = tmp_path / 'motion.mp4'
    encoder()(frames(tmp_path), destination, 10, (16, 12))
    capture = cv2.VideoCapture(str(destination))
    try:
        assert capture.isOpened()
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 3
        for _ in range(3):
            ok, frame = capture.read()
            assert ok and frame.shape[:2] == (12, 16)
    finally:
        capture.release()
