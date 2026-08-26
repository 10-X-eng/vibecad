# SPDX-License-Identifier: LGPL-2.1-or-later

"""Structured Assembly VibeScript authoring contract."""

from __future__ import annotations

import pytest


def _request() -> dict:
    return {
        "program_name": "Robot shoulder",
        "label": "Robot Shoulder Assembly",
        "grounded_component_key": "base",
        "components": [
            {
                "key": "base",
                "catalog_key": "component-1",
                "label": "Fixed Base",
            },
            {
                "key": "turret",
                "catalog_key": "component-2",
                "flexible_subassembly": True,
                "label": "Turret",
            },
        ],
        "joints": [
            {
                "key": "turret_axis",
                "first_key": "base",
                "first_interface": "BaseAxis",
                "second_key": "turret",
                "second_interface": "ShoulderAxis",
                "joint": {
                    "kind": "revolute",
                    "angle_limits_degrees": [-90.0, 90.0],
                },
                "label": "Turret Axis",
            }
        ],
        "simulation": {
            "motions": [
                {
                    "key": "turret_sweep",
                    "joint": "turret_axis",
                    "formula": "0.5*pi*sin(time)",
                }
            ],
            "end_time_s": 4.0,
            "time_step_s": 0.02,
            "label": "Turret Sweep",
        },
        "bom": {"label": "Robot Shoulder BOM"},
    }


def _geared_request() -> dict:
    return {
        "program_name": "Gear pair",
        "grounded_component_key": "base",
        "components": [
            {"key": "base", "catalog_key": "component-3"},
            {"key": "first_gear", "catalog_key": "component-1"},
            {"key": "second_gear", "catalog_key": "component-2"},
        ],
        "joints": [
            {
                "key": "first_bearing",
                "first_key": "base",
                "first_interface": "FirstBearing",
                "second_key": "first_gear",
                "second_interface": "GearAxis",
                "joint": {"kind": "revolute"},
            },
            {
                "key": "second_bearing",
                "first_key": "base",
                "first_interface": "SecondBearing",
                "second_key": "second_gear",
                "second_interface": "GearAxis",
                "joint": {"kind": "revolute"},
            },
            {
                "key": "gear_pair",
                "first_key": "first_gear",
                "first_interface": "GearAxis",
                "second_key": "second_gear",
                "second_interface": "GearAxis",
                "joint": {
                    "kind": "gear",
                    "radius1_mm": 20.0,
                    "radius2_mm": 40.0,
                },
            },
        ],
    }


def test_structured_assembly_program_compiles_portable_source() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    compiled = compile_assembly_program(_request())

    assert compiled["program_name"] == "Robot shoulder"
    assert compiled["input_schema"] == {
        "properties": {
            "base": {"type": "object", "x-vibecad-reference": True},
            "turret": {"type": "object", "x-vibecad-reference": True},
        },
        "required": ["base", "turret"],
        "additionalProperties": False,
    }
    assert compiled["inputs"] == {
        "base": {"catalog_key": "component-1"},
        "turret": {"catalog_key": "component-2"},
    }
    assert compiled["expected_outputs"] == [
        {"name": "assembly", "type": "assembly"},
        {"name": "solver_diagnostics", "type": "solver_diagnostics"},
        {"name": "simulation", "type": "simulation"},
        {"name": "bom", "type": "bom"},
    ]
    source = compiled["source"]
    assert "def main():" in source
    assert "api.component(inputs['base'], grounded=True" in source
    assert "api.component(inputs['turret'], flexible=True" in source
    assert "'interface_name': 'BaseAxis'" in source
    assert "'interface_name': 'ShoulderAxis'" in source
    assert "api.assembly(components, joints" in source
    assert "api.motion(turret_axis, '0.5*pi*sin(time)'" in source
    assert "api.simulation(model, motions" in source
    assert "api.bill_of_materials(model" in source
    assert "'catalog_key'" not in source
    compile(source, "<structured-assembly>", "exec")


def test_structured_assembly_program_derives_optional_program_name() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    request = _request()
    request.pop("program_name")

    compiled = compile_assembly_program(request)

    assert compiled["program_name"] == "Robot Shoulder Assembly"

    request.pop("label")
    compiled = compile_assembly_program(request)
    assert compiled["program_name"] == "Assembly"


