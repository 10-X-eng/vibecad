# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider scope follows durable Analyze study state, not geometry guesses."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from VibeCADNativeAnalyzeProviderScope import (
    analyze_provider_tool_names,
    scope_analyze_provider_surface,
)
from VibeCADNativeAnalyzeFluidSchema import analyze_fluid_capability_definition
from VibeCADNativeAnalyzeFluidCreateSchema import (
    ANALYZE_EDIT_FLUID_BOUNDARY,
    analyze_fluid_create_capability_definitions,
)
from VibeCADNativeAnalyzeCfdLifecycleSchema import (
    analyze_cfd_lifecycle_capability_definitions,
)
from VibeCADNativeAnalyzeFaceSchema import analyze_face_capability_definition
from VibeCADNativeAnalyzeConnectionSchema import (
    analyze_connection_capability_definition,
)
from VibeCADNativeAnalyzeFlowResultSchema import (
    ANALYZE_COMPARE_FLOW,
    ANALYZE_FLOW_PERFORMANCE,
    ANALYZE_FLOW_RESULT,
    ANALYZE_SHOW_FLOW,
)
from VibeCADNativeAnalyzeInspectSchema import (
    ANALYZE_MATERIAL_CATALOG,
    analyze_inspect_capability_definition,
    analyze_material_catalog_capability_definition,
)
from VibeCADNativeAnalyzeGeometrySchema import analyze_geometry_capability_definition
from VibeCADNativeAnalyzeMeshLifecycleSchema import (
    ANALYZE_EDIT_GMSH_MESH,
    ANALYZE_FLOW_MESH,
    ANALYZE_SOLID_MESH,
    analyze_mesh_lifecycle_capability_definitions,
)
from VibeCADNativeAnalyzeMeshSchema import analyze_mesh_capability_definition
from VibeCADNativeAnalyzeLocalMeshSchema import (
    ANALYZE_EDIT_LOCAL_MESH_SIZE,
    ANALYZE_LOCAL_MESH_SIZE,
    analyze_local_mesh_capability_definitions,
)
from VibeCADNativeAnalyzeMechanicalResultSchema import (
    ANALYZE_MECHANICAL_RESULTS,
    ANALYZE_SHOW_MECHANICAL,
    analyze_mechanical_result_capability_definitions,
)
from VibeCADNativeAnalyzeMechanicalResultBindings import _mechanical_fields, _summary
from VibeCADNativeAnalyzeResultState import _vtk_field_unit, _vtk_unit_system
from VibeCADNativeAnalyzeThermalResultSchema import (
    ANALYZE_SHOW_TEMPERATURE,
    ANALYZE_TEMPERATURE_RESULTS,
)
from VibeCADNativeAnalyzeAssignmentViewSchema import (
    analyze_assignment_view_capability_definition,
)
from VibeCADNativeAnalyzeInspectBindings import _focused_material_catalog_result
from VibeCADNativeAnalyzeMaterials import (
    resolve_material_card_name,
    search_material_catalog,
)
from VibeCADNativeAnalyzeModelSchema import analyze_model_capability_definition
from VibeCADNativeAnalyzeSolidDomainSchema import ANALYZE_SOLID_DOMAIN
from VibeCADNativeAnalyzeStructuralLifecycleSchema import (
    ANALYZE_CATALOG_MATERIAL,
    ANALYZE_CENTRIFUGAL,
    ANALYZE_CUSTOM_MATERIAL,
    ANALYZE_EDIT_CENTRIFUGAL,
    ANALYZE_EDIT_DISPLACEMENT_SUPPORT,
    ANALYZE_EDIT_FIXED_SUPPORT,
    ANALYZE_EDIT_FORCE,
    ANALYZE_EDIT_GRAVITY,
    ANALYZE_EDIT_PRESSURE,
    ANALYZE_EDIT_RIGID_COUPLING,
    ANALYZE_EDIT_SPRING_SUPPORT,
    ANALYZE_DISPLACEMENT_SUPPORT,
    ANALYZE_FIXED_SUPPORT,
    ANALYZE_FORCE,
    ANALYZE_GRAVITY,
    ANALYZE_PRESSURE,
    ANALYZE_RIGID_COUPLING,
    ANALYZE_SOLID_REGION_MATERIAL,
    ANALYZE_SOLID_MATERIAL,
    ANALYZE_SPRING_SUPPORT,
    analyze_structural_lifecycle_capability_definitions,
)
from VibeCADNativeAnalyzeSolverSchema import analyze_solver_capability_definition
from VibeCADNativeAnalyzeSupportSchema import analyze_support_capability_definition
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
)
from VibeCADNativeCommonSchema import common_capability_definitions
from VibeCADNativeProviderContext import provider_authorized_native_surface
from VibeCADNativeSurface import NativeSurfaceSnapshot


_SHARED = {
    "core.capture_view_screenshot",
    "document.query",
    "document.save",
    "document.undo",
    "object.properties",
    "selection.query",
    "view.control",
    "workspace.switch",
}

_ANALYZE = {
    "analyze.model",
    ANALYZE_SOLID_DOMAIN,
    "analyze.faces",
    "analyze.inspect",
    "analyze.material_catalog",
    "analyze.geometry",
    "analyze.electromagnetic",
    "analyze.fluid",
    "analyze.initial_velocity",
    "analyze.initial_pressure",
    "analyze.boundary_velocity",
    "analyze.fluid_boundary",
    "analyze.edit_fluid_boundary",
    "analyze.fluid_material",
    "analyze.openfoam_solver",
    "analyze.flow_results",
    "analyze.flow_performance",
    "analyze.compare_flow",
    "analyze.show_flow",
    "analyze.mechanical_results",
    "analyze.show_mechanical",
    "analyze.temperature_results",
    "analyze.show_temperature",
    "analyze.solid_material",
    "analyze.solid_region_material",
    "analyze.catalog_material",
    "analyze.custom_material",
    "analyze.fixed_support",
    "analyze.edit_fixed_support",
    "analyze.rigid_coupling",
    "analyze.edit_rigid_coupling",
    "analyze.displacement_support",
    "analyze.edit_displacement_support",
    "analyze.spring_support",
    "analyze.edit_spring_support",
    "analyze.force",
    "analyze.edit_force",
    "analyze.pressure",
    "analyze.edit_pressure",
    "analyze.gravity",
    "analyze.edit_gravity",
    "analyze.centrifugal_load",
    "analyze.edit_centrifugal_load",
    "analyze.geometrical",
    "analyze.support",
    "analyze.connection",
    "analyze.load",
    "analyze.thermal",
    "analyze.mesh",
    "analyze.gmsh_mesh",
    "analyze.solid_mesh",
    "analyze.flow_mesh",
    "analyze.edit_gmsh_mesh",
    "analyze.generate_gmsh",
    "analyze.mesh_field",
    "analyze.mesh_output",
    "analyze.mesh_refinement",
    "analyze.local_mesh_size",
    "analyze.edit_local_mesh_size",
    "analyze.structured_mesh",
    "analyze.solver",
    "analyze.solver_control",
    "analyze.solver_execution",
    "analyze.run_solver",
    "analyze.equation",
    "analyze.results",
    "analyze.presentation",
    "analyze.post",
    "analyze.post_function",
    "analyze.visualization",
    "native.job",
}


