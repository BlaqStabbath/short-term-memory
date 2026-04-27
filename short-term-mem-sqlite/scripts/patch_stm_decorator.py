#!/usr/bin/env python3
"""
Idempotent patch: adds @stm_track to run_agent.py if not already present.
Safe to re-run after Hermes updates — checks before modifying.

Usage:
    python3 patch_stm_decorator.py          # dry run (print only)
    python3 patch_stm_decorator.py --apply  # actually patch
    python3 patch_stm_decorator.py --verify # exit 0 if already patched
"""
import re
import sys
from pathlib import Path

RUN_AGENT = Path.home() / ".hermes" / "hermes-agent" / "run_agent.py"
STM_SCRIPT = Path.home() / ".hermes" / "scripts" / "stm.py"
IDX_SCRIPT = Path.home() / ".hermes" / "scripts" / "build_topic_index.py"


PATCH_CODE = r'''
# ── Short-Term Memory Track (SQLite, cross-session) ─────────────────────────
# Intercepts AIAgent.run_conversation() to:
#   1. Inject cross-session topic index on new sessions
#   2. Append/persist each turn to stm.db via stm.py subprocess
_STM_SCRIPT = str(Path.home() / ".hermes" / "scripts" / "stm.py")
_STM_IDX_SCRIPT = str(Path.home() / ".hermes" / "scripts" / "build_topic_index.py")


def stm_track(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        import sys as _sys
        _sess_id = self.session_id or "cli"

        # ── NEW SESSION: two-tier cross-session context injection ──────────────
        _hist = kwargs.get("conversation_history")
        if _hist is None or len(_hist) == 0:
            try:
                res = subprocess.run(
                    [sys.executable, _STM_SCRIPT, "summaries"],
                    capture_output=True, text=True, timeout=10,
                    env={**os.environ, "STM_DEBUG": ""}
                )
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    recent = data.get("recent", [])
                    older = data.get("older", [])

                    ctx_lines = ["[Session Context - recent cross-session activity]"]

                    for s in recent:
                        _a = s.get("actions") or "-"
                        _r = s.get("result") or "-"
                        _st = s.get("status") or "-"
                        _sid = s.get("session_id", "?")
                        ctx_lines.append(
                            "  " + _sid + " [" + _st + "] " + _a + " -> " + _r[:120]
                        )

                    if older:
                        try:
                            idx_res = subprocess.run(
                                [sys.executable, _STM_IDX_SCRIPT],
                                capture_output=True, text=True, timeout=10,
                                env={**os.environ, "STM_DEBUG": ""}
                            )
                            if idx_res.returncode == 0 and idx_res.stdout.strip():
                                ctx_lines.append("")
                                ctx_lines.append("  [Earlier sessions - topic index]")
                                ctx_lines.append("  " + idx_res.stdout.strip())
                        except Exception:
                            pass

                    inject_msg = "\n".join(ctx_lines)
                    _orig_sys = kwargs.get("system_message") or ""
                    kwargs = dict(kwargs)
                    kwargs["system_message"] = (
                        (_orig_sys + "\n\n" + inject_msg) if _orig_sys else inject_msg
                    )
                    print(f"[stm] Injected {len(recent)} recent + {len(older)} older "
                          f"(topic-indexed) entries: "
                          + "; ".join(s.get("session_id", "?") for s in recent[:3]),
                          file=_sys.stderr, flush=True)
            except Exception:
                pass

        # ── APPEND (before call) ─────────────────────────────────────────────
        _user_msg = args[0] if args else kwargs.get("user_message", "")
        _entry_id = None
        try:
            res = subprocess.run(
                [sys.executable, _STM_SCRIPT, "append", _sess_id, _user_msg[:500]],
                capture_output=True, text=True, timeout=5,
                env={**os.environ, "STM_DEBUG": ""}
            )
            if res.returncode == 0 and res.stdout.startswith("id:"):
                _entry_id = int(res.stdout.strip().split(":")[1])
        except Exception as e:
            print(f"[stm] append error: {e}", file=_sys.stderr, flush=True)

        result = fn(self, *args, **kwargs)

        # ── UPDATE (after call) ──────────────────────────────────────────────
        if _entry_id is not None:
            _tool_names = []
            for msg in result.get("messages", []):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        _n = tc.get("function", {}).get("name", "")
                        if _n:
                            _tool_names.append(_n)
            _act = ", ".join(_tool_names) if _tool_names else "no tools"
            _res = (result.get("final_response") or "")[:300].replace("\n", " ")
            _st = "success" if result.get("completed") and not result.get("interrupted") \
                else ("failed" if result.get("interrupted") else "partial")
            try:
                subprocess.run(
                    [sys.executable, _STM_SCRIPT, "update",
                     str(_entry_id), _act[:200], _res[:300], _st],
                    capture_output=True, text=True, timeout=5,
                    env={**os.environ, "STM_DEBUG": ""}
                )
            except Exception:
                pass
        return result
    return wrapper
'''


def is_patched(content: str) -> bool:
    return "def stm_track(" in content and "@stm_track" in content


def needs_imports(content: str) -> bool:
    return "import functools" not in content or "import subprocess" not in content


def apply_patch():
    content = RUN_AGENT.read_text()

    if is_patched(content):
        print("[stm patch] Already applied — nothing to do.")
        return True

    # Add missing imports
    if "import functools" not in content:
        content = content.replace(
            "import json\n",
            "import functools\nimport json\nimport subprocess\n",
            1
        )
        print("[stm patch] Added functools + subprocess imports.")
    elif "import subprocess" not in content:
        content = content.replace(
            "import functools\n",
            "import functools\nimport subprocess\n",
            1
        )
        print("[stm patch] Added subprocess import.")

    # Insert decorator code after the env-loading block
    marker = '    logger.info("No .env file found. Using system environment variables.")'
    if marker in content:
        insert_pos = content.find(marker) + len(marker)
        # Find end of that line + newline
        after_marker = content[insert_pos:]
        # Make sure we break before the next meaningful line
        content = content[:insert_pos] + "\n" + PATCH_CODE.strip() + "\n" + after_marker
        print("[stm patch] Inserted stm_track decorator definition.")

    # Apply @stm_track to run_conversation
    if "@stm_track" not in content:
        content = content.replace(
            "\n    def run_conversation(",
            "\n    @stm_track\n    def run_conversation(",
            1
        )
        print("[stm patch] Applied @stm_track decorator to run_conversation.")

    RUN_AGENT.write_text(content)

    # Verify
    new_content = RUN_AGENT.read_text()
    if is_patched(new_content):
        print("[stm patch] ✓ Patch applied successfully.")
        return True
    else:
        print("[stm patch] ✗ Patch may have failed — please check manually.")
        return False


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    verify_only = "--verify" in sys.argv

    if not RUN_AGENT.exists():
        print(f"[stm patch] ERROR: {RUN_AGENT} not found.")
        sys.exit(1)

    content = RUN_AGENT.read_text()

    if verify_only:
        if is_patched(content):
            print("[stm patch] VERIFY: OK — decorator is present.")
            sys.exit(0)
        else:
            print("[stm patch] VERIFY: MISSING — decorator not found in run_agent.py.")
            sys.exit(1)

    if dry_run:
        print("[stm patch] DRY RUN — use --apply to actually patch.")
        print(f"  run_agent: {RUN_AGENT}")
        print(f"  stm_track present: {is_patched(content)}")
        print(f"  functools import: {'import functools' in content}")
        print(f"  subprocess import: {'import subprocess' in content}")
    else:
        ok = apply_patch()
        sys.exit(0 if ok else 1)
