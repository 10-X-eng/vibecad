# SPDX-License-Identifier: LGPL-2.1-or-later
"""Retain upstream unfolding correspondence for edits in either representation.

Coordinates are in the input shape's frame, with the developed sheet in the
stationary face's plane. Face indices belong only to this geometry snapshot;
they are not stable feature identities across edits. Callers must bind the
snapshot to their model revision before using it for selection or publication.
"""

from dataclasses import dataclass
from math import isfinite, pi

import Part
from FreeCAD import Base, Vector

from SheetMetalNewUnfolder import BendDirection, UVRef, _unfold


_POINT_TOLERANCE = 1e-6


class FaceRegion:
    """Bidirectional surface coordinates for one planar or cylindrical region.

    This maps points on the selected sheet skin, not arbitrary solid volumes.
    Cylinders use their developed neutral-axis length, including the material's
    allowance. Both directions enforce trimmed boundaries (including holes).
    """

    def __init__(self, face_index, face, transform, thickness, bac,
                 alignment=None, uvref=None):
        self.face_index = face_index
        self._face = face.copy()
        self._bounds = face.ParameterRange
        if isinstance(face.Surface, Part.Plane):
            self.kind = "plane"
            self._transform = transform
        elif isinstance(face.Surface, Part.Cylinder) and alignment is not None:
            self.kind = "bend"
            self._transform = transform * alignment
            u0, u1, _, _ = self._bounds
            self._neutral_radius = bac.get_bend_allowance(
                BendDirection.from_face(face), face.Surface.Radius, thickness,
                u1 - u0,
            ) / (u1 - u0)
            if not isfinite(self._neutral_radius) or self._neutral_radius <= 0:
                raise ValueError("Bend allowance must produce a positive neutral radius")
            self._mirror_x = uvref in (UVRef.TOP_LEFT, UVRef.TOP_RIGHT)
            self._mirror_y = uvref in (UVRef.BOTTOM_RIGHT, UVRef.TOP_RIGHT)
        else:
            raise ValueError(f"Face{face_index + 1} has no supported sheet-region map")
        # Upstream unbend matrices compose only rotations and translations.
        # General matrix arithmetic can introduce a tiny scale/shear, which
        # OCCT rejects when a Boolean consumes the transformed planar face.
        # Retain the rigid transform explicitly for both geometry and picks.
        placement = Base.Placement(self._transform)
        self._transform = placement.toMatrix()
        self._inverse = placement.inverse().toMatrix()

    @staticmethod
    def _check_finite(point):
        if not all(isfinite(coordinate) for coordinate in (point.x, point.y, point.z)):
            raise ValueError("Sheet coordinates must be finite")

    def _check_folded(self, point):
        self._check_finite(point)
        if self._face.distToShape(Part.Vertex(point))[0] > _POINT_TOLERANCE:
            raise ValueError(f"Point lies outside sheet region Face{self.face_index + 1}")

    def to_flat(self, point):
        """Map a point on this folded region to the developed sheet surface."""
        self._check_folded(point)
        if self.kind == "plane":
            return self._transform.multVec(point)
        u0, u1, v0, v1 = self._bounds
        u, v = self._face.Surface.parameter(point)
        # Surface.parameter may return the equivalent angle on the other side
        # of the periodic seam. Choose the period belonging to this patch.
        u += 2*pi * round(((u0 + u1)/2 - u) / (2*pi))
        x = v1 - v if self._mirror_x else v - v0
        y = (u1 - u if self._mirror_y else u - u0) * self._neutral_radius
        return self._transform.multVec(Vector(x, y, 0))

    def to_folded(self, point):
        """Map a developed surface point back onto this folded region."""
        self._check_finite(point)
        local = self._inverse.multVec(point)
        if self.kind == "plane":
            folded = local
        else:
            if abs(local.z) > _POINT_TOLERANCE:
                raise ValueError("Point is not on the developed sheet surface")
            u0, u1, v0, v1 = self._bounds
            angle = local.y / self._neutral_radius
            u = u1 - angle if self._mirror_y else u0 + angle
            v = v1 - local.x if self._mirror_x else v0 + local.x
            # Do not let periodic cylinder evaluation wrap an out-of-patch
            # flat point back onto valid folded material.
            angular_tolerance = _POINT_TOLERANCE / self._neutral_radius
            if (u < u0 - angular_tolerance or u > u1 + angular_tolerance
                    or v < v0 - _POINT_TOLERANCE or v > v1 + _POINT_TOLERANCE):
                raise ValueError("Point lies outside the developed bend region")
            folded = self._face.valueAt(u, v)
        self._check_folded(folded)
        return folded

    def flat_face(self):
        """Return this region's developed trimmed face, retaining curve knots.

        Cylinder UV coordinates undergo an affine transformation. Transforming
        their rational B-spline poles and retaining weights/knots preserves the
        curves, unlike transforming poles through a nonlinear 3D bend.
        """
        if self.kind == "plane":
            return self._face.transformed(self._transform)
        u0, u1, v0, v1 = self._bounds
        wires = []
        for wire in self._face.Wires:
            edges = []
            for edge in wire.Edges:
                curve, first, last = self._face.curveOnSurface(edge)
                spline = curve.toBSpline(first, last)
                poles = []
                for uv in spline.getPoles():
                    x = v1 - uv.y if self._mirror_x else uv.y - v0
                    y = (u1 - uv.x if self._mirror_y else uv.x - u0) * self._neutral_radius
                    poles.append(self._transform.multVec(Vector(x, y, 0)))
                flat_curve = Part.BSplineCurve()
                flat_curve.buildFromPolesMultsKnots(
                    poles, spline.getMultiplicities(), spline.getKnots(),
                    spline.isPeriodic(), spline.Degree, spline.getWeights(),
                )
                mapped = flat_curve.toShape()
                mapped.Orientation = edge.Orientation
                edges.append(mapped)
            wires.append(self._closed_wire(edges))
        flat = Part.makeFace(wires, "Part::FaceMakerBullseye")
        normal = (self._transform.multVec(Vector(0, 0, 1))
                  - self._transform.multVec(Vector()))
        if flat.Faces[0].normalAt(0, 0).dot(normal) < 0:
            flat.reverse()
        return flat

    @staticmethod
    def _closed_wire(edges):
        # TopoShape.Edges is not a traversal of the wire. MakeWire can silently
        # omit an edge presented before its neighbors, so sort and verify it.
        wire = Part.Wire(Part.__sortEdges__(edges))
        if not wire.isClosed() or len(wire.Edges) != len(edges):
            raise ValueError("Mapped sheet boundary is not a complete closed wire")
        return wire

    def fold_face(self, flat_face):
        """Wrap a trimmed developed patch onto this region's original surface.

        Callers clip a cut to ``flat_face()`` before wrapping it. This method
        performs geometry work and does not modify documents or presentation.
        """
        if flat_face.cut(self.flat_face()).Area > _POINT_TOLERANCE**2:
            raise ValueError("Patch extends outside the developed sheet region")
        if self.kind == "plane":
            folded = flat_face.transformed(self._inverse)
        else:
            u0, u1, v0, v1 = self._bounds

            def wrap_wire(wire):
                edges = []
                for edge in wire.Edges:
                    spline = edge.Curve.toBSpline(*edge.ParameterRange)
                    poles = []
                    for point in spline.getPoles():
                        local = self._inverse.multVec(point)
                        if abs(local.z) > _POINT_TOLERANCE:
                            raise ValueError("Patch is not on the developed sheet plane")
                        u = u1-local.y/self._neutral_radius if self._mirror_y else u0+local.y/self._neutral_radius
                        v = v1-local.x if self._mirror_x else v0+local.x
                        poles.append(Base.Vector2d(u, v))
                    curve = Part.Geom2d.BSplineCurve2d()
                    curve.buildFromPolesMultsKnots(
                        poles, spline.getMultiplicities(), spline.getKnots(),
                        spline.isPeriodic(), spline.Degree, spline.getWeights(),
                    )
                    mapped = curve.toShape(self._face.Surface)
                    mapped.Orientation = edge.Orientation
                    edges.append(mapped)
                return self._closed_wire(edges)

            outer = flat_face.OuterWire
            folded = Part.Face(self._face.Surface, wrap_wire(outer))
            # As in upstream bend_solid, repair wire orientation and curve
            # parameter consistency at a cylinder's periodic seam before BOP.
            folded.validate()
            for wire in flat_face.Wires:
                if not wire.isSame(outer):
                    inner = Part.Face(self._face.Surface, wrap_wire(wire))
                    inner.validate()
                    folded = folded.cut(inner)
        faces = []
        for face in folded.Faces:
            if self.kind == "plane":
                if face.normalAt(0, 0).dot(self._face.normalAt(0, 0)) < 0:
                    face.reverse()
            else:
                face.Orientation = self._face.Orientation
            faces.append(face)
        if not faces or not all(face.isValid() for face in faces):
            raise ValueError("The wrapped sheet patch is invalid")
        return faces[0] if len(faces) == 1 else Part.makeCompound(faces)


