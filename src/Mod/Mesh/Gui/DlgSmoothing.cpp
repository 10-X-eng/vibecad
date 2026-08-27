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
#include <iomanip>
#include <sstream>

#include <QButtonGroup>
#include <QDialogButtonBox>


#include <App/Application.h>
#include <App/Document.h>
#include <Gui/Command.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Selection/Selection.h>
#include <Base/Interpreter.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/Core/Smoothing.h>

#include "DlgSmoothing.h"
#include "BackgroundMeshModification.h"
#include "CommandGuard.h"
#include "ParametricMeshFilter.h"
#include "ui_DlgSmoothing.h"
#include "Selection.h"


using namespace MeshGui;

/* TRANSLATOR MeshGui::DlgSmoothing */

DlgSmoothing::DlgSmoothing(QWidget* parent)
    : QWidget(parent)
    , ui(new Ui_DlgSmoothing())
{
    // clang-format off
    ui->setupUi(this);
    bg = new QButtonGroup(this); //NOLINT
    bg->addButton(ui->radioButtonTaubin, 0);
    bg->addButton(ui->radioButtonLaplace, 1);

    connect(ui->checkBoxSelection, &QCheckBox::toggled,
            this, &DlgSmoothing::onCheckBoxSelectionToggled);
    connect(bg, qOverload<int>(&QButtonGroup::idClicked),
            this, &DlgSmoothing::methodClicked);

    ui->labelLambda->setText(QString::fromUtf8("\xce\xbb"));
    ui->labelMu->setText(QString::fromUtf8("\xce\xbc"));
    this->resize(this->sizeHint());
    // clang-format on
}

/*
 *  Destroys the object and frees any allocated resources
 */
DlgSmoothing::~DlgSmoothing()
{
    // no need to delete child widgets, Qt does it all for us
    delete ui;
}

void DlgSmoothing::methodClicked(int id)
{
    if (bg->button(id) == ui->radioButtonTaubin) {
        ui->labelMu->setEnabled(true);
        ui->spinMicro->setEnabled(true);
    }
    else {
        ui->labelMu->setEnabled(false);
        ui->spinMicro->setEnabled(false);
    }
}

int DlgSmoothing::iterations() const
{
    return ui->iterations->value();
}

double DlgSmoothing::lambdaStep() const
{
    return ui->spinLambda->value();
}

double DlgSmoothing::microStep() const
{
    return ui->spinMicro->value();
}

DlgSmoothing::Smooth DlgSmoothing::method() const
{
    if (ui->radioButtonTaubin->isChecked()) {
        return DlgSmoothing::Taubin;
    }
    if (ui->radioButtonLaplace->isChecked()) {
        return DlgSmoothing::Laplace;
    }
    return DlgSmoothing::None;
}

bool DlgSmoothing::smoothSelection() const
{
    return ui->checkBoxSelection->isChecked();
}

void DlgSmoothing::onCheckBoxSelectionToggled(bool on)
{
    Q_EMIT toggledSelection(on);
}

// ------------------------------------------------

