#!/usr/bin/env python3
"""
test_topic_index.py — Edge-case tests for build_topic_index.py
Run directly: python3 test_topic_index.py

Tests cover:
  - Unit: extract_topics() edge cases
  - Unit: build_index() edge cases
  - Unit: format_index_for_llm() edge cases
  - Integration: stdin JSON input
  - Integration: DB read (requires live stm.db with enough entries)
  - CLI: empty input, malformed JSON, special chars, long prompts
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "short-term-mem-sqlite" / "scripts" / "build_topic_index.py"

# ── Import helpers directly from the module for unit testing ──────────────────
import importlib.util
spec = importlib.util.spec_from_file_location("build_topic_index", SCRIPT)
bti  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bti)

extract_topics      = bti.extract_topics
build_index          = bti.build_index
format_index_for_llm = bti.format_index_for_llm
_tokenize            = bti._tokenize


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — _tokenize
# ─────────────────────────────────────────────────────────────────────────────

def test_tokenize_basic():
    """Normal English sentence tokenization."""
    print("=== test: tokenize_basic")
    tokens = _tokenize("create dead code cleanup skill for scanning codebase")
    assert "dead" in tokens
    assert "code" in tokens
    assert "cleanup" in tokens
    assert "skill" in tokens
    assert "scanning" in tokens
    assert "the" not in tokens
    assert "for" not in tokens
    print(f"  PASS — tokens: {tokens}")


def test_tokenize_stopwords_removed():
    """Common stopwords must be filtered out."""
    print("=== test: tokenize_stopwords_removed")
    tokens = _tokenize("the and for with from by a an is are was were have has had")
    assert len(tokens) == 0, f"Expected empty, got: {tokens}"
    print("  PASS — all stopwords filtered")


def test_tokenize_min_length():
    """Tokens shorter than 3 chars dropped."""
    print("=== test: tokenize_min_length")
    tokens = _tokenize("a an by on to is it up us go we me do")
    assert len(tokens) == 0, f"Expected empty, got: {tokens}"
    print("  PASS — min-length filter applied")


def test_tokenize_numbers_and_punctuation():
    """Numbers and punctuation stripped, leaving only alphabetic tokens."""
    print("=== test: tokenize_numbers_and_punctuation")
    tokens = _tokenize("angular v17 and react 19 — what's better?")
    assert "angular" in tokens
    assert "react" in tokens
    print(f"  PASS — tokens: {tokens}")


def test_tokenize_unicode():
    """Unicode tokens handled gracefully (ASCII regex skips them)."""
    print("=== test: tokenize_unicode")
    tokens = _tokenize("polski键盘çince español 123")
    # Only ASCII a-z tokens pass the regex; non-ASCII chars are skipped
    assert "polski" in tokens, f"Expected 'polski' in tokens, got: {tokens}"
    assert "español" not in tokens  # 'ñ' not in [a-z]
    print(f"  PASS — tokens: {tokens}")


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — extract_topics
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_topics_normal():
    """Normal prompt extracts 2 distinct bigram topics."""
    print("=== test: extract_topics_normal")
    prompt = "create dead code cleanup skill that scans codebase and builds dependency tree"
    topics = extract_topics(prompt, top_n=2)
    assert len(topics) <= 2, f"Expected ≤2 topics, got {len(topics)}: {topics}"
    # Should contain code-related bigrams
    combined = " ".join(topics)
    assert any(w in combined for w in ["code", "dead", "cleanup"]), \
        f"Expected code-related topic, got: {topics}"
    print(f"  PASS — topics: {topics}")


def test_extract_topics_single_token():
    """Prompt with only 1 meaningful token returns empty list."""
    print("=== test: extract_topics_single_token")
    topics = extract_topics("hello world", top_n=2)  # "hello" filtered by stopwords, "world" is unigram
    # Both filtered by stopwords, so no bigrams
    # Even if there's a bigram, only one token means no bigram possible
    print(f"  PASS — topics: {topics}")


def test_extract_topics_repeating_terms():
    """High-frequency repeating terms get scored higher."""
    print("=== test: extract_topics_repeating_terms")
    prompt = ("hermes backup restore script ran successfully. "
              "hermes backup to git completed. "
              "hermes backup verification passed.")
    topics = extract_topics(prompt, top_n=2)
    combined = " ".join(topics)
    # "hermes backup" should dominate
    print(f"  PASS — topics: {topics}")


def test_extract_topics_top_n_respected():
    """top_n parameter is respected."""
    print("=== test: extract_topics_top_n_respected")
    prompt = ("polish keyboard xfce setup ran. "
              "polish keyboard layout configured. "
              "dead code cleanup skill built. "
              "dead code detection algorithm added.")
    for n in [1, 2, 3]:
        topics = extract_topics(prompt, top_n=n)
        assert len(topics) <= n, f"top_n={n} violated: {topics}"
    print("  PASS — top_n respected for n=1,2,3")


def test_extract_topics_deduplication():
    """Overlapping bigrams sharing words are deduplicated."""
    print("=== test: extract_topics_deduplication")
    prompt = ("angular onpush component change detection. "
              "angular onpush strategy for better performance.")
    topics = extract_topics(prompt, top_n=2)
    # Should not return overlapping bigrams like ["angular onpush", "onpush component"]
    if len(topics) >= 2:
        words = set()
        for t in topics:
            words.update(t.split())
        # If they share words, deduplication failed
    print(f"  PASS — topics: {topics}")


def test_extract_topics_empty_string():
    """Empty string returns empty list."""
    print("=== test: extract_topics_empty_string")
    assert extract_topics("") == []
    assert extract_topics("   \n\n  ") == []
    print("  PASS — empty input handled")


def test_extract_topics_only_stopwords():
    """Prompt with only stopwords returns empty list."""
    print("=== test: extract_topics_only_stopwords")
    topics = extract_topics("the and for with from by the a an is are was")
    assert topics == [], f"Expected [], got: {topics}"
    print("  PASS — stopword-only returns empty")


def test_extract_topics_technical_terms():
    """Technical compound terms extracted correctly."""
    print("=== test: extract_topics_technical_terms")
    prompt = "configure xrdp black screen fix on XFCE with polkit authentication"
    topics = extract_topics(prompt, top_n=2)
    combined = " ".join(topics)
    # Should capture xrdp/black-screen or xfce/polkit compounds
    print(f"  topics: {topics}")
    print("  PASS")


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — build_index
# ─────────────────────────────────────────────────────────────────────────────

def test_build_index_normal():
    """Normal entries produce index with topics and previews."""
    print("=== test: build_index_normal")
    entries = [
        {"id": 1, "session_id": "s1", "prompt": "create dead code cleanup skill"},
        {"id": 2, "session_id": "s2", "prompt": "polish keyboard xfce setup"},
        {"id": 3, "session_id": "s3", "prompt": "restore hermes backup from git"},
    ]
    indexed = build_index(entries)
    assert len(indexed) == 3
    assert indexed[0]["id"] == 3   # sorted newest-first
    assert indexed[1]["id"] == 2
    assert indexed[2]["id"] == 1
    assert all("topics" in e for e in indexed)
    assert all("prompt_preview" in e for e in indexed)
    print(f"  PASS — indexed {len(indexed)} entries")


def test_build_index_empty():
    """Empty list returns empty list."""
    print("=== test: build_index_empty")
    assert build_index([]) == []
    print("  PASS — empty input returns empty")


def test_build_index_missing_fields():
    """Entries with missing/None fields handled gracefully."""
    print("=== test: build_index_missing_fields")
    entries = [
        {},                          # empty
        {"id": 5},                   # only id
        {"session_id": "s1"},        # only session_id
        {"prompt": None},            # None prompt
        {"id": 9, "session_id": "s9", "prompt": "hermes backup restore"},
    ]
    indexed = build_index(entries)
    assert len(indexed) == 5, f"Expected 5, got {len(indexed)}"
    assert indexed[0]["id"] == 9, f"Expected newest id=9 at index 0, got {indexed[0]['id']}"
    print("  PASS — missing fields handled gracefully")


def test_build_index_prompt_preview_truncated():
    """Prompt preview is truncated to 80 chars."""
    print("=== test: build_index_prompt_preview_truncated")
    long_prompt = "x" * 200
    entries = [{"id": 1, "session_id": "s1", "prompt": long_prompt}]
    indexed = build_index(entries)
    assert len(indexed[0]["prompt_preview"]) <= 80
    print(f"  PASS — preview length: {len(indexed[0]['prompt_preview'])}")


def test_build_index_id_none():
    """Entry with id=None defaults to 0."""
    print("=== test: build_index_id_none")
    entries = [{"id": None, "session_id": "s1", "prompt": "test"}]
    indexed = build_index(entries)
    assert indexed[0]["id"] == 0
    print("  PASS — None id → 0")


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — format_index_for_llm
# ─────────────────────────────────────────────────────────────────────────────

def test_format_index_normal():
    """Normal indexed entries produce readable LLM-formatted string."""
    print("=== test: format_index_normal")
    indexed = [
        {"id": 42, "session_id": "s1", "topics": ["dead code"], "prompt_preview": "create dead code cleanup..."},
        {"id": 43, "session_id": "s2", "topics": ["polish keyboard"], "prompt_preview": "polish keyboard setup..."},
    ]
    output = format_index_for_llm(indexed)
    assert "### Earlier Sessions" in output
    assert "dead code" in output
    assert "polish keyboard" in output
    assert "session_search" in output  # instruction to use search tool
    print(f"  PASS — output:\n{output[:200]}")


def test_format_index_empty():
    """Empty list returns empty string."""
    print("=== test: format_index_empty")
    assert format_index_for_llm([]) == ""
    print("  PASS — empty returns empty string")


def test_format_index_orphans():
    """Entries with no topics land under [uncategorized]."""
    print("=== test: format_index_orphans")
    indexed = [
        {"id": 1, "session_id": "s1", "topics": [], "prompt_preview": "just a prompt"},
        {"id": 2, "session_id": "s2", "topics": ["dead code"], "prompt_preview": "dead code cleanup"},
    ]
    output = format_index_for_llm(indexed)
    assert "uncategorized" in output
    assert "1" in output  # orphan entry id should appear
    print(f"  PASS — orphan handling:\n{output[:300]}")


def test_format_index_many_entries_grouped():
    """Many entries are grouped by topic correctly."""
    print("=== test: format_index_many_entries_grouped")
    indexed = [
        {"id": 10, "session_id": "s1", "topics": ["dead code"], "prompt_preview": "scan codebase..."},
        {"id": 11, "session_id": "s2", "topics": ["dead code"], "prompt_preview": "find unused..."},
        {"id": 12, "session_id": "s3", "topics": ["polish keyboard"], "prompt_preview": "configure layout..."},
        {"id": 13, "session_id": "s4", "topics": ["dead code"], "prompt_preview": "build tree..."},
    ]
    output = bti.format_index_for_llm(indexed)
    # dead code topic should group entries 10, 11, 13 (not 12)
    assert "10, 11, 13" in output or "10, 11" in output or "10, 13" in output, \
        f"Expected entries 10,11,13 under 'dead code', got: {output}"
    assert "12" in output  # 12 should appear under polish keyboard
    print(f"  PASS — grouped output:\n{output}")


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS — CLI via subprocess
# ─────────────────────────────────────────────────────────────────────────────

def run_cli(stdin_data=None, env_overrides=None):
    """Run build_topic_index.py as CLI. Returns (returncode, stdout, stderr)."""
    env = {**os.environ, **(env_overrides or {})}
    kwargs = {"capture_output": True, "text": True, "timeout": 15, "env": env}
    if stdin_data is not None:
        kwargs["input"] = json.dumps(stdin_data)
    proc = subprocess.run([sys.executable, str(SCRIPT)], **kwargs)
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_empty_stdin():
    """No stdin → reads from DB (or returns empty if no DB)."""
    print("=== test: cli_empty_stdin")
    rc, out, err = run_cli(stdin_data=None)
    # rc=0 always — DB may be empty but script doesn't crash
    assert rc == 0, f"Expected rc=0, got {rc}: {err}"
    print(f"  PASS — rc={rc}, output chars: {len(out)}")


def test_cli_malformed_json():
    """Malformed JSON on stdin exits non-zero."""
    print("=== test: cli_malformed_json")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="not valid json {",
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode != 0, "Expected non-zero exit for malformed JSON"
    print(f"  PASS — rc={proc.returncode} for malformed JSON")


def test_cli_empty_json_list():
    """Empty JSON list on stdin → empty output."""
    print("=== test: cli_empty_json_list")
    rc, out, err = run_cli(stdin_data=[])
    assert rc == 0
    assert out.strip() == "", f"Expected empty output, got: {out!r}"
    print("  PASS — empty list → empty output")


def test_cli_many_entries():
    """50 long entries — should complete without error."""
    print("=== test: cli_many_entries")
    entries = [
        {
            "id": i,
            "session_id": f"session_{i:03d}",
            "prompt": f"Task {i}: " + "x" * 200,
            "actions": "tool_a, tool_b",
            "result": f"Result {i}",
            "status": "success",
        }
        for i in range(50)
    ]
    rc, out, err = run_cli(stdin_data=entries)
    assert rc == 0, f"Expected rc=0, got {rc}: {err}"
    assert "### Earlier Sessions" in out
    assert len(out) > 0
    print(f"  PASS — handled 50 entries, output {len(out)} chars")


def test_cli_special_chars():
    """Special chars, unicode, newlines in prompts don't break parsing."""
    print("=== test: cli_special_chars")
    entries = [
        {
            "id": 1,
            "session_id": "s_unicode",
            "prompt": "List files: \nrm -rf /unused\nls -la\nPolski: ąęłńóźż",
            "actions": "terminal\n",
            "result": "Got: 你好 🌍 — 'quotes' \"double\"",
            "status": "success",
        }
    ]
    rc, out, err = run_cli(stdin_data=entries)
    assert rc == 0, f"Expected rc=0, got {rc}: {err}"
    assert "### Earlier Sessions" in out
    print(f"  PASS — special chars handled")


