"""Thread→global memory roll-up.

Promotes universally-true facts out of per-thread MEMORY.md files into the
shared global MEMORY.md. The LLM is injected as ``llm_fn`` so the logic is
unit-testable without network. See
docs/superpowers/specs/2026-07-15-memory-thread-global-rollup-design.md
"""
from __future__ import annotations

import json
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


_PROMPT_TEMPLATE = """\
You are curating an AI agent's long-term memory. Memory has per-thread files \
(notes local to one conversation topic) and a shared GLOBAL file injected into \
every conversation.

Identify facts sitting in per-thread memory that are UNIVERSALLY TRUE regardless \
of topic (e.g. machine/agent setup, stable global conventions) and should live in \
GLOBAL. Do NOT promote topic- or task-specific notes. Do NOT duplicate anything \
already in GLOBAL. Keep the total added text within {char_budget} characters, \
consolidating/rephrasing when helpful. For each promoted fact, list the per-thread \
entries it fully covers so they can be removed.

Reply with ONLY a JSON object, no prose:
{{"promotions": [{{"fact": "<global text>", "source_scopes": ["<scope>"], \
"remove": [["<scope>", "<verbatim per-thread entry>"]]}}]}}

PER-THREAD ENTRIES:
{threads}

CURRENT GLOBAL:
{global_}
"""


def build_prompt(thread_entries: Dict[str, List[str]], global_entries: List[str],
                 char_budget: int) -> str:
    return _PROMPT_TEMPLATE.format(
        char_budget=char_budget,
        threads=json.dumps(thread_entries, indent=2, ensure_ascii=False),
        global_=json.dumps(global_entries, indent=2, ensure_ascii=False),
    )


def _extract_json_object(text: str) -> Optional[dict]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def propose_promotions(thread_entries: Dict[str, List[str]],
                       global_entries: List[str], char_budget: int,
                       llm_fn: Callable[[str], str]) -> List[Promotion]:
    if not thread_entries:
        return []
    raw = llm_fn(build_prompt(thread_entries, global_entries, char_budget))
    obj = _extract_json_object(raw or "")
    if not obj:
        return []
    out: List[Promotion] = []
    for item in obj.get("promotions", []):
        if not isinstance(item, dict):
            continue
        fact = (item.get("fact") or "").strip()
        if not fact:
            continue
        remove = [(str(r[0]), str(r[1])) for r in item.get("remove", [])
                  if isinstance(r, (list, tuple)) and len(r) == 2]
        out.append(Promotion(fact=fact,
                             source_scopes=list(item.get("source_scopes", [])),
                             remove=remove))
    return out
