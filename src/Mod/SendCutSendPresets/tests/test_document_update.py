# SPDX-License-Identifier: MIT
"""One preset action owns one mutation/undo boundary and one recompute request."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def update(monkeypatch):
    events = []
    app = SimpleNamespace(GuiUp=True)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    class Owned:
        def __init__(self, doc, name):
            events.append("open")
        def commit(self):
            events.append("commit")
        def abort(self):
            events.append("abort")
    monkeypatch.setitem(sys.modules, "VibeCADNativeTransaction", SimpleNamespace(
        _OwnedDocumentTransaction=Owned
    ))
    spec = importlib.util.spec_from_file_location("scs_update_test",
        Path(__file__).resolve().parents[1] / "preset_update.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    doc = SimpleNamespace(Recomputing=False, RecomputePending=False, CooperativeMutationActive=False,
        getBookedTransactionID=lambda: 0, HasPendingTransaction=False,
        beginCooperativeMutation=lambda: events.append("begin"),
        endCooperativeMutation=lambda: events.append("end"),
        recomputeAsync=lambda: events.append("async"),
        recompute=lambda: events.append("sync"))
    return module, doc, events


def test_update_groups_edits_and_requests_one_async_recompute(update):
    module, doc, events = update
    assert module.run_document_update(doc, lambda: events.append("edit") or "done") == "done"
    assert events == ["open", "begin", "edit", "end", "async", "commit"]


def test_update_aborts_owned_transaction_and_releases_mutation_on_failure(update):
    module, doc, events = update
    def fail():
        events.append("edit")
        raise ValueError("write failed")
    with pytest.raises(ValueError, match="write failed"):
        module.run_document_update(doc, fail)
    assert events == ["open", "begin", "edit", "abort", "end"]


def test_existing_task_transaction_is_never_closed_by_preset(update):
    module, doc, events = update
    doc.getBookedTransactionID = lambda: 42
    module.run_document_update(doc, lambda: events.append("edit"))
    assert events == ["begin", "edit", "end", "async"]


@pytest.mark.parametrize("flag", ["Recomputing", "RecomputePending", "CooperativeMutationActive"])
def test_busy_document_is_not_mutated(update, flag):
    module, doc, events = update
    setattr(doc, flag, True)
    with pytest.raises(RuntimeError, match="finish"):
        module.run_document_update(doc, lambda: events.append("edit"))
    assert events == []


def test_commit_failure_does_not_change_a_retained_commit_into_abort(update, monkeypatch):
    module, doc, events = update
    owner = sys.modules["VibeCADNativeTransaction"]._OwnedDocumentTransaction
    def commit(self):
        events.append("commit")
        raise RuntimeError("close retained")
    monkeypatch.setattr(owner, "commit", commit)
    with pytest.raises(RuntimeError, match="close retained"):
        module.run_document_update(doc, lambda: events.append("edit"))
    assert events == ["open", "begin", "edit", "end", "async", "commit"]


def test_begin_failure_aborts_owned_transaction_without_ending_unstarted_mutation(update):
    module, doc, events = update
    def fail_begin():
        events.append("begin")
        raise RuntimeError("document is closing")
    doc.beginCooperativeMutation = fail_begin
    with pytest.raises(RuntimeError, match="document is closing"):
        module.run_document_update(doc, lambda: events.append("edit"))
    assert events == ["open", "begin", "abort"]
