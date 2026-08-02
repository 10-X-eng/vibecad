// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2006 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <Inventor/events/SoMouseButtonEvent.h>
#include <QInputDialog>
#include <QMessageBox>
#include <algorithm>
#include <iterator>
#include <ranges>
#include <unordered_set>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/FileDialog.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Gui/WaitCursor.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Mesh/Gui/ParametricMeshFilter.h>

#include "../App/PointsFeature.h"
#include "../App/Properties.h"
#include "../App/Structured.h"
#include "../App/Tools.h"

#include "DlgPointsReadImp.h"
#include "ViewProvider.h"


namespace
{

App::Document* cleanActivePointsDocument()
{
    auto* document = App::GetApplication().getActiveDocument();
    return MeshGui::canStartNativeMeshCommand(document) ? document : nullptr;
}

template<typename Object>
bool allObjectsBelongTo(const std::vector<Object*>& objects, const App::Document* document)
{
    return document && !objects.empty()
        && std::ranges::all_of(objects, [document](const Object* object) {
               return object && object->getDocument() == document
                   && MeshGui::isNativeMeshInputActive(object);
           });
}

std::unordered_set<long> objectIds(const App::Document& document)
{
    std::unordered_set<long> result;
    for (const auto* object : document.getObjects()) {
        if (object) {
            result.insert(object->getID());
        }
    }
    return result;
}

std::vector<App::DocumentObject*> createdObjects(
    App::Document& document,
    const std::unordered_set<long>& previousIds
)
{
    std::vector<App::DocumentObject*> result;
    for (auto* object : document.getObjects()) {
        if (object && !previousIds.contains(object->getID())
            && !object->isDerivedFrom<App::DocumentTimeline>()) {
            result.push_back(object);
        }
    }
    return result;
}

void addPointSourceDependency(App::DocumentObject& output, App::DocumentObject& source)
{
    auto* property = output.getPropertyByName("Source");
    if (!property) {
        property = output.addDynamicProperty(
            "App::PropertyLink",
            "Source",
            "Operation",
            "Point-cloud or geometry source used to create this result",
            App::Prop_ReadOnly,
            true,
            true
        );
    }
    auto* link = dynamic_cast<App::PropertyLink*>(property);
    if (!link) {
        throw Base::TypeError("The point operation Source property has an incompatible type");
    }
    link->setValue(&source);
}

void addPointSourceDependencies(
    App::DocumentObject& output,
    const std::vector<App::DocumentObject*>& sources
)
{
    auto* property = output.getPropertyByName("Sources");
    if (!property) {
        property = output.addDynamicProperty(
            "App::PropertyLinkList",
            "Sources",
            "Operation",
            "Point clouds combined by this operation",
            App::Prop_ReadOnly,
            true,
            true
        );
    }
    auto* links = dynamic_cast<App::PropertyLinkList*>(property);
    if (!links) {
        throw Base::TypeError("The point operation Sources property has an incompatible type");
    }
    links->setValues(sources);
}

void commitExactMutation(Gui::ExactTransaction& transaction)
{
    if (!transaction.commit()) {
        throw Base::RuntimeError("The point operation could not be committed");
    }
}

}  // namespace


//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

//===========================================================================
// CmdPointsImport
//===========================================================================
DEF_STD_CMD_A(CmdPointsImport)

CmdPointsImport::CmdPointsImport()
    : Command("Points_Import")
{
    sAppModule = "Points";
    sGroup = QT_TR_NOOP("Points");
    sMenuText = QT_TR_NOOP("Import Points…");
    sToolTipText = QT_TR_NOOP("Imports a point cloud");
    sWhatsThis = "Points_Import";
    sStatusTip = sToolTipText;
    sPixmap = "Points_Import_Point_cloud";
}

