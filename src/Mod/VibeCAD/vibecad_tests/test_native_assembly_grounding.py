# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyComponents as components_module
import VibeCADNativeAssemblySnapshot as snapshot_module
from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeAssemblyGrounding import (
    GroundingSpec,
    GroundingTargetSpec,
    NativeAssemblyGroundingError,
    active_grounded_joints,
    apply_grounding,
    preflight_grounding,
    verify_grounding,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeTargets import NativeObjectRef
from VibeCADRibbonSurface import RibbonSurface


@pytest.fixture(autouse=True)
def _active_timeline(monkeypatch):
    monkeypatch.setattr(components_module, "_timeline_active", lambda _obj: True)


class _Object:
    def __init__(self, document, name: str, type_id: str, parent=None) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.ID = document.next_id
        document.next_id += 1
        self.Group = []
        self.InList = []
        self.PropertiesList = [] if type_id == "Assembly::JointGroup" else ["Placement"]
        self._statuses: dict[str, set[str]] = {}
        self.ViewObject = SimpleNamespace(Proxy=None, isInEditMode=lambda: True)
        self.VibeCADTimelineRole = "operation"
        self._parent = parent

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected or (
            self.TypeId == "Assembly::AssemblyObject"
            and expected == "App::Part"
        )

    def newObject(self, type_id: str, base_name: str):
        return self.Document._create(type_id, base_name, self)

    def hasObject(self, candidate, recursive: bool = False) -> bool:
        if candidate in self.Group:
            return True
        return recursive and any(
            child.hasObject(candidate, True)
            for child in self.Group
            if hasattr(child, "hasObject")
        )

    def getPropertyStatus(self, name: str):
        return tuple(sorted(self._statuses.get(name, set())))

    def setPropertyStatus(self, name: str, status: str) -> None:
        values = self._statuses.setdefault(name, set())
        if status.startswith("-"):
            values.discard(status[1:])
        else:
            values.add(status)

    def isValid(self) -> bool:
        return self.Document.getObject(self.Name) is self

    def isPartGrounded(self, component) -> bool:
        if self.TypeId != "Assembly::AssemblyObject":
            return False
        joint_group = next(
            child for child in self.Group if child.TypeId == "Assembly::JointGroup"
        )
        return any(
            getattr(joint, "ObjectToGround", None) is component
            for joint in joint_group.Group
        )


class _Document:
    def __init__(self) -> None:
        self.Name = "GroundingDocument"
        self.Uid = "grounding-document"
        self.Objects = []
        self.next_id = 1

    def _unique_name(self, base_name: str) -> str:
        if self.getObject(base_name) is None:
            return base_name
        index = 1
        while self.getObject(f"{base_name}{index:03d}") is not None:
            index += 1
        return f"{base_name}{index:03d}"

    def _create(self, type_id: str, base_name: str, parent=None):
        obj = _Object(self, self._unique_name(base_name), type_id, parent)
        self.Objects.append(obj)
        if parent is not None:
            parent.Group.append(obj)
            obj.InList.append(parent)
        return obj

    def addObject(self, type_id: str, base_name: str):
        return self._create(type_id, base_name)

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def removeObject(self, name: str) -> None:
        obj = self.getObject(name)
        if obj is None:
            return
        component = getattr(obj, "ObjectToGround", None)
        if component is not None:
            component.setPropertyStatus("Placement", "-ReadOnly")
        for parent in tuple(obj.InList):
            if obj in parent.Group:
                parent.Group.remove(obj)
        self.Objects.remove(obj)


def _fixture(component_count: int = 2):
    document = _Document()
    assembly = document.addObject("Assembly::AssemblyObject", "Assembly")
    joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
    components = tuple(
        assembly.newObject("App::Link", f"Component{index + 1}")
        for index in range(component_count)
    )

    def active(_document):
        return assembly

    def owns(owner, component):
        return owner is assembly and owner.hasObject(component, True)

    def factory(component, owner):
        assert owner is assembly
        joint = joint_group.newObject("App::FeaturePython", "GroundedJoint")
        joint.ObjectToGround = component
        joint.VibeCADTimelineRole = "operation"
        component.setPropertyStatus("Placement", "ReadOnly")
        joint.ViewObject.Proxy = "grounded-view"
        return joint

    return document, assembly, joint_group, components, active, owns, factory


def _spec(
    document,
    assembly,
    components,
    *,
    grounded: bool,
    expected_grounded: bool,
    expected_grounded_count: int,
) -> GroundingSpec:
    return GroundingSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        targets=tuple(
            GroundingTargetSpec(
                NativeObjectRef(document.Uid, component.Name),
                expected_grounded,
            )
            for component in components
        ),
        grounded=grounded,
        expected_component_count=len(components),
        expected_grounded_count=expected_grounded_count,
    )


