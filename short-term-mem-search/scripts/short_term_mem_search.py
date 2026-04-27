#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path.home() / ".hermes" / "sessions" / "stm.db"

def search_short_term_mem_files(query: str, limit: int = 3) -> list[dict[str, Any]]:
    if not query or not query.strip():
        return []
    results = []
    q = query.lower()
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        rows = conn.execute(

            "SELECT id, session_id, prompt, actions, result, status, timestamp "
            "FROM entries ORDER BY id DESC LIMIT 100"
        ).fetchall()
        conn.close()
    except Exception:
        return []
    for row in rows:
        row_id, session_id, prompt, actions, result, status, timestamp = row
        prompt = prompt or ""
        actions = actions or ""
        result = result or ""
        status = status or ""
        if q in prompt.lower() or q in actions.lower() or q in result.lower():
            results.append({
                "id": row_id,
                "session_id": session_id,
                "prompt": prompt,
                "actions": actions,
                "result": result,
                "status": status,
            })
            if len(results) >= limit:
                return results
    return results

if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    matches = search_short_term_mem_files(query, limit)
    print(json.dumps({"query": query, "matches": matches, "count": len(matches)}, indent=2))
