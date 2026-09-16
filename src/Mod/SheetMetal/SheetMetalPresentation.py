# SPDX-License-Identifier: LGPL-2.1-or-later
"""Detached mesh preparation and GUI-owned folded/flat presentation.

Mesh workers own geometry copies and plain immutable data, never Coin nodes.
Warmed switches change one SoSwitch field. Model edits still use the shared
feature and native recompute; presentation does not write Shape or Visibility.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import threading
import weakref

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartGui
from PySide import QtCore, QtWidgets
from pivy import coin

import SheetMetalEditable as Editable


_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sheet-mesh")
_views = weakref.WeakSet()
_mesh_status = None


def _gui_thread():
    if QtCore.QThread.currentThread() != QtWidgets.QApplication.instance().thread():
        raise RuntimeError("Sheet presentation belongs to the GUI thread")


def _update_mesh_status():
    """Keep pending display work visible without replacing another job's status."""
    global _mesh_status
    _gui_thread()
    count = sum(view.pending and not view._closed for view in _views)
    if count and _mesh_status is None:
        bar = Gui.getMainWindow().statusBar()
        _mesh_status = QtWidgets.QLabel(bar)
        _mesh_status.setObjectName("SheetMetalDisplayStatus")
        bar.addPermanentWidget(_mesh_status)
    if _mesh_status is not None:
        _mesh_status.setText(QtCore.QCoreApplication.translate(
            "SheetMetal", "Loading sheet display (%1)\u2026").replace("%1", str(count)))
        _mesh_status.setVisible(bool(count))


@dataclass(frozen=True)
class Mesh:
    points: tuple
    normals: tuple
    triangles: tuple
    face_ids: tuple
    edges: tuple


@dataclass(frozen=True)
class SheetPick:
    """A resolved skin pick bound to one exact feature and prepared display."""
    representation: str
    region: int
    folded: tuple
    flat: tuple
    input_hash: str
    generation: int
    _owner: object = field(repr=False, compare=False)


def prepare_pair(shapes, placement, deflection, cancelled):
    """Prepare both meshes using the folded object's local display frame."""
    inverse = placement.inverse()
    rotation = inverse.Rotation
    meshes = []
    for original in shapes:
        if cancelled.is_set():
            return None
        shape = original.copy()
        points, normals, triangles, face_ids, edges = [], [], [], [], []
        for face_index, face in enumerate(shape.Faces, 1):
            if cancelled.is_set():
                return None
            vertices, facets = face.tessellateDetached(deflection)
            offset = len(points)
            surface = face.Surface
            # A plane's normal is constant, including for faces with many holes.
            # normalAt rebuilds a trimmed-face adaptor; do that once, not once
            # per vertex. Keep the face orientation and curved-face evaluation.
            planar_normal = (tuple(rotation.multVec(face.normalAt(
                *surface.parameter(vertices[0]))))
                if vertices and isinstance(surface, Part.Plane) else None)
            for vertex in vertices:
                points.append(tuple(inverse.multVec(vertex)))
                normals.append(planar_normal if planar_normal is not None else
                               tuple(rotation.multVec(face.normalAt(*surface.parameter(vertex)))))
            triangles.extend(tuple(index+offset for index in triangle) for triangle in facets)
            face_ids.extend([face_index]*len(facets))
        for edge in shape.Edges:
            if cancelled.is_set():
                return None
            vertices = edge.discretize(Deflection=deflection)
            edges.append(tuple(tuple(inverse.multVec(vertex)) for vertex in vertices))
        meshes.append(Mesh(tuple(points), tuple(normals), tuple(triangles),
                           tuple(face_ids), tuple(edges)))
    return tuple(meshes)


def _node(mesh, color, transparency):
    _gui_thread()
    root = coin.SoSeparator()
    material = coin.SoMaterial()
    material.diffuseColor = color
    material.transparency = transparency
    root.addChild(material)
    coordinates = coin.SoCoordinate3()
    coordinates.point.setValues(mesh.points)
    root.addChild(coordinates)
    normals = coin.SoNormal()
    normals.vector.setValues(mesh.normals)
    root.addChild(normals)
    binding = coin.SoNormalBinding()
    binding.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
    root.addChild(binding)
    faces = coin.SoIndexedFaceSet()
    indices = tuple(index for triangle in mesh.triangles for index in (*triangle, -1))
    faces.coordIndex.setValues(0, len(indices), indices)
    faces.normalIndex.setValues(0, len(indices), indices)
    root.addChild(faces)
    edge_root = coin.SoSeparator()
    edge_material = coin.SoBaseColor()
    edge_material.rgb = (.12, .12, .12)
    edge_root.addChild(edge_material)
    lighting = coin.SoLightModel()
    lighting.model = coin.SoLightModel.BASE_COLOR
    edge_root.addChild(lighting)
    edge_coordinates = coin.SoCoordinate3()
    edge_coordinates.point.setValues(tuple(point for edge in mesh.edges for point in edge))
    edge_root.addChild(edge_coordinates)
    lines = coin.SoLineSet()
    lines.numVertices.setValues(0, len(mesh.edges), tuple(len(edge) for edge in mesh.edges))
    edge_root.addChild(lines)
    root.addChild(edge_root)
    return root