def test_structured_assembly_treats_empty_optional_program_name_as_omitted() -> None:
    from VibeCADAssemblyGraphProgram import (
        assembly_program_tool_spec,
        compile_assembly_program,
    )
    from VibeCADTools import ToolSpec

    request = _request()
    request["program_name"] = ""

    ToolSpec.from_mapping(assembly_program_tool_spec()).validate_arguments(request)
    compiled = compile_assembly_program(request)

    assert compiled["program_name"] == "Robot Shoulder Assembly"


def test_structured_assembly_keeps_hyphenated_member_keys() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    request = _request()
    request["components"][0]["key"] = "fixed-base"
    request["components"][1]["key"] = "rotating-turret"
    request["grounded_component_key"] = "fixed-base"
    request["joints"][0].update(
        key="base-turret-axis",
        first_key="fixed-base",
        second_key="rotating-turret",
    )
    request["simulation"]["motions"][0].update(
        key="turret-sweep",
        joint="base-turret-axis",
    )

    compiled = compile_assembly_program(request)

    assert "fixed_base = api.component(inputs['fixed-base']" in compiled["source"]
    assert "'fixed-base': fixed_base" in compiled["source"]
    assert "'base-turret-axis': base_turret_axis" in compiled["source"]
    assert "'turret-sweep': turret_sweep" in compiled["source"]
    assert set(compiled["input_schema"]["properties"]) == {
        "fixed-base",
        "rotating-turret",
    }


