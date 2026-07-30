// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <algorithm>
#include <exception>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <utility>

#include <QKeyEvent>

#include <TopoDS.hxx>

#include <Inventor/nodes/SoBaseColor.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoDrawStyle.h>
#include <Inventor/nodes/SoLineSet.h>
#include <Inventor/nodes/SoSeparator.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/GeoFeatureGroupExtension.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Sequencer.h>
#include <Base/Tools.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Macro.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/ViewProvider.h>
#include <Mod/Part/App/CrossSection.h>
#include <Mod/Part/App/FeatureCrossSections.h>
#include <Mod/Part/App/PartFeature.h>

#include "CrossSections.h"
#include "ModelingSelection.h"
#include "ui_CrossSections.h"

#include <QMessageBox>
#include <Gui/MainWindow.h>


using namespace PartGui;

namespace PartGui
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
}  // namespace PartGui

namespace
{
std::string pythonString(const std::string& value)
{
    return "'" + Base::Tools::escapeEncodeString(value) + "'";
}

std::string pythonObjectReference(const App::DocumentObject* object)
{
    if (!object || !object->getDocument() || !object->getNameInDocument()) {
        return "None";
    }
    return "App.getDocument(" + pythonString(object->getDocument()->getName())
        + ").getObject(" + pythonString(object->getNameInDocument()) + ")";
}

std::string pythonStringList(const std::vector<std::string>& values)
{
    std::ostringstream stream;
    stream << '[';
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index) {
            stream << ',';
        }
        stream << pythonString(values[index]);
    }
    stream << ']';
    return stream.str();
}

std::string pythonFloat(double value)
{
    std::ostringstream stream;
    stream.precision(17);
    stream << value;
    return stream.str();
}

std::string pythonFloatList(const std::vector<double>& values)
{
    std::ostringstream stream;
    stream << '[';
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index) {
            stream << ',';
        }
        stream << pythonFloat(values[index]);
    }
    stream << ']';
    return stream.str();
}

struct CrossSectionsState
{
    std::vector<Gui::SelectionObject> sources;
    std::vector<App::DocumentObject*> appliedResults;
};

std::map<const CrossSections*, CrossSectionsState>& crossSectionsStates()
{
    static std::map<const CrossSections*, CrossSectionsState> states;
    return states;
}

void recordAcceptedCrossSection(
    const Part::CrossSections& result,
    const Gui::SelectionObject& selected,
    const Base::Vector3d& normal,
    const std::vector<double>& positions
)
{
    if (!Gui::Application::Instance
        || !Gui::Application::Instance->macroManager()) {
        return;
    }
    auto* manager = Gui::Application::Instance->macroManager();
    auto* source = selected.getObject();
    const auto& subElements = selected.getSubNames();
    manager->addLine(Gui::MacroManager::App, "import Part");
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_cross_doc = App.getDocument("
         + pythonString(result.getDocument()->getName()) + ")")
            .c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_cross = __vibecad_cross_doc.addObject('Part::CrossSections',"
         + pythonString(result.getNameInDocument()) + ")")
            .c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_cross.Source = (" + pythonObjectReference(source) + ","
         + pythonStringList(subElements) + ")")
            .c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_cross.PlaneNormal = App.Vector(" + pythonFloat(normal.x) + ","
         + pythonFloat(normal.y) + "," + pythonFloat(normal.z) + ")")
            .c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_cross.PlanePositions = " + pythonFloatList(positions)).c_str()
    );
    auto* parent = App::GeoFeatureGroupExtension::getGroupOfObject(&result);
    if (parent) {
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_cross_parent = " + pythonObjectReference(parent)).c_str()
        );
        manager->addLine(
            Gui::MacroManager::App,
            "__vibecad_cross_parent.addObject(__vibecad_cross)"
        );
        manager->addLine(
            Gui::MacroManager::App,
            "if hasattr(__vibecad_cross_parent, 'Tip'): "
            "__vibecad_cross_parent.Tip = __vibecad_cross"
        );
    }
    manager->addLine(Gui::MacroManager::App, "__vibecad_cross_doc.recompute()");
    manager->addLine(
        Gui::MacroManager::App,
        parent
            ? "del __vibecad_cross_parent, __vibecad_cross, __vibecad_cross_doc"
            : "del __vibecad_cross, __vibecad_cross_doc"
    );
}

