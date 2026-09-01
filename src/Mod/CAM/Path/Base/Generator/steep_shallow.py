# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

################################################################################
#                                                                              #
#   FreeCAD is free software: you can redistribute it and/or modify            #
#   it under the terms of the GNU Lesser General Public License as             #
#   published by the Free Software Foundation, either version 2.1              #
#   of the License, or (at your option) any later version.                     #
#                                                                              #
#   FreeCAD is distributed in the hope that it will be useful,                 #
#   but WITHOUT ANY WARRANTY; without even the implied warranty                #
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                    #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with FreeCAD. If not, see https://www.gnu.org/licenses       #
#                                                                              #
################################################################################

"""
Steep/shallow 3-D finishing toolpath generator.

Splits a sampled cutter-location (CL) heightfield into steep and shallow
regions by comparing the local surface slope against a threshold angle,
then emits the appropriate finishing strategy for each region:

* Steep regions (slope >= threshold): constant-Z contour passes
  (waterline style). Iso-lines of the heightfield are extracted with
  marching squares and stepped down by ``stepdown``.
* Shallow regions (slope < threshold): surface-following parallel
  passes along the X axis, stepped over in Y by ``stepover``.

The heightfield is a cutter-location grid: ``heightfield[i][j]`` is the
lowest gouge-free Z for the tool control point positioned at
``(xs[i], ys[j])``, typically produced by an OCL drop-cutter sample.
Every emitted cutting position lies on the (bilinearly interpolated)
CL surface, so the output is gouge-free by construction.

``boundary_overlap`` grows the shallow region into the steep region so
the two strategies overlap instead of leaving an unmachined stripe at
the classification boundary.

``rest_reference_diameter`` enables rest machining: both strategies
are restricted to the region a ball-end reference tool of that
diameter could not machine down to the CL surface (where the
morphological closing of the heightfield with the reference ball stays
above the surface), so only material left behind by a previous, larger
tool is re-cut.

This module is deliberately FreeCAD-geometry-free and OCL-free: it
consumes plain numeric arrays and returns ``Path.Command`` objects, so
it can be unit tested headless without OCL installed.
"""

import math

import numpy

import Path

__title__ = "Steep/Shallow 3D Finishing Generator"
__author__ = "FreeCAD CAM developers"
__url__ = "https://www.freecad.org"
__doc__ = (
    "Generate steep (constant-Z) and shallow (surface-following) finishing passes."
)

__all__ = ["generate"]


# Marching-squares segment table. Corner bits: A=(i,j)=1, B=(i+1,j)=2,
# C=(i+1,j+1)=4, D=(i,j+1)=8; a bit is set when the corner is above the
# contour level. Edge names: b(ottom)=A-B, r(ight)=B-C, t(op)=D-C,
# l(eft)=A-D. Saddle cases 5 and 10 are resolved at runtime from the
# cell-center average.
_CASE_SEGMENTS = {
    1: (("l", "b"),),
    2: (("b", "r"),),
    3: (("l", "r"),),
    4: (("r", "t"),),
    6: (("b", "t"),),
    7: (("l", "t"),),
    8: (("l", "t"),),
    9: (("b", "t"),),
    11: (("r", "t"),),
    12: (("l", "r"),),
    13: (("b", "r"),),
    14: (("l", "b"),),
}


def _dilate(mask, iterations: int):
    """Grow a boolean mask by `iterations` cells (8-neighborhood).

    Chebyshev dilation never under-extends the euclidean overlap
    distance along any direction, so the shallow passes always reach at
    least `boundary_overlap` into the steep region.
    """
    out = mask.copy()
    for _ in range(iterations):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        grown[1:, 1:] |= out[:-1, :-1]
        grown[1:, :-1] |= out[:-1, 1:]
        grown[:-1, 1:] |= out[1:, :-1]
        grown[:-1, :-1] |= out[1:, 1:]
        out = grown
    return out


