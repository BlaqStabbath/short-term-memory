# Hermes Short-Term Memory (STM)

SQLite-backed short-term memory for the Hermes AI Agent. Tracks prompts, actions, and results across all parallel CLI sessions — enabling new sessions to recall what happened in other sessions.

---

## Overview

Hermes runs multiple independent CLI sessions simultaneously. Without shared memory, each session is siloed — unable to see what the user was doing in another terminal window moments ago.

**Short-Term Memory (STM)** solves this with a single SQLite database (`stm.db`) that all sessions write to. When you start a fresh session or type `/new`, Hermes scans recent entries and injects them as context — so you can pick up work from a parallel session without re-explaining.

```
Session A (terminal 1): "deploy the API to prod"  →  logged
Session B (terminal 2): /new                      →  sees "deploy the API to prod" in context
```

---

## Architecture

```
~/.hermes/
├── sessions/
│   └── stm.db        # SQLite WAL database (all sessions share)
└── scripts/
    └── stm.py             # Single CLI tool: append / update / scan / summaries
```

### Database Schema

```sql
CREATE TABLE entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,       -- e.g. "20260421020102_abc123"
    prompt     TEXT    NOT NULL,       -- what the user asked
    actions    TEXT    DEFAULT "",      -- tools that were called
    result     TEXT    DEFAULT "",      -- outcome summary
    status     TEXT    DEFAULT "executing",  -- executing|success|partial|failed
    timestamp  TEXT    NOT NULL         -- ISO8601
);
```

### Constants

| Constant   | Default | Description                                      |
|------------|---------|--------------------------------------------------|
| `TOTAL_CAP` | 500    | Max entries before global purge fires             |
| `RAW_CAP`   | 15     | Entries fully injected into new session context   |
| `SCAN_CAP`  | 40     | Entries scanned for LLM summarization (after raw)|
| `PURGE_CAP` | 50     | Entries purged when `TOTAL_CAP` exceeded          |

Override via environment variables:

```bash
STM_DB_PATH=/absolute/path/to/custom.db  # absolute path supported, or relative (default: stm.db in ~/.hermes/sessions/)
STM_TOTAL_CAP=600      # max entries before purge (default: 500)
STM_RAW_CAP=20         # entries fully injected into new session context (default: 15)
STM_SCAN_CAP=50        # entries scanned for summarization (default: 40)
STM_PURGE_CAP=50      # entries purged when TOTAL_CAP exceeded (default: 50)
STM_DEBUG=1            # enables verbose stderr output
```

Example — run with custom database path:
```bash
STM_DB_PATH=/tmp/prod-stm.db python3 ~/.hermes/scripts/stm.py summaries 5
```

---

## stm.py Command Reference

```bash
# Log a new prompt (BEFORE action) — returns id:<rowid>
stm.py append "session_id" "deploy to prod"
# → id:312

# Finalize entry (AFTER action)
stm.py update 312 "terminal, kubectl" "all pods healthy" "success"
# → ok

# Scan recent entries across all sessions
stm.py scan --raw 3 --scan 5

# Scan entries for a specific session
stm.py scan --session "20260421020102_abc123"

# Count total entries
stm.py count

# Get summaries for new session context injection (returns JSON)
stm.py summaries 5
```

---

## Installation

### Prerequisites

- Python 3.8+
- Hermes agent (any recent version)
- `~/.hermes/scripts/` directory writable

### Step 1 — Copy the skill

Copy `short-term-memory/` into your Hermes skills directory:

```bash
cp -r short-term-memory/ ~/.hermes/skills/
```

### Step 2 — Install the script

Symlink or copy `stm.py` to your scripts directory:

```bash
mkdir -p ~/.hermes/scripts
ln -sf ~/.hermes/skills/short-term-memory/short-term-mem-sqlite/scripts/stm.py \
       ~/.hermes/scripts/stm.py
chmod +x ~/.hermes/scripts/stm.py
```

Verify:
```bash
python3 ~/.hermes/scripts/stm.py count
# → 0  (fresh install)
```

### Step 3 — Apply the run_agent.py patch (one-time, after Hermes updates)

After any Hermes version update, `run_agent.py` is overwritten and the decorator must be replaced. Run the recovery skill:

```
# In Hermes, trigger the recovery skill:
short-term-mem-sqlite recovery
```

Or apply manually — see `short-term-mem-sqlite-recovery/SKILL.md` for the 3-step patch procedure.

### Step 4 — Verify end-to-end

```bash
# Log a test entry
python3 ~/.hermes/scripts/stm.py append "test_session" "hello world"
# → id:1

# Retrieve it
python3 ~/.hermes/scripts/stm.py summaries 1
# → JSON with the test entry

# In a new Hermes session, type /new — you should see cross-session context
```

---

## How It Works

### Lifecycle of a tracked prompt

1. **User sends prompt** → `stm.py append <session_id> <prompt>` creates entry with `status=executing`
2. **Agent works** → tools are called, decorated with `@stm_track`
3. **Agent finishes** → entry updated with `actions`, `result`, and `status`
4. **New session starts** → `@stm_track` detects empty `conversation_history`, calls `stm.py summaries 5`, injects results into `system_message`

### Cross-session injection format

```
[Session Context - recent cross-session activity]
  20260421_041957_a5e1e2: [success] memory -> Memory saved successfully...
  20260421_041955_93bd96: [success] search_files, skill_view, execute_code -> short term memory working...
```

### Key design decisions

- **WAL mode** — SQLite Write-Ahead Logging enables safe concurrent reads/writes across parallel sessions with no locking conflicts
- **Fail silent** — `stm.py` errors are caught and ignored; never crashes the agent
- **Rowid stability** — `id` from `append` is used for `update`; stable within a single prompt lifecycle
- **Global purge** — when `TOTAL_CAP` exceeded, oldest entries purged regardless of session (FIFO)
- **subprocess calls** — `stm.py` is called via `subprocess.run()` from within `run_agent.py`, keeping DB operations isolated from the agent's process

---

## Files

```
short-term-memory/
├── README.md                               # This file
├── LICENSE                                 # MIT License
├── short-term-mem-sqlite/
│   ├── SKILL.md                            # STM core skill (usage + integration)
│   └── scripts/stm.py                       # SQLite CRUD CLI tool
└── short-term-mem-sqlite-recovery/
    └── SKILL.md                            # Post-update patch procedure
```

---

## Uninstall

```bash
# Remove the script
rm ~/.hermes/scripts/stm.py

# Remove skills (restore run_agent.py first if Hermes hasn't been updated since)
cd ~/.hermes/hermes-agent && git checkout run_agent.py

# Remove skill directory
rm -rf ~/.hermes/skills/short-term-memory/

# Optionally remove the DB (all session history will be lost)
rm ~/.hermes/sessions/stm.db
```

---

## License

**MIT License** — See `LICENSE` file.

Chosen because:
- Permissive: anyone can use, modify, distribute, even in commercial projects
- Low overhead: no attribution requirements beyond keeping the copyright notice
- Industry standard: widely understood, no license compatibility concerns
- Easy to change later if needed (MIT → Apache 2.0, etc.)
