# SPDX-License-Identifier: LGPL-2.1-or-later
import json
from pathlib import Path
import threading

import pytest

from async_report_writer import AsyncReportWriter


def test_blocked_disk_does_not_block_submit_and_pending_reports_coalesce(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    writes = []
    owner = threading.get_ident()

    def blocked_write(path, text, **kwargs):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5), 'Test did not release the simulated blocked disk'
        writes.append(json.loads(text))

    monkeypatch.setattr(Path, 'write_text', blocked_write)
    writer = AsyncReportWriter(tmp_path / 'trace.json')
    try:
        first = writer.submit({'value': 1})
        assert entered.wait(5)
        second = writer.submit({'value': 2})
        final = writer.submit({'value': 3})
        assert second.cancelled()
        assert not first.done() and not final.done()
        release.set()
        first.result(timeout=5)
        final.result(timeout=5)
        assert writes == [{'value': 1}, {'value': 3}]
    finally:
        release.set()
        writer.close()


def test_write_error_reaches_completion_and_writer_accepts_next_report(monkeypatch, tmp_path):
    original = Path.write_text
    monkeypatch.setattr(Path, 'write_text', lambda *a, **k: (_ for _ in ()).throw(OSError('disk error')))
    writer = AsyncReportWriter(tmp_path / 'trace.json')
    try:
        with pytest.raises(OSError, match='disk error'):
            writer.submit({'ok': False}).result(timeout=5)
        monkeypatch.setattr(Path, 'write_text', original)
        writer.submit({'ok': True}).result(timeout=5)
        assert json.loads((tmp_path / 'trace.json').read_text()) == {'ok': True}
    finally:
        writer.close()
    with pytest.raises(RuntimeError, match='closed'):
        writer.submit({})
