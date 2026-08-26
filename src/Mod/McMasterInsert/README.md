# McMaster-Carr insert

Python workbench that opens the live McMaster-Carr catalog inside VibeCAD and
imports 3-D STEP as a `PartDesign::Component`.

## Use

- Ribbon tab **McMaster**: **Catalog** and **Import**
- Menu **McMaster-Carr → Open Cache Folder** for local copies

Download **3-D STEP** from a product page. The catalog overlay intercepts the
file, imports it, names the component with the part number, and puts the catalog
title on **Description**. A transform manipulator opens so the component can be
placed immediately. If more than one catalog download is active, VibeCAD waits
for and imports every completed file before closing the catalog.

## macOS catalog overlay

The Fusion-style overlay uses an in-process WKWebView (`McMasterWebKit.swift`)
attached to a Qt tool window. Build the helper library with:

```
swiftc -emit-library -o libMcMasterWebKit.dylib \
  -framework AppKit -framework WebKit McMasterWebKit.swift
```

Place `libMcMasterWebKit.dylib` next to the Python files. Without it, Catalog
still opens the overlay host; Import remains available for a STEP already on
disk.

`McMasterCatalog.swift` is the older out-of-process helper. It cannot join a
macOS fullscreen Space, so the in-process WebKit attach is preferred.

## Windows catalog

Catalog opens the live site in a VibeCAD window backed by the installed
Microsoft Edge WebView2 Runtime. Downloaded CAD goes directly to VibeCAD's
watched McMaster inbox and imports automatically. The WebView2 profile is kept
under `%LOCALAPPDATA%\VibeCAD\McMasterBrowser`, preserving McMaster cookies and
the login session across VibeCAD restarts and upgrades.

## Linux catalog

Catalog opens the live site in a separate WebKitGTK window when the host system
provides WebKitGTK 4.1 and Python GObject bindings. Downloads go directly to a
private VibeCAD inbox, so unrelated files in **Downloads** are ignored. Cookies
and website data are kept under the VibeCAD user-data directory in
`McMasterBrowser/webkitgtk`, preserving the McMaster login across launches.

If WebKitGTK is unavailable, Catalog opens the system browser and watches the
standard **Downloads** folder for one McMaster CAD file. Use **Import** when the
browser saves somewhere else.

## Cache

Downloaded CAD is stored under the user app-data directory:

`McMasterCache/<part-number>/…STEP`

## Tests

```
python3 -m unittest src/Mod/McMasterInsert/tests/test_catalog.py src/Mod/McMasterInsert/tests/test_ribbon.py
```

`test_catalog.py` needs FreeCAD on `PYTHONPATH` (or `freecadcmd`). `test_ribbon.py` is pure Python.
