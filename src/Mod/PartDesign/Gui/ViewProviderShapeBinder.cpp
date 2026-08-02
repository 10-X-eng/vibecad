// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2015 Stefan Tröger <stefantroeger@gmx.net>              *
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


#include <QApplication>
#include <QMenu>
#include <QMessageBox>
#include <sstream>
#include <string>
#include <TopExp.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <tuple>
#include <utility>
#include <vector>
#include <boost/algorithm/string/predicate.hpp>
#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/ActionFunction.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/Macro.h>
#include <Gui/MainWindow.h>
#include <Gui/TaskView/TaskDialog.h>
#include <Gui/ViewParams.h>
#include <Mod/PartDesign/App/ShapeBinder.h>

#include "ViewProviderShapeBinder.h"
#include "TaskShapeBinder.h"

FC_LOG_LEVEL_INIT("ShapeBinder", true, true)

using namespace PartDesignGui;

namespace
{
struct BinderIdentity
{
    std::string documentName;
    std::string documentUid;
    std::string objectName;
    long objectId {-1};
};

BinderIdentity binderIdentityOf(const App::DocumentObject* binder)
{
    if (!binder || !binder->isAttachedToDocument()) {
        return {};
    }
    return {
        binder->getDocument()->getName(),
        binder->getDocument()->Uid.getValueStr(),
        binder->getNameInDocument(),
        binder->getID(),
    };
}

App::Document* resolveBinderDocument(
    const BinderIdentity& identity
) noexcept
{
    try {
        auto* document = identity.documentName.empty()
            ? nullptr
            : App::GetApplication().getDocument(
                  identity.documentName.c_str()
              );
        return document
                && document->Uid.getValueStr()
                    == identity.documentUid
            ? document
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

template<typename BinderT>
std::pair<App::Document*, BinderT*> resolveBinder(const BinderIdentity& identity) noexcept
{
    try {
        auto* document = resolveBinderDocument(identity);
        if (!document) {
            return {};
        }
        auto* binder = freecad_cast<BinderT*>(
            document->getObject(identity.objectName.c_str())
        );
        if (!binder || binder->getID() != identity.objectId) {
            return {};
        }
        return {document, binder};
    }
    catch (...) {
        return {};
    }
}
}  // namespace

PROPERTY_SOURCE(PartDesignGui::ViewProviderShapeBinder, PartGui::ViewProviderPart)

ViewProviderShapeBinder::ViewProviderShapeBinder()
{
    sPixmap = "PartDesign_ShapeBinder.svg";

    // make the viewprovider more datum like
    AngularDeflection.setStatus(App::Property::Hidden, true);
    Deviation.setStatus(App::Property::Hidden, true);
    DrawStyle.setStatus(App::Property::Hidden, true);
    Lighting.setStatus(App::Property::Hidden, true);
    LineColor.setStatus(App::Property::Hidden, true);
    LineWidth.setStatus(App::Property::Hidden, true);
    PointColor.setStatus(App::Property::Hidden, true);
    PointSize.setStatus(App::Property::Hidden, true);
    DisplayMode.setStatus(App::Property::Hidden, true);

    // get the datum coloring scheme
    //  set default color for datums (golden yellow with 60% transparency)
    ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/PartDesign"
    );
    unsigned long shcol = hGrp->GetUnsigned("DefaultDatumColor", 0xFFD70099);
    Base::Color col((uint32_t)shcol);

    ShapeAppearance.setDiffuseColor(col);
    LineColor.setValue(col);
    PointColor.setValue(col);
    Transparency.setValue(60);
    LineWidth.setValue(1);
}

ViewProviderShapeBinder::~ViewProviderShapeBinder() = default;

bool ViewProviderShapeBinder::doubleClicked()
{
    auto* document = getDocument();
    return document
        && document->setEdit(this, ViewProvider::Default);
}

bool ViewProviderShapeBinder::setEdit(int ModNum)
{
    // TODO Share code with other view providers (2015-09-11, Fat-Zer)

    if (ModNum == ViewProvider::Default || ModNum == 1) {
        auto* binder = getObject();
        auto* document =
            binder ? binder->getDocument() : nullptr;
        if (!document) {
            return false;
        }
        // When double-clicking on the item for this pad the
        // object unsets and sets its edit mode without closing
        // the task panel
        Gui::TaskView::TaskDialog* dlg =
            Gui::Control().activeDialog(document);
        TaskDlgShapeBinder* sbDlg = qobject_cast<TaskDlgShapeBinder*>(dlg);
        if (dlg && !sbDlg) {
            QMessageBox msgBox(Gui::getMainWindow());
            msgBox.setText(QObject::tr("A dialog is already open in the task panel"));
            msgBox.setInformativeText(QObject::tr("Close this dialog?"));
            msgBox.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
            msgBox.setDefaultButton(QMessageBox::Yes);
            int ret = msgBox.exec();
            if (ret == QMessageBox::Yes) {
                Gui::Control().reject(document);
            }
            else {
                return false;
            }
        }

        // clear the selection (convenience)
        Gui::Selection().clearSelection(document->getName());

        // start the edit dialog
        // another pad left open its task panel
        if (sbDlg) {
            Gui::Control().showDialog(sbDlg, document);
        }
        else {
            Gui::Control().showDialog(
                new TaskDlgShapeBinder(this, ModNum == 1),
                document
            );
        }

        return true;
    }
    else {
        return ViewProviderPart::setEdit(ModNum);
    }
}

void ViewProviderShapeBinder::unsetEdit(int ModNum)
{

    PartGui::ViewProviderPart::unsetEdit(ModNum);
}

void ViewProviderShapeBinder::attach(App::DocumentObject* obj)
{
    if (auto geo = dynamic_cast<App::GeoFeature*>(obj)) {
        geo->setMaterialAppearance(ShapeAppearance[0]);
    }
    ViewProviderPart::attach(obj);
}

void ViewProviderShapeBinder::highlightReferences(bool on)
{
    App::GeoFeature* obj = nullptr;
    std::vector<std::string> subs;

    if (getObject()->isDerivedFrom<PartDesign::ShapeBinder>()) {
        PartDesign::ShapeBinder::getFilteredReferences(
            &getObject<PartDesign::ShapeBinder>()->Support,
            obj,
            subs
        );
    }
    else {
        return;
    }

    // stop if not a Part feature was found
    if (!obj || !obj->isDerivedFrom<Part::Feature>()) {
        return;
    }

    PartGui::ViewProviderPart* svp = dynamic_cast<PartGui::ViewProviderPart*>(
        Gui::Application::Instance->getViewProvider(obj)
    );
    if (!svp) {
        return;
    }

    if (on) {
        if (!subs.empty() && originalLineColors.empty()) {
            TopTools_IndexedMapOfShape eMap;
            TopExp::MapShapes(static_cast<Part::Feature*>(obj)->Shape.getValue(), TopAbs_EDGE, eMap);
            originalLineColors = svp->LineColorArray.getValues();
            std::vector<Base::Color> lcolors = originalLineColors;
            lcolors.resize(eMap.Extent(), svp->LineColor.getValue());

            TopExp::MapShapes(static_cast<Part::Feature*>(obj)->Shape.getValue(), TopAbs_FACE, eMap);
            originalFaceAppearance = svp->ShapeAppearance.getValues();
            std::vector<App::Material> fcolors = originalFaceAppearance;
            fcolors.resize(eMap.Extent(), svp->ShapeAppearance[0]);

            for (const std::string& e : subs) {
                // Note: stoi may throw, but it strictly shouldn't happen
                if (e.compare(0, 4, "Edge") == 0) {
                    int idx = std::stoi(e.substr(4)) - 1;
                    assert(idx >= 0);
                    if (idx < static_cast<int>(lcolors.size())) {
                        lcolors[idx] = Base::Color(1.0, 0.0, 1.0);  // magenta
                    }
                }
                else if (e.compare(0, 4, "Face") == 0) {
                    int idx = std::stoi(e.substr(4)) - 1;
                    assert(idx >= 0);
                    if (idx < static_cast<int>(fcolors.size())) {
                        fcolors[idx].diffuseColor = Base::Color(1.0, 0.0, 1.0);  // magenta
                    }
                }
            }
            svp->LineColorArray.setValues(lcolors);
            svp->ShapeAppearance.setValues(fcolors);
        }
    }
    else {
        if (!subs.empty() && !originalLineColors.empty()) {
            svp->LineColorArray.setValues(originalLineColors);
            originalLineColors.clear();

            svp->ShapeAppearance.setValues(originalFaceAppearance);
            originalFaceAppearance.clear();
        }
    }
}

void ViewProviderShapeBinder::setupContextMenu(QMenu* menu, QObject* receiver, const char* member)
{
    Q_UNUSED(receiver)
    Q_UNUSED(member)

    auto* binder = getObject<PartDesign::ShapeBinder>();
    if (!binder) {
        return;
    }
    const auto identity = binderIdentityOf(binder);
    const std::string transactionText =
        QObject::tr("Edit %1")
            .arg(QString::fromUtf8(binder->Label.getValue()))
            .toStdString();

    QAction* act = menu->addAction(QObject::tr("Edit Shape Binder"));
    act->setData(QVariant((int)ViewProvider::Default));

    Gui::ActionFunction* func = new Gui::ActionFunction(menu);
    func->trigger(act, [identity, transactionText]() {
        if (!Gui::Application::Instance) {
            return;
        }

        auto [appDocument, liveBinder] =
            resolveBinder<PartDesign::ShapeBinder>(identity);
        auto* guiDocument = appDocument
            ? Gui::Application::Instance->getDocument(appDocument)
            : nullptr;
        auto* viewProvider = liveBinder
            ? dynamic_cast<ViewProviderShapeBinder*>(
                  Gui::Application::Instance->getViewProvider(liveBinder)
              )
            : nullptr;
        if (!appDocument || !guiDocument || !viewProvider
            || Gui::Application::Instance->activeDocument() != guiDocument
            || appDocument->getBookedTransactionID()
                != App::NullTransaction
            || appDocument->hasPendingTransaction()
            || appDocument->isPerformingTransaction()
            || appDocument->isTransactionLocked()
            || appDocument->testStatus(App::Document::Recomputing)
            || App::GetApplication().hasPendingRecomputeRequest(
                appDocument->getName()
            )
            || App::GetApplication().isRestoring()
            || Gui::Control().activeDialog(appDocument)) {
            return;
        }

        Gui::TaskView::TaskDialog::beginCommandInvocation();
        bool invocationFinished = false;
        int transactionId = App::NullTransaction;
        bool transactionAdopted = false;
        const auto finishInvocation = [&](bool success) {
            if (!invocationFinished) {
                invocationFinished = true;
                Gui::TaskView::TaskDialog::endCommandInvocation(
                    success
                );
            }
        };
        const auto abortTransaction = [&]() {
            if (transactionAdopted
                || transactionId == App::NullTransaction
                || !App::GetApplication().abortTransaction(
                    transactionId
                )) {
                return;
            }
            if (auto* document =
                    resolveBinderDocument(identity)) {
                Gui::TaskView::TaskDialog::
                    recordCommandTransactionCompletion(
                        document,
                        transactionId
                    );
            }
        };
        const auto resetEditor = [&]() {
            auto* document = resolveBinderDocument(identity);
            auto* currentGuiDocument =
                document && Gui::Application::Instance
                ? Gui::Application::Instance->getDocument(document)
                : nullptr;
            if (!currentGuiDocument
                || !currentGuiDocument->getEditViewProvider()) {
                return true;
            }
            try {
                currentGuiDocument->resetEdit();
                return true;
            }
            catch (Base::Exception& error) {
                error.reportException();
            }
            catch (...) {
                Base::Console().error(
                    "Closing the rejected Shape Binder editor failed\n"
                );
            }
            return false;
        };
        const auto rejectEdit = [&]() {
            const bool editorClosed = resetEditor();
            if (editorClosed) {
                abortTransaction();
            }
            finishInvocation(false);
        };

        try {
            auto* macroManager =
                Gui::Application::Instance->macroManager();
            const auto submittedMacroLines =
                macroManager
                ? macroManager->getSubmittedCommandLines()
                : 0;
            transactionId = Gui::Command::openDocumentCommand(
                appDocument,
                transactionText
            );
            std::tie(appDocument, liveBinder) =
                resolveBinder<PartDesign::ShapeBinder>(identity);
            guiDocument = appDocument
                ? Gui::Application::Instance->getDocument(appDocument)
                : nullptr;
            viewProvider = liveBinder
                ? dynamic_cast<ViewProviderShapeBinder*>(
                      Gui::Application::Instance->getViewProvider(
                          liveBinder
                      )
                  )
                : nullptr;
            if (transactionId == App::NullTransaction
                || !appDocument || !guiDocument || !viewProvider
                || appDocument->getBookedTransactionID()
                    != transactionId
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                rejectEdit();
                return;
            }

            const bool launched = viewProvider->doubleClicked();
            std::tie(appDocument, liveBinder) =
                resolveBinder<PartDesign::ShapeBinder>(identity);
            guiDocument = appDocument
                ? Gui::Application::Instance->getDocument(appDocument)
                : nullptr;
            auto* liveViewProvider = liveBinder
                ? dynamic_cast<ViewProviderShapeBinder*>(
                      Gui::Application::Instance->getViewProvider(
                          liveBinder
                      )
                  )
                : nullptr;
            if (!launched || !appDocument || !guiDocument
                || liveViewProvider != viewProvider
                || guiDocument->getEditViewProvider() != viewProvider
                || Gui::Application::Instance->activeDocument()
                    != guiDocument
                || !Gui::Application::Instance->isInEdit(
                    guiDocument
                )
                || !guiDocument->adoptOwnedEditTransaction(
                    transactionId
                )) {
                rejectEdit();
                return;
            }
            transactionAdopted = true;

            try {
                if (macroManager
                    && submittedMacroLines
                        == macroManager->getSubmittedCommandLines()) {
                    std::ostringstream command;
                    command
                        << Gui::Command::getObjectCmd(liveBinder)
                        << ".ViewObject.doubleClicked()";
                    macroManager->addLine(
                        Gui::MacroManager::Gui,
                        command.str().c_str()
                    );
                }
            }
            catch (...) {
                Base::Console().warning(
                    "Recording the accepted Shape Binder edit failed\n"
                );
            }
            finishInvocation(true);
        }
        catch (Base::Exception& error) {
            rejectEdit();
            error.reportException();
        }
        catch (...) {
            rejectEdit();
            Base::Console().error(
                "Opening the Shape Binder editor failed\n"
            );
        }
    });
}

//=====================================================================================

PROPERTY_SOURCE(PartDesignGui::ViewProviderSubShapeBinder, PartGui::ViewProviderPart)

ViewProviderSubShapeBinder::ViewProviderSubShapeBinder()
{
    sPixmap = "PartDesign_SubShapeBinder.svg";

    ADD_PROPERTY_TYPE(UseBinderStyle, (false), "", (App::PropertyType)(App::Prop_None), "");
}

void ViewProviderSubShapeBinder::attach(App::DocumentObject* obj)
{

    UseBinderStyle.setValue(boost::istarts_with(obj->getNameInDocument(), "binder"));
    if (auto geo = dynamic_cast<App::GeoFeature*>(obj)) {
        geo->setMaterialAppearance(ShapeAppearance[0]);
    }
    ViewProviderPart::attach(obj);
}

void ViewProviderSubShapeBinder::onChanged(const App::Property* prop)
{
    if (prop == &UseBinderStyle && (!getObject() || !getObject()->isRestoring())) {
        Base::Color shapeColor, lineColor, pointColor;
        int transparency, linewidth;
        if (UseBinderStyle.getValue()) {
            // get the datum coloring scheme
            //  set default color for datums (golden yellow with 60% transparency)
            static ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
                "User parameter:BaseApp/Preferences/Mod/PartDesign"
            );
            shapeColor.setPackedValue(hGrp->GetUnsigned("DefaultDatumColor", 0xFFD70099));
            lineColor = shapeColor;
            pointColor = shapeColor;
            transparency = 60;
            linewidth = 1;
        }
        else {
            shapeColor.setPackedValue(Gui::ViewParams::instance()->getDefaultShapeColor());
            lineColor.setPackedValue(Gui::ViewParams::instance()->getDefaultShapeLineColor());
            pointColor = lineColor;
            transparency = Gui::ViewParams::instance()->getDefaultShapeTransparency();
            linewidth = Gui::ViewParams::instance()->getDefaultShapeLineWidth();
        }
        ShapeAppearance.setDiffuseColor(shapeColor);
        LineColor.setValue(lineColor);
        PointColor.setValue(pointColor);
        Transparency.setValue(transparency);
        LineWidth.setValue(linewidth);
    }

