// SPDX-License-Identifier: LGPL-2.1-or-later

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

#include <limits>

#include <QMessageBox>
#include <BRep_Tool.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <Precision.hxx>
#include <ShapeExtend_Explorer.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopTools_HSequenceOfShape.hxx>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/Link.h>
#include <App/Part.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/Utilities.h>
#include <Gui/ViewProvider.h>
#include <Gui/WaitCursor.h>
#include <Mod/Part/App/FeatureRevolution.h>

#include <Mod/Part/App/Part2DObject.h>

#include "DlgRevolution.h"
#include "ModelingSelection.h"
#include "ui_DlgRevolution.h"


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

class DlgRevolution::EdgeSelection: public Gui::SelectionFilterGate
{
public:
    bool canSelect;

    EdgeSelection()
        : Gui::SelectionFilterGate(nullPointer())
    {
        canSelect = false;
    }
    bool allow(App::Document* /*pDoc*/, App::DocumentObject* pObj, const char* sSubName) override
    {
        this->canSelect = false;

        if (Base::Tools::isNullOrEmpty(sSubName)) {
            return false;
        }
        std::string element(sSubName);
        if (element.substr(0, 4) != "Edge") {
            return false;
        }
        Part::TopoShape part = Part::Feature::getTopoShape(
            pObj,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
        );
        if (part.isNull()) {
            return false;
        }
        try {
            TopoDS_Shape sub = part.getSubShape(sSubName);
            if (!sub.IsNull() && sub.ShapeType() == TopAbs_EDGE) {
                const TopoDS_Edge& edge = TopoDS::Edge(sub);
                BRepAdaptor_Curve adapt(edge);
                if (adapt.GetType() == GeomAbs_Line || adapt.GetType() == GeomAbs_Circle) {
                    this->canSelect = true;
                    return true;
                }
            }
        }
        catch (...) {
        }

        return false;
    }
};

