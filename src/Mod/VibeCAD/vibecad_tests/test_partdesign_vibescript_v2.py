# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contract tests for the unified Part Design VibeScript v2 domain."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import VibeCADVibeScriptDomainRuntime as runtime
import VibeCADVibeScriptDomains as domains
from vibescript_domain_api import DomainValue, create_domain_api
from vibescript_partdesign_api import PartDesignDomainAPI


PROGRAM_ID = "0123456789abcdef0123456789abcdef"
OUTPUT_TYPES = ("solid", "shell", "face", "wire", "compound")


def _pack():
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    return pack


def _capture(root: Path, *, operation: str, arguments: dict) -> dict:
    pack = _pack()
    return {
        "pack": pack,
        "operation": operation,
        "tool_name": f"vibescript.partdesign.{operation}",
        "arguments": arguments,
        "project_root": str(root),
        "document_name": "PartDesignContractTest",
        "document_uid": "partdesign-contract-document",
        "document_revision": "document-revision-1",
        "document_objects": [],
        "surface": {
            "workbench": pack.workbench,
            "engine": "vibescript",
            "surface_id": pack.surface_id,
        },
        "freecad_home": str(root / "freecad-home"),
        "timeout_seconds": 30.0,
        "memory_limit_bytes": 512 * 1024 * 1024,
    }