    ViewProviderPart::onChanged(prop);
}

bool ViewProviderSubShapeBinder::canDropObjectEx(
    App::DocumentObject*,
    App::DocumentObject*,
    const char*,
    const std::vector<std::string>&
) const
{
    return true;
}

std::string ViewProviderSubShapeBinder::dropObjectEx(
    App::DocumentObject* obj,
    App::DocumentObject* owner,
    const char* subname,
    const std::vector<std::string>& elements
)
{
    auto self = getObject<PartDesign::SubShapeBinder>();
    if (!self) {
        return {};
    }
    std::map<App::DocumentObject*, std::vector<std::string>> values;
    if (!subname) {
        subname = "";
    }
    std::string sub(subname);
    if (sub.empty()) {
        values[owner ? owner : obj] = elements;
    }
    else {
        std::vector<std::string> subs;
        if (!elements.empty()) {
            subs.reserve(elements.size());
            for (auto& element : elements) {
                subs.push_back(sub + element);
            }
        }
        else {
            subs.push_back(sub);
        }
        values[owner ? owner : obj] = std::move(subs);
    }

    self->setLinks(std::move(values), QApplication::keyboardModifiers() == Qt::ControlModifier);
    if (self->Relative.getValue()) {
        updatePlacement(false);
    }
    return {};
}


