# SPDX-License-Identifier: LGPL-2.1-or-later

"""Unit contracts for responsive, observable VibeScript publication."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADPublicationProgress import PublicationProgress


def test_publication_progress_reports_items_and_bounds_ui_yields() -> None:
    events = []
    yields = []
    clock_values = iter((0.0, 0.01, 0.06, 0.07, 0.08, 0.13, 0.14))
    progress = PublicationProgress(
        domain="assembly",
        total=3,
        callback=events.append,
        event_yield=lambda: yields.append(True),
        clock=lambda: next(clock_values),
        yield_interval_seconds=0.05,
    )

    progress.start()
    progress.checkpoint(0, name="Model", output_type="assembly")
    progress.checkpoint(1, name="Part001", output_type="component_link")
    progress.checkpoint(2, name="Joint001", output_type="joint")
    progress.finish()

    assert [event["completed"] for event in events] == [0, 1, 2, 3]
    assert all(event["total"] == 3 for event in events)
    assert events[2]["current_output"] == "Joint001"
    assert events[2]["output_type"] == "joint"
    assert events[-1]["phase"] == "completed"
    assert len(yields) == 2


def test_publication_throttle_measures_from_completed_event_yield() -> None:
    now = [0.0]
    yields = []

    def event_yield() -> None:
        yields.append(now[0])
        now[0] += 0.20

    progress = PublicationProgress(
        domain="assembly",
        total=2,
        event_yield=event_yield,
        clock=lambda: now[0],
        yield_interval_seconds=0.05,
    )

    progress.start()
    now[0] = 0.06
    progress.checkpoint(1, name="Part001", output_type="component_link")
    now[0] = 0.27
    progress.checkpoint(2, name="Part002", output_type="component_link")

    assert yields == [0.06]


def test_publication_releases_document_batch_when_progress_reporting_fails() -> None:
    from VibeCADVibeScriptDomainPublication import publish_candidate

    calls = []

    class Service:
        @staticmethod
        def begin_document_change_batch(uid, **_kwargs):
            calls.append(("begin", uid))

        @staticmethod
        def end_document_change_batch(uid, *, commit=True):
            calls.append(("end", uid, commit))

    def reject_progress(_event):
        raise RuntimeError("progress failed")

    with pytest.raises(RuntimeError, match="progress failed"):
        publish_candidate(
            Service(),
            {
                "document_uid": "document-a",
                "pack": SimpleNamespace(domain="assembly"),
            },
            {"outputs": [], "assembly_members": []},
            progress_callback=reject_progress,
        )

    assert calls == [("begin", "document-a"), ("end", "document-a", False)]


@pytest.mark.parametrize("fails", [False, True])
def test_publication_holds_document_cooperative_mutation_for_its_full_lifetime(
    monkeypatch,
    fails: bool,
) -> None:
    import VibeCADVibeScriptDomainPublication as publication

    calls = []

    class Document:
        @staticmethod
        def beginCooperativeMutation():
            calls.append("mutation-begin")

        @staticmethod
        def endCooperativeMutation():
            calls.append("mutation-end")

    class Service:
        @staticmethod
        def _active_document():
            return Document()

        @staticmethod
        def begin_document_change_batch(uid, **_kwargs):
            calls.append(("batch-begin", uid))

        @staticmethod
        def end_document_change_batch(uid, *, commit=True):
            calls.append(("batch-end", uid, commit))

    def publication_steps(*_args, **_kwargs):
        calls.append("publication-start")
        yield {"event": "publication-slice"}
        if fails:
            raise RuntimeError("publication failed")
        return {"ok": True}

    monkeypatch.setattr(
        publication,
        "_iter_publish_candidate_unbatched",
        publication_steps,
    )
    inputs = (
        Service(),
        {
            "document_uid": "document-a",
            "pack": SimpleNamespace(domain="assembly"),
        },
        {"outputs": [], "assembly_members": []},
    )

    if fails:
        with pytest.raises(RuntimeError, match="publication failed"):
            publication.publish_candidate(*inputs)
    else:
        assert publication.publish_candidate(*inputs) == {"ok": True}

    assert calls == [
        "mutation-begin",
        ("batch-begin", "document-a"),
        "publication-start",
        ("batch-end", "document-a", not fails),
        "mutation-end",
    ]


def test_publication_releases_cooperative_mutation_when_batch_setup_fails() -> None:
    from VibeCADVibeScriptDomainPublication import publish_candidate

    calls = []

    class Document:
        @staticmethod
        def beginCooperativeMutation():
            calls.append("mutation-begin")

        @staticmethod
        def endCooperativeMutation():
            calls.append("mutation-end")

    class Service:
        @staticmethod
        def _active_document():
            return Document()

        @staticmethod
        def begin_document_change_batch(_uid, **_kwargs):
            calls.append("batch-begin")
            raise RuntimeError("batch setup failed")

        @staticmethod
        def end_document_change_batch(_uid, *, commit=True):
            calls.append(("batch-end", commit))

    with pytest.raises(RuntimeError, match="batch setup failed"):
        publish_candidate(
            Service(),
            {
                "document_uid": "document-a",
                "pack": SimpleNamespace(domain="assembly"),
            },
            {"outputs": [], "assembly_members": []},
        )

    assert calls == ["mutation-begin", "batch-begin", "mutation-end"]


def test_domain_adapter_cooperatively_dispatches_each_publication_step(
    monkeypatch,
) -> None:
    import VibeCADVibeScriptDomainRuntime as runtime

    dispatches = []
    events = []
    trace_attributes = None

    def publication_steps(*_args, **_kwargs):
        yield {"event": "publication_slice", "completed": 1, "total": 2}
        yield {"event": "publication_slice", "completed": 2, "total": 2}
        return {"ok": True, "outputs": ["Model"]}

    monkeypatch.setattr(runtime, "iter_publish_candidate", publication_steps)
    run_document_thread_steps = runtime.run_document_thread_steps

    def run_traced_steps(*args, **kwargs):
        nonlocal trace_attributes
        trace_attributes = kwargs.get("trace_attributes")
        return run_document_thread_steps(*args, **kwargs)

    monkeypatch.setattr(runtime, "run_document_thread_steps", run_traced_steps)
    adapter = runtime.DeclarativeDomainAdapter(
        SimpleNamespace(domain="assembly", workbench="Assembly")
    )

    result = adapter.publish_cooperatively(
        object(),
        {
            "attempt_id": "attempt-a",
            "document_uid": "document-a",
        },
        {},
        document_thread_dispatch=lambda operation: (dispatches.append(operation), operation())[1],
        cancellation_check=lambda: False,
        progress_callback=events.append,
    )

    assert result == {"ok": True, "outputs": ["Model"]}
    assert len(dispatches) == 3
    assert [event["completed"] for event in events] == [1, 2]
    assert trace_attributes == {
        "operation_id": "attempt-a",
        "document_uid": "document-a",
        "capability": "vibescript.publish",
        "domain": "assembly",
    }


def test_large_assembly_publication_dispatches_all_307_members(
    monkeypatch,
) -> None:
    import VibeCADVibeScriptDomainPublication as publication
    import VibeCADVibeScriptDomainRuntime as runtime

    dispatches = 0
    progress_events = []
    document = SimpleNamespace(
        beginCooperativeMutation=lambda: None,
        endCooperativeMutation=lambda: None,
    )

    class Service:
        @staticmethod
        def _active_document():
            return document

        @staticmethod
        def begin_document_change_batch(_uid, **_kwargs):
            return None

        @staticmethod
        def end_document_change_batch(_uid, *, commit=True):
            return commit

    public_outputs = [
        {"name": "Model", "type": "assembly"},
        {"name": "Diagnostics", "type": "solver_diagnostics"},
    ]
    assembly_members = [
        {"name": f"Member{index:03d}", "type": "component_link"} for index in range(307)
    ]

    def publication_steps(
        _service,
        _prepared,
        validated,
        *,
        publication_progress,
    ):
        items = [
            *list(validated["outputs"]),
            *list(validated["assembly_members"]),
        ]
        for index, item in enumerate(items, start=1):
            publication_progress.checkpoint(
                index,
                name=item["name"],
                output_type=item["type"],
            )
            yield {"phase": "publication_item"}
        return {"ok": True, "published": len(items)}

    monkeypatch.setattr(
        publication,
        "_iter_publish_candidate_unbatched",
        publication_steps,
    )
    # Runtime imports the generator function directly, so bind the patched
    # production wrapper rather than replacing its lifecycle behavior.
    monkeypatch.setattr(
        runtime,
        "iter_publish_candidate",
        publication.iter_publish_candidate,
    )
    adapter = runtime.DeclarativeDomainAdapter(
        SimpleNamespace(domain="assembly", workbench="Assembly")
    )

    def dispatch(operation):
        nonlocal dispatches
        dispatches += 1
        return operation()

    result = adapter.publish_cooperatively(
        Service(),
        {
            "document_uid": "large-assembly",
            "program_id": "program-a",
            "pack": SimpleNamespace(domain="assembly"),
        },
        {
            "outputs": public_outputs,
            "assembly_members": assembly_members,
        },
        document_thread_dispatch=dispatch,
        cancellation_check=lambda: False,
        progress_callback=progress_events.append,
    )

    assert result == {"ok": True, "published": 309}
    assert dispatches == 310
    publication_events = [
        event
        for event in progress_events
        if event.get("event") == "vibescript_domain_publication_progress"
    ]
    assert publication_events[-1]["phase"] == "completed"
    assert publication_events[-1]["completed"] == 309
    assert all(event["total"] == 309 for event in publication_events)


def test_publication_slice_validation_rejects_replaced_targets() -> None:
    from VibeCADVibeScriptDomainPublication import (
        _assert_publication_document_intact,
    )

    original = SimpleNamespace(Name="Member001")
    replacement = SimpleNamespace(Name="Member001")

    class Document:
        Name = "AssemblyDocument"
        Uid = "document-a"

        @staticmethod
        def getObject(name):
            return replacement if name == "Member001" else None

    document = Document()
    service = SimpleNamespace(_active_document=lambda: document)

    with pytest.raises(RuntimeError, match="changed between publication slices"):
        _assert_publication_document_intact(
            service,
            {
                "document_name": document.Name,
                "document_uid": document.Uid,
            },
            document,
            (original,),
        )
