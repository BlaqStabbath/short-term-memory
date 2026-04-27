#!/usr/bin/env python3
"""
build_topic_index.py — Extractive topic indexing for STM older entries.

Replaces llm_summarize.py with a deterministic, keyword-based approach.
No LLM needed: uses TF-IDF-inspired bigram scoring on the PROMPT field only.

Output format (for LLM context injection):
    ### Earlier Sessions — Topic Index
    [entry_id] <topic>  |  "<prompt preview...>"  |  sessions: s1, s2
    ...

The LLM can use session_search to retrieve full entry details by ID on demand.

Input:
    - stdin: JSON list of entry dicts  (same format as llm_summarize.py)
    - CLI:   reads directly from stm.db (offset=RAW_CAP, limit=SCAN_CAP)

Env overrides:
    STM_DB_PATH   — path to stm.db
    STM_RAW_CAP   — offset (default 5)
    STM_SCAN_CAP  — how many older entries to index (default 45)
    STM_DEBUG      — set to "1" for verbose output
"""

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict, Counter
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
DB_PATH     = os.environ.get("STM_DB_PATH", str(HERMES_HOME / "sessions" / "stm.db"))
RAW_CAP     = int(os.environ.get("STM_RAW_CAP",  "5"))
SCAN_CAP    = int(os.environ.get("STM_SCAN_CAP",  "45"))
DEBUG       = os.environ.get("STM_DEBUG", "") == "1"

# ── Stopwords ─────────────────────────────────────────────────────────────────
_STOPWORDS = {
    # Articles / pronouns
    "a", "an", "the",
    # Prepositions
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as", "into", "through",
    "about", "over", "under", "between", "after", "before", "during", "above", "below",
    # Conjunctions
    "and", "or", "but", "nor", "so", "yet", "both", "either", "neither",
    # Common verbs (generic)
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "done", "doing", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "need", "needs", "needed",
    # Pronouns
    "i", "me", "my", "mine", "we", "us", "our", "ours",
    "you", "your", "yours", "he", "him", "his", "she", "her", "hers", "it", "its",
    "they", "them", "their", "theirs", "this", "that", "these", "those",
    # Adverbs
    "not", "no", "yes", "very", "just", "also", "now", "then", "here", "there",
    "up", "down", "out", "off", "back", "again", "more", "most", "some", "any", "all",
    # Common tech noise
    "get", "got", "getting", "use", "used", "using", "make", "made", "making",
    "see", "saw", "seen", "know", "knew", "known", "want", "wanted", "wants",
    "give", "gave", "given", "take", "took", "taken", "go", "went", "gone",
    "come", "came", "coming", "look", "looked", "looking", "find", "found",
    "tell", "told", "say", "said", "saying", "check", "checked", "checking",
    "run", "ran", "running", "run", "try", "tried", "trying",
    "put", "set", "let", "call", "called", "keep", "kept", "turn", "turned",
    "need", "needed", "start", "started", "stop", "stopped",
    # Misc filler
    "one", "two", "first", "last", "new", "old", "like", "way", "thing", "things",
    "much", "many", "such", "even", "well", "still", "right", "left", "see",
}
_MIN_TOKEN_LEN = 3
_MAX_TOKEN_LEN = 30


def _tokenize(text: str) -> list[str]:
    """Lowercase, extract alphanumeric tokens, filter by length and stopwords."""
    text = text or ""
    tokens = re.findall(r'[a-z]{1,30}', text.lower())
    return [t for t in tokens if t not in _STOPWORDS and _MIN_TOKEN_LEN <= len(t) <= _MAX_TOKEN_LEN]


def _score_bigrams(tokens: list[str]) -> dict[tuple[str, str], float]:
    """
    Score all bigrams in a token list using TF-IDF-inspired weighting.
    Score = bigram_freq * log(unigram_freq + 1) for each constituent word.
    """
    if len(tokens) < 2:
        return {}

    unigrams = Counter(tokens)
    bigrams  = Counter((tokens[i], tokens[i+1]) for i in range(len(tokens)-1))

    scored = {}
    for (w1, w2), bg_count in bigrams.items():
        u1 = unigrams[w1]
        u2 = unigrams[w2]
        # IDF-like bonus: words that are moderately rare across the passage score higher
        # score = co-occurrence count * (log avg_unigram_freq)
        avg_freq = (u1 + u2) / 2
        scored[(w1, w2)] = bg_count * (1 + (avg_freq / (avg_freq + 2)))
    return scored