def test_structured_assembly_accepts_numbered_occurrence_keys() -> None:
    from VibeCADAssemblyGraphProgram import (
        assembly_program_tool_spec,
        compile_assembly_program,
    )

    request = _request()
    request["components"][0]["key"] = "01_Fixed_Base"
    request["grounded_component_key"] = "01_Fixed_Base"
    request["joints"][0]["first_key"] = "01_Fixed_Base"

    compiled = compile_assembly_program(request)

    assert "component_01_Fixed_Base = api.component" in compiled["source"]
    assert "'01_Fixed_Base': component_01_Fixed_Base" in compiled["source"]
    spec = assembly_program_tool_spec()["parameters"]
    assert spec["properties"]["grounded_component_key"]["pattern"] == (
        "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )


def test_structured_assembly_uses_natural_singular_gear_name() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    compiled = compile_assembly_program(_geared_request())

    assert "api.joint('gears'" in compiled["source"]


def test_structured_assembly_rejects_gear_without_rotating_dependencies() -> None:
    from VibeCADAssemblyGraphProgram import (
        AssemblyGraphProgramError,
        compile_assembly_program,
    )

    request = _request()
    request.pop("simulation")
    request.pop("bom")
    request["joints"][0]["joint"] = {
        "kind": "gear",
        "radius1_mm": 20.0,
        "radius2_mm": 40.0,
    }

    with pytest.raises(AssemblyGraphProgramError) as raised:
        compile_assembly_program(request)

    failure = raised.value
    assert str(failure) == (
        "joints[0] gear requires one distinct revolute joint at each interface"
    )
    assert failure.path == ["joints", 0]
    assert failure.observed == {
        "joint_key": "turret_axis",
        "grounded_component_keys": ["base"],
        "endpoints": [
            {
                "component_key": "base",
                "interface": "BaseAxis",
                "revolute_joint_keys": [],
            },
            {
                "component_key": "turret",
                "interface": "ShoulderAxis",
                "revolute_joint_keys": [],
            },
        ],
    }
    assert failure.required_changes == [
        {
            "path": ["joints", 0],
            "require_distinct_revolute_at_each_interface": True,
            "unground_component_keys": ["base"],
        }
    ]


def test_structured_assembly_accepts_gear_on_a_moving_support() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    request = _geared_request()
    request["components"].append(
        {"key": "carrier", "catalog_key": "component-4"}
    )
    request["joints"].insert(
        0,
        {
            "key": "carrier_bearing",
            "first_key": "base",
            "first_interface": "CarrierAxis",
            "second_key": "carrier",
            "second_interface": "Axis",
            "joint": {"kind": "revolute"},
        },
    )
    request["joints"][1].update(
        first_key="carrier",
        first_interface="FirstBearing",
    )

    compiled = compile_assembly_program(request)

    assert "carrier_bearing = api.joint('revolute'" in compiled["source"]
    assert "gear_pair = api.joint('gears'" in compiled["source"]


def test_structured_assembly_compiles_initial_occurrence_placement() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    request = _request()
    request["components"][1]["placement"] = {
        "position": [125.0, 0.0, 40.0],
        "axis": [0.0, 0.0, 1.0],
        "angle_degrees": 90.0,
    }

    compiled = compile_assembly_program(request)

    assert (
        "api.component(inputs['turret'], placement={'position': [125.0, 0.0, 40.0], "
        "'axis': [0.0, 0.0, 1.0], 'angle_degrees': 90.0}"
    ) in compiled["source"]


def test_structured_assembly_requires_joint_specific_values() -> None:
    from VibeCADAssemblyGraphProgram import (
        AssemblyGraphProgramError,
        compile_assembly_program,
    )

    request = _geared_request()
    request["joints"][2]["joint"] = {"kind": "gear"}

    with pytest.raises(
        AssemblyGraphProgramError,
        match=r"joints\[2\] gear requires radius1_mm and radius2_mm",
    ):
        compile_assembly_program(request)


def test_structured_assembly_uses_published_pitch_radii() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    request = _geared_request()
    request["joints"][2]["joint"] = {"kind": "gear"}
    catalog = {
        "candidates": [
            {
                "catalog_key": "component-1",
                "interfaces": [
                    {
                        "name": "GearAxis",
                        "connector": {
                            "allowed_joints": ["revolute", "gears"],
                            "compatibility": {"gears": "GEAR-PAIR"},
                            "pitch_radius_mm": 20.0,
                        },
                    }
                ],
            },
            {
                "catalog_key": "component-2",
                "interfaces": [
                    {
                        "name": "GearAxis",
                        "connector": {
                            "allowed_joints": ["revolute", "gears"],
                            "compatibility": {"gears": "GEAR-PAIR"},
                            "pitch_radius_mm": 40.0,
                        },
                    }
                ],
            },
        ]
    }

    compiled = compile_assembly_program(request, component_catalog=catalog)

    assert "radius1_mm=20.0, radius2_mm=40.0" in compiled["source"]


@pytest.mark.parametrize(
    ("second_connector", "message"),
    [
        (
            {"allowed_joints": ["fixed"], "compatibility": "AXIS-A"},
            "connector 2 explicitly disallows joint type 'revolute'",
        ),
        (
            {"allowed_joints": ["revolute"], "compatibility": "AXIS-B"},
            "explicit connector compatibility tokens do not match",
        ),
    ],
)
def test_structured_assembly_preflights_connector_contracts(
    second_connector: dict,
    message: str,
) -> None:
    from VibeCADAssemblyGraphProgram import (
        AssemblyGraphProgramError,
        compile_assembly_program,
    )

    request = _request()
    request.pop("simulation")
    request.pop("bom")
    catalog = {
        "candidates": [
            {
                "catalog_key": "component-1",
                "interfaces": [
                    {
                        "name": "BaseAxis",
                        "connector": {
                            "allowed_joints": ["revolute", "fixed"],
                            "compatibility": "AXIS-A",
                        },
                    }
                ],
            },
            {
                "catalog_key": "component-2",
                "interfaces": [
                    {
                        "name": "ShoulderAxis",
                        "connector": second_connector,
                    }
                ],
            },
        ]
    }

    with pytest.raises(AssemblyGraphProgramError, match=message) as raised:
        compile_assembly_program(request, component_catalog=catalog)

    failure = raised.value
    assert failure.path == ["joints", 0, "joint", "kind"]
    assert failure.observed == {
        "joint_kind": "revolute",
        "first": {
            "component_key": "base",
            "interface": "BaseAxis",
            "connector": {
                "allowed_joints": ["revolute", "fixed"],
                "compatibility": "AXIS-A",
            },
        },
        "second": {
            "component_key": "turret",
            "interface": "ShoulderAxis",
            "connector": second_connector,
        },
    }
    assert failure.allowed_values == (["fixed"] if "disallows" in message else [])
    assert failure.required_changes == (
        [
            {
                "path": ["joints", 0, "joint", "kind"],
                "allowed_values": ["fixed"],
            }
        ]
        if "disallows" in message
        else [{"path": ["joints", 0], "change_endpoints": True}]
    )


def test_structured_assembly_preflights_published_interface_names() -> None:
    from VibeCADAssemblyGraphProgram import (
        AssemblyGraphProgramError,
        compile_assembly_program,
    )

    request = _request()
    request.pop("simulation")
    request.pop("bom")
    request["joints"][0]["second_interface"] = "MissingAxis"
    catalog = {
        "candidates": [
            {
                "catalog_key": "component-1",
                "interfaces": [{"name": "BaseAxis", "connector": {}}],
            },
            {
                "catalog_key": "component-2",
                "interfaces": [
                    {"name": "ShoulderAxis", "connector": {}},
                    {"name": "Mount", "connector": {}},
                ],
            },
        ]
    }

    with pytest.raises(AssemblyGraphProgramError) as raised:
        compile_assembly_program(request, component_catalog=catalog)

    failure = raised.value
    assert str(failure) == (
        "joints[0].second_interface is not published by component 'turret'"
    )
    assert failure.path == ["joints", 0, "second_interface"]
    assert failure.observed == {
        "component_key": "turret",
        "received": "MissingAxis",
    }
    assert failure.allowed_values == ["ShoulderAxis", "Mount"]


def test_structured_assembly_rejects_values_for_another_joint_kind() -> None:
    from VibeCADAssemblyGraphProgram import (
        AssemblyGraphProgramError,
        compile_assembly_program,
    )

    request = _request()
    request.pop("simulation")
    request.pop("bom")
    request["joints"][0]["joint"]["angle_degrees"] = 0.0

    with pytest.raises(
        AssemblyGraphProgramError,
        match=r"joints\[0\] revolute does not accept angle_degrees",
    ):
        compile_assembly_program(request)


def test_structured_assembly_reports_self_joint_repair() -> None:
    from VibeCADAssemblyGraphProgram import (
        AssemblyGraphProgramError,
        compile_assembly_program,
    )

    request = _request()
    request.pop("simulation")
    request.pop("bom")
    request["joints"][0].update(
        second_key="base",
        second_interface="BaseAxis",
    )

    with pytest.raises(AssemblyGraphProgramError) as raised:
        compile_assembly_program(request)

    failure = raised.value
    assert failure.path == ["joints", 0, "second_key"]
    assert failure.allowed_values == ["turret"]
    assert failure.required_changes == [
        {
            "path": ["joints", 0, "second_key"],
            "allowed_values": ["turret"],
        }
    ]


def test_structured_assembly_derives_joint_key_from_label() -> None:
    from VibeCADAssemblyGraphProgram import compile_assembly_program

    request = _request()
    request["joints"][0].pop("key")
    request.pop("simulation")
    request.pop("bom")

    compiled = compile_assembly_program(request)

    assert "Turret_Axis = api.joint('revolute'" in compiled["source"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda request: request["components"][1].update(key="base"),
            "component keys must be unique",
        ),
        (
            lambda request: request.update(grounded_component_key="missing"),
            "grounded_component_key references unknown component 'missing'",
        ),
        (
            lambda request: request["joints"][0].update(
                second_key="missing"
            ),
            "unknown component 'missing'",
        ),
        (
            lambda request: request["simulation"]["motions"][0].update(
                joint="missing"
            ),
            "unknown joint 'missing'",
        ),
    ],
)
def test_structured_assembly_program_rejects_invalid_graph(mutate, message) -> None:
    from VibeCADAssemblyGraphProgram import (
        AssemblyGraphProgramError,
        compile_assembly_program,
    )

    request = _request()
    mutate(request)
    with pytest.raises(AssemblyGraphProgramError, match=message):
        compile_assembly_program(request)


