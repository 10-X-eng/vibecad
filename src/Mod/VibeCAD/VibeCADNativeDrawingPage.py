# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact page creation and template-field editing for Native Drawing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import (
    MAX_DRAWING_TEMPLATE_BYTES,
    MAX_EDITABLE_TEMPLATE_FIELDS,
    MAX_TEMPLATE_FIELD_NAME_CHARACTERS,
    drawing_page_invariants,
    drawing_page_state,
    editable_template_fields,
    is_drawing_page,
    is_svg_template,
    template_content_state,
)
from VibeCADNativeInput import (
    NativeInputArtifact,
    NativeInputError,
    NativeInputRequest,
    authorize_native_input_path,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


_FREECAD_EDITABLE_ATTRIBUTE = (
    "{http://www.freecad.org/wiki/index.php?title=Svg_Namespace}editable"
)
BUILT_IN_DRAWING_TEMPLATES = {
    "iso_a0_landscape": "ISO/A0_Landscape_ISO5457_advanced.svg",
    "iso_a1_landscape": "ISO/A1_Landscape_ISO5457_advanced.svg",
    "iso_a2_landscape": "ISO/A2_Landscape_ISO5457_advanced.svg",
    "iso_a3_landscape": "ISO/A3_Landscape_ISO5457_advanced.svg",
    "iso_a4_landscape": "ISO/A4_Landscape_ISO5457_advanced.svg",
    "iso_a4_portrait": "ISO/A4_Portrait_ISO5457_advanced.svg",
    "asme_ansi_a_landscape": "ASME/ANSIA_Landscape.svg",
    "asme_ansi_a_portrait": "ASME/ANSIA_Portrait.svg",
    "asme_ansi_b_landscape": "ASME/ANSIB_Landscape.svg",
    "asme_ansi_b_portrait": "ASME/ANSIB_Portrait.svg",
    "asme_ansi_c_landscape": "ASME/ANSIC_Landscape.svg",
    "asme_ansi_c_portrait": "ASME/ANSIC_Portrait.svg",
    "asme_ansi_d_landscape": "ASME/ANSID_Landscape.svg",
    "asme_ansi_d_portrait": "ASME/ANSID_Portrait.svg",
    "asme_ansi_e_landscape": "ASME/ANSIE_Landscape.svg",
    "asme_ansi_e_portrait": "ASME/ANSIE_Portrait.svg",
}


def built_in_template_relative_path(template: str) -> str:
    try:
        relative = BUILT_IN_DRAWING_TEMPLATES[str(template)]
    except KeyError as exc:
        raise NativeDrawingError(
            "The requested built-in Drawing template is unavailable.",
            error_code="NATIVE_DRAWING_TEMPLATE_UNKNOWN",
            repair={"available_templates": sorted(BUILT_IN_DRAWING_TEMPLATES)},
        ) from exc
    return f"Mod/TechDraw/Templates/{relative}"


@dataclass(frozen=True, slots=True)
class PreparedPageCreate:
    artifact: NativeInputArtifact
    source_kind: str
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


@dataclass(frozen=True, slots=True)
class TemplateFieldChange:
    field_name: str
    expected_value: str | None
    value: str


@dataclass(frozen=True, slots=True)
class PreparedTemplateFieldEdit:
    page: Any
    template: Any
    changes: tuple[TemplateFieldChange, ...]
    fields_before: dict[str, str]
    fields_after: dict[str, str]
    page_invariants_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


@dataclass(frozen=True, slots=True)
class PreparedKeepUpdatedEdit:
    page: Any
    previous_keep_updated: bool
    keep_updated: bool
    page_invariants_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def drawing_template_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="drawing_page_template",
        title="Create Drawing Page From Template",
        allowed_suffixes=(".svg",),
        name_filter="SVG drawing templates (*.svg *.SVG)",
        maximum_bytes=MAX_DRAWING_TEMPLATE_BYTES,
    )


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _visibility(document: Any) -> tuple[tuple[Any, bool], ...]:
    result = []
    for obj in tuple(document.Objects):
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            result.append((obj, bool(getattr(view, "Visibility", False))))
    return tuple(result)


