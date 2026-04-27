---
name: short-term-mem-search
description: Hook STM rolling context (SQLite) into session_search — patch session_search_tool.py after Hermes updates to add import + output injection for short_term_mem_search
triggers:
  - after hermes update
  - stm not in session_search
  - session_search_tool patched
  - session search missing stm
  - hook stm into session search
related_skills:
  - short-term-mem-sqlite
---

## ⚠ Status: NOT YET APPLIED TO LIVE SYSTEM

The `session_search_tool.py` patch documented here has **not been deployed** to the live system. The core STM tracking and new-session context injection work fine without this skill. This skill exists as a reference implementation for when the patch is ready to be applied.

To apply manually:
```bash
/hermes /skill short-term-mem-search
```

# short-term-mem-search — Hook STM into session_search

After every Hermes update, `session_search_tool.py` is overwritten and loses the STM integration. This skill detects the current state and applies only the patches needed.

## What Gets Patched

Two patches in `session_search_tool.py`:

1. **Import line** — adds `from scripts.short_term_mem_search import search_short_term_mem_files`
2. **Output dict** — replaces bare `return json.dumps({...})` with output-dict pattern + `stm_matches` injection using `search_short_term_mem_files`

## Prerequisite: `scripts/short_term_mem_search.py`

The script must exist at `~/.hermes/scripts/short_term_mem_search.py`. If missing, it is recreated automatically (SQLite query against `stm.db`).

## Analysis — Always Run First

```python
from hermes_tools import search_files
from pathlib import Path

SEARCH_TOOL = "/home/blaq/.hermes/hermes-agent/tools/session_search_tool.py"
SCRIPT_PATH = Path.home() / ".hermes" / "scripts" / "short_term_mem_search.py"

def analyse():
    findings = {}

    # 1. Is import already present?
    r = search_files(pattern='from scripts.short_term_mem_search import', path=SEARCH_TOOL, output_mode='content')
    findings['import_present'] = bool(r['matches'])

    # 2. Is output dict pattern present?
    r = search_files(pattern='output = \\{', path=SEARCH_TOOL, output_mode='content')
    findings['output_dict_present'] = bool(r['matches'])

    # 3. Is stm_matches injection present?
    r = search_files(pattern='stm_matches = search_short_term_mem_files', path=SEARCH_TOOL, output_mode='content')
    findings['injection_present'] = bool(r['matches'])

    # 4. Does short_term_mem_search.py exist?
    findings['script_exists'] = SCRIPT_PATH.exists()

    print("=== Analysis Results ===")
    print(f"  import present:             {findings['import_present']}")
    print(f"  output dict present:         {findings['output_dict_present']}")
    print(f"  stm_matches injected:        {findings['injection_present']}")
    print(f"  scripts/short_term_mem_search.py: {findings['script_exists']}")
    status = 'FULLY PATCHED' if all(v for v in findings.values()) \
        else 'NEEDS PATCHING' if not findings['import_present'] \
        else 'PARTIALLY PATCHED'
    print(f"  status: {status}")
    return findings
```

## Patching — Content-Based, No Line Numbers

### Patch 1: Add import (only if missing)

```python
r = search_files(pattern='from scripts.short_term_mem_search import', path=SEARCH_TOOL, output_mode='content')
if not r['matches']:
    patch(path=SEARCH_TOOL,
          old_string="from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning\nMAX_SESSION_CHARS",
          new_string="from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning\nfrom scripts.short_term_mem_search import search_short_term_mem_files\nMAX_SESSION_CHARS")
    print("[PATCHED] added short_term_mem_search import")
else:
    print("[SKIP]    import already present")
```

### Patch 2: Inject into output dict (only if not already patched)

