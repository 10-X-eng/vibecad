# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise production entry points beyond historical model-size ceilings."""
from types import SimpleNamespace

import pytest

import VibeCADVibeScriptDomainRuntime as runtime


def test_large_simulation_reaches_domain_postconditions(tmp_path, monkeypatch):
    definition = {'domain': 'assembly', 'output_type': 'simulation',
                  'frames': [{'values': [0.123456789] * 10_000} for _ in range(22)]}
    prepared = dict(pack=SimpleNamespace(domain='assembly'), staging=tmp_path,
                    expected_outputs=[dict(name='simulation', type='simulation')])
    execution = dict(ok=True, schema=runtime.WORKER_SCHEMA, domain='assembly',
                     outputs=[dict(name='simulation', type='simulation', definition=definition)])
    calls = []

    def validate(prepared, execution, outputs):
        calls.append(outputs)
        return {'validated': True}

    monkeypatch.setattr(runtime, '_validate_assembly_execution', validate)
    result = runtime.validate_candidate(prepared, execution)
    assert calls and result['assembly_validation'] == {'validated': True}
    assert result['outputs'][0]['definition'] == definition
    definition['frames'][0]['values'][0] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        runtime.validate_candidate(prepared, execution)


def test_definition_serialization_validation_is_streamed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Validation must not allocate a second complete JSON payload')
    monkeypatch.setattr(runtime.json, 'dumps', forbidden)
    runtime._validate_json_serializable({'values': list(range(100_001))})
    with pytest.raises(ValueError):
        runtime._validate_json_serializable({'value': float('nan')})


def test_definition_paths_only_parse_possible_parent_segments(monkeypatch):
    original_path = runtime.Path
    parsed = []

    def track_path(value):
        parsed.append(value)
        return original_path(value)

    monkeypatch.setattr(runtime, 'Path', track_path)
    prepared = dict(pack=SimpleNamespace(domain='assembly'))
    values = ['body_' + str(index) for index in range(5000)]
    values += ['relative/name', 'relative\\name', 'segment..name', './relative']
    runtime._validate_definition_value(values, prepared, 'definition')
    assert parsed == ['segment..name']


@pytest.mark.parametrize('value', [
    '..', '../part', 'part/..', 'part/../other', 'part\\..\\other',
    '/absolute', '\\absolute', 'C:/absolute', 'C:\\absolute',
])
def test_definition_paths_still_reject_absolute_and_parent_segments(value):
    prepared = dict(pack=SimpleNamespace(domain='assembly'))
    with pytest.raises(ValueError, match='filesystem path'):
        runtime._validate_definition_value(value, prepared, 'definition')


def test_placement_delta_preserves_legacy_roundoff_but_checks_new_metric(monkeypatch):
    pose = SimpleNamespace(Base=[0.0, 0.0, 0.0], Rotation=SimpleNamespace(Q=[0, 0, 0, 1]))
    monkeypatch.setattr(runtime, '_assembly_native_placement_from_matrix', lambda *args: pose)
    fact = {'matrix': []}
    legacy = dict(translation_mm=[0, 0, 0], translation_distance_mm=0,
                  rotation_degrees=1.7075472925031877e-6)
    assert runtime._assembly_placement_delta(fact, fact, legacy, 'test') == legacy
    current = dict(legacy, rotation_metric='quaternion_chord_v1')
    with pytest.raises(ValueError, match='rotation_degrees'):
        runtime._assembly_placement_delta(fact, fact, current, 'test')
    current['rotation_degrees'] = 0.0
    assert runtime._assembly_placement_delta(fact, fact, current, 'test') == current
    legacy['rotation_degrees'] = 0.01
    with pytest.raises(ValueError, match='rotation_degrees'):
        runtime._assembly_placement_delta(fact, fact, legacy, 'test')