def _current_selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _validate_template_content(artifact: NativeInputArtifact) -> None:
    try:
        content = artifact.read_bytes(maximum_bytes=MAX_DRAWING_TEMPLATE_BYTES)
    except NativeInputError as exc:
        raise NativeDrawingError(
            str(exc),
            error_code=exc.code,
        ) from exc
    if not content:
        raise NativeDrawingError(
            "The selected Drawing template is empty.",
            error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
        )
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise NativeDrawingError(
            "The selected Drawing template contains an unsupported document type or entity declaration.",
            error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
        )
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise NativeDrawingError(
            "The selected Drawing template is not valid SVG XML.",
            error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
        ) from exc
    if str(root.tag).rsplit("}", 1)[-1].casefold() != "svg":
        raise NativeDrawingError(
            "The selected Drawing template has no SVG root element.",
            error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
        )
    editable_names = []
    element_count = 0
    for element in root.iter():
        element_count += 1
        if element_count > 100_000:
            raise NativeDrawingError(
                "The selected Drawing template contains too many XML elements.",
                error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
            )
        name = str(element.attrib.get(_FREECAD_EDITABLE_ATTRIBUTE, "") or "")
        if name:
            editable_names.append(name)
    if (
        len(editable_names) > MAX_EDITABLE_TEMPLATE_FIELDS
        or len(editable_names) != len(set(editable_names))
        or any(
            len(name) > MAX_TEMPLATE_FIELD_NAME_CHARACTERS
            for name in editable_names
        )
    ):
        raise NativeDrawingError(
            "The selected Drawing template has duplicate or unsupported editable field names.",
            error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
        )


def _capture_create_boundary(
    document: Any,
    artifact: NativeInputArtifact,
    source_kind: str,
) -> PreparedPageCreate:
    _validate_template_content(artifact)
    return PreparedPageCreate(
        artifact=artifact,
        source_kind=source_kind,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_current_selection(document),
        visibility_before=_visibility(document),
    )


def prepare_default_page_create(document: Any) -> PreparedPageCreate:
    import FreeCAD as App

    default_path = Path(App.getResourceDir()) / (
        "Mod/TechDraw/Templates/Default_Template_A4_Landscape.svg"
    )
    preferences = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/TechDraw/Files"
    )
    configured = str(preferences.GetString("TemplateFile", "") or "").strip()
    candidate = configured or str(default_path)
    source_kind = "configured_default" if configured else "built_in_default"
    request = drawing_template_input_request()
    try:
        authorization = authorize_native_input_path(request, candidate)
    except NativeInputError:
        if candidate == str(default_path):
            raise NativeDrawingError(
                "The configured and built-in default Drawing templates are unavailable.",
                error_code="NATIVE_DRAWING_TEMPLATE_UNAVAILABLE",
            )
        source_kind = "built_in_fallback"
        try:
            authorization = authorize_native_input_path(request, str(default_path))
        except NativeInputError as exc:
            raise NativeDrawingError(
                "The configured and built-in default Drawing templates are unavailable.",
                error_code="NATIVE_DRAWING_TEMPLATE_UNAVAILABLE",
            ) from exc
    try:
        artifact = authorization.claim(request)
    except NativeInputError as exc:
        raise NativeDrawingError(str(exc), error_code=exc.code) from exc
    return _capture_create_boundary(document, artifact, source_kind)


def prepare_built_in_page_create(
    document: Any,
    *,
    template: str,
) -> PreparedPageCreate:
    import FreeCAD as App

    path = Path(App.getResourceDir()) / built_in_template_relative_path(template)
    request = drawing_template_input_request()
    try:
        authorization = authorize_native_input_path(request, str(path))
        artifact = authorization.claim(request)
    except NativeInputError as exc:
        raise NativeDrawingError(
            "The requested built-in Drawing template is unavailable.",
            error_code="NATIVE_DRAWING_TEMPLATE_UNAVAILABLE",
        ) from exc
    return _capture_create_boundary(document, artifact, f"built_in:{template}")


def prepare_authorized_page_create(
    document: Any,
    authorization: Any,
    request: NativeInputRequest,
) -> PreparedPageCreate:
    try:
        artifact = authorization.claim(request)
    except (AttributeError, NativeInputError) as exc:
        code = getattr(exc, "code", "NATIVE_INPUT_AUTHORIZATION_FAILED")
        raise NativeDrawingError(str(exc), error_code=code) from exc
    return _capture_create_boundary(document, artifact, "human_authorized")


