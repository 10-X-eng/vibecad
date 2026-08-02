// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2012 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Mod/Mesh/App/Core/Curvature.h>
#include <Mod/Mesh/App/Core/Segmentation.h>
#include <Mod/Mesh/App/Core/Smoothing.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/MeshFeature.h>

#include "Segmentation.h"
#include "CommandGuard.h"
#include "ParametricMeshFilter.h"
#include "ui_Segmentation.h"


using namespace MeshGui;

Segmentation::Segmentation(Mesh::Feature* mesh, QWidget* parent, Qt::WindowFlags fl)
    : QWidget(parent, fl)
    , ui(new Ui_Segmentation)
    , myMesh(mesh)
{
    constexpr int max = std::numeric_limits<int>::max();
    ui->setupUi(this);
    ui->numPln->setRange(1, max);
    ui->numPln->setValue(100);
    ui->crvCyl->setRange(0, max);
    ui->numCyl->setRange(1, max);
    ui->numCyl->setValue(100);
    ui->crvSph->setRange(0, max);
    ui->numSph->setRange(1, max);
    ui->numSph->setValue(100);
    ui->crv1Free->setRange(-max, max);
    ui->crv2Free->setRange(-max, max);
    ui->numFree->setRange(1, max);
    ui->numFree->setValue(100);

    ui->checkBoxSmooth->setChecked(false);
}

Segmentation::~Segmentation()
{
    // no need to delete child widgets, Qt does it all for us
    delete ui;
}

void Segmentation::accept()
{
    auto* target = myMesh.get<Mesh::Feature>();
    if (!target || !target->getNameInDocument()
        || !MeshGui::isNativeMeshInputActive(target)) {
        throw Base::RuntimeError(
            "The mesh selected for segmentation is no longer active in History"
        );
    }
    const Mesh::MeshObject* mesh = target->Mesh.getValuePtr();
    // make a copy because we might smooth the mesh before
    MeshCore::MeshKernel kernel = mesh->getKernel();

    if (ui->checkBoxSmooth->isChecked()) {
        MeshCore::LaplaceSmoothing smoother(kernel);
        smoother.Smooth(ui->smoothSteps->value());
    }

    MeshCore::MeshSegmentAlgorithm finder(kernel);
    MeshCore::MeshCurvature meshCurv(kernel);
    meshCurv.ComputePerVertex();

    std::vector<MeshCore::MeshSurfaceSegmentPtr> segm;
    if (ui->groupBoxFree->isChecked()) {
        segm.emplace_back(
            std::make_shared<MeshCore::MeshCurvatureFreeformSegment>(
                meshCurv.GetCurvature(),
                ui->numFree->value(),
                ui->tol1Free->value(),
                ui->tol2Free->value(),
                ui->crv1Free->value(),
                ui->crv2Free->value()
            )
        );
    }
    if (ui->groupBoxCyl->isChecked()) {
        segm.emplace_back(
            std::make_shared<MeshCore::MeshCurvatureCylindricalSegment>(
                meshCurv.GetCurvature(),
                ui->numCyl->value(),
                ui->tol1Cyl->value(),
                ui->tol2Cyl->value(),
                ui->crvCyl->value()
            )
        );
    }
    if (ui->groupBoxSph->isChecked()) {
        segm.emplace_back(
            std::make_shared<MeshCore::MeshCurvatureSphericalSegment>(
                meshCurv.GetCurvature(),
                ui->numSph->value(),
                ui->tolSph->value(),
                ui->crvSph->value()
            )
        );
    }
    if (ui->groupBoxPln->isChecked()) {
        segm.emplace_back(
            std::make_shared<MeshCore::MeshCurvaturePlanarSegment>(
                meshCurv.GetCurvature(),
                ui->numPln->value(),
                ui->tolPln->value()
            )
        );
    }
    finder.FindSegments(segm);

    struct Result
    {
        std::vector<long> facets;
        std::string type;
    };
    std::vector<Result> results;
    for (const auto& segment : segm) {
        for (const auto& result : segment->GetSegments()) {
            if (result.empty()) {
                continue;
            }
            results.push_back(
                {
                    std::vector<long>(result.begin(), result.end()),
                    segment->GetType(),
                }
            );
        }
    }
    if (results.empty()) {
        throw Base::RuntimeError("The current settings did not find any mesh segments");
    }

    App::Document* document = target->getDocument();
    if (!MeshGui::hasCleanNativeMutationBoundary(document)) {
        throw Base::RuntimeError("Another document operation is already in progress");
    }
    std::vector<std::string> labels;
    std::vector<MeshGui::ParametricMeshFilterTarget> operations;
    labels.reserve(results.size());
    operations.reserve(results.size());
    for (const auto& result : results) {
        std::stringstream label;
        label << "Mesh Segment (" << result.type << ")";
        labels.push_back(label.str());
        operations.push_back(
            MeshGui::ParametricMeshFilterTarget {
                target,
                [target,
                 facets = result.facets,
                 label = labels.back(),
                 type = result.type](
                    App::DocumentObject& object
                ) {
                    auto& subset =
                        static_cast<Mesh::FacetSubset&>(object);
                    subset.Label.setValue(label);
                    subset.FacetIndices.setValues(facets);
                    subset.AcceptedTopology.setValue(
                        target->Mesh.getValue()
                    );
                    subset.SelectionKind.setValue(type);
                },
            }
        );
    }
    std::string groupLabel = "Curvature Segments ";
    groupLabel += target->Label.getValue();
    MeshGui::createParametricMeshFilters(
        *document,
        operations,
        MeshGui::ParametricMeshFilterSpec {
            "Mesh::FacetSubset",
            "Segment",
            "Mesh Segment",
            "Segmentation",
            true,
            true,
            true,
            "CurvatureSegmentation",
            groupLabel.c_str(),
            "Curvature segmentation",
        }
    );
}

void Segmentation::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    QWidget::changeEvent(e);
}

// ---------------------------------------

/* TRANSLATOR MeshGui::TaskRemoveComponents */

TaskSegmentation::TaskSegmentation(Mesh::Feature* mesh)
{
    if (mesh && mesh->getDocument()) {
        setDocumentName(mesh->getDocument()->getName());
        setAutoCloseOnDeletedDocument(true);
        associateToObject3dView(mesh);
    }
    widget = new Segmentation(mesh);  // NOLINT
    addTaskBox(widget, false);
}

bool TaskSegmentation::accept()
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
        Base::Console().error("Mesh segmentation failed because of an unknown error\n");
        return false;
    }
}
