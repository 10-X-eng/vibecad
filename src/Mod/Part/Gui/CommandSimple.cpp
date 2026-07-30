// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2002 Jürgen Riegel <juergen.riegel@web.de>              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


#include <algorithm>
#include <set>
#include <sstream>

#include <QInputDialog>

#include <TopAbs_ShapeEnum.hxx>
#include <TopoDS_Shape.hxx>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObserver.h>
#include <App/ComplexGeoData.h>
#include <App/PropertyGeo.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Control.h>
#include <Gui/CommandT.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/WaitCursor.h>

#include "DlgPartCylinderImp.h"
#include "ModelingSelection.h"
#include "ShapeFromMesh.h"
#include "TaskResultValidation.h"
#include <Mod/Part/App/PartFeatures.h>
#include <Mod/Part/App/PartFeature.h>

namespace
{

bool hasMeshFacets(const App::DocumentObject* object)
{
    const auto* property = object ? object->getPropertyByName("Mesh") : nullptr;
    const auto* geometryProperty = property && property->isDerivedFrom<App::PropertyComplexGeoData>()
        ? static_cast<const App::PropertyComplexGeoData*>(property)
        : nullptr;
    const Data::ComplexGeoData* geometry =
        geometryProperty
            ? geometryProperty->getComplexData()
            : nullptr;
    if (!geometry) {
        return false;
    }

    std::vector<Base::Vector3d> points;
    std::vector<Data::ComplexGeoData::Facet> facets;
    geometry->getFaces(points, facets, 0.0);
    return !facets.empty();
}

}  // namespace


//===========================================================================
// Part_SimpleCylinder
//===========================================================================
DEF_STD_CMD_A(CmdPartSimpleCylinder)

CmdPartSimpleCylinder::CmdPartSimpleCylinder()
    : Command("Part_SimpleCylinder")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Cylinder");
    sToolTipText = QT_TR_NOOP("Creates a solid cylinder");
    sWhatsThis = "Part_SimpleCylinder";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Cylinder_Parametric";
}

void CmdPartSimpleCylinder::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    PartGui::DlgPartCylinderImp dlg(Gui::getMainWindow());
    if (dlg.exec() == QDialog::Accepted) {
        Base::Vector3d dir = dlg.getDirection();
        Base::Vector3d pos = dlg.getPosition();
        openCommand(QT_TRANSLATE_NOOP("Command", "Create Cylinder"));
        doCommand(Doc, "from FreeCAD import Base");
        doCommand(Doc, "import Part");
        doCommand(
            Doc,
            "App.ActiveDocument.addObject(\"Part::Feature\",\"Cylinder\")"
            ".Shape=Part.makeCylinder(%f,%f,"
            "Base.Vector(%f,%f,%f),"
            "Base.Vector(%f,%f,%f))",
            dlg.getRadius(),
            dlg.getLength(),
            pos.x,
            pos.y,
            pos.z,
            dir.x,
            dir.y,
            dir.z
        );
        commitCommand();
        updateActive();
        doCommand(Gui, "Gui.SendMsgToActiveView(\"ViewFit\")");
    }
}

bool CmdPartSimpleCylinder::isActive()
{
    if (getActiveGuiDocument()) {
        return true;
    }
    else {
        return false;
    }
}


//===========================================================================
// Part_ShapeFromMesh
//===========================================================================
DEF_STD_CMD_A(CmdPartShapeFromMesh)

CmdPartShapeFromMesh::CmdPartShapeFromMesh()
    : Command("Part_ShapeFromMesh")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Shape From Mesh");
    sToolTipText = QT_TR_NOOP("Creates a shape from the selected mesh");
    sWhatsThis = "Part_ShapeFromMesh";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Shape_from_Mesh";
}

void CmdPartShapeFromMesh::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    PartGui::ShapeFromMesh dlg(Gui::getMainWindow());
    dlg.exec();
}

