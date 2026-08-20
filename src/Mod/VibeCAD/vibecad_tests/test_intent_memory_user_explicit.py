# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADIntentMemory import (
    apply_memory_update,
    empty_memory,
    require_user_explicit_preserved,
)


TURN = "a" * 32


def _memory_with_wall() -> dict:
    memory = empty_memory("project-a")
    memory["entries"] = [
        {
            "id": "wall",
            "category": "constraint",
            "statement": "2 mm wall",
            "authority": "user_explicit",
            "source_turn_ids": [TURN],
            "status": "active",
            "superseded_by": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]
    from VibeCADIntentMemory import memory_revision

    memory["revision"] = memory_revision(memory)
    return memory


def test_cannot_supersede_user_explicit_with_model_assumption() -> None:
    memory = _memory_with_wall()
    update = {
        "base_revision": memory["revision"],
        "turn_dispositions": [
            {"turn_id": TURN, "durable": True, "entry_ids": ["thinner"]}
        ],
        "upserts": [
            {
                "id": "thinner",
                "category": "constraint",
                "statement": "1 mm wall",
                "authority": "model_assumption",
                "source_turn_ids": [TURN],
            }
        ],
        "supersessions": [{"entry_id": "wall", "superseded_by": "thinner"}],
        "sources": {},
    }
    with pytest.raises(RuntimeError, match="user_explicit"):
        apply_memory_update(
            memory,
            update,
            expected_turns=[{"turn_id": TURN}],
            known_turn_ids={TURN},
        )


def test_cannot_downgrade_user_explicit_authority() -> None:
    memory = _memory_with_wall()
    update = {
        "base_revision": memory["revision"],
        "turn_dispositions": [
            {"turn_id": TURN, "durable": True, "entry_ids": ["wall"]}
        ],
        "upserts": [
            {
                "id": "wall",
                "category": "constraint",
                "statement": "2 mm wall",
                "authority": "model_assumption",
                "source_turn_ids": [TURN],
            }
        ],
        "supersessions": [],
        "sources": {},
    }
    with pytest.raises(RuntimeError, match="downgrade user_explicit"):
        apply_memory_update(
            memory,
            update,
            expected_turns=[{"turn_id": TURN}],
            known_turn_ids={TURN},
        )


def test_apply_memory_update_keeps_user_explicit_wall() -> None:
    memory = _memory_with_wall()
    update = {
        "base_revision": memory["revision"],
        "turn_dispositions": [
            {"turn_id": TURN, "durable": True, "entry_ids": ["wall", "keep"]}
        ],
        "upserts": [
            {
                "id": "keep",
                "category": "constraint",
                "statement": "keep the motor hole",
                "authority": "user_explicit",
                "source_turn_ids": [TURN],
            }
        ],
        "supersessions": [],
        "sources": {},
    }
    updated = apply_memory_update(
        memory,
        update,
        expected_turns=[
            {"turn_id": TURN, "conversation_id": "c" * 32, "sequence": 1}
        ],
        known_turn_ids={TURN},
    )
    require_user_explicit_preserved(memory, updated)
    ids = {item["id"] for item in updated["entries"]}
    assert "wall" in ids
    wall = next(item for item in updated["entries"] if item["id"] == "wall")
    assert wall["authority"] == "user_explicit"
    assert wall["status"] == "active"
    assert wall["statement"] == "2 mm wall"


def test_apply_cannot_drop_user_explicit_by_omission() -> None:
    memory = _memory_with_wall()
    omitted = empty_memory("project-a")
    omitted["entries"] = [
        {
            "id": "pretty",
            "category": "constraint",
            "statement": "prettier shape",
            "authority": "model_assumption",
            "source_turn_ids": [TURN],
            "status": "active",
            "superseded_by": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]
    with pytest.raises(RuntimeError, match="dropped user_explicit"):
        require_user_explicit_preserved(memory, omitted)
