// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2019 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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
#include <sstream>

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QKeyEvent>
#include <QMessageBox>

#include <Inventor/nodes/SoBaseColor.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoDrawStyle.h>
#include <Inventor/nodes/SoLineSet.h>
#include <Inventor/nodes/SoSeparator.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/ViewProvider.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/Gui/BackgroundMeshCut.h>
#include <Mod/Mesh/Gui/CommandGuard.h>

#include "CrossSections.h"
#include "ui_CrossSections.h"


using namespace MeshPartGui;
namespace MeshPartGui
{
class ViewProviderCrossSections: public Gui::ViewProvider
{
public:
    ViewProviderCrossSections()
    {
        coords = new SoCoordinate3();
        coords->ref();
        planes = new SoLineSet();
        planes->ref();
        SoBaseColor* color = new SoBaseColor();
        color->rgb.setValue(1.0f, 0.447059f, 0.337255f);
        SoDrawStyle* style = new SoDrawStyle();
        style->lineWidth.setValue(2.0f);
        this->pcRoot->addChild(color);
        this->pcRoot->addChild(style);
        this->pcRoot->addChild(coords);
        this->pcRoot->addChild(planes);
    }
    ~ViewProviderCrossSections() override
    {
        coords->unref();
        planes->unref();
    }
    void updateData(const App::Property*) override
    {}
    const char* getDefaultDisplayMode() const override
    {
        return "";
    }
    std::vector<std::string> getDisplayModes() const override
    {
        return {};
    }
    void setCoords(const std::vector<Base::Vector3f>& v)
    {
        coords->point.setNum(v.size());
        SbVec3f* p = coords->point.startEditing();
        for (unsigned int i = 0; i < v.size(); i++) {
            const Base::Vector3f& pt = v[i];
            p[i].setValue(pt.x, pt.y, pt.z);
        }
        coords->point.finishEditing();
        unsigned int count = v.size() / 5;
        planes->numVertices.setNum(count);
        int32_t* l = planes->numVertices.startEditing();
        for (unsigned int i = 0; i < count; i++) {
            l[i] = 5;
        }
        planes->numVertices.finishEditing();
    }

private:
    SoCoordinate3* coords;
    SoLineSet* planes;
};

class CrossSections::SelectionState
{
public:
    explicit SelectionState(App::Document* targetDocument)
        : document(targetDocument)
    {
        if (!targetDocument) {
            return;
        }
        for (auto* object :
             Gui::Selection().getObjectsOfType<Mesh::Feature>()) {
            if (object && object->getDocument() == targetDocument
                && MeshGui::isNativeMeshInputActive(object)) {
                meshes.emplace_back(object);
            }
        }
    }

    App::DocumentWeakPtrT document;
    std::vector<App::DocumentObjectWeakPtrT> meshes;
};
}  // namespace MeshPartGui

