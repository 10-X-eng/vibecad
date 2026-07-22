# SPDX-License-Identifier: LGPL-2.1-or-later

"""Qt heartbeat integration for production-ready geometry/solver workers.

Run this file inside ``FreeCADCmd``.  Candidate subprocess waits happen on a
background thread while the main thread owns a real Qt event loop. Part covers
OCC generation, Part Design covers native Body/sketch/feature recompute,
Sketcher covers geometry/constraint solving, Draft covers
native parametric proxy recompute and array generation, Surface covers B-spline
interpolation plus native extension, and Assembly covers authenticated reference
transfer plus a native solver. Spreadsheet covers a large native batch and
formula recompute. Material covers native catalog loading, full-card hashing,
and physical-property requirement validation. Mesh covers substantial triangle
generation, repair, decimation, topology diagnostics,
and BMS export. MeshPart covers authenticated BREP transfer plus a substantial
native Mefisto conversion and BMS validation. Points covers a substantial
canonical transform/filter/voxel pipeline plus authenticated ASC export. Reverse
Engineering covers native B-spline surface fitting, structured reconstruction,
normal-region segmentation, fit metrics, and authenticated BREP/BMS export.
Inspection covers authenticated Points/BREP transfer, native signed-distance
evaluation, typed aggregation, and bounded float32 artifact validation.
Robot covers native trajectory generation, dress-up recompute, thousands of
trajectory interpolation and inverse-kinematics samples, and authenticated
binary diagnostics. FEM covers authenticated BREP transfer, native constraint
mapping, native mesh reconstruction, and CalculiX input-deck generation. CAM
covers authenticated BREP transfer, native operation generation, stock-removal
and protected-model simulation, and native postprocessing.
TechDraw covers authenticated BREP transfer, multi-direction HLR projection,
projected dimension evaluation, and authenticated precomputed-state artifacts.
Future gated prototype domains remain excluded until their production
implementations pass this executable test.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from PySide import QtCore  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    execute_candidate,
    finalize_candidate,
    prepare_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import get_vibescript_pack  # noqa: E402


WORKER_CASES = (
    (
        "PartWorkbench",
        "solid",
        "result = {'Result': api.box(2, 3, 4, label=str(total))}\n",
    ),
    (
        "PartWorkbench",
        "solid",
        "base = api.box(8, 8, 8)\n"
        "bore = api.cylinder(2, 8, origin=[4,4,0])\n"
        "result = {'Result': api.cut(base, bore, label=str(total))}\n",
    ),
    (
        "PartWorkbench",
        "solid",
        "lower = api.wire([[0,0,0],[8,0,0],[8,5,0],[0,5,0]], closed=True)\n"
        "upper = api.wire([[1,1,8],[7,1,8],[7,4,8],[1,4,8]], closed=True)\n"
        "result = {'Result': api.loft([lower, upper], solid=True, label=str(total))}\n",
    ),
    (
        "PartDesignWorkbench",
        "solid",
        "circle = api.circle([0,0], 4)\n"
        "profile = api.sketch([circle], label='Heartbeat Profile')\n"
        "tip = api.pad(profile, 8, label='Heartbeat Pad')\n"
        "result = {'Result': api.body(tip, label=str(total))}\n",
    ),
    (
        "SketcherWorkbench",
        "sketch",
        "geometry = []\n"
        "constraints = []\n"
        "for index in range(120):\n"
        "    line = api.line([0,index], [10,index], name='Line' + str(index))\n"
        "    geometry.append(line)\n"
        "    constraints.append(api.constraint('horizontal', [line]))\n"
        "result = {'Result': api.sketch(geometry, constraints, label=str(total))}\n",
    ),
)


class _Bridge(QtCore.QObject):
    finished = QtCore.Signal()


def _candidate(root: Path, index: int, workbench: str, output_type: str, result: str):
    import FreeCAD as App

    pack = get_vibescript_pack(workbench)
    assert pack is not None
    busy_source = (
        "total = 0\nfor value in range(30000):\n    total += value % 7\n" + result
    )
    return prepare_candidate(
        {
            "tool_name": f"vibescript.{pack.domain}.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": busy_source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [{"name": "Result", "type": output_type}],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatDocument",
            "document_uid": "qt-heartbeat-document",
            "document_revision": "qt-heartbeat-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(workbench, "vibescript").summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
    )


def _reference_schema() -> dict:
    return {
        "type": "object",
        "x-vibecad-reference": True,
        "properties": {
            "document_uid": {"type": "string", "minLength": 1},
            "object_name": {"type": "string", "minLength": 1},
        },
        "required": ["document_uid", "object_name"],
        "additionalProperties": False,
    }


def _assembly_candidate(root: Path, index: int):
    """Solve, simulate, and generate a native BOM entirely off-thread."""

    import FreeCAD as App
    import Part

    pack = get_vibescript_pack("AssemblyWorkbench")
    assert pack is not None
    document_uid = "qt-heartbeat-assembly-document"
    references = {
        "base": {"document_uid": document_uid, "object_name": "HeartbeatBase"},
        "arm": {"document_uid": document_uid, "object_name": "HeartbeatArm"},
    }
    busy_source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "base = api.component(inputs['base'], grounded=True, label='Base')\n"
        "arm = api.component(inputs['arm'], placement=[0,0,8], label=str(total))\n"
        "hinge = api.joint('revolute', api.connector(base), api.connector(arm), "
        "angle_limits_degrees=[-90,90], label='Hinge')\n"
        "model = api.assembly([base,arm], [hinge], label='Heartbeat Assembly')\n"
        "diagnostics = api.solve(model)\n"
        "drive = api.motion(hinge, 'initialValue + pi*time', label='Drive')\n"
        "simulation = api.simulation(model, [drive], end_time_s=.5, "
        "time_step_s=.01, label='Heartbeat Simulation')\n"
        "bill = api.bill_of_materials(model, columns=['name','quantity'], "
        "label='Heartbeat BOM')\n"
        "result = {'Model':model,'Base':base,'Arm':arm,'Hinge':hinge,"
        "'Drive':drive,'Simulation':simulation,'Bill':bill,"
        "'Diagnostics':diagnostics}\n"
    )
    prepared = prepare_candidate(
        {
            "tool_name": "vibescript.assembly.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": busy_source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "base": _reference_schema(),
                        "arm": _reference_schema(),
                    },
                    "required": ["base", "arm"],
                    "additionalProperties": False,
                },
                "inputs": references,
                "expected_outputs": [
                    {"name": "Model", "type": "assembly"},
                    {"name": "Base", "type": "component_link"},
                    {"name": "Arm", "type": "component_link"},
                    {"name": "Hinge", "type": "joint"},
                    {"name": "Drive", "type": "motion"},
                    {"name": "Simulation", "type": "simulation"},
                    {"name": "Bill", "type": "bom"},
                    {"name": "Diagnostics", "type": "solver_diagnostics"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatAssemblyDocument",
            "document_uid": document_uid,
            "document_revision": "qt-heartbeat-assembly-revision",
            "document_objects": [
                {"name": "HeartbeatBase", "label": "Base", "type_id": "Part::Feature"},
                {"name": "HeartbeatArm", "label": "Arm", "type_id": "Part::Feature"},
            ],
            "surface": resolve_modeling_surface(
                "AssemblyWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
    )
    shapes = {
        "HeartbeatBase": Part.makeBox(20, 16, 8),
        "HeartbeatArm": Part.makeCylinder(3, 30),
    }
    return finalize_candidate(
        prepared,
        [
            {
                "document_uid": document_uid,
                "object_name": name,
                "label": name,
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "detached_shape": shape,
                "snapshot_index": snapshot_index,
                "source_kind": "shape",
                "transient_topology": False,
                "requires_semantic_interfaces": False,
                "published_interfaces": {},
            }
            for snapshot_index, (name, shape) in enumerate(shapes.items())
        ],
    )


def _draft_candidate(root: Path, index: int):
    """Generate a substantial native Draft link array in the isolated worker."""

    import FreeCAD as App

    pack = get_vibescript_pack("DraftWorkbench")
    assert pack is not None
    source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "base = api.rectangle(2, 1, make_face=True, label='Heartbeat Base')\n"
        "array = api.array(base, kind='orthogonal', interval_x=[3,0,0], "
        "interval_y=[0,2,0], count_x=40, count_y=30, count_z=1, "
        "use_link=True, label=str(total))\n"
        "result = {'Base':base, 'Array':array}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.draft.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [
                    {"name": "Base", "type": "rectangle"},
                    {"name": "Array", "type": "array"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatDraftDocument",
            "document_uid": "qt-heartbeat-draft-document",
            "document_revision": "qt-heartbeat-draft-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "DraftWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
    )


def _surface_candidate(root: Path, index: int):
    """Interpolate and extend a substantial B-spline surface in the worker."""

    import FreeCAD as App

    pack = get_vibescript_pack("SurfaceWorkbench")
    assert pack is not None
    source = (
        "total = 0\n"
        "grid = []\n"
        "for row in range(24):\n"
        "    points = []\n"
        "    for column in range(24):\n"
        "        z = row * column * 0.005\n"
        "        points.append([column * 0.5, row * 0.5, z])\n"
        "        total += (row + column) % 7\n"
        "    grid.append(points)\n"
        "surface = api.surface(grid, mode='interpolate', label=str(total))\n"
        "extended = api.extend(surface, u_negative=.05, u_positive=.05, "
        "v_negative=.05, v_positive=.05, tolerance=.05, samples_u=48, "
        "samples_v=48, label='Heartbeat Extension')\n"
        "result = {'Surface':surface, 'Extended':extended}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.surface.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [
                    {"name": "Surface", "type": "surface"},
                    {"name": "Extended", "type": "extension"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatSurfaceDocument",
            "document_uid": "qt-heartbeat-surface-document",
            "document_revision": "qt-heartbeat-surface-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "SurfaceWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
    )


def _spreadsheet_candidate(root: Path, index: int):
    """Apply and recompute a substantial native Spreadsheet batch in the worker."""

    import FreeCAD as App

    pack = get_vibescript_pack("SpreadsheetWorkbench")
    assert pack is not None
    source = (
        "cells = []\n"
        "for row in range(1, 401):\n"
        "    address = str(row)\n"
        "    cells.append(api.cell('A' + address, row, alias='value_' + address))\n"
        "    cells.append(api.cell('B' + address, expression='value_' + address + ' * 2'))\n"
        "body = api.range_style('A1:B400', alignment='right')\n"
        "result = {'Result': api.sheet(cells, range_styles=[body], "
        "column_widths={'A':90,'B':110}, label='Heartbeat Sheet')}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.spreadsheet.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [{"name": "Result", "type": "sheet"}],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatSpreadsheetDocument",
            "document_uid": "qt-heartbeat-spreadsheet-document",
            "document_revision": "qt-heartbeat-spreadsheet-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "SpreadsheetWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
    )


def _material_candidate(root: Path, index: int):
    """Resolve and hash a native catalog card in the isolated worker."""

    import FreeCAD as App
    import Materials

    pack = get_vibescript_pack("MaterialWorkbench")
    assert pack is not None
    card = next(
        value
        for value in Materials.MaterialManager().Materials.values()
        if value.hasPhysicalProperty("Density")
    )
    document_uid = "qt-heartbeat-material-document"
    reference = {"document_uid": document_uid, "object_name": "HeartbeatTarget"}
    source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "card = api.material(inputs['material_uuid'], "
        "require_physical_properties=['Density'])\n"
        "result = {'Result': api.assign(inputs['target'], card, label=str(total))}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.material.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "material_uuid": {"type": "string"},
                        "target": _reference_schema(),
                    },
                    "required": ["material_uuid", "target"],
                    "additionalProperties": False,
                },
                "inputs": {
                    "material_uuid": str(card.UUID),
                    "target": reference,
                },
                "expected_outputs": [{"name": "Result", "type": "material_assignment"}],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatMaterialDocument",
            "document_uid": document_uid,
            "document_revision": "qt-heartbeat-material-revision",
            "document_objects": [
                {
                    "name": "HeartbeatTarget",
                    "label": "Heartbeat Target",
                    "type_id": "Part::Feature",
                }
            ],
            "material_targets": [
                {
                    "reference": reference,
                    "label": "Heartbeat Target",
                    "type_id": "Part::Feature",
                    "physical_assignment_supported": True,
                    "current_material": None,
                    "appearance_supported_properties": [],
                    "display_modes": [],
                    "display_modes_truncated": False,
                    "managed_material_output": False,
                }
            ],
            "surface": resolve_modeling_surface(
                "MaterialWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
    )


def _mesh_candidate(root: Path, index: int):
    """Generate, repair, diagnose, and export a substantial native mesh."""

    import FreeCAD as App

    pack = get_vibescript_pack("MeshWorkbench")
    assert pack is not None
    source = (
        "triangles = []\n"
        "for x in range(70):\n"
        "    for y in range(70):\n"
        "        a = [x,y,0]\n"
        "        b = [x+1,y,0]\n"
        "        c = [x+1,y+1,0]\n"
        "        d = [x,y+1,0]\n"
        "        triangles.append([a,b,c])\n"
        "        triangles.append([a,c,d])\n"
        "raw = api.mesh(triangles, label='Heartbeat Grid')\n"
        "clean = api.repair(raw, decimate_reduction=0.2, "
        "decimate_tolerance=0.05, label='Heartbeat Repaired Grid')\n"
        "checked = api.diagnostics(clean, require_manifold=True, "
        "require_consistent_orientation=True, require_no_self_intersections=True, "
        "max_components=1, label='Heartbeat Checked Grid')\n"
        "result = {'Result': checked}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.mesh.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [{"name": "Result", "type": "mesh"}],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatMeshDocument",
            "document_uid": "qt-heartbeat-mesh-document",
            "document_revision": "qt-heartbeat-mesh-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "MeshWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )


def _meshpart_candidate(root: Path, index: int):
    """Mesh a detached curved BREP with the native Mefisto backend."""

    import FreeCAD as App
    import Part

    pack = get_vibescript_pack("MeshPartWorkbench")
    assert pack is not None and pack.production_ready
    document_uid = "qt-heartbeat-meshpart-document"
    reference = {
        "document_uid": document_uid,
        "object_name": "HeartbeatCurvedShape",
    }
    source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "result = {'Result': api.mesh_from_shape(inputs['source'], "
        "method='max_length', max_length=0.75, label=str(total))}\n"
    )
    prepared = prepare_candidate(
        {
            "tool_name": "vibescript.meshpart.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {"source": _reference_schema()},
                    "required": ["source"],
                    "additionalProperties": False,
                },
                "inputs": {"source": reference},
                "expected_outputs": [{"name": "Result", "type": "mesh"}],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatMeshPartDocument",
            "document_uid": document_uid,
            "document_revision": "qt-heartbeat-meshpart-revision",
            "document_objects": [
                {
                    "name": reference["object_name"],
                    "label": "Heartbeat Curved Shape",
                    "type_id": "Part::Feature",
                }
            ],
            "surface": resolve_modeling_surface(
                "MeshPartWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )
    shape = Part.makeSphere(20.0)
    return finalize_candidate(
        prepared,
        [
            {
                "document_uid": document_uid,
                "object_name": reference["object_name"],
                "label": "Heartbeat Curved Shape",
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "reference_artifact_kind": "brep",
                "detached_shape": shape,
                "snapshot_index": 0,
                "source_kind": "shape",
                "transient_topology": False,
                "requires_semantic_interfaces": False,
                "published_interfaces": {},
            }
        ],
    )


def _points_candidate(root: Path, index: int):
    """Process and serialize a substantial point cloud in the isolated worker."""

    import FreeCAD as App

    pack = get_vibescript_pack("PointsWorkbench")
    assert pack is not None and pack.production_ready
    source = (
        "points = []\n"
        "for x in range(200):\n"
        "    for y in range(200):\n"
        "        points.append([x * 0.25, y * 0.25, (x + y) % 7])\n"
        "cloud = api.point_cloud(points, pipeline=["
        "{'op':'transform','translation':[1,2,3],'rotation':[0,0,0,1],"
        "'scale':[1.1,0.9,1]},"
        "{'op':'filter','method':'crop_box','minimum':[0,0,0],"
        "'maximum':[100,100,20]},"
        "{'op':'sample','method':'voxel','voxel_size':0.6,"
        "'reduction':'centroid'}], label='Heartbeat Points')\n"
        "result = {'Result': cloud}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.points.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [{"name": "Result", "type": "points"}],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatPointsDocument",
            "document_uid": "qt-heartbeat-points-document",
            "document_revision": "qt-heartbeat-points-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "PointsWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )


def _reverse_engineering_candidate(root: Path, index: int):
    """Fit and reconstruct a substantial cloud in the isolated worker."""

    import FreeCAD as App

    pack = get_vibescript_pack("ReverseEngineeringWorkbench")
    assert pack is not None and pack.production_ready
    source = (
        "points = []\n"
        "for row in range(24):\n"
        "    for column in range(24):\n"
        "        points.append([column * 0.5, row * 0.5, "
        "(row * column) * 0.002])\n"
        "surface = api.fit_surface(points, u_degree=3, v_degree=3, "
        "u_poles=8, v_poles=8, iterations=3, label='Heartbeat Fit')\n"
        "mesh = api.reconstruct(points, method='structured_grid', "
        "parameters={'grid_size':[24,24],'diagonal':'shortest'}, "
        "label='Heartbeat Reconstruction')\n"
        "regions = api.segment(mesh, method='normal_regions', "
        "parameters={'segment':'all','minimum_facets':1,'angle_degrees':8}, "
        "label='Heartbeat Regions')\n"
        "report = api.fit_metrics(regions, tolerance=0.1, "
        "label='Heartbeat Fit Metrics')\n"
        "result = {'Surface':surface,'Mesh':mesh,'Regions':regions,'Report':report}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.reverse_engineering.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [
                    {"name": "Surface", "type": "surface"},
                    {"name": "Mesh", "type": "mesh"},
                    {"name": "Regions", "type": "mesh"},
                    {"name": "Report", "type": "fit_metrics"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatReverseEngineeringDocument",
            "document_uid": "qt-heartbeat-reverse-engineering-document",
            "document_revision": "qt-heartbeat-reverse-engineering-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "ReverseEngineeringWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )


def _inspection_candidate(root: Path, index: int):
    """Run a substantial native nominal-versus-actual solve in the worker."""

    import FreeCAD as App
    import Part
    import Points

    pack = get_vibescript_pack("InspectionWorkbench")
    assert pack is not None and pack.production_ready
    document_uid = "qt-heartbeat-inspection-document"
    actual_reference = {
        "document_uid": document_uid,
        "object_name": "HeartbeatActualPoints",
    }
    nominal_reference = {
        "document_uid": document_uid,
        "object_name": "HeartbeatNominalPlane",
    }
    source = (
        "comparison = api.comparison(inputs['actual'], [inputs['nominal']], "
        "search_radius=1.0, tolerance=0.2, label='Heartbeat Comparison')\n"
        "inspection = api.group([comparison], label='Heartbeat Inspection')\n"
        "rms = api.measurement(comparison, metric='rms', label='Heartbeat RMS')\n"
        "report = api.report(inspection, label='Heartbeat Report')\n"
        "result = {'Comparison':comparison,'Inspection':inspection,"
        "'RMS':rms,'Report':report}\n"
    )
    prepared = prepare_candidate(
        {
            "tool_name": "vibescript.inspection.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "actual": _reference_schema(),
                        "nominal": _reference_schema(),
                    },
                    "required": ["actual", "nominal"],
                    "additionalProperties": False,
                },
                "inputs": {
                    "actual": actual_reference,
                    "nominal": nominal_reference,
                },
                "expected_outputs": [
                    {"name": "Comparison", "type": "inspection_feature"},
                    {"name": "Inspection", "type": "inspection_group"},
                    {"name": "RMS", "type": "measurement"},
                    {"name": "Report", "type": "report"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatInspectionDocument",
            "document_uid": document_uid,
            "document_revision": "qt-heartbeat-inspection-revision",
            "document_objects": [
                {
                    "name": actual_reference["object_name"],
                    "label": "Heartbeat Actual Points",
                    "type_id": "Points::Feature",
                },
                {
                    "name": nominal_reference["object_name"],
                    "label": "Heartbeat Nominal Plane",
                    "type_id": "Part::Feature",
                },
            ],
            "surface": resolve_modeling_surface(
                "InspectionWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )
    actual = Points.Points(
        [
            App.Vector(column * 0.25, row * 0.25, ((row + column) % 9) * 0.01)
            for row in range(100)
            for column in range(100)
        ]
    )
    nominal = Part.makePlane(30.0, 30.0)
    return finalize_candidate(
        prepared,
        [
            {
                **actual_reference,
                "label": "Heartbeat Actual Points",
                "type_id": "Points::Feature",
                "reference_artifact_kind": "points_asc",
                "detached_points": actual,
                "point_attributes": {},
                "structured": {"width": 100, "height": 100},
                "snapshot_index": 0,
                "source_kind": "points",
                "transient_topology": False,
                "requires_semantic_interfaces": False,
                "published_interfaces": {},
            },
            {
                **nominal_reference,
                "label": "Heartbeat Nominal Plane",
                "type_id": "Part::Feature",
                "shape_type": str(nominal.ShapeType),
                "reference_artifact_kind": "brep",
                "detached_shape": nominal,
                "snapshot_index": 1,
                "source_kind": "shape",
                "transient_topology": False,
                "requires_semantic_interfaces": False,
                "published_interfaces": {},
            },
        ],
    )


def _robot_candidate(root: Path, index: int):
    """Run native Robot path generation and substantial isolated IK sampling."""

    import FreeCAD as App

    pack = get_vibescript_pack("RobotWorkbench")
    assert pack is not None and pack.production_ready
    source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "rotation = [0.7071067811865476, -8.659560562354933e-17, "
        "0.7071067811865475, 4.3297802811774664e-17]\n"
        "robot = api.robot(axis_positions=[0,-90,90,0,0,0], label='Heartbeat Robot')\n"
        "start = api.waypoint({'position':[1825,0,2400], 'rotation':rotation}, "
        "name='Start', velocity=100, acceleration=100)\n"
        "finish = api.waypoint({'position':[1825,300,2400], 'rotation':rotation}, "
        "name='Finish', velocity=100, acceleration=100)\n"
        "trajectory = api.trajectory([start,finish], label='Heartbeat Trajectory')\n"
        "dressup = api.dressup(trajectory, speed=80, label='Heartbeat DressUp')\n"
        "simulation = api.simulate(robot, dressup, sample_period=0.001, "
        "maximum_samples=5000, require_reachable=False, label=str(total))\n"
        "result = {'Robot':robot,'Trajectory':trajectory,'DressUp':dressup,"
        "'Simulation':simulation}\n"
    )
    return prepare_candidate(
        {
            "tool_name": "vibescript.robot.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "inputs": {},
                "expected_outputs": [
                    {"name": "Robot", "type": "robot"},
                    {"name": "Trajectory", "type": "trajectory"},
                    {"name": "DressUp", "type": "dressup"},
                    {"name": "Simulation", "type": "simulation"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatRobotDocument",
            "document_uid": "qt-heartbeat-robot-document",
            "document_revision": "qt-heartbeat-robot-revision",
            "document_objects": [],
            "surface": resolve_modeling_surface(
                "RobotWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
    )


def _fem_candidate(root: Path, index: int):
    """Generate and validate a complete native FEM input graph in the worker."""

    import FreeCAD as App
    import Part

    pack = get_vibescript_pack("FemWorkbench")
    assert pack is not None and pack.production_ready
    document_uid = "qt-heartbeat-fem-document"
    reference = {"document_uid": document_uid, "object_name": "HeartbeatSolid"}
    source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "solver = api.solver(label='CalculiX')\n"
        "material = api.material(name='Steel', youngs_modulus_mpa=210000, "
        "poisson_ratio=0.3, density_kg_m3=7850, label='Steel')\n"
        "fixed = api.constraint('fixed', inputs['solid'], "
        "{'type':'subelement','name':'Face1'}, label='Fixed')\n"
        "force = api.constraint('force', inputs['solid'], "
        "{'type':'subelement','name':'Face2'}, magnitude=1000, "
        "direction=[1,0,0], label='Force')\n"
        "case = api.load_case([fixed,force], label='Load Case')\n"
        "mesh = api.mesh(inputs['solid'], method='inline', "
        "nodes=[[0,0,0],[10,0,0],[10,8,0],[0,8,0],"
        "[0,0,6],[10,0,6],[10,8,6],[0,8,6]], "
        "elements=[[0,1,3,4],[1,2,3,6],[1,3,4,6],"
        "[1,4,5,6],[3,4,6,7]], element_type='tetra4', order=1, "
        "label='Mesh')\n"
        "analysis = api.analysis(solver,[material],[case],mesh,label='Analysis')\n"
        "solved = api.solve(analysis,execution='validate_only',label=str(total))\n"
        "result = {'Analysis':analysis,'Solver':solver,'Material':material,"
        "'Fixed':fixed,'Force':force,'LoadCase':case,'Mesh':mesh,'Result':solved}\n"
    )
    prepared = prepare_candidate(
        {
            "tool_name": "vibescript.fem.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {"solid": _reference_schema()},
                    "required": ["solid"],
                    "additionalProperties": False,
                },
                "inputs": {"solid": reference},
                "expected_outputs": [
                    {"name": "Analysis", "type": "analysis"},
                    {"name": "Solver", "type": "solver"},
                    {"name": "Material", "type": "material"},
                    {"name": "Fixed", "type": "constraint"},
                    {"name": "Force", "type": "constraint"},
                    {"name": "LoadCase", "type": "load_case"},
                    {"name": "Mesh", "type": "mesh"},
                    {"name": "Result", "type": "result"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatFEMDocument",
            "document_uid": document_uid,
            "document_revision": "qt-heartbeat-fem-revision",
            "document_objects": [
                {
                    "name": reference["object_name"],
                    "label": "Heartbeat Solid",
                    "type_id": "Part::Feature",
                }
            ],
            "surface": resolve_modeling_surface(
                "FemWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )
    shape = Part.makeBox(10, 8, 6)
    return finalize_candidate(
        prepared,
        [
            {
                **reference,
                "label": "Heartbeat Solid",
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "detached_shape": shape,
                "snapshot_index": 0,
                "source_kind": "shape",
                "transient_topology": False,
                "requires_semantic_interfaces": False,
                "published_interfaces": {},
            }
        ],
    )


def _cam_candidate(root: Path, index: int):
    """Generate, simulate, and postprocess a native CAM job in the worker."""

    import FreeCAD as App
    import Part

    pack = get_vibescript_pack("CAMWorkbench")
    assert pack is not None and pack.production_ready
    document_uid = "qt-heartbeat-cam-document"
    reference = {"document_uid": document_uid, "object_name": "HeartbeatSolid"}
    source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "stock = api.stock([inputs['solid']], x_negative_mm=2, "
        "x_positive_mm=2, y_negative_mm=2, y_positive_mm=2, "
        "z_negative_mm=1, z_positive_mm=2, label='Heartbeat Stock')\n"
        "tool = api.tool('endmill', diameter_mm=3, length_mm=30, flutes=2, "
        "tool_number=1, spindle_rpm=12000, horizontal_feed_mm_per_min=600, "
        "vertical_feed_mm_per_min=180, cutting_edge_height_mm=15, "
        "shank_diameter_mm=3, label='Heartbeat Endmill')\n"
        "profile = api.operation('profile', tool, start_depth_mm=10, "
        "final_depth_mm=8, step_down_mm=1, side='outside', "
        "label='Heartbeat Profile')\n"
        "generated = api.generate_toolpath(stock, [profile], "
        "simulation_resolution_mm=5, require_collision_free=False, "
        "label='Heartbeat Generated Path')\n"
        "toolpath = api.postprocess(generated, processor='grbl', "
        "units='metric', comments=False, line_numbers=False, "
        "label='Heartbeat GRBL')\n"
        "job = api.job([inputs['solid']], stock, [tool], [profile], toolpath, "
        "description=str(total), label='Heartbeat CAM Job')\n"
        "result = {'Job':job,'Stock':stock,'Tool':tool,'Profile':profile,"
        "'Toolpath':toolpath}\n"
    )
    prepared = prepare_candidate(
        {
            "tool_name": "vibescript.cam.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {"solid": _reference_schema()},
                    "required": ["solid"],
                    "additionalProperties": False,
                },
                "inputs": {"solid": reference},
                "expected_outputs": [
                    {"name": "Job", "type": "job"},
                    {"name": "Stock", "type": "stock"},
                    {"name": "Tool", "type": "tool"},
                    {"name": "Profile", "type": "operation"},
                    {"name": "Toolpath", "type": "toolpath"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatCAMDocument",
            "document_uid": document_uid,
            "document_revision": "qt-heartbeat-cam-revision",
            "document_objects": [
                {
                    "name": reference["object_name"],
                    "label": "Heartbeat Solid",
                    "type_id": "Part::Feature",
                }
            ],
            "surface": resolve_modeling_surface(
                "CAMWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )
    shape = Part.makeBox(20, 16, 10)
    return finalize_candidate(
        prepared,
        [
            {
                **reference,
                "label": "Heartbeat Solid",
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "detached_shape": shape,
                "snapshot_index": 0,
                "source_kind": "shape",
                "transient_topology": False,
                "requires_semantic_interfaces": False,
                "published_interfaces": {},
            }
        ],
    )


def _techdraw_candidate(root: Path, index: int):
    """Project a detailed solid and evaluate a native drawing dimension."""

    import FreeCAD as App
    import Part

    pack = get_vibescript_pack("TechDrawWorkbench")
    assert pack is not None and pack.production_ready
    document_uid = "qt-heartbeat-techdraw-document"
    reference = {"document_uid": document_uid, "object_name": "HeartbeatDrawingSolid"}
    source = (
        "total = 0\n"
        "for value in range(30000):\n"
        "    total += value % 7\n"
        "template = api.template('a3_landscape', "
        "editable_texts={'TITLE':'Heartbeat Drawing'})\n"
        "views = api.projection([inputs['solid']], "
        "directions=['front','top','right','rear','left','bottom'], "
        "convention='third_angle', x_mm=210, y_mm=148, scale=1, "
        "spacing_x_mm=12, spacing_y_mm=12, label='Heartbeat Views')\n"
        "dimension = api.dimension(views, 'distance', ['Edge0'], "
        "projection_direction='front', x_mm=210, y_mm=30, "
        "label='Heartbeat Width')\n"
        "note = api.annotation(str(total), x_mm=20, y_mm=20, alignment='left')\n"
        "page = api.page(template, [views, dimension, note], "
        "convention='third_angle', label='Heartbeat Page')\n"
        "result = {'Template':template,'Views':views,'Dimension':dimension,"
        "'Note':note,'Page':page}\n"
    )
    prepared = prepare_candidate(
        {
            "tool_name": "vibescript.techdraw.create_program",
            "operation": "create_program",
            "arguments": {
                "program_name": f"Qt Heartbeat {index}",
                "source": source,
                "input_schema": {
                    "type": "object",
                    "properties": {"solid": _reference_schema()},
                    "required": ["solid"],
                    "additionalProperties": False,
                },
                "inputs": {"solid": reference},
                "expected_outputs": [
                    {"name": "Template", "type": "template"},
                    {"name": "Views", "type": "projection"},
                    {"name": "Dimension", "type": "dimension"},
                    {"name": "Note", "type": "annotation"},
                    {"name": "Page", "type": "page"},
                ],
            },
            "pack": pack,
            "project_root": str(root),
            "document_name": "QtHeartbeatTechDrawDocument",
            "document_uid": document_uid,
            "document_revision": "qt-heartbeat-techdraw-revision",
            "document_objects": [
                {
                    "name": reference["object_name"],
                    "label": "Heartbeat Drawing Solid",
                    "type_id": "Part::Feature",
                }
            ],
            "surface": resolve_modeling_surface(
                "TechDrawWorkbench", "vibescript"
            ).summary(),
            "freecad_home": str(App.getHomePath()),
            "timeout_seconds": 90.0,
            "memory_limit_bytes": 3 * 1024 * 1024 * 1024,
        }
    )
    shape = Part.makeBox(120, 80, 20)
    for x in (20, 40, 60, 80, 100):
        for y in (20, 40, 60):
            shape = shape.cut(Part.makeCylinder(3, 20, App.Vector(x, y, 0)))
    return finalize_candidate(
        prepared,
        [
            {
                **reference,
                "label": "Heartbeat Drawing Solid",
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "detached_shape": shape,
                "snapshot_index": 0,
                "source_kind": "shape",
                "transient_topology": False,
                "requires_semantic_interfaces": False,
                "published_interfaces": {},
            }
        ],
    )


def main() -> int:
    application = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    del application
    root = Path(tempfile.mkdtemp(prefix="vibecad-qt-heartbeat-"))
    try:
        prepared = [
            _candidate(root, index, workbench, output_type, result)
            for index, (workbench, output_type, result) in enumerate(WORKER_CASES)
        ]
        prepared.append(_draft_candidate(root, len(prepared)))
        prepared.append(_surface_candidate(root, len(prepared)))
        prepared.append(_spreadsheet_candidate(root, len(prepared)))
        prepared.append(_material_candidate(root, len(prepared)))
        prepared.append(_mesh_candidate(root, len(prepared)))
        prepared.append(_meshpart_candidate(root, len(prepared)))
        prepared.append(_points_candidate(root, len(prepared)))
        prepared.append(_reverse_engineering_candidate(root, len(prepared)))
        prepared.append(_inspection_candidate(root, len(prepared)))
        prepared.append(_robot_candidate(root, len(prepared)))
        prepared.append(_fem_candidate(root, len(prepared)))
        prepared.append(_cam_candidate(root, len(prepared)))
        prepared.append(_techdraw_candidate(root, len(prepared)))
        prepared.append(_assembly_candidate(root, len(prepared)))
        outcomes: list[dict] = []
        failures: list[str] = []
        bridge = _Bridge()
        event_loop = QtCore.QEventLoop()
        bridge.finished.connect(event_loop.quit)
        heartbeats = [0]
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: heartbeats.__setitem__(0, heartbeats[0] + 1))

        def work() -> None:
            try:
                for candidate in prepared:
                    outcome = execute_candidate(candidate, cancellation_check=None)
                    outcomes.append(outcome)
                    if outcome.get("ok") is not True:
                        failures.append(str(outcome))
                        break
            except BaseException as exc:
                failures.append(f"{exc.__class__.__name__}: {exc}")
            finally:
                bridge.finished.emit()

        started = time.monotonic()
        worker = threading.Thread(
            target=work, name="VibeCADQtHeartbeatWorker", daemon=True
        )
        timer.start()
        worker.start()
        event_loop.exec()
        timer.stop()
        worker.join(timeout=5.0)
        elapsed = time.monotonic() - started
        assert not worker.is_alive(), "The background domain worker did not finish."
        assert not failures, failures
        assert len(outcomes) == len(prepared)
        assert heartbeats[0] >= 10, (
            "The Qt event loop did not remain responsive during domain subprocess waits: "
            f"heartbeats={heartbeats[0]}, elapsed={elapsed:.3f}s"
        )
        for candidate, outcome in zip(prepared, outcomes):
            validated = validate_candidate(candidate, outcome)
            assert validated["ok"] is True
        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "qt_domain_worker_heartbeat",
                    "domains": [candidate["pack"].domain for candidate in prepared],
                    "heartbeats": heartbeats[0],
                    "elapsed_seconds": round(elapsed, 3),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
