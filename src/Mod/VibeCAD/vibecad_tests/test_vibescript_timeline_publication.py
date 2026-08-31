# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for scripted regeneration in the document timeline."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import VibeCADScriptedPublication as scripted_publication
import VibeCADVibeScriptDomainPublication as domain_publication


def test_every_shipped_domain_has_one_explicit_history_strategy() -> None:
    expected = {
        "partdesign": "design_program_operation",
        "sketcher": "public_outputs",
        "part": "public_outputs",
        "draft": "public_outputs",
        "surface": "public_outputs",
        "assembly": "assembly_resource_graphs",
        "spreadsheet": "public_outputs",
        "material": "material_operations",
        "mesh": "public_outputs",
        "meshpart": "public_outputs",
        "points": "public_outputs",
        "reverse_engineering": "public_outputs",
        "inspection": "public_outputs",
        "robot": "public_outputs",
        "fem": "public_outputs",
        "cam": "cam_job_graphs",
        "techdraw": "techdraw_owner_graphs",
    }

    assert domain_publication._TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN == expected
    assert {
        pack.domain
        for pack in domain_publication.contracts.VIBESCRIPT_WORKBENCH_PACKS.values()
    } == set(expected)


def test_every_runner_owned_write_has_an_explicit_history_lifecycle() -> None:
    packs = tuple(domain_publication.contracts.VIBESCRIPT_WORKBENCH_PACKS.values())
    universal_writes = {
        str(spec["name"])
        for spec in domain_publication.contracts.universal_tool_specs()
        if str(spec["safety"]) == "SAFE_WRITE"
    }
    assert universal_writes == {
        "vibescript.create_program",
        "vibescript.build_program",
        "vibescript.edit_source",
        "vibescript.set_inputs",
        "vibescript.reconfigure_program",
        "vibescript.delete_output",
        "vibescript.delete_program",
        "vibescript.delete_object",
        "vibescript.create_part",
        "vibescript.create_assembly",
    }

    expected_operations = {
        "create_program",
        "set_inputs",
        "reconfigure_program",
        "delete_program",
    }
    registered_writes: set[str] = set(universal_writes)
    contracts: dict[str, str] = {
        "vibescript.create_program": "delegated_domain_strategy",
        "vibescript.build_program": "exact_regeneration",
        "vibescript.edit_source": "exact_regeneration",
        "vibescript.set_inputs": "exact_regeneration",
        "vibescript.reconfigure_program": "exact_regeneration",
        "vibescript.delete_output": "semantic_deletion",
        "vibescript.delete_program": "semantic_deletion",
        "vibescript.delete_object": "semantic_deletion",
        "vibescript.create_part": "delegated_domain_strategy",
        "vibescript.create_assembly": "delegated_domain_strategy",
    }
    for pack in packs:
        domain_specs = {
            str(spec["name"]): spec
            for spec in domain_publication.contracts.domain_tool_specs(pack)
            if str(spec["safety"]) == "SAFE_WRITE"
        }
        assert {name.rpartition(".")[2] for name in domain_specs} == expected_operations
        assert len(domain_specs) == len(expected_operations)
        registered_writes.update(domain_specs)
        strategy = domain_publication._TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN[
            pack.domain
        ]
        contracts[f"vibescript.{pack.domain}.create_program"] = strategy
        contracts[f"vibescript.{pack.domain}.set_inputs"] = "exact_regeneration"
        contracts[f"vibescript.{pack.domain}.reconfigure_program"] = (
            "exact_regeneration"
        )
        contracts[f"vibescript.{pack.domain}.delete_program"] = "semantic_deletion"

    # Ten canonical writes plus four callable compatibility aliases per shipped pack.
    assert len(registered_writes) == 10 + 4 * len(packs) == 78
    assert set(contracts) == registered_writes
    assert set(contracts.values()) <= {
        *domain_publication._TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN.values(),
        "exact_regeneration",
        "semantic_deletion",
        "delegated_domain_strategy",
    }


