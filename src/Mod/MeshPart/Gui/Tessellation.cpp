// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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
#include <cmath>

#include <QFutureWatcher>
#include <QMessageBox>
#include <QProgressDialog>
#include <QtConcurrent/QtConcurrentRun>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Part/App/BodyBase.h>

#include "Tessellation.h"
#include "ui_Tessellation.h"


using namespace MeshPartGui;

class Tessellation::SelectionState
{
public:
    struct Target
    {
        Target(App::DocumentObject* object, std::string subName)
            : object(object)
            , subName(std::move(subName))
        {}

        App::DocumentObjectWeakPtrT object;
        std::string subName;
    };

    explicit SelectionState(App::Document* targetDocument)
        : document(targetDocument)
    {
        if (!targetDocument) {
            return;
        }
        for (const auto& selected : Gui::Selection().getSelection("*", Gui::ResolveMode::NoResolve)) {
            if (selected.pObject && selected.pObject->getDocument() == targetDocument
                && MeshGui::isNativeMeshInputActive(selected.pObject)) {
                targets.emplace_back(selected.pObject, selected.SubName);
            }
        }
    }

    App::DocumentWeakPtrT document;
    std::vector<Target> targets;
};

/* TRANSLATOR MeshPartGui::Tessellation */

Tessellation::Tessellation(QWidget* parent)
    : QWidget(parent)
    , selectionState(std::make_unique<SelectionState>(App::GetApplication().getActiveDocument()))
    , ui(new Ui_Tessellation)
{
    ui->setupUi(this);
    gmsh = new Mesh2ShapeGmsh(this);
    edgeEstimateWatcher = new QFutureWatcher<double>(this);
    setupConnections();

    ui->stackedWidget->addTab(gmsh, tr("Gmsh"));

    ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Mesh/Meshing/Standard"
    );
    double value = ui->spinSurfaceDeviation->value().getValue();
    value = handle->GetFloat("LinearDeflection", value);
    double angle = ui->spinAngularDeviation->value().getValue();
    angle = handle->GetFloat("AngularDeflection", angle);
    bool relative = ui->relativeDeviation->isChecked();
    relative = handle->GetBool("RelativeLinearDeflection", relative);
    ui->relativeDeviation->setChecked(relative);

    ui->spinSurfaceDeviation->setMaximum(std::numeric_limits<int>::max());
    ui->spinSurfaceDeviation->setValue(value);
    ui->spinAngularDeviation->setValue(angle);

    ui->spinMaximumEdgeLength->setRange(0, std::numeric_limits<int>::max());

    ui->comboFineness->setCurrentIndex(2);
    onComboFinenessCurrentIndexChanged(2);

#if !defined(HAVE_MEFISTO)
    ui->stackedWidget->setTabEnabled(Mefisto, false);
#endif
#if !defined(HAVE_NETGEN)
    ui->stackedWidget->setTabEnabled(Netgen, false);
#endif

}

Tessellation::~Tessellation() = default;

void Tessellation::setupConnections()
{
    connect(
        ui->estimateMaximumEdgeLength,
        &QPushButton::clicked,
        this,
        &Tessellation::onEstimateMaximumEdgeLengthClicked
    );
    connect(
        ui->comboFineness,
        qOverload<int>(&QComboBox::currentIndexChanged),
        this,
        &Tessellation::onComboFinenessCurrentIndexChanged
    );
    connect(ui->checkSecondOrder, &QCheckBox::toggled, this, &Tessellation::onCheckSecondOrderToggled);
    connect(ui->checkQuadDominated, &QCheckBox::toggled, this, &Tessellation::onCheckQuadDominatedToggled);
    connect(edgeEstimateWatcher, &QFutureWatcher<double>::finished, this, [this]() {
        ui->estimateMaximumEdgeLength->setEnabled(true);
        const bool cancelled = edgeEstimateWatcher->isCanceled();
        if (edgeEstimateProgress) {
            edgeEstimateProgress->disconnect(edgeEstimateWatcher);
            edgeEstimateProgress->close();
            edgeEstimateProgress->deleteLater();
            edgeEstimateProgress = nullptr;
        }
        if (cancelled) {
            return;
        }
        try {
            const double estimate = edgeEstimateWatcher->result();
            if (std::isfinite(estimate) && estimate > 0.0) {
                ui->spinMaximumEdgeLength->setValue(estimate);
            }
        }
        catch (...) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("The maximum edge length could not be estimated.")
            );
        }
    });
}

