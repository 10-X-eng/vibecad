// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2004 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <FCConfig.h>

#ifdef FC_OS_WIN32
# include <windows.h>
#endif
#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <iterator>
#include <map>
#include <limits>
#include <string>
#include <unordered_map>
#include <vector>

#include <QApplication>
#include <QPointer>
#include <qfileinfo.h>
#include <qinputdialog.h>
#include <qmessagebox.h>
#include <qstringlist.h>

#include <Gui/InventorAll.h>

#include <App/ComplexGeoData.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectGroup.h>
#include <App/DocumentObserver.h>
#include <App/PropertyGeo.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/FileDialog.h>
#include <Gui/Macro.h>
#include <Gui/MainWindow.h>
#include <Gui/MouseSelection.h>
#include <Gui/Navigation/NavigationStyle.h>
#include <Gui/Selection/Selection.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/WaitCursor.h>

#include <Mod/Mesh/App/Core/Smoothing.h>
#include <Mod/Mesh/App/Core/Triangulation.h>
#include <Mod/Mesh/App/FeatureMeshCurvature.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/Importer.h>
#include <Mod/Mesh/App/MeshFeature.h>

#include "DlgDecimating.h"
#include "DlgEvaluateMeshImp.h"
#include "DlgRegularSolidImp.h"
#include "DlgSmoothing.h"
#include "MeshEditor.h"
#include "CommandGuard.h"
#include "ParametricMeshFilter.h"
#include "RemeshGmsh.h"
#include "RemoveComponents.h"
#include "Segmentation.h"
#include "SegmentationBestFit.h"
#include "ViewProviderCurvature.h"
#include "ViewProviderMeshFaceSet.h"


using namespace Mesh;

namespace
{

App::Document* cleanActiveMeshDocument()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    return MeshGui::canStartNativeMeshCommand(document) ? document : nullptr;
}

template<typename Object>
bool allObjectsBelongTo(const std::vector<Object*>& objects, const App::Document* document)
{
    return document && std::ranges::all_of(objects, [document](const Object* object) {
               return object && object->getDocument() == document
                   && MeshGui::isNativeMeshInputActive(object);
           });
}

void commitExactMutation(Gui::ExactTransaction& transaction)
{
    if (!transaction.commit()) {
        throw Base::RuntimeError("The mesh operation could not be committed");
    }
}

bool allMeshesNonEmpty(const std::vector<App::DocumentObject*>& objects)
{
    return std::ranges::all_of(objects, [](const App::DocumentObject* object) {
        const auto* mesh = freecad_cast<const Mesh::Feature*>(object);
        return mesh && mesh->Mesh.getValue().countFacets() > 0;
    });
}

template<typename Object>
bool allMeshesNonEmpty(const std::vector<Object*>& objects)
{
    return std::ranges::all_of(objects, [](const Object* object) {
        const auto* mesh = freecad_cast<const Mesh::Feature*>(object);
        return mesh && mesh->Mesh.getValue().countFacets() > 0;
    });
}

template<typename Object>
bool anyObjectVisible(const std::vector<Object*>& objects)
{
    return std::ranges::any_of(objects, [](const Object* object) {
        auto* viewProvider = object
            ? Gui::Application::Instance->getViewProvider(const_cast<Object*>(object))
            : nullptr;
        return viewProvider && viewProvider->isVisible();
    });
}

template<typename Object>
bool allObjectsVisible(const std::vector<Object*>& objects)
{
    return std::ranges::all_of(objects, [](const Object* object) {
        auto* viewProvider = object
            ? Gui::Application::Instance->getViewProvider(const_cast<Object*>(object))
            : nullptr;
        return viewProvider && viewProvider->isVisible();
    });
}

template<typename Object>
bool documentHasVisibleObject(const App::Document* document)
{
    if (!document) {
        return false;
    }
    const auto objects = document->getObjectsOfType<Object>();
    return std::ranges::any_of(objects, [](const Object* object) {
        auto* viewProvider = object
            ? Gui::Application::Instance->getViewProvider(const_cast<Object*>(object))
            : nullptr;
        return MeshGui::isNativeMeshInputActive(object) && viewProvider && viewProvider->isVisible();
    });
}

bool documentHasVisibleNonEmptyMesh(const App::Document* document)
{
    if (!document) {
        return false;
    }
    const auto meshes = document->getObjectsOfType(Mesh::Feature::getClassTypeId());
    return std::ranges::any_of(meshes, [](const App::DocumentObject* object) {
        const auto* mesh = freecad_cast<const Mesh::Feature*>(object);
        auto* viewProvider = mesh ? Gui::Application::Instance->getViewProvider(mesh) : nullptr;
        return mesh && MeshGui::isNativeMeshInputActive(mesh)
            && mesh->Mesh.getValue().countFacets() > 0 && viewProvider && viewProvider->isVisible();
    });
}

void runNativeMeshBoolean(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const char* objectName,
    const char* operation,
    const char* transactionName,
    const char* translationContext,
    const char* dialogTitle
)
{
    try {
        Base::Interpreter().loadModule("MeshPart");
        Gui::WaitCursor wait;
        Gui::ExactTransaction mutation(document, transactionName);
        const std::string uniqueName = document.getUniqueObjectName(objectName);
        auto* resultObject = document.addObject("MeshPart::Boolean", uniqueName.c_str());
        auto* result = freecad_cast<Mesh::Feature*>(resultObject);
        if (!result) {
            throw Base::RuntimeError("The parametric mesh boolean object could not be created.");
        }
        auto* source1 = freecad_cast<App::PropertyLink*>(result->getPropertyByName("Source1"));
        auto* source2 = freecad_cast<App::PropertyLink*>(result->getPropertyByName("Source2"));
        auto* operationProperty = freecad_cast<App::PropertyEnumeration*>(
            result->getPropertyByName("Operation")
        );
        if (!source1 || !source2 || !operationProperty) {
            throw Base::RuntimeError("The parametric mesh boolean type has an invalid property "
                                     "contract.");
        }
        source1->setValue(sources.front());
        source2->setValue(sources.back());
        operationProperty->setValue(operation);

        if (!result->recomputeFeature() || result->isError()) {
            throw Base::RuntimeError(result->getStatusString());
        }
        if (result->Mesh.getValue().countFacets() == 0 || !result->Mesh.getValue().isSolid()) {
            throw Base::RuntimeError("The mesh boolean did not produce a non-empty closed solid.");
        }

        std::vector<App::DocumentObject*> replacedInputs;
        replacedInputs.reserve(sources.size());
        for (auto* source : sources) {
            auto* view = Gui::Application::Instance->getViewProvider(source);
            if (view && view->isVisible()) {
                replacedInputs.push_back(source);
            }
        }
        MeshGui::markMeshTimelineReplacement(*result, replacedInputs);
        for (auto* source : sources) {
            if (auto* view = Gui::Application::Instance->getViewProvider(source)) {
                view->setVisible(false);
            }
        }
        commitExactMutation(mutation);
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate(translationContext, dialogTitle),
            QString::fromUtf8(error.what())
        );
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate(translationContext, dialogTitle),
            QString::fromUtf8(error.what())
        );
    }
    catch (...) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate(translationContext, dialogTitle),
            qApp->translate(translationContext, "The native mesh boolean failed unexpectedly.")
        );
    }
}

void runParametricMeshFilter(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const char* typeName,
    const char* objectName,
    const char* objectLabel,
    const char* transactionName,
    const char* dialogTitle,
    const std::function<void(App::DocumentObject&)>& configure = {}
)
{
    try {
        std::vector<MeshGui::ParametricMeshFilterTarget> targets;
        targets.reserve(sources.size());
        for (auto* source : sources) {
            targets.push_back(MeshGui::ParametricMeshFilterTarget {
                freecad_cast<Mesh::Feature*>(source),
                configure,
            });
        }
        MeshGui::createParametricMeshFilters(
            document,
            targets,
            MeshGui::ParametricMeshFilterSpec {
                typeName,
                objectName,
                objectLabel,
                transactionName,
            }
        );
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QString::fromUtf8(dialogTitle),
            QString::fromUtf8(error.what())
        );
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QString::fromUtf8(dialogTitle),
            QString::fromUtf8(error.what())
        );
    }
    catch (...) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QString::fromUtf8(dialogTitle),
            qApp->translate("Mesh", "The parametric mesh operation failed unexpectedly.")
        );
    }
}

}  // namespace


DEF_STD_CMD_A(CmdMeshUnion)