class CrossSectionBatch
{
public:
    explicit CrossSectionBatch(const std::set<App::Document*>& documents)
    {
        try {
            auto& application = App::GetApplication();
            if (application.getGlobalTransaction() != App::NullTransaction) {
                throw Base::RuntimeError(
                    "Cannot create cross-sections while another global operation is active"
                );
            }
            states.reserve(documents.size());
            for (auto* document : documents) {
                if (!document) {
                    throw Base::RuntimeError(
                        "A cross-section source document is unavailable"
                    );
                }
                if (document->hasPendingTransaction()
                    || document->getBookedTransactionID()
                        != App::NullTransaction) {
                    throw Base::RuntimeError(
                        "Cannot create cross-sections while another operation is active"
                    );
                }

                states.emplace_back();
                auto& state = states.back();
                state.document = document;
                if (Gui::Application::Instance) {
                    auto* guiDocument =
                        Gui::Application::Instance->getDocument(
                            document->getName()
                        );
                    if (guiDocument) {
                        state.hadGuiDocument = true;
                        state.guiDocumentModified =
                            guiDocument->isModified();
                    }
                }
                for (auto* object : document->getObjects()) {
                    if (object) {
                        state.initialObjectIds.insert(object->getID());
                    }
                }
            }

            std::vector<App::Document*> exactDocuments;
            exactDocuments.reserve(states.size());
            for (const auto& state : states) {
                exactDocuments.push_back(state.document);
            }
            transaction =
                std::make_unique<Gui::ExactTransaction>(
                    exactDocuments,
                    "Create cross-sections"
                );
            transactionId = transaction->id();
            if (!transaction->ownsCurrentTransaction()) {
                throw Base::RuntimeError(
                    "Could not establish the cross-section transaction"
                );
            }
        }
        catch (...) {
            rollback();
            throw;
        }
    }

    CrossSectionBatch(const CrossSectionBatch&) = delete;
    CrossSectionBatch& operator=(const CrossSectionBatch&) = delete;

    ~CrossSectionBatch()
    {
        rollback();
    }

    void track(App::DocumentObject& object)
    {
        auto found = std::ranges::find(
            states,
            object.getDocument(),
            &DocumentState::document
        );
        if (found == states.end() || !object.getNameInDocument()
            || found->initialObjectIds.contains(object.getID())) {
            throw Base::RuntimeError(
                "Cross-section result does not belong to this operation"
            );
        }
        found->createdObjects.push_back(
            {object.getID(), object.getNameInDocument()}
        );
    }

    void commit(const std::vector<Part::CrossSections*>& results)
    {
        if (closed) {
            throw Base::RuntimeError(
                "Cross-section transaction is already closed"
            );
        }

        if (Gui::Application::Instance) {
            std::map<App::Document*, std::vector<long>> resultsByDocument;
            for (auto* result : results) {
                if (result && result->getDocument()) {
                    resultsByDocument[result->getDocument()].push_back(
                        result->getID()
                    );
                }
            }
            for (const auto& [document, ids] : resultsByDocument) {
                Gui::Application::Instance->prepareDurableTaskResults(
                    *document,
                    ids
                );
            }
        }
        std::map<App::Document*, std::vector<App::DocumentObject*>>
            groupedResultsByDocument;
        for (auto* result : results) {
            if (result && result->getDocument()) {
                groupedResultsByDocument[result->getDocument()].push_back(
                    result
                );
            }
        }
        for (const auto& [document, groupedResults] :
             groupedResultsByDocument) {
            Q_UNUSED(document);
            PartGui::groupModelingCommandOutputs(groupedResults);
        }

        for (auto& state : states) {
            state.document->recompute();
        }
        for (auto* result : results) {
            if (!result || !result->isValid()
                || result->Shape.getShape().isNull()
                || !result->Shape.getShape().isValid()) {
                const auto* status = result ? result->getStatusString() : nullptr;
                throw Base::RuntimeError(
                    status && *status
                        ? status
                        : "Cross-section result validation failed"
                );
            }
        }

        for (auto& state : states) {
            if (state.document->getBookedTransactionID()
                    != transactionId
                || !state.document->hasPendingTransaction()) {
                throw Base::RuntimeError(
                    "Cross-section transaction ownership changed"
                );
            }
        }
        if (!transaction || !transaction->commit()) {
            throw Base::RuntimeError(
                "The cross-section transaction could not be committed"
            );
        }
        transaction.reset();
        transactionId = App::NullTransaction;
        closed = true;
    }

private:
    struct ObjectIdentity
    {
        long id = -1;
        std::string name;
    };