def test_assignment_view_identifies_eligible_provider_targets() -> None:
    definition = analyze_assignment_view_capability_definition()
    highlight = next(
        variant for variant in definition.variants if variant.operation == "highlight"
    )

    assert definition.description == (
        "Highlight or isolate one material, support, connection, load, "
        "boundary condition, or mesh refinement."
    )
    assert highlight.parameters["properties"]["assignment"]["description"] == (
        "Copy target from materials, support_conditions, connections, loads, "
        "thermal_conditions, fluid_constraints, or mesh_refinements."
    )

_AVAILABLE = tuple(sorted(_SHARED | _ANALYZE))


def _domain(
    *physics: str,
    analysis_count: int = 1,
    mesh_count: int = 0,
    generated_mesh_count: int = 0,
    solver_count: int = 0,
    result_count: int = 0,
    geometry_source_count: int = 0,
) -> dict:
    workflows = []
    if analysis_count:
        workflows.append(
            {
                "study": (
                    {
                        "declared": True,
                        "physics": list(physics),
                        "regime": "steady",
                        "schema_version": 1,
                    }
                    if physics
                    else {"declared": False}
                ),
                "study_inventory": {
                    "mesh_definition_count": mesh_count,
                    "generated_mesh_count": generated_mesh_count,
                    "solver_count": solver_count,
                    "result_count": result_count,
                },
            }
        )
    result = {
        "kind": "analyze",
        "analysis_count": analysis_count,
        "analysis_workflow_count": analysis_count,
        "analysis_workflows": workflows,
        "geometry_source_count": geometry_source_count,
        "provider_scope": {
            "analysis_count": analysis_count,
            "undeclared_analysis_count": analysis_count if not physics else 0,
            "physics": list(physics),
            "mesh_definition_count": mesh_count,
            "generated_mesh_count": generated_mesh_count,
            "solver_count": solver_count,
            "result_count": result_count,
        },
    }
    if solver_count:
        result["solvers"] = [
            {"solver_kind": "openfoam" if physics == ("fluid",) else "calculix"}
        ]
    return result


def _names(domain: dict) -> set[str]:
    return set(analyze_provider_tool_names(domain, _AVAILABLE))


def test_new_analysis_surface_contains_only_setup_and_observation() -> None:
    names = _names(_domain(analysis_count=0))

    assert {"analyze.model", ANALYZE_MATERIAL_CATALOG} <= names
    assert "analyze.inspect" not in names
    assert not ({"analyze.fluid", "analyze.load", "analyze.mesh"} & names)
    assert "workspace.switch" not in names
    assert (_SHARED - {"workspace.switch"}) <= names


def test_face_reader_appears_only_when_exact_geometry_exists() -> None:
    assert "analyze.faces" not in _names(_domain(analysis_count=0))
    assert "analyze.faces" in _names(
        _domain(analysis_count=0, geometry_source_count=1)
    )


def test_multipart_domain_tool_appears_only_for_multiple_current_sources() -> None:
    assert ANALYZE_SOLID_DOMAIN not in _names(
        _domain(analysis_count=0, geometry_source_count=1)
    )
    assert ANALYZE_SOLID_DOMAIN in _names(
        _domain(analysis_count=0, geometry_source_count=3)
    )


def test_declared_study_exposes_only_its_physics_and_core_lifecycle() -> None:
    fluid = _names(_domain("fluid"))
    mechanical = _names(_domain("mechanical"))

    assert {
        "analyze.initial_velocity",
        "analyze.initial_pressure",
        "analyze.fluid_boundary",
        "analyze.fluid_material",
        "analyze.openfoam_solver",
        ANALYZE_FLOW_MESH,
    } <= fluid
    assert not (
        {
            "analyze.boundary_velocity",
            "analyze.geometry",
            "analyze.load",
            "analyze.solver",
            "analyze.support",
            "analyze.thermal",
        }
        & fluid
    )
    assert "analyze.inspect" not in fluid
    assert {
        "analyze.geometry",
        ANALYZE_CATALOG_MATERIAL,
        ANALYZE_CUSTOM_MATERIAL,
        ANALYZE_FIXED_SUPPORT,
        ANALYZE_RIGID_COUPLING,
        ANALYZE_DISPLACEMENT_SUPPORT,
        ANALYZE_SPRING_SUPPORT,
        ANALYZE_FORCE,
        ANALYZE_PRESSURE,
        ANALYZE_GRAVITY,
        ANALYZE_CENTRIFUGAL,
        ANALYZE_SOLID_MESH,
        "analyze.connection",
        "analyze.solver",
    } <= mechanical
    assert "analyze.support" not in mechanical
    assert "analyze.gmsh_mesh" not in mechanical
    assert ANALYZE_FLOW_MESH not in mechanical
    assert ANALYZE_SOLID_MESH not in fluid
    assert "analyze.gmsh_mesh" not in fluid
    assert "analyze.load" not in mechanical
    assert not (
        {"analyze.fluid", "analyze.thermal", "analyze.electromagnetic"} & mechanical
    )


def test_fully_conformal_shared_domain_omits_redundant_connections() -> None:
    domain = _domain("mechanical", geometry_source_count=1)
    domain["connection_count"] = 0
    domain["geometry_sources"] = [
        {
            "object_name": "SolidAnalysisDomain",
            "interface_mode": "shared",
            "all_solids_conformal": True,
            "topology": {"solids": 3, "faces": 18, "edges": 36},
        }
    ]

    assert "analyze.connection" not in _names(domain)