CmdMeshUnion::CmdMeshUnion()
    : Command("Mesh_Union")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Union");
    sToolTipText = QT_TR_NOOP("Unifies the selected meshes");
    sWhatsThis = "Mesh_Union";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Union";
}

void CmdMeshUnion::activated(int)
{
    std::vector<App::DocumentObject*> obj = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (obj.size() != 2 || !allObjectsBelongTo(obj, document) || !allMeshesNonEmpty(obj)) {
        return;
    }
    runNativeMeshBoolean(
        *document,
        obj,
        "Union",
        "Union",
        QT_TRANSLATE_NOOP("Command", "Mesh union"),
        "Mesh_Union",
        "Mesh Union"
    );
}

bool CmdMeshUnion::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() == 2 && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshDifference)

CmdMeshDifference::CmdMeshDifference()
    : Command("Mesh_Difference")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Difference");
    sToolTipText = QT_TR_NOOP("Creates a boolean difference of the selected meshes");
    sWhatsThis = "Mesh_Difference";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Difference";
}

void CmdMeshDifference::activated(int)
{
    std::vector<App::DocumentObject*> obj = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (obj.size() != 2 || !allObjectsBelongTo(obj, document) || !allMeshesNonEmpty(obj)) {
        return;
    }
    runNativeMeshBoolean(
        *document,
        obj,
        "Difference",
        "Difference",
        QT_TRANSLATE_NOOP("Command", "Mesh difference"),
        "Mesh_Difference",
        "Mesh Difference"
    );
}

bool CmdMeshDifference::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() == 2 && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshIntersection)

CmdMeshIntersection::CmdMeshIntersection()
    : Command("Mesh_Intersection")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Intersection");
    sToolTipText = QT_TR_NOOP("Creates a boolean intersection from the selected meshes");
    sWhatsThis = "Mesh_Intersection";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Intersection";
}

void CmdMeshIntersection::activated(int)
{
    std::vector<App::DocumentObject*> obj = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (obj.size() != 2 || !allObjectsBelongTo(obj, document) || !allMeshesNonEmpty(obj)) {
        return;
    }
    runNativeMeshBoolean(
        *document,
        obj,
        "Intersection",
        "Intersection",
        QT_TRANSLATE_NOOP("Command", "Mesh intersection"),
        "Mesh_Intersection",
        "Mesh Intersection"
    );
}

bool CmdMeshIntersection::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() == 2 && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshImport)

CmdMeshImport::CmdMeshImport()
    : Command("Mesh_Import")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Import Mesh…");
    sToolTipText = QT_TR_NOOP("Imports a mesh from a file");
    sWhatsThis = "Mesh_Import";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Import";
}

void CmdMeshImport::activated(int)
{
    App::Document* launchDocument = cleanActiveMeshDocument();
    if (!launchDocument) {
        return;
    }
    App::DocumentWeakPtrT targetDocument(launchDocument);

    const Gui::FileDialog::FilterList filter {
        {QObject::tr("All Mesh Files"),
         {"*.stl", "*.ast", "*.bms", "*.obj", "*.off", "*.iv", "*.ply", "*.nas", "*.bdf"}},
        {QObject::tr("Binary STL"), {"*.stl"}},
        {QObject::tr("ASCII STL"), {"*.ast"}},
        {QObject::tr("Binary Mesh"), {"*.bms"}},
        {QObject::tr("Alias Mesh"), {"*.obj"}},
        {QObject::tr("Object File Format"), {"*.off"}},
        {QObject::tr("Inventor V2.1 ASCII"), {"*.iv"}},
        {QObject::tr("Stanford Polygon"), {"*.ply"}},
        {QStringLiteral("NASTRAN"), {"*.nas", "*.bdf"}},
        Gui::FileDialog::Filter::AllFiles(),
    };

    // Allow multi selection
    QStringList fn = Gui::FileDialog::getOpenFileNames(
        Gui::getMainWindow(),
        QObject::tr("Import Mesh"),
        QString(),
        filter
    );
    App::Document* document = *targetDocument;
    if (fn.isEmpty() || !MeshGui::canStartNativeMeshCommand(document)) {
        return;
    }

    try {
        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Import Mesh"));
        std::vector<App::DocumentObject*> outputs;
        Mesh::Importer importer(document);
        doCommand(Doc, "import Mesh");
        for (const auto& it : fn) {
            std::string unicodepath = Base::Tools::escapedUnicodeFromUtf8(it.toUtf8().data());
            unicodepath = Base::Tools::escapeEncodeFilename(unicodepath);
            const std::string recordedCommand = "Mesh.insert(u\"" + unicodepath + "\", \""
                + document->getName() + "\")";
            Gui::Application::Instance->macroManager()->addLine(
                Gui::MacroManager::App,
                recordedCommand.c_str()
            );

            auto imported = importer.loadWithResults(it.toUtf8().toStdString());
            if (imported.empty()) {
                throw Base::RuntimeError("The selected file did not contain an importable mesh");
            }
            for (auto* mesh : imported) {
                if (!mesh || !document->containsObject(mesh) || mesh->getDocument() != document) {
                    throw Base::RuntimeError("Mesh import returned an invalid result identity");
                }
                if (mesh->Mesh.getValue().countFacets() == 0) {
                    throw Base::RuntimeError("The selected file produced an empty mesh");
                }
                outputs.push_back(mesh);
            }
        }
        std::vector<std::string> importedFiles;
        importedFiles.reserve(fn.size());
        std::ranges::transform(fn, std::back_inserter(importedFiles), [](const QString& fileName) {
            return QFileInfo(fileName).fileName().toUtf8().toStdString();
        });
        MeshGui::createStandaloneOutputGroup(
            *document,
            outputs,
            importedFiles,
            "ImportedMeshes",
            "Imported Meshes",
            "Import meshes"
        );
        document->recompute();
        commitExactMutation(mutation);
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Import Mesh"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdMeshImport::isActive()
{
    return cleanActiveMeshDocument() != nullptr;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshExport)

CmdMeshExport::CmdMeshExport()
    : Command("Mesh_Export")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Export Mesh…");
    sToolTipText = QT_TR_NOOP("Exports a mesh to a file");
    sWhatsThis = "Mesh_Export";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Export";
    eType = 0;
}

void CmdMeshExport::activated(int)
{
    App::Document* document = App::GetApplication().getActiveDocument();
    auto meshes = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    if (meshes.size() != 1 || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)) {
        return;
    }
    App::DocumentObjectWeakPtrT target(meshes.front());

    // clang-format off
    QString dir =
        QString::fromUtf8(meshes.front()->Label.getValue());
    using Filter = Gui::FileDialog::Filter;
    QList<QPair<Filter, QByteArray> > ext;
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Binary STL"), {"*.stl"}}, "STL");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("ASCII STL"), {"*.stl"}}, "AST");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("ASCII STL"), {"*.ast"}}, "AST");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Binary Mesh"), {"*.bms"}}, "BMS");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Alias Mesh"), {"*.obj"}}, "OBJ");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Simple Model Format"), {"*.smf"}}, "SMF");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Object File Format"), {"*.off"}}, "OFF");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Inventor V2.1 ascii"), {"*.iv"}}, "IV");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("X3D Extensible 3D"), {"*.x3d"}}, "X3D");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Compressed X3D"), {"*.x3dz"}}, "X3DZ");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("WebGL/X3D"), {"*.xhtml"}}, "X3DOM");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Stanford Polygon"), {"*.ply"}}, "PLY");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("VRML V2.0"), {"*.wrl *.vrml"}}, "VRML");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Compressed VRML 2.0"), {"*.wrz"}}, "WRZ");
    ext << qMakePair<Filter, QByteArray>({QStringLiteral("NASTRAN"), {"*.nas *.bdf"}}, "NAS");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Python module def"), {"*.py"}}, "PY");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("Asymptote Format"), {"*.asy"}}, "ASY");
    ext << qMakePair<Filter, QByteArray>({QObject::tr("3D Manufacturing Format"), {"*.3mf"}}, "3MF");
    ext << qMakePair<Filter, QByteArray>(Filter::AllFiles(), ""); // Undefined
    // clang-format on
    Gui::FileDialog::FilterList filter;
    for (const auto& it : ext) {
        filter << it.first;
    }

    qsizetype formatIndex = -1;
    QString fn = Gui::FileDialog::getSaveFileName(
        Gui::getMainWindow(),
        QObject::tr("Export Mesh"),
        dir,
        filter,
        &formatIndex
    );
    if (!fn.isEmpty()) {
        auto* mesh = target.get<Mesh::Feature>();
        if (!mesh || mesh->getDocument() != document || !MeshGui::isNativeMeshInputActive(mesh)
            || mesh->Mesh.getValue().countFacets() == 0) {
            return;
        }
        QByteArray extension;
        if (formatIndex >= 0 && formatIndex < ext.size()) {
            extension = ext[formatIndex].second;
        }

        MeshGui::ViewProviderMesh* vp = dynamic_cast<MeshGui::ViewProviderMesh*>(
            Gui::Application::Instance->getViewProvider(mesh)
        );
        if (vp) {
            vp->exportMesh((const char*)fn.toUtf8(), (const char*)extension);
        }
    }
}

