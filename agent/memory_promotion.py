"""Thread→global memory roll-up.

Promotes universally-true facts out of per-thread MEMORY.md files into the
shared global MEMORY.md. The LLM is injected as ``llm_fn`` so the logic is
unit-testable without network. See
docs/superpowers/specs/2026-07-15-memory-thread-global-rollup-design.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from tools.memory_tool import MemoryStore, get_memory_dir

_read_file = MemoryStore._read_file  # staticmethod: parse a MEMORY.md into entries


@dataclass
class Promotion:
    fact: str
    source_scopes: List[str]
    remove: List[Tuple[str, str]]  # (scope, entry_text)


def gather_thread_entries(mem_dir: Path) -> Dict[str, List[str]]:
    """Return {scope: [entries]} for every mem_dir/threads/<scope>/MEMORY.md."""
    threads_dir = mem_dir / "threads"
    result: Dict[str, List[str]] = {}
    if not threads_dir.is_dir():
        return result
    for scope_dir in sorted(threads_dir.iterdir()):
        f = scope_dir / "MEMORY.md"
        if f.is_file():
            entries = _read_file(f)
            if entries:
                result[scope_dir.name] = entries
    return result


def read_global_entries(mem_dir: Path) -> List[str]:
    """Return the entries in the shared global mem_dir/MEMORY.md."""
    return _read_file(mem_dir / "MEMORY.md")