def _write_v1_program(root: Path) -> Path:
    directory = root / "vibescript" / PROGRAM_ID
    directory.mkdir(parents=True)
    manifest = {
        "schema": domains.PARTDESIGN_V1_SCHEMA,
        "model_id": PROGRAM_ID,
        "model_name": "Saved Part",
        "source": "result = {'Part': output('Part')}",
        "parameters": {"radius": 4.0},
        "expected_outputs": ["Part"],
        "revision": "saved-v1-revision",
        "outputs": {
            "Part": {
                "object_name": "SavedPartResult",
                "type": "solid",
            }
        },
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return directory


def _write_v2_program(root: Path, source: str) -> tuple[Path, str]:
    pack = _pack()
    input_schema = {
        "type": "object",
        "properties": {
            "radius": {"type": "number", "exclusiveMinimum": 0},
            "height": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["radius", "height"],
        "additionalProperties": False,
    }
    inputs = {"radius": 4.0, "height": 5.0}
    expected_outputs = [{"name": "Part", "type": "solid"}]
    revision = domains.program_revision(
        domain=pack.domain,
        source=source,
        input_schema=input_schema,
        inputs=inputs,
        expected_outputs=expected_outputs,
    )
    directory = root / "vibescript" / pack.domain / PROGRAM_ID
    directory.mkdir(parents=True)
    manifest = {
        "schema": domains.PROGRAM_SCHEMA,
        "version": domains.PROGRAM_VERSION,
        "program_id": PROGRAM_ID,
        "domain": pack.domain,
        "workbench": pack.workbench,
        "label": "Saved Part",
        "source": source,
        "input_schema": input_schema,
        "inputs": inputs,
        "expected_outputs": expected_outputs,
        "working_revision": revision,
        "accepted_revision": revision,
        "accepted_contract": None,
        "live_outputs": {},
        "latest_candidate": {
            "revision": revision,
            "status": "accepted",
        },
    }
    (directory / "program.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return directory, revision


def test_partdesign_uses_the_exact_common_v2_lifecycle() -> None:
    pack = _pack()
    assert pack.production_ready is True
    assert pack.surface_id == "vibescript:partdesign:v2"
    assert pack.tool_names == tuple(
        f"vibescript.partdesign.{operation}"
        for operation in domains.LIFECYCLE_OPERATIONS
    )
    assert domains.LIFECYCLE_OPERATIONS == (
        "describe_api",
        "inspect_program",
        "create_program",
        "edit_source",
        "set_inputs",
        "reconfigure_program",
        "delete_program",
    )


def test_partdesign_runtime_api_is_explicit_and_matches_describe_api() -> None:
    pack = _pack()
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    assert isinstance(api, PartDesignDomainAPI)
    assert api.exported_names == pack.api_exports
    assert {"extrude", "revolve", "loft", "material", "appearance"} <= set(
        api.exported_names
    )
    assert {"pad", "pocket", "groove"}.isdisjoint(api.exported_names)
    assert {"pad", "pocket", "groove"}.isdisjoint(PartDesignDomainAPI.__dict__)
    assert all(not hasattr(api, name) for name in ("pad", "pocket", "groove"))
    assert {"pad", "pocket", "groove"}.isdisjoint(dir(api))
    signatures = {
        name: str(inspect.signature(getattr(api, name)))
        for name in api.exported_names
    }
    assert all("*args" not in signature for signature in signatures.values())
    assert all("**kwargs" not in signature for signature in signatures.values())
    assert all("**properties" not in signature for signature in signatures.values())
    description = domains.get_domain_adapter("partdesign").describe_api()
    assert {
        item["name"]: item["signature"]
        for item in description["runtime_exports"]
    } == signatures
    assert description["source_globals"] == ["doc", "inputs", "api"]
    assert description["accepted_output_types"] == list(OUTPUT_TYPES)
    assert "api.compound" in description["operation_selection"]["disconnected_geometry"]
    assert "Part workbench is retired" in description["workbench_handoffs"][
        "part_compatibility"
    ]
    assert "api.material" in description["operation_selection"]["physical_material"]
    assert "0-255" in description["operation_selection"]["visible_appearance"]
    priority = description["authoring_priority"]
    assert "api.sketch plus native feature operations" in priority["default"]
    assert "offset loft section" in priority["planar_profile_rule"]
    assert "nonplanar, imported, repair" in priority["direct_topology_exception"]
    assert "Do not replace valid native history" in priority["do_not_regress"]
    assert "empty sketch list" in priority["verification"]
    serialized_description = json.dumps(description, sort_keys=True)
    assert "Pad" not in serialized_description
    assert "Pocket" not in serialized_description
    assert "Groove" not in serialized_description
    assert "recommended_patterns" not in description

    exports = {
        item["name"]: item for item in description["runtime_exports"]
    }
    for direct_name in ("line_3d", "arc_3d", "wire"):
        assert "prefer api.sketch and native Body features" not in exports[direct_name][
            "description"
        ]
    assert "Use api.sketch sections for planar profiles" in exports["loft"][
        "description"
    ]
    assert "subtractive" not in exports["loft"]["signature"]
    assert "standalone solid is accepted only" in exports["body"]["description"]


def test_partdesign_publication_material_and_appearance_are_explicit_and_immutable() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    card = api.material(
        "0051bddf-6f62-4406-b8c9-569322880564",
        require_physical_properties=["Density"],
    )
    white = api.appearance(
        card,
        color_rgb=[255, 255, 255],
        line_color_rgb=[32, 64, 96],
        point_color_rgb=[255, 0, 0],
        transparency_percent=7,
        line_width=2.5,
        point_size=3.5,
        display_mode="Flat Lines",
        visible=True,
        selectable=False,
    )
    publication = api.publish(
        api.box(2, 3, 4),
        material=card,
        appearance=white,
        label="Leather Cover",
    )
    payload = publication.to_payload()
    presentation = payload["properties"]

    assert (card.operation, card.output_type) == ("material", "material_card")
    assert (white.operation, white.output_type) == ("appearance", "appearance")
    assert white.properties["shape_color"] == (1.0, 1.0, 1.0)
    assert white.properties["line_color"] == (
        32.0 / 255.0,
        64.0 / 255.0,
        96.0 / 255.0,
    )
    assert white.properties["point_color"] == (1.0, 0.0, 0.0)
    assert presentation["material"]["arguments"][0] == (
        "0051bddf-6f62-4406-b8c9-569322880564"
    )
    assert presentation["appearance"]["arguments"][0]["operation"] == "material"
    assert presentation["appearance"]["properties"]["transparency"] == 7
    with pytest.raises(TypeError):
        white.properties["shape_color"] = (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda api: api.appearance(color_rgb=[256, 0, 0]),
            "inclusive range 0-255",
        ),
        (
            lambda api: api.appearance(color_rgb=[1.0, 0, 0]),
            "must be an integer",
        ),
        (
            lambda api: api.appearance(),
            "at least one display change",
        ),
        (
            lambda api: api.publish(api.box(1, 1, 1), material=object()),
            "returned by api.material",
        ),
        (
            lambda api: api.publish(api.box(1, 1, 1), appearance=object()),
            "returned by api.appearance",
        ),
    ],
)
def test_partdesign_presentation_rejects_ambiguous_or_invalid_values(
    call,
    message: str,
) -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    with pytest.raises((TypeError, ValueError), match=message):
        call(api)


def test_unified_api_disambiguates_sketch_curves_and_standalone_3d_curves() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)

    sketch_line = api.line([0, 0], [1, 0])
    spatial_line = api.line_3d([0, 0, 0], [1, 0, 0])
    primitive = api.box(2, 3, 4)

    assert (sketch_line.operation, sketch_line.output_type) == (
        "line",
        "sketch_geometry",
    )
    assert (spatial_line.operation, spatial_line.output_type) == ("line", "edge")
    assert (primitive.operation, primitive.output_type) == ("box", "solid")
    assert {sketch_line.domain, spatial_line.domain, primitive.domain} == {
        "partdesign"
    }


