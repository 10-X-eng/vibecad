from AeroNativeBridge import AttachmentState, capture_authority, decide_attachment


class State:
    def __init__(self, revision: int):
        self.revision = revision
    def current_revision(self, _document_uid: str) -> int:
        return self.revision


def test_authority_snapshot_and_stale_decisions() -> None:
    snap = capture_authority(State(7), document_uid="doc-1", geometry_revision="geom-a")
    current = decide_attachment(snap, current_native_revision=7, current_geometry_revision="geom-a")
    assert current.current is True
    assert current.state is AttachmentState.CURRENT
    stale = decide_attachment(snap, current_native_revision=8, current_geometry_revision="geom-b")
    assert stale.current is False
    assert stale.preserve_as_history is True
    assert stale.state is AttachmentState.STALE_BOTH
