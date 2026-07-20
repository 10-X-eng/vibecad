# SPDX-License-Identifier: LGPL-2.1-or-later

"""Transaction helpers for VibeCAD write tools."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADEditState import active_edit_object


ActionHandler = Callable[[], dict[str, Any]]
VerificationHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _diagnostic_generation_advanced(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> bool:
    """Return whether a handler already initiated a native recompute."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    if not before.get("captured") or not after.get("captured"):
        return False
    before_generation = before.get("generation")
    after_generation = after.get("generation")
    return (
        after_generation is not None and before_generation != after_generation
    )


def _active_edit_recompute_objects(doc: Any) -> list[Any] | None:
    """Limit an in-edit Sketcher transaction to the sketch being authored."""
    edit_object = active_edit_object()
    if (
        getattr(edit_object, "TypeId", "") == "Sketcher::SketchObject"
        and getattr(edit_object, "Document", None) is doc
    ):
        return [edit_object]
    return None


def run_freecad_transaction(
    name: str,
    handler: ActionHandler,
    verifier: VerificationHandler | None = None,
) -> dict[str, Any]:
    """Run one native FreeCAD undo transaction without rollback or cleanup.

    Failure is retained in the document exactly as FreeCAD produced it so the
    model and user can inspect and repair the real feature history.
    """
    try:
        import FreeCAD as App
    except Exception as exc:
        return {
            "ok": False,
            "failure_code": "FREECAD_UNAVAILABLE",
            "failure_stage": "precondition",
            "error": f"FreeCAD unavailable: {exc}",
            "state_change": _state_change({}, mutation_started=False),
        }

    doc = App.ActiveDocument
    if doc is not None and bool(getattr(doc, "Recomputing", False)):
        return {
            "ok": False,
            "failure_code": "DOCUMENT_RECOMPUTE_IN_PROGRESS",
            "failure_stage": "precondition",
            "error": (
                f"Document {getattr(doc, 'Name', '')} is still recomputing; "
                "the mutation was not started."
            ),
            "transaction_name": name,
            "state_change": _state_change({}, mutation_started=False),
            "transaction_opened": False,
            "mutation_started": False,
            "commit_attempted": False,
            "commit_succeeded": False,
            "recompute_performed": False,
            "recompute_scope": "none",
            "handler_recomputed": False,
            "document_delta": {},
            "native_diagnostics": [],
        }
    before = _document_snapshot(doc)
    baseline_diagnostics = recompute_diagnostic_summary(doc)
    handler_diagnostics: dict[str, Any] | None = None
    recompute_scope = "none"
    recompute_performed = False
    handler_recomputed = False
    result: dict[str, Any] = {}
    operation_error: str | None = None
    recompute_error: str | None = None
    commit_error: str | None = None
    verification: dict[str, Any] = {"ok": True, "checks": []}
    transaction_opened = False
    transaction_pending = False
    mutation_started = False
    commit_attempted = False
    commit_succeeded = False
    try:
        if doc is not None and hasattr(doc, "openTransaction"):
            doc.openTransaction(name)
            transaction_opened = True
            transaction_pending = True
        mutation_started = True
        try:
            raw_result = handler()
            result = raw_result if isinstance(raw_result, dict) else {"value": raw_result}
        except Exception as exc:
            operation_error = str(exc)
        finally:
            handler_diagnostics = recompute_diagnostic_summary(App.ActiveDocument or doc)
        active_doc = App.ActiveDocument or doc
        handler_recomputed = _diagnostic_generation_advanced(
            baseline_diagnostics,
            handler_diagnostics,
        )
        if (
            active_doc is not None
            and hasattr(active_doc, "recompute")
            and not handler_recomputed
        ):
            try:
                recompute_objects = _active_edit_recompute_objects(active_doc)
                if recompute_objects:
                    active_doc.recompute(recompute_objects)
                    recompute_scope = "active_sketch"
                else:
                    active_doc.recompute()
                    recompute_scope = "document"
                recompute_performed = True
            except Exception as exc:
                recompute_error = str(exc)
        elif handler_recomputed:
            recompute_scope = "handler"
        if operation_error or recompute_error:
            verification = {
                "ok": False,
                "checks": [
                    {
                        "ok": False,
                        "name": "operation",
                        "message": operation_error or recompute_error,
                    }
                ],
            }
        elif verifier is not None:
            try:
                verification = verifier(result)
            except Exception as exc:
                verification = {"ok": False, "error": str(exc), "checks": []}
        final_diagnostics = recompute_diagnostic_summary(App.ActiveDocument or doc)
        native_diagnostics = _merge_recompute_diagnostics(
            baseline_diagnostics,
            handler_diagnostics,
            final_diagnostics,
        )
        if transaction_pending and doc is not None and hasattr(doc, "commitTransaction"):
            commit_attempted = True
            try:
                doc.commitTransaction()
                commit_succeeded = True
            except Exception as exc:
                commit_error = str(exc)
            transaction_pending = False
        active_doc = App.ActiveDocument or doc
        after = _document_snapshot(active_doc)
        document_delta = _document_delta(before, after)
        diagnostic_scope = _transaction_object_scope(
            active_doc,
            document_delta,
            result,
        )
        native_diagnostics = _scope_recompute_diagnostics(
            native_diagnostics,
            diagnostic_scope,
        )
        diagnostic_error = _native_diagnostic_error(native_diagnostics)
        if diagnostic_error:
            verification = dict(verification)
            verification["ok"] = False
            checks = list(verification.get("checks", []) or [])
            checks.append(
                {
                    "ok": False,
                    "name": "native_recompute_diagnostics",
                    "message": diagnostic_error,
                }
            )
            verification["checks"] = checks
        state_change = _state_change(
            document_delta,
            transaction_opened=transaction_opened,
            mutation_started=mutation_started,
            commit_attempted=commit_attempted,
            commit_succeeded=commit_succeeded,
        )
        transaction_ok = (
            not operation_error
            and not recompute_error
            and bool(verification.get("ok", True))
            and not bool(diagnostic_error)
            and not commit_error
        )
        transaction: dict[str, Any] = {
            "ok": transaction_ok,
            "result": result,
            "verification": verification,
            "document_delta": document_delta,
            "native_diagnostics": native_diagnostics,
            "transaction_name": name,
            "state_change": state_change,
            "transaction_opened": transaction_opened,
            "mutation_started": mutation_started,
            "commit_attempted": commit_attempted,
            "commit_succeeded": commit_succeeded,
            "recompute_performed": recompute_performed,
            "recompute_scope": recompute_scope,
            "handler_recomputed": handler_recomputed,
        }
        if not transaction_ok:
            if operation_error:
                transaction["failure_code"] = "NATIVE_OPERATION_FAILED"
                transaction["failure_stage"] = "native_call"
            elif recompute_error or diagnostic_error:
                transaction["failure_code"] = "NATIVE_RECOMPUTE_FAILED"
                transaction["failure_stage"] = "native_recompute"
            elif commit_error:
                transaction["failure_code"] = "TRANSACTION_COMMIT_FAILED"
                transaction["failure_stage"] = "native_call"
            else:
                transaction["failure_code"] = "POSTCONDITION_FAILED"
                transaction["failure_stage"] = "postcondition"
            if operation_error:
                transaction["error"] = operation_error
            elif recompute_error:
                transaction["error"] = recompute_error
            elif diagnostic_error:
                transaction["error"] = diagnostic_error
            elif commit_error:
                transaction["error"] = commit_error
            elif verification.get("error"):
                transaction["error"] = str(verification.get("error"))
            else:
                transaction["error"] = "FreeCAD transaction verification failed."
            if commit_error:
                transaction["commit_error"] = commit_error
            if recompute_error:
                transaction["recompute_error"] = recompute_error
        return transaction
    except Exception as exc:
        emergency_commit_error = None
        if transaction_pending and doc is not None and hasattr(doc, "commitTransaction"):
            commit_attempted = True
            try:
                doc.commitTransaction()
                commit_succeeded = True
            except Exception as commit_exc:
                emergency_commit_error = str(commit_exc)
            transaction_pending = False
        document_delta = _document_delta(
            before,
            _document_snapshot(App.ActiveDocument or doc),
        )
        transaction = {
            "ok": False,
            "failure_code": "TRANSACTION_ORCHESTRATION_FAILED",
            "failure_stage": "native_call",
            "error": str(exc),
            "result": result,
            "document_delta": document_delta,
            "native_diagnostics": recompute_diagnostic_summary(App.ActiveDocument or doc),
            "transaction_name": name,
            "state_change": _state_change(
                document_delta,
                transaction_opened=transaction_opened,
                mutation_started=mutation_started,
                commit_attempted=commit_attempted,
                commit_succeeded=commit_succeeded,
            ),
            "transaction_opened": transaction_opened,
            "mutation_started": mutation_started,
            "commit_attempted": commit_attempted,
            "commit_succeeded": commit_succeeded,
            "recompute_performed": recompute_performed,
            "recompute_scope": recompute_scope,
            "handler_recomputed": handler_recomputed,
        }
        if emergency_commit_error:
            transaction["commit_error"] = emergency_commit_error
        return transaction