bool CmdPartShapeFromMesh::isActive()
{
    App::Document* document =
        App::GetApplication().getActiveDocument();
    if (!document
        || Gui::Control().activeDialog()
        || !PartGui::canStartRetainedModelingTask(document)) {
        return false;
    }
    Base::Type meshType = Base::Type::fromName("Mesh::Feature");
    auto meshes = Gui::Selection().getObjectsOfType(meshType);
    return !meshes.empty()
        && std::ranges::all_of(
            meshes,
            [document](const App::DocumentObject* mesh) {
                return mesh
                    && mesh->getDocument() == document
                    && PartGui::isModelingObjectActive(mesh)
                    && hasMeshFacets(mesh);
            }
        );
}
//===========================================================================
// Part_PointsFromMesh
//===========================================================================
DEF_STD_CMD_A(CmdPartPointsFromMesh)

CmdPartPointsFromMesh::CmdPartPointsFromMesh()
    : Command("Part_PointsFromMesh")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Points From Shape");
    sToolTipText = QT_TR_NOOP("Creates distributed points from the selected shape");
    sWhatsThis = "Part_PointsFromMesh";
    sStatusTip = sToolTipText;
    sPixmap = "Part_PointsFromMesh";
}

void CmdPartPointsFromMesh::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* activeDocument = App::GetApplication().getActiveDocument();
    if (!activeDocument) {
        return;
    }

    auto getDefaultDistance = [](App::DocumentObject* geometry) {
        auto shape = Part::Feature::getTopoShape(
            geometry,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
        );
        auto bbox = shape.getBoundBox();
        int steps {20};
        return bbox.CalcDiagonalLength() / steps;
    };

    std::vector<App::DocumentObject*> geoms;
    for (auto& selected :
         PartGui::getModelingShapeSelection(activeDocument->getName())) {
        if (auto* object = selected.getObject();
            object && std::ranges::find(geoms, object) == geoms.end()) {
            geoms.push_back(object);
        }
    }
    if (geoms.empty()) {
        return;
    }

    double distance {1.0};
    if (!geoms.empty()) {
        double defaultDistance = getDefaultDistance(geoms.front());

        double STD_OCC_TOLERANCE = 1e-6;

        int decimals = Base::UnitsApi::getDecimals();
        double tolerance_from_decimals = pow(10., -decimals);

        double minimal_tolerance = tolerance_from_decimals < STD_OCC_TOLERANCE
            ? STD_OCC_TOLERANCE
            : tolerance_from_decimals;

        bool ok;
        distance = QInputDialog::getDouble(
            Gui::getMainWindow(),
            QObject::tr("Distance in Parameter Space"),
            QObject::tr("Enter distance:"),
            defaultDistance,
            minimal_tolerance,
            10.0 * defaultDistance,
            decimals,
            &ok,
            Qt::MSWindowsFixedSizeDialogHint
        );
        if (!ok) {
            return;
        }
    }

    Gui::WaitCursor wc;
    Base::PyGILStateLocker lock;
    try {
        PartGui::ModelingTaskAttempt attempt(
            *activeDocument,
            QT_TRANSLATE_NOOP("Command", "Points from geometry")
        );
        PyObject* module = PyImport_ImportModule("BasicShapes.Utils");
        if (!module) {
            throw Py::Exception();
        }
        Py::Module utils(module, true);
        std::vector<App::DocumentObject*> commandResults;

        for (auto it : geoms) {
            Py::Tuple args(2);
            args.setItem(0, Py::asObject(it->getPyObject()));
            args.setItem(1, Py::Float(distance));
            auto* result = PartGui::TaskResultValidation::
                requireExactPartResult(
                    *activeDocument,
                    utils.callMemberFunction(
                        "showCompoundFromPoints",
                        args
                    )
                );
            attempt.trackCreatedObject(*result);
            commandResults.push_back(result);
            PartGui::prepareModelingResultsForOperands({result}, {it});
        }
        if (commandResults.size() != geoms.size()) {
            throw Base::RuntimeError(
                "Points from geometry did not create every selected result"
            );
        }
        attempt.commit();
    }
    catch (Py::Exception&) {
        Base::PyException e;
        e.reportException();
        return;
    }
    catch (Base::Exception& e) {
        e.reportException();
        return;
    }
    catch (...) {
        throw;
    }
}

bool CmdPartPointsFromMesh::isActive()
{
    auto* document = App::GetApplication().getActiveDocument();
    return document && !Gui::Control().activeDialog()
        && PartGui::canStartRetainedModelingTask(document)
        && !PartGui::getModelingShapeSelection(
                document->getName()
            )
                .empty();
}

//===========================================================================
// Part_SimpleCopy
//===========================================================================
DEF_STD_CMD_A(CmdPartSimpleCopy)

