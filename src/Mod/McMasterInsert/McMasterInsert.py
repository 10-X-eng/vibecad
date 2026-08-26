# SPDX-License-Identifier: LGPL-2.1-or-later
"""Browse the McMaster-Carr catalog and import downloaded CAD into VibeCAD.

Opens McMaster's live website in a catalog window (same idea as Fusion 360 /
SolidWorks). When you download 3-D STEP from a product page, the file is
imported into the active document. No McMaster API key and no part number
required up front.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

import FreeCAD as App

CAD_SUFFIXES = (
    ".step",
    ".stp",
    ".stpz",
    ".iges",
    ".igs",
    ".sat",
    ".sab",
    ".x_t",
    ".x_b",
    ".sldprt",
    ".zip",
)
PART_NUMBER_RE = re.compile(r"^[0-9]{2,6}[A-Za-z][0-9A-Za-z]{1,8}$")
FILENAME_PN_RE = re.compile(r"([0-9]{2,6}[A-Za-z][0-9A-Za-z]{1,8})")
CATALOG_URL = "https://www.mcmaster.com/"


def cache_root() -> Path:
    path = Path(App.getUserAppDataDir()) / "McMasterCache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def inbox_dir() -> Path:
    path = cache_root() / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_session_inbox() -> Path:
    """Create an inbox owned by one catalog window and one import."""
    path = inbox_dir() / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def downloads_dir() -> Path:
    return Path.home() / "Downloads"


def webview2_profile_root() -> Path:
    """Persistent WebView2 data so McMaster login survives VibeCAD restarts."""
    local_app_data = str(os.environ.get("LOCALAPPDATA", "") or "").strip()
    if local_app_data:
        root = Path(local_app_data) / "VibeCAD" / "McMasterBrowser"
    else:
        root = Path(App.getUserAppDataDir()) / "McMasterBrowser"
    root.mkdir(parents=True, exist_ok=True)
    return root


def webview2_helper_path() -> Path:
    return Path(__file__).resolve().parent / "McMasterCatalogWebView2.exe"


def linux_helper_path() -> Path:
    return Path(__file__).resolve().parent / "McMasterCatalogWebKit.py"


def linux_helper_python() -> str:
    for candidate in (Path("/usr/bin/python3"), Path("/bin/python3")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def helper_app_path() -> Path:
    return Path(__file__).resolve().parent / "McMasterCatalog.app"


def helper_path() -> Path:
    bundled = helper_app_path() / "Contents" / "MacOS" / "McMasterCatalog"
    if bundled.is_file():
        return bundled
    return Path(__file__).resolve().parent / "McMasterCatalog"


def normalize_part_number(raw: str) -> str:
    token = (raw or "").strip()
    if "mcmaster.com" in token.lower():
        token = token.split("?", 1)[0].rstrip("/")
        token = token.rsplit("/", 1)[-1]
    token = token.strip().upper().replace(" ", "")
    return token.strip("/")


def product_url(part_number: str) -> str:
    return f"https://www.mcmaster.com/{part_number}"


def part_number_from_filename(name: str) -> str:
    match = FILENAME_PN_RE.search(Path(name).stem.upper().replace(" ", ""))
    if match and PART_NUMBER_RE.match(match.group(1)):
        return match.group(1)
    return ""


def cached_cad(part_number: str) -> Path | None:
    folder = cache_root() / part_number
    if not folder.is_dir():
        return None
    for path in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file() and path.suffix.lower() in CAD_SUFFIXES and path.suffix.lower() != ".zip":
            return path
    return None


def unpack_if_zip(path: Path, part_number: str) -> Path:
    if path.suffix.lower() != ".zip":
        return path
    dest = cache_root() / part_number
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(dest)
    for candidate in dest.rglob("*"):
        if (
            candidate.is_file()
            and candidate.suffix.lower() in CAD_SUFFIXES
            and candidate.suffix.lower() != ".zip"
        ):
            return candidate
    raise RuntimeError(f"ZIP {path.name} did not contain a CAD file")


def store_in_cache(source: Path, part_number: str) -> Path:
    unpacked = unpack_if_zip(source, part_number or "UNNUMBERED")
    key = part_number or part_number_from_filename(unpacked.name) or "UNNUMBERED"
    dest_dir = cache_root() / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / unpacked.name
    if unpacked.resolve() != dest.resolve():
        shutil.copy2(unpacked, dest)
    return dest


_CAD_VARIANT_PREFIX = re.compile(
    r"^(NO THREADS|NO-THREADS|NOTHREADS|SIMPLIFIED)\s+",
    re.IGNORECASE,
)


def catalog_description(part_number: str, source_path: Path) -> str:
    """McMaster catalog summary from the STEP filename, without CAD-variant prefixes."""
    stem = re.sub(r"[_\s]+", " ", source_path.stem).strip()
    title = stem
    if part_number:
        prefix = re.compile(re.escape(part_number) + r"[\s\-]*", re.IGNORECASE)
        title = prefix.sub("", stem, count=1).strip(" -")
    title = _CAD_VARIANT_PREFIX.sub("", title).strip()
    title = re.sub(r"\s+", " ", title).strip()
    if not title or (part_number and title.upper() == part_number.upper()):
        return "McMaster-Carr catalog part"
    return title


def _legal_object_name(prefix: str, part_number: str) -> str:
    raw = re.sub(r"[^0-9A-Za-z_]", "_", part_number or "Part")
    if not raw:
        raw = prefix
    elif not raw[0].isalpha():
        raw = f"_{raw}"
    return raw[:50]


def _add_named_object(doc, type_id: str, visible_name: str, fallback_prefix: str):
    """Create an object whose tree name is the catalog number when possible."""
    visible = str(visible_name or fallback_prefix).strip()
    candidates = []
    if visible:
        candidates.append(visible)
        if visible[0].isdigit():
            candidates.append(f"_{visible}")
    candidates.append(_legal_object_name(fallback_prefix, visible))
    candidates.append(fallback_prefix)
    seen: set[str] = set()
    obj = None
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            obj = doc.addObject(type_id, name)
        except Exception:
            obj = None
        if obj is not None:
            break
    if obj is None:
        raise RuntimeError(f"could not create {type_id}")
    _set_label(obj, visible)
    return obj


def _set_label(obj, label: str) -> None:
    wanted = str(label or "").strip()
    if obj is None or not wanted:
        return
    try:
        obj.Label = wanted
    except Exception as exc:
        App.Console.PrintWarning(f"McMaster: could not set Label on {obj.Name}: {exc}\n")
        return
    actual = str(getattr(obj, "Label", "") or "")
    if actual != wanted:
        App.Console.PrintWarning(
            f"McMaster: {obj.Name} Label is {actual!r}, wanted {wanted!r}\n"
        )


def _clear_description(obj) -> None:
    for attr in ("Label2", "Description"):
        try:
            if hasattr(obj, attr):
                setattr(obj, attr, "")
        except Exception:
            pass


def _stamp_metadata(obj, part_number: str, source_path: Path) -> None:
    label = part_number or source_path.stem
    description = catalog_description(part_number, source_path)
    _set_label(obj, label)
    try:
        obj.Label2 = description
    except Exception:
        pass
    try:
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            view.ToolTip = description
    except Exception:
        pass
    group = "McMaster"
    specs = (
        ("Vendor", "App::PropertyString", "McMaster-Carr"),
        ("PartNumber", "App::PropertyString", part_number),
        ("Description", "App::PropertyString", description),
        ("SourceURL", "App::PropertyString", product_url(part_number) if part_number else CATALOG_URL),
        ("SourceFile", "App::PropertyString", str(source_path)),
    )
    for name, ptype, value in specs:
        try:
            if not hasattr(obj, name):
                adder = getattr(obj, "addProperty", None)
                if callable(adder):
                    adder(ptype, name, group, "")
            setattr(obj, name, value)
        except Exception:
            continue


def _stamp_body(
    obj,
    part_number: str,
    source_path: Path,
    role: str = "Body",
) -> None:
    base_label = part_number or source_path.stem
    _set_label(obj, f"{base_label} {role}".strip())
    _clear_description(obj)


def _name_imported_tree(component, part_number: str, source_path: Path) -> None:
    """Apply stable, unique labels to the imported component hierarchy."""
    _stamp_metadata(component, part_number, source_path)
    body_index = 0
    geometry_index = 0
    for child in list(getattr(component, "Group", []) or []):
        if _is_origin_object(child):
            continue
        if _type_id(child) == "PartDesign::Body":
            body_index += 1
            body_role = "Body" if body_index == 1 else f"Body {body_index}"
            _stamp_body(child, part_number, source_path, body_role)
            owned = list(getattr(child, "Group", []) or [])
            tip = getattr(child, "Tip", None)
            if tip is not None and tip not in owned:
                owned.append(tip)
            for inner in owned:
                if _is_origin_object(inner):
                    continue
                geometry_index += 1
                role = "Geometry"
                if geometry_index > 1:
                    role = f"Geometry {geometry_index}"
                _stamp_body(inner, part_number, source_path, role)


_SKIP_TRANSFORM_TYPES = (
    "App::Origin",
    "App::Line",
    "App::Plane",
    "App::OriginFeature",
)


def _shape_volume(obj) -> float:
    try:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            return 0.0
        return abs(float(shape.Volume))
    except Exception:
        return 0.0


def _type_id(obj) -> str:
    return str(getattr(obj, "TypeId", "") or "")


def _is_origin_object(obj) -> bool:
    tid = _type_id(obj)
    name = str(getattr(obj, "Name", "") or "")
    return (
        tid in _SKIP_TRANSFORM_TYPES
        or "Origin" in tid
        or bool(re.fullmatch(r"Origin\d*", name))
        or name == "VibeCADTimeline"
    )


def _classify_structure(doc, obj) -> None:
    classify = getattr(doc, "classifyProvisionalTimelineInternalObject", None)
    if callable(classify):
        classify(obj)


def _active_component():
    try:
        import FreeCADGui as Gui

        view = Gui.activeView()
        if view is None:
            return None
        for key in ("part", "pdcomponent"):
            candidate = view.getActiveObject(key)
            if _type_id(candidate) in ("PartDesign::Component", "App::Part"):
                return candidate
    except Exception:
        return None
    return None


def _import_roots(created: list) -> list:
    created_set = set(created)
    roots = []
    for obj in created:
        if obj is None or _is_origin_object(obj):
            continue
        parent = None
        try:
            parent = obj.getParentGeoFeatureGroup()
        except Exception:
            parent = None
        if parent is not None and parent in created_set:
            continue
        roots.append(obj)
    return roots


def _parent_of(obj):
    try:
        return obj.getParentGeoFeatureGroup()
    except Exception:
        return None


def _adopt(container, obj) -> bool:
    if obj is None or container is None or obj is container:
        return False
    current = _parent_of(obj)
    if current is container:
        return True
    if current is not None:
        remover = getattr(current, "removeObject", None)
        if callable(remover):
            try:
                remover(obj)
            except Exception:
                pass
    try:
        container.addObject(obj)
    except Exception as exc:
        App.Console.PrintWarning(
            f"McMaster: {container.Name}.addObject({obj.Name}) failed: {exc}\n"
        )
        return _parent_of(obj) is container
    return _parent_of(obj) is container


def _shape_of(obj):
    for candidate in (getattr(obj, "Tip", None), obj):
        if candidate is None:
            continue
        shape = getattr(candidate, "Shape", None)
        if shape is not None and not getattr(shape, "isNull", lambda: True)():
            return candidate, shape
    return None, None


def _copy_into_body(body, source, part_number: str) -> bool:
    _source_obj, shape = _shape_of(source)
    if shape is None:
        return False
    feature = body.newObject(
        "PartDesign::Feature",
        _legal_object_name("Solid", part_number),
    )
    try:
        feature.Shape = shape.copy() if hasattr(shape, "copy") else shape
    except Exception:
        feature.Shape = shape
    try:
        src_vo = getattr(_source_obj, "ViewObject", None)
        dst_vo = getattr(feature, "ViewObject", None)
        if src_vo is not None and dst_vo is not None and hasattr(src_vo, "DiffuseColor"):
            dst_vo.DiffuseColor = src_vo.DiffuseColor
    except Exception:
        pass
    try:
        body.Tip = feature
    except Exception:
        pass
    return True


def _discard(doc, obj) -> None:
    if obj is None:
        return
    try:
        if getattr(obj, "ViewObject", None) is not None:
            obj.ViewObject.Visibility = False
    except Exception:
        pass
    try:
        doc.removeObject(obj.Name)
    except Exception:
        pass


def _promote_to_component(doc, created: list, part_number: str, source_path: Path):
    """Always make a new PartDesign::Component and put the imported solid in it."""
    label = part_number or source_path.stem
    skip = set()
    imported = []
    for obj in created:
        if obj is None or _is_origin_object(obj):
            continue
        if _type_id(obj) == "PartDesign::Component":
            skip.add(obj)
            continue
        imported.append(obj)

    component = _add_named_object(
        doc,
        "PartDesign::Component",
        label,
        "Component",
    )
    if component is None or _type_id(component) != "PartDesign::Component":
        raise RuntimeError("VibeCAD did not create a PartDesign::Component")
    _classify_structure(doc, component)
    _set_label(component, label)
    _stamp_metadata(component, part_number, source_path)

    placeholder = _add_named_object(
        doc,
        "PartDesign::Body",
        "McMaster Import Body",
        "Body",
    )
    _classify_structure(doc, placeholder)
    if not _adopt(component, placeholder):
        raise RuntimeError("could not add Body to Component")

    imported_bodies = [obj for obj in imported if _type_id(obj) == "PartDesign::Body"]
    imported_other = [obj for obj in imported if _type_id(obj) != "PartDesign::Body"]
    leftovers = []
    kept_bodies = []

    for obj in imported_bodies:
        if _adopt(component, obj):
            body_index = len(kept_bodies) + 1
            role = "Body" if body_index == 1 else f"Body {body_index}"
            _stamp_body(obj, part_number, source_path, role)
            kept_bodies.append(obj)
        elif _copy_into_body(placeholder, obj, part_number):
            leftovers.append(obj)
        else:
            App.Console.PrintWarning(f"McMaster: could not adopt body {obj.Name}\n")

    if kept_bodies:
        leftovers.append(placeholder)
    else:
        _stamp_body(placeholder, part_number, source_path)
        kept_bodies.append(placeholder)
        geometry_index = 0
        for obj in imported_other:
            if _parent_of(obj) in imported_bodies:
                leftovers.append(obj)
                continue
            if _adopt(placeholder, obj):
                try:
                    placeholder.Tip = obj
                except Exception:
                    pass
                geometry_index += 1
                role = "Geometry" if geometry_index == 1 else f"Geometry {geometry_index}"
                _stamp_body(obj, part_number, source_path, role)
            elif _copy_into_body(placeholder, obj, part_number):
                leftovers.append(obj)

    for obj in leftovers:
        if obj not in (component,) and obj not in kept_bodies:
            _discard(doc, obj)
    for obj in skip:
        if obj is not component and _component_is_empty(obj):
            _discard(doc, obj)

    owned_bodies = [
        child
        for child in list(getattr(component, "Group", []) or [])
        if _type_id(child) == "PartDesign::Body"
    ]
    if not owned_bodies:
        raise RuntimeError("imported geometry did not land inside the Component")

    try:
        import FreeCADGui as Gui

        view = Gui.activeView()
        if view is not None:
            view.setActiveObject("part", component)
    except Exception:
        pass
    App.Console.PrintMessage(
        f"McMaster: component {component.Name} Label={component.Label!r} "
        f"bodies={[b.Name for b in owned_bodies]}\n"
    )
    return component


def _component_is_empty(component) -> bool:
    owned = [
        child
        for child in list(getattr(component, "Group", []) or [])
        if not _is_origin_object(child)
    ]
    return not owned


def _transform_target(objects: list):
    usable = [
        obj
        for obj in objects
        if obj is not None and not _is_origin_object(obj)
    ]
    if not usable:
        return None
    for type_id in (
        "PartDesign::Component",
        "App::Part",
        "App::DocumentObjectGroup",
        "PartDesign::Body",
    ):
        for obj in usable:
            if _type_id(obj) == type_id:
                return obj
    placed = [obj for obj in usable if hasattr(obj, "Placement")]
    if not placed:
        return usable[0]
    return max(placed, key=_shape_volume)


def _show_placement_dialog(obj) -> None:
    """Fallback XYZ / rotation panel when VibeCAD has no transform command."""
    from PySide import QtCore, QtWidgets
    import FreeCADGui as Gui

    original = App.Placement(obj.Placement)

    class PlacementPanel(QtWidgets.QDialog):
        def __init__(self, parent=None):
            flags = (
                QtCore.Qt.Tool
                | QtCore.Qt.WindowTitleHint
                | QtCore.Qt.WindowCloseButtonHint
            )
            super().__init__(parent, flags)
            self.setObjectName("McMasterPlacementPanel")
            self.setWindowTitle(f"Position {obj.Label}")
            self.setAttribute(QtCore.Qt.WA_MacAlwaysShowToolWindow, True)
            form = QtWidgets.QFormLayout(self)
            self._spins = {}
            base = original.Base
            yaw, pitch, roll = original.Rotation.getYawPitchRoll()
            for key, value, suffix in (
                ("X", base.x, " mm"),
                ("Y", base.y, " mm"),
                ("Z", base.z, " mm"),
                ("Yaw", yaw, " °"),
                ("Pitch", pitch, " °"),
                ("Roll", roll, " °"),
            ):
                spin = QtWidgets.QDoubleSpinBox()
                spin.setRange(-1e6, 1e6)
                spin.setDecimals(3)
                spin.setSingleStep(1.0 if suffix == " mm" else 15.0)
                spin.setSuffix(suffix)
                spin.setValue(value)
                spin.valueChanged.connect(self._apply)
                self._spins[key] = spin
                form.addRow(key, spin)
            rotate_row = QtWidgets.QHBoxLayout()
            for axis, angle in (("X", 90), ("Y", 90), ("Z", 90)):
                button = QtWidgets.QPushButton(f"Rotate {axis} 90°")
                button.clicked.connect(lambda _=False, a=axis: self._nudge_rotate(a))
                rotate_row.addWidget(button)
            form.addRow(rotate_row)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Reset
            )
            buttons.accepted.connect(self.accept)
            buttons.button(QtWidgets.QDialogButtonBox.Reset).clicked.connect(self._reset)
            form.addRow(buttons)

        def _placement(self) -> "App.Placement":
            return App.Placement(
                App.Vector(
                    self._spins["X"].value(),
                    self._spins["Y"].value(),
                    self._spins["Z"].value(),
                ),
                App.Rotation(
                    self._spins["Yaw"].value(),
                    self._spins["Pitch"].value(),
                    self._spins["Roll"].value(),
                ),
            )

        def _apply(self, *_args) -> None:
            try:
                obj.Placement = self._placement()
                obj.Document.recompute()
            except Exception:
                pass

        def _nudge_rotate(self, axis: str) -> None:
            current = obj.Placement
            rotation = App.Rotation(
                App.Vector(
                    1 if axis == "X" else 0,
                    1 if axis == "Y" else 0,
                    1 if axis == "Z" else 0,
                ),
                90,
            )
            obj.Placement = App.Placement(current.Base, rotation * current.Rotation)
            yaw, pitch, roll = obj.Placement.Rotation.getYawPitchRoll()
            self._spins["Yaw"].blockSignals(True)
            self._spins["Pitch"].blockSignals(True)
            self._spins["Roll"].blockSignals(True)
            self._spins["Yaw"].setValue(yaw)
            self._spins["Pitch"].setValue(pitch)
            self._spins["Roll"].setValue(roll)
            self._spins["Yaw"].blockSignals(False)
            self._spins["Pitch"].blockSignals(False)
            self._spins["Roll"].blockSignals(False)
            try:
                obj.Document.recompute()
            except Exception:
                pass

        def _reset(self) -> None:
            obj.Placement = App.Placement(original)
            base = original.Base
            yaw, pitch, roll = original.Rotation.getYawPitchRoll()
            values = {
                "X": base.x,
                "Y": base.y,
                "Z": base.z,
                "Yaw": yaw,
                "Pitch": pitch,
                "Roll": roll,
            }
            for key, value in values.items():
                self._spins[key].blockSignals(True)
                self._spins[key].setValue(value)
                self._spins[key].blockSignals(False)
            try:
                obj.Document.recompute()
            except Exception:
                pass

    panel = PlacementPanel(Gui.getMainWindow())
    panel.show()
    panel.raise_()
    Gui.McMasterPlacementPanel = panel


def open_position_dialog(object_names: list[str]) -> None:
    """Select the imported body and open VibeCAD's transform / placement UI."""
    from PySide import QtCore
    import FreeCADGui as Gui

    names = list(object_names)

    def _launch() -> None:
        doc = App.ActiveDocument
        if doc is None:
            return
        objects = [doc.getObject(name) for name in names]
        target = _transform_target(objects)
        if target is None or not hasattr(target, "Placement"):
            return
        try:
            existing = getattr(Gui, "McMasterPlacementPanel", None)
            if existing is not None:
                existing.close()
                Gui.McMasterPlacementPanel = None
        except Exception:
            pass
        try:
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
        except Exception:
            pass
        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, target.Name)
        except Exception:
            return
        try:
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass
        try:
            Gui.updateGui()
        except Exception:
            pass
        available = set()
        try:
            available = set(Gui.listCommands())
        except Exception:
            pass
        for command in ("Std_TransformManip", "Std_Transform", "Std_Placement"):
            if available and command not in available:
                continue
            try:
                Gui.runCommand(command, 0)
                App.Console.PrintMessage(
                    f"McMaster: position {target.Label} — drag the triad or use the transform panel\n"
                )
                return
            except Exception:
                continue
        try:
            Gui.activeDocument().setEdit(target.Name, 1)
            App.Console.PrintMessage(
                f"McMaster: position {target.Label} — transform handles are active\n"
            )
            return
        except Exception:
            pass
        _show_placement_dialog(target)

    QtCore.QTimer.singleShot(500, _launch)


