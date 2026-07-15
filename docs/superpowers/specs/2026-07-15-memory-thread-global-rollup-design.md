# Design: Automatic thread→global memory roll-up

**Date:** 2026-07-15
**Repo:** gabrielfior/hermes-agent (fork of NousResearch/hermes-agent)
**Status:** approved design, pending spec review

## Problem

Curated memory has three tiers (`tools/memory_tool.py`): per-thread
`memories/threads/<scope>/MEMORY.md`, a shared `memories/MEMORY.md` (global),
and `memories/USER.md`. A fact reaches the global tier only when the model
explicitly saves with `target: global`. There is no mechanism to notice that a
fact learned in individual threads is actually universal and roll it up into
global. This feature adds that.

## Goals

- Periodically detect facts sitting in per-thread memory that are **universally
  true regardless of topic**, and promote them into the global tier.
- Auto-apply promotions, with a report and a backup so any bad promotion is
  visible and trivially reversible.
- Keep the global tier within its existing character budget.
- Be isolated and testable — no network required to unit-test the logic.

## Non-goals

- No changes to runtime `memory_tool.py` write/read behavior or the system-prompt
  snapshot format.
- Does not touch `USER.md`, skills, or the existing skill curator.
- Not a demotion/GC mechanism (global→thread) — promotion only.
- No cross-user / cross-agent sharing.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Promotion criterion | **LLM-judged** — an LLM decides what is universal |
| Apply model | **Auto-apply + report**, but **roll out dry-run first** (config-gated) |
| Structure | Standalone module + CLI + weekly systemd timer |
| Move vs copy | **Move/dedupe** — add to global, remove redundant per-thread copies |
| Cadence | **Weekly** |

### Rollout mode (config-gated)

The end state is auto-apply, but the timer **starts in dry-run** so the reports
can be reviewed before anything is written. A config key controls it:

```yaml
memory_promotion:
  mode: dry-run   # dry-run (default): propose + report only, no writes
                  # apply: perform the roll-up
```

The weekly timer runs `hermes memory promote` (no flag) and the effective mode
comes from `memory_promotion.mode`. Flipping to live is a one-line config edit —
no unit-file edit, no restart (the timer re-reads config each run). Explicit
`--dry-run` / `--apply` on the CLI override the config for a manual run.

## Architecture

New module `agent/memory_promotion.py` with pure functions; the LLM is injected
as a callable so tests run without network.

```
gather_thread_entries(mem_dir: Path) -> dict[str, list[str]]
    # {scope: [entries]} for every memories/threads/<scope>/MEMORY.md

read_global_entries(mem_dir: Path) -> list[str]

propose_promotions(thread_entries, global_entries, char_budget, llm_fn)
    -> list[Promotion]
    # Promotion = {fact: str, source_scopes: [str], remove: [{scope, entry}]}
    # llm_fn(prompt: str) -> str (JSON). LLM sees all thread entries + current
    # global + remaining char budget; returns universal facts NOT already in
    # global, plus which per-thread entries are now covered and should be removed.

apply_promotions(store: MemoryStore, promotions) -> ApplyResult
    # For each promotion: add fact to global (respecting char limit), then
    # remove the flagged per-thread entries. Exact-dedupe against existing
    # global. Persists via the existing MemoryStore file-locked write path.

run_promotion(mem_dir, llm_fn, *, dry_run: bool) -> Report
    # Orchestrates gather -> propose -> (backup + apply unless dry_run) -> report
```

### Data flow

1. `gather_thread_entries` enumerates all thread `MEMORY.md` files; `read_global_entries` reads current global.
2. Compute remaining global char budget = `memory_char_limit − len(current global)`.
3. `propose_promotions` builds one LLM request (JSON in / structured JSON out) and returns candidate promotions.
4. If `dry_run`: log proposals to the report, write nothing.
5. Else: back up `memories/MEMORY.md`, then `apply_promotions` (add to global, remove per-thread copies) through the `MemoryStore` locked-write path, then write the report.

### LLM contract

- **Input** (JSON): `{ "threads": {scope: [entries]}, "global": [entries], "global_char_budget": N }`.
- **Output** (structured JSON): `{ "promotions": [ { "fact": str, "source_scopes": [str], "remove": [ {"scope": str, "entry": str} ] } ] }`.
- **Prompt rules:** promote ONLY facts that hold regardless of topic (e.g. machine/agent setup, stable global conventions); never promote topic-/task-specific notes; do not duplicate anything already in `global`; keep total added text within `global_char_budget`, consolidating/rephrasing when needed; `remove` lists only per-thread entries fully covered by the promoted fact.
- Model: the configured default (currently opencode-go/deepseek-v4-flash), obtained via the same auxiliary-LLM mechanism the skill curator uses. Exact call site pinned during planning.

### Safety

- **Backup:** copy `memories/MEMORY.md` → timestamped backup before any write.
- **Report:** `logs/memory-curator/<ts>/REPORT.md` + `run.json` listing promoted facts, source scopes, and removed per-thread entries (curator-style).
- **Char-limit aware:** never exceed `memory_char_limit`; budget passed to the LLM and re-checked in `apply_promotions` (skip + note any overflow).
- **Idempotent:** the LLM sees current global; `apply_promotions` also exact-dedupes, so re-runs promote nothing new.
- **Sanitization unchanged:** the runtime load path already sanitizes global entries for the system-prompt snapshot; promoted facts originate from user-curated thread files.
- **Failure isolation:** any error (LLM down, parse failure) aborts with no writes and a logged reason — never leaves global half-written (single locked write).

### CLI

`hermes memory promote [--dry-run] [--apply] [--json]`
- No flag: effective mode comes from `memory_promotion.mode` (default `dry-run`).
- `--dry-run`: force propose + report only, no writes (overrides config).
- `--apply`: force the roll-up (overrides config).
- `--json`: machine-readable output.

### Scheduling

`~/.config/systemd/user/hermes-memory-rollup.{service,timer}`, weekly
(`OnCalendar=weekly`, `Persistent=true`, `RandomizedDelaySec`), running
`hermes memory promote` (no flag) so `memory_promotion.mode` governs behavior.
It therefore **starts in dry-run** — writing only reports until you set
`mode: apply`. Same install pattern as the existing `hermes-update-check.timer`.
Disable = `systemctl --user disable --now`.

## Testing (TDD, fake `llm_fn`)

1. Universal fact present in ≥1 thread → promoted to global; source entries removed.
2. Topic-specific fact → NOT promoted (LLM returns none for it); thread untouched.
3. Move/dedupe: promoted fact removed from all flagged threads; not duplicated in global.
4. Idempotency: second run with the fact already in global promotes nothing.
5. Char-limit: proposals exceeding budget → apply skips overflow and notes it; global never exceeds limit.
6. No-ops: zero threads / single thread / empty files → no writes, clean report.
7. Failure: `llm_fn` raises or returns invalid JSON → no writes, error in report, non-zero handled.
8. `apply_promotions` persists through `MemoryStore` (re-read from disk shows the change).
9. Config-gating: `mode: dry-run` (or unset) → proposals produced but NO writes even when the timer runs; `mode: apply` → writes occur; explicit `--apply`/`--dry-run` override config.

## Rollback

Per-run global backup + report make manual undo trivial. Disabling the feature =
stop/disable the timer; the module and CLI are inert unless invoked.