class _Object:
    def __init__(self, name: str, object_id: int, type_id: str) -> None:
        self.Name = name
        self.ID = object_id
        self.TypeId = type_id
        self.PropertiesList: list[str] = []
        self.InList: list[_Object] = []
        self.Group: list[_Object] = []
        self.Visibility = True
        self._property_types: dict[str, str] = {}
        self._property_status: dict[str, tuple[str, ...]] = {}
        self._editor_modes: dict[str, int] = {}

    def addProperty(
        self,
        property_type: str,
        name: str,
        _group: str = "",
        _description: str = "",
        **_kwargs,
    ) -> None:
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
        self._property_types[name] = property_type
        setattr(self, name, "")

    def add_reference(
        self,
        name: str,
        property_type: str,
        value,
    ) -> None:
        self.PropertiesList.append(name)
        self._property_types[name] = property_type
        setattr(self, name, value)

    def getTypeIdOfProperty(self, name: str) -> str:
        return self._property_types[name]

    def setPropertyStatus(self, name: str, status) -> None:
        self._property_status[name] = tuple(status)

    def getPropertyStatus(self, name: str) -> tuple[str, ...]:
        return self._property_status.get(name, ())

    def setEditorMode(self, name: str, mode: int) -> None:
        self._editor_modes[name] = int(mode)

    def getEditorMode(self, name: str) -> int:
        return self._editor_modes.get(name, 0)


def test_partdesign_program_history_has_exact_edit_and_delete_commands() -> None:
    operation = _Object(
        "ProgramOperation",
        42,
        "PartDesign::DesignScriptOperation",
    )

    domain_publication._set_partdesign_program_history_commands(operation)
    domain_publication._set_partdesign_program_history_commands(operation)

    expected = {
        "VibeCADTimelineEditCommand": "VibeCAD_EditScriptedModel",
        "VibeCADTimelineDeleteCommand": "VibeCAD_DeleteScriptedModel",
    }
    for name, command in expected.items():
        assert operation.getTypeIdOfProperty(name) == "App::PropertyString"
        assert getattr(operation, name) == command
        assert set(operation.getPropertyStatus(name)) == {
            "Hidden",
            "LockDynamic",
            "NoRecompute",
        }
        assert operation.getEditorMode(name) == 2


class _Timeline(_Object):
    def __init__(
        self,
        operations: list[_Object],
        visibility: list[bool],
        position: int,
        suppression: list[bool] | None = None,
    ) -> None:
        super().__init__("VibeCADTimeline", 9000, "App::DocumentTimeline")
        self.PropertiesList = [
            "Operations",
            "VisibilityAtEnd",
            "SuppressionAtEnd",
            "Position",
        ]
        self.Operations = list(operations)
        self.VisibilityAtEnd = list(visibility)
        self.SuppressionAtEnd = list(
            suppression if suppression is not None else [False] * len(operations)
        )
        self.Position = int(position)


class _Document:
    def __init__(self, objects: list[_Object]) -> None:
        self.Objects = list(objects)
        self.staged_segments: list[list[list[_Object]]] = []
        self.segment_replacements: list[list[tuple]] = []
        self.published_blocks: list[tuple[_Object, list[_Object], list[_Object]]] = []

    def stageTimelineOperationSegmentReplacement(
        self,
        segments: list[list[_Object]],
    ) -> None:
        self.staged_segments.append([list(segment) for segment in segments])

    def finalizeProvisionalTimelineOperationSegmentReplacement(
        self,
        mappings: list[tuple],
    ) -> None:
        self.segment_replacements.append(list(mappings))

    def publishProvisionalTimelineOperationBlock(
        self,
        operation: _Object,
        resources: list[_Object],
        owners: list[_Object],
    ) -> None:
        self.published_blocks.append((operation, list(resources), list(owners)))


def _tag_body(body: _Object, output_key: str = "Part") -> None:
    setattr(
        body,
        scripted_publication.PROP_ROLE,
        scripted_publication.ROLE_IMPLEMENTATION,
    )
    setattr(
        body,
        scripted_publication.PROP_ENGINE,
        "vibescript:partdesign",
    )
    setattr(body, scripted_publication.PROP_MODEL_ID, "program-full-id")
    setattr(body, scripted_publication.PROP_OUTPUT_KEY, output_key)