SmoothingDialog::SmoothingDialog(QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
{
    widget = new DlgSmoothing(this);
    this->setWindowTitle(widget->windowTitle());

    QVBoxLayout* hboxLayout = new QVBoxLayout(this);
    QDialogButtonBox* buttonBox = new QDialogButtonBox(this);
    buttonBox->setStandardButtons(QDialogButtonBox::Cancel | QDialogButtonBox::Ok);

    connect(buttonBox, &QDialogButtonBox::accepted, this, &QDialog::accept);
    connect(buttonBox, &QDialogButtonBox::rejected, this, &QDialog::reject);

    hboxLayout->addWidget(widget);
    hboxLayout->addWidget(buttonBox);
}

SmoothingDialog::~SmoothingDialog() = default;

// ---------------------------------------

/* TRANSLATOR MeshGui::TaskSmoothing */

TaskSmoothing::TaskSmoothing()
{
    auto selected = Gui::Selection().getSelectionEx(nullptr, Mesh::Feature::getClassTypeId());
    widget = new DlgSmoothing();  // NOLINT
    addTaskBox(widget, false, nullptr);

    selection = new Selection();  // NOLINT
    selection->setObjects(selected);
    targets.reserve(selected.size());
    for (auto& item : selected) {
        if (auto* object = freecad_cast<Mesh::Feature*>(item.getObject())) {
            targets.emplace_back(object);
        }
    }
    Gui::Selection().clearSelection();
    QWidget* box = addTaskBoxWithoutHeader(selection);
    box->hide();

    connect(widget, &DlgSmoothing::toggledSelection, box, &QWidget::setVisible);
    App::Document* taskDocument = nullptr;
    if (!selected.empty()) {
        if (auto* object = selected.front().getObject()) {
            taskDocument = object->getDocument();
        }
    }
    else {
        taskDocument = App::GetApplication().getActiveDocument();
    }
    if (taskDocument) {
        setDocumentName(taskDocument->getName());
        setAutoCloseOnDeletedDocument(true);
    }
}

bool TaskSmoothing::accept()
{
    if (targets.empty()) {
        return false;
    }
    std::vector<Mesh::Feature*> meshes;
    meshes.reserve(targets.size());
    for (const auto& target : targets) {
        auto* mesh = target.get<Mesh::Feature>();
        if (!mesh) {
            return false;
        }
        meshes.push_back(mesh);
    }

    App::Document* document = meshes.front()->getDocument();
    if (!MeshGui::hasCleanNativeMutationBoundary(document)
        || std::ranges::any_of(meshes, [document](const Mesh::Feature* object) {
               return !object || object->getDocument() != document
                   || !MeshGui::isNativeMeshInputActive(object);
           })) {
        return false;
    }

    std::vector<MeshGui::BackgroundMeshModificationTarget> operations;
    operations.reserve(meshes.size());
    for (auto* mesh : meshes) {
        std::vector<Mesh::FacetIndex> selectedFacets;
        std::vector<Mesh::PointIndex> selectedPoints;
        if (widget->smoothSelection()) {
            mesh->Mesh.getValue().getFacetsFromSelection(selectedFacets);
            selectedPoints = mesh->Mesh.getValue().getPointsFromFacets(selectedFacets);
            if (selectedPoints.empty()) {
                return false;
            }
        }
        std::vector<long> persistedPoints(selectedPoints.begin(), selectedPoints.end());
        operations.push_back(
            MeshGui::BackgroundMeshModificationTarget {
                mesh,
                "Smooth Mesh",
                std::move(persistedPoints),
                {},
            }
        );
    }

    try {
        const char* method = "taubin";
        if (widget->method() == MeshGui::DlgSmoothing::Laplace) {
            method = "laplace";
        }
        else if (widget->method() == MeshGui::DlgSmoothing::MedianFilter) {
            method = "median";
        }
        std::ostringstream arguments;
        arguments << "{\"settings\":{\"method\":\"" << method
                  << "\",\"iterations\":" << widget->iterations();
        if (widget->method() != MeshGui::DlgSmoothing::MedianFilter) {
            arguments << ",\"lambda\":" << std::setprecision(17) << widget->lambdaStep();
        }
        if (widget->method() == MeshGui::DlgSmoothing::Taubin) {
            arguments << ",\"mu\":" << std::setprecision(17) << widget->microStep();
        }
        arguments << "}}";
        MeshGui::startBackgroundMeshModification(
            operations,
            "smooth",
            arguments.str()
        );
        return true;
    }
    catch (const Py::Exception&) {
        Base::PyException error;
        Base::Console().error("Mesh smoothing failed: %s\n", error.what());
        return false;
    }
    catch (const Base::Exception& error) {
        Base::Console().error("Mesh smoothing failed: %s\n", error.what());
        return false;
    }
}

#include "moc_DlgSmoothing.cpp"