void CmdPointsImport::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    App::Document* launchDocument = cleanActivePointsDocument();
    if (!launchDocument) {
        return;
    }
    App::DocumentWeakPtrT targetDocument(launchDocument);

    const Gui::FileDialog::FilterList formatList {
        {QObject::tr("Point formats"), {"*.asc", "*.pcd", "*.ply", "*.e57"}},
        Gui::FileDialog::Filter::AllFiles(),
    };
    QString fn
        = Gui::FileDialog::getOpenFileName(Gui::getMainWindow(), QString(), QString(), formatList);
    if (fn.isEmpty()) {
        return;
    }

    App::Document* document = *targetDocument;
    if (!MeshGui::canStartNativeMeshCommand(document)) {
        return;
    }

    try {
        const std::string fnEscapedUtf8 = Base::Tools::escapeEncodeFilename(fn.toUtf8().constData());
        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Import points"));
        const auto previousIds = objectIds(*document);
        addModule(Command::App, "Points");
        doCommand(
            Command::Doc,
            "Points.insert(\"%s\", \"%s\")",
            fnEscapedUtf8.c_str(),
            document->getName()
        );

        auto created = createdObjects(*document, previousIds);
        std::vector<App::DocumentObject*> outputs;
        std::ranges::copy_if(created, std::back_inserter(outputs), [](const App::DocumentObject* object) {
            return object && object->isDerivedFrom<Points::Feature>();
        });
        if (outputs.size() != 1) {
            throw Base::RuntimeError("The selected file did not produce exactly one point cloud");
        }
        auto* pointCloud = static_cast<Points::Feature*>(outputs.front());
        if (pointCloud->Points.getValue().size() == 0) {
            throw Base::RuntimeError("The selected file produced an empty point cloud");
        }

        /** check if boundbox contains the origin, offer to move it to the origin if not
         *  addresses issue #5808 where an imported points cloud that was far from the
         *  origin had inaccuracies in the relative positioning of the points due to
         *  imprecise floating point variables used in COIN
         **/
        auto points = pointCloud->Points.getValue();
        auto bbox = points.getBoundBox();
        auto center = bbox.GetCenter();

        if (!bbox.IsInBox(Base::Vector3d(0, 0, 0))) {
            QMessageBox msgBox(Gui::getMainWindow());
            msgBox.setIcon(QMessageBox::Question);
            msgBox.setWindowTitle(QObject::tr("Points not at Origin"));
            msgBox.setText(
                QObject::tr(
                    "The bounding box of the imported points does not contain the origin. "
                    "Translate it to the origin?"
                )
            );
            msgBox.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
            msgBox.setDefaultButton(QMessageBox::Yes);
            auto ret = msgBox.exec();

            if (ret == QMessageBox::Yes) {
                Points::PointKernel* kernel = pointCloud->Points.startEditing();
                kernel->moveGeometry(-center);
                pointCloud->Points.finishEditing();
            }
        }

        MeshGui::createStandaloneOutputGroup(
            *document,
            outputs,
            {Base::FileInfo(fn.toUtf8().constData()).fileName()},
            "ImportedPoints",
            "Imported Points",
            "Import points"
        );
        document->recompute();
        commitExactMutation(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Import Points"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdPointsImport::isActive()
{
    return cleanActivePointsDocument() != nullptr;
}

DEF_STD_CMD_A(CmdPointsExport)

CmdPointsExport::CmdPointsExport()
    : Command("Points_Export")
{
    sAppModule = "Points";
    sGroup = QT_TR_NOOP("Points");
    sMenuText = QT_TR_NOOP("Export Points…");
    sToolTipText = QT_TR_NOOP("Exports a point cloud");
    sWhatsThis = "Points_Export";
    sStatusTip = QT_TR_NOOP("Exports a point cloud");
    sPixmap = "Points_Export_Point_cloud";
    eType = 0;
}

void CmdPointsExport::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    auto* document = App::GetApplication().getActiveDocument();
    auto points = getSelection().getObjectsOfType<Points::Feature>();
    if (!allObjectsBelongTo(points, document)) {
        return;
    }
    std::vector<App::DocumentObjectWeakPtrT> targets;
    targets.reserve(points.size());
    for (auto* point : points) {
        targets.emplace_back(point);
    }

    addModule(Command::App, "Points");
    for (const auto& target : targets) {
        const Gui::FileDialog::FilterList formatList {
            {QObject::tr("Point formats"), {"*.asc", "*.pcd", "*.ply"}},
            Gui::FileDialog::Filter::AllFiles(),
        };
        QString fn
            = Gui::FileDialog::getSaveFileName(Gui::getMainWindow(), QString(), QString(), formatList);
        if (fn.isEmpty()) {
            break;
        }

        auto* point = target.get<Points::Feature>();
        if (!point || point->getDocument() != document || !MeshGui::isNativeMeshInputActive(point)
            || point->Points.getValue().size() == 0) {
            return;
        }
        const std::string fnEscapedUtf8 = Base::Tools::escapeEncodeFilename(fn.toUtf8().constData());
        const App::DocumentObjectT pointIdentity(point);
        const std::string objectPython = pointIdentity.getObjectPython();
        doCommand(
            Command::Doc,
            "Points.export([%s], \"%s\")",
            objectPython.c_str(),
            fnEscapedUtf8.c_str()
        );
    }
}

bool CmdPointsExport::isActive()
{
    auto* document = App::GetApplication().getActiveDocument();
    const auto points = getSelection().getObjectsOfType<Points::Feature>();
    return allObjectsBelongTo(points, document)
        && std::ranges::all_of(points, [](const Points::Feature* point) {
               return point->Points.getValue().size() > 0;
           });
}

DEF_STD_CMD_A(CmdPointsConvert)

CmdPointsConvert::CmdPointsConvert()
    : Command("Points_Convert")
{
    sAppModule = "Points";
    sGroup = QT_TR_NOOP("Points");
    sMenuText = QT_TR_NOOP("Convert to Points");
    sToolTipText = QT_TR_NOOP("Converts to points");
    sWhatsThis = "Points_Convert";
    sStatusTip = sToolTipText;
    sPixmap = "Points_Convert";
}

void CmdPointsConvert::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    constexpr double standardTolerance = 1e-6;
    auto* launchDocument = cleanActivePointsDocument();
    auto selected = getSelection().getObjectsOfType<App::GeoFeature>();
    if (!allObjectsBelongTo(selected, launchDocument)) {
        return;
    }
    App::DocumentWeakPtrT targetDocument(launchDocument);
    std::vector<App::DocumentObjectWeakPtrT> targetObjects;
    targetObjects.reserve(selected.size());
    for (auto* object : selected) {
        targetObjects.emplace_back(object);
    }

    int decimals = Base::UnitsApi::getDecimals();
    double tolerance_from_decimals = pow(10., -decimals);

    double minimal_tolerance = tolerance_from_decimals < standardTolerance ? standardTolerance
                                                                           : tolerance_from_decimals;

    bool ok;
    double tol = QInputDialog::getDouble(
        Gui::getMainWindow(),
        QObject::tr("Distance"),
        QObject::tr("Enter maximum distance:"),
        0.1,
        minimal_tolerance,
        10.0,
        decimals,
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok) {
        return;
    }

    auto* document = *targetDocument;
    if (!MeshGui::canStartNativeMeshCommand(document)) {
        return;
    }
    std::vector<App::GeoFeature*> sources;
    sources.reserve(targetObjects.size());
    for (const auto& target : targetObjects) {
        auto* source = target.get<App::GeoFeature>();
        const auto* geometry = source ? source->getPropertyOfGeometry() : nullptr;
        if (!source || source->getDocument() != document || !MeshGui::isNativeMeshInputActive(source)
            || !geometry || !geometry->getComplexData()) {
            return;
        }
        sources.push_back(source);
    }

    auto run_python = [](const std::vector<App::GeoFeature*>& geoObject, double tol) -> bool {
        Py::List list;
        for (auto it : geoObject) {
            const App::PropertyComplexGeoData* prop = it->getPropertyOfGeometry();
            if (prop) {
                list.append(Py::asObject(it->getPyObject()));
            }
        }

        if (list.size() > 0) {
            PyObject* module = PyImport_ImportModule("pointscommands.commands");
            if (!module) {
                throw Py::Exception();
            }

            Py::Module commands(module, true);
            commands.callMemberFunction("make_points_from_geometry", Py::TupleN(list, Py::Float(tol)));
            return true;
        }

        return false;
    };

    try {
        Gui::WaitCursor wc;
        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Convert to points"));
        std::vector<App::DocumentObject*> outputs;
        outputs.reserve(sources.size());
        Base::PyGILStateLocker lock;
        for (auto* source : sources) {
            const auto previousIds = objectIds(*document);
            if (!run_python({source}, tol)) {
                throw Base::RuntimeError("The selected geometry did not provide point data");
            }
            auto created = createdObjects(*document, previousIds);
            std::vector<App::DocumentObject*> sourceOutputs;
            std::ranges::copy_if(
                created,
                std::back_inserter(sourceOutputs),
                [](const App::DocumentObject* object) {
                    return object && object->isDerivedFrom<Points::Feature>();
                }
            );
            if (sourceOutputs.size() != 1) {
                throw Base::RuntimeError("Geometry conversion did not produce exactly one point cloud");
            }
            auto* output = static_cast<Points::Feature*>(sourceOutputs.front());
            if (output->Points.getValue().size() == 0) {
                throw Base::RuntimeError("Geometry conversion produced an empty point cloud");
            }
            addPointSourceDependency(*output, *source);
            outputs.push_back(output);
        }

        std::vector<App::DocumentObject*> sourceObjects(sources.begin(), sources.end());
        MeshGui::createSourcePreservingOutputGroup(
            *document,
            sourceObjects,
            outputs,
            "PointsFromGeometry",
            "Points From Geometry",
            "Convert geometry to points"
        );
        document->recompute();
        commitExactMutation(mutation);
        updateActive();
    }
    catch (const Py::Exception&) {
        Base::PyException e;
        e.reportException();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Convert to Points"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdPointsConvert::isActive()
{
    auto* document = cleanActivePointsDocument();
    const auto objects = getSelection().getObjectsOfType<App::GeoFeature>();
    return allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const App::GeoFeature* object) {
               const auto* geometry = object ? object->getPropertyOfGeometry() : nullptr;
               return geometry && geometry->getComplexData();
           });
}

