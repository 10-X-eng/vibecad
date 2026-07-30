# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fusion-style always-on 3D grid for VibeCAD.

Reuses the Draft workbench grid machinery (``Gui.Snapper`` + ``gridTracker``)
to keep a reference grid visible in every 3D view regardless of the active
workbench, similar to Fusion 360's viewport grid.

Behavior:

- Draft grid preferences (``alwaysShowGrid`` and ``GridHideInOtherWorkbenches``)
  are seeded exactly once, guarded by a flag parameter, so later manual
  changes made by the user in Draft preferences are never overridden.
- A lightweight observer on the MDI area initializes the grid for each 3D
  view at most once. A Qt timer updates only the active 3D view to keep minor
  lines within a readable screen-space range and display the minor/major
  spacing in the viewport. Non-3D MDI views never touch Coin grid state.
  Manually toggling the grid off is respected.
- The native ``VibeCAD_ToggleGrid`` command owns the View-menu action. This
  module only updates the global preference and existing per-view trackers;
  when no GUI document is open it updates the preference without creating the
  Draft Snapper or attempting to render a grid.
- The automatic always-on-at-startup behavior sits behind the ``Mod/VibeCAD``
  ``AlwaysShowGrid`` boolean preference (default: enabled) as a master
  kill-switch.

The module imports safely outside FreeCAD (guarded imports) so tooling such
as linters and test collectors can load it.
"""

from __future__ import annotations

import math
from typing import Any

try:
    import FreeCAD as App
except ImportError:  # pragma: no cover - only outside FreeCAD (tooling/tests)
    App = None  # type: ignore[assignment]

_PARAM_ROOT = "User parameter:BaseApp/Preferences/"
_VIBECAD_PARAM_PATH = _PARAM_ROOT + "Mod/VibeCAD"
_DRAFT_PARAM_PATH = _PARAM_ROOT + "Mod/Draft"
_SEED_FLAG = "GridPreferencesSeeded"

_TARGET_GRID_PIXELS = 28.0
_MIN_GRID_PIXELS = 18.0
_MAX_GRID_PIXELS = 44.0
_MIN_GRID_SPACING_MM = 1.0e-9
_MAX_GRID_SPACING_MM = 1.0e12
_IMPERIAL_SCHEMAS = frozenset({2, 3, 5, 7})
_FRACTIONAL_INCH_SCHEMAS = frozenset({2, 5})
_FOOT_BASED_SCHEMAS = frozenset({5, 7})
_MM_PER_INCH = 25.4

# Identity record used for diagnostics and recycled-wrapper protection. The
# authoritative "grid already exists for this view" check is membership in
# ``Gui.Snapper.trackers[0]`` (equality-based across Python wrappers).
_seen_views: set[int] = set()
_observer_installed = False
_adaptive_controllers: list[Any] = []
_maintenance_timer: Any = None
_main_window_filter: Any = None
_close_suspended = False
_activation_generation = 0
_observed_subwindows: dict[int, Any] = {}
_document_views: dict[str, list[Any]] = {}
_document_observer: Any = None


def _document_restore_active() -> bool:
    if App is None:
        return False
    is_restoring = getattr(App, "isRestoring", None)
    if callable(is_restoring):
        try:
            if bool(is_restoring()):
                return True
        except Exception:
            pass
    document = getattr(App, "ActiveDocument", None)
    return document is not None and bool(getattr(document, "Restoring", False))


def _nearest_125(value: float) -> float:
    """Return the nearest positive value in the ``1, 2, 5 x 10^n`` series."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError("grid spacing input must be finite and positive")
    exponent = math.floor(math.log10(value))
    candidates = [
        step * (10.0**power)
        for power in range(exponent - 1, exponent + 2)
        for step in (1.0, 2.0, 5.0)
    ]
    return min(candidates, key=lambda candidate: abs(math.log(candidate / value)))


