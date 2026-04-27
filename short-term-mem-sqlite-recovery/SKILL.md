---
name: short-term-mem-sqlite-recovery
description: Patch run_agent.py after Hermes update — injects the stm_track decorator for SQLite-backed short-term memory. Handles cross-session context injection on new sessions and logs all prompts/results to stm.db.
triggers:
  - after hermes update
  - patch stm
  - short-term mem patch
  - short-term-mem-sqlite recovery
---

# short-term-mem-sqlite Recovery — Post-Update Decorator Patch

After Hermes updates, `run_agent.py` is overwritten and the `stm_track` decorator must be re-injected.

## Pre-Update Checklist

Before patching, verify:
1. `~/.hermes/scripts/stm.py` exists and is symlinked correctly
2. `~/.hermes/scripts/build_topic_index.py` exists and is symlinked correctly
3. `import subprocess` is present in run_agent.py (Step 1 below adds it if missing)

## Patch Applied (3 steps)

### Step 1 — Ensure `import subprocess` is present

Check that `import subprocess` exists near the top of run_agent.py (around line 38).

If missing, find:
```python
import functools
import threading
```

Replace with:
```python
import functools
import subprocess
import threading
```

### Step 2 — Add the `stm_track` decorator (after imports)

Find the line:
```python
import functools
```

Insert the following new decorator block after it:

```python
# ── STM TRACKING DECORATOR ──────────────────────────────────────────────────────
# Short-Term Memory via SQLite — logs prompts/results to stm.db
# for cross-session awareness on /new sessions.
#
# Two-tier injection on new sessions:
#   Tier 1 — recent entries (up to RAW_CAP): injected as-is
#   Tier 2 — older entries (up to SCAN_CAP): topic-indexed (TF-IDF bigrams, NO LLM)
# ─────────────────────────────────────────────────────────────────────────────────
def stm_track(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        session_id = getattr(self, "session_id", None) or "cli"

        # ── NEW SESSION: two-tier cross-session context injection ─────────────
        _hist = kwargs.get("conversation_history")
        if _hist is None or len(_hist) == 0:
            try:
                # Get two-tier summaries from stm.py
                res = subprocess.run(
                    [sys.executable,
                     str(Path.home() / ".hermes" / "scripts" / "stm.py"),
                     "summaries"],
                    capture_output=True, text=True, timeout=10,
                    env={**os.environ, "STM_DEBUG": "1"}
                )
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    recent = data.get("recent", [])
                    older  = data.get("older",  [])

                    ctx_lines = ["[Session Context - recent cross-session activity]"]

                    # Tier 1: recent entries — injected as-is
                    for s in recent:
                        actions = s.get("actions") or "-"
                        result  = s.get("result")  or "-"
                        status  = s.get("status")  or "-"
                        sid     = s.get("session_id", "?")
                        ctx_lines.append(
                            "  " + sid + ": [" + status + "] "
                            + actions + " -> " + result[:120]
                        )

                    # Tier 2: older entries — topic-indexed via build_topic_index.py.
                    # No LLM, no API key needed. Reads from stm.db directly.
                    if older:
                        try:
                            idx_res = subprocess.run(
                                [sys.executable,
                                 str(Path.home() / ".hermes" / "scripts" / "build_topic_index.py")],
                                capture_output=True, text=True, timeout=10,
                                env={**os.environ, "STM_DEBUG": "1"}
                            )
                            if idx_res.returncode == 0 and idx_res.stdout.strip():
                                ctx_lines.append("")
                                ctx_lines.append("  [Earlier sessions — topic index]")
                                ctx_lines.append("  " + idx_res.stdout.strip())
                        except Exception:
                            pass  # topic indexing optional — fail silent

                    inject_msg = chr(10).join(ctx_lines)
                    _orig_sys = kwargs.get("system_message") or ""
                    kwargs = dict(kwargs)
                    kwargs["system_message"] = (
                        (_orig_sys + chr(10) + chr(10) + inject_msg)
                        if _orig_sys else inject_msg
                    )
                    import sys as _sys
                    total = len(recent) + len(older)
                    print(f"[stm] Injected {len(recent)} recent + {len(older)} older "
                          f"(topic-indexed) cross-session entries: "
                          + "; ".join(s.get("session_id","?") for s in recent[:3]),
                          file=_sys.stderr, flush=True)
            except Exception:
                pass

        import sys as _sys
        user_message = args[0] if args else kwargs.get("user_message", "")
        entry_id = None
        try:
            print(f"[stm] append: session={session_id} prompt={user_message[:80]}",
                  file=_sys.stderr, flush=True)
            res = subprocess.run(
                [sys.executable, str(Path.home() / ".hermes" / "scripts" / "stm.py"),
                 "append", session_id, user_message[:500]],
                capture_output=True, text=True, timeout=5,
                env={**os.environ, "STM_DEBUG": "1"}
            )
            if res.returncode == 0 and res.stdout.startswith("id:"):
                entry_id = int(res.stdout.strip().split(":")[1])
                print(f"[stm] append: entry_id={entry_id}", file=_sys.stderr, flush=True)
        except Exception as e:
            print(f"[stm] append error: {e}", file=_sys.stderr, flush=True)
            entry_id = None

        result = fn(self, *args, **kwargs)

        if entry_id is not None:
            tool_names = []
            for msg in result.get("messages", []):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        name = tc.get("function", {}).get("name", "")
                        if name:
                            tool_names.append(name)
            actions_str = ", ".join(tool_names) if tool_names else "no tools"
            result_str  = (result.get("final_response") or "")[:300].replace(chr(92)+"n", " ")
            completed   = result.get("completed", False)
            interrupted = result.get("interrupted", False)
            status_str  = "success" if completed and not interrupted \
                          else ("failed" if interrupted else "partial")
            try:
                print(f"[stm] update: id={entry_id} [{status_str}] "
                      f"actions={actions_str[:60]}", file=_sys.stderr, flush=True)
                subprocess.run(
                    [sys.executable, str(Path.home() / ".hermes" / "scripts" / "stm.py"),
                     "update", str(entry_id),
                     actions_str[:200], result_str[:300], status_str],
                    capture_output=True, text=True, timeout=5,
                    env={**os.environ, "STM_DEBUG": "1"}
                )
            except Exception as e:
                print(f"[stm] update error: {e}", file=_sys.stderr, flush=True)
        return result
    return wrapper
```