DlgRevolution::DlgRevolution(QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
    , ui(new Ui_DlgRevolution)
    , filter(nullptr)
    , filterSelection(false)
{
    ui->setupUi(this);
    setupConnections();

    constexpr double max = std::numeric_limits<double>::max();
    ui->xPos->setRange(-max, max);
    ui->yPos->setRange(-max, max);
    ui->zPos->setRange(-max, max);
    ui->xPos->setUnit(Base::Unit::Length);
    ui->yPos->setUnit(Base::Unit::Length);
    ui->zPos->setUnit(Base::Unit::Length);

    ui->xDir->setRange(-max, max);
    ui->yDir->setRange(-max, max);
    ui->zDir->setRange(-max, max);
    ui->xDir->setUnit(Base::Unit());
    ui->yDir->setUnit(Base::Unit());
    ui->zDir->setUnit(Base::Unit());
    ui->zDir->setValue(1.0);

    ui->angle->setUnit(Base::Unit::Angle);
    ui->angle->setValue(360.0);
    findShapes();

    Gui::ItemViewSelection sel(ui->treeWidget);
    auto modelingSelection =
        PartGui::getModelingSelection(document.c_str());
    std::vector<App::DocumentObject*> selectedObjects;
    for (auto& selected : modelingSelection) {
        selectedObjects.push_back(selected.getObject());
    }
    sel.applyFrom(selectedObjects);

    connect(ui->txtAxisLink, &QLineEdit::textChanged, this, &DlgRevolution::onAxisLinkTextChanged);

    autoSolid();
}

/*
 *  Destroys the object and frees any allocated resources
 */
DlgRevolution::~DlgRevolution()
{
    // no need to delete child widgets, Qt does it all for us
    Gui::Selection().rmvSelectionGate();
}

void DlgRevolution::setupConnections()
{
    // clang-format off
    connect(ui->selectLine, &QPushButton::clicked,
            this, &DlgRevolution::onSelectLineClicked);
    connect(ui->btnX, &QPushButton::clicked,
            this, &DlgRevolution::onButtonXClicked);
    connect(ui->btnY, &QPushButton::clicked,
            this, &DlgRevolution::onButtonYClicked);
    connect(ui->btnZ, &QPushButton::clicked,
            this, &DlgRevolution::onButtonZClicked);
    connect(ui->txtAxisLink, &QLineEdit::textChanged,
            this, &DlgRevolution::onAxisLinkTextChanged);
    // clang-format on
}

Base::Vector3d DlgRevolution::getDirection() const
{
    return Base::Vector3d(
        ui->xDir->value().getValue(),
        ui->yDir->value().getValue(),
        ui->zDir->value().getValue()
    );
}

Base::Vector3d DlgRevolution::getPosition() const
{
    return Base::Vector3d(
        ui->xPos->value().getValueAs(Base::Quantity::MilliMetre),
        ui->yPos->value().getValueAs(Base::Quantity::MilliMetre),
        ui->zPos->value().getValueAs(Base::Quantity::MilliMetre)
    );
}

void DlgRevolution::getAxisLink(App::PropertyLinkSub& lnk) const
{
    QString text = ui->txtAxisLink->text();

    if (text.length() == 0) {
        lnk.setValue(nullptr);
    }
    else {
        QStringList parts = text.split(QChar::fromLatin1(':'));
        auto* doc = App::GetApplication().getDocument(document.c_str());
        if (!doc) {
            throw Base::RuntimeError("Document lost");
        }
        App::DocumentObject* obj = doc->getObject(parts[0].toLatin1());
        if (!obj) {
            throw Base::ValueError(tr("Object not found: %1").arg(parts[0]).toUtf8().constData());
        }
        obj = PartGui::resolveModelingObject(obj);
        if (!obj) {
            throw Base::ValueError(
                tr("Selected Body has no Tip: %1").arg(parts[0]).toUtf8().constData()
            );
        }
        lnk.setValue(obj);
        if (parts.size() == 1) {
            return;
        }
        else if (parts.size() == 2) {
            std::vector<std::string> subs;
            subs.emplace_back(parts[1].toLatin1().constData());
            lnk.setValue(obj, subs);
        }
    }
}

double DlgRevolution::getAngle() const
{
    return ui->angle->value().getValueAs(Base::Quantity::Degree);
}

void DlgRevolution::setDirection(Base::Vector3d dir)
{
    ui->xDir->setValue(dir.x);
    ui->yDir->setValue(dir.y);
    ui->zDir->setValue(dir.z);
}

void DlgRevolution::setPosition(Base::Vector3d pos)
{
    ui->xPos->setValue(pos.x);
    ui->yPos->setValue(pos.y);
    ui->zPos->setValue(pos.z);
}

void DlgRevolution::setAxisLink(const App::PropertyLinkSub& lnk)
{
    if (!lnk.getValue()) {
        ui->txtAxisLink->clear();
        return;
    }
    if (lnk.getSubValues().size() == 1) {
        this->setAxisLink(lnk.getValue()->getNameInDocument(), lnk.getSubValues()[0].c_str());
    }
    else {
        this->setAxisLink(lnk.getValue()->getNameInDocument(), "");
    }
}

void DlgRevolution::setAxisLink(const char* objname, const char* subname)
{
    if (objname && strlen(objname) > 0) {
        QString txt = QString::fromLatin1(objname);
        if (subname && strlen(subname) > 0) {
            txt = txt + QStringLiteral(":") + QString::fromLatin1(subname);
        }
        ui->txtAxisLink->setText(txt);
    }
    else {
        ui->txtAxisLink->clear();
    }
}

std::vector<App::DocumentObject*> DlgRevolution::getShapesToRevolve() const
{
    QList<QTreeWidgetItem*> items = ui->treeWidget->selectedItems();
    App::Document* doc = resolveRetainedTaskDocument(document, documentAddress, documentUid);
    if (!doc) {
        throw Base::RuntimeError(
            "The document used to start this revolution task is no longer available"
        );
    }

    std::vector<App::DocumentObject*> objects;
    for (auto item : items) {
        App::DocumentObject* obj = item ? resolveRetainedTaskSource(*doc, *item) : nullptr;
        if (!obj) {
            throw Base::RuntimeError(
                "Selected revolution source is no longer the active object used to start this task"
            );
        }
        objects.push_back(obj);
    }
    return objects;
}

bool DlgRevolution::validate()
{
    // check source shapes
    if (ui->treeWidget->selectedItems().isEmpty()) {
        QMessageBox::critical(this, windowTitle(), tr("Select a shape for revolution."));
        return false;
    }

    // check axis link
    bool axisLinkIsValid = false;
    bool axisLinkHasAngle = false;
    try {
        App::PropertyLinkSub lnk;
        this->getAxisLink(lnk);
        double angle_edge = 1e100;
        Base::Vector3d axis, center;
        axisLinkIsValid = Part::Revolution::fetchAxisLink(lnk, center, axis, angle_edge);
        axisLinkHasAngle = angle_edge != 1e100;
    }
    catch (Base::Exception& err) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Revolution axis link is invalid.\n\n%1")
                .arg(QCoreApplication::translate("Exception", err.what()))
        );
        ui->txtAxisLink->setFocus();
        return false;
    }
    catch (Standard_Failure& err) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Revolution axis link is invalid.\n\n%1")
                .arg(QString::fromLocal8Bit(err.GetMessageString()))
        );
        ui->txtAxisLink->setFocus();
        return false;
    }
    catch (...) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Revolution axis link is invalid.\n\n%1").arg(tr("Unknown error"))
        );
        ui->txtAxisLink->setFocus();
        return false;
    }

    // check axis dir
    if (!axisLinkIsValid) {
        if (this->getDirection().Length() < Precision::Confusion()) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Revolution axis direction is zero-length. It must be non-zero.")
            );
            ui->xDir->setFocus();
            return false;
        }
    }

    // check angle
    if (!axisLinkHasAngle) {
        if (fabs(Base::toRadians(this->getAngle())) < Precision::Angular()) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Revolution angle span is zero. It must be non-zero.")
            );
            ui->angle->setFocus();
            return false;
        }
    }

    return true;
}

