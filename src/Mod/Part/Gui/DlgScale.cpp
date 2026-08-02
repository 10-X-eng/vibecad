// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2023 Wanderer Fan <wandererfan@gmail.com>               *
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

#include <BRepAdaptor_Curve.hxx>
#include <BRep_Tool.hxx>
#include <Precision.hxx>
#include <ShapeExtend_Explorer.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopTools_HSequenceOfShape.hxx>
#include <QKeyEvent>
#include <QMessageBox>
#include <QTreeWidget>
#include <QTreeWidgetItem>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/Link.h>
#include <App/Part.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/Utilities.h>
#include <Gui/ViewProvider.h>
#include <Gui/WaitCursor.h>

#include <Mod/Part/App/FeatureScale.h>

#include "ui_DlgScale.h"
#include "DlgScale.h"
#include "ModelingSelection.h"


FC_LOG_LEVEL_INIT("Part", true, true)

using namespace PartGui;

namespace
{
constexpr int sourceNameRole = Qt::UserRole;
constexpr int sourceIdRole = Qt::UserRole + 1;
constexpr int sourceAddressRole = Qt::UserRole + 2;

App::Document* resolveRetainedTaskDocument(
    const std::string& name,
    App::Document* address,
    const std::string& uid
) noexcept
{
    if (name.empty() || !address || uid.empty()) {
        return nullptr;
    }
    try {
        auto* document = App::GetApplication().getDocument(name.c_str());
        return document == address && document->Uid.getValueStr() == uid ? document : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

App::DocumentObject* resolveRetainedTaskSource(
    App::Document& document,
    const QTreeWidgetItem& item
) noexcept
{
    try {
        const auto name = item.data(0, sourceNameRole).toString().toLatin1();
        const long id = item.data(0, sourceIdRole).toLongLong();
        const auto address = reinterpret_cast<App::DocumentObject*>(
            static_cast<quintptr>(item.data(0, sourceAddressRole).toULongLong())
        );
        auto* object = id >= 0 ? document.getObjectByID(id) : nullptr;
        return object && object == address && object->getID() == id
                && document.containsObject(object) && object->getNameInDocument()
                && name == object->getNameInDocument()
                && document.getObject(name.constData()) == object
                && PartGui::isModelingObjectActive(object)
            ? object
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}
}

DlgScale::DlgScale(QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
    , ui(new Ui_DlgScale)
{
    ui->setupUi(this);
    setupConnections();

    ui->dsbUniformScale->setDecimals(Base::UnitsApi::getDecimals());
    ui->dsbXScale->setDecimals(Base::UnitsApi::getDecimals());
    ui->dsbYScale->setDecimals(Base::UnitsApi::getDecimals());
    ui->dsbZScale->setDecimals(Base::UnitsApi::getDecimals());
    findShapes();

    // this will mark as selected all the items in treeWidget that are selected in the document
    Gui::ItemViewSelection sel(ui->treeWidget);
    auto modelingSelection =
        PartGui::getModelingSelection(m_document.c_str());
    std::vector<App::DocumentObject*> selectedObjects;
    for (auto& selected : modelingSelection) {
        selectedObjects.push_back(selected.getObject());
    }
    sel.applyFrom(selectedObjects);
}

void DlgScale::setupConnections()
{
    connect(ui->rbUniform, &QRadioButton::toggled, this, &DlgScale::onUniformScaleToggled);
}

void DlgScale::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    QDialog::changeEvent(e);
}

void DlgScale::onUniformScaleToggled(bool state)
{
    //    Base::Console().message("DS::onUniformScaleToggled()\n");
    if (state) {
        // this is uniform scaling, so hide the non-uniform input fields
        ui->dsbUniformScale->setEnabled(true);
        ui->dsbXScale->setEnabled(false);
        ui->dsbYScale->setEnabled(false);
        ui->dsbZScale->setEnabled(false);
    }
    else {
        // this is non-uniform scaling, so hide the uniform input fields
        ui->dsbUniformScale->setEnabled(false);
        ui->dsbXScale->setEnabled(true);
        ui->dsbYScale->setEnabled(true);
        ui->dsbZScale->setEnabled(true);
    }
}

//! find all the scalable objects in the active document and load them into the
//! list widget
void DlgScale::findShapes()
{
    //    Base::Console().message("DS::findShapes()\n");
    App::Document* activeDoc = App::GetApplication().getActiveDocument();
    if (!activeDoc) {
        return;
    }
    Gui::Document* activeGui = Gui::Application::Instance->getDocument(activeDoc);
    m_document = activeDoc->getName();
    m_label = activeDoc->Label.getValue();
    documentAddress = activeDoc;
    documentUid = activeDoc->Uid.getValueStr();

    const auto objs = PartGui::resolveModelingObjects(
        activeDoc->getObjectsOfType<App::DocumentObject>()
    );

    for (auto obj : objs) {
        Part::TopoShape topoShape = Part::Feature::getTopoShape(
            obj,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
        );
        if (topoShape.isNull()) {
            continue;
        }
        TopoDS_Shape shape = topoShape.getShape();
        if (shape.IsNull()) {
            continue;
        }
        if (canScale(shape)) {
            QTreeWidgetItem* item = new QTreeWidgetItem(ui->treeWidget);
            item->setText(0, QString::fromUtf8(obj->Label.getValue()));
            item->setData(0, sourceNameRole, QString::fromLatin1(obj->getNameInDocument()));
            item->setData(0, sourceIdRole, QVariant::fromValue<qlonglong>(obj->getID()));
            item->setData(
                0,
                sourceAddressRole,
                QVariant::fromValue<qulonglong>(reinterpret_cast<quintptr>(obj))
            );
            Gui::ViewProvider* vp = activeGui->getViewProvider(obj);
            if (vp) {
                item->setIcon(0, vp->getIcon());
            }
        }
    }
}

//! return true if shape can be scaled.
bool DlgScale::canScale(const TopoDS_Shape& shape) const
{
    if (shape.IsNull()) {
        return false;
    }
    // if the shape is a solid or a compound containing shapes, then we can scale it
    TopAbs_ShapeEnum type = shape.ShapeType();

    if (type == TopAbs_VERTEX) {
        return false;
    }

    if (type == TopAbs_COMPOUND || type == TopAbs_COMPSOLID) {
        TopExp_Explorer xp;
        xp.Init(shape, TopAbs_EDGE);
        for (; xp.More(); xp.Next()) {
            // there is at least 1 edge inside the compound, so as long as it isn't null,
            // we can scale this shape.  We can stop looking as soon as we find a non-null
            // edge.
            if (!xp.Current().IsNull()) {
                // found a non-null edge
                return true;
            }
        }
        // did not find a non-null shape
        return false;
    }
    else {
        // not a Vertex, Compound or CompSolid, must be one of Edge, Wire, Face, Shell or
        // Solid, all of which we can scale.
        return true;
    }

    return false;
}

void DlgScale::accept()
{
    //    Base::Console().message("DS::accept()\n");
    try {
        apply();
        if (applySucceeded) {
            QDialog::accept();
        }
    }
    catch (Base::AbortException&) {
        Base::Console().message("DS::accept - apply failed!\n");
    };
}

// create a FeatureScale for each scalable object
void DlgScale::apply()
{
    //    Base::Console().message("DS::apply()\n");
    applySucceeded = false;
    appliedResults.clear();
    App::Document* activeDoc = nullptr;

    try {
        if (!validate()) {
            QMessageBox::critical(this, windowTitle(), tr("No scalable shapes selected"));
            return;
        }

        Gui::WaitCursor wc;
        activeDoc =
            resolveRetainedTaskDocument(m_document, documentAddress, documentUid);
        if (!activeDoc) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("The document used to start this scale task is no longer available.")
            );
            return;
        }
        std::vector<App::DocumentObject*> objects = this->getShapesToScale();

        Base::Reference<ParameterGrp> hGrp = App::GetApplication()
                                                 .GetUserParameter()
                                                 .GetGroup("BaseApp")
                                                 ->GetGroup("Preferences")
                                                 ->GetGroup("Mod/Part");
        bool addBaseName = hGrp->GetBool("AddBaseObjectName", false);

        PartGui::ModelingTaskAttempt attempt(*activeDoc, "Scale");
        std::vector<App::DocumentObject*> results;
        results.reserve(objects.size());
        for (App::DocumentObject* sourceObj : objects) {
            assert(sourceObj);

            if (Part::Feature::getTopoShape(
                    sourceObj,
                    Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
                )
                    .isNull()) {
                throw Base::RuntimeError(
                    "Object " + sourceObj->getFullName()
                    + " is not a shape object. Scaling is not possible."
                );
            }

            std::string name;
            name = sourceObj->getDocument()->getUniqueObjectName("Scale").c_str();
            if (addBaseName) {
                // FIXME: implement
                // QString baseName = QStringLiteral("Scale_%1").arg(sourceObjectName);
                // label = QStringLiteral("%1_Scale").arg((*it)->text(0));
            }

            const QString factory = QStringLiteral("App.getDocument('%1').addObject("
                                                   "'Part::Scale','%2')")
                                        .arg(
                                            QString::fromLatin1(sourceObj->getDocument()->getName()),
                                            QString::fromStdString(name)
                                        );
            auto* newObj = Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                *sourceObj->getDocument(),
                factory.toUtf8(),
                Part::Scale::getClassTypeId()
            );
            attempt.trackCreatedObject(*newObj);

            this->writeParametersToFeature(*newObj, sourceObj);
            auto* presentation = PartGui::resolveModelingPresentationObject(sourceObj);
            if (presentation && presentation->Visibility.getValue()) {
                attempt.trackReplacedInputs(*newObj, {presentation});
            }

            Gui::Command::copyVisual(newObj, "ShapeAppearance", sourceObj);
            Gui::Command::copyVisual(newObj, "LineColor", sourceObj);
            Gui::Command::copyVisual(newObj, "PointColor", sourceObj);

            // The resolved modeling source may be an immutable Body state.
            // Replacing it visually must hide the stable presentation object,
            // not an internal state which does not own viewport visibility.
            if (presentation) {
                Gui::cmdAppObjectHide(presentation);
            }
            else {
                Gui::cmdAppObjectHide(sourceObj);
            }
            results.push_back(newObj);
        }

        if (results.empty()) {
            throw Base::RuntimeError("No scale result was created");
        }
        attempt.markResultAsDesignDefinition(*results.back());

        activeDoc->recompute();
        for (auto* result : results) {
            auto shape = Part::Feature::getTopoShape(result, Part::ShapeOption::NoFlag);
            if (!result->isValid()) {
                throw Base::RuntimeError(result->getStatusString());
            }
            if (shape.isNull() || shape.getShape().IsNull()) {
                throw Base::RuntimeError(std::string(result->getFullLabel()) + " produced no shape");
            }
            if (!shape.isValid()) {
                throw Base::RuntimeError(
                    std::string(result->getFullLabel()) + " produced an invalid shape"
                );
            }
        }

        appliedResults = results;
        attempt.commit();
        applySucceeded = true;
    }
    catch (Base::AbortException&) {
        throw;
    }
    catch (Base::Exception& err) {
        appliedResults.clear();
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Creating scale failed.\n%1").arg(QCoreApplication::translate("Exception", err.what()))
        );
        return;
    }
    catch (...) {
        appliedResults.clear();
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Creating scale failed.\n%1").arg(QStringLiteral("Unknown error"))
        );
        return;
    }
}

