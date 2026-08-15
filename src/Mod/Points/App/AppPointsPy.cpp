// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <cmath>
#include <memory>


#include <App/Application.h>
#include <App/ComplexGeoDataPy.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectPy.h>
#include <App/Property.h>
#include <Base/Console.h>
#include <Base/FileInfo.h>
#include <Base/Interpreter.h>
#include <Base/PlacementPy.h>
#include <Base/VectorPy.h>

#include "NativePointOperations.h"
#include "Points.h"
#include "PointsAlgos.h"
#include "PointsPy.h"
#include "Properties.h"
#include "Structured.h"


namespace Points
{
class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("Points")
    {
        add_varargs_method("open", &Module::open);
        add_varargs_method("insert", &Module::importer);
        add_varargs_method("export", &Module::exporter);
        add_varargs_method(
            "readNativePointCloud",
            &Module::readNativePointCloud,
            "Read a detached point cloud without creating or changing a document."
        );
        add_varargs_method(
            "writeNativePointCloud",
            &Module::writeNativePointCloud,
            "Write a detached point cloud and its optional attributes."
        );
        add_varargs_method(
            "sampleNativeGeometry",
            &Module::sampleNativeGeometry,
            "Sample detached geometry into a detached point cloud."
        );
        add_varargs_method(
            "structureNativePointCloud",
            &Module::structureNativePointCloud,
            "Infer a detached structured grid from a detached point cloud."
        );
        add_varargs_method(
            "selectNativePointCloud",
            &Module::selectNativePointCloud,
            "Select detached point-cloud points through a model-space polygon prism."
        );
        add_varargs_method(
            "mergeNativePointClouds",
            &Module::mergeNativePointClouds,
            "Merge detached point clouds in document coordinates."
        );
        add_varargs_method(
            "show",
            &Module::show,
            "show(points,[string]) -- Add the points to the active document or "
            "create one if no document exists.  Returns document object."
        );
        initialize("This module is the Points module.");  // register with Python
    }

private:
    std::unique_ptr<Reader> readerFor(const Base::FileInfo& file) const
    {
        if (file.hasExtension("asc")) {
            return std::make_unique<AscReader>();
        }
        if (file.hasExtension("e57")) {
            const auto setting = readE57Settings();
            return std::make_unique<E57Reader>(
                std::get<0>(setting),
                std::get<1>(setting),
                std::get<2>(setting)
            );
        }
        if (file.hasExtension("ply")) {
            return std::make_unique<PlyReader>();
        }
        if (file.hasExtension("pcd")) {
            return std::make_unique<PcdReader>();
        }
        throw Py::ValueError("point-cloud input must use .asc, .pcd, .ply, or .e57");
    }

    static std::unique_ptr<Writer> writerFor(
        const Base::FileInfo& file,
        const PointKernel& points
    )
    {
        if (file.hasExtension("asc")) {
            return std::make_unique<AscWriter>(points);
        }
        if (file.hasExtension("ply")) {
            return std::make_unique<PlyWriter>(points);
        }
        if (file.hasExtension("pcd")) {
            return std::make_unique<PcdWriter>(points);
        }
        throw Py::ValueError("point-cloud output must use .asc, .pcd, or .ply");
    }

    static Py::List floatValues(const std::vector<float>& values)
    {
        Py::List result;
        for (const float value : values) {
            result.append(Py::Float(value));
        }
        return result;
    }

    static Py::List colorValues(const std::vector<Base::Color>& values)
    {
        Py::List result;
        for (const Base::Color& value : values) {
            result.append(Py::TupleN(
                Py::Float(value.r),
                Py::Float(value.g),
                Py::Float(value.b),
                Py::Float(value.a)
            ));
        }
        return result;
    }

    static Py::List vectorValues(const std::vector<Base::Vector3f>& values)
    {
        Py::List result;
        for (const Base::Vector3f& value : values) {
            result.append(Py::TupleN(
                Py::Float(value.x),
                Py::Float(value.y),
                Py::Float(value.z)
            ));
        }
        return result;
    }

    static Py::List indexValues(const std::vector<std::size_t>& values)
    {
        Py::List result;
        for (const std::size_t value : values) {
            result.append(Py::Long(value));
        }
        return result;
    }

