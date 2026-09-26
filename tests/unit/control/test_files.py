from __future__ import annotations

import json
import sys

import pytest

from tbot_console.control import state
from tbot_console.control.files import flock_held, read_object, write_json_atomic


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, None),
        ("{half", None),
        ("[1, 2]", None),
        ('{"pid": 1}', {"pid": 1}),
    ],
)
def test_read_object(tmp_path, content, expected):
    path = tmp_path / "record.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert read_object(path) == expected


def test_undecodable_bytes_read_as_nothing(tmp_path):
    path = tmp_path / "record.json"
    path.write_bytes(b'{"pid": 1, "note": "\xff"}')
    assert read_object(path) is None


def test_an_atomic_write_is_readable_sorted_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "run" / "record.json"
    write_json_atomic(path, {"b": "ё", "a": 1})
    assert (
        path.read_text(encoding="utf-8")
        == json.dumps({"a": 1, "b": "ё"}, ensure_ascii=False, indent=2) + "\n"
    )
    assert read_object(path) == {"a": 1, "b": "ё"}
    assert [p.name for p in path.parent.iterdir()] == ["record.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")
def test_a_lock_probe_tells_held_free_and_missing_apart(tmp_path):
    lock = tmp_path / "unit.lock"
    assert flock_held(lock) is None
    with state.transition_lock(lock):
        assert flock_held(lock) is True
    assert flock_held(lock) is False