def _nice_grid_spacing(raw_mm: float, unit_schema: int) -> float:
    """Quantize one raw spacing to an engineering-friendly internal value."""
    raw_mm = min(max(float(raw_mm), _MIN_GRID_SPACING_MM), _MAX_GRID_SPACING_MM)
    if unit_schema not in _IMPERIAL_SCHEMAS:
        return _nearest_125(raw_mm)

    raw_inches = raw_mm / _MM_PER_INCH
    if unit_schema in _FRACTIONAL_INCH_SCHEMAS and raw_inches < 1.0:
        spacing_inches = 2.0 ** round(math.log2(raw_inches))
    elif unit_schema in _FOOT_BASED_SCHEMAS and raw_inches >= 6.0:
        spacing_inches = 12.0 * _nearest_125(raw_inches / 12.0)
    else:
        spacing_inches = _nearest_125(raw_inches)
    return min(
        max(spacing_inches * _MM_PER_INCH, _MIN_GRID_SPACING_MM),
        _MAX_GRID_SPACING_MM,
    )


def _select_grid_spacing(
    world_units_per_pixel: float,
    current_spacing_mm: float | None,
    unit_schema: int,
) -> float:
    """Choose a stable spacing while keeping minor lines visually readable."""
    if not math.isfinite(world_units_per_pixel) or world_units_per_pixel <= 0:
        raise ValueError("world units per pixel must be finite and positive")
    if current_spacing_mm is not None and current_spacing_mm > 0:
        current_pixels = current_spacing_mm / world_units_per_pixel
        if _MIN_GRID_PIXELS <= current_pixels <= _MAX_GRID_PIXELS:
            return current_spacing_mm
    return _nice_grid_spacing(world_units_per_pixel * _TARGET_GRID_PIXELS, unit_schema)


def _xyz(vector: Any) -> tuple[float, float, float]:
    """Return XYZ components from a FreeCAD or Coin vector-like value."""
    if hasattr(vector, "x"):
        return float(vector.x), float(vector.y), float(vector.z)
    return float(vector[0]), float(vector[1]), float(vector[2])


def _ray_plane_intersection(
    ray_start: Any,
    ray_end: Any,
    plane_origin: Any,
    plane_normal: Any,
) -> tuple[float, float, float] | None:
    """Intersect one viewport projection line with the active grid plane."""
    start = _xyz(ray_start)
    end = _xyz(ray_end)
    origin = _xyz(plane_origin)
    normal = _xyz(plane_normal)
    direction = tuple(end[index] - start[index] for index in range(3))
    denominator = sum(normal[index] * direction[index] for index in range(3))
    normal_length = math.sqrt(sum(component * component for component in normal))
    direction_length = math.sqrt(sum(component * component for component in direction))
    if normal_length <= 0 or direction_length <= 0:
        return None
    if abs(denominator) <= normal_length * direction_length * 1.0e-10:
        return None
    distance = sum(normal[index] * (origin[index] - start[index]) for index in range(3))
    parameter = distance / denominator
    point = tuple(start[index] + parameter * direction[index] for index in range(3))
    return point if all(math.isfinite(component) for component in point) else None