void Tessellation::onComboFinenessCurrentIndexChanged(int index)
{
    // NOLINTBEGIN
    if (index == 5) {
        ui->doubleGrading->setEnabled(true);
        ui->spinEdgeElements->setEnabled(true);
        ui->spinCurvatureElements->setEnabled(true);
    }
    else {
        ui->doubleGrading->setEnabled(false);
        ui->spinEdgeElements->setEnabled(false);
        ui->spinCurvatureElements->setEnabled(false);
    }

    switch (index) {
        case VeryCoarse:
            ui->doubleGrading->setValue(0.7);
            ui->spinEdgeElements->setValue(0.3);
            ui->spinCurvatureElements->setValue(1.0);
            break;
        case Coarse:
            ui->doubleGrading->setValue(0.5);
            ui->spinEdgeElements->setValue(0.5);
            ui->spinCurvatureElements->setValue(1.5);
            break;
        case Moderate:
            ui->doubleGrading->setValue(0.3);
            ui->spinEdgeElements->setValue(1.0);
            ui->spinCurvatureElements->setValue(2.0);
            break;
        case Fine:
            ui->doubleGrading->setValue(0.2);
            ui->spinEdgeElements->setValue(2.0);
            ui->spinCurvatureElements->setValue(3.0);
            break;
        case VeryFine:
            ui->doubleGrading->setValue(0.1);
            ui->spinEdgeElements->setValue(3.0);
            ui->spinCurvatureElements->setValue(5.0);
            break;
        default:
            break;
    }
    // NOLINTEND
}

void Tessellation::onCheckSecondOrderToggled(bool on)
{
    if (on) {
        ui->checkQuadDominated->setChecked(false);
    }
}

void Tessellation::onCheckQuadDominatedToggled(bool on)
{
    if (on) {
        ui->checkSecondOrder->setChecked(false);
    }
}

void Tessellation::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        int index = ui->comboFineness->currentIndex();
        ui->retranslateUi(this);
        ui->comboFineness->setCurrentIndex(index);
    }
    QWidget::changeEvent(e);
}

void Tessellation::onEstimateMaximumEdgeLengthClicked()
{
    if (edgeEstimateWatcher->isRunning()) {
        return;
    }
    App::Document* targetDocument = selectionState ? *selectionState->document : nullptr;
    if (!targetDocument) {
        return;
    }

    if (!Gui::Application::Instance->getDocument(targetDocument)) {
        return;
    }

    std::vector<Part::TopoShape> shapes;
    shapes.reserve(selectionState->targets.size());
    for (const auto& target : selectionState->targets) {
        auto* object = target.object.get<App::DocumentObject>();
        if (!object || object->getDocument() != targetDocument
            || !MeshGui::isNativeMeshInputActive(object)) {
            continue;
        }
        auto shape = Part::Feature::getTopoShape(
            object,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
            target.subName.c_str()
        );
        if (!shape.isNull()) {
            shapes.push_back(std::move(shape));
        }
    }
    if (shapes.empty()) {
        return;
    }

    ui->estimateMaximumEdgeLength->setEnabled(false);
    edgeEstimateProgress = new QProgressDialog(
        tr("Estimating maximum edge length in the background"),
        tr("Cancel"),
        0,
        0,
        this
    );
    edgeEstimateProgress->setWindowTitle(tr("Mesh From Shape"));
    edgeEstimateProgress->setWindowModality(Qt::NonModal);
    edgeEstimateProgress->setMinimumDuration(0);
    connect(edgeEstimateProgress, &QProgressDialog::canceled, edgeEstimateWatcher, [this]() {
        edgeEstimateWatcher->cancel();
    });
    edgeEstimateProgress->show();

    edgeEstimateWatcher->setFuture(QtConcurrent::run([shapes = std::move(shapes)]() {
        double edgeLength = 0.0;
        for (const auto& shape : shapes) {
            const Base::BoundBox3d bounds = shape.getBoundBox();
            edgeLength = std::max(edgeLength, bounds.LengthX());
            edgeLength = std::max(edgeLength, bounds.LengthY());
            edgeLength = std::max(edgeLength, bounds.LengthZ());
        }
        return edgeLength / 10.0;
    }));
}