### Step 3 — Apply `@stm_track` to `run_conversation` (line ~8623)

Find:
```python
    def run_conversation(self, user_message: str, system_message: str = None,
```

Add `@stm_track` directly above it:
```python
    @stm_track
    def run_conversation(self, user_message: str, system_message: str = None,
```

## Full `stm_track` Decorator (two-tier version)

```python
# ── STM TRACKING DECORATOR ──────────────────────────────────────────────────────
def stm_track(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        import sys as _sys, os
        _sess_id = self.session_id or "cli"

        # ── NEW SESSION: two-tier cross-session context injection ─────────────
        _hist = kwargs.get("conversation_history")
        if _hist is None or len(_hist) == 0:
            try:
                res = subprocess.run(
                    [sys.executable,
                     str(Path.home() / ".hermes" / "scripts" / "stm.py"),
                     "summaries"],
                    capture_output=True, text=True, timeout=10,
                    env={**os.environ, "STM_DEBUG": "1"}
                )
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    recent = data.get("recent", [])
                    older  = data.get("older",  [])

                    ctx_lines = ["[Session Context - recent cross-session activity]"]

                    # Tier 1: recent entries — injected as-is
                    for s in recent:
                        _a  = s.get("actions") or "-"
                        _r  = s.get("result")  or "-"
                        _st = s.get("status")   or "-"
                        _sid = s.get("session_id", "?")
                        ctx_lines.append(
                            "  " + _sid + " [" + _st + "] "
                            + _a + " -> " + _r[:120]
                        )

                    # Tier 2: older entries — topic-indexed (TF-IDF, no LLM)
                    if older:
                        try:
                            idx_res = subprocess.run(
                                [sys.executable,
                                 str(Path.home() / ".hermes" / "scripts" / "build_topic_index.py")],
                                capture_output=True, text=True, timeout=10,
                                env={**os.environ, "STM_DEBUG": "1"}
                            )
                            if idx_res.returncode == 0 and idx_res.stdout.strip():
                                ctx_lines.append("")
                                ctx_lines.append("  [Earlier sessions — topic index]")
                                ctx_lines.append("  " + idx_res.stdout.strip())
                        except Exception:
                            pass  # topic indexing optional — fail silent

                    inject_msg = "\n".join(ctx_lines)
                    _orig_sys = kwargs.get("system_message") or ""
                    kwargs = dict(kwargs)
                    kwargs["system_message"] = (
                        (_orig_sys + "\n\n" + inject_msg) if _orig_sys else inject_msg
                    )
                    print(f"[stm] Injected {len(recent)} recent + {len(older)} older "
                          f"(topic-indexed) cross-session entries: "
                          + "; ".join(s.get("session_id","?") for s in recent[:3]),
                          file=_sys.stderr, flush=True)
            except Exception: pass

        # ── APPEND ───────────────────────────────────────────────────────────────
        _user_msg = args[0] if args else kwargs.get("user_message", "")
        _entry_id = None
        try:
            print(f"[stm] append: {_sess_id} prompt={_user_msg[:80]}",
                  file=_sys.stderr, flush=True)
            res = subprocess.run(
                [sys.executable,
                 str(Path.home() / ".hermes" / "scripts" / "stm.py"),
                 "append", _sess_id, _user_msg[:500]],
                capture_output=True, text=True, timeout=5,
                env={**os.environ, "STM_DEBUG": "1"}
            )
            if res.returncode == 0 and res.stdout.startswith("id:"):
                _entry_id = int(res.stdout.strip().split(":")[1])
        except Exception as e:
            print(f"[stm] append error: {e}", file=_sys.stderr, flush=True)

        result = fn(self, *args, **kwargs)

        # ── UPDATE ───────────────────────────────────────────────────────────────
        if _entry_id is not None:
            _tool_names = []
            for msg in result.get("messages", []):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        _n = tc.get("function",{}).get("name","")
                        if _n: _tool_names.append(_n)
            _act = ", ".join(_tool_names) if _tool_names else "no tools"
            _res = (result.get("final_response") or "")[:300].replace("\n", " ")
            _st = "success" if result.get("completed") and not result.get("interrupted") \
                  else ("failed" if result.get("interrupted") else "partial")
            try:
                subprocess.run(
                    [sys.executable,
                     str(Path.home() / ".hermes" / "scripts" / "stm.py"),
                     "update", str(_entry_id),
                     _act[:200], _res[:300], _st],
                    capture_output=True, text=True, timeout=5,
                    env={**os.environ, "STM_DEBUG": "1"}
                )
            except Exception: pass
        return result
    return wrapper
```

## Verification

After patching:
```bash
python3 -c "import run_agent; print('OK')"  # should import without error
python3 ~/.hermes/scripts/stm.py append "test_session" "hello world"
# → should return id:1
python3 ~/.hermes/scripts/stm.py summaries 1
# → should return JSON with the test entry
```

## Rollback

If something goes wrong:
```bash
cd ~/.hermes/hermes-agent && git checkout run_agent.py
# Then re-run the patch
```