```python
r = search_files(pattern='output = \\{', path=SEARCH_TOOL, output_mode='content')
if r['matches']:
    print("[SKIP]    output dict already present (patched)")
else:
    patch(path=SEARCH_TOOL,
          old_string='''            summaries.append(entry)

        return json.dumps({
            "success": True,
            "query": query,
            "results": summaries,
            "count": len(summaries),
            "sessions_searched": len(seen_sessions),
        }, ensure_ascii=False)''',
          new_string='''            summaries.append(entry)

        output = {
            "success": True,
            "query": query,
            "results": summaries,
            "count": len(summaries),
            "sessions_searched": len(seen_sessions),
        }
        stm_matches = search_short_term_mem_files(query, limit)
        if stm_matches:
            output["stm_matches"] = stm_matches
        return json.dumps(output, ensure_ascii=False)''')
    print("[PATCHED] injected stm_matches into output dict")
```

### Script recreation (if missing)

```python
SCRIPT_CONTENT = """#!/usr/bin/env python3
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
"""
if not SCRIPT_PATH.exists():
    SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCRIPT_PATH.write_text(SCRIPT_CONTENT)
    print(f"[CREATED] {SCRIPT_PATH}")
else:
    print(f"[SKIP]    {SCRIPT_PATH} already exists")
```

## Full Apply Procedure

```python
from pathlib import Path
from hermes_tools import search_files, patch

SEARCH_TOOL = "/home/blaq/.hermes/hermes-agent/tools/session_search_tool.py"
SCRIPT_PATH = Path.home() / ".hermes" / "scripts" / "short_term_mem_search.py"

SCRIPT_CONTENT = """#!/usr/bin/env python3
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
"""

def analyse_and_patch():
    print("=== stm-session-search Patch ===\n")

    # Script recreation
    if not SCRIPT_PATH.exists():
        SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCRIPT_PATH.write_text(SCRIPT_CONTENT)
        print(f"[CREATED] {SCRIPT_PATH}")
    else:
        print(f"[SKIP]    {SCRIPT_PATH} already exists")

    # Patch 1: import
    r = search_files(pattern='from scripts.short_term_mem_search import', path=SEARCH_TOOL, output_mode='content')
    if not r['matches']:
        patch(path=SEARCH_TOOL,
              old_string="from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning\nMAX_SESSION_CHARS",
              new_string="from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning\nfrom scripts.short_term_mem_search import search_short_term_mem_files\nMAX_SESSION_CHARS")
        print("[PATCHED] added short_term_mem_search import")
    else:
        print("[SKIP]    import already present")

    # Patch 2: output dict injection
    r = search_files(pattern='output = \\{', path=SEARCH_TOOL, output_mode='content')
    if r['matches']:
        print("[SKIP]    output dict already present")
    else:
        patch(path=SEARCH_TOOL,
              old_string="            summaries.append(entry)\n\n        return json.dumps({\n            \"success\": True,\n            \"query\": query,\n            \"results\": summaries,\n            \"count\": len(summaries),\n            \"sessions_searched\": len(seen_sessions),\n        }, ensure_ascii=False)",
              new_string="            summaries.append(entry)\n\n        output = {\n            \"success\": True,\n            \"query\": query,\n            \"results\": summaries,\n            \"count\": len(summaries),\n            \"sessions_searched\": len(seen_sessions),\n        }\n        stm_matches = search_short_term_mem_files(query, limit)\n        if stm_matches:\n            output[\"stm_matches\"] = stm_matches\n        return json.dumps(output, ensure_ascii=False)")
        print("[PATCHED] injected stm_matches into output dict")

    print("\nDone. Restart Hermes for changes to take effect.")
```

## If Patches Fail After Hermes Update

1. Run `analyse_and_patch()` first
2. If import patch fails: search for the current import block:
   ```
   search_files(pattern='from agent.auxiliary_client', path=SEARCH_TOOL, output_mode='content')
   ```
3. If output-dict patch fails: the upstream code likely changed the return block — search for the return pattern:
   ```
   search_files(pattern='return json.dumps.*success.*results', path=SEARCH_TOOL, output_mode='content')
   ```
4. Build a unique `old_string` from the new context and patch manually
5. Update this skill with the new patterns