bool Tessellation::accept()
{
    std::list<App::SubObjectT> shapeObjects;
    App::Document* targetDocument = selectionState ? *selectionState->document : nullptr;
    if (!targetDocument) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("The document selected for meshing is no longer open.")
        );
        return false;
    }

    Gui::Document* targetGui = Gui::Application::Instance->getDocument(targetDocument);
    if (!targetGui) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("The document selected for meshing is no longer open.")
        );
        return false;
    }

    this->document = QString::fromUtf8(targetDocument->getName());
    if (!MeshGui::hasCleanNativeMutationBoundary(targetDocument)) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Finish the current document operation before meshing.")
        );
        return false;
    }

    bool bodyWithNoTip = false;
    bool partWithNoFace = false;
    bool missingSelection = false;
    bool inactiveSelection = false;
    bool invalidSelection = false;
    for (const auto& target : selectionState->targets) {
        auto* object = target.object.get<App::DocumentObject>();
        if (!object || object->getDocument() != targetDocument) {
            missingSelection = true;
            continue;
        }
        if (!MeshGui::isNativeMeshInputActive(object)) {
            inactiveSelection = true;
            continue;
        }
        auto shape = Part::Feature::getTopoShape(
            object,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
            target.subName.c_str()
        );
        if (shape.hasSubShape(TopAbs_FACE)) {
            shapeObjects.emplace_back(object, target.subName.c_str());
        }
        else {
            invalidSelection = true;
            if (object->isDerivedFrom<Part::Feature>()) {
                partWithNoFace = true;
            }
            if (auto body = dynamic_cast<Part::BodyBase*>(object)) {
                if (!body->Tip.getValue()) {
                    bodyWithNoTip = true;
                }
            }
        }
    }

    if (missingSelection) {
        QMessageBox::critical(this, windowTitle(), tr("A shape selected for meshing no longer exists."));
        return false;
    }

    if (inactiveSelection) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("A shape selected for meshing is no longer active in History.")
        );
        return false;
    }

    if (invalidSelection || shapeObjects.empty()) {
        if (bodyWithNoTip) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Error: body without a tip selected.\n"
                   "Either set the tip of the body or select a different shape.")
            );
        }
        else if (partWithNoFace) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Error: shape without faces selected.\n"
                   "Select a different shape.")
            );
        }
        else {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Every selected object must contain faces that can be meshed.")
            );
        }
        return false;
    }

    bool doClose = !ui->checkBoxDontQuit->isChecked();
    int method = ui->stackedWidget->currentIndex();

    return processAndCommit(method, targetDocument, shapeObjects) && doClose;
}

void Tessellation::reject()
{
    if (edgeEstimateWatcher->isRunning()) {
        edgeEstimateWatcher->cancel();
    }
    if (gmsh) {
        gmsh->reject();
    }
}