def create_page(
    document: Any,
    *,
    prepared: PreparedPageCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPageCreate):
        raise TypeError("prepared must be a PreparedPageCreate")
    prepared.artifact.verify_unchanged()
    template = document.addObject("TechDraw::DrawSVGTemplate", "Template")
    page = document.addObject("TechDraw::DrawPage", "Page")
    if template is None or page is None:
        raise NativeDrawingError(
            "The Drawing page and template objects could not be created.",
            error_code="NATIVE_DRAWING_PAGE_CREATE_FAILED",
        )
    template.Label = "Template"
    page.Label = "Page"
    page.Template = template
    template.Template = str(
        prepared.artifact.host_path_after_content_verification()
    )
    prepared.artifact.verify_unchanged()
    document.publishProvisionalTimelineOperationBlock(page, (template,), ())
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "page": page,
            "template": template,
        },
        recompute_targets=(template, page),
        created=(object_identity(page), object_identity(template)),
    )


def _assert_presentation_unchanged(
    document: Any,
    *,
    selection_before: Mapping[str, Any],
    visibility_before: tuple[tuple[Any, bool], ...],
) -> None:
    if _current_selection(document) != dict(selection_before):
        raise NativeDrawingError(
            "The Drawing operation changed the human selection.",
            error_code="NATIVE_DRAWING_POSTCONDITION_FAILED",
        )
    if tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in visibility_before
    ) != visibility_before:
        raise NativeDrawingError(
            "The Drawing operation changed existing object visibility.",
            error_code="NATIVE_DRAWING_POSTCONDITION_FAILED",
        )


def verify_created_page(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    prepared: PreparedPageCreate = value["prepared"]
    page = value["page"]
    template = value["template"]
    new_objects = tuple(
        obj for obj in document.Objects if obj not in prepared.objects_before
    )
    timeline = document.getObject("VibeCADTimeline")
    allowed_new = {page, template}
    if timeline is not None and timeline not in prepared.objects_before:
        allowed_new.add(timeline)
    if set(new_objects) != allowed_new or len(new_objects) != len(allowed_new):
        raise NativeDrawingError(
            "Drawing page creation changed objects outside its exact page block.",
            error_code="NATIVE_DRAWING_PAGE_POSTCONDITION_FAILED",
        )
    if (
        not is_drawing_page(page)
        or not is_svg_template(template)
        or page.Template is not template
        or str(getattr(page, "VibeCADTimelineRole", "") or "") != "operation"
        or str(getattr(template, "VibeCADTimelineRole", "") or "") != "resource"
        or getattr(template, "VibeCADTimelineOwner", None) is not page
        or _timeline_operations(document)
        != (*prepared.timeline_before, template, page)
    ):
        raise NativeDrawingError(
            "The Drawing page did not retain its exact template and History ownership.",
            error_code="NATIVE_DRAWING_PAGE_POSTCONDITION_FAILED",
        )
    content = template_content_state(template)
    if (
        not content.get("available")
        or content.get("sha256") != prepared.artifact.sha256
        or content.get("size_bytes") != prepared.artifact.size_bytes
    ):
        raise NativeDrawingError(
            "The Drawing page did not embed the exact authorized SVG template.",
            error_code="NATIVE_DRAWING_TEMPLATE_STALE",
        )
    state = drawing_page_state(page)
    if not state["editable_fields_supported"]:
        raise NativeDrawingError(
            "The Drawing template produced unsupported editable field data.",
            error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
        )
    _assert_presentation_unchanged(
        document,
        selection_before=prepared.selection_before,
        visibility_before=prepared.visibility_before,
    )
    return {
        "page": state,
        "template_input": {
            "source": prepared.source_kind,
            **prepared.artifact.summary(),
        },
    }


def prepare_template_field_edit(
    document: Any,
    *,
    target: Mapping[str, Any],
    updates: tuple[Mapping[str, Any], ...],
) -> PreparedTemplateFieldEdit:
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": target["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    current_state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != current_state["state_sha256"]:
        raise NativeDrawingError(
            "The exact Drawing page changed after it was inspected.",
            error_code="NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": current_state["state_sha256"]},
        )
    template = getattr(page, "Template", None)
    if not is_svg_template(template):
        raise NativeDrawingError(
            "The exact Drawing page has no editable SVG template.",
            error_code="NATIVE_DRAWING_TEMPLATE_UNAVAILABLE",
        )
    if not current_state["editable_fields_supported"]:
        raise NativeDrawingError(
            "The exact Drawing template exceeds the supported editable field bounds.",
            error_code="NATIVE_DRAWING_TEMPLATE_INVALID",
        )
    fields_before = editable_template_fields(template)
    changes = tuple(
        TemplateFieldChange(
            field_name=str(item["field_name"]),
            expected_value=(
                str(item["expected_value"])
                if "expected_value" in item
                else None
            ),
            value=str(item["value"]),
        )
        for item in updates
    )
    names = tuple(change.field_name for change in changes)
    if len(names) != len(set(names)):
        raise NativeDrawingError(
            "Each editable Drawing template field may appear only once.",
            error_code="NATIVE_DRAWING_TEMPLATE_FIELDS_INVALID",
        )
    fields_after = dict(fields_before)
    for change in changes:
        if change.field_name not in fields_before:
            raise NativeDrawingError(
                f"The Drawing template has no editable field {change.field_name!r}.",
                error_code="NATIVE_DRAWING_TEMPLATE_FIELD_UNKNOWN",
                repair={"available_fields": sorted(fields_before)[:64]},
            )
        if (
            change.expected_value is not None
            and fields_before[change.field_name] != change.expected_value
        ):
            raise NativeDrawingError(
                f"Editable field {change.field_name!r} changed after inspection.",
                error_code="NATIVE_DRAWING_TEMPLATE_FIELD_STALE",
                repair={"current_value": fields_before[change.field_name]},
            )
        fields_after[change.field_name] = change.value
    if fields_after == fields_before:
        raise NativeDrawingError(
            "The requested Drawing template fields already have those values.",
            error_code="NATIVE_DRAWING_NO_CHANGE",
        )
    return PreparedTemplateFieldEdit(
        page=page,
        template=template,
        changes=changes,
        fields_before=fields_before,
        fields_after=fields_after,
        page_invariants_before=drawing_page_invariants(page),
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_current_selection(document),
        visibility_before=_visibility(document),
    )