def test_standalone_lofts_publish_as_solids_or_explicit_compounds() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    first = api.loft(
        [
            api.sketch([api.circle([0, 0], 1)]),
            api.sketch([api.circle([0, 0], 1)], z_offset_mm=2),
        ],
        operation="new_solid",
    )
    second = api.loft(
        [
            api.sketch([api.circle([4, 0], 1)]),
            api.sketch([api.circle([4, 0], 1)], z_offset_mm=2),
        ],
        operation="new_solid",
    )
    stitching = api.compound([first, second])
    check = api.measure(stitching, "solid_count", expected=2)

    assert first.operation == "standalone_loft"
    assert stitching.operation == "model_compound"
    assert api.publish(stitching, checks=[check]).output_type == "compound"
    with pytest.raises(ValueError, match="must be a value returned.*solid"):
        api.body(stitching)


def test_unified_api_rejects_impossible_topology_claims_and_zero_directions() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    edge = api.line_3d([0, 0, 0], [1, 0, 0])

    with pytest.raises(ValueError, match="all be solids"):
        api.boolean([edge, edge], operation="union", output_type="solid")
    with pytest.raises(ValueError, match="must be non-zero"):
        api.linear_pattern(api.box(1, 1, 1), 2, 2, direction=[0, 0, 0])
    with pytest.raises(ValueError, match="must be non-zero"):
        api.mirror(api.box(1, 1, 1), plane_normal=[0, 0, 0])
    with pytest.raises(ValueError, match="must be a solid"):
        api.polar_pattern(edge, 3, result="union")


def test_topology_editing_accepts_stable_queries_and_keeps_index_compatibility() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    solid = api.box(4, 5, 6)
    top = api.find_subelements(
        element_type="face",
        expected_count=1,
        geometry_type="plane",
        normal=[0, 0, 1],
    )

    selected = api.subshape(solid, "face", top)
    healed = api.defeature(solid, top)
    legacy = api.subshape(solid, "face", 1)

    assert (selected.operation, selected.output_type) == ("model_subshape", "face")
    assert (healed.operation, healed.output_type) == ("model_defeature", "solid")
    assert (legacy.operation, legacy.output_type) == ("subshape", "face")


