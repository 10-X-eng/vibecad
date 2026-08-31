# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact CAM machining operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation as _run_immediate_mutation
from VibeCADNativeManufactureAdaptive import (
    AdaptiveCreateSpec,
    AdaptiveDefaultsSpec,
    create_adaptive,
    create_adaptive_defaults,
    preflight_adaptive_create,
    preflight_adaptive_defaults,
    verify_created_adaptive,
    verify_created_adaptive_defaults,
)
from VibeCADNativeManufactureArray import (
    ArrayCreateSpec,
    create_array,
    preflight_array_create,
    verify_created_array,
)
from VibeCADNativeManufactureDrilling import (
    DrillingDefaultsSpec,
    create_drilling_defaults,
    preflight_drilling_defaults,
    verify_created_drilling_defaults,
)
from VibeCADNativeManufactureDeburr import (
    DeburrCreateSpec,
    create_deburr,
    preflight_deburr_create,
    verify_created_deburr,
)
from VibeCADNativeManufactureEngrave import (
    EngraveCreateSpec,
    create_engrave,
    preflight_engrave_create,
    verify_created_engrave,
)
from VibeCADNativeManufactureHelix import (
    HelixCreateSpec,
    create_helix,
    preflight_helix_create,
    verify_created_helix,
)
from VibeCADNativeManufactureMillFacing import (
    MillFacingDefaultsSpec,
    create_mill_facing_defaults,
    preflight_mill_facing_defaults,
    verify_created_mill_facing_defaults,
)
from VibeCADNativeManufactureProfile import (
    ProfileDefaultsSpec,
    create_profile_defaults,
    preflight_profile_defaults,
    verify_created_profile_defaults,
)
from VibeCADNativeManufactureSlot import (
    SlotCreateSpec,
    create_slot,
    preflight_slot_create,
    verify_created_slot,
)
from VibeCADNativeManufactureStartPoint import (
    StartPointSpec,
    preflight_start_point,
    set_start_point,
    verify_start_point,
)
from VibeCADNativeManufactureSimpleCopy import (
    SimpleCopyCreateSpec,
    create_simple_copy,
    preflight_simple_copy_create,
    verify_created_simple_copy,
)
from VibeCADNativeManufactureThreadMilling import (
    ThreadMillingCreateSpec,
    create_thread_milling,
    preflight_thread_milling_create,
    verify_created_thread_milling,
)
from VibeCADNativeManufactureVCarve import (
    VCarveCreateSpec,
    create_v_carve,
    preflight_v_carve_create,
    verify_created_v_carve,
)
from VibeCADNativeManufacturePocket3D import (
    Pocket3DCreateSpec,
    create_pocket_3d,
    preflight_pocket_3d_create,
    verify_created_pocket_3d,
)
from VibeCADNativeManufacturePocketShape import (
    PocketShapeDefaultsSpec,
    create_pocket_shape_defaults,
    preflight_pocket_shape_defaults,
    verify_created_pocket_shape_defaults,
)
from VibeCADNativeManufactureSurface import (
    SurfaceCreateSpec,
    create_surface,
    preflight_surface_create,
    verify_created_surface,
)
from VibeCADNativeManufactureWaterline import (
    WaterlineCreateSpec,
    create_waterline,
    preflight_waterline_create,
    verify_created_waterline,
)
from VibeCADNativeManufactureRotarySurface import (
    RotarySurfaceCreateSpec,
    create_rotary_surface,
    preflight_rotary_surface_create,
    verify_created_rotary_surface,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_PROFILE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "cut_side",
        "coolant",
    }
)
_POCKET_SHAPE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "coolant",
    }
)
_POCKET_3D_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "pocket",
        "depths",
        "heights",
        "coolant",
    }
)
_SURFACE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "surface",
        "depths",
        "heights",
        "coolant",
    }
)
_WATERLINE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "waterline",
        "depths",
        "heights",
        "coolant",
    }
)
_ROTARY_SURFACE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "rotary_surface",
        "heights",
        "coolant",
    }
)
_MILL_FACING_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "coolant",
    }
)
_HELIX_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "helix",
        "depths",
        "heights",
        "linking",
        "coolant",
    }
)
_ADAPTIVE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "adaptive",
        "helix_entry",
        "depths",
        "heights",
        "extensions",
        "coolant",
    }
)
_ADAPTIVE_DEFAULTS_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "coolant",
    }
)
_SLOT_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "slot",
        "depths",
        "heights",
        "coolant",
    }
)
_DRILLING_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "coolant",
    }
)
_THREAD_MILLING_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "targets",
        "thread",
        "depths",
        "heights",
        "linking",
        "coolant",
    }
)
_ENGRAVE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "engrave",
        "depths",
        "heights",
        "linking",
        "coolant",
    }
)
_DEBURR_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "deburr",
        "depths",
        "heights",
        "linking",
        "coolant",
    }
)
_V_CARVE_FIELDS = frozenset(
    {
        "label",
        "job",
        "tool_controller",
        "geometry",
        "v_carve",
        "depths",
        "heights",
        "coolant",
    }
)
_ARRAY_FIELDS = frozenset(
    {
        "label",
        "job",
        "base_operations",
        "pattern",
        "reverse_direction",
        "jitter",
    }
)
_SIMPLE_COPY_FIELDS = frozenset({"label", "job", "source_operations"})
_START_POINT_FIELDS = frozenset({"job", "target", "point_mm"})
_PATH_GENERATION_OPERATIONS = frozenset(
    {
        "profile",
        "pocket_shape",
        "pocket_3d",
        "surface",
        "waterline",
        "rotary_surface",
        "mill_facing",
        "helix",
        "adaptive",
        "adaptive_defaults",
        "slot",
        "drilling",
        "thread_milling",
        "engrave",
        "deburr",
        "v_carve",
    }
)