@pytest.mark.parametrize('node_count,element_count', [(100_001, 1), (2, 500_001)])
def test_fem_native_readback_accepts_large_mesh(node_count, element_count):
    from vibescript_fem_worker import _mesh_topology
    point = SimpleNamespace(x=0.0, y=0.0, z=0.0)
    mesh = SimpleNamespace(Nodes=dict.fromkeys(range(1, node_count + 1), point),
        Edges=range(1, element_count + 1), Faces=[], Volumes=[],
        getElementNodes=lambda _id: [1, 2])
    topology = _mesh_topology(mesh)
    assert topology['facts']['node_count'] == node_count
    assert topology['facts']['element_count'] == element_count


def test_sketch_api_accepts_large_geometry_and_constraint_lists():
    from vibescript_sketcher_api import SketcherDomainAPI
    api = SketcherDomainAPI(SketcherDomainAPI.exported_names, ['sketch'])
    points = [api.point([float(i), 0.0]) for i in range(4097)]
    value = api.sketch(points)
    assert value is not None
    constraints = [api.constraint('block', [points[0]], active=False) for _ in range(16_385)]
    assert api.sketch(points, constraints) is not None


def test_drawing_sources_accepts_large_assemblies():
    from vibescript_techdraw_api import _references
    sources = [dict(document_uid='document', object_name=f'Body{i}') for i in range(337)]
    assert len(_references('view', sources)) == len(sources)
    with pytest.raises(ValueError, match='duplicate'):
        _references('view', sources + [sources[0]])


def test_host_reference_capture_accepts_ten_thousand_objects():
    sources = [dict(document_uid='doc', object_name=f'Body{i}') for i in range(10_000)]
    assert runtime._input_references({'parts': sources + sources}) == sources


def test_assembly_api_accepts_ten_thousand_named_components():
    from vibescript_assembly_api import AssemblyDomainAPI, _PUBLISHABLE_TYPES
    api = AssemblyDomainAPI(AssemblyDomainAPI.exported_names, _PUBLISHABLE_TYPES)
    components = {f'Part{i}': api.component(dict(document_uid='doc', object_name='Body'),
                  grounded=(i == 0)) for i in range(10_000)}
    assert len(api.assembly(components).properties['components']) == 10_000


def _large_bom():
    from VibeCADAssemblyBOM import plan_assembly_bom
    sources = [dict(output_name=f'Part{i}', reference=dict(document_uid='doc',
               object_name=f'Body{i}', label=f'Part {i}')) for i in range(10_000)]
    return plan_assembly_bom(sources,
        columns=[dict(kind='builtin', key='name', heading='Name', native_name='Name')],
        detail_subassemblies=True, detail_parts=True, only_parts=False, row_overrides=[])


def test_detailed_bom_accepts_ten_thousand_rows():
    result = _large_bom()
    assert result['row_count'] == 10_000
    assert result['occurrence_path_count'] == 10_000


def test_large_accepted_bom_can_be_restored(monkeypatch):
    import json
    import VibeCADVibeScriptDomainPublication as publication
    data = _large_bom()
    encoded = json.dumps(data)
    assert len(encoded) > 1_000_000
    target = SimpleNamespace(TypeId='Assembly::BomObject')
    setattr(target, publication.PROP_ASSEMBLY_BOM_VALIDATION, encoded)
    owner = SimpleNamespace()
    setattr(owner, publication.PROP_ASSEMBLY_BOM_RESTORE_TARGET, target)
    restored = []
    monkeypatch.setattr(publication, '_ensure_assembly_bom_restore_properties', lambda obj: None)
    monkeypatch.setattr(publication, '_unfreeze_object', lambda *args: None)
    monkeypatch.setattr(publication, '_freeze_object', lambda *args: None)
    monkeypatch.setattr(publication, '_populate_assembly_bom_without_recomputing',
                        lambda obj, value: restored.append(value))
    monkeypatch.setattr(publication, '_assembly_bom_live_readback', lambda *args: 'same')
    monkeypatch.setattr(publication, '_assembly_bom_expected_readback', lambda *args: 'same')
    publication.AssemblyBOMRestoreProxy().onDocumentRestored(owner)
    assert restored == [data]
    assert getattr(owner, publication.PROP_ASSEMBLY_BOM_RESTORE_ERROR) == ''