def test_body_and_standalone_options_are_never_silently_reinterpreted() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    feature = api.extrude(
        api.sketch([api.circle([0, 0], 2)]),
        2,
        operation="add_material",
    )

    assert api.linear_pattern(feature, 2, 2).properties["result"] == "union"
    assert api.multi_transform(
        feature,
        [
            {"type": "translate", "vector": [2, 0, 0]},
            {"type": "translate", "vector": [2, 0, 0]},
        ],
    ).properties["result"] == "union"
    with pytest.raises(ValueError, match="standalone-shape settings"):
        api.polar_pattern(feature, 3, center=[1, 0, 0])
    with pytest.raises(ValueError, match="standalone shapes"):
        api.mirror(feature, plane_origin=[1, 0, 0])
    with pytest.raises(ValueError, match="vector is required"):
        api.extrude(
            api.line_3d([0, 0, 0], [1, 0, 0]),
            2,
            operation="new_surface",
        )
    with pytest.raises(ValueError, match="must be X, Y, or Z"):
        api.draft(
            feature,
            api.find_subelements(element_type="face", expected_count=1),
            2,
            pull_direction="N",
        )


def test_hole_validates_cut_geometry_before_native_execution() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    base = api.extrude(
        api.sketch([api.circle([0, 0], 5)]),
        5,
        operation="add_material",
    )
    profile = api.sketch([api.circle([0, 0], 1)], z_offset_mm=5)

    with pytest.raises(ValueError, match="greater than diameter_mm"):
        api.hole(
            base,
            profile,
            2,
            through_all=True,
            countersink_diameter_mm=2,
        )
    with pytest.raises(ValueError, match="less than 180"):
        api.hole(
            base,
            profile,
            2,
            through_all=True,
            countersink_diameter_mm=4,
            countersink_angle_degrees=180,
        )


def test_multi_transform_has_a_closed_llm_readable_step_contract() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    transformed = api.multi_transform(
        api.box(1, 1, 1),
        [
            {"type": "translate", "vector": [2, 0, 0]},
            {
                "type": "rotate",
                "origin": [0, 0, 0],
                "axis": [0, 0, 1],
                "angle_degrees": 90,
            },
            {"type": "mirror", "normal": [1, 0, 0]},
            {"type": "scale", "factor": 2},
        ],
    )
    steps = transformed.to_payload()["arguments"][1]

    assert steps[0] == {"type": "translate", "vector": [2.0, 0.0, 0.0]}
    assert steps[2]["origin"] == [0.0, 0.0, 0.0]
    assert steps[3] == {
        "type": "scale",
        "center": [0.0, 0.0, 0.0],
        "factor": 2.0,
    }
    with pytest.raises(ValueError, match="exactly type and vector"):
        api.multi_transform(
            api.box(1, 1, 1),
            [
                {"type": "translate", "vector": [1, 0, 0], "ignored": True},
                {"type": "scale", "factor": 2},
            ],
        )


def test_canonical_material_features_extend_an_existing_body_feature() -> None:
    api = PartDesignDomainAPI(
        PartDesignDomainAPI.exported_names,
        OUTPUT_TYPES,
    )
    base_profile = api.sketch([api.circle([0, 0], 10)])
    base = api.extrude(base_profile, 5, operation="add_material")
    boss_profile = api.sketch([api.circle([7, 0], 2)], z_offset_mm=5)
    boss = api.extrude(boss_profile, 3, operation="add_material", base=base)
    cut = api.extrude(
        boss_profile,
        operation="remove_material",
        base=boss,
        through_all=True,
    )
    revolved = api.revolve(
        boss_profile,
        operation="add_material",
        base=base,
        axis="V",
    )
    grooved = api.revolve(
        boss_profile,
        operation="remove_material",
        base=revolved,
        axis="V",
    )
    lofted = api.loft(
        [
            api.sketch([api.circle([0, 0], 3)], z_offset_mm=5),
            api.sketch([api.circle([0, 0], 2)], z_offset_mm=10),
        ],
        base=base,
    )
    patterned = api.polar_pattern(boss, 4)

    assert boss.properties["base"] is base
    assert base.operation == "pad"
    assert boss.operation == "pad"
    assert cut.operation == "pocket"
    assert revolved.properties["base"] is base
    assert grooved.operation == "groove"
    assert grooved.arguments[0] is revolved
    assert lofted.properties["base"] is base
    assert patterned.arguments[0] is boss
    assert api.body(patterned).output_type == "solid"


