# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bounded sheet context referencing the existing Tree and operation History.

Only document metadata is read here. Geometry preparation and detailed sheet
inspection remain explicit operations, so assembling a prompt does not rebuild
or mesh a model, including an invalid or suppressed feature.
"""

from VibeCADNativeSnapshot import concise_object, _selection_names


MAX_SHEET_CONTEXT_OBJECTS = 12


def build_sheet_metal_snapshot(document, *, selection=None):
    import SheetMetalEditable as Editable

    values = []
    for obj in document.Objects:
        if isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState):
            category = "shared_sheet"
        elif getattr(obj, "VibeCADTimelineEditCommand", None) == "SheetMetal_EditSource":
            category = "sheet_source"
        elif obj.isDerivedFrom("Sketcher::SketchObject"):
            category = "sketch"
        elif obj.TypeId in ("PartDesign::Body", "App::Part"):
            category = "container"
        elif obj.isDerivedFrom("Part::Feature"):
            # A candidate input, not a claim of validity or unfoldability.
            category = "shape_source"
        else:
            continue
        values.append((obj, category))
    selected = _selection_names(selection or {})
    priority = {name: index for index, name in enumerate(selected)}
    values.sort(key=lambda entry: priority.get(entry[0].Name, len(priority)))
    objects = []
    for obj, category in values[:MAX_SHEET_CONTEXT_OBJECTS]:
        item = concise_object(obj)
        item.update(category=category,
                    active=bool(document.isObjectUsableAtCurrentTimelinePosition(obj)),
                    suppressed=bool(getattr(obj, "Suppressed", False)),
                    visibility=bool(obj.ViewObject.Visibility))
        command = getattr(obj, "VibeCADTimelineEditCommand", None)
        if command:
            item["editor_command"] = str(command)[:160]
        if category == "shared_sheet":
            item["representation"] = getattr(obj.ViewObject.Proxy, "mode", None)
            predecessor = getattr(obj, "BaseSheet", None)
            if predecessor is None:
                source = getattr(obj, "SourceFace", None)
                predecessor = source[0] if source else None
            if predecessor is not None and predecessor.Document is document:
                item["predecessor"] = concise_object(predecessor)
        objects.append(item)
    timelines = document.findObjects("App::DocumentTimeline")
    history = None
    if len(timelines) == 1:
        timeline = timelines[0]
        history = {"position": int(timeline.Position),
                   "operation_count": len(timeline.Operations)}
    return {"kind": "sheet_metal", "objects": objects,
            "total_objects": len(values), "truncated": len(values) > len(objects),
            "history": history}
