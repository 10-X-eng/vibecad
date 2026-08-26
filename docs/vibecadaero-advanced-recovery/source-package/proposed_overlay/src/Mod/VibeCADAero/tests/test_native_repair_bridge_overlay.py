import pytest
from AeroNativeRepairBridge import capture, validate_apply


def test_repair_apply_rejects_native_revision_change():
    snap = capture(document_uid="doc", native_revision=4, geometry_revision="g", intent_rows=[])
    with pytest.raises(ValueError, match="native_revision_stale"):
        validate_apply(snap, current_native_revision=5, current_geometry_revision="g", current_intent_rows=[])


def test_repair_apply_preserves_user_explicit_intent():
    before = [{"kind":"user_explicit", "key":"target_mass", "value":1.5}]
    snap = capture(document_uid="doc", native_revision=4, geometry_revision="g", intent_rows=before)
    validate_apply(snap, current_native_revision=4, current_geometry_revision="g", current_intent_rows=before)
    after = [{"kind":"user_explicit", "key":"target_mass", "value":2.0}]
    with pytest.raises(ValueError, match="user_explicit_intent_changed"):
        validate_apply(snap, current_native_revision=4, current_geometry_revision="g", current_intent_rows=after)