def import_cad(
    path: Path,
    part_number: str,
    document_name: str = "",
) -> list[str]:
    doc = None
    if document_name:
        try:
            doc = App.getDocument(document_name)
        except Exception:
            doc = None
    if doc is None:
        doc = App.ActiveDocument
    if doc is None:
        doc = App.newDocument("McMaster")
    before = set(doc.Objects)
    owns_transaction = not bool(getattr(doc, "HasPendingTransaction", False))
    if owns_transaction:
        doc.openTransaction("Insert McMaster-Carr Component")
    try:
        imported = False
        try:
            import ImportGui

            try:
                ImportGui.insert(
                    name=str(path),
                    docName=doc.Name,
                    merge=False,
                    useLinkGroup=True,
                    importSolidBodies=True,
                )
            except TypeError:
                ImportGui.insert(str(path), doc.Name)
            imported = True
        except Exception:
            pass
        if not imported:
            import Part

            Part.insert(str(path), doc.Name)
        created = [obj for obj in doc.Objects if obj not in before]
        if not created:
            raise RuntimeError(f"Import produced no objects from {path.name}")
        try:
            component = _promote_to_component(doc, created, part_number, path)
            _name_imported_tree(component, part_number, path)
            doc.recompute()
            _name_imported_tree(component, part_number, path)
            try:
                import FreeCADGui as Gui
                from PySide import QtCore

                Gui.updateGui()
                doc_name = doc.Name
                obj_name = component.Name
                path_str = str(path)
                pn = part_number

                def _relabel() -> None:
                    live_doc = App.getDocument(doc_name)
                    if live_doc is None:
                        return
                    live = live_doc.getObject(obj_name)
                    if live is None:
                        return
                    _name_imported_tree(live, pn, Path(path_str))
                    App.Console.PrintMessage(
                        f"McMaster: renamed {live.Name} -> {live.Label!r}\n"
                    )

                QtCore.QTimer.singleShot(0, _relabel)
                QtCore.QTimer.singleShot(250, _relabel)
            except Exception:
                pass
            result = [component.Name]
        except Exception as exc:
            App.Console.PrintWarning(
                f"McMaster: could not wrap as a component ({exc}); "
                "imported objects were left as-is\n"
            )
            for obj in created:
                if not _is_origin_object(obj):
                    _stamp_metadata(obj, part_number, path)
            doc.recompute()
            result = [obj.Name for obj in created if not _is_origin_object(obj)] or [
                obj.Name for obj in created
            ]
        if owns_transaction:
            doc.commitTransaction()
        return result
    except Exception:
        if owns_transaction:
            doc.abortTransaction()
        raise


