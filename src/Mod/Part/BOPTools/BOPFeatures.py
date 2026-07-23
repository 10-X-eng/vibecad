# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2023 Werner Mayer <wmayer[at]users.sourceforge.net>     *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

__title__ = "BOPTools.BOPFeatures module"
__author__ = "Werner Mayer"
__url__ = "https://www.freecad.org"
__doc__ = "Helper class to create the features for Boolean operations."

class BOPFeatures:
    def __init__(self, doc):
        self.doc = doc

    def make_section(self, inputNames):
        obj = self.doc.addObject("Part::Section", "Section")
        obj.Base = self.doc.getObject(inputNames[0])
        obj.Tool = self.doc.getObject(inputNames[1])
        self.copy_visual_attributes(obj, obj.Base)
        target = self.common_input_owner([obj.Base, obj.Tool])
        self.add_result_to_target(target, obj)
        return obj

    def make_cut(self, inputNames):
        obj = self.doc.addObject("Part::Cut", "Cut")
        obj.Base = self.doc.getObject(inputNames[0])
        obj.Tool = self.doc.getObject(inputNames[1])
        self.copy_visual_attributes(obj, obj.Base)
        target = self.common_input_owner([obj.Base, obj.Tool])
        self.add_result_to_target(target, obj)
        return obj

    def make_common(self, inputNames):
        obj = self.doc.addObject("Part::Common", "Common")
        obj.Base = self.doc.getObject(inputNames[0])
        obj.Tool = self.doc.getObject(inputNames[1])
        self.copy_visual_attributes(obj, obj.Base)
        target = self.common_input_owner([obj.Base, obj.Tool])
        self.add_result_to_target(target, obj)
        return obj

    def make_multi_common(self, inputNames):
        obj = self.doc.addObject("Part::MultiCommon", "Common")
        obj.Shapes = [self.doc.getObject(name) for name in inputNames]
        self.copy_visual_attributes(obj, obj.Shapes[0])
        target = self.common_input_owner(obj.Shapes)
        self.add_result_to_target(target, obj)
        return obj

    def make_fuse(self, inputNames):
        obj = self.doc.addObject("Part::Fuse", "Fusion")
        obj.Base = self.doc.getObject(inputNames[0])
        obj.Tool = self.doc.getObject(inputNames[1])
        self.copy_visual_attributes(obj, obj.Base)
        target = self.common_input_owner([obj.Base, obj.Tool])
        self.add_result_to_target(target, obj)
        return obj

    def make_multi_fuse(self, inputNames):
        obj = self.doc.addObject("Part::MultiFuse", "Fusion")
        obj.Shapes = [self.doc.getObject(name) for name in inputNames]
        self.copy_visual_attributes(obj, obj.Shapes[0])
        target = self.common_input_owner(obj.Shapes)
        self.add_result_to_target(target, obj)
        return obj

    @staticmethod
    def add_result_to_target(target, obj):
        if target and (not hasattr(target, "Group") or obj not in target.Group):
            target.addObject(obj)

    def common_input_owner(self, objects):
        parents = []
        for obj in objects:
            obj.Visibility = False
            try:
                parent = obj.getParentGeoFeatureGroup()
            except (AttributeError, RuntimeError):
                parent = None
            if parent is None:
                try:
                    parent = obj.getParentGroup()
                except (AttributeError, RuntimeError):
                    parent = None
            parents.append(parent)

        # Dependencies remain where their creators put them.  Reparenting an
        # operand merely to recreate Part's historical nested tree is unsafe:
        # operands may belong to different Bodies or App::Parts, and a feature
        # can belong to only one GeoFeatureGroup.  Place the result beside the
        # inputs only when every input already has the exact same owner.
        if parents and parents[0] is not None and all(
            parent == parents[0] for parent in parents[1:]
        ):
            return parents[0]
        return None

    def copy_visual_attributes(self, target, source):
        target_view = getattr(target, "ViewObject", None)
        source_view = getattr(source, "ViewObject", None)
        if target_view and source_view:
            displayMode = source_view.DisplayMode
            src = source
            while displayMode == "Link":
                if getattr(src, "LinkedObject", None):
                    candidate = src.LinkedObject
                elif getattr(src, "Base", None):
                    # Draft Link array
                    candidate = src.Base
                else:
                    break
                candidate_view = getattr(candidate, "ViewObject", None)
                if not candidate_view:
                    break
                src = candidate
                source_view = candidate_view
                displayMode = source_view.DisplayMode
            if displayMode in target_view.getEnumerationsOfProperty("DisplayMode"):
                target_view.DisplayMode = displayMode
            # Seed the result with the source's overall appearance before its shape is
            # recomputed. The Part view provider then expands that appearance across the
            # result faces instead of expanding the default gray material.
            target_view.ShapeColor = source_view.ShapeColor
            target_view.LineColor = source_view.LineColor
            target_view.PointColor = source_view.PointColor
            target_view.Transparency = source_view.Transparency
