# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD
import Part

from .CompoundFilter import makeCompoundFilter


def explodeCompound(compound_obj, b_group=None):
    """explodeCompound(compound_obj, b_group = None): creates a bunch of compound filters, to extract every child of a compound into a separate object.
    group: if True, Group is always made. If False, group is never made. If None, group is made if there is more than one child.
    returns: (group_object, list_of_child_objects)"""

    if isinstance(compound_obj, FreeCAD.GeoFeature) and isinstance(
        compound_obj.getPropertyOfGeometry(), Part.Shape
    ):
        sh = compound_obj.getPropertyOfGeometry()
    else:
        raise TypeError("Object must be App.GeoFeature with Part.Shape property")

    n = len(sh.childShapes(False, False))
    body_target = None
    if b_group is None:
        try:
            parent = compound_obj.getParentGeoFeatureGroup()
        except (AttributeError, RuntimeError):
            parent = None
        if parent is not None and parent.isDerivedFrom("PartDesign::Body"):
            # In the consolidated modeling workbench, exploded pieces are features of the
            # existing Body. A nested plain group would split ownership and recreate the tree
            # ambiguity that Part Design now resolves.
            body_target = parent
            b_group = False
        else:
            b_group = n > 1
    if body_target is not None:
        group = body_target
    elif b_group:
        group = compound_obj.Document.addObject(
            "App::DocumentObjectGroup", "GrExplode_" + compound_obj.Name
        )
        group.Label = "Exploded {obj.Label}".format(obj=compound_obj)
    else:
        group = compound_obj.Document
    features_created = []
    for i in range(0, n):
        cf = makeCompoundFilter(
            "{obj.Name}_child{child_num}".format(obj=compound_obj, child_num=i), group
        )
        cf.Label = "{obj.Label}.{child_num}".format(obj=compound_obj, child_num=i)
        cf.Base = compound_obj
        cf.FilterType = "specific items"
        cf.items = str(i)
        if cf.ViewObject is not None:
            cf.ViewObject.DontUnhideOnDelete = True
        features_created.append(cf)
    return (group, features_created)
