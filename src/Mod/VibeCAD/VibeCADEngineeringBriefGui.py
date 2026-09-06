# SPDX-License-Identifier: LGPL-2.1-or-later

"""Readable, modeless Engineering Brief window for the VibeCAD assistant."""

from __future__ import annotations

import html
import queue
import threading
from typing import Any, Callable, Mapping

from PySide import QtCore, QtWidgets

from VibeCADEngineeringBrief import (
    render_engineering_brief,
    update_engineering_brief_draft,
)


class _EngineeringBriefSignals(QtCore.QObject):
    turn_completed = QtCore.Signal(object)
    turn_failed = QtCore.Signal(str)
    persistence_failed = QtCore.Signal(str)


class EngineeringBriefDialog(QtWidgets.QDialog):
    """A non-modal interview and review surface for one engineering brief."""

    def __init__(
        self,
        state: Mapping[str, Any],
        *,
        turn_runner: Callable[..., dict[str, Any]],
        persist_callback: Callable[[Mapping[str, Any]], Any],
        start_callback: Callable[[Mapping[str, Any], str], bool],
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VibeEngineeringBriefDialog")
        self.setWindowTitle("VibeCAD Engineering Brief")
        self.setModal(False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.resize(1120, 760)
        self.setMinimumSize(820, 560)

        self._state = dict(state)
        self._turn_runner = turn_runner
        self._persist_callback = persist_callback
        self._start_callback = start_callback
        self._turn_thread: threading.Thread | None = None
        self._turn_active = False
        self._cancel_event = threading.Event()
        self._signals = _EngineeringBriefSignals(self)
        self._signals.turn_completed.connect(self._complete_turn)
        self._signals.turn_failed.connect(self._fail_turn)
        self._signals.persistence_failed.connect(self._show_persistence_failure)

        self._closing = False
        self._lifecycle_lock = threading.Lock()
        self._persist_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._persist_thread = threading.Thread(
            target=self._persistence_loop,
            name="VibeCAD-Engineering-Brief-Persistence",
            daemon=True,
        )
        self._persist_thread.start()
        self._build_ui()
        self._render_state()
        self._queue_persistence()

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QtWidgets.QLabel("Build an Engineering Brief", self)
        title.setObjectName("VibeEngineeringBriefTitle")
        title_font = title.font()
        title_font.setPointSize(max(title_font.pointSize() + 4, 14))
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)

        explanation = QtWidgets.QLabel(
            "The blue button always shows the next step. VibeCAD uses your active "
            "conversation and document context, asks only consequential engineering "
            "questions, and lets you review the result before any CAD work begins.",
            self,
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        self.pages = QtWidgets.QStackedWidget(self)
        self.pages.setObjectName("VibeEngineeringBriefSteps")
        root.addWidget(self.pages, 1)

        request_page = QtWidgets.QWidget(self.pages)
        request_layout = QtWidgets.QVBoxLayout(request_page)
        request_layout.setContentsMargins(0, 8, 0, 0)
        request_layout.setSpacing(8)
        request_title = QtWidgets.QLabel(
            "1. Describe the result you need", request_page
        )
        request_title_font = request_title.font()
        request_title_font.setBold(True)
        request_title.setFont(request_title_font)
        request_layout.addWidget(request_title)
        request_help = QtWidgets.QLabel(
            "Enter the engineering outcome in the box below. This is the only field "
            "you need to complete at this step. Relevant details from the active "
            "VibeCAD conversation will be included automatically.",
            request_page,
        )
        request_help.setWordWrap(True)
        request_layout.addWidget(request_help)

        self.request_edit = QtWidgets.QPlainTextEdit(request_page)
        self.request_edit.setObjectName("VibeEngineeringBriefRequest")
        self.request_edit.setPlaceholderText(
            "Example: Finish the robot design using the requirements we already "
            "discussed."
        )
        self.request_edit.setMinimumHeight(180)
        self.request_edit.textChanged.connect(self._schedule_human_edit_persistence)
        request_layout.addWidget(self.request_edit, 1)
        self.pages.addWidget(request_page)

        interview_page = QtWidgets.QWidget(self.pages)
        interview_layout = QtWidgets.QVBoxLayout(interview_page)
        interview_layout.setContentsMargins(0, 8, 0, 0)
        interview_layout.setSpacing(8)
        interview_title = QtWidgets.QLabel(
            "2. Answer one engineering question", interview_page
        )
        interview_title_font = interview_title.font()
        interview_title_font.setBold(True)
        interview_title.setFont(interview_title_font)
        interview_layout.addWidget(interview_title)
        interview_help = QtWidgets.QLabel(
            "VibeCAD asks only for information that would materially change the "
            "result. Enter your answer in the single box below.",
            interview_page,
        )
        interview_help.setWordWrap(True)
        interview_layout.addWidget(interview_help)

        self.transcript = QtWidgets.QTextBrowser(interview_page)
        self.transcript.setObjectName("VibeEngineeringBriefTranscript")
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setOpenLinks(False)
        interview_layout.addWidget(self.transcript, 1)

        self.question_label = QtWidgets.QLabel(interview_page)
        self.question_label.setObjectName("VibeEngineeringBriefQuestion")
        question_font = self.question_label.font()
        question_font.setBold(True)
        self.question_label.setFont(question_font)
        self.question_label.setWordWrap(True)
        interview_layout.addWidget(self.question_label)

        self.answer_edit = QtWidgets.QPlainTextEdit(interview_page)
        self.answer_edit.setObjectName("VibeEngineeringBriefAnswer")
        self.answer_edit.setPlaceholderText("Type your answer here.")
        self.answer_edit.setMinimumHeight(80)
        self.answer_edit.setMaximumHeight(140)
        self.answer_edit.textChanged.connect(self._update_primary_action)
        interview_layout.addWidget(self.answer_edit)
        self.pages.addWidget(interview_page)

        review_page = QtWidgets.QWidget(self.pages)
        review_layout = QtWidgets.QVBoxLayout(review_page)
        review_layout.setContentsMargins(0, 8, 0, 0)
        review_layout.setSpacing(8)
        review_title = QtWidgets.QLabel("3. Review the engineering brief", review_page)
        review_title_font = review_title.font()
        review_title_font.setBold(True)
        review_title.setFont(review_title_font)
        review_layout.addWidget(review_title)
        review_help = QtWidgets.QLabel(
            "Read the generated brief and edit anything that needs correction. When "
            "it is accurate, the blue button starts the CAD work.",
            review_page,
        )
        review_help.setWordWrap(True)
        review_layout.addWidget(review_help)

        self.preview = QtWidgets.QPlainTextEdit(review_page)
        self.preview.setObjectName("VibeEngineeringBriefPreview")
        self.preview.setPlaceholderText(
            "Your generated engineering brief will appear here."
        )
        self.preview.textChanged.connect(self._schedule_human_edit_persistence)
        review_layout.addWidget(self.preview, 1)
        self.pages.addWidget(review_page)

        self.status = QtWidgets.QLabel(self)
        self.status.setObjectName("VibeEngineeringBriefStatus")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        footer = QtWidgets.QHBoxLayout()
        self.best_judgment_button = QtWidgets.QPushButton(
            "Finish with Assumptions", self
        )
        self.best_judgment_button.setObjectName("VibeEngineeringBriefBestJudgment")
        self.best_judgment_button.setToolTip(
            "Let VibeCAD make and clearly record reasonable assumptions for the "
            "remaining details"
        )
        self.best_judgment_button.clicked.connect(lambda: self._begin_turn(True))
        footer.addWidget(self.best_judgment_button)
        footer.addStretch(1)
        self.close_button = QtWidgets.QPushButton("Close", self)
        self.close_button.setObjectName("VibeEngineeringBriefClose")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)

        self.primary_button = QtWidgets.QPushButton("Build My Brief", self)
        self.primary_button.setObjectName("VibeEngineeringBriefPrimary")
        self.primary_button.setToolTip("Continue to the next step shown in this window")
        self.primary_button.clicked.connect(self._advance)
        footer.addWidget(self.primary_button)
        root.addLayout(footer)

        self._edit_timer = QtCore.QTimer(self)
        self._edit_timer.setObjectName("VibeEngineeringBriefDraftTimer")
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(600)
        self._edit_timer.timeout.connect(self._capture_and_persist_human_edits)

    def _render_state(self) -> None:
        request_blocked = self.request_edit.blockSignals(True)
        preview_blocked = self.preview.blockSignals(True)
        try:
            self.request_edit.setPlainText(
                str(self._state.get("original_request") or "")
            )
            self.preview.setPlainText(render_engineering_brief(self._state))
        finally:
            self.request_edit.blockSignals(request_blocked)
            self.preview.blockSignals(preview_blocked)

        transcript = list(self._state.get("transcript") or [])
        if transcript:
            blocks = []
            for item in transcript:
                role = "You" if item.get("role") == "user" else "Brief assistant"
                content = html.escape(str(item.get("content") or "")).replace(
                    "\n", "<br/>"
                )
                blocks.append(f"<p><b>{html.escape(role)}</b><br/>{content}</p>")
            self.transcript.setHtml("".join(blocks))
            scrollbar = self.transcript.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        else:
            self.transcript.setHtml(
                "<p><b>Brief assistant</b><br/>I will use your active VibeCAD "
                "conversation and ask one consequential question at a time.</p>"
            )

        question = str(self._state.get("next_question") or "").strip()
        self.question_label.setText(question)
        if self._state.get("ready"):
            self.pages.setCurrentIndex(2)
        elif question or transcript:
            self.pages.setCurrentIndex(1)
        else:
            self.pages.setCurrentIndex(0)
        self.request_edit.setReadOnly(self.pages.currentIndex() != 0)
        self.preview.setReadOnly(not bool(self._state.get("ready")))
        self.best_judgment_button.setVisible(
            bool(question) and not bool(self._state.get("ready"))
        )
        if self._state.get("ready"):
            self.status.setText(
                "Review the brief. The blue button will start CAD work when it is ready."
            )
        elif question:
            self.status.setText(
                "Type one answer, then use the blue button. No CAD work has started."
            )
        else:
            self.status.setText(
                "Enter your request, then use the blue button. No CAD work will begin yet."
            )
        self._update_primary_action()

    def _update_primary_action(self) -> None:
        if self._turn_active:
            label = "Building Brief..."
            enabled = False
        elif self._state.get("ready"):
            label = "Start CAD Work"
            enabled = bool(self.preview.toPlainText().strip())
        elif str(self._state.get("next_question") or "").strip():
            label = "Submit Answer"
            enabled = bool(self.answer_edit.toPlainText().strip())
        else:
            label = "Build My Brief"
            enabled = bool(self.request_edit.toPlainText().strip())
        self.primary_button.setText(label)
        self.primary_button.setEnabled(enabled)
        self.primary_button.setAutoDefault(enabled)
        self.primary_button.setDefault(enabled)

    def _advance(self) -> None:
        if self._state.get("ready"):
            self._start_in_vibecad()
        else:
            self._begin_turn(False)

    def _schedule_human_edit_persistence(self) -> None:
        self._update_primary_action()
        if not self._closing:
            self._edit_timer.start()

    def _capture_human_edits(self) -> None:
        self._state = update_engineering_brief_draft(
            self._state,
            original_request=self.request_edit.toPlainText(),
            editable_text=self.preview.toPlainText(),
        )

    def _capture_and_persist_human_edits(self) -> None:
        self._capture_human_edits()
        self._queue_persistence()

    def _queue_persistence(self) -> None:
        with self._lifecycle_lock:
            if not self._closing:
                self._persist_queue.put(dict(self._state))

    def _persistence_loop(self) -> None:
        while True:
            state = self._persist_queue.get()
            try:
                if state is None:
                    return
                self._persist_callback(state)
            except Exception as exc:
                try:
                    self._signals.persistence_failed.emit(str(exc))
                except RuntimeError:
                    return
            finally:
                self._persist_queue.task_done()

    def _show_persistence_failure(self, message: str) -> None:
        if not self._closing:
            self.status.setText(f"The brief is open but could not be saved: {message}")

    def _set_turn_busy(self, busy: bool) -> None:
        self.request_edit.setReadOnly(busy or self.pages.currentIndex() != 0)
        self.preview.setReadOnly(busy or not bool(self._state.get("ready")))
        self.answer_edit.setReadOnly(busy)
        self.best_judgment_button.setEnabled(not busy)
        self.close_button.setText("Cancel" if busy else "Close")
        self._update_primary_action()

    def _begin_turn(self, use_best_judgment: bool) -> None:
        if self._turn_active:
            return
        self._capture_human_edits()
        if not str(self._state.get("original_request") or "").strip():
            self.status.setText(
                "Describe the engineering outcome before developing the brief."
            )
            self.request_edit.setFocus()
            return
        response = self.answer_edit.toPlainText().strip()
        if self._state.get("next_question") and not response and not use_best_judgment:
            self.status.setText("Answer the current question or use best judgment.")
            self.answer_edit.setFocus()
            return
        snapshot = dict(self._state)
        self._cancel_event.clear()
        self._turn_active = True
        self._set_turn_busy(True)
        self.status.setText("Building your engineering brief...")

        def run() -> None:
            try:
                updated = self._turn_runner(
                    snapshot,
                    user_response=response,
                    use_best_judgment=use_best_judgment,
                    cancellation_check=self._cancel_event.is_set,
                )
                with self._lifecycle_lock:
                    if self._closing or self._cancel_event.is_set():
                        return
                    self._persist_queue.put(dict(updated))
            except Exception as exc:
                try:
                    self._signals.turn_failed.emit(str(exc))
                except RuntimeError:
                    pass
                return
            try:
                self._signals.turn_completed.emit(updated)
            except RuntimeError:
                pass

        self._turn_thread = threading.Thread(
            target=run,
            name="VibeCAD-Engineering-Brief-Provider",
            daemon=True,
        )
        self._turn_thread.start()

    @QtCore.Slot(object)
    def _complete_turn(self, state: Mapping[str, Any]) -> None:
        if self._closing:
            return
        self._state = dict(state)
        self._turn_thread = None
        self._turn_active = False
        self.answer_edit.clear()
        self._set_turn_busy(False)
        self._render_state()
        if not self._state.get("ready"):
            self.answer_edit.setFocus()

    @QtCore.Slot(str)
    def _fail_turn(self, message: str) -> None:
        if self._closing:
            return
        self._turn_thread = None
        self._turn_active = False
        self._set_turn_busy(False)
        self.status.setText(f"The brief assistant could not continue: {message}")

    def _start_in_vibecad(self) -> None:
        if self._turn_active:
            return
        self._capture_human_edits()
        request = str(self._state.get("original_request") or "").strip()
        readable = self.preview.toPlainText().strip()
        if not request:
            self.status.setText("Describe the engineering outcome before starting.")
            self.request_edit.setFocus()
            return
        if not readable:
            self.status.setText(
                "Develop or write the engineering brief before starting."
            )
            self.preview.setFocus()
            return
        self._queue_persistence()
        try:
            started = bool(self._start_callback(dict(self._state), readable))
        except Exception as exc:
            self.status.setText(f"VibeCAD could not start this brief: {exc}")
            return
        if started:
            self.close()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        if self._closing:
            event.accept()
            return
        self._edit_timer.stop()
        self._cancel_event.set()
        self._capture_human_edits()
        with self._lifecycle_lock:
            self._closing = True
            self._persist_queue.put(dict(self._state))
            self._persist_queue.put(None)
        self._persist_thread.join()
        event.accept()
