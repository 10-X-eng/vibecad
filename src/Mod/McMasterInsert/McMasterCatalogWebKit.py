#!/usr/bin/python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Linux McMaster-Carr catalog window using the platform WebKitGTK runtime."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from urllib.parse import urlparse


CAD_SUFFIXES = {
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
}
STEP_SUFFIXES = {".step", ".stp"}


def load_webkit():
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import GLib, Gtk, WebKit2

    return GLib, Gtk, WebKit2


def safe_filename(name: str) -> str:
    candidate = Path(str(name or "")).name.replace("/", "_").replace("\\", "_")
    return candidate if candidate not in {"", ".", ".."} else "McMaster-CAD.step"


def is_cad_filename(name: str) -> bool:
    candidate = Path(str(name or "")).name
    return bool(candidate) and Path(candidate).suffix.lower() in CAD_SUFFIXES


def is_complete_step_file(path: Path) -> bool:
    """Return whether a staged STEP file contains its complete exchange record."""
    candidate = Path(path)
    filename = candidate.name
    if filename.lower().endswith(".download"):
        filename = filename[: -len(".download")]
    if Path(filename).suffix.lower() not in STEP_SUFFIXES:
        return False
    try:
        size = candidate.stat().st_size
        if size < 40:
            return False
        with candidate.open("rb") as stream:
            header = stream.read(256)
            stream.seek(max(0, size - 256))
            trailer = stream.read()
    except OSError:
        return False
    header = header.lstrip(b"\xef\xbb\xbf\r\n\t ")
    return header.startswith(b"ISO-10303-21;") and trailer.rstrip().endswith(
        b"END-ISO-10303-21;"
    )


def unique_path(folder: Path, suggested_name: str) -> Path:
    filename = Path(safe_filename(suggested_name))
    candidate = folder / filename
    if not candidate.exists() and not Path(str(candidate) + ".download").exists():
        return candidate
    for suffix in range(2, 10000):
        candidate = folder / f"{filename.stem}-{suffix}{filename.suffix}"
        if not candidate.exists() and not Path(str(candidate) + ".download").exists():
            return candidate
    return folder / f"{filename.stem}-download{filename.suffix}"


def create_web_context(WebKit2, profile: Path):
    """Create a persistent catalog context, including login cookies."""
    profile.mkdir(parents=True, exist_ok=True)
    manager = WebKit2.WebsiteDataManager(
        base_data_directory=str(profile / "data"),
        base_cache_directory=str(profile / "cache"),
    )
    manager.get_cookie_manager().set_persistent_storage(
        str(profile / "cookies.sqlite"),
        WebKit2.CookiePersistentStorage.SQLITE,
    )
    return WebKit2.WebContext.new_with_website_data_manager(manager)