def _is_cad_file(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name.lower()
    if name.endswith(".download") or name.endswith(".crdownload") or name.endswith(".part"):
        return False
    return path.suffix.lower() in CAD_SUFFIXES


def _looks_like_mcmaster_download(path: Path) -> bool:
    if not _is_cad_file(path):
        return False
    return bool(part_number_from_filename(path.name)) or path.parent == inbox_dir()


class CatalogDownloadSession:
    """Files and lifetime belonging to one catalog launch."""

    def __init__(
        self,
        session_inbox: Path,
        external_downloads: Path,
        document_name: str = "",
        lifetime_seconds: float = 20 * 60,
    ) -> None:
        self.inbox = Path(session_inbox)
        self.downloads = Path(external_downloads)
        self.document_name = str(document_name or "")
        self.mode = ""
        self.active = True
        self.started = time.time()
        self.expires = self.started + lifetime_seconds
        self._seen: set[str] = set()
        self._pending: dict[str, str] = {}
        for folder in (self.inbox, self.downloads):
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if _is_cad_file(path):
                    self._seen.add(self._key(path))

    @staticmethod
    def _key(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return str(path)
        return f"{path.resolve()}::{stat.st_mtime_ns}::{stat.st_size}"

    def set_mode(self, mode: str) -> None:
        if mode not in {"embedded", "external"}:
            self.stop()
            return
        self.mode = mode

    def stop(self) -> None:
        self.active = False
        self._pending.clear()

    def ready_paths(self) -> list[Path]:
        if not self.active or not self.mode:
            return []
        if self.mode == "external" and time.time() >= self.expires:
            self.stop()
            return []
        folder = self.inbox if self.mode == "embedded" else self.downloads
        if not folder.is_dir():
            return []
        ready = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
            if not _is_cad_file(path):
                continue
            if self.mode == "external" and not _looks_like_mcmaster_download(path):
                continue
            key = self._key(path)
            if key in self._seen:
                continue
            try:
                if path.stat().st_mtime < self.started - 1:
                    self._seen.add(key)
                    continue
            except OSError:
                continue
            identity = str(path.resolve())
            if self._pending.get(identity) != key:
                self._pending[identity] = key
                continue
            self._pending.pop(identity, None)
            self._seen.add(key)
            ready.append(path)
        return ready


def catalog_paths_to_import(paths: list[Path], mode: str) -> list[Path]:
    """Return files owned by this catalog session in import order."""
    candidates = list(paths)
    if mode == "embedded":
        return candidates
    return candidates[:1]


def catalog_helper_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", str(helper_path())],
            capture_output=True,
            text=True,
            check=False,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


_webkit_lib = None


def _webkit_dylib():
    global _webkit_lib
    if _webkit_lib is not None:
        return _webkit_lib
    path = Path(__file__).resolve().parent / "libMcMasterWebKit.dylib"
    if not path.is_file():
        return None
    import ctypes

    lib = ctypes.CDLL(str(path))
    lib.McMasterWebKit_Attach.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.McMasterWebKit_Attach.restype = ctypes.c_int
    lib.McMasterWebKit_Status.argtypes = []
    lib.McMasterWebKit_Status.restype = ctypes.c_char_p
    _webkit_lib = lib
    return lib


def close_catalog_panel() -> None:
    try:
        import FreeCADGui as Gui
    except Exception:
        return
    panel = getattr(Gui, "McMasterCatalogPanel", None)
    if panel is None:
        return
    try:
        panel.close()
        panel.deleteLater()
    except Exception:
        pass
    Gui.McMasterCatalogPanel = None


def _open_system_url(url: str) -> bool:
    from PySide import QtCore, QtGui

    return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)))


