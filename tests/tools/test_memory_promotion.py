"""Tests for agent/memory_promotion.py — thread→global memory roll-up."""
from pathlib import Path

import pytest

from agent.memory_promotion import (
    Promotion, gather_thread_entries, read_global_entries,
    build_prompt, propose_promotions,
    ApplyResult, apply_promotions,
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


# --- Task 2: propose_promotions -------------------------------------------

def test_build_prompt_contains_inputs():
    p = build_prompt({"s1": ["deploys with docker"]}, ["machine is ubuntu"], 500)
    assert "deploys with docker" in p
    assert "machine is ubuntu" in p
    assert "500" in p


def test_propose_parses_llm_json():
    def fake_llm(prompt):
        return ('noise before {"promotions": [{"fact": "machine is ubuntu 24.04", '
                '"source_scopes": ["s1"], "remove": [["s1", "ubuntu 24.04 box"]]}]} trailing')
    out = propose_promotions({"s1": ["ubuntu 24.04 box"]}, [], 500, fake_llm)
    assert len(out) == 1
    assert out[0].fact == "machine is ubuntu 24.04"
    assert out[0].source_scopes == ["s1"]
    assert out[0].remove == [("s1", "ubuntu 24.04 box")]


def test_propose_empty_on_bad_json():
    assert propose_promotions({"s1": ["x"]}, [], 500, lambda p: "not json") == []
    assert propose_promotions({"s1": ["x"]}, [], 500, lambda p: '{"promotions": []}') == []


def test_propose_empty_when_no_threads():
    called = []
    propose_promotions({}, [], 500, lambda p: called.append(1) or "{}")
    assert called == []  # LLM not called when there are no threads


# --- Task 3: apply_promotions ---------------------------------------------

def test_apply_moves_fact_to_global_and_removes_thread_copy(mem_dir):
    src = MemoryStore(memory_char_limit=2000, scope="s1")
    src.load_from_disk()
    src.add("memory", "the box runs ubuntu 24.04")
    proms = [Promotion(fact="machine runs ubuntu 24.04",
                       source_scopes=["s1"],
                       remove=[("s1", "the box runs ubuntu 24.04")])]
    res = apply_promotions(mem_dir, proms)
    assert res.promoted == ["machine runs ubuntu 24.04"]
    assert ("s1", "the box runs ubuntu 24.04") in res.removed
    assert "machine runs ubuntu 24.04" in read_global_entries(mem_dir)
    assert "the box runs ubuntu 24.04" not in _thread_entries("s1")


def test_apply_dedupes_against_existing_global(mem_dir):
    g = MemoryStore(scope=None)
    g.load_from_disk()
    g.add("global", "already here")
    apply_promotions(mem_dir, [Promotion("already here", ["s1"], [])])
    assert read_global_entries(mem_dir).count("already here") == 1


def test_apply_char_limit_overflow_skips(mem_dir):
    g = MemoryStore(scope=None)   # default limit 2200
    g.load_from_disk()
    g.add("global", "x" * 1900)   # global now ~1900/2200
    res = apply_promotions(mem_dir, [Promotion("y" * 500, ["s1"], [("s1", "z")])])
    assert res.promoted == []
    assert len(res.skipped_overflow) == 1
    assert res.removed == []  # removal skipped when the add fails
