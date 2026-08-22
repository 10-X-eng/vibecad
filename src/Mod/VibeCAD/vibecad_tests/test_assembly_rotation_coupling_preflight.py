# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from vibescript_domain_api import DomainValue


def _component(name: str, *, grounded: bool = False) -> DomainValue:
    return DomainValue(
        domain="assembly",
        operation="component",
        output_type="component_link",
        arguments=({"document_uid": "document", "object_name": name},),
        properties={"grounded": grounded},
    )


def _connector(component: DomainValue, interface: str) -> DomainValue:
    return DomainValue(
        domain="assembly",
        operation="connector",
        output_type="connector",
        arguments=(component,),
        properties={
            "selection": {
                "type": "published_interface",
                "interface_name": interface,
            },
            "occurrence_path": None,
            "anchor": None,
            "offset": {
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            },
        },
    )


def _joint(kind: str, first: DomainValue, second: DomainValue) -> DomainValue:
    return DomainValue(
        domain="assembly",
        operation="joint",
        output_type="joint",
        arguments=(first, second),
        properties={"kind": kind, "suppressed": False},
    )


def _outputs(values: list[DomainValue], prefix: str) -> dict[int, str]:
    return {id(value): f"{prefix}{index}" for index, value in enumerate(values)}


def test_worker_preflight_accepts_two_exact_revolute_dependencies() -> None:
    from vibescript_assembly_worker import _preflight_coupled_joint_structure

    base = _component("Base", grounded=True)
    first_gear = _component("FirstGear")
    second_gear = _component("SecondGear")
    first_axis = _connector(first_gear, "Axis")
    second_axis = _connector(second_gear, "Axis")
    joints = [
        _joint("revolute", _connector(base, "FirstBearing"), first_axis),
        _joint("revolute", _connector(base, "SecondBearing"), second_axis),
        _joint(
            "gears",
            _connector(first_gear, "Axis"),
            _connector(second_gear, "Axis"),
        ),
    ]
    components = [base, first_gear, second_gear]

    assert _preflight_coupled_joint_structure(
        joints,
        component_outputs=_outputs(components, "Component"),
        joint_outputs=_outputs(joints, "Joint"),
    ) == []


def test_worker_preflight_rejects_grounded_or_unmatched_gear_endpoints() -> None:
    from vibescript_assembly_worker import _preflight_coupled_joint_structure

    base = _component("Base", grounded=True)
    gear = _component("Gear")
    coupling = _joint(
        "gears",
        _connector(base, "RingAxis"),
        _connector(gear, "GearAxis"),
    )

    issues = _preflight_coupled_joint_structure(
        [coupling],
        component_outputs={id(base): "Base", id(gear): "Gear"},
        joint_outputs={id(coupling): "Coupling"},
    )

    assert issues == [
        {
            "code": "invalid_revolute_dependencies",
            "joint_output": "Coupling",
            "joint_type": "gears",
            "grounded_component_outputs": ["Base"],
            "endpoints": [
                {
                    "component_output": "Base",
                    "selection": {
                        "type": "published_interface",
                        "interface_name": "RingAxis",
                    },
                    "revolute_joint_outputs": [],
                },
                {
                    "component_output": "Gear",
                    "selection": {
                        "type": "published_interface",
                        "interface_name": "GearAxis",
                    },
                    "revolute_joint_outputs": [],
                },
            ],
            "requirement": (
                "Each connector must exactly reuse one distinct non-suppressed "
                "Revolute joint connector; neither coupled component may be grounded."
            ),
            "suggestion": (
                "Create one Revolute joint for each rotating component, then reuse "
                "those exact connectors in the gears joint."
            ),
        }
    ]


def test_worker_preflight_requires_the_same_interface_and_distinct_revolutes() -> None:
    from vibescript_assembly_worker import _preflight_coupled_joint_structure

    first = _component("First")
    second = _component("Second")
    shared_revolute = _joint(
        "revolute",
        _connector(first, "Axis"),
        _connector(second, "Axis"),
    )
    different_interface = _joint(
        "gears",
        _connector(first, "OtherAxis"),
        _connector(second, "Axis"),
    )
    joints = [shared_revolute, different_interface]

    issues = _preflight_coupled_joint_structure(
        joints,
        component_outputs={id(first): "First", id(second): "Second"},
        joint_outputs={
            id(shared_revolute): "SharedRevolute",
            id(different_interface): "Coupling",
        },
    )

    assert issues[0]["endpoints"] == [
        {
            "component_output": "First",
            "selection": {
                "type": "published_interface",
                "interface_name": "OtherAxis",
            },
            "revolute_joint_outputs": [],
        },
        {
            "component_output": "Second",
            "selection": {
                "type": "published_interface",
                "interface_name": "Axis",
            },
            "revolute_joint_outputs": ["SharedRevolute"],
        },
    ]