def test_drawing_page_accepts_more_than_128_contents():
    from vibescript_techdraw_api import TechDrawDomainAPI
    api = TechDrawDomainAPI()
    template = api.template()
    contents = [api.annotation([f'Note {i}']) for i in range(129)]
    contents.append(api.view([dict(document_uid='doc', object_name='Body')]))
    assert api.page(template, contents) is not None


def test_native_bom_reads_large_tables_but_honors_explicit_cell_budget():
    from VibeCADNativeAssemblyBomState import read_bom_table, NativeAssemblyBomStateError
    sheet = SimpleNamespace(getUsedRange=lambda: ('A1', 'A10001'),
                            getContents=lambda address: address)
    result = read_bom_table(sheet)
    assert result['row_count'] == 10_000
    with pytest.raises(NativeAssemblyBomStateError):
        read_bom_table(sheet, maximum_cells=100)


def test_threaded_fastener_creation_does_not_scan_document_count(monkeypatch):
    import VibeCADFasteners as fasteners
    monkeypatch.setattr(fasteners, 'resolve_fastener', lambda **kwargs: {'model_thread': True})

    class Document:
        @property
        def Objects(self):
            raise AssertionError('Counting document objects is unrelated to this fastener')

        def addObject(self, *args):
            raise RuntimeError('reached native creation')

    with pytest.raises(RuntimeError, match='reached native creation'):
        fasteners.create_fastener_feature(Document(), standard='ISO4762', nominal_thread='M6')