CrossSections::CrossSections(const Base::BoundBox3d& bb, QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
    , bbox(bb)
    , selectionState(std::make_unique<SelectionState>(
          App::GetApplication().getActiveDocument()
      ))
{
    ui = new Ui_CrossSections();
    ui->setupUi(this);
    setupConnections();

    constexpr double max = std::numeric_limits<double>::max();
    ui->position->setRange(-max, max);
    ui->position->setUnit(Base::Unit::Length);
    ui->distance->setRange(0, max);
    ui->distance->setUnit(Base::Unit::Length);
    ui->spinEpsilon->setMinimum(0.0001);
    vp = new ViewProviderCrossSections();

    Base::Vector3d c = bbox.GetCenter();
    calcPlane(CrossSections::XY, c.z);
    ui->position->setValue(c.z);

    App::Document* targetDocument =
        selectionState ? *selectionState->document : nullptr;
    Gui::Document* doc = targetDocument
        ? Gui::Application::Instance->getDocument(targetDocument)
        : nullptr;
    view = doc
        ? qobject_cast<Gui::View3DInventor*>(doc->getActiveView())
        : nullptr;
    if (view) {
        view->getViewer()->addViewProvider(vp);
    }
}

/*
 *  Destroys the object and frees any allocated resources
 */
CrossSections::~CrossSections()
{
    // no need to delete child widgets, Qt does it all for us
    delete ui;
    if (view) {
        view->getViewer()->removeViewProvider(vp);
    }
    delete vp;
}

void CrossSections::setupConnections()
{
    // clang-format off
    connect(ui->xyPlane, &QRadioButton::clicked,
            this, &CrossSections::xyPlaneClicked);
    connect(ui->xzPlane, &QRadioButton::clicked,
            this, &CrossSections::xzPlaneClicked);
    connect(ui->yzPlane, &QRadioButton::clicked,
            this, &CrossSections::yzPlaneClicked);
    connect(ui->position,qOverload<double>(&Gui::QuantitySpinBox::valueChanged),
            this, &CrossSections::positionValueChanged);
    connect(ui->distance, qOverload<double>(&Gui::QuantitySpinBox::valueChanged),
            this, &CrossSections::distanceValueChanged);
    connect(ui->countSections, qOverload<int>(&QSpinBox::valueChanged),
            this, &CrossSections::countSectionsValueChanged);
    connect(ui->checkBothSides, &QCheckBox::toggled,
            this, &CrossSections::checkBothSidesToggled);
    connect(ui->sectionsBox, &QGroupBox::toggled,
            this, &CrossSections::sectionsBoxToggled);
    // clang-format on
}

CrossSections::Plane CrossSections::plane() const
{
    if (ui->xyPlane->isChecked()) {
        return CrossSections::XY;
    }
    else if (ui->xzPlane->isChecked()) {
        return CrossSections::XZ;
    }
    else {
        return CrossSections::YZ;
    }
}

void CrossSections::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    else {
        QDialog::changeEvent(e);
    }
}

void CrossSections::keyPressEvent(QKeyEvent* ke)
{
    // The cross-sections dialog is embedded into a task panel
    // which is a parent widget and will handle the event
    ke->ignore();
}

void CrossSections::accept()
{
    if (applyAndReport()) {
        QDialog::accept();
    }
}

void CrossSections::apply()
{
    (void)applyAndReport();
}

bool CrossSections::applyAndReport()
{
    App::Document* document =
        selectionState ? *selectionState->document : nullptr;
    if (!document || selectionState->meshes.empty()) {
        return false;
    }
    std::vector<Mesh::Feature*> meshes;
    meshes.reserve(selectionState->meshes.size());
    for (const auto& target : selectionState->meshes) {
        auto* mesh = target.get<Mesh::Feature>();
        if (!mesh || mesh->getDocument() != document
            || !MeshGui::isNativeMeshInputActive(mesh)) {
            return false;
        }
        meshes.push_back(mesh);
    }
    if (!MeshGui::hasCleanNativeMutationBoundary(document)) {
        return false;
    }

    std::vector<double> d;
    if (ui->sectionsBox->isChecked()) {
        d = getPlanes();
    }
    else {
        d.push_back(ui->position->value().getValue());
    }
    double a = 0, b = 0, c = 0;
    switch (plane()) {
        case CrossSections::XY:
            c = 1.0;
            break;
        case CrossSections::XZ:
            b = 1.0;
            break;
        case CrossSections::YZ:
            a = 1.0;
            break;
    }

    bool connectEdges = ui->checkBoxConnect->isChecked();
    double eps = ui->spinEpsilon->value();

    QJsonArray targets;
    for (auto* source : meshes) {
        targets.append(QString::fromUtf8(source->getNameInDocument()));
    }
    QJsonArray normal;
    normal.append(a);
    normal.append(b);
    normal.append(c);
    QJsonArray positions;
    for (const double value : d) {
        positions.append(value);
    }
    const QJsonObject payload {
        {"targets", targets},
        {"normal", normal},
        {"positions_mm", positions},
        {"epsilon_mm", eps},
        {"connect_edges", connectEdges},
    };
    try {
        MeshGui::startBackgroundMeshCut(
            "cross_sections",
            QJsonDocument(payload).toJson(QJsonDocument::Compact).toStdString()
        );
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            this,
            tr("Cross-Sections"),
            QString::fromUtf8(error.what())
        );
        return false;
    }
    return true;
}

