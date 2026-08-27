// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2018 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include <QDialog>
#include <QDoubleSpinBox>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMessageBox>
#include <QVBoxLayout>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Selection/SelectionObject.h>
#include <Mod/Mesh/App/Core/Approximation.h>
#include <Mod/Mesh/App/Core/Segmentation.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/MeshFeature.h>

#include "SegmentationBestFit.h"
#include "BackgroundMeshSegmentation.h"
#include "CommandGuard.h"
#include "ParametricMeshFilter.h"
#include "ui_SegmentationBestFit.h"


using namespace MeshGui;

namespace MeshGui
{
class PlaneFitParameter: public FitParameter
{
public:
    PlaneFitParameter() = default;
    std::vector<float> getParameter(FitParameter::Points pts) const override
    {
        std::vector<float> values;
        MeshCore::PlaneFit fit;
        fit.AddPoints(pts.points);
        if (fit.Fit() < std::numeric_limits<float>::max()) {
            Base::Vector3f base = fit.GetBase();
            Base::Vector3f axis = fit.GetNormal();
            values.push_back(base.x);
            values.push_back(base.y);
            values.push_back(base.z);
            values.push_back(axis.x);
            values.push_back(axis.y);
            values.push_back(axis.z);
        }
        return values;
    }
};

class CylinderFitParameter: public FitParameter
{
public:
    CylinderFitParameter() = default;
    std::vector<float> getParameter(FitParameter::Points pts) const override
    {
        std::vector<float> values;
        MeshCore::CylinderFit fit;
        fit.AddPoints(pts.points);
        if (!pts.normals.empty()) {
            Base::Vector3f base = fit.GetGravity();
            Base::Vector3f axis = fit.GetInitialAxisFromNormals(pts.normals);
            fit.SetInitialValues(base, axis);

#if defined(FC_DEBUG)
            Base::Console().message("Initial axis: (%f, %f, %f)\n", axis.x, axis.y, axis.z);
#endif
        }

        if (fit.Fit() < std::numeric_limits<float>::max()) {
            Base::Vector3f base, top;
            fit.GetBounding(base, top);
            Base::Vector3f axis = fit.GetAxis();
            float radius = fit.GetRadius();
            values.push_back(base.x);
            values.push_back(base.y);
            values.push_back(base.z);
            values.push_back(axis.x);
            values.push_back(axis.y);
            values.push_back(axis.z);
            values.push_back(radius);
        }
        return values;
    }
};

class SphereFitParameter: public FitParameter
{
public:
    SphereFitParameter() = default;
    std::vector<float> getParameter(FitParameter::Points pts) const override
    {
        std::vector<float> values;
        MeshCore::SphereFit fit;
        fit.AddPoints(pts.points);
        if (fit.Fit() < std::numeric_limits<float>::max()) {
            Base::Vector3f base = fit.GetCenter();
            float radius = fit.GetRadius();
            values.push_back(base.x);
            values.push_back(base.y);
            values.push_back(base.z);
            values.push_back(radius);
        }
        return values;
    }
};
}  // namespace MeshGui

