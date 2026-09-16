# SPDX-License-Identifier: LGPL-2.1-or-later
"""Owned asynchronous creation and parameter editing of upstream sheet sources."""

from dataclasses import dataclass
import json
import re

import FreeCAD as App
import Part
import PartGui

import SheetMetalEditable as Editable
import SheetMetalOperations as Operations
import SheetMetalSourceFeatures as Features
from SheetMetalPresentation import _gui_thread
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeMutation import NativeMutationDraft, run_human_mutation
from VibeCADNativeTargets import object_identity


PARAMETERS = {
    "base_shape": {"shape_type": "shapeType", "thickness": "thickness", "bend_radius": "radius",
                   "width": "width", "length": "length", "height": "height",
                   "flange_width": "flangeWidth", "origin": "originLoc", "fill_gaps": "fillGaps"},
    "base_from_sketch": {"thickness": "Thickness", "bend_radius": "Radius", "length": "Length",
                         "bend_side": "BendSide", "midplane": "MidPlane", "reverse": "Reverse"},
    "from_solid": {"thickness": "Thickness", "bend_radius": "Radius", "invert": "Invert"},
    "add_flange": {"length": "length", "bend_radius": "radius", "bend_angle": "angle",
                   "invert": "invert", "bend_type": "BendType", "length_spec": "LengthSpec"},
    "fold_from_sketch": {"bend_radius": "radius", "bend_angle": "angle", "invert": "invert",
                         "invert_bend": "invertbend", "k_factor": "kfactor", "position": "Position"},
}
CHOICES = {
    "shape_type": ("Flat", "L-Shape", "U-Shape", "Tub", "Hat", "Box"),
    "origin": ("-X,-Y", "-X,0", "-X,+Y", "0,-Y", "0,0", "0,+Y", "+X,-Y", "+X,0", "+X,+Y"),
    "bend_side": ("Outside", "Inside", "Middle"),
    "bend_type": ("Material Outside", "Material Inside", "Thickness Outside"),
    "length_spec": ("Leg", "Outer Sharp", "Inner Sharp", "Tangential"),
    # These positions use the requested neutral-axis factor. The upstream
    # intersection-of-planes mode computes a different factor internally.
    "position": ("middle", "backward", "forward"),
}
_CLASSES = {"base_shape": Features.BaseShape, "base_from_sketch": Features.BaseBend,
            "from_solid": Features.FromSolid, "add_flange": Features.Flange,
            "fold_from_sketch": Features.Fold}
_NAMES = {"base_shape": "BaseShape", "base_from_sketch": "BaseBend", "from_solid": "SheetFromSolid",
          "add_flange": "SheetFlange", "fold_from_sketch": "SheetFold"}
_FIELDS = {key: frozenset(values) | extra for key, values, extra in (
    ("base_shape", PARAMETERS["base_shape"], {"container_name"}),
    ("base_from_sketch", PARAMETERS["base_from_sketch"], {"object_name"}),
    ("from_solid", PARAMETERS["from_solid"], {"object_name", "subelements"}),
    ("add_flange", PARAMETERS["add_flange"], {"object_name", "subelements"}),
    ("fold_from_sketch", PARAMETERS["fold_from_sketch"], {"object_name", "subelements", "sketch_name"}),
)}


def capture_revision(document):
    _gui_thread()
    if document is None or App.getDocument(document.Name) is not document:
        raise RuntimeError("Use the exact open document for sheet creation")
    return Operations._capture_revision(document, "", "")


@dataclass(frozen=True)
class PreparedSource:
    revision: Operations.SheetRevision
    arguments_json: str
    document: object
    source: object
    container: object


def _resolve(document, name):
    if not isinstance(name, str) or not name:
        raise ValueError("Use the source's exact internal object name")
    obj = document.getObject(name)
    if obj is None:
        raise ValueError("The sheet source no longer exists in this document")
    Operations.capture_source_revision(obj)
    if any({"Touched", "Invalid"}.intersection(item.State) for item in (obj, *obj.OutListRecursive)):
        raise RuntimeError("Prepare the source geometry before creating a sheet")
    return obj


def prepare(document, arguments, *, expected_revision):
    return _prepare(document, arguments, expected_revision=expected_revision)


