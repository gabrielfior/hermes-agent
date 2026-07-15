# Thread→Global Memory Roll-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a periodic, LLM-judged pass that promotes universally-true facts from per-thread `MEMORY.md` files into the shared global `MEMORY.md`, with move/dedupe, config-gated dry-run→apply, backups, and a report.

**Architecture:** A standalone, LLM-injected module `agent/memory_promotion.py` with pure functions (gather → propose → apply → run), exposed as `hermes memory promote` and scheduled by a weekly systemd user timer that starts in dry-run. All writes go through the existing `MemoryStore` file-locked API.

**Tech Stack:** Python 3.12, existing `tools/memory_tool.MemoryStore`, `hermes_cli.config.load_config`, pytest.

## Global Constraints

- Python 3.12; match existing import style in `tools/memory_tool.py` (`from typing import Dict, Any, List, Optional`).
- Never exceed `memory_char_limit` (default 2200) on the global file.
- All memory file writes go through `MemoryStore` (file-locked); do not hand-write `MEMORY.md`.
- Global default behavior is **dry-run** — writes only happen when effective mode is `apply`.
- Tests live in `tests/tools/`, use the `mem_dir` fixture pattern (monkeypatch `tools.memory_tool.get_memory_dir`), and inject a fake `llm_fn` (no network in unit tests).
- Repo: run all commands from `/home/ubuntu/.hermes/hermes-agent`; Python is `./venv/bin/python`; pytest is `./venv/bin/python -m pytest`.

---

### Task 1: Module scaffolding + gather/read helpers

**Files:**
- Create: `agent/memory_promotion.py`
- Test: `tests/tools/test_memory_promotion.py`

**Interfaces:**
- Produces:
  - `@dataclass Promotion(fact: str, source_scopes: List[str], remove: List[Tuple[str, str]])` — `remove` is a list of `(scope, entry_text)`.
  - `gather_thread_entries(mem_dir: Path) -> Dict[str, List[str]]` — `{scope: [entries]}` for each `mem_dir/threads/<scope>/MEMORY.md`.
  - `read_global_entries(mem_dir: Path) -> List[str]` — entries in `mem_dir/MEMORY.md`.
- Consumes: `tools.memory_tool._read_file` (static), `get_memory_dir`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_memory_promotion.py
from pathlib import Path
import pytest
from agent.memory_promotion import (
    Promotion, gather_thread_entries, read_global_entries,
)
from tools.memory_tool import MemoryStore


def _write(store_scope, mem_dir, target, entries):
    s = MemoryStore(memory_char_limit=2000, scope=store_scope)
    s.load_from_disk()
    for e in entries:
        s.add(target, e)


@pytest.fixture()
def mem_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    return tmp_path


def test_gather_thread_entries_and_global(mem_dir):
    _write("scopeA", mem_dir, "memory", ["fact a1", "fact a2"])
    _write("scopeB", mem_dir, "memory", ["fact b1"])
    _write(None, mem_dir, "global", ["global fact"])

    threads = gather_thread_entries(mem_dir)
    assert set(threads.keys()) == {"scopeA", "scopeB"}
    assert threads["scopeA"] == ["fact a1", "fact a2"]
    assert threads["scopeB"] == ["fact b1"]
    assert read_global_entries(mem_dir) == ["global fact"]


def test_gather_empty_when_no_threads(mem_dir):
    assert gather_thread_entries(mem_dir) == {}
    assert read_global_entries(mem_dir) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.memory_promotion'`.

- [ ] **Step 3: Write minimal implementation**

```python
# agent/memory_promotion.py
"""Thread→global memory roll-up.

Promotes universally-true facts out of per-thread MEMORY.md files into the
shared global MEMORY.md. LLM is injected as ``llm_fn`` so the logic is
unit-testable without network. See docs/superpowers/specs/2026-07-15-memory-thread-global-rollup-design.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from tools.memory_tool import MemoryStore, _read_file, get_memory_dir


@dataclass
class Promotion:
    fact: str
    source_scopes: List[str]
    remove: List[Tuple[str, str]]  # (scope, entry_text)


def gather_thread_entries(mem_dir: Path) -> Dict[str, List[str]]:
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
    return _read_file(mem_dir / "MEMORY.md")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/memory_promotion.py tests/tools/test_memory_promotion.py
git commit -m "feat(memory): scaffold roll-up module with gather/read helpers"
```