def test_focused_structural_setup_names_current_objects_and_geometry() -> None:
    definitions = {
        definition.name: definition
        for definition in analyze_structural_lifecycle_capability_definitions()
    }

    assert "zero displacement" in definitions[ANALYZE_FIXED_SUPPORT].description
    support = analyze_support_capability_definition()
    assert support.description == (
        "Create rigid couplings, prescribed displacements, and springs; "
        "edit existing support conditions."
    )
    rigid_body = next(
        variant for variant in support.variants if variant.operation == "create_rigid_body"
    )
    assert "reference node" in rigid_body.description

    connection = analyze_connection_capability_definition()
    tie = next(
        variant for variant in connection.variants if variant.operation == "create_tie"
    )
    assert tie.parameters["properties"]["slave"]["description"] == (
        "Dependent mating face; choose the smaller or finer surface."
    )
    assert tie.parameters["properties"]["master"]["description"] == (
        "Independent mating face; choose the larger or coarser surface."
    )

    material = definitions[ANALYZE_SOLID_MATERIAL].variants[0].parameters
    assert set(material["properties"]) == {
        "analysis_name",
        "source_name",
        "material",
    }
    assert material["required"] == [
        "analysis_name",
        "source_name",
        "material",
    ]
    sources = material["properties"]["material"]["oneOf"]
    assert [branch["properties"]["kind"]["const"] for branch in sources] == [
        "catalog",
        "custom",
    ]
    assert "yield_strength_mpa" in sources[1]["properties"]["properties"]["properties"]

    catalog_material = definitions[ANALYZE_CATALOG_MATERIAL].variants[0].parameters
    assert catalog_material["required"] == [
        "analysis_name",
        "source_name",
        "material_name",
    ]
    assert set(catalog_material["properties"]) == {
        "analysis_name",
        "source_name",
        "material_name",
    }
    custom_material = definitions[ANALYZE_CUSTOM_MATERIAL].variants[0].parameters
    assert custom_material["required"] == [
        "analysis_name",
        "source_name",
        "properties",
    ]
    assert "solid_regions" not in custom_material["properties"]
    custom_properties = custom_material["properties"]["properties"]
    assert custom_properties["minProperties"] == 1
    assert "required" not in custom_properties
    assert {
        "density_kg_m3",
        "young_modulus_mpa",
        "poisson_ratio",
        "yield_strength_mpa",
        "thermal_conductivity_w_m_k",
        "thermal_expansion_per_k",
        "reference_temperature_k",
        "specific_heat_j_kg_k",
    } == set(custom_properties["properties"])

    region_material = definitions[ANALYZE_SOLID_REGION_MATERIAL].variants[0].parameters
    assert set(region_material["properties"]) == {
        "analysis_name",
        "source_name",
        "solid_regions",
        "material",
    }
    assert region_material["properties"]["solid_regions"]["description"] == (
        "SolidN regions receiving this material."
    )

    fixed = definitions[ANALYZE_FIXED_SUPPORT].variants[0].parameters
    assert set(fixed["properties"]) == {
        "analysis_name",
        "source_name",
        "subelement_names",
    }

    rigid = definitions[ANALYZE_RIGID_COUPLING].variants[0]
    assert rigid.operation == "create"
    assert set(rigid.parameters["properties"]) == {
        "analysis_name",
        "source_name",
        "subelement_names",
        "reference_node_mm",
        "translation",
        "rotation",
    }
    assert "operation" not in rigid.parameters["properties"]
    assert set(rigid.parameters["properties"]["rotation"]["properties"]) == {
        "x",
        "y",
        "z",
    }

    displacement = definitions[ANALYZE_DISPLACEMENT_SUPPORT].variants[0]
    assert displacement.operation == "create"
    assert set(displacement.parameters["properties"]) == {
        "analysis_name",
        "source_name",
        "subelement_names",
        "translation",
        "rotation",
        "flow_surface_force",
    }

    spring = definitions[ANALYZE_SPRING_SUPPORT].variants[0]
    assert spring.operation == "create"
    assert set(spring.parameters["properties"]) == {
        "analysis_name",
        "source_name",
        "face_names",
        "normal_stiffness_n_m",
        "tangential_stiffness_n_m",
        "elmer_component",
    }

    for name in (
        ANALYZE_EDIT_FIXED_SUPPORT,
        ANALYZE_EDIT_RIGID_COUPLING,
        ANALYZE_EDIT_DISPLACEMENT_SUPPORT,
        ANALYZE_EDIT_SPRING_SUPPORT,
    ):
        edit_support = definitions[name].variants[0]
        assert edit_support.operation == "edit"
        assert "operation" not in edit_support.parameters["properties"]

    force = definitions[ANALYZE_FORCE].variants[0].parameters
    assert set(force["properties"]) == {
        "analysis_name",
        "source_name",
        "subelement_names",
        "force_vector_n",
    }
    assert force["properties"]["subelement_names"]["description"] == (
        "VertexN, EdgeN, or FaceN receiving the force."
    )
    assert force["properties"]["force_vector_n"]["description"] == (
        "Signed force vector in newtons."
    )

    pressure = definitions[ANALYZE_PRESSURE].variants[0].parameters
    assert pressure["required"] == [
        "analysis_name",
        "source_name",
        "subelement_names",
        "pressure_pa",
        "reversed",
    ]
    gravity = definitions[ANALYZE_GRAVITY].variants[0].parameters
    assert gravity["required"] == [
        "analysis_name",
        "acceleration_m_s2",
        "direction",
    ]
    centrifugal = definitions[ANALYZE_CENTRIFUGAL].variants[0].parameters
    assert centrifugal["required"] == [
        "analysis_name",
        "rotation_frequency_hz",
        "axis",
    ]
    assert "scope" in centrifugal["properties"]

    assert definitions[ANALYZE_EDIT_PRESSURE].variants[0].operation == "edit"
    assert definitions[ANALYZE_EDIT_GRAVITY].variants[0].operation == "edit"
    assert definitions[ANALYZE_EDIT_CENTRIFUGAL].variants[0].operation == "edit"
    edit = definitions[ANALYZE_EDIT_FORCE].variants[0]
    assert edit.operation == "edit"
    assert set(edit.parameters["properties"]) == {"load_name", "changes"}
    assert set(edit.parameters["properties"]["changes"]["properties"]) == {
        "force_vector_n",
        "applied_to",
    }

    from VibeCADNativeAnalyzeStructuralLifecycleBindings import _force_from_vector

    magnitude, direction = _force_from_vector({"x": 0, "y": -1000, "z": 0})
    assert magnitude == 1000.0
    assert direction == {"kind": "vector", "x": 0.0, "y": -1.0, "z": 0.0}


def test_solid_mesh_presents_its_quadratic_default_first() -> None:
    definitions = {
        definition.name: definition
        for definition in analyze_mesh_lifecycle_capability_definitions()
    }
    element_order = definitions[ANALYZE_SOLID_MESH].variants[0].parameters[
        "properties"
    ]["element_order"]

    assert element_order["default"] == "second"
    assert element_order["enum"] == ["second", "first"]


def test_material_search_uses_its_fixed_safe_result_bound_by_default() -> None:
    definition = analyze_inspect_capability_definition()
    catalog = next(
        variant for variant in definition.variants if variant.operation == "material_catalog"
    )

    assert catalog.parameters["required"] == ["query", "category"]
    assert catalog.parameters["properties"]["limit"]["default"] == 25

    mesh_elements = next(
        variant
        for variant in definition.variants
        if variant.operation == "fem_mesh_elements"
    )
    assert mesh_elements.parameters["required"] == ["target", "element_kind"]
    assert mesh_elements.parameters["properties"]["offset"]["default"] == 0
    assert mesh_elements.parameters["properties"]["page_size"]["default"] == 64

    focused = analyze_material_catalog_capability_definition().variants[0]
    assert focused.operation == "search"
    assert focused.parameters["required"] == ["query", "category"]
    assert focused.parameters["properties"]["category"]["enum"] == [
        "solid",
        "fluid",
        "any",
    ]

    mesh_elements = next(
        variant
        for variant in definition.variants
        if variant.operation == "fem_mesh_elements"
    )
    assert mesh_elements.parameters["properties"]["target"]["description"] == (
        "Copy target from a generated mesh in mesh_definitions."
    )


