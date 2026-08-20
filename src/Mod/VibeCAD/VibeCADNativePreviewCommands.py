# SPDX-License-Identifier: LGPL-2.1-or-later

"""Python commands for Native preview Apply / Reject. Not a C++ ribbon."""

from __future__ import annotations

from typing import Any


class _PreviewCommand:
    menu_text = "Native preview"
    tooltip = "Native preview control"
    pixmap = ""

    def GetResources(self) -> dict[str, Any]:
        return {
            "Pixmap": self.pixmap,
            "MenuText": self.menu_text,
            "ToolTip": self.tooltip,
        }

    def IsActive(self) -> bool:
        return False


class ApplyNativePreviewCommand(_PreviewCommand):
    menu_text = "Apply Native preview"
    tooltip = (
        "Apply the latest Native preview if the document revision is unchanged. "
        "A preview is not measured, manufacturable, or airworthy."
    )

    def IsActive(self) -> bool:
        try:
            from VibeCADCore import get_service
            from VibeCADNativePreviewControl import pending_document_previews

            return any(
                not item.get("stale") for item in pending_document_previews(get_service())
            )
        except Exception:
            return False

    def Activated(self) -> None:
        from VibeCADCore import get_service
        from VibeCADNativePreviewControl import apply_document_preview
        from VibeCADNativeSessionFactory import create_live_native_session_execution

        execution = create_live_native_session_execution(service=get_service())
        try:
            apply_document_preview(execution.dispatcher)
        finally:
            execution.close()


class RejectNativePreviewCommand(_PreviewCommand):
    menu_text = "Reject Native preview"
    tooltip = (
        "Reject the latest Native preview without changing the document. "
        "Stale previews may be rejected."
    )

    def IsActive(self) -> bool:
        try:
            from VibeCADCore import get_service
            from VibeCADNativePreviewControl import pending_document_previews

            return bool(pending_document_previews(get_service()))
        except Exception:
            return False

    def Activated(self) -> None:
        from VibeCADCore import get_service
        from VibeCADNativePreviewControl import reject_document_preview

        reject_document_preview(get_service())


def register_preview_commands(gui: Any | None = None) -> None:
    if gui is None:
        return
    add_command = getattr(gui, "addCommand", None)
    if not callable(add_command):
        return
    add_command("VibeCAD_ApplyNativePreview", ApplyNativePreviewCommand())
    add_command("VibeCAD_RejectNativePreview", RejectNativePreviewCommand())
