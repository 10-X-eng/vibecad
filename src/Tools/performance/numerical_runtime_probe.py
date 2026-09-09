# SPDX-License-Identifier: LGPL-2.1-or-later

"""Run with packaged Windows Python in a disposable process, never the live GUI."""


def main():
    import json
    import os
    from pathlib import Path
    import sys
    import time

    handles = [os.add_dll_directory(str(Path(sys.executable).parent))]
    import FreeCAD as App
    import numpy as np
    import psutil
    from threadpoolctl import threadpool_info
    from VibeCADNumericalRuntime import _controller, numerical_thread_limits

    _controller()
    pools = [p for p in threadpool_info() if p["internal_api"] == "openblas"]
    assert pools and all(p["threading_layer"] == "pthreads" for p in pools), pools
    assert all(p["num_threads"] == 1 for p in pools), pools
    process = psutil.Process()
    print(json.dumps({"event": "startup", "private_bytes": process.memory_info().private,
                      "pools": pools, "runtime": App.hostRuntimeStatus()}), flush=True)
    matrix = np.random.default_rng(512).normal(size=(1024, 1024))
    vector = np.random.default_rng(513).normal(size=1024)
    expected = matrix @ (matrix @ vector)
    for slots in (1, 3, 8, 42, 3):
        with numerical_thread_limits(slots, 2 << 30) as threads:
            pools = [p for p in threadpool_info() if p["internal_api"] == "openblas"]
            assert all(p["num_threads"] == threads for p in pools), pools
            start = time.perf_counter()
            result = matrix @ matrix
            elapsed = time.perf_counter() - start
            np.testing.assert_allclose(result @ vector, expected, rtol=1e-9, atol=1e-8)
            print(json.dumps({"event": "job", "slots": slots, "threads": threads,
                              "seconds": elapsed, "private_bytes": process.memory_info().private,
                              "correct": True}), flush=True)
        assert all(p["num_threads"] == 1 for p in threadpool_info()
                   if p["internal_api"] == "openblas")
    try:
        with numerical_thread_limits(3, 2 << 30):
            raise ValueError("probe cancellation/exception cleanup")
    except ValueError:
        pass
    assert all(p["num_threads"] == 1 for p in threadpool_info()
               if p["internal_api"] == "openblas")
    print(json.dumps({"event": "complete", "ok": True}), flush=True)


if __name__ == "__main__":
    main()
