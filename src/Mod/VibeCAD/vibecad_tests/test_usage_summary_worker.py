# SPDX-License-Identifier: LGPL-2.1-or-later
"""Streaming summary work stays bounded and can be discarded without joining."""
import threading

import VibeCADTokenUsage as usage


def test_worker_coalesces_pending_updates_and_discards_superseded_results():
    entered = threading.Event()
    release = threading.Event()
    published = threading.Event()
    calls, results = [], []

    def compute(history, active):
        calls.append(active)
        if active == 0:
            entered.set()
            assert release.wait(3)
        return active

    worker = usage.UsageSummaryWorker(
        lambda generation, result: (results.append((generation, result)), published.set()),
        compute=compute,
    )
    try:
        worker.submit([], 0)
        assert entered.wait(3)
        for value in range(1, 101):
            last = worker.submit([], value)
        release.set()
        assert published.wait(3)
        assert calls == [0, 100]
        assert results == [(last, 100)]
        assert worker.is_current(last)
        worker.invalidate()
        assert not worker.is_current(last)
    finally:
        release.set()
        worker.close()


def test_closing_worker_does_not_wait_for_calculation_or_publish_after_close():
    entered, release, finished = (threading.Event() for _ in range(3))
    results = []

    def compute(history, active):
        entered.set()
        assert release.wait(3)
        finished.set()
        return active

    worker = usage.UsageSummaryWorker(lambda *args: results.append(args), compute=compute)
    try:
        generation = worker.submit([], {})
        assert entered.wait(3)
        worker.close()
        assert not finished.is_set()
        assert not worker.is_current(generation)
        release.set()
        assert finished.wait(3)
        worker._thread.join(3)
        assert not worker._thread.is_alive()
        assert results == []
    finally:
        release.set()
        worker.close()


def test_snapshot_preserves_history_and_active_usage_without_mutating_inputs():
    import copy
    accumulator = usage.TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    accumulator.set_thread_id("thread-1")
    accumulator.set_active_turn("turn-1")
    accumulator.observe({"threadId": "thread-1", "turnId": "turn-1",
                         "tokenUsage": {"last": {"inputTokens": 10, "totalTokens": 12}}})
    active = accumulator.metadata()
    history = []
    before = copy.deepcopy(active)
    rendered = usage.render_usage_snapshot(history, active)
    assert history == []
    assert active == before
    assert "total 12" in rendered["text"]
    assert rendered["graph"]["rows"][0]["counts"]["total_tokens"] == 12
