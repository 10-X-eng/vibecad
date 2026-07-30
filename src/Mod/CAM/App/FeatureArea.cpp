// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *   Copyright (c) 2017 Zheng Lei (realthunder) <realthunder.dev@gmail.com> *
 *                                                                          *
 *   This file is part of the FreeCAD CAx development system.               *
 *                                                                          *
 *   This library is free software; you can redistribute it and/or          *
 *   modify it under the terms of the GNU Library General Public            *
 *   License as published by the Free Software Foundation; either           *
 *   version 2 of the License, or (at your option) any later version.       *
 *                                                                          *
 *   This library  is distributed in the hope that it will be useful,       *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of         *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the          *
 *   GNU Library General Public License for more details.                   *
 *                                                                          *
 *   You should have received a copy of the GNU Library General Public      *
 *   License along with this library; see the file COPYING.LIB. If not,     *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,          *
 *   Suite 330, Boston, MA  02111-1307, USA                                 *
 *                                                                          *
 ****************************************************************************/

#include <BRep_Builder.hxx>
#include <Precision.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS_Compound.hxx>

#include <string_view>

#include <App/Document.h>
#include <Base/Console.h>  // for FC_LOG_LEVEL_INIT
#include <Base/Placement.h>

#include "FeatureArea.h"
#include "Base/TimeInfo.h"
#include "FeatureAreaPy.h"


FC_LOG_LEVEL_INIT("Path.Area", true, true)

using namespace Path;

PROPERTY_SOURCE(Path::FeatureArea, Part::Feature)

PARAM_ENUM_STRING_DECLARE(static const char* Enums, AREA_PARAMS_ALL)

FeatureArea::FeatureArea()
    : myInited(false)
{
    ADD_PROPERTY(Sources, (nullptr));
    ADD_PROPERTY_TYPE(
        WorkPlane,
        (TopoDS_Shape()),
        "Compatibility",
        App::Prop_Hidden,
        "Cached workplane shape retained for legacy callers"
    );
    ADD_PROPERTY_TYPE(
        WorkPlaneSourceEnabled,
        (false),
        "Area",
        App::Prop_Hidden,
        "Use the exact linked model subelement as the authoritative workplane"
    );
    ADD_PROPERTY_TYPE(
        WorkPlaneSource,
        (nullptr),
        "Area",
        App::Prop_None,
        "Exact model subelement defining the parametric workplane"
    );
    ADD_PROPERTY_TYPE(
        WorkPlaneSourceCollection,
        (""),
        "Area",
        App::Prop_Hidden,
        "Optional containing subshape collection used by the workplane source"
    );

    PARAM_PROP_ADD("Area", AREA_PARAMS_OPCODE);
    PARAM_PROP_ADD("Area", AREA_PARAMS_BASE);
    PARAM_PROP_ADD("Offset", AREA_PARAMS_OFFSET);
    PARAM_PROP_ADD("Offset", AREA_PARAMS_OFFSET_CONF);
    PARAM_PROP_ADD("Pocket", AREA_PARAMS_POCKET);
    PARAM_PROP_ADD("Pocket", AREA_PARAMS_POCKET_CONF);
    PARAM_PROP_ADD("Section", AREA_PARAMS_SECTION);
    PARAM_PROP_ADD("libarea", AREA_PARAMS_CAREA);

    PARAM_PROP_SET_ENUM(Enums, AREA_PARAMS_ALL);
    PocketMode.setValue((long)0);
}

FeatureArea::~FeatureArea()
{}

Area& FeatureArea::getArea()
{
    if (!myInited) {
        execute();
    }
    return myArea;
}