ParametersDialog::ParametersDialog(
    std::vector<float>& val,
    FitParameter* fitPar,
    ParameterList par,
    Mesh::Feature* mesh,
    QWidget* parent
)
    : QDialog(parent)
    , values(val)
    , fitParameter(fitPar)
    , parameter(std::move(par))
    , myMesh(mesh)
{
    this->setWindowTitle(tr("Surface Fit"));

    QGridLayout* gridLayout {};
    gridLayout = new QGridLayout(this);

    QGroupBox* groupBox {};
    groupBox = new QGroupBox(this);
    groupBox->setTitle(tr("Parameters"));
    gridLayout->addWidget(groupBox, 0, 0, 1, 1);

    QGroupBox* selectBox {};
    selectBox = new QGroupBox(this);
    selectBox->setTitle(tr("Selection"));
    gridLayout->addWidget(selectBox, 1, 0, 1, 1);

    QVBoxLayout* selectLayout {};
    selectLayout = new QVBoxLayout(selectBox);

    QPushButton* regionButton {};
    regionButton = new QPushButton(this);
    regionButton->setText(tr("Region"));
    regionButton->setObjectName(QStringLiteral("region"));
    selectLayout->addWidget(regionButton);

    QPushButton* singleButton {};
    singleButton = new QPushButton(this);
    singleButton->setText(tr("Triangle"));
    singleButton->setObjectName(QStringLiteral("single"));
    selectLayout->addWidget(singleButton);

    QPushButton* clearButton {};
    clearButton = new QPushButton(this);
    clearButton->setText(tr("Clear"));
    clearButton->setObjectName(QStringLiteral("clear"));
    selectLayout->addWidget(clearButton);

    QPushButton* computeButton {};
    computeButton = new QPushButton(this);
    computeButton->setText(tr("Compute"));
    computeButton->setObjectName(QStringLiteral("compute"));
    gridLayout->addWidget(computeButton, 2, 0, 1, 1);

    QDialogButtonBox* buttonBox {};
    buttonBox = new QDialogButtonBox(this);
    buttonBox->setOrientation(Qt::Horizontal);
    buttonBox->setStandardButtons(QDialogButtonBox::Cancel | QDialogButtonBox::Ok);
    gridLayout->addWidget(buttonBox, 3, 0, 1, 1);

    int index = 0;
    QGridLayout* layout {};
    layout = new QGridLayout(groupBox);
    groupBox->setLayout(layout);
    for (const auto& it : parameter) {
        QLabel* label = new QLabel(groupBox);
        label->setText(it.first);
        layout->addWidget(label, index, 0, 1, 1);

        QDoubleSpinBox* doubleSpinBox = new QDoubleSpinBox(groupBox);
        doubleSpinBox->setObjectName(it.first);
        doubleSpinBox->setRange(-std::numeric_limits<int>::max(), std::numeric_limits<int>::max());
        doubleSpinBox->setValue(it.second);
        layout->addWidget(doubleSpinBox, index, 1, 1, 1);
        spinBoxes.push_back(doubleSpinBox);
        ++index;
    }

    // clang-format off
    connect(buttonBox, &QDialogButtonBox::accepted, this, &ParametersDialog::accept);
    connect(buttonBox, &QDialogButtonBox::rejected, this, &ParametersDialog::reject);
    connect(regionButton, &QPushButton::clicked, this, &ParametersDialog::onRegionClicked);
    connect(singleButton, &QPushButton::clicked, this, &ParametersDialog::onSingleClicked);
    connect(clearButton, &QPushButton::clicked, this, &ParametersDialog::onClearClicked);
    connect(computeButton, &QPushButton::clicked, this, &ParametersDialog::onComputeClicked);
    // clang-format on

    Gui::SelectionObject obj(mesh);
    std::vector<Gui::SelectionObject> sel;
    sel.push_back(obj);
    Gui::Selection().clearSelection();
    meshSel.setObjects(sel);
    meshSel.setCheckOnlyPointToUserTriangles(true);
    meshSel.setCheckOnlyVisibleTriangles(true);
    meshSel.setEnabledViewerSelection(false);
}

ParametersDialog::~ParametersDialog()
{
    meshSel.clearSelection();
    meshSel.setEnabledViewerSelection(true);
    delete fitParameter;
}

void ParametersDialog::onRegionClicked()
{
    meshSel.startSelection();
}

void ParametersDialog::onSingleClicked()
{
    meshSel.selectTriangle();
}

void ParametersDialog::onClearClicked()
{
    meshSel.clearSelection();
}

void ParametersDialog::onComputeClicked()
{
    auto* target = myMesh.get<Mesh::Feature>();
    if (!MeshGui::isNativeMeshInputActive(target)) {
        QMessageBox::warning(
            this,
            tr("Mesh unavailable"),
            tr("The mesh selected for fitting no longer exists.")
        );
        return;
    }
    const Mesh::MeshObject& kernel = target->Mesh.getValue();
    if (kernel.hasSelectedFacets()) {
        FitParameter::Points fitpts;
        std::vector<Mesh::ElementIndex> facets, points;
        kernel.getFacetsFromSelection(facets);
        points = kernel.getPointsFromFacets(facets);
        MeshCore::MeshPointArray coords = kernel.getKernel().GetPoints(points);
        fitpts.normals = kernel.getKernel().GetFacetNormals(facets);

        // Copy points into right format
        fitpts.points.insert(fitpts.points.end(), coords.begin(), coords.end());
        coords.clear();

        const std::vector<float> computed = fitParameter->getParameter(fitpts);
        if (computed.size() == spinBoxes.size()) {
            for (std::size_t i = 0; i < computed.size(); ++i) {
                spinBoxes[i]->setValue(computed[i]);
            }
        }
        meshSel.stopSelection();
        meshSel.clearSelection();
    }
    else {
        QMessageBox::warning(this, tr("No selection"), tr("Before fitting the surface select an area."));
    }
}