@dataclass(frozen=True)
class UnfoldMapping:
    edges: tuple
    bends: tuple
    regions: tuple
    thickness: float


def unfold_with_mapping(shape, root_face_index, bac):
    """Unfold a solid while retaining exact planar/cylindrical point maps.

    This performs geometry work and belongs in preparation for a model revision,
    never in a warmed view toggle. It reads no documents or GUI state. The legacy
    ``SheetMetalNewUnfolder.unfold`` API remains unchanged.
    """
    if (not isinstance(root_face_index, int) or root_face_index < 0
            or root_face_index >= len(shape.Faces)):
        raise ValueError("Stationary face index is outside the input shape")
    if not isinstance(shape.Faces[root_face_index].Surface, Part.Plane):
        raise ValueError("The stationary sheet face must be planar")
    if not shape.isValid() or len(shape.Solids) != 1:
        raise ValueError("Sheet correspondence requires one valid solid")
    snapshot = shape.copy()
    regions = []
    thicknesses = []

    def collect(face_index, face, transform, thickness, alignment, uvref):
        regions.append(FaceRegion(face_index, face, transform, thickness, bac,
                                  alignment, uvref))
        thicknesses.append(thickness)

    edges, bends = _unfold(snapshot, root_face_index, bac, region_sink=collect)
    return UnfoldMapping(tuple(edges), tuple(bends), tuple(regions), thicknesses[0])