bool CmdMeshExport::isActive()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    return meshes.size() == 1 && allObjectsBelongTo(meshes, document) && allMeshesNonEmpty(meshes);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshFromGeometry)

CmdMeshFromGeometry::CmdMeshFromGeometry()
    : Command("Mesh_FromGeometry")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Mesh From Geometry");
    sToolTipText = QT_TR_NOOP("Creates a mesh from the selected geometry");
    sWhatsThis = "Mesh_FromGeometry";
    sStatusTip = sToolTipText;
}

void CmdMeshFromGeometry::activated(int)
{
    App::Document* launchDocument = cleanActiveMeshDocument();
    std::vector<App::DocumentObject*> selected = Gui::Selection().getObjectsOfType(
        App::GeoFeature::getClassTypeId()
    );
    if (!launchDocument || selected.empty() || !allObjectsBelongTo(selected, launchDocument)) {
        return;
    }
    App::DocumentWeakPtrT targetDocument(launchDocument);
    std::vector<App::DocumentObjectWeakPtrT> targets;
    targets.reserve(selected.size());
    for (auto* object : selected) {
        targets.emplace_back(object);
    }

    bool ok {};
    double tol = QInputDialog::getDouble(
        Gui::getMainWindow(),
        QObject::tr("Meshing Tolerance"),
        QObject::tr("Enter tolerance for meshing geometry:"),
        0.1,
        0.01,
        10.0,
        2,
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok) {
        return;
    }

    App::Document* document = *targetDocument;
    if (!MeshGui::canStartNativeMeshCommand(document)) {
        return;
    }

    std::vector<App::GeoFeature*> sources;
    sources.reserve(targets.size());
    for (const auto& target : targets) {
        auto* object = target.get<App::GeoFeature>();
        if (!object || object->getDocument() != document
            || !MeshGui::isNativeMeshInputActive(object)) {
            return;
        }
        if (object->isDerivedFrom<Mesh::Feature>()) {
            continue;
        }

        const auto* geometry = object->getPropertyOfGeometry();
        if (geometry && geometry->getComplexData()) {
            sources.push_back(object);
        }
    }
    if (sources.empty()) {
        return;
    }

    Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Mesh from geometry"));
    std::vector<App::DocumentObject*> outputs;
    outputs.reserve(sources.size());
    for (auto* source : sources) {
        auto* result = document->addObject<Mesh::MeshFromGeometry>("Mesh");
        result->Label.setValue(source->Label.getStrValue() + " (Meshed)");
        result->Source.setValue(source);
        result->Tolerance.setValue(tol);
        outputs.push_back(result);
    }
    document->recompute();
    if (std::ranges::any_of(outputs, [](const App::DocumentObject* output) {
            const auto* result = freecad_cast<const Mesh::MeshFromGeometry*>(output);
            return !result || result->Mesh.getValue().countFacets() == 0 || result->isError();
        })) {
        throw Base::RuntimeError("Geometry meshing produced an invalid result");
    }
    std::vector<App::DocumentObject*> sourceObjects(sources.begin(), sources.end());
    MeshGui::createSourcePreservingOutputGroup(
        *document,
        sourceObjects,
        outputs,
        "MeshesFromGeometry",
        "Meshes From Geometry",
        "Mesh from geometry"
    );
    commitExactMutation(mutation);
}

bool CmdMeshFromGeometry::isActive()
{
    App::Document* doc = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<App::GeoFeature>();
    return !objects.empty() && allObjectsBelongTo(objects, doc)
        && std::ranges::any_of(objects, [](const App::GeoFeature* object) {
               return object && !object->isDerivedFrom<Mesh::Feature>();
           });
}

//===========================================================================
// Mesh_FromPart
//===========================================================================
DEF_STD_CMD_A(CmdMeshFromPartShape)

CmdMeshFromPartShape::CmdMeshFromPartShape()
    : Command("Mesh_FromPartShape")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Mesh From Shape");
    sToolTipText = QT_TR_NOOP("Tessellates the selected shape to a mesh");
    sWhatsThis = "Mesh_FromPartShape";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_FromPartShape.svg";
}

void CmdMeshFromPartShape::activated(int)
{
    if (!isActive()) {
        return;
    }
    doCommand(Doc, "import MeshPartGui, FreeCADGui\nFreeCADGui.runCommand('MeshPart_Mesher')\n");
}

bool CmdMeshFromPartShape::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    if (!document) {
        return false;
    }

    for (const auto& selected : Gui::Selection().getSelection("*", Gui::ResolveMode::NoResolve)) {
        if (!selected.pObject || selected.pObject->getDocument() != document) {
            continue;
        }

        App::Property* shapeProperty = selected.pObject->getPropertyByName("Shape");
        if (!shapeProperty || !shapeProperty->isDerivedFrom<App::PropertyComplexGeoData>()) {
            continue;
        }

        const auto* geometryProperty = static_cast<const App::PropertyComplexGeoData*>(shapeProperty);
        const Data::ComplexGeoData* geometry = geometryProperty->getComplexData();
        if (geometry && geometry->countSubElements("Face") > 0) {
            return true;
        }
    }
    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshVertexCurvature)

CmdMeshVertexCurvature::CmdMeshVertexCurvature()
    : Command("Mesh_VertexCurvature")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Curvature Plot");
    sToolTipText = QT_TR_NOOP("Calculates the curvature of the vertices of a mesh");
    sWhatsThis = "Mesh_VertexCurvature";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_VertexCurvature";
}

void CmdMeshVertexCurvature::activated(int)
{
    std::vector<App::DocumentObject*> meshes = getSelection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (meshes.empty() || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)) {
        return;
    }
    Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Mesh VertexCurvature"));
    std::vector<std::pair<Mesh::Curvature*, Mesh::Feature*>> results;
    results.reserve(meshes.size());
    for (auto* object : meshes) {
        auto* source = static_cast<Mesh::Feature*>(object);
        std::string fName = source->getNameInDocument();
        fName += "_Curvature";
        fName = document->getUniqueObjectName(fName);

        auto* result = document->addObject<Mesh::Curvature>(fName.c_str());
        if (auto* group = App::DocumentObjectGroup::getGroupOfObject(source)) {
            if (auto* extension = group->getExtensionByType<App::GroupExtension>()) {
                extension->addObject(result);
            }
        }
        result->Source.setValue(source);
        results.emplace_back(result, source);
    }

    document->recompute();
    for (const auto& [result, source] : results) {
        if (!result || result->isError() || result->Source.getValue() != source
            || result->CurvInfo.getSize() != static_cast<int>(source->Mesh.getValue().countPoints())) {
            throw Base::RuntimeError("Curvature calculation did not produce a valid result");
        }
    }
    std::vector<App::DocumentObject*> outputs;
    outputs.reserve(results.size());
    std::ranges::transform(results, std::back_inserter(outputs), [](const auto& result) {
        return result.first;
    });
    MeshGui::createSourcePreservingOutputGroup(
        *document,
        meshes,
        outputs,
        "MeshCurvatureResults",
        "Mesh Curvature",
        "Calculate mesh curvature"
    );
    commitExactMutation(mutation);
}

bool CmdMeshVertexCurvature::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshVertexCurvatureInfo)

CmdMeshVertexCurvatureInfo::CmdMeshVertexCurvatureInfo()
    : Command("Mesh_CurvatureInfo")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Curvature Info");
    sToolTipText = QT_TR_NOOP("Displays information about the curvature");
    sWhatsThis = "Mesh_CurvatureInfo";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_CurvatureInfo";
    eType = Alter3DView;
}