DEF_STD_CMD_A(CmdPointsPolyCut)

CmdPointsPolyCut::CmdPointsPolyCut()
    : Command("Points_PolyCut")
{
    sAppModule = "Points";
    sGroup = QT_TR_NOOP("Points");
    sMenuText = QT_TR_NOOP("Cut Point Cloud");
    sToolTipText = QT_TR_NOOP("Cuts a point cloud with a selected polygon");
    sWhatsThis = "Points_PolyCut";
    sStatusTip = sToolTipText;
    sPixmap = "PolygonPick";
}

void CmdPointsPolyCut::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    auto* document = cleanActivePointsDocument();
    auto points = Gui::Selection().getObjectsOfType<Points::Feature>();
    if (!allObjectsBelongTo(points, document)
        || std::ranges::any_of(points, [](const Points::Feature* point) {
               return point->Points.getValue().size() == 0 || !point->Visibility.getValue();
           })) {
        return;
    }
    auto* guiDocument = Gui::Application::Instance->getDocument(document);
    auto* view = guiDocument ? dynamic_cast<Gui::View3DInventor*>(guiDocument->getActiveView())
                             : nullptr;
    if (!view || view->getViewer()->isEditing()) {
        return;
    }

    std::vector<Gui::ViewProvider*> viewProviders;
    viewProviders.reserve(points.size());
    for (auto* point : points) {
        Gui::ViewProvider* viewProvider = guiDocument->getViewProvider(point);
        if (!viewProvider || !viewProvider->isVisible()) {
            return;
        }
        viewProviders.push_back(viewProvider);
    }

    Gui::View3DInventorViewer* viewer = view->getViewer();
    viewer->setEditing(true);
    viewer->startSelection(Gui::View3DInventorViewer::Lasso);
    viewer->addEventCallback(
        SoMouseButtonEvent::getClassTypeId(),
        PointsGui::ViewProviderPoints::clipPointsCallback
    );
    for (auto* viewProvider : viewProviders) {
        viewProvider->startEditing(Gui::ViewProvider::Cutting);
    }
}

