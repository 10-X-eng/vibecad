# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Native Assembly creation without entering persistent edit mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyState import (
    assembly_objects,
    read_active_assembly,
    same_assembly,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


class NativeAssemblyStructureError(RuntimeError):
    """Assembly structure input or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_STRUCTURE_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblyCreateSpec:
    label: str
    parent_ref: NativeObjectRef | None
    expected_assembly_count: int


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _enforce_one_root() -> bool:
    try:
        import FreeCAD as App

        return bool(
            App.ParamGet(
                "User parameter:BaseApp/Preferences/Mod/Assembly"
            ).GetBool("EnforceOneAssemblyRule", True)
        )
    except (ImportError, AttributeError, RuntimeError):
        return True


def _direct_parent(obj: Any) -> Any | None:
    reader = getattr(obj, "getParentGeoFeatureGroup", None)
    if not callable(reader):
        return None
    try:
        return reader()
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def _root_assemblies(document: Any) -> tuple[Any, ...]:
    return tuple(
        assembly
        for assembly in assembly_objects(document)
        if _direct_parent(assembly) is None
    )


def _resolve_parent(document: Any, parent_ref: NativeObjectRef | None) -> Any | None:
    if parent_ref is None:
        return None
    return resolve_object(
        document,
        parent_ref,
        expected_types=("Assembly::AssemblyObject",),
    )


def preflight_create_assembly(
    document: Any,
    spec: AssemblyCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    enforce_one_root: Callable[[], bool] = _enforce_one_root,
) -> Any | None:
    assemblies = assembly_objects(document)
    if len(assemblies) != spec.expected_assembly_count:
        raise NativeAssemblyStructureError(
            "The Assembly count changed; read current Assemble state and retry."
        )
    parent = _resolve_parent(document, spec.parent_ref)
    active = active_reader(document)
    if not same_assembly(parent, active):
        raise NativeAssemblyStructureError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    if parent is not None and not _timeline_active(parent):
        raise NativeAssemblyStructureError(
            "The human-active Assembly is outside the current document history."
        )
    if parent is None and enforce_one_root() and _root_assemblies(document):
        raise NativeAssemblyStructureError(
            "This document already has a root Assembly and the one-root preference is enabled."
        )
    return parent


def create_assembly(
    document: Any,
    spec: AssemblyCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    enforce_one_root: Callable[[], bool] = _enforce_one_root,
) -> NativeMutationDraft:
    parent = preflight_create_assembly(
        document,
        spec,
        active_reader=active_reader,
        enforce_one_root=enforce_one_root,
    )
    before_count = len(assembly_objects(document))
    active_before = active_reader(document)
    if parent is None:
        assembly = document.addObject("Assembly::AssemblyObject", "Assembly")
    else:
        assembly = parent.newObject("Assembly::AssemblyObject", "Assembly")
    if (
        assembly is None
        or str(getattr(assembly, "TypeId", "") or "")
        != "Assembly::AssemblyObject"
        or getattr(assembly, "Document", None) is not document
    ):
        raise NativeAssemblyStructureError(
            "The Assembly factory returned the wrong object type."
        )
    assembly.Type = "Assembly"
    assembly.Label = spec.label
    joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
    if (
        joint_group is None
        or str(getattr(joint_group, "TypeId", "") or "")
        != "Assembly::JointGroup"
        or getattr(joint_group, "Document", None) is not document
    ):
        raise NativeAssemblyStructureError(
            "The Assembly did not create its native Joints group."
        )
    changed = (object_identity(parent),) if parent is not None else ()
    return NativeMutationDraft(
        value={
            "assembly": assembly,
            "joint_group": joint_group,
            "parent": parent,
            "active_before": active_before,
            "before_count": before_count,
            "label": spec.label,
        },
        recompute_targets=tuple(
            value for value in (joint_group, assembly, parent) if value is not None
        ),
        created=(object_identity(assembly), object_identity(joint_group)),
        changed=changed,
    )


def verify_created_assembly(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
) -> dict[str, Any]:
    value = draft.value
    assembly = value["assembly"]
    joint_group = value["joint_group"]
    parent = value["parent"]
    get_object = getattr(document, "getObject", None)
    children = list(getattr(assembly, "Group", ()) or ())
    joint_groups = [
        child
        for child in children
        if str(getattr(child, "TypeId", "") or "") == "Assembly::JointGroup"
    ]
    parent_contains = (
        parent is None
        or assembly in list(getattr(parent, "Group", ()) or ())
    )
    active_after = active_reader(document)
    if (
        not callable(get_object)
        or get_object(str(getattr(assembly, "Name", "") or "")) is not assembly
        or get_object(str(getattr(joint_group, "Name", "") or "")) is not joint_group
        or len(assembly_objects(document)) != int(value["before_count"]) + 1
        or str(getattr(assembly, "TypeId", "") or "")
        != "Assembly::AssemblyObject"
        or str(getattr(assembly, "Type", "") or "") != "Assembly"
        or str(getattr(assembly, "Label", "") or "") != value["label"]
        or int(getattr(assembly, "ID", 0) or 0) <= 0
        or len(joint_groups) != 1
        or joint_groups[0] is not joint_group
        or not parent_contains
        or _direct_parent(assembly) is not parent
        or not _timeline_active(assembly)
        or not _timeline_active(joint_group)
        or not same_assembly(value["active_before"], active_after)
    ):
        raise NativeAssemblyStructureError(
            "The new Assembly failed its exact structure or activation postcondition."
        )
    result: dict[str, Any] = {
        "assembly": object_reference(assembly),
        "joint_group": object_reference(joint_group),
        "nested": parent is not None,
        "active_assembly_unchanged": True,
        "assembly_count": len(assembly_objects(document)),
    }
    if parent is not None:
        result["parent_assembly"] = object_reference(parent)
    return result
