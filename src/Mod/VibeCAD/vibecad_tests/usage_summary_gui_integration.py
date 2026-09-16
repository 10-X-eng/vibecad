# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live Qt coverage for bounded usage rendering with long histories."""

import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide import QtCore, QtWidgets
import VibeCADGui as gui
from VibeCADTokenUsage import TokenUsageAccumulator

class TestUsageSummaryGui(unittest.TestCase):
    def test_real_widgets_long_history_toggle_and_graph(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        output = QtWidgets.QTextBrowser(panel)
        output.setObjectName("VibeConversation")
        toggle = QtWidgets.QToolButton(panel)
        toggle.setObjectName("VibeUsageSummaryToggle")
        toggle.setCheckable(True)
        scroll = gui._make_usage_summary_widget(panel)
        details = scroll.findChild(QtWidgets.QLabel, "VibeUsageSummaryDetails")
        for widget in (toggle, scroll, output):
            layout.addWidget(widget)
        panel.resize(900, 800)
        panel.show()
        self.addCleanup(panel.close)
        accumulator = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
        accumulator.set_thread_id("smoke-thread")
        accumulator.set_model("reported-model", source="transport")
        entries=[]
        for i in range(1000):
            accumulator.set_active_turn(f"turn-{i}")
            accumulator.observe({"threadId": "smoke-thread", "turnId": f"turn-{i}",
                "tokenUsage": {"last": {"inputTokens": 100, "cachedInputTokens": 20,
                    "outputTokens": 10, "reasoningOutputTokens": 2, "totalTokens": 110}}})
            entries.append({"role": "assistant", "sequence": i+1, "content": "Synthetic reply",
                            "metadata": {"usage": accumulator.metadata(status="completed")}})
        output.setProperty("VibeConversationEntries", entries)
        original = gui._conversation_usage_entries
        with patch.object(gui, "_conversation_usage_entries", wraps=original) as reads:
            start=time.perf_counter()
            for i in range(100):
                gui._render_usage_summary(panel)
            collapsed=time.perf_counter()-start
            self.assertEqual(reads.call_count, 0)
            toggle.setChecked(True)
            start=time.perf_counter()
            gui._render_usage_summary(panel)
            expanded=time.perf_counter()-start
            self.assertEqual(reads.call_count, 1)
        graph=panel.findChild(QtWidgets.QWidget, "VibeUsageGraph")
        self.assertIsNotNone(graph)
        self.assertFalse(graph.isHidden())
        self.assertFalse(details.isHidden())
        self.assertFalse(scroll.isHidden())
        self.assertLessEqual(scroll.height(), 280)
        self.assertIn("reported-model", details.text())
        self.assertIn("110,000", details.text())
        saved_turns = scroll.findChild(QtWidgets.QLabel, "VibeUsageTurnDetails")
        self.assertIsNotNone(saved_turns)
        self.assertIn("Turn 1000", saved_turns.text())
        QtWidgets.QApplication.processEvents()
        self.assertLessEqual(panel.height(), 1000, "Usage must not expand the conversation window")
        screenshot=panel.grab()
        self.assertFalse(screenshot.isNull())
        artifact_dir = os.environ.get("VIBECAD_TEST_OUTPUT")
        if artifact_dir:
            screenshot.save(str(Path(artifact_dir) / "usage-graph.png"))
        # Real calculations and queued publication with a large history.
        accumulator.set_active_turn("streamed-turn")
        accumulator.observe({"threadId": "smoke-thread", "turnId": "streamed-turn",
            "tokenUsage": {"last": {"inputTokens": 100, "outputTokens": 10,
                                     "totalTokens": 110}}})
        history_text = saved_turns.text()
        history_updates = []
        original_set_text = saved_turns.setText
        saved_turns.setText = lambda text: (history_updates.append(text), original_set_text(text))
        output.setProperty("VibeActiveTokenUsage", accumulator.metadata())
        gui._request_usage_summary(panel)
        with patch.object(gui, "_conversation_usage_entries", wraps=original) as reads:
            start = time.perf_counter()
            for _ in range(100):
                gui._request_usage_summary(panel)
            streamed_submissions = time.perf_counter() - start
            self.assertEqual(reads.call_count, 0)
        deadline = time.monotonic() + 5
        longest_gui_pass = 0.0
        while "110,110" not in details.text() and time.monotonic() < deadline:
            start = time.perf_counter()
            QtWidgets.QApplication.processEvents()
            longest_gui_pass = max(longest_gui_pass, time.perf_counter() - start)
            time.sleep(0.001)
        self.assertIn("110,110", details.text())
        self.assertEqual(saved_turns.text(), history_text)
        self.assertEqual(history_updates, [])
        renderer = output._vibecad_usage_renderer
        renderer.worker.close()
        renderer.worker._thread.join(3)
        self.assertFalse(renderer.worker._thread.is_alive())
        toggle.setChecked(False)
        gui._render_usage_summary(panel)
        self.assertTrue(graph.isHidden())
        self.assertTrue(details.isHidden())
        self.assertTrue(scroll.isHidden())
        if artifact_dir:
            Path(artifact_dir, "timings.txt").write_text(
                f"100 collapsed updates: {collapsed:.6f}s\n"
                f"1000-turn expanded update: {expanded:.6f}s\n"
                f"100 streamed submissions: {streamed_submissions:.6f}s\n"
                f"Longest GUI event pass while computing: {longest_gui_pass:.6f}s\n"
            )

    def test_streaming_keeps_gui_available_and_discards_closed_or_replaced_history(self):
        import threading
        import VibeCADTokenUsage as usage

        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        output = QtWidgets.QTextBrowser(panel)
        output.setObjectName("VibeConversation")
        toggle = QtWidgets.QToolButton(panel)
        toggle.setObjectName("VibeUsageSummaryToggle")
        toggle.setCheckable(True)
        toggle.setChecked(True)
        scroll = gui._make_usage_summary_widget(panel)
        details = scroll.findChild(QtWidgets.QLabel, "VibeUsageSummaryDetails")
        for widget in (toggle, scroll, output):
            layout.addWidget(widget)
        panel.show()
        self.addCleanup(panel.close)
        output.setProperty("VibeConversationEntries", [{"role": "user", "content": "test"}] * 1000)
        entered, release = threading.Event(), threading.Event()
        owner = threading.get_ident()
        calculation_threads = []
        original_compute = usage.render_usage_snapshot

        def paused_compute(history, active):
            calculation_threads.append(threading.get_ident())
            entered.set()
            self.assertTrue(release.wait(3))
            return original_compute(history, active)

        class Heartbeat(QtCore.QObject):
            beat = QtCore.Signal()
        heartbeat = Heartbeat()
        received = []
        heartbeat.beat.connect(lambda: received.append(True), QtCore.Qt.QueuedConnection)
        try:
            with patch.object(usage, "render_usage_snapshot", paused_compute), patch.object(
                gui, "_conversation_usage_entries", wraps=gui._conversation_usage_entries
            ) as reads:
                gui._request_usage_summary(panel)
                self.assertTrue(entered.wait(3))
                for _ in range(100):
                    gui._request_usage_summary(panel)
                self.assertEqual(reads.call_count, 1)
                heartbeat.beat.emit()
                QtWidgets.QApplication.processEvents()
                self.assertEqual(received, [True])
                self.assertNotIn(owner, calculation_threads)
                # Replacing the conversation must invalidate both queued and in-flight results.
                details.setText("new conversation")
                renderer = output._vibecad_usage_renderer
                prior_generation = renderer.worker._generation
                output.setProperty("VibeConversationEntries", [])
                self.assertFalse(renderer.worker.is_current(prior_generation))
                self.assertIsNone(renderer.entries)
                # QObject teardown must stop the worker without joining on the GUI.
                output.deleteLater()
                QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                self.assertFalse(renderer.worker.is_current(renderer.worker._generation))
                release.set()
                renderer.worker._thread.join(3)
                self.assertFalse(renderer.worker._thread.is_alive())
                QtWidgets.QApplication.processEvents()
                self.assertEqual(details.text(), "new conversation")
        finally:
            release.set()
            renderer = getattr(output, "_vibecad_usage_renderer", None)
            if renderer is not None:
                renderer.worker.close()