def _morph_drop(z, xs, ys, radius: float, dilate: bool):
    """Morphological drop-cutter sweep of a ball of `radius` over `z`.

    ``drop(r) = radius - sqrt(radius^2 - r^2)`` is the height of the
    ball's cutting edge above its tip at horizontal offset ``r``. With
    ``dilate=True`` this returns the cutter-location surface of the
    ball tip dropped onto ``z`` (max over the footprint of
    ``z - drop``); with ``dilate=False`` it returns the surface swept
    by a ball whose tip follows ``z`` (min over the footprint of
    ``z + drop``). Handles non-uniform grids by evaluating true
    horizontal distances per node pair.
    """
    nx, ny = z.shape
    max_di = int(radius / float(numpy.diff(xs).min()) + 1e-9)
    max_dj = int(radius / float(numpy.diff(ys).min()) + 1e-9)
    r2_max = radius * radius + 1e-12
    out = z.copy()
    for di in range(-max_di, max_di + 1):
        dst_i = slice(max(0, -di), nx - max(0, di))
        src_i = slice(max(0, di), nx + min(0, di))
        dx = xs[src_i] - xs[dst_i]
        for dj in range(-max_dj, max_dj + 1):
            if di == 0 and dj == 0:
                continue
            dst_j = slice(max(0, -dj), ny - max(0, dj))
            src_j = slice(max(0, dj), ny + min(0, dj))
            dy = ys[src_j] - ys[dst_j]
            r2 = dx[:, None] ** 2 + dy[None, :] ** 2
            inside = r2 <= r2_max
            if not inside.any():
                continue
            drop = radius - numpy.sqrt(numpy.maximum(radius * radius - r2, 0.0))
            if dilate:
                cand = numpy.where(inside, z[src_i, src_j] - drop, -numpy.inf)
                numpy.maximum(out[dst_i, dst_j], cand, out=out[dst_i, dst_j])
            else:
                cand = numpy.where(inside, z[src_i, src_j] + drop, numpy.inf)
                numpy.minimum(out[dst_i, dst_j], cand, out=out[dst_i, dst_j])
    return out


def _rest_mask(z, xs, ys, reference_diameter: float):
    """Boolean mask of grid nodes a ball-end reference tool of
    `reference_diameter` could not machine down to the CL surface `z`.

    The reference tool's drop-cutter surface over `z` (dilation) is
    swept back down by the same ball (erosion); the result is the
    morphological closing of `z` with the reference ball: the lowest
    surface that tool can actually leave. Nodes where the closing stays
    above `z` carry rest material.
    """
    radius = float(reference_diameter) / 2.0
    cl_ref = _morph_drop(z, xs, ys, radius, dilate=True)
    machined = _morph_drop(cl_ref, xs, ys, radius, dilate=False)
    return machined > z + 1e-6


def _chain_segments(segments):
    """Chain (key_a, key_b) segments into ordered key paths.

    Open chains are walked from their degree-1 endpoints first; whatever
    remains forms closed loops, which are walked from any node back to
    the start.
    """
    adj = {}
    for a, b in segments:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    def seg_id(a, b):
        return (a, b) if a <= b else (b, a)

    used = set()
    chains = []
    endpoints = [k for k, v in adj.items() if len(v) == 1]
    for start in endpoints + list(adj.keys()):
        for nxt in adj[start]:
            if seg_id(start, nxt) in used:
                continue
            chain = [start, nxt]
            used.add(seg_id(start, nxt))
            cur = nxt
            while True:
                ext = None
                for cand in adj[cur]:
                    if seg_id(cur, cand) not in used:
                        ext = cand
                        break
                if ext is None:
                    break
                used.add(seg_id(cur, ext))
                chain.append(ext)
                cur = ext
            chains.append(chain)
    return chains