def test_whole_source_material_tools_do_not_duplicate_region_assignment() -> None:
    single = _domain("mechanical", geometry_source_count=1)
    single.update(
        {
            "geometry_sources": [
                {"source_name": "Part", "topology": {"solids": 1}}
            ],
            "geometry_sources_truncated": False,
        }
    )
    multi = _domain("mechanical", geometry_source_count=1)
    multi.update(
        {
            "geometry_sources": [
                {"source_name": "Part", "topology": {"solids": 3}}
            ],
            "geometry_sources_truncated": False,
        }
    )

    single_names = _names(single)
    assert {ANALYZE_CATALOG_MATERIAL, ANALYZE_CUSTOM_MATERIAL} <= single_names
    assert ANALYZE_SOLID_MATERIAL not in single_names
    assert ANALYZE_SOLID_REGION_MATERIAL not in single_names

    multi_names = _names(multi)
    assert {ANALYZE_CATALOG_MATERIAL, ANALYZE_CUSTOM_MATERIAL} <= multi_names
    assert ANALYZE_SOLID_MATERIAL not in multi_names
    assert ANALYZE_SOLID_REGION_MATERIAL in multi_names


def test_material_search_ranks_relevant_words_in_an_ordinary_query(
    monkeypatch,
) -> None:
    cards = {
        "steel": SimpleNamespace(
            UUID="11111111-1111-1111-1111-111111111111",
            Name="Structural Steel",
            Directory="Solid/Metals",
            Description="General structural steel",
            Tags=("steel", "metal"),
            Properties={"Name": "Structural Steel", "YoungsModulus": "210000 MPa"},
        ),
        "aluminum": SimpleNamespace(
            UUID="22222222-2222-2222-2222-222222222222",
            Name="Aluminum Alloy",
            Directory="Solid/Metals",
            Description="General aluminum alloy",
            Tags=("aluminum", "metal"),
            Properties={"Name": "Aluminum Alloy", "YoungsModulus": "70000 MPa"},
        ),
    }
    monkeypatch.setitem(
        sys.modules,
        "Materials",
        SimpleNamespace(
            MaterialManager=lambda: SimpleNamespace(Materials=cards)
        ),
    )

    result = search_material_catalog(
        "Steel cantilever beam material properties",
        "solid",
        25,
    )

    assert [material["name"] for material in result["materials"]] == [
        "Structural Steel"
    ]

    uuid, properties = resolve_material_card_name(
        "Structural Steel",
        category="solid",
    )
    assert uuid == "11111111-1111-1111-1111-111111111111"
    assert properties["Name"] == "Structural Steel"


def test_material_search_excludes_appearance_cards_and_exact_names_resolve(
    monkeypatch,
) -> None:
    cards = {
        "appearance": SimpleNamespace(
            UUID="11111111-1111-1111-1111-111111111111",
            Name="Steel",
            Directory="Appearance/Metal",
            Description="Steel appearance",
            Tags=("steel",),
            Properties={"Name": "Steel", "DiffuseColor": "0.4,0.4,0.4"},
        ),
        "upper": SimpleNamespace(
            UUID="22222222-2222-2222-2222-222222222222",
            Name="Steel-Grade",
            Directory="Solid/Metals",
            Description="Upper-case engineering card",
            Tags=("steel",),
            Properties={
                "Name": "Steel-Grade",
                "YoungsModulus": "210000 MPa",
                "PoissonRatio": "0.3",
            },
        ),
        "lower": SimpleNamespace(
            UUID="33333333-3333-3333-3333-333333333333",
            Name="steel-grade",
            Directory="Solid/Metals",
            Description="Lower-case engineering card",
            Tags=("steel",),
            Properties={
                "Name": "steel-grade",
                "YoungsModulus": "200000 MPa",
                "PoissonRatio": "0.29",
            },
        ),
    }
    monkeypatch.setitem(
        sys.modules,
        "Materials",
        SimpleNamespace(MaterialManager=lambda: SimpleNamespace(Materials=cards)),
    )

    result = search_material_catalog("steel", "solid", 25)

    assert [material["name"] for material in result["materials"]] == [
        "Steel-Grade",
        "steel-grade",
    ]
    upper_uuid, _properties = resolve_material_card_name(
        "Steel-Grade",
        category="solid",
    )
    lower_uuid, _properties = resolve_material_card_name(
        "steel-grade",
        category="solid",
    )
    assert upper_uuid == "22222222-2222-2222-2222-222222222222"
    assert lower_uuid == "33333333-3333-3333-3333-333333333333"


def test_focused_material_catalog_returns_assignment_field_names() -> None:
    result = _focused_material_catalog_result(
        {
            "query": "steel",
            "match_count": 1,
            "returned_count": 1,
            "truncated": False,
            "materials": [
                {
                    "uuid": "11111111-1111-1111-1111-111111111111",
                    "name": "Steel-1C45",
                    "category": "solid",
                    "description": "Medium-carbon steel",
                    "properties": {"young_modulus_mpa": 210000.0},
                }
            ],
        }
    )

    assert result["materials"] == [
        {
            "material_name": "Steel-1C45",
            "material_uuid": "11111111-1111-1111-1111-111111111111",
            "category": "solid",
            "description": "Medium-carbon steel",
            "properties": {"young_modulus_mpa": 210000.0},
        }
    ]

def test_focused_mechanical_results_have_one_obvious_read_and_view_call() -> None:
    definitions = {
        definition.name: definition
        for definition in analyze_mechanical_result_capability_definitions()
    }

    read = definitions[ANALYZE_MECHANICAL_RESULTS].variants[0]
    show = definitions[ANALYZE_SHOW_MECHANICAL].variants[0]

    assert read.operation == "read"
    assert set(read.parameters["properties"]) == {"result_name"}
    assert show.operation == "show"
    assert set(show.parameters["properties"]) == {"result_name", "field"}
    assert show.parameters["properties"]["field"]["enum"] == [
        "von_mises_stress",
        "displacement_magnitude",
    ]


def test_calculix_mechanical_results_publish_freecad_engineering_units() -> None:
    state = {
        "result_kind": "pipeline",
        "fields": [
            {
                "name": "von Mises Stress",
                "semantic": "von_mises_stress",
                "unit": "Pa",
                "range": [1.0e6, 114.4e6],
            },
            {
                "name": "Displacement",
                "semantic": "displacement_magnitude",
                "unit": "m",
                "range": [0.0, 0.000789],
            },
        ],
    }

    fields = _mechanical_fields(state, solver_kind="calculix")

    assert [(field["semantic"], field["unit"]) for field in fields] == [
        ("von_mises_stress", "MPa"),
        ("displacement_magnitude", "mm"),
    ]
    assert [field["range"] for field in fields] == [
        [1.0, 114.4],
        [0.0, 0.789],
    ]


def test_modern_calculix_pipeline_preserves_native_engineering_units() -> None:
    modern = SimpleNamespace(
        VibeCADTimelineOwner=SimpleNamespace(
            Proxy=SimpleNamespace(Type="Fem::SolverCalculiX")
        )
    )
    legacy = SimpleNamespace(
        VibeCADTimelineOwner=SimpleNamespace(
            Proxy=SimpleNamespace(Type="Fem::SolverCcxTools")
        )
    )

    assert _vtk_unit_system(modern) == "freecad_engineering"
    assert _vtk_field_unit("Displacement", unit_system="freecad_engineering") == "mm"
    assert _vtk_field_unit("von Mises Stress", unit_system="freecad_engineering") == "MPa"
    assert _vtk_unit_system(legacy) == "si"
    assert _vtk_field_unit("Displacement", unit_system="si") == "m"
    assert _vtk_field_unit("von Mises Stress", unit_system="si") == "Pa"