    static Py::List signedIndexValues(const std::vector<std::ptrdiff_t>& values)
    {
        Py::List result;
        for (const std::ptrdiff_t value : values) {
            result.append(Py::Long(value));
        }
        return result;
    }

    static std::vector<float> parseFloats(
        PyObject* value,
        std::size_t pointCount,
        const char* label
    )
    {
        PyObject* sequence = PySequence_Fast(value, label);
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count != 0 && static_cast<std::size_t>(count) != pointCount) {
            throw Py::ValueError(std::string(label) + " must be empty or match the point count");
        }
        std::vector<float> result;
        result.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            const double item = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(sequence, index));
            if (PyErr_Occurred() || !std::isfinite(item)) {
                throw Py::ValueError(std::string(label) + " must contain finite numbers");
            }
            result.push_back(static_cast<float>(item));
        }
        return result;
    }

    static std::vector<Base::Vector3f> parseVectors(
        PyObject* value,
        std::size_t pointCount
    )
    {
        PyObject* sequence = PySequence_Fast(value, "normals must be a sequence");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count != 0 && static_cast<std::size_t>(count) != pointCount) {
            throw Py::ValueError("normals must be empty or match the point count");
        }
        std::vector<Base::Vector3f> result;
        result.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* item = PySequence_Fast(
                PySequence_Fast_GET_ITEM(sequence, index),
                "every normal must contain three finite numbers"
            );
            if (!item) {
                throw Py::Exception();
            }
            Py::Object itemOwner(item, true);
            if (PySequence_Fast_GET_SIZE(item) != 3) {
                throw Py::ValueError("every normal must contain three finite numbers");
            }
            double coordinates[3] {};
            for (std::size_t axis = 0; axis < 3; ++axis) {
                coordinates[axis] = PyFloat_AsDouble(
                    PySequence_Fast_GET_ITEM(item, static_cast<Py_ssize_t>(axis))
                );
                if (PyErr_Occurred() || !std::isfinite(coordinates[axis])) {
                    throw Py::ValueError("every normal must contain three finite numbers");
                }
            }
            result.emplace_back(coordinates[0], coordinates[1], coordinates[2]);
        }
        return result;
    }

    static std::vector<Base::Color> parseColors(PyObject* value, std::size_t pointCount)
    {
        PyObject* sequence = PySequence_Fast(value, "colors must be a sequence");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count != 0 && static_cast<std::size_t>(count) != pointCount) {
            throw Py::ValueError("colors must be empty or match the point count");
        }
        std::vector<Base::Color> result;
        result.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* item = PySequence_Fast(
                PySequence_Fast_GET_ITEM(sequence, index),
                "every color must contain three or four numbers from zero to one"
            );
            if (!item) {
                throw Py::Exception();
            }
            Py::Object itemOwner(item, true);
            const Py_ssize_t channels = PySequence_Fast_GET_SIZE(item);
            if (channels != 3 && channels != 4) {
                throw Py::ValueError(
                    "every color must contain three or four numbers from zero to one"
                );
            }
            double rgba[4] {0.0, 0.0, 0.0, 1.0};
            for (Py_ssize_t channel = 0; channel < channels; ++channel) {
                rgba[channel] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(item, channel));
                if (PyErr_Occurred() || !std::isfinite(rgba[channel])
                    || rgba[channel] < 0.0 || rgba[channel] > 1.0) {
                    throw Py::ValueError(
                        "every color must contain three or four numbers from zero to one"
                    );
                }
            }
            result.emplace_back(rgba[0], rgba[1], rgba[2], rgba[3]);
        }
        return result;
    }

    std::tuple<bool, bool, double> readE57Settings() const
    {
        Base::Reference<ParameterGrp> hGrp = App::GetApplication()
                                                 .GetUserParameter()
                                                 .GetGroup("BaseApp")
                                                 ->GetGroup("Preferences")
                                                 ->GetGroup("Mod/Points/E57");
        bool useColor = hGrp->GetBool("UseColor", true);
        bool checkState = hGrp->GetBool("CheckInvalidState", true);
        double minDistance = hGrp->GetFloat("MinDistance", -1.);

        return std::make_tuple(useColor, checkState, minDistance);
    }

    Py::Object readNativePointCloud(const Py::Tuple& args)
    {
        const char* path {};
        if (!PyArg_ParseTuple(args.ptr(), "s", &path)) {
            throw Py::Exception();
        }
        try {
            Base::FileInfo file(path);
            auto reader = readerFor(file);
            {
                Base::PyGILStateRelease release;
                reader->read(path);
            }
            const PointKernel& points = reader->getPoints();
            if (points.size() == 0) {
                throw Py::ValueError("the selected file contains no point data");
            }
            Py::Dict result;
            result.setItem(
                "points",
                Py::asObject(new PointsPy(new PointKernel(points)))
            );
            result.setItem("point_count", Py::Long(points.size()));
            result.setItem("structured", Py::Boolean(reader->isStructured()));
            result.setItem("width", Py::Long(reader->getWidth()));
            result.setItem("height", Py::Long(reader->getHeight()));
            result.setItem("intensities", floatValues(reader->getIntensities()));
            result.setItem("colors", colorValues(reader->getColors()));
            result.setItem("normals", vectorValues(reader->getNormals()));
            return result;
        }
        catch (const Py::Exception&) {
            throw;
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object writeNativePointCloud(const Py::Tuple& args)
    {
        PyObject* pythonPoints {};
        const char* path {};
        PyObject* pythonPlacement {};
        int width {};
        int height {};
        PyObject* pythonIntensities {};
        PyObject* pythonColors {};
        PyObject* pythonNormals {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!sO!iiOOO",
                &PointsPy::Type,
                &pythonPoints,
                &path,
                &Base::PlacementPy::Type,
                &pythonPlacement,
                &width,
                &height,
                &pythonIntensities,
                &pythonColors,
                &pythonNormals
            )) {
            throw Py::Exception();
        }
        try {
            const auto* pointsObject = static_cast<PointsPy*>(pythonPoints);
            const PointKernel& points = *pointsObject->getPointKernelPtr();
            if (points.size() == 0) {
                throw Py::ValueError("a detached point-cloud export cannot be empty");
            }
            if ((width != 0 || height != 0)
                && (width < 2 || height < 2
                    || static_cast<std::size_t>(width * height) != points.size())) {
                throw Py::ValueError(
                    "structured export dimensions must be zero or match the exact point count"
                );
            }
            const auto intensities = parseFloats(
                pythonIntensities,
                points.size(),
                "intensities"
            );
            const auto colors = parseColors(pythonColors, points.size());
            const auto normals = parseVectors(pythonNormals, points.size());
            const Base::Placement placement = *static_cast<Base::PlacementPy*>(
                pythonPlacement
            )->getPlacementPtr();
            auto writer = writerFor(Base::FileInfo(path), points);
            if (width > 0) {
                writer->setWidth(width);
                writer->setHeight(height);
            }
            if (!intensities.empty()) {
                writer->setIntensities(intensities);
            }
            if (!colors.empty()) {
                writer->setColors(colors);
            }
            if (!normals.empty()) {
                writer->setNormals(normals);
            }
            writer->setPlacement(placement);
            {
                Base::PyGILStateRelease release;
                writer->write(path);
            }
            Py::Dict result;
            result.setItem("point_count", Py::Long(points.size()));
            return result;
        }
        catch (const Py::Exception&) {
            throw;
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object sampleNativeGeometry(const Py::Tuple& args)
    {
        PyObject* pythonGeometry {};
        double maximumDistance {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!d",
                &Data::ComplexGeoDataPy::Type,
                &pythonGeometry,
                &maximumDistance
            )) {
            throw Py::Exception();
        }
        try {
            const auto* geometryObject = static_cast<Data::ComplexGeoDataPy*>(pythonGeometry);
            NativePointSample sampled;
            {
                Base::PyGILStateRelease release;
                sampled = Points::sampleNativeGeometry(
                    *geometryObject->getComplexGeoDataPtr(),
                    maximumDistance
                );
            }
            const auto pointCount = sampled.points.size();
            Py::Dict result;
            result.setItem(
                "points",
                Py::asObject(new PointsPy(new PointKernel(std::move(sampled.points))))
            );
            result.setItem("point_count", Py::Long(pointCount));
            result.setItem("normals", vectorValues(sampled.normals));
            return result;
        }
        catch (const Py::Exception&) {
            throw;
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object structureNativePointCloud(const Py::Tuple& args)
    {
        PyObject* pythonPoints {};
        double coordinateTolerance {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!d",
                &PointsPy::Type,
                &pythonPoints,
                &coordinateTolerance
            )) {
            throw Py::Exception();
        }
        try {
            const auto* pointsObject = static_cast<PointsPy*>(pythonPoints);
            NativePointStructure structured;
            {
                Base::PyGILStateRelease release;
                structured = Points::structureNativePointCloud(
                    *pointsObject->getPointKernelPtr(),
                    coordinateTolerance
                );
            }
            const auto pointCount = structured.points.size();
            Py::Dict result;
            result.setItem(
                "points",
                Py::asObject(new PointsPy(new PointKernel(std::move(structured.points))))
            );
            result.setItem("point_count", Py::Long(pointCount));
            result.setItem("width", Py::Long(structured.width));
            result.setItem("height", Py::Long(structured.height));
            result.setItem("source_indices", signedIndexValues(structured.sourceIndices));
            return result;
        }
        catch (const Py::Exception&) {
            throw;
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object selectNativePointCloud(const Py::Tuple& args)
    {
        PyObject* pythonPoints {};
        PyObject* pythonPlacement {};
        PyObject* pythonPolygon {};
        int keepInside {};
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!O!Op",
                &PointsPy::Type,
                &pythonPoints,
                &Base::PlacementPy::Type,
                &pythonPlacement,
                &pythonPolygon,
                &keepInside
            )) {
            throw Py::Exception();
        }
        PyObject* sequence = PySequence_Fast(
            pythonPolygon,
            "polygon must be a sequence of document-space vectors"
        );
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 3 || count > 256) {
            throw Py::ValueError("polygon must contain 3 to 256 document-space vectors");
        }
        std::vector<Base::Vector3d> polygon;
        polygon.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* item = PySequence_Fast_GET_ITEM(sequence, index);
            if (!PyObject_TypeCheck(item, &Base::VectorPy::Type)) {
                throw Py::TypeError("every polygon vertex must be a document-space vector");
            }
            polygon.push_back(static_cast<Base::VectorPy*>(item)->value());
        }
        try {
            const auto* pointsObject = static_cast<PointsPy*>(pythonPoints);
            const auto placement = *static_cast<Base::PlacementPy*>(pythonPlacement)
                                         ->getPlacementPtr();
            NativePointSubset selected;
            {
                Base::PyGILStateRelease release;
                selected = Points::selectNativePointCloud(
                    *pointsObject->getPointKernelPtr(),
                    placement,
                    polygon,
                    keepInside != 0
                );
            }
            const auto pointCount = selected.points.size();
            Py::Dict result;
            result.setItem(
                "points",
                Py::asObject(new PointsPy(new PointKernel(std::move(selected.points))))
            );
            result.setItem("point_count", Py::Long(pointCount));
            result.setItem("source_indices", indexValues(selected.sourceIndices));
            return result;
        }
        catch (const Py::Exception&) {
            throw;
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }

    Py::Object mergeNativePointClouds(const Py::Tuple& args)
    {
        PyObject* pythonClouds {};
        PyObject* pythonPlacements {};
        if (!PyArg_ParseTuple(args.ptr(), "OO", &pythonClouds, &pythonPlacements)) {
            throw Py::Exception();
        }
        PyObject* cloudSequence = PySequence_Fast(
            pythonClouds,
            "clouds must be a sequence of detached point clouds"
        );
        if (!cloudSequence) {
            throw Py::Exception();
        }
        Py::Object cloudOwner(cloudSequence, true);
        PyObject* placementSequence = PySequence_Fast(
            pythonPlacements,
            "placements must match the detached point clouds"
        );
        if (!placementSequence) {
            throw Py::Exception();
        }
        Py::Object placementOwner(placementSequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(cloudSequence);
        if (count < 2 || count != PySequence_Fast_GET_SIZE(placementSequence)) {
            throw Py::ValueError("merge requires matching lists of at least two point clouds");
        }
        std::vector<const PointKernel*> clouds;
        std::vector<Base::Placement> placements;
        clouds.reserve(static_cast<std::size_t>(count));
        placements.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* cloud = PySequence_Fast_GET_ITEM(cloudSequence, index);
            PyObject* placement = PySequence_Fast_GET_ITEM(placementSequence, index);
            if (!PyObject_TypeCheck(cloud, &PointsPy::Type)
                || !PyObject_TypeCheck(placement, &Base::PlacementPy::Type)) {
                throw Py::TypeError(
                    "merge requires detached point clouds and matching placements"
                );
            }
            clouds.push_back(static_cast<PointsPy*>(cloud)->getPointKernelPtr());
            placements.push_back(
                *static_cast<Base::PlacementPy*>(placement)->getPlacementPtr()
            );
        }
        try {
            NativePointMerge merged;
            {
                Base::PyGILStateRelease release;
                merged = Points::mergeNativePointClouds(clouds, placements);
            }
            const auto pointCount = merged.points.size();
            Py::List sourceIndices;
            for (const auto& [source, index] : merged.sourceIndices) {
                sourceIndices.append(Py::TupleN(Py::Long(source), Py::Long(index)));
            }
            Py::Dict result;
            result.setItem(
                "points",
                Py::asObject(new PointsPy(new PointKernel(std::move(merged.points))))
            );
            result.setItem("point_count", Py::Long(pointCount));
            result.setItem("source_indices", sourceIndices);
            return result;
        }
        catch (const Py::Exception&) {
            throw;
        }
        catch (const Base::Exception& error) {
            throw Py::RuntimeError(error.what());
        }
    }
    Py::Object open(const Py::Tuple& args)
    {
        char* Name {};
        if (!PyArg_ParseTuple(args.ptr(), "et", "utf-8", &Name)) {
            throw Py::Exception();
        }
        std::string EncodedName = std::string(Name);
        PyMem_Free(Name);

        try {
            Base::Console().log("Open in Points with %s", EncodedName.c_str());
            Base::FileInfo file(EncodedName.c_str());

            // extract ending
            if (file.extension().empty()) {
                throw Py::RuntimeError("No file extension");
            }

            std::unique_ptr<Reader> reader;
            if (file.hasExtension("asc")) {
                reader = std::make_unique<AscReader>();
            }
            else if (file.hasExtension("e57")) {
                auto setting = readE57Settings();
                reader = std::make_unique<E57Reader>(
                    std::get<0>(setting),
                    std::get<1>(setting),
                    std::get<2>(setting)
                );
            }
            else if (file.hasExtension("ply")) {
                reader = std::make_unique<PlyReader>();
            }
            else if (file.hasExtension("pcd")) {
                reader = std::make_unique<PcdReader>();
            }
            else {
                throw Py::RuntimeError("Unsupported file extension");
            }

            reader->read(EncodedName);

            App::Document* pcDoc = App::GetApplication().newDocument();

            Points::Feature* pcFeature = nullptr;
            if (reader->hasProperties()) {
                // Scattered or structured points?
                if (reader->isStructured()) {
                    pcFeature = new Points::StructuredCustom();

                    App::PropertyInteger* width = static_cast<App::PropertyInteger*>(
                        pcFeature->getPropertyByName("Width")
                    );
                    if (width) {
                        width->setValue(reader->getWidth());
                    }
                    App::PropertyInteger* height = static_cast<App::PropertyInteger*>(
                        pcFeature->getPropertyByName("Height")
                    );
                    if (height) {
                        height->setValue(reader->getHeight());
                    }
                }
                else {
                    pcFeature = new Points::FeatureCustom();
                }

                pcFeature->Points.setValue(reader->getPoints());
                // add gray values
                if (reader->hasIntensities()) {
                    Points::PropertyGreyValueList* prop = static_cast<Points::PropertyGreyValueList*>(
                        pcFeature->addDynamicProperty("Points::PropertyGreyValueList", "Intensity")
                    );
                    if (prop) {
                        prop->setValues(reader->getIntensities());
                    }
                }
                // add colors
                if (reader->hasColors()) {
                    App::PropertyColorList* prop = static_cast<App::PropertyColorList*>(
                        pcFeature->addDynamicProperty("App::PropertyColorList", "Color")
                    );
                    if (prop) {
                        prop->setValues(reader->getColors());
                    }
                }
                // add normals
                if (reader->hasNormals()) {
                    Points::PropertyNormalList* prop = static_cast<Points::PropertyNormalList*>(
                        pcFeature->addDynamicProperty("Points::PropertyNormalList", "Normal")
                    );
                    if (prop) {
                        prop->setValues(reader->getNormals());
                    }
                }

                // delayed adding of the points feature
                pcDoc->addObject(pcFeature, file.fileNamePure().c_str());
                pcDoc->recomputeFeature(pcFeature);
                pcFeature->purgeTouched();
            }
            else {
                if (reader->isStructured()) {
                    Structured* structured = new Points::Structured();
                    structured->Width.setValue(reader->getWidth());
                    structured->Height.setValue(reader->getHeight());
                    pcFeature = structured;
                }
                else {
                    pcFeature = new Points::Feature();
                }

                // delayed adding of the points feature
                pcFeature->Points.setValue(reader->getPoints());
                pcDoc->addObject(pcFeature, file.fileNamePure().c_str());
                pcDoc->recomputeFeature(pcFeature);
                pcFeature->purgeTouched();
            }
        }
        catch (const Base::Exception& e) {
            throw Py::RuntimeError(e.what());
        }

        return Py::None();
    }

    Py::Object importer(const Py::Tuple& args)
    {
        char* Name {};
        const char* DocName {};
        if (!PyArg_ParseTuple(args.ptr(), "ets", "utf-8", &Name, &DocName)) {
            throw Py::Exception();
        }
        std::string EncodedName = std::string(Name);
        PyMem_Free(Name);

        try {
            Base::Console().log("Import in Points with %s", EncodedName.c_str());
            Base::FileInfo file(EncodedName.c_str());

            // extract ending
            if (file.extension().empty()) {
                throw Py::RuntimeError("No file extension");
            }

            std::unique_ptr<Reader> reader;
            if (file.hasExtension("asc")) {
                reader = std::make_unique<AscReader>();
            }
            else if (file.hasExtension("e57")) {
                auto setting = readE57Settings();
                reader = std::make_unique<E57Reader>(
                    std::get<0>(setting),
                    std::get<1>(setting),
                    std::get<2>(setting)
                );
            }
            else if (file.hasExtension("ply")) {
                reader = std::make_unique<PlyReader>();
            }
            else if (file.hasExtension("pcd")) {
                reader = std::make_unique<PcdReader>();
            }
            else {
                throw Py::RuntimeError("Unsupported file extension");
            }

            reader->read(EncodedName);

            App::Document* pcDoc = App::GetApplication().getDocument(DocName);
            if (!pcDoc) {
                pcDoc = App::GetApplication().newDocument(DocName);
            }

            Points::Feature* pcFeature = nullptr;
            if (reader->hasProperties()) {
                // Scattered or structured points?
                if (reader->isStructured()) {
                    pcFeature = new Points::StructuredCustom();

                    App::PropertyInteger* width = static_cast<App::PropertyInteger*>(
                        pcFeature->getPropertyByName("Width")
                    );
                    if (width) {
                        width->setValue(reader->getWidth());
                    }
                    App::PropertyInteger* height = static_cast<App::PropertyInteger*>(
                        pcFeature->getPropertyByName("Height")
                    );
                    if (height) {
                        height->setValue(reader->getHeight());
                    }
                }
                else {
                    pcFeature = new Points::FeatureCustom();
                }

                pcFeature->Points.setValue(reader->getPoints());
                // add gray values
                if (reader->hasIntensities()) {
                    Points::PropertyGreyValueList* prop = static_cast<Points::PropertyGreyValueList*>(
                        pcFeature->addDynamicProperty("Points::PropertyGreyValueList", "Intensity")
                    );
                    if (prop) {
                        prop->setValues(reader->getIntensities());
                    }
                }
                // add colors
                if (reader->hasColors()) {
                    App::PropertyColorList* prop = static_cast<App::PropertyColorList*>(
                        pcFeature->addDynamicProperty("App::PropertyColorList", "Color")
                    );
                    if (prop) {
                        prop->setValues(reader->getColors());
                    }
                }
                // add normals
                if (reader->hasNormals()) {
                    Points::PropertyNormalList* prop = static_cast<Points::PropertyNormalList*>(
                        pcFeature->addDynamicProperty("Points::PropertyNormalList", "Normal")
                    );
                    if (prop) {
                        prop->setValues(reader->getNormals());
                    }
                }

                // delayed adding of the points feature
                pcDoc->addObject(pcFeature, file.fileNamePure().c_str());
                pcDoc->recomputeFeature(pcFeature);
                pcFeature->purgeTouched();
            }
            else {
                auto* pcFeature = pcDoc->addObject<Points::Feature>(file.fileNamePure().c_str());
                pcFeature->Points.setValue(reader->getPoints());
                pcDoc->recomputeFeature(pcFeature);
                pcFeature->purgeTouched();
            }
        }
        catch (const Base::Exception& e) {
            throw Py::RuntimeError(e.what());
        }

        return Py::None();
    }

    Py::Object exporter(const Py::Tuple& args)
    {
        PyObject* object {};
        char* Name {};

        if (!PyArg_ParseTuple(args.ptr(), "Oet", &object, "utf-8", &Name)) {
            throw Py::Exception();
        }

        std::string encodedName = std::string(Name);
        PyMem_Free(Name);

        Base::FileInfo file(encodedName);

        // extract ending
        if (file.extension().empty()) {
            throw Py::RuntimeError("No file extension");
        }

        Py::Sequence list(object);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            PyObject* item = (*it).ptr();
            if (PyObject_TypeCheck(item, &(App::DocumentObjectPy::Type))) {
                App::DocumentObject* obj
                    = static_cast<App::DocumentObjectPy*>(item)->getDocumentObjectPtr();
                if (obj->isDerivedFrom<Points::Feature>()) {
                    // get relative placement
                    Points::Feature* fea = static_cast<Points::Feature*>(obj);
                    Base::Placement globalPlacement = fea->globalPlacement();

                    const PointKernel& kernel = fea->Points.getValue();
                    std::unique_ptr<Writer> writer;
                    if (file.hasExtension("asc")) {
                        writer = std::make_unique<AscWriter>(kernel);
                    }
                    else if (file.hasExtension("ply")) {
                        writer = std::make_unique<PlyWriter>(kernel);
                    }
                    else if (file.hasExtension("pcd")) {
                        writer = std::make_unique<PcdWriter>(kernel);
                    }
                    else {
                        throw Py::RuntimeError("Unsupported file extension");
                    }

                    // get additional properties if there
                    App::PropertyInteger* width = dynamic_cast<App::PropertyInteger*>(
                        fea->getPropertyByName("Width")
                    );
                    if (width) {
                        writer->setWidth(width->getValue());
                    }
                    App::PropertyInteger* height = dynamic_cast<App::PropertyInteger*>(
                        fea->getPropertyByName("Height")
                    );
                    if (height) {
                        writer->setHeight(height->getValue());
                    }
                    // get gray values
                    Points::PropertyGreyValueList* grey = dynamic_cast<Points::PropertyGreyValueList*>(
                        fea->getPropertyByName("Intensity")
                    );
                    if (grey) {
                        writer->setIntensities(grey->getValues());
                    }
                    // get colors
                    App::PropertyColorList* col = dynamic_cast<App::PropertyColorList*>(
                        fea->getPropertyByName("Color")
                    );
                    if (col) {
                        writer->setColors(col->getValues());
                    }
                    // get normals
                    Points::PropertyNormalList* nor = dynamic_cast<Points::PropertyNormalList*>(
                        fea->getPropertyByName("Normal")
                    );
                    if (nor) {
                        writer->setNormals(nor->getValues());
                    }

                    writer->setPlacement(globalPlacement);
                    writer->write(encodedName);

                    break;
                }
                else {
                    Base::Console().message(
                        "'%s' is not a point object, export will be ignored.\n",
                        obj->Label.getValue()
                    );
                }
            }
        }

        return Py::None();
    }

    Py::Object show(const Py::Tuple& args)
    {
        PyObject* pcObj {};
        const char* name = "Points";
        if (!PyArg_ParseTuple(args.ptr(), "O!|s", &(PointsPy::Type), &pcObj, &name)) {
            throw Py::Exception();
        }

        try {
            App::Document* pcDoc = App::GetApplication().getActiveDocument();
            if (!pcDoc) {
                pcDoc = App::GetApplication().newDocument();
            }
            auto* pPoints = static_cast<PointsPy*>(pcObj);
            auto* pcFeature = pcDoc->addObject<Points::Feature>(name);
            // copy the data
            pcFeature->Points.setValue(*(pPoints->getPointKernelPtr()));
            return Py::asObject(pcFeature->getPyObject());
        }
        catch (const Base::Exception& e) {
            throw Py::RuntimeError(e.what());
        }

        return Py::None();
    }
};

PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}

}  // namespace Points