def test_worker_preflight_accepts_a_rotating_bearing_support() -> None:
    from vibescript_assembly_worker import _preflight_coupled_joint_structure

    base = _component("Base", grounded=True)
    carrier = _component("Carrier")
    first = _component("First")
    second = _component("Second")
    joints = [
        _joint(
            "revolute",
            _connector(base, "CarrierAxis"),
            _connector(carrier, "Axis"),
        ),
        _joint(
            "revolute",
            _connector(carrier, "FirstAxis"),
            _connector(first, "Axis"),
        ),
        _joint(
            "revolute",
            _connector(base, "SecondAxis"),
            _connector(second, "Axis"),
        ),
        _joint(
            "gears",
            _connector(first, "Axis"),
            _connector(second, "Axis"),
        ),
    ]
    components = [base, carrier, first, second]

    issues = _preflight_coupled_joint_structure(
        joints,
        component_outputs=_outputs(components, "Component"),
        joint_outputs=_outputs(joints, "Joint"),
    )

    assert issues == []


def test_native_readback_rechecks_rotational_dependencies_before_solving() -> None:
    from vibescript_assembly_worker import _coupled_joint_issues

    def frame(component: str, interface: str, x_mm: float = 0.0) -> dict:
        return {
            "component_output": component,
            "selection": {
                "type": "published_interface",
                "interface_name": interface,
            },
            "occurrence_path": None,
            "anchor": "",
            "offset": {"matrix": [1.0, 0.0, 0.0, 0.0] * 4},
            "local_frame": {"matrix": [1.0, 0.0, 0.0, 0.0] * 4},
            "global_frame": {"position_mm": [x_mm, 0.0, 0.0]},
        }

    first_axis = frame("FirstGear", "Axis")
    second_axis = frame("SecondGear", "Axis", 60.0)
    valid = {
        "FirstBearing": {
            "kind": "revolute",
            "suppressed": False,
            "connectors": [frame("Base", "FirstBearing"), first_axis],
        },
        "SecondBearing": {
            "kind": "revolute",
            "suppressed": False,
            "connectors": [frame("Base", "SecondBearing"), second_axis],
        },
        "Coupling": {
            "kind": "gears",
            "suppressed": False,
            "connectors": [
                frame("FirstGear", "Axis"),
                frame("SecondGear", "Axis", 60.0),
            ],
        },
    }

    assert _coupled_joint_issues(valid, grounded_outputs={"Base"}) == []

    invalid = dict(valid)
    invalid["Coupling"] = {
        "kind": "gears",
        "suppressed": False,
        "connectors": [
            frame("Base", "FirstBearing"),
            frame("SecondGear", "Axis", 60.0),
        ],
    }
    issues = _coupled_joint_issues(invalid, grounded_outputs={"Base"})

    assert issues[0]["code"] == "invalid_revolute_dependencies"
    assert issues[0]["grounded_component_outputs"] == ["Base"]


def test_native_readback_rejects_coincident_rotational_coupling_axes() -> None:
    from vibescript_assembly_worker import _coupled_joint_issues

    def frame(component: str) -> dict:
        return {
            "component_output": component,
            "selection": {
                "type": "published_interface",
                "interface_name": "Axis",
            },
            "occurrence_path": None,
            "anchor": "",
            "offset": {},
            "local_frame": {},
            "global_frame": {"position_mm": [0.0, 0.0, 0.0]},
        }

    first = frame("FirstGear")
    second = frame("SecondGear")
    joint_data = {
        "FirstBearing": {
            "kind": "revolute",
            "suppressed": False,
            "connectors": [frame("Base"), first],
        },
        "SecondBearing": {
            "kind": "revolute",
            "suppressed": False,
            "connectors": [frame("Carrier"), second],
        },
        "Coupling": {
            "kind": "gears",
            "suppressed": False,
            "connectors": [frame("FirstGear"), frame("SecondGear")],
        },
    }

    issues = _coupled_joint_issues(joint_data, grounded_outputs={"Base"})

    assert issues[0]["code"] == "coincident_coupling_axes"
    assert issues[0]["joint_output"] == "Coupling"
    assert issues[0]["axis_separation_mm"] == 0.0