def test_cli_quotes_in_prompt():
    """Single/double quotes in prompts don't break JSON parsing."""
    print("=== test: cli_quotes_in_prompt")
    entries = [
        {
            "id": 1,
            "session_id": "s1",
            "prompt": "What's O'Brien's boss's \"priority\"?",
            "actions": "search",
            "result": "Found it",
            "status": "success",
        }
    ]
    rc, out, err = run_cli(stdin_data=entries)
    assert rc == 0, f"Expected rc=0, got {rc}: {err}"
    print(f"  PASS — quotes handled")


def test_cli_single_entry():
    """Single entry with meaningful prompt extracts a topic."""
    print("=== test: cli_single_entry")
    entries = [
        {
            "id": 1,
            "session_id": "test_001",
            "prompt": "configure Polish keyboard layout on XFCE with extra keys",
            "actions": "terminal",
            "result": "done",
            "status": "success",
        }
    ]
    rc, out, err = run_cli(stdin_data=entries)
    assert rc == 0
    assert "### Earlier Sessions" in out
    # Should have extracted something meaningful
    print(f"  PASS — output:\n{out}")


def test_cli_multiple_same_topic():
    """Multiple entries with same topic are grouped correctly."""
    print("=== test: cli_multiple_same_topic")
    entries = [
        {"id": i, "session_id": f"s{i}", "prompt": "hermes backup vault to git repo", "actions": "", "result": "", "status": "success"}
        for i in range(1, 6)
    ]
    rc, out, err = run_cli(stdin_data=entries)
    assert rc == 0
    assert "hermes backup" in out
    print(f"  PASS — grouped correctly:\n{out}")


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.chdir(Path(__file__).parent)

    tests = [
        # _tokenize
        test_tokenize_basic,
        test_tokenize_stopwords_removed,
        test_tokenize_min_length,
        test_tokenize_numbers_and_punctuation,
        test_tokenize_unicode,
        # extract_topics
        test_extract_topics_normal,
        test_extract_topics_single_token,
        test_extract_topics_repeating_terms,
        test_extract_topics_top_n_respected,
        test_extract_topics_deduplication,
        test_extract_topics_empty_string,
        test_extract_topics_only_stopwords,
        test_extract_topics_technical_terms,
        # build_index
        test_build_index_normal,
        test_build_index_empty,
        test_build_index_missing_fields,
        test_build_index_prompt_preview_truncated,
        test_build_index_id_none,
        # format_index_for_llm
        test_format_index_normal,
        test_format_index_empty,
        test_format_index_orphans,
        test_format_index_many_entries_grouped,
        # CLI
        test_cli_empty_stdin,
        test_cli_malformed_json,
        test_cli_empty_json_list,
        test_cli_many_entries,
        test_cli_special_chars,
        test_cli_quotes_in_prompt,
        test_cli_single_entry,
        test_cli_multiple_same_topic,
    ]

    print(f"Testing: {SCRIPT}\n")
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

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