void CmdMeshVertexCurvatureInfo::activated(int)
{
    if (!isActive()) {
        return;
    }
    App::Document* document = App::GetApplication().getActiveDocument();
    Gui::Document* doc = Gui::Application::Instance->getDocument(document);
    auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
    if (view) {
        Gui::View3DInventorViewer* viewer = view->getViewer();
        viewer->setEditing(true);
        viewer->setRedirectToSceneGraph(true);
        viewer->setSelectionEnabled(false);
        viewer->setEditingCursor(
            QCursor(Gui::BitmapFactory().pixmapFromSvg("Mesh_Pipette", QSize(32, 32)), 4, 29)
        );
        viewer->addEventCallback(
            SoEvent::getClassTypeId(),
            MeshGui::ViewProviderMeshCurvature::curvatureInfoCallback
        );
    }
}

bool CmdMeshVertexCurvatureInfo::isActive()
{
    App::Document* doc = App::GetApplication().getActiveDocument();
    if (!doc || !documentHasVisibleObject<Mesh::Curvature>(doc)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshPolySegm)

CmdMeshPolySegm::CmdMeshPolySegm()
    : Command("Mesh_PolySegm")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Segment");
    sToolTipText = QT_TR_NOOP("Creates a mesh segment");
    sWhatsThis = "Mesh_PolySegm";
    sStatusTip = sToolTipText;
    sPixmap = "PolygonPick";
}

void CmdMeshPolySegm::activated(int)
{
    std::vector<App::DocumentObject*> docObj = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (docObj.empty() || !allObjectsBelongTo(docObj, document) || !allMeshesNonEmpty(docObj)
        || !allObjectsVisible(docObj)) {
        return;
    }
    for (std::vector<App::DocumentObject*>::iterator it = docObj.begin(); it != docObj.end(); ++it) {
        if (it == docObj.begin()) {
            Gui::Document* doc = Gui::Application::Instance->getDocument(document);
            auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
            if (view) {
                Gui::View3DInventorViewer* viewer = view->getViewer();
                viewer->setEditing(true);
                viewer->startSelection(Gui::View3DInventorViewer::Clip);
                viewer->addEventCallback(
                    SoMouseButtonEvent::getClassTypeId(),
                    MeshGui::ViewProviderMeshFaceSet::segmMeshCallback
                );
            }
            else {
                return;
            }
        }

        Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
        Gui::ViewProvider* pVP = guiDocument ? guiDocument->getViewProvider(*it) : nullptr;
        if (pVP && pVP->isVisible()) {
            pVP->startEditing();
        }
    }
}

bool CmdMeshPolySegm::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    if (objects.empty() || !allObjectsBelongTo(objects, document) || !allMeshesNonEmpty(objects)
        || !allObjectsVisible(objects)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}


DEF_STD_CMD_A(CmdMeshAddFacet)

CmdMeshAddFacet::CmdMeshAddFacet()
    : Command("Mesh_AddFacet")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Add Triangle");
    sToolTipText = QT_TR_NOOP("Adds a triangle manually to a mesh");
    sWhatsThis = "Mesh_AddFacet";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_AddFacet";
}

void CmdMeshAddFacet::activated(int)
{
    auto meshes = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    App::Document* document = cleanActiveMeshDocument();
    if (meshes.size() != 1 || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)
        || !anyObjectVisible(meshes)) {
        return;
    }

    auto meshObj = meshes.front();
    Gui::Document* doc = Gui::Application::Instance->getDocument(meshObj->getDocument());
    auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
    auto* viewProvider = dynamic_cast<MeshGui::ViewProviderMesh*>(
        Gui::Application::Instance->getViewProvider(meshObj)
    );
    if (view && viewProvider && viewProvider->isVisible()) {
        auto edit = new MeshGui::MeshFaceAddition(view);
        edit->startEditing(viewProvider);
    }
}

bool CmdMeshAddFacet::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    if (objects.size() != 1 || !allObjectsBelongTo(objects, document) || !allMeshesNonEmpty(objects)
        || !allObjectsVisible(objects)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshPolyCut)

CmdMeshPolyCut::CmdMeshPolyCut()
    : Command("Mesh_PolyCut")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Cut");
    sToolTipText = QT_TR_NOOP("Cuts the mesh with a selected polygon");
    sWhatsThis = "Mesh_PolyCut";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_PolyCut";
}

void CmdMeshPolyCut::activated(int)
{
    std::vector<App::DocumentObject*> docObj = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (docObj.empty() || !allObjectsBelongTo(docObj, document) || !allMeshesNonEmpty(docObj)
        || !allObjectsVisible(docObj)) {
        return;
    }
    for (std::vector<App::DocumentObject*>::iterator it = docObj.begin(); it != docObj.end(); ++it) {
        if (it == docObj.begin()) {
            Gui::Document* doc = Gui::Application::Instance->getDocument(document);
            auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
            if (view) {
                Gui::View3DInventorViewer* viewer = view->getViewer();
                viewer->setEditing(true);

                Gui::PolyClipSelection* clip = new Gui::PolyClipSelection();
                clip->setRole(Gui::SelectionRole::Split, true);
                clip->setColor(0.0f, 0.0f, 1.0f);
                clip->setLineWidth(1.0f);
                viewer->navigationStyle()->startSelection(clip);
                viewer->addEventCallback(
                    SoMouseButtonEvent::getClassTypeId(),
                    MeshGui::ViewProviderMeshFaceSet::clipMeshCallback
                );
            }
            else {
                return;
            }
        }

        Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
        Gui::ViewProvider* pVP = guiDocument ? guiDocument->getViewProvider(*it) : nullptr;
        if (pVP && pVP->isVisible()) {
            pVP->startEditing();
        }
    }
}

bool CmdMeshPolyCut::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    if (objects.empty() || !allObjectsBelongTo(objects, document) || !allMeshesNonEmpty(objects)
        || !allObjectsVisible(objects)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshPolyTrim)

CmdMeshPolyTrim::CmdMeshPolyTrim()
    : Command("Mesh_PolyTrim")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Trim");
    sToolTipText = QT_TR_NOOP("Trims a mesh with a selected polygon");
    sWhatsThis = "Mesh_PolyTrim";
    sStatusTip = QT_TR_NOOP("Trims a mesh with a picked polygon");
    sPixmap = "Mesh_PolyTrim";
}

void CmdMeshPolyTrim::activated(int)
{
    std::vector<App::DocumentObject*> docObj = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (docObj.empty() || !allObjectsBelongTo(docObj, document) || !allMeshesNonEmpty(docObj)
        || !allObjectsVisible(docObj)) {
        return;
    }
    for (std::vector<App::DocumentObject*>::iterator it = docObj.begin(); it != docObj.end(); ++it) {
        if (it == docObj.begin()) {
            Gui::Document* doc = Gui::Application::Instance->getDocument(document);
            auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
            if (view) {
                Gui::View3DInventorViewer* viewer = view->getViewer();
                viewer->setEditing(true);

                Gui::PolyClipSelection* clip = new Gui::PolyClipSelection();
                clip->setRole(Gui::SelectionRole::Split, true);
                clip->setColor(0.0f, 0.0f, 1.0f);
                clip->setLineWidth(1.0f);
                viewer->navigationStyle()->startSelection(clip);
                viewer->addEventCallback(
                    SoMouseButtonEvent::getClassTypeId(),
                    MeshGui::ViewProviderMeshFaceSet::trimMeshCallback
                );
            }
            else {
                return;
            }
        }

        Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
        Gui::ViewProvider* pVP = guiDocument ? guiDocument->getViewProvider(*it) : nullptr;
        if (pVP && pVP->isVisible()) {
            pVP->startEditing();
        }
    }
}

bool CmdMeshPolyTrim::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    if (objects.empty() || !allObjectsBelongTo(objects, document) || !allMeshesNonEmpty(objects)
        || !allObjectsVisible(objects)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshTrimByPlane)

CmdMeshTrimByPlane::CmdMeshTrimByPlane()
    : Command("Mesh_TrimByPlane")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Trim With Plane");
    sToolTipText = QT_TR_NOOP("Trims a mesh by removing faces on one side of a selected plane");
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_TrimByPlane";
}

void CmdMeshTrimByPlane::activated(int)
{
    if (!isActive()) {
        return;
    }
    const char* cmd = "import MeshPartGui\n"
                      "import FreeCADGui\n"
                      "FreeCADGui.runCommand('MeshPart_TrimByPlane')\n";
    runCommand(Doc, cmd);
}

