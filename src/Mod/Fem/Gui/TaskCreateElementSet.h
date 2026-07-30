/***************************************************************************
 *   Copyright (c) 2023 Peter McB                                          *
 *   Copyright (c) 2013 Jürgen Riegel (FreeCAD@juergen-riegel.net)         *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 ***************************************************************************/

#pragma once

#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <QPointer>

#include <Gui/TaskView/TaskView.h>
#include <Mod/Fem/App/FemSetElementNodesObject.h>


class Ui_TaskCreateElementSet;
class SoEventCallback;

namespace Base
{
class Polygon2d;
}
namespace App
{
class Document;
class DocumentObject;
class Property;
}  // namespace App

namespace Fem
{
class FemMesh;
class FemMeshObject;
}  // namespace Fem

namespace Gui
{
class Document;
class View3DInventorViewer;
class ViewProvider;
class ViewVolumeProjection;
}  // namespace Gui

namespace FemGui
{

class ViewProviderFemMesh;


class TaskCreateElementSet: public Gui::TaskView::TaskBox, public Gui::SelectionObserver
{
    Q_OBJECT

public:
    explicit TaskCreateElementSet(Fem::FemSetElementNodesObject* pcObject, QWidget* parent = nullptr);
    ~TaskCreateElementSet() override;

    void finalizeTimelineBlock();

    std::set<long> elementTempSet;
    ViewProviderFemMesh* MeshViewProvider;
    // Kept for source and binary compatibility.  Erase Elements is now
    // entirely task/document-owned and deliberately does not use this
    // process-global value.
    static std::string currentProject;

private Q_SLOTS:
    void Poly();
    void Restore();
    void CopyResultsMesh();

protected:
    Fem::FemSetElementNodesObject* pcObject;
    static void DefineElementsCallback(void* ud, SoEventCallback* n);
    void DefineNodes(const Base::Polygon2d& polygon, const Gui::ViewVolumeProjection& proj, bool);
    void stopPolygonSelection();

protected:
    void onSelectionChanged(const Gui::SelectionChanges& msg) override;
    enum selectionModes
    {
        none,
        PickElement
    } selectionMode;

private:
    bool ownsObject(const App::DocumentObject* object, const std::string& name) const;
    bool publishWorkingMesh(const Fem::FemMesh& mesh);
    void ensurePreviewObject(const Fem::FemMesh& mesh);
    std::set<long> elementSetForMesh(const Fem::FemMesh& mesh) const;

    QWidget* proxy;
    std::unique_ptr<Ui_TaskCreateElementSet> ui;
    App::Document* document;
    Fem::FemMeshObject* sourceMeshObject;
    Fem::FemMeshObject* previewMeshObject;
    ViewProviderFemMesh* sourceMeshViewProvider;
    QPointer<Gui::View3DInventorViewer> polygonViewer;
    std::unique_ptr<Fem::FemMesh> sourceMeshSnapshot;
    std::unique_ptr<Fem::FemMesh> workingMesh;
    std::string operationObjectName;
    std::string sourceMeshName;
    std::string previewMeshName;
    bool sourceWasVisible;
    bool operationWasTimelineOperation;
    std::vector<std::pair<std::string, long>> oldTimelineResources;
};

}  // namespace FemGui