def test_model_assembly_surface_has_structured_assembly_creation() -> None:
    import VibeCADVibeScriptDomains as domains

    assembly = domains.get_vibescript_pack("AssemblyWorkbench")
    model = domains.get_vibescript_pack("PartDesignWorkbench")
    assert assembly is not None and model is not None
    assert "vibescript.create_assembly" in assembly.provider_tool_names
    assert "vibescript.create_part" in assembly.provider_tool_names
    assert "vibescript.create_program" not in assembly.provider_tool_names
    assert "vibescript.create_assembly" in model.provider_tool_names
    assert "vibescript.create_part" in model.provider_tool_names
    assert "vibescript.create_program" not in model.provider_tool_names
    assert assembly.provider_tool_names == model.provider_tool_names

    spec = next(
        item
        for item in domains.universal_tool_specs()
        if item["name"] == "vibescript.create_assembly"
    )
    assert spec["description"] == (
        "Create or replace one complete assembly definition."
    )
    parameters = spec["parameters"]
    assert parameters["required"] == [
        "grounded_component_key",
        "components",
        "joints",
    ]
    assert parameters["properties"]["replace"] == {
        "description": "Existing assembly source to replace.",
        "type": "object",
        "properties": {
            "program": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "pattern": "^[^/]+(?:/[^/]+/[^/]+)?$",
            },
            "expected_revision": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
        },
        "required": ["program", "expected_revision"],
        "additionalProperties": False,
    }
    assert parameters["properties"]["components"]["description"] == (
        "All occurrences; repeat catalog_key for repeated parts."
    )
    assert parameters["properties"]["joints"]["description"] == (
        "All joints between occurrences."
    )
    assert parameters["properties"]["bom"]["properties"]["only_parts"] == {
        "description": "Part containers and subassemblies only.",
        "type": "boolean",
    }
    component_properties = parameters["properties"]["components"]["items"][
        "properties"
    ]
    assert "grounded" not in component_properties
    assert "placement" in component_properties
    assert "flexible" not in component_properties
    assert "flexible_subassembly" in component_properties

    part_spec = next(
        item
        for item in domains.universal_tool_specs()
        if item["name"] == "vibescript.create_part"
    )
    part_types = part_spec["parameters"]["properties"]["expected_outputs"][
        "items"
    ]["properties"]["type"]["enum"]
    assert "solid" in part_types
    assert "component_link" not in part_types
    assert set(parameters["properties"]) == {
        "program_name",
        "replace",
        "label",
        "grounded_component_key",
        "components",
        "joints",
        "simulation",
        "bom",
    }
    joint = parameters["properties"]["joints"]["items"]
    assert joint["required"] == [
        "first_key",
        "first_interface",
        "second_key",
        "second_interface",
        "joint",
    ]
    variants = {}
    for item in joint["properties"]["joint"]["oneOf"]:
        variants.setdefault(item["properties"]["kind"]["const"], []).append(item)
    assert set(variants) == {
        "fixed",
        "revolute",
        "cylindrical",
        "slider",
        "ball",
        "distance",
        "parallel",
        "perpendicular",
        "angle",
        "rack_pinion",
        "screw",
        "gear",
        "belt",
    }
    revolute = variants["revolute"][0]
    assert revolute["required"] == ["kind"]
    assert "angle_limits_degrees" in revolute["properties"]
    assert "angle_degrees" not in revolute["properties"]
    assert len(variants["gear"]) == 1
    gear = variants["gear"][0]
    assert gear["required"] == ["kind"]
    assert {"radius1_mm", "radius2_mm"} <= set(gear["properties"])
    assert "first_name" not in joint["properties"]
    assert "first_path" not in joint["properties"]
    formula = parameters["properties"]["simulation"]["properties"]["motions"][
        "items"
    ]["properties"]["formula"]
    assert "time" in formula["description"]
    simulation_description = parameters["properties"]["simulation"]["description"]
    assert "revolute, slider, or cylindrical" in simulation_description
    assert "time, initialValue, pi" in simulation_description


