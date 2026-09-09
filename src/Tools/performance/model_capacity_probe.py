# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real packaged-kernel capacity checks, isolated from any user's GUI/document."""


def main():
    import argparse
    import json
    import os
    from pathlib import Path
    import sys
    import tempfile
    import time

    dll_handles = [os.add_dll_directory(str(Path(sys.executable).parent))] if os.name == 'nt' else []
    import FreeCAD as App
    import Part

    parser = argparse.ArgumentParser()
    parser.add_argument('--module-dir', type=Path, required=True)
    parser.add_argument('--components', type=int, default=2,
                        help='Native simulation size; use 3000 for the expensive capacity stress run')
    args = parser.parse_args()
    if args.components < 2:
        parser.error('--components must be at least 2 for the revolute joint')
    sys.path.insert(0, str(args.module_dir.resolve()))
    import VibeCADVibeScriptDomainRuntime as runtime
    from VibeCADAssemblyHierarchy import capture_assembly_hierarchy, hierarchy_context
    from VibeCADVibeScriptDomains import get_vibescript_pack
    from vibescript_assembly_worker import _load_assembly_hierarchy
    import vibescript_domain_worker as worker
    from vibecad_tests.assembly_vibescript_api_integration import (
        _Service, _document_objects, _reference_schema, resolve_modeling_surface,
    )

    assert Path(runtime.__file__).parent == args.module_dir.resolve()
    started = time.perf_counter()

    def report(event, **values):
        print(json.dumps(dict(event=event, seconds=round(time.perf_counter() - started, 3),
                              **values)), flush=True)

    with tempfile.TemporaryDirectory(prefix='vibecad-model-capacity-') as directory:
        root = Path(directory)
        document = App.newDocument('ModelCapacity')
        try:
            group = document.addObject('App::Part', 'SourceHierarchy')
            shapes = []
            box = Part.makeBox(1, 1, 1)
            for index in range(337):
                obj = document.addObject('Part::Feature', f'Source{index}')
                obj.Shape = box
                group.addObject(obj)
                shapes.append(obj)
            for index in range(2100):
                link = document.addObject('App::Link', f'Occurrence{index}')
                link.setLink(shapes[index % len(shapes)])
                group.addObject(link)
            document.recompute()
            captured = capture_assembly_hierarchy(group, detach_shapes=True)
            descriptor, artifact_bytes = runtime._finalize_assembly_hierarchy(
                captured, staging=root, reference_index=0)
            loaded = _load_assembly_hierarchy(root, descriptor, context='native capacity')
            # App::Part may also supply its aggregate Shape; every leaf must survive.
            leaf_ids = {node['node_id'] for node in descriptor['nodes'] if node['kind'] == 'shape'}
            assert len(leaf_ids) == 337
            assert leaf_ids <= set(loaded['shapes'])
            assert descriptor['counts']['occurrences'] == 2437
            preview = hierarchy_context(descriptor)
            assert len(preview['occurrence_paths']) == 256
            assert preview['occurrence_paths_omitted'] == 2181
            report('hierarchy_passed', **descriptor['counts'], artifact_bytes=artifact_bytes)

            pack = get_vibescript_pack('AssemblyWorkbench')
            source = (
                f"members = {{'Part'+str(i): api.component(inputs['source'], "
                f"grounded=(i != 1), placement=[i*3,0,0], label='Part '+str(i)+'x'*100) "
                f"for i in range({args.components})}}\n"
                "hinge = api.joint('revolute', api.connector(members['Part0']), "
                "api.connector(members['Part1']))\n"
                "model = api.assembly(members, {'Hinge':hinge})\n"
                "drive = api.motion(hinge, 'initialValue + time')\n"
                "simulation = api.simulation(model, {'Drive':drive}, end_time_s=0.1, "
                "time_step_s=0.05, collision_mode='off')\n"
                "result = {'Model':model, 'Simulation':simulation, 'Diagnostics':api.solve(model)}\n"
            )
            expected = [dict(name='Model', type='assembly'), dict(name='Simulation', type='simulation'),
                        dict(name='Diagnostics', type='solver_diagnostics')]
            service = _Service(document, root)
            capture = dict(pack=pack, project_root=str(root), document_name=document.Name,
                document_uid=str(document.Uid), document_revision='assembly-production-revision',
                document_objects=_document_objects(document),
                surface=resolve_modeling_surface('AssemblyWorkbench', 'vibescript').summary(),
                freecad_home=App.getHomePath(), timeout_seconds=0.0,
                memory_limit_bytes=2 * 1024 * 1024 * 1024,
                operation='create_program', tool_name='vibescript.assembly.create_program',
                arguments=dict(program_name='Capacity Simulation', source=source,
                    input_schema=dict(type='object', properties={'source': _reference_schema()},
                                      required=['source'], additionalProperties=False),
                    inputs={'source': dict(document_uid=str(document.Uid), object_name=shapes[0].Name)},
                    expected_outputs=expected))
            prepared = runtime.prepare_candidate(capture)
            prepared = runtime.finalize_candidate(prepared, runtime.capture_reference_inputs(service, prepared))
            report('worker_start', components=args.components)
            # This executable is the isolated test process. Use the actual worker
            # entry point, then the actual host postcondition and publication code.
            execution = worker._run(prepared['worker_request'], Path(prepared['staging']))
            assert execution['ok'], execution
            simulation = next(item for item in execution['outputs'] if item['name'] == 'Simulation')
            definition_bytes = len(json.dumps(simulation['definition']).encode())
            report('worker_complete', definition_bytes=definition_bytes)
            if args.components >= 3000:
                assert definition_bytes > 1_000_000
            validated = runtime.validate_candidate(prepared, execution)
            report('host_validated')
            runtime.retain_candidate(prepared, status='validated')
            publication = runtime.publish_candidate(service, prepared, validated)
            accepted = runtime.accept_candidate(prepared, publication)
            assert accepted['model_state']['status'] == 'accepted'
            report('published', outputs=len(publication['outputs']), definition_bytes=definition_bytes)
        finally:
            if document.Name in App.listDocuments():
                App.closeDocument(document.Name)
    report('complete', ok=True)


if __name__ == '__main__':
    main()