bool Tessellation::processAndCommit(
    int method,
    App::Document* doc,
    const std::list<App::SubObjectT>& shapeObjects
)
{
    try {
        if (!doc || shapeObjects.empty() || !MeshGui::hasCleanNativeMutationBoundary(doc)) {
            return false;
        }
        for (const auto& info : shapeObjects) {
            auto* object = info.getObject();
            if (!object || object->getDocument() != doc || !MeshGui::isNativeMeshInputActive(object)) {
                return false;
            }
        }

        saveParameters(method);

        Base::PyGILStateLocker lock;
        Py::List entries;
        for (const auto& info : shapeObjects) {
            auto* obj = info.getObject();
            if (!obj || obj->getDocument() != doc || !MeshGui::isNativeMeshInputActive(obj)) {
                throw Base::RuntimeError("A shape selected for meshing no longer exists");
            }
            auto* sobj = obj->getSubObject(info.getSubName().c_str());
            if (!sobj) {
                throw Base::RuntimeError("A shape selected for meshing no longer exists");
            }
            sobj = sobj->getLinkedObject(true);
            if (!sobj || !MeshGui::isNativeMeshInputActive(sobj)) {
                throw Base::RuntimeError("A linked shape selected for meshing no longer exists");
            }
            Py::List subelements;
            if (!info.getSubName().empty()) {
                subelements.append(Py::String(info.getSubName()));
            }
            entries.append(Py::TupleN(
                Py::asObject(obj->getPyObject()),
                subelements,
                Py::String(sobj->Label.getStrValue() + " (Meshed)")
            ));
        }

        Py::Dict settings;
        if (method == Standard) {
            settings.setItem("method", Py::String("standard"));
            settings.setItem(
                "linear_deflection_mm",
                Py::Float(ui->spinSurfaceDeviation->value().getValue())
            );
            settings.setItem(
                "angular_deflection_radians",
                Py::Float(Base::toRadians<double>(ui->spinAngularDeviation->value().getValue()))
            );
            settings.setItem("relative", Py::Boolean(ui->relativeDeviation->isChecked()));
            settings.setItem("segments", Py::Boolean(ui->meshShapeColors->isChecked()));
        }
        else if (method == Mefisto) {
            settings.setItem("method", Py::String("mefisto"));
            settings.setItem(
                "maximum_edge_length_mm",
                Py::Float(
                    ui->spinMaximumEdgeLength->isEnabled()
                        ? ui->spinMaximumEdgeLength->value().getValue()
                        : 0.0
                )
            );
        }
        else if (method == Netgen) {
            settings.setItem("method", Py::String("netgen"));
            settings.setItem("fineness", Py::Long(ui->comboFineness->currentIndex()));
            settings.setItem("growth_rate", Py::Float(ui->doubleGrading->value()));
            settings.setItem("segments_per_edge", Py::Float(ui->spinEdgeElements->value()));
            settings.setItem(
                "segments_per_radius",
                Py::Float(ui->spinCurvatureElements->value())
            );
            settings.setItem("second_order", Py::Boolean(ui->checkSecondOrder->isChecked()));
            settings.setItem("optimize", Py::Boolean(ui->checkOptimizeSurface->isChecked()));
            settings.setItem("quad_dominated", Py::Boolean(ui->checkQuadDominated->isChecked()));
        }
        else if (method == Gmsh) {
            settings.setItem("method", Py::String("gmsh"));
            settings.setItem("algorithm", Py::Long(gmsh->algorithm()));
            settings.setItem("minimum_size_mm", Py::Float(gmsh->minimumSize()));
            settings.setItem("maximum_size_mm", Py::Float(gmsh->maximumSize()));
            settings.setItem("geometry_tolerance_mm", Py::Float(1.0e-6));
            settings.setItem("element_order", Py::Long(2));
            settings.setItem("optimize", Py::Boolean(true));
            settings.setItem("executable", Py::String(gmsh->executable()));
            settings.setItem("timeout_seconds", Py::Long(600));
        }
        else {
            throw Base::ValueError("Unknown shape tessellation method");
        }

        PyObject* imported = PyImport_ImportModule("VibeCADMeshTessellationGui");
        if (!imported) {
            throw Py::Exception();
        }
        Py::Module module(imported, true);
        module.callMemberFunction("start_shape_tessellations", Py::TupleN(
            entries,
            settings,
            Py::Boolean(method == Standard && ui->meshShapeColors->isChecked()),
            Py::Boolean(ui->groupsFaceColors->isChecked())
        ));
        return true;
    }
    catch (const Py::Exception&) {
        Base::PyException error;
        Base::Console().error("%s\n", error.what());
        return false;
    }
    catch (const Base::Exception& e) {
        Base::Console().error(e.what());
        return false;
    }
    catch (...) {
        Base::Console().error("Meshing failed because of an unknown error\n");
        return false;
    }
}

void Tessellation::saveParameters(int method)
{
    if (method == Standard) {
        ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Mod/Mesh/Meshing/Standard"
        );
        double value = ui->spinSurfaceDeviation->value().getValue();
        handle->SetFloat("LinearDeflection", value);
        double angle = ui->spinAngularDeviation->value().getValue();
        handle->SetFloat("AngularDeflection", angle);
        bool relative = ui->relativeDeviation->isChecked();
        handle->SetBool("RelativeLinearDeflection", relative);
    }
}

// ---------------------------------------

Mesh2ShapeGmsh::Mesh2ShapeGmsh(QWidget* parent, Qt::WindowFlags fl)
    : GmshWidget(parent, fl)
{}

Mesh2ShapeGmsh::~Mesh2ShapeGmsh() = default;

int Mesh2ShapeGmsh::algorithm() const
{
    return meshingAlgorithm();
}

double Mesh2ShapeGmsh::minimumSize() const
{
    return getMinSize();
}

double Mesh2ShapeGmsh::maximumSize() const
{
    return getMaxSize();
}

std::string Mesh2ShapeGmsh::executable() const
{
    return executablePath().toStdString();
}

TaskTessellation::TaskTessellation()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    if (document) {
        setDocumentName(document->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    widget = new Tessellation();
    addTaskBox(widget);
}

void TaskTessellation::open()
{}

void TaskTessellation::clicked(int id)
{
    Q_UNUSED(id)
}

bool TaskTessellation::accept()
{
    return widget->accept();
}

bool TaskTessellation::reject()
{
    widget->reject();
    return true;
}

#include "moc_Tessellation.cpp"
