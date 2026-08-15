# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for contextual Sketch constraints."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeSketchTaskMutation import (
    run_active_sketch_mutation as run_immediate_mutation,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchCoincident import (
    create_sketch_coincident,
    preflight_sketch_coincident,
    prepare_sketch_coincident,
    verify_sketch_coincident,
)
from VibeCADNativeSketchAngle import (
    create_sketch_angle,
    preflight_sketch_angle,
    prepare_sketch_angle,
    verify_sketch_angle,
)
from VibeCADNativeSketchLock import (
    create_sketch_lock,
    preflight_sketch_lock,
    prepare_sketch_lock,
    verify_sketch_lock,
)
from VibeCADNativeSketchHorizontalVertical import (
    create_sketch_horizontal_vertical,
    preflight_sketch_horizontal_vertical,
    prepare_sketch_horizontal_vertical,
    verify_sketch_horizontal_vertical,
)
from VibeCADNativeSketchHorizontal import (
    create_sketch_horizontal,
    preflight_sketch_horizontal,
    prepare_sketch_horizontal,
    verify_sketch_horizontal,
)
from VibeCADNativeSketchVertical import (
    create_sketch_vertical,
    preflight_sketch_vertical,
    prepare_sketch_vertical,
    verify_sketch_vertical,
)
from VibeCADNativeSketchParallel import (
    create_sketch_parallel,
    preflight_sketch_parallel,
    prepare_sketch_parallel,
    verify_sketch_parallel,
)
from VibeCADNativeSketchPerpendicular import (
    create_sketch_perpendicular,
    preflight_sketch_perpendicular,
    prepare_sketch_perpendicular,
    verify_sketch_perpendicular,
)
from VibeCADNativeSketchTangent import (
    create_sketch_tangent,
    preflight_sketch_tangent,
    prepare_sketch_tangent,
    verify_sketch_tangent,
)
from VibeCADNativeSketchEqual import (
    create_sketch_equal,
    preflight_sketch_equal,
    prepare_sketch_equal,
    verify_sketch_equal,
)
from VibeCADNativeSketchSymmetric import (
    create_sketch_symmetric,
    preflight_sketch_symmetric,
    prepare_sketch_symmetric,
    verify_sketch_symmetric,
)
from VibeCADNativeSketchBlock import (
    create_sketch_block,
    preflight_sketch_block,
    prepare_sketch_block,
    verify_sketch_block,
)
from VibeCADNativeSketchGroup import (
    create_sketch_group,
    preflight_sketch_group,
    prepare_sketch_group,
    verify_sketch_group,
)
from VibeCADNativeSketchDriving import (
    create_sketch_driving,
    preflight_sketch_driving,
    prepare_sketch_driving,
    verify_sketch_driving,
)
from VibeCADNativeSketchActive import (
    create_sketch_active,
    preflight_sketch_active,
    prepare_sketch_active,
    verify_sketch_active,
)
from VibeCADNativeSketchDimension import (
    create_sketch_dimension,
    preflight_sketch_dimension,
    prepare_sketch_dimension,
    verify_sketch_dimension,
)
from VibeCADNativeSketchDiameter import (
    create_sketch_diameter,
    preflight_sketch_diameter,
    prepare_sketch_diameter,
    verify_sketch_diameter,
)
from VibeCADNativeSketchDistance import (
    create_sketch_distance,
    preflight_sketch_distance,
    prepare_sketch_distance,
    verify_sketch_distance,
)
from VibeCADNativeSketchDistanceX import (
    create_sketch_horizontal_distance,
    preflight_sketch_horizontal_distance,
    prepare_sketch_horizontal_distance,
    verify_sketch_horizontal_distance,
)
from VibeCADNativeSketchDistanceY import (
    create_sketch_vertical_distance,
    preflight_sketch_vertical_distance,
    prepare_sketch_vertical_distance,
    verify_sketch_vertical_distance,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchRadiam import (
    create_sketch_radiam,
    preflight_sketch_radiam,
    prepare_sketch_radiam,
    verify_sketch_radiam,
)
from VibeCADNativeSketchRadius import (
    create_sketch_radius,
    preflight_sketch_radius,
    prepare_sketch_radius,
    verify_sketch_radius,
)
from VibeCADNativeSketchVirtualSpace import (
    PreparedSketchVirtualSpaceConstraints,
    create_sketch_virtual_space_constraints,
    preflight_sketch_virtual_space,
    prepare_sketch_virtual_space,
    set_sketch_virtual_space_view,
    verify_sketch_virtual_space_constraints,
)
from VibeCADNativeState import NativeCallTicket


_OUTER_FIELDS = {
    "infer_dimension": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "expected_inference",
            "dimension",
            "driving",
        }
    ),
    "constrain_distance_x": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "dimension",
            "driving",
        }
    ),
    "constrain_distance_y": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "dimension",
            "driving",
        }
    ),
    "constrain_distance": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "dimension",
            "driving",
        }
    ),
    "constrain_radius_diameter": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "expected_constraint",
            "dimension",
            "driving",
        }
    ),
    "constrain_radius": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "dimension",
            "driving",
        }
    ),
    "constrain_diameter": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "dimension",
            "driving",
        }
    ),
    "constrain_angle": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "expected_form",
            "dimension",
            "driving",
        }
    ),
    "constrain_lock": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
            "driving",
        }
    ),
    "constrain_coincident": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
    "constrain_horizontal_vertical": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
            "expected_inference",
        }
    ),
    "constrain_horizontal": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        }
    ),
    "constrain_vertical": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        }
    ),
    "constrain_parallel": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        }
    ),
    "constrain_perpendicular": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
    "constrain_tangent": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
    "constrain_equal": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        }
    ),
    "constrain_symmetric": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
    "constrain_block": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        }
    ),
    "constrain_group": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        }
    ),
    "toggle_driving_reference": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "targets",
        }
    ),
    "toggle_active_inactive": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "targets",
        }
    ),
    "set_virtual_space": frozenset(
        {
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "target",
        }
    ),
}


class NativeSketchConstraintRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_constraint(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _OUTER_FIELDS)
        if operation == "infer_dimension":
            spec = prepare_sketch_dimension(self._context.document_uid, values)
            prepared = preflight_sketch_dimension(self._context, spec)
            transaction_name = "Create Native Sketch Dimension"
            mutate = create_sketch_dimension
            verify = verify_sketch_dimension
        elif operation == "constrain_distance_x":
            spec = prepare_sketch_horizontal_distance(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_horizontal_distance(self._context, spec)
            transaction_name = "Create Native Sketch Horizontal Distance"
            mutate = create_sketch_horizontal_distance
            verify = verify_sketch_horizontal_distance
        elif operation == "constrain_distance_y":
            spec = prepare_sketch_vertical_distance(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_vertical_distance(self._context, spec)
            transaction_name = "Create Native Sketch Vertical Distance"
            mutate = create_sketch_vertical_distance
            verify = verify_sketch_vertical_distance
        elif operation == "constrain_distance":
            spec = prepare_sketch_distance(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_distance(self._context, spec)
            transaction_name = "Create Native Sketch Distance"
            mutate = create_sketch_distance
            verify = verify_sketch_distance
        elif operation == "constrain_radius_diameter":
            spec = prepare_sketch_radiam(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_radiam(self._context, spec)
            transaction_name = "Create Native Sketch Radius/Diameter"
            mutate = create_sketch_radiam
            verify = verify_sketch_radiam
        elif operation == "constrain_radius":
            spec = prepare_sketch_radius(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_radius(self._context, spec)
            transaction_name = "Create Native Sketch Radius"
            mutate = create_sketch_radius
            verify = verify_sketch_radius
        elif operation == "constrain_diameter":
            spec = prepare_sketch_diameter(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_diameter(self._context, spec)
            transaction_name = "Create Native Sketch Diameter"
            mutate = create_sketch_diameter
            verify = verify_sketch_diameter
        elif operation == "constrain_angle":
            spec = prepare_sketch_angle(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_angle(self._context, spec)
            transaction_name = "Create Native Sketch Angle"
            mutate = create_sketch_angle
            verify = verify_sketch_angle
        elif operation == "constrain_lock":
            spec = prepare_sketch_lock(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_lock(self._context, spec)
            transaction_name = "Create Native Sketch Lock"
            mutate = create_sketch_lock
            verify = verify_sketch_lock
        elif operation == "constrain_coincident":
            spec = prepare_sketch_coincident(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_coincident(self._context, spec)
            transaction_name = "Create Native Sketch Coincident"
            mutate = create_sketch_coincident
            verify = verify_sketch_coincident
        elif operation == "constrain_horizontal_vertical":
            spec = prepare_sketch_horizontal_vertical(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_horizontal_vertical(self._context, spec)
            transaction_name = "Create Native Sketch Horizontal/Vertical"
            mutate = create_sketch_horizontal_vertical
            verify = verify_sketch_horizontal_vertical
        elif operation == "constrain_horizontal":
            spec = prepare_sketch_horizontal(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_horizontal(self._context, spec)
            transaction_name = "Create Native Sketch Horizontal"
            mutate = create_sketch_horizontal
            verify = verify_sketch_horizontal
        elif operation == "constrain_vertical":
            spec = prepare_sketch_vertical(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_vertical(self._context, spec)
            transaction_name = "Create Native Sketch Vertical"
            mutate = create_sketch_vertical
            verify = verify_sketch_vertical
        elif operation == "constrain_parallel":
            spec = prepare_sketch_parallel(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_parallel(self._context, spec)
            transaction_name = "Create Native Sketch Parallel"
            mutate = create_sketch_parallel
            verify = verify_sketch_parallel
        elif operation == "constrain_perpendicular":
            spec = prepare_sketch_perpendicular(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_perpendicular(self._context, spec)
            transaction_name = "Create Native Sketch Perpendicular"
            mutate = create_sketch_perpendicular
            verify = verify_sketch_perpendicular
        elif operation == "constrain_tangent":
            spec = prepare_sketch_tangent(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_tangent(self._context, spec)
            transaction_name = "Create Native Sketch Tangent"
            mutate = create_sketch_tangent
            verify = verify_sketch_tangent
        elif operation == "constrain_equal":
            spec = prepare_sketch_equal(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_equal(self._context, spec)
            transaction_name = "Create Native Sketch Equal"
            mutate = create_sketch_equal
            verify = verify_sketch_equal
        elif operation == "constrain_symmetric":
            spec = prepare_sketch_symmetric(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_symmetric(self._context, spec)
            transaction_name = "Create Native Sketch Symmetric"
            mutate = create_sketch_symmetric
            verify = verify_sketch_symmetric
        elif operation == "constrain_block":
            spec = prepare_sketch_block(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_block(self._context, spec)
            transaction_name = "Create Native Sketch Block"
            mutate = create_sketch_block
            verify = verify_sketch_block
        elif operation == "constrain_group":
            spec = prepare_sketch_group(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_group(self._context, spec)
            transaction_name = "Create Native Sketch Constraint Group"
            mutate = create_sketch_group
            verify = verify_sketch_group
        elif operation == "toggle_driving_reference":
            spec = prepare_sketch_driving(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_driving(self._context, spec)
            transaction_name = "Toggle Native Sketch Driving/Reference"
            mutate = create_sketch_driving
            verify = verify_sketch_driving
        elif operation == "toggle_active_inactive":
            spec = prepare_sketch_active(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_active(self._context, spec)
            transaction_name = "Toggle Native Sketch Active/Inactive"
            mutate = create_sketch_active
            verify = verify_sketch_active
        elif operation == "set_virtual_space":
            spec = prepare_sketch_virtual_space(
                self._context.document_uid,
                values,
            )
            prepared = preflight_sketch_virtual_space(self._context, spec)
            if not isinstance(prepared, PreparedSketchVirtualSpaceConstraints):
                return set_sketch_virtual_space_view(self._context, prepared)
            transaction_name = "Set Native Sketch Virtual Space"
            mutate = create_sketch_virtual_space_constraints
            verify = verify_sketch_virtual_space_constraints
        else:
            raise NativeSketchError("That Sketch constraint operation is unavailable.")
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name=transaction_name,
            mutate=lambda document: mutate(document, prepared),
            verify=verify,
        )
