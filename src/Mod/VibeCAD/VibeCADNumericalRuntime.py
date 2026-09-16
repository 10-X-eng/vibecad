# SPDX-License-Identifier: LGPL-2.1-or-later

"""Numerical scratch admission for single-job Windows isolation processes.

Never change process-global BLAS settings around concurrent in-process jobs.
The host process uses its outer compute pool; only an exclusive isolation job
may enlarge its numerical team, within the CPU slots granted by that host.
"""

from contextlib import contextmanager
from functools import cache
import os

# OpenBLAS 0.3.30 common_x86_64.h BUFFER_SIZE; pinned Windows package below.
# This is a scratch allocation estimate, not a limit on matrices or CAD models.
_SCRATCH_BYTES_PER_THREAD = 128 * 1024 * 1024


@cache
def _controller():
    from threadpoolctl import OpenBLASController, ThreadpoolController, register

    # conda-forge Windows calls the DLL openblas.dll, not libopenblas.dll.
    # Reuse the library controller, extending only its filename recognition.
    class BundledOpenBLASController(OpenBLASController):
        filename_prefixes = ("openblas",)

    register(BundledOpenBLASController)
    return ThreadpoolController()


def numerical_thread_count(cpu_slots, available_bytes, memory_limit_bytes, used_bytes):
    headroom = max(0, int(available_bytes))
    if memory_limit_bytes > 0:
        headroom = min(headroom, max(0, int(memory_limit_bytes) - int(used_bytes)))
    # Leave at least half the current headroom for matrix operands and geometry.
    memory_threads = headroom // (2 * _SCRATCH_BYTES_PER_THREAD)
    return max(1, min(max(1, int(cpu_slots)), memory_threads))


@contextmanager
def numerical_thread_limits(cpu_slots, memory_limit_bytes=0):
    import psutil

    # Discover the numerical backend before setting its team size. Importing
    # once per persistent worker avoids repeating initialization per operation.
    import numpy  # noqa: F401

    threads = numerical_thread_count(
        cpu_slots,
        psutil.virtual_memory().available,
        memory_limit_bytes,
        psutil.Process().memory_info().private,
    )
    previous = os.environ.get("OPENBLAS_NUM_THREADS")
    os.environ["OPENBLAS_NUM_THREADS"] = str(threads)
    try:
        with _controller().limit(limits=threads, user_api="blas"):
            yield threads
    finally:
        if previous is None:
            os.environ.pop("OPENBLAS_NUM_THREADS", None)
        else:
            os.environ["OPENBLAS_NUM_THREADS"] = previous
