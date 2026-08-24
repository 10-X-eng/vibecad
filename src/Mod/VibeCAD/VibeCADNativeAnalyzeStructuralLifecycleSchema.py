# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for common structural study assignments."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import (
    _OBJECT_NAME,
)
from VibeCADNativeAnalyzeSupportSchema import (
    _DISPLACEMENT,
    _RIGID_BODY,
    _SPRING,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_SOLID_MATERIAL = "analyze.solid_material"
ANALYZE_SOLID_REGION_MATERIAL = "analyze.solid_region_material"
ANALYZE_CATALOG_MATERIAL = "analyze.catalog_material"
ANALYZE_CUSTOM_MATERIAL = "analyze.custom_material"
ANALYZE_FIXED_SUPPORT = "analyze.fixed_support"
ANALYZE_EDIT_FIXED_SUPPORT = "analyze.edit_fixed_support"
ANALYZE_RIGID_COUPLING = "analyze.rigid_coupling"
ANALYZE_EDIT_RIGID_COUPLING = "analyze.edit_rigid_coupling"
ANALYZE_DISPLACEMENT_SUPPORT = "analyze.displacement_support"
ANALYZE_EDIT_DISPLACEMENT_SUPPORT = "analyze.edit_displacement_support"
ANALYZE_SPRING_SUPPORT = "analyze.spring_support"
ANALYZE_EDIT_SPRING_SUPPORT = "analyze.edit_spring_support"
ANALYZE_FORCE = "analyze.force"
ANALYZE_EDIT_FORCE = "analyze.edit_force"
ANALYZE_PRESSURE = "analyze.pressure"
ANALYZE_EDIT_PRESSURE = "analyze.edit_pressure"
ANALYZE_GRAVITY = "analyze.gravity"
ANALYZE_EDIT_GRAVITY = "analyze.edit_gravity"
ANALYZE_CENTRIFUGAL = "analyze.centrifugal_load"
ANALYZE_EDIT_CENTRIFUGAL = "analyze.edit_centrifugal_load"


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _geometry_names(
    pattern: str,
    description: str,
    *,
    min_items: int = 1,
) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "string",
            "pattern": pattern,
            "maxLength": 32,
        },
        "minItems": min_items,
        "maxItems": 256,
        "uniqueItems": True,
        "description": description,
    }


def _definition(
    name: str,
    description: str,
    action_id: str,
    parameters: dict,
    *,
    operation: str = "create",
    exact_target_type: str = "CurrentNamedStructuralStudyAndGeometry",
) -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation=operation,
                description=description,
                action_ids=frozenset({action_id}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type=exact_target_type,
                transaction_behavior="document",
                background_required=False,
                parameters=parameters,
                provider_supplemental=True,
            ),
        ),
    )