void CrossSections::xyPlaneClicked()
{
    Base::Vector3d c = bbox.GetCenter();
    ui->position->setValue(c.z);
    if (!ui->sectionsBox->isChecked()) {
        calcPlane(CrossSections::XY, c.z);
    }
    else {
        double dist = bbox.LengthZ() / ui->countSections->value();
        if (!ui->checkBothSides->isChecked()) {
            dist *= 0.5f;
        }
        ui->distance->setValue(dist);
        calcPlanes(CrossSections::XY);
    }
}

void CrossSections::xzPlaneClicked()
{
    Base::Vector3d c = bbox.GetCenter();
    ui->position->setValue(c.y);
    if (!ui->sectionsBox->isChecked()) {
        calcPlane(CrossSections::XZ, c.y);
    }
    else {
        double dist = bbox.LengthY() / ui->countSections->value();
        if (!ui->checkBothSides->isChecked()) {
            dist *= 0.5f;
        }
        ui->distance->setValue(dist);
        calcPlanes(CrossSections::XZ);
    }
}

void CrossSections::yzPlaneClicked()
{
    Base::Vector3d c = bbox.GetCenter();
    ui->position->setValue(c.x);
    if (!ui->sectionsBox->isChecked()) {
        calcPlane(CrossSections::YZ, c.x);
    }
    else {
        double dist = bbox.LengthX() / ui->countSections->value();
        if (!ui->checkBothSides->isChecked()) {
            dist *= 0.5f;
        }
        ui->distance->setValue(dist);
        calcPlanes(CrossSections::YZ);
    }
}

void CrossSections::positionValueChanged(double v)
{
    if (!ui->sectionsBox->isChecked()) {
        calcPlane(plane(), v);
    }
    else {
        calcPlanes(plane());
    }
}

void CrossSections::sectionsBoxToggled(bool ok)
{
    if (ok) {
        countSectionsValueChanged(ui->countSections->value());
    }
    else {
        CrossSections::Plane type = plane();
        Base::Vector3d c = bbox.GetCenter();
        double value = 0;
        switch (type) {
            case CrossSections::XY:
                value = c.z;
                break;
            case CrossSections::XZ:
                value = c.y;
                break;
            case CrossSections::YZ:
                value = c.x;
                break;
        }

        ui->position->setValue(value);
        calcPlane(type, value);
    }
}

void CrossSections::checkBothSidesToggled(bool b)
{
    double d = ui->distance->value().getValue();
    d = b ? 2.0 * d : 0.5 * d;
    ui->distance->setValue(d);
    calcPlanes(plane());
}

void CrossSections::countSectionsValueChanged(int v)
{
    CrossSections::Plane type = plane();
    double dist = 0;
    switch (type) {
        case CrossSections::XY:
            dist = bbox.LengthZ() / v;
            break;
        case CrossSections::XZ:
            dist = bbox.LengthY() / v;
            break;
        case CrossSections::YZ:
            dist = bbox.LengthX() / v;
            break;
    }
    if (!ui->checkBothSides->isChecked()) {
        dist *= 0.5f;
    }
    ui->distance->setValue(dist);
    calcPlanes(type);
}

void CrossSections::distanceValueChanged(double)
{
    calcPlanes(plane());
}

