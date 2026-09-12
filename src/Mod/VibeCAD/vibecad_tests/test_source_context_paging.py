# SPDX-License-Identifier: LGPL-2.1-or-later

import copy
import json
from types import SimpleNamespace

import pytest

import VibeCADSession as session
import VibeCADVibeScriptDomains as domains


def inventory(count=45):
    sources = [
        {"program": f"Robot/assembly/Module{i:03}", "label": f"Module{i:03}",
         "domain": "assembly", "status": "accepted", "current_revision": "a" * 64,
         "affected_outputs": [{"name": f"part{j}", "object_name": f"Body{j}",
                               "type_id": "PartDesign::Body", "visible": False}
                              for j in range(10000)] if i == 0 else []}
        for i in range(count)
    ]
    return {"schema": "vibecad-editable-sources-v1", "domain": "assembly",
            "source_count": count, "sources": sources, "all_sources": sources,
            "all_source_count": count}


def test_large_document_context_is_a_small_discoverable_index():
    context = {"editable_sources": inventory()}
    before = copy.deepcopy(context)
    prompt = session._provider_prompt("Inspect the robot", context)
    state = json.JSONDecoder().raw_decode(prompt.split("\n", 1)[1])[0]["active_state"]
    index = state["editable_sources"]["program_index"]
    assert index["program_count"] == 45
    assert len(index["programs"]) == 20
    assert index["next_read"]["arguments"]["offset"] == 20
    assert index["programs"][0]["output_count"] == 10000
    assert "all_sources" not in state["editable_sources"]
    assert len(prompt.encode()) < 16000
    assert context == before


def test_pages_cover_every_program_once_and_search_preserves_identity():
    data = inventory()
    programs = []
    args = {"offset": 0, "limit": 7}
    while args is not None:
        page = session._paged_source_index_payload(data, **args)
        programs.extend(p["program"] for p in page["programs"])
        args = page["next_read"]["arguments"] if page["next_read"] else None
    assert len(programs) == len(set(programs)) == 45
    page = session._paged_source_index_payload(data, query="module044")
    assert page["programs"][0]["program"] == "Robot/assembly/Module044"
    assert page["programs"][0]["current_revision"] == "a" * 64
    assert page["matched_count"] == 1
    assert not session._paged_source_index_payload(data, query="absent")["programs"]


def test_cross_domain_sources_remain_discoverable_when_active_domain_empty():
    data = inventory(1)
    data.update(source_count=0, sources=[], domain="partdesign")
    result = session._provider_editable_sources_payload(data)
    assert result["program_index"]["program_count"] == 1


def test_read_source_schema_exposes_optional_index_pagination():
    spec = next(s for s in domains.universal_tool_specs() if s["name"] == "vibescript.read_source")
    assert {"offset", "limit", "query"} <= set(spec["parameters"]["properties"])
    assert not spec["parameters"]["required"]


def test_source_search_uses_provider_session_adapter_without_document_scan():
    result = session._run_universal_vibescript_tool(
        SimpleNamespace(), "AssemblyWorkbench", "vibescript.read_source",
        {"query": "Module044", "limit": 1, "offset": 0},
        editable_sources=inventory(), document_thread_dispatch=None,
        cancellation_check=None, progress_callback=None,
    )
    assert result["ok"] is True
    assert result["programs"][0]["program"] == "Robot/assembly/Module044"


def test_exact_source_reads_are_complete_ranged_and_revision_explicit():
    inspected = {
        "ok": True,
        "program": {
            "program_id": "program-1",
            "working_revision": "a" * 64,
            "source": "first line\nsecond line\nthird line\n",
            "domain": "assembly",
            "workbench": "AssemblyWorkbench",
            "label": "Robot",
            "input_schema": {},
            "inputs": {},
            "expected_outputs": [],
            "live_outputs": {},
        },
    }

    complete = session._read_source_payload(inspected)
    ranged = session._read_source_payload(inspected, line_start=2, line_end=2)

    assert complete["source"] == "first line\nsecond line\nthird line\n"
    assert complete["source_range"] == {
        "line_start": 1,
        "line_end": 3,
        "total_lines": 3,
        "complete": True,
    }
    assert complete["current_revision"] == "a" * 64
    assert ranged["source"] == "second line\n"
    assert ranged["source_range"] == {
        "line_start": 2,
        "line_end": 2,
        "total_lines": 3,
        "complete": False,
    }
    assert len(ranged["source"]) < len(complete["source"])

    inspected["program"]["working_revision"] = "b" * 64
    updated = session._read_source_payload(inspected)
    assert updated["current_revision"] == "b" * 64


@pytest.mark.parametrize("args", [{"offset": -1}, {"limit": 0}, {"limit": 101}])
def test_invalid_page_arguments_are_rejected(args):
    with pytest.raises(ValueError):
        session._paged_source_index_payload({}, **args)
