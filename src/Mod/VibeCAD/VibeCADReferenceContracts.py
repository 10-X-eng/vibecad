# SPDX-License-Identifier: LGPL-2.1-or-later

"""Managed semantic references to regenerating scripted CAD outputs."""

from __future__ import annotations

import json
from typing import Any

import VibeCADScriptedPublication as publication


CONTRACT_SCHEMA = "vibecad-reference-contract-v1"
PROP_CONTRACT = "VibeCADReferenceContract"
PROP_DERIVED_STATE = "VibeCADDerivedState"
PROP_STALE_REASON = "VibeCADStaleReason"
PROP_SOURCE_REVISION = "VibeCADSourceRevision"


class ReferenceContractError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        self.details = dict(details or {})
        super().__init__(message)


def interface_selection_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {
                "const": "published_interface",
                "description": (
                    "Bind to a stable semantic interface declared by a "
                    "scripted model, not to a transient FaceN/EdgeN name."
                ),
            },
            "interface_name": {
                "type": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_]*$",
                "description": "Exact published interface name from model context.",
            },
        },
        "required": ["type", "interface_name"],
        "additionalProperties": False,
    }


def set_contract(obj: Any, domain: str, payload: dict[str, Any]) -> None:
    if PROP_CONTRACT not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", PROP_CONTRACT, "VibeCAD References")
    contract = {
        "schema": CONTRACT_SCHEMA,
        "domain": str(domain),
        **dict(payload),
    }
    setattr(obj, PROP_CONTRACT, json.dumps(contract, sort_keys=True, separators=(",", ":")))


def read_contract(obj: Any) -> dict[str, Any] | None:
    if PROP_CONTRACT not in list(getattr(obj, "PropertiesList", []) or []):
        return None
    raw = str(getattr(obj, PROP_CONTRACT, "") or "")
    if not raw:
        return None
    try:
        contract = json.loads(raw)
    except ValueError as exc:
        raise ReferenceContractError(
            f"Object {getattr(obj, 'Name', '<object>')} has invalid managed "
            "reference metadata.",
            details={"native_error": str(exc), "raw_contract": raw},
        ) from exc
    if not isinstance(contract, dict) or contract.get("schema") != CONTRACT_SCHEMA:
        raise ReferenceContractError(
            f"Object {getattr(obj, 'Name', '<object>')} has an unsupported "
            "managed reference contract.",
            details={"contract": contract},
        )
    return contract


def published_object(value: Any) -> Any | None:
    # App::Link forwards properties from LinkedObject, including the scripted
    # role tag. Resolve the native link target before inspecting the occurrence
    # itself or an assembly component can be mistaken for the publication.
    linked = getattr(value, "LinkedObject", None)
    if publication.is_publication(linked):
        return linked
    if publication.is_publication(value):
        return value
    objects = list(getattr(value, "Objects", []) or [])
    if len(objects) == 1 and publication.is_publication(objects[0]):
        return objects[0]
    return None


