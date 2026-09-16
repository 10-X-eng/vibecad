import ctypes
import argparse
import json
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser(description='Isolated packaged OpenBLAS memory measurement')
parser.add_argument('--benchmark', action='store_true', help='Compare checked dense linear algebra kernels')
arguments = parser.parse_args()

class Counters(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
        (name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'peak_paged', 'paged',
                                          'peak_nonpaged', 'nonpaged', 'pagefile',
                                          'peak_pagefile', 'private')]
k = ctypes.WinDLL('kernel32')
k.GetCurrentProcess.restype = ctypes.c_void_p
p = ctypes.WinDLL('psapi')
p.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
def sample(phase):
    c= Counters()
    c.cb=ctypes.sizeof(c)
    if not p.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise ctypes.WinError()
    print(json.dumps({'phase': phase, 'private': c.private, 'working_set': c.ws}), flush=True)
sample('before_dll')
directory = Path(sys.executable).parent
with os.add_dll_directory(str(directory)):
    dll = ctypes.CDLL(os.environ.get('OPENBLAS_PROBE_DLL', str(directory / 'openblas.dll')))
sample('after_dll')
dll.openblas_get_config.restype = ctypes.c_char_p
print(json.dumps({'config': dll.openblas_get_config().decode(),
                  'threads': dll.openblas_get_num_threads()}), flush=True)
import numpy as np
sample('after_numpy')
x=np.ones((1000,1000))
y=x@x
assert (y==1000).all()
sample('after_matmul')

if arguments.benchmark:
    from statistics import median

    def measure(name, operation, verify):
        verify(operation())  # Warm up kernels; exclude validation from timings.
        timings = []
        for _ in range(3):
            cpu = time.process_time()
            wall = time.perf_counter()
            result = operation()
            elapsed = time.perf_counter() - wall
            cpu_elapsed = time.process_time() - cpu
            verify(result)
            timings.append({'wall_seconds': elapsed, 'cpu_seconds': cpu_elapsed,
                            'average_cpu_cores': cpu_elapsed / elapsed})
            del result
        print(json.dumps({'phase': 'benchmark', 'kernel': name,
                          'threads': dll.openblas_get_num_threads(),
                          'median_seconds': median(t['wall_seconds'] for t in timings),
                          'runs': timings, 'correct': True}), flush=True)
        sample('after_' + name)

    rng = np.random.default_rng(187)
    a = rng.standard_normal((2048, 2048))
    b = rng.standard_normal((2048, 2048))
    vector = rng.standard_normal(2048)
    expected = a @ (b @ vector)
    measure('matmul_2048', lambda: a @ b,
            lambda result: np.testing.assert_allclose(result @ vector, expected, rtol=1e-9, atol=1e-8))
    del a, b, vector, expected
    a = rng.standard_normal((1024, 1024))
    a = a.T @ a + 1024 * np.eye(1024)
    expected = rng.standard_normal((1024, 8))
    rhs = a @ expected
    measure('solve_1024_8rhs', lambda: np.linalg.solve(a, rhs),
            lambda result: np.testing.assert_allclose(result, expected, rtol=1e-9, atol=1e-10))
    del a, expected, rhs
    a = rng.standard_normal((512, 512))

    def verify_svd(result):
        u, s, vh = result
        np.testing.assert_allclose((u * s) @ vh, a, rtol=1e-9, atol=1e-10)

    measure('svd_512', lambda: np.linalg.svd(a, full_matrices=False), verify_svd)