def test_legacy_material_names_remain_callable_but_are_not_exported() -> None:
    canonical = PartDesignDomainAPI(
        PartDesignDomainAPI.exported_names,
        OUTPUT_TYPES,
    )
    assert all(
        not hasattr(canonical, name) for name in ("pad", "pocket", "groove")
    )
    assert {"pad", "pocket", "groove"}.isdisjoint(dir(canonical))

    saved_source = create_domain_api(
        "partdesign",
        PartDesignDomainAPI.exported_names,
        OUTPUT_TYPES,
        compatibility_methods=("pad", "pocket", "groove"),
    )
    profile = saved_source.sketch([saved_source.circle([0, 0], 5)])
    base = saved_source.pad(profile, 5)
    cut_profile = saved_source.sketch(
        [saved_source.circle([0, 0], 2)],
        z_offset_mm=5,
    )

    assert saved_source.pocket(base, cut_profile, through_all=True).operation == (
        "pocket"
    )
    assert saved_source.groove(base, cut_profile).operation == "groove"
    assert {"pad", "pocket", "groove"}.isdisjoint(saved_source.exported_names)


def test_new_partdesign_source_cannot_use_saved_source_compatibility_calls(
    tmp_path: Path,
) -> None:
    source = (
        "profile = api.sketch([api.circle([0,0], inputs['radius'])])\n"
        "feature = api.pad(profile, inputs['height'])\n"
        "result = {'Part': api.body(feature)}\n"
    )
    capture = _capture(
        tmp_path,
        operation="create_program",
        arguments={
            "program_name": "Canonical API Required",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "radius": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["radius", "height"],
                "additionalProperties": False,
            },
            "inputs": {"radius": 4.0, "height": 5.0},
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )

    with pytest.raises(runtime.DomainRuntimeFailure) as failure:
        runtime.prepare_candidate(capture)

    assert failure.value.payload["failure_code"] == (
        "LEGACY_PARTDESIGN_API_NOT_AVAILABLE"
    )
    assert failure.value.payload["observed"]["retired_api_members"] == ["pad"]
    assert not (tmp_path / "vibescript").exists()


def test_edited_partdesign_source_must_finish_compatibility_migration(
    tmp_path: Path,
) -> None:
    source = (
        "profile = api.sketch([api.circle([0,0], inputs['radius'])])\n"
        "feature = api.pad(profile, inputs['height'], label='Original')\n"
        "result = {'Part': api.body(feature)}\n"
    )
    _directory, revision = _write_v2_program(tmp_path, source)
    capture = _capture(
        tmp_path,
        operation="edit_source",
        arguments={
            "program_id": PROGRAM_ID,
            "expected_revision": revision,
            "replacements": [{"old": "label='Original'", "new": "label='Edited'"}],
        },
    )

    with pytest.raises(runtime.DomainRuntimeFailure) as failure:
        runtime.prepare_candidate(capture)

    assert failure.value.payload["failure_code"] == (
        "LEGACY_PARTDESIGN_API_NOT_AVAILABLE"
    )
    assert failure.value.payload["observed"]["retired_api_members"] == ["pad"]