def resolve_interface(
    service: Any,
    source: Any,
    interface_name: str,
) -> dict[str, Any]:
    published = published_object(source)
    if published is None:
        raise ReferenceContractError(
            "Published interfaces can only be selected on a VibeCAD published "
            "output or an App::Link to one.",
            details={"source": getattr(source, "Name", None)},
        )
    model_id = str(getattr(published, publication.PROP_MODEL_ID, "") or "")
    output_key = str(getattr(published, publication.PROP_OUTPUT_KEY, "") or "")
    try:
        root = publication.model_root_for(published)
    except publication.PublicationError as exc:
        raise ReferenceContractError(
            "The published output does not resolve to exactly one scripted model root.",
            details={"model_id": model_id, **exc.details},
        ) from exc
    try:
        interfaces = json.loads(
            str(getattr(root, publication.PROP_INTERFACES, "{}") or "{}")
        )
    except ValueError as exc:
        raise ReferenceContractError(
            "The scripted model's published interface table is invalid.",
            details={"model_root": root.Name, "native_error": str(exc)},
        ) from exc
    definition = interfaces.get(str(interface_name or ""))
    if not isinstance(definition, dict):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} does not exist on this model.",
            details={
                "model_root": root.Name,
                "available_interfaces": sorted(interfaces),
            },
        )
    if str(definition.get("output") or "") != output_key:
        raise ReferenceContractError(
            f"Published interface {interface_name!r} belongs to a different output.",
            details={
                "requested_output": output_key,
                "interface_output": definition.get("output"),
            },
        )
    selection = dict(definition.get("selection") or {})
    resolved = definition.get("resolved")
    if not isinstance(resolved, dict):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} has no validated resolution "
            "for the accepted model revision.",
            details={
                "model_id": model_id,
                "output_key": output_key,
                "selection": selection,
                "resolved": resolved,
            },
        )
    subelements = list(resolved.get("subelements") or [])
    geometry = list(resolved.get("geometry") or [])
    mode = str(selection.get("type") or "")
    expected = 0 if mode == "origin" else int(selection.get("expected_count") or 0)
    if (
        mode not in {"origin", "query"}
        or str(resolved.get("object") or "") != published.Name
        or len(subelements) != expected
        or len(geometry) != expected
    ):
        raise ReferenceContractError(
            f"Published interface {interface_name!r} has inconsistent validated "
            "resolution metadata.",
            details={
                "model_id": model_id,
                "output_key": output_key,
                "selection": selection,
                "resolved": resolved,
                "expected_count": expected,
            },
        )
    return {
        "ok": True,
        "model_id": model_id,
        "model_root": root.Name,
        "publication": published,
        "publication_name": published.Name,
        "output_key": output_key,
        "interface_name": interface_name,
        "selection": selection,
        "subelements": subelements,
        "geometry": geometry,
    }


def referenced_interface_names(contract: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "published_interface":
                model_id = str(value.get("model_id") or "")
                name = str(value.get("interface_name") or "")
                if model_id and name:
                    found.append((model_id, name))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract)
    return found


def referenced_model_ids(contract: dict[str, Any]) -> set[str]:
    """Return scripted-model dependencies explicitly recorded by a contract."""

    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") in {"published_interface", "scripted_model"}:
                model_id = str(value.get("model_id") or "")
                if model_id:
                    found.add(model_id)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract)
    return found


def scripted_model_dependencies(obj: Any) -> list[str]:
    """Find scripted models in the native dependency ancestry of ``obj``."""

    candidates = [obj, *list(getattr(obj, "OutListRecursive", []) or [])]
    seen: set[int] = set()
    model_ids: set[str] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        published = published_object(candidate)
        if published is not None:
            model_id = str(
                getattr(published, publication.PROP_MODEL_ID, "") or ""
            )
            if model_id:
                model_ids.add(model_id)
        contract = read_contract(candidate)
        if contract is not None:
            model_ids.update(referenced_model_ids(contract))
    return sorted(model_ids)


def dependency_records(model_ids: list[str]) -> list[dict[str, str]]:
    return [
        {"type": "scripted_model", "model_id": model_id}
        for model_id in sorted(set(model_ids))
        if model_id
    ]


def validate_removed_interfaces(
    doc: Any,
    publications: list[Any],
    model_id: str,
    previous_names: set[str],
    current_names: set[str],
    *,
    preflight: dict[str, Any] | None = None,
) -> None:
    removed = previous_names - current_names
    if not removed:
        return
    consumers: list[dict[str, Any]] = []
    carriers = list((preflight or {}).get("_carriers") or [])
    if not carriers:
        carriers, _uses = _reference_graph(doc, publications)
    for obj in carriers:
        if publication.is_publication(obj):
            continue
        contract = read_contract(obj)
        if contract is None:
            continue
        used = sorted(
            name
            for referenced_model, name in referenced_interface_names(contract)
            if referenced_model == model_id and name in removed
        )
        if used:
            consumers.append({"object": obj.Name, "interfaces": used})
    if consumers:
        raise ReferenceContractError(
            "The VibeScript update removes published interfaces that are still "
            "used by downstream CAD objects.",
            details={"removed_interfaces": sorted(removed), "consumers": consumers},
        )


