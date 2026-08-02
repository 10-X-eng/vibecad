// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2015 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <QMessageBox>
#include <algorithm>


#include <App/ComplexGeoData.h>
#include <App/Document.h>
#include <App/Placement.h>
#include <Base/Exception.h>
#include <Base/Converter.h>
#include <Base/CoordinateSystem.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Selection/Selection.h>
#include <Gui/WaitCursor.h>
#include <Mod/Mesh/App/Core/Approximation.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Part/App/PartFeature.h>

#include "FitBSplineSurface.h"
#include "OperationSupport.h"
#include "ui_FitBSplineSurface.h"


using namespace ReenGui;

class FitBSplineSurfaceWidget::Private
{
public:
    Ui_FitBSplineSurface ui;
    App::DocumentObjectT obj;
    Private() = default;
    ~Private() = default;
};

/* TRANSLATOR ReenGui::FitBSplineSurfaceWidget */

FitBSplineSurfaceWidget::FitBSplineSurfaceWidget(const App::DocumentObjectT& obj, QWidget* parent)
    : d(new Private())
{
    Q_UNUSED(parent);
    d->ui.setupUi(this);
    connect(
        d->ui.makePlacement,
        &QPushButton::clicked,
        this,
        &FitBSplineSurfaceWidget::onMakePlacementClicked
    );
    d->obj = obj;
    restoreSettings();
}

FitBSplineSurfaceWidget::~FitBSplineSurfaceWidget()
{
    saveSettings();
    delete d;
}

void FitBSplineSurfaceWidget::restoreSettings()
{
    d->ui.degreeU->onRestore();
    d->ui.polesU->onRestore();
    d->ui.degreeV->onRestore();
    d->ui.polesV->onRestore();
    d->ui.iterations->onRestore();
    d->ui.sizeFactor->onRestore();
    d->ui.totalWeight->onRestore();
    d->ui.gradient->onRestore();
    d->ui.bending->onRestore();
    d->ui.curvature->onRestore();
    d->ui.uvdir->onRestore();
}

void FitBSplineSurfaceWidget::saveSettings()
{
    d->ui.degreeU->onSave();
    d->ui.polesU->onSave();
    d->ui.degreeV->onSave();
    d->ui.polesV->onSave();
    d->ui.iterations->onSave();
    d->ui.sizeFactor->onSave();
    d->ui.totalWeight->onSave();
    d->ui.gradient->onSave();
    d->ui.bending->onSave();
    d->ui.curvature->onSave();
    d->ui.uvdir->onSave();
}

void FitBSplineSurfaceWidget::onMakePlacementClicked()
{
    try {
        auto* source = ReverseEngineeringGui::OperationSupport::usableTaskSource(d->obj);
        auto* geometrySource = freecad_cast<App::GeoFeature*>(source);
        auto* targetDocument = source ? source->getDocument() : nullptr;
        const auto* geometry = geometrySource ? geometrySource->getPropertyOfGeometry() : nullptr;
        const auto* complexGeometry = geometry ? geometry->getComplexData() : nullptr;
        if (!geometrySource || !targetDocument || !complexGeometry) {
            throw Base::RuntimeError("The original geometry is no longer available");
        }

        std::vector<Base::Vector3d> points;
        std::vector<Base::Vector3d> normals;
        complexGeometry->getPoints(points, normals, 0.001);
        if (points.size() < 3) {
            throw Base::ValueError("Placement fitting requires at least three source points");
        }

        std::vector<Base::Vector3f> data;
        data.reserve(points.size());
        std::transform(
            points.begin(),
            points.end(),
            std::back_inserter(data),
            [](const Base::Vector3d& point) { return Base::convertTo<Base::Vector3f>(point); }
        );
        MeshCore::PlaneFit fit;
        fit.AddPoints(data);
        if (fit.Fit() >= std::numeric_limits<float>::max()) {
            throw Base::RuntimeError("The source points could not define a local placement");
        }

        Base::CoordinateSystem coordinateSystem;
        coordinateSystem.setPosition(Base::convertTo<Base::Vector3d>(fit.GetBase()));
        coordinateSystem.setAxes(
            Base::convertTo<Base::Vector3d>(fit.GetNormal()),
            Base::convertTo<Base::Vector3d>(fit.GetDirU())
        );
        const Base::Placement placement = Base::CoordinateSystem().displacement(coordinateSystem);

        Gui::ExactTransaction mutation(
            *targetDocument,
            QT_TRANSLATE_NOOP("Command", "Create fitted placement")
        );
        auto* output = targetDocument->addObject<App::Placement>("FittedPlacement");
        if (!output) {
            throw Base::RuntimeError("The fitted placement object could not be created");
        }
        output->Label.setValue(geometrySource->Label.getStrValue() + " Placement");
        output->GeoFeature::Placement.setValue(placement);
        ReverseEngineeringGui::OperationSupport::setSource(*output, *geometrySource);
        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *targetDocument,
            {geometrySource},
            {output},
            "FittedPlacements",
            "Fitted Placement",
            "Create fitted placement"
        );
        targetDocument->recompute();
        if (output->isError()) {
            throw Base::RuntimeError("The fitted placement is invalid");
        }
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& e) {
        QMessageBox::warning(this, tr("Create Fitted Placement"), QString::fromUtf8(e.what()));
    }
}