def test_structured_assembly_reports_an_unknown_joint_kind_directly() -> None:
    from VibeCADAssemblyGraphProgram import assembly_program_tool_spec
    from VibeCADTools import ToolArgumentValidationError, ToolSpec

    spec = ToolSpec.from_mapping(assembly_program_tool_spec())
    arguments = {
        "components": [
            {"key": "base", "catalog_key": "component-1"},
            {"key": "gear", "catalog_key": "component-2"},
        ],
        "grounded_component_key": "base",
        "joints": [
            {
                "first_key": "base",
                "first_interface": "Axis",
                "second_key": "gear",
                "second_interface": "Axis",
                "joint": {"kind": "gears"},
            }
        ],
    }

    with pytest.raises(ToolArgumentValidationError) as raised:
        spec.validate_arguments(arguments)

    failure = raised.value.payload
    assert failure["observed"]["path"] == ["joints", 0, "joint", "kind"]
    assert failure["observed"]["received"] == "gears"
    assert failure["allowed_values"] == [
        "fixed",
        "revolute",
        "cylindrical",
        "slider",
        "ball",
        "distance",
        "parallel",
        "perpendicular",
        "angle",
        "rack_pinion",
        "screw",
        "gear",
        "belt",
    ]
    assert failure["error"].endswith(
        "joints.0.joint.kind: 'gears' is not an allowed value"
    )


