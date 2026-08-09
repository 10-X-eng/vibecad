# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit Native inventory for shipped context actions outside the ribbon."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeActionManifest import NativeActionClassification
from VibeCADRibbonSurface import SURFACE_IDS


class NativeContextManifestError(RuntimeError):
    """A context-action record is incomplete or internally inconsistent."""


_CONTEXT_SOURCES = frozenset(
    {
        "tree_context",
        "workbench_context",
        "drawing_canvas_context",
        "inspection_view_context",
        "menu",
    }
)
_TRANSACTION_BEHAVIORS = frozenset(
    {"document", "presentation", "output", "background_output", "human"}
)
_INSPECTION_SURFACES = (
    "model",
    "assemble",
    "mesh",
    "analyze",
    "manufacture",
    "drawing",
    "parameters",
    "sketch.setup",
)


def _classification(
    primary: str,
    *,
    interactive: bool = False,
) -> NativeActionClassification:
    if primary not in {"mutation", "view", "export", "human_only"}:
        raise NativeContextManifestError(
            f"Unsupported context-action classification {primary!r}."
        )
    return NativeActionClassification(
        read=False,
        mutation=primary == "mutation",
        view=primary == "view",
        export=primary == "export",
        interactive=interactive,
        parent_only=False,
        human_only=primary == "human_only",
    )


@dataclass(frozen=True, slots=True)
class NativeContextActionPlan:
    action_id: str
    surface_ids: tuple[str, ...]
    sources: tuple[str, ...]
    source_command_id: str | None
    classification: NativeActionClassification
    capability_family: str
    operation_variant: str | None
    exact_target_type: str
    transaction_behavior: str
    background_required: bool = False
    implementation_status: str = "planned"

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise NativeContextManifestError("Context action ID cannot be empty.")
        if not self.surface_ids or any(
            surface_id not in SURFACE_IDS or surface_id == "unavailable"
            for surface_id in self.surface_ids
        ):
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has invalid surfaces."
            )
        if len(self.surface_ids) != len(set(self.surface_ids)):
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} repeats a surface."
            )
        if not self.sources or any(source not in _CONTEXT_SOURCES for source in self.sources):
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has invalid sources."
            )
        if not self.capability_family or "." not in self.capability_family:
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has no domain capability family."
            )
        if not self.exact_target_type:
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has no exact target type."
            )
        if self.transaction_behavior not in _TRANSACTION_BEHAVIORS:
            raise NativeContextManifestError(
                f"Context action {self.action_id!r} has invalid transaction behavior."
            )
        if self.classification.human_only:
            if self.operation_variant is not None or self.transaction_behavior != "human":
                raise NativeContextManifestError(
                    f"Human-only context action {self.action_id!r} cannot advertise an operation."
                )
            if self.implementation_status != "human_only":
                raise NativeContextManifestError(
                    f"Human-only context action {self.action_id!r} has invalid status."
                )
        elif not self.operation_variant or self.implementation_status != "planned":
            raise NativeContextManifestError(
                f"Provider context action {self.action_id!r} lacks a planned operation."
            )

    def summary(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "surface_ids": list(self.surface_ids),
            "sources": list(self.sources),
            "source_command_id": self.source_command_id,
            "classification": {
                "mutation": self.classification.mutation,
                "view": self.classification.view,
                "export": self.classification.export,
                "interactive": self.classification.interactive,
                "human_only": self.classification.human_only,
            },
            "capability_family": self.capability_family,
            "operation_variant": self.operation_variant,
            "exact_target_type": self.exact_target_type,
            "transaction_behavior": self.transaction_behavior,
            "background_required": self.background_required,
            "implementation_status": self.implementation_status,
        }


def _action(
    action_id: str,
    surface_ids: tuple[str, ...],
    sources: tuple[str, ...],
    primary: str,
    capability_family: str,
    operation_variant: str | None,
    exact_target_type: str,
    transaction_behavior: str,
    *,
    source_command_id: str | None = None,
    interactive: bool = False,
    background_required: bool = False,
) -> NativeContextActionPlan:
    human_only = primary == "human_only"
    return NativeContextActionPlan(
        action_id=action_id,
        surface_ids=surface_ids,
        sources=sources,
        source_command_id=source_command_id,
        classification=_classification(primary, interactive=interactive),
        capability_family=capability_family,
        operation_variant=operation_variant,
        exact_target_type=exact_target_type,
        transaction_behavior=transaction_behavior,
        background_required=background_required,
        implementation_status="human_only" if human_only else "planned",
    )