    struct DocumentState
    {
        App::Document* document = nullptr;
        bool hadGuiDocument = false;
        bool guiDocumentModified = false;
        std::set<long> initialObjectIds;
        std::vector<ObjectIdentity> createdObjects;
    };

    static void restoreModifiedState(const DocumentState& state) noexcept
    {
        if (!state.hadGuiDocument || !state.document
            || !Gui::Application::Instance) {
            return;
        }
        try {
            auto* guiDocument =
                Gui::Application::Instance->getDocument(
                    state.document->getName()
                );
            if (guiDocument) {
                guiDocument->setModified(state.guiDocumentModified);
            }
        }
        catch (...) {
            try {
                Base::Console().error(
                    "Could not restore the failed cross-section modified state.\n"
                );
            }
            catch (...) {
            }
        }
    }

    void rollback() noexcept
    {
        if (closed) {
            return;
        }
        bool transactionAborted = true;
        try {
            if (transaction && !transaction->abort()) {
                transactionAborted = false;
                Base::Console().error(
                    "The failed cross-section transaction could not be aborted.\n"
                );
            }
        }
        catch (...) {
            transactionAborted = false;
        }
        if (!transactionAborted) {
            return;
        }
        closed = true;
        transaction.reset();
        transactionId = App::NullTransaction;
        for (auto state = states.rbegin(); state != states.rend(); ++state) {
            if (state->document) {
                for (auto object = state->createdObjects.rbegin();
                     object != state->createdObjects.rend();
                     ++object) {
                    try {
                        auto* current =
                            state->document->getObjectByID(object->id);
                        if (current && current->getNameInDocument()
                            && object->name
                                == current->getNameInDocument()) {
                            state->document->removeObject(
                                object->name.c_str()
                            );
                        }
                    }
                    catch (...) {
                    }
                }
            }
            restoreModifiedState(*state);
        }
    }

    std::vector<DocumentState> states;
    std::unique_ptr<Gui::ExactTransaction> transaction;
    int transactionId = App::NullTransaction;
    bool closed = false;
};
}  // namespace

CrossSections::CrossSections(const Base::BoundBox3d& bb, QWidget* parent, Qt::WindowFlags fl)
    : CrossSections(bb, PartGui::getModelingShapeSelection(), parent, fl)
{}

CrossSections::CrossSections(
    const Base::BoundBox3d& bb,
    std::vector<Gui::SelectionObject> sources,
    QWidget* parent,
    Qt::WindowFlags fl
)
    : QDialog(parent, fl)
    , ui(new Ui_CrossSections)
    , bbox(bb)
{
    crossSectionsStates().emplace(
        this,
        CrossSectionsState {std::move(sources), {}}
    );
    ui->setupUi(this);
    setupConnections();

    constexpr double max = std::numeric_limits<double>::max();
    ui->position->setRange(-max, max);
    ui->position->setUnit(Base::Unit::Length);
    ui->distance->setRange(0, max);
    ui->distance->setUnit(Base::Unit::Length);
    vp = new ViewProviderCrossSections();

    Base::Vector3d c = bbox.GetCenter();
    calcPlane(CrossSections::XY, c.z);
    ui->position->setValue(c.z);

    Gui::Document* doc = Gui::Application::Instance->activeDocument();
    view = qobject_cast<Gui::View3DInventor*>(doc->getActiveView());
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
    if (view) {
        view->getViewer()->removeViewProvider(vp);
    }
    delete vp;
    crossSectionsStates().erase(this);
}

void CrossSections::setupConnections()
{
    connect(ui->xyPlane, &QRadioButton::clicked, this, &CrossSections::xyPlaneClicked);
    connect(ui->xzPlane, &QRadioButton::clicked, this, &CrossSections::xzPlaneClicked);
    connect(ui->yzPlane, &QRadioButton::clicked, this, &CrossSections::yzPlaneClicked);
    connect(
        ui->position,
        qOverload<double>(&Gui::QuantitySpinBox::valueChanged),
        this,
        &CrossSections::positionValueChanged
    );
    connect(
        ui->distance,
        qOverload<double>(&Gui::QuantitySpinBox::valueChanged),
        this,
        &CrossSections::distanceValueChanged
    );
    connect(
        ui->countSections,
        qOverload<int>(&QSpinBox::valueChanged),
        this,
        &CrossSections::countSectionsValueChanged
    );
    connect(ui->checkBothSides, &QCheckBox::toggled, this, &CrossSections::checkBothSidesToggled);
    connect(ui->sectionsBox, &QGroupBox::toggled, this, &CrossSections::sectionsBoxToggled);
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
    if (apply()) {
        QDialog::accept();
    }
}