bool ViewProviderSubShapeBinder::doubleClicked()
{
    updatePlacement(true);
    return true;
}

void ViewProviderSubShapeBinder::setupContextMenu(QMenu* menu, QObject* receiver, const char* member)
{
    QAction* act;
    act = menu->addAction(QObject::tr("Synchronize"), receiver, member);
    act->setData(QVariant((int)Synchronize));
    act = menu->addAction(QObject::tr("Select Bound Object"), receiver, member);
    act->setData(QVariant((int)SelectObject));
    ViewProviderPart::setupContextMenu(menu, receiver, member);
}

bool ViewProviderSubShapeBinder::setEdit(int ModNum)
{

    switch (ModNum) {
        case Synchronize:
            updatePlacement(true);
            break;
        case SelectObject: {
            auto self = getObject<PartDesign::SubShapeBinder>();
            if (!self || !self->Support.getValue()) {
                break;
            }

            Gui::Selection().selStackPush();
            Gui::Selection().clearSelection();
            for (auto& link : self->Support.getSubListValues()) {
                auto obj = link.getValue();
                if (!obj || !obj->isAttachedToDocument()) {
                    continue;
                }
                const auto& subs = link.getSubValues();
                if (!subs.empty()) {
                    Gui::Selection().addSelections(
                        obj->getDocument()->getName(),
                        obj->getNameInDocument(),
                        subs
                    );
                }
                else {
                    Gui::Selection().addSelection(
                        obj->getDocument()->getName(),
                        obj->getNameInDocument()
                    );
                }
            }
            Gui::Selection().selStackPush();
            break;
        }
        default:
            return ViewProviderPart::setEdit(ModNum);
    }
    return false;
}