NATIVE_CONTEXT_ACTIONS = (
    _action(
        "AssemblyContextToggleActive", ("assemble",), ("tree_context",),
        "human_only", "assembly.structure", None, "Assembly::Assembly",
        "human", interactive=True,
    ),
    _action(
        "AssemblyContextMakeFlexible", ("assemble",), ("tree_context",),
        "mutation", "assembly.structure", "make_flexible",
        "Assembly::AssemblyLink", "document",
    ),
    _action(
        "AssemblyContextMakeRigid", ("assemble",), ("tree_context",),
        "mutation", "assembly.structure", "make_rigid",
        "Assembly::AssemblyLink", "document",
    ),
    _action(
        "CAM_ExportTemplate", ("manufacture",), ("workbench_context", "menu"),
        "export", "manufacture.job", "export_template", "CAM::Job", "output",
        source_command_id="CAM_ExportTemplate",
    ),
    _action(
        "CAM_SetStartPoint", ("manufacture",), ("workbench_context",),
        "mutation", "manufacture.operation", "set_start_point",
        "CAM::Operation", "document", source_command_id="CAM_SetStartPoint",
    ),
    _action(
        "CAM_ToolBitSave", ("manufacture",), ("workbench_context",),
        "export", "manufacture.tool_output", "save", "CAM::ToolBit", "output",
        source_command_id="CAM_ToolBitSave",
    ),
    _action(
        "CAM_ToolBitSaveAs", ("manufacture",), ("workbench_context",),
        "export", "manufacture.tool_output", "save_as", "CAM::ToolBit", "output",
        source_command_id="CAM_ToolBitSaveAs",
    ),
    _action(
        "TechDrawContextEditBalloon", ("drawing",), ("tree_context",),
        "human_only", "drawing.annotation", None,
        "TechDraw::DrawViewBalloon", "human", interactive=True,
    ),
    _action(
        "TechDrawContextEditDimension", ("drawing",), ("tree_context",),
        "human_only", "drawing.dimension", None,
        "TechDraw::DrawViewDimension", "human", interactive=True,
    ),
    _action(
        "TechDrawContextShowDrawing", ("drawing",), ("tree_context",),
        "view", "drawing.presentation", "show", "TechDraw::DrawPage", "presentation",
    ),
    _action(
        "TechDrawContextToggleKeepUpdated", ("drawing",),
        ("tree_context", "drawing_canvas_context"), "mutation", "drawing.page",
        "toggle_keep_updated", "TechDraw::DrawPage", "document",
    ),
    _action(
        "TechDrawContextToggleFrames", ("drawing",), ("drawing_canvas_context",),
        "view", "drawing.presentation", "toggle_frames",
        "TechDraw::DrawPage", "presentation",
    ),
    _action(
        "TechDrawContextToggleGrid", ("drawing",), ("drawing_canvas_context",),
        "view", "drawing.presentation", "toggle_grid",
        "TechDraw::DrawPage", "presentation",
    ),
    _action(
        "TechDrawContextExportSVG", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "svg", "TechDraw::DrawPage",
        "background_output", background_required=True,
    ),
    _action(
        "TechDrawContextExportDXF", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "dxf", "TechDraw::DrawPage",
        "background_output", background_required=True,
    ),
    _action(
        "TechDrawContextExportPDF", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "pdf", "TechDraw::DrawPage",
        "background_output", background_required=True,
    ),
    _action(
        "TechDrawContextPrintAll", ("drawing",), ("drawing_canvas_context",),
        "export", "drawing.export", "print_all", "App::Document",
        "background_output", background_required=True,
    ),
    _action(
        "InspectionContextAnnotation", _INSPECTION_SURFACES,
        ("inspection_view_context",), "human_only", "inspect.interactive", None,
        "Inspection::Session", "human", interactive=True,
    ),
    _action(
        "InspectionContextLeaveInfoMode", _INSPECTION_SURFACES,
        ("inspection_view_context",), "human_only", "inspect.interactive", None,
        "Inspection::Session", "human", interactive=True,
    ),
)

if len({action.action_id for action in NATIVE_CONTEXT_ACTIONS}) != len(NATIVE_CONTEXT_ACTIONS):
    raise NativeContextManifestError("Native context-action IDs must be unique.")


def context_actions_for_surface(surface_id: str) -> tuple[NativeContextActionPlan, ...]:
    """Return classified context actions for one human-selected surface."""

    if surface_id not in SURFACE_IDS or surface_id == "unavailable":
        raise NativeContextManifestError(f"Unknown Native surface {surface_id!r}.")
    return tuple(
        action for action in NATIVE_CONTEXT_ACTIONS if surface_id in action.surface_ids
    )


def provider_context_actions_for_surface(
    surface_id: str,
) -> tuple[NativeContextActionPlan, ...]:
    """Return only context-equivalent operations the provider may receive."""

    return tuple(
        action
        for action in context_actions_for_surface(surface_id)
        if not action.classification.human_only
    )
