# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Qt gate for Points project-artifact management in the code editor."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtWidgets  # noqa: E402

import VibeCADPointArtifacts as artifacts  # noqa: E402
import VibeCADScriptedEditor as editor  # noqa: E402


def _wait_until(predicate, *, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for the Points artifact UI operation.")


class _Service:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.workbench = "PointsWorkbench"
        self.main_thread = threading.get_ident()
        self.background_calls: list[tuple[str, int]] = []

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    def active_workbench_name(self) -> str:
        return self.workbench

    def project_scope_snapshot(self) -> dict[str, str]:
        assert threading.get_ident() == self.main_thread
        return {"root": str(self.project_root)}

    def _record_background(self, operation: str) -> None:
        thread_id = threading.get_ident()
        assert thread_id != self.main_thread
        self.background_calls.append((operation, thread_id))

    def point_artifacts(self, *, project_root: str) -> dict[str, object]:
        self._record_background("list")
        assert project_root == str(self.project_root)
        return {"ok": True, **artifacts.point_artifacts_summary(project_root)}

    def approve_point_artifact(
        self,
        source_path: str,
        *,
        label: str,
        project_root: str,
    ) -> dict[str, object]:
        self._record_background("approve")
        assert project_root == str(self.project_root)
        return {
            "ok": True,
            "artifact": artifacts.approve_point_artifact(
                project_root,
                source_path,
                label=label,
            ),
        }

    def remove_point_artifact(
        self,
        artifact_id: str,
        *,
        project_root: str,
    ) -> dict[str, object]:
        self._record_background("remove")
        assert project_root == str(self.project_root)
        return {
            "ok": True,
            **artifacts.remove_point_artifact(project_root, artifact_id),
        }


def main() -> int:
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory(prefix="vibecad-points-ui-") as temporary:
        project_root = Path(temporary) / "project"
        project_root.mkdir()
        source = Path(temporary) / "approved-cloud.xyz"
        source.write_text("0 0 0\n1 2 3\n", encoding="utf-8")
        service = _Service(project_root)
        original_get_service = editor.get_service
        editor.get_service = lambda: service
        dock = QtWidgets.QDockWidget()
        widget = editor._build_widget()
        dock.setWidget(widget)
        controller = editor.ScriptedEditorController(dock)
        controller.editor_active = True
        controller._start_vibescript_model_refresh = (
            lambda preferred_model_id="": None
        )
        try:
            controller.refresh()
            _wait_until(lambda: not controller.point_artifact_busy)
            assert controller.engine == "vibescript"
            assert controller.domain == "points"
            visible_editor_actions = [
                controller.button(name).text()
                for name in (
                    "VibeScriptedNew",
                    "VibeScriptedSave",
                    "VibeScriptedRender",
                    "VibeScriptedAccept",
                    "VibeScriptedRevert",
                    "VibeScriptedImport",
                    "VibeScriptedExport",
                )
                if not controller.button(name).isHidden()
            ]
            assert visible_editor_actions == ["New", "Save", "Build"]
            assert not controller.point_artifact_row.isHidden()
            assert controller.point_artifact_selector.count() == 1
            assert controller.point_artifact_selector.currentData() == ""
            assert controller.model_id == ""
            assert controller.active_prepared is None
            assert controller.preview_revision == ""

            controller._approve_point_artifact_path(
                str(source), str(project_root)
            )
            _wait_until(
                lambda: not controller.point_artifact_busy
                and controller.point_artifact_selector.count() == 2
            )
            artifact_id = str(controller.point_artifact_selector.currentData() or "")
            assert len(artifact_id) == 32
            assert artifact_id in controller.point_artifact_selector.currentText()
            tooltip = str(
                controller.point_artifact_selector.currentData(
                    controller.QtCore.Qt.ToolTipRole
                )
                or ""
            )
            assert artifact_id in tooltip
            assert f"{{'artifact_id': '{artifact_id}'}}" in controller.status.text()
            summary = artifacts.point_artifacts_summary(project_root)
            assert summary["artifact_count"] == 1
            assert summary["artifacts"][0]["available"] is True

            controller.status.setText("stale-event-sentinel")
            controller._preview_completed(
                {
                    "event_kind": "point_artifact_list",
                    "engine": "vibescript",
                    "domain": "points",
                    "artifact_generation": controller.point_artifact_generation - 1,
                    "result": {"ok": False, "error": "stale event applied"},
                }
            )
            assert controller.status.text() == "stale-event-sentinel"

            controller._remove_point_artifact_id(artifact_id, str(project_root))
            _wait_until(
                lambda: not controller.point_artifact_busy
                and controller.point_artifact_selector.count() == 1
            )
            assert artifacts.point_artifacts_summary(project_root)["artifact_count"] == 0
            assert artifact_id in controller.status.text()

            service.workbench = "PartWorkbench"
            controller.refresh()
            assert controller.domain == "part"
            assert controller.point_artifact_row.isHidden()
            assert controller.point_artifact_selector.currentData() == ""
            assert {name for name, _thread_id in service.background_calls} >= {
                "list",
                "approve",
                "remove",
            }
            assert all(
                thread_id != service.main_thread
                for _name, thread_id in service.background_calls
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "integration": "points_artifact_ui",
                        "background_operations": [
                            name for name, _thread_id in service.background_calls
                        ],
                        "stable_id_visible": True,
                        "stale_event_rejected": True,
                        "opening_created_preview": False,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0
        finally:
            editor.get_service = original_get_service
            controller.editor_active = False
            dock.close()
            dock.deleteLater()
            QtWidgets.QApplication.processEvents()
            main_window = Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None
            if main_window is not None:
                main_window.close()
            QtWidgets.QApplication.processEvents()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            QtWidgets.QApplication.processEvents()


class PointsArtifactUIIntegration(unittest.TestCase):
    """FreeCAD internal-GUI entry point for the artifact panel lifecycle."""

    def test_artifact_panel_lifecycle(self) -> None:
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    result_code = main()
    if result_code:
        raise RuntimeError(
            f"Points artifact UI integration failed with {result_code}."
        )
