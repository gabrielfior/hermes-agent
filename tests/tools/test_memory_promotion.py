"""Tests for agent/memory_promotion.py — thread→global memory roll-up."""
from pathlib import Path

import pytest

from agent.memory_promotion import (
    Promotion, gather_thread_entries, read_global_entries,
)
from tools.memory_tool import MemoryStore


@pytest.fixture()
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    return tmp_path


def _write(store_scope, target, entries):
    s = MemoryStore(memory_char_limit=2000, scope=store_scope)
    s.load_from_disk()
    for e in entries:
        s.add(target, e)


def _thread_entries(scope):
    s = MemoryStore(scope=scope)
    s.load_from_disk()
    return s.memory_entries


def test_gather_thread_entries_and_global(mem_dir):
    _write("scopeA", "memory", ["fact a1", "fact a2"])
    _write("scopeB", "memory", ["fact b1"])
    _write(None, "global", ["global fact"])

    threads = gather_thread_entries(mem_dir)
    assert set(threads.keys()) == {"scopeA", "scopeB"}
    assert threads["scopeA"] == ["fact a1", "fact a2"]
    assert threads["scopeB"] == ["fact b1"]
    assert read_global_entries(mem_dir) == ["global fact"]


def test_gather_empty_when_no_threads(mem_dir):
    assert gather_thread_entries(mem_dir) == {}
    assert read_global_entries(mem_dir) == []