def edit_template_fields(
    _document: Any,
    *,
    prepared: PreparedTemplateFieldEdit,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedTemplateFieldEdit):
        raise TypeError("prepared must be a PreparedTemplateFieldEdit")
    prepared.template.EditableTexts = dict(prepared.fields_after)
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.template, prepared.page),
        changed=(object_identity(prepared.page), object_identity(prepared.template)),
    )


def verify_template_field_edit(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedTemplateFieldEdit = draft.value["prepared"]
    if (
        tuple(document.Objects) != prepared.objects_before
        or _timeline_operations(document) != prepared.timeline_before
        or prepared.page.Template is not prepared.template
        or editable_template_fields(prepared.template) != prepared.fields_after
        or drawing_page_invariants(prepared.page)
        != prepared.page_invariants_before
    ):
        raise NativeDrawingError(
            "Drawing template-field editing changed state outside the exact fields.",
            error_code="NATIVE_DRAWING_TEMPLATE_FIELDS_POSTCONDITION_FAILED",
        )
    _assert_presentation_unchanged(
        document,
        selection_before=prepared.selection_before,
        visibility_before=prepared.visibility_before,
    )
    return {
        "page": drawing_page_state(prepared.page),
        "changed_fields": [change.field_name for change in prepared.changes],
        "changed_field_count": len(prepared.changes),
    }


def _normalize_keep_updated_plan(raw: Any) -> dict[str, Any]:
    fields = {
        "page_name",
        "previous_keep_updated",
        "keep_updated",
        "changed",
    }
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise NativeDrawingError(
            "TechDraw returned malformed Drawing update-policy state.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_STATE_INVALID",
        )
    page_name = raw["page_name"]
    previous = raw["previous_keep_updated"]
    keep_updated = raw["keep_updated"]
    changed = raw["changed"]
    if not isinstance(page_name, str) or not page_name:
        raise NativeDrawingError(
            "TechDraw returned an invalid Drawing update-policy page identity.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_STATE_INVALID",
        )
    if any(type(value) is not bool for value in (previous, keep_updated, changed)):
        raise NativeDrawingError(
            "TechDraw returned non-boolean Drawing update-policy state.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_STATE_INVALID",
        )
    if changed is not (previous is not keep_updated):
        raise NativeDrawingError(
            "TechDraw returned inconsistent Drawing update-policy state.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_STATE_INVALID",
        )
    return {
        "page_name": page_name,
        "previous_keep_updated": previous,
        "keep_updated": keep_updated,
        "changed": changed,
    }


def prepare_keep_updated_edit(
    document: Any,
    *,
    target: Mapping[str, Any],
    keep_updated: bool,
) -> PreparedKeepUpdatedEdit:
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": target["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != state["state_sha256"]:
        raise NativeDrawingError(
            "The exact Drawing page changed after it was inspected.",
            error_code="NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    if type(keep_updated) is not bool:
        raise NativeDrawingError(
            "keep_updated must be a boolean.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    previous = bool(state["keep_updated"])
    if previous is keep_updated:
        raise NativeDrawingError(
            "The Drawing page already has the requested update policy.",
            error_code="NATIVE_DRAWING_NO_CHANGE",
            repair={"keep_updated": previous},
        )
    import TechDrawGui

    plan = _normalize_keep_updated_plan(
        TechDrawGui.validateDrawingKeepUpdated(page, keep_updated)
    )
    if (
        plan["page_name"] != page.Name
        or plan["previous_keep_updated"] is not previous
        or plan["keep_updated"] is not keep_updated
        or plan["changed"] is not True
    ):
        raise NativeDrawingError(
            "TechDraw produced a different Drawing update-policy plan.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_STATE_INVALID",
        )
    return PreparedKeepUpdatedEdit(
        page=page,
        previous_keep_updated=previous,
        keep_updated=keep_updated,
        page_invariants_before=drawing_page_invariants(page),
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_current_selection(document),
        visibility_before=_visibility(document),
    )


def change_keep_updated(
    _document: Any,
    *,
    prepared: PreparedKeepUpdatedEdit,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedKeepUpdatedEdit):
        raise TypeError("prepared must be a PreparedKeepUpdatedEdit")
    import TechDrawGui

    plan = _normalize_keep_updated_plan(
        TechDrawGui.changeDrawingKeepUpdated(
            prepared.page,
            prepared.keep_updated,
        )
    )
    if (
        plan["page_name"] != prepared.page.Name
        or plan["previous_keep_updated"] is not prepared.previous_keep_updated
        or plan["keep_updated"] is not prepared.keep_updated
        or plan["changed"] is not True
    ):
        raise NativeDrawingError(
            "TechDraw applied a different Drawing update policy.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_FAILED",
        )
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.page,),
        changed=(object_identity(prepared.page),),
    )


def verify_keep_updated_edit(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedKeepUpdatedEdit = draft.value["prepared"]
    current_invariants = drawing_page_invariants(prepared.page)
    expected_invariants = dict(prepared.page_invariants_before)
    expected_invariants["keep_updated"] = prepared.keep_updated
    if (
        tuple(document.Objects) != prepared.objects_before
        or _timeline_operations(document) != prepared.timeline_before
        or current_invariants != expected_invariants
    ):
        raise NativeDrawingError(
            "Drawing update-policy editing changed state outside the exact page property.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_POSTCONDITION_FAILED",
        )
    _assert_presentation_unchanged(
        document,
        selection_before=prepared.selection_before,
        visibility_before=prepared.visibility_before,
    )
    import TechDrawGui

    current = _normalize_keep_updated_plan(
        TechDrawGui.drawingKeepUpdated(prepared.page)
    )
    if (
        current["page_name"] != prepared.page.Name
        or current["previous_keep_updated"] is not prepared.keep_updated
        or current["keep_updated"] is not prepared.keep_updated
        or current["changed"] is not False
    ):
        raise NativeDrawingError(
            "The Drawing page did not retain its exact update policy.",
            error_code="NATIVE_DRAWING_PAGE_UPDATE_POSTCONDITION_FAILED",
        )
    return {
        "operation": "set_keep_updated",
        "page": drawing_page_state(prepared.page),
        "previous_keep_updated": prepared.previous_keep_updated,
        "keep_updated": prepared.keep_updated,
        "changed": True,
    }