def test_mechanical_result_maximum_names_its_exact_result_position() -> None:
    class Array:
        def GetNumberOfTuples(self):
            return 3

        def GetNumberOfComponents(self):
            return 1

        def GetTuple1(self, index):
            return (10.0, 80.0, 30.0)[index]

    class Attributes:
        def GetArray(self, name):
            return Array() if name == "vonmises" else None

    class Dataset:
        def GetPointData(self):
            return Attributes()

        def GetPoint(self, index):
            return ((200.0, 0.0, 0.0), (0.0, 0.0, 10.0), (100.0, 0.0, 0.0))[index]

    class Result:
        def getDataSet(self):
            return Dataset()

    summary = _summary(
        [
            {
                "name": "vonmises",
                "association": "point",
                "components": 1,
                "semantic": "von_mises_stress",
                "range": [10.0, 80.0],
                "unit": "MPa",
            }
        ],
        result=Result(),
    )

    assert summary["maximum_von_mises_stress"] == {
        "value": 80.0,
        "unit": "MPa",
        "result_position_mm": {"x": 0.0, "y": 0.0, "z": 10.0},
    }


def test_focused_fluid_boundary_names_faces_on_one_exact_source() -> None:
    definition = next(
        value
        for value in analyze_fluid_create_capability_definitions()
        if value.name == "analyze.fluid_boundary"
    )
    parameters = definition.variants[0].parameters

    assert definition.description == "Create an adiabatic CFD condition on exact faces."
    assert set(parameters["properties"]) == {
        "analysis_name",
        "source_name",
        "face_names",
        "condition",
        "turbulence",
        "label",
    }
    assert parameters["required"] == [
        "analysis_name",
        "source_name",
        "face_names",
        "condition",
    ]
    assert parameters["properties"]["analysis_name"]["description"] == (
        "Analysis object name."
    )
    assert parameters["properties"]["source_name"]["description"] == (
        "Geometry object name."
    )
    assert parameters["properties"]["face_names"]["items"]["pattern"].startswith(
        "^Face"
    )
    condition_branches = parameters["properties"]["condition"]["oneOf"]
    condition_kinds = {
        branch["properties"]["kind"]["const"]: branch["properties"]["kind"]
        for branch in condition_branches
    }
    assert condition_kinds["outlet_total_pressure"]["description"] == (
        "Stagnation pressure."
    )
    assert condition_kinds["outlet_static_pressure"]["description"] == (
        "Static gauge pressure."
    )
    turbulence = parameters["properties"]["turbulence"]
    assert turbulence["properties"]["kind"]["const"] == "intensity_length_scale"
    assert set(turbulence["properties"]) == {
        "kind",
        "intensity_ratio",
        "length_scale_m",
    }


def test_focused_openfoam_solver_names_the_supported_momentum_models() -> None:
    definition = next(
        value
        for value in analyze_cfd_lifecycle_capability_definitions()
        if value.name == "analyze.openfoam_solver"
    )
    parameters = definition.variants[0].parameters

    assert parameters["properties"]["momentum_model"] == {
        "type": "string",
        "enum": ["laminar", "k_omega_sst"],
        "default": "laminar",
    }


def test_focused_face_reader_accepts_a_bounded_hundred_face_page() -> None:
    page_size = analyze_face_capability_definition().variants[0].parameters[
        "properties"
    ]["page_size"]

    assert page_size["maximum"] == 128


def test_focused_cfd_mesh_uses_the_supported_first_order_elements() -> None:
    definitions = {
        value.name: value for value in analyze_mesh_lifecycle_capability_definitions()
    }
    flow = definitions[ANALYZE_FLOW_MESH].variants[0]
    flow_parameters = flow.parameters
    assert "element_order" not in flow_parameters["properties"]
    definition = definitions[ANALYZE_SOLID_MESH]
    parameters = definition.variants[0].parameters
    element_order = parameters["properties"]["element_order"]
    maximum_size = parameters["properties"]["maximum_size_mm"]
    editable = definitions[ANALYZE_EDIT_GMSH_MESH].variants[0]
    editable_maximum_size = editable.parameters["properties"][
        "maximum_size_mm"
    ]

    assert element_order["enum"] == ["second", "first"]
    assert element_order["default"] == "second"
    assert "bending" in element_order["description"]
    assert "maximum_size_mm" in parameters["required"]
    assert maximum_size["exclusiveMinimum"] == 0.0
    assert editable_maximum_size["exclusiveMinimum"] == 0.0
    assert editable.parameters["properties"]["element_order"]["enum"] == [
        "second",
        "first",
    ]
    assert editable.parameters["properties"]["source_name"]["description"] == (
        "Replacement geometry object name; changing it invalidates the generated mesh."
    )
    assert editable.operation == "edit"
    assert editable.parameters["minProperties"] == 2


def test_local_mesh_size_has_one_natural_create_and_edit_contract() -> None:
    definitions = {
        definition.name: definition
        for definition in analyze_local_mesh_capability_definitions()
    }
    create = definitions[ANALYZE_LOCAL_MESH_SIZE].variants[0]
    edit = definitions[ANALYZE_EDIT_LOCAL_MESH_SIZE].variants[0]

    assert create.operation == "create"
    assert set(create.parameters["properties"]) == {
        "mesh_name",
        "source_name",
        "subelement_names",
        "element_size_mm",
    }
    assert create.parameters["required"] == [
        "mesh_name",
        "source_name",
        "subelement_names",
        "element_size_mm",
    ]
    assert edit.operation == "edit"
    assert set(edit.parameters["properties"]) == {"refinement_name", "changes"}
    assert "operation" not in create.parameters["properties"]
    assert "operation" not in edit.parameters["properties"]


def test_local_mesh_size_edit_appears_only_for_an_existing_region() -> None:
    domain = _domain("mechanical", mesh_count=1)
    domain.update(
        {
            "mesh_definitions": [{"mesher": "gmsh"}],
            "mesh_refinement_count": 0,
            "mesh_refinements": [],
            "mesh_refinements_truncated": False,
        }
    )
    assert ANALYZE_LOCAL_MESH_SIZE in _names(domain)
    assert ANALYZE_EDIT_LOCAL_MESH_SIZE not in _names(domain)

    domain["mesh_refinement_count"] = 1
    domain["mesh_refinements"] = [{"refinement_mode": "region"}]
    assert ANALYZE_EDIT_LOCAL_MESH_SIZE in _names(domain)