CmdPartSimpleCopy::CmdPartSimpleCopy()
    : Command("Part_SimpleCopy")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Simple Copy");
    sToolTipText = QT_TR_NOOP("Creates a simple non-parametric copy of the selected shapes");
    sWhatsThis = "Part_SimpleCopy";
    sStatusTip = sToolTipText;
    sPixmap = "Part_3D_object";
}

static void _copyShape(
    const char* cmdName,
    bool resolve,
    bool needElement = false,
    bool refine = false,
    bool replacePresentation = false
)
{
    Gui::WaitCursor wc;
    auto* activeDocument = App::GetApplication().getActiveDocument();
    if (!activeDocument) {
        return;
    }

    int tid = Gui::Command::openActiveDocumentCommand(cmdName);
    std::vector<App::DocumentObject*> commandResults;
    std::vector<App::DocumentObject*> replacedPresentations;
    try {
        for (auto& sel : PartGui::getModelingSelection(activeDocument->getName())) {
            const auto resultCountBeforeSelection = commandResults.size();
            std::map<std::string, App::DocumentObject*> subMap;
            auto obj = sel.getObject();
            if (!obj) {
                throw Base::ValueError("A selected copy source is no longer available");
            }
            if (needElement && !sel.hasSubNames()) {
                throw Base::ValueError("Shape Element Copy requires a selected shape element");
            }
            if (resolve || !sel.hasSubNames()) {
                subMap.emplace("", obj);
            }
            else {
                for (const auto& sub : sel.getSubNames()) {
                    const char* element = nullptr;
                    auto sobj = obj->resolve(sub.c_str(), nullptr, nullptr, &element);
                    if (!sobj) {
                        throw Base::ValueError("A selected copy element is no longer available");
                    }
                    if (!needElement && element) {
                        subMap.emplace(sub.substr(0, element - sub.c_str()), sobj);
                    }
                    else {
                        subMap.emplace(sub, sobj);
                    }
                }
                if (subMap.empty()) {
                    throw Base::ValueError("The selected copy source has no usable shape");
                }
            }
            auto parentName = Gui::Command::getObjectCmd(obj);
            for (auto& v : subMap) {
                std::ostringstream factory;
                factory << "(lambda result, shape: ("
                           "setattr(result, 'Shape', shape), "
                           "setattr(result, 'Label', "
                        << Gui::Command::getObjectCmd(v.second)
                        << ".Label), result)[2])("
                           "App.ActiveDocument.addObject('Part::Feature','"
                        << v.second->getNameInDocument() << "'), Part.getShape(" << parentName
                        << ",'" << v.first << "',needSubElement=" << (needElement ? "True" : "False")
                        << ",refine=" << (refine ? "True" : "False") << ")"
                        << (needElement ? ".copy()" : "") << ")";
                auto* newObj = Gui::Command::runDocumentObjectCommand(
                    Gui::Command::Doc,
                    *activeDocument,
                    QByteArray::fromStdString(factory.str()),
                    Part::Feature::getClassTypeId()
                );
                PartGui::prepareModelingResultsForOperands({newObj}, {obj});
                commandResults.push_back(newObj);
                auto* presentation = PartGui::resolveModelingPresentationObject(obj);
                if (replacePresentation && presentation && presentation->Visibility.getValue()) {
                    if (std::ranges::find(replacedPresentations, presentation)
                        == replacedPresentations.end()) {
                        replacedPresentations.push_back(presentation);
                    }
                }
                Gui::Command::copyVisual(newObj, "ShapeAppearance", v.second);
                Gui::Command::copyVisual(newObj, "LineColor", v.second);
                Gui::Command::copyVisual(newObj, "PointColor", v.second);
            }
            if (commandResults.size() == resultCountBeforeSelection) {
                throw Base::RuntimeError("Copy did not create a result for every selected source");
            }
        }
        PartGui::groupModelingCommandOutputs(commandResults);
        if (!commandResults.empty() && !replacedPresentations.empty()
            && PartGui::setModelingReplacedInputs(*commandResults.back(), replacedPresentations)) {
            for (auto* presentation : replacedPresentations) {
                Gui::cmdAppObjectHide(presentation);
            }
        }
        Gui::Command::commitCommand(tid);
    }
    catch (...) {
        Gui::Command::abortCommand(tid);
        throw;
    }
    Gui::Command::updateActive();
}

