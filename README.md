# Hermes Short-Term Memory (STM)

**Shared rolling memory across all parallel Hermes CLI sessions — backed by SQLite, zero API cost.**

```
Session A (terminal 1): "deploy the API to prod"  →  logged to stm.db
Session B (terminal 2): /new                      →  sees "deploy to prod" in context
```

When you start a new session (`/new`) and your conversation history is empty, STM automatically injects context from recent sessions — so you never repeat work that's already been done.

---

## Install (one command)

```bash
cd ~/DEV/SRC/short-term-memory
./install.sh
```

That's it. `install.sh` symlinks skills and scripts into `~/.hermes/`, runs the `patch_stm_decorator.py` script to inject the `@stm_track` decorator into `run_agent.py`, and creates a daily cron job to re-apply the patch automatically after any Hermes update.

**Idempotent** — safe to re-run. The patch script checks before modifying.

To remove entirely:

```bash
./uninstall.sh
```

---

## What Gets Installed

| Component | Location | Purpose |
|-----------|---------|---------|
| `short-term-mem-sqlite` skill | `~/.hermes/skills/short-term-mem-sqlite/` | Day-to-day reference: `stm.py` CLI, DB schema |
| `short-term-mem-sqlite-recovery` skill | `~/.hermes/skills/short-term-mem-sqlite-recovery/` | Post-update recovery reference |
| `short-term-mem-search` skill | `~/.hermes/skills/short-term-mem-search/` | `session_search` integration (optional) |
| `stm.py` | `~/.hermes/scripts/stm.py` | SQLite CRUD CLI |
| `build_topic_index.py` | `~/.hermes/scripts/build_topic_index.py` | TF-IDF topic indexer for older entries |
| `patch_stm_decorator.py` | `~/.hermes/scripts/patch_stm_decorator.py` | Idempotent patch script for `run_agent.py` |
| `stm.db` | `~/.hermes/sessions/stm.db` | SQLite database (WAL mode) |
| Cron job `hermes-post-update-recovery` | Hermes scheduler | Daily 05:00 UTC re-patch |

---

## How It Works

### The Two Problems STM Solves

1. **Tracking** — Every turn across all sessions writes `(session_id, prompt, actions, result, status)` to `stm.db` so nothing is forgotten.
2. **Context injection** — When a new session starts (empty `conversation_history`), recent entries are injected into the system message so the agent has cross-session awareness.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  run_agent.py :: run_conversation(self, system_message, ...)   │
│                           │                                     │
│                    @stm_track decorator                          │
│                           │                                     │
│         ┌─────────────────┼─────────────────┐                   │
│         ▼                 ▼                 ▼                   │
│   [NEW SESSION?]    [BEFORE call]    [AFTER call]              │
│         │                 │                 │                   │
│   stm.py summaries  →  stm.py append   stm.py update           │
│   + build_topic_index.py                  (status, result)      │
│         │                                                       │
│         ▼                                                       │
│   Inject topic-indexed context into system_message             │
└─────────────────────────────────────────────────────────────────┘

                           │
                           ▼ (writes to ~/.hermes/sessions/stm.db)
                    ┌──────────────┐
                    │   stm.db     │  WAL mode — safe concurrent R/W
                    │  entries     │
                    └──────────────┘
```

### The `@stm_track` Decorator

Applied to `run_conversation()` in `~/.hermes/hermes-agent/run_agent.py`. It wraps every LLM call:

- **Before the call** — `stm.py append <session_id> <prompt>` → creates entry with `status=executing`
- **After the call** — `stm.py update <id> <actions> <result> <status>` → finalizes entry
- **New session** (empty `conversation_history`) — injects two-tier context (see below)

### Two-Tier Context Injection

When `conversation_history` is empty, `stm.py summaries` returns:

**Tier 1 — Recent (up to `RAW_CAP=5`):** Injected as-is — full entries with session ID, prompt, actions, result, and status.

**Tier 2 — Older (up to `SCAN_CAP=45`):** Passed to `build_topic_index.py` which runs TF-IDF bigram extraction across all prompts, groups by topic, and returns a compact **topic index** — no LLM needed. Example:

```
### Earlier Sessions — Topic Index

  [angular routing]  sessions: 904, 902  e.g. "[SYSTEM: The user has invoked..."
  [hermes backup]    sessions: 901, 899, 897  e.g. "Run the obsidian vault backup..."
  [spring boot test] sessions: 888, 884  e.g. "Fix the @DataJpaTest..."

Use session_search tool to retrieve full entries by ID when needed.
```

The LLM can call `session_search` with an entry ID to get the full details.

### Why TF-IDF Instead of LLM Summarization?

LLM summarization costs API credits and introduces latency. TF-IDF bigram scoring is:
- **Deterministic** — same input always produces same output
- **Zero cost** — no API call
- **Fast** — runs in milliseconds as a subprocess
- **Sufficient** — the LLM can use `session_search` to retrieve full entries on demand

### Auto-Recovery After Hermes Updates

Hermes updates overwrite `run_agent.py`, which removes the `@stm_track` decorator. Two safeguards:

1. **Daily cron** — `hermes-post-update-recovery` (job ID: `e0dde69b683a`) runs `patch_stm_decorator.py --apply` every day at 05:00 UTC. Safe and idempotent.
2. **Idempotent patch script** — `patch_stm_decorator.py` checks if the decorator is already present before modifying anything.

To manually verify or re-apply:

```bash
# Verify (read-only)
python3 ~/.hermes/scripts/patch_stm_decorator.py --verify
# → "VERIFY: OK" or "VERIFY: MISSING"