def _document_snapshot(doc: Any | None) -> dict[str, Any]:
    if doc is None:
        return {"document": None, "object_count": 0, "objects": []}
    objects = []
    for obj in getattr(doc, "Objects", []):
        item = {
            "name": getattr(obj, "Name", ""),
            "label": getattr(obj, "Label", getattr(obj, "Name", "")),
            "type": getattr(obj, "TypeId", ""),
            "state": sorted(
                str(value) for value in list(getattr(obj, "State", []) or [])
            ),
        }
        shape = _shape_summary(obj)
        if shape.get("available") and _should_include_shape_in_snapshot(obj, shape):
            item["shape"] = shape
        objects.append(item)
    return {
        "document": getattr(doc, "Name", None),
        "object_count": len(objects),
        "objects": objects,
    }


def _should_include_shape_in_snapshot(obj: Any, shape: dict[str, Any]) -> bool:
    type_id = str(getattr(obj, "TypeId", ""))
    if type_id.startswith("App::"):
        return False
    return (
        type_id.startswith("Part::")
        or type_id.startswith("PartDesign::")
        or type_id.startswith("Sketcher::")
        or int(shape.get("solids", 0) or 0) > 0
        or int(shape.get("faces", 0) or 0) > 0
        or int(shape.get("edges", 0) or 0) > 0
        or int(shape.get("vertices", 0) or 0) > 0
    )