void CrossSections::calcPlane(Plane type, double pos)
{
    double bound[4];
    switch (type) {
        case XY:
            bound[0] = bbox.MinX;
            bound[1] = bbox.MaxX;
            bound[2] = bbox.MinY;
            bound[3] = bbox.MaxY;
            break;
        case XZ:
            bound[0] = bbox.MinX;
            bound[1] = bbox.MaxX;
            bound[2] = bbox.MinZ;
            bound[3] = bbox.MaxZ;
            break;
        case YZ:
            bound[0] = bbox.MinY;
            bound[1] = bbox.MaxY;
            bound[2] = bbox.MinZ;
            bound[3] = bbox.MaxZ;
            break;
    }

    std::vector<double> d;
    d.push_back(pos);
    makePlanes(type, d, bound);
}

void CrossSections::calcPlanes(Plane type)
{
    double bound[4];
    switch (type) {
        case XY:
            bound[0] = bbox.MinX;
            bound[1] = bbox.MaxX;
            bound[2] = bbox.MinY;
            bound[3] = bbox.MaxY;
            break;
        case XZ:
            bound[0] = bbox.MinX;
            bound[1] = bbox.MaxX;
            bound[2] = bbox.MinZ;
            bound[3] = bbox.MaxZ;
            break;
        case YZ:
            bound[0] = bbox.MinY;
            bound[1] = bbox.MaxY;
            bound[2] = bbox.MinZ;
            bound[3] = bbox.MaxZ;
            break;
    }

    std::vector<double> d = getPlanes();
    makePlanes(type, d, bound);
}

std::vector<double> CrossSections::getPlanes() const
{
    int count = ui->countSections->value();
    double pos = ui->position->value().getValue();
    double stp = ui->distance->value().getValue();
    bool both = ui->checkBothSides->isChecked();

    std::vector<double> d;
    if (both) {
        double start = pos - 0.5f * (count - 1) * stp;
        for (int i = 0; i < count; i++) {
            d.push_back(start + i * stp);
        }
    }
    else {
        for (int i = 0; i < count; i++) {
            d.push_back(pos + i * stp);
        }
    }
    return d;
}

void CrossSections::makePlanes(Plane type, const std::vector<double>& d, double bound[4])
{
    std::vector<Base::Vector3f> points;
    for (double it : d) {
        Base::Vector3f v[4];
        switch (type) {
            case XY:
                v[0].Set(bound[0], bound[2], it);
                v[1].Set(bound[1], bound[2], it);
                v[2].Set(bound[1], bound[3], it);
                v[3].Set(bound[0], bound[3], it);
                break;
            case XZ:
                v[0].Set(bound[0], it, bound[2]);
                v[1].Set(bound[1], it, bound[2]);
                v[2].Set(bound[1], it, bound[3]);
                v[3].Set(bound[0], it, bound[3]);
                break;
            case YZ:
                v[0].Set(it, bound[0], bound[2]);
                v[1].Set(it, bound[1], bound[2]);
                v[2].Set(it, bound[1], bound[3]);
                v[3].Set(it, bound[0], bound[3]);
                break;
        }

        points.push_back(v[0]);
        points.push_back(v[1]);
        points.push_back(v[2]);
        points.push_back(v[3]);
        points.push_back(v[0]);
    }
    vp->setCoords(points);
}

// ---------------------------------------

TaskCrossSections::TaskCrossSections(const Base::BoundBox3d& bb)
{
    App::Document* document =
        App::GetApplication().getActiveDocument();
    if (document) {
        setDocumentName(document->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    widget = new CrossSections(bb);
    addTaskBox(Gui::BitmapFactory().pixmap("Mesh_CrossSections"), widget, true);
}

bool TaskCrossSections::accept()
{
    widget->accept();
    return (widget->result() == QDialog::Accepted);
}

void TaskCrossSections::clicked(int id)
{
    if (id == QDialogButtonBox::Apply) {
        widget->apply();
    }
}

#include "moc_CrossSections.cpp"