def open_external_catalog() -> bool:
    """Open the live catalog in the user's browser when no embedded backend exists."""
    try:
        return _open_system_url(CATALOG_URL)
    except Exception as exc:
        App.Console.PrintError(f"McMaster-Carr: could not open {CATALOG_URL}: {exc}\n")
        return False


def show_webview2_catalog_window(out_dir: Path | None = None) -> bool:
    """Launch the Windows catalog helper backed by Edge WebView2."""
    if os.name != "nt":
        return False
    helper = webview2_helper_path()
    if not helper.is_file():
        return False
    destination = Path(out_dir) if out_dir is not None else inbox_dir()
    try:
        import FreeCADGui as Gui

        process = subprocess.Popen(
            [
                str(helper),
                f"--inbox={destination}",
                f"--profile={webview2_profile_root()}",
                f"--url={CATALOG_URL}",
                f"--parent-pid={os.getpid()}",
            ],
            cwd=str(helper.parent),
            close_fds=True,
        )
        Gui.McMasterCatalogProcess = process
        App.Console.PrintMessage(
            "McMaster catalog opened with Edge WebView2; "
            f"profile={webview2_profile_root()}\n"
        )
        return True
    except OSError as exc:
        App.Console.PrintWarning(
            f"McMaster-Carr: WebView2 helper could not start ({exc})\n"
        )
        return False


