---
name: short-term-mem-sqlite
description: SQLite-backed short-term memory for Hermes — stores recent session events (prompts, actions, results) across parallel CLI sessions. Uses a single stm.db with WAL mode, configurable via STM_DB_PATH env var.
triggers:
  - short-term memory
  - stm
  - sqlite short term memory
  - short term mem sqlite
  - stm skill
---

# short-term-mem-sqlite — Session Context via SQLite

## Purpose

Maintain Hermes' awareness of recent events across all CLI sessions. On new CLI startup or `/new session`, recent entries are scanned and optionally summarized into context, enabling Hermes to pick up work from parallel or previous sessions.

## Database

`~/.hermes/sessions/stm.db` — SQLite with WAL mode. Override path with `STM_DB_PATH` env var.

```sql
CREATE TABLE entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,        -- e.g. "20260421020102_abc123"
    prompt     TEXT    NOT NULL,
    actions    TEXT    DEFAULT "",
    result     TEXT    DEFAULT "",
    status     TEXT    DEFAULT "executing",  -- executing|success|partial|failed
    timestamp  TEXT    NOT NULL               -- ISO8601
);
CREATE INDEX idx_session_id ON entries(session_id);
CREATE INDEX idx_timestamp  ON entries(timestamp);
```

## Constants

| Constant | Default | Description |
|----------|---------|-------------|
| `TOTAL_CAP` | 500 | Max entries before global purge |
| `RAW_CAP` | 15 | Entries fully injected into new session context |
| `SCAN_CAP` | 40 | Entries scanned for LLM summarization (after raw) |
| `PURGE_CAP` | 50 | Entries purged when TOTAL_CAP exceeded |

Override via env: `STM_TOTAL_CAP=600 python3 ~/.hermes/scripts/stm.py ...`

## Script: `~/.hermes/scripts/stm.py`

### Commands

- `append <session_id> <prompt>` → adds entry (status=executing), returns `id:<rowid>`
- `update <id> <actions> <result> <status>` → updates entry by rowid
- `scan [--raw N] [--scan N] [--session SESSION_ID]` → scan entries
- `count` → total entry count
- `summaries` → two-tier dict `{recent: [...], older: [...]}` for new session context injection:
    - `recent`: up to `RAW_CAP` entries (injected as-is)
    - `older`: up to `SCAN_CAP` entries (LLM-summarized before injection)

### Examples

```bash
# Log a new prompt (BEFORE action)
stm.py append "20260421_020102_abc123" "deploy to prod"
# Returns: id:312

# Update after action completes
stm.py update 312 "terminal, kubectl" "all pods healthy" "success"
# Returns: ok

# Scan last 3 raw + 5 scanned entries
stm.py scan --raw 3 --scan 5

# Scan all entries for a specific session
stm.py scan --session "20260421_020102_abc123"

# Get two-tier summaries (recent + older for LLM summarization)
stm.py summaries

# Entry count
stm.py count
```

## Integration with run_agent.py

The `@stm_track` decorator (embedded in run_agent.py after post-update recovery) handles append/update automatically:

- **BEFORE work**: `stm.py append <session_id> <prompt>` — entry created with status=executing
- **AFTER work**: `stm.py update <id> <actions> <result> <status>` — entry finalized

Session ID: use `self.session_id` from AIAgent. If unavailable, use "cli" as default.

On new session (empty conversation_history): `summaries` returns `{recent, older}`. The decorator:
1. Injects `recent` entries as-is (tier 1, up to RAW_CAP)
2. Calls `llm_summarize.py --key <api_key> --base-url <base_url> --model <model>` for tier 2 (older, up to SCAN_CAP)
   — credentials come from the agent's own `self.api_key`, `self.base_url`, `self.model`, so no env-probing needed

`llm_summarize.py` reads older entries directly from `stm.db` (offset=RAW_CAP, limit=SCAN_CAP) when no stdin is provided. Falls back to config/env when called without `--key/--base-url/--model` args.

Failures are silent — never crash the agent.

## Implementation Notes

- Uses SQLite WAL mode for safe concurrent access across sessions
- Purge is global (oldest entries purged regardless of session) when TOTAL_CAP exceeded
- `id` (rowid) is used for update — stable within the append→update window
- Prompts stored raw — no shlex or special quoting required
- `stm.py` is called via `subprocess.run()` from within `run_agent.py`, keeping DB operations isolated from the agent's process
