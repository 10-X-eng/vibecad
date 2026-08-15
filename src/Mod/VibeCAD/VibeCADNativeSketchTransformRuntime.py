# SPDX-License-Identifier: LGPL-2.1-or-later

"""Runtime catalog for exact Sketch transformation operations."""

from VibeCADNativeSketchTranslate import (
    create_translate,
    preflight_translate,
    prepare_translate,
    verify_translate,
)
from VibeCADNativeSketchRotate import (
    create_rotate,
    preflight_rotate,
    prepare_rotate,
    verify_rotate,
)
from VibeCADNativeSketchScale import (
    create_scale,
    preflight_scale,
    prepare_scale,
    verify_scale,
)
from VibeCADNativeSketchOffset import (
    create_offset,
    preflight_offset,
    prepare_offset,
    verify_offset,
)
from VibeCADNativeSketchSymmetry import (
    create_symmetry,
    preflight_symmetry,
    prepare_symmetry,
    verify_symmetry,
)
from VibeCADNativeSketchAxisAlignment import (
    create_axis_alignment,
    preflight_axis_alignment,
    prepare_axis_alignment,
    verify_axis_alignment,
)
from VibeCADNativeSketchNURBSConversion import (
    create_nurbs_conversion,
    preflight_nurbs_conversion,
    prepare_nurbs_conversion,
    verify_nurbs_conversion,
)
from VibeCADNativeSketchBSplineDegree import (
    create_bspline_degree,
    preflight_bspline_degree,
    prepare_bspline_degree,
    verify_bspline_degree,
)
from VibeCADNativeSketchBSplineDegreeDecrease import (
    create_bspline_degree_decrease,
    preflight_bspline_degree_decrease,
    prepare_bspline_degree_decrease,
    verify_bspline_degree_decrease,
)
from VibeCADNativeSketchBSplineKnotMultiplicityIncrease import (
    create_bspline_knot_multiplicity_increase,
    preflight_bspline_knot_multiplicity_increase,
    prepare_bspline_knot_multiplicity_increase,
    verify_bspline_knot_multiplicity_increase,
)
from VibeCADNativeSketchBSplineKnotMultiplicityDecrease import (
    create_bspline_knot_multiplicity_decrease,
    preflight_bspline_knot_multiplicity_decrease,
    prepare_bspline_knot_multiplicity_decrease,
    verify_bspline_knot_multiplicity_decrease,
)
from VibeCADNativeSketchBSplineKnotInsertion import (
    create_bspline_knot_insertion,
    preflight_bspline_knot_insertion,
    prepare_bspline_knot_insertion,
    verify_bspline_knot_insertion,
)
from VibeCADNativeSketchJoin import (
    create_sketch_join,
    preflight_sketch_join,
    prepare_sketch_join,
    verify_sketch_join,
)


TRANSFORM_OUTER_FIELDS = {
    "translate": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "first_translation_mm",
            "copy_count",
            "second_translation_mm",
            "row_count",
            "constraint_mode",
        }
    ),
    "rotate": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "center_mm",
            "total_angle",
            "copy_count",
            "constraint_mode",
        }
    ),
    "scale": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "center_mm",
            "scale_factor",
            "keep_originals",
        }
    ),
    "offset": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "offset_distance",
            "join_type",
            "source_mode",
        }
    ),
    "symmetry": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
            "reference",
            "source_mode",
        }
    ),
    "remove_axis_alignment": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
        }
    ),
    "convert_to_nurbs": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
        }
    ),
    "increase_bspline_degree": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_indices",
        }
    ),
    "decrease_bspline_degree": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_index",
            "maximum_deviation_mm",
        }
    ),
    "increase_bspline_knot_multiplicity": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_index",
            "knot_index",
        }
    ),
    "decrease_bspline_knot_multiplicity": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_index",
            "knot_index",
            "maximum_deviation_mm",
        }
    ),
    "insert_bspline_knot": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "geometry_index",
            "parameter",
        }
    ),
    "join_curves": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "first",
            "second",
        }
    ),
}

TRANSFORM_OPERATIONS = {
    "translate": (
        prepare_translate,
        preflight_translate,
        create_translate,
        verify_translate,
        "Translate Native Sketch Geometry",
    ),
    "rotate": (
        prepare_rotate,
        preflight_rotate,
        create_rotate,
        verify_rotate,
        "Rotate Native Sketch Geometry",
    ),
    "scale": (
        prepare_scale,
        preflight_scale,
        create_scale,
        verify_scale,
        "Scale Native Sketch Geometry",
    ),
    "offset": (
        prepare_offset,
        preflight_offset,
        create_offset,
        verify_offset,
        "Offset Native Sketch Geometry",
    ),
    "symmetry": (
        prepare_symmetry,
        preflight_symmetry,
        create_symmetry,
        verify_symmetry,
        "Mirror Native Sketch Geometry",
    ),
    "remove_axis_alignment": (
        prepare_axis_alignment,
        preflight_axis_alignment,
        create_axis_alignment,
        verify_axis_alignment,
        "Remove Sketch Axes Alignment",
    ),
    "convert_to_nurbs": (
        prepare_nurbs_conversion,
        preflight_nurbs_conversion,
        create_nurbs_conversion,
        verify_nurbs_conversion,
        "Convert Sketch Geometry to B-Splines",
    ),
    "increase_bspline_degree": (
        prepare_bspline_degree,
        preflight_bspline_degree,
        create_bspline_degree,
        verify_bspline_degree,
        "Increase Sketch B-Spline Degree",
    ),
    "decrease_bspline_degree": (
        prepare_bspline_degree_decrease,
        preflight_bspline_degree_decrease,
        create_bspline_degree_decrease,
        verify_bspline_degree_decrease,
        "Decrease Sketch B-Spline Degree",
    ),
    "increase_bspline_knot_multiplicity": (
        prepare_bspline_knot_multiplicity_increase,
        preflight_bspline_knot_multiplicity_increase,
        create_bspline_knot_multiplicity_increase,
        verify_bspline_knot_multiplicity_increase,
        "Increase Sketch B-Spline Knot Multiplicity",
    ),
    "decrease_bspline_knot_multiplicity": (
        prepare_bspline_knot_multiplicity_decrease,
        preflight_bspline_knot_multiplicity_decrease,
        create_bspline_knot_multiplicity_decrease,
        verify_bspline_knot_multiplicity_decrease,
        "Decrease Sketch B-Spline Knot Multiplicity",
    ),
    "insert_bspline_knot": (
        prepare_bspline_knot_insertion,
        preflight_bspline_knot_insertion,
        create_bspline_knot_insertion,
        verify_bspline_knot_insertion,
        "Insert Sketch B-Spline Knot",
    ),
    "join_curves": (
        prepare_sketch_join,
        preflight_sketch_join,
        create_sketch_join,
        verify_sketch_join,
        "Join Native Sketch Curves",
    ),
}