bool CrossSections::apply()
{
    auto& state = crossSectionsStates().at(this);
    auto& sources = state.sources;
    state.appliedResults.clear();
    if (sources.empty()) {
        QMessageBox::critical(
            Gui::getMainWindow(),
            tr("Cannot compute cross-sections"),
            tr("Select at least one valid source shape.")
        );
        return false;
    }

    std::set<App::Document*> documents;
    for (const auto& selected : sources) {
        auto* object = selected.getObject();
        if (object && object->getDocument()) {
            documents.insert(object->getDocument());
        }
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

    auto reportFailure = [](const QString& message) {
        QMessageBox::critical(
            Gui::getMainWindow(),
            tr("Cannot compute cross-sections"),
            message
        );
        return false;
    };

    try {
        CrossSectionBatch batch(documents);
        Base::SequencerLauncher seq("Cross-sections…", sources.size() * 2);
        std::vector<Part::CrossSections*> results;
        results.reserve(sources.size());
        for (auto& selected : sources) {
            auto* source = selected.getObject();
            if (!source || !source->getDocument()) {
                throw Base::RuntimeError(
                    "A selected source is no longer available"
                );
            }

            App::Document* doc = source->getDocument();
            std::string s = source->getNameInDocument();
            s += "_cs";
            auto* section =
                doc->addObject<Part::CrossSections>(s.c_str());
            if (!section) {
                throw Base::RuntimeError(
                    "Could not create a cross-section result"
                );
            }
            batch.track(*section);
            section->Source.setValue(
                source,
                std::vector<std::string>(selected.getSubNames())
            );
            section->PlaneNormal.setValue(Base::Vector3d(a, b, c));
            section->PlanePositions.setValues(d);
            results.push_back(section);
            seq.next();
        }

        for (auto* document : documents) {
            document->recompute();
        }
        for (auto* result : results) {
            if (!result || !result->isValid() || result->Shape.getShape().isNull()
                || !result->Shape.getShape().isValid()) {
                const auto* status = result ? result->getStatusString() : nullptr;
                throw Base::RuntimeError(
                    status && *status
                        ? status
                        : "Cross-section result validation failed"
                );
            }
            seq.next();
        }
        batch.commit(results);
        state.appliedResults.assign(results.begin(), results.end());
        const Base::Vector3d normal(a, b, c);
        for (std::size_t index = 0; index < results.size(); ++index) {
            try {
                recordAcceptedCrossSection(*results[index], sources[index], normal, d);
            }
            catch (...) {
                Base::Console().warning(
                    "A cross-section was committed, but its macro record could not be written.\n"
                );
            }
        }
    }
    catch (Base::Exception& error) {
        error.reportException();
        return reportFailure(QString::fromStdString(error.getMessage()));
    }
    catch (const std::exception& error) {
        return reportFailure(QString::fromUtf8(error.what()));
    }
    catch (...) {
        return reportFailure(tr("Unexpected failure while creating cross-sections."));
    }

    return true;
}

const std::vector<App::DocumentObject*>&
CrossSections::lastAppliedResults() const
{
    return crossSectionsStates().at(this).appliedResults;
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

void CrossSections::sectionsBoxToggled(bool b)
{
    if (b) {
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
    : TaskCrossSections(bb, PartGui::getModelingShapeSelection())
{}

TaskCrossSections::TaskCrossSections(
    const Base::BoundBox3d& bb,
    std::vector<Gui::SelectionObject> sources
)
{
    widget = new CrossSections(bb, std::move(sources));
    addTaskBox(Gui::BitmapFactory().pixmap("Part_CrossSections"), widget);
}

bool TaskCrossSections::accept()
{
    widget->accept();
    const bool accepted = widget->result() == QDialog::Accepted;
    if (accepted) {
        markCommandInteractionStateDurable(widget->lastAppliedResults());
    }
    return accepted;
}

void TaskCrossSections::clicked(int id)
{
    if (id == QDialogButtonBox::Apply) {
        if (widget->apply()) {
            markCommandInteractionStateDurable(widget->lastAppliedResults());
        }
    }
}

#include "moc_CrossSections.cpp"