def test_unchanged_saved_partdesign_source_gets_private_compatibility_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        "profile = api.sketch([api.circle([0,0], inputs['radius'])])\n"
        "feature = api.pad(profile, inputs['height'])\n"
        "result = {'Part': api.body(feature)}\n"
    )
    _directory, revision = _write_v2_program(tmp_path, source)
    monkeypatch.setattr(runtime, "_freecadcmd", lambda _home: Path("/FreeCADCmd"))
    capture = _capture(
        tmp_path,
        operation="set_inputs",
        arguments={
            "program_id": PROGRAM_ID,
            "expected_revision": revision,
            "patch": {"height": 6.0},
        },
    )

    prepared = runtime.prepare_candidate(capture)
    assert prepared["source"] == source
    assert prepared["worker_request"]["compatibility_methods"] == ["pad"]
    runtime.abandon_prepared_candidate(prepared)


def test_saved_loft_subtractive_keyword_is_detected_but_new_source_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        "lower = api.sketch([api.circle([0,0], inputs['radius'])])\n"
        "upper = api.sketch([api.circle([0,0], inputs['radius'] / 2)], "
        "z_offset_mm=inputs['height'])\n"
        "base = api.extrude(lower, inputs['height'])\n"
        "feature = api.loft([lower, upper], base=base, subtractive=True)\n"
        "result = {'Part': api.body(feature)}\n"
    )
    create_capture = _capture(
        tmp_path,
        operation="create_program",
        arguments={
            "program_name": "Legacy Loft Rejected",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "radius": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["radius", "height"],
                "additionalProperties": False,
            },
            "inputs": {"radius": 4.0, "height": 5.0},
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    with pytest.raises(runtime.DomainRuntimeFailure) as failure:
        runtime.prepare_candidate(create_capture)
    assert failure.value.payload["observed"]["retired_api_members"] == [
        "loft_subtractive"
    ]

    _directory, revision = _write_v2_program(tmp_path, source)
    monkeypatch.setattr(runtime, "_freecadcmd", lambda _home: Path("/FreeCADCmd"))
    saved_capture = _capture(
        tmp_path,
        operation="set_inputs",
        arguments={
            "program_id": PROGRAM_ID,
            "expected_revision": revision,
            "patch": {"height": 6.0},
        },
    )
    prepared = runtime.prepare_candidate(saved_capture)
    assert prepared["worker_request"]["compatibility_methods"] == [
        "loft_subtractive"
    ]
    runtime.abandon_prepared_candidate(prepared)


def test_loft_legacy_keyword_is_private_to_unchanged_saved_source() -> None:
    api = PartDesignDomainAPI(PartDesignDomainAPI.exported_names, OUTPUT_TYPES)
    sections = [
        api.sketch([api.circle([0, 0], 3)]),
        api.sketch([api.circle([0, 0], 2)], z_offset_mm=5),
    ]

    with pytest.raises(TypeError, match="subtractive"):
        api.loft(
            sections,
            subtractive=True,
        )

    saved_source = create_domain_api(
        "partdesign",
        PartDesignDomainAPI.exported_names,
        OUTPUT_TYPES,
        compatibility_methods=("loft_subtractive",),
    )
    saved_sections = [
        saved_source.sketch([saved_source.circle([0, 0], 3)]),
        saved_source.sketch(
            [saved_source.circle([0, 0], 2)],
            z_offset_mm=5,
        ),
    ]
    base = saved_source.extrude(saved_sections[0], 5)
    with pytest.raises(ValueError, match="one consistent intent"):
        saved_source.loft(
            saved_sections,
            base=base,
            operation="add_material",
            subtractive=True,
        )
    legacy = saved_source.loft(
        saved_sections,
        subtractive=True,
        base=base,
    )
    assert legacy.properties["subtractive"] is True


def test_partdesign_rejects_cross_domain_graphs_and_transient_topology_names() -> None:
    api = PartDesignDomainAPI(
        PartDesignDomainAPI.exported_names,
        OUTPUT_TYPES,
    )
    foreign = DomainValue(
        domain="part",
        operation="box",
        output_type="solid",
        arguments=(),
        properties={},
    )
    with pytest.raises(ValueError, match="returned by this Part Design api"):
        api.body(foreign)
    base = api.extrude(
        api.sketch([api.circle([0, 0], 5)]),
        5,
        operation="add_material",
    )
    with pytest.raises(ValueError, match="transient FaceN/EdgeN names are forbidden"):
        api.body(
            base,
            interfaces={"Top": {"selection": "Face6"}},
        )


def test_v1_saved_data_migrates_to_a_non_executable_v2_view(tmp_path: Path) -> None:
    directory = _write_v1_program(tmp_path)
    migrated = domains.migrate_program_manifest(
        json.loads((directory / "manifest.json").read_text(encoding="utf-8")),
        artifact_directory=directory,
    )
    assert migrated["schema"] == domains.PROGRAM_SCHEMA
    assert migrated["version"] == 2
    assert migrated["program_id"] == PROGRAM_ID
    assert migrated["domain"] == "partdesign"
    assert migrated["artifact_directory"] == str(directory)
    assert migrated["migration_required"] is True
    assert migrated["migration_action"] == (
        "vibescript.partdesign.reconfigure_program"
    )
    assert migrated["accepted_revision"] == "saved-v1-revision"
    assert migrated["live_outputs"]["Part"]["object_name"] == "SavedPartResult"


def test_v1_source_cannot_edit_set_inputs_or_execute(tmp_path: Path) -> None:
    _write_v1_program(tmp_path)
    for operation, extra in (
        (
            "edit_source",
            {"replacements": [{"old": "output('Part')", "new": "output('Other')"}]},
        ),
        ("set_inputs", {"patch": {"radius": 5.0}}),
    ):
        capture = _capture(
            tmp_path,
            operation=operation,
            arguments={
                "program_id": PROGRAM_ID,
                "expected_revision": "saved-v1-revision",
                **extra,
            },
        )
        with pytest.raises(runtime.DomainRuntimeFailure) as failure:
            runtime.prepare_candidate(capture)
        assert failure.value.payload["failure_code"] == (
            "PROGRAM_RECONFIGURATION_REQUIRED"
        )
        assert failure.value.payload["retry"]["required_changes"] == [
            {
                "tool": "vibescript.partdesign.reconfigure_program",
                "expected_revision": "saved-v1-revision",
                "replace": [
                    "source",
                    "input_schema",
                    "inputs",
                    "expected_outputs",
                ],
            }
        ]
    assert not (tmp_path / "vibescript" / PROGRAM_ID / "program.json").exists()


def test_reconfigure_stages_v2_in_the_existing_saved_program_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _write_v1_program(tmp_path)
    monkeypatch.setattr(runtime, "_freecadcmd", lambda _home: Path("/FreeCADCmd"))
    source = (
        "profile = api.sketch([api.circle([0,0], inputs['radius'])])\n"
        "feature = api.extrude(profile, inputs['height'], operation='add_material')\n"
        "result = {'Part': api.body(feature, label='Part')}\n"
    )
    capture = _capture(
        tmp_path,
        operation="reconfigure_program",
        arguments={
            "program_id": PROGRAM_ID,
            "expected_revision": "saved-v1-revision",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "radius": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["radius", "height"],
                "additionalProperties": False,
            },
            "inputs": {"radius": 4.0, "height": 12.0},
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    prepared = runtime.prepare_candidate(capture)
    assert prepared["program_directory"] == str(directory)
    assert prepared["worker_request"]["domain"] == "partdesign"
    assert prepared["worker_request"]["source"] == source
    assert prepared["worker_request"]["compatibility_methods"] == []
    persisted = json.loads((directory / "program.json").read_text(encoding="utf-8"))
    assert persisted["schema"] == domains.PROGRAM_SCHEMA
    assert persisted["source"] == source
    assert persisted["working_revision"] == prepared["revision"]
    assert "migration_required" not in persisted
    assert "migration_action" not in persisted
    runtime.abandon_prepared_candidate(prepared)
