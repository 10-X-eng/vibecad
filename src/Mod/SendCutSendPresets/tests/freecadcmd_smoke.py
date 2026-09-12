# SPDX-License-Identifier: MIT
"""FreeCADCmd smoke: KFactor write on a FeaturePython (Unfold-like props)."""

import FreeCAD as App

doc = App.newDocument("SCS_Smoke")
obj = doc.addObject("Part::FeaturePython", "Unfold")
obj.addProperty("App::PropertyFloatConstraint", "KFactor", "Parameters", "Manual K-Factor")
obj.KFactor = (0.4, 0.0, 2.0, 0.01)
obj.addProperty("App::PropertyString", "MaterialSheet", "Parameters", "MDS")
obj.MaterialSheet = "_none"
before = float(obj.KFactor)
obj.KFactor = 0.5
after = float(obj.KFactor)
assert abs(before - 0.4) < 1e-9, before
assert abs(after - 0.5) < 1e-9, after
App.closeDocument(doc.Name)
print("freecadcmd_smoke OK: KFactor %s -> %s" % (before, after))