def _contour_polylines(z, xs, ys, level: float, cell_mask):
    """Extract iso-contour polylines of `z` at `level`, restricted to
    cells where `cell_mask` is True. Returns a list of [(x, y)] paths."""
    span = float(z.max() - z.min())
    eps = 1e-9 * max(1.0, abs(level), span)
    z_adj = numpy.where(numpy.abs(z - level) < eps, level + eps, z)
    inside = z_adj > level
    case = (
        inside[:-1, :-1].astype(int)
        + inside[1:, :-1].astype(int) * 2
        + inside[1:, 1:].astype(int) * 4
        + inside[:-1, 1:].astype(int) * 8
    )
    crossing = (case > 0) & (case < 15) & cell_mask
    cells = numpy.argwhere(crossing)
    if cells.size == 0:
        return []

    pts = {}

    def edge_key(name, i, j):
        if name == "b":
            return ("x", i, j)
        if name == "t":
            return ("x", i, j + 1)
        if name == "l":
            return ("y", i, j)
        return ("y", i + 1, j)

    def edge_point(key):
        if key in pts:
            return
        kind, i, j = key
        if kind == "x":
            z0 = z_adj[i, j]
            z1 = z_adj[i + 1, j]
            t = (level - z0) / (z1 - z0)
            t = min(max(t, 0.0), 1.0)
            pts[key] = (float(xs[i] + t * (xs[i + 1] - xs[i])), float(ys[j]))
        else:
            z0 = z_adj[i, j]
            z1 = z_adj[i, j + 1]
            t = (level - z0) / (z1 - z0)
            t = min(max(t, 0.0), 1.0)
            pts[key] = (float(xs[i]), float(ys[j] + t * (ys[j + 1] - ys[j])))

    segments = []
    for cell in cells:
        i = int(cell[0])
        j = int(cell[1])
        c = int(case[i, j])
        if c in (5, 10):
            center = (
                z_adj[i, j] + z_adj[i + 1, j] + z_adj[i, j + 1] + z_adj[i + 1, j + 1]
            ) / 4.0
            center_inside = center > level
            if c == 5:
                pairs = (
                    (("b", "r"), ("t", "l"))
                    if center_inside
                    else (("b", "l"), ("t", "r"))
                )
            else:
                pairs = (
                    (("b", "l"), ("t", "r"))
                    if center_inside
                    else (("b", "r"), ("t", "l"))
                )
        else:
            pairs = _CASE_SEGMENTS[c]
        for a, b in pairs:
            ka = edge_key(a, i, j)
            kb = edge_key(b, i, j)
            edge_point(ka)
            edge_point(kb)
            if ka != kb:
                segments.append((ka, kb))

    polylines = []
    for chain in _chain_segments(segments):
        poly = []
        for key in chain:
            p = pts[key]
            if (
                poly
                and abs(p[0] - poly[-1][0]) < 1e-9
                and abs(p[1] - poly[-1][1]) < 1e-9
            ):
                continue
            poly.append(p)
        if len(poly) >= 2:
            polylines.append(poly)
    return polylines


def _shallow_passes(z, xs, ys, mask, stepover: float):
    """Build surface-following parallel passes along X, stepping over in
    Y by `stepover`, restricted to grid columns where `mask` is True.
    Returns a list of [(x, y, z)] passes in ascending-y order."""
    passes = []
    y_positions = []
    y = float(ys[0])
    y_end = float(ys[-1])
    while y < y_end - 1e-9:
        y_positions.append(y)
        y += stepover
    y_positions.append(y_end)

    n_x = xs.size
    for y0 in y_positions:
        j = int(numpy.searchsorted(ys, y0, side="right") - 1)
        j = min(max(j, 0), ys.size - 2)
        t = (y0 - ys[j]) / (ys[j + 1] - ys[j])
        t = min(max(t, 0.0), 1.0)
        z_row = z[:, j] * (1.0 - t) + z[:, j + 1] * t
        j_near = j if t < 0.5 else j + 1
        included = mask[:, j_near]
        run = []
        for i in range(n_x):
            if included[i]:
                run.append((float(xs[i]), float(y0), float(z_row[i])))
            else:
                if len(run) >= 2:
                    passes.append(run)
                run = []
        if len(run) >= 2:
            passes.append(run)
    return passes