void DlgRevolution::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    else {
        QDialog::changeEvent(e);
    }
}

void DlgRevolution::keyPressEvent(QKeyEvent* ke)
{
    // The revolution dialog is embedded into a task panel
    // which is a parent widget and will handle the event
    ke->ignore();
}

void DlgRevolution::findShapes()
{
    App::Document* activeDoc = App::GetApplication().getActiveDocument();
    if (!activeDoc) {
        return;
    }
    document = activeDoc->getName();
    documentAddress = activeDoc;
    documentUid = activeDoc->Uid.getValueStr();
    Gui::Document* activeGui = Gui::Application::Instance->getDocument(activeDoc);

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

        TopExp_Explorer xp;
        xp.Init(shape, TopAbs_SOLID);
        if (xp.More()) {
            continue;  // solids not allowed
        }
        xp.Init(shape, TopAbs_COMPSOLID);
        if (xp.More()) {
            continue;  // compound solids not allowed
        }
        // So allowed are: vertex, edge, wire, face, shell and compound
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

void DlgRevolution::accept()
{
    appliedResults.clear();
    if (!this->validate()) {
        return;
    }

    Gui::WaitCursor wc;
    App::Document* activeDoc =
        resolveRetainedTaskDocument(document, documentAddress, documentUid);
    if (!activeDoc) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("The document used to start this revolution task is no longer available.")
        );
        return;
    }

    try {
        QList<QTreeWidgetItem*> items = ui->treeWidget->selectedItems();
        const auto sources = getShapesToRevolve();
        if (sources.size() != static_cast<std::size_t>(items.size())) {
            throw Base::RuntimeError("Revolution source selection changed while applying");
        }

        PartGui::ModelingTaskAttempt attempt(*activeDoc, "Revolve");

        QString name, solid;
        std::vector<App::DocumentObject*> results;
        results.reserve(items.size());
        if (ui->checkSolid->isChecked()) {
            solid = QStringLiteral("True");
        }
        else {
            solid = QStringLiteral("False");
        }

        App::PropertyLinkSub axisLink;
        this->getAxisLink(axisLink);
        QString strAxisLink;
        if (axisLink.getValue()) {
            strAxisLink
                = QStringLiteral("(%1, %2)")
                      .arg(
                          QString::fromStdString(Gui::Command::getObjectCmd(axisLink.getValue())),
                          axisLink.getSubValues().size() == 1
                              ? QStringLiteral("\"%1\"").arg(
                                    QString::fromLatin1(axisLink.getSubValues()[0].c_str())
                                )
                              : QString()
                      );
        }
        else {
            strAxisLink = QStringLiteral("None");
        }

        QString symmetric;
        if (ui->checkSymmetric->isChecked()) {
            symmetric = QStringLiteral("True");
        }
        else {
            symmetric = QStringLiteral("False");
        }

        for (qsizetype itemIndex = 0; itemIndex < items.size(); ++itemIndex) {
            name = QString::fromLatin1(activeDoc->getUniqueObjectName("Revolve").c_str());
            auto* sourceObj = sources[static_cast<std::size_t>(itemIndex)];
            auto* presentation = PartGui::resolveModelingPresentationObject(sourceObj);
            const bool replacesPresentation = presentation && presentation->Visibility.getValue();
            Base::Vector3d axis = this->getDirection();
            Base::Vector3d pos = this->getPosition();

            const QString factory = QStringLiteral("FreeCAD.getDocument(\"%1\").addObject("
                                                   "\"Part::Revolution\",\"%2\")")
                                        .arg(QString::fromLatin1(document.c_str()), name);
            auto* newObj = Gui::Command::runDocumentObjectCommand(
                Gui::Command::App,
                *activeDoc,
                factory.toUtf8(),
                Part::Revolution::getClassTypeId()
            );
            const QString resultObject = QString::fromStdString(Gui::Command::getObjectCmd(newObj));
            const QString sourceObject = QString::fromStdString(Gui::Command::getObjectCmd(sourceObj));
            const QString code = QStringLiteral("%1.Source = %2\n"
                                                "%1.Axis = (%3,%4,%5)\n"
                                                "%1.Base = (%6,%7,%8)\n"
                                                "%1.Angle = %9\n"
                                                "%1.Solid = %10\n"
                                                "%1.AxisLink = %11\n"
                                                "%1.Symmetric = %12")
                                     .arg(resultObject, sourceObject)
                                     .arg(axis.x, 0, 'f', 15)
                                     .arg(axis.y, 0, 'f', 15)
                                     .arg(axis.z, 0, 'f', 15)
                                     .arg(pos.x, 0, 'f', 15)
                                     .arg(pos.y, 0, 'f', 15)
                                     .arg(pos.z, 0, 'f', 15)
                                     .arg(getAngle(), 0, 'f', 15)
                                     .arg(solid, strAxisLink, symmetric);
            Gui::Command::runCommand(Gui::Command::App, code.toUtf8());
            FCMD_OBJ_HIDE(sourceObj);
            attempt.trackCreatedObject(*newObj);
            if (replacesPresentation) {
                attempt.trackReplacedInputs(*newObj, {presentation});
            }

            if (!sourceObj->isDerivedFrom<Part::Part2DObject>()) {
                Gui::Command::copyVisual(newObj, "ShapeAppearance", sourceObj);
                Gui::Command::copyVisual(newObj, "LineColor", sourceObj);
                Gui::Command::copyVisual(newObj, "PointColor", sourceObj);
            }
            results.push_back(newObj);
        }

        if (results.empty()) {
            throw Base::RuntimeError("No revolution result was created");
        }
        attempt.markResultAsDesignDefinition(*results.back());

        activeDoc->recompute();
        for (auto* result : results) {
            auto resultShape = Part::Feature::getTopoShape(result, Part::ShapeOption::NoFlag);
            if (!result->isValid()) {
                throw Base::RuntimeError(result->getStatusString());
            }
            if (resultShape.isNull() || resultShape.getShape().IsNull()) {
                throw Base::RuntimeError(std::string(result->getFullLabel()) + " produced no shape");
            }
            if (!resultShape.isValid()) {
                throw Base::RuntimeError(
                    std::string(result->getFullLabel()) + " produced an invalid shape"
                );
            }
        }

        appliedResults = results;
        attempt.commit();
    }
    catch (Base::Exception& err) {
        appliedResults.clear();
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Creating Revolve failed.\n\n%1").arg(QCoreApplication::translate("Exception", err.what()))
        );
        return;
    }
    catch (...) {
        appliedResults.clear();
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Creating Revolve failed.\n\n%1").arg(QStringLiteral("Unknown error"))
        );
        return;
    }

    QDialog::accept();
}