def _old_document(position: int):
    before = _Object("Before", 1, "PartDesign::Feature")
    body = _Object("OldBody", 2, "PartDesign::Body")
    sketch = _Object("OldSketch", 3, "Sketcher::SketchObject")
    feature = _Object("OldFeature", 4, "PartDesign::Feature")
    after = _Object("After", 5, "PartDesign::Feature")
    body.Group = [sketch, feature]
    _tag_body(body)
    operations = [before, body, sketch, feature, after]
    timeline = _Timeline(
        operations,
        [True, True, False, True, True],
        position,
    )
    document = _Document([timeline, *operations])
    captured = domain_publication._capture_partdesign_timeline_replacement(
        document,
        [body, sketch, feature],
    )
    assert captured is not None
    return document, timeline, captured, before, after


def _install_regenerated_body(
    document: _Document,
    timeline: _Timeline,
    before: _Object,
    after: _Object,
    *,
    include_chamfer: bool = False,
) -> dict:
    body = _Object("NewBody", 20, "PartDesign::Body")
    sketch = _Object("OldSketch", 21, "Sketcher::SketchObject")
    feature = _Object("OldFeature", 22, "PartDesign::Feature")
    chamfer = _Object("NewChamfer", 23, "PartDesign::Feature")
    body.Group = [sketch, feature, *([chamfer] if include_chamfer else [])]
    _tag_body(body)
    for operation in body.Group:
        _tag_timeline_operation(operation)
    document.Objects = [
        timeline,
        before,
        after,
        body,
        sketch,
        feature,
        *([chamfer] if include_chamfer else []),
    ]
    blocks = [
        {
            "operation": operation,
            "resources": [],
            "resource_owners": [],
            "ordered": [operation],
            "keys": [operation.Name],
            "root_key": operation.Name,
        }
        for operation in body.Group
    ]
    return {
        "bodies": {"Part": body},
        "timeline_blocks": {"Part": blocks},
    }


def test_document_timeline_is_not_a_scripted_geometry_consumer() -> None:
    target = _Object("GeneratedFeature", 1, "PartDesign::Feature")
    timeline = _Timeline([target], [True], 1)
    timeline._property_types["Operations"] = "App::PropertyLinkListHidden"
    real_consumer = _Object("RealConsumer", 2, "App::FeaturePython")
    real_consumer.add_reference("Source", "App::PropertyLink", target)
    unknown_consumer = _Object("UnknownConsumer", 3, "App::FeaturePython")
    target.InList = [timeline, real_consumer, unknown_consumer]

    uses = scripted_publication.external_reference_uses(
        _Document([target, timeline, real_consumer, unknown_consumer]),
        [target],
    )

    assert {item["owner_name"] for item in uses} == {
        "RealConsumer",
        "UnknownConsumer",
    }
    assert not any(item["owner_name"] == timeline.Name for item in uses)
    assert (
        next(item for item in uses if item["owner_name"] == "UnknownConsumer")[
            "property"
        ]
        == "<native_inbound_reference>"
    )


@pytest.mark.parametrize(
    ("old_position", "expected_active_root_count"),
    (
        (1, -1),
        (3, 1),
        (5, -1),
    ),
)
def test_regeneration_stages_and_replaces_one_exact_segment(
    old_position: int,
    expected_active_root_count: int,
) -> None:
    document, timeline, captured, before, after = _old_document(old_position)
    domain_publication._stage_partdesign_timeline_replacement(
        document,
        captured,
    )
    native_history = _install_regenerated_body(document, timeline, before, after)

    domain_publication._replace_partdesign_timeline_segments(
        document,
        captured,
        native_history,
    )

    assert [
        [obj.Name for obj in segment] for segment in document.staged_segments[0]
    ] == [["OldBody", "OldSketch", "OldFeature"]]
    assert len(document.segment_replacements) == 1
    replacement = document.segment_replacements[0][0]
    assert replacement[0] == 0
    assert [[obj.Name for obj in block] for block in replacement[1]] == [
        ["OldSketch"],
        ["OldFeature"],
    ]
    assert replacement[2] == [1, 2]
    assert replacement[3] == [-1, -1, -1]
    assert replacement[4] == expected_active_root_count
    assert document.published_blocks == []


