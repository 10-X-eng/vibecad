# SPDX-License-Identifier: LGPL-2.1-or-later

import pytest

from VibeCADNumericalRuntime import numerical_thread_count


@pytest.mark.parametrize("slots,available,limit,used,expected", [
    (1, 128 << 30, 0, 0, 1),
    (3, 8 << 30, 0, 0, 3),
    (6, 8 << 30, 0, 0, 6),
    (42, 128 << 30, 0, 0, 42),
    (42, 1 << 30, 0, 0, 4),
    (42, 128 << 30, 2 << 30, 1 << 30, 4),
    (3, 0, 0, 0, 1),
    (42, 128 << 30, 1 << 30, 2 << 30, 1),
])
def test_numerical_threads_follow_cpu_lease_and_memory_headroom(
    slots, available, limit, used, expected
):
    assert numerical_thread_count(slots, available, limit, used) == expected