class NativeManufactureOperationRuntime:
    def __init__(
        self,
        context: NativeRuntimeContext,
        *,
        mutation_executor: Callable[..., Mapping[str, Any]] = _run_immediate_mutation,
    ) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        if not callable(mutation_executor):
            raise TypeError("mutation_executor must be callable")
        self._context = context
        self._mutation_executor = mutation_executor

    def mutate_operation(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "profile": _PROFILE_FIELDS,
                "pocket_shape": _POCKET_SHAPE_FIELDS,
                "pocket_3d": _POCKET_3D_FIELDS,
                "surface": _SURFACE_FIELDS,
                "waterline": _WATERLINE_FIELDS,
                "rotary_surface": _ROTARY_SURFACE_FIELDS,
                "mill_facing": _MILL_FACING_FIELDS,
                "helix": _HELIX_FIELDS,
                "adaptive": _ADAPTIVE_FIELDS,
                "adaptive_defaults": _ADAPTIVE_DEFAULTS_FIELDS,
                "slot": _SLOT_FIELDS,
                "drilling": _DRILLING_FIELDS,
                "thread_milling": _THREAD_MILLING_FIELDS,
                "engrave": _ENGRAVE_FIELDS,
                "deburr": _DEBURR_FIELDS,
                "v_carve": _V_CARVE_FIELDS,
                "array": _ARRAY_FIELDS,
                "simple_copy": _SIMPLE_COPY_FIELDS,
                "set_start_point": _START_POINT_FIELDS,
            },
            defaults={
                "profile": {"label": "Profile", "coolant": "none"},
                "pocket_shape": {"label": "Pocket Shape", "coolant": "none"},
                "mill_facing": {"label": "Mill Facing", "coolant": "none"},
                "adaptive_defaults": {"label": "Adaptive", "coolant": "none"},
                "drilling": {"label": "Drilling", "coolant": "none"},
            },
        )
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)

        def run_immediate_mutation(
            runtime_context: NativeRuntimeContext,
            **options: Any,
        ) -> Mapping[str, Any]:
            if (
                operation not in _PATH_GENERATION_OPERATIONS
                or self._mutation_executor is _run_immediate_mutation
            ):
                return _run_immediate_mutation(runtime_context, **options)
            return self._mutation_executor(
                runtime_context,
                request={"operation": operation, **values},
                **options,
            )
        if operation == "set_start_point":
            prepared = preflight_start_point(
                context.document,
                StartPointSpec(
                    job=values["job"],
                    target=values["target"],
                    point_mm=values["point_mm"],
                ),
            )
            transaction_name = "Set Native CAM Start Point"
            mutate = partial(set_start_point, prepared=prepared)
            verify = verify_start_point
        elif operation == "profile":
            prepared = preflight_profile_defaults(
                context.document,
                ProfileDefaultsSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=tuple(values["geometry"]),
                    cut_side=values["cut_side"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Profile"
            mutate = partial(create_profile_defaults, prepared=prepared)
            verify = verify_created_profile_defaults
        elif operation == "pocket_shape":
            prepared = preflight_pocket_shape_defaults(
                context.document,
                PocketShapeDefaultsSpec(
                    label=values.get("label", "Pocket Shape"),
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=tuple(values["geometry"]),
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Pocket Shape"
            mutate = partial(create_pocket_shape_defaults, prepared=prepared)
            verify = verify_created_pocket_shape_defaults
        elif operation == "pocket_3d":
            prepared = preflight_pocket_3d_create(
                context.document,
                Pocket3DCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    pocket=values["pocket"],
                    depths=values["depths"],
                    heights=values["heights"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM 3D Pocket"
            mutate = partial(create_pocket_3d, prepared=prepared)
            verify = verify_created_pocket_3d
        elif operation == "surface":
            prepared = preflight_surface_create(
                context.document,
                SurfaceCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    surface=values["surface"],
                    depths=values["depths"],
                    heights=values["heights"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Surface"
            mutate = partial(create_surface, prepared=prepared)
            verify = verify_created_surface
        elif operation == "waterline":
            prepared = preflight_waterline_create(
                context.document,
                WaterlineCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    waterline=values["waterline"],
                    depths=values["depths"],
                    heights=values["heights"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Waterline"
            mutate = partial(create_waterline, prepared=prepared)
            verify = verify_created_waterline
        elif operation == "rotary_surface":
            prepared = preflight_rotary_surface_create(
                context.document,
                RotarySurfaceCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    rotary_surface=values["rotary_surface"],
                    heights=values["heights"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Rotary Surface"
            mutate = partial(create_rotary_surface, prepared=prepared)
            verify = verify_created_rotary_surface
        elif operation == "mill_facing":
            prepared = preflight_mill_facing_defaults(
                context.document,
                MillFacingDefaultsSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Mill Facing"
            mutate = partial(create_mill_facing_defaults, prepared=prepared)
            verify = verify_created_mill_facing_defaults
        elif operation == "helix":
            prepared = preflight_helix_create(
                context.document,
                HelixCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    helix=values["helix"],
                    depths=values["depths"],
                    heights=values["heights"],
                    linking=values["linking"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Helix"
            mutate = partial(create_helix, prepared=prepared)
            verify = verify_created_helix
        elif operation == "adaptive":
            prepared = preflight_adaptive_create(
                context.document,
                AdaptiveCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    adaptive=values["adaptive"],
                    helix_entry=values["helix_entry"],
                    depths=values["depths"],
                    heights=values["heights"],
                    extensions=values["extensions"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Adaptive"
            mutate = partial(create_adaptive, prepared=prepared)
            verify = verify_created_adaptive
        elif operation == "adaptive_defaults":
            prepared = preflight_adaptive_defaults(
                context.document,
                AdaptiveDefaultsSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=tuple(values["geometry"]),
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Adaptive"
            mutate = partial(create_adaptive_defaults, prepared=prepared)
            verify = verify_created_adaptive_defaults
        elif operation == "slot":
            prepared = preflight_slot_create(
                context.document,
                SlotCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    slot=values["slot"],
                    depths=values["depths"],
                    heights=values["heights"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Slot"
            mutate = partial(create_slot, prepared=prepared)
            verify = verify_created_slot
        elif operation == "drilling":
            prepared = preflight_drilling_defaults(
                context.document,
                DrillingDefaultsSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=tuple(values["geometry"]),
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Drilling"
            mutate = partial(create_drilling_defaults, prepared=prepared)
            verify = verify_created_drilling_defaults
        elif operation == "thread_milling":
            prepared = preflight_thread_milling_create(
                context.document,
                ThreadMillingCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    targets=values["targets"],
                    thread=values["thread"],
                    depths=values["depths"],
                    heights=values["heights"],
                    linking=values["linking"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Thread Milling"
            mutate = partial(create_thread_milling, prepared=prepared)
            verify = verify_created_thread_milling
        elif operation == "engrave":
            prepared = preflight_engrave_create(
                context.document,
                EngraveCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    engrave=values["engrave"],
                    depths=values["depths"],
                    heights=values["heights"],
                    linking=values["linking"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Engrave"
            mutate = partial(create_engrave, prepared=prepared)
            verify = verify_created_engrave
        elif operation == "deburr":
            prepared = preflight_deburr_create(
                context.document,
                DeburrCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    deburr=values["deburr"],
                    depths=values["depths"],
                    heights=values["heights"],
                    linking=values["linking"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM Deburr"
            mutate = partial(create_deburr, prepared=prepared)
            verify = verify_created_deburr
        elif operation == "v_carve":
            prepared = preflight_v_carve_create(
                context.document,
                VCarveCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    tool_controller=values["tool_controller"],
                    geometry=values["geometry"],
                    v_carve=values["v_carve"],
                    depths=values["depths"],
                    heights=values["heights"],
                    coolant=values["coolant"],
                ),
            )
            transaction_name = "Create Native CAM V-carve"
            mutate = partial(create_v_carve, prepared=prepared)
            verify = verify_created_v_carve
        elif operation == "array":
            prepared = preflight_array_create(
                context.document,
                ArrayCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    base_operations=values["base_operations"],
                    pattern=values["pattern"],
                    reverse_direction=values["reverse_direction"],
                    jitter=values["jitter"],
                ),
            )
            transaction_name = "Create Native CAM Array"
            mutate = partial(create_array, prepared=prepared)
            verify = verify_created_array
        elif operation == "simple_copy":
            prepared = preflight_simple_copy_create(
                context.document,
                SimpleCopyCreateSpec(
                    label=values["label"],
                    job=values["job"],
                    source_operations=values["source_operations"],
                ),
            )
            transaction_name = "Create Native CAM Simple Copy"
            mutate = partial(create_simple_copy, prepared=prepared)
            verify = verify_created_simple_copy
        else:
            raise RuntimeError("The requested CAM machining operation is unavailable.")
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=transaction_name,
            mutate=mutate,
            verify=verify,
        )
