# SPDX-License-Identifier: LGPL-2.1-or-later
"""Revalidate retained worker results without rebuilding or modifying a CAD document."""


def main():
    import argparse
    import hashlib
    import json
    import os
    from pathlib import Path
    import sys
    import threading
    import time

    dll_handles = [os.add_dll_directory(str(Path(sys.executable).parent))] if os.name == 'nt' else []
    import FreeCAD as App

    parser = argparse.ArgumentParser()
    parser.add_argument('--module-dir', type=Path, required=True)
    parser.add_argument('--attempt', type=Path, required=True)
    parser.add_argument('--adapter', action='store_true',
                        help='Use the production adapter and persistent isolation pool.')
    parser.add_argument('--cancel-after', type=float,
                        help='Cancel adapter validation after this many seconds, then retry.')
    args = parser.parse_args()
    sys.path.insert(0, str(args.module_dir.resolve()))
    import VibeCADVibeScriptDomainRuntime as runtime
    from VibeCADVibeScriptDomains import get_vibescript_pack

    assert Path(runtime.__file__).parent == args.module_dir.resolve()
    started = time.perf_counter()
    request_bytes = (args.attempt / 'request.json').read_bytes()
    result_bytes = (args.attempt / 'result.json').read_bytes()
    request = json.loads(request_bytes)
    execution = json.loads(result_bytes)
    assert execution['ok'] and request['domain'] == 'assembly'
    prepared = dict(pack=get_vibescript_pack('AssemblyWorkbench'), staging=args.attempt,
                    expected_outputs=request['expected_outputs'], worker_request=request,
                    resolved_references=request.get('document_references', []))
    original_documents = dict(App.listDocuments())
    if args.cancel_after is not None:
        if not args.adapter or args.cancel_after <= 0:
            parser.error('--cancel-after requires --adapter and a positive duration')
        cancelled = threading.Event()
        timer = threading.Timer(args.cancel_after, cancelled.set)
        timer.start()
        try:
            runtime.AssemblyDomainAdapter(prepared['pack']).validate_result_with_cancellation(
                prepared, execution, cancellation_check=cancelled.is_set)
        except runtime.DomainRuntimeFailure as exc:
            assert exc.payload['cancelled']
            assert exc.payload['failure_code'] == 'RUN_CANCELLED'
            print(json.dumps(dict(cancelled=True, seconds=time.perf_counter() - started)), flush=True)
        else:
            raise AssertionError('Validation finished without exercising cancellation')
        finally:
            timer.cancel()
            timer.join()
        started = time.perf_counter()
    validate = (runtime.AssemblyDomainAdapter(prepared['pack']).validate_result
                if args.adapter else runtime.validate_candidate)
    validated = validate(prepared, execution)
    assert dict(App.listDocuments()) == original_documents
    for filename, before in (('request.json', request_bytes), ('result.json', result_bytes)):
        assert hashlib.sha256((args.attempt / filename).read_bytes()).digest() == hashlib.sha256(before).digest()
    summary = validated['assembly_validation'].get('simulation') or {}
    print(json.dumps(dict(ok=True, seconds=round(time.perf_counter() - started, 3),
        output_count=len(validated['outputs']),
        largest_definition_bytes=max(len(json.dumps(item['definition']).encode())
                                     for item in validated['outputs']),
        frame_count=summary.get('frame_count'), pose_count=summary.get('pose_count'),
        document_modified=False, request_and_result_unchanged=True)), flush=True)


if __name__ == '__main__':
    main()
