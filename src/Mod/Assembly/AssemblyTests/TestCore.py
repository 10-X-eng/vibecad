# SPDX-License-Identifier: LGPL-2.1-or-later
# /****************************************************************************
#                                                                           *
#    Copyright (c) 2023 Ondsel <development@ondsel.com>                     *
#                                                                           *
#    This file is part of FreeCAD.                                          *
#                                                                           *
#    FreeCAD is free software: you can redistribute it and/or modify it     *
#    under the terms of the GNU Lesser General Public License as            *
#    published by the Free Software Foundation, either version 2.1 of the   *
#    License, or (at your option) any later version.                        *
#                                                                           *
#    FreeCAD is distributed in the hope that it will be useful, but         *
#    WITHOUT ANY WARRANTY; without even the implied warranty of             *
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
#    Lesser General Public License for more details.                        *
#                                                                           *
#    You should have received a copy of the GNU Lesser General Public       *
#    License along with FreeCAD. If not, see                                *
#    <https://www.gnu.org/licenses/>.                                       *
#                                                                           *
# ***************************************************************************/

import FreeCAD as App
import Part
import tempfile
import unittest

import UtilsAssembly
import JointObject


class _ShowTimelineResourceOnRestore:
    """Simulate a Python proxy changing presentation during reconstruction."""

    def __init__(self, obj):
        obj.Proxy = self

    def onDocumentRestored(self, obj):
        obj.Visibility = True

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def _msg(text, end="\n"):
    """Write messages to the console including the line ending."""
    App.Console.PrintMessage(text + end)


class TestCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """setUpClass()...
        This method is called upon instantiation of this test class.  Add code and objects here
        that are needed for the duration of the test() methods in this class.  In other words,
        set up the 'global' test environment here; use the `setUp()` method to set up a 'local'
        test environment.
        This method does not have access to the class `self` reference, but it
        is able to call static methods within this same class.
        """
        pass

    @classmethod
    def tearDownClass(cls):
        """tearDownClass()...
        This method is called prior to destruction of this test class.  Add code and objects here
        that cleanup the test environment after the test() methods in this class have been executed.
        This method does not have access to the class `self` reference.  This method
        is able to call static methods within this same class.
        """
        pass

    # Setup and tear down methods called before and after each unit test
    def setUp(self):
        """setUp()...
        This method is called prior to each `test()` method.  Add code and objects here
        that are needed for multiple `test()` methods.
        """
        doc_name = self.__class__.__name__
        if App.ActiveDocument:
            if App.ActiveDocument.Name != doc_name:
                App.newDocument(doc_name)
        else:
            App.newDocument(doc_name)
        App.setActiveDocument(doc_name)
        self.doc = App.ActiveDocument

        self.assembly = App.ActiveDocument.addObject("Assembly::AssemblyObject", "Assembly")
        if self.assembly:
            self.jointgroup = self.assembly.newObject("Assembly::JointGroup", "Joints")

        _msg("  Temporary document '{}'".format(self.doc.Name))

    def tearDown(self):
        """tearDown()...
        This method is called after each test() method. Add cleanup instructions here.
        Such cleanup instructions will likely undo those in the setUp() method.
        """
        App.closeDocument(self.doc.Name)

    def _disable_solve_on_recompute(self):
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Assembly"
        )
        previous = preferences.GetBool("SolveOnRecompute", True)
        preferences.SetBool("SolveOnRecompute", False)
        self.addCleanup(
            preferences.SetBool,
            "SolveOnRecompute",
            previous,
        )

    def _timeline(self):
        timeline = self.doc.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(timeline.TypeId, "App::DocumentTimeline")
        return timeline

    def _timeline_index(self, obj):
        operations = list(self._timeline().Operations)
        self.assertIn(obj, operations)
        return operations.index(obj)

    def test_create_assembly(self):
        """Create an assembly."""
        operation = "Create Assembly Object"
        _msg("  Test '{}'".format(operation))
        self.assertTrue(self.assembly, "'{}' failed".format(operation))

    def test_create_jointGroup(self):
        """Create a joint group in an assembly."""
        operation = "Create JointGroup Object"
        _msg("  Test '{}'".format(operation))
        self.assertTrue(self.jointgroup, "'{}' failed".format(operation))

    def test_create_joint(self):
        """Create a joint in an assembly."""
        operation = "Create Joint Object"
        _msg("  Test '{}'".format(operation))

        joint = self.jointgroup.newObject("App::FeaturePython", "testJoint")
        self.assertTrue(joint, "'{}' failed (FeaturePython creation failed)".format(operation))
        JointObject.Joint(joint, 0)

        self.assertTrue(hasattr(joint, "JointType"), "'{}' failed".format(operation))

    def test_create_grounded_joint(self):
        """Create a grounded joint in an assembly."""
        operation = "Create Grounded Joint Object"
        _msg("  Test '{}'".format(operation))

        groundedjoint = self.jointgroup.newObject("App::FeaturePython", "testJoint")
        self.assertTrue(
            groundedjoint, "'{}' failed (FeaturePython creation failed)".format(operation)
        )

        box = self.assembly.newObject("Part::Box", "Box")

        JointObject.GroundedJoint(groundedjoint, box)

        self.assertTrue(
            hasattr(groundedjoint, "ObjectToGround"),
            "'{}' failed: No attribute 'ObjectToGround'".format(operation),
        )
        self.assertTrue(
            groundedjoint.ObjectToGround == box,
            "'{}' failed: ObjectToGround not set correctly.".format(operation),
        )

    @unittest.skipUnless(App.GuiUp, "GUI test requires FreeCAD GUI mode")
    def test_restore_joint_view_providers_from_placeholder(self):
        """Restore native joint view providers saved with FreeCAD's placeholder."""

        def assert_restored_view_providers(document):
            restored_joint = document.getObject("RestoredJoint")
            restored_ground = document.getObject("RestoredGround")
            joint_view = restored_joint.ViewObject.Proxy
            ground_view = restored_ground.ViewObject.Proxy
            self.assertIsInstance(joint_view, JointObject.ViewProviderJoint)
            self.assertTrue(hasattr(joint_view, "switch_JCS1"))
            self.assertTrue(hasattr(joint_view, "switch_JCS2"))
            joint_view.redrawJointPlacements(restored_joint)
            self.assertIsInstance(ground_view, JointObject.ViewProviderGroundedJoint)
            self.assertIs(ground_view.app_obj, restored_ground)

        joint = self.jointgroup.newObject("App::FeaturePython", "RestoredJoint")
        JointObject.Joint(joint, 1)
        joint.Detach1 = True
        joint.Detach2 = True

        box = self.assembly.newObject("Part::Box", "GroundedBox")
        other_box = self.assembly.newObject("Part::Box", "OtherBox")
        joint.Reference1 = [box, ["", ""]]
        joint.Reference2 = [other_box, ["", ""]]
        grounded = self.jointgroup.newObject("App::FeaturePython", "RestoredGround")
        JointObject.GroundedJoint(grounded, box)
        self.doc.recompute()
        joint.ViewObject.Proxy = 1
        grounded.ViewObject.Proxy = 1

        temporary = tempfile.TemporaryDirectory(prefix="assembly-joint-view-provider-")
        self.addCleanup(temporary.cleanup)
        path = temporary.name + "/joint-view-provider.FCStd"
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)

        self.doc = App.openDocument(path)
        assert_restored_view_providers(self.doc)

        # The repaired providers must persist as their native Python classes,
        # rather than reverting to the integer placeholder on the next load.
        self.doc.save()
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        assert_restored_view_providers(self.doc)

    def test_toggle_grounded_joint(self):
        """test grounding and ungrounding a part, added because of github.com/freecad/freecad/issues/28440"""
        operation = "Toggle Grounded Joint"
        _msg("  Test '{}'".format(operation))

        box = self.assembly.newObject("Part::Box", "Box")

        # ground the part
        groundedjoint = self.jointgroup.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(groundedjoint, box)
        self.doc.recompute()

        # verify grounded
        self.assertTrue(
            hasattr(groundedjoint, "ObjectToGround"),
            "'{}' failed: No attribute 'ObjectToGround'".format(operation),
        )
        self.assertEqual(
            groundedjoint.ObjectToGround,
            box,
            "'{}' failed: ObjectToGround not set correctly".format(operation),
        )

        # unground the part
        self.doc.removeObject(groundedjoint.Name)
        self.doc.recompute()

        # verify no grounded joints remain in this part
        for joint in self.jointgroup.Group:
            if hasattr(joint, "ObjectToGround"):
                self.assertNotEqual(
                    joint.ObjectToGround,
                    box,
                    "'{}' failed: part still grounded after toggle".format(operation),
                )

    def test_timeline_filters_components_and_joints_not_visibility(self):
        """Assembly membership follows history, while ordinary hiding does not."""
        self._disable_solve_on_recompute()

        grounded_part = self.assembly.newObject("Part::Box", "TimelineGround")
        moving_part = self.assembly.newObject("Part::Box", "TimelineMoving")
        self.doc.recompute()

        ground = self.jointgroup.newObject("App::FeaturePython", "TimelineGroundedJoint")
        JointObject.GroundedJoint(ground, grounded_part)
        joint = self.jointgroup.newObject("App::FeaturePython", "TimelineFixedJoint")
        JointObject.Joint(joint, 0)
        joint.Proxy.setJointConnectors(
            joint,
            [
                [grounded_part, ["Face6", "Vertex7"]],
                [moving_part, ["Face6", "Vertex7"]],
            ],
        )
        self.doc.recompute()

        timeline = self._timeline()
        end_position = len(timeline.Operations)
        moving_index = self._timeline_index(moving_part)
        joint_index = self._timeline_index(joint)

        self.assertEqual(UtilsAssembly.number_of_components_in(self.assembly), 2)
        self.assertTrue(self.assembly.isPartConnected(moving_part))

        moving_part.Visibility = False
        joint.Visibility = False
        self.assertEqual(UtilsAssembly.number_of_components_in(self.assembly), 2)
        self.assertTrue(self.assembly.isPartConnected(moving_part))

        timeline.Position = joint_index
        self.doc.recompute()
        self.assertFalse(self.assembly.isPartConnected(moving_part))

        timeline.Position = moving_index
        self.doc.recompute()
        self.assertEqual(UtilsAssembly.number_of_components_in(self.assembly), 1)

        timeline.Position = end_position
        self.doc.recompute()
        self.assertEqual(UtilsAssembly.number_of_components_in(self.assembly), 2)
        self.assertTrue(self.assembly.isPartConnected(moving_part))

    def test_timeline_grounding_survives_undo_redo_and_reopen(self):
        """A future GroundedJoint is retained, unlocked, and restored exactly."""
        self._disable_solve_on_recompute()
        self.doc.UndoMode = True

        component = self.assembly.newObject("Part::Box", "TimelineGroundedComponent")
        ground = self.jointgroup.newObject("App::FeaturePython", "TimelineGroundedJoint")
        JointObject.GroundedJoint(ground, component)
        self.doc.recompute()

        timeline = self._timeline()
        ground_index = self._timeline_index(ground)
        end_position = len(timeline.Operations)

        def grounded_joints():
            return [
                obj
                for obj in self.jointgroup.Group
                if hasattr(obj, "ObjectToGround")
                and obj.ObjectToGround == component
            ]

        self.assertIn("ReadOnly", component.getPropertyStatus("Placement"))
        self.assertTrue(self.assembly.isPartGrounded(component))
        self.assertEqual(len(grounded_joints()), 1)

        self.doc.openTransaction("Move before grounded joint")
        timeline.Position = ground_index
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertNotIn("ReadOnly", component.getPropertyStatus("Placement"))
        self.assertFalse(self.assembly.isPartGrounded(component))
        self.assertEqual(len(grounded_joints()), 1)

        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(timeline.Position, end_position)
        self.assertIn("ReadOnly", component.getPropertyStatus("Placement"))
        self.assertTrue(self.assembly.isPartGrounded(component))
        self.assertEqual(len(grounded_joints()), 1)

        self.doc.redo()
        self.doc.recompute()
        self.assertEqual(timeline.Position, ground_index)
        self.assertNotIn("ReadOnly", component.getPropertyStatus("Placement"))
        self.assertFalse(self.assembly.isPartGrounded(component))
        self.assertEqual(len(grounded_joints()), 1)

        temporary = tempfile.TemporaryDirectory(prefix="assembly-timeline-")
        self.addCleanup(temporary.cleanup)
        path = temporary.name + "/rolled-back-assembly.FCStd"
        assembly_name = self.assembly.Name
        component_name = component.Name
        joint_group_name = self.jointgroup.Name
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)

        self.doc = App.openDocument(path)
        self.assembly = self.doc.getObject(assembly_name)
        self.jointgroup = self.doc.getObject(joint_group_name)
        component = self.doc.getObject(component_name)
        timeline = self._timeline()
        self.doc.recompute()

        self.assertEqual(timeline.Position, ground_index)
        self.assertNotIn("ReadOnly", component.getPropertyStatus("Placement"))
        self.assertFalse(self.assembly.isPartGrounded(component))
        self.assertEqual(
            len(
                [
                    obj
                    for obj in self.jointgroup.Group
                    if hasattr(obj, "ObjectToGround")
                    and obj.ObjectToGround == component
                ]
            ),
            1,
        )

        timeline.Position = len(timeline.Operations)
        self.doc.recompute()
        self.assertIn("ReadOnly", component.getPropertyStatus("Placement"))
        self.assertTrue(self.assembly.isPartGrounded(component))

    def test_exploded_steps_follow_timeline_not_visibility(self):
        """Only active exploded steps move components."""
        import CommandCreateView

        self._disable_solve_on_recompute()
        component = self.assembly.newObject("Part::Box", "ExplodedTimelineComponent")
        self.doc.recompute()

        view_group = UtilsAssembly.getViewGroup(self.assembly)
        exploded = view_group.newObject("App::FeaturePython", "TimelineExplodedView")
        CommandCreateView.ExplodedView(exploded)
        move = self.assembly.newObject("App::FeaturePython", "TimelineExplodedMove")
        CommandCreateView.ExplodedViewStep(move)
        move.References = [self.assembly, [component.Name + "."]]
        move.MovementTransform = App.Placement(
            App.Vector(10, 0, 0),
            App.Rotation(),
        )
        exploded.Group = [move]
        self.doc.recompute()

        timeline = self._timeline()
        exploded_index = self._timeline_index(exploded)
        end_position = len(timeline.Operations)
        self.assertNotIn(view_group, timeline.Operations)
        self.assertEqual(exploded.VibeCADTimelineRole, "operation")
        self.assertEqual(move.VibeCADTimelineRole, "resource")
        self.assertIs(move.VibeCADTimelineOwner, exploded)
        self.assertEqual(
            move.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertNotIn(exploded, move.OutList)

        component.Placement = App.Placement()
        exploded.Proxy.applyMoves(exploded)
        self.assertAlmostEqual(component.Placement.Base.x, 10.0)

        component.Placement = App.Placement()
        timeline.Position = exploded_index
        self.doc.recompute()
        exploded.Proxy.applyMoves(exploded)
        self.assertAlmostEqual(component.Placement.Base.x, 0.0)

        timeline.Position = exploded_index + 1
        self.doc.recompute()
        exploded.Proxy.applyMoves(exploded)
        self.assertAlmostEqual(component.Placement.Base.x, 10.0)

        timeline.Position = end_position
        self.doc.recompute()
        move.Visibility = False
        exploded.Proxy.applyMoves(exploded)
        self.assertAlmostEqual(component.Placement.Base.x, 10.0)

    def test_restore_reconciles_python_resource_to_accepted_visibility(self):
        """History wins after every Python restore callback has completed."""
        with tempfile.TemporaryDirectory(prefix="assembly-timeline-restore-") as root:
            path = f"{root}/accepted-resource-visibility.FCStd"
            self.doc.openTransaction("Create restore presentation graph")
            operation = self.doc.addObject(
                "App::FeaturePython",
                "RestorePresentationOperation",
            )
            resource = self.doc.addObject(
                "App::FeaturePython",
                "RestorePresentationResource",
            )
            _ShowTimelineResourceOnRestore(resource)
            UtilsAssembly.markTimelineOperation(operation)
            UtilsAssembly.markTimelineResource(resource, operation)
            resource.Visibility = False
            self.doc.finalizeProvisionalTimelineOperationBlock(
                operation,
                [resource, operation],
            )
            self.doc.commitTransaction()

            timeline = self._timeline()
            resource_index = list(timeline.Operations).index(resource)
            self.assertFalse(bool(timeline.VisibilityAtEnd[resource_index]))
            self.assertFalse(bool(resource.Visibility))
            self.doc.recompute()
            self.doc.saveAs(path)

            document_name = self.doc.Name
            App.closeDocument(document_name)
            self.doc = App.openDocument(path)
            resource = self.doc.getObject("RestorePresentationResource")
            timeline = self._timeline()
            resource_index = list(timeline.Operations).index(resource)
            self.assertFalse(bool(timeline.VisibilityAtEnd[resource_index]))
            self.assertFalse(bool(resource.Visibility))

    def test_simulation_motions_are_one_timeline_operation_and_reopen(self):
        """Simulation parameters own their motions as one durable history step."""
        import CommandCreateSimulation

        simulation_group = UtilsAssembly.getSimulationGroup(self.assembly)
        simulation = simulation_group.newObject(
            "App::FeaturePython",
            "TimelineSimulation",
        )
        CommandCreateSimulation.Simulation(simulation)
        motion = self.assembly.newObject(
            "App::FeaturePython",
            "TimelineMotion",
        )
        CommandCreateSimulation.Motion(motion)
        simulation.Group = [motion]
        self.doc.recompute()

        timeline = self._timeline()
        simulation_index = self._timeline_index(simulation)
        self.assertNotIn(simulation_group, timeline.Operations)
        self.assertEqual(simulation.VibeCADTimelineRole, "operation")
        self.assertEqual(motion.VibeCADTimelineRole, "resource")
        self.assertIs(motion.VibeCADTimelineOwner, simulation)
        self.assertEqual(
            motion.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertNotIn(simulation, motion.OutList)

        timeline.Position = simulation_index
        self.assertFalse(UtilsAssembly.isTimelineOperationActive(simulation))
        self.assertFalse(UtilsAssembly.isTimelineOperationActive(motion))
        timeline.Position = simulation_index + 1
        self.assertTrue(UtilsAssembly.isTimelineOperationActive(simulation))
        self.assertTrue(UtilsAssembly.isTimelineOperationActive(motion))

        temporary = tempfile.TemporaryDirectory(prefix="assembly-simulation-timeline-")
        self.addCleanup(temporary.cleanup)
        path = temporary.name + "/simulation.FCStd"
        assembly_name = self.assembly.Name
        simulation_name = simulation.Name
        motion_name = motion.Name
        saved_position = timeline.Position
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.assembly = self.doc.getObject(assembly_name)

        restored_simulation = self.doc.getObject(simulation_name)
        restored_motion = self.doc.getObject(motion_name)
        restored_timeline = self._timeline()
        self.assertEqual(
            restored_simulation.VibeCADTimelineRole,
            "operation",
        )
        self.assertEqual(restored_motion.VibeCADTimelineRole, "resource")
        self.assertIs(
            restored_motion.VibeCADTimelineOwner,
            restored_simulation,
        )
        self.assertEqual(restored_timeline.Position, saved_position)
        self.assertTrue(
            UtilsAssembly.isTimelineOperationActive(restored_motion)
        )

    def test_assembly_and_bom_group_containers_are_not_history_steps(self):
        """Structural groups stay in the tree without becoming user actions."""

        bom_group = UtilsAssembly.getBomGroup(self.assembly)
        bom = bom_group.newObject(
            "Assembly::BomObject",
            "TimelineBillOfMaterials",
        )
        self.doc.recompute()

        timeline = self._timeline()
        self.assertIn(self.assembly, timeline.Operations)
        self.assertIn(bom, timeline.Operations)
        self.assertNotIn(self.jointgroup, timeline.Operations)
        self.assertNotIn(bom_group, timeline.Operations)

    def test_tracked_occurrence_synchronizes_source_membership_on_commit(self):
        """Source structure and its occurrence close as one History change."""

        self.doc.UndoMode = False
        source_assembly = self.doc.addObject(
            "Assembly::AssemblyObject",
            "AutomaticSourceAssembly",
        )
        source_assembly.Type = "Assembly"
        source_shape = self.doc.addObject(
            "Part::Feature",
            "AutomaticSourceShape",
        )
        source_shape.Shape = Part.makeBox(8, 6, 4)
        first_source = source_assembly.newObject(
            "App::Link",
            "AutomaticFirstSource",
        )
        first_source.LinkedObject = source_shape
        self.doc.recompute()
        self.doc.UndoMode = True

        self.doc.openTransaction("Insert automatic occurrence")
        try:
            occurrence = self.assembly.newObject(
                "Assembly::AssemblyLink",
                "AutomaticOccurrence",
            )
            occurrence.LinkedObject = source_assembly
            UtilsAssembly.finalizeInsertedComponentTimeline(
                occurrence
            )
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise

        old_resource_names = tuple(
            resource.Name
            for resource
            in UtilsAssembly._assemblyOccurrenceResources(
                occurrence
            )
        )
        self.doc.openTransaction("Create occurrence consumer")
        try:
            consumer = self.doc.addObject(
                "App::FeaturePython",
                "AutomaticOccurrenceConsumer",
            )
            consumer.addProperty(
                "App::PropertyXLink",
                "Occurrence",
            )
            consumer.Occurrence = occurrence
            self.doc.publishProvisionalTimelineOperationBlock(
                consumer,
                [],
            )
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise

        self.doc.openTransaction("Add automatic source member")
        try:
            added_source = source_assembly.newObject(
                "App::Link",
                "AutomaticAddedSource",
            )
            added_source.LinkedObject = source_shape
            self.doc.publishProvisionalTimelineOperationBlock(
                added_source,
                [],
            )
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise

        occurrence_name = occurrence.Name
        local_added = next(
            resource
            for resource
            in UtilsAssembly._assemblyOccurrenceResources(
                occurrence
            )
            if getattr(
                resource,
                "VibeCADAssemblySourceObjectId",
                -1,
            )
            == int(added_source.ID)
        )
        local_added_name = local_added.Name
        operations = list(self._timeline().Operations)
        self.assertLess(
            operations.index(added_source),
            operations.index(local_added),
        )
        self.assertLess(
            operations.index(local_added),
            operations.index(occurrence),
        )
        self.assertLess(
            operations.index(occurrence),
            operations.index(consumer),
        )

        self.doc.undo()
        occurrence = self.doc.getObject(occurrence_name)
        self.assertIsNone(
            self.doc.getObject("AutomaticAddedSource")
        )
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            old_resource_names,
        )

        self.doc.redo()
        occurrence = self.doc.getObject(occurrence_name)
        added_source = self.doc.getObject(
            "AutomaticAddedSource"
        )
        self.assertIsNotNone(added_source)
        self.assertTrue(
            any(
                getattr(
                    resource,
                    "VibeCADAssemblySourceObjectId",
                    -1,
                )
                == int(added_source.ID)
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            )
        )

        self.doc.openTransaction(
            "Delete automatic source member"
        )
        try:
            self.doc.removeObject(added_source.Name)
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise

        occurrence = self.doc.getObject(occurrence_name)
        self.assertIsNone(
            self.doc.getObject("AutomaticAddedSource")
        )
        self.assertIsNone(
            self.doc.getObject(local_added_name)
        )
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            old_resource_names,
        )

        self.doc.undo()
        occurrence = self.doc.getObject(occurrence_name)
        self.assertIsNotNone(
            self.doc.getObject("AutomaticAddedSource")
        )
        self.assertIsNotNone(
            self.doc.getObject(local_added_name)
        )
        self.assertTrue(
            any(
                resource.Name == local_added_name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            )
        )

        self.doc.redo()
        occurrence = self.doc.getObject(occurrence_name)
        self.assertIsNone(
            self.doc.getObject("AutomaticAddedSource")
        )
        self.assertIsNone(
            self.doc.getObject(local_added_name)
        )
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            old_resource_names,
        )

    def test_external_source_membership_synchronizes_atomically_on_commit(self):
        """An external source edit updates and persists its occurrence."""

        self._disable_solve_on_recompute()
        source_document = App.newDocument(
            "AutomaticExternalAssemblySource",
        )
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                source_path = (
                    temporary_directory
                    + "/automatic-external-source.FCStd"
                )
                target_path = (
                    temporary_directory
                    + "/automatic-external-target.FCStd"
                )

                source_document.UndoMode = False
                source_assembly = source_document.addObject(
                    "Assembly::AssemblyObject",
                    "ExternalSourceAssembly",
                )
                source_assembly.Type = "Assembly"
                source_shape = source_document.addObject(
                    "Part::Feature",
                    "ExternalSourceShape",
                )
                source_shape.Shape = Part.makeBox(9, 7, 5)
                first_source = source_assembly.newObject(
                    "App::Link",
                    "ExternalFirstSource",
                )
                first_source.LinkedObject = source_shape
                source_document.recompute()
                source_document.UndoMode = True
                source_document.saveAs(source_path)

                App.setActiveDocument(self.doc.Name)
                self.doc.UndoMode = True
                self.doc.recompute()
                self.doc.saveAs(target_path)
                self.doc.openTransaction(
                    "Insert external automatic occurrence"
                )
                try:
                    occurrence = self.assembly.newObject(
                        "Assembly::AssemblyLink",
                        "ExternalAutomaticOccurrence",
                    )
                    occurrence.LinkedObject = source_assembly
                    UtilsAssembly.finalizeInsertedComponentTimeline(
                        occurrence
                    )
                    self.doc.commitTransaction()
                except Exception:
                    self.doc.abortTransaction()
                    raise

                occurrence_name = occurrence.Name
                old_resource_names = tuple(
                    resource.Name
                    for resource
                    in UtilsAssembly._assemblyOccurrenceResources(
                        occurrence
                    )
                )
                source_undo_before = int(source_document.UndoCount)
                target_undo_before = int(self.doc.UndoCount)

                source_document.openTransaction(
                    "Add external automatic source member"
                )
                try:
                    added_source = source_assembly.newObject(
                        "App::Link",
                        "ExternalAutomaticallyAddedSource",
                    )
                    added_source.LinkedObject = source_shape
                    source_document.publishProvisionalTimelineOperationBlock(
                        added_source,
                        [],
                    )
                    # Closing only the source edit must join and update the
                    # occurrence document under this same exact transaction.
                    source_document.commitTransaction()
                except Exception:
                    source_document.abortTransaction()
                    raise

                self.assertFalse(source_document.HasPendingTransaction)
                self.assertFalse(self.doc.HasPendingTransaction)
                self.assertEqual(
                    int(source_document.getBookedTransactionID()),
                    0,
                )
                self.assertEqual(
                    int(self.doc.getBookedTransactionID()),
                    0,
                )
                self.assertEqual(
                    int(source_document.UndoCount),
                    source_undo_before + 1,
                )
                self.assertEqual(
                    int(self.doc.UndoCount),
                    target_undo_before + 1,
                )

                local_added = next(
                    resource
                    for resource
                    in UtilsAssembly._assemblyOccurrenceResources(
                        occurrence
                    )
                    if getattr(
                        resource,
                        "VibeCADAssemblySourceObjectId",
                        -1,
                    )
                    == int(added_source.ID)
                )
                target_operations = list(
                    self._timeline().Operations
                )
                self.assertLess(
                    target_operations.index(local_added),
                    target_operations.index(occurrence),
                )

                synchronized_resource_names = tuple(
                    resource.Name
                    for resource
                    in UtilsAssembly._assemblyOccurrenceResources(
                        occurrence
                    )
                )
                synchronized_target_undo = int(self.doc.UndoCount)
                synchronized_source_undo = int(
                    source_document.UndoCount
                )
                source_document.openTransaction(
                    "Cancel external source member"
                )
                try:
                    canceled_source = source_assembly.newObject(
                        "App::Link",
                        "CanceledExternalSource",
                    )
                    canceled_source.LinkedObject = source_shape
                    source_document.publishProvisionalTimelineOperationBlock(
                        canceled_source,
                        [],
                    )
                finally:
                    source_document.abortTransaction()

                self.assertIsNone(
                    source_document.getObject(
                        "CanceledExternalSource"
                    )
                )
                self.assertEqual(
                    tuple(
                        resource.Name
                        for resource
                        in UtilsAssembly._assemblyOccurrenceResources(
                            occurrence
                        )
                    ),
                    synchronized_resource_names,
                )
                self.assertEqual(
                    int(source_document.UndoCount),
                    synchronized_source_undo,
                )
                self.assertEqual(
                    int(self.doc.UndoCount),
                    synchronized_target_undo,
                )

                source_document.save()
                self.doc.save()
                App.closeDocument(self.doc.Name)
                App.closeDocument(source_document.Name)
                source_document = App.openDocument(source_path)
                self.doc = App.openDocument(target_path)

                restored_source = source_document.getObject(
                    "ExternalAutomaticallyAddedSource"
                )
                restored_occurrence = self.doc.getObject(
                    occurrence_name
                )
                self.assertIsNotNone(restored_source)
                self.assertIsNotNone(restored_occurrence)
                self.assertEqual(
                    tuple(
                        resource.Name
                        for resource
                        in UtilsAssembly._assemblyOccurrenceResources(
                            restored_occurrence
                        )
                    ),
                    synchronized_resource_names,
                )
                self.assertTrue(
                    any(
                        getattr(
                            resource,
                            "VibeCADAssemblySourceObjectId",
                            -1,
                        )
                        == int(restored_source.ID)
                        for resource
                        in UtilsAssembly._assemblyOccurrenceResources(
                            restored_occurrence
                        )
                    )
                )
                self.assertNotEqual(
                    synchronized_resource_names,
                    old_resource_names,
                )
        finally:
            if source_document.Name in App.listDocuments():
                App.closeDocument(source_document.Name)

    def test_flexible_occurrence_synchronizes_source_joint_lifecycle(self):
        """Source joint creation and deletion update one flexible occurrence."""

        self._disable_solve_on_recompute()
        self.doc.UndoMode = False
        source_assembly = self.doc.addObject(
            "Assembly::AssemblyObject",
            "AutomaticJointSourceAssembly",
        )
        source_assembly.Type = "Assembly"
        source_joint_group = source_assembly.newObject(
            "Assembly::JointGroup",
            "AutomaticSourceJoints",
        )
        source_shape = self.doc.addObject(
            "Part::Feature",
            "AutomaticJointSourceShape",
        )
        source_shape.Shape = Part.makeBox(10, 8, 6)
        source_components = []
        for index in range(2):
            component = source_assembly.newObject(
                "App::Link",
                f"AutomaticJointSourceComponent{index + 1}",
            )
            component.LinkedObject = source_shape
            component.LinkPlacement.Base.x = index * 20
            source_components.append(component)
        self.doc.recompute()
        self.doc.UndoMode = True

        self.doc.openTransaction(
            "Insert flexible automatic occurrence"
        )
        try:
            occurrence = self.assembly.newObject(
                "Assembly::AssemblyLink",
                "FlexibleAutomaticOccurrence",
            )
            occurrence.LinkedObject = source_assembly
            occurrence.Rigid = False
            UtilsAssembly.finalizeInsertedComponentTimeline(
                occurrence
            )
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise

        occurrence_name = occurrence.Name
        initial_resource_names = tuple(
            resource.Name
            for resource
            in UtilsAssembly._assemblyOccurrenceResources(
                occurrence
            )
        )
        self.doc.openTransaction(
            "Create automatically synchronized source joint"
        )
        try:
            source_joint = source_joint_group.newObject(
                "App::FeaturePython",
                "AutomaticallySynchronizedJoint",
            )
            JointObject.Joint(source_joint, 1)
            source_joint.Proxy.setJointConnectors(
                source_joint,
                [
                    [
                        source_components[0],
                        ["Face1", "Vertex1"],
                    ],
                    [
                        source_components[1],
                        ["Face1", "Vertex1"],
                    ],
                ],
            )
            self.doc.publishProvisionalTimelineOperationBlock(
                source_joint,
                [],
            )
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise

        local_joint = next(
            resource
            for resource
            in UtilsAssembly._assemblyOccurrenceResources(
                occurrence
            )
            if getattr(
                resource,
                "VibeCADAssemblySourceObjectId",
                -1,
            )
            == int(source_joint.ID)
        )
        local_components = {
            int(
                resource.VibeCADAssemblySourceObjectId
            ): resource
            for resource
            in UtilsAssembly._assemblyOccurrenceResources(
                occurrence
            )
            if resource.TypeId == "App::Link"
        }
        self.assertIs(
            local_joint.Reference1[0],
            local_components[int(source_components[0].ID)],
        )
        self.assertIs(
            local_joint.Reference2[0],
            local_components[int(source_components[1].ID)],
        )
        self.assertEqual(
            list(local_joint.Reference1[1]),
            ["Face1", "Vertex1"],
        )
        self.assertEqual(
            list(local_joint.Reference2[1]),
            ["Face1", "Vertex1"],
        )

        operations = list(self._timeline().Operations)
        self.assertLess(
            operations.index(source_joint),
            operations.index(local_joint),
        )
        self.assertLess(
            operations.index(local_joint),
            operations.index(occurrence),
        )
        synchronized_resource_names = tuple(
            resource.Name
            for resource
            in UtilsAssembly._assemblyOccurrenceResources(
                occurrence
            )
        )
        source_joint_name = source_joint.Name
        local_joint_name = local_joint.Name

        self.doc.undo()
        occurrence = self.doc.getObject(occurrence_name)
        self.assertIsNone(self.doc.getObject(source_joint_name))
        self.assertIsNone(self.doc.getObject(local_joint_name))
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            initial_resource_names,
        )

        self.doc.redo()
        occurrence = self.doc.getObject(occurrence_name)
        source_joint = self.doc.getObject(source_joint_name)
        self.assertIsNotNone(source_joint)
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            synchronized_resource_names,
        )

        self.doc.openTransaction(
            "Delete automatically synchronized source joint"
        )
        try:
            self.doc.removeObject(source_joint_name)
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise

        occurrence = self.doc.getObject(occurrence_name)
        self.assertIsNone(self.doc.getObject(source_joint_name))
        self.assertIsNone(self.doc.getObject(local_joint_name))
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            initial_resource_names,
        )

        self.doc.undo()
        occurrence = self.doc.getObject(occurrence_name)
        restored_source_joint = self.doc.getObject(
            source_joint_name
        )
        restored_local_joint = self.doc.getObject(
            local_joint_name
        )
        self.assertIsNotNone(restored_source_joint)
        self.assertIsNotNone(restored_local_joint)
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            synchronized_resource_names,
        )

        self.doc.redo()
        occurrence = self.doc.getObject(occurrence_name)
        self.assertIsNone(self.doc.getObject(source_joint_name))
        self.assertIsNone(self.doc.getObject(local_joint_name))
        self.assertEqual(
            tuple(
                resource.Name
                for resource
                in UtilsAssembly._assemblyOccurrenceResources(
                    occurrence
                )
            ),
            initial_resource_names,
        )

    def test_find_placement(self):
        """Test find placement of joint."""
        operation = "Find placement"
        _msg("  Test '{}'".format(operation))

        joint = self.jointgroup.newObject("App::FeaturePython", "testJoint")
        JointObject.Joint(joint, 0)

        L = 2
        W = 3
        H = 7
        box = self.assembly.newObject("Part::Box", "Box")
        box.Length = L
        box.Width = W
        box.Height = H
        box.Placement = App.Placement(App.Vector(10, 20, 30), App.Rotation(15, 25, 35))

        # Step 0 : box with placement. No element selected
        ref = [self.assembly, [box.Name + ".", box.Name + "."]]
        plc = joint.Proxy.findPlacement(joint, ref)
        targetPlc = App.Placement(App.Vector(), App.Rotation())
        self.assertTrue(plc.isSame(targetPlc, 1e-6), "'{}' failed - Step 0".format(operation))

        # Step 1 : box with placement. Face + Vertex
        ref = [self.assembly, [box.Name + ".Face6", box.Name + ".Vertex7"]]
        plc = joint.Proxy.findPlacement(joint, ref)
        targetPlc = App.Placement(App.Vector(L, W, H), App.Rotation())
        self.assertTrue(plc.isSame(targetPlc, 1e-6), "'{}' failed - Step 1".format(operation))

        # Step 2 : box with placement. Edge + Vertex
        ref = [self.assembly, [box.Name + ".Edge8", box.Name + ".Vertex8"]]
        plc = joint.Proxy.findPlacement(joint, ref)
        targetPlc = App.Placement(App.Vector(L, W, 0), App.Rotation(0, -90, 270))
        self.assertTrue(plc.isSame(targetPlc, 1e-6), "'{}' failed - Step 2".format(operation))

        # Step 3 : box with placement. Vertex
        ref = [self.assembly, [box.Name + ".Vertex3", box.Name + ".Vertex3"]]
        plc = joint.Proxy.findPlacement(joint, ref)
        targetPlc = App.Placement(App.Vector(0, W, H), App.Rotation())
        _msg("  plc '{}'".format(plc))
        _msg("  targetPlc '{}'".format(targetPlc))
        self.assertTrue(plc.isSame(targetPlc, 1e-6), "'{}' failed - Step 3".format(operation))

        # Step 4 : box with placement. Face
        ref = [self.assembly, [box.Name + ".Face2", box.Name + ".Face2"]]
        plc = joint.Proxy.findPlacement(joint, ref)
        targetPlc = App.Placement(App.Vector(L, W / 2, H / 2), App.Rotation(0, -90, 180))
        _msg("  plc '{}'".format(plc))
        _msg("  targetPlc '{}'".format(targetPlc))
        self.assertTrue(plc.isSame(targetPlc, 1e-6), "'{}' failed - Step 4".format(operation))

    def test_solve_assembly(self):
        """Test solving an assembly."""
        operation = "Solve assembly"
        _msg("  Test '{}'".format(operation))

        box = self.assembly.newObject("Part::Box", "Box")
        box.Length = 10
        box.Width = 10
        box.Height = 10
        box.Placement = App.Placement(App.Vector(10, 20, 30), App.Rotation(15, 25, 35))

        box2 = self.assembly.newObject("Part::Box", "Box")
        box2.Length = 10
        box2.Width = 10
        box2.Height = 10
        box2.Placement = App.Placement(App.Vector(40, 50, 60), App.Rotation(45, 55, 65))

        ground = self.jointgroup.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, box2)

        joint = self.jointgroup.newObject("App::FeaturePython", "testJoint")
        JointObject.Joint(joint, 0)

        refs = [
            [box2, ["Face6", "Vertex7"]],
            [box, ["Face6", "Vertex7"]],
        ]

        joint.Proxy.setJointConnectors(joint, refs)

        self.assertTrue(box.Placement.isSame(box2.Placement, 1e-6), "'{}'".format(operation))
