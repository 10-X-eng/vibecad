/***************************************************************************
 *   Copyright (c) 2020 FreeCAD Developers                                 *
 *   Author: Uwe Stöhr <uwestoehr@lyx.org>                                 *
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

# include <cmath>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Base/Vector3D.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawHatch.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>

#include "TaskHatch.h"
#include "ui_TaskHatch.h"
#include "HatchBuilder.h"
#include "ViewProviderHatch.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;
using DU = DrawUtil;

//ctor for creation
TaskHatch::TaskHatch(TechDraw::DrawViewPart* inDvp, std::vector<std::string> subs) :
    ui(new Ui_TaskHatch),
    m_hatch(nullptr),
    m_vp(nullptr),
    m_dvp(inDvp),
    m_viewIdentity(inDvp),
    m_subs(subs)
{
    if (!inDvp || subs.empty()
        || inDvp->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The image hatch requires selected faces and an owning "
            "drawing transaction"
        );
    }
    ui->setupUi(this);

    connect(ui->fcFile, &FileChooser::fileNameSelected, this, &TaskHatch::onFileChanged);
    connect(ui->sbScale, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskHatch::onScaleChanged);
    connect(ui->ccColor, &ColorButton::changed, this, &TaskHatch::onColorChanged);
    connect(ui->dsbRotation, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskHatch::onRotationChanged);
    connect(ui->dsbOffsetX, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskHatch::onOffsetChanged);
    connect(ui->dsbOffsetY, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskHatch::onOffsetChanged);
    setUiPrimary();
}

//ctor for edit
TaskHatch::TaskHatch(TechDrawGui::ViewProviderHatch* inVp) :
    ui(new Ui_TaskHatch),
    m_vp(inVp)
{
    if (!inVp) {
        throw Base::TypeError(
            "The image-hatch editor requires a live hatch"
        );
    }
    ui->setupUi(this);
    m_hatch = m_vp->getViewObject();
    App::DocumentObject* obj =
        m_hatch ? m_hatch->Source.getValue() : nullptr;
    m_dvp = dynamic_cast<TechDraw::DrawViewPart*>(obj);
    if (!m_hatch || !m_dvp
        || m_hatch->getDocument() != m_dvp->getDocument()
        || m_hatch->getDocument()->getBookedTransactionID()
            == App::NullTransaction) {
        throw Base::RuntimeError(
            "The image hatch has no live source or owning transaction"
        );
    }
    m_hatchIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawHatch>(m_hatch);
    m_viewIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewPart>(m_dvp);

    connect(ui->fcFile, &FileChooser::fileNameSelected, this, &TaskHatch::onFileChanged);
    connect(ui->sbScale, qOverload<double>(&QuantitySpinBox::valueChanged), this, &TaskHatch::onScaleChanged);
    connect(ui->ccColor, &ColorButton::changed, this, &TaskHatch::onColorChanged);
    connect(ui->dsbRotation, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskHatch::onRotationChanged);
    connect(ui->dsbOffsetX, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskHatch::onOffsetChanged);
    connect(ui->dsbOffsetY, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &TaskHatch::onOffsetChanged);

    saveHatchState();
    setUiEdit();
}
TaskHatch::~TaskHatch()
{
}

bool TaskHatch::resolveTargets()
{
    auto* view = m_viewIdentity.resolve();
    auto* hatch = m_hatchIdentity.resolve();
    if (!view || (m_hatch && !hatch)) {
        return false;
    }
    m_dvp = view;
    m_hatch = hatch;
    if (!hatch) {
        m_vp = nullptr;
        return true;
    }
    auto* guiDocument =
        Gui::Application::Instance->getDocument(hatch->getDocument());
    m_vp = guiDocument
        ? dynamic_cast<ViewProviderHatch*>(
              guiDocument->getViewProvider(hatch)
          )
        : nullptr;
    return m_vp != nullptr;
}

void TaskHatch::setUiPrimary()
{
    setWindowTitle(QObject::tr("Create Face Hatch"));
    ui->fcFile->setFileName(QString::fromStdString(DrawHatch::prefSvgHatch()));
    ui->fcFile->setFilter(QStringLiteral(
            "SVG files (*.svg *.SVG);;Bitmap files(*.jpg *.jpeg *.png *.bmp);;All files (*)"));
    ui->sbScale->setValue(1.0);
    ui->sbScale->setSingleStep(0.1);
    ui->ccColor->setColor(TechDraw::DrawHatch::prefSvgHatchColor().asValue<QColor>());
    ui->dsbRotation->setValue(0.0);
}

void TaskHatch::setUiEdit()
{
    setWindowTitle(QObject::tr("Edit Face Hatch"));
    ui->fcFile->setFileName(QString::fromStdString(m_saveFile));
    ui->fcFile->setFilter(QStringLiteral(
            "SVG files (*.svg *.SVG);;Bitmap files(*.jpg *.jpeg *.png *.bmp);;All files (*)"));
    ui->sbScale->setValue(m_saveScale);
    ui->sbScale->setSingleStep(0.1);
    ui->ccColor->setColor(m_saveColor.asValue<QColor>());
    ui->dsbRotation->setValue(m_saveRotation);
    ui->dsbOffsetX->setValue(m_saveOffset.x);
    ui->dsbOffsetY->setValue(m_saveOffset.y);
}

void TaskHatch::saveHatchState()
{
    m_saveFile = m_hatch->HatchPattern.getValue();
    m_saveScale = m_vp->HatchScale.getValue();
    m_saveColor = m_vp->HatchColor.getValue();
    m_saveRotation = m_vp->HatchRotation.getValue();
    m_saveOffset = m_vp->HatchOffset.getValue();

}

//restore the start conditions
void TaskHatch::restoreHatchState()
{
//    Base::Console().message("TH::restoreHatchState()\n");
    if (m_hatch) {
        m_hatch->HatchPattern.setValue(m_saveFile);
        m_vp->HatchScale.setValue(m_saveScale);
        m_vp->HatchColor.setValue(m_saveColor);
        m_vp->HatchRotation.setValue(m_saveRotation);
        m_vp->HatchOffset.setValue(m_saveOffset);
    }
}

void TaskHatch::onFileChanged()
{
    m_file = ui->fcFile->fileName().toStdString();
    apply();
}

void TaskHatch::onScaleChanged()
{
    m_scale = ui->sbScale->value().getValue();
    apply();
}

void TaskHatch::onColorChanged()
{
    m_color.setValue<QColor>(ui->ccColor->color());
    apply();
}

void TaskHatch::onRotationChanged()
{
    m_rotation = ui->dsbRotation->value();
    apply();
}

void TaskHatch::onOffsetChanged()
{
    m_offset.x = ui->dsbOffsetX->value();
    m_offset.y = ui->dsbOffsetY->value();
    apply();
}

void TaskHatch::apply(bool forceUpdate)
{
    Q_UNUSED(forceUpdate)
    if (!resolveTargets()) {
        throw Base::RuntimeError(
            "The image hatch target is no longer available"
        );
    }
    if (!m_hatch) {
        createHatch();
    }
    if (m_hatch) {
        updateHatch();
    }

    if (m_dvp) {
        //only need requestPaint to hatch the face
        //need a recompute in order to claimChildren in tree
        m_dvp->touch();
        m_dvp->getDocument()->recompute();
    }
}

void TaskHatch::createHatch()
{
    App::Document* doc = m_dvp->getDocument();
    auto filespec = ui->fcFile->fileName().toStdString();
    filespec = DU::cleanFilespecBackslash(filespec);
    Base::Color ac;
    ac.setValue<QColor>(ui->ccColor->color());
    const DrawingImageHatchStyle style {
        ui->sbScale->value().getValue(),
        ui->dsbRotation->value(),
        Base::Vector3d(
            ui->dsbOffsetX->value(),
            ui->dsbOffsetY->value(),
            0.0),
        ac};
    m_hatch = createDrawingImageHatch(m_dvp, m_subs, filespec, style);
    doc->publishProvisionalTimelineOperationBlock(m_hatch, {}, {});
    m_hatchIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawHatch>(m_hatch);

    auto* guiDocument = Gui::Application::Instance->getDocument(doc);
    auto* provider =
        guiDocument ? guiDocument->getViewProvider(m_hatch) : nullptr;
    m_vp = dynamic_cast<TechDrawGui::ViewProviderHatch*>(provider);
    if (!m_vp) {
        throw Base::RuntimeError("The image hatch has no compatible view provider");
    }
}

void TaskHatch::updateHatch()
{
    if (!m_hatch || !m_vp) {
        throw Base::RuntimeError(
            "The image hatch is unavailable for update"
        );
    }
    auto filespec = ui->fcFile->fileName().toStdString();
    filespec = DU::cleanFilespecBackslash(filespec);
    const std::string pythonFilespec =
        Base::InterpreterSingleton::strToPython(filespec.c_str());
    const std::string hatchCommand =
        Gui::Command::getObjectCmd(m_hatch);
    Command::doCommand(
        Command::Doc,
        "%s.HatchPattern = '%s'",
        hatchCommand.c_str(),
        pythonFilespec.c_str()
    );

    Base::Color ac;
    ac.setValue<QColor>(ui->ccColor->color());
    m_vp->HatchColor.setValue(ac);
    m_vp->HatchScale.setValue(ui->sbScale->value().getValue());
    m_vp->HatchRotation.setValue(ui->dsbRotation->value());
    Base::Vector3d offset(ui->dsbOffsetX->value(), ui->dsbOffsetY->value(), 0.0);
    m_vp->HatchOffset.setValue(offset);
}

bool TaskHatch::accept()
{
    try {
        apply(true);
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not apply image hatch: %s\n",
            error.what()
        );
        return false;
    }
    if (!m_hatch || !m_vp || m_hatch->isError()) {
        return false;
    }

    TaskInternal::updateExactDocument(m_hatch->getDocument());
    TaskInternal::resetExactEdit(m_hatch->getDocument());

    return true;
}

bool TaskHatch::reject()
{
    TaskInternal::resetExactEdit(m_viewIdentity.resolveDocument());
    return true;
}

void TaskHatch::changeEvent(QEvent *e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgHatch::TaskDlgHatch(TechDraw::DrawViewPart* inDvp, std::vector<std::string> subs) :
    TaskDialog()
{
    widget  = new TaskHatch(inDvp, subs);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("TechDraw_TreeHatch"),
                                         widget->windowTitle(), true, 0);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgHatch::TaskDlgHatch(TechDrawGui::ViewProviderHatch* inVp) :
    TaskDialog()
{
    widget  = new TaskHatch(inVp);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("TechDraw_TreeHatch"),
                                         widget->windowTitle(), true, 0);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    setAutoCloseOnTransactionChange(true);
}

TaskDlgHatch::~TaskDlgHatch()
{
}

void TaskDlgHatch::update()
{
    //widget->updateTask();
}

//==== calls from the TaskView ===============================================================
void TaskDlgHatch::open()
{
}

void TaskDlgHatch::clicked(int i)
{
    Q_UNUSED(i);
}

bool TaskDlgHatch::accept()
{
    return widget->accept();
}

bool TaskDlgHatch::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskHatch.cpp>
