# SPDX-License-Identifier: LGPL-2.1-or-later

"""Prove portable VibeScript source/drafts survive an FCStd round trip."""

from __future__ import annotations

from pathlib import Path
import tempfile

import FreeCAD as App

import VibeCADVibeScriptDomains as domains


def _add_string(obj, name: str, value: str) -> None:
    if name not in set(obj.PropertiesList):
        obj.addProperty("App::PropertyString", name, "VibeCAD")
    setattr(obj, name, value)


def main() -> int:
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    program_id = "a" * 32
    revision = "b" * 64
    source = (
        "width = inputs['width']\n"
        "result = {'Result': api.box(width, width, width)}\n"
    )
    input_schema = {
        "type": "object",
        "properties": {"width": {"type": "number", "exclusiveMinimum": 0}},
        "required": ["width"],
        "additionalProperties": False,
    }
    expected_outputs = [{"name": "Result", "type": "solid"}]
    contract = domains.encode_document_program_contract(
        pack,
        program_id=program_id,
        label="Portable cube",
        revision=revision,
        source=source,
        input_schema=input_schema,
        inputs={"width": 10.0},
        expected_outputs=expected_outputs,
    )
    draft = domains.encode_editor_draft(
        program_id=program_id,
        domain="partdesign",
        base_revision=revision,
        source=source.replace("10.0", "12.0"),
        input_schema=input_schema,
        inputs_json='{"width": 12.0}',
        expected_outputs=expected_outputs,
    )

    with tempfile.TemporaryDirectory(prefix="vibecad-portable-fcstd-") as temporary:
        path = Path(temporary) / "portable.FCStd"
        doc = App.newDocument("PortableVibeScript")
        root = doc.addObject("App::Part", "PortableProgram")
        for name, value in (
            (domains.PROP_PROGRAM_ID, program_id),
            (domains.PROP_PROGRAM_DOMAIN, "partdesign"),
            (domains.PROP_PROGRAM_WORKBENCH, "PartDesignWorkbench"),
            (domains.PROP_PROGRAM_REVISION, revision),
            (domains.PROP_PROGRAM_LABEL, "Portable cube"),
            (domains.PROP_PROGRAM_CONTRACT, contract),
            (domains.PROP_PROGRAM_EDITOR_DRAFT, draft),
        ):
            _add_string(root, name, value)
        doc.recompute()
        doc.saveAs(str(path))
        App.closeDocument(doc.Name)

        reopened = App.openDocument(str(path))
        try:
            payload = domains.capture_document_program_payload(
                reopened,
                "partdesign",
                program_id,
            )
            manifest = domains.decode_document_program_contract(
                payload["contract"],
                pack,
                expected_program_id=program_id,
                expected_revision=revision,
            )
            restored_draft = domains.decode_editor_draft(
                payload["editor_draft"],
                expected_program_id=program_id,
                expected_domain="partdesign",
            )
            assert manifest["source"] == source
            assert manifest["inputs"] == {"width": 10.0}
            assert restored_draft["inputs_json"] == '{"width": 12.0}'
            assert restored_draft["base_revision"] == revision
        finally:
            App.closeDocument(reopened.Name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
