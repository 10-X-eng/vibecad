/***************************************************************************
 *   Copyright (c) 2019 WandererFan <wandererfan@gmail.com>                *
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

# include <regex>

# include <QMessageBox>
# include <QPushButton>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Console.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/View3DInventor.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawViewImage.h>
#include <Mod/TechDraw/App/DrawUtil.h>

#include "TaskActiveView.h"
#include "ui_TaskActiveView.h"
#include "Grabber3d.h"
#include "ViewProviderImage.h"
#include "Rez.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;
using DU = DrawUtil;

constexpr int SXGAWidth{1280};
constexpr int SXGAHeight{1024};

// ctor for creation
TaskActiveView::TaskActiveView(TechDraw::DrawPage* pageFeat)
    : ui(new Ui_TaskActiveView)
    , m_pageFeat(pageFeat)
    , m_imageFeat(nullptr)
    , m_previewImageFeat(nullptr)
    , m_pageIdentity(pageFeat)
    , m_btnOK(nullptr)
    , m_btnCancel(nullptr)
{
    ui->setupUi(this);

    ui->qsbWidth->setUnit(Base::Unit::Length);
    ui->qsbHeight->setUnit(Base::Unit::Length);

    setUiPrimary();

    if (!pageFeat || !pageFeat->getDocument()) {
        throw Base::RuntimeError(
            "The active-view task requires a live drawing page"
        );
    }

    // Command activation opens the exact transaction before this constructor
    // creates provisional geometry. TaskView adopts that same transaction
    // when the panel is attached to the page document.
    if (pageFeat->getDocument()->getBookedTransactionID()
        == App::NullTransaction) {
        throw Base::RuntimeError(
            "The active-view task has no owning transaction"
        );
    }

    m_previewImageFeat = createActiveView();
    if (!m_previewImageFeat) {
        throw Base::RuntimeError(
            "The active-view image could not be created"
        );
    }
    m_previewIdentity =
        TaskInternal::ObjectIdentity<TechDraw::DrawViewImage>(
            m_previewImageFeat
        );

    connect(ui->gbFraming, &QGroupBox::toggled, this, &TaskActiveView::onCropChanged);

    connect(ui->qsbWidth, &Gui::QuantitySpinBox::editingFinished, this, &TaskActiveView::updatePreview);
    connect(ui->qsbHeight, &Gui::QuantitySpinBox::editingFinished, this, &TaskActiveView::updatePreview);

    connect(ui->cbBg, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &TaskActiveView::onBgTypeChanged);
    connect(ui->cbBg, QOverload<int>::of(&QComboBox::currentIndexChanged), this, &TaskActiveView::updatePreview);
    connect(ui->ccBgColor, &Gui::ColorButton::changed, this, &TaskActiveView::updatePreview);

    updatePreview();
}

TaskActiveView::~TaskActiveView()
{
}

bool TaskActiveView::accept()
{
    auto* preview = m_previewIdentity.resolve();
    if (!preview) {
        return false;
    }
    preview->recomputeFeature();
    if (preview->isError()) {
        return false;
    }
    m_imageFeat = preview;
    return true;
}

bool TaskActiveView::reject()
{
    // TaskView performs the exact rollback after this widget is torn down.
    return true;
}

void TaskActiveView::updatePreview()
{
    auto* page = m_pageIdentity.resolve();
    auto* preview = m_previewIdentity.resolve();
    if (!page || !preview) {
        return;
    }

    View3DInventor* view3d = qobject_cast<View3DInventor*>(Gui::getMainWindow()->activeWindow());
    if (!view3d) {
        Gui::Document* pageGuiDocument =
            Gui::Application::Instance->getDocument(page->getDocument());
        auto views3dAll = pageGuiDocument->getMDIViewsOfType(Gui::View3DInventor::getClassTypeId());
        if (!views3dAll.empty()) {
            view3d = qobject_cast<View3DInventor*>(views3dAll.front());
        } else {
             auto mdiWindows = Gui::getMainWindow()->windows();
             for (auto& mdi : mdiWindows) {
                 auto mdiView = qobject_cast<View3DInventor*>(mdi);
                 if (mdiView) {
                     view3d = mdiView;
                     break;
                 }
             }
        }
    }
    if (!view3d) {
        Base::Console().warning("TaskActiveView::updatePreview - No 3D View found.\n");
        return;
    }

    App::Document* doc = preview->getDocument();
    std::string pageName = page->getNameInDocument();
    std::string imageName = preview->getNameInDocument();

    std::string baseName = pageName + imageName;
    std::string tempName =
        Base::FileInfo::getTempFileName(baseName.c_str(), doc->TransientDir.getValue()) + ".png";

    QColor bg;
    auto bgType = static_cast<BackgroundType>(ui->cbBg->currentIndex());

    switch (bgType) {
        case BackgroundType::Transparent:
            bg = QColor(Qt::transparent);
            break;
        case BackgroundType::Solid:
            bg = ui->ccBgColor->color();
            break;
        case BackgroundType::View3D:
            bg = QColor();
            break;
    }

    int imageWidth{SXGAWidth};
    int imageHeight{SXGAHeight};
    if (ui->gbFraming->isChecked()) {
        imageWidth = Rez::guiX(ui->qsbWidth->rawValue());
        imageHeight = Rez::guiX(ui->qsbHeight->rawValue());
    }

    QImage image(imageWidth, imageHeight, QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    Grabber3d::quickView(view3d, bg, image);
    if (!image.save(QString::fromStdString(tempName), "PNG")) {
         Base::Console().error("ActiveView could not save file: %s\n", tempName.c_str());
    }

    tempName = DU::cleanFilespecBackslash(tempName);
    preview->ImageFile.setValue(tempName);
    preview->Width.setValue(ui->qsbWidth->rawValue());
    preview->Height.setValue(ui->qsbHeight->rawValue());

    if (auto* guiDoc = Gui::Application::Instance->getDocument(doc)) {
        if (auto* vp = guiDoc->getViewProvider(preview)) {
            if (auto* vpImage = freecad_cast<ViewProviderImage*>(vp)) {
                vpImage->Crop.setValue(ui->gbFraming->isChecked());
            }
        }
    }

    preview->recomputeFeature();
}

void TaskActiveView::saveButtons(QPushButton* btnOK, QPushButton* btnCancel)
{
    m_btnOK = btnOK;
    m_btnCancel = btnCancel;
}

void TaskActiveView::enableTaskButtons(bool b)
{
    m_btnOK->setEnabled(b);
    m_btnCancel->setEnabled(b);
}

void TaskActiveView::blockButtons(bool b) { Q_UNUSED(b); }

// Slots
void TaskActiveView::onCropChanged()
{
    enableCrop(ui->gbFraming->isChecked());
    updatePreview();
}

// Private helper methods
void TaskActiveView::setUiPrimary()
{
    setWindowTitle(QObject::tr("Insert Active View"));
    ui->gbFraming->setChecked(false);
    enableCrop(false);
    
    ui->cbBg->setCurrentIndex(static_cast<int>(BackgroundType::Transparent));
    onBgTypeChanged(static_cast<int>(BackgroundType::Transparent)); 

    ui->qsbWidth->setValue(Rez::appX(SXGAWidth));
    ui->qsbHeight->setValue(Rez::appX(SXGAHeight));
}

void TaskActiveView::onBgTypeChanged(int index)
{
    auto bgType = static_cast<BackgroundType>(index);
    bool isSolid = (bgType == BackgroundType::Solid);
    
    ui->ccBgColor->setEnabled(isSolid);
    ui->lColor->setEnabled(isSolid);
}

void TaskActiveView::enableCrop(bool state)
{
    ui->qsbHeight->setEnabled(state);
    ui->qsbWidth->setEnabled(state);
    ui->lWidth->setEnabled(state);
    ui->lHeight->setEnabled(state);
}

TechDraw::DrawViewImage* TaskActiveView::createActiveView()
{
    auto* page = m_pageIdentity.resolve();
    if (!page) {
        return nullptr;
    }
    View3DInventor* view3d = qobject_cast<View3DInventor*>(Gui::getMainWindow()->activeWindow());
    if (!view3d) {
        // Fallback 1: Try to find a 3D view in the page's document
        Gui::Document* pageGuiDocument =
            Gui::Application::Instance->getDocument(page->getDocument());
        if (pageGuiDocument) {
            auto views3dAll = pageGuiDocument->getMDIViewsOfType(Gui::View3DInventor::getClassTypeId());
            if (!views3dAll.empty()) {
                view3d = qobject_cast<View3DInventor*>(views3dAll.front());
            }
        }
    }
    if (!view3d) {
        // This check is simplified as the more complex fallback is in updatePreview
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("No 3D Viewer"),
                             QObject::tr("Can not find a 3D viewer"));
        return nullptr;
    }

    App::Document* pageDocument = page->getDocument();
    const std::string objectName{"ActiveView"};
    const std::string imageType = "TechDraw::DrawViewImage";

    std::string sObjName = pageDocument->getUniqueObjectName(objectName.c_str());

    auto* newObj = pageDocument->addObject(
        imageType.c_str(),
        sObjName.c_str()
    );
    if (!newObj
        || !newObj->isDerivedFrom<TechDraw::DrawViewImage>()
        || newObj->getDocument() != pageDocument
        || !pageDocument->containsObject(newObj)) {
        throw Base::RuntimeError(
            "The active-view image factory returned an invalid object"
        );
    }

    page->addView(newObj);
    newObj->Label.setValue("ActiveView");

    return static_cast<TechDraw::DrawViewImage*>(newObj);
}

void TaskActiveView::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}

void TaskActiveView::updateTask()
{
}

TaskDlgActiveView::TaskDlgActiveView(TechDraw::DrawPage* page) : TaskDialog()
{
    widget = new TaskActiveView(page);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("actions/TechDraw_ActiveView"),
                                         widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
}

TaskDlgActiveView::~TaskDlgActiveView() {}

bool TaskDlgActiveView::accept()
{
    return widget->accept();
}

bool TaskDlgActiveView::reject()
{
    widget->reject();
    return true;
}

void TaskDlgActiveView::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* btnOK = box->button(QDialogButtonBox::Ok);
    QPushButton* btnCancel = box->button(QDialogButtonBox::Cancel);
    widget->saveButtons(btnOK, btnCancel);
}

void TaskDlgActiveView::open() {}

void TaskDlgActiveView::clicked(int) {}

void TaskDlgActiveView::update() {}


#include <Mod/TechDraw/Gui/moc_TaskActiveView.cpp>
