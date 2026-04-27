#!/usr/bin/env python3
"""
stm.py — Short-Term Memory via SQLite for Hermes parallel sessions.
Maintains rolling short-term memory of recent events across all sessions.

Usage: stm.py <command> [args]

Constants (can override via env):
  TOTAL_CAP  = 500   — max entries before purge
  RAW_CAP    = 5    — entries fully injected as-is (no summarization)
  SCAN_CAP   = 45   — older entries scanned for LLM summarization (after raw)
  PURGE_CAP  = 50   — entries purged when TOTAL_CAP exceeded

Commands:
  append <session_id> <prompt>         → adds entry (BEFORE action), returns id:<rowid>
  update <id> <actions> <result> <status>  → updates entry (AFTER action)
  scan [--raw N] [--scan N] [--session SESSION_ID]  → scan entries
  count                                → total entry count
  summaries [limit]                   → two-tier dict {recent, older} for session injection:
                                          recent: up to RAW_CAP entries (injected as-is)
                                          older:  up to SCAN_CAP entries (LLM-summarized then injected)

Status values: executing | success | partial | failed
"""

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
_db_name = os.environ.get("STM_DB_PATH", "stm.db")
DB_PATH = Path(_db_name) if Path(_db_name).is_absolute() \
          else Path.home() / ".hermes" / "sessions" / _db_name
TOTAL_CAP  = int(os.environ.get("STM_TOTAL_CAP",  "500"))
RAW_CAP    = int(os.environ.get("STM_RAW_CAP",    "5"))
SCAN_CAP   = int(os.environ.get("STM_SCAN_CAP",   "45"))
PURGE_CAP  = int(os.environ.get("STM_PURGE_CAP",  "50"))
DEBUG      = os.environ.get("STM_DEBUG", "") == "1"


def _debug(msg: str) -> None:
    if DEBUG:
        print(f"[stm DEBUG] {msg}", file=sys.stderr)


# ── Schema ───────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    prompt     TEXT    NOT NULL,
    actions    TEXT    DEFAULT "",
    result     TEXT    DEFAULT "",
    status     TEXT    DEFAULT "executing",
    timestamp  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_id ON entries(session_id);
CREATE INDEX IF NOT EXISTS idx_timestamp  ON entries(timestamp);
"""


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


# ── Core operations ───────────────────────────────────────────────────────────

def append_entry(session_id: str, prompt: str) -> int:
    """
    Append a new entry (status=executing) BEFORE action.
    Returns the SQLite rowid so it can be updated later.
    Triggers purge if TOTAL_CAP exceeded.
    """
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        cur = conn.execute(
            "INSERT INTO entries (session_id, prompt, actions, result, status, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, prompt, "", "", "executing", now)
        )
        rowid = cur.lastrowid
        conn.commit()
        _debug(f"append: id={rowid} session={session_id} prompt={prompt[:60]}")

        # Purge if over TOTAL_CAP
        total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        if total > TOTAL_CAP:
            purged = conn.execute(
                "DELETE FROM entries WHERE id IN ("
                "  SELECT id FROM entries ORDER BY timestamp ASC LIMIT ?"
                ")",
                (PURGE_CAP,)
            ).rowcount
            conn.commit()
            _debug(f"purge: removed {purged} oldest entries (total was {total})")

        return rowid
    finally:
        conn.close()


def update_entry(entry_id: int, actions: str, result: str, status: str) -> None:
    """Update an existing entry's fields by rowid (AFTER action)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE entries SET actions=?, result=?, status=? WHERE id=?",
            (actions, result, status, entry_id)
        )
        conn.commit()
        _debug(f"update: id={entry_id} [{status}] {actions[:60]}")
    finally:
        conn.close()