def test_joint_schema_is_exact_bounded_and_registered() -> None:
    definition = assembly_joint_capability_definition()
    variant = definition.variants[0]
    schema = definition.provider_schema(("set_grounded",))["parameters"]["oneOf"][0]

    assert definition.name == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert variant.operation == "set_grounded"
    assert variant.action_ids == frozenset({"Assembly_ToggleGrounded"})
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "targets",
        "grounded",
        "expected_component_count",
        "expected_grounded_count",
    }
    assert schema["properties"]["targets"]["maxItems"] == 16
    assert schema["additionalProperties"] is False
    registry = build_native_capability_registry()
    assert registry.definition(ASSEMBLY_JOINT_CAPABILITY_NAME) is not None
    assert registry.implementation(ASSEMBLY_JOINT_CAPABILITY_NAME) is not None


def test_ground_action_maps_to_desired_state_joint_operation() -> None:
    surface = RibbonSurface.from_manifest(
        {
            "schema_version": 1,
            "surface_id": "assemble",
            "groups": [
                {
                    "label": "Joints",
                    "actions": [
                        {
                            "command_id": "Assembly_ToggleGrounded",
                            "kind": "command",
                            "label": "Grounding",
                            "available": True,
                        }
                    ],
                }
            ],
        },
        revision=1,
    )

    plan = classify_native_surface(surface)[0]

    assert plan.capability_family == ASSEMBLY_JOINT_CAPABILITY_NAME
    assert plan.operation_variant == "set_grounded"
    assert plan.transaction_behavior == "document"


def test_batch_ground_and_unground_are_exact_desired_state_mutations() -> None:
    document, assembly, joint_group, components, active, owns, factory = _fixture()
    ground_spec = _spec(
        document,
        assembly,
        components,
        grounded=True,
        expected_grounded=False,
        expected_grounded_count=0,
    )
    ground = apply_grounding(
        document,
        ground_spec,
        active_reader=active,
        timeline_active=lambda _obj: True,
        ownership_checker=owns,
        joint_factory=factory,
    )
    grounded = verify_grounding(
        document,
        ground,
        active_reader=active,
        timeline_active=lambda _obj: True,
        ownership_checker=owns,
        view_checker=lambda joint: joint.ViewObject.Proxy == "grounded-view",
    )

    assert grounded["grounded"] is True
    assert grounded["grounded_count"] == 2
    assert len(ground.created) == 2
    assert not ground.deleted
    assert all(
        "ReadOnly" in component.getPropertyStatus("Placement")
        for component in components
    )

    unground_spec = _spec(
        document,
        assembly,
        components,
        grounded=False,
        expected_grounded=True,
        expected_grounded_count=2,
    )
    unground = apply_grounding(
        document,
        unground_spec,
        active_reader=active,
        timeline_active=lambda _obj: True,
        ownership_checker=owns,
        joint_factory=factory,
    )
    ungrounded = verify_grounding(
        document,
        unground,
        active_reader=active,
        timeline_active=lambda _obj: True,
        ownership_checker=owns,
        view_checker=lambda _joint: True,
    )

    assert ungrounded["grounded"] is False
    assert ungrounded["grounded_count"] == 0
    assert not unground.created
    assert len(unground.deleted) == 2
    assert active_grounded_joints(
        joint_group,
        timeline_active=lambda _obj: True,
    ) == ()
    assert all(
        "ReadOnly" not in component.getPropertyStatus("Placement")
        for component in components
    )


