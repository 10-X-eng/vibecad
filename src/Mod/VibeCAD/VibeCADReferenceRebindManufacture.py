# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable CAM-reference rebinding after VibeScript regeneration."""

from __future__ import annotations

from typing import Any

import VibeCADReferenceContracts as reference_contracts


def rebind_scripted_reference(
    service: Any,
    operation_obj: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    job_name = str(contract.get("job_name") or "")
    job = service._get_cam_job(job_name)
    if job is None:
        return _invalid("The CAM job recorded by this operation no longer exists.")
    faces = contract.get("faces")
    if not isinstance(faces, list) or not faces:
        return _invalid("The CAM reference contract contains no faces.")
    doc = service._active_document()
    refs, error = _resolve_rebind_faces(service, job, faces)
    if error:
        return _invalid(error)
    grouped: dict[str, list[str]] = {}
    for object_name, face_name in refs:
        grouped.setdefault(object_name, []).append(face_name)
    try:
        operation_obj.Base = [
            (doc.getObject(object_name), names)
            for object_name, names in grouped.items()
        ]
        operation_obj.touch()
    except Exception as exc:
        return _invalid(
            "FreeCAD could not rebind the CAM operation.",
            native_error=str(exc),
        )
    actual_refs: list[tuple[str, str]] = []
    for linked_object, linked_names in list(getattr(operation_obj, "Base", []) or []):
        for linked_name in list(linked_names or []):
            actual_refs.append(
                (
                    str(getattr(linked_object, "Name", "") or ""),
                    str(linked_name or ""),
                )
            )
    if sorted(actual_refs) != sorted(refs):
        return _invalid(
            "FreeCAD did not retain the rebound CAM base references exactly.",
            requested_references=refs,
            actual_references=actual_refs,
        )
    reference_contracts.mark_stale(
        operation_obj,
        str(contract.get("source_revision") or ""),
        "A referenced scripted model changed; regenerate and verify this CAM toolpath.",
    )
    return {
        "ok": True,
        "domain": "cam_reference",
        "object": operation_obj.Name,
        "resolved_faces": actual_refs,
        "toolpath_recompute_deferred": True,
    }


def _resolve_rebind_faces(
    service: Any,
    job: Any,
    faces: list[dict[str, Any]],
) -> tuple[list[tuple[str, str]], str | None]:
    """Resolve persisted CAM interfaces without inspecting live face geometry."""

    model_names = {
        str(getattr(item, "Name", "") or "")
        for item in list(
            getattr(getattr(job, "Model", None), "Group", []) or []
        )
    }
    doc = service._active_document()
    refs: list[tuple[str, str]] = []
    for item in faces:
        if not isinstance(item, dict):
            return [], "The CAM reference contract contains a non-object face entry."
        object_name = str(item.get("object_name") or "")
        if object_name not in model_names:
            return [], f"CAM job model clone no longer exists: {object_name}"
        obj = doc.getObject(object_name) if doc is not None else None
        if obj is None:
            return [], f"CAM job model object no longer exists: {object_name}"
        selection = item.get("selection")
        if isinstance(selection, dict):
            interface_name = str(selection.get("interface_name") or "")
            try:
                interface = reference_contracts.resolve_interface(
                    service, obj, interface_name
                )
            except reference_contracts.ReferenceContractError as exc:
                return [], f"{object_name}: {exc}"
            names = list(interface.get("subelements") or [])
            if not names or any(not name.startswith("Face") for name in names):
                return [], (
                    f"CAM interface {interface_name!r} must resolve only to one "
                    "or more faces."
                )
            refs.extend((object_name, name) for name in names)
            continue
        face_name = str(item.get("face_name") or "")
        if not face_name.startswith("Face"):
            return [], f"Invalid persisted CAM face name: {face_name!r}"
        refs.append((object_name, face_name))
    if len(refs) != len(set(refs)):
        return [], "The rebound CAM face contract resolves duplicate faces."
    return refs, None


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