def _shape_summary(obj: Any) -> dict[str, Any]:
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return {"available": False}
    summary: dict[str, Any] = {"available": True}
    try:
        summary["is_null"] = bool(shape.isNull())
    except Exception as exc:
        summary["inspection_complete"] = False
        summary["is_null_error"] = str(exc)
        return summary
    if summary["is_null"]:
        summary.update(
            {
                "shape_hash": None,
                "solids": 0,
                "faces": 0,
                "edges": 0,
                "vertices": 0,
                "inspection_complete": True,
            }
        )
        return summary
    try:
        summary["shape_hash"] = int(shape.hashCode())
    except Exception as exc:
        summary["inspection_complete"] = False
        summary["shape_hash_error"] = str(exc)
    for name, attribute in (
        ("solids", "Solids"),
        ("faces", "Faces"),
        ("edges", "Edges"),
        ("vertices", "Vertexes"),
    ):
        try:
            summary[name] = len(getattr(shape, attribute, []) or [])
        except Exception as exc:
            summary["inspection_complete"] = False
            summary[f"{name}_error"] = str(exc)
    bound_box = _bound_box_summary(getattr(shape, "BoundBox", None))
    if bound_box:
        summary["bound_box"] = bound_box
    summary.setdefault("inspection_complete", True)
    return summary


def _bound_box_summary(bound_box: Any) -> dict[str, Any] | None:
    if bound_box is None:
        return None
    try:
        return {
            "xmin": float(bound_box.XMin),
            "ymin": float(bound_box.YMin),
            "zmin": float(bound_box.ZMin),
            "xmax": float(bound_box.XMax),
            "ymax": float(bound_box.YMax),
            "zmax": float(bound_box.ZMax),
            "xlength": float(bound_box.XLength),
            "ylength": float(bound_box.YLength),
            "zlength": float(bound_box.ZLength),
        }
    except Exception:
        return None


def _document_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_objects = {item["name"]: item for item in before.get("objects", [])}
    after_objects = {item["name"]: item for item in after.get("objects", [])}
    before_names = set(before_objects)
    after_names = set(after_objects)
    changed = []
    for name in sorted(before_names.intersection(after_names)):
        before_item = before_objects[name]
        after_item = after_objects[name]
        if before_item != after_item:
            changed.append({"name": name, "before": before_item, "after": after_item})
    return {
        "object_count_before": int(before.get("object_count", 0)),
        "object_count_after": int(after.get("object_count", 0)),
        "object_count_delta": int(after.get("object_count", 0)) - int(before.get("object_count", 0)),
        "created_objects": [after_objects[name] for name in sorted(after_names - before_names)],
        "deleted_objects": [before_objects[name] for name in sorted(before_names - after_names)],
        "changed_objects": changed,
    }


def _state_change(
    document_delta: dict[str, Any],
    *,
    transaction_opened: bool = False,
    mutation_started: bool = False,
    commit_attempted: bool = False,
    commit_succeeded: bool = False,
) -> dict[str, Any]:
    created = list(document_delta.get("created_objects") or [])
    changed = list(document_delta.get("changed_objects") or [])
    deleted = list(document_delta.get("deleted_objects") or [])
    document_changed = bool(created or changed or deleted)
    repair_targets: list[str] = []
    for item in created + changed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name is None and isinstance(item.get("after"), dict):
            name = item["after"].get("name")
        if name and str(name) not in repair_targets:
            repair_targets.append(str(name))
    return {
        "transaction_opened": bool(transaction_opened),
        "mutation_started": bool(mutation_started),
        "commit_attempted": bool(commit_attempted),
        "commit_succeeded": bool(commit_succeeded),
        "document_changed": document_changed,
        "changed": document_changed,
        "retained": document_changed,
        "created_objects": created,
        "changed_objects": changed,
        "deleted_objects": deleted,
        "repair_targets": repair_targets,
    }