# Apply (safe to re-run)
python3 ~/.hermes/scripts/patch_stm_decorator.py --apply
```

---

## `stm.py` CLI Reference

```bash
# Log BEFORE an action — returns id:<rowid>
stm.py append "session_id" "deploy to prod"
# → id:312

# Finalize AFTER an action
stm.py update 312 "terminal, kubectl" "all pods healthy" "success"
# → ok

# Scan recent entries (all sessions)
stm.py scan --raw 3 --scan 5

# Scan specific session
stm.py scan --session "20260421_041957_a5e1e2"

# Total entry count
stm.py count
# → 467 entries

# Get summaries for context injection (used by decorator internally)
stm.py summaries
# → JSON: {"recent": [...], "older": [...]}  (recent=5 as-is, older=45 topic-indexed)
```

### Status Values

| Status | Meaning |
|--------|---------|
| `executing` | Entry created, action in progress |
| `success` | Action completed successfully |
| `partial` | Completed with warnings or partial result |
| `failed` | Action failed |

---

## Environment Variables

All have sensible defaults — override by setting before running Hermes or in your shell profile.

| Variable | Default | Description |
|----------|---------|-------------|
| `STM_DB_PATH` | `~/.hermes/sessions/stm.db` | SQLite database path |
| `STM_TOTAL_CAP` | `500` | Max entries before FIFO purge |
| `STM_RAW_CAP` | `5` | Recent entries injected as-is |
| `STM_SCAN_CAP` | `45` | Older entries topic-indexed |
| `STM_PURGE_CAP` | `50` | Entries purged when cap exceeded |
| `STM_DEBUG` | `""` | Set `"1"` for verbose stderr |

---

## Database Schema

```sql
CREATE TABLE entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,       -- e.g. "20260421_041957_a5e1e2"
    prompt     TEXT    NOT NULL,       -- what the user asked
    actions    TEXT    DEFAULT "",
    result     TEXT    DEFAULT "",
    status     TEXT    DEFAULT "executing",
    timestamp  TEXT    NOT NULL        -- ISO8601
);
-- Index for session-scoped queries
CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session_id);
-- Index for time-ordered scans
CREATE INDEX IF NOT EXISTS idx_entries_id ON entries(id);
```

---

## Project File Structure

```
short-term-memory/
├── README.md
├── LICENSE                          # MIT
├── install.sh                       # Symlink skills + auto-patch run_agent.py
├── uninstall.sh                     # Remove everything cleanly
│
├── short-term-mem-sqlite/           # Core skill
│   ├── SKILL.md
│   └── scripts/
│       ├── stm.py                   # SQLite CRUD CLI
│       └── build_topic_index.py     # TF-IDF topic indexer (replaces llm_summarize.py)
│
├── short-term-mem-sqlite-recovery/  # Recovery skill
│   └── SKILL.md                     # Documents patch script + cron job
│
└── short-term-mem-search/           # Optional session_search integration
    ├── SKILL.md
    └── scripts/
        └── short_term_mem_search.py # Appends stm_matches to session_search output
```

---

## `session_search` Integration (Optional)

The `short-term-mem-search` skill appends STM entries to every `session_search` result, so you get short-term memory matches alongside transcript matches automatically.

**Status: not yet applied to live system.** The skill exists and is documented, but the `session_search_tool.py` patch has not been deployed. To apply manually:

```bash
/hermes /skill short-term-mem-search
```

This is optional — STM tracking and new-session context injection work fine without it.

---

## Troubleshooting

### "VERIFY: MISSING" after running `patch_stm_decorator.py --verify`

The decorator was removed by a Hermes update. The daily cron will fix this at 05:00 UTC. To fix immediately:

```bash
python3 ~/.hermes/scripts/patch_stm_decorator.py --apply
```

### `stm.db` not growing

Check the decorator is applied:
```bash
python3 ~/.hermes/scripts/patch_stm_decorator.py --verify
```

Check `stm.py` works directly:
```bash
python3 ~/.hermes/scripts/stm.py count
```

If that works but entries aren't being written, the decorator might not be catching the right `run_conversation` method. Check the skill's verification section.

### Entries are being purged

`TOTAL_CAP=500` by default. When exceeded, oldest `PURGE_CAP=50` entries are deleted. Lower the caps via environment:

```bash
STM_TOTAL_CAP=1000 STM_PURGE_CAP=100 hermes
```

Or disable purge by setting both to very high values.

### Topic index is empty or poor quality

The TF-IDF indexer needs at least `SCAN_CAP=45` entries in the DB to build meaningful topics. With a fresh DB you'll only see recent entries (Tier 1). As the DB grows the topic index becomes richer.

To force a rebuild:
```bash
python3 ~/.hermes/scripts/build_topic_index.py
```

### `patch_stm_decorator.py` fails

If the script can't find the right insertion points in `run_agent.py`, Hermes may have significantly changed its structure. Run the recovery skill manually:

```bash
/hermes /skill short-term-mem-sqlite-recovery
```

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Decorator over plugin** | `BuiltinMemoryProvider` is hardcoded in `run_agent.py`. The plugin system only supports `holographic`/`mem0` backends — no hook for new-session detection or system message injection. Decorator is the only viable path. |
| **TF-IDF over LLM summarization** | Zero API cost, deterministic, millisecond-speed. The LLM can retrieve full entries via `session_search` when needed. |
| **WAL mode** | Allows concurrent reads from parallel sessions without locking. |
| **Subprocess isolation** | `stm.py` and `build_topic_index.py` run as subprocesses — failures are caught silently and never crash the agent. |
| **Idempotent patch script** | Safe to re-run after any Hermes update without risk of duplicate definitions. |
| **Daily cron over immediate webhook** | No Hermes update webhook exists. Daily cron at 05:00 UTC catches any update that happened during the day. |
