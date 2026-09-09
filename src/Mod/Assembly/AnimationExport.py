# SPDX-License-Identifier: LGPL-2.1-or-later
"""Nonblocking frame capture and detached animation-file publication."""
from pathlib import Path
import json
import os
import queue
import shutil
import tempfile
import threading

from PySide import QtCore
from VibeCADNativeBackground import NativeBackgroundCancelled


class AnimationExportError(RuntimeError):
    def failure(self):
        return {'error_code': 'ANIMATION_EXPORT_FAILED', 'message': str(self)}


class AnimationExportJob:
    """Only detached values cross into the existing job supervisor/isolation pool.

    Capture owns the staging directory until finish_capture. The supervisor
    waits outside Qt, then encodes, atomically publishes and removes staging.
    Neither file I/O nor final encoding is dispatched back onto the GUI.
    """
    def __init__(self, manager, *, document_uid, output, frame_count, fps, size,
                 execute, isolation):
        self.manager = manager
        self.output = Path(output)
        self.frame_count = frame_count
        self.size = tuple(size)
        self.staging_ready = queue.Queue(maxsize=1)
        self._capture_done = threading.Event()
        self._capture_error = ''
        self._temporary = None

        def prepare(cancelled, report):
            try:
                # Same volume as the destination: publication is one rename,
                # never a cross-volume copy or a partially overwritten file.
                self._temporary = tempfile.TemporaryDirectory(
                    prefix='.vibecad-animation-', dir=self.output.parent,
                )
                staging = Path(self._temporary.name)
                self.staging_ready.put_nowait(staging)
                report(1, 'Capturing animation frames')
                # Cancellation cannot release staging while native PNG writing
                # still owns it. The controller acknowledges after PNG completion.
                self._capture_done.wait()
                if cancelled():
                    raise NativeBackgroundCancelled()
                if self._capture_error:
                    raise AnimationExportError(self._capture_error)
                report(50, 'Encoding animation on a background worker')
                candidate = staging / ('animation' + self.output.suffix.lower())
                request = {
                    'frames': [str(staging / f'frame_{index:05d}.png')
                               for index in range(frame_count)],
                    'output': str(candidate), 'fps': fps, 'size': self.size,
                }
                (staging / 'animation.json').write_text(json.dumps(request), encoding='utf-8')
                shutil.copyfile(Path(__file__).with_name('AnimationEncoder.py'),
                                staging / 'AnimationEncoder.py')
                result = execute(staging=staging, script='AnimationEncoder.py',
                                 cancellation_check=cancelled, **isolation)
                if result.get('cancelled') or cancelled():
                    raise NativeBackgroundCancelled()
                if result.get('returncode') != 0:
                    raise AnimationExportError(result.get('error') or result.get('stderr')
                                               or 'Animation encoding failed')
                if not candidate.is_file() or candidate.stat().st_size == 0:
                    raise AnimationExportError('The animation encoder produced no file')
                return candidate
            except (AnimationExportError, NativeBackgroundCancelled):
                raise
            except Exception as error:
                raise AnimationExportError(str(error)) from error

        def commit(candidate):
            try:
                os.replace(candidate, self.output)
            except OSError as error:
                raise AnimationExportError(str(error)) from error
            return {'file': str(self.output), 'frames': frame_count}

        def cleanup(_):
            if self._temporary is not None:
                self._temporary.cleanup()

        submitted = manager.submit(
            document_uid=document_uid, capability_name='assembly.animation_export',
            resource_scope='animation-export', prepare=prepare,
            validate_before_commit=lambda: None, commit=commit,
            # This commit mutates only a detached file, never a document or Qt
            # object. Keep it on the supervisor, including the atomic rename.
            dispatch_to_document_thread=lambda callback: callback(),
            finalize_message='Publishing animation file', cleanup=cleanup,
        )
        self.job_id = submitted.job_id

    def finish_capture(self, error=''):
        self._capture_error = str(error)
        self._capture_done.set()


class AnimationExportController(QtCore.QObject):
    progress = QtCore.Signal(int, str)
    finished = QtCore.Signal(object)

    def __init__(self, panel, viewer, job):
        super().__init__()
        self.panel, self.job = panel, job
        # Retain the bound cancellation/completion methods: looking up a new
        # attribute on a deleted viewer is deliberately rejected by its wrapper.
        self.start_png = viewer.startFrameExport
        self.finish_png = viewer.finishFrameExport
        self.cancel_png = viewer.cancelFrameExport
        self.original_frame = panel.form.frameSlider.value()
        self.index = 0
        self.staging = None
        self.frame = None
        self.image = None
        self.restore = None
        self.capture_finished = False
        self.cancelled = False
        self.error = ''
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(16)  # Completion polling cadence, never a deadline.
        self.timer.timeout.connect(self.tick)
        panel.playbackClosed.connect(self.cancel)
        self.timer.start()

    def cancel(self):
        if self.cancelled:
            return
        self.cancelled = True
        self.job.manager.cancel(self.job.job_id)
        if self.frame is not None:
            self.frame.cancel()
            self.frame = None
        if self.image is not None:
            self.cancel_png(self.image)

    def _finish_capture(self):
        if self.capture_finished:
            return
        self.capture_finished = True
        self.job.finish_capture(self.error)
        if self.panel._ownsLiveTaskContext():
            self.restore = self.panel.requestFrameAsync(self.original_frame)

    def tick(self):
        try:
            snapshot = self.job.manager.snapshot(self.job.job_id)
            if snapshot.cancel_requested:
                self.cancel()
            if self.image is not None:
                try:
                    if not self.finish_png(self.image):
                        return
                except Exception:
                    self.image = None
                    raise
                self.image = None
                self.index += 1
            if self.cancelled:
                self._finish_capture()
            if not self.capture_finished:
                if not self.panel._ownsLiveTaskContext():
                    self.cancel()
                    self._finish_capture()
                elif self.staging is None:
                    try:
                        self.staging = self.job.staging_ready.get_nowait()
                    except queue.Empty:
                        pass
                if self.staging is not None and not self.capture_finished:
                    if self.frame is not None:
                        if not self.frame.done():
                            return
                        self.frame.result()
                        self.frame = None
                        self.image = self.start_png(
                            str(self.staging / f'frame_{self.index:05d}.png'), *self.job.size,
                        )
                        return
                    if self.index == self.job.frame_count:
                        self._finish_capture()
                    else:
                        self.progress.emit(self.index, 'Capturing animation frames')
                        self.frame = self.panel.requestFrameAsync(self.index, include_input=True)
                        return
            snapshot = self.job.manager.snapshot(self.job.job_id)
            if snapshot.terminal and not snapshot.worker_active:
                self._finish_capture()
                if self.restore is not None:
                    if not self.restore.done():
                        return
                    restore, self.restore = self.restore, None
                    restore.result()
                self.timer.stop()
                self.panel.playbackClosed.disconnect(self.cancel)
                self.finished.emit(snapshot)
            elif self.capture_finished:
                self.progress.emit(self.job.frame_count, snapshot.progress_message)
        except Exception as error:
            self.error = str(error)
            self.cancel()
            if self.image is None:
                self._finish_capture()
