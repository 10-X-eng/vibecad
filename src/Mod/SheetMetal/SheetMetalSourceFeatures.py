# SPDX-License-Identifier: LGPL-2.1-or-later
"""Opt-in upstream builders with transient, worker-prepared validity evidence.

The original builders and their document properties remain compatible. Native
creation uses these proxies to validate on the recompute worker before reporting
success; this proves solid validity, not that a source can be unfolded.
"""

import copy
import math

from SheetMetalBaseShapeCmd import SMBaseShape
from SheetMetalBaseCmd import SMBaseBend
from SheetMetalFromSolid import SMFromSolid
from SheetMetalCmd import SMBendWall
from SheetMetalFoldCmd import SMFoldWall
from SheetMetalNewUnfolder import EstimateThickness


def nominal_thickness(obj):
    """Read the gauge contract of supported upstream builders, without geometry work."""
    proxy = getattr(obj, "Proxy", None)
    if isinstance(proxy, SMBaseShape):
        value = float(obj.thickness)
    elif isinstance(proxy, (SMBaseBend, SMFromSolid)):
        value = float(obj.Thickness)
    elif isinstance(proxy, (Flange, Fold)):
        if not obj.baseObject:
            return None
        parent = obj.baseObject[0]
        import SheetMetalEditable as Editable
        if isinstance(getattr(parent, "Proxy", None), Editable.PreparedSheetState):
            import SheetMetalCutHistory as History
            root, _ = History._chain(parent)
            thickness = nominal_thickness(root.SourceFace[0])
            if thickness is not None:
                # Restored cut states have no transient mapping yet. Their
                # source's persisted gauge also applies after cuts and folds.
                return thickness
            return Editable.get_state_geometry(parent).mapping.thickness
        return nominal_thickness(parent)
    else:
        return None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Source sheet thickness must be positive and finite")
    return value


def _reference_faces(shape, summary, thickness):
    """Bound worker inspection; candidates still require complete unfold validation."""
    candidates, checked = [], []
    for face in summary["planar_faces"]:
        try:
            inferred = EstimateThickness.using_best_method(shape, int(face["name"][4:])-1)
        except (RuntimeError, ValueError):
            continue
        checked.append(face["name"])
        if math.isclose(inferred, thickness, rel_tol=1e-6, abs_tol=1e-6):
            candidates.append(dict(face))
    complete = len(checked) == summary["planar_face_count"]
    return {"thickness_mm": thickness, "reference_faces": candidates,
            "reference_faces_checked": checked,
            "reference_face_count": len(candidates) if complete else None,
            "reference_face_scan_complete": complete}


def _validate_shape(shape):
    import Part

    if shape.isNull() or len(shape.Solids) != 1 or not shape.isValid():
        raise RuntimeError("The sheet source must produce one valid solid")
    bounds = shape.BoundBox
    size = (bounds.XLength, bounds.YLength, bounds.ZLength)
    volume = shape.Volume
    if not all(math.isfinite(value) and value > 0 for value in (*size, volume)):
        raise RuntimeError("The sheet source has invalid dimensions or volume")
    planar_faces = [{"name": f"Face{index}", "area_mm2": face.Area}
                    for index, face in enumerate(shape.Faces, 1)
                    if isinstance(face.Surface, Part.Plane)]
    planar_faces.sort(key=lambda face: face["area_mm2"], reverse=True)
    return {"solid_count": 1, "volume_mm3": volume, "size_mm": size,
            "planar_faces": planar_faces[:32], "planar_face_count": len(planar_faces)}


class _ValidatedSource:
    def execute(self, obj):
        self._prepared_source = None
        super().execute(obj)
        shape = obj.Shape
        summary = _validate_shape(shape)
        thickness = nominal_thickness(obj)
        if thickness is not None:
            summary.update(_reference_faces(shape, summary, thickness))
        self._prepared_source = (shape, summary)

    def __getstate__(self):
        # Persist editable document properties, never cached OCCT wrappers.
        return None

    def __setstate__(self, state):
        self._prepared_source = None


class BaseShape(_ValidatedSource, SMBaseShape):
    """Upstream base-shape parameters with worker-side result validation."""


class BaseBend(_ValidatedSource, SMBaseBend):
    """Upstream sheet builder retaining its exact editable sketch link."""


class FromSolid(_ValidatedSource, SMFromSolid):
    """Upstream conversion retaining the original solid and selected subelements."""

    def execute(self, obj):
        self._prepared_source = None
        if not obj.baseObject or not obj.baseObject[0].Shape.Faces:
            raise RuntimeError("Restore the sheet source's solid input before rebuilding")
        super().execute(obj)


class Flange(_ValidatedSource, SMBendWall):
    """Upstream edge flange retaining its exact parent and selected edges/faces."""


class Fold(_ValidatedSource, SMFoldWall):
    """Upstream internal fold retaining its exact parent skin and bend sketch."""


def get_prepared_source(obj):
    """Read detached evidence on the GUI thread without rerunning BRep validation."""
    from SheetMetalOperations import capture_source_revision

    revision = capture_source_revision(obj)
    if any({"Touched", "Invalid"}.intersection(item.State)
           for item in (obj, *obj.OutListRecursive)):
        raise RuntimeError("Prepare the sheet source's current geometry first")
    proxy = getattr(obj, "Proxy", None)
    prepared = getattr(proxy, "_prepared_source", None)
    if not isinstance(proxy, _ValidatedSource) or prepared is None:
        raise RuntimeError("Recompute the sheet source to validate its geometry")
    shape, summary = prepared
    if not obj.Shape.isSame(shape) or nominal_thickness(obj) != summary.get("thickness_mm"):
        raise RuntimeError("The sheet source changed; recompute to validate its geometry")
    return {**revision.summary(), **copy.deepcopy(summary), "size_mm": list(summary["size_mm"])}
