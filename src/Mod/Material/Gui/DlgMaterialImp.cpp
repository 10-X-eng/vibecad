// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2002 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <QDockWidget>
#include <QSignalBlocker>
#include <QString>
#include <algorithm>
#include <fastsignals/signal.h>
#include <ranges>
#include <utility>

#include <Base/Console.h>
#include <Base/Exception.h>
#include <App/Application.h>
#include <App/Document.h>
#include <Gui/Application.h>
#include <Gui/DockWindowManager.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProvider.h>
#include <Gui/WaitCursor.h>

#include <Mod/Material/App/Exceptions.h>
#include <Mod/Material/App/MaterialManager.h>
#include <Mod/Material/App/ModelUuids.h>
#include <Mod/Material/App/PropertyMaterial.h>

#include "DlgMaterialImp.h"
#include "SelectionTargetIdentity.h"
#include "ui_DlgMaterial.h"


using namespace MatGui;
using namespace std;
namespace sp = std::placeholders;

namespace
{
App::Document& activeAppDocument()
{
    auto* guiDocument = Gui::Application::Instance ? Gui::Application::Instance->activeDocument()
                                                   : nullptr;
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    if (!document) {
        throw Base::RuntimeError("The material editor requires an active document");
    }
    return *document;
}

std::vector<App::Document*> materialMutationDocuments(
    App::Document& occurrenceDocument,
    const std::vector<SelectionPropertyTargetIdentity>& targets
)
{
    std::vector<App::Document*> documents {&occurrenceDocument};
    for (const auto& target : targets) {
        auto* selectedDocument = target.occurrence.resolveDocument();
        auto* ownerDocument = target.owner.resolveDocument();
        if (selectedDocument != &occurrenceDocument || !ownerDocument || !target.resolveProperty()) {
            throw Base::RuntimeError("A selected material target changed before editing began");
        }
        if (std::ranges::find(documents, ownerDocument) == documents.end()) {
            documents.push_back(ownerDocument);
        }
    }
    return documents;
}
}  // namespace

/* TRANSLATOR Gui::Dialog::DlgMaterialImp */

#if 0  // needed for Qt's lupdate utility
    qApp->translate("QDockWidget", "Material");
#endif

class DlgMaterialImp::Private
{
    using DlgMaterialImp_Connection = fastsignals::connection;

public:
    Ui::DlgMaterial ui;
    bool floating;
    std::vector<SelectionPropertyTargetIdentity> targets;
    DlgMaterialImp_Connection connectChangedObject;
    App::Document* targetDocumentAddress {nullptr};
    std::string targetDocumentName;
    std::string targetDocumentUid;
    int transactionId {App::NullTransaction};

    void addTarget(const App::DocumentObject* object)
    {
        auto target = SelectionPropertyTargetIdentity::capture(object, "ShapeMaterial");
        if (target && std::ranges::find(targets, *target) == targets.end()) {
            targets.push_back(std::move(*target));
        }
    }

    bool mutationAllowed() const noexcept
    {
        if (transactionId == App::NullTransaction) {
            return true;
        }
        try {
            auto* document = targetDocumentName.empty()
                ? nullptr
                : App::GetApplication().getDocument(targetDocumentName.c_str());
            if (!document || document != targetDocumentAddress
                || document->Uid.getValueStr() != targetDocumentUid
                || document->getBookedTransactionID() != transactionId
                || !App::GetApplication().transactionIsActive(transactionId)) {
                return false;
            }
            return std::ranges::all_of(
                targets,
                [document, this](const SelectionPropertyTargetIdentity& target) {
                    auto* occurrenceDocument = target.occurrence.resolveDocument();
                    auto* ownerDocument = target.owner.resolveDocument();
                    return occurrenceDocument == document && ownerDocument
                        && ownerDocument->getBookedTransactionID() == transactionId
                        && target.resolveProperty();
                }
            );
        }
        catch (...) {
            return false;
        }
    }
};

/**
 *  Constructs a DlgMaterialImp which is a child of 'parent', with the
 *  name 'name' and widget flags set to 'f'
 *
 *  The dialog will by default be modeless, unless you set 'modal' to
 *  true to construct a modal dialog.
 */
DlgMaterialImp::DlgMaterialImp(bool floating, QWidget* parent, Qt::WindowFlags fl)
    : DlgMaterialImp(
          floating,
          Gui::Application::Instance && Gui::Application::Instance->activeDocument()
              ? Gui::Application::Instance->activeDocument()->getDocument()
              : nullptr,
          parent,
          fl
      )
{}

DlgMaterialImp::DlgMaterialImp(bool floating, App::Document* document, QWidget* parent, Qt::WindowFlags fl)
    : DlgMaterialImp(floating, document, App::NullTransaction, parent, fl)
{}

DlgMaterialImp::DlgMaterialImp(
    bool floating,
    App::Document* document,
    int transactionId,
    QWidget* parent,
    Qt::WindowFlags fl
)
    : DlgMaterialImp(
          floating,
          document,
          transactionId,
          captureMaterialTargets(document, floating),
          parent,
          fl
      )
{}