void ParametersDialog::accept()
{
    std::vector<float> v;
    for (auto it : spinBoxes) {
        v.push_back(it->value());
    }
    values = v;
    QDialog::accept();
}

void ParametersDialog::reject()
{
    QDialog::reject();
}

// ----------------------------------------------------------------------------

/* TRANSLATOR MeshGui::SegmentationBestFit */

SegmentationBestFit::SegmentationBestFit(Mesh::Feature* mesh, QWidget* parent, Qt::WindowFlags fl)
    : QWidget(parent, fl)
    , ui(new Ui_SegmentationBestFit)
    , myMesh(mesh)
{
    ui->setupUi(this);
    setupConnections();

    ui->numPln->setRange(1, std::numeric_limits<int>::max());
    ui->numPln->setValue(100);
    ui->numCyl->setRange(1, std::numeric_limits<int>::max());
    ui->numCyl->setValue(100);
    ui->numSph->setRange(1, std::numeric_limits<int>::max());
    ui->numSph->setValue(100);

    Gui::SelectionObject obj(mesh);
    std::vector<Gui::SelectionObject> sel;
    sel.push_back(obj);
    meshSel.setObjects(sel);
}

SegmentationBestFit::~SegmentationBestFit()
{
    // no need to delete child widgets, Qt does it all for us
    delete ui;
}

void SegmentationBestFit::setupConnections()
{
    // clang-format off
    connect(ui->planeParameters, &QPushButton::clicked,
            this, &SegmentationBestFit::onPlaneParametersClicked);
    connect(ui->cylinderParameters, &QPushButton::clicked,
            this, &SegmentationBestFit::onCylinderParametersClicked);
    connect(ui->sphereParameters, &QPushButton::clicked,
            this, &SegmentationBestFit::onSphereParametersClicked);
    // clang-format on
}

void SegmentationBestFit::onPlaneParametersClicked()
{
    ParameterList list;
    std::vector<float> p = planeParameter;
    p.resize(6);
    QString base = tr("Base");
    QString axis = tr("Normal");
    QString x = QStringLiteral(" x");
    QString y = QStringLiteral(" y");
    QString z = QStringLiteral(" z");
    list.push_back(std::make_pair(base + x, p[0]));
    list.push_back(std::make_pair(base + y, p[1]));
    list.push_back(std::make_pair(base + z, p[2]));
    list.push_back(std::make_pair(axis + x, p[3]));
    list.push_back(std::make_pair(axis + y, p[4]));
    list.push_back(std::make_pair(axis + z, p[5]));

    auto* target = myMesh.get<Mesh::Feature>();
    if (!MeshGui::isNativeMeshInputActive(target)) {
        return;
    }
    ParametersDialog dialog(planeParameter, new PlaneFitParameter, list, target, this);
    dialog.exec();
}

void SegmentationBestFit::onCylinderParametersClicked()
{
    ParameterList list;
    std::vector<float> p = cylinderParameter;
    p.resize(7);
    QString base = tr("Base");
    QString axis = tr("Axis");
    QString radius = tr("Radius");
    QString x = QStringLiteral(" x");
    QString y = QStringLiteral(" y");
    QString z = QStringLiteral(" z");
    list.push_back(std::make_pair(base + x, p[0]));
    list.push_back(std::make_pair(base + y, p[1]));
    list.push_back(std::make_pair(base + z, p[2]));
    list.push_back(std::make_pair(axis + x, p[3]));
    list.push_back(std::make_pair(axis + y, p[4]));
    list.push_back(std::make_pair(axis + z, p[5]));
    list.push_back(std::make_pair(radius, p[6]));

    auto* target = myMesh.get<Mesh::Feature>();
    if (!MeshGui::isNativeMeshInputActive(target)) {
        return;
    }
    ParametersDialog dialog(cylinderParameter, new CylinderFitParameter, list, target, this);
    dialog.exec();
}

void SegmentationBestFit::onSphereParametersClicked()
{
    ParameterList list;
    std::vector<float> p = sphereParameter;
    p.resize(4);
    QString base = tr("Center");
    QString radius = tr("Radius");
    QString x = QStringLiteral(" x");
    QString y = QStringLiteral(" y");
    QString z = QStringLiteral(" z");
    list.push_back(std::make_pair(base + x, p[0]));
    list.push_back(std::make_pair(base + y, p[1]));
    list.push_back(std::make_pair(base + z, p[2]));
    list.push_back(std::make_pair(radius, p[3]));

    auto* target = myMesh.get<Mesh::Feature>();
    if (!MeshGui::isNativeMeshInputActive(target)) {
        return;
    }
    ParametersDialog dialog(sphereParameter, new SphereFitParameter, list, target, this);
    dialog.exec();
}

