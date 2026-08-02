/***************************************************************************
 *   Copyright (c) 2016 WandererFan <wandererfan@gmail.com>                *
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
# include <QMessageBox>
# include <QTreeWidget>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Exception.h>
#include <Base/Console.h>

#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>

#include "TaskLinkDim.h"
#include "ui_TaskLinkDim.h"


using namespace Gui;
using namespace TechDraw;
using namespace TechDrawGui;


TaskLinkDim::TaskLinkDim(std::vector<App::DocumentObject*> parts, std::vector<std::string>& subs, TechDraw::DrawPage* page) :
    ui(new Ui_TaskLinkDim),
    m_parts(parts),
    m_subs(subs),
    m_page(page),
    m_documentIdentity(page ? page->getDocument() : nullptr),
    m_pageIdentity(page)
{
    if (!page || !page->getDocument() || parts.empty()
        || parts.size() != subs.size()) {
        throw Base::ValueError(
            "Dimension linking requires a page and matching references"
        );
    }
    m_partIdentities.reserve(parts.size());
    for (auto* part : parts) {
        if (!part || part->getDocument() != page->getDocument()) {
            throw Base::ValueError(
                "Dimension references must belong to the drawing document"
            );
        }
        m_partIdentities.emplace_back(part);
    }

    ui->setupUi(this);
    ui->selector->setAvailableLabel(tr("Available"));
    ui->selector->setSelectedLabel(tr("Selected"));

    connect(ui->selector->availableTreeWidget(), &QTreeWidget::currentItemChanged,
            this, &TaskLinkDim::onCurrentItemChanged);
    connect(ui->selector->selectedTreeWidget(), &QTreeWidget::currentItemChanged,
            this, &TaskLinkDim::onCurrentItemChanged);

    loadAvailDims();

    ui->leFeature1->setText(QString::fromStdString(parts.at(0)->getNameInDocument()));
    ui->leGeometry1->setText(QString::fromStdString(subs.at(0)));

    if (subs.size() > 1) {
        ui->leGeometry2->setText(QString::fromStdString(subs.at(1)));
        if (parts.at(0)->getNameInDocument() != parts.at(1)->getNameInDocument()) {
            ui->leFeature2->setText(QString::fromStdString(parts.at(1)->getNameInDocument()));
        } else {
            ui->leFeature2->clear();
        }
    }
}

bool TaskLinkDim::resolveInputs()
{
    auto* page = m_pageIdentity.resolve();
    if (!page || page->getDocument() != m_documentIdentity.resolve()
        || m_partIdentities.size() != m_subs.size()) {
        m_page = nullptr;
        m_parts.clear();
        return false;
    }

    std::vector<App::DocumentObject*> parts;
    parts.reserve(m_partIdentities.size());
    for (const auto& identity : m_partIdentities) {
        auto* part = identity.resolve();
        if (!part || part->getDocument() != page->getDocument()) {
            m_page = nullptr;
            m_parts.clear();
            return false;
        }
        parts.push_back(part);
    }
    m_page = page;
    m_parts = std::move(parts);
    return true;
}

TaskLinkDim::~TaskLinkDim()
{
}

void TaskLinkDim::loadAvailDims()
{
    if (!resolveInputs()) {
        return;
    }
    App::Document* doc = m_page->getDocument();
    Gui::Document* guiDoc = Gui::Application::Instance->getDocument(doc);
    if (!guiDoc)
        return;

    std::string result;
    TechDraw::DrawViewDimension::RefType selRefType = TechDraw::DrawViewDimension::getRefTypeSubElements(m_subs);
    //int found = 0;
    for (auto* view : m_page->getAllActiveViews()) {
        if (view->isDerivedFrom<TechDraw::DrawViewDimension>()) {
            auto* dim = static_cast<TechDraw::DrawViewDimension*>(view);
            TechDraw::DrawViewDimension::RefType dimRefType = dim->getRefType();
            if (dimRefType == selRefType) {                                     //potential matches
    //            found++;
                if (dim->has3DReferences()) {
                    if (dimReferencesSelection(dim))  {
                        loadToTree(dim, true, guiDoc);
                    }
                    else {
                        continue;                                               //already linked to something else
                    }
                }
                else {
                    loadToTree(dim, false, guiDoc);
                }
            }
        }
    }
    //if (found == 0) { "No matching Dimensions found in %s", m_page->getNameInDocument())
}

void TaskLinkDim::loadToTree(const TechDraw::DrawViewDimension* dim, const bool selected, Gui::Document* guiDoc)
{
    QString label = QString::fromUtf8(dim->Label.getValue());
    QString name = QString::fromUtf8(dim->getNameInDocument());
    QString tooltip = label + QStringLiteral(" / ") + name;

    QTreeWidgetItem* child = new QTreeWidgetItem();
    child->setText(0, label);
    child->setToolTip(0, tooltip);
    child->setData(0, Qt::UserRole, name);
    child->setData(
        0,
        Qt::UserRole + 1,
        QVariant::fromValue<qlonglong>(dim->getID())
    );
    Gui::ViewProvider* vp = guiDoc->getViewProvider(dim);
    if (vp) child->setIcon(0, vp->getIcon());
    if (selected) {
        ui->selector->selectedTreeWidget()->addTopLevelItem(child);
    } else {
        ui->selector->availableTreeWidget()->addTopLevelItem(child);
    }
}

//! does this dim already have a reference to the selection?
bool TaskLinkDim::dimReferencesSelection(const TechDraw::DrawViewDimension* dim) const
{
    if (!dim->has3DReferences()) {
        return false;
    }

    std::vector<App::DocumentObject*> refParts = dim->References3D.getValues();
    std::vector<std::string> refSubs = dim->References3D.getSubValues();
    if (refParts.size() != m_parts.size()) {
        return false;
    }

    if(refParts.empty()) {
        //shouldn't happen!
    } else if (refParts.size() == 1) {
        if ((refParts[0] == m_parts[0]) &&
                (refSubs[0] == m_subs[0]) ) {         //everything matches
            return true;
        }
    } else if (refParts.size() == 2) {
        if (( (refParts[0] == m_parts[0]) &&
                (refParts[1] == m_parts[1]) )  &&
            ( (refSubs[0] == m_subs[0])   &&
                (refSubs[1] == m_subs[1]) ) ) {
            return true;
        } else if (( (refParts[0] == m_parts[1]) &&
                        (refParts[1] == m_parts[0]) )  &&
                    ( (refSubs[0] == m_subs[1])   &&
                        (refSubs[1] == m_subs[0]) ) ) {
            return true;
        }
    }

    return false;
}

void TaskLinkDim::updateDims()
{
    if (!resolveInputs()) {
        throw Base::RuntimeError(
            "The dimension-linking target is no longer available"
        );
    }
    App::Document* document = m_documentIdentity.resolve();
    int iDim;
    int count = ui->selector->selectedTreeWidget()->topLevelItemCount();
    for (iDim=0; iDim<count; iDim++) {
        QTreeWidgetItem* child = ui->selector->selectedTreeWidget()->topLevelItem(iDim);
        QString name = child->data(0, Qt::UserRole).toString();
        const long objectId =
            child->data(0, Qt::UserRole + 1).toLongLong();
        App::DocumentObject* obj = document->getObjectByID(objectId);
        if (!obj || !obj->getNameInDocument()
            || name.toStdString() != obj->getNameInDocument()) {
            throw Base::RuntimeError(
                "A selected dimension is no longer available"
            );
        }
        TechDraw::DrawViewDimension* dim = dynamic_cast<TechDraw::DrawViewDimension*>(obj);
        if (!dim || dim->findParentPage() != m_page) {
            throw Base::RuntimeError(
                "A selected dimension no longer belongs to this page"
            );
        }
        dim->References3D.setValues(m_parts, m_subs);
        dim->MeasureType.setValue("True");
        dim->recomputeFeature();
        if (dim->isError()) {
            throw Base::RuntimeError(
                "A selected dimension could not be linked"
            );
        }
    }
    count = ui->selector->availableTreeWidget()->topLevelItemCount();
    for (iDim=0; iDim < count; iDim++) {
        QTreeWidgetItem* child = ui->selector->availableTreeWidget()->topLevelItem(iDim);
        QString name = child->data(0, Qt::UserRole).toString();
        const long objectId =
            child->data(0, Qt::UserRole + 1).toLongLong();
        App::DocumentObject* obj = document->getObjectByID(objectId);
        if (!obj || !obj->getNameInDocument()
            || name.toStdString() != obj->getNameInDocument()) {
            throw Base::RuntimeError(
                "An available dimension is no longer available"
            );
        }
        TechDraw::DrawViewDimension* dim = dynamic_cast<TechDraw::DrawViewDimension*>(obj);
        if (dim && dimReferencesSelection(dim))  {
           dim->MeasureType.setValue("Projected");
           dim->References3D.setValue(nullptr, "");            //DVD.References3D
           dim->clear3DMeasurements();                  //DVD.measurement.References3D
           dim->recomputeFeature();
           if (dim->isError()) {
               throw Base::RuntimeError(
                   "A dimension could not be unlinked"
               );
           }
        }
    }
}

void TaskLinkDim::onCurrentItemChanged(QTreeWidgetItem* current, QTreeWidgetItem* previous)
{
    Q_UNUSED(current);
    Q_UNUSED(previous);
//    if (previous) {
//        Base::Console().message("TRACE - TLD::onCurrent - text: %s data: %s is previous\n",
//                                qPrintable(previous->text(0)), qPrintable(previous->data(0, Qt::UserRole).toString()));
//        if (previous->treeWidget() == ui->selector->selectedTreeWidget()) {
//            Base::Console().message("TRACE - TLD::onCurrent - previous belongs to selected\n");
//        }
//        if (previous->treeWidget() == ui->selector->availableTreeWidget()) {
//            Base::Console().message("TRACE - TLD::onCurrent - previous belongs to available\n");
//        }
//    }
//    if (current) {
//        Base::Console().message("TRACE - TLD::onCurrent - text: %s data: %s is current\n",
//                                 qPrintable(current->text(0)), qPrintable(current->data(0, Qt::UserRole).toString()));
//        if (current->treeWidget() == ui->selector->selectedTreeWidget()) {
//            Base::Console().message("TRACE - TLD::onCurrent - current belongs to selected\n");
//        }
//        if (current->treeWidget() == ui->selector->availableTreeWidget()) {
//            Base::Console().message("TRACE - TLD::onCurrent - current belongs to available\n");
//        }
//    }
}

bool TaskLinkDim::accept()
{
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            m_documentIdentity.resolve(),
            QT_TRANSLATE_NOOP("Command", "Link dimensions")
        );
        updateDims();
        TaskInternal::updateExactDocument(
            m_documentIdentity.resolve()
        );
        transaction.commit();
        return true;
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            this,
            tr("Link dimensions"),
            QString::fromUtf8(error.what())
        );
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            this,
            tr("Link dimensions"),
            QString::fromUtf8(error.what())
        );
    }
    return false;
}

bool TaskLinkDim::reject()
{
    return true;
}

void TaskLinkDim::changeEvent(QEvent *event)
{
    if (event->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
}


/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
TaskDlgLinkDim::TaskDlgLinkDim(std::vector<App::DocumentObject*> parts, std::vector<std::string>& subs, TechDraw::DrawPage* page) :
    TaskDialog()
{
    widget  = new TaskLinkDim(parts, subs, page);
    taskbox = new Gui::TaskView::TaskBox(Gui::BitmapFactory().pixmap("TechDraw_LinkDimension"),
                                         widget->windowTitle(), true, nullptr);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
}

TaskDlgLinkDim::~TaskDlgLinkDim()
{
}

void TaskDlgLinkDim::update()
{
    //widget->updateTask();
}

//==== calls from the TaskView ===============================================================
void TaskDlgLinkDim::open()
{
}

void TaskDlgLinkDim::clicked(int i)
{
    Q_UNUSED(i);
}

bool TaskDlgLinkDim::accept()
{
    return widget->accept();
}

bool TaskDlgLinkDim::reject()
{
    return widget->reject();
}

#include <Mod/TechDraw/Gui/moc_TaskLinkDim.cpp>
