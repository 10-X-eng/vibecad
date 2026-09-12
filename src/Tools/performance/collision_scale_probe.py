# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-kernel collision scale check; run with packaged Python, never a live GUI."""


def main():
    import argparse
    import json
    import os
    from pathlib import Path
    import sys
    import time

    handles = [os.add_dll_directory(str(Path(sys.executable).parent))]
    import FreeCAD as App
    import Part
    import psutil
    parser = argparse.ArgumentParser()
    parser.add_argument('--module-dir', type=Path)
    args = parser.parse_args()
    if args.module_dir:
        sys.path.insert(0, str(args.module_dir.resolve()))
    import VibeCADMechanismGeometry as geometry
    if args.module_dir:
        assert Path(geometry.__file__).parent == args.module_dir.resolve(), geometry.__file__
    from VibeCADMechanismGeometry import (
        DynamicCollisionEvaluator, evaluate_dynamic_collisions,
    )

    box = Part.makeBox(1, 1, 1)
    process = psutil.Process()
    for count, frame_count in ((337, 22), (10_000, 1)):
        start = time.perf_counter()
        names = [f'Component_{index}' for index in range(count)]
        components = dict.fromkeys(names, box)
        definitions = dict.fromkeys(names, 'shared-unit-box')
        placements = {
            name: dict(position_mm=[index * 2.0, 0, 0], rotation_xyzw=[0, 0, 0, 1])
            for index, name in enumerate(names)
        }
        # One real overlap, including the final component beyond the old cap.
        placements[names[-1]] = dict(position_mm=[0.5, 0, 0], rotation_xyzw=[0, 0, 0, 1])
        frames = [dict(frame_index=index,
                       nominal_time_s=None if index == 0 else (index - 1) / 10,
                       component_placements=placements)
                  for index in range(frame_count + 1)]
        result = evaluate_dynamic_collisions(components, frames,
            definition_keys=definitions, frame_workers=2 if count == 337 else 1)
        summary = result['summary']
        assert summary['component_count'] == count, summary
        assert summary['evaluated_frame_count'] == frame_count, summary
        assert summary['colliding_pair_count'] == 1, summary
        assert summary['colliding_frame_count'] == frame_count, summary
        assert summary['pairs'][0]['second_component'] == names[-1], summary
        assert summary['possible_pair_count'] == count * (count - 1) // 2
        print(json.dumps(dict(event='scale', components=count, frames=frame_count,
            seconds=time.perf_counter() - start, private_bytes=process.memory_info().private,
            pair_count=summary['possible_pair_count'], colliding_pairs=1)), flush=True)

    # Broad-phase rejection must not discard an authenticated rigid-pair result,
    # and an explicit exclusion must still take precedence over that reuse.
    evaluator = DynamicCollisionEvaluator({'First': box, 'Last': box},
        definition_keys={'First': 'box', 'Last': 'box'})
    pose = {name: dict(position_mm=[index * 10.0, 0, 0], rotation_xyzw=[0, 0, 0, 1])
            for index, name in enumerate(evaluator.component_names)}
    evidence = dict(first_component='First', second_component='Last',
        interference_volume_mm3=1.0, common_solid_count=1)
    reused = evaluator.evaluate_with_known_pairs(pose,
        known_pair_results={('First', 'Last'): evidence})
    assert reused['collisions'] == [evidence], reused
    excluded = evaluator.evaluate_with_known_pairs(pose,
        known_pair_results={('First', 'Last'): evidence}, excluded_pairs={('First', 'Last')})
    assert excluded['collisions'] == [], excluded
    fork = evaluator.fork()
    assert fork.pair_count == evaluator.pair_count
    assert fork.evaluate(pose)['collisions'] == []
    assert not App.listDocuments()
    print(json.dumps(dict(event='complete', ok=True)), flush=True)


if __name__ == '__main__':
    main()