void DlgScale::reject()
{
    QDialog::reject();
}

//! retrieve the document objects associated with the selected items in the list
//! widget
std::vector<App::DocumentObject*> DlgScale::getShapesToScale() const
{
    //    Base::Console().message("DS::getShapesToScale()\n");
    QList<QTreeWidgetItem*> items = ui->treeWidget->selectedItems();
    App::Document* doc =
        resolveRetainedTaskDocument(m_document, documentAddress, documentUid);
    if (!doc) {
        throw Base::RuntimeError(
            "The document used to start this scale task is no longer available"
        );
    }

    std::vector<App::DocumentObject*> objects;
    for (auto item : items) {
        App::DocumentObject* obj = item ? resolveRetainedTaskSource(*doc, *item) : nullptr;
        if (!obj) {
            throw Base::RuntimeError(
                "Selected scale source is no longer the active object used to start this task"
            );
        }
        objects.push_back(obj);
    }
    return objects;
}

//! return true if at least one item in the list widget corresponds to an
//! available document object in the document
bool DlgScale::validate()
{
    QList<QTreeWidgetItem*> items = ui->treeWidget->selectedItems();
    App::Document* doc =
        resolveRetainedTaskDocument(m_document, documentAddress, documentUid);
    if (!doc) {
        throw Base::RuntimeError(
            "The document used to start this scale task is no longer available"
        );
    }

    for (auto item : items) {
        if (!item || !resolveRetainedTaskSource(*doc, *item)) {
            throw Base::RuntimeError(
                "Selected scale source is no longer the active object used to start this task"
            );
        }
    }
    return !items.empty();
}