bool CmdMeshTrimByPlane::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    Base::Type planeType = Base::Type::fromName("Part::Plane");
    auto planes = getSelection().getObjectsOfType(planeType);
    return meshes.size() == 1 && planes.size() == 1 && allObjectsBelongTo(meshes, document)
        && allObjectsBelongTo(planes, document) && allMeshesNonEmpty(meshes);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshSectionByPlane)

CmdMeshSectionByPlane::CmdMeshSectionByPlane()
    : Command("Mesh_SectionByPlane")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Section From Plane");
    sToolTipText = QT_TR_NOOP("Sections the mesh with the selected plane");
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_SectionByPlane";
}

void CmdMeshSectionByPlane::activated(int)
{
    if (!isActive()) {
        return;
    }
    const char* cmd = "import MeshPartGui\n"
                      "import FreeCADGui\n"
                      "FreeCADGui.runCommand('MeshPart_SectionByPlane')\n";
    runCommand(Doc, cmd);
}

bool CmdMeshSectionByPlane::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    Base::Type planeType = Base::Type::fromName("Part::Plane");
    auto planes = getSelection().getObjectsOfType(planeType);
    return meshes.size() == 1 && planes.size() == 1 && allObjectsBelongTo(meshes, document)
        && allObjectsBelongTo(planes, document) && allMeshesNonEmpty(meshes);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshCrossSections)

CmdMeshCrossSections::CmdMeshCrossSections()
    : Command("Mesh_CrossSections")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Cross-Sections");
    sToolTipText = QT_TR_NOOP("Creates cross-sections of the mesh");
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_CrossSections";
}

void CmdMeshCrossSections::activated(int)
{
    if (!isActive()) {
        return;
    }
    const char* cmd = "import MeshPartGui\n"
                      "import FreeCADGui\n"
                      "FreeCADGui.runCommand('MeshPart_CrossSections')\n";
    runCommand(Doc, cmd);
}

bool CmdMeshCrossSections::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document) && allMeshesNonEmpty(objects);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshPolySplit)

CmdMeshPolySplit::CmdMeshPolySplit()
    : Command("Mesh_PolySplit")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Split");
    sToolTipText = QT_TR_NOOP("Splits a mesh into 2 meshes");
    sWhatsThis = "Mesh_PolySplit";
    sStatusTip = sToolTipText;
}

void CmdMeshPolySplit::activated(int)
{
    std::vector<App::DocumentObject*> docObj = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (docObj.empty() || !allObjectsBelongTo(docObj, document) || !allMeshesNonEmpty(docObj)
        || !allObjectsVisible(docObj)) {
        return;
    }
    for (std::vector<App::DocumentObject*>::iterator it = docObj.begin(); it != docObj.end(); ++it) {
        if (it == docObj.begin()) {
            Gui::Document* doc = Gui::Application::Instance->getDocument(document);
            auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
            if (view) {
                Gui::View3DInventorViewer* viewer = view->getViewer();
                viewer->setEditing(true);
                viewer->startSelection(Gui::View3DInventorViewer::Clip);
                viewer->addEventCallback(
                    SoMouseButtonEvent::getClassTypeId(),
                    MeshGui::ViewProviderMeshFaceSet::partMeshCallback
                );
            }
            else {
                return;
            }
        }

        Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
        Gui::ViewProvider* pVP = guiDocument ? guiDocument->getViewProvider(*it) : nullptr;
        if (pVP && pVP->isVisible()) {
            pVP->startEditing();
        }
    }
}

bool CmdMeshPolySplit::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    if (objects.empty() || !allObjectsBelongTo(objects, document) || !allMeshesNonEmpty(objects)
        || !allObjectsVisible(objects)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshEvaluation)

CmdMeshEvaluation::CmdMeshEvaluation()
    : Command("Mesh_Evaluation")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    // needs two ampersands to display one
    sMenuText = QT_TR_NOOP("Evaluate and Repair");
    sToolTipText = QT_TR_NOOP("Opens a dialog to analyze and repair a mesh");
    sWhatsThis = "Mesh_Evaluation";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Evaluation";
    eType = 0;
}

void CmdMeshEvaluation::activated(int)
{
    App::Document* document = App::GetApplication().getActiveDocument();
    if (!document) {
        return;
    }
    MeshGui::DlgEvaluateMeshImp* dlg = MeshGui::DockEvaluateMeshImp::instance();
    dlg->setAttribute(Qt::WA_DeleteOnClose);
    dlg->setEvaluationDocument(document);
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    if (meshes.size() == 1 && allObjectsBelongTo(meshes, document) && allMeshesNonEmpty(meshes)) {
        dlg->setMesh(meshes.front());
    }

    dlg->show();
}

bool CmdMeshEvaluation::isActive()
{
    App::Document* doc = App::GetApplication().getActiveDocument();
    if (!doc
        || !std::ranges::any_of(
            doc->getObjectsOfType(Mesh::Feature::getClassTypeId()),
            [](const App::DocumentObject* object) {
                const auto* mesh = freecad_cast<const Mesh::Feature*>(object);
                return mesh && mesh->Mesh.getValue().countFacets() > 0;
            }
        )) {
        return false;
    }
    return true;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshEvaluateFacet)

CmdMeshEvaluateFacet::CmdMeshEvaluateFacet()
    : Command("Mesh_EvaluateFacet")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Face Info");
    sToolTipText = QT_TR_NOOP("Displays information about the selected faces");
    sWhatsThis = "Mesh_EvaluateFacet";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_EvaluateFacet";
    eType = Alter3DView;
}

void CmdMeshEvaluateFacet::activated(int)
{
    if (!isActive()) {
        return;
    }
    App::Document* document = App::GetApplication().getActiveDocument();
    Gui::Document* doc = Gui::Application::Instance->getDocument(document);
    auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
    if (view) {
        Gui::View3DInventorViewer* viewer = view->getViewer();
        viewer->setEditing(true);
        viewer->setEditingCursor(
            QCursor(Gui::BitmapFactory().pixmapFromSvg("Mesh_Pipette", QSize(32, 32)), 4, 29)
        );
        viewer->addEventCallback(
            SoMouseButtonEvent::getClassTypeId(),
            MeshGui::ViewProviderMeshFaceSet::faceInfoCallback
        );
    }
}

bool CmdMeshEvaluateFacet::isActive()
{
    App::Document* doc = App::GetApplication().getActiveDocument();
    if (!doc || !documentHasVisibleNonEmptyMesh(doc)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshRemoveComponents)

CmdMeshRemoveComponents::CmdMeshRemoveComponents()
    : Command("Mesh_RemoveComponents")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Remove Components");
    sToolTipText = QT_TR_NOOP("Removes topologically independent components from the mesh");
    sWhatsThis = "Mesh_RemoveComponents";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_RemoveComponents";
}

void CmdMeshRemoveComponents::activated(int)
{
    if (!isActive()) {
        return;
    }
    auto* dlg = new MeshGui::TaskRemoveComponents();
    dlg->setButtonPosition(Gui::TaskView::TaskDialog::South);
    Gui::Control().showDialog(dlg);
}

bool CmdMeshRemoveComponents::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* doc = getDocument();
    if (!MeshGui::canStartNativeMeshCommand(doc) || !documentHasVisibleNonEmptyMesh(doc)) {
        return false;
    }
    Gui::Document* viewDoc = Gui::Application::Instance->getDocument(doc);
    Gui::View3DInventor* view = viewDoc
        ? dynamic_cast<Gui::View3DInventor*>(viewDoc->getActiveView())
        : nullptr;
    if (!view) {
        return false;
    }
    return !view->getViewer()->isEditing();
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshRemeshGmsh)

CmdMeshRemeshGmsh::CmdMeshRemeshGmsh()
    : Command("Mesh_RemeshGmsh")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Refinement");
    sToolTipText = QT_TR_NOOP("Refines an existing mesh");
    sStatusTip = sToolTipText;
    sWhatsThis = "Mesh_RemeshGmsh";
    sPixmap = "Mesh_RemeshGmsh";
}

void CmdMeshRemeshGmsh::activated(int)
{
    if (!isActive()) {
        return;
    }
    std::vector<Mesh::Feature*> mesh = getSelection().getObjectsOfType<Mesh::Feature>();
    if (mesh.size() != 1) {
        return;
    }
    auto* dlg = new MeshGui::TaskRemeshGmsh(mesh.front());
    Gui::Control().showDialog(dlg);
}

bool CmdMeshRemeshGmsh::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() == 1 && allObjectsBelongTo(objects, document) && objects.front()
        && objects.front()->Mesh.getValue().countFacets() > 0;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshRemoveCompByHand)

