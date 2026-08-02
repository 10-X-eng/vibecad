/***************************************************************************
 *   Copyright (c) 2009 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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
//this file originally part of TechDraw workbench
//migrated to TechDraw workbench 2022-01-26 by Wandererfan

# include <QMessageBox>

#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/TechDraw/App/FeatureProjection.h>

#include "CommandHelpers.h"
#include "TaskProjection.h"
#include "ui_TaskProjection.h"


using namespace TechDrawGui;

/* TRANSLATOR TechDrawGui::TaskProjection */

TaskProjection::TaskProjection()
    : TaskProjection(
          App::GetApplication().getActiveDocument(),
          Gui::Selection().getObjectsOfType<Part::Feature>()
      )
{}

TaskProjection::TaskProjection(
    App::Document* document,
    const std::vector<Part::Feature*>& shapes
)
    : ui(new Ui_TaskProjection)
    , m_documentIdentity(document)
{
    ui->setupUi(this);
    m_shapeIdentities.reserve(shapes.size());
    for (auto* shape : shapes) {
        if (shape && shape->getDocument() == document) {
            m_shapeIdentities.emplace_back(shape);
        }
    }
}

TaskProjection::~TaskProjection()
{
    // automatically deleted in the sub-class
}

bool TaskProjection::accept()
{
    auto* appDocument = m_documentIdentity.resolve();
    Gui::Document* guiDocument =
        m_documentIdentity.guiDocument();
    if (!appDocument || !guiDocument) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Document Closed"),
            tr("The document selected for projection is no longer open.")
        );
        return false;
    }
    std::list<Gui::MDIView*> mdis =
        guiDocument->getMDIViewsOfType(
            Gui::View3DInventor::getClassTypeId()
        );
    if (mdis.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("No 3D View"),
            tr("Open a 3D view for this document before projecting shapes.")
        );
        return false;
    }

    auto* view3D =
        dynamic_cast<Gui::View3DInventor*>(mdis.front());
    auto* viewer = view3D ? view3D->getViewer() : nullptr;
    if (!viewer) {
        return false;
    }
    SbVec3f pnt, dir;
    viewer->getNearPlane(pnt, dir);
    float x=0, y=1, z=1;
    dir.getValue(x, y,z);

    std::vector<Part::Feature*> shapes;
    shapes.reserve(m_shapeIdentities.size());
    for (const auto& identity : m_shapeIdentities) {
        auto* shape = identity.resolve();
        if (!shape) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                tr("Selection Changed"),
                tr(
                    "A shape selected for projection is no longer "
                    "available."
                )
            );
            return false;
        }
        shapes.push_back(shape);
    }
    if (shapes.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("No Shapes"),
            tr("Select at least one solid or surface to project.")
        );
        return false;
    }

    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            appDocument,
            "Project shape"
        );
        Gui::Command::addModule(Gui::Command::Doc, "TechDraw");
        std::vector<App::DocumentObject*> createdProjections;
        createdProjections.reserve(shapes.size());
        for (auto* shape : shapes) {
            const std::string projectionName =
                appDocument->getUniqueObjectName(
                    (std::string(shape->getNameInDocument())
                     + "_proj")
                        .c_str()
                );
            const std::string documentName =
                Base::InterpreterSingleton::strToPython(
                    appDocument->getName()
                );
            const QString projectionFactory =
                QStringLiteral(
                    "App.getDocument('%1').addObject"
                    "('TechDraw::FeatureProjection', '%2')"
                )
                    .arg(
                        QString::fromStdString(documentName),
                        QString::fromStdString(projectionName)
                    );
            auto* projection =
                dynamic_cast<TechDraw::FeatureProjection*>(
                    Gui::Command::runDocumentObjectCommand(
                        Gui::Command::Doc,
                        *appDocument,
                        projectionFactory.toUtf8(),
                        TechDraw::FeatureProjection::getClassTypeId()
                    )
                );
            if (!projection
                || projection->getDocument() != appDocument
                || !appDocument->containsObject(projection)) {
                throw Base::RuntimeError(
                    "The projected-shape factory returned an invalid object"
                );
            }
            createdProjections.push_back(projection);
            projection->Direction.setValue(
                Base::Vector3d(x, y, z)
            );
            projection->Source.setValue(shape);
            projection->VCompound.setValue(
                ui->cbVisSharp->isChecked()
            );
            projection->Rg1LineVCompound.setValue(
                ui->cbVisSmooth->isChecked()
            );
            projection->RgNLineVCompound.setValue(
                ui->cbVisSewn->isChecked()
            );
            projection->OutLineVCompound.setValue(
                ui->cbVisOutline->isChecked()
            );
            projection->IsoLineVCompound.setValue(
                ui->cbVisIso->isChecked()
            );
            projection->HCompound.setValue(
                ui->cbHidSharp->isChecked()
            );
            projection->Rg1LineHCompound.setValue(
                ui->cbHidSmooth->isChecked()
            );
            projection->RgNLineHCompound.setValue(
                ui->cbHidSewn->isChecked()
            );
            projection->OutLineHCompound.setValue(
                ui->cbHidOutline->isChecked()
            );
            projection->IsoLineHCompound.setValue(
                ui->cbHidIso->isChecked()
            );
            projection->recomputeFeature();
            if (projection->isError()) {
                throw Base::RuntimeError(
                    "A projected shape could not produce valid geometry"
                );
            }
        }
        TechDraw::CommandHelpers::groupTimelineOutputs(
            appDocument,
            createdProjections,
            "ProjectedShapes",
            QT_TRANSLATE_NOOP("Command", "Projected Shapes")
        );
        TaskInternal::updateExactDocument(appDocument);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::critical(
            Gui::getMainWindow(),
            tr("Projection Failed"),
            QString::fromUtf8(error.what())
        );
        return false;
    }
    return true;
}

bool TaskProjection::reject()
{
    return true;
}


///////////////////////////////////////////////////////////////////////////

TaskDlgProjection::TaskDlgProjection() :
    TaskDialog()
{
    widget  = new TaskProjection();
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_ProjectShape"), widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgProjection::TaskDlgProjection(
    App::Document* document,
    const std::vector<Part::Feature*>& shapes
)
    : TaskDialog()
{
    widget = new TaskProjection(document, shapes);
    taskbox = new Gui::TaskView::TaskBox(
        Gui::BitmapFactory().pixmap(
            "actions/TechDraw_ProjectShape"
        ),
        widget->windowTitle(),
        true,
        nullptr
    );
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
    if (document) {
        setDocumentName(document->getName());
    }
}

TaskDlgProjection::~TaskDlgProjection()
{
}

void TaskDlgProjection::update()
{
}

//==== calls from the TaskView ===============================================================
void TaskDlgProjection::open()
{
}

void TaskDlgProjection::clicked(int i)
{
    Q_UNUSED(i);
}

bool TaskDlgProjection::accept()
{
    return widget->accept();
}

bool TaskDlgProjection::reject()
{
    return widget->reject();
}

#include "moc_TaskProjection.cpp"