def extract_topics(prompt: str, top_n: int = 2) -> list[str]:
    """
    Extract top-N most salient bigram topics from a single prompt.
    Returns list of "w1 w2" phrase strings, ordered by score descending.
    """
    tokens = _tokenize(prompt)
    if len(tokens) < 2:
        return []

    scored = _score_bigrams(tokens)
    if not scored:
        return []

    # Sort by score descending, take top N
    sorted_bigrams = sorted(scored.items(), key=lambda x: -x[1])
    chosen = []
    covered = set()
    for (w1, w2), _ in sorted_bigrams:
        # Skip if either word already covered by a higher-scoring phrase
        if w1 in covered or w2 in covered:
            continue
        chosen.append(f"{w1} {w2}")
        covered.add(w1)
        covered.add(w2)
        if len(chosen) >= top_n:
            break

    return chosen


def build_index(entries: list[dict]) -> list[dict]:
    """
    Build a topic index from a list of entry dicts.
    Returns a list of index entries sorted by entry ID descending (newest first).

    Each result entry:
        {
            "id":         int,
            "session_id": str,
            "topics":     list[str],       # 1-2 bigram topics
            "prompt_preview": str,          # first 80 chars of prompt
        }
    """
    results = []
    for entry in entries:
        eid   = entry.get("id") or 0
        sid   = entry.get("session_id", "?")
        prompt = entry.get("prompt", "")

        topics = extract_topics(prompt, top_n=2)
        preview = (prompt or "")[:80].replace("\n", " ").strip()

        results.append({
            "id":             eid,
            "session_id":     sid,
            "topics":         topics,
            "prompt_preview": preview,
        })

    # Sort newest-first by id descending
    results.sort(key=lambda x: -x["id"])
    return results


def format_index_for_llm(indexed: list[dict]) -> str:
    """
    Format the topic index for LLM context injection.
    Shows: [entry_id] <topic(s)>  |  "<prompt preview...>"
    Grouped by topic so LLM can scan and decide what to retrieve.
    """
    if not indexed:
        return ""

    # Group by topic
    by_topic: dict[str, list[dict]] = defaultdict(list)
    orphan_count = 0
    for entry in indexed:
        if entry["topics"]:
            for topic in entry["topics"]:
                by_topic[topic].append(entry)
        else:
            orphan_count += 1

    lines = ["### Earlier Sessions — Topic Index", ""]

    # Sort topics by total entries (most discussed first)
    for topic, entries in sorted(by_topic.items(), key=lambda x: -len(x[1])):
        ids = [str(e["id"]) for e in entries]
        previews = [e["prompt_preview"][:50] for e in entries[:2]]
        lines.append(f"  [{topic}]  sessions: {', '.join(ids[:5])}"
                     + (f"  e.g. \"{previews[0]}\"" if previews else ""))

    if orphan_count:
        lines.append(f"  [uncategorized]  {orphan_count} entries (no clear topic)")

    lines.append("")
    lines.append("Use session_search tool to retrieve full entries by ID when needed.")

    return "\n".join(lines)


def read_from_db() -> list[dict]:
    """Read older entries from stm.db (offset=RAW_CAP, limit=SCAN_CAP)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, session_id, prompt, actions, result, status, timestamp "
        "FROM entries ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (SCAN_CAP, RAW_CAP),
    ).fetchall()
    conn.close()
    return [
        {
            "id":         r[0],
            "session_id": r[1],
            "prompt":     r[2] or "",
            "actions":    r[3] or "",
            "result":     r[4] or "",
            "status":     r[5] or "",
            "timestamp":  r[6],
        }
        for r in rows
    ]


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    stdin_data = sys.stdin.read().strip()

    if stdin_data:
        entries = json.loads(stdin_data)
    else:
        entries = read_from_db()

    if DEBUG:
        print(f"[build_topic_index] Processing {len(entries)} entries", file=sys.stderr)

    indexed = build_index(entries)
    output  = format_index_for_llm(indexed)

    print(output)

    if DEBUG:
        print(f"[build_topic_index] Produced {len(indexed)} index entries, "
              f"output {len(output)} chars", file=sys.stderr)
