# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native assembly container in the active document."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "assembly.create_assembly",
    "description": (
        "Create one native assembly container (Assembly::AssemblyObject) with "
        "its joint group in the active document. Add parts with "
        "assembly.insert_component, then relate them with assembly.create_joint."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Visible label for the new assembly, e.g. 'MainAssembly'.",
            },
        },
        "required": ["label"],
        "additionalProperties": False,
    },
}


def run(service: Any, label: str) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")

    def create() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        with domain_runtime.NewTimelineOperation() as timeline:
            assembly = doc.addObject(
                "Assembly::AssemblyObject",
                "Assembly",
            )
            if assembly is None:
                raise RuntimeError(
                    "FreeCAD did not create an Assembly::AssemblyObject; "
                    "the Assembly workbench may not be available in this build."
                )
            timeline.set_operation(assembly)
            assembly.Type = "Assembly"
            assembly.Label = clean_label
            joint_group = assembly.newObject(
                "Assembly::JointGroup",
                "Joints",
            )
        doc.recompute()
        return {
            "document": doc.Name,
            "assembly": assembly.Name,
            "assembly_label": assembly.Label,
            "assembly_type": assembly.TypeId,
            "joint_group": getattr(joint_group, "Name", None),
        }

    transaction = run_freecad_transaction(
        f"Create assembly: {clean_label}",
        create,
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "create_assembly"},
        next_action=(
            "Insert components with assembly.insert_component using the "
            "returned exact assembly name, ground the first one, then add joints."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
