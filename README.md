# Hermes Short-Term Memory (STM)

Shared short-term memory across all parallel Hermes CLI sessions, backed by SQLite.

```
Session A (terminal 1): "deploy the API to prod"  →  logged
Session B (terminal 2): /new                      →  sees "deploy the API to prod"
```

When `session_search` runs, matching STM entries are also returned — search your recent activity across all sessions from the normal `/session_search` command.

---

## Skills

| Skill | When to load |
|---|---|
| `short-term-mem-sqlite` | Day-to-day reference — DB schema, `stm.py` CLI, integration details |
| `short-term-mem-sqlite-recovery` | After Hermes update — re-injects `@stm_track` into `run_agent.py` |
| `short-term-mem-search` | After Hermes update — hooks STM into `session_search` tool |

---

## Quick Start

```bash
# Install (idempotent — safe to re-run)
./install.sh

# Apply the one-time run_agent.py patch
/hermes /skill short-term-mem-sqlite-recovery

# Verify
python3 ~/.hermes/scripts/stm.py append "test" "hello world"
python3 ~/.hermes/scripts/stm.py summaries 1
```

After Hermes updates, re-run the recovery skills:

```
/skill short-term-mem-sqlite-recovery
/skill short-term-mem-search
```

To remove entirely:

```bash
./uninstall.sh
```

---

## How It Works

### Cross-session context on `/new`

The `@stm_track` decorator on `run_conversation` in `run_agent.py`:

1. **Before** — `stm.py append <session_id> <prompt>` creates an entry (`status=executing`)
2. **After** — `stm.py update <id> <actions> <result> <status>` finalizes it
3. **New session** — empty `conversation_history` triggers `stm.py summaries 5`; results injected into `system_message`

```
[Session Context - recent cross-session activity]
  20260421_041957_a5e1e2: [success] memory -> Memory saved successfully...
  20260421_041955_93bd96: [success] search_files, skill_view -> short term memory working...
```

### `session_search` integration

The patched `session_search_tool.py` also queries `stm.db` for each search and appends `stm_matches` to the output — so every session search automatically surfaces relevant recent STM entries alongside transcript matches.

### Design

- **WAL mode** — safe concurrent reads/writes across parallel sessions
- **Fail silent** — `stm.py` errors are caught; never crashes the agent
- **Global FIFO purge** — when `TOTAL_CAP` (500) exceeded, oldest entries purged regardless of session
- **subprocess isolation** — `stm.py` runs outside the agent's process

---

## `stm.py` CLI

```bash
# Log a new prompt (before action) — returns id:<rowid>
stm.py append "session_id" "deploy to prod"
# → id:312

# Finalize entry (after action)
stm.py update 312 "terminal, kubectl" "all pods healthy" "success"
# → ok

# Scan recent entries across all sessions
stm.py scan --raw 3 --scan 5

# Scan entries for a specific session
stm.py scan --session "20260421020102_abc123"

# Count total entries
stm.py count

# Get summaries for new session injection (JSON)
stm.py summaries 5
```

### Environment overrides

```bash
STM_DB_PATH=/path/to/custom.db   # default: ~/.hermes/sessions/stm.db
STM_TOTAL_CAP=600                # default: 500
STM_RAW_CAP=20                   # default: 15
STM_SCAN_CAP=50                  # default: 40
STM_PURGE_CAP=50                 # default: 50
STM_DEBUG=1                      # verbose stderr
```

---

## Database Schema

```sql
CREATE TABLE entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,       -- e.g. "20260421020102_abc123"
    prompt     TEXT    NOT NULL,       -- what the user asked
    actions    TEXT    DEFAULT "",
    result     TEXT    DEFAULT "",
    status     TEXT    DEFAULT "executing",  -- executing|success|partial|failed
    timestamp  TEXT    NOT NULL         -- ISO8601
);
```

---

## Files

```
short-term-memory/
├── install.sh                                  # Idempotent install script
├── uninstall.sh                                # Remove skills, scripts, and optionally DB
├── README.md
├── LICENSE                                     # MIT
├── short-term-mem-sqlite/
│   ├── SKILL.md
│   └── scripts/stm.py                          # SQLite CRUD CLI
├── short-term-mem-sqlite-recovery/
│   └── SKILL.md                                # run_agent.py patch
└── short-term-mem-search/
    ├── SKILL.md                                # session_search patch
    └── scripts/short_term_mem_search.py        # session_search query script
```

---

## Uninstall

```bash
./uninstall.sh
```