def refresh_after_publication(
    service: Any,
    model_id: str,
    publications: list[Any],
    *,
    revision: str,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = service._active_document()
    carriers = list((preflight or {}).get("_carriers") or [])
    uses = list((preflight or {}).get("_uses") or [])
    if not carriers:
        carriers, uses = _reference_graph(doc, publications)
    managed: list[dict[str, Any]] = []
    unsafe: list[dict[str, Any]] = []
    for use in uses:
        if not use.get("subelements"):
            continue
        owner = use["owner"]
        contract = read_contract(owner)
        if contract is None:
            unsafe.append(publication.json_reference_uses([use])[0])
            continue
        managed.append({"object": owner, "contract": contract})
    if unsafe:
        raise ReferenceContractError(
            "Regeneration would leave unmanaged Face/Edge/Vertex references "
            "pointing at potentially different geometry.",
            details={
                "unsafe_references": unsafe,
                "required_action": (
                    "Recreate these references with a semantic published interface "
                    "or remove them before regenerating the model."
                ),
            },
        )

    carrier_order = {id(carrier): index for index, carrier in enumerate(carriers)}
    managed.sort(
        key=lambda item: carrier_order.get(id(item["object"]), len(carriers))
    )
    rebound: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    deferred_part_rebinds: list[str] = []
    seen: set[int] = set()
    for item in managed:
        obj = item["object"]
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        contract = item["contract"]
        if model_id not in referenced_model_ids(contract):
            continue
        if str(contract.get("domain") or "") == "part_edge_finish":
            deferred_part_rebinds.append(str(getattr(obj, "Name", "") or ""))
            continue
        contract = dict(contract)
        contract["source_revision"] = revision
        outcome = _rebind_one(service, obj, contract)
        if outcome.get("rebind_deferred"):
            deferred.append(outcome)
        else:
            rebound.append(outcome)

    invalidated: list[dict[str, Any]] = []
    for carrier in carriers:
        if publication.is_publication(carrier):
            continue
        if id(carrier) in seen:
            continue
        touch = getattr(carrier, "touch", None)
        if callable(touch):
            touch()
        if _is_derived_analysis_or_manufacturing_object(carrier):
            mark_stale(
                carrier,
                revision,
                "A referenced scripted model changed; regenerate this derived result.",
            )
            invalidated.append(
                {"object": carrier.Name, "type": getattr(carrier, "TypeId", "")}
            )
    # _reference_graph expands publication consumers breadth-first, which is
    # already dependency order from the regenerated source toward derived Part
    # features. Avoid materializing the whole document's topological ordering.
    ordered = list(carriers)
    part_recompute_objects = [
        str(getattr(item, "Name", "") or "")
        for item in ordered
        if not publication.is_publication(item)
        and "Shape" in list(getattr(item, "PropertiesList", []) or [])
        and str(getattr(item, "TypeId", "") or "").startswith(("Part::", "PartDesign::"))
        and "Python" not in str(getattr(item, "TypeId", "") or "")
    ]
    return {
        "rebound": rebound,
        "deferred": deferred,
        "invalidated": invalidated,
        "carrier_count": len(carriers),
        "part_recompute_objects": part_recompute_objects,
        "deferred_part_rebinds": deferred_part_rebinds,
        "native_part_expectations": list(
            (preflight or {}).get("native_part_expectations") or []
        ),
    }


def preflight_regeneration(
    service: Any,
    publications: list[Any],
    *,
    model_root: Any | None = None,
) -> dict[str, Any]:
    doc = service._active_document()
    carriers, uses = _reference_graph(
        doc,
        publications,
        model_root=model_root,
    )
    unsafe: list[dict[str, Any]] = []
    managed_objects: set[str] = set()
    for use in uses:
        if not use.get("subelements"):
            continue
        owner = use["owner"]
        contract = read_contract(owner)
        if contract is None:
            unsafe.append(publication.json_reference_uses([use])[0])
        else:
            managed_objects.add(str(getattr(owner, "Name", "") or ""))
    if unsafe:
        raise ReferenceContractError(
            "Regeneration would leave unmanaged Face/Edge/Vertex references "
            "pointing at potentially different geometry.",
            details={
                "unsafe_references": unsafe,
                "required_action": (
                    "Recreate these references with a semantic published interface "
                    "or remove them before regenerating the model."
                ),
            },
        )
    return {
        "carrier_count": len(carriers),
        "carrier_objects": [
            str(getattr(item, "Name", "") or "") for item in carriers
        ],
        "managed_reference_objects": sorted(managed_objects),
        "native_part_expectations": native_part_carrier_expectations(carriers),
        "_carriers": carriers,
        "_uses": uses,
    }


def _reference_graph(
    doc: Any,
    publications: list[Any],
    *,
    model_root: Any | None = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    roots: dict[str, Any] = {}
    if model_root is not None:
        root_name = str(getattr(model_root, "Name", "") or "")
        unowned = []
        for item in publications:
            try:
                owner = publication.model_root_for(item)
                publication.publication_target(item, model_root)
            except publication.PublicationError:
                owner = None
            if owner is not model_root:
                unowned.append(str(getattr(item, "Name", "") or ""))
        if not root_name or unowned:
            raise ReferenceContractError(
                "Scripted outputs do not belong to their declared model root.",
                details={"model_root": root_name, "unowned_outputs": unowned},
            )
        roots[root_name] = model_root
    else:
        for item in publications:
            try:
                root = publication.model_root_for(item)
            except publication.PublicationError as exc:
                raise ReferenceContractError(
                    "A scripted publication has no unambiguous model owner.",
                    details=exc.details,
                ) from exc
            roots[str(getattr(root, "Name", "") or "")] = root
    internal = list(roots.values())
    for root in roots.values():
        internal.extend(publication.implementation_closure(root))
    carriers = list(publications)
    carrier_ids = {id(item) for item in carriers}
    all_uses: list[dict[str, Any]] = []
    use_keys: set[tuple[Any, ...]] = set()

    def retain_uses(uses: list[dict[str, Any]]) -> None:
        for use in uses:
            key = (
                id(use.get("owner")),
                str(use.get("property") or ""),
                use.get("_target_id"),
                str(use.get("target_name") or ""),
                tuple(str(item) for item in list(use.get("subelements") or [])),
            )
            if key in use_keys:
                continue
            use_keys.add(key)
            all_uses.append(use)

    changed = True
    while changed:
        changed = False
        uses = publication.external_reference_uses(
            doc,
            carriers,
            internal_objects=[*publications, *internal],
        )
        retain_uses(uses)
        for use in uses:
            property_type = str(use.get("property_type") or "")
            if "LinkSub" in property_type:
                continue
            owner = use["owner"]
            if id(owner) not in carrier_ids:
                carrier_ids.add(id(owner))
                carriers.append(owner)
                changed = True
    final_uses = publication.external_reference_uses(
        doc,
        carriers,
        internal_objects=[*publications, *internal],
    )
    retain_uses(final_uses)
    return carriers, all_uses


def _rebind_one(service: Any, obj: Any, contract: dict[str, Any]) -> dict[str, Any]:
    domain = str(contract.get("domain") or "")
    if domain == "assembly_joint":
        from tool_impl.service import assembly_create_joint as handler
    elif domain == "fem_constraint":
        from tool_impl.service import fem_add_constraint as handler
    elif domain == "techdraw_dimension":
        from tool_impl.service import techdraw_add_dimension as handler
    elif domain == "cam_reference":
        from tool_impl.service import cam_add_operation as handler
    elif domain == "part_edge_finish":
        from tool_impl.service import part_fillet as handler
    else:
        raise ReferenceContractError(
            f"No rebinding implementation exists for managed reference domain {domain!r}.",
            details={"object": obj.Name, "contract": contract},
        )
    rebind = getattr(handler, "rebind_scripted_reference", None)
    if not callable(rebind):
        raise ReferenceContractError(
            f"Reference domain {domain!r} does not implement regeneration rebinding.",
            details={"object": obj.Name},
        )
    result = rebind(service, obj, contract)
    if not isinstance(result, dict) or not result.get("ok"):
        raise ReferenceContractError(
            f"Managed references on {obj.Name} could not be rebound.",
            details={"object": obj.Name, "domain": domain, "result": result},
        )
    return result


def _native_part_carriers(carriers: list[Any]) -> list[Any]:
    return [
        obj
        for obj in carriers
        if not publication.is_publication(obj)
        and str(getattr(obj, "TypeId", "") or "").startswith(
            ("Part::", "PartDesign::")
        )
        and "Python" not in str(getattr(obj, "TypeId", "") or "")
        and "Shape" in list(getattr(obj, "PropertiesList", []) or [])
    ]


def _expected_content_kind(shape_type: str) -> str:
    return {
        "Solid": "solid",
        "CompSolid": "solid",
        "Shell": "face",
        "Face": "face",
        "Wire": "edge",
        "Edge": "edge",
        "Vertex": "vertex",
    }.get(shape_type, "topology")


def native_part_carrier_expectations(carriers: list[Any]) -> list[dict[str, Any]]:
    """Capture cheap owner-thread expectations without validating geometry."""

    expectations: list[dict[str, Any]] = []
    for obj in _native_part_carriers(carriers):
        shape = getattr(obj, "Shape", None)
        try:
            is_null = shape is None or bool(shape.isNull())
            shape_type = None if is_null else str(shape.ShapeType)
        except Exception as exc:
            is_null = True
            shape_type = None
            inspection_error = str(exc)
        else:
            inspection_error = None
        expectations.append(
            {
                "object": str(getattr(obj, "Name", "") or ""),
                "type": str(getattr(obj, "TypeId", "") or ""),
                "state": [
                    str(value) for value in list(getattr(obj, "State", []) or [])
                ],
                "shape_null": is_null,
                "shape_type": shape_type,
                "expected_content_kind": _expected_content_kind(shape_type or ""),
                **(
                    {"inspection_error": inspection_error}
                    if inspection_error
                    else {}
                ),
            }
        )
    return expectations


def capture_native_part_carriers(carriers: list[Any]) -> list[dict[str, Any]]:
    """Capture immutable shape handles for provider-thread validation."""

    return [
        {
            "object": str(getattr(obj, "Name", "") or ""),
            "type": str(getattr(obj, "TypeId", "") or ""),
            "state": [str(value) for value in list(getattr(obj, "State", []) or [])],
            "_shape": getattr(obj, "Shape", None),
        }
        for obj in _native_part_carriers(carriers)
    ]


def native_part_carrier_facts(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate detached native Part shape handles on the provider worker."""

    facts: list[dict[str, Any]] = []
    for snapshot in snapshots:
        item: dict[str, Any] = {
            "object": str(snapshot.get("object") or ""),
            "type": str(snapshot.get("type") or ""),
            "state": list(snapshot.get("state") or []),
        }
        try:
            shape = snapshot.get("_shape")
            is_null = shape is None or bool(shape.isNull())
            item.update(
                {
                    "shape_null": is_null,
                    "shape_valid": False if is_null else bool(shape.isValid()),
                    "shape_type": None if is_null else str(shape.ShapeType),
                    "solids": 0 if is_null else len(list(shape.Solids or [])),
                    "faces": 0 if is_null else len(list(shape.Faces or [])),
                    "edges": 0 if is_null else len(list(shape.Edges or [])),
                    "vertices": 0 if is_null else len(list(shape.Vertexes or [])),
                    "volume_mm3": 0.0 if is_null else float(shape.Volume),
                }
            )
        except Exception as exc:
            item["inspection_error"] = str(exc)
        facts.append(item)
    return facts


def _validate_native_part_carriers(
    snapshots: list[dict[str, Any]],
    expectations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reject invalid downstream Part features before regeneration commits."""

    expected_by_name = {
        str(item.get("object") or ""): item
        for item in expectations
        if isinstance(item, dict) and item.get("object")
    }
    checked = native_part_carrier_facts(snapshots)
    failures: list[dict[str, Any]] = []
    for item in checked:
        state_values = list(item.get("state") or [])
        before = expected_by_name.get(str(item.get("object") or ""))
        retained_content = True
        if before is not None:
            expected_kind = str(before.get("expected_content_kind") or "topology")
            if expected_kind == "solid":
                retained_content = int(item.get("solids", 0) or 0) > 0
            elif expected_kind == "face":
                retained_content = int(item.get("faces", 0) or 0) > 0
            elif expected_kind == "edge":
                retained_content = int(item.get("edges", 0) or 0) > 0
            elif expected_kind == "vertex":
                retained_content = int(item.get("vertices", 0) or 0) > 0
            else:
                retained_content = any(
                    int(item.get(field, 0) or 0) > 0
                    for field in ("solids", "faces", "edges", "vertices")
                )
        item["pre_regeneration_shape"] = before
        item["retained_shape_content"] = retained_content
        item["ok"] = bool(
            item.get("shape_null") is False
            and item.get("shape_valid") is True
            and not any("Invalid" in value for value in state_values)
            and not item.get("inspection_error")
            and retained_content
        )
        if not item["ok"]:
            failures.append(item)
    if failures:
        raise ReferenceContractError(
            "A downstream native Part feature became invalid after scripted-model "
            "regeneration; the update was not accepted.",
            details={"invalid_part_features": failures},
        )
    return {"ok": True, "checked": checked}


def _is_derived_analysis_or_manufacturing_object(obj: Any) -> bool:
    type_id = str(getattr(obj, "TypeId", "") or "").lower()
    if any(
        marker in type_id
        for marker in ("femmesh", "femresult", "resultmechanical")
    ):
        return True
    properties = set(getattr(obj, "PropertiesList", []) or [])
    return type_id.startswith("path::") and {"Base", "Path"}.issubset(properties)


def mark_stale(obj: Any, revision: str, reason: str) -> None:
    for name in (PROP_DERIVED_STATE, PROP_STALE_REASON, PROP_SOURCE_REVISION):
        if name not in list(getattr(obj, "PropertiesList", []) or []):
            obj.addProperty("App::PropertyString", name, "VibeCAD References")
    setattr(obj, PROP_DERIVED_STATE, "stale")
    setattr(obj, PROP_STALE_REASON, reason)
    setattr(obj, PROP_SOURCE_REVISION, revision)


def rebind_managed_reference(
    service: Any, obj: Any, *, revision: str
) -> dict[str, Any]:
    """Rebind one persisted semantic contract without recomputing the document."""

    contract = read_contract(obj)
    if contract is None:
        raise ReferenceContractError(
            "The requested downstream object has no managed reference contract.",
            details={"object": str(getattr(obj, "Name", "") or "")},
        )
    effective = dict(contract)
    effective["source_revision"] = revision
    return _rebind_one(service, obj, effective)


def validate_native_part_refresh(
    snapshots: list[dict[str, Any]], expectations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Validate recomputed detached Part carriers against preflight facts."""

    return _validate_native_part_carriers(snapshots, expectations)