def test_advanced_mesh_solver_and_post_tools_follow_exact_artifacts() -> None:
    declared = _names(_domain("thermal"))
    meshed = _names(_domain("thermal", mesh_count=1, generated_mesh_count=1))
    solved = _names(
        _domain(
            "thermal",
            mesh_count=1,
            generated_mesh_count=1,
            solver_count=1,
            result_count=1,
        )
    )

    assert not ({"analyze.mesh_field", "analyze.mesh_output"} & declared)
    assert {
        "analyze.mesh_field",
        "analyze.mesh_refinement",
        "analyze.structured_mesh",
    } <= meshed
    assert "analyze.solver_control" not in meshed
    assert {
        "analyze.solver_control",
        "analyze.run_solver",
    } <= solved
    assert "analyze.equation" not in solved
    assert {
        ANALYZE_TEMPERATURE_RESULTS,
        ANALYZE_SHOW_TEMPERATURE,
    } <= solved
    assert ANALYZE_MECHANICAL_RESULTS not in solved
    assert ANALYZE_SHOW_MECHANICAL not in solved
    assert not {
        "analyze.results",
        "analyze.presentation",
        "analyze.post",
        "analyze.post_function",
        "analyze.visualization",
    } & solved

    elmer_domain = _domain(
        "thermal",
        mesh_count=1,
        generated_mesh_count=1,
        solver_count=1,
    )
    elmer_domain["solvers"] = [{"solver_kind": "elmer"}]
    assert "analyze.equation" in _names(elmer_domain)

    fluid_solved = _names(
        _domain(
            "fluid",
            mesh_count=1,
            generated_mesh_count=1,
            solver_count=1,
            result_count=1,
        )
    )
    assert "analyze.flow_results" in fluid_solved
    assert ANALYZE_FLOW_PERFORMANCE in fluid_solved
    assert ANALYZE_COMPARE_FLOW not in fluid_solved
    assert "analyze.show_flow" in fluid_solved
    assert not {
        "analyze.results",
        "analyze.presentation",
        "analyze.post",
        "analyze.post_function",
        "analyze.visualization",
    } & fluid_solved

    fluid_comparable = _names(
        _domain(
            "fluid",
            mesh_count=1,
            generated_mesh_count=1,
            solver_count=1,
            result_count=2,
        )
    )
    assert ANALYZE_COMPARE_FLOW in fluid_comparable


def test_fluid_singleton_creation_tools_disappear_after_creation() -> None:
    meshed_domain = _domain("fluid", mesh_count=1)
    meshed_domain["mesh_definitions"] = [{"mesher": "gmsh"}]
    generated_domain = _domain("fluid", mesh_count=1, generated_mesh_count=1)
    generated_domain["mesh_definitions"] = [{"mesher": "gmsh"}]
    solved_domain = _domain("fluid", mesh_count=1, solver_count=1)
    solved_domain["solvers"] = [{"solver_kind": "openfoam"}]
    meshed = _names(meshed_domain)
    generated = _names(generated_domain)
    solved = _names(solved_domain)

    assert ANALYZE_FLOW_MESH not in meshed
    assert "analyze.gmsh_mesh" not in meshed
    assert "analyze.edit_gmsh_mesh" in meshed
    assert "analyze.generate_gmsh" in meshed
    assert "analyze.generate_gmsh" in generated
    assert "analyze.mesh" not in meshed
    assert "analyze.openfoam_solver" not in solved


def test_background_job_control_appears_only_for_a_live_analyze_job() -> None:
    idle = _domain("fluid", mesh_count=1, generated_mesh_count=1, solver_count=1)
    running = dict(idle)
    running["run_status"] = {
        "phase": "running",
        "job_id": "a" * 32,
        "terminal": False,
    }

    assert "native.job" not in _names(idle)
    assert "native.job" in _names(running)
    assert "analyze.run_solver" in _names(idle)
    assert "analyze.run_solver" not in _names(running)
    assert "analyze.generate_gmsh" not in _names(running)


def test_independent_background_jobs_keep_exact_job_control_available() -> None:
    running = _domain(
        "mechanical",
        mesh_count=1,
        generated_mesh_count=1,
        solver_count=1,
    )
    running["run_status"] = {
        "phase": "running",
        "terminal": False,
        "background_jobs": [
            {
                "phase": "running",
                "job_id": "a" * 32,
                "resource_scope": "analyze:StudyA",
                "terminal": False,
            },
            {
                "phase": "completed",
                "job_id": "b" * 32,
                "resource_scope": "analyze:StudyB",
                "terminal": True,
            },
        ],
    }

    assert "native.job" in _names(running)


def test_scoped_solver_job_does_not_block_another_independent_study() -> None:
    running = _domain(
        "mechanical",
        mesh_count=1,
        generated_mesh_count=1,
        solver_count=1,
    )
    running["run_status"] = {
        "phase": "running",
        "job_id": "a" * 32,
        "resource_scope": "analyze:StudyA",
        "terminal": False,
    }

    names = _names(running)

    assert "native.job" in names
    assert "analyze.run_solver" in names
    assert "analyze.generate_gmsh" not in names


def test_multiple_studies_compose_declared_physics_without_guessing() -> None:
    domain = _domain("mechanical")
    domain["analysis_count"] = 2
    domain["analysis_workflow_count"] = 2
    domain["analysis_workflows"].append(
        {
            "study": {
                "declared": True,
                "physics": ["thermal"],
                "regime": "transient",
                "schema_version": 1,
            },
            "study_inventory": {
                "mesh_definition_count": 0,
                "generated_mesh_count": 0,
                "solver_count": 0,
                "result_count": 0,
            },
        }
    )

    names = _names(domain)

    assert {
        ANALYZE_FORCE,
        ANALYZE_PRESSURE,
        ANALYZE_GRAVITY,
        ANALYZE_CENTRIFUGAL,
        ANALYZE_FIXED_SUPPORT,
        ANALYZE_RIGID_COUPLING,
        ANALYZE_DISPLACEMENT_SUPPORT,
        ANALYZE_SPRING_SUPPORT,
        "analyze.thermal",
    } <= names
    assert "analyze.fluid" not in names


def test_incomplete_snapshot_fails_closed_to_setup_tools() -> None:
    names = _names(
        {
            "kind": "analyze",
            "analysis_count": 1,
            "analysis_workflow_count": 1,
            "analysis_workflows": [],
        }
    )

    assert names == (_SHARED - {"workspace.switch"}) | {
        "analyze.model",
        ANALYZE_MATERIAL_CATALOG,
    }


def test_provider_scope_covers_analyses_beyond_the_detailed_snapshot_page() -> None:
    domain = _domain("mechanical")
    domain["analysis_count"] = 1000
    domain["analysis_workflow_count"] = 1000
    domain["provider_scope"] = {
        "analysis_count": 1000,
        "undeclared_analysis_count": 0,
        "physics": ["fluid", "thermal"],
        "mesh_definition_count": 500,
        "generated_mesh_count": 400,
        "solver_count": 300,
        "result_count": 200,
    }

    names = _names(domain)

    assert {
        "analyze.fluid",
        "analyze.thermal",
        ANALYZE_FLOW_RESULT,
        ANALYZE_SHOW_FLOW,
        ANALYZE_TEMPERATURE_RESULTS,
        ANALYZE_SHOW_TEMPERATURE,
    } <= names
    assert ANALYZE_MECHANICAL_RESULTS not in names
    assert ANALYZE_SHOW_MECHANICAL not in names
    assert not {"analyze.load", "analyze.support"} & names


