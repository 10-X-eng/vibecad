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

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

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
#include "BackgroundMeshSegmentation.h"
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
    QJsonArray surfaces;
    if (ui->groupBoxFree->isChecked()) {
        surfaces.append(QJsonObject {
            {"kind", "freeform"},
            {"minimum_facets", ui->numFree->value()},
            {"maximum_curvature_per_mm", ui->crv1Free->value()},
            {"minimum_curvature_per_mm", ui->crv2Free->value()},
            {"maximum_curvature_tolerance", ui->tol1Free->value()},
            {"minimum_curvature_tolerance", ui->tol2Free->value()},
        });
    }
    if (ui->groupBoxCyl->isChecked()) {
        surfaces.append(QJsonObject {
            {"kind", "cylinder"},
            {"minimum_facets", ui->numCyl->value()},
            {"curvature_per_mm", ui->crvCyl->value()},
            {"flat_curvature_tolerance", ui->tol1Cyl->value()},
            {"curved_curvature_tolerance", ui->tol2Cyl->value()},
        });
    }
    if (ui->groupBoxSph->isChecked()) {
        surfaces.append(QJsonObject {
            {"kind", "sphere"},
            {"minimum_facets", ui->numSph->value()},
            {"curvature_per_mm", ui->crvSph->value()},
            {"curvature_tolerance", ui->tolSph->value()},
        });
    }
    if (ui->groupBoxPln->isChecked()) {
        surfaces.append(QJsonObject {
            {"kind", "plane"},
            {"minimum_facets", ui->numPln->value()},
            {"curvature_tolerance", ui->tolPln->value()},
        });
    }
    if (surfaces.isEmpty()) {
        throw Base::ValueError("Select at least one surface type to segment");
    }
    const QJsonObject settings {
        {"surfaces", surfaces},
        {
            "smoothing_steps",
            ui->checkBoxSmooth->isChecked() ? ui->smoothSteps->value() : 0
        },
        {"result_label_prefix", "Mesh Segment"},
    };
    MeshGui::startBackgroundMeshSegmentation(
        {target},
        "mesh_segmentation",
        QJsonDocument(settings).toJson(QJsonDocument::Compact).toStdString()
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
