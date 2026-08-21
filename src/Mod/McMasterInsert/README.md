# McMaster-Carr insert

Python workbench that opens the live McMaster-Carr catalog inside VibeCAD and
imports 3-D STEP as a `PartDesign::Component`.

## Use

- Ribbon tab **McMaster**: **Catalog** and **Import**
- Menu **McMaster-Carr → Open Cache Folder** for local copies

Download **3-D STEP** from a product page. The catalog overlay intercepts the
file (no Save dialog on macOS), imports it, names the component with the part
number, and puts the catalog title on **Description**. A transform manipulator
opens so the component can be placed immediately. The overlay closes after one
part.

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

Catalog opens the live site in the system browser. Download 3-D STEP to the
standard Downloads folder and VibeCAD imports it automatically. If the browser
saves somewhere else, use **Import** to select the downloaded CAD file.

## Cache

Downloaded CAD is stored under the user app-data directory:

`McMasterCache/<part-number>/…STEP`

## Tests

```
python3 -m unittest src/Mod/McMasterInsert/tests/test_catalog.py src/Mod/McMasterInsert/tests/test_ribbon.py
```

`test_catalog.py` needs FreeCAD on `PYTHONPATH` (or `freecadcmd`). `test_ribbon.py` is pure Python.
