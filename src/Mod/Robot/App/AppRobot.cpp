// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <App/DocumentObjectPy.h>
#include <Base/Console.h>
#include <Base/Interpreter.h>
#include <Base/PlacementPy.h>

#include "Edge2TracObject.h"
#include "PropertyTrajectory.h"
#include "Robot6Axis.h"
#include "Robot6AxisPy.h"
#include "RobotObject.h"
#include "Simulation.h"
#include "Trajectory.h"
#include "TrajectoryCompound.h"
#include "TrajectoryDressUpObject.h"
#include "TrajectoryObject.h"
#include "TrajectoryPy.h"
#include "Waypoint.h"
#include "WaypointPy.h"


namespace Robot
{
class Module: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("Robot")
    {
        add_varargs_method(
            "simulateToFile",
            &Module::simulateToFile,
            "simulateToFile(Robot,Trajectory,TickSize,FileName) - runs the "
            "simulation and write the result to a file."
        );
        add_varargs_method(
            "swapPrecomputedTrajectory",
            &Module::swapPrecomputedTrajectory,
            "swapPrecomputedTrajectory(object, trajectory) - atomically exchange an "
            "already-generated trajectory with a Robot trajectory document object."
        );
        add_varargs_method(
            "previewTrajectorySamples",
            &Module::previewTrajectorySamples,
            "previewTrajectorySamples(robot, trajectory, times) - calculate bounded "
            "preview-only trajectory samples without changing either document object."
        );
        initialize("This module is the Robot module.");  // register with Python
    }

private:
    Py::Object simulateToFile(const Py::Tuple& args)
    {
        PyObject* pcRobObj;
        PyObject* pcTracObj;
        float tick;
        char* FileName;

        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!O!fs",
                &(Robot6AxisPy::Type),
                &pcRobObj,
                &(TrajectoryPy::Type),
                &pcTracObj,
                &tick,
                &FileName
            )) {
            throw Py::Exception();
        }

        try {
            Robot::Trajectory& Trac = *static_cast<TrajectoryPy*>(pcTracObj)->getTrajectoryPtr();
            Robot::Robot6Axis& Rob = *static_cast<Robot6AxisPy*>(pcRobObj)->getRobot6AxisPtr();
            Simulation Sim(Trac, Rob);
        }
        catch (const Base::Exception& e) {
            throw Py::RuntimeError(e.what());
        }

        return Py::Float(0.0);
    }

    Py::Object swapPrecomputedTrajectory(const Py::Tuple& args)
    {
        PyObject* objectPy = nullptr;
        PyObject* trajectoryPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!O!:swapPrecomputedTrajectory",
                &App::DocumentObjectPy::Type,
                &objectPy,
                &TrajectoryPy::Type,
                &trajectoryPy
            )) {
            throw Py::Exception();
        }

        auto* object = dynamic_cast<Robot::TrajectoryObject*>(
            static_cast<App::DocumentObjectPy*>(objectPy)->getDocumentObjectPtr()
        );
        if (!object) {
            throw Py::TypeError(
                "object must be a Robot::TrajectoryObject or a derived native type"
            );
        }
        auto* trajectory = static_cast<TrajectoryPy*>(trajectoryPy)->getTrajectoryPtr();
        object->Trajectory.swapValue(*trajectory);
        const Robot::Trajectory& installed = object->Trajectory.getValue();
        Py::Dict installedSummary;
        installedSummary["waypoint_count"] = Py::Long(
            static_cast<unsigned long>(installed.getSize())
        );
        installedSummary["length"] = Py::Float(installed.getLength());
        installedSummary["duration"] = Py::Float(installed.getDuration());
        Py::Dict displacedSummary;
        displacedSummary["waypoint_count"] = Py::Long(
            static_cast<unsigned long>(trajectory->getSize())
        );
        displacedSummary["length"] = Py::Float(trajectory->getLength());
        displacedSummary["duration"] = Py::Float(trajectory->getDuration());
        Py::Dict result;
        result["installed"] = installedSummary;
        result["displaced"] = displacedSummary;
        return result;
    }

    Py::Object previewTrajectorySamples(const Py::Tuple& args)
    {
        PyObject* robotPy = nullptr;
        PyObject* trajectoryPy = nullptr;
        PyObject* timesPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!O!O:previewTrajectorySamples",
                &App::DocumentObjectPy::Type,
                &robotPy,
                &App::DocumentObjectPy::Type,
                &trajectoryPy,
                &timesPy
            )) {
            throw Py::Exception();
        }

        auto* robot = dynamic_cast<Robot::RobotObject*>(
            static_cast<App::DocumentObjectPy*>(robotPy)->getDocumentObjectPtr()
        );
        auto* trajectoryObject = dynamic_cast<Robot::TrajectoryObject*>(
            static_cast<App::DocumentObjectPy*>(trajectoryPy)->getDocumentObjectPtr()
        );
        if (!robot) {
            throw Py::TypeError("robot must be a Robot::RobotObject");
        }
        if (!trajectoryObject) {
            throw Py::TypeError(
                "trajectory must be a Robot::TrajectoryObject or a derived native type"
            );
        }
        if (!robot->getDocument()
            || robot->getDocument() != trajectoryObject->getDocument()) {
            throw Py::ValueError("robot and trajectory must belong to the same document");
        }

        std::unique_ptr<PyObject, decltype(&Py_DecRef)> times(
            PySequence_Fast(timesPy, "times must be a sequence"),
            &Py_DecRef
        );
        if (!times) {
            throw Py::Exception();
        }
        const Py_ssize_t sampleCount = PySequence_Fast_GET_SIZE(times.get());
        if (sampleCount < 1 || sampleCount > 64) {
            throw Py::ValueError("times must contain between 1 and 64 samples");
        }

        const Robot::Trajectory& trajectory = trajectoryObject->Trajectory.getValue();
        const double duration = trajectory.getDuration();
        if (trajectory.getSize() < 2 || !std::isfinite(duration) || duration <= 0.0) {
            throw Py::ValueError(
                "trajectory must contain at least two waypoints and have positive duration"
            );
        }

        Robot::Robot6Axis probe(robot->getRobot());
        Robot::Simulation simulation(trajectory, probe);
        simulation.Tool = robot->Tool.getValue();
        Py::List samples;
        float previousTime = -1.0F;
        for (Py_ssize_t index = 0; index < sampleCount; ++index) {
            PyObject* item = PySequence_Fast_GET_ITEM(times.get(), index);
            const double requestedTime = PyFloat_AsDouble(item);
            if (PyErr_Occurred()) {
                throw Py::Exception();
            }
            const float time = static_cast<float>(requestedTime);
            if (!std::isfinite(requestedTime) || !std::isfinite(time) || time < 0.0F
                || time > static_cast<float>(duration) || time <= previousTime) {
                throw Py::ValueError(
                    "times must be finite, strictly increasing float32 values within duration"
                );
            }
            previousTime = time;
            simulation.setToTime(time);

            Py::List axes;
            for (int axis = 0; axis < 6; ++axis) {
                const double value = simulation.Axis[axis];
                if (!std::isfinite(value)) {
                    throw Py::RuntimeError(
                        "Robot simulation returned a non-finite axis value"
                    );
                }
                axes.append(Py::Float(value));
            }
            const Base::Placement tcp = probe.getTcp();
            const Base::Placement pathTarget = simulation.getPosition();
            const double velocity = simulation.getVelocity();
            if (!std::isfinite(velocity)) {
                throw Py::RuntimeError("Robot simulation returned a non-finite velocity");
            }

            Py::Dict sample;
            sample["time_s"] = Py::Float(static_cast<double>(time));
            sample["axes_degrees"] = axes;
            sample["tcp"] = Py::asObject(
                new Base::PlacementPy(new Base::Placement(tcp))
            );
            sample["path_target"] = Py::asObject(
                new Base::PlacementPy(new Base::Placement(pathTarget))
            );
            sample["velocity_mm_per_s"] = Py::Float(velocity);
            samples.append(sample);
        }
        return samples;
    }
};

PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}

}  // namespace Robot


/* Python entry */
PyMOD_INIT_FUNC(Robot)
{
    // clang-format off
    // load dependent module
    try {
        Base::Interpreter().runString("import Part");
    }
    catch(const Base::Exception& e) {
        PyErr_SetString(PyExc_ImportError, e.what());
        PyMOD_Return(nullptr);
    }

    PyObject* robotModule = Robot::initModule();
    Base::Console().log("Loading Robot module… done\n");

    // Add Types to module
    Base::Interpreter().addType(&Robot::Robot6AxisPy          ::Type,robotModule,"Robot6Axis");
    Base::Interpreter().addType(&Robot::WaypointPy            ::Type,robotModule,"Waypoint");
    Base::Interpreter().addType(&Robot::TrajectoryPy          ::Type,robotModule,"Trajectory");


    // NOTE: To finish the initialization of our own type objects we must
    // call PyType_Ready, otherwise we run into a segmentation fault, later on.
    // This function is responsible for adding inherited slots from a type's base class.

    Robot::Robot6Axis              ::init();
    Robot::RobotObject             ::init();
    Robot::TrajectoryObject        ::init();
    Robot::Edge2TracObject         ::init();
    Robot::Waypoint                ::init();
    Robot::Trajectory              ::init();
    Robot::PropertyTrajectory      ::init();
    Robot::TrajectoryCompound      ::init();
    Robot::TrajectoryDressUpObject ::init();

    PyMOD_Return(robotModule);
    // clang-format on
}