def _prepare(document, arguments, *, expected_revision, editing=None):
    revision = capture_revision(document)
    expected = (expected_revision.summary() if isinstance(expected_revision, Operations.SheetRevision)
                else expected_revision)
    if revision.summary() != expected:
        raise RuntimeError("The source document changed; inspect its current revision")
    operation, values = strict_variant_arguments(arguments, _FIELDS,
        defaults={"base_shape": {"container_name": None, "flange_width": 8}})
    for name in PARAMETERS[operation]:
        value = values[name]
        if name in CHOICES:
            if not isinstance(value, str) or value not in CHOICES[name]:
                raise ValueError(f"Choose a supported {name}")
        elif name in ("fill_gaps", "midplane", "reverse", "invert", "invert_bend"):
            if type(value) is not bool:
                raise ValueError(f"{name} must be a boolean")
        else:
            value = Editable._number(value)
            if value < 0 or (name not in ("bend_radius", "k_factor") and value == 0):
                raise ValueError(f"{name} is outside the supported range")
            if name == "k_factor" and value > 1:
                raise ValueError("K-factor must be between zero and one")
            if name == "bend_angle" and value > 180:
                raise ValueError("Bend angle must be greater than zero and at most 180 degrees")
            values[name] = value
    source, container, bend_sketch = None, None, None
    if operation == "base_shape":
        if values["container_name"] is not None:
            container = _resolve(document, values["container_name"])
            if container.TypeId not in ("App::Part", "PartDesign::Body"):
                raise ValueError("Choose a Part or Body container")
    else:
        source = _resolve(document, values["object_name"])
        container = source.getParentGeoFeatureGroup()
        if operation == "base_from_sketch":
            if not source.isDerivedFrom("Sketcher::SketchObject"):
                raise ValueError("Choose an editable sketch as the sheet source")
        else:
            if not hasattr(source, "Shape") or source.Shape.isNull():
                raise ValueError("Choose a solid source")
            names = values["subelements"]
            if (not isinstance(names, list) or not names or len(names) > 256
                    or any(not isinstance(name, str) for name in names)
                    or len(set(names)) != len(names)):
                raise ValueError("Choose distinct flange faces or edges" if operation == "add_flange" else
                                 "Choose distinct faces to remove or edges to rip")
            counts = {"Face": len(source.Shape.Faces), "Edge": len(source.Shape.Edges)}
            for name in names:
                match = re.fullmatch(r"(Face|Edge)([1-9][0-9]{0,8})", name)
                if match is None or int(match[2]) > counts[match[1]]:
                    raise ValueError("A selected source face or edge no longer exists")
            if operation == "fold_from_sketch":
                if len(names) != 1 or not names[0].startswith("Face"):
                    raise ValueError("Choose one planar sheet skin for the internal fold")
                bend_sketch = _resolve(document, values["sketch_name"])
                if (not bend_sketch.isDerivedFrom("Sketcher::SketchObject")
                        or bend_sketch.GeometryCount != 1
                        or not isinstance(bend_sketch.Geometry[0], Part.LineSegment)):
                    raise ValueError("Choose an editable sketch containing one non-construction straight bend line")
                if bend_sketch.getConstruction(0):
                    raise ValueError(
                        f"{bend_sketch.Name}'s only line is construction geometry, so its shape is empty. "
                        "Edit that sketch and turn off Construction Mode for its line, then retry the fold.")
                if bend_sketch.getParentGeoFeatureGroup() is not container:
                    raise ValueError("The bend sketch and sheet must share the same Part or Body container")
                line = bend_sketch.Geometry[0]
                endpoints = [bend_sketch.Placement.multVec(point) for point in (line.StartPoint, line.EndPoint)]
                def on_sketch_plane(face):
                    if not isinstance(face.Surface, Part.Plane):
                        return False
                    origin, normal = face.Vertexes[0].Point, face.normalAt(0, 0)
                    return all(abs((point-origin).dot(normal)) <= 1e-7 for point in endpoints)
                if not on_sketch_plane(source.Shape.getElement(names[0])):
                    # Check the selected face directly; bound extra GUI-thread
                    # metadata inspection used only for repair suggestions.
                    limit = min(counts["Face"], 32)
                    matching = [f"Face{index}" for index in range(1, limit+1)
                                if on_sketch_plane(source.Shape.getElement(f"Face{index}"))]
                    choices = ", ".join(matching) if matching else "none among the checked faces"
                    raise ValueError("Choose a sheet skin on the bend sketch plane. Matching faces: "
                                     + choices + f" (checked {limit} of {counts['Face']} faces).")
    if container is not None and container.TypeId == "PartDesign::Body":
        tip = container.Tip
        if editing is None and tip is not None and tip is not source and tip is not bend_sketch:
            raise ValueError("A sheet base needs an empty Body or its current Tip as source")
    frozen = json.dumps({"operation": operation, **values}, sort_keys=True, allow_nan=False)
    return PreparedSource(revision, frozen, document, source, container)


