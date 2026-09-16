# SPDX-License-Identifier: LGPL-2.1-or-later
"""Document-independent geometry for edits shared by folded and flat views.

These operations prepare a pair of solids; they are not view-toggle operations.
The document feature layer must snapshot its inputs and bind publication to the
model revision. Original region indices belong to that preparation, not to the
topology numbering of a resulting cut solid.
"""

from math import isclose, isfinite

import Part

from SheetMetalMapping import unfold_with_mapping


_TOLERANCE = 1e-6


class SheetGeometry:
    """One prepared geometry pair with correspondence through its base sheet."""

    def __init__(self, folded, flat_face, normal, mapping, region_faces):
        self.folded = folded
        self.flat_face = flat_face
        self.normal = normal
        self.mapping = mapping
        self._region_faces = region_faces
        self.flat = flat_face.extrude(normal * -mapping.thickness)
        for shape in (self.folded, self.flat):
            if not shape.isValid() or len(shape.Solids) != 1 or shape.Volume <= 0:
                raise ValueError("A sheet edit must produce one valid solid in each representation")

    @classmethod
    def prepare(cls, shape, stationary_face_index, calculator, *, expected_thickness=None):
        if expected_thickness is not None and (
                not isfinite(expected_thickness) or expected_thickness <= 0):
            raise ValueError("Expected sheet thickness must be positive and finite")
        snapshot = shape.copy()
        mapping = unfold_with_mapping(snapshot, stationary_face_index, calculator)
        if expected_thickness is not None and not isclose(
                mapping.thickness, expected_thickness, rel_tol=_TOLERANCE, abs_tol=_TOLERANCE):
            raise ValueError(
                f"This face implies {mapping.thickness:g} mm thickness, but the source stock is "
                f"{expected_thickness:g} mm; select a planar sheet skin instead of an end wall")
        normal = snapshot.Faces[stationary_face_index].normalAt(0, 0)
        region_faces = tuple(region.flat_face() for region in mapping.regions)
        face = region_faces[0]
        if len(region_faces) > 1:
            face = face.multiFuse(list(region_faces[1:])).removeSplitter()
        if len(face.Faces) != 1:
            raise ValueError("The developed sheet must be one connected face")
        return cls(snapshot, face, normal, mapping, region_faces)

    def _region(self, face_index):
        for region in self.mapping.regions:
            if region.face_index == face_index:
                return region
        raise ValueError("The selected face does not belong to this sheet revision")

    def to_flat(self, face_index, point):
        flat = self._region(face_index).to_flat(point)
        if self.flat_face.distToShape(Part.Vertex(flat))[0] > _TOLERANCE:
            raise ValueError("The selected point has been removed from the sheet")
        return flat

    def to_folded(self, face_index, point):
        if self.flat_face.distToShape(Part.Vertex(point))[0] > _TOLERANCE:
            raise ValueError("The selected point is outside the current sheet")
        return self._region(face_index).to_folded(point)

    def map_surface_point(self, representation, face_index, point):
        """Resolve an exact result-skin point to the shared sheet definition.

        face_index is zero-based in the current result solid, not in the base
        mapping. Return (base region index, reference-skin folded point, flat
        point). Both skins are accepted; thickness walls and removed material
        are not. Rendered triangles must first project onto their analytic face.
        """
        if representation not in ("folded", "flat"):
            raise ValueError("Sheet representation must be folded or flat")
        if not all(isfinite(value) for value in point):
            raise ValueError("Sheet coordinates must be finite")
        shape = self.folded if representation == "folded" else self.flat
        if type(face_index) is not int or not 0 <= face_index < len(shape.Faces):
            raise ValueError("Select a face from the current result solid")
        face = shape.Faces[face_index]
        if face.distToShape(Part.Vertex(point))[0] > _TOLERANCE:
            raise ValueError("The point is outside the selected result face")
        normal = face.normalAt(*face.Surface.parameter(point))
        matches = []
        if representation == "flat":
            if (not isinstance(face.Surface, Part.Plane)
                    or abs(normal.dot(self.normal)) < 1-_TOLERANCE):
                raise ValueError("Select a sheet skin, not a thickness or cut wall")
            origin = self.flat_face.Vertexes[0].Point
            depth = (point-origin).dot(self.normal)
            if min(abs(depth), abs(depth+self.mapping.thickness)) > _TOLERANCE:
                raise ValueError("The point is not on either sheet skin")
            flat = point-self.normal*depth
            for region in self.mapping.regions:
                try:
                    matches.append((region.face_index, self.to_folded(region.face_index, flat), flat))
                except ValueError:
                    continue
        else:
            for region in self.mapping.regions:
                reference = region._face
                if not isinstance(face.Surface, type(reference.Surface)):
                    continue
                u, v = reference.Surface.parameter(point)
                folded = reference.valueAt(u, v)
                outward = reference.normalAt(u, v)
                depth = (point-folded).dot(outward)
                if (abs(normal.dot(outward)) < 1-_TOLERANCE
                        or min(abs(depth), abs(depth+self.mapping.thickness)) > _TOLERANCE
                        or (point-folded-outward*depth).Length > _TOLERANCE):
                    continue
                try:
                    flat = self.to_flat(region.face_index, folded)
                    matches.append((region.face_index, folded, flat))
                except ValueError:
                    continue
        if not matches:
            raise ValueError("Select remaining material on a mapped sheet skin")
        first = matches[0]
        if any((folded-first[1]).Length > _TOLERANCE or (flat-first[2]).Length > _TOLERANCE
               for _, folded, flat in matches[1:]):
            raise ValueError("The sheet pick has ambiguous folded/flat correspondence")
        # At a tangent seam adjacent regions have the same two points. Either
        # maps correctly; choose deterministically within this prepared revision.
        return min(matches, key=lambda match: match[0])

    def develop_profile(self, profile):
        """Map a planar folded-skin profile without moving its authored sketch.

        Developed profiles keep their existing interpretation. Otherwise only
        real planar skin planes are accepted, including the opposite skin at
        the stock thickness. Ambiguous maps are rejected rather than guessed.
        This is geometry preparation and must run on the edit worker.
        """
        if not profile.isValid() or not profile.Faces:
            raise ValueError("The cut profile must contain valid planar faces")

        def on_plane(origin, normal):
            return all(isinstance(face.Surface, Part.Plane)
                       and abs(abs(face.normalAt(0, 0).dot(normal))-1) <= _TOLERANCE
                       and abs((face.CenterOfMass-origin).dot(normal)) <= _TOLERANCE
                       for face in profile.Faces)

        if on_plane(self.flat_face.Vertexes[0].Point, self.normal):
            return profile
        candidates = []
        for region, developed in zip(self.mapping.regions, self._region_faces):
            if region.kind != "plane":
                continue
            normal = region._face.normalAt(0, 0)
            origin = region._face.CenterOfMass
            for depth in (0, -self.mapping.thickness):
                if not on_plane(origin + normal*depth, normal):
                    continue
                skin = profile.copy()
                skin.translate(normal * -depth)
                candidate = skin.transformed(region._transform)
                if candidate.common(developed.copy()).Area > _TOLERANCE**2:
                    candidates.append(candidate)
        if not candidates:
            raise ValueError("The cut sketch must lie on the developed plane or a planar sheet skin")
        first = candidates[0]
        if any(first.cut(other).Area > _TOLERANCE**2
               or other.cut(first).Area > _TOLERANCE**2 for other in candidates[1:]):
            raise ValueError("The folded cut sketch has ambiguous sheet-region correspondence")
        return first

    def cut(self, profile):
        """Cut a developed profile through all intersected sheet regions.

        A circle authored across a bend remains circular in development. In the
        folded solid its boundary follows each planar/cylindrical region, with
        the corresponding through-thickness cut. No faceted approximation or
        single rigid transform is used to represent a curved bend.
        """
        if not profile.isValid() or not profile.Faces:
            raise ValueError("The cut profile must contain valid planar faces")
        origin = self.flat_face.Vertexes[0].Point
        for face in profile.Faces:
            if (not isinstance(face.Surface, Part.Plane)
                    or abs(abs(face.normalAt(0, 0).dot(self.normal))-1) > _TOLERANCE
                    or abs((face.CenterOfMass-origin).dot(self.normal)) > _TOLERANCE):
                raise ValueError("The cut profile must be on the developed sheet plane")
        # The flat face also backs the parent's cached extrusion. Booleans
        # must operate on detached topology in both representations.
        flat_face = self.flat_face.copy()
        cut_area = flat_face.common(profile)
        if cut_area.Area <= _TOLERANCE**2:
            raise ValueError("The cut profile does not intersect remaining sheet material")
        cutters = []
        for region, developed in zip(self.mapping.regions, self._region_faces):
            intersection = cut_area.common(developed.copy())
            for patch in intersection.Faces:
                if patch.Area <= _TOLERANCE**2:
                    continue
                for folded_face in region.fold_face(patch).Faces:
                    cutter = folded_face.makeOffsetShape(
                        -self.mapping.thickness, _TOLERANCE/10, fill=True)
                    if not cutter.isValid() or not cutter.Solids:
                        raise ValueError("Could not build a valid through-thickness cutter")
                    cutters.append(cutter)
        # OCCT Boolean operations can update tolerances on their operands.
        # The parent is shared with persisted History and other edit workers.
        folded = self.folded.copy().cut(Part.makeCompound(cutters)).removeSplitter()
        remaining = flat_face.cut(cut_area).removeSplitter()
        return SheetGeometry(folded, remaining, self.normal, self.mapping, self._region_faces)
