# BIM and Architecture Removal

VibeCAD no longer ships the BIM workbench or the architectural tools that were
coupled to Draft and TechDraw. This narrows the product to its mechanical and
general-purpose CAD workflows while keeping Draft available for ordinary 2D
drawing and geometry.

## Removed Surface

- The BIM workbench, commands, preferences, examples, icons, translations, and
  Start page entry.
- Architectural Draft objects and editing behavior, including walls, windows,
  structures, building parts, section planes, axes, rebars, and IFC grouping.
- The TechDraw architectural view type and its DXF export path.
- BIM-specific VibeCAD native tools, VibeScript domain, prompts, validation,
  tests, and visual fixtures.
- BIM-owned import and export handlers, including IFC support. These handlers
  were removed rather than moved into another workbench.
- IfcOpenShell and Lark packaging dependencies that were required only by BIM.

Unrelated features whose names happen to contain architectural terms remain.
For example, Part Design draft angles, Draft's architectural unit notation,
and ASME Arch paper sizes are still supported.

## Existing Documents

Documents containing legacy BIM, Arch, NativeIFC, or TechDraw architectural
objects are unsupported and may open with missing or degraded objects. VibeCAD
detects these signatures after document restore and shows a warning before the
user continues working. It does not silently convert or delete document data.

To preserve an architectural document:

1. Close it without saving in the current VibeCAD release.
2. Open it in a VibeCAD or FreeCAD release that still includes the BIM and Arch
   implementation.
3. Keep the original file and export any geometry needed in current VibeCAD to
   a neutral format such as STEP, BREP, or mesh.

On first launch after upgrading, VibeCAD removes obsolete BIM workbench entries
from the user's workbench and autoload preferences. If BIM was the last active
workbench, Part Design becomes the fallback. This preference migration does not
modify CAD documents.

## Rollback

There is no in-place rollback or compatibility shim for removed architectural
objects. Install a release from before this removal and reopen the untouched
original document. Files saved after opening with unsupported objects may no
longer be recoverable by reinstalling the older release, which is why the
warning recommends closing without saving.
