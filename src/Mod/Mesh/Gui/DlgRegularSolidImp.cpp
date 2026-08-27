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


#include <qmessagebox.h>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Base/PyObjectBase.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/DocumentObserver.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MDIView.h>
#include <Gui/WaitCursor.h>
#include <Mod/Mesh/App/FeatureMeshSolid.h>

#include "DlgRegularSolidImp.h"
#include "CommandGuard.h"
#include "ParametricMeshFilter.h"
#include "ui_DlgRegularSolid.h"


using namespace MeshGui;

class DlgRegularSolidImp::LaunchState
{
public:
    explicit LaunchState(Gui::Document* document)
        : document(document)
    {}

    Gui::DocumentWeakPtrT document;
};

/* TRANSLATOR MeshGui::DlgRegularSolidImp */

// clang-format off
DlgRegularSolidImp::DlgRegularSolidImp(QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
    , launchState(std::make_unique<LaunchState>(
          Gui::Application::Instance->activeDocument()
      ))
    , ui(new Ui_DlgRegularSolid)
{
    ui->setupUi(this);
    connect(ui->createSolidButton, &QPushButton::clicked,
            this, &DlgRegularSolidImp::onCreateSolidButtonClicked);
    Gui::Command::doCommand(Gui::Command::Doc, "import Mesh,BuildRegularGeoms");

    // set limits
    constexpr double doubleMax = std::numeric_limits<double>::max();
    // Box
    ui->boxLength->setMaximum(doubleMax);
    ui->boxLength->setMinimum(0);
    ui->boxWidth->setMaximum(doubleMax);
    ui->boxWidth->setMinimum(0);
    ui->boxHeight->setMaximum(doubleMax);
    ui->boxHeight->setMinimum(0);
    // Cylinder
    ui->cylinderRadius->setMaximum(doubleMax);
    ui->cylinderRadius->setMinimum(0);
    ui->cylinderLength->setMaximum(doubleMax);
    ui->cylinderLength->setMinimum(0);
    ui->cylinderEdgeLength->setMaximum(doubleMax);
    ui->cylinderEdgeLength->setMinimum(0);
    ui->cylinderCount->setMaximum(1000);
    // Cone
    ui->coneRadius1->setMaximum(doubleMax);
    ui->coneRadius1->setMinimum(0);
    ui->coneRadius2->setMaximum(doubleMax);
    ui->coneRadius2->setMinimum(0);
    ui->coneLength->setMaximum(doubleMax);
    ui->coneLength->setMinimum(0);
    ui->coneEdgeLength->setMaximum(doubleMax);
    ui->coneEdgeLength->setMinimum(0);
    ui->coneCount->setMaximum(1000);
    // Sphere
    ui->sphereRadius->setMaximum(doubleMax);
    ui->sphereRadius->setMinimum(0);
    ui->sphereCount->setMaximum(1000);
    // Ellipsoid
    ui->ellipsoidRadius1->setMaximum(doubleMax);
    ui->ellipsoidRadius1->setMinimum(0);
    ui->ellipsoidRadius2->setMaximum(doubleMax);
    ui->ellipsoidRadius2->setMinimum(0);
    ui->ellipsoidCount->setMaximum(1000);
    // Torus
    ui->toroidRadius1->setMaximum(doubleMax);
    ui->toroidRadius1->setMinimum(0);
    ui->toroidRadius2->setMaximum(doubleMax);
    ui->toroidRadius2->setMinimum(0);
    ui->toroidCount->setMaximum(1000);
}

/**
 *  Destroys the object and frees any allocated resources
 */
DlgRegularSolidImp::~DlgRegularSolidImp() = default;

void DlgRegularSolidImp::changeEvent(QEvent *e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    QDialog::changeEvent(e);
}

/**
 * Builds a mesh solid from the currently active solid type.
 */
void DlgRegularSolidImp::onCreateSolidButtonClicked()
{
    try {
        Gui::WaitCursor wc;
        std::string name;
        Gui::Document* guiDocument =
            launchState ? *launchState->document : nullptr;
        App::Document* doc = guiDocument
            ? guiDocument->getDocument()
            : nullptr;
        if (!doc) {
            QMessageBox::warning(
                this,
                tr("Create %1").arg(ui->comboBox1->currentText()),
                tr("The document used to open this dialog is no longer available.")
            );
            return;
        }
        if (!MeshGui::hasCleanNativeMutationBoundary(doc)
            || Gui::Control().activeDialog()) {
            QMessageBox::warning(
                this,
                tr("Create %1").arg(ui->comboBox1->currentText()),
                tr("Finish the current operation before creating a mesh.")
            );
            return;
        }

        QString solid =
            tr("Create %1").arg(ui->comboBox1->currentText());
        Gui::ExactTransaction mutation(
            *doc,
            solid.toUtf8().constData()
        );
        Mesh::Feature* result = nullptr;
        switch (ui->comboBox1->currentIndex()) {
            case 0: {
                name = doc->getUniqueObjectName("Cube");
                auto* cube = doc->addObject<Mesh::Cube>(name.c_str());
                cube->Length.setValue(ui->boxLength->value().getValue());
                cube->Width.setValue(ui->boxWidth->value().getValue());
                cube->Height.setValue(ui->boxHeight->value().getValue());
                result = cube;
                break;
            }
            case 1: {
                name = doc->getUniqueObjectName("Cylinder");
                auto* cylinder =
                    doc->addObject<Mesh::Cylinder>(name.c_str());
                cylinder->Radius.setValue(
                    ui->cylinderRadius->value().getValue()
                );
                cylinder->Length.setValue(
                    ui->cylinderLength->value().getValue()
                );
                cylinder->EdgeLength.setValue(
                    ui->cylinderEdgeLength->value().getValue()
                );
                cylinder->Closed.setValue(
                    ui->cylinderClosed->isChecked()
                );
                cylinder->Sampling.setValue(
                    ui->cylinderCount->value()
                );
                result = cylinder;
                break;
            }
            case 2: {
                name = doc->getUniqueObjectName("Cone");
                auto* cone = doc->addObject<Mesh::Cone>(name.c_str());
                cone->Radius1.setValue(
                    ui->coneRadius1->value().getValue()
                );
                cone->Radius2.setValue(
                    ui->coneRadius2->value().getValue()
                );
                cone->Length.setValue(
                    ui->coneLength->value().getValue()
                );
                cone->EdgeLength.setValue(
                    ui->coneEdgeLength->value().getValue()
                );
                cone->Closed.setValue(ui->coneClosed->isChecked());
                cone->Sampling.setValue(ui->coneCount->value());
                result = cone;
                break;
            }
            case 3: {
                name = doc->getUniqueObjectName("Sphere");
                auto* sphere =
                    doc->addObject<Mesh::Sphere>(name.c_str());
                sphere->Radius.setValue(
                    ui->sphereRadius->value().getValue()
                );
                sphere->Sampling.setValue(ui->sphereCount->value());
                result = sphere;
                break;
            }
            case 4: {
                name = doc->getUniqueObjectName("Ellipsoid");
                auto* ellipsoid =
                    doc->addObject<Mesh::Ellipsoid>(name.c_str());
                ellipsoid->Radius1.setValue(
                    ui->ellipsoidRadius1->value().getValue()
                );
                ellipsoid->Radius2.setValue(
                    ui->ellipsoidRadius2->value().getValue()
                );
                ellipsoid->Sampling.setValue(
                    ui->ellipsoidCount->value()
                );
                result = ellipsoid;
                break;
            }
            case 5: {
                name = doc->getUniqueObjectName("Torus");
                auto* torus =
                    doc->addObject<Mesh::Torus>(name.c_str());
                torus->Radius1.setValue(
                    ui->toroidRadius1->value().getValue()
                );
                torus->Radius2.setValue(
                    ui->toroidRadius2->value().getValue()
                );
                torus->Sampling.setValue(ui->toroidCount->value());
                result = torus;
                break;
            }
            default:
                throw Base::RuntimeError(
                    "Unknown regular mesh type"
                );
        }

        doc->recompute();
        if (!result
            || result->Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError(
                "The regular solid did not produce a usable mesh"
            );
        }
        MeshGui::ensureMeshesGroup(*doc);
        if (!mutation.commit()) {
            throw Base::RuntimeError(
                "The mesh could not be committed"
            );
        }
        if (auto* view = guiDocument->getActiveView()) {
            view->onMsg("ViewFit");
        }
    }
    catch (const Base::PyException& e) {
        QMessageBox::warning(this, tr("Create %1").arg(ui->comboBox1->currentText()),
            QString::fromLatin1(e.what()));
    }
    catch (const Base::Exception& e) {
        QMessageBox::warning(
            this,
            tr("Create %1").arg(ui->comboBox1->currentText()),
            QString::fromUtf8(e.what())
        );
    }
    catch (...) {
        QMessageBox::warning(
            this,
            tr("Create %1").arg(ui->comboBox1->currentText()),
            tr("The mesh could not be created.")
        );
    }
}
// clang-format on

#include "moc_DlgRegularSolidImp.cpp"
