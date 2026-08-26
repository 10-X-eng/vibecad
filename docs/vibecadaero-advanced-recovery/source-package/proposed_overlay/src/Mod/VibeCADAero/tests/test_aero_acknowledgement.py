import AeroAcknowledgement as ack


class FakePreferences:
    def __init__(self):
        self.values = {}

    def GetBool(self, key, default=False):
        return bool(self.values.get(key, default))

    def SetBool(self, key, value):
        self.values[key] = bool(value)


def test_aero_acknowledgement_is_single_persistent_unversioned_flag():
    store = FakePreferences()
    before = ack.first_use_state(store)
    assert before["show_notice"] is True
    assert before["versioned"] is False
    assert "license terms separate from VibeCAD" in before["product_license_notice"]
    assert "ownership of CAD designs created in VibeCAD" in before["product_license_notice"]
    assert before["checkbox_text"] == "I understand."

    ack.acknowledge(store)
    after = ack.first_use_state(store)
    assert after["show_notice"] is False
    assert after["acknowledged"] is True
    assert store.values == {ack.ACKNOWLEDGEMENT_KEY: True}


def test_acknowledgement_text_is_informational_not_compliance_agreement():
    text = ack.ACKNOWLEDGEMENT_TEXT.lower()
    assert "agree to comply" not in text
    assert "commercial" not in text
    assert "military" not in text
    assert "license eligibility" not in text
    assert text == "i understand."