def test_retired_scripted_output_removes_its_segment() -> None:
    document, timeline, captured, before, after = _old_document(5)
    domain_publication._stage_partdesign_timeline_replacement(
        document,
        captured,
    )

    domain_publication._replace_partdesign_timeline_segments(
        document,
        captured,
        {"bodies": {}, "timeline_blocks": {}},
    )

    replacement = document.segment_replacements[0][0]
    assert replacement == (0, [], [], [-1, -1, -1], -1)


def test_interleaved_generated_history_is_rejected_before_mutation() -> None:
    before = _Object("Before", 1, "PartDesign::Feature")
    body = _Object("Body", 2, "PartDesign::Body")
    sketch = _Object("Sketch", 3, "Sketcher::SketchObject")
    unrelated = _Object("Unrelated", 4, "PartDesign::Feature")
    feature = _Object("Feature", 5, "PartDesign::Feature")
    body.Group = [sketch, feature]
    _tag_body(body)
    timeline = _Timeline(
        [before, body, sketch, unrelated, feature],
        [True, True, False, True, True],
        5,
    )
    document = _Document([timeline, before, body, sketch, unrelated, feature])

    with pytest.raises(RuntimeError, match="interleaved"):
        domain_publication._capture_partdesign_timeline_replacement(
            document,
            [body, sketch, feature],
        )


class _DeletionDocument:
    def __init__(self, objects: list[_Object]) -> None:
        self.Uid = "timeline-deletion-document"
        self.Objects = list(objects)
        self.removed: list[str] = []
        self.finalized: list[tuple[_Object, list[_Object]]] = []
        self.published: list[tuple[_Object, list[_Object], list[_Object]]] = []
        self.resource_stages: list[tuple[_Object, list[_Object]]] = []
        self.resource_finalizations: list[
            tuple[_Object, list[_Object], list[int], list[int]]
        ] = []
        self.semantic_closures: dict[_Object, list[_Object]] = {}
        self.provisional: list[_Object] = []
        for obj in self.Objects:
            obj.Document = self
            obj.ViewObject = SimpleNamespace(Visibility=obj.Visibility)

    def getObject(self, name: str):
        return next(
            (obj for obj in self.Objects if obj.Name == name),
            None,
        )

    def removeObject(self, name: str) -> None:
        obj = self.getObject(name)
        if obj is None:
            return
        self.Objects.remove(obj)
        obj.Document = None
        self.removed.append(name)

    def finalizeProvisionalTimelineOperationBlock(
        self,
        operation: _Object,
        ordered: list[_Object],
    ) -> None:
        self.finalized.append((operation, list(ordered)))

    def isProvisionallyEnrolledInTimelineByCurrentTransaction(
        self,
        obj: _Object,
    ) -> bool:
        return any(candidate is obj for candidate in self.provisional)

    def semanticTimelineCopyClosure(
        self,
        objects: list[_Object],
    ) -> tuple[_Object, ...]:
        assert len(objects) == 1
        return tuple(self.semantic_closures[objects[0]])

    def stageTimelineOperationResourceReconciliation(
        self,
        operation: _Object,
        roots: list[_Object],
    ) -> None:
        self.resource_stages.append((operation, list(roots)))

    def finalizeProvisionalTimelineOperationResourceReconciliation(
        self,
        operation: _Object,
        resources: list[_Object],
        state_sources: list[int],
        consumer_replacements: list[int],
    ) -> None:
        self.resource_finalizations.append(
            (
                operation,
                list(resources),
                list(state_sources),
                list(consumer_replacements),
            )
        )

    def publishProvisionalTimelineOperationBlock(
        self,
        operation: _Object,
        resources: list[_Object],
        owners: list[_Object],
    ) -> None:
        self.published.append((operation, list(resources), list(owners)))


def _tag_timeline_operation(operation: _Object) -> None:
    operation.add_reference(
        "VibeCADTimelineRole",
        "App::PropertyString",
        "operation",
    )


def _tag_resource_key(obj: _Object, key: str) -> None:
    obj.add_reference(
        domain_publication.contracts.PROP_PROGRAM_OUTPUT,
        "App::PropertyString",
        key,
    )