def _same(left, right):
    return (left is not None and right is not None and left[0] == right[0]
            and left[1] is right[1] and left[2].isEqual(right[2]) and left[3].isEqual(right[3]))


def _refresh(reference):
    view = reference()
    if view is not None and not view._closed:
        view._refresh_queued = False
        if view._alive():
            # Hidden history features need geometry for editing, not display
            # meshes. Visibility changes request their current revision on demand.
            # Link instances share their source's scene even when it is hidden.
            if not view._view.Visibility and not App.getLinksTo(view._object):
                return
            if (view._object.Document.Restoring or view._object.Document.Recomputing
                    or view._object.Document.RecomputePending):
                view._queue_refresh()
            else:
                view.request()


def _close(reference):
    view = reference()
    # Undo may restore/rebind this Python provider before the deferred deletion
    # callback runs. A live restored owner must not be closed by that old event.
    if view is not None and not view._closed and not view._alive():
        view.close()


def _finished(reference, generation, signature, future):
    # This callback runs on a worker and captures only a weak GUI reference.
    try:
        data, error = future.result(), None
    except Exception as failure:
        data, error = None, str(failure)
    Gui.deferToNextFrame(_publish, reference, generation, signature, data, error)


def _publish(reference, generation, signature, meshes, error):
    view = reference()
    if view is None or view._closed or not view._alive() or generation != view._generation:
        return
    _gui_thread()
    view.pending = False
    view._requested = None
    _update_mesh_status()
    if not _same(view._capture(), signature):
        return
    view.error = error
    if meshes is None:
        return
    appearance = view._view.ShapeAppearance[0]
    color = tuple(appearance.DiffuseColor)[:3]
    nodes = tuple(_node(mesh, color, view._view.Transparency/100) for mesh in meshes)
    # Publish the complete pair in one GUI callback. Until here the old nodes
    # remain attached and continue to render while geometry/meshing is pending.
    view._switch.removeAllChildren()
    for node in nodes:
        view._switch.addChild(node)
    view.cached_nodes = nodes
    view._meshes = meshes
    view._published = signature
    view._switch.whichChild = 0 if view.mode == "folded" else 1