def _emit_commands(passes, safe_height: float, horiz_feed: float, vert_feed: float):
    """Link the passes with rapids at safe height and emit commands.

    Every pass is entered with: retract to safe height, rapid traverse
    at safe height above the pass start, feed plunge to the start point,
    then cutting feed moves through the pass. A final retract to safe
    height closes the program.
    """
    commands = []
    for pts in passes:
        x0, y0, z0 = pts[0]
        commands.append(Path.Command("G0", {"Z": safe_height}))
        commands.append(Path.Command("G0", {"X": x0, "Y": y0, "Z": safe_height}))
        commands.append(Path.Command("G1", {"X": x0, "Y": y0, "Z": z0, "F": vert_feed}))
        for x, y, zz in pts[1:]:
            commands.append(
                Path.Command("G1", {"X": x, "Y": y, "Z": zz, "F": horiz_feed})
            )
    if commands:
        commands.append(Path.Command("G0", {"Z": safe_height}))
    return commands


def generate(
    heightfield,
    xs,
    ys,
    slope_threshold: float,
    stepdown: float,
    stepover: float,
    safe_height: float,
    horiz_feed: float,
    vert_feed: float,
    boundary_overlap: float = 0.0,
    direction: str = "Climb",
    rest_reference_diameter: float | None = None,
) -> list:
    """Build a combined steep/shallow finishing toolpath from a CL heightfield.

    Parameters
    ----------
    heightfield : 2-D array-like, shape (len(xs), len(ys))
        Cutter-location heights: heightfield[i][j] is the lowest
        gouge-free Z for the tool control point at (xs[i], ys[j]).
        NaN entries (no surface contact) are treated as the minimum
        finite height.
    xs, ys : 1-D sequences of floats
        Grid coordinates, strictly increasing, at least 2 points each.
    slope_threshold : float
        Classification angle in degrees, 0..90. Grid nodes whose local
        surface slope is at or above the threshold are steep and receive
        constant-Z contour passes; the rest are shallow and receive
        surface-following parallel passes. 0 classifies everything as
        steep (pure waterline); 90 classifies everything as shallow
        (pure surface-following).
    stepdown : float
        Vertical spacing of the constant-Z contour levels; must be > 0.
    stepover : float
        Lateral (Y) spacing of the shallow parallel passes; must be > 0.
    safe_height : float
        Absolute Z for linking rapids; must be at or above the
        heightfield maximum.
    horiz_feed, vert_feed : float
        Cutting and plunge feed rates; must be > 0.
    boundary_overlap : float
        Distance the shallow passes extend past the classification
        boundary into the steep region, so the two strategies overlap
        instead of leaving an unmachined stripe. Must be >= 0.
    direction : str
        "Climb" traverses passes in canonical order (shallow passes run
        +X); "Conventional" reverses the traversal direction of every
        pass.
    rest_reference_diameter : float or None
        Diameter of the previous (larger, ball-end) tool for rest
        machining, or None to machine the whole surface. When set, both
        strategies are restricted to the nodes that tool could not
        machine down to the CL surface, and the restriction region is
        grown by ``boundary_overlap`` so rest passes blend into the
        already-machined surround. Must be positive.

    Returns
    -------
    list of Path.Command
        G0 linking rapids (all at safe_height) and G1 cutting moves.
        Steep constant-Z passes are emitted first, top-down; shallow
        passes follow in ascending-Y order. Every cutting position lies
        on the bilinearly interpolated CL surface.
    """
    for pname, value in (
        ("slope_threshold", slope_threshold),
        ("stepdown", stepdown),
        ("stepover", stepover),
        ("safe_height", safe_height),
        ("horiz_feed", horiz_feed),
        ("vert_feed", vert_feed),
        ("boundary_overlap", boundary_overlap),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("%s must be a number" % pname)

    if not isinstance(direction, str):
        raise TypeError("direction must be a string")
    if direction not in ("Climb", "Conventional"):
        raise ValueError("Invalid value for parameter 'direction'")

    if rest_reference_diameter is not None:
        if isinstance(rest_reference_diameter, bool) or not isinstance(
            rest_reference_diameter, (int, float)
        ):
            raise TypeError("rest_reference_diameter must be a number or None")
        if rest_reference_diameter <= 0.0:
            raise ValueError("rest_reference_diameter must be positive")

    try:
        z = numpy.asarray(heightfield, dtype=float)
        xs_arr = numpy.asarray(xs, dtype=float)
        ys_arr = numpy.asarray(ys, dtype=float)
    except (TypeError, ValueError) as exc:
        raise TypeError("heightfield, xs, and ys must be numeric array-likes") from exc

    if z.ndim != 2:
        raise ValueError("heightfield must be 2-D")
    if xs_arr.ndim != 1 or ys_arr.ndim != 1:
        raise ValueError("xs and ys must be 1-D")
    if xs_arr.size < 2 or ys_arr.size < 2:
        raise ValueError("xs and ys need at least 2 points each")
    if z.shape != (xs_arr.size, ys_arr.size):
        raise ValueError(
            "heightfield shape %r does not match (len(xs), len(ys)) = %r"
            % (z.shape, (xs_arr.size, ys_arr.size))
        )
    if numpy.any(numpy.diff(xs_arr) <= 0.0):
        raise ValueError("xs must be strictly increasing")
    if numpy.any(numpy.diff(ys_arr) <= 0.0):
        raise ValueError("ys must be strictly increasing")

    if not 0.0 <= float(slope_threshold) <= 90.0:
        raise ValueError("slope_threshold must be between 0 and 90 degrees")
    if stepdown <= 0.0:
        raise ValueError("stepdown must be positive")
    if stepover <= 0.0:
        raise ValueError("stepover must be positive")
    if boundary_overlap < 0.0:
        raise ValueError("boundary_overlap must not be negative")
    if horiz_feed <= 0.0 or vert_feed <= 0.0:
        raise ValueError("feed rates must be positive")

    finite = numpy.isfinite(z)
    if not finite.any():
        raise ValueError("heightfield has no finite values")
    if not finite.all():
        z = numpy.where(finite, z, z[finite].min())

    z_min = float(z.min())
    z_max = float(z.max())
    if float(safe_height) < z_max - 1e-9:
        raise ValueError("safe_height must be at or above the heightfield maximum")

    # Slope classification: angle of the surface normal from vertical.
    gx, gy = numpy.gradient(z, xs_arr, ys_arr)
    slope_deg = numpy.degrees(numpy.arctan(numpy.hypot(gx, gy)))
    steep = slope_deg >= float(slope_threshold)

    min_step = float(min(numpy.diff(xs_arr).min(), numpy.diff(ys_arr).min()))
    overlap_iterations = 0
    if boundary_overlap > 0.0:
        overlap_iterations = int(math.ceil(float(boundary_overlap) / min_step - 1e-9))

    # Rest machining: restrict both strategies to the region a ball-end
    # reference tool of rest_reference_diameter could not reach, grown
    # by boundary_overlap to blend into the machined surround.
    rest = None
    if rest_reference_diameter is not None:
        rest = _rest_mask(z, xs_arr, ys_arr, float(rest_reference_diameter))
        if overlap_iterations and rest.any():
            rest = _dilate(rest, overlap_iterations)

    passes = []

    # Steep regions: constant-Z contour passes, top-down. A grid cell
    # participates when any of its corners is steep so contours reach
    # all the way to the classification boundary.
    cell_steep = steep[:-1, :-1] | steep[1:, :-1] | steep[:-1, 1:] | steep[1:, 1:]
    if rest is not None:
        cell_steep = cell_steep & (
            rest[:-1, :-1] | rest[1:, :-1] | rest[:-1, 1:] | rest[1:, 1:]
        )
    if cell_steep.any():
        level = z_max - float(stepdown)
        while level > z_min + 1e-9:
            for poly in _contour_polylines(z, xs_arr, ys_arr, level, cell_steep):
                passes.append([(px, py, level) for px, py in poly])
            level -= float(stepdown)

    # Shallow regions: surface-following parallel passes, optionally
    # dilated into the steep region by boundary_overlap.
    shallow = ~steep
    if overlap_iterations and shallow.any():
        shallow = _dilate(shallow, overlap_iterations)
    if rest is not None:
        shallow = shallow & rest
    if shallow.any():
        passes.extend(_shallow_passes(z, xs_arr, ys_arr, shallow, float(stepover)))

    if direction == "Conventional":
        passes = [list(reversed(p)) for p in passes]

    return _emit_commands(
        passes, float(safe_height), float(horiz_feed), float(vert_feed)
    )