def arguments(source):
    Operations.capture_source_revision(source)
    operation = next((name for name, cls in _CLASSES.items() if isinstance(source.Proxy, cls)), None)
    if operation is None:
        raise ValueError("Choose a source created by the shared sheet workflow")
    result = {"operation": operation}
    for name, prop in PARAMETERS[operation].items():
        value = getattr(source, prop)
        result[name] = value if isinstance(value, (str, bool)) else float(value)
    if operation == "base_shape":
        container = source.getParentGeoFeatureGroup()
        result["container_name"] = container.Name if container is not None else None
    elif operation == "base_from_sketch":
        result["object_name"] = source.BendSketch.Name if source.BendSketch else None
    else:
        link = source.baseObject
        result.update(object_name=link[0].Name if link else None,
                      subelements=list(link[1]) if link else [])
        if operation == "fold_from_sketch":
            result["sketch_name"] = source.BendLine.Name if source.BendLine else None
    return result


def _expectation(source):
    # No BRep serialization or validation on the GUI thread. Topology identity
    # additionally detects a rebuilt sketch/solid while this request is pending.
    values = _parameter_values(source)
    links = tuple((obj, obj.Shape.hashCode()) for obj in source.OutListRecursive
                  if hasattr(obj, "Shape"))
    selected = tuple(source.baseObject[1]) if hasattr(source, "baseObject") and source.baseObject else ()
    return values, links, selected


def _parameter_values(source):
    operation = next(name for name, cls in _CLASSES.items() if isinstance(source.Proxy, cls))
    return tuple((name, str(getattr(source, prop))) for name, prop in PARAMETERS[operation].items())


def _validate_update_result(source):
    result = Features.get_prepared_source(source)
    for dependent in source.InListRecursive:
        if (isinstance(getattr(dependent, "Proxy", None), Editable.PreparedSheetState)
                and source.Document.isObjectUsableAtCurrentTimelinePosition(dependent)):
            Editable.get_state_geometry(dependent)
    return result


def _source_creation_result(source):
    geometry = Features.get_prepared_source(source)
    result = {"source_geometry": geometry, "shared_state_created": False}
    candidates = geometry.get("reference_faces", ())
    if candidates:
        result["next_step"] = {
            "tool": "sheet_metal.create",
            "arguments": {"operation": "from_source", "object_name": source.Name,
                          "reference_face": candidates[0]["name"]},
            "purpose": "Create linked folded/flat editing while retaining this source's history. The selected candidate is validated during creation.",
        }
    return result


def _queue(source, immediate, queue, *, validate_dependents=False):
    run = Operations.EditRun(source, dict(immediate), _expectation(source),
        revision_reader=Operations.capture_source_revision, owner_reader=Editable._owner,
        expectation_reader=_expectation,
        geometry_reader=_validate_update_result if validate_dependents else Features.get_prepared_source,
        ready_result=(lambda obj: {"source_geometry": Features.get_prepared_source(obj)})
                     if validate_dependents else _source_creation_result)
    try:
        source.Document.recomputeAsync() if queue is None else queue(source.Document)
    except Exception as error:
        run._finish("failed", str(error))
    else:
        run._schedule()
    return run


def _apply_parameters(obj, operation, values):
    for name, prop in PARAMETERS[operation].items():
        setattr(obj, prop, values[name])
        actual = getattr(obj, prop)
        actual = actual if isinstance(actual, (str, bool)) else float(actual)
        if actual != values[name]:
            raise ValueError(f"The upstream source rejected {name}")