App::DocumentObjectExecReturn* FeatureArea::execute()
{
    myInited = true;

    Base::TimeTracker tracker("FeatureArea::execute");
    // Derived geometry must never outlive an invalid authoritative input.
    // WorkPlane remains a legacy compatibility/cache property, but stale
    // linked input cannot leave its previous result looking usable.
    Shape.setValue(TopoDS_Shape());
    myShapes.clear();
    myArea.clean(true);

    std::vector<App::DocumentObject*> links = Sources.getValues();
    if (links.empty()) {
        return new App::DocumentObjectExecReturn("No shapes linked");
    }

    for (std::vector<App::DocumentObject*>::iterator it = links.begin(); it != links.end(); ++it) {
        if (!(*it && (*it)->isDerivedFrom<Part::Feature>()) || !(*it)->isValid()) {
            return new App::DocumentObjectExecReturn("Linked object is not a valid Part object.");
        }
        TopoDS_Shape shape = static_cast<Part::Feature*>(*it)->Shape.getShape().getShape();
        if (shape.IsNull()) {
            return new App::DocumentObjectExecReturn("Linked shape object is empty");
        }
    }

    AreaParams params;

#define AREA_PROP_GET(_param) \
    params.PARAM_FNAME(_param) = static_cast<PARAM_TYPE(_param)>(PARAM_FNAME(_param).getValue());
    PARAM_FOREACH(AREA_PROP_GET, AREA_PARAMS_CONF)

    myArea.setParams(params);

    TopoDS_Shape workPlane = WorkPlane.getShape().getShape();
    if (WorkPlaneSourceEnabled.getValue()) {
        auto* sourceObject = WorkPlaneSource.getValue();
        if (!sourceObject) {
            return new App::DocumentObjectExecReturn(
                "Authoritative linked workplane source is missing"
            );
        }
        auto* document = getDocument();
        auto* source = freecad_cast<Part::Feature*>(sourceObject);
        if (!document || !source || !source->isValid() || source->getDocument() != document
            || !document->containsObject(source)) {
            return new App::DocumentObjectExecReturn(
                "Linked workplane source is not a live Part feature"
            );
        }
        const std::string_view sourceCollection(WorkPlaneSourceCollection.getValue());
        if (!sourceCollection.empty() && sourceCollection != "Wires") {
            return new App::DocumentObjectExecReturn("Unsupported workplane source collection");
        }
        Part::TopoShape sourceShape = source->Shape.getShape();
        const auto& subelements = WorkPlaneSource.getSubValues();
        if (subelements.size() > 1) {
            return new App::DocumentObjectExecReturn(
                "Workplane source must contain at most one subelement"
            );
        }
        if (!subelements.empty() && !subelements.front().empty()) {
            const TopoDS_Shape selected = sourceShape.getSubShape(subelements.front().c_str());
            sourceShape = Part::TopoShape(selected);
            if (sourceCollection == "Wires" && !selected.IsNull()) {
                for (TopExp_Explorer wire(source->Shape.getShape().getShape(), TopAbs_WIRE);
                     wire.More();
                     wire.Next()) {
                    bool containsSelected = false;
                    for (TopExp_Explorer edge(wire.Current(), TopAbs_EDGE); edge.More(); edge.Next()) {
                        if (edge.Current().IsSame(selected)) {
                            containsSelected = true;
                            break;
                        }
                    }
                    if (containsSelected) {
                        sourceShape = Part::TopoShape(wire.Current());
                        break;
                    }
                }
            }
        }
        workPlane = sourceShape.getShape();
        if (workPlane.IsNull()) {
            return new App::DocumentObjectExecReturn("Linked workplane source is empty");
        }
        WorkPlane.setValue(workPlane);
    }
    else if (
        WorkPlaneSource.getValue() || !std::string_view(WorkPlaneSourceCollection.getValue()).empty()
    ) {
        return new App::DocumentObjectExecReturn(
            "Disabled workplane source retains authoritative link data"
        );
    }
    myArea.setPlane(workPlane);

    for (std::vector<App::DocumentObject*>::iterator it = links.begin(); it != links.end(); ++it) {
        myArea.add(
            static_cast<Part::Feature*>(*it)->Shape.getShape().getShape(),
            PARAM_PROP_ARGS(AREA_PARAMS_OPCODE)
        );
    }

    myShapes.clear();
    if (myArea.getSectionCount() == 0) {
        myShapes.push_back(myArea.getShape(-1));
    }
    else {
        myShapes.reserve(myArea.getSectionCount());
        for (int i = 0; i < (int)myArea.getSectionCount(); ++i) {
            myShapes.push_back(myArea.getShape(i));
        }
    }

    bool hasShape = false;
    if (myShapes.empty()) {
        Shape.setValue(TopoDS_Shape());
    }
    else {
        // compound is built even if there is only one shape to save the
        // trouble of messing around with placement
        BRep_Builder builder;
        TopoDS_Compound compound;
        builder.MakeCompound(compound);
        for (auto& shape : myShapes) {
            if (shape.IsNull()) {
                continue;
            }
            hasShape = true;
            builder.Add(compound, shape);
        }
        Shape.setValue(compound);
    }

    if (!hasShape) {
        return new App::DocumentObjectExecReturn("no output shape");
    }

    return DocumentObject::StdReturn;
}