void SegmentationBestFit::accept()
{
    auto* target = myMesh.get<Mesh::Feature>();
    if (!target || !target->getNameInDocument()
        || !MeshGui::isNativeMeshInputActive(target)) {
        throw Base::RuntimeError(
            "The mesh selected for segmentation is no longer active in History"
        );
    }
    QJsonArray surfaces;
    if (ui->groupBoxCyl->isChecked()) {
        QJsonObject surface {
            {"kind", "cylinder"},
            {"minimum_facets", ui->numCyl->value()},
            {"distance_tolerance_mm", ui->tolCyl->value()},
        };
        if (cylinderParameter.size() == 7) {
            surface.insert("initial", QJsonObject {
                {
                    "base_mm",
                    QJsonObject {
                        {"x_mm", cylinderParameter[0]},
                        {"y_mm", cylinderParameter[1]},
                        {"z_mm", cylinderParameter[2]},
                    }
                },
                {
                    "axis",
                    QJsonObject {
                        {"x", cylinderParameter[3]},
                        {"y", cylinderParameter[4]},
                        {"z", cylinderParameter[5]},
                    }
                },
                {"radius_mm", cylinderParameter[6]},
            });
        }
        surfaces.append(surface);
    }
    if (ui->groupBoxSph->isChecked()) {
        QJsonObject surface {
            {"kind", "sphere"},
            {"minimum_facets", ui->numSph->value()},
            {"distance_tolerance_mm", ui->tolSph->value()},
        };
        if (sphereParameter.size() == 4) {
            surface.insert("initial", QJsonObject {
                {
                    "center_mm",
                    QJsonObject {
                        {"x_mm", sphereParameter[0]},
                        {"y_mm", sphereParameter[1]},
                        {"z_mm", sphereParameter[2]},
                    }
                },
                {"radius_mm", sphereParameter[3]},
            });
        }
        surfaces.append(surface);
    }
    if (ui->groupBoxPln->isChecked()) {
        QJsonObject surface {
            {"kind", "plane"},
            {"minimum_facets", ui->numPln->value()},
            {"distance_tolerance_mm", ui->tolPln->value()},
        };
        if (planeParameter.size() == 6) {
            surface.insert("initial", QJsonObject {
                {
                    "point_mm",
                    QJsonObject {
                        {"x_mm", planeParameter[0]},
                        {"y_mm", planeParameter[1]},
                        {"z_mm", planeParameter[2]},
                    }
                },
                {
                    "normal",
                    QJsonObject {
                        {"x", planeParameter[3]},
                        {"y", planeParameter[4]},
                        {"z", planeParameter[5]},
                    }
                },
            });
        }
        surfaces.append(surface);
    }
    if (surfaces.isEmpty()) {
        throw Base::ValueError("Select at least one surface type to segment");
    }
    const QJsonObject settings {
        {"surfaces", surfaces},
        {"result_label_prefix", "Mesh Segment"},
    };
    MeshGui::startBackgroundMeshSegmentation(
        {target},
        "segmentation_best_fit",
        QJsonDocument(settings).toJson(QJsonDocument::Compact).toStdString()
    );
}

void SegmentationBestFit::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    QWidget::changeEvent(e);
}

// ---------------------------------------

/* TRANSLATOR MeshGui::TaskSegmentationBestFit */

TaskSegmentationBestFit::TaskSegmentationBestFit(Mesh::Feature* mesh)
{
    if (mesh && mesh->getDocument()) {
        setDocumentName(mesh->getDocument()->getName());
        setAutoCloseOnDeletedDocument(true);
        associateToObject3dView(mesh);
    }
    widget = new SegmentationBestFit(mesh);  // NOLINT
    addTaskBox(widget, false);
}

bool TaskSegmentationBestFit::accept()
{
    try {
        widget->accept();
        return true;
    }
    catch (const Base::Exception& error) {
        error.reportException();
        return false;
    }
    catch (...) {
        Base::Console().error("Best-fit mesh segmentation failed because of an unknown error\n");
        return false;
    }
}

#include "moc_SegmentationBestFit.cpp"
