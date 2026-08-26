# SPDX-License-Identifier: LGPL-2.1-or-later
"""Geometry/mesh preparation for VibeCADAero CFD backends.

FreeCAD and Gmsh imports are deliberately lazy.  The module can therefore be
imported by unit tests without a FreeCAD installation, while the live runtime
still gets native Part/Mesh/MeshPart behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Sequence

from AeroCFDContracts import Artifact, GeometryArtifact


class MeshError(RuntimeError):
    pass


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _active_selection() -> list[Any]:
    import FreeCADGui as Gui  # type: ignore

    selected = list(Gui.Selection.getSelection())
    if not selected:
        raise MeshError("Select at least one Part/Body/mesh object for CFD geometry export.")
    return selected


def _shape_from_objects(objects: Sequence[Any]) -> Any:
    import Part  # type: ignore

    shapes = [obj.Shape for obj in objects if hasattr(obj, "Shape") and not obj.Shape.isNull()]
    if not shapes:
        raise MeshError("Selection contains no non-null Part shapes.")
    return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)


def export_shape_to_stl(
    shape: Any,
    path: str | Path,
    *,
    linear_deflection_mm: float = 0.08,
    angular_deflection_rad: float = 0.15,
) -> str:
    """Tessellate a Part shape and write an STL through FreeCAD MeshPart.

    No assumption is made here about ASCII versus binary STL; the artifact is
    verified by content hash.  A later packaging pass can pin the exact writer
    mode after it is tested against VibeCAD's FreeCAD version.
    """

    import MeshPart  # type: ignore

    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    mesh = MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=float(linear_deflection_mm),
        AngularDeflection=float(angular_deflection_rad),
        Relative=False,
    )
    if int(getattr(mesh, "CountFacets", 0)) <= 0:
        raise MeshError("FreeCAD tessellation produced an empty mesh.")
    mesh.write(str(p))
    if not p.is_file() or p.stat().st_size <= 84:
        raise MeshError("STL export did not produce a valid non-empty file.")
    return str(p)


def geometry_revision(document: Any, cfg: dict[str, Any] | None = None) -> str:
    """Reuse upstream AeroPreview revision semantics when available."""

    try:
        import AeroPreview  # type: ignore
        import AeroConfig  # type: ignore

        resolved = cfg if cfg is not None else AeroConfig.resolve_geometry(document)
        return str(AeroPreview.geometry_revision(document, resolved))
    except Exception:
        # Deterministic fallback for hosts that lack AeroPreview.  This is not as
        # rich as the upstream document revision and is therefore clearly marked.
        names = sorted(str(getattr(o, "Name", "")) for o in getattr(document, "Objects", []) or [])
        raw = json.dumps(names, separators=(",", ":")).encode("utf-8")
        return "fallback:" + hashlib.sha256(raw).hexdigest()


def prepare_geometry_from_selection(
    output_path: str | Path,
    *,
    document: Any | None = None,
    linear_deflection_mm: float = 0.08,
    angular_deflection_rad: float = 0.15,
) -> GeometryArtifact:
    import FreeCAD as App  # type: ignore

    doc = document or App.ActiveDocument
    if doc is None:
        raise MeshError("No active FreeCAD document.")
    selected = _active_selection()
    shape = _shape_from_objects(selected)
    stl = export_shape_to_stl(
        shape,
        output_path,
        linear_deflection_mm=linear_deflection_mm,
        angular_deflection_rad=angular_deflection_rad,
    )
    artifact = Artifact.from_file(stl, media_type="model/stl", role="solver_geometry")
    return GeometryArtifact(
        artifact=artifact,
        geometry_revision=geometry_revision(doc),
        source_object_names=tuple(str(getattr(o, "Name", "")) for o in selected),
        source_units="mm",
        solver_units="m",
        triangulation_linear_deflection_mm=float(linear_deflection_mm),
        triangulation_angular_deflection_rad=float(angular_deflection_rad),
    )


def import_stl(path: str | Path, *, name: str = "ImportedSTL", document: Any | None = None) -> Any:
    import FreeCAD as App  # type: ignore
    import Mesh  # type: ignore

    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    mesh = Mesh.Mesh(str(p))
    if int(getattr(mesh, "CountFacets", 0)) <= 0:
        raise MeshError("STL contains no facets.")
    doc = document or App.ActiveDocument or App.newDocument("AeroMesh")
    obj = doc.addObject("Mesh::Feature", name)
    obj.Mesh = mesh
    obj.Label = name
    doc.recompute()
    return obj


def export_mesh_to_stl(mesh_or_object: Any, path: str | Path) -> str:
    """Write a raw ``Mesh.Mesh`` or ``Mesh::Feature`` to STL."""

    mesh = getattr(mesh_or_object, "Mesh", mesh_or_object)
    if int(getattr(mesh, "CountFacets", 0)) <= 0:
        raise MeshError("Mesh is empty.")
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    mesh.write(str(p))
    return str(p)


def subdivide_tri6(points: Sequence[Any]) -> list[list[Any]]:
    """Linearize a Gmsh 6-node quadratic triangle into four TRI3 facets."""

    if len(points) != 6:
        raise MeshError("TRI6 requires exactly six nodes.")
    n0, n1, n2, n3, n4, n5 = points
    return [
        [n0, n3, n5],
        [n3, n1, n4],
        [n5, n4, n2],
        [n3, n4, n5],
    ]


def _chunks(values: Iterable[int], size: int) -> Iterable[tuple[int, ...]]:
    bucket: list[int] = []
    for value in values:
        bucket.append(int(value))
        if len(bucket) == size:
            yield tuple(bucket)
            bucket = []
    if bucket:
        raise MeshError(f"element connectivity length is not divisible by {size}")


def gmsh_current_surface_to_freecad_mesh() -> Any:
    """Convert the current Gmsh surface mesh into ``Mesh.Mesh``.

    Canonical supported element types in this reference:
    * type 2  TRI3: direct
    * type 3  QUAD4: split into two triangles
    * type 9  TRI6: topologically subdivided into four triangles

    Other higher-order elements are rejected rather than silently reducing them
    to corner nodes.  That strict behavior corrects an unsafe fallback from the
    prior discussion.
    """

    import FreeCAD as App  # type: ignore
    import Mesh  # type: ignore
    import gmsh  # type: ignore

    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    coords = list(coordinates)
    if len(coords) != 3 * len(node_tags):
        raise MeshError("Gmsh node coordinate payload is malformed.")
    tag_to_vec = {
        int(tag): App.Vector(float(coords[3 * i]), float(coords[3 * i + 1]), float(coords[3 * i + 2]))
        for i, tag in enumerate(node_tags)
    }

    element_types, _element_tags, element_nodes = gmsh.model.mesh.getElements(dim=2)
    triangles: list[list[Any]] = []
    unsupported: set[int] = set()
    for etype, flat in zip(element_types, element_nodes):
        et = int(etype)
        if et == 2:  # TRI3
            for tags in _chunks(flat, 3):
                triangles.append([tag_to_vec[tag] for tag in tags])
        elif et == 3:  # QUAD4
            for tags in _chunks(flat, 4):
                p = [tag_to_vec[tag] for tag in tags]
                triangles.extend(([p[0], p[1], p[2]], [p[0], p[2], p[3]]))
        elif et == 9:  # TRI6
            for tags in _chunks(flat, 6):
                triangles.extend(subdivide_tri6([tag_to_vec[tag] for tag in tags]))
        else:
            unsupported.add(et)

    if unsupported:
        raise MeshError(
            "Unsupported Gmsh surface element type(s): " + ", ".join(str(v) for v in sorted(unsupported))
        )
    if not triangles:
        raise MeshError("Gmsh conversion produced no surface triangles.")
    return Mesh.Mesh(triangles)


def shape_to_gmsh_surface_mesh(
    shape: Any,
    *,
    max_size_mm: float = 5.0,
    min_size_mm: float = 0.5,
    element_order: int = 1,
) -> Any:
    """Part.Shape -> Gmsh OCC -> FreeCAD surface mesh."""

    import gmsh  # type: ignore

    if int(element_order) not in (1, 2):
        raise MeshError("this reference supports Gmsh element order 1 or 2")
    brep = tempfile.NamedTemporaryFile(suffix=".brep", delete=False)
    brep.close()
    shape.exportBrep(brep.name)
    initialized_here = False
    try:
        if not gmsh.isInitialized():
            gmsh.initialize()
            initialized_here = True
        gmsh.clear()
        gmsh.model.add("vibecad_cfd")
        gmsh.model.occ.importShapes(brep.name)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", float(max_size_mm))
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", float(min_size_mm))
        gmsh.option.setNumber("Mesh.ElementOrder", int(element_order))
        gmsh.model.mesh.generate(2)
        return gmsh_current_surface_to_freecad_mesh()
    finally:
        try:
            os.unlink(brep.name)
        except OSError:
            pass
        if initialized_here:
            gmsh.finalize()


def mesh_to_part_shape(mesh_or_object: Any, *, tolerance_mm: float = 0.1) -> Any:
    """Convert mesh topology to a Part shape; no solid claim is made."""

    import Part  # type: ignore

    mesh = getattr(mesh_or_object, "Mesh", mesh_or_object)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, float(tolerance_mm))
    if shape.isNull():
        raise MeshError("FreeCAD makeShapeFromMesh produced a null shape.")
    return shape


def mesh_to_solid(mesh_or_object: Any, *, tolerance_mm: float = 0.1) -> Any:
    """Attempt to create a Part solid from a closed mesh-derived shell.

    This is intentionally strict.  Open/non-manifold meshes fail instead of
    being mislabeled as solids.
    """

    import Part  # type: ignore

    shape = mesh_to_part_shape(mesh_or_object, tolerance_mm=tolerance_mm)
    shells = list(getattr(shape, "Shells", []) or [])
    if not shells:
        # Some FreeCAD builds return a single shell-shaped object directly.
        if str(getattr(shape, "ShapeType", "")) == "Shell":
            shells = [shape]
        else:
            raise MeshError("Mesh-derived shape contains no shell.")
    if len(shells) != 1:
        raise MeshError(f"Expected one closed shell, found {len(shells)}.")
    shell = shells[0]
    if not bool(shell.isClosed()):
        raise MeshError("Mesh-derived shell is not closed/manifold enough to form a solid.")
    solid = Part.makeSolid(shell)
    if solid.isNull() or not bool(solid.isValid()):
        raise MeshError("Part.makeSolid produced an invalid solid.")
    return solid