const std::vector<TopoDS_Shape>& FeatureArea::getShapes()
{
    getArea();
    return myShapes;
}

short FeatureArea::mustExecute() const
{
    if (myInited && !myArea.isBuilt()) {
        return 1;
    }
    return Part::Feature::mustExecute();
}

PyObject* FeatureArea::getPyObject()
{
    if (PythonObject.is(Py::_None())) {
        // ref counter is set to 1
        PythonObject = Py::Object(new FeatureAreaPy(this), true);
    }
    return Py::new_reference_to(PythonObject);
}


// FeatureAreaView -------------------------------------------------------------
//
PROPERTY_SOURCE(Path::FeatureAreaView, Part::Feature)

FeatureAreaView::FeatureAreaView()
{
    ADD_PROPERTY(Source, (nullptr));
    ADD_PROPERTY_TYPE(
        SectionIndex,
        (0),
        "Section",
        App::Prop_None,
        "The start index of the section to show, negative value for reverse index from bottom"
    );
    ADD_PROPERTY_TYPE(
        SectionCount,
        (1),
        "Section",
        App::Prop_None,
        "Number of sections to show, 0 to show all section starting from SectionIndex"
    );
}

std::list<TopoDS_Shape> FeatureAreaView::getShapes()
{
    std::list<TopoDS_Shape> shapes;
    App::DocumentObject* pObj = Source.getValue();
    if (!pObj) {
        return shapes;
    }
    if (!pObj->isDerivedFrom<FeatureArea>()) {
        return shapes;
    }

    auto all_shapes = static_cast<FeatureArea*>(pObj)->getShapes();

    if (all_shapes.empty()) {
        return shapes;
    }

    int index = SectionIndex.getValue(), count = SectionCount.getValue();
    if (index < 0) {
        index += ((int)all_shapes.size());
        if (index < 0) {
            return shapes;
        }
        if (count <= 0 || index + 1 - count < 0) {
            count = index + 1;
            index = 0;
        }
        else {
            index -= count - 1;
        }
    }
    else if (index >= (int)all_shapes.size()) {
        return shapes;
    }

    if (count <= 0) {
        count = all_shapes.size();
    }
    count += index;
    if (count > (int)all_shapes.size()) {
        count = all_shapes.size();
    }
    for (int i = index; i < count; ++i) {
        shapes.push_back(all_shapes[i]);
    }
    return shapes;
}

App::DocumentObjectExecReturn* FeatureAreaView::execute()
{
    App::DocumentObject* pObj = Source.getValue();
    if (!pObj) {
        return new App::DocumentObjectExecReturn("No shape linked");
    }

    if (!pObj->isDerivedFrom<FeatureArea>()) {
        return new App::DocumentObjectExecReturn("Linked object is not a FeatureArea");
    }

    bool hasShape = false;
    std::list<TopoDS_Shape> shapes = getShapes();
    if (shapes.empty()) {
        Shape.setValue(TopoDS_Shape());
    }
    else {
        BRep_Builder builder;
        TopoDS_Compound compound;
        builder.MakeCompound(compound);
        for (auto& shape : shapes) {
            if (shape.IsNull()) {
                continue;
            }
            hasShape = true;
            builder.Add(compound, shape);
        }
        Shape.setValue(compound);
    }

    if (!hasShape) {
        return new App::DocumentObjectExecReturn("no output shape");
    }

    return DocumentObject::StdReturn;
}

// Python feature ---------------------------------------------------------

namespace App
{
/// @cond DOXERR
PROPERTY_SOURCE_TEMPLATE(Path::FeatureAreaPython, Path::FeatureArea)
PROPERTY_SOURCE_TEMPLATE(Path::FeatureAreaViewPython, Path::FeatureAreaView)

template<>
const char* Path::FeatureAreaPython::getViewProviderName() const
{
    return "PathGui::ViewProviderAreaPython";
}
template<>
const char* Path::FeatureAreaViewPython::getViewProviderName() const
{
    return "PathGui::ViewProviderAreaViewPython";
}
/// @endcond

// explicit template instantiation
template class PathExport FeaturePythonT<Path::FeatureArea>;
}  // namespace App