void ViewProviderSubShapeBinder::updatePlacement(bool transaction)
{
    auto self = getObject<PartDesign::SubShapeBinder>();
    if (!self || !self->Support.getValue()) {
        return;
    }

    std::vector<Base::Matrix4D> mats;
    bool relative = self->Relative.getValue();
    App::DocumentObject* parent = nullptr;
    std::string parentSub;
    if (relative && !self->getParents().empty()) {
        const auto& sel = Gui::Selection().getSelection("", Gui::ResolveMode::NoResolve);
        if (sel.size() != 1 || !sel[0].pObject
            || sel[0].pObject->getSubObject(sel[0].SubName) != self) {
            FC_WARN("invalid selection");
        }
        else {
            parent = sel[0].pObject;
            parentSub = sel[0].SubName;
        }
    }

    if (!transaction) {
        if (relative) {
            self->Context.setValue(parent, parentSub.c_str());
        }
        try {
            self->update(PartDesign::SubShapeBinder::UpdateForced);
        }
        catch (Base::Exception& e) {
            e.reportException();
        }
        return;
    }

    auto* document = self->getDocument();
    if (!document
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()
        || Gui::Control().activeDialog(document)) {
        return;
    }
    const auto identity = binderIdentityOf(self);
    const auto* timeline = App::DocumentTimeline::get(document);
    if (timeline
        && timeline->Position.getValue()
            != static_cast<long>(
                timeline->Operations.getSize()
            )) {
        return;
    }
    const auto timelineOperations =
        timeline ? timeline->Operations.getValues()
                 : std::vector<App::DocumentObject*> {};
    const long timelinePosition =
        timeline ? timeline->Position.getValue() : 0;
    auto* macroManager =
        Gui::Application::Instance
        ? Gui::Application::Instance->macroManager()
        : nullptr;
    const auto submittedMacroLines =
        macroManager
        ? macroManager->getSubmittedCommandLines()
        : 0;
    const int transactionId =
        Gui::Command::openDocumentCommand(
            document,
            "Sync binder"
        );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        if (relative) {
            self->Context.setValue(parent, parentSub.c_str());
        }
        std::tie(document, self) =
            resolveBinder<PartDesign::SubShapeBinder>(identity);
        if (!document || !self) {
            throw Base::RuntimeError(
                "The binder changed while synchronizing its context"
            );
        }
        self->update(PartDesign::SubShapeBinder::UpdateForced);
        std::tie(document, self) =
            resolveBinder<PartDesign::SubShapeBinder>(identity);
        if (!document || !self) {
            throw Base::RuntimeError(
                "The binder changed while synchronizing its shape"
            );
        }
        Gui::Command::updateDocument(document);
        std::tie(document, self) =
            resolveBinder<PartDesign::SubShapeBinder>(identity);
        timeline = App::DocumentTimeline::get(document);
        if (!document || !self || !self->isValid()
            || (timeline
                && (timeline->Operations.getValues()
                        != timelineOperations
                    || timeline->Position.getValue()
                        != timelinePosition))
            || (!timeline && !timelineOperations.empty())) {
            throw Base::RuntimeError(
                "Synchronizing the binder produced an invalid tracked state"
            );
        }
        Gui::Command::commitCommand(transactionId);
        try {
            std::tie(document, self) =
                resolveBinder<PartDesign::SubShapeBinder>(identity);
            if (macroManager && document && self
                && submittedMacroLines
                    == macroManager->getSubmittedCommandLines()) {
                std::ostringstream command;
                command
                    << Gui::Command::getObjectCmd(self)
                    << ".ViewObject.doubleClicked()";
                macroManager->addLine(
                    Gui::MacroManager::Gui,
                    command.str().c_str()
                );
            }
        }
        catch (...) {
            Base::Console().warning(
                "Recording the accepted binder synchronization failed\n"
            );
        }
        return;
    }
    catch (Base::Exception& e) {
        e.reportException();
    }
    catch (Standard_Failure& e) {
        std::ostringstream str;
        Standard_CString msg = e.GetMessageString();
        str << typeid(e).name() << " ";
        if (msg) {
            str << msg;
        }
        else {
            str << "No OCCT Exception Message";
        }
        FC_ERR(str.str());
    }
    catch (...) {
        Gui::Command::abortCommand(transactionId);
        FC_ERR("Unknown exception while synchronizing binder");
        return;
    }
    Gui::Command::abortCommand(transactionId);
}