DlgMaterialImp::DlgMaterialImp(
    bool floating,
    App::Document* document,
    int transactionId,
    std::vector<SelectionPropertyTargetIdentity> targets,
    QWidget* parent,
    Qt::WindowFlags fl
)
    : QDialog(parent, fl)
    , d(new Private)
{
    d->ui.setupUi(this);
    setupConnections();

    d->floating = floating;
    d->targetDocumentAddress = document;
    d->targetDocumentName = document ? document->getName() : "";
    d->targetDocumentUid = document ? document->Uid.getValueStr() : "";
    d->transactionId = transactionId;
    d->targets = std::move(targets);

    // Create a filter to only include current format materials
    // that contain physical properties.
    Materials::MaterialFilter filter;
    filter.requirePhysical(true);
    d->ui.widgetMaterial->setFilter(filter);

    setMaterial();

    // embed this dialog into a dockable widget container
    if (floating) {
        Gui::DockWindowManager* pDockMgr = Gui::DockWindowManager::instance();
        QDockWidget* dw = pDockMgr->addDockWindow("Display Properties", this, Qt::AllDockWidgetAreas);
        dw->setFeatures(QDockWidget::DockWidgetMovable | QDockWidget::DockWidgetFloatable);
        dw->setFloating(true);
        dw->show();
    }

    Gui::Selection().Attach(this);

    // NOLINTBEGIN
    d->connectChangedObject = Gui::Application::Instance->signalChangedObject.connect(
        std::bind(&DlgMaterialImp::slotChangedObject, this, sp::_1, sp::_2)
    );
    // NOLINTEND
}

std::vector<SelectionPropertyTargetIdentity> DlgMaterialImp::captureMaterialTargets(
    App::Document* document,
    bool floating
)
{
    std::vector<SelectionPropertyTargetIdentity> targets;
    for (const auto& selected : Gui::Selection().getCompleteSelection()) {
        if (!floating && selected.pDoc != document) {
            continue;
        }
        auto target = SelectionPropertyTargetIdentity::capture(selected.pObject, "ShapeMaterial");
        if (target && std::ranges::find(targets, *target) == targets.end()) {
            targets.push_back(std::move(*target));
        }
    }
    return targets;
}

/**
 *  Destroys the object and frees any allocated resources
 */
DlgMaterialImp::~DlgMaterialImp()
{
    // no need to delete child widgets, Qt does it all for us
    d->connectChangedObject.disconnect();
    Gui::Selection().Detach(this);
}

void DlgMaterialImp::setupConnections()
{
    connect(
        d->ui.widgetMaterial,
        &MaterialTreeWidget::materialSelected,
        this,
        &DlgMaterialImp::onMaterialSelected
    );
}

void DlgMaterialImp::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        d->ui.retranslateUi(this);
    }
    QDialog::changeEvent(e);
}

/// @cond DOXERR
void DlgMaterialImp::OnChange(
    Gui::SelectionSingleton::SubjectType& rCaller,
    Gui::SelectionSingleton::MessageType Reason
)
{
    Q_UNUSED(rCaller);
    if (d->floating
        && (Reason.Type == Gui::SelectionChanges::AddSelection
            || Reason.Type == Gui::SelectionChanges::RmvSelection
            || Reason.Type == Gui::SelectionChanges::SetSelection
            || Reason.Type == Gui::SelectionChanges::ClrSelection)) {
        d->targets.clear();
        for (const auto& selected : Gui::Selection().getCompleteSelection()) {
            d->addTarget(selected.pObject);
        }
        setMaterial();
    }
}
/// @endcond

void DlgMaterialImp::slotChangedObject(const Gui::ViewProvider& obj, const App::Property& prop)
{
    // This method gets called if a property of any view provider is changed.
    // We pick out all the properties for which we need to update this dialog.
    std::vector<Gui::ViewProvider*> Provider = getSelection();
    auto vp = std::find_if(Provider.begin(), Provider.end(), [&obj](Gui::ViewProvider* v) {
        return v == &obj;
    });

    if (vp != Provider.end()) {
        const char* name = obj.getPropertyName(&prop);
        // this is not a property of the view provider but of the document object
        if (!name) {
            return;
        }
        std::string prop_name = name;
        if (prop.isDerivedFrom<App::PropertyMaterial>()) {
            // auto& value = static_cast<const App::PropertyMaterial&>(prop).getValue();
            if (prop_name == "ShapeMaterial") {
                // bool blocked = d->ui.buttonColor->blockSignals(true);
                // auto color = value.diffuseColor;
                // d->ui.buttonColor->setColor(QColor((int)(255.0f * color.r),
                //                                    (int)(255.0f * color.g),
                //                                    (int)(255.0f * color.b)));
                // d->ui.buttonColor->blockSignals(blocked);
            }
        }
    }
}

/**
 * Destroys the dock window this object is embedded into without destroying itself.
 */
void DlgMaterialImp::reject()
{
    if (d->floating) {
        // closes the dock window
        Gui::DockWindowManager* pDockMgr = Gui::DockWindowManager::instance();
        pDockMgr->removeDockWindow(this);
    }
    QDialog::reject();
}