static bool hasSelectedShapeElement()
{
    auto* document = App::GetApplication().getActiveDocument();
    if (!document) {
        return false;
    }
    const auto selection = PartGui::getModelingShapeSelection(
        document->getName()
    );
    return !selection.empty()
        && std::ranges::all_of(
            selection,
            [](const Gui::SelectionObject& selected) {
                const auto& subNames = selected.getSubNames();
                return !subNames.empty()
                    && std::ranges::all_of(
                        subNames,
                        [&selected](const std::string& subName) {
                            return !Part::Feature::getTopoShape(
                                        selected.getObject(),
                                        Part::ShapeOption::
                                                NeedSubElement
                                            | Part::ShapeOption::
                                                  ResolveLink
                                            | Part::ShapeOption::
                                                  Transform,
                                        subName.c_str()
                                    )
                                        .isNull();
                        }
                    );
            }
        );
}

void CmdPartSimpleCopy::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    _copyShape("Simple copy", true);
}

bool CmdPartSimpleCopy::isActive()
{
    auto* document = App::GetApplication().getActiveDocument();
    return document && !Gui::Control().activeDialog()
        && PartGui::canStartRetainedModelingTask(document)
        && !PartGui::getModelingShapeSelection(
                document->getName()
            )
                .empty();
}

//===========================================================================
// Part_TransformedCopy
//===========================================================================
DEF_STD_CMD_A(CmdPartTransformedCopy)

CmdPartTransformedCopy::CmdPartTransformedCopy()
    : Command("Part_TransformedCopy")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Transformed Copy");
    sToolTipText = QT_TR_NOOP(
        "Creates a non-parametric copy with transformed placement of the selected shapes"
    );
    sWhatsThis = "Part_TransformCopy";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Transformed_Copy.svg";
}

void CmdPartTransformedCopy::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    _copyShape("Transformed copy", false);
}

bool CmdPartTransformedCopy::isActive()
{
    auto* document = App::GetApplication().getActiveDocument();
    return document && !Gui::Control().activeDialog()
        && PartGui::canStartRetainedModelingTask(document)
        && !PartGui::getModelingShapeSelection(
                document->getName()
            )
                .empty();
}

//===========================================================================
// Part_ElementCopy
//===========================================================================
DEF_STD_CMD_A(CmdPartElementCopy)

CmdPartElementCopy::CmdPartElementCopy()
    : Command("Part_ElementCopy")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Shape Element Copy");
    sToolTipText = QT_TR_NOOP("Creates a non-parametric copy of the selected shape element");
    sWhatsThis = "Part_ElementCopy";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Element_Copy.svg";
}

void CmdPartElementCopy::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    _copyShape("Element copy", false, true);
}

bool CmdPartElementCopy::isActive()
{
    auto* document = App::GetApplication().getActiveDocument();
    return document && !Gui::Control().activeDialog()
        && PartGui::canStartRetainedModelingTask(document)
        && hasSelectedShapeElement();
}

//===========================================================================
// Part_RefineShape
//===========================================================================
DEF_STD_CMD_A(CmdPartRefineShape)

CmdPartRefineShape::CmdPartRefineShape()
    : Command("Part_RefineShape")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Refine Shape");
    sToolTipText = QT_TR_NOOP("Creates a refined copy of the selected shapes");
    sWhatsThis = "Part_RefineShape";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Refine_Shape";
}