def _tag_assembly_source_identity(
    obj: _Object,
    document_uid: str,
    object_id: int,
    object_name: str,
) -> None:
    obj.add_reference(
        "VibeCADAssemblySourceDocument",
        "App::PropertyString",
        document_uid,
    )
    obj.add_reference(
        "VibeCADAssemblySourceObjectId",
        "App::PropertyInteger",
        object_id,
    )
    obj.add_reference(
        "VibeCADAssemblySourceObjectName",
        "App::PropertyString",
        object_name,
    )


def test_assembly_resource_keys_use_only_persisted_exact_identities() -> None:
    occurrence = _Object("Occurrence", 1, "Assembly::AssemblyLink")
    nested = _Object("NestedOccurrence", 2, "Assembly::AssemblyLink")
    child = _Object("NestedPart", 3, "App::Link")
    helper = _Object("FastenerDefinition", 4, "Part::FeaturePython")
    _DeletionDocument([occurrence, nested, child, helper])
    _tag_timeline_operation(occurrence)
    _tag_timeline_resource(nested, occurrence)
    _tag_timeline_resource(child, nested)
    _tag_timeline_resource(helper, occurrence)
    _tag_assembly_source_identity(nested, "source-doc", 20, "Subassembly")
    _tag_assembly_source_identity(child, "source-doc", 21, "Part")
    _tag_resource_key(helper, "Bolt.__fastener_source")

    assert (
        domain_publication._assembly_timeline_resource_key(
            nested,
            context="nested occurrence",
        )
        == '[["source-doc",20,"Subassembly"]]'
    )
    assert domain_publication._assembly_timeline_resource_key(
        child,
        context="nested part",
    ) == ('[["source-doc",20,"Subassembly"],["source-doc",21,"Part"]]')
    assert (
        domain_publication._assembly_timeline_resource_key(
            helper,
            context="fastener definition",
        )
        == "vibescript:Bolt.__fastener_source"
    )


def test_resource_reconciliation_uses_exact_authored_keys_and_nested_owners() -> None:
    operation = _Object("Job", 1, "Path::FeaturePython")
    old_leaf = _Object("OldBit", 2, "Part::FeaturePython")
    retained_parent = _Object("ToolController", 3, "Path::FeaturePython")
    retired_sibling = _Object("OldStock", 4, "Part::Feature")
    document = _DeletionDocument(
        [old_leaf, retained_parent, retired_sibling, operation]
    )
    _tag_timeline_operation(operation)
    _tag_timeline_resource(old_leaf, retained_parent)
    _tag_timeline_resource(retained_parent, operation)
    _tag_timeline_resource(retired_sibling, operation)
    _tag_resource_key(old_leaf, "job.tool.bit")
    _tag_resource_key(retained_parent, "job.tool")
    _tag_resource_key(retired_sibling, "job.stock")
    document.semantic_closures[operation] = [
        old_leaf,
        retained_parent,
        retired_sibling,
        operation,
    ]

    captured = domain_publication._capture_timeline_resource_reconciliation(
        document,
        operation,
        context="CAM Job",
    )
    domain_publication._stage_timeline_resource_reconciliation(
        document,
        operation,
        captured,
        context="CAM Job",
    )

    replacement_leaf = _Object("ReplacementBit", 5, "Part::FeaturePython")
    replacement_leaf.Document = document
    replacement_leaf.ViewObject = SimpleNamespace(Visibility=True)
    document.Objects.append(replacement_leaf)
    _tag_timeline_resource(replacement_leaf, retained_parent)
    _tag_resource_key(replacement_leaf, "job.tool.bit")
    retired = domain_publication._finalize_timeline_resource_reconciliation(
        document,
        operation,
        captured,
        [retained_parent, replacement_leaf],
        context="CAM Job",
    )

    assert document.resource_stages == [(operation, [retained_parent, retired_sibling])]
    assert document.resource_finalizations == [
        (
            operation,
            [replacement_leaf, retained_parent],
            [0, 1],
            [0, 1, -1],
        )
    ]
    assert retired == [old_leaf, retired_sibling]


