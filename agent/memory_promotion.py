"""Thread→global memory roll-up.

Promotes universally-true facts out of per-thread MEMORY.md files into the
shared global MEMORY.md. The LLM is injected as ``llm_fn`` so the logic is
unit-testable without network. See
docs/superpowers/specs/2026-07-15-memory-thread-global-rollup-design.md
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home
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


@dataclass
class ApplyResult:
    promoted: List[str] = field(default_factory=list)
    removed: List[Tuple[str, str]] = field(default_factory=list)
    skipped_overflow: List[str] = field(default_factory=list)


def apply_promotions(mem_dir: Path, promotions: List[Promotion]) -> ApplyResult:
    """Add each fact to the global tier (char-limit + scan enforced by
    MemoryStore.add) and remove the per-thread copies it covers. Move, not
    copy. Idempotent: facts already in global are skipped but still trigger
    removal of their redundant per-thread copies."""
    res = ApplyResult()
    gstore = MemoryStore(scope=None)
    gstore.load_from_disk()
    seen = set(gstore.global_entries)
    for p in promotions:
        if p.fact in seen:
            _remove_thread_entries(p.remove, res)
            continue
        add_res = gstore.add("global", p.fact)   # global tier -> MEMORY.md
        if not add_res.get("success"):
            res.skipped_overflow.append(p.fact)
            continue
        res.promoted.append(p.fact)
        seen.add(p.fact)
        _remove_thread_entries(p.remove, res)
    return res


def _remove_thread_entries(remove: List[Tuple[str, str]], res: ApplyResult) -> None:
    by_scope: Dict[str, List[str]] = {}
    for scope, entry in remove:
        by_scope.setdefault(scope, []).append(entry)
    for scope, entries in by_scope.items():
        tstore = MemoryStore(scope=scope)
        tstore.load_from_disk()
        kept = [e for e in tstore.memory_entries if e not in entries]
        if kept != tstore.memory_entries:
            tstore._set_entries("memory", kept)
            tstore.save_to_disk("memory")
            for e in entries:
                if e not in kept:
                    res.removed.append((scope, e))


def effective_apply(config: dict, cli_flag: Optional[bool]) -> bool:
    """Resolve whether to apply. Explicit CLI flag wins; else config
    ``memory_promotion.mode == 'apply'``; default False (dry-run)."""
    if cli_flag is not None:
        return cli_flag
    mode = (config.get("memory_promotion") or {}).get("mode", "dry-run")
    return str(mode).strip().lower() == "apply"


@dataclass
class Report:
    proposals: List[Promotion]
    applied: Optional[ApplyResult]
    dry_run: bool
    error: Optional[str] = None


def run_promotion(mem_dir: Path, llm_fn: Callable[[str], str], *, apply: bool,
                  char_limit: int = 2200, ts: str = "run") -> Report:
    """Gather -> propose -> (backup + apply unless dry-run) -> report.

    Never raises: LLM/parse errors are captured in Report.error with no writes.
    """
    try:
        threads = gather_thread_entries(mem_dir)
        global_entries = read_global_entries(mem_dir)
        budget = max(0, char_limit - sum(len(e) for e in global_entries))
        proposals = propose_promotions(threads, global_entries, budget, llm_fn)
    except Exception as exc:  # noqa: BLE001 - failure isolation, never raise
        rep = Report(proposals=[], applied=None, dry_run=not apply, error=str(exc))
        _safe_write_report(rep, ts)
        return rep

    applied = None
    if apply and proposals:
        gpath = mem_dir / "MEMORY.md"
        if gpath.is_file():
            shutil.copy2(gpath, gpath.with_suffix(f".md.bak.{ts}"))
        applied = apply_promotions(mem_dir, proposals)

    rep = Report(proposals=proposals, applied=applied, dry_run=not apply)
    _safe_write_report(rep, ts)
    return rep


def write_report(report: Report, ts: str) -> Path:
    out_dir = get_hermes_home() / "logs" / "memory-curator" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Memory roll-up — {ts}", "",
             f"Mode: {'dry-run' if report.dry_run else 'apply'}",
             f"Error: {report.error or 'none'}", "", "## Proposals"]
    for p in report.proposals:
        n = len(p.remove)
        lines.append(f"- **{p.fact}**  (from {', '.join(p.source_scopes)}; "
                     f"removes {n} thread entr{'y' if n == 1 else 'ies'})")
    if report.applied:
        lines += ["", "## Applied",
                  f"- promoted: {len(report.applied.promoted)}",
                  f"- removed thread entries: {len(report.applied.removed)}",
                  f"- skipped (overflow): {len(report.applied.skipped_overflow)}"]
    path = out_dir / "REPORT.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _safe_write_report(report: Report, ts: str) -> None:
    try:
        write_report(report, ts)
    except Exception:  # noqa: BLE001 - reporting must never break the run
        pass