class SheetViewProvider:
    def __init__(self, view=None):
        self._reset()
        if view is not None:
            view.Proxy = self

    def _reset(self):
        self._closed = False
        self._view = self._object = self._document = self._switch = None
        self._native_mode_switch = None
        self._mode_index = None
        self._generation = getattr(self, "_generation", 0) + 1
        self._requested = self._published = None
        self._cancelled = threading.Event()
        self._refresh_queued = False
        self._meshes = ()
        self.cached_nodes = ()
        self.pending = False
        self.error = None
        self.mode = "folded"

    def attach(self, view):
        _gui_thread()
        if not hasattr(self, "_closed") or self._closed:
            self._reset()
        self._view, self._object = view, view.Object
        self._document = self._object.Document
        self._switch = coin.SoSwitch()
        self._switch.setName("SheetMetalRepresentation")
        self._switch.whichChild = -1
        self._native_mode_switch = view.SwitchNode
        self._mode_index = self._native_mode_switch.getNumChildren()
        view.addDisplayMode(self._switch, "Sheet")
        _views.add(self)
        self._queue_refresh()

    def _alive(self):
        try:
            Editable._owner(self._object)
            return True
        except (RuntimeError, ReferenceError, AttributeError, TypeError):
            return False

    def resume(self, view):
        """Reconnect a native Undo-retained provider without adding scene nodes."""
        _gui_thread()
        if not self._closed or view.Proxy is not self:
            return
        Editable._owner(view.Object)
        index, mode = self._mode_index, self.mode
        modes = view.SwitchNode
        if index is None or not 0 <= index < modes.getNumChildren():
            raise RuntimeError("The restored sheet lost its native display mode")
        switch = modes.getChild(index)
        if (not switch.isOfType(coin.SoSwitch.getClassTypeId())
                or str(switch.getName()) != "SheetMetalRepresentation"):
            raise RuntimeError("The restored sheet display mode changed identity")
        self._reset()
        self._switch, self._mode_index, self.mode = switch, index, mode
        # getChild() returns a borrowed Coin node. SwitchNode's binding owns a
        # native reference; retain it until GUI-thread close has released the
        # child, including the deferred interval after document deletion.
        self._native_mode_switch = modes
        self._view, self._object = view, view.Object
        self._document = view.Object.Document
        _views.add(self)
        self._queue_refresh()

    def _capture(self):
        if not self._alive():
            return None
        obj = self._object
        # A pending or failed Python restore can leave saved shapes without a
        # feature proxy. They are not a prepared sheet and must not be published.
        if not isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState):
            return None
        if (obj.Document.Recomputing or obj.Document.RecomputePending
                or {"Touched", "Invalid"}.intersection(obj.State)):
            return None
        try:
            geometry = Editable.get_state_geometry(obj)
        except (RuntimeError, ValueError):
            # Saved outputs may be displayed immediately after restore. They
            # cannot be returned by current() for editing/export until prepared.
            if obj.Proxy._geometry is not None or any(
                    {"Touched", "Invalid"}.intersection(dep.State) for dep in obj.OutListRecursive):
                return None
            geometry = None
        folded, flat = obj.Shape, obj.FlatShape
        if folded.isNull() or flat.isNull():
            return None
        return obj.PreparedInputHash, geometry, folded, flat

    def _queue_refresh(self):
        if not self._closed and not self._refresh_queued:
            self._refresh_queued = True
            Gui.deferToNextFrame(_refresh, weakref.ref(self))

    def request(self, *, force=False):
        _gui_thread()
        signature = self._capture()
        if signature is None or (not force and (
                _same(signature, self._requested) or _same(signature, self._published))):
            return
        self._cancelled.set()
        self._cancelled = threading.Event()
        self._generation += 1
        self._requested = signature
        self.pending, self.error = True, None
        future = _workers.submit(prepare_pair, signature[2:], self._object.Placement,
                                 .1, self._cancelled)
        _update_mesh_status()
        reference, generation = weakref.ref(self), self._generation
        future.add_done_callback(lambda done: _finished(reference, generation, signature, done))

    @property
    def ready(self):
        return bool(self.cached_nodes) and not self.pending and self.error is None

    def current(self):
        _gui_thread()
        if (self._closed or self.pending or not _same(self._capture(), self._published)
                or self._published[1] is None):
            raise RuntimeError("Prepare and display the current sheet revision first")
        return self._published[0]

    def current_display(self):
        """Read the current mesh pair, including saved outputs after restore.

        This validates presentation only. Picks and mapped edits still require
        current(), which additionally verifies a prepared geometry mapping.
        """
        _gui_thread()
        if (self._closed or self.pending or self.error is not None or not self.cached_nodes
                or not _same(self._capture(), self._published)):
            raise RuntimeError("Prepare and display the current sheet revision first")
        return self._published[0]

    def switch(self, mode):
        _gui_thread()
        if mode not in ("folded", "flat"):
            raise ValueError("Sheet representation must be folded or flat")
        if self._closed or not self.cached_nodes:
            raise RuntimeError("The sheet meshes are not available")
        self.mode = mode
        self._switch.whichChild = 0 if mode == "folded" else 1

    def pick(self, picked_point):
        """Map a Coin face hit onto the exact sheet surfaces, never its chord.

        Coin object coordinates are relative to the native object placement;
        applying it once recovers the container frame used by shared geometry.
        The returned record retains no Coin object or borrowed picked-point data.
        """
        fingerprint = self.current()
        index = 0 if self.mode == "folded" else 1
        if (picked_point is None
                or not picked_point.getPath().containsNode(self.cached_nodes[index])):
            raise ValueError("The pick does not belong to this displayed sheet")
        detail = picked_point.getDetail()
        if detail is None or not detail.isOfType(coin.SoFaceDetail.getClassTypeId()):
            raise ValueError("Select a sheet face")
        triangle = PartGui.getPickedFaceIndex(picked_point)
        mesh = self._meshes[index]
        if not 0 <= triangle < len(mesh.face_ids):
            raise ValueError("The picked triangle is not in the current sheet mesh")
        face_index = mesh.face_ids[triangle]-1
        face = self._published[index+2].Faces[face_index]
        point = self._object.Placement.multVec(App.Vector(*picked_point.getObjectPoint().getValue()))
        point = face.valueAt(*face.Surface.parameter(point))
        geometry = self._published[1]
        region, folded, flat = geometry.map_surface_point(self.mode, face_index, point)
        return SheetPick(self.mode, region, tuple(folded), tuple(flat), fingerprint,
                         self._generation, self._object)

    def validate_pick(self, picked):
        """Reject picks after an edit, restore, deletion, or mesh replacement.

        Switching modes and moving the camera do not invalidate a mapped point.
        Call immediately before applying its model operation.
        """
        fingerprint = self.current()
        if (not isinstance(picked, SheetPick) or picked._owner is not self._object
                or picked.input_hash != fingerprint or picked.generation != self._generation):
            raise RuntimeError("Select the current sheet revision again")
        return picked

    def pick_screen(self, view, position):
        """Resolve one viewport pixel using its current camera and visible scene.

        Use the nearest visible primitive, so another object occluding the sheet
        cannot silently select material behind it. No active-document lookup or
        global selection mutation is used to choose the model owner.
        """
        self.current()
        views = Gui.getDocument(self._document.Name).mdiViewsOfType("Gui::View3DInventor")
        if view not in views:
            raise ValueError("The viewport belongs to another document")
        if len(position) != 2 or any(type(value) is not int for value in position):
            raise ValueError("A sheet pick requires two integer viewport coordinates")
        manager = view.getViewer().getSoRenderManager()
        viewport = manager.getViewportRegion()
        width, height = viewport.getWindowSize().getValue()
        if not 0 <= position[0] < width or not 0 <= position[1] < height:
            raise ValueError("The point is outside this viewport")
        action = coin.SoRayPickAction(viewport)
        action.setPoint(coin.SbVec2s(*position))
        action.setRadius(0)
        action.apply(manager.getSceneGraph())
        return self.pick(action.getPickedPoint())

    def updateData(self, obj, name):
        if name in ("Shape", "FlatShape", "PreparedInputHash"):
            self._queue_refresh()

    def onChanged(self, view, name):
        if name == "Visibility":
            if view.Visibility:
                self._queue_refresh()
            elif self.pending and not App.getLinksTo(self._object):
                # Superseded history can already be queued when an operation
                # hides it. Stop that work without discarding its warmed cache.
                self._cancelled.set()
                self._generation += 1
                self._requested = None
                self.pending = False
                _update_mesh_status()
        if name in ("ShapeAppearance", "Transparency") and self.cached_nodes:
            _gui_thread()
            color = tuple(view.ShapeAppearance[0].DiffuseColor)[:3]
            for node in self.cached_nodes:
                material = node.getChild(0)
                material.diffuseColor = color
                material.transparency = view.Transparency/100

    def finishRestoring(self):
        # attach/updateData can run while restored dependencies are still
        # incomplete. Request saved meshes after the native restore boundary.
        self._queue_refresh()
        if getattr(self._object, "VibeCADTimelineEditCommand", None) == "SheetMetal_EditParameters":
            # History must work when the file opens in another workbench.
            # Register the stored editor without activating a workbench or
            # changing the document, selection, or ribbon surface.
            from SheetMetalGui import ensure_commands_registered
            ensure_commands_registered()

    def getDisplayModes(self, view):
        return ["Sheet"]

    def getIcon(self):
        import os
        import SheetMetalTools
        return os.path.join(SheetMetalTools.icons_path, "SMLogo.svg")

    def getTreeViewDetails(self):
        """Describe this shared definition without adding model/history objects."""
        import os
        import SheetMetalTools
        _gui_thread()
        if self._closed or not self._alive():
            return []
        sheet = self._object
        Editable._editable_owner(sheet)
        rows = [
            {"key": "representation:folded", "label": "Folded", "secondary_text": "3D sheet",
             "tooltip": "Double-click to show the cached folded sheet",
             "icon": os.path.join(SheetMetalTools.icons_path, "SheetMetal_AddWall.svg")},
            {"key": "representation:flat", "label": "Flat", "secondary_text": "Developed sheet",
             "tooltip": "Double-click to show the cached flat sheet",
             "icon": os.path.join(SheetMetalTools.icons_path, "SheetMetal_Unfold.svg")},
            {"key": "dimensions", "label": "Dimensions", "tooltip": "Edit source dimensions"},
            {"key": "material", "label": "Material", "secondary_text": sheet.Material or "Not set",
             "tooltip": "Edit material and bend allowance"},
        ]
        try:
            operations = Editable._definition(sheet.Definition)["operations"]
        except ValueError as error:
            rows.append({"key": "invalid", "label": "Invalid sheet definition", "tooltip": str(error)})
            return rows
        for index, operation in enumerate(operations, 1):
            circle = operation["kind"] == "circle"
            profile = None if circle else getattr(sheet, operation["profile"], None)
            rows.append({"key": "operation:" + operation["id"],
                         "label": f"Hole {index}" if circle else f"Sketch cut {index}",
                         "secondary_text": (f"Radius {operation['radius']:g} mm" if circle
                                            else profile.Label if profile is not None else "Missing sketch"),
                         "tooltip": "Double-click to edit this shared cut",
                         "icon": os.path.join(SheetMetalTools.icons_path, "SheetMetal_AddCutout.svg")})
        return rows

    def treeViewDetailsAffectedBy(self, property_name):
        return property_name in ("Definition", "Material", "KFactor", "SourceFace", "ProfileSources")

    def activateTreeViewDetail(self, key):
        _gui_thread()
        sheet = self._object
        if Editable._editable_owner(sheet) is not App.ActiveDocument:
            raise RuntimeError("Activate this sheet's document before using its tree controls")
        if key in ("representation:folded", "representation:flat"):
            self.switch(key.split(":", 1)[1])
            return True
        mode, operation = None, None
        if key in ("dimensions", "material"):
            mode = "parameters" if key == "dimensions" else "materials"
        elif key.startswith("operation:"):
            identity = key[len("operation:"):]
            operation = next((entry for entry in Editable._definition(sheet.Definition)["operations"]
                              if entry["id"] == identity), None)
            if operation is None:
                raise ValueError("This sheet operation no longer exists")
            mode = "cuts"
        if mode is None:
            return False
        if Gui.Control.activeDialog():
            raise RuntimeError("Close the current task panel first")
        from SheetMetalOperations import capture_revision
        capture_revision(sheet)
        from SheetMetalGui import SheetPanel
        panel = SheetPanel(sheet, mode)
        if operation is not None:
            panel.operations.setCurrentIndex(panel.operations.findData(operation["id"]))
            if operation["kind"] == "circle":
                panel.radius.setValue(operation["radius"])
        Gui.Control.showDialog(panel)
        return True

    def getDefaultDisplayMode(self):
        return "Sheet"

    def setDisplayMode(self, mode):
        return "Sheet"

    def onDelete(self, view, subelements):
        self.close()
        return True

    def close(self):
        _gui_thread()
        self._closed = True
        self._cancelled.set()
        self.cached_nodes = self._meshes = ()
        if self._switch is not None:
            self._switch.removeAllChildren()
        # Native Undo retains its empty display-mode node. Keep only its slot
        # number; Redo resolves it from the exact native provider. This proxy
        # releases every model/scene/cache reference at deletion.
        self._requested = self._published = None
        self._switch = self._view = self._object = self._document = None
        self._native_mode_switch = None
        _views.discard(self)
        _update_mesh_status()

    def dumps(self):
        return None

    def loads(self, state):
        self._reset()


