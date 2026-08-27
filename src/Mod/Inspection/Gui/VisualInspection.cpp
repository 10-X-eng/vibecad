// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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
#include <unordered_set>

#include <QByteArray>
#include <QMessageBox>
#include <QVariant>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/PrefWidgets.h>
#include <Gui/ViewProvider.h>
#include <Mod/Inspection/App/InspectionSource.h>
#include <Mod/Part/App/BodyBase.h>

#include "VisualInspection.h"
#include "ui_VisualInspection.h"

using namespace InspectionGui;

namespace
{
constexpr int ObjectNameRole = Qt::UserRole;
constexpr int ObjectIdRole = Qt::UserRole + 1;
}  // namespace

std::vector<App::DocumentObject*> VisualInspection::candidateObjects(App::Document* document)
{
    std::vector<App::DocumentObject*> candidates;
    if (!document) {
        return candidates;
    }

    std::unordered_set<App::DocumentObject*> seen;

    for (auto* object : document->getObjects()) {
        App::DocumentObject* candidate = object;
        if (auto* body = freecad_cast<Part::BodyBase*>(object)) {
            // The Body is the stable public result object. Inspection reads
            // its current Tip shape at execution time, so the reference
            // remains legal and follows future history changes.
            candidate = body;
        }
        else if (Part::BodyBase::findBodyOf(object)) {
            // Body-owned features and origin geometry are private history,
            // already represented by their owning Body.
            continue;
        }

        if (!candidate || !seen.insert(candidate).second) {
            continue;
        }
        Inspection::ResolvedSource source;
        if (Inspection::resolveSource(candidate, document, source)) {
            candidates.push_back(candidate);
        }
    }
    return candidates;
}

namespace InspectionGui
{
class SingleSelectionItem: public QTreeWidgetItem
{
public:
    explicit SingleSelectionItem(QTreeWidget* parent)
        : QTreeWidgetItem(parent)
        , _compItem(nullptr)
    {}

    explicit SingleSelectionItem(QTreeWidgetItem* parent)
        : QTreeWidgetItem(parent)
        , _compItem(nullptr)
    {}

    ~SingleSelectionItem() override = default;

    SingleSelectionItem* getCompetitiveItem() const
    {
        return _compItem;
    }

    void setCompetitiveItem(SingleSelectionItem* item)
    {
        _compItem = item;
    }

private:
    SingleSelectionItem* _compItem;
};
}  // namespace InspectionGui

/* TRANSLATOR InspectionGui::DlgVisualInspectionImp */

/**
 *  Constructs a VisualInspection as a child of 'parent', with the
 *  name 'name' and widget flags set to 'f'.
 */
VisualInspection::VisualInspection(QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
    , ui(new Ui_VisualInspection)
{
    ui->setupUi(this);
    connect(ui->treeWidgetActual, &QTreeWidget::itemClicked, this, &VisualInspection::onActivateItem);
    connect(ui->treeWidgetNominal, &QTreeWidget::itemClicked, this, &VisualInspection::onActivateItem);
    connect(
        ui->buttonBox,
        &QDialogButtonBox::helpRequested,
        Gui::getMainWindow(),
        &Gui::MainWindow::whatsThis
    );

    // FIXME: Not used yet
    ui->textLabel2->hide();
    ui->thickness->hide();
    ui->searchRadius->setUnit(Base::Unit::Length);
    ui->searchRadius->setRange(0, std::numeric_limits<double>::max());
    ui->thickness->setUnit(Base::Unit::Length);
    ui->thickness->setRange(0, std::numeric_limits<double>::max());

    App::Document* doc = App::GetApplication().getActiveDocument();
    // disable Ok button and enable of at least one item in each view is on
    buttonOk = ui->buttonBox->button(QDialogButtonBox::Ok);
    buttonOk->setDisabled(true);

    if (!doc) {
        ui->treeWidgetActual->setDisabled(true);
        ui->treeWidgetNominal->setDisabled(true);
        return;
    }
    targetDocumentName = doc->getName();
    targetDocumentUid = doc->Uid.getValueStr();
    targetDocumentAddress = doc;

    Gui::Document* gui = Gui::Application::Instance->getDocument(doc);
    if (!gui) {
        ui->treeWidgetActual->setDisabled(true);
        ui->treeWidgetNominal->setDisabled(true);
        return;
    }

    for (auto* object : candidateObjects(doc)) {
        Gui::ViewProvider* view = gui->getViewProvider(object);
        QIcon px = view ? view->getIcon() : QIcon();
        SingleSelectionItem* item1 = new SingleSelectionItem(ui->treeWidgetActual);
        item1->setText(0, QString::fromUtf8(object->Label.getValue()));
        item1->setData(0, ObjectNameRole, QString::fromLatin1(object->getNameInDocument()));
        item1->setData(0, ObjectIdRole, QVariant::fromValue<qlonglong>(object->getID()));
        item1->setCheckState(0, Qt::Unchecked);
        item1->setIcon(0, px);

        SingleSelectionItem* item2 = new SingleSelectionItem(ui->treeWidgetNominal);
        item2->setText(0, QString::fromUtf8(object->Label.getValue()));
        item2->setData(0, ObjectNameRole, QString::fromLatin1(object->getNameInDocument()));
        item2->setData(0, ObjectIdRole, QVariant::fromValue<qlonglong>(object->getID()));
        item2->setCheckState(0, Qt::Unchecked);
        item2->setIcon(0, px);

        item1->setCompetitiveItem(item2);
        item2->setCompetitiveItem(item1);
    }

    loadSettings();
}

/*
 *  Destroys the object and frees any allocated resources
 */
VisualInspection::~VisualInspection()
{
    // no need to delete child widgets, Qt does it all for us
    delete ui;
}

void VisualInspection::loadSettings()
{
    ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Inspection/Inspection"
    );

    double searchDistance = ui->searchRadius->value().getValue();
    searchDistance = handle->GetFloat("SearchDistance", searchDistance);
    ui->searchRadius->setValue(searchDistance);

    double thickness = ui->thickness->value().getValue();
    thickness = handle->GetFloat("Thickness", thickness);
    ui->thickness->setValue(thickness);
}