bool FitBSplineSurfaceWidget::accept()
{
    try {
        auto* source = ReverseEngineeringGui::OperationSupport::usableTaskSource(d->obj);
        auto* geometrySource = freecad_cast<App::GeoFeature*>(source);
        auto* targetDocument = source ? source->getDocument() : nullptr;
        const auto* geometry = geometrySource ? geometrySource->getPropertyOfGeometry() : nullptr;
        const auto* complexGeometry = geometry ? geometry->getComplexData() : nullptr;
        if (!geometrySource || !targetDocument || !complexGeometry) {
            throw Base::RuntimeError("The original point cloud or mesh is no longer available");
        }
        std::vector<Base::Vector3d> sourcePoints;
        std::vector<Base::Vector3d> sourceNormals;
        complexGeometry->getPoints(sourcePoints, sourceNormals, 0.001);
        if (sourcePoints.size() < 4) {
            throw Base::ValueError("Surface fitting requires at least four source points");
        }

        QString documentPython = QString::fromStdString(d->obj.getDocumentPython());
        QString objectPython = QString::fromStdString(d->obj.getObjectPython());

        QString argument = QStringLiteral(
                               "Points=getattr(%1, %1.getPropertyNameOfGeometry()), "
                               "UDegree=%2, VDegree=%3, "
                               "NbUPoles=%4, NbVPoles=%5, "
                               "Smooth=%6, "
                               "Weight=%7, "
                               "Grad=%8, "
                               "Bend=%9, "
                               "Curv=%10, "
                               "Iterations=%11, "
                               "PatchFactor=%12, "
                               "Correction=True"
        )
                               .arg(objectPython)
                               .arg(d->ui.degreeU->value())
                               .arg(d->ui.degreeV->value())
                               .arg(d->ui.polesU->value())
                               .arg(d->ui.polesV->value())
                               .arg(
                                   d->ui.groupBoxSmooth->isChecked() ? QLatin1String("True")
                                                                     : QLatin1String("False")
                               )
                               .arg(d->ui.totalWeight->value())
                               .arg(d->ui.gradient->value())
                               .arg(d->ui.bending->value())
                               .arg(d->ui.curvature->value())
                               .arg(d->ui.iterations->value())
                               .arg(d->ui.sizeFactor->value());
        if (d->ui.uvdir->isChecked()) {
            std::vector<App::Placement*> selection
                = Gui::Selection().getObjectsOfType<App::Placement>();
            if (selection.size() != 1 || selection.front()->getDocument() != source->getDocument()
                || !MeshGui::isNativeMeshInputActive(selection.front())) {
                QMessageBox::warning(
                    this,
                    tr("Wrong selection"),
                    tr("Select a single placement object to get the local orientation.")
                );
                return false;
            }

            Base::Rotation rot = selection.front()->GeoFeature::Placement.getValue().getRotation();
            Base::Vector3d u(1, 0, 0);
            Base::Vector3d v(0, 1, 0);
            rot.multVec(u, u);
            rot.multVec(v, v);
            argument += QStringLiteral(", UVDirs=(FreeCAD.Vector(%1,%2,%3), FreeCAD.Vector(%4,%5,%6))")
                            .arg(u.x)
                            .arg(u.y)
                            .arg(u.z)
                            .arg(v.x)
                            .arg(v.y)
                            .arg(v.z);
        }
        QString command = QStringLiteral(
                              "%1.addObject(\"Part::Spline\", \"Spline\").Shape = "
                              "ReverseEngineering.approxSurface(%2).toShape()"
        )
                              .arg(documentPython, argument);

        Gui::WaitCursor wc;
        Gui::ExactTransaction mutation(
            *targetDocument,
            QT_TRANSLATE_NOOP("Command", "Fit B-spline surface")
        );
        const auto previousIds = ReverseEngineeringGui::OperationSupport::objectIds(*targetDocument);
        Gui::Command::addModule(Gui::Command::App, "ReverseEngineering");
        Gui::Command::runCommand(Gui::Command::Doc, command.toLatin1());

        auto created
            = ReverseEngineeringGui::OperationSupport::createdObjects(*targetDocument, previousIds);
        std::vector<Part::Feature*> outputs;
        for (auto* createdObject : created) {
            if (auto* feature = freecad_cast<Part::Feature*>(createdObject)) {
                outputs.push_back(feature);
            }
        }
        if (outputs.size() != 1 || outputs.front()->Shape.getValue().IsNull()) {
            throw Base::RuntimeError("Surface fitting did not produce exactly one usable B-spline");
        }
        auto* output = outputs.front();
        output->Label.setValue(geometrySource->Label.getStrValue() + " B-Spline Surface");
        ReverseEngineeringGui::OperationSupport::setSource(*output, *geometrySource);
        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *targetDocument,
            {geometrySource},
            {output},
            "FittedSurface",
            "Fitted Surface",
            "Fit B-spline surface"
        );
        targetDocument->recompute();
        if (output->isError() || output->Shape.getValue().IsNull()) {
            throw Base::RuntimeError("The fitted B-spline surface is invalid");
        }
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& e) {
        QMessageBox::warning(this, tr("Fit B-Spline Surface"), QString::fromUtf8(e.what()));
        return false;
    }

    return true;
}

void FitBSplineSurfaceWidget::changeEvent(QEvent* e)
{
    QWidget::changeEvent(e);
    if (e->type() == QEvent::LanguageChange) {
        d->ui.retranslateUi(this);
    }
}


/* TRANSLATOR ReenGui::TaskFitBSplineSurface */

TaskFitBSplineSurface::TaskFitBSplineSurface(const App::DocumentObjectT& obj)
{
    if (auto* document = obj.getDocument()) {
        setDocumentName(document->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    widget = new FitBSplineSurfaceWidget(obj);
    addTaskBox(Gui::BitmapFactory().pixmap("actions/FitSurface"), widget);
}

void TaskFitBSplineSurface::open()
{}

bool TaskFitBSplineSurface::accept()
{
    return widget->accept();
}

#include "moc_FitBSplineSurface.cpp"