def test_structured_assembly_returns_exact_preflight_repair_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADAssemblyGraphProgram as graph
    import VibeCADSession as session

    def reject(*_args, **_kwargs):
        raise graph.AssemblyGraphProgramError(
            "joints[4] has incompatible interfaces",
            path=("joints", 4, "joint", "kind"),
            observed={"joint_kind": "fixed"},
            allowed_values=("revolute",),
        )

    monkeypatch.setattr(graph, "compile_assembly_program", reject)
    document = type(
        "Document",
        (),
        {"Name": "Fixture", "FileName": "/project/fixture.FCStd", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()

    result = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.create_assembly",
        _request(),
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["failure_code"] == "ASSEMBLY_GRAPH_INVALID"
    assert result["observed"] == {
        "path": ["joints", 4, "joint", "kind"],
        "joint_kind": "fixed",
    }
    assert result["allowed_values"] == ["revolute"]
    assert result["retry"]["required_changes"] == [
        {
            "path": ["joints", 4, "joint", "kind"],
            "allowed_values": ["revolute"],
        }
    ]


def test_structured_assembly_routes_through_portable_program_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session
    from VibeCADComponentCatalog import prepare_captured_component_catalog

    references = [
        {"document_uid": "fixture-uid", "object_name": "FixedBase"},
        {"document_uid": "fixture-uid", "object_name": "Turret"},
    ]
    catalog = prepare_captured_component_catalog(
        {
            "owner_document_uid": "fixture-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": [
                {
                    "label": label,
                    "kind": "definition",
                    "reference": reference,
                }
                for label, reference in zip(("Fixed Base", "Turret"), references)
            ],
        }
    )
    observed = {}

    def run_internal(_service, tool_name, arguments, **_kwargs):
        observed.update(tool_name=tool_name, arguments=arguments)
        return {
            "ok": True,
            "tool": tool_name,
            "program_id": "a" * 32,
            "working_revision": "b" * 64,
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Name": "Fixture", "FileName": "/project/fixture.FCStd", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    request = _request()
    request.pop("simulation")
    request.pop("bom")

    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.create_assembly",
        request,
        component_catalog=catalog,
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert observed["tool_name"] == "vibescript.assembly.create_program"
    assert observed["arguments"]["inputs"] == {
        "base": references[0],
        "turret": references[1],
    }
    assert "catalog_key" not in observed["arguments"]["source"]
    assert result["tool"] == "vibescript.create_assembly"
    assert result["source_id"] == "a" * 32


def test_structured_assembly_failure_retries_the_graph_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session
    from VibeCADComponentCatalog import prepare_captured_component_catalog

    catalog = prepare_captured_component_catalog(
        {
            "owner_document_uid": "fixture-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": [
                {
                    "label": "Fixed Base",
                    "kind": "definition",
                    "reference": {
                        "document_uid": "fixture-uid",
                        "object_name": "FixedBase",
                    },
                },
                {
                    "label": "Turret",
                    "kind": "definition",
                    "reference": {
                        "document_uid": "fixture-uid",
                        "object_name": "Turret",
                    },
                },
            ],
        }
    )

    def run_internal(_service, tool_name, _arguments, **_kwargs):
        return {
            "ok": False,
            "tool": tool_name,
            "error": "The requested joint violates an interface contract.",
            "working_revision": "b" * 64,
            "next_actions": [
                {"tool": "vibescript.read_source"},
                {"tool": "vibescript.edit_source"},
            ],
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Name": "Fixture", "FileName": "/project/fixture.FCStd", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    request = _request()
    request.pop("simulation")
    request.pop("bom")

    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.create_assembly",
        request,
        component_catalog=catalog,
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is False
    assert result["tool"] == "vibescript.create_assembly"
    assert result["next_actions"] == [
        {
            "tool": "vibescript.create_assembly",
            "target_arguments": {
                "replace": {
                    "program": "Fixture/assembly/Robot shoulder",
                    "expected_revision": "b" * 64,
                }
            },
        }
    ]


def test_structured_assembly_can_replace_a_failed_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session
    from VibeCADComponentCatalog import prepare_captured_component_catalog

    source_id = "a" * 32
    revision = "b" * 64
    program = "Fixture/assembly/Robot shoulder"
    references = [
        {"document_uid": "fixture-uid", "object_name": "FixedBase"},
        {"document_uid": "fixture-uid", "object_name": "Turret"},
    ]
    catalog = prepare_captured_component_catalog(
        {
            "owner_document_uid": "fixture-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": [
                {
                    "label": label,
                    "kind": "definition",
                    "reference": reference,
                }
                for label, reference in zip(("Fixed Base", "Turret"), references)
            ],
        }
    )
    observed = {}

    def run_internal(_service, tool_name, arguments, **_kwargs):
        observed.update(tool_name=tool_name, arguments=arguments)
        return {
            "ok": True,
            "tool": tool_name,
            "program_id": source_id,
            "working_revision": "c" * 64,
        }

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Name": "Fixture", "FileName": "/project/fixture.FCStd", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()
    request = _request()
    request.pop("simulation")
    request.pop("bom")
    request["replace"] = {"program": program, "expected_revision": revision}

    result = session._run_universal_vibescript_tool(
        service,
        "PartDesignWorkbench",
        "vibescript.create_assembly",
        request,
        component_catalog=catalog,
        editable_sources={
            "domain": "partdesign",
            "authoring_domains": ["partdesign", "assembly"],
            "sources": [
                {
                    "source_id": source_id,
                    "program": program,
                    "label": "Robot shoulder",
                    "domain": "assembly",
                    "current_revision": revision,
                    "affected_outputs": [],
                }
            ]
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is True, result
    assert observed["tool_name"] == "vibescript.assembly.reconfigure_program"
    assert observed["arguments"]["program_id"] == source_id
    assert observed["arguments"]["expected_revision"] == revision
    assert "program_name" not in observed["arguments"]
    assert result["tool"] == "vibescript.create_assembly"
    assert result["source_id"] == source_id


def test_structured_assembly_name_collision_returns_exact_replace_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    revision = "b" * 64
    program = "Fixture/assembly/Robot shoulder"
    called = False

    def run_internal(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(session, "_run_domain_vibescript_tool", run_internal)
    document = type(
        "Document",
        (),
        {"Name": "Fixture", "FileName": "/project/fixture.FCStd", "Objects": []},
    )()
    service = type("Service", (), {"_active_document": lambda self: document})()

    result = session._run_universal_vibescript_tool(
        service,
        "AssemblyWorkbench",
        "vibescript.create_assembly",
        _request(),
        editable_sources={
            "domain": "assembly",
            "sources": [
                {
                    "source_id": "a" * 32,
                    "program": program,
                    "label": "Robot shoulder",
                    "domain": "assembly",
                    "current_revision": revision,
                    "affected_outputs": [],
                }
            ],
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert called is False
    assert result["failure_code"] == "ASSEMBLY_REPLACEMENT_REQUIRED"
    assert result["next_actions"] == [
        {
            "tool": "vibescript.create_assembly",
            "target_arguments": {
                "replace": {
                    "program": program,
                    "expected_revision": revision,
                }
            },
        }
    ]
