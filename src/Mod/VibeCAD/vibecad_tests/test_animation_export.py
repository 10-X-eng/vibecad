# SPDX-License-Identifier: LGPL-2.1-or-later
import importlib.util
from pathlib import Path
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'VibeCAD'))
from VibeCADNativeBackground import NativeBackgroundManager


def load_export():
    import PySide6
    sys.modules.setdefault('PySide', PySide6)
    path = ROOT / 'Assembly' / 'AnimationExport.py'
    spec = importlib.util.spec_from_file_location('AnimationExport', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.001)
    pytest.fail('Export did not complete')


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'encoder_failure', 'capture_failure'])
def test_export_publication_and_cleanup_are_detached_and_atomic(tmp_path, outcome):
    module = load_export()
    owner = threading.get_ident()
    calls = []
    target = tmp_path / 'movie.gif'
    target.write_bytes(b'original')
    manager = NativeBackgroundManager()
    def execute(**kwargs):
        import json
        assert threading.get_ident() != owner
        calls.append('encode')
        request = json.loads((Path(kwargs['staging']) / 'animation.json').read_text())
        Path(request['output']).write_bytes(b'encoded')
        return {'returncode': 1 if outcome == 'encoder_failure' else 0,
                'cancelled': False, 'error': 'codec failed'}
    job = module.AnimationExportJob(
        manager, document_uid='doc', output=target, frame_count=3,
        fps=10, size=(16, 12), execute=execute, isolation={},
    )
    wait_until(lambda: not job.staging_ready.empty())
    staging = job.staging_ready.get_nowait()
    assert staging.parent == tmp_path
    if outcome == 'cancel':
        manager.cancel(job.job_id)
        # Cancellation must not delete files while native PNG encoding is live.
        time.sleep(.02)
        assert staging.exists()
    job.finish_capture('capture failed' if outcome == 'capture_failure' else '')
    wait_until(lambda: not manager.snapshot(job.job_id).worker_active)
    snapshot = manager.snapshot(job.job_id)
    assert snapshot.phase == ('completed' if outcome == 'success' else
                              'cancelled' if outcome == 'cancel' else 'failed')
    assert target.read_bytes() == (b'encoded' if outcome == 'success' else b'original')
    assert not staging.exists()
    assert calls == ([] if outcome in {'cancel', 'capture_failure'} else ['encode'])
    if outcome == 'encoder_failure':
        assert 'codec failed' in snapshot.error['message']


@pytest.mark.parametrize('close', [False, True])
def test_controller_waits_for_pose_and_png_before_advancing(tmp_path, close):
    from concurrent.futures import Future
    from types import SimpleNamespace
    from PySide6 import QtCore
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    module = load_export()
    calls, pending = [], []
    live = True
    class Panel(QtCore.QObject):
        playbackClosed = QtCore.Signal()
        def _ownsLiveTaskContext(self):
            return live
        def requestFrameAsync(self, frame, **kwargs):
            calls.append(('pose', frame))
            future = Future()
            pending.append(future)
            return future
    panel = Panel()
    panel.form = SimpleNamespace(frameSlider=SimpleNamespace(value=lambda: 2))
    png_ready = False
    viewer = SimpleNamespace(
        startFrameExport=lambda path, w, h: calls.append(('png', Path(path).name)) or 7,
        finishFrameExport=lambda token: png_ready,
        cancelFrameExport=lambda token: calls.append(('cancel png', token)),
    )
    import queue
    stages = queue.Queue()
    stages.put(tmp_path)
    job = SimpleNamespace(
        staging_ready=stages, frame_count=3, size=(16, 12), job_id='job',
        finish_capture=lambda error='': calls.append(('capture done', error)),
        manager=SimpleNamespace(
            snapshot=lambda token: SimpleNamespace(terminal=False, cancel_requested=False,
                                                   progress_message='Cancelled'),
            cancel=lambda token: calls.append(('cancel job', token)),
        ),
    )
    controller = module.AnimationExportController(panel, viewer, job)
    controller.timer.stop()
    controller.tick()
    assert calls == [('pose', 0)]
    controller.tick()
    assert len(calls) == 1
    pending[0].set_result(0)
    controller.tick()
    assert calls[-1] == ('png', 'frame_00000.png')
    controller.tick()
    assert calls[-1][0] == 'png'
    if close:
        live = False
        panel.playbackClosed.emit()
        assert calls[-1] == ('cancel png', 7)
        controller.tick()
        assert not any(call[0] == 'capture done' for call in calls)
    png_ready = True
    controller.tick()
    assert calls[-1] == (('capture done', '') if close else ('pose', 1))
    controller.timer.stop()
    panel.playbackClosed.disconnect(controller.cancel)