CmdMeshRemoveCompByHand::CmdMeshRemoveCompByHand()
    : Command("Mesh_RemoveCompByHand")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Remove Components Manually");
    sToolTipText = QT_TR_NOOP("Marks a component to remove it from the mesh");
    sWhatsThis = "Mesh_RemoveCompByHand";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_RemoveCompByHand";
}

void CmdMeshRemoveCompByHand::activated(int)
{
    if (!isActive()) {
        return;
    }
    App::Document* document = App::GetApplication().getActiveDocument();
    Gui::Document* doc = Gui::Application::Instance->getDocument(document);
    auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
    if (view) {
        Gui::View3DInventorViewer* viewer = view->getViewer();
        viewer->setEditing(true);
        viewer->setEditingCursor(QCursor(Qt::OpenHandCursor));
        viewer->addEventCallback(
            SoMouseButtonEvent::getClassTypeId(),
            MeshGui::ViewProviderMeshFaceSet::markPartCallback
        );
        viewer->setSelectionEnabled(false);
    }
}

bool CmdMeshRemoveCompByHand::isActive()
{
    App::Document* doc = App::GetApplication().getActiveDocument();
    if (!MeshGui::canStartNativeMeshCommand(doc) || !documentHasVisibleNonEmptyMesh(doc)) {
        return false;
    }

    Gui::View3DInventor* view = dynamic_cast<Gui::View3DInventor*>(
        Gui::getMainWindow()->activeWindow()
    );
    if (view) {
        Gui::View3DInventorViewer* viewer = view->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshEvaluateSolid)

CmdMeshEvaluateSolid::CmdMeshEvaluateSolid()
    : Command("Mesh_EvaluateSolid")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Evaluate Solid");
    sToolTipText = QT_TR_NOOP("Checks whether the mesh is a solid");
    sWhatsThis = "Mesh_EvaluateSolid";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_EvaluateSolid";
    eType = 0;
}

void CmdMeshEvaluateSolid::activated(int)
{
    App::Document* document = App::GetApplication().getActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    if (meshes.size() != 1 || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)) {
        return;
    }
    for (auto it : meshes) {
        Mesh::Feature* mesh = (Mesh::Feature*)(it);
        QString msg;
        if (mesh->Mesh.getValue().getKernel().HasOpenEdges()) {
            msg = QObject::tr("The mesh '%1' is not a solid.")
                      .arg(QString::fromLatin1(mesh->Label.getValue()));
        }
        else {
            msg = QObject::tr("The mesh '%1' is a solid.")
                      .arg(QString::fromLatin1(mesh->Label.getValue()));
        }
        QMessageBox::information(Gui::getMainWindow(), QObject::tr("Solid Mesh"), msg);
    }
}

bool CmdMeshEvaluateSolid::isActive()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    return meshes.size() == 1 && allObjectsBelongTo(meshes, document) && allMeshesNonEmpty(meshes);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshSmoothing)

CmdMeshSmoothing::CmdMeshSmoothing()
    : Command("Mesh_Smoothing")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Smooth");
    sToolTipText = QT_TR_NOOP("Smoothes the selected meshes");
    sWhatsThis = "Mesh_Smoothing";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Smoothing";
}

void CmdMeshSmoothing::activated(int)
{
    if (!isActive()) {
        return;
    }
    Gui::Control().showDialog(new MeshGui::TaskSmoothing());
}

bool CmdMeshSmoothing::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document) && allMeshesNonEmpty(objects);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshDecimating)

CmdMeshDecimating::CmdMeshDecimating()
    : Command("Mesh_Decimating")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Decimate");
    sToolTipText = QT_TR_NOOP("Decimates a mesh");
    sWhatsThis = "Mesh_Decimating";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Decimating";
}

void CmdMeshDecimating::activated(int)
{
    if (!isActive()) {
        return;
    }
    Gui::Control().showDialog(new MeshGui::TaskDecimating());
}

bool CmdMeshDecimating::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document) && allMeshesNonEmpty(objects);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshHarmonizeNormals)

CmdMeshHarmonizeNormals::CmdMeshHarmonizeNormals()
    : Command("Mesh_HarmonizeNormals")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Harmonize Normals");
    sToolTipText = QT_TR_NOOP("Harmonizes the normals of the mesh");
    sWhatsThis = "Mesh_HarmonizeNormals";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_HarmonizeNormals";
}

void CmdMeshHarmonizeNormals::activated(int)
{
    std::vector<App::DocumentObject*> meshes = getSelection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (meshes.empty() || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)) {
        return;
    }

    std::vector<App::DocumentObject*> sources;
    sources.reserve(meshes.size());
    for (auto* object : meshes) {
        auto* feature = static_cast<Mesh::Feature*>(object);
        if (feature->Mesh.getValue().countNonUniformOrientedFacets() != 0) {
            sources.push_back(feature);
        }
    }
    if (sources.empty()) {
        return;
    }

    runParametricMeshFilter(
        *document,
        sources,
        "Mesh::HarmonizeNormals",
        "HarmonizedNormals",
        QT_TRANSLATE_NOOP("Mesh", "Harmonized Normals"),
        QT_TRANSLATE_NOOP("Command", "Harmonize mesh normals"),
        QT_TRANSLATE_NOOP("Mesh", "Harmonize Normals")
    );
}

bool CmdMeshHarmonizeNormals::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshFlipNormals)

CmdMeshFlipNormals::CmdMeshFlipNormals()
    : Command("Mesh_FlipNormals")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Flip Normals");
    sToolTipText = QT_TR_NOOP("Flips the normals of the selected mesh");
    sWhatsThis = "Mesh_FlipNormals";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_FlipNormals";
}

void CmdMeshFlipNormals::activated(int)
{
    std::vector<App::DocumentObject*> meshes = getSelection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (meshes.empty() || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)) {
        return;
    }

    runParametricMeshFilter(
        *document,
        meshes,
        "Mesh::FlipNormals",
        "FlippedNormals",
        QT_TRANSLATE_NOOP("Mesh", "Flipped Normals"),
        QT_TRANSLATE_NOOP("Command", "Flip mesh normals"),
        QT_TRANSLATE_NOOP("Mesh", "Flip Normals")
    );
}

bool CmdMeshFlipNormals::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshBoundingBox)

CmdMeshBoundingBox::CmdMeshBoundingBox()
    : Command("Mesh_BoundingBox")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Bounding Box Info");
    sToolTipText = QT_TR_NOOP("Shows the bounding box coordinates of the selected mesh");
    sWhatsThis = "Mesh_BoundingBox";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_BoundingBox";
    eType = 0;
}

void CmdMeshBoundingBox::activated(int)
{
    App::Document* document = App::GetApplication().getActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    if (meshes.size() != 1 || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)) {
        return;
    }
    for (auto it : meshes) {
        const Base::BoundBox3d box = static_cast<Mesh::Feature*>(it)->Mesh.getValue().getBoundBox();

        Base::Console().message(
            "Boundings: Min=<%f,%f,%f>, Max=<%f,%f,%f>\n",
            box.MinX,
            box.MinY,
            box.MinZ,
            box.MaxX,
            box.MaxY,
            box.MaxZ
        );

        QString bound = qApp->translate("Mesh_BoundingBox", "Boundings of %1:")
                            .arg(QString::fromUtf8(it->Label.getValue()));
        bound += QStringLiteral("\n\nMin=<%1,%2,%3>\n\nMax=<%4,%5,%6>")
                     .arg(box.MinX)
                     .arg(box.MinY)
                     .arg(box.MinZ)
                     .arg(box.MaxX)
                     .arg(box.MaxY)
                     .arg(box.MaxZ);
        QMessageBox::information(Gui::getMainWindow(), QObject::tr("Boundings"), bound);
        break;
    }
}

bool CmdMeshBoundingBox::isActive()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    return meshes.size() == 1 && allObjectsBelongTo(meshes, document) && allMeshesNonEmpty(meshes);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshBuildRegularSolid)

CmdMeshBuildRegularSolid::CmdMeshBuildRegularSolid()
    : Command("Mesh_BuildRegularSolid")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Regular Solid");
    sToolTipText = QT_TR_NOOP("Builds a regular solid");
    sWhatsThis = "Mesh_BuildRegularSolid";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_BuildRegularSolid";
}

