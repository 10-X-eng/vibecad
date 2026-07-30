# SPDX-License-Identifier: LGPL-2.1-or-later

"""Move the document history marker so one PartDesign feature is current."""

from __future__ import annotations

from typing import Any

import VibeCADTransactions as transactions

from . import domain_runtime

TOOL_SPEC = {
    "name": "partdesign.set_tip",
    "description": (
        "Move the document's current model state immediately after an exact solid feature "
        "already in an exact Body. FreeCAD rewinds every later document operation through "
        "the native feature timeline and makes that feature the Body Tip without deleting, "
        "cloning, or reordering history."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "body_name": {
                "type": "string",
                "description": "Exact internal name of the Body whose Tip changes.",
            },
            "feature_name": {
                "type": "string",
                "description": "Exact internal name of the solid feature that becomes the Tip.",
            },
        },
        "required": ["body_name", "feature_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, body_name: str, feature_name: str) -> dict[str, Any]:
    body = service._get_partdesign_body(str(body_name or "").strip())
    if body is None:
        return _invalid(f"Body not found by exact internal name: {body_name}")
    doc = service._active_document()
    if doc is None or getattr(body, "Document", None) is not doc:
        return _invalid("The requested Body is not in the active document.")
    feature = doc.getObject(str(feature_name or "").strip()) if doc is not None else None
    if feature is None:
        return _invalid(f"Feature not found by exact internal name: {feature_name}")
    if getattr(feature, "Document", None) is not doc:
        return _invalid("The requested feature is not in the active document.")
    if feature not in list(body.Group):
        return _invalid(f"Feature {feature.Name} is not in Body {body.Name}.")
    if not str(getattr(feature, "TypeId", "")).startswith("PartDesign::") or not hasattr(
        feature, "Shape"
    ):
        return _invalid("Only a solid PartDesign feature can be a Body Tip.")
    feature_state = domain_runtime.feature_state_summary(feature)
    feature_shape = domain_runtime.shape_summary(feature)
    if (
        feature_state.get("marked_invalid")
        or feature_state.get("shape_null")
        or feature_state.get("shape_valid") is not True
        or int(feature_shape.get("solids", 0) or 0) != 1
    ):
        return _invalid(
            "The requested Tip must be one valid, non-null PartDesign solid.",
            selected_feature_state=feature_state,
            selected_feature_shape=feature_shape,
        )
    body_group_before = list(body.Group)
    feature_index = body_group_before.index(feature)
    downstream = [
        item.Name
        for item in body_group_before[feature_index + 1 :]
        if str(getattr(item, "TypeId", "")).startswith("PartDesign::")
    ]
    if body in list(getattr(feature, "OutListRecursive", []) or []):
        return _invalid(
            "The requested Tip would create a dependency cycle back to its owning Body.",
            dependency_cycle={"body": body.Name, "feature": feature.Name},
        )

    try:
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtGui
    except Exception as exc:
        return _invalid(f"FreeCAD GUI unavailable: {exc}")
    if App.ActiveDocument is not doc:
        return _invalid("The requested Body's document is not the active FreeCAD document.")
    main_window = Gui.getMainWindow()
    timeline_widget = (
        main_window.findChild(QtGui.QWidget, "VibeCADFeatureTimeline")
        if main_window is not None
        else None
    )
    move_after_operation = getattr(
        timeline_widget,
        "moveCurrentStateAfterOperation",
        None,
    )
    if not callable(move_after_operation):
        return _invalid("The native VibeCAD feature timeline is unavailable in this GUI session.")
    controller = next(
        (
            item
            for item in list(getattr(doc, "Objects", []) or [])
            if getattr(item, "TypeId", "") == "App::DocumentTimeline"
        ),
        None,
    )
    operations_before = list(getattr(controller, "Operations", []) or [])
    if controller is None or feature not in operations_before:
        return _invalid(f"Feature {feature.Name} is not recorded in the native document timeline.")

    before_tip = getattr(getattr(body, "Tip", None), "Name", None)
    position_before = int(getattr(controller, "Position", 0) or 0)
    requested_position = operations_before.index(feature) + 1
    operation_identities = tuple(
        (str(getattr(item, "Name", "")), int(getattr(item, "ID", -1))) for item in operations_before
    )
    document_uid = str(getattr(doc, "Uid", "") or "")
    body_identity = (body.Name, int(body.ID))
    feature_identity = (feature.Name, int(feature.ID))
    before_document = transactions._document_snapshot(doc)
    invocation_error = None
    try:
        invoked = bool(
            move_after_operation(
                doc.Name,
                document_uid,
                feature.Name,
                int(feature.ID),
            )
        )
    except Exception as exc:
        invoked = False
        invocation_error = str(exc)

    active_doc = App.ActiveDocument
    current_doc = (
        active_doc
        if active_doc is not None
        and active_doc.Name == doc.Name
        and str(getattr(active_doc, "Uid", "") or "") == document_uid
        else None
    )
    current_body = current_doc.getObject(body_identity[0]) if current_doc is not None else None
    if current_body is not None and int(getattr(current_body, "ID", -1)) != body_identity[1]:
        current_body = None
    current_feature = (
        current_doc.getObject(feature_identity[0]) if current_doc is not None else None
    )
    if (
        current_feature is not None
        and int(getattr(current_feature, "ID", -1)) != feature_identity[1]
    ):
        current_feature = None
    current_controller = next(
        (
            item
            for item in list(getattr(current_doc, "Objects", []) or [])
            if getattr(item, "TypeId", "") == "App::DocumentTimeline"
        ),
        None,
    )
    operations_after = list(getattr(current_controller, "Operations", []) or [])
    operations_unchanged = (
        tuple(
            (str(getattr(item, "Name", "")), int(getattr(item, "ID", -1)))
            for item in operations_after
        )
        == operation_identities
    )
    actual_tip = getattr(getattr(current_body, "Tip", None), "Name", None)
    actual_position = (
        int(getattr(current_controller, "Position", -1)) if current_controller is not None else -1
    )
    document_delta = transactions._document_delta(
        before_document,
        transactions._document_snapshot(current_doc),
    )
    diagnostic_summary = transactions.recompute_diagnostic_summary(current_doc)
    native_diagnostics = [
        dict(item)
        for item in list(diagnostic_summary.get("diagnostics") or [])
        if isinstance(item, dict)
    ]
    ok = (
        invoked
        and current_body is not None
        and current_feature is not None
        and actual_tip == feature.Name
        and actual_position == requested_position
        and operations_unchanged
        and current_body not in list(getattr(current_feature, "OutListRecursive", []) or [])
    )
    result = {
        "document": getattr(current_doc, "Name", None),
        "document_uid": document_uid,
        "body": getattr(current_body, "Name", None),
        "tip_before": before_tip,
        "tip_after": actual_tip,
        "current_state_after": getattr(current_feature, "Name", None),
        "timeline_position_before": position_before,
        "timeline_position_after": actual_position,
        "timeline_operation_count": len(operations_after),
        "timeline_operations_unchanged": operations_unchanged,
        "body_group": [item.Name for item in list(getattr(current_body, "Group", []) or [])],
        "selected_feature_state": (
            domain_runtime.feature_state_summary(current_feature)
            if current_feature is not None
            else {}
        ),
        "selected_feature_shape": (
            domain_runtime.shape_summary(current_feature) if current_feature is not None else {}
        ),
        "selected_feature_history_index": feature_index,
        "selected_feature_timeline_index": requested_position - 1,
        "later_body_features_made_inactive": downstream,
        "later_document_operations_made_inactive": [
            item.Name for item in operations_before[requested_position:]
        ],
        "dag_safe": (
            current_body is not None
            and current_feature is not None
            and current_body not in list(getattr(current_feature, "OutListRecursive", []) or [])
        ),
    }
    response = {
        "ok": ok,
        "mutation": result,
        "document_delta": document_delta,
        "native_diagnostics": native_diagnostics,
        "body_state": (
            service._partdesign_body_summary(current_body) if current_body is not None else None
        ),
        "tip_before": before_tip,
        "tip_after": actual_tip,
        "tip_changed": actual_tip != before_tip,
    }
    if not ok:
        response["failure_code"] = "TIMELINE_NAVIGATION_FAILED"
        response["failure_stage"] = "native_call" if not invoked else "postcondition"
        response["error"] = invocation_error or (
            "FreeCAD did not move the document's current model state to the " "requested feature."
        )
        response["retry_same_call"] = False
    return response


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