def test_preflight_rejects_stale_noop_duplicate_and_malformed_grounding() -> None:
    document, assembly, joint_group, components, active, owns, factory = _fixture()
    before = tuple(document.Objects)
    stale = _spec(
        document,
        assembly,
        components[:1],
        grounded=True,
        expected_grounded=False,
        expected_grounded_count=1,
    )
    stale = GroundingSpec(
        stale.assembly_ref,
        stale.targets,
        stale.grounded,
        expected_component_count=2,
        expected_grounded_count=1,
    )
    with pytest.raises(NativeAssemblyGroundingError, match="grounded count changed"):
        preflight_grounding(
            document,
            stale,
            active_reader=active,
            timeline_active=lambda _obj: True,
            ownership_checker=owns,
        )
    assert tuple(document.Objects) == before

    target = GroundingTargetSpec(
        NativeObjectRef(document.Uid, components[0].Name),
        False,
    )
    duplicate = GroundingSpec(
        NativeObjectRef(document.Uid, assembly.Name),
        (target, target),
        True,
        2,
        0,
    )
    with pytest.raises(NativeAssemblyGroundingError, match="repeat"):
        preflight_grounding(
            document,
            duplicate,
            active_reader=active,
            timeline_active=lambda _obj: True,
            ownership_checker=owns,
        )

    joint = factory(components[0], assembly)
    no_op = GroundingSpec(
        NativeObjectRef(document.Uid, assembly.Name),
        (
            GroundingTargetSpec(
                NativeObjectRef(document.Uid, components[0].Name),
                True,
            ),
        ),
        True,
        2,
        1,
    )
    with pytest.raises(NativeAssemblyGroundingError, match="must change"):
        preflight_grounding(
            document,
            no_op,
            active_reader=active,
            timeline_active=lambda _obj: True,
            ownership_checker=owns,
        )

    duplicate_joint = joint_group.newObject("App::FeaturePython", "GroundedJoint")
    duplicate_joint.ObjectToGround = components[0]
    with pytest.raises(NativeAssemblyGroundingError, match="duplicate active"):
        preflight_grounding(
            document,
            GroundingSpec(
                no_op.assembly_ref,
                no_op.targets,
                False,
                2,
                2,
            ),
            active_reader=active,
            timeline_active=lambda _obj: True,
            ownership_checker=owns,
        )
    assert joint in joint_group.Group


def test_assemble_snapshot_exposes_exact_component_grounding(monkeypatch) -> None:
    document, assembly, _joint_group, components, _active, _owns, factory = _fixture()
    joint = factory(components[0], assembly)
    monkeypatch.setattr(snapshot_module, "read_active_assembly", lambda _doc: assembly)
    monkeypatch.setattr(snapshot_module, "_timeline_active", lambda _obj: True)
    monkeypatch.setattr(
        snapshot_module,
        "active_grounded_joints",
        lambda group: tuple(
            child for child in group.Group if hasattr(child, "ObjectToGround")
        ),
    )

    snapshot = build_assembly_snapshot(document)
    summary = next(
        item for item in snapshot["assemblies"] if item["object_name"] == assembly.Name
    )

    assert summary["counts"]["grounded"] == 1
    assert summary["components"][0]["grounded"] is True
    assert summary["components"][0]["grounded_joint"]["object_name"] == joint.Name
    assert summary["components"][1]["grounded"] is False
    assert summary["components"][1]["grounded_joint"] is None