void CmdMeshBuildRegularSolid::activated(int)
{
    if (!isActive()) {
        return;
    }
    static QPointer<QDialog> dlg = nullptr;
    if (!dlg) {
        dlg = new MeshGui::DlgRegularSolidImp(Gui::getMainWindow());
    }
    dlg->setAttribute(Qt::WA_DeleteOnClose);
    dlg->show();
}

bool CmdMeshBuildRegularSolid::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    return cleanActiveMeshDocument() != nullptr;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshFillupHoles)

CmdMeshFillupHoles::CmdMeshFillupHoles()
    : Command("Mesh_FillupHoles")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Fill Holes");
    sToolTipText = QT_TR_NOOP("Fills holes in the mesh");
    sWhatsThis = "Mesh_FillupHoles";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_FillupHoles";
}

void CmdMeshFillupHoles::activated(int)
{
    App::Document* launchDocument = cleanActiveMeshDocument();
    std::vector<Mesh::Feature*> selected = getSelection().getObjectsOfType<Mesh::Feature>();
    if (!launchDocument || selected.empty() || !allObjectsBelongTo(selected, launchDocument)
        || !std::ranges::all_of(selected, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           })) {
        return;
    }
    App::DocumentWeakPtrT targetDocument(launchDocument);
    std::vector<App::DocumentObjectWeakPtrT> targets;
    targets.reserve(selected.size());
    for (auto* mesh : selected) {
        targets.emplace_back(mesh);
    }

    bool ok {};
    int FillupHolesOfLength = QInputDialog::getInt(
        Gui::getMainWindow(),
        QObject::tr("Fill Holes"),
        QObject::tr("Fill holes with maximum number of edges"),
        3,
        3,
        10000,
        1,
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok) {
        return;
    }

    App::Document* document = *targetDocument;
    if (!MeshGui::canStartNativeMeshCommand(document)) {
        return;
    }

    std::vector<App::DocumentObject*> sources;
    for (const auto& target : targets) {
        auto* feature = target.get<Mesh::Feature>();
        if (!feature || feature->getDocument() != document
            || !MeshGui::isNativeMeshInputActive(feature)) {
            return;
        }
        Mesh::MeshObject filled = feature->Mesh.getValue();
        const unsigned long before = filled.countFacets();
        MeshCore::FlatTriangulator triangulator;
        triangulator.SetVerifier(new MeshCore::TriangulationVerifierV2);
        filled.fillupHoles(static_cast<unsigned long>(FillupHolesOfLength), 0, triangulator);
        if (filled.countFacets() > before) {
            sources.push_back(feature);
        }
    }
    if (sources.empty()) {
        return;
    }

    runParametricMeshFilter(
        *document,
        sources,
        "Mesh::FillHoles",
        "FilledHoles",
        QT_TRANSLATE_NOOP("Mesh", "Filled Holes"),
        QT_TRANSLATE_NOOP("Command", "Fill up holes"),
        QT_TRANSLATE_NOOP("Mesh", "Fill Holes"),
        [FillupHolesOfLength](App::DocumentObject& object) {
            auto* length = freecad_cast<App::PropertyInteger*>(
                object.getPropertyByName("FillupHolesOfLength")
            );
            auto* method = freecad_cast<App::PropertyEnumeration*>(object.getPropertyByName("Method"));
            if (!length || !method) {
                throw Base::RuntimeError("The native fill-holes properties are unavailable");
            }
            length->setValue(FillupHolesOfLength);
            method->setValue(1);
        }
    );
}

bool CmdMeshFillupHoles::isActive()
{
    // Check for the selected mesh feature (all Mesh types)
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshFillInteractiveHole)

CmdMeshFillInteractiveHole::CmdMeshFillInteractiveHole()
    : Command("Mesh_FillInteractiveHole")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Close Hole");
    sToolTipText = QT_TR_NOOP("Closes a hole interactively in the mesh");
    sWhatsThis = "Mesh_FillInteractiveHole";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_FillInteractiveHole";
}

void CmdMeshFillInteractiveHole::activated(int)
{
    if (!isActive()) {
        return;
    }
    App::Document* document = App::GetApplication().getActiveDocument();
    Gui::Document* doc = Gui::Application::Instance->getDocument(document);
    auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
    if (view) {
        Gui::View3DInventorViewer* viewer = view->getViewer();
        viewer->setEditing(true);
        viewer->setEditingCursor(
            QCursor(Gui::BitmapFactory().pixmapFromSvg("Mesh_CursorFillInteractive", QSize(32, 32)), 6, 6)
        );
        viewer->addEventCallback(
            SoMouseButtonEvent::getClassTypeId(),
            MeshGui::ViewProviderMeshFaceSet::fillHoleCallback
        );
        viewer->setSelectionEnabled(false);
    }
}

bool CmdMeshFillInteractiveHole::isActive()
{
    App::Document* doc = App::GetApplication().getActiveDocument();
    if (!MeshGui::canStartNativeMeshCommand(doc) || !documentHasVisibleNonEmptyMesh(doc)) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

DEF_STD_CMD_A(CmdMeshSegmentation)

CmdMeshSegmentation::CmdMeshSegmentation()
    : Command("Mesh_Segmentation")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Segmentation");
    sToolTipText = QT_TR_NOOP("Creates new mesh segments from the mesh");
    sWhatsThis = "Mesh_Segmentation";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Segmentation";
}

void CmdMeshSegmentation::activated(int)
{
    std::vector<App::DocumentObject*> objs = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (objs.size() != 1 || !allObjectsBelongTo(objs, document)) {
        return;
    }
    Mesh::Feature* mesh = static_cast<Mesh::Feature*>(objs.front());
    auto* dlg = new MeshGui::TaskSegmentation(mesh);
    Gui::Control().showDialog(dlg);
}

bool CmdMeshSegmentation::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() == 1 && allObjectsBelongTo(objects, document) && allMeshesNonEmpty(objects);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshSegmentationBestFit)

CmdMeshSegmentationBestFit::CmdMeshSegmentationBestFit()
    : Command("Mesh_SegmentationBestFit")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Segmentation From Best-Fit Surfaces");
    sToolTipText = QT_TR_NOOP("Creates new mesh segments from the best-fit surfaces");
    sWhatsThis = "Mesh_SegmentationBestFit";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_SegmentationBestFit";
}

void CmdMeshSegmentationBestFit::activated(int)
{
    std::vector<App::DocumentObject*> objs = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    App::Document* document = cleanActiveMeshDocument();
    if (objs.size() != 1 || !allObjectsBelongTo(objs, document)) {
        return;
    }
    Mesh::Feature* mesh = static_cast<Mesh::Feature*>(objs.front());
    auto* dlg = new MeshGui::TaskSegmentationBestFit(mesh);
    Gui::Control().showDialog(dlg);
}

bool CmdMeshSegmentationBestFit::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() == 1 && allObjectsBelongTo(objects, document) && allMeshesNonEmpty(objects);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshMerge)

CmdMeshMerge::CmdMeshMerge()
    : Command("Mesh_Merge")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Merge");
    sToolTipText = QT_TR_NOOP("Merges selected meshes into one");
    sWhatsThis = "Mesh_Merge";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Merge";
}

void CmdMeshMerge::activated(int)
{
    App::Document* pcDoc = cleanActiveMeshDocument();
    if (!pcDoc) {
        return;
    }

    std::vector<App::DocumentObject*> objs = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    if (objs.size() < 2 || !allObjectsBelongTo(objs, pcDoc) || !allMeshesNonEmpty(objs)) {
        return;
    }

    std::vector<App::DocumentObject*> replacedInputs;
    replacedInputs.reserve(objs.size());
    for (auto* source : objs) {
        auto* view = Gui::Application::Instance->getViewProvider(source);
        if (view && view->isVisible()) {
            replacedInputs.push_back(source);
        }
    }

    try {
        Gui::WaitCursor wait;
        Gui::ExactTransaction mutation(*pcDoc, QT_TRANSLATE_NOOP("Command", "Mesh merge"));
        const std::string name = pcDoc->getUniqueObjectName("MeshMerge");
        auto* result = pcDoc->addObject<Mesh::Merge>(name.c_str());
        result->Label.setValue(QT_TRANSLATE_NOOP("App::Property", "Merged mesh"));
        result->Sources.setValues(objs);
        if (!result->recomputeFeature() || result->isError()
            || result->Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError(result->getStatusString());
        }

        MeshGui::markMeshTimelineReplacement(*result, replacedInputs);
        for (auto* source : objs) {
            if (auto* view = Gui::Application::Instance->getViewProvider(source)) {
                view->setVisible(false);
            }
        }
        commitExactMutation(mutation);
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Merge meshes"),
            QString::fromUtf8(error.what())
        );
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Merge meshes"),
            QString::fromUtf8(error.what())
        );
    }
    catch (...) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Merge meshes"),
            QObject::tr("The mesh merge failed unexpectedly.")
        );
    }
}

