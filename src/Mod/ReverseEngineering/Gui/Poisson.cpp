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


#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/ExactTransaction.h>
#include <Gui/WaitCursor.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Points/App/PointsFeature.h>

#include "OperationSupport.h"
#include "Poisson.h"
#include "ui_Poisson.h"


using namespace ReenGui;

namespace
{

bool poissonSupportAvailable()
{
    Base::PyGILStateLocker lock;
    PyObject* module = PyImport_ImportModule("ReverseEngineering");
    if (!module) {
        PyErr_Clear();
        return false;
    }
    const int available = PyObject_HasAttrString(module, "poissonReconstruction");
    Py_DECREF(module);
    if (available < 0) {
        PyErr_Clear();
        return false;
    }
    return available != 0;
}

}  // namespace

class PoissonWidget::Private
{
public:
    Ui_PoissonWidget ui;
    App::DocumentObjectT obj;
    Private() = default;
    ~Private() = default;
};

/* TRANSLATOR ReenGui::PoissonWidget */

PoissonWidget::PoissonWidget(const App::DocumentObjectT& obj, QWidget* parent)
    : d(new Private())
{
    Q_UNUSED(parent);
    d->ui.setupUi(this);
    d->obj = obj;
}

PoissonWidget::~PoissonWidget()
{
    delete d;
}

bool PoissonWidget::accept()
{
    try {
        auto* source = ReverseEngineeringGui::OperationSupport::usableTaskSource(d->obj);
        auto* pointCloud = freecad_cast<Points::Feature*>(source);
        auto* document = source ? source->getDocument() : nullptr;
        if (!pointCloud || !document || pointCloud->Points.getValue().size() == 0) {
            throw Base::RuntimeError(
                "The original point cloud is no longer available for reconstruction"
            );
        }
        if (!poissonSupportAvailable()) {
            throw Base::RuntimeError(
                "Poisson reconstruction requires a VibeCAD build with PCL Surface support"
            );
        }

        const QString documentPython = QString::fromStdString(d->obj.getDocumentPython());
        const QString objectPython = QString::fromStdString(d->obj.getObjectPython());

        QString argument = QStringLiteral(
                               "Points=%1.Points, "
                               "OctreeDepth=%2, "
                               "SolverDivide=%3, "
                               "SamplesPerNode=%4"
        )
                               .arg(objectPython)
                               .arg(d->ui.octreeDepth->value())
                               .arg(d->ui.solverDivide->value())
                               .arg(d->ui.samplesPerNode->value());
        QString command = QStringLiteral(
                              "%1.addObject(\"Mesh::Feature\", \"Poisson\").Mesh = "
                              "ReverseEngineering.poissonReconstruction(%2)"
        )
                              .arg(documentPython, argument);

        Gui::WaitCursor wc;
        Gui::ExactTransaction mutation(
            *document,
            QT_TRANSLATE_NOOP("Command", "Poisson reconstruction")
        );
        const auto previousIds = ReverseEngineeringGui::OperationSupport::objectIds(*document);
        Gui::Command::addModule(Gui::Command::App, "ReverseEngineering");
        Gui::Command::runCommand(Gui::Command::Doc, command.toLatin1());

        auto created = ReverseEngineeringGui::OperationSupport::createdObjects(*document, previousIds);
        std::vector<Mesh::Feature*> outputs;
        for (auto* object : created) {
            if (auto* mesh = freecad_cast<Mesh::Feature*>(object)) {
                outputs.push_back(mesh);
            }
        }
        if (outputs.size() != 1 || outputs.front()->Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError("Poisson reconstruction did not produce exactly one usable mesh");
        }
        auto* output = outputs.front();
        output->Label.setValue(pointCloud->Label.getStrValue() + " Poisson Surface");
        ReverseEngineeringGui::OperationSupport::setSource(*output, *pointCloud);
        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *document,
            {pointCloud},
            {output},
            "PoissonSurface",
            "Poisson Surface",
            "Poisson reconstruction"
        );
        document->recompute();
        if (output->isError() || output->Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError("The reconstructed Poisson mesh is invalid");
        }
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& e) {
        QMessageBox::warning(this, tr("Poisson Reconstruction"), QString::fromUtf8(e.what()));
        return false;
    }

    return true;
}

void PoissonWidget::changeEvent(QEvent* e)
{
    QWidget::changeEvent(e);
    if (e->type() == QEvent::LanguageChange) {
        d->ui.retranslateUi(this);
    }
}


/* TRANSLATOR ReenGui::TaskPoisson */

TaskPoisson::TaskPoisson(const App::DocumentObjectT& obj)
{
    if (auto* document = obj.getDocument()) {
        setDocumentName(document->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    widget = new PoissonWidget(obj);
    addTaskBox(Gui::BitmapFactory().pixmap("actions/FitSurface"), widget);
}

TaskPoisson::~TaskPoisson() = default;

void TaskPoisson::open()
{}

bool TaskPoisson::accept()
{
    return widget->accept();
}

#include "moc_Poisson.cpp"
