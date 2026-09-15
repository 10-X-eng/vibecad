# SPDX-License-Identifier: LGPL-2.1-or-later
"""Optional recompute provenance survives workers without tagging later UI edits."""

import threading
import unittest
from unittest.mock import patch

import FreeCAD as App

from SMTests import testPresentation


class TestSheetRecomputeOrigin(unittest.TestCase):
    def setUp(self):
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.sheet = self.model.sheet

    def observe(self, changed=None):
        events = []
        class Observer:
            def slotChangedObjectWithOrigin(self, obj, name, origin):
                if obj.Document is not None:
                    events.append((obj.Document.Name, obj.Name, name,
                                   origin, threading.get_ident()))
                    if changed is not None:
                        changed(obj, name)
        observer = Observer()
        App.addDocumentObserver(observer)
        self.addCleanup(lambda: App.removeDocumentObserver(observer))
        return events

    def wait(self):
        doc = self.model.doc
        self.fixture.wait_for(lambda: not (doc.RecomputePending or doc.Recomputing or doc.CooperativeMutationActive))
        self.model.settle()

    def test_origin_follows_nested_geometry_workers_and_gui_signals_only(self):
        import SheetMetalEditable as Editable
        doc = self.model.doc
        self.assertEqual(doc.getCurrentRecomputeOrigin(), "")
        nested, execution = [], []
        def changed(obj, name):
            if obj is self.sheet and name == "Shape" and not nested:
                nested.append(True)
                obj.Label = "Independent edit inside an observer"
        events = self.observe(changed)
        # FeaturePython caches execute when its proxy is attached. Observe the
        # real geometry call inside execute instead of replacing that binding.
        original = Editable.SheetGeometry.prepare
        def prepare(*args, **kwargs):
            execution.append((doc.getCurrentRecomputeOrigin(), threading.get_ident()))
            return original(*args, **kwargs)
        self.sheet.Material = "Tracked material"
        with patch.object(Editable.SheetGeometry, "prepare", side_effect=prepare):
            request = doc.recomputeAsyncTracked()
            self.assertEqual(request["request_count"], 1)
            self.assertTrue(request["origin"])
            self.assertEqual(doc.getCurrentRecomputeOrigin(), "")
            self.sheet.Label = "Independent GUI edit"
            self.wait()
        self.assertTrue(execution)
        self.assertTrue(all(origin == request["origin"] and thread != threading.get_ident()
                            for origin, thread in execution))
        shapes = [event for event in events if event[1] == self.sheet.Name
                  and event[2] in ("Shape", "FlatShape", "PreparedInputHash")]
        self.assertTrue(shapes, events)
        self.assertTrue(all(event[3] == request["origin"] for event in shapes), shapes)
        labels = [event for event in events if event[1] == self.sheet.Name and event[2] == "Label"]
        self.assertTrue(labels)
        self.assertTrue(all(event[3] == "" for event in labels), labels)
        self.assertTrue(all(event[4] == threading.get_ident() for event in events))
        self.assertTrue(nested)
        self.assertEqual(doc.getCurrentRecomputeOrigin(), "")

    def test_plain_recompute_and_rejected_requests_do_not_reuse_an_origin(self):
        doc = self.model.doc
        first = doc.recomputeAsyncTracked()
        self.wait()
        with self.assertRaises(TypeError):
            doc.recomputeAsyncTracked("not objects")
        self.assertEqual(doc.getCurrentRecomputeOrigin(), "")
        second = doc.recomputeAsyncTracked()
        self.wait()
        self.assertNotEqual(first["origin"], second["origin"])
        events = self.observe()
        self.sheet.Material = "Ordinary material"
        self.assertEqual(doc.recomputeAsync(), 1)
        self.wait()
        shapes = [event for event in events if event[2] in ("Shape", "FlatShape")]
        self.assertTrue(shapes)
        self.assertTrue(all(event[3] == "" for event in events), events)

    def test_document_origins_remain_isolated(self):
        doc = self.model.doc
        other = App.newDocument("OtherRecomputeOrigin")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        self.assertEqual(other.getCurrentRecomputeOrigin(), "")
        observed = []
        class Observer:
            def slotChangedObjectWithOrigin(self, obj, name, origin):
                if obj.Document is doc and name == "Shape":
                    observed.append(other.getCurrentRecomputeOrigin())
        observer = Observer()
        App.addDocumentObserver(observer)
        self.addCleanup(lambda: App.removeDocumentObserver(observer))
        self.sheet.Material = "Isolated material"
        request = doc.recomputeAsyncTracked()
        self.wait()
        self.assertTrue(request["origin"])
        self.assertTrue(observed)
        self.assertEqual(set(observed), {""})