def test_complete_manifest_is_projected_without_weakening_its_validation() -> None:
    snapshot = NativeSurfaceSnapshot(
        surface_id="analyze",
        revision=7,
        manifest_sha256="a" * 64,
        command_ids=("FEM_Analysis",),
        available_command_ids=("FEM_Analysis",),
        unavailable_command_ids=(),
    )
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=_AVAILABLE,
        schemas=tuple(
            {
                "name": name,
                "description": name,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
            for name in _AVAILABLE
        ),
        human_only_action_ids=("FEM_Examples",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    projected = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": _domain("fluid")},
    )

    assert projected.available is True
    assert projected.snapshot is snapshot
    assert projected.human_only_action_ids == surface.human_only_action_ids
    assert set(projected.tool_names) == _names(_domain("fluid"))
    assert tuple(schema["name"] for schema in projected.schemas) == projected.tool_names


def test_human_keeps_ribbon_control_on_every_native_surface() -> None:
    snapshot = NativeSurfaceSnapshot(
        surface_id="model",
        revision=7,
        manifest_sha256="b" * 64,
        command_ids=("PartDesign_DesignExtrude",),
        available_command_ids=("PartDesign_DesignExtrude",),
        unavailable_command_ids=(),
    )
    names = ("model.design", "workspace.switch", "document.query")
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=names,
        schemas=tuple(
            {
                "name": name,
                "description": name,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
            for name in names
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    authorized = provider_authorized_native_surface(surface)

    assert authorized.tool_names == ("model.design", "document.query")


def _schema_operations(schema: dict) -> set[str]:
    parameters = schema["parameters"]
    branches = parameters.get("oneOf", [parameters])
    result = set()
    for branch in branches:
        operation = branch["properties"]["operation"]
        result.update(operation.get("enum", [operation.get("const")]))
    return result


def test_operation_scope_publishes_only_calls_that_match_current_study_state() -> None:
    registry = NativeCapabilityRegistry()
    definitions = (
        analyze_model_capability_definition(),
        analyze_face_capability_definition(),
        analyze_inspect_capability_definition(),
        analyze_material_catalog_capability_definition(),
        analyze_geometry_capability_definition(),
        analyze_fluid_capability_definition(),
        *analyze_fluid_create_capability_definitions(),
        *analyze_cfd_lifecycle_capability_definitions(),
        analyze_mesh_capability_definition(),
        *analyze_mesh_lifecycle_capability_definitions(),
        analyze_solver_capability_definition(),
    )
    for definition in definitions:
        registry.register_definition(definition)
    snapshot = NativeSurfaceSnapshot(
        surface_id="analyze",
        revision=7,
        manifest_sha256="c" * 64,
        command_ids=("FEM_Analysis",),
        available_command_ids=("FEM_Analysis",),
        unavailable_command_ids=(),
    )
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=tuple(definition.name for definition in definitions),
        schemas=tuple(
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            )
            for definition in definitions
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    blank = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": _domain(analysis_count=0)},
        registry=registry,
    )
    fluid_domain = _domain("fluid")
    fluid_domain.update(
        {
            "geometry_source_count": 1,
            "materials": [],
            "fluid_constraints": [],
            "element_definitions": [],
        }
    )
    fluid = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": fluid_domain},
        registry=registry,
    )
    fluid_domain["fluid_constraint_count"] = 1
    fluid_domain["fluid_constraints"] = [
        {"constraint_kind": "fluid_boundary"}
    ]
    editable_fluid = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": fluid_domain},
        registry=registry,
    )
    fluid_domain["fluid_constraint_count"] = 3
    fluid_domain["fluid_constraints"] = [
        {"constraint_kind": "fluid_boundary"},
        {"constraint_kind": "initial_flow_velocity"},
        {"constraint_kind": "initial_pressure"},
    ]
    fluid_with_initials = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": fluid_domain},
        registry=registry,
    )
    fluid_domain["material_count"] = 1
    fluid_domain["materials"] = [{"material_kind": "fluid"}]
    fluid_with_material = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": fluid_domain},
        registry=registry,
    )
    fluid_domain["mesh_definition_count"] = 1
    fluid_domain["mesh_definitions"] = [{"mesher": "gmsh"}]
    fluid_domain["provider_scope"]["mesh_definition_count"] = 1
    fluid_with_mesh = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": fluid_domain},
        registry=registry,
    )
    mechanical_domain = _domain("mechanical")
    mechanical_domain.update(
        {
            "material_count": 1,
            "materials": [{"material_kind": "solid"}],
        }
    )
    mechanical = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": mechanical_domain},
        registry=registry,
    )
    mechanical_domain["materials_truncated"] = True
    truncated_mechanical = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": mechanical_domain},
        registry=registry,
    )

    blank_operations = {
        schema["name"]: _schema_operations(schema) for schema in blank.schemas
    }
    fluid_operations = {
        schema["name"]: _schema_operations(schema) for schema in fluid.schemas
    }
    editable_fluid_operations = {
        schema["name"]: _schema_operations(schema)
        for schema in editable_fluid.schemas
    }
    fluid_with_initials_operations = {
        schema["name"]: _schema_operations(schema)
        for schema in fluid_with_initials.schemas
    }
    fluid_with_material_operations = {
        schema["name"]: _schema_operations(schema)
        for schema in fluid_with_material.schemas
    }
    fluid_with_mesh_operations = {
        schema["name"]: _schema_operations(schema)
        for schema in fluid_with_mesh.schemas
    }
    mechanical_operations = {
        schema["name"]: _schema_operations(schema) for schema in mechanical.schemas
    }
    truncated_mechanical_operations = {
        schema["name"]: _schema_operations(schema)
        for schema in truncated_mechanical.schemas
    }
    assert blank_operations == {
        "analyze.model": {"create_analysis"},
        ANALYZE_MATERIAL_CATALOG: {"search"},
    }
    assert fluid_operations["analyze.model"] == {
        "create_analysis",
        "update_study",
    }
    assert fluid_operations[ANALYZE_MATERIAL_CATALOG] == {"search"}
    assert "analyze.inspect" not in fluid_operations
    assert "analyze.solver" not in fluid_operations
    assert fluid_operations["analyze.faces"] == {"read"}
    assert "analyze.fluid" not in fluid_operations
    assert "analyze.fluid" not in editable_fluid_operations
    assert editable_fluid_operations[ANALYZE_EDIT_FLUID_BOUNDARY] == {"edit"}
    assert "analyze.initial_velocity" not in fluid_with_initials_operations
    assert "analyze.initial_pressure" not in fluid_with_initials_operations
    assert fluid_with_initials_operations[ANALYZE_EDIT_FLUID_BOUNDARY] == {"edit"}
    for name in (
        "analyze.initial_velocity",
        "analyze.initial_pressure",
        "analyze.fluid_boundary",
        "analyze.openfoam_solver",
        ANALYZE_FLOW_MESH,
    ):
        assert fluid_operations[name] == {"create"}
    assert fluid_operations["analyze.fluid_material"] == {"create"}
    assert fluid_with_material_operations["analyze.fluid_material"] == {"update"}
    assert ANALYZE_FLOW_MESH not in fluid_with_mesh_operations
    assert "analyze.gmsh_mesh" not in fluid_with_mesh_operations
    assert fluid_with_mesh_operations[ANALYZE_EDIT_GMSH_MESH] == {"edit"}
    assert "analyze.mesh" not in fluid_with_mesh_operations
    assert "analyze.generate_gmsh" not in fluid_operations
    assert mechanical_operations[ANALYZE_SOLID_MESH] == {"create"}
    assert "analyze.gmsh_mesh" not in mechanical_operations
    assert mechanical_operations[ANALYZE_MATERIAL_CATALOG] == {"search"}
    assert "analyze.inspect" not in mechanical_operations
    assert truncated_mechanical_operations["analyze.inspect"] == {
        "assignments",
        "validate_assignments",
    }