def recompute_diagnostic_summary(doc: Any | None = None) -> dict[str, Any]:
    """Read FreeCAD's structured diagnostics for the latest recompute generation."""
    if doc is None:
        try:
            import FreeCAD as App
        except Exception as exc:
            return {
                "captured": False,
                "generation": None,
                "diagnostics": [],
                "source": "document_recompute_diagnostics",
                "reason": str(exc),
            }
        doc = App.ActiveDocument
    if doc is None:
        return {
            "captured": True,
            "generation": None,
            "diagnostics": [],
            "source": "document_recompute_diagnostics",
        }
    getter = getattr(doc, "getRecomputeDiagnostics", None)
    if not callable(getter):
        return {
            "captured": False,
            "generation": None,
            "diagnostics": [],
            "source": "document_recompute_diagnostics",
            "reason": "This FreeCAD build does not expose getRecomputeDiagnostics().",
        }
    try:
        raw = getter()
    except Exception as exc:
        return {
            "captured": False,
            "generation": None,
            "diagnostics": [],
            "source": "document_recompute_diagnostics",
            "reason": str(exc),
        }
    if not isinstance(raw, dict):
        return {
            "captured": False,
            "generation": None,
            "diagnostics": [],
            "source": "document_recompute_diagnostics",
            "reason": "getRecomputeDiagnostics() returned a non-object value.",
        }
    diagnostics = [
        dict(item)
        for item in list(raw.get("diagnostics") or [])
        if isinstance(item, dict)
    ]
    return {
        "captured": True,
        "generation": raw.get("generation"),
        "diagnostics": diagnostics,
        "source": "document_recompute_diagnostics",
    }


def _native_diagnostic_error(summary: dict[str, Any]) -> str | None:
    if not bool(summary.get("captured")):
        return str(
            summary.get("reason")
            or "FreeCAD's structured recompute diagnostics are unavailable."
        )
    errors = [
        item
        for item in list(summary.get("diagnostics") or [])
        if str(item.get("severity") or "").lower() == "error"
    ]
    if not errors:
        return None
    first = errors[0]
    return (
        f"FreeCAD recompute generation {summary.get('generation')} reported "
        f"{len(errors)} error(s). First: {first.get('code')} on "
        f"{first.get('object')}: {first.get('message')}"
    )


def _merge_recompute_diagnostics(
    baseline: dict[str, Any],
    *summaries: dict[str, Any] | None,
) -> dict[str, Any]:
    baseline_generation = baseline.get("generation") if isinstance(baseline, dict) else None
    generations: list[Any] = []
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        if not bool(summary.get("captured")):
            return dict(summary)
        generation = summary.get("generation")
        if generation == baseline_generation or generation in generations:
            continue
        generations.append(generation)
        for item in list(summary.get("diagnostics") or []):
            if not isinstance(item, dict):
                continue
            key = (
                item.get("generation"),
                item.get("code"),
                item.get("object"),
                item.get("property"),
                item.get("subelement"),
                item.get("algorithm"),
                item.get("message"),
            )
            if key in seen:
                continue
            seen.add(key)
            diagnostics.append(dict(item))
    return {
        "captured": True,
        "generation": generations[-1] if generations else baseline_generation,
        "generations": generations,
        "diagnostics": diagnostics,
        "source": "document_recompute_diagnostics",
    }


def _transaction_object_scope(
    doc: Any | None,
    document_delta: dict[str, Any],
    result: dict[str, Any],
) -> list[str]:
    """Return exact document objects attributable to the current tool call."""
    names: set[str] = set()
    for item in list(document_delta.get("created_objects") or []):
        if isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]))
    for item in list(document_delta.get("changed_objects") or []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name is None and isinstance(item.get("after"), dict):
            name = item["after"].get("name")
        if name:
            names.add(str(name))

    if doc is not None:
        stack: list[Any] = [result]
        visited: set[int] = set()
        while stack:
            value = stack.pop()
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            if isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, (list, tuple, set)):
                stack.extend(value)
            elif isinstance(value, str) and doc.getObject(value) is not None:
                names.add(value)
    return sorted(names)


def _scope_recompute_diagnostics(
    summary: dict[str, Any],
    object_names: list[str],
) -> dict[str, Any]:
    if not isinstance(summary, dict) or not bool(summary.get("captured")):
        return summary
    scope = set(object_names)
    relevant = []
    out_of_scope = []
    for item in list(summary.get("diagnostics") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("object") or "") in scope:
            relevant.append(item)
        else:
            out_of_scope.append(item)
    scoped = dict(summary)
    scoped["diagnostic_scope_objects"] = sorted(scope)
    scoped["diagnostics"] = relevant
    scoped["out_of_scope_diagnostics"] = out_of_scope
    return scoped
