from pathlib import Path
from AeroDetachedExecution import AttachmentGuard, can_attach, freeze_directory


def test_detached_input_hash_is_path_and_content_stable(tmp_path: Path):
    (tmp_path / "case.json").write_text('{"a":1}', encoding="utf-8")
    frozen = freeze_directory(tmp_path)
    assert frozen.file_count == 1
    assert len(frozen.sha256) == 64


def test_detached_result_attachment_requires_exact_frozen_state():
    guard = AttachmentGuard("i", 3, "g", "c")
    assert can_attach(guard, input_sha256="i", native_revision=3, geometry_revision="g", case_sha256="c")
    assert not can_attach(guard, input_sha256="i", native_revision=4, geometry_revision="g", case_sha256="c")