void DlgMaterialImp::setMaterial()
{
    for (auto* property : getMaterialProperties()) {
        auto* materialProperty = dynamic_cast<Materials::PropertyMaterial*>(property);
        if (!materialProperty) {
            continue;
        }
        try {
            const auto& material = materialProperty->getValue();
            d->ui.widgetMaterial->setMaterial(material.getUUID());
            return;
        }
        catch (const Materials::MaterialNotFound&) {
        }
    }
    d->ui.widgetMaterial->setMaterial(Materials::MaterialManager::defaultMaterialUUID());
}

std::vector<Gui::ViewProvider*> DlgMaterialImp::getSelection() const
{
    std::vector<Gui::ViewProvider*> views;
    if (!d->mutationAllowed()) {
        return views;
    }
    views.reserve(d->targets.size());
    for (const auto& target : d->targets) {
        if (auto* view = target.occurrence.resolveViewProvider()) {
            views.push_back(view);
        }
    }
    return views;
}

std::vector<App::Property*> DlgMaterialImp::getMaterialProperties() const
{
    std::vector<App::Property*> properties;
    if (!d->mutationAllowed()) {
        return properties;
    }
    properties.reserve(d->targets.size());
    for (const auto& target : d->targets) {
        if (auto* property = target.resolveProperty()) {
            if (std::ranges::find(properties, property) == properties.end()) {
                properties.push_back(property);
            }
        }
    }
    return properties;
}

void DlgMaterialImp::onMaterialSelected(const std::shared_ptr<Materials::Material>& material)
{
    for (auto* property : getMaterialProperties()) {
        if (auto* materialProperty = dynamic_cast<Materials::PropertyMaterial*>(property)) {
            materialProperty->setValue(*material);
        }
    }
}

// ----------------------------------------------------------------------------

/* TRANSLATOR Gui::Dialog::TaskMaterial */

TaskMaterial::TaskMaterial()
    : TaskMaterial(activeAppDocument())
{}

TaskMaterial::TaskMaterial(App::Document& document)
{
    targetDocumentAddress = &document;
    targetDocumentName = document.getName();
    targetDocumentUid = document.Uid.getValueStr();
    auto targets = DlgMaterialImp::captureMaterialTargets(&document, false);
    auto documents = materialMutationDocuments(document, targets);
    transaction = std::make_unique<Gui::ExactTransaction>(
        document,
        documents,
        QT_TRANSLATE_NOOP("Command", "Set Material")
    );
    tid = transaction->id();
    if (tid == App::NullTransaction || !transaction->ownsCurrentTransaction()) {
        throw Base::RuntimeError("Could not establish the material transaction");
    }

    this->setButtonPosition(TaskMaterial::North);
    setAutoCloseOnDeletedDocument(true);
    try {
        widget
            = new DlgMaterialImp(false, &document, tid, std::move(targets), nullptr, Qt::WindowFlags());
        taskbox = new Gui::TaskView::TaskBox(QPixmap(), widget->windowTitle(), true, nullptr);
        taskbox->groupLayout()->addWidget(widget);
        Content.push_back(taskbox);
    }
    catch (...) {
        if (transaction) {
            (void)transaction->abort();
            transaction.reset();
        }
        tid = App::NullTransaction;
        throw;
    }
}

TaskMaterial::~TaskMaterial()
{
    if (transaction) {
        (void)transaction->abort();
    }
}

QDialogButtonBox::StandardButtons TaskMaterial::getStandardButtons() const
{
    return QDialogButtonBox::Ok | QDialogButtonBox::Cancel;
}

bool TaskMaterial::accept()
{
    if (!ownsTransaction()) {
        if (transaction && transaction->isClosed()) {
            transaction.reset();
            tid = App::NullTransaction;
            return true;
        }
        return false;
    }
    if (!transaction->commit()) {
        return false;
    }
    transaction.reset();
    tid = App::NullTransaction;
    return true;
}

bool TaskMaterial::reject()
{
    if (!ownsTransaction()) {
        if (transaction && transaction->isClosed()) {
            transaction.reset();
            tid = App::NullTransaction;
            widget->reject();
            return (widget->result() == QDialog::Rejected);
        }
        return false;
    }
    if (!transaction->abort()) {
        return false;
    }
    transaction.reset();
    tid = App::NullTransaction;
    widget->reject();
    return (widget->result() == QDialog::Rejected);
}

bool TaskMaterial::ownsTransaction() const
{
    if (!transaction || tid == App::NullTransaction || !targetDocumentAddress
        || targetDocumentName.empty() || targetDocumentUid.empty()) {
        return false;
    }
    try {
        auto* document = App::GetApplication().getDocument(targetDocumentName.c_str());
        return document && document == targetDocumentAddress
            && document->Uid.getValueStr() == targetDocumentUid
            && document->getBookedTransactionID() == tid && transaction->ownsCurrentTransaction();
    }
    catch (...) {
        return false;
    }
}

#include "moc_DlgMaterialImp.cpp"