---

### Task 2: `propose_promotions` — prompt + LLM call + JSON parse

**Files:**
- Modify: `agent/memory_promotion.py`
- Test: `tests/tools/test_memory_promotion.py`

**Interfaces:**
- Consumes: `Promotion`, `gather_thread_entries`.
- Produces:
  - `build_prompt(thread_entries: Dict[str, List[str]], global_entries: List[str], char_budget: int) -> str`
  - `propose_promotions(thread_entries, global_entries, char_budget: int, llm_fn: Callable[[str], str]) -> List[Promotion]`
  - `llm_fn` receives a prompt string and returns raw model text; `propose_promotions` extracts the first `{...}` JSON object, reads `promotions`, returns `[]` on empty/invalid.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/tools/test_memory_promotion.py
import json
from agent.memory_promotion import build_prompt, propose_promotions


def test_build_prompt_contains_inputs():
    p = build_prompt({"s1": ["deploys with docker"]}, ["machine is ubuntu"], 500)
    assert "deploys with docker" in p
    assert "machine is ubuntu" in p
    assert "500" in p


def test_propose_parses_llm_json():
    def fake_llm(prompt):
        return 'noise before {"promotions": [{"fact": "machine is ubuntu 24.04", ' \
               '"source_scopes": ["s1"], "remove": [["s1", "ubuntu 24.04 box"]]}]} trailing'
    out = propose_promotions({"s1": ["ubuntu 24.04 box"]}, [], 500, fake_llm)
    assert len(out) == 1
    assert out[0].fact == "machine is ubuntu 24.04"
    assert out[0].source_scopes == ["s1"]
    assert out[0].remove == [("s1", "ubuntu 24.04 box")]