std::vector<App::DocumentObject*> ViewProviderSubShapeBinder::claimChildren() const
{
    std::vector<App::DocumentObject*> ret;
    auto self = freecad_cast<PartDesign::SubShapeBinder*>(getObject());
    if (self && self->ClaimChildren.getValue() && self->Support.getValue()) {
        std::set<App::DocumentObject*> objSet;
        for (auto& l : self->Support.getSubListValues()) {
            auto obj = l.getValue();
            if (!obj) {
                continue;
            }
            const auto& subs = l.getSubValues();
            if (subs.empty()) {
                if (objSet.insert(obj).second) {
                    ret.push_back(obj);
                }
                continue;
            }
            for (auto& sub : subs) {
                auto sobj = obj->getSubObject(sub.c_str());
                if (sobj && objSet.insert(sobj).second) {
                    ret.push_back(sobj);
                }
            }
        }
    }
    return ret;
}

////////////////////////////////////////////////////////////////////////////////////////

namespace Gui
{
PROPERTY_SOURCE_TEMPLATE(
    PartDesignGui::ViewProviderSubShapeBinderPython,
    PartDesignGui::ViewProviderSubShapeBinder
)
template class PartDesignGuiExport ViewProviderFeaturePythonT<ViewProviderSubShapeBinder>;
}  // namespace Gui