class CatalogWindow:
    def __init__(self, args, modules) -> None:
        self.args = args
        self.GLib, self.Gtk, self.WebKit2 = modules
        self.inbox = Path(args.inbox).resolve()
        self.profile = Path(args.profile).resolve() / "webkitgtk"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.profile.mkdir(parents=True, exist_ok=True)
        self.downloads = {}
        self.windows = []

        self.context = create_web_context(self.WebKit2, self.profile)
        self.context.connect("download-started", self._download_started)
        self._create_window(args.url, primary=True)
        if args.parent_pid > 0:
            self.GLib.timeout_add(1000, self._check_parent)

    def _create_webview(self):
        view = self.WebKit2.WebView.new_with_context(self.context)
        view.connect("decide-policy", self._decide_policy)
        view.connect("create", self._create_popup)
        view.connect("load-changed", self._load_changed)
        view.connect("load-failed", self._load_failed)
        return view

    def _create_window(self, url: str = "", primary: bool = False):
        window = self.Gtk.Window(title="Insert McMaster-Carr Component")
        window.set_default_size(1000, 720)
        window.set_position(self.Gtk.WindowPosition.CENTER)
        window.connect("destroy", self._window_destroyed, primary)

        header = self.Gtk.HeaderBar()
        header.set_title("Insert McMaster-Carr Component")
        header.set_subtitle("Download 3-D STEP to insert it into VibeCAD")
        header.set_show_close_button(True)
        window.set_titlebar(header)

        view = self._create_webview()
        back = self.Gtk.Button.new_from_icon_name(
            "go-previous-symbolic", self.Gtk.IconSize.BUTTON
        )
        forward = self.Gtk.Button.new_from_icon_name(
            "go-next-symbolic", self.Gtk.IconSize.BUTTON
        )
        reload_button = self.Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", self.Gtk.IconSize.BUTTON
        )
        back.connect("clicked", lambda _button: view.go_back())
        forward.connect("clicked", lambda _button: view.go_forward())
        reload_button.connect("clicked", lambda _button: view.reload())
        header.pack_start(back)
        header.pack_start(forward)
        header.pack_end(reload_button)

        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL)
        status = self.Gtk.Label(label="Loading McMaster-Carr…")
        status.set_xalign(0.0)
        status.set_margin_start(10)
        status.set_margin_end(10)
        status.set_margin_top(5)
        status.set_margin_bottom(5)
        box.pack_start(view, True, True, 0)
        box.pack_end(status, False, False, 0)
        window.add(box)
        window._catalog_view = view
        window._catalog_status = status
        self.windows.append(window)
        window.show_all()
        if url:
            view.load_uri(url)
        return view

    def _create_popup(self, _view, navigation_action):
        uri = ""
        try:
            uri = navigation_action.get_request().get_uri()
        except Exception:
            pass
        return self._create_window(uri)

    def _decide_policy(self, _view, decision, decision_type):
        if decision_type != self.WebKit2.PolicyDecisionType.RESPONSE:
            return False
        try:
            response = decision.get_response()
            filename = response.get_suggested_filename() or ""
            path = urlparse(response.get_uri() or "").path
            if is_cad_filename(filename) or is_cad_filename(path):
                decision.download()
                return True
        except Exception:
            return False
        return False

    def _download_started(self, _context, download) -> None:
        download.connect("decide-destination", self._decide_destination)
        download.connect("received-data", self._download_received)
        download.connect("finished", self._download_finished)
        download.connect("failed", self._download_failed)

    def _decide_destination(self, download, suggested_name: str) -> bool:
        if not is_cad_filename(suggested_name):
            return False
        target = unique_path(self.inbox, suggested_name)
        staging = Path(str(target) + ".download")
        staging.unlink(missing_ok=True)
        self.downloads[id(download)] = (staging, target)
        download.set_allow_overwrite(False)
        download.set_destination(staging.as_uri())
        self._set_status(f"Downloading {target.name}…")
        return True

    def _download_received(self, download, _data_length: int) -> None:
        paths = self.downloads.get(id(download))
        if paths is not None and is_complete_step_file(paths[0]):
            self._publish_download(download)

    def _download_finished(self, download) -> None:
        self._publish_download(download)

    def _publish_download(self, download) -> None:
        paths = self.downloads.pop(id(download), None)
        if paths is None:
            return
        staging, target = paths
        try:
            staging.replace(target)
        except OSError as exc:
            self._set_status(f"Download finished, but could not publish it: {exc}")
            return
        print(f"Downloaded {target}", flush=True)
        self._set_status(f"Downloaded {target.name}. VibeCAD is importing it now.")
        if not self.downloads:
            self.GLib.timeout_add(500, self._quit)

    def _download_failed(self, download, error) -> None:
        paths = self.downloads.pop(id(download), None)
        if paths is not None:
            paths[0].unlink(missing_ok=True)
        self._set_status(f"Download failed: {error}")

    def _load_changed(self, view, event) -> None:
        if event == self.WebKit2.LoadEvent.FINISHED:
            self._set_status("Download 3-D STEP to insert it into VibeCAD.")
        elif event == self.WebKit2.LoadEvent.STARTED:
            self._set_status("Loading McMaster-Carr…")

    def _load_failed(self, _view, _event, failing_uri, error) -> bool:
        if getattr(error, "code", None) != 1:
            self._set_status(f"Could not load {failing_uri}: {error.message}")
        return False

    def _set_status(self, text: str) -> None:
        for window in self.windows:
            label = getattr(window, "_catalog_status", None)
            if label is not None:
                label.set_text(text)

    def _window_destroyed(self, window, primary: bool) -> None:
        if window in self.windows:
            self.windows.remove(window)
        if primary or not self.windows:
            self._quit()

    def _check_parent(self) -> bool:
        try:
            os.kill(self.args.parent_pid, 0)
        except (OSError, ProcessLookupError):
            self._quit()
            return False
        return True

    def _quit(self) -> bool:
        for window in list(self.windows):
            try:
                window.destroy()
            except Exception:
                pass
        self.windows.clear()
        self.Gtk.main_quit()
        return False

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_args: self.GLib.idle_add(self._quit))
        signal.signal(signal.SIGINT, lambda *_args: self.GLib.idle_add(self._quit))
        self.Gtk.main()


def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--inbox", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--url", default="https://www.mcmaster.com/")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--smoke-test", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        modules = load_webkit()
    except Exception as exc:
        print(f"WebKitGTK 4.1 is unavailable: {exc}", file=sys.stderr)
        return 1
    if args.smoke_test:
        print("WebKitGTK browser helper is available")
        return 0
    if not args.inbox or not args.profile:
        print("McMaster helper requires --inbox and --profile", file=sys.stderr)
        return 2
    CatalogWindow(args, modules).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