def _distance(first: Any, second: Any) -> float:
    a = _xyz(first)
    b = _xyz(second)
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def _world_units_per_pixel(view: Any, grid: Any) -> float:
    """Measure local model-space scale where the camera sees the grid plane."""
    width, height = (int(value) for value in view.getSize())
    if width < 2 or height < 2:
        raise ValueError("3D view has no measurable viewport")

    center_x = width // 2
    center_y = height // 2
    sample_pixels = max(4, min(24, min(width, height) // 16))
    working_plane = grid._get_wp()
    origin = working_plane.position
    normal = working_plane.axis

    intersections: list[tuple[float, float, float] | None] = []
    for x, y in (
        (center_x, center_y),
        (center_x + sample_pixels, center_y),
        (center_x, center_y + sample_pixels),
    ):
        ray_start, ray_end = view.projectPointToLine((x, y))
        intersections.append(
            _ray_plane_intersection(ray_start, ray_end, origin, normal)
        )

    center, horizontal, vertical = intersections
    samples = [
        _distance(center, point) / sample_pixels
        for point in (horizontal, vertical)
        if center is not None and point is not None
    ]
    if samples:
        measured = max(samples)
        if math.isfinite(measured) and measured > 0:
            return measured

    # An edge-on grid plane cannot be intersected robustly. The focal plane
    # still provides a finite camera-scale estimate until the grid is visible
    # enough for exact plane intersections again.
    center = view.getPointOnFocalPlane((center_x, center_y))
    horizontal = view.getPointOnFocalPlane((center_x + sample_pixels, center_y))
    vertical = view.getPointOnFocalPlane((center_x, center_y + sample_pixels))
    measured = max(_distance(center, horizontal), _distance(center, vertical))
    measured /= sample_pixels
    if not math.isfinite(measured) or measured <= 0:
        raise ValueError("camera scale could not be measured")
    return measured


def _warn(message: str) -> None:
    """Print a console warning when FreeCAD is available."""
    console = getattr(App, "Console", None) if App is not None else None
    if console is not None:
        console.PrintWarning(f"VibeCAD grid: {message}\n")


def _unit_schema() -> int:
    if App is None:
        return 0
    try:
        return int(App.Units.getSchema())
    except (AttributeError, TypeError, ValueError):
        return App.ParamGet(_PARAM_ROOT + "Units").GetInt("UserSchema", 0)


def _format_length(value_mm: float) -> str:
    if App is None:
        return f"{value_mm:g} mm"
    try:
        return str(App.Units.Quantity(value_mm, App.Units.Length).UserString)
    except (AttributeError, TypeError, ValueError):
        return f"{value_mm:g} mm"


def _active_3d_view() -> Any:
    """Return the active MDI view only when it is a live 3D view.

    ``Gui.activeDocument()`` remains valid while a spreadsheet or drawing
    page owns the MDI focus, so document presence is not sufficient here.
    The native MDI view itself is the authoritative boundary: only
    ``Gui::View3DInventor`` exposes a scene graph.
    """
    try:
        import FreeCADGui as Gui

        view = Gui.getMainWindow().getActiveWindow()
        if view is None or not hasattr(view, "getSceneGraph"):
            return None
        return view
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def _same_view(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return False
    try:
        return bool(first == second)
    except (ReferenceError, RuntimeError):
        return False


def _live_3d_views() -> list[Any]:
    """Return every currently owned 3D view."""
    if App is None or not App.GuiUp:
        return []
    try:
        import FreeCADGui as Gui

        view_type = App.Base.TypeId.fromName("Gui::View3DInventor")
        return list(Gui.getMainWindow().getWindowsOfType(view_type))
    except (AttributeError, ReferenceError, RuntimeError):
        return []


def _view_is_live(view: Any) -> bool:
    return any(_same_view(view, candidate) for candidate in _live_3d_views())


def _document_key(document: Any) -> str:
    try:
        return str(
            getattr(document, "Uid", "")
            or getattr(document, "Name", "")
        )
    except (AttributeError, ReferenceError, RuntimeError):
        return ""


def _remember_document_view(view: Any) -> None:
    document = getattr(App, "ActiveDocument", None) if App is not None else None
    key = _document_key(document)
    if not key:
        return
    views = _document_views.setdefault(key, [])
    if not any(_same_view(view, candidate) for candidate in views):
        views.append(view)


def _tracker_index(snapper: Any, view: Any) -> int | None:
    for index, candidate in enumerate(snapper.trackers[0]):
        if candidate is view or _same_view(candidate, view):
            return index
    return None


def _grid_is_rendered(view: Any, grid: Any) -> bool:
    """Return whether ``grid`` is enabled in this exact view's scene graph."""
    try:
        switch = grid.switch
        scene = view.getSceneGraph()
        return (
            switch is not None
            and scene.findChild(switch) >= 0
            and int(switch.whichChild.getValue()) >= 0
            and bool(grid.Visible)
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


def _active_view_parent(view: Any) -> Any:
    """Return the QWidget containing ``view`` while it owns MDI focus."""
    if not _same_view(view, _active_3d_view()):
        return None
    try:
        import FreeCADGui as Gui
        from PySide import QtWidgets

        mdi_area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        sub_window = mdi_area.currentSubWindow() if mdi_area is not None else None
        if sub_window is None:
            return None
        return sub_window.widget() or sub_window
    except (AttributeError, RuntimeError):
        return None


class _AdaptiveGridController:
    """Keep one active Draft grid legible and report its scale.

    Camera-node sensors are deliberately not used here. A Python
    ``SoNodeSensor`` can remain queued while Qt changes from a 3D MDI view to
    a spreadsheet or drawing page; detaching it during that native Coin
    transition corrupts Coin's sensor tree. A short Qt timer is both
    deterministic and lifecycle-safe, and only touches the currently active
    3D view.
    """

    def __init__(self, view: Any, grid: Any, parent: Any) -> None:
        self.view = view
        self.grid = grid
        self.parent = None
        self.label = None
        self.spacing_mm: float | None = None
        self.unit_schema: int | None = None
        self.update_pending = False
        self.disposed = False
        self.last_error = ""
        self.ensure_parent(parent)
        self.schedule_update()

    def matches(self, view: Any) -> bool:
        try:
            return bool(self.view == view)
        except (ReferenceError, RuntimeError):
            return False

    def ensure_parent(self, parent: Any) -> None:
        if parent is None or self.label is not None:
            return
        try:
            from PySide import QtCore, QtWidgets

            label = QtWidgets.QLabel(parent)
            label.setObjectName("VibeCADGridScale")
            label.setTextFormat(QtCore.Qt.PlainText)
            label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            label.setFocusPolicy(QtCore.Qt.NoFocus)
            label.setStyleSheet(
                "QLabel#VibeCADGridScale {"
                "background-color: rgba(25, 29, 36, 218);"
                "color: #f4f6f8;"
                "border: 1px solid rgba(255, 255, 255, 42);"
                "border-radius: 3px;"
                "padding: 4px 7px;"
                "}"
            )
            label.hide()
            self.parent = parent
            self.label = label
        except (AttributeError, RuntimeError, TypeError) as exc:
            self._report_error(f"scale indicator creation failed: {exc}")

    def _report_error(self, message: str) -> None:
        if message != self.last_error:
            self.last_error = message
            _warn(message)

    def schedule_update(self) -> None:
        if (
            self.disposed
            or self.update_pending
            or not _same_view(self.view, _active_3d_view())
        ):
            return
        self.update_pending = True
        try:
            from PySide import QtCore

            QtCore.QTimer.singleShot(16, self._run_scheduled_update)
        except (AttributeError, RuntimeError):
            self.update_pending = False

    def _run_scheduled_update(self) -> None:
        self.update_pending = False
        self.update()

    def _hide_label(self) -> None:
        if self.label is not None:
            try:
                self.label.hide()
            except RuntimeError:
                self.label = None
                self.parent = None

    def _position_label(self) -> None:
        if self.label is None or self.parent is None:
            return
        self.label.adjustSize()
        x = 12
        y = max(12, self.parent.height() - self.label.height() - 12)
        self.label.move(x, y)
        self.label.raise_()

    def _desired_line_count(self, spacing_mm: float, units_per_pixel: float) -> int:
        width, height = (int(value) for value in self.view.getSize())
        spacing_pixels = max(spacing_mm / units_per_pixel, 1.0)
        visible_lines = math.ceil(max(width, height) / spacing_pixels)
        major_every = max(1, int(getattr(self.grid, "mainlines", 10)))
        quantum = 2 * major_every
        requested = max(quantum, math.ceil(1.6 * visible_lines) + quantum)
        requested = math.ceil(requested / quantum) * quantum
        return max(quantum, min(requested, max(600, quantum)))

    def update(self) -> bool:
        if self.disposed:
            return False
        if not _same_view(self.view, _active_3d_view()):
            self._hide_label()
            return True
        try:
            if not bool(getattr(self.grid, "Visible", False)):
                self._hide_label()
                return True
            if int(getattr(self.grid, "mainlines", 0)) <= 0:
                self._hide_label()
                return True

            units_per_pixel = _world_units_per_pixel(self.view, self.grid)
            schema = _unit_schema()
            current = self.spacing_mm if schema == self.unit_schema else None
            spacing = _select_grid_spacing(units_per_pixel, current, schema)
            line_count = self._desired_line_count(spacing, units_per_pixel)

            tracker_spacing = float(getattr(self.grid, "space", 0.0))
            tracker_lines = int(getattr(self.grid, "numlines", 0))
            if (
                not math.isclose(tracker_spacing, spacing, rel_tol=1.0e-12)
                or tracker_lines != line_count
            ):
                self.grid.space = spacing
                self.grid.numlines = line_count
                self.grid.update()

            self.spacing_mm = spacing
            self.unit_schema = schema
            major_every = max(1, int(getattr(self.grid, "mainlines", 10)))
            text = (
                f"Grid {_format_length(spacing)} | "
                f"Major {_format_length(spacing * major_every)}"
            )
            if self.label is not None:
                if self.label.text() != text:
                    self.label.setText(text)
                self._position_label()
                self.label.show()
            self.last_error = ""
            return True
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            self._hide_label()
            self._report_error(f"adaptive update failed: {exc}")
            # Camera replacement can transiently invalidate a projection.
            # The parent-liveness check in maintain() owns final disposal.
            return True

    def maintain(self) -> bool:
        if self.disposed:
            return False
        if not _same_view(self.view, _active_3d_view()):
            self._hide_label()
            return True
        try:
            self.view.getSize()
            if self.parent is not None:
                self.parent.objectName()
            return self.update()
        except (ReferenceError, RuntimeError):
            return False

    def dispose(self) -> None:
        if self.disposed:
            return
        self.disposed = True
        if self.label is not None:
            try:
                self.label.deleteLater()
            except RuntimeError:
                pass
        self.label = None
        self.parent = None


def _update_adaptive_controllers() -> None:
    if _document_restore_active():
        return
    for controller in list(_adaptive_controllers):
        if controller.maintain():
            continue
        controller.dispose()
        _adaptive_controllers.remove(controller)


def _dispose_adaptive_controllers() -> None:
    """Release every per-view controller before its native view disappears."""
    for controller in list(_adaptive_controllers):
        controller.dispose()
    _adaptive_controllers.clear()


def _dispose_adaptive_controller_for_view(view: Any) -> None:
    for controller in list(_adaptive_controllers):
        if not controller.matches(view):
            continue
        controller.dispose()
        _adaptive_controllers.remove(controller)


def _remove_view_trackers(view: Any) -> None:
    """Release Draft's aligned tracker row for one destroyed 3D view."""
    _seen_views.discard(id(view))
    _dispose_adaptive_controller_for_view(view)
    try:
        import FreeCADGui as Gui

        snapper = getattr(Gui, "Snapper", None)
        if snapper is None:
            return
        index = _tracker_index(snapper, view)
        if index is None:
            return

        tracker_fields = (
            "activeview",
            "grid",
            "tracker",
            "extLine",
            "radiusTracker",
            "dim1",
            "dim2",
            "trackLine",
            "extLine2",
            "holdTracker",
        )
        removed = [
            trackers[index] if index < len(trackers) else None
            for trackers in snapper.trackers
        ]
        for trackers in snapper.trackers:
            if index < len(trackers):
                del trackers[index]
        for field, tracker in zip(tracker_fields, removed):
            current = getattr(snapper, field, None)
            if current is tracker or (
                field == "activeview" and _same_view(current, tracker)
            ):
                setattr(snapper, field, None)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        # The native view is already being destroyed. Its scene graph owns no
        # surviving viewport state; cleanup must remain teardown-safe.
        return


def _on_sub_window_destroyed(window_key: int) -> None:
    view = _observed_subwindows.pop(window_key, None)
    if view is not None:
        _remove_view_trackers(view)


def _observe_sub_window(window: Any, view: Any) -> None:
    if window is None or view is None:
        return
    key = id(window)
    if key in _observed_subwindows:
        return
    try:
        window.destroyed.connect(
            lambda _object=None, window_key=key: _on_sub_window_destroyed(window_key)
        )
        _observed_subwindows[key] = view
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return


class _GridDocumentObserver:
    def slotDeletedDocument(self, document: Any) -> None:
        key = _document_key(document)
        for view in _document_views.pop(key, []):
            _remove_view_trackers(view)


def _install_document_observer() -> None:
    global _document_observer
    if _document_observer is not None or App is None:
        return
    try:
        observer = _GridDocumentObserver()
        App.addDocumentObserver(observer)
        _document_observer = observer
    except (AttributeError, RuntimeError, TypeError) as exc:
        _warn(f"document lifecycle observer unavailable: {exc}")


def _suspend_for_main_window_close() -> None:
    """Stop callbacks before FreeCAD processes its main-window close event."""
    global _close_suspended
    _close_suspended = True
    if _maintenance_timer is not None:
        try:
            _maintenance_timer.stop()
        except RuntimeError:
            pass
    _dispose_adaptive_controllers()


def _resume_after_cancelled_close() -> None:
    """Restore maintenance if a human cancels FreeCAD shutdown."""
    global _close_suspended
    if not _close_suspended:
        return
    try:
        from PySide import QtCore

        if QtCore.QCoreApplication.closingDown():
            return
    except (AttributeError, RuntimeError):
        return
    _close_suspended = False
    if _maintenance_timer is not None:
        try:
            _maintenance_timer.start()
        except RuntimeError:
            pass
    try:
        import FreeCADGui as Gui
        from PySide import QtWidgets

        mdi_area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        active = mdi_area.currentSubWindow() if mdi_area is not None else None
        if active is not None:
            _on_sub_window_activated(active)
    except (AttributeError, RuntimeError):
        pass


def _install_main_window_close_guard(main_window: Any) -> None:
    """Install one pre-close sensor guard on FreeCAD's main window."""
    global _main_window_filter
    if _main_window_filter is not None:
        return
    try:
        from PySide import QtCore, QtWidgets

        class _MainWindowCloseFilter(QtCore.QObject):
            def eventFilter(self, watched: Any, event: Any) -> bool:
                event_type = event.type()
                if event_type == QtCore.QEvent.Close:
                    _suspend_for_main_window_close()
                elif event_type in (QtCore.QEvent.Show, QtCore.QEvent.WindowActivate):
                    if _close_suspended:
                        QtCore.QTimer.singleShot(0, _resume_after_cancelled_close)
                return False

        observer = _MainWindowCloseFilter(main_window)
        main_window.installEventFilter(observer)
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(_suspend_for_main_window_close)
        _main_window_filter = observer
    except (AttributeError, RuntimeError, TypeError) as exc:
        _warn(f"main-window close guard unavailable: {exc}")


def _ensure_maintenance_timer() -> None:
    global _maintenance_timer
    if _close_suspended:
        return
    if _maintenance_timer is not None:
        return
    try:
        import FreeCADGui as Gui
        from PySide import QtCore

        timer = QtCore.QTimer(Gui.getMainWindow())
        # Poll only the active 3D view. This is frequent enough to track a
        # camera gesture without installing a Python Coin sensor whose
        # callback can outlive an MDI view transition.
        timer.setInterval(100)
        timer.timeout.connect(_update_adaptive_controllers)
        timer.start()
        _maintenance_timer = timer
    except (AttributeError, RuntimeError) as exc:
        _warn(f"adaptive grid timer unavailable: {exc}")


def _ensure_adaptive_controller(view: Any, grid: Any) -> None:
    if _close_suspended or not _same_view(view, _active_3d_view()):
        return
    parent = _active_view_parent(view)
    for controller in _adaptive_controllers:
        if controller.matches(view):
            controller.grid = grid
            controller.ensure_parent(parent)
            controller.schedule_update()
            return
    _adaptive_controllers.append(_AdaptiveGridController(view, grid, parent))
    _ensure_maintenance_timer()


def is_enabled() -> bool:
    """Return True when the always-on grid feature is enabled."""
    if App is None:
        return False
    return App.ParamGet(_VIBECAD_PARAM_PATH).GetBool("AlwaysShowGrid", True)


def seed_grid_preferences() -> None:
    """Seed Draft grid preferences once so the grid shows app-wide.

    Write-once: guarded by a flag in the VibeCAD parameter group. If the user
    later disables the grid via Draft preferences, we never force it back on.
    """
    if App is None:
        return
    vibecad_params = App.ParamGet(_VIBECAD_PARAM_PATH)
    if vibecad_params.GetBool(_SEED_FLAG, False):
        return
    draft_params = App.ParamGet(_DRAFT_PARAM_PATH)
    draft_params.SetBool("alwaysShowGrid", True)
    draft_params.SetBool("GridHideInOtherWorkbenches", False)
    vibecad_params.SetBool(_SEED_FLAG, True)


def _get_snapper() -> Any:
    """Return the shared Draft Snapper, creating it if it does not exist."""
    import FreeCADGui as Gui
    import WorkingPlane

    # Draft's tracker classes still read the compatibility
    # ``FreeCAD.DraftWorkingPlane`` attribute. The supported working-plane
    # API creates and synchronizes that attribute for the active 3D view;
    # importing gui_snapper alone does not.
    WorkingPlane.get_working_plane(update=False)

    snapper = getattr(Gui, "Snapper", None)
    if snapper is None:
        from draftguitools import gui_snapper

        snapper = gui_snapper.Snapper()
        Gui.Snapper = snapper
    return snapper


def _grid_should_always_show() -> bool:
    """Return the current value of the Draft ``alwaysShowGrid`` preference."""
    if App is None:
        return False
    return App.ParamGet(_DRAFT_PARAM_PATH).GetBool("alwaysShowGrid", False)


def _apply_active_grid_state(payload: tuple[Any, Any, int]) -> None:
    """Apply desired grid state after Coin-safe deferred tracker insertion."""
    view, grid, expected_generation = payload
    if (
        _close_suspended
        or expected_generation != _activation_generation
        or not _same_view(view, _active_3d_view())
        or not _view_is_live(view)
    ):
        return
    if not _grid_should_always_show():
        try:
            grid.show_always = False
            grid.off()
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        return

    try:
        switch = grid.switch
        if switch is None:
            return
        owner = getattr(grid, "_scene_view", None)
        if owner is None:
            # Compatibility with a tracker created before view ownership was
            # introduced during this process.
            grid._scene_view = view
        elif not _same_view(owner, view):
            _warn("grid tracker ownership did not match its registered 3D view")
            return

        desired_scene = view.getSceneGraph()
        for candidate in _live_3d_views():
            if _same_view(candidate, view):
                continue
            try:
                scene = candidate.getSceneGraph()
                if scene.findChild(switch) >= 0:
                    scene.removeChild(switch)
            except (AttributeError, ReferenceError, RuntimeError):
                continue
        if desired_scene.findChild(switch) < 0:
            desired_scene.addChild(switch)

        grid.show_always = True
        grid.set()
        if not _grid_is_rendered(view, grid):
            _warn("grid tracker did not enter its owning 3D scene")
            return
        _ensure_adaptive_controller(view, grid)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        _warn(f"grid scene reconciliation failed: {exc}")


def _queue_active_grid_state(view: Any, grid: Any, generation: int) -> None:
    try:
        from draftutils.todo import ToDo

        ToDo.delay(_apply_active_grid_state, (view, grid, generation))
    except Exception as exc:
        _warn(f"grid scene update could not be queued: {exc}")


def is_grid_visible() -> bool:
    """Return True when the active 3D view actually renders its grid.

    Scene membership is authoritative; a stale tracker from a closed document
    or a switch attached to another view must not report a visible grid.
    Falls back to the preference only when no 3D view is active.
    """
    if App is None or not App.GuiUp:
        return False
    try:
        import FreeCADGui as Gui

        view = _active_3d_view()
        if view is None:
            return _grid_should_always_show()
        snapper = getattr(Gui, "Snapper", None)
        if snapper is not None:
            index = _tracker_index(snapper, view)
            if index is not None:
                return _grid_is_rendered(view, snapper.trackers[1][index])
        return False
    except Exception as exc:
        _warn(f"grid visibility query failed: {exc}")
    return _grid_should_always_show()


def toggle_grid(show: bool | None = None) -> None:
    """Show or hide the grid in every 3D view, current and future.

    Writes the Draft ``alwaysShowGrid`` preference (so views opened later
    follow suit) and flips all existing grid trackers. With ``show=None`` the
    current visibility is inverted.
    """
    if App is None or not App.GuiUp:
        return
    try:
        if show is None:
            show = not is_grid_visible()
        show = bool(show)
        App.ParamGet(_DRAFT_PARAM_PATH).SetBool("alwaysShowGrid", show)

        import FreeCADGui as Gui

        # A native View-menu command remains available before a document is
        # opened. In that state the preference is all that should change:
        # creating Draft's Snapper would attempt to initialize view trackers
        # without a 3D view.
        if Gui.activeDocument() is None:
            return

        snapper = _get_snapper()
        if show:
            for grid in snapper.trackers[1]:
                grid.show_always = True
            snapper.show()
            try:
                from PySide import QtWidgets

                mdi_area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
                active = mdi_area.currentSubWindow() if mdi_area is not None else None
                if active is not None:
                    _on_sub_window_activated(active)
            except (AttributeError, RuntimeError):
                pass
        else:
            for grid in snapper.trackers[1]:
                grid.show_always = False
                grid.off()
            _update_adaptive_controllers()
    except Exception as exc:
        _warn(f"grid toggle failed: {exc}")


# ---------------------------------------------------------------------------
# Per-view grid initialization
# ---------------------------------------------------------------------------


def _show_grid_in_active_view(
    expected_generation: int | None = None,
    window: Any = None,
) -> None:
    """Reconcile the desired grid into the active 3D view.

    The persisted preference is the desired state. Tracker-list membership is
    not sufficient: the Coin switch is verified in this exact view's scene
    graph by the deferred apply step.
    """
    if (
        App is None
        or not App.GuiUp
        or _close_suspended
        or (
            expected_generation is not None
            and expected_generation != _activation_generation
        )
    ):
        return
    try:
        view = _active_3d_view()
        if view is None:
            return
        if window is None:
            try:
                import FreeCADGui as Gui
                from PySide import QtWidgets

                mdi_area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
                window = mdi_area.currentSubWindow() if mdi_area is not None else None
            except (AttributeError, RuntimeError):
                window = None
        _observe_sub_window(window, view)
        _remember_document_view(view)
        if not _grid_should_always_show():
            return
        key = id(view)
        snapper = _get_snapper()
        index = _tracker_index(snapper, view)
        if index is None:
            # A recycled Python id must never suppress initialization of a new
            # native view; tracker membership is the identity boundary.
            _seen_views.discard(key)
            snapper.setTrackers()
            index = _tracker_index(snapper, view)
        _seen_views.add(key)
        if index is not None:
            generation = (
                _activation_generation
                if expected_generation is None
                else expected_generation
            )
            _queue_active_grid_state(view, snapper.trackers[1][index], generation)
    except Exception as exc:
        _warn(f"unable to show grid in active view: {exc}")


def _on_sub_window_activated(_window: Any = None) -> None:
    """Defer grid initialization until this exact activation settles.

    A spreadsheet can be activated immediately after a 3D subwindow and
    before its queued callback runs. The generation token makes that older
    callback inert, so it cannot create or update Coin trackers against the
    wrong native MDI view.
    """
    global _activation_generation
    _activation_generation += 1
    generation = _activation_generation
    if _window is None:
        # QMdiArea emits ``subWindowActivated(None)`` synchronously while the
        # final document view is closing. Release per-view widgets before Qt
        # destroys their parents.
        _dispose_adaptive_controllers()
        return
    if _close_suspended:
        return
    try:
        from PySide import QtCore

        QtCore.QTimer.singleShot(
            0,
            lambda: _show_grid_in_active_view(generation, _window),
        )
    except Exception as exc:
        _warn(f"deferred grid update failed: {exc}")


def _on_workbench_activated(_workbench: str = "") -> None:
    """Reconcile after workbench teardown and setup have both completed."""
    if _close_suspended:
        return
    try:
        import FreeCADGui as Gui
        from PySide import QtWidgets

        mdi_area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        active = mdi_area.currentSubWindow() if mdi_area is not None else None
        if active is not None:
            _on_sub_window_activated(active)
    except (AttributeError, RuntimeError):
        return


def _install_view_observer() -> None:
    """Install the MDI observer that grids new 3D views (idempotent)."""
    global _observer_installed
    if _observer_installed:
        return
    try:
        import FreeCADGui as Gui
        from PySide import QtWidgets

        main_window = Gui.getMainWindow()
        _install_main_window_close_guard(main_window)
        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        if mdi_area is None:
            _warn("MDI area not found; grid observer not installed")
            return
        mdi_area.subWindowActivated.connect(_on_sub_window_activated)
        main_window.workbenchActivated.connect(_on_workbench_activated)
        _observer_installed = True
        _ensure_maintenance_timer()
        # Handle a 3D view that is already active at setup time. Passing None
        # means a native subwindow closed, so never use it as a setup sentinel.
        active = mdi_area.currentSubWindow()
        if active is not None:
            _on_sub_window_activated(active)
    except Exception as exc:
        _warn(f"observer installation failed: {exc}")


def setup() -> None:
    """Install the grid feature (idempotent).

    Preference seeding is gated by the ``AlwaysShowGrid`` kill-switch, so the
    grid only turns itself on at startup when the feature is enabled. The view
    observer follows the Draft ``alwaysShowGrid`` preference for current and
    future 3D views. View-menu ownership remains entirely native.
    """
    if App is None or not App.GuiUp:
        return
    if is_enabled():
        seed_grid_preferences()
    _install_document_observer()
    _install_view_observer()