void CmdPartRefineShape::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Part"
    );
    bool parametric = hGrp->GetBool("ParametricRefine", true);
    if (parametric) {
        Gui::WaitCursor wc;
        auto* activeDocument = App::GetApplication().getActiveDocument();
        if (!activeDocument) {
            return;
        }
        std::vector<App::DocumentObject*> objs;
        for (auto& selected : PartGui::getModelingShapeSelection(activeDocument->getName())) {
            if (auto* object = selected.getObject();
                object && std::ranges::find(objs, object) == objs.end()) {
                objs.push_back(object);
            }
        }
        if (objs.empty()) {
            return;
        }
        openCommand(QT_TRANSLATE_NOOP("Command", "Refine shape"));
        std::vector<App::DocumentObject*> commandResults;
        std::vector<App::DocumentObject*> replacedPresentations;
        try {
            for (auto* obj : objs) {
                auto* presentation = PartGui::resolveModelingPresentationObject(obj);
                const bool replacesPresentation = presentation && presentation->Visibility.getValue();
                App::DocumentObjectT objT(obj);
                const QString factory
                    = QStringLiteral("%1.addObject("
                                     "'Part::Refine','%2')")
                          .arg(
                              QString::fromStdString(
                                  App::DocumentT(obj->getDocument()).getDocumentPython()
                              ),
                              QString::fromLatin1(obj->getNameInDocument())
                          );
                auto* newObj = Gui::Command::runDocumentObjectCommand(
                    Gui::Command::Doc,
                    *activeDocument,
                    factory.toUtf8(),
                    Part::Refine::getClassTypeId()
                );
                FCMD_OBJ_CMD(newObj, "Source = " << objT.getObjectPython());
                FCMD_OBJ_CMD(newObj, "Label = " << objT.getObjectPython() << ".Label");
                if (replacesPresentation) {
                    if (std::ranges::find(replacedPresentations, presentation)
                        == replacedPresentations.end()) {
                        replacedPresentations.push_back(presentation);
                    }
                }
                commandResults.push_back(newObj);
                Gui::copyVisualT(newObj, "ShapeAppearance", obj);
                Gui::copyVisualT(newObj, "LineColor", obj);
                Gui::copyVisualT(newObj, "PointColor", obj);
            }
            if (commandResults.size() != objs.size()) {
                throw Base::RuntimeError("Refine did not create every selected result");
            }
            PartGui::groupModelingCommandOutputs(commandResults);
            if (!commandResults.empty() && !replacedPresentations.empty()
                && PartGui::setModelingReplacedInputs(*commandResults.back(), replacedPresentations)) {
                for (auto* presentation : replacedPresentations) {
                    Gui::cmdAppObjectHide(presentation);
                }
            }
            commitCommand();
        }
        catch (Base::Exception& e) {
            abortCommand();
            e.reportException();
            return;
        }
        catch (...) {
            abortCommand();
            throw;
        }
        updateActive();
    }
    else {
        _copyShape("Refined copy", true, false, true, true);
    }
}

bool CmdPartRefineShape::isActive()
{
    auto* document = App::GetApplication().getActiveDocument();
    return document && !Gui::Control().activeDialog()
        && PartGui::canStartRetainedModelingTask(document)
        && !PartGui::getModelingShapeSelection(document->getName()).empty();
}

//===========================================================================
// Part_Defeaturing
//===========================================================================
DEF_STD_CMD_A(CmdPartDefeaturing)

CmdPartDefeaturing::CmdPartDefeaturing()
    : Command("Part_Defeaturing")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Defeaturing");
    sToolTipText = QT_TR_NOOP("Removes the selected features from a shape");
    sWhatsThis = "Part_Defeaturing";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Defeaturing";
}