//! update a FeatureScale with the parameters from the UI
void DlgScale::writeParametersToFeature(App::DocumentObject& feature, App::DocumentObject* base) const
{
    //    Base::Console().message("DS::writeParametersToFeature()\n");
    Gui::Command::doCommand(Gui::Command::Doc, "f = %s", Gui::Command::getObjectCmd(&feature).c_str());

    if (!base) {
        return;
    }

    Gui::Command::doCommand(Gui::Command::Doc, "f.Base = %s", Gui::Command::getObjectCmd(base).c_str());

    Gui::Command::doCommand(
        Gui::Command::Doc,
        "f.Uniform = %s",
        ui->rbUniform->isChecked() ? "True" : "False"
    );
    Gui::Command::doCommand(Gui::Command::Doc, "f.UniformScale = %.7f", ui->dsbUniformScale->value());
    Gui::Command::doCommand(Gui::Command::Doc, "f.XScale = %.7f", ui->dsbXScale->value());
    Gui::Command::doCommand(Gui::Command::Doc, "f.YScale = %.7f", ui->dsbYScale->value());
    Gui::Command::doCommand(Gui::Command::Doc, "f.ZScale = %.7f", ui->dsbZScale->value());
}

// ---------------------------------------

TaskScale::TaskScale()
{
    widget = new DlgScale();
    addTaskBox(Gui::BitmapFactory().pixmap("Part_Scale"), widget);
}

bool TaskScale::accept()
{
    widget->accept();
    const bool accepted = widget->result() == QDialog::Accepted;
    if (accepted) {
        markCommandInteractionStateDurable(widget->lastAppliedResults());
    }
    return accepted;
}

bool TaskScale::reject()
{
    widget->reject();
    return true;
}

void TaskScale::clicked(int id)
{
    if (id == QDialogButtonBox::Apply) {
        try {
            widget->apply();
            if (widget->wasLastApplySuccessful()) {
                markCommandInteractionStateDurable(widget->lastAppliedResults());
            }
        }
        catch (Base::AbortException&) {
        };
    }
}

#include "moc_DlgScale.cpp"