def start(prepared, *, transaction_runner=run_human_mutation, recompute_queue=None):
    _gui_thread()
    if not isinstance(prepared, PreparedSource):
        raise TypeError("Prepare an exact sheet source request first")
    # Resolve again before mutation, rejecting deleted/reparented or edited input.
    checked = prepare(prepared.document, json.loads(prepared.arguments_json),
                      expected_revision=prepared.revision)
    if checked.source is not prepared.source or checked.container is not prepared.container:
        raise RuntimeError("The source or its container changed")
    if not callable(transaction_runner) or (recompute_queue is not None and not callable(recompute_queue)):
        raise TypeError("Use callable sheet transaction and recompute callbacks")
    from SheetMetalSourceGui import SourceViewProvider, ensure_commands_registered
    from tool_impl.service.domain_runtime import (
        _ensure_timeline_property, _mark_timeline_operation, finalize_new_timeline_operation,
    )
    ensure_commands_registered()
    values = json.loads(prepared.arguments_json)
    operation = values.pop("operation")
    bend_sketch = prepared.document.getObject(values["sketch_name"]) if operation == "fold_from_sketch" else None
    created = []
    def mutate(document):
        if document is not prepared.document:
            raise RuntimeError("The source transaction belongs to another document")
        obj = document.addObject("Part::FeaturePython", _NAMES[operation])
        if operation == "base_shape":
            Features.BaseShape(obj)
        elif operation == "base_from_sketch":
            Features.BaseBend(obj, prepared.source)
        elif operation == "add_flange":
            Features.Flange(obj, prepared.source, values["subelements"])
        elif operation == "fold_from_sketch":
            if document.getObject(values["sketch_name"]) is not bend_sketch:
                raise RuntimeError("The exact bend sketch changed before creation")
            Features.Fold(obj, prepared.source, values["subelements"], bend_sketch)
        else:
            Features.FromSolid(obj, prepared.source, values["subelements"])
        _apply_parameters(obj, operation, values)
        SourceViewProvider(obj.ViewObject)
        if prepared.container is not None:
            prepared.container.addObject(obj)
            if prepared.container.TypeId == "PartDesign::Body":
                prepared.container.Tip = obj
        inputs = [prepared.source] if prepared.source is not None else []
        if bend_sketch is not None:
            inputs.append(bend_sketch)
        replaced = PartGui.setModelingReplacedInputs(obj, inputs) if inputs else False
        _ensure_timeline_property(obj, "App::PropertyString", "VibeCADTimelineEditCommand",
                                  "Edit this sheet source's exact parameters")
        obj.VibeCADTimelineEditCommand = "SheetMetal_EditSource"
        if replaced or prepared.container is None or prepared.container.TypeId != "PartDesign::Body":
            finalize_new_timeline_operation(obj)
        else:
            _mark_timeline_operation(obj)
            document.finalizeProvisionalTimelineOperationBlock(obj, [obj])
        if prepared.source is not None:
            prepared.source.Visibility = False
        if bend_sketch is not None:
            bend_sketch.Visibility = False
        created.append(obj)
        changed = tuple(object_identity(item) for item in (prepared.source, prepared.container, bend_sketch)
                        if item is not None)
        return NativeMutationDraft(value=obj, created=(object_identity(obj),), changed=changed)
    def verify(document, draft):
        obj = draft.value
        if obj.Document is not document or obj.getParentGeoFeatureGroup() is not prepared.container:
            raise RuntimeError("The created sheet lost its exact container")
        return {"operation": operation, "object_name": obj.Name}
    immediate = transaction_runner(document=prepared.document, transaction_name="Create sheet source",
                                   mutate=mutate, verify=verify)
    return _queue(created[-1], immediate, recompute_queue)


def update(source, changes, *, expected_revision):
    current = Operations.capture_source_revision(source)
    expected = expected_revision.summary() if isinstance(expected_revision, Operations.SheetRevision) else expected_revision
    if current.summary() != expected:
        raise RuntimeError("The sheet source changed; reopen its current parameters")
    values = arguments(source)
    operation = values["operation"]
    if not isinstance(changes, dict) or not changes or set(changes)-set(PARAMETERS[operation]):
        raise ValueError("Choose editable source dimensions or options")
    values.update(changes)
    # Validate the same inputs without applying the second-base insertion rule:
    # this edit retains the exact existing source, its container and its Tip.
    _prepare(source.Document, values, expected_revision=capture_revision(source.Document), editing=source)
    def mutate(document):
        if document is not source.Document:
            raise RuntimeError("The source edit belongs to another document")
        _apply_parameters(source, operation, values)
        return NativeMutationDraft(value=source, changed=(object_identity(source),))
    immediate = run_human_mutation(document=source.Document, transaction_name="Edit sheet source",
        mutate=mutate, verify=lambda document, draft: {"operation": "edit_source", "object_name": source.Name})
    return _queue(source, immediate, None, validate_dependents=True)