def scan_entries(raw_limit: int = None, scan_limit: int = None,
                 session_id: str = None) -> list[dict]:
    """
    Scan entries with optional filters.
    If session_id given: returns last N entries for that session.
    Otherwise: returns last N entries across all sessions (raw first, then scan).
    """
    conn = get_db()
    try:
        rows = []
        if session_id:
            cur = conn.execute(
                "SELECT id, session_id, prompt, actions, result, status, timestamp "
                "FROM entries WHERE session_id=? ORDER BY timestamp DESC LIMIT ?",
                (session_id, raw_limit or scan_limit or RAW_CAP)
            )
            rows = cur.fetchall()
        elif raw_limit or scan_limit:
            # Raw entries first
            if raw_limit:
                cur = conn.execute(
                    "SELECT id, session_id, prompt, actions, result, status, timestamp "
                    "FROM entries ORDER BY timestamp DESC LIMIT ?",
                    (raw_limit,)
                )
                rows.extend(cur.fetchall())
            # Then scan entries (after raw)
            if scan_limit:
                offset = raw_limit or 0
                cur = conn.execute(
                    "SELECT id, session_id, prompt, actions, result, status, timestamp "
                    "FROM entries ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (scan_limit, offset)
                )
                rows.extend(cur.fetchall())
        else:
            cur = conn.execute(
                "SELECT id, session_id, prompt, actions, result, status, timestamp "
                "FROM entries ORDER BY timestamp DESC LIMIT ?",
                (RAW_CAP,)
            )
            rows = cur.fetchall()

        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_summaries(limit: int = 5) -> dict:
    """
    NEW SESSION: return entries in two tiers for context injection.

    Tier 1 — recent entries (up to RAW_CAP): injected as-is.
    Tier 2 — older entries (up to SCAN_CAP beyond tier 1): LLM-summarized
              before injection to compress the context window.

    Returns a dict so the caller (decorator) can handle each tier appropriately.
    """
    conn = get_db()
    try:
        # Tier 1: most recent entries, as-is
        recent_rows = conn.execute(
            "SELECT id, session_id, prompt, actions, result, status, timestamp "
            "FROM entries ORDER BY timestamp DESC LIMIT ?",
            (RAW_CAP,)
        ).fetchall()

        # Tier 2: next SCAN_CAP entries after tier 1 — for LLM summarization
        older_rows = conn.execute(
            "SELECT id, session_id, prompt, actions, result, status, timestamp "
            "FROM entries ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (SCAN_CAP, RAW_CAP)
        ).fetchall()

        return {
            "recent": [_row_to_dict(r) for r in recent_rows],
            "older":  [_row_to_dict(r) for r in older_rows],
        }
    finally:
        conn.close()


def entry_count() -> int:
    conn = get_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    finally:
        conn.close()


def _row_to_dict(row: tuple) -> dict:
    return {
        "id":        row[0],
        "session_id": row[1],
        "prompt":    row[2],
        "actions":   row[3],
        "result":    row[4],
        "status":    row[5],
        "timestamp": row[6],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "append":
        session_id = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        prompt     = sys.argv[3] if len(sys.argv) > 3 else "(no prompt)"
        rowid = append_entry(session_id, prompt)
        print(f"id:{rowid}")

    elif cmd == "update":
        entry_id = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        actions  = sys.argv[3] if len(sys.argv) > 3 else ""
        result   = sys.argv[4] if len(sys.argv) > 4 else ""
        status   = sys.argv[5] if len(sys.argv) > 5 else "success"
        update_entry(entry_id, actions, result, status)
        print("ok")

    elif cmd == "scan":
        raw_limit   = None
        scan_limit  = None
        session_id  = None

        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--raw" and i+1 < len(args):
                raw_limit = int(args[i+1]); i += 2
            elif args[i] == "--scan" and i+1 < len(args):
                scan_limit = int(args[i+1]); i += 2
            elif args[i] == "--session" and i+1 < len(args):
                session_id = args[i+1]; i += 2
            else:
                i += 1

        entries = scan_entries(raw_limit=raw_limit, scan_limit=scan_limit,
                               session_id=session_id)
        if not entries:
            print("(no entries)")
        else:
            for e in entries:
                print(f"### id={e['id']} [{e['status']}] session={e['session_id']}")
                print(f"**Prompt:** {e['prompt']}")
                print(f"**Actions:** {e['actions'] or '(pending)'}")
                print(f"**Result:** {e['result'] or '(pending)'}")
                print()

    elif cmd == "count":
        print(entry_count())

    elif cmd == "summaries":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        summaries = get_summaries(limit)
        # Always include both tiers for the decorator
        print(json.dumps(summaries, indent=2))

    else:
        print(__doc__)
