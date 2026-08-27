# SPDX-License-Identifier: LGPL-2.1-or-later

"""Complete authority-policy census projected from the Native registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


POLICY_CLASSES = frozenset({
    "read_only",
    "presentation_change",
    "safe_immediate_mutation",
    "preview_required",
    "explicit_confirmation_required",
    "human_authorized_export",
    "external_side_effect",
    "privileged_compatibility_execution",
})


@dataclass(frozen=True, slots=True)
class NativeAuthorityPolicy:
    capability: str
    operation: str
    policy_class: str
    reason: str
    mutation_owner: str
    transaction_behavior: str
    currentness_inputs: tuple[str, ...]
    effect_evidence: tuple[str, ...]
    rollback_behavior: str
    test_owner: str

    def __post_init__(self) -> None:
        if self.policy_class not in POLICY_CLASSES:
            raise ValueError(f"Unknown Native authority policy {self.policy_class!r}.")
        for name in ("capability", "operation", "reason", "mutation_owner",
                     "transaction_behavior", "rollback_behavior", "test_owner"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must be non-empty.")
        if not self.currentness_inputs or not self.effect_evidence:
            raise ValueError("Authority policy requires currentness and effect evidence.")


def _stage_values(parameters: Mapping[str, Any]) -> frozenset[str]:
    properties = parameters.get("properties")
    stage = properties.get("stage") if isinstance(properties, Mapping) else None
    if not isinstance(stage, Mapping):
        return frozenset()
    values = stage.get("enum")
    if not isinstance(values, list):
        value = stage.get("const")
        values = [value] if value is not None else []
    return frozenset(str(value) for value in values)


def _classify(
    definition: NativeCapabilityDefinition,
    variant: NativeCapabilityVariant,
) -> str:
    primary = definition.primary_classification
    if primary == "read":
        return "read_only"
    if primary == "view":
        return "presentation_change"
    if primary == "export":
        return "human_authorized_export"
    if {"propose", "apply"} <= _stage_values(variant.parameters):
        return "preview_required"
    if variant.transaction_behavior in {"background", "background_output"}:
        return "explicit_confirmation_required"
    if variant.transaction_behavior == "output":
        return "external_side_effect"
    return "safe_immediate_mutation"


def _policy(
    definition: NativeCapabilityDefinition,
    variant: NativeCapabilityVariant,
) -> NativeAuthorityPolicy:
    policy_class = _classify(definition, variant)
    owner = definition.name.split(".", 1)[0]
    currentness = ["frozen Native surface", "document structural revision"]
    if variant.exact_target_type:
        currentness.append("exact target identity and type")
    if policy_class == "preview_required":
        currentness.extend(("preview identity", "preview dependency snapshot"))
    evidence = ["Native receipt", "structural revision after dispatch"]
    if policy_class == "read_only":
        evidence = ["bounded response", "unchanged structural revision"]
    elif policy_class == "presentation_change":
        evidence = ["presentation receipt", "unchanged structural revision"]
    elif policy_class in {"human_authorized_export", "external_side_effect"}:
        evidence = ["authorized destination", "output descriptor or effect receipt"]
    rollback = {
        "read_only": "No mutation; rollback is not applicable.",
        "presentation_change": "Presentation owner restores or replaces view state.",
        "preview_required": "Reject or expiry leaves accepted state unchanged; apply uses the existing document transaction.",
        "human_authorized_export": "External output is not document undo; failure retains no publication claim.",
        "external_side_effect": "Effect owner reports partial/unknown outcome; never infer rollback.",
        "explicit_confirmation_required": "Cancellation precedes commit; committed document effects use the existing transaction owner.",
        "safe_immediate_mutation": "Existing Native document transaction and undo behavior remain authoritative.",
    }[policy_class]
    return NativeAuthorityPolicy(
        capability=definition.name,
        operation=variant.operation,
        policy_class=policy_class,
        reason=f"Derived from primary class {definition.primary_classification!r}, transaction {variant.transaction_behavior!r}, and the frozen operation schema.",
        mutation_owner=owner,
        transaction_behavior=variant.transaction_behavior,
        currentness_inputs=tuple(currentness),
        effect_evidence=tuple(evidence),
        rollback_behavior=rollback,
        test_owner=f"{definition.name}:{variant.operation}",
    )


def build_native_authority_census(
    registry: NativeCapabilityRegistry,
) -> tuple[NativeAuthorityPolicy, ...]:
    """Return exactly one policy for every operation plus privileged `/v1/run`."""

    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    records = []
    for name in registry.definition_names:
        definition = registry.definition(name)
        if definition is None:
            raise AssertionError(f"Registry definition {name!r} disappeared.")
        records.extend(_policy(definition, variant) for variant in definition.variants)
    records.append(NativeAuthorityPolicy(
        capability="agent.compatibility_run",
        operation="/v1/run",
        policy_class="privileged_compatibility_execution",
        reason="Privileged local Python compatibility route; it is not a safe Native mutation path.",
        mutation_owner="agent-control compatibility owner",
        transaction_behavior="privileged utility execution",
        currentness_inputs=("active local control session", "route request identity"),
        effect_evidence=("bounded route response", "compatibility execution log"),
        rollback_behavior="No generic rollback claim; guarded CAD and Aero mutation families remain refused.",
        test_owner="test_agent_control_grok_bot.py",
    ))
    records.append(NativeAuthorityPolicy(
        capability="agent.prompt",
        operation="/v1/prompt",
        policy_class="external_side_effect",
        reason="Starts an external model/provider interaction through the local agent control owner.",
        mutation_owner="agent-control prompt owner",
        transaction_behavior="external provider interaction",
        currentness_inputs=("active local control session", "prompt request identity"),
        effect_evidence=("provider response or error", "bounded request correlation"),
        rollback_behavior="External provider effects are not rolled back; unknown outcomes remain explicit.",
        test_owner="test_agent_control_grok_bot.py",
    ))
    keys = tuple((item.capability, item.operation) for item in records)
    if len(keys) != len(set(keys)):
        raise ValueError("Native authority census contains duplicate operation policies.")
    return tuple(records)
