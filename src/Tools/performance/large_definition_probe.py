# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exercise large output serialization using real packaged CAD kernels."""


def main():
    import argparse
    import json
    import os
    from pathlib import Path
    import sys
    import tempfile

    handles = [os.add_dll_directory(str(Path(sys.executable).parent))]
    import FreeCAD as App
    import Mesh
    parser = argparse.ArgumentParser()
    parser.add_argument('--module-dir', type=Path)
    args = parser.parse_args()
    if args.module_dir:
        sys.path.insert(0, str(args.module_dir.resolve()))
    import VibeCADVibeScriptDomains as domains
    import vibescript_domain_worker as worker
    import VibeCADVibeScriptDomainRuntime as runtime
    if args.module_dir:
        assert Path(runtime.__file__).parent == args.module_dir.resolve()

    pack = domains.get_vibescript_pack("MeshWorkbench")
    source = (
        "triangles = []\n"
        "for i in range(15000):\n"
        "    x = i + 0.123456789\n"
        "    triangles.append([[x,0,0], [x,1,0], [x,0,1]])\n"
        "result = {'Mesh': api.mesh(triangles)}\n"
    )
    request = {
        "schema": worker.SCHEMA, "domain": pack.domain,
        "source": source, "inputs": {},
        "expected_outputs": [{"name": "Mesh", "type": "mesh"}],
        "api_exports": list(pack.api_exports), "output_types": list(pack.output_types),
        "max_operations": 0, "max_seconds": 30.0,
    }
    with tempfile.TemporaryDirectory(prefix="vibecad-large-definition-") as directory:
        response = worker._run(request, Path(directory))
        assert response["ok"], response
        output = response["outputs"][0]
        definition_bytes = len(json.dumps(output["definition"]).encode("utf-8"))
        assert definition_bytes > 1_000_000, definition_bytes
        assert output["artifact_kind"] == "mesh_bms", output
        mesh = Mesh.Mesh()
        mesh.read(str(Path(directory) / output["artifact_path"]))
        assert mesh.CountFacets == 15_000 and not mesh.hasCorruptedFacets()
        validated = runtime.validate_candidate(dict(pack=pack, staging=directory,
            expected_outputs=request['expected_outputs'], worker_request=request), response)
        assert validated['outputs'][0]['name'] == 'Mesh'
        print(json.dumps({"ok": True, "definition_bytes": definition_bytes,
                          "facets": mesh.CountFacets, "host_validated": True,
                          "budget": response["budget"]}), flush=True)
    assert not App.listDocuments()


if __name__ == "__main__":
    main()