def test_propose_empty_on_bad_json():
    assert propose_promotions({"s1": ["x"]}, [], 500, lambda p: "not json") == []
    assert propose_promotions({"s1": ["x"]}, [], 500, lambda p: '{"promotions": []}') == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -k "propose or prompt" -q`
Expected: FAIL with `ImportError: cannot import name 'build_prompt'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to agent/memory_promotion.py
import json

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
{global}
"""


def build_prompt(thread_entries: Dict[str, List[str]], global_entries: List[str],
                 char_budget: int) -> str:
    return _PROMPT_TEMPLATE.format(
        char_budget=char_budget,
        threads=json.dumps(thread_entries, indent=2, ensure_ascii=False),
        global=json.dumps(global_entries, indent=2, ensure_ascii=False),
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
        fact = (item.get("fact") or "").strip()
        if not fact:
            continue
        remove = [(str(r[0]), str(r[1])) for r in item.get("remove", [])
                  if isinstance(r, (list, tuple)) and len(r) == 2]
        out.append(Promotion(fact=fact,
                             source_scopes=list(item.get("source_scopes", [])),
                             remove=remove))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -k "propose or prompt" -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/memory_promotion.py tests/tools/test_memory_promotion.py
git commit -m "feat(memory): LLM-judged promotion proposal (prompt + parse)"
```

---

### Task 3: `apply_promotions` — move/dedupe, char-limit, persistence

**Files:**
- Modify: `agent/memory_promotion.py`
- Test: `tests/tools/test_memory_promotion.py`

**Interfaces:**
- Consumes: `Promotion`, `MemoryStore`.
- Produces:
  - `@dataclass ApplyResult(promoted: List[str], removed: List[Tuple[str, str]], skipped_overflow: List[str])`
  - `apply_promotions(mem_dir: Path, promotions: List[Promotion]) -> ApplyResult` — adds each `fact` to global via `MemoryStore(scope=None).add("global", fact)` (enforces char-limit + scan + dedupe); on success removes the listed per-thread entries via a scoped `MemoryStore` (`_set_entries("memory", kept)` + `save_to_disk("memory")`). A fact whose add fails (overflow) goes to `skipped_overflow` and its removals are skipped.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/tools/test_memory_promotion.py
from agent.memory_promotion import apply_promotions, ApplyResult
from tools.memory_tool import MemoryStore


def _thread_entries(mem_dir, scope):
    s = MemoryStore(scope=scope); s.load_from_disk(); return s.memory_entries


def test_apply_moves_fact_to_global_and_removes_thread_copy(mem_dir):
    src = MemoryStore(memory_char_limit=2000, scope="s1"); src.load_from_disk()
    src.add("memory", "the box runs ubuntu 24.04")
    proms = [Promotion(fact="machine runs ubuntu 24.04",
                       source_scopes=["s1"],
                       remove=[("s1", "the box runs ubuntu 24.04")])]
    res = apply_promotions(mem_dir, proms)
    assert res.promoted == ["machine runs ubuntu 24.04"]
    assert ("s1", "the box runs ubuntu 24.04") in res.removed
    # persisted: global has the fact, thread no longer does
    assert "machine runs ubuntu 24.04" in read_global_entries(mem_dir)
    assert "the box runs ubuntu 24.04" not in _thread_entries(mem_dir, "s1")


def test_apply_dedupes_against_existing_global(mem_dir):
    g = MemoryStore(scope=None); g.load_from_disk(); g.add("global", "already here")
    res = apply_promotions(mem_dir, [Promotion("already here", ["s1"], [])])
    assert read_global_entries(mem_dir).count("already here") == 1


def test_apply_char_limit_overflow_skips(mem_dir):
    big = "x" * 1900
    g = MemoryStore(memory_char_limit=2000, scope=None); g.load_from_disk()
    g.add("global", big)  # global now ~1900/2000
    res = apply_promotions(mem_dir, [Promotion("y" * 500, ["s1"], [("s1", "z")])])
    assert res.promoted == []
    assert len(res.skipped_overflow) == 1
    assert res.removed == []  # removal skipped when add fails
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -k apply -q`
Expected: FAIL with `ImportError: cannot import name 'apply_promotions'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to agent/memory_promotion.py
@dataclass
class ApplyResult:
    promoted: List[str] = field(default_factory=list)
    removed: List[Tuple[str, str]] = field(default_factory=list)
    skipped_overflow: List[str] = field(default_factory=list)


def apply_promotions(mem_dir: Path, promotions: List[Promotion]) -> ApplyResult:
    res = ApplyResult()
    gstore = MemoryStore(scope=None)
    gstore.load_from_disk()
    for p in promotions:
        if p.fact in gstore.global_entries:
            # already global — still allow removing redundant thread copies
            _remove_thread_entries(p.remove, res)
            continue
        add_res = gstore.add("global", p.fact)   # global tier -> MEMORY.md
        if not add_res.get("success"):
            res.skipped_overflow.append(p.fact)
            continue
        res.promoted.append(p.fact)
        gstore.global_entries.append(p.fact)     # keep local view in sync for dedupe
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
                res.removed.append((scope, e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -k apply -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/memory_promotion.py tests/tools/test_memory_promotion.py
git commit -m "feat(memory): apply promotions with move/dedupe and char-limit guard"
```

---

### Task 4: `run_promotion` orchestration + config-mode gating + report/backup

**Files:**
- Modify: `agent/memory_promotion.py`
- Test: `tests/tools/test_memory_promotion.py`

**Interfaces:**
- Consumes: all of the above; `hermes_cli.config.load_config`.
- Produces:
  - `effective_apply(config: dict, cli_flag: Optional[bool]) -> bool` — `cli_flag` (True=apply, False=dry-run) overrides; else `config["memory_promotion"]["mode"] == "apply"`; default False.
  - `@dataclass Report(proposals: List[Promotion], applied: Optional[ApplyResult], dry_run: bool, error: Optional[str])`
  - `run_promotion(mem_dir: Path, llm_fn, *, apply: bool, char_limit: int = 2200) -> Report` — gather → propose → if apply: backup `MEMORY.md` then `apply_promotions`; write `logs/memory-curator/<ts>/REPORT.md` via `write_report`. Never raises; LLM/parse errors → `Report(error=...)`, no writes.
  - `write_report(report: Report, ts: str) -> Path`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/tools/test_memory_promotion.py
from agent.memory_promotion import run_promotion, effective_apply


def _fake_llm_promoting(fact, scope, entry):
    payload = ('{"promotions": [{"fact": "%s", "source_scopes": ["%s"], '
               '"remove": [["%s", "%s"]]}]}' % (fact, scope, scope, entry))
    return lambda prompt: payload


def test_effective_apply_precedence():
    assert effective_apply({"memory_promotion": {"mode": "apply"}}, None) is True
    assert effective_apply({"memory_promotion": {"mode": "dry-run"}}, None) is False
    assert effective_apply({}, None) is False                      # default dry-run
    assert effective_apply({"memory_promotion": {"mode": "apply"}}, False) is False  # CLI overrides


def test_run_dry_run_writes_nothing(mem_dir, monkeypatch):
    monkeypatch.setattr("agent.memory_promotion.get_hermes_home", lambda: mem_dir)
    src = MemoryStore(scope="s1"); src.load_from_disk(); src.add("memory", "ubuntu box")
    rep = run_promotion(mem_dir, _fake_llm_promoting("machine=ubuntu", "s1", "ubuntu box"),
                        apply=False)
    assert rep.dry_run is True
    assert [p.fact for p in rep.proposals] == ["machine=ubuntu"]
    assert read_global_entries(mem_dir) == []          # nothing written
    assert "ubuntu box" in _thread_entries(mem_dir, "s1")


def test_run_apply_writes(mem_dir, monkeypatch):
    monkeypatch.setattr("agent.memory_promotion.get_hermes_home", lambda: mem_dir)
    src = MemoryStore(scope="s1"); src.load_from_disk(); src.add("memory", "ubuntu box")
    rep = run_promotion(mem_dir, _fake_llm_promoting("machine=ubuntu", "s1", "ubuntu box"),
                        apply=True)
    assert rep.dry_run is False
    assert "machine=ubuntu" in read_global_entries(mem_dir)
    assert rep.applied.promoted == ["machine=ubuntu"]


def test_run_llm_failure_no_writes(mem_dir, monkeypatch):
    monkeypatch.setattr("agent.memory_promotion.get_hermes_home", lambda: mem_dir)
    src = MemoryStore(scope="s1"); src.load_from_disk(); src.add("memory", "ubuntu box")
    def boom(prompt): raise RuntimeError("provider down")
    rep = run_promotion(mem_dir, boom, apply=True)
    assert rep.error is not None
    assert read_global_entries(mem_dir) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -k "run or effective" -q`
Expected: FAIL with `ImportError: cannot import name 'run_promotion'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to agent/memory_promotion.py — add near the top imports:
import shutil
from hermes_constants import get_hermes_home


def effective_apply(config: dict, cli_flag: Optional[bool]) -> bool:
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
        lines.append(f"- **{p.fact}**  (from {', '.join(p.source_scopes)}; "
                     f"removes {len(p.remove)} thread entr{'y' if len(p.remove)==1 else 'ies'})")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add agent/memory_promotion.py tests/tools/test_memory_promotion.py
git commit -m "feat(memory): run_promotion orchestration, config gating, report + backup"
```

---

### Task 5: Production `llm_fn` + CLI `hermes memory promote`

**Files:**
- Modify: `agent/memory_promotion.py` (add `default_llm_fn`, `promote_cli`)
- Modify: `hermes_cli/subcommands/memory.py` (add `promote` subparser)
- Modify: `hermes_cli/main.py` (`cmd_memory`: dispatch `promote`)

**Interfaces:**
- Consumes: `run_promotion`, `effective_apply`, `load_config`.
- Produces:
  - `default_llm_fn(prompt: str) -> str` — subprocess to `python -m hermes_cli.main chat -q <prompt> -Q --yolo --max-turns 1 --ignore-rules`, returns stdout.
  - `promote_cli(*, cli_apply: Optional[bool], as_json: bool) -> int` — resolves mode via `effective_apply(load_config(), cli_apply)`, timestamps via `datetime`, calls `run_promotion`, prints summary, returns exit code.

- [ ] **Step 1: Write the failing test** (unit-test the CLI resolver with injected deps; the subprocess `default_llm_fn` is verified manually in Task 6)

```python
# add to tests/tools/test_memory_promotion.py
from agent import memory_promotion as mp


def test_promote_cli_uses_config_mode(mem_dir, monkeypatch, capsys):
    monkeypatch.setattr("agent.memory_promotion.get_hermes_home", lambda: mem_dir)
    monkeypatch.setattr(mp, "load_config", lambda: {"memory_promotion": {"mode": "dry-run"}})
    monkeypatch.setattr(mp, "default_llm_fn",
                        lambda prompt: '{"promotions": []}')
    src = MemoryStore(scope="s1"); src.load_from_disk(); src.add("memory", "x")
    rc = mp.promote_cli(cli_apply=None, as_json=False)
    assert rc == 0
    assert "dry-run" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -k promote_cli -q`
Expected: FAIL (`load_config`/`promote_cli`/`default_llm_fn` not defined).

- [ ] **Step 3: Write minimal implementation**

```python
# add to agent/memory_promotion.py
import subprocess
import sys
from datetime import datetime, timezone
from hermes_cli.config import load_config


def default_llm_fn(prompt: str) -> str:
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "chat", "-q", prompt,
         "-Q", "--yolo", "--max-turns", "1", "--ignore-rules"],
        capture_output=True, text=True, timeout=180, cwd=str(repo),
    )
    return proc.stdout or ""


def promote_cli(*, cli_apply: Optional[bool], as_json: bool) -> int:
    apply = effective_apply(load_config(), cli_apply)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rep = run_promotion(get_memory_dir(), default_llm_fn, apply=apply, ts=ts)
    if as_json:
        import json as _json
        print(_json.dumps({
            "mode": "apply" if apply else "dry-run",
            "proposals": [p.fact for p in rep.proposals],
            "error": rep.error,
        }))
    else:
        mode = "apply" if apply else "dry-run"
        print(f"Memory roll-up ({mode}): {len(rep.proposals)} proposal(s)."
              + (f" ERROR: {rep.error}" if rep.error else ""))
        for p in rep.proposals:
            print(f"  - {p.fact}")
    return 1 if rep.error else 0
```

```python
# hermes_cli/subcommands/memory.py — add inside build_memory_parser, before
# `memory_parser.set_defaults(func=cmd_memory)`:
    _promote = memory_sub.add_parser(
        "promote",
        help="Roll up universal per-thread facts into shared/global memory",
    )
    _mode = _promote.add_mutually_exclusive_group()
    _mode.add_argument("--apply", dest="promote_apply", action="store_true",
                       default=None, help="Force apply (override config mode)")
    _mode.add_argument("--dry-run", dest="promote_apply", action="store_false",
                       help="Force dry-run (override config mode)")
    _promote.add_argument("--json", dest="promote_json", action="store_true",
                          help="Machine-readable output")
```

```python
# hermes_cli/main.py — inside cmd_memory(args), add a branch alongside the
# existing `if args.memory_command == ...` dispatch (match the surrounding style):
    if args.memory_command == "promote":
        from agent.memory_promotion import promote_cli
        return promote_cli(cli_apply=getattr(args, "promote_apply", None),
                           as_json=getattr(args, "promote_json", False))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/tools/test_memory_promotion.py -q`
Expected: PASS (all). Then verify CLI wiring:
Run: `./venv/bin/python -m hermes_cli.main memory promote --help`
Expected: shows `--apply`, `--dry-run`, `--json`.

- [ ] **Step 5: Commit**

```bash
git add agent/memory_promotion.py hermes_cli/subcommands/memory.py hermes_cli/main.py tests/tools/test_memory_promotion.py
git commit -m "feat(memory): hermes memory promote CLI + subprocess llm_fn"
```

---

### Task 6: Weekly systemd timer (dry-run default) + live dry-run verification

**Files:**
- Create (on box): `~/.config/systemd/user/hermes-memory-rollup.service`
- Create (on box): `~/.config/systemd/user/hermes-memory-rollup.timer`

**Interfaces:**
- Consumes: `hermes memory promote` CLI.

- [ ] **Step 1: Write the service unit**

```ini
# ~/.config/systemd/user/hermes-memory-rollup.service
[Unit]
Description=Roll up universal per-thread memory into global (config-gated; dry-run by default)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/ubuntu/.hermes/hermes-agent
ExecStart=/home/ubuntu/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main memory promote
```

- [ ] **Step 2: Write the timer unit**

```ini
# ~/.config/systemd/user/hermes-memory-rollup.timer
[Unit]
Description=Weekly memory roll-up

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=3h

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Install + enable**

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermes-memory-rollup.timer
systemctl --user list-timers hermes-memory-rollup.timer --no-pager
```
Expected: timer listed with a NEXT run ~1 week out.

- [ ] **Step 4: Verify a live dry-run (uses the real model, writes nothing)**

Run: `cd /home/ubuntu/.hermes/hermes-agent && ./venv/bin/python -m hermes_cli.main memory promote --dry-run`
Expected: prints `Memory roll-up (dry-run): N proposal(s)`; a `REPORT.md` appears under `~/.hermes/logs/memory-curator/<ts>/`; `~/.hermes/memories/MEMORY.md` is unchanged (diff/byte-count before and after).

- [ ] **Step 5: Confirm config default is dry-run**

Run: `grep -A2 '^memory_promotion:' /home/ubuntu/.hermes/config.yaml || echo "(absent → defaults to dry-run)"`
Expected: absent (effective mode is dry-run by default). Document that flipping to live is: add `memory_promotion:\n  mode: apply` to `config.yaml` (no restart needed).

- [ ] **Step 6: Commit deployment notes** (units live outside the repo; record them in-repo for reproducibility)

```bash
mkdir -p ops/systemd
cp ~/.config/systemd/user/hermes-memory-rollup.service ops/systemd/
cp ~/.config/systemd/user/hermes-memory-rollup.timer ops/systemd/
git add ops/systemd/hermes-memory-rollup.service ops/systemd/hermes-memory-rollup.timer
git commit -m "ops(memory): weekly roll-up systemd timer (dry-run by default)"
```

---

## Self-Review

**Spec coverage:**
- LLM-judged criterion → Task 2. ✅
- Auto-apply + report, dry-run rollout → Task 4 (`run_promotion`, report/backup), Task 6 (timer runs no-flag → config `dry-run` default). ✅
- Standalone module + CLI + weekly timer → Tasks 1-5 (module/CLI), Task 6 (timer). ✅
- Move/dedupe → Task 3 (`apply_promotions` + `_remove_thread_entries`). ✅
- Char-limit aware → Task 3 (add() enforces; overflow → skipped) + Task 4 (budget passed to LLM). ✅
- Backup + REPORT.md → Task 4. ✅
- Idempotent → Task 3 (dedupe vs existing global). ✅
- Config-gated mode + CLI override → Task 4 (`effective_apply`) + Task 5 (CLI flags). ✅
- 8 spec test cases → covered across Tasks 1-4 (gather/no-ops, promote, don't-promote via empty LLM, dedupe, char-limit, idempotency, failure, persistence) + config-gating (#9) in Task 4. ✅

**Placeholder scan:** none — every code step is complete.

**Type consistency:** `Promotion(fact, source_scopes, remove)` used consistently; `remove` is `List[Tuple[str,str]]` everywhere; `apply_promotions(mem_dir, promotions) -> ApplyResult`; `run_promotion(..., apply=bool, ts=str)`; `effective_apply(config, cli_flag)`; `promote_cli(cli_apply, as_json)`. Consistent across tasks.

## Notes / assumptions to verify during execution

- `MemoryStore.add` returns a dict with `"success"` (confirmed at `tools/memory_tool.py:381`). If the real key differs, adjust `apply_promotions`.
- `cmd_memory` dispatches on `args.memory_command` (confirmed pattern in `subcommands/memory.py`). Match the existing branch style in `main.py` when inserting the `promote` branch.
- `default_llm_fn` strips nothing beyond taking stdout; `_extract_json_object` already tolerates the `session_id:` preamble that `hermes chat -Q` prints. Verify in Task 6 step 4 that the model returns a parseable object; if it wraps JSON in a code fence, `_extract_json_object` still finds the `{…}`.
