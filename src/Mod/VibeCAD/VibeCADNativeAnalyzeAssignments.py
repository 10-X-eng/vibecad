# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded inventory for FEM assignments and mesh regions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeTargets import PreparedAnalysisTarget, prepare_analysis_target


ASSIGNMENT_CATEGORIES = (
    "material",
    "element",
    "electromagnetic",
    "fluid",
    "geometrical",
    "support",
    "connection",
    "load",
    "thermal",
    "mesh",
    "mesh_refinement",
)
_TARGET_INVALID = "NATIVE_ANALYZE_TARGET_TYPE_INVALID"
_SUBELEMENT = re.compile(r"^(Solid|Face|Edge|Vertex)([1-9][0-9]*)$")
_KIND_FIELDS = {
    "material": "material_kind",
    "element": "element_kind",
    "electromagnetic": "constraint_kind",
    "fluid": "constraint_kind",
    "geometrical": "feature_kind",
    "support": "condition_kind",
    "connection": "connection_kind",
    "load": "load_kind",
    "thermal": "thermal_mode",
    "mesh": "mesher",
    "mesh_refinement": "refinement_mode",
}


def _readers() -> tuple[tuple[str, Any], ...]:
    from VibeCADNativeAnalyzeConnectionState import connection_state
    from VibeCADNativeAnalyzeConstraintState import electromagnetic_constraint_state
    from VibeCADNativeAnalyzeElementState import element_definition_state
    from VibeCADNativeAnalyzeFluidState import fluid_constraint_state
    from VibeCADNativeAnalyzeGeometricalState import geometrical_feature_state
    from VibeCADNativeAnalyzeLoadState import load_state
    from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
    from VibeCADNativeAnalyzeState import material_state
    from VibeCADNativeAnalyzeSupportState import support_condition_state
    from VibeCADNativeAnalyzeThermalState import thermal_condition_state

    return (
        ("material", material_state),
        ("element", element_definition_state),
        ("electromagnetic", electromagnetic_constraint_state),
        ("fluid", fluid_constraint_state),
        ("geometrical", geometrical_feature_state),
        ("support", support_condition_state),
        ("connection", connection_state),
        ("load", load_state),
        ("thermal", thermal_condition_state),
        ("mesh", fem_mesh_definition_state),
    )


def _canonical_reference(value: Mapping[str, Any]) -> dict[str, Any] | None:
    object_name = str(value.get("object_name") or "")
    if not object_name:
        return None
    raw = value.get("subelements")
    if raw is None and value.get("subelement"):
        raw = [value["subelement"]]
    return {
        "object_name": object_name,
        "subelements": [str(item) for item in tuple(raw or ())],
    }