def test_new_resource_block_is_published_in_canonical_nested_order() -> None:
    operation = _Object("Projection", 1, "TechDraw::DrawProjGroup")
    parent = _Object("ProjectionFrame", 2, "App::FeaturePython")
    child = _Object("ProjectedItem", 3, "TechDraw::DrawProjGroupItem")
    document = _DeletionDocument([operation, parent, child])
    _tag_timeline_operation(operation)
    _tag_timeline_resource(parent, operation)
    _tag_timeline_resource(child, parent)

    published = domain_publication._publish_new_timeline_resource_block(
        document,
        operation,
        [parent, child],
        context="TechDraw projection",
    )

    assert published == [child.Name, parent.Name, operation.Name]
    assert document.published == [(operation, [child, parent], [parent, operation])]


def test_vibescript_finalizes_new_resource_before_new_operation() -> None:
    resource = _Object("GeneratedResource", 1, "Part::Feature")
    operation = _Object("GeneratedOperation", 2, "Part::Feature")
    document = _DeletionDocument([resource, operation])
    document.provisional = [resource, operation]
    _tag_timeline_operation(operation)
    _tag_timeline_resource(resource, operation)

    finalized = domain_publication._finalize_timeline_resource_block(
        document,
        operation,
        [resource],
        [resource, operation],
    )

    assert finalized == [resource.Name, operation.Name]
    assert document.finalized == [(operation, [resource, operation])]


def test_vibescript_adds_only_new_resource_to_existing_operation() -> None:
    operation = _Object("ExistingOperation", 1, "Part::Feature")
    previous = _Object("ExistingResource", 2, "Part::Feature")
    added = _Object("AddedResource", 3, "Part::Feature")
    document = _DeletionDocument([previous, operation, added])
    document.provisional = [added]
    _tag_timeline_operation(operation)
    _tag_timeline_resource(previous, operation)
    _tag_timeline_resource(added, operation)

    finalized = domain_publication._finalize_timeline_resource_block(
        document,
        operation,
        [previous, added],
        [added],
    )

    assert finalized == [added.Name]
    assert document.finalized == [(operation, [added])]


def test_vibescript_accepts_exact_implicit_resource_for_new_operation() -> None:
    operation = _Object("GeneratedOccurrence", 1, "Assembly::AssemblyLink")
    resource = _Object("MaterializedChild", 2, "App::Link")
    document = _DeletionDocument([operation, resource])
    document.provisional = [operation, resource]
    _tag_timeline_operation(operation)
    _tag_timeline_resource(resource, operation)

    finalized = domain_publication._finalize_timeline_resource_block(
        document,
        operation,
        [resource],
        [operation],
    )

    assert finalized == [resource.Name, operation.Name]
    assert document.finalized == [(operation, [resource, operation])]


def test_vibescript_refuses_unowned_preexisting_resource_before_finalization() -> None:
    operation = _Object("ExistingOperation", 1, "Part::Feature")
    resource = _Object("ExistingResource", 2, "Part::Feature")
    document = _DeletionDocument([operation, resource])
    _tag_timeline_operation(operation)

    with pytest.raises(RuntimeError, match="does not have the exact owner"):
        domain_publication._finalize_timeline_resource_block(
            document,
            operation,
            [resource],
            [],
        )

    assert document.finalized == []


def test_vibescript_deletion_consumes_native_resource_and_reveal_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _Object("Source", 1, "Part::Feature")
    source.Visibility = False
    owner = _Object("Generated", 2, "Part::Feature")
    resource = _Object("GeneratedHelper", 3, "Part::Feature")
    resource.InList = [owner]
    document = _DeletionDocument([source, owner, resource])
    source.ViewObject.Visibility = False

    def deletion_plan(obj):
        assert obj is owner
        return {
            "applicable": True,
            "valid": True,
            "replaced_inputs": [source],
            "objects_to_reveal": [source],
            "owned_resources": [resource],
        }

    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(timelineOperationDeletionPlan=deletion_plan),
    )
    remove_object = document.removeObject

    def remove_with_live_owner(name: str) -> None:
        if name == resource.Name:
            assert document.getObject(owner.Name) is owner
        remove_object(name)

    document.removeObject = remove_with_live_owner

    removed = domain_publication._remove_owned_objects(
        document,
        [owner],
    )

    assert removed == ["GeneratedHelper", "Generated"]
    assert document.removed == ["GeneratedHelper", "Generated"]
    assert document.getObject("Generated") is None
    assert document.getObject("GeneratedHelper") is None
    assert document.getObject("Source") is source
    assert source.ViewObject.Visibility is True