class _Observer:
    def slotRecomputedDocument(self, document):
        for view in tuple(_views):
            view._queue_refresh()

    def slotDeletedObject(self, obj):
        for view in tuple(_views):
            if view._object is obj:
                Gui.deferToNextFrame(_close, weakref.ref(view))

    def slotDeletedDocument(self, document):
        for view in tuple(_views):
            if view._document is document:
                Gui.deferToNextFrame(_close, weakref.ref(view))


_observer = _Observer()
App.addDocumentObserver(_observer)


class _GuiObserver:
    def slotCreatedObject(self, view):
        proxy = getattr(view, "Proxy", None)
        if isinstance(proxy, SheetViewProvider) and proxy._closed:
            proxy.resume(view)


_gui_observer = _GuiObserver()
Gui.addDocumentObserver(_gui_observer)


def create_presented_sheet(source, reference_face, *, name="EditableSheet", tree_details=False,
                           consume_source=True):
    """Create the shared feature with cached presentation; caller transacts."""
    _gui_thread()
    view_type = ("PartGui::ViewProviderCachedDetailsPython" if tree_details
                 else "PartGui::ViewProviderCachedPython")
    obj = Editable.create_sheet(source, reference_face, name=name, view_type=view_type)
    SheetViewProvider(obj.ViewObject)
    # Consuming the source is part of feature creation, not view switching.
    if consume_source:
        source.Visibility = False
    return obj