bool CmdPointsPolyCut::isActive()
{
    auto* document = cleanActivePointsDocument();
    const auto points = getSelection().getObjectsOfType<Points::Feature>();
    if (!allObjectsBelongTo(points, document)
        || std::ranges::any_of(points, [](const Points::Feature* point) {
               return point->Points.getValue().size() == 0 || !point->Visibility.getValue();
           })) {
        return false;
    }
    auto* guiDocument = Gui::Application::Instance->getDocument(document);
    auto* view = guiDocument ? dynamic_cast<Gui::View3DInventor*>(guiDocument->getActiveView())
                             : nullptr;
    return view && !view->getViewer()->isEditing();
}

DEF_STD_CMD_A(CmdPointsMerge)

CmdPointsMerge::CmdPointsMerge()
    : Command("Points_Merge")
{
    sAppModule = "Points";
    sGroup = QT_TR_NOOP("Points");
    sMenuText = QT_TR_NOOP("Merge Point Clouds");
    sToolTipText = QT_TR_NOOP("Merges several point clouds into one");
    sWhatsThis = "Points_Merge";
    sStatusTip = sToolTipText;
    sPixmap = "Points_Merge";
}

void CmdPointsMerge::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    auto* document = cleanActivePointsDocument();
    auto points = Gui::Selection().getObjectsOfType<Points::Feature>();
    if (points.size() < 2 || !allObjectsBelongTo(points, document)
        || std::ranges::any_of(points, [](const Points::Feature* point) {
               return point->Points.getValue().size() == 0;
           })) {
        return;
    }

    try {
        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Merge point clouds"));
        auto* result = document->addObject<Points::Feature>("MergedPoints");
        result->Label.setValue("Merged Points");
        Points::PointKernel* kernel = result->Points.startEditing();

        std::vector<App::DocumentObject*> sourceObjects;
        sourceObjects.reserve(points.size());
        for (auto* point : points) {
            sourceObjects.push_back(point);
            const Points::PointKernel& sourcePoints = point->Points.getValue();
            const std::size_t offset = kernel->size();
            kernel->resize(offset + sourcePoints.size());
            for (std::size_t index = 0; index < sourcePoints.size(); ++index) {
                kernel->setPoint(index + offset, sourcePoints.getPoint(index));
            }
        }

        result->Points.finishEditing();
        addPointSourceDependencies(*result, sourceObjects);

        std::string displayMode = "Points";
        if (Points::copyProperty<App::PropertyColorList>(result, sourceObjects, "Color")) {
            displayMode = "Color";
        }
        if (Points::copyProperty<Points::PropertyNormalList>(result, sourceObjects, "Normal")) {
            displayMode = "Shaded";
        }
        if (Points::copyProperty<Points::PropertyGreyValueList>(result, sourceObjects, "Intensity")) {
            displayMode = "Intensity";
        }

        if (auto* viewProvider = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                Gui::Application::Instance->getViewProvider(result)
            )) {
            viewProvider->DisplayMode.setValue(displayMode.c_str());
        }

        MeshGui::createSourcePreservingOutputGroup(
            *document,
            sourceObjects,
            {result},
            "MergedPoints",
            "Merged Points",
            "Merge point clouds"
        );
        document->recompute();
        commitExactMutation(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Merge Point Clouds"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdPointsMerge::isActive()
{
    auto* document = cleanActivePointsDocument();
    const auto points = getSelection().getObjectsOfType<Points::Feature>();
    return points.size() > 1 && allObjectsBelongTo(points, document)
        && std::ranges::all_of(points, [](const Points::Feature* point) {
               return point->Points.getValue().size() > 0;
           });
}

DEF_STD_CMD_A(CmdPointsStructure)

CmdPointsStructure::CmdPointsStructure()
    : Command("Points_Structure")
{
    sAppModule = "Points";
    sGroup = QT_TR_NOOP("Points");
    sMenuText = QT_TR_NOOP("Structured Point Cloud");
    sToolTipText = QT_TR_NOOP("Converts points to a structured point cloud");
    sWhatsThis = "Points_Structure";
    sStatusTip = sToolTipText;
    sPixmap = "Points_Structure";
}

void CmdPointsStructure::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    auto* document = cleanActivePointsDocument();
    auto points = Gui::Selection().getObjectsOfType<Points::Feature>();
    if (points.size() != 1 || !allObjectsBelongTo(points, document)
        || points.front()->Points.getValue().size() == 0) {
        return;
    }
    auto* input = points.front();

    try {
        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Structure point cloud"));
        std::string name = input->Label.getValue();
        name += " (Structured)";
        Points::Structured* output = document->addObject<Points::Structured>("StructuredPoints");
        output->Label.setValue(name);
        addPointSourceDependency(*output, *input);

        // Already sorted, so just make a copy
        if (input->isDerivedFrom<Points::Structured>()) {
            auto* structuredInput = static_cast<Points::Structured*>(input);

            Points::PointKernel* kernel = output->Points.startEditing();
            const Points::PointKernel& sourcePoints = structuredInput->Points.getValue();

            kernel->resize(sourcePoints.size());
            for (std::size_t index = 0; index < sourcePoints.size(); ++index) {
                kernel->setPoint(index, sourcePoints.getPoint(index));
            }
            output->Points.finishEditing();
            output->Width.setValue(structuredInput->Width.getValue());
            output->Height.setValue(structuredInput->Height.getValue());
        }
        // Sort the points
        else {
            Points::PointKernel* kernel = output->Points.startEditing();
            const Points::PointKernel& sourcePoints = input->Points.getValue();

            Base::BoundBox3d bbox = input->Points.getBoundingBox();
            double width = bbox.LengthX();
            double height = bbox.LengthY();

            // Count the number of different x or y values to get the size
            std::set<double> countX, countY;
            for (std::size_t index = 0; index < sourcePoints.size(); ++index) {
                Base::Vector3d pnt = sourcePoints.getPoint(index);
                countX.insert(pnt.x);
                countY.insert(pnt.y);
            }

            const long pointColumns = static_cast<long>(countX.size());
            const long pointRows = static_cast<long>(countY.size());
            if (pointColumns < 2 || pointRows < 2 || width <= 0.0 || height <= 0.0) {
                throw Base::ValueError(
                    "A structured point cloud requires at least two distinct X and Y coordinates"
                );
            }

            const double dx = width / static_cast<double>(pointColumns - 1);
            const double dy = height / static_cast<double>(pointRows - 1);

            // Pre-fill the vector with <nan, nan, nan> points and afterwards replace them
            // with valid point coordinates
            double nan = std::numeric_limits<double>::quiet_NaN();
            std::vector<Base::Vector3d> sortedPoints(
                static_cast<std::size_t>(pointColumns * pointRows),
                Base::Vector3d(nan, nan, nan)
            );

            for (std::size_t index = 0; index < sourcePoints.size(); ++index) {
                Base::Vector3d pnt = sourcePoints.getPoint(index);
                double xi = (pnt.x - bbox.MinX) / dx;
                double yi = (pnt.y - bbox.MinY) / dy;

                double xx = std::fabs(xi - std::round(xi));
                double yy = std::fabs(yi - std::round(yi));
                if (xx < 0.01 && yy < 0.01) {
                    xi = std::round(xi);
                    yi = std::round(yi);
                    const long targetIndex = static_cast<long>(yi * pointColumns + xi);
                    if (targetIndex < 0 || targetIndex >= static_cast<long>(sortedPoints.size())) {
                        throw Base::ValueError("A point lies outside the inferred structured grid");
                    }
                    sortedPoints[static_cast<std::size_t>(targetIndex)] = pnt;
                }
            }

            kernel->resize(sortedPoints.size());
            for (std::size_t index = 0; index < sortedPoints.size(); index++) {
                kernel->setPoint(index, sortedPoints[index]);
            }

            output->Points.finishEditing();
            output->Width.setValue(pointColumns);
            output->Height.setValue(pointRows);
        }

        MeshGui::createSourcePreservingOutputGroup(
            *document,
            {input},
            {output},
            "StructuredPoints",
            "Structured Points",
            "Structure point cloud"
        );
        document->recompute();
        if (output->isError()
            || output->Points.getValue().size()
                != static_cast<std::size_t>(output->Width.getValue() * output->Height.getValue())) {
            throw Base::RuntimeError("The structured point cloud did not produce a valid grid");
        }
        commitExactMutation(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Structured Point Cloud"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdPointsStructure::isActive()
{
    auto* document = cleanActivePointsDocument();
    const auto points = getSelection().getObjectsOfType<Points::Feature>();
    return points.size() == 1 && allObjectsBelongTo(points, document)
        && points.front()->Points.getValue().size() > 0;
}

void CreatePointsCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    rcCmdMgr.addCommand(new CmdPointsImport());
    rcCmdMgr.addCommand(new CmdPointsExport());
    rcCmdMgr.addCommand(new CmdPointsConvert());
    rcCmdMgr.addCommand(new CmdPointsPolyCut());
    rcCmdMgr.addCommand(new CmdPointsMerge());
    rcCmdMgr.addCommand(new CmdPointsStructure());
}
