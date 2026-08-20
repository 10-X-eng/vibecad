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
import time
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


def downloads_dir() -> Path:
    return Path.home() / "Downloads"


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


def catalog_description(part_number: str, source_path: Path) -> str:
    """Catalog title from the McMaster STEP filename, e.g. 'Black-Oxide Alloy Steel Socket Head Screw'."""
    stem = re.sub(r"[_\s]+", " ", source_path.stem).strip()
    title = stem
    if part_number:
        prefix = re.compile(re.escape(part_number) + r"[\s\-]*", re.IGNORECASE)
        title = prefix.sub("", stem, count=1).strip(" -")
    title = re.sub(r"\s+", " ", title).strip()
    if not title or (part_number and title.upper() == part_number.upper()):
        return "McMaster-Carr catalog part"
    return title


def _stamp_metadata(obj, part_number: str, source_path: Path) -> None:
    label = part_number or source_path.stem
    description = catalog_description(part_number, source_path)
    try:
        obj.Label = label
    except Exception:
        pass
    try:
        obj.Label2 = description
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
    return tid in _SKIP_TRANSFORM_TYPES or "Origin" in tid


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


def _ensure_body(doc, obj):
    tid = _type_id(obj)
    if tid == "PartDesign::Body":
        return obj
    if tid == "PartDesign::Component":
        return obj
    try:
        parent = obj.getParentGeoFeatureGroup()
        if parent is not None and _type_id(parent) == "PartDesign::Body":
            return parent
    except Exception:
        pass
    body = doc.addObject("PartDesign::Body", "Body")
    _classify_structure(doc, body)
    try:
        body.Label = "Body"
    except Exception:
        pass
    try:
        body.addObject(obj)
    except Exception:
        pass
    try:
        if getattr(obj, "Shape", None) is not None:
            body.Tip = obj
    except Exception:
        pass
    return body


def _promote_to_component(doc, created: list, part_number: str, source_path: Path):
    roots = _import_roots(created)
    existing = [obj for obj in roots if _type_id(obj) == "PartDesign::Component"]
    if len(existing) == 1 and len(roots) == 1:
        return existing[0]

    component = doc.addObject("PartDesign::Component", "Component")
    if component is None or _type_id(component) != "PartDesign::Component":
        raise RuntimeError("VibeCAD did not create a PartDesign::Component")
    _classify_structure(doc, component)
    try:
        component.Label = part_number or source_path.stem
    except Exception:
        pass

    parent = _active_component()
    if parent is not None and parent is not component:
        try:
            parent.addObject(component)
        except Exception:
            pass

    bodies = []
    for root in roots:
        if root is component or _is_origin_object(root):
            continue
        if _type_id(root) == "PartDesign::Component":
            for child in list(getattr(root, "Group", []) or []):
                if _type_id(child) == "PartDesign::Body":
                    bodies.append(child)
            continue
        bodies.append(_ensure_body(doc, root))

    unique_bodies = []
    seen = set()
    for body in bodies:
        if body is None or id(body) in seen:
            continue
        seen.add(id(body))
        unique_bodies.append(body)
        try:
            if body.getParentGeoFeatureGroup() is not component:
                component.addObject(body)
        except Exception:
            pass
        if len(unique_bodies) == 1:
            try:
                body.Label = "Body"
            except Exception:
                pass

    try:
        import FreeCADGui as Gui

        view = Gui.activeView()
        if view is not None:
            view.setActiveObject("part", component)
    except Exception:
        pass
    return component


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


def import_cad(path: Path, part_number: str) -> list[str]:
    doc = App.ActiveDocument
    if doc is None:
        doc = App.newDocument("McMaster")
    before = set(doc.Objects)
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
        _stamp_metadata(component, part_number, path)
        doc.recompute()
        return [component.Name]
    except Exception as exc:
        App.Console.PrintWarning(
            f"McMaster: could not wrap as a component ({exc}); imported objects were left as-is\n"
        )
        for obj in created:
            if not _is_origin_object(obj):
                _stamp_metadata(obj, part_number, path)
        doc.recompute()
        return [obj.Name for obj in created if not _is_origin_object(obj)] or [
            obj.Name for obj in created
        ]


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


def show_catalog_window() -> bool:
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
            if attach_webkit(self.host, inbox_dir()):
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


def _ensure_import_watcher() -> None:
    from PySide import QtCore
    import FreeCADGui as Gui

    existing = getattr(Gui, "McMasterImportWatcher", None)
    if existing is not None:
        return

    class Watcher(QtCore.QObject):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._seen: set[str] = set()
            self._started = time.time()
            for folder in (inbox_dir(), downloads_dir()):
                if not folder.is_dir():
                    continue
                for path in folder.iterdir():
                    if _is_cad_file(path):
                        self._seen.add(self._key(path))
            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(800)
            self._timer.timeout.connect(self._poll)
            self._timer.start()

        def _key(self, path: Path) -> str:
            try:
                st = path.stat()
            except OSError:
                return str(path)
            return f"{path.resolve()}::{st.st_mtime_ns}::{st.st_size}"

        def _poll(self) -> None:
            for folder in (inbox_dir(), downloads_dir()):
                if not folder.is_dir():
                    continue
                for path in folder.iterdir():
                    key = self._key(path)
                    if key in self._seen:
                        continue
                    if not _looks_like_mcmaster_download(path):
                        continue
                    try:
                        if path.stat().st_mtime < self._started - 1:
                            self._seen.add(key)
                            continue
                    except OSError:
                        continue
                    self._seen.add(key)
                    self._import_path(path)

        def _import_path(self, path: Path) -> None:
            pn = part_number_from_filename(path.name)
            try:
                cached = store_in_cache(path, pn)
                names = import_cad(cached, pn or cached.stem)
                App.Console.PrintMessage(
                    f"McMaster: inserted {pn or cached.stem} as {', '.join(names)}\n"
                )
                try:
                    Gui.SendMsgToActiveView("ViewFit")
                except Exception:
                    pass
                close_catalog_panel()
                open_position_dialog(names)
            except Exception as exc:
                App.Console.PrintError(f"McMaster insert: {exc}\n")

    mw = Gui.getMainWindow()
    Gui.McMasterImportWatcher = Watcher(mw)


def run() -> None:
    if not App.GuiUp:
        raise RuntimeError("McMaster catalog requires the VibeCAD GUI")
    _ensure_import_watcher()
    if show_catalog_window():
        App.Console.PrintMessage(
            "McMaster-Carr catalog overlay opened. "
            "Download 3-D STEP — it imports automatically (no Save dialog).\n"
        )
        return
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