def test_solver_operations_follow_exact_engineering_readiness() -> None:
    registry = NativeCapabilityRegistry()
    definitions = (
        analyze_model_capability_definition(),
        analyze_solver_capability_definition(),
    )
    for definition in definitions:
        registry.register_definition(definition)
    snapshot = NativeSurfaceSnapshot(
        surface_id="analyze",
        revision=7,
        manifest_sha256="e" * 64,
        command_ids=("FEM_SolverCalculiX",),
        available_command_ids=("FEM_SolverCalculiX",),
        unavailable_command_ids=(),
    )
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=tuple(definition.name for definition in definitions),
        schemas=tuple(
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            )
            for definition in definitions
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    incomplete = _domain("mechanical")
    incomplete["analysis_workflows"][0]["engineering_readiness"] = {
        "ready_to_solve": False,
        "blockers": ["missing_mechanical_load", "missing_solver"],
    }
    ready = _domain("mechanical", mesh_count=1, generated_mesh_count=1)
    ready["analysis_workflows"][0]["engineering_readiness"] = {
        "ready_to_solve": False,
        "blockers": ["missing_solver"],
    }

    incomplete_surface = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": incomplete},
        registry=registry,
    )
    ready_surface = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": ready},
        registry=registry,
    )

    assert "analyze.solver" not in incomplete_surface.tool_names
    ready_schema = next(
        schema for schema in ready_surface.schemas if schema["name"] == "analyze.solver"
    )
    assert _schema_operations(ready_schema) == {
        "create_calculix",
        "create_elmer",
        "create_mystran",
        "create_z88",
    }


def test_element_geometry_operations_follow_exact_source_dimension() -> None:
    registry = NativeCapabilityRegistry()
    definition = analyze_geometry_capability_definition()
    model_definition = analyze_model_capability_definition()
    registry.register_definition(definition)
    registry.register_definition(model_definition)
    snapshot = NativeSurfaceSnapshot(
        surface_id="analyze",
        revision=7,
        manifest_sha256="d" * 64,
        command_ids=("FEM_ElementGeometry1D",),
        available_command_ids=("FEM_ElementGeometry1D",),
        unavailable_command_ids=(),
    )
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=("analyze.model", "analyze.geometry"),
        schemas=(
            model_definition.provider_schema(
                tuple(
                    variant.operation for variant in model_definition.variants
                )
            ),
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            ),
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    def operations(topology: dict) -> set[str]:
        domain = _domain("mechanical", geometry_source_count=1)
        domain["geometry_sources"] = [
            {"source_name": "Geometry", "topology": topology}
        ]
        projected = scope_analyze_provider_surface(
            surface,
            {"surface_id": "analyze", "domain": domain},
            registry=registry,
        )
        geometry_schema = next(
            (
                schema
                for schema in projected.schemas
                if schema["name"] == "analyze.geometry"
            ),
            None,
        )
        return _schema_operations(geometry_schema) if geometry_schema else set()

    assert operations({"solids": 1, "faces": 6, "edges": 12}) == set()
    assert operations({"solids": 0, "faces": 0, "edges": 1}) == {
        "create_beam_section",
        "create_beam_rotation",
    }
    assert operations({"solids": 0, "faces": 1, "edges": 4}) == {
        "create_shell_thickness",
    }


def test_capture_selection_requires_an_exact_current_selection() -> None:
    registry = NativeCapabilityRegistry()
    definition = next(
        value
        for value in common_capability_definitions()
        if value.name == "view.control"
    )
    registry.register_definition(definition)
    operations = tuple(variant.operation for variant in definition.variants)
    snapshot = NativeSurfaceSnapshot(
        surface_id="analyze",
        revision=7,
        manifest_sha256="e" * 64,
        command_ids=("VibeCAD_NativeCaptureView",),
        available_command_ids=("VibeCAD_NativeCaptureView",),
        unavailable_command_ids=(),
    )
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=("view.control",),
        schemas=(definition.provider_schema(operations),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    def scoped(selection: dict | None) -> set[str]:
        state = {"surface_id": "analyze", "domain": _domain()}
        if selection is not None:
            state["selection"] = selection
        projected = scope_analyze_provider_surface(
            surface,
            state,
            registry=registry,
        )
        schema = next(
            value
            for value in projected.schemas
            if value["name"] == "view.control"
        )
        return _schema_operations(schema)

    assert "capture_selection" not in scoped(None)
    assert "capture_selection" not in scoped({"items": []})
    assert "capture_selection" in scoped(
        {"items": [{"object_name": "Body", "subelements": ["Face1"]}]}
    )


def test_solid_material_creation_follows_exact_assignment_coverage() -> None:
    domain = _domain("mechanical", geometry_source_count=1)
    domain["geometry_sources"] = [
        {
            "source_name": "Bracket",
            "topology": {"solids": 2, "faces": 12, "edges": 24},
        }
    ]
    domain["material_count"] = 1
    domain["materials"] = [
        {
            "material_kind": "solid",
            "references": [
                {"object_name": "Bracket", "subelements": ["Solid1"]}
            ],
        }
    ]

    assert {ANALYZE_CATALOG_MATERIAL, ANALYZE_CUSTOM_MATERIAL} <= _names(domain)

    domain["materials"][0]["references"][0]["subelements"].append("Solid2")
    assert ANALYZE_CATALOG_MATERIAL not in _names(domain)
    assert ANALYZE_CUSTOM_MATERIAL not in _names(domain)

    domain["materials_truncated"] = True
    assert {ANALYZE_CATALOG_MATERIAL, ANALYZE_CUSTOM_MATERIAL} <= _names(domain)

    domain["materials_truncated"] = False
    domain["analysis_count"] = 2
    domain["provider_scope"]["analysis_count"] = 2
    assert {ANALYZE_CATALOG_MATERIAL, ANALYZE_CUSTOM_MATERIAL} <= _names(domain)


def test_solid_material_creation_follows_exact_study_readiness() -> None:
    domain = _domain("mechanical", geometry_source_count=1)
    domain["analysis_workflows"][0]["engineering_readiness"] = {
        "blockers": ["missing_mechanical_material", "missing_support"],
        "ready_to_solve": False,
    }
    assert {ANALYZE_CATALOG_MATERIAL, ANALYZE_CUSTOM_MATERIAL} <= _names(domain)

    domain["analysis_workflows"][0]["engineering_readiness"] = {
        "blockers": ["missing_support"],
        "ready_to_solve": False,
    }
    assert ANALYZE_CATALOG_MATERIAL not in _names(domain)
    assert ANALYZE_CUSTOM_MATERIAL not in _names(domain)