def _state_references(category: str, state: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw: list[Mapping[str, Any]] = []
    if isinstance(state.get("references"), list):
        raw.extend(item for item in state["references"] if isinstance(item, Mapping))
    if category == "connection":
        raw.extend(
            value
            for value in (state.get("slave"), state.get("master"))
            if isinstance(value, Mapping)
        )
    if category == "geometrical" and isinstance(state.get("face"), Mapping):
        raw.append(state["face"])
    if category == "mesh" and isinstance(state.get("source"), Mapping):
        raw.append(state["source"])
    result = []
    for value in raw:
        normalized = _canonical_reference(value)
        if normalized is not None and normalized not in result:
            result.append(normalized)
    return result


def _state_definition(category: str, state: Mapping[str, Any]) -> dict[str, Any]:
    if category == "material":
        result = {"properties": dict(state.get("properties") or {})}
        material_uuid = str(state.get("material_uuid") or "")
        if material_uuid:
            result["material_uuid"] = material_uuid
        return result
    if category == "mesh":
        return {
            "settings": dict(state.get("settings") or {}),
            "generated": bool(state.get("generated")),
            "topology": dict(state.get("topology") or {}),
        }
    definition = state.get("definition")
    return dict(definition) if isinstance(definition, Mapping) else {}


def compact_assignment_state(
    category: str,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only the exact identity, target, and physical definition needed at a glance."""

    if category not in ASSIGNMENT_CATEGORIES:
        raise NativeAnalyzeError("assignment category is unsupported.")
    if not isinstance(state, Mapping):
        raise NativeAnalyzeError("assignment state must be one object.")
    object_name = str(state.get("object_name") or "")
    state_sha = str(state.get("state_sha256") or "")
    if not object_name or len(state_sha) != 64:
        raise NativeAnalyzeError("assignment state is missing its exact identity.")
    return {
        "object_name": object_name,
        "label": str(state.get("label") or object_name),
        "type_id": str(state.get("type_id") or ""),
        "category": category,
        "kind": str(state.get(_KIND_FIELDS[category]) or category),
        "state_sha256": state_sha,
        "references": _state_references(category, state),
        "definition": _state_definition(category, state),
    }


def _invalid_record(obj: Any, category: str, exc: BaseException) -> dict[str, Any]:
    return {
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": str(getattr(obj, "TypeId", "") or ""),
        "category": category,
        "valid": False,
        "error": " ".join(str(exc).split()),
    }


def _read_assignment(obj: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for category, reader in _readers():
        try:
            state = reader(obj)
        except NativeAnalyzeError as exc:
            if exc.error_code == _TARGET_INVALID:
                continue
            return None, _invalid_record(obj, category, exc)
        return compact_assignment_state(category, state), None
    return None, None


def assignment_records(analysis: Any) -> tuple[dict[str, Any], ...]:
    """Return every exact assignment and mesh resource in stable document order."""

    from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state

    records: list[dict[str, Any]] = []
    mesh_objects = []
    seen: set[tuple[str, int]] = set()
    for member in tuple(getattr(analysis, "Group", ()) or ()):
        identity = (
            str(getattr(member, "Name", "") or ""),
            int(getattr(member, "ID", -1)),
        )
        if identity in seen:
            continue
        seen.add(identity)
        record, invalid = _read_assignment(member)
        if record is not None:
            records.append(record)
            if record["category"] == "mesh":
                mesh_objects.append(member)
        elif invalid is not None:
            records.append(invalid)
    for mesh in mesh_objects:
        resources = (
            *tuple(getattr(mesh, "MeshRefinementList", ()) or ()),
            *tuple(getattr(mesh, "MeshGroupList", ()) or ()),
        )
        for resource in resources:
            identity = (
                str(getattr(resource, "Name", "") or ""),
                int(getattr(resource, "ID", -1)),
            )
            if identity in seen:
                continue
            seen.add(identity)
            try:
                state = mesh_refinement_state(resource)
                records.append(compact_assignment_state("mesh_refinement", state))
            except NativeAnalyzeError as exc:
                records.append(_invalid_record(resource, "mesh_refinement", exc))
    return tuple(records)


def _mesh_validation_record(obj: Any) -> dict[str, Any] | None:
    """Read only mesh facts needed for validation, without serializing FemMesh."""

    from VibeCADNativeAnalyzeMeshState import fem_mesher_kind

    try:
        kind = fem_mesher_kind(obj)
    except NativeAnalyzeError as exc:
        if exc.error_code == _TARGET_INVALID:
            return None
        return _invalid_record(obj, "mesh", exc)
    try:
        source = obj.Shape
        source_name = str(source.Name)
        fem_mesh = obj.FemMesh
        topology = {
            "nodes": int(fem_mesh.NodeCount),
            "edges": int(fem_mesh.EdgeCount),
            "faces": int(fem_mesh.FaceCount),
            "volumes": int(fem_mesh.VolumeCount),
        }
        if not source_name:
            raise NativeAnalyzeError("The FEM mesh definition has no geometry source.")
        return {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "category": "mesh",
            "kind": "netgen" if kind == "netgen_legacy" else kind,
            "references": [{"object_name": source_name, "subelements": []}],
            "definition": {
                "generated": topology["nodes"] > 0,
                "topology": topology,
            },
        }
    except Exception as exc:
        return _invalid_record(obj, "mesh", exc)


def assignment_validation_records(analysis: Any) -> tuple[dict[str, Any], ...]:
    """Return assignment facts needed for validation without hashing mesh payloads."""

    from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state

    records: list[dict[str, Any]] = []
    mesh_objects = []
    seen: set[tuple[str, int]] = set()
    for member in tuple(getattr(analysis, "Group", ()) or ()):
        identity = (
            str(getattr(member, "Name", "") or ""),
            int(getattr(member, "ID", -1)),
        )
        if identity in seen:
            continue
        seen.add(identity)
        mesh_record = _mesh_validation_record(member)
        if mesh_record is not None:
            records.append(mesh_record)
            if mesh_record.get("valid") is not False:
                mesh_objects.append(member)
            continue
        record, invalid = _read_assignment(member)
        if record is not None:
            records.append(record)
        elif invalid is not None:
            records.append(invalid)
    for mesh in mesh_objects:
        resources = (
            *tuple(getattr(mesh, "MeshRefinementList", ()) or ()),
            *tuple(getattr(mesh, "MeshGroupList", ()) or ()),
        )
        for resource in resources:
            identity = (
                str(getattr(resource, "Name", "") or ""),
                int(getattr(resource, "ID", -1)),
            )
            if identity in seen:
                continue
            seen.add(identity)
            try:
                state = mesh_refinement_state(resource)
                records.append(compact_assignment_state("mesh_refinement", state))
            except NativeAnalyzeError as exc:
                records.append(_invalid_record(resource, "mesh_refinement", exc))
    return tuple(records)


def page_assignment_records(
    records: Iterable[Mapping[str, Any]],
    *,
    category: Any,
    offset: Any,
    page_size: Any,
) -> dict[str, Any]:
    requested = str(category or "")
    if requested != "all" and requested not in ASSIGNMENT_CATEGORIES:
        raise NativeAnalyzeError("category must be all or one assignment category.")
    if type(offset) is not int or offset < 0:
        raise NativeAnalyzeError("offset must be a non-negative integer.")
    if type(page_size) is not int or not 1 <= page_size <= 64:
        raise NativeAnalyzeError("page_size must be an integer from 1 to 64.")
    values = tuple(
        dict(record)
        for record in records
        if requested == "all" or record.get("category") == requested
    )
    page = values[offset : offset + page_size]
    next_offset = offset + len(page)
    return {
        "category": requested,
        "offset": offset,
        "page_size": page_size,
        "total": len(values),
        "assignments": list(page),
        "next_offset": next_offset if next_offset < len(values) else None,
    }


def list_assignments(
    analysis: Any,
    *,
    category: Any,
    offset: Any,
    page_size: Any,
) -> dict[str, Any]:
    return page_assignment_records(
        assignment_records(analysis),
        category=category,
        offset=offset,
        page_size=page_size,
    )


def _reference_issue(
    document: Any, assignment: str, reference: Mapping[str, Any]
) -> str | None:
    source_name = str(reference.get("object_name") or "")
    source = document.getObject(source_name) if source_name else None
    if source is None:
        return f"{assignment} references missing object {source_name or '<empty>'}."
    shape = getattr(source, "Shape", None)
    for subelement in tuple(reference.get("subelements") or ()):
        match = _SUBELEMENT.fullmatch(str(subelement))
        if match is None:
            return f"{assignment} has invalid subelement {subelement}."
        values = (
            getattr(shape, match.group(1) + "s", None) if shape is not None else None
        )
        if values is None or int(match.group(2)) > len(values):
            return f"{assignment} references missing {source_name}.{subelement}."
    return None


def validate_assignment_coverage(
    records: Iterable[Mapping[str, Any]],
    *,
    mesh_domains: Mapping[str, set[str]],
    solid_units: Mapping[str, set[tuple[str, str]]],
    solid_reference_units: (
        Mapping[tuple[str, str], set[tuple[str, str]]] | None
    ) = None,
) -> dict[str, Any]:
    """Validate assignment and material coverage for generated mesh domains.

    ``mesh_domains`` expands each mesh source into every original geometry object
    represented by that source. ``solid_units`` records the exact solid units that
    require material within the same generated source. The optional alias map lets
    derived multipart domains translate their ``SolidN`` names back to source
    solids without weakening direct-object validation.
    """

    values = tuple(dict(record) for record in records)
    generated_sources: list[str] = []
    for record in values:
        if record.get("category") != "mesh":
            continue
        definition = record.get("definition")
        if not isinstance(definition, Mapping) or definition.get("generated") is not True:
            continue
        for reference in tuple(record.get("references") or ()):
            if not isinstance(reference, Mapping):
                continue
            name = str(reference.get("object_name") or "")
            if name and name not in generated_sources:
                generated_sources.append(name)
    if not generated_sources:
        return {"valid": True, "issue_count": 0, "issues": []}

    covered_objects = set()
    required_solids: set[tuple[str, str]] = set()
    for source_name in generated_sources:
        covered_objects.update(mesh_domains.get(source_name, {source_name}))
        required_solids.update(solid_units.get(source_name, set()))

    issues: list[dict[str, str]] = []
    for record in values:
        if record.get("category") in {"mesh", "mesh_refinement"}:
            continue
        assignment = str(record.get("object_name") or "<unnamed>")
        for reference in tuple(record.get("references") or ()):
            if not isinstance(reference, Mapping):
                continue
            object_name = str(reference.get("object_name") or "")
            if object_name and object_name not in covered_objects:
                issues.append(
                    {
                        "object_name": assignment,
                        "message": (
                            f"{assignment} references {object_name}, which is outside "
                            "every generated mesh domain in this analysis."
                        ),
                    }
                )

    if required_solids:
        material_records = tuple(
            record
            for record in values
            if record.get("category") == "material"
            and record.get("kind") in {"solid", "reinforced"}
        )
        covered_solids: set[tuple[str, str]] = set()
        aliases = solid_reference_units or {}
        for material in material_records:
            references = tuple(material.get("references") or ())
            if not references:
                covered_solids.update(required_solids)
                continue
            for reference in references:
                if not isinstance(reference, Mapping):
                    continue
                object_name = str(reference.get("object_name") or "")
                subelements = tuple(reference.get("subelements") or ())
                if not subelements:
                    covered_solids.update(
                        unit for unit in required_solids if unit[0] == object_name
                    )
                    continue
                for raw in subelements:
                    subelement = str(raw)
                    if "." in subelement:
                        prefix, subelement = subelement.split(".", 1)
                        if prefix != object_name:
                            continue
                    if not subelement.startswith("Solid"):
                        continue
                    token = (object_name, subelement)
                    covered_solids.update(aliases.get(token, {token}))
        for object_name, subelement in sorted(required_solids - covered_solids):
            identity = f"{object_name}.{subelement}"
            issues.append(
                {
                    "object_name": identity,
                    "message": f"{identity} has no solid material in its generated mesh domain.",
                }
            )

    return {
        "valid": not issues,
        "issue_count": len(issues),
        "issues": issues[:64],
    }


def _mesh_domain_coverage(
    source: Any,
) -> tuple[set[str], set[tuple[str, str]], dict[tuple[str, str], set[tuple[str, str]]]]:
    source_name = str(getattr(source, "Name", "") or "")
    covered = {source_name}
    units: set[tuple[str, str]] = set()
    aliases: dict[tuple[str, str], set[tuple[str, str]]] = {}
    is_domain = bool(getattr(source, "VibeCADAnalysisDomain", False))
    members = tuple(getattr(source, "AnalysisSources", ()) or ()) if is_domain else ()
    mode = str(getattr(source, "AnalysisInterfaceMode", "") or "")
    if not members:
        solid_count = len(tuple(getattr(getattr(source, "Shape", None), "Solids", ()) or ()))
        for index in range(1, solid_count + 1):
            token = (source_name, f"Solid{index}")
            units.add(token)
            aliases[token] = {token}
        return covered, units, aliases

    covered.update(str(getattr(member, "Name", "") or "") for member in members)
    if mode == "separate":
        domain_index = 1
        for member in members:
            member_name = str(getattr(member, "Name", "") or "")
            solid_count = len(
                tuple(getattr(getattr(member, "Shape", None), "Solids", ()) or ())
            )
            for member_index in range(1, solid_count + 1):
                token = (member_name, f"Solid{member_index}")
                units.add(token)
                aliases[token] = {token}
                aliases[(source_name, f"Solid{domain_index}")] = {token}
                domain_index += 1
    else:
        solid_count = len(tuple(getattr(getattr(source, "Shape", None), "Solids", ()) or ()))
        for index in range(1, solid_count + 1):
            token = (source_name, f"Solid{index}")
            units.add(token)
            aliases[token] = {token}
    return covered, units, aliases


def validate_assignments(analysis: Any) -> dict[str, Any]:
    document = analysis.Document
    records = assignment_validation_records(analysis)
    issues = []
    for record in records:
        name = str(record.get("object_name") or "<unnamed>")
        if record.get("valid") is False:
            issues.append({"object_name": name, "message": str(record["error"])})
            continue
        obj = document.getObject(name)
        if obj is None or not bool(obj.isValid()):
            issues.append({"object_name": name, "message": f"{name} is invalid."})
            continue
        for reference in record.get("references") or ():
            issue = _reference_issue(document, name, reference)
            if issue:
                issues.append({"object_name": name, "message": issue})
    mesh_domains: dict[str, set[str]] = {}
    solid_units: dict[str, set[tuple[str, str]]] = {}
    solid_aliases: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for record in records:
        if record.get("category") != "mesh":
            continue
        references = tuple(record.get("references") or ())
        if len(references) != 1 or not isinstance(references[0], Mapping):
            continue
        source_name = str(references[0].get("object_name") or "")
        source = document.getObject(source_name) if source_name else None
        if source is None:
            continue
        covered, units, aliases = _mesh_domain_coverage(source)
        mesh_domains[source_name] = covered
        solid_units[source_name] = units
        solid_aliases.update(aliases)
    coverage = validate_assignment_coverage(
        records,
        mesh_domains=mesh_domains,
        solid_units=solid_units,
        solid_reference_units=solid_aliases,
    )
    total_issue_count = len(issues) + int(coverage.get("issue_count", 0) or 0)
    issues.extend(coverage["issues"])
    visible = issues[:64]
    return {
        "valid": total_issue_count == 0,
        "assignment_count": len(records),
        "issue_count": total_issue_count,
        "issues": visible,
        "issues_truncated": total_issue_count > len(visible),
        "mesh_coverage": coverage,
    }


@dataclass(frozen=True, slots=True)
class PreparedAssignmentTarget:
    analysis_target: PreparedAnalysisTarget
    assignment: Any
    record: dict[str, Any]


def prepare_assignment_target(
    document: Any,
    document_uid: str,
    *,
    analysis: Any,
    assignment: Any,
) -> PreparedAssignmentTarget:
    prepared_analysis = prepare_analysis_target(document, document_uid, analysis)
    if not isinstance(assignment, Mapping) or set(assignment) != {
        "object_name",
        "expected_state_sha256",
    }:
        raise NativeAnalyzeError(
            "assignment must contain only object_name and expected_state_sha256."
        )
    object_name = str(assignment["object_name"] or "")
    expected_sha = str(assignment["expected_state_sha256"] or "")
    record = next(
        (
            value
            for value in assignment_records(prepared_analysis.analysis)
            if value.get("object_name") == object_name
        ),
        None,
    )
    if record is None or record.get("valid") is False:
        raise NativeAnalyzeError(
            "The exact target is not a valid assignment in this FEM analysis.",
            error_code=_TARGET_INVALID,
        )
    if record["state_sha256"] != expected_sha:
        raise NativeAnalyzeError(
            "The exact FEM assignment changed after it was read.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "assignment": {"object_name": object_name},
                "current_state_sha256": record["state_sha256"],
            },
        )
    obj = document.getObject(object_name)
    if obj is None:
        raise NativeAnalyzeError("The FEM assignment is no longer available.")
    return PreparedAssignmentTarget(prepared_analysis, obj, record)