bool CmdMeshMerge::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() >= 2 && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshSplitComponents)

CmdMeshSplitComponents::CmdMeshSplitComponents()
    : Command("Mesh_SplitComponents")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Split by Components");
    sToolTipText = QT_TR_NOOP("Splits the selected mesh into its components");
    sWhatsThis = "Mesh_SplitComponents";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_SplitComponents";
}

void CmdMeshSplitComponents::activated(int)
{
    App::Document* pcDoc = cleanActiveMeshDocument();
    if (!pcDoc) {
        return;
    }

    std::vector<App::DocumentObject*> objs = Gui::Selection().getObjectsOfType(
        Mesh::Feature::getClassTypeId()
    );
    if (objs.size() != 1 || !allObjectsBelongTo(objs, pcDoc) || !allMeshesNonEmpty(objs)) {
        return;
    }

    auto* source = static_cast<Mesh::Feature*>(objs.front());
    const MeshObject& mesh = source->Mesh.getValue();
    const std::vector<std::vector<Mesh::FacetIndex>> components = mesh.getComponents();
    if (components.size() <= 1) {
        return;
    }

    std::vector<MeshGui::ParametricMeshFilterTarget> operations;
    operations.reserve(components.size());
    for (const auto& component : components) {
        if (component.empty()) {
            return;
        }
        std::vector<long> indices(component.begin(), component.end());
        operations.push_back(MeshGui::ParametricMeshFilterTarget {
            source,
            [source, indices = std::move(indices)](App::DocumentObject& object) {
                auto& subset = static_cast<Mesh::FacetSubset&>(object);
                subset.FacetIndices.setValues(indices);
                subset.AcceptedTopology.setValue(source->Mesh.getValue());
                subset.SelectionKind.setValue("Connected component");
            },
        });
    }
    MeshGui::createParametricMeshFilters(
        *pcDoc,
        operations,
        MeshGui::ParametricMeshFilterSpec {
            "Mesh::FacetSubset",
            "Component",
            "Mesh Component",
            QT_TRANSLATE_NOOP("Command", "Mesh split"),
            true,
            true,
            true,
            "SplitComponents",
            "Split Mesh Components",
            "Split connected components",
        }
    );
}

bool CmdMeshSplitComponents::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return objects.size() == 1 && allObjectsBelongTo(objects, document) && objects.front()
        && objects.front()->Mesh.getValue().countFacets() > 0;
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshScale)

CmdMeshScale::CmdMeshScale()
    : Command("Mesh_Scale")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Scale");
    sToolTipText = QT_TR_NOOP("Scales the selected mesh objects");
    sWhatsThis = "Mesh_Scale";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Scale";
}

void CmdMeshScale::activated(int)
{
    App::Document* launchDocument = cleanActiveMeshDocument();
    std::vector<Mesh::Feature*> selected = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    if (!launchDocument || selected.empty() || !allObjectsBelongTo(selected, launchDocument)) {
        return;
    }
    App::DocumentWeakPtrT targetDocument(launchDocument);
    std::vector<App::DocumentObjectWeakPtrT> targets;
    targets.reserve(selected.size());
    for (auto* object : selected) {
        targets.emplace_back(object);
    }

    bool ok {};
    double factor = QInputDialog::getDouble(
        Gui::getMainWindow(),
        QObject::tr("Scaling"),
        QObject::tr("Enter scaling factor:"),
        1,
        0,
        std::numeric_limits<double>::max(),
        5,
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok || factor == 0 || factor == 1) {
        return;
    }

    App::Document* document = *targetDocument;
    if (!MeshGui::canStartNativeMeshCommand(document)) {
        return;
    }

    std::vector<App::DocumentObject*> sources;
    sources.reserve(targets.size());
    for (const auto& target : targets) {
        auto* feature = target.get<Mesh::Feature>();
        if (!feature || feature->getDocument() != document
            || !MeshGui::isNativeMeshInputActive(feature)) {
            return;
        }
        const MeshObject& mesh = feature->Mesh.getValue();
        if (mesh.countFacets() == 0) {
            return;
        }
        const double maximumCoordinate = std::numeric_limits<float>::max();
        const auto& points = mesh.getKernel().GetPoints();
        const bool finiteResult = std::ranges::all_of(
            points,
            [factor, maximumCoordinate](const MeshCore::MeshPoint& point) {
                return std::ranges::all_of(
                    std::array<double, 3> {
                        point.x,
                        point.y,
                        point.z,
                    },
                    [factor, maximumCoordinate](double coordinate) {
                        const double scaled = coordinate * factor;
                        return std::isfinite(scaled) && std::abs(scaled) <= maximumCoordinate;
                    }
                );
            }
        );
        if (!finiteResult) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Scaling"),
                QObject::tr("The scaling factor would create invalid "
                            "mesh coordinates.")
            );
            return;
        }
        sources.push_back(feature);
    }

    runParametricMeshFilter(
        *document,
        sources,
        "Mesh::Scale",
        "Scale",
        "Scale Mesh",
        QT_TRANSLATE_NOOP("Command", "Mesh scale"),
        "Mesh Scale",
        [factor](App::DocumentObject& object) {
            static_cast<Mesh::Scale&>(object).Factor.setValue(factor);
        }
    );
}

bool CmdMeshScale::isActive()
{
    App::Document* document = cleanActiveMeshDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document)
        && std::ranges::all_of(objects, [](const Mesh::Feature* mesh) {
               return mesh && mesh->Mesh.getValue().countFacets() > 0;
           });
}


void CreateMeshCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    rcCmdMgr.addCommand(new CmdMeshImport());
    rcCmdMgr.addCommand(new CmdMeshExport());
    rcCmdMgr.addCommand(new CmdMeshVertexCurvature());
    rcCmdMgr.addCommand(new CmdMeshVertexCurvatureInfo());
    rcCmdMgr.addCommand(new CmdMeshUnion());
    rcCmdMgr.addCommand(new CmdMeshDifference());
    rcCmdMgr.addCommand(new CmdMeshIntersection());
    rcCmdMgr.addCommand(new CmdMeshPolySegm());
    rcCmdMgr.addCommand(new CmdMeshAddFacet());
    rcCmdMgr.addCommand(new CmdMeshPolyCut());
    rcCmdMgr.addCommand(new CmdMeshPolySplit());
    rcCmdMgr.addCommand(new CmdMeshPolyTrim());
    rcCmdMgr.addCommand(new CmdMeshTrimByPlane());
    rcCmdMgr.addCommand(new CmdMeshSectionByPlane());
    rcCmdMgr.addCommand(new CmdMeshCrossSections());
    rcCmdMgr.addCommand(new CmdMeshEvaluation());
    rcCmdMgr.addCommand(new CmdMeshEvaluateFacet());
    rcCmdMgr.addCommand(new CmdMeshEvaluateSolid());
    rcCmdMgr.addCommand(new CmdMeshHarmonizeNormals());
    rcCmdMgr.addCommand(new CmdMeshFlipNormals());
    rcCmdMgr.addCommand(new CmdMeshSmoothing());
    rcCmdMgr.addCommand(new CmdMeshDecimating());
    rcCmdMgr.addCommand(new CmdMeshBoundingBox());
    rcCmdMgr.addCommand(new CmdMeshBuildRegularSolid());
    rcCmdMgr.addCommand(new CmdMeshFillupHoles());
    rcCmdMgr.addCommand(new CmdMeshRemoveComponents());
    rcCmdMgr.addCommand(new CmdMeshRemeshGmsh());
    rcCmdMgr.addCommand(new CmdMeshFillInteractiveHole());
    rcCmdMgr.addCommand(new CmdMeshRemoveCompByHand());
    rcCmdMgr.addCommand(new CmdMeshFromGeometry());
    rcCmdMgr.addCommand(new CmdMeshFromPartShape());
    rcCmdMgr.addCommand(new CmdMeshSegmentation());
    rcCmdMgr.addCommand(new CmdMeshSegmentationBestFit);
    rcCmdMgr.addCommand(new CmdMeshMerge());
    rcCmdMgr.addCommand(new CmdMeshSplitComponents());
    rcCmdMgr.addCommand(new CmdMeshScale());
}
