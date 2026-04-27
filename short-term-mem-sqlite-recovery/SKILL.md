---
name: short-term-mem-sqlite-recovery
description: >
  Auto-loading approach for SQLite short-term memory — verifies correctness
  of decorator patch AND handles post-update recovery via idempotent patch script.
triggers:
  - after hermes update
  - patch stm
  - short-term mem patch
  - short-term-mem-sqlite recovery
  - stm auto-load
---

# short-term-mem-sqlite Recovery — Implementation (Apr 27 2026)

## Status: DEPLOYED

- `@stm_track` decorator applied to `run_agent.py` — ✓ live since Apr 27 2026
- `patch_stm_decorator.py` — ✓ in `~/.hermes/scripts/`
- `hermes-post-update-recovery` cron job — ✓ created (daily at 05:00, job_id: e0dde69b683a)
- Scripts: `stm.py` (symlink), `build_topic_index.py` (symlink) in `~/.hermes/scripts/` — ✓

---

## Architecture

```
run_agent.py::run_conversation()
    │
    └── @stm_track decorator
            │
            ├── NEW SESSION? → stm.py summaries + build_topic_index.py → inject system_message
            ├── BEFORE CALL  → stm.py append (prompt, status=executing)
            └── AFTER CALL   → stm.py update (actions, result, status)
```

Two layers:
1. **STM Tracking**: append/update via `stm.py` subprocess calls
2. **Context Injection**: TF-IDF topic index via `build_topic_index.py` on new sessions only

---

## Why Not a Plugin?

The `MemoryProvider` ABC has no hook that can:
- Detect a new session before the first API call (needs `conversation_history`)
- Inject into the system message for new sessions only

Plugin hooks (`system_prompt_block()`, `prefetch()`, `sync_turn()`) are called per-turn and don't have access to `conversation_history` as a signal for "new session". The decorator approach intercepts `run_conversation()` directly — this is the only place that has both.

---

## Auto-Recovery: `patch_stm_decorator.py`

**Location:** `~/.hermes/scripts/patch_stm_decorator.py`

**Modes:**
```bash
python3 ~/.hermes/scripts/patch_stm_decorator.py           # dry run
python3 ~/.hermes/scripts/patch_stm_decorator.py --apply   # patch if missing
python3 ~/.hermes/scripts/patch_stm_decorator.py --verify  # exit 0 if OK
```

**Is idempotent** — safe to re-run after Hermes updates. The cron job runs it daily.

The script:
1. Checks if `def stm_track(` and `@stm_track` are in `run_agent.py`
2. Adds `import functools` + `import subprocess` if missing
3. Inserts the decorator definition after the env-loading block
4. Applies `@stm_track` to `run_conversation`

---

## Cron Job: `hermes-post-update-recovery`

- **Job ID:** `e0dde69b683a`
- **Schedule:** Daily at 05:00
- **Action:** Runs `patch_stm_decorator.py --apply` then verifies scripts

---

## Verification

```bash
# Quick check — is decorator present?
python3 ~/.hermes/scripts/patch_stm_decorator.py --verify
# → "VERIFY: OK" or "VERIFY: MISSING"

# Manual patch
python3 ~/.hermes/scripts/patch_stm_decorator.py --apply

# Check stm.db is being written
python3 ~/.hermes/scripts/stm.py count
# Should increase after each CLI turn

# Check topic index works
python3 ~/.hermes/scripts/build_topic_index.py | head -10

# Check stm is logging
# stderr output from the decorator goes to wherever Hermes stderr goes
# Look for: "[stm] Injected N recent + M older (topic-indexed) entries:"
```

---

## Rollback

```bash
cd ~/.hermes/hermes-agent && git checkout run_agent.py
python3 ~/.hermes/scripts/patch_stm_decorator.py --apply  # re-apply if needed
```

---

## Key Files

| File | Location |
|------|----------|
| `run_agent.py` | `~/.hermes/hermes-agent/run_agent.py` |
| `patch_stm_decorator.py` | `~/.hermes/scripts/patch_stm_decorator.py` |
| `stm.py` | `~/.hermes/scripts/stm.py` → `skills/short-term-mem-sqlite/scripts/stm.py` |
| `build_topic_index.py` | `~/.hermes/scripts/build_topic_index.py` → same |
| `stm.db` | `~/.hermes/sessions/stm.db` |
| Cron job | Hermes cron scheduler (job_id: e0dde69b683a) |
