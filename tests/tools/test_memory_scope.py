"""Tests for per-thread (scoped) memory in tools/memory_tool.py.

Verifies the two-tier model: a shared global MEMORY.md + per-thread MEMORY.md,
with USER.md shared. A fact written in one thread's memory must NOT appear in
another thread, while the global tier and the user profile remain shared.
"""

import pytest

from tools.memory_tool import MemoryStore, _sanitize_scope, memory_tool, MEMORY_SCHEMA


SCOPE_A = "agent:main:telegram:dm:498299501:111"
SCOPE_B = "agent:main:telegram:dm:498299501:222"


@pytest.fixture()
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    return tmp_path


def _store(scope):
    s = MemoryStore(memory_char_limit=2000, user_char_limit=1000, scope=scope)
    s.load_from_disk()
    return s


# --------------------------------------------------------------------------
# Scope sanitization
# --------------------------------------------------------------------------

class TestSanitizeScope:
    def test_deterministic_and_slugged(self):
        out = _sanitize_scope("agent:main:telegram:dm:498299501:72671")
        assert out == _sanitize_scope("agent:main:telegram:dm:498299501:72671")
        assert ":" not in out and "/" not in out
        assert out == "agent_main_telegram_dm_498299501_72671"

    def test_distinct_scopes_distinct_slugs(self):
        assert _sanitize_scope(SCOPE_A) != _sanitize_scope(SCOPE_B)


# --------------------------------------------------------------------------
# Per-thread isolation
# --------------------------------------------------------------------------

class TestThreadIsolation:
    def test_thread_memory_not_visible_in_other_thread(self, mem_dir):
        a = _store(SCOPE_A)
        a.add("memory", "thread A only: tailscale mirror cron")
        a.add("global", "shared: agent stack is Codex+Claude+Hermes")
        a.add("user", "Gabriel studied at Oxford")

        b = _store(SCOPE_B)  # loads fresh, after A's writes
        b_mem = b._entries_for("memory")
        b_global = b._entries_for("global")
        b_user = b._entries_for("user")

        assert "thread A only: tailscale mirror cron" not in b_mem
        assert any("agent stack" in e for e in b_global)
        assert any("Oxford" in e for e in b_user)

    def test_thread_memory_visible_in_same_thread(self, mem_dir):
        a = _store(SCOPE_A)
        a.add("memory", "thread A only: tailscale mirror cron")
        a2 = _store(SCOPE_A)
        assert any("tailscale" in e for e in a2._entries_for("memory"))

    def test_files_land_in_expected_paths(self, mem_dir):
        a = _store(SCOPE_A)
        a.add("memory", "thread fact")
        a.add("global", "global fact")
        a.add("user", "user fact")
        assert (mem_dir / "threads" / _sanitize_scope(SCOPE_A) / "MEMORY.md").exists()
        assert (mem_dir / "MEMORY.md").exists()       # global tier
        assert (mem_dir / "USER.md").exists()
        # thread fact must not be in the global file
        assert "thread fact" not in (mem_dir / "MEMORY.md").read_text()
        assert "thread fact" in (mem_dir / "threads" / _sanitize_scope(SCOPE_A) / "MEMORY.md").read_text()


class TestSystemPromptBlocks:
    def test_scoped_store_renders_thread_and_global_blocks(self, mem_dir):
        a = _store(SCOPE_A)
        a.add("memory", "thread fact alpha")
        a.add("global", "global fact beta")
        a2 = _store(SCOPE_A)  # reload to refresh frozen snapshot
        thread_block = a2.format_for_system_prompt("memory")
        global_block = a2.format_for_system_prompt("global")
        assert thread_block and "thread fact alpha" in thread_block
        assert global_block and "global fact beta" in global_block
        # they are distinct, separately-labelled blocks
        assert "thread fact alpha" not in global_block
        assert "global fact beta" not in thread_block


# --------------------------------------------------------------------------
# Backward compatibility: no scope == single global MEMORY.md (today's behavior)
# --------------------------------------------------------------------------

class TestNoScopeBackwardCompat:
    def test_no_scope_writes_to_global_memory_file(self, mem_dir):
        s = MemoryStore(memory_char_limit=2000, user_char_limit=1000)  # scope=None
        s.load_from_disk()
        s.add("memory", "cli fact")
        assert (mem_dir / "MEMORY.md").exists()
        assert "cli fact" in (mem_dir / "MEMORY.md").read_text()
        # no per-thread dir created
        assert not (mem_dir / "threads").exists()
        # still injected via the "memory" block
        s2 = MemoryStore(memory_char_limit=2000, user_char_limit=1000)
        s2.load_from_disk()
        block = s2.format_for_system_prompt("memory")
        assert block and "cli fact" in block


# --------------------------------------------------------------------------
# Tool schema / dispatcher
# --------------------------------------------------------------------------

class TestSchemaAndDispatch:
    def test_schema_allows_global_target(self):
        enum = MEMORY_SCHEMA["parameters"]["properties"]["target"]["enum"]
        assert "global" in enum
        assert "memory" in enum and "user" in enum

    def test_dispatch_rejects_unknown_target(self, mem_dir):
        s = _store(SCOPE_A)
        out = memory_tool(action="add", target="bogus", content="x", store=s)
        assert "Invalid target" in out

    def test_dispatch_global_add(self, mem_dir):
        s = _store(SCOPE_A)
        out = memory_tool(action="add", target="global", content="dispatch global fact", store=s)
        assert '"success": true' in out and '"target": "global"' in out
        # verify it persisted to the shared global file, not the per-thread file
        s.load_from_disk()
        assert "dispatch global fact" in s.global_entries