def show_linux_catalog_window(out_dir: Path | None = None) -> bool:
    """Launch the Linux catalog helper backed by the platform WebKit."""
    if not sys.platform.startswith("linux"):
        return False
    helper = linux_helper_path()
    interpreter = linux_helper_python()
    if not helper.is_file() or not interpreter:
        return False
    destination = Path(out_dir) if out_dir is not None else inbox_dir()
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    try:
        import FreeCADGui as Gui

        smoke = subprocess.run(
            [interpreter, str(helper), "--smoke-test"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            env=environment,
        )
        if smoke.returncode != 0:
            detail = (smoke.stderr or smoke.stdout or "unavailable").strip()
            App.Console.PrintWarning(
                f"McMaster-Carr: Linux embedded browser unavailable ({detail})\n"
            )
            return False
        process = subprocess.Popen(
            [
                interpreter,
                str(helper),
                f"--inbox={destination}",
                f"--profile={webview2_profile_root()}",
                f"--url={CATALOG_URL}",
                f"--parent-pid={os.getpid()}",
            ],
            cwd=str(helper.parent),
            close_fds=True,
            env=environment,
        )
        Gui.McMasterCatalogProcess = process
        App.Console.PrintMessage(
            "McMaster catalog opened with the Linux WebKit helper\n"
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        App.Console.PrintWarning(
            f"McMaster-Carr: Linux catalog helper could not start ({exc})\n"
        )
        return False


def attach_webkit(widget, out_dir: Path) -> bool:
    lib = _webkit_dylib()
    if lib is None:
        return False
    from PySide import QtCore

    widget.setAttribute(QtCore.Qt.WA_NativeWindow, True)
    handle = int(widget.winId())
    if handle == 0:
        return False
    return lib.McMasterWebKit_Attach(handle, str(out_dir).encode("utf-8")) == 0


def show_catalog_window(out_dir: Path | None = None) -> bool:
    """Fusion-style catalog: a tool window owned by VibeCAD (works in fullscreen)."""
    from PySide import QtCore, QtWidgets
    import FreeCADGui as Gui

    existing = getattr(Gui, "McMasterCatalogPanel", None)
    if existing is not None:
        try:
            existing.close()
            existing.deleteLater()
        except Exception:
            pass
        Gui.McMasterCatalogPanel = None

    mw = Gui.getMainWindow()
    flags = (
        QtCore.Qt.Tool
        | QtCore.Qt.WindowTitleHint
        | QtCore.Qt.WindowCloseButtonHint
        | QtCore.Qt.WindowMinMaxButtonsHint
    )

    destination = Path(out_dir) if out_dir is not None else inbox_dir()

    class CatalogPanel(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent, flags)
            self.setObjectName("McMasterCatalogPanel")
            self.setWindowTitle("Insert McMaster-Carr Component")
            self.resize(1000, 720)
            self.setAttribute(QtCore.Qt.WA_MacAlwaysShowToolWindow, True)
            self._attached = False

            self.host = QtWidgets.QWidget(self)
            self.host.setMinimumSize(640, 480)
            self.host.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            self.status = QtWidgets.QLabel("Loading McMaster-Carr…")
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 8, 8)
            layout.addWidget(self.host, 1)
            layout.addWidget(self.status)
            self._status_timer = QtCore.QTimer(self)
            self._status_timer.setInterval(800)
            self._status_timer.timeout.connect(self._poll_webkit)
            QtCore.QTimer.singleShot(0, self._attach)
            QtCore.QTimer.singleShot(300, self._attach)
            QtCore.QTimer.singleShot(800, self._attach)

        def showEvent(self, event) -> None:
            super().showEvent(event)
            QtCore.QTimer.singleShot(0, self._attach)

        def _attach(self) -> None:
            if self._attached:
                return
            if self.host.width() < 64 or self.host.height() < 64:
                QtCore.QTimer.singleShot(120, self._attach)
                return
            if attach_webkit(self.host, destination):
                self._attached = True
                self.status.setText("Connecting to McMaster-Carr…")
                self._status_timer.start()
                App.Console.PrintMessage("McMaster catalog attached inside VibeCAD\n")
            else:
                self.status.setText(
                    "Catalog view is not ready yet. If it stays blank, click Catalog again."
                )

        def _poll_webkit(self) -> None:
            lib = _webkit_dylib()
            if lib is None:
                return
            raw = lib.McMasterWebKit_Status()
            if not raw:
                return
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            if "download finished" in text.lower():
                self.status.setText("Imported. Closing catalog…")
                QtCore.QTimer.singleShot(400, close_catalog_panel)
            elif text.startswith("ok"):
                self.status.setText("Download 3-D STEP to import into this document.")
            elif text.startswith("error") or "fail" in text.lower():
                self.status.setText(f"McMaster load problem: {text}")
            elif text.startswith("loading"):
                self.status.setText("Loading McMaster-Carr…")

    panel = CatalogPanel(mw)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    Gui.McMasterCatalogPanel = panel
    return True


def open_catalog(out_dir: Path | None = None) -> str:
    """Open the best available catalog backend and return its mode."""
    destination = Path(out_dir) if out_dir is not None else inbox_dir()
    try:
        if show_webview2_catalog_window(destination):
            return "embedded"
    except Exception as exc:
        try:
            App.Console.PrintWarning(
                f"McMaster-Carr: WebView2 unavailable ({exc}); "
                "trying another browser\n"
            )
        except Exception:
            pass
    try:
        if show_linux_catalog_window(destination):
            return "embedded"
    except Exception as exc:
        try:
            App.Console.PrintWarning(
                f"McMaster-Carr: Linux browser unavailable ({exc}); "
                "trying another browser\n"
            )
        except Exception:
            pass
    try:
        if _webkit_dylib() is not None and show_catalog_window(destination):
            return "embedded"
    except Exception as exc:
        try:
            App.Console.PrintWarning(
                f"McMaster-Carr: embedded catalog unavailable ({exc}); "
                "using the system browser\n"
            )
        except Exception:
            pass
    if open_external_catalog():
        return "external"
    return ""


def _cleanup_session_inbox(path: Path) -> None:
    try:
        resolved = path.resolve()
        root = inbox_dir().resolve()
        if resolved.parent == root and resolved.is_dir():
            shutil.rmtree(resolved)
    except OSError:
        pass


def _stop_catalog_process() -> None:
    try:
        import FreeCADGui as Gui
    except Exception:
        return
    process = getattr(Gui, "McMasterCatalogProcess", None)
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
    except Exception:
        pass
    Gui.McMasterCatalogProcess = None


def _catalog_backend_running() -> bool:
    try:
        import FreeCADGui as Gui
    except Exception:
        return False
    process = getattr(Gui, "McMasterCatalogProcess", None)
    if process is not None:
        try:
            if process.poll() is None:
                return True
        except Exception:
            pass
        Gui.McMasterCatalogProcess = None
    panel = getattr(Gui, "McMasterCatalogPanel", None)
    if panel is None:
        return False
    try:
        return bool(panel.isVisible())
    except Exception:
        return True


def _ensure_import_watcher(session_inbox: Path | None = None):
    from PySide import QtCore
    import FreeCADGui as Gui

    existing = getattr(Gui, "McMasterImportWatcher", None)
    if existing is not None:
        try:
            old_inbox = existing.session.inbox
            existing.stop()
            QtCore.QTimer.singleShot(
                1000,
                lambda path=old_inbox: _cleanup_session_inbox(path),
            )
        except Exception:
            pass

    destination = Path(session_inbox) if session_inbox is not None else new_session_inbox()
    document_name = str(getattr(App.ActiveDocument, "Name", "") or "")

    class Watcher(QtCore.QObject):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.session = CatalogDownloadSession(
                destination,
                downloads_dir(),
                document_name=document_name,
            )
            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(800)
            self._timer.timeout.connect(self._poll)
            self._closed_polls = 0
            self._imported_names = []
            self._timer.start()

        def set_mode(self, mode: str) -> None:
            self.session.set_mode(mode)

        def stop(self) -> None:
            self.session.stop()
            self._timer.stop()
            if getattr(Gui, "McMasterImportWatcher", None) is self:
                Gui.McMasterImportWatcher = None

        def _poll(self) -> None:
            paths = self.session.ready_paths()
            if not self.session.active:
                self._finish()
                return
            if len(paths) > 1 and self.session.mode == "external":
                from PySide import QtWidgets

                selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
                    Gui.getMainWindow(),
                    "Choose the McMaster CAD download",
                    str(downloads_dir()),
                    "CAD (*.step *.stp *.iges *.igs *.sat *.sldprt *.zip)",
                )
                if not selected:
                    return
                paths = [Path(selected)]
            for path in catalog_paths_to_import(paths, self.session.mode):
                self._import_path(path)
            if paths and self.session.mode == "external":
                self._finish()
                return
            if self.session.mode != "embedded":
                return
            if _catalog_backend_running():
                self._closed_polls = 0
                return
            self._closed_polls += 1
            if self._closed_polls >= 2:
                self._finish()

        def _import_path(self, path: Path) -> None:
            pn = part_number_from_filename(path.name)
            try:
                cached = store_in_cache(path, pn)
                names = import_cad(
                    cached,
                    pn or cached.stem,
                    document_name=self.session.document_name,
                )
                App.Console.PrintMessage(
                    f"McMaster: inserted {pn or cached.stem} as {', '.join(names)}\n"
                )
                try:
                    Gui.SendMsgToActiveView("ViewFit")
                except Exception:
                    pass
                self._imported_names.extend(names)
            except Exception as exc:
                App.Console.PrintError(f"McMaster insert: {exc}\n")

        def _finish(self) -> None:
            names = list(self._imported_names)
            self.stop()
            _stop_catalog_process()
            close_catalog_panel()
            _cleanup_session_inbox(self.session.inbox)
            if names:
                open_position_dialog(names)

    mw = Gui.getMainWindow()
    Gui.McMasterImportWatcher = Watcher(mw)
    return Gui.McMasterImportWatcher