void VisualInspection::saveSettings()
{
    ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Inspection/Inspection"
    );
    double searchDistance = ui->searchRadius->value().getValue();
    handle->SetFloat("SearchDistance", searchDistance);

    double thickness = ui->thickness->value().getValue();
    handle->SetFloat("Thickness", thickness);
}

void VisualInspection::onActivateItem(QTreeWidgetItem* item)
{
    if (item) {
        SingleSelectionItem* sel = static_cast<SingleSelectionItem*>(item);
        SingleSelectionItem* cmp = sel->getCompetitiveItem();
        if (cmp && cmp->checkState(0) == Qt::Checked) {
            cmp->setCheckState(0, Qt::Unchecked);
        }
    }

    bool ok = false;
    for (QTreeWidgetItemIterator it(ui->treeWidgetActual); *it; ++it) {
        SingleSelectionItem* sel = (SingleSelectionItem*)*it;
        if (sel->checkState(0) == Qt::Checked) {
            ok = true;
            break;
        }
    }

    if (ok) {
        ok = false;
        for (QTreeWidgetItemIterator it(ui->treeWidgetNominal); *it; ++it) {
            SingleSelectionItem* sel = (SingleSelectionItem*)*it;
            if (sel->checkState(0) == Qt::Checked) {
                ok = true;
                break;
            }
        }
    }

    buttonOk->setEnabled(ok);
}

void VisualInspection::accept()
{
    onActivateItem(nullptr);
    if (!buttonOk->isEnabled()) {
        return;
    }

    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(targetDocumentName.c_str());
    }
    catch (...) {
        return;
    }
    auto* guiDocument = Gui::Application::Instance
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
    if (!document || targetDocumentUid.empty() || document != targetDocumentAddress
        || document->Uid.getValueStr() != targetDocumentUid || !guiDocument
        || App::GetApplication().getActiveDocument() != document
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()) {
        Base::Console().warning("Visual Inspection was not applied because its document "
                                "transaction is no longer clean.\n");
        return;
    }

    std::vector<App::DocumentObject*> nominalObjects;
    std::vector<App::DocumentObject*> actualObjects;
    const auto resolveItemObject = [document](const QTreeWidgetItem& item) {
        bool validId = false;
        const qlonglong objectId = item.data(0, ObjectIdRole).toLongLong(&validId);
        const QByteArray objectName = item.data(0, ObjectNameRole).toString().toLatin1();
        if (!validId || objectId <= 0 || objectName.isEmpty()) {
            return static_cast<App::DocumentObject*>(nullptr);
        }
        auto* object = document->getObjectByID(static_cast<long>(objectId));
        return object && object->getNameInDocument() && objectName == object->getNameInDocument()
                && document->containsObject(object)
                && document->getObject(objectName.constData()) == object
                && Inspection::isSourceUsable(object, document)
            ? object
            : nullptr;
    };
    for (QTreeWidgetItemIterator it(ui->treeWidgetNominal); *it; ++it) {
        auto* item = static_cast<SingleSelectionItem*>(*it);
        if (item->checkState(0) != Qt::Checked) {
            continue;
        }
        auto* object = resolveItemObject(*item);
        if (!object) {
            return;
        }
        nominalObjects.push_back(object);
    }
    for (QTreeWidgetItemIterator it(ui->treeWidgetActual); *it; ++it) {
        auto* item = static_cast<SingleSelectionItem*>(*it);
        if (item->checkState(0) != Qt::Checked) {
            continue;
        }
        auto* object = resolveItemObject(*item);
        if (!object) {
            return;
        }
        actualObjects.push_back(object);
    }
    if (nominalObjects.empty() || actualObjects.empty()) {
        return;
    }

    const double searchRadius = ui->searchRadius->value().getValue();
    const double thickness = ui->thickness->value().getValue();
    try {
        Base::PyGILStateLocker lock;
        Py::List actuals;
        for (auto* actual : actualObjects) {
            actuals.append(Py::asObject(actual->getPyObject()));
        }
        Py::List nominals;
        for (auto* nominal : nominalObjects) {
            nominals.append(Py::asObject(nominal->getPyObject()));
        }
        PyObject* imported = PyImport_ImportModule("VibeCADInspectionComparisonGui");
        if (!imported) {
            throw Py::Exception();
        }
        Py::Module module(imported, true);
        module.callMemberFunction("start_visual_inspection", Py::TupleN(
            actuals,
            nominals,
            Py::Float(searchRadius),
            Py::Float(thickness)
        ));
    }
    catch (const Py::Exception&) {
        Base::PyException error;
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Visual Inspection"),
            QString::fromUtf8(error.what())
        );
        return;
    }
    saveSettings();
    QDialog::accept();
}

#include "moc_VisualInspection.cpp"
