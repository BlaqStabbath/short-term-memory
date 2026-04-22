#!/usr/bin/env python3
"""Edge-case tests for llm_summarize.py — run directly: python3 test_llm_summarize.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parent / "short-term-mem-sqlite" / "scripts" / "llm_summarize.py"
HERMES_HOME = Path.home() / ".hermes"

def run(stdin_data, env_overrides=None):
    """Run llm_summarize.py with stdin input and env overrides. Returns stdout."""
    env = {**os.environ, **(env_overrides or {})}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(stdin_data),
        capture_output=True, text=True, timeout=35,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_empty_entries():
    """Edge: empty list — should return empty string, not crash."""
    print("=== test: empty_entries ===")
    rc, out, err = run([])
    assert rc == 0, f"expected rc=0, got {rc}"
    assert out.strip() == "", f"expected empty, got: {out!r}"
    print("  PASS — returns empty string for empty list")


def test_single_entry():
    """Edge: single entry."""
    print("=== test: single_entry ===")
    entries = [{
        "id": 1,
        "session_id": "test_001",
        "prompt": "deploy the API to prod",
        "actions": "terminal, kubectl",
        "result": "all pods healthy",
        "status": "success",
    }]
    rc, out, err = run(entries)
    assert rc == 0, f"expected rc=0, got {rc}"
    assert len(out.strip()) > 0, f"expected non-empty summary, got: {out!r}"
    print(f"  PASS — output: {out.strip()!r}")


def test_malformed_json():
    """Edge: no stdin / invalid JSON — should exit non-zero."""
    print("=== test: malformed_json ===")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=5,
    )
    assert proc.returncode != 0, "expected non-zero exit"
    print(f"  PASS — rc={proc.returncode} for empty stdin")


def test_many_entries():
    """Edge: many entries (stress test with long prompts/results)."""
    print("=== test: many_entries ===")
    entries = [
        {
            "id": i,
            "session_id": f"session_{i:03d}",
            "prompt": f"Task {i}: " + ("x" * 200),
            "actions": f"tool_a, tool_b, tool_c",
            "result": f"Completed successfully with result {i} " + ("y" * 200),
            "status": "success",
        }
        for i in range(50)
    ]
    rc, out, err = run(entries)
    assert rc == 0, f"expected rc=0, got {rc}: {err}"
    assert len(out.strip()) > 0, f"expected non-empty summary"
    print(f"  PASS — handled 50 long entries")


def test_no_api_key_fallback():
    """Edge: no API key available — should use non-LLM fallback."""
    print("=== test: no_api_key_fallback ===")
    entries = [
        {"session_id": "s1", "prompt": "deploy API", "actions": "kubectl", "result": "ok", "status": "success"},
        {"session_id": "s2", "prompt": "rollback", "actions": "kubectl", "result": "ok", "status": "success"},
    ]
    # Clear all common key env vars for this test
    test_env = {k: v for k, v in os.environ.items()
                if k not in (
                    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                    "DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "MINIMAX_API_KEY",
                    "AZURE_API_KEY", "MISTRAL_API_KEY", "TOGETHER_API_KEY",
                    "CLOUDFLARE_API_KEY", "GROQ_API_KEY",
                )}
    # Also make sure .env isn't read as a key source
    rc, out, err = run(entries, env_overrides=test_env)
    assert rc == 0, f"expected rc=0, got {rc}: {err}"
    # Should fall back to compact format
    assert "s1" in out and "s2" in out, f"expected fallback format in output: {out!r}"
    print(f"  PASS — fallback output: {out.strip()!r}")


def test_special_chars_in_entries():
    """Edge: entries with special chars, unicode, newlines."""
    print("=== test: special_chars ===")
    entries = [
        {
            "session_id": "test_unicode",
            "prompt": "List files in /tmp: \nrm -rf /unused\nls -la",
            "actions": "terminal\nexecute_code",
            "result": "Got output with unicode: 你好 🌍 — and 'quotes' and \"double quotes\"",
            "status": "success",
        }
    ]
    rc, out, err = run(entries)
    assert rc == 0, f"expected rc=0, got {rc}: {err}"
    print(f"  PASS — special chars handled: {out.strip()!r}")


def test_missing_fields_in_entry():
    """Edge: entry dicts with missing fields — should not crash."""
    print("=== test: missing_fields ===")
    entries = [
        {"session_id": "s1"},  # only session_id
        {"prompt": "hello"},  # only prompt
        {},                   # empty
        {"id": 5, "result": "partial", "status": "partial"},  # sparse
    ]
    rc, out, err = run(entries)
    assert rc == 0, f"expected rc=0, got {rc}: {err}"
    print(f"  PASS — missing fields handled gracefully")


def test_quotes_in_prompt():
    """Edge: single/double quotes in prompts that could break JSON."""
    print("=== test: quotes_in_prompt ===")
    entries = [
        {"session_id": "s1", "prompt": "What's O'Brien's boss's \"priority\"?",
         "actions": "search", "result": "Found it", "status": "success"},
    ]
    rc, out, err = run(entries)
    assert rc == 0, f"expected rc=0, got {rc}: {err}"
    print(f"  PASS — quotes handled: {out.strip()!r}")


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    print(f"Testing: {SCRIPT}\n")
    tests = [
        test_empty_entries,
        test_single_entry,
        test_malformed_json,
        test_many_entries,
        test_no_api_key_fallback,
        test_special_chars_in_entries,
        test_missing_fields_in_entry,
        test_quotes_in_prompt,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL — {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR — {e}")
            failed += 1
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