def run() -> None:
    if not App.GuiUp:
        raise RuntimeError("McMaster catalog requires the VibeCAD GUI")
    close_catalog_panel()
    _stop_catalog_process()
    destination = new_session_inbox()
    watcher = _ensure_import_watcher(destination)
    mode = open_catalog(destination)
    watcher.set_mode(mode)
    if mode == "embedded":
        App.Console.PrintMessage(
            "McMaster-Carr catalog overlay opened. "
            "Download 3-D STEP — it imports automatically (no Save dialog).\n"
        )
        return
    if mode == "external":
        App.Console.PrintMessage(
            "McMaster-Carr opened in your browser. Download 3-D STEP to your "
            "Downloads folder and VibeCAD will import it automatically; use "
            "Import if you save it somewhere else.\n"
        )
        return
    watcher.stop()
    _cleanup_session_inbox(destination)
    App.Console.PrintError("McMaster-Carr: could not open https://www.mcmaster.com/\n")


def import_file() -> None:
    """Pick a CAD file and import it without opening the catalog."""
    if not App.GuiUp:
        raise RuntimeError("Import requires the VibeCAD GUI")
    from PySide import QtWidgets
    import FreeCADGui as Gui

    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        Gui.getMainWindow(),
        "McMaster CAD file",
        str(downloads_dir()),
        "CAD (*.step *.stp *.iges *.igs *.sat *.sldprt *.zip);;All files (*)",
    )
    if not path:
        return
    source = Path(path)
    pn = part_number_from_filename(source.name)
    cached = store_in_cache(source, pn)
    names = import_cad(cached, pn or cached.stem)
    App.Console.PrintMessage(
        f"McMaster: inserted {pn or cached.stem} as {', '.join(names)}\n"
    )
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    open_position_dialog(names)


def open_cache() -> None:
    from PySide import QtCore, QtGui

    QtGui.QDesktopServices.openUrl(
        QtCore.QUrl.fromLocalFile(str(cache_root()))
    )