def test_vibescript_deletion_prefers_headless_app_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Object("Generated", 2, "Part::Feature")
    document = _DeletionDocument([owner])
    calls: list[_Object] = []

    def deletion_plan(obj):
        calls.append(obj)
        return {
            "applicable": False,
            "valid": True,
            "replaced_inputs": [],
            "objects_to_reveal": [],
            "owned_resources": [],
        }

    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(timelineOperationDeletionPlan=deletion_plan),
    )
    monkeypatch.setitem(
        sys.modules,
        "FreeCADGui",
        SimpleNamespace(
            timelineOperationDeletionPlan=lambda _obj: (_ for _ in ()).throw(
                AssertionError("GUI planner must not be used when App exposes it")
            )
        ),
    )

    removed = domain_publication._remove_owned_objects(document, [owner])

    assert removed == [owner.Name]
    assert calls == [owner]


def test_vibescript_deletion_blocks_malformed_native_history_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Object("Generated", 2, "Part::Feature")
    document = _DeletionDocument([owner])
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(
            timelineOperationDeletionPlan=lambda _obj: {
                "applicable": True,
                "valid": False,
                "replaced_inputs": [],
                "objects_to_reveal": [],
                "owned_resources": [],
            }
        ),
    )

    with pytest.raises(RuntimeError, match="malformed"):
        domain_publication._remove_owned_objects(document, [owner])

    assert document.getObject("Generated") is owner
    assert document.removed == []


def _tag_timeline_resource(resource: _Object, owner: _Object) -> None:
    resource.add_reference(
        "VibeCADTimelineRole",
        "App::PropertyString",
        "resource",
    )
    resource.add_reference(
        "VibeCADTimelineOwner",
        "App::PropertyLinkHidden",
        owner,
    )


def test_vibescript_deletion_refuses_resource_without_its_semantic_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Object("GeneratedOperation", 2, "Part::Feature")
    owner.Label = "Generated operation"
    resource = _Object("GeneratedResult", 3, "Part::Feature")
    _tag_timeline_resource(resource, owner)
    document = _DeletionDocument([owner, resource])
    planner_calls: list[_Object] = []

    def deletion_plan(obj):
        planner_calls.append(obj)
        return {
            "applicable": False,
            "valid": True,
            "replaced_inputs": [],
            "objects_to_reveal": [],
            "owned_resources": [],
        }

    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(timelineOperationDeletionPlan=deletion_plan),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "belongs to the history operation 'Generated operation'.*History instead"
        ),
    ):
        domain_publication._remove_owned_objects(document, [resource])

    assert planner_calls == []
    assert document.getObject(owner.Name) is owner
    assert document.getObject(resource.Name) is resource
    assert document.removed == []


def test_vibescript_deletion_accepts_resource_with_its_semantic_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Object("GeneratedOperation", 2, "Part::Feature")
    resource = _Object("GeneratedResult", 3, "Part::Feature")
    _tag_timeline_resource(resource, owner)
    document = _DeletionDocument([owner, resource])

    def deletion_plan(obj):
        if obj is owner:
            return {
                "applicable": True,
                "valid": True,
                "replaced_inputs": [],
                "objects_to_reveal": [],
                "owned_resources": [resource],
            }
        assert obj is resource
        return {
            "applicable": False,
            "valid": True,
            "replaced_inputs": [],
            "objects_to_reveal": [],
            "owned_resources": [],
        }

    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(timelineOperationDeletionPlan=deletion_plan),
    )
    remove_object = document.removeObject

    def remove_with_live_owner(name: str) -> None:
        if name == resource.Name:
            assert document.getObject(owner.Name) is owner
        remove_object(name)

    document.removeObject = remove_with_live_owner

    removed = domain_publication._remove_owned_objects(
        document,
        [resource, owner],
    )

    assert removed == [resource.Name, owner.Name]
    assert document.removed == [resource.Name, owner.Name]
    assert document.Objects == []