def test_large_hierarchy_capture_and_worker_validation(tmp_path, monkeypatch):
    import sys
    import FreeCAD
    import VibeCADAssemblyHierarchy as hierarchy
    from vibescript_assembly_worker import _load_assembly_hierarchy
    from .test_native_assembly_bom import _Document

    matrix = [float(i // 4 == i % 4) for i in range(16)]
    monkeypatch.setattr(FreeCAD, 'Placement',
                        lambda: SimpleNamespace(toMatrix=lambda: SimpleNamespace(A=matrix)),
                        raising=False)
    monkeypatch.setitem(sys.modules, 'Part', SimpleNamespace())
    document = _Document()
    root = document.add('Root', 'App::Part')
    root.Group = [document.add(f'Module{i}', 'App::Part') for i in range(10_000)]
    captured = hierarchy.capture_assembly_hierarchy(root, detach_shapes=False)
    assert captured['counts']['nodes'] == 10_001
    assert captured['counts']['occurrences'] == 10_000
    loaded = _load_assembly_hierarchy(tmp_path, captured, context='test')
    assert len(loaded['nodes']) == 10_001
    preview = hierarchy.hierarchy_context(captured)
    assert len(preview['occurrence_paths']) <= 256
    assert preview['occurrence_paths_omitted'] == 10_000 - len(preview['occurrence_paths'])
    captured['limits'] = dict(maximum_depth=16, nodes=512, occurrences=2048,
                              joints=1024, shape_artifacts=256)
    assert len(_load_assembly_hierarchy(tmp_path, captured, context='old')['nodes']) == 10_001


def test_hierarchy_metadata_serialization_does_not_add_a_byte_ceiling(tmp_path, monkeypatch):
    import sys
    import VibeCADAssemblyHierarchy as hierarchy
    monkeypatch.setitem(sys.modules, 'Part', SimpleNamespace())
    value = dict(schema=hierarchy.ASSEMBLY_HIERARCHY_SCHEMA,
                 nodes=[dict(node_id='n0000', label='x' * (8 * 1024 * 1024 + 1))],
                 _detached_shapes={})
    descriptor, size = runtime._finalize_assembly_hierarchy(value, staging=tmp_path,
                                                          reference_index=0)
    assert descriptor['nodes'] == value['nodes'] and size == 0


def test_native_bom_source_graph_accepts_large_assembly(monkeypatch):
    from .test_native_assembly_bom import _Document
    from VibeCADNativeAssemblyBomState import _source_graph
    monkeypatch.setattr('VibeCADNativeAssemblyBomState._timeline_active', lambda obj: True)
    document = _Document()
    root = document.add('Root', 'App::Part')
    root.Group = [document.add(f'Body{i}', 'Part::Feature') for i in range(10_000)]
    root.OutList = root.Group
    assert len(_source_graph(root)) == 10_001


def test_partdesign_history_validation_accepts_more_than_16384_objects(tmp_path):
    import base64
    import hashlib
    import json

    content = b'<Object/>'
    records = [dict(name=f'Feature{i}', label=f'Feature {i}', type_id='PartDesign::Feature',
                    content=base64.b64encode(content).decode(),
                    content_sha256=hashlib.sha256(content).hexdigest(), links={}, visible=False)
               for i in range(16_385)]
    payload = dict(schema=runtime._PARTDESIGN_NATIVE_HISTORY_SCHEMA, outputs=[dict(
        output_name='Result', body_name='Body', body_label='Body', representation='body',
        tip_name=records[-1]['name'], objects=records)])
    encoded = json.dumps(payload).encode()
    (tmp_path / 'history.json').write_bytes(encoded)
    metadata = dict(schema=payload['schema'], artifact_path='history.json',
                    artifact_bytes=len(encoded), artifact_sha256=hashlib.sha256(encoded).hexdigest(),
                    outputs=['Result'])
    prepared = dict(staging=tmp_path, expected_outputs=[dict(name='Result', type='body')])
    validated = runtime._validate_partdesign_native_history(prepared, dict(partdesign_native_history=metadata))
    assert len(validated['outputs'][0]['objects']) == 16_385
    metadata['artifact_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='digest'):
        runtime._validate_partdesign_native_history(prepared, dict(partdesign_native_history=metadata))


def test_large_drawing_projection_still_requires_aligned_metadata(tmp_path, monkeypatch):
    import vibescript_techdraw_worker as worker

    edge_count, face_count, vertex_count = 200_001, 50_001, 250_001
    vector = [0.0, 0.0, 1.0]
    snapshot = dict(edges=SimpleNamespace(Edges=range(edge_count)),
                    faces=SimpleNamespace(Faces=range(face_count)),
                    edge_classes=[0] * edge_count, edge_visibility=[True] * edge_count,
                    source_indices=[0] * edge_count, centroid=vector)
    descriptors = dict(coordinate_space='view', view_scale=1.0,
                       edges=[{}] * edge_count, vertices=[{}] * vertex_count)
    view = SimpleNamespace(getPrecomputedProjection=lambda: snapshot,
                           getProjectedElementDescriptors=lambda: descriptors,
                           TypeId='TechDraw::DrawViewPart', Direction=vector, XDirection=vector,
                           X=0.0, Y=0.0, Scale=1.0)
    monkeypatch.setattr(worker, '_write_shape_artifact', lambda *args, **kwargs: {})
    monkeypatch.setattr(worker, '_shape_bounds_2d', lambda shape: [0, 0, 1, 1])
    result = worker._projection_snapshot(view, tmp_path, output_index=0, suffix='view', source_keys=[])
    assert (result['edge_count'], result['face_count'], result['vertex_count']) == (
        edge_count, face_count, vertex_count)
    snapshot['source_indices'].pop()
    with pytest.raises(RuntimeError, match='edge-aligned'):
        worker._projection_snapshot(view, tmp_path, output_index=0, suffix='view', source_keys=[])