def analyze_structural_lifecycle_capability_definitions(
) -> tuple[NativeCapabilityDefinition, ...]:
    analysis_name = {**_OBJECT_NAME, "description": "Study analysis_name."}
    source_name = {**_OBJECT_NAME, "description": "Geometry source_name."}
    material_properties = {
        "type": "object",
        "properties": {
            "density_kg_m3": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0e9,
            },
            "young_modulus_mpa": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0e12,
            },
            "poisson_ratio": {
                "type": "number",
                "exclusiveMinimum": -1.0,
                "exclusiveMaximum": 0.5,
            },
            "yield_strength_mpa": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0e12,
            },
            "thermal_conductivity_w_m_k": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0e9,
            },
            "thermal_expansion_per_k": {
                "type": "number",
                "minimum": -1.0,
                "maximum": 1.0,
            },
            "reference_temperature_k": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 100_000.0,
            },
            "specific_heat_j_kg_k": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1.0e12,
            },
        },
        "minProperties": 1,
        "additionalProperties": False,
    }
    solid_regions = _geometry_names(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?Solid[1-9][0-9]*$",
        "SolidN regions receiving this material.",
    )
    support_geometry = _geometry_names(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:Vertex|Edge|Face)[1-9][0-9]*$",
        "VertexN, EdgeN, or FaceN held fixed.",
    )
    spring_geometry = _geometry_names(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?Face[1-9][0-9]*$",
        "FaceN receiving the spring support.",
    )
    force_geometry = _geometry_names(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:Vertex|Edge|Face)[1-9][0-9]*$",
        "VertexN, EdgeN, or FaceN receiving the force.",
    )
    pressure_geometry = _geometry_names(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:Edge|Face)[1-9][0-9]*$",
        "EdgeN or FaceN receiving the pressure.",
    )
    centrifugal_geometry = _geometry_names(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:Solid|Face)[1-9][0-9]*$",
        "Geometry receiving the centrifugal load.",
    )
    direction = _closed(
        {
            axis: {
                "type": "number",
                "minimum": -1.0e30,
                "maximum": 1.0e30,
            }
            for axis in ("x", "y", "z")
        },
        ("x", "y", "z"),
    )
    force_vector = {**direction, "description": "Signed force vector in newtons."}
    pressure_magnitude = {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 1.0e30,
        "description": "Positive pressure magnitude in pascals.",
    }
    acceleration = {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 1.0e30,
        "description": "Positive acceleration magnitude in metres per second squared.",
    }
    rotation_frequency = {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 1.0e30,
        "description": "Rotation frequency in hertz.",
    }
    applied_to = _closed(
        {
            "source_name": source_name,
            "subelement_names": force_geometry,
        },
        ("source_name", "subelement_names"),
    )
    support_applied_to = _closed(
        {
            "source_name": source_name,
            "subelement_names": support_geometry,
        },
        ("source_name", "subelement_names"),
    )
    spring_applied_to = _closed(
        {
            "source_name": source_name,
            "face_names": spring_geometry,
        },
        ("source_name", "face_names"),
    )
    pressure_applied_to = _closed(
        {
            "source_name": source_name,
            "subelement_names": pressure_geometry,
        },
        ("source_name", "subelement_names"),
    )
    axis = _closed(
        {
            "source_name": source_name,
            "edge_name": {
                "type": "string",
                "pattern": r"^Edge[1-9][0-9]*$",
                "maxLength": 32,
            },
        },
        ("source_name", "edge_name"),
    )
    centrifugal_scope = {
        "oneOf": [
            _closed(
                {"kind": {"type": "string", "const": "all_bodies"}},
                ("kind",),
            ),
            _closed(
                {
                    "kind": {"type": "string", "const": "selected_geometry"},
                    "source_name": source_name,
                    "subelement_names": centrifugal_geometry,
                },
                ("kind", "source_name", "subelement_names"),
            ),
        ],
        "description": "Loaded bodies; omit to load every body in the study.",
    }
    material_source = {
        "oneOf": [
            _closed(
                {
                    "kind": {"type": "string", "const": "catalog"},
                    "uuid": {
                        "type": "string",
                        "pattern": (
                            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
                        ),
                        "maxLength": 36,
                    },
                },
                ("kind", "uuid"),
            ),
            _closed(
                {
                    "kind": {"type": "string", "const": "custom"},
                    "properties": material_properties,
                },
                ("kind", "properties"),
            ),
        ]
    }
    material_parameters = _closed(
        {
            "analysis_name": analysis_name,
            "source_name": source_name,
            "material": material_source,
        },
        ("analysis_name", "source_name", "material"),
    )
    region_material_parameters = _closed(
        {
            "analysis_name": analysis_name,
            "source_name": source_name,
            "solid_regions": solid_regions,
            "material": material_source,
        },
        ("analysis_name", "source_name", "solid_regions", "material"),
    )
    catalog_material_parameters = _closed(
        {
            "analysis_name": analysis_name,
            "source_name": source_name,
            "material_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
                "description": "Exact catalog material name.",
            },
        },
        ("analysis_name", "source_name", "material_name"),
    )
    custom_material_parameters = _closed(
        {
            "analysis_name": analysis_name,
            "source_name": source_name,
            "properties": material_properties,
        },
        ("analysis_name", "source_name", "properties"),
    )
    return (
        _definition(
            ANALYZE_CATALOG_MATERIAL,
            "Assign a catalog material by exact name.",
            "VibeCAD_AnalyzeCreateCatalogMaterial",
            catalog_material_parameters,
        ),
        _definition(
            ANALYZE_CUSTOM_MATERIAL,
            "Assign explicit material properties.",
            "VibeCAD_AnalyzeCreateCustomMaterial",
            custom_material_parameters,
        ),
        _definition(
            ANALYZE_SOLID_MATERIAL,
            "Assign structural material to geometry.",
            "VibeCAD_AnalyzeCreateSolidMaterial",
            material_parameters,
        ),
        _definition(
            ANALYZE_SOLID_REGION_MATERIAL,
            "Assign structural material to selected solid regions.",
            "VibeCAD_AnalyzeCreateSolidRegionMaterial",
            region_material_parameters,
        ),
        _definition(
            ANALYZE_FIXED_SUPPORT,
            "Lock geometry at zero displacement.",
            "VibeCAD_AnalyzeCreateFixedSupport",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "subelement_names": support_geometry,
                },
                ("analysis_name", "source_name", "subelement_names"),
            ),
        ),
        _definition(
            ANALYZE_EDIT_FIXED_SUPPORT,
            "Move one fixed support to different geometry.",
            "VibeCAD_AnalyzeEditFixedSupportFocused",
            _closed(
                {
                    "support_name": {
                        **_OBJECT_NAME,
                        "description": "Fixed support_name.",
                    },
                    "changes": _closed(
                        {"applied_to": support_applied_to},
                        ("applied_to",),
                    ),
                },
                ("support_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemFixedSupport",
        ),
        _definition(
            ANALYZE_RIGID_COUPLING,
            "Couple geometry rigidly through a reference node.",
            "VibeCAD_AnalyzeCreateRigidCouplingFocused",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "subelement_names": support_geometry,
                    **_RIGID_BODY["properties"],
                },
                (
                    "analysis_name",
                    "source_name",
                    "subelement_names",
                    "reference_node_mm",
                    "translation",
                    "rotation",
                ),
            ),
        ),
        _definition(
            ANALYZE_EDIT_RIGID_COUPLING,
            "Edit one rigid coupling.",
            "VibeCAD_AnalyzeEditRigidCouplingFocused",
            _closed(
                {
                    "support_name": {
                        **_OBJECT_NAME,
                        "description": "Rigid coupling support_name.",
                    },
                    "changes": {
                        "type": "object",
                        "properties": {
                            "applied_to": support_applied_to,
                            **_RIGID_BODY["properties"],
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("support_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemRigidCoupling",
        ),
        _definition(
            ANALYZE_DISPLACEMENT_SUPPORT,
            "Prescribe selected translation and rotation components.",
            "VibeCAD_AnalyzeCreateDisplacementSupportFocused",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "subelement_names": support_geometry,
                    **_DISPLACEMENT["properties"],
                },
                (
                    "analysis_name",
                    "source_name",
                    "subelement_names",
                    "translation",
                    "rotation",
                    "flow_surface_force",
                ),
            ),
        ),
        _definition(
            ANALYZE_EDIT_DISPLACEMENT_SUPPORT,
            "Edit one displacement support.",
            "VibeCAD_AnalyzeEditDisplacementSupportFocused",
            _closed(
                {
                    "support_name": {
                        **_OBJECT_NAME,
                        "description": "Displacement support_name.",
                    },
                    "changes": {
                        "type": "object",
                        "properties": {
                            "applied_to": support_applied_to,
                            **_DISPLACEMENT["properties"],
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("support_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemDisplacementSupport",
        ),
        _definition(
            ANALYZE_SPRING_SUPPORT,
            "Support faces with normal and tangential stiffness.",
            "VibeCAD_AnalyzeCreateSpringSupportFocused",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "face_names": spring_geometry,
                    **_SPRING["properties"],
                },
                (
                    "analysis_name",
                    "source_name",
                    "face_names",
                    "normal_stiffness_n_m",
                    "tangential_stiffness_n_m",
                    "elmer_component",
                ),
            ),
        ),
        _definition(
            ANALYZE_EDIT_SPRING_SUPPORT,
            "Edit one spring support.",
            "VibeCAD_AnalyzeEditSpringSupportFocused",
            _closed(
                {
                    "support_name": {
                        **_OBJECT_NAME,
                        "description": "Spring support_name.",
                    },
                    "changes": {
                        "type": "object",
                        "properties": {
                            "applied_to": spring_applied_to,
                            **_SPRING["properties"],
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("support_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemSpringSupport",
        ),
        _definition(
            ANALYZE_FORCE,
            "Apply a directed force to geometry.",
            "VibeCAD_AnalyzeCreateForce",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "subelement_names": force_geometry,
                    "force_vector_n": force_vector,
                },
                (
                    "analysis_name",
                    "source_name",
                    "subelement_names",
                    "force_vector_n",
                ),
            ),
        ),
        _definition(
            ANALYZE_EDIT_FORCE,
            "Edit one force.",
            "VibeCAD_AnalyzeUpdateForceFocused",
            _closed(
                {
                    "load_name": {
                        **_OBJECT_NAME,
                        "description": "Force load_name.",
                    },
                    "changes": {
                        "type": "object",
                        "properties": {
                            "force_vector_n": force_vector,
                            "applied_to": applied_to,
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("load_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemForce",
        ),
        _definition(
            ANALYZE_PRESSURE,
            "Apply pressure normal to geometry.",
            "VibeCAD_AnalyzeCreatePressureFocused",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "source_name": source_name,
                    "subelement_names": pressure_geometry,
                    "pressure_pa": pressure_magnitude,
                    "reversed": {
                        "type": "boolean",
                        "description": "Flip the selected geometry normal.",
                    },
                },
                (
                    "analysis_name",
                    "source_name",
                    "subelement_names",
                    "pressure_pa",
                    "reversed",
                ),
            ),
        ),
        _definition(
            ANALYZE_EDIT_PRESSURE,
            "Edit one pressure load.",
            "VibeCAD_AnalyzeUpdatePressureFocused",
            _closed(
                {
                    "load_name": {**_OBJECT_NAME, "description": "Pressure load_name."},
                    "changes": {
                        "type": "object",
                        "properties": {
                            "pressure_pa": pressure_magnitude,
                            "applied_to": pressure_applied_to,
                            "reversed": {
                                "type": "boolean",
                                "description": "Flip the selected geometry normal.",
                            },
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("load_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemPressure",
        ),
        _definition(
            ANALYZE_GRAVITY,
            "Apply uniform gravity to a study.",
            "VibeCAD_AnalyzeCreateGravityFocused",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "acceleration_m_s2": acceleration,
                    "direction": {**direction, "description": "Gravity direction."},
                },
                ("analysis_name", "acceleration_m_s2", "direction"),
            ),
        ),
        _definition(
            ANALYZE_EDIT_GRAVITY,
            "Edit the study gravity load.",
            "VibeCAD_AnalyzeUpdateGravityFocused",
            _closed(
                {
                    "load_name": {**_OBJECT_NAME, "description": "Gravity load_name."},
                    "changes": {
                        "type": "object",
                        "properties": {
                            "acceleration_m_s2": acceleration,
                            "direction": {
                                **direction,
                                "description": "Gravity direction.",
                            },
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("load_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemGravity",
        ),
        _definition(
            ANALYZE_CENTRIFUGAL,
            "Apply a rotating-body load around an axis.",
            "VibeCAD_AnalyzeCreateCentrifugalFocused",
            _closed(
                {
                    "analysis_name": analysis_name,
                    "rotation_frequency_hz": rotation_frequency,
                    "axis": axis,
                    "scope": centrifugal_scope,
                },
                ("analysis_name", "rotation_frequency_hz", "axis"),
            ),
        ),
        _definition(
            ANALYZE_EDIT_CENTRIFUGAL,
            "Edit one rotating-body load.",
            "VibeCAD_AnalyzeUpdateCentrifugalFocused",
            _closed(
                {
                    "load_name": {
                        **_OBJECT_NAME,
                        "description": "Centrifugal load_name.",
                    },
                    "changes": {
                        "type": "object",
                        "properties": {
                            "rotation_frequency_hz": rotation_frequency,
                            "axis": axis,
                            "scope": centrifugal_scope,
                        },
                        "minProperties": 1,
                        "additionalProperties": False,
                    },
                },
                ("load_name", "changes"),
            ),
            operation="edit",
            exact_target_type="CurrentNamedFemCentrifugalLoad",
        ),
    )


def register_analyze_structural_lifecycle_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_structural_lifecycle_capability_definitions():
        registry.register_definition(definition)