void CmdPartDefeaturing::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::WaitCursor wc;
    auto* activeDocument = App::GetApplication().getActiveDocument();
    if (!activeDocument) {
        return;
    }
    auto objs = PartGui::getModelingShapeSelection(
        activeDocument->getName()
    );
    if (objs.empty()) {
        return;
    }
    openCommand(QT_TRANSLATE_NOOP("Command", "Defeaturing"));
    std::vector<App::DocumentObject*> commandResults;
    std::vector<App::DocumentObject*> replacedPresentations;
    for (auto it = objs.begin(); it != objs.end(); ++it) {
        try {
            auto* presentation =
                PartGui::resolveModelingPresentationObject(
                    it->getObject()
                );
            const bool replacesPresentation =
                presentation
                && presentation->Visibility.getValue();
            const auto sourceShape = Part::Feature::getTopoShape(
                it->getObject(),
                Part::ShapeOption::ResolveLink
                    | Part::ShapeOption::Transform
            );
            if (sourceShape.isNull()) {
                throw Base::ValueError(
                    "Defeaturing requires a valid source shape"
                );
            }
            std::vector<TopoDS_Shape> selectedFaces;
            std::string faces;
            std::vector<std::string> subnames = it->getSubNames();
            for (const auto& subname : subnames) {
                auto face = sourceShape.getSubShape(
                    subname.c_str(),
                    true
                );
                if (face.IsNull() || face.ShapeType() != TopAbs_FACE) {
                    throw Base::ValueError(
                        "Defeaturing requires selected faces"
                    );
                }
                selectedFaces.push_back(face);
                faces.append(
                    Gui::Command::getObjectCmd(it->getObject())
                );
                faces.append(".Shape.");
                faces.append(subname);
                faces.append(",");
            }
            const TopoDS_Shape defeatedShape =
                sourceShape.defeaturing(selectedFaces);
            if (defeatedShape.IsNull()
                || sourceShape.getShape().IsPartner(defeatedShape)) {
                throw Base::RuntimeError("Defeaturing failed");
            }

            const std::string resultName =
                activeDocument->getUniqueObjectName("Defeatured");
            const QString factory = QStringLiteral(
                                        "App.getDocument('%1').addObject("
                                        "'Part::Feature','%2')"
                                    )
                                        .arg(
                                            QString::fromLatin1(
                                                activeDocument->getName()
                                            ),
                                            QString::fromStdString(
                                                resultName
                                            )
                                        );
            auto* result = Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                *activeDocument,
                factory.toUtf8(),
                Part::Feature::getClassTypeId()
            );
            FCMD_OBJ_CMD(
                result,
                "Shape = "
                    << Gui::Command::getObjectCmd(it->getObject())
                    << ".Shape.defeaturing([" << faces << "])"
            );
            PartGui::prepareModelingResultsForOperands(
                {result},
                {it->getObject()}
            );
            if (replacesPresentation) {
                if (std::ranges::find(
                        replacedPresentations,
                        presentation
                    )
                    == replacedPresentations.end()) {
                    replacedPresentations.push_back(presentation);
                }
            }
            commandResults.push_back(result);
        }
        catch (const Base::Exception& e) {
            Base::Console().warning(
                "%s: %s\n",
                it->getFeatName(),
                e.what()
            );
            abortCommand();
            return;
        }
        catch (...) {
            abortCommand();
            throw;
        }
    }
    try {
        updateDocument(activeDocument);
        for (auto* result : commandResults) {
            PartGui::TaskResultValidation::validatePartResult(result);
        }
        PartGui::groupModelingCommandOutputs(commandResults);
        if (!commandResults.empty()
            && !replacedPresentations.empty()
            && PartGui::setModelingReplacedInputs(
                *commandResults.back(),
                replacedPresentations
            )) {
            for (auto* presentation : replacedPresentations) {
                Gui::cmdAppObjectHide(presentation);
            }
        }
    }
    catch (Base::Exception& e) {
        abortCommand();
        e.reportException();
        return;
    }
    commitCommand();
    updateActive();
}

bool CmdPartDefeaturing::isActive()
{
    auto* document = App::GetApplication().getActiveDocument();
    if (!document || Gui::Control().activeDialog()
        || !PartGui::canStartRetainedModelingTask(document)) {
        return false;
    }
    const auto selection = PartGui::getModelingShapeSelection(
        document->getName()
    );
    return !selection.empty()
        && std::ranges::all_of(
            selection,
            [](const Gui::SelectionObject& selected) {
                const auto& subNames = selected.getSubNames();
                return !subNames.empty()
                    && std::ranges::all_of(
                        subNames,
                        [&selected](const std::string& subName) {
                            return subName.starts_with("Face")
                                && !Part::Feature::getTopoShape(
                                        selected.getObject(),
                                        Part::ShapeOption::
                                                NeedSubElement
                                            | Part::ShapeOption::
                                                  ResolveLink
                                            | Part::ShapeOption::
                                                  Transform,
                                        subName.c_str()
                                    )
                                        .isNull();
                        }
                    );
            }
        );
}


//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

void CreateSimplePartCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    rcCmdMgr.addCommand(new CmdPartSimpleCylinder());
    rcCmdMgr.addCommand(new CmdPartShapeFromMesh());
    rcCmdMgr.addCommand(new CmdPartPointsFromMesh());
    rcCmdMgr.addCommand(new CmdPartSimpleCopy());
    rcCmdMgr.addCommand(new CmdPartElementCopy());
    rcCmdMgr.addCommand(new CmdPartTransformedCopy());
    rcCmdMgr.addCommand(new CmdPartRefineShape());
    rcCmdMgr.addCommand(new CmdPartDefeaturing());
}