void DlgRevolution::onSelectLineClicked()
{
    if (!filterSelection) {
        filterSelection = true;
        filter = new EdgeSelection();
        Gui::Selection().addSelectionGate(filter);
        ui->selectLine->setText(tr("Selecting… (Line or Arc)"));
    }
    else {
        Gui::Selection().rmvSelectionGate();
        filter = nullptr;
        filterSelection = false;
        ui->selectLine->setText(tr("Select Reference"));
    }
}

void DlgRevolution::onButtonXClicked()
{
    setDirection(Base::Vector3d(1, 0, 0));
    if (!ui->xDir->isEnabled()) {
        ui->txtAxisLink->clear();
    }
}

void DlgRevolution::onButtonYClicked()
{
    setDirection(Base::Vector3d(0, 1, 0));
    if (!ui->xDir->isEnabled()) {
        ui->txtAxisLink->clear();
    }
}

void DlgRevolution::onButtonZClicked()
{
    setDirection(Base::Vector3d(0, 0, 1));
    if (!ui->xDir->isEnabled()) {
        ui->txtAxisLink->clear();
    }
}

void DlgRevolution::onAxisLinkTextChanged(QString)
{
    bool en = true;
    try {
        Base::Vector3d pos, dir;
        double angle_edge = 1e100;
        App::PropertyLinkSub lnk;
        this->getAxisLink(lnk);
        bool fetched = Part::Revolution::fetchAxisLink(lnk, pos, dir, angle_edge);
        if (fetched) {
            this->setDirection(dir);
            this->setPosition(pos);
            if (angle_edge != 1e100) {
                ui->angle->setValue(0.0);
            }
            else if (fabs(ui->angle->value().getValue()) < 1e-12) {
                ui->angle->setValue(360.0);
            }
            en = false;
        }
    }
    catch (Base::Exception&) {
    }
    catch (...) {
    }
    ui->xDir->setEnabled(en);
    ui->yDir->setEnabled(en);
    ui->zDir->setEnabled(en);
    ui->xPos->setEnabled(en);
    ui->yPos->setEnabled(en);
    ui->zPos->setEnabled(en);
}

void DlgRevolution::onSelectionChanged(const Gui::SelectionChanges& msg)
{
    if (msg.Type == Gui::SelectionChanges::AddSelection) {
        if (filter && filter->canSelect) {
            this->setAxisLink(msg.pObjectName, msg.pSubName);
        }
    }
}

App::DocumentObject& DlgRevolution::getShapeToRevolve() const
{
    std::vector<App::DocumentObject*> objs = this->getShapesToRevolve();
    if (objs.empty()) {
        throw Base::ValueError("No shapes selected");
    }
    return *(objs[0]);
}

void DlgRevolution::autoSolid()
{
    try {
        App::DocumentObject& dobj = this->getShapeToRevolve();
        Part::TopoShape topoShape = Part::Feature::getTopoShape(
            &dobj,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
        );
        if (topoShape.isNull()) {
            return;
        }
        else {
            TopoDS_Shape sh = topoShape.getShape();
            if (sh.IsNull()) {
                return;
            }
            ShapeExtend_Explorer xp;
            Handle(TopTools_HSequenceOfShape)
                leaves = xp.SeqFromCompound(sh, /*recursive= */ Standard_True);
            int cntClosedWires = 0;
            for (int i = 0; i < leaves->Length(); i++) {
                const TopoDS_Shape& leaf = leaves->Value(i + 1);
                if (leaf.IsNull()) {
                    return;
                }
                if (leaf.ShapeType() == TopAbs_WIRE || leaf.ShapeType() == TopAbs_EDGE) {
                    if (BRep_Tool::IsClosed(leaf)) {
                        cntClosedWires++;
                    }
                }
            }
            ui->checkSolid->setChecked(cntClosedWires == leaves->Length());
        }
    }
    catch (...) {
    }
}

// ---------------------------------------

TaskRevolution::TaskRevolution()
{
    widget = new DlgRevolution();
    addTaskBox(Gui::BitmapFactory().pixmap("Part_Revolve"), widget);
}

bool TaskRevolution::accept()
{
    widget->accept();
    const bool accepted = widget->result() == QDialog::Accepted;
    if (accepted) {
        markCommandInteractionStateDurable(widget->lastAppliedResults());
    }
    return accepted;
}

#include "moc_DlgRevolution.cpp"
