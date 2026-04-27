#!/usr/bin/env python3
"""
llm_summarize.py — Condense older STM entries via LLM for context compression.

Reads older entries directly from stm.db (offset=RAW_CAP, limit=SCAN_CAP),
matching the two-tier injection design in short-term-mem-sqlite.

API credentials read from the same config.yaml / .env that Hermes uses,
so no separate API key env var is needed.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH    = HERMES_HOME / ".env"

# ── Cap constants (must match stm.py) ─────────────────────────────────────────
RAW_CAP  = int(os.environ.get("STM_RAW_CAP",  "5"))   # recent entries injected as-is
SCAN_CAP = int(os.environ.get("STM_SCAN_CAP", "45"))   # older entries → LLM summarization
TOKEN_CAP = 400                                        # max tokens in LLM summary output
DB_PATH  = os.environ.get("STM_DB_PATH", str(HERMES_HOME / "sessions" / "stm.db"))

# ── API key env vars ──────────────────────────────────────────────────────────
_KNOWN_KEY_VARS = [
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "AZURE_API_KEY",
    "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY", "KIMI_API_KEY",
    "DASHSCOPE_API_KEY", "XIAOMI_API_KEY", "KILOCODE_API_KEY",
    "MISTRAL_API_KEY", "TOGETHER_API_KEY", "CLOUDFLARE_API_KEY",
    "GROQ_API_KEY", "OPENCODE_ZEN_API_KEY", "OPENCODE_GO_API_KEY",
    "AI_GATEWAY_API_KEY",
]


def _load_env() -> dict[str, str]:
    """
    Parse .env into a dict, then overlay os.environ API key vars
    so shell-level env overrides the .env file.
    """
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in _KNOWN_KEY_VARS:
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def _read_from_db() -> list[dict]:
    """
    Read older entries from stm.db — offset=RAW_CAP, limit=SCAN_CAP.
    These are the tier-2 entries that need LLM summarization.
    Returns list of entry dicts.
    """
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
            "prompt":     r[2],
            "actions":   r[3] or "",
            "result":     r[4] or "",
            "status":     r[5] or "",
            "timestamp":  r[6],
        }
        for r in rows
    ]


def _get_model_config() -> tuple[str, str, str]:
    """
    Return (api_key, base_url, model) from config.yaml + .env.
    Reads the [model] section of config.yaml for provider/base_url,
    and the corresponding env var for the API key.
    """
    try:
        import yaml
        has_yaml = True
    except ImportError:
        has_yaml = False

    env = _load_env()
    api_key  = ""
    base_url = "https://api.openai.com/v1"
    model    = "gpt-4o-mini"

    if has_yaml and CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    model_cfg = cfg.get("model", {}) or {}
    provider  = model_cfg.get("provider", "openai")

    # Map provider name → (env_var|list[env_var], base_url)
    provider_defaults = {
        "openai":    (["OPENAI_API_KEY"],              "https://api.openai.com/v1"),
        "anthropic": (["ANTHROPIC_API_KEY"],            "https://api.anthropic.com"),
        "openrouter":(["OPENROUTER_API_KEY"],           "https://openrouter.ai/api"),
        "deepseek":  (["DEEPSEEK_API_KEY"],             "https://api.deepseek.com"),
        "google":    (["GOOGLE_API_KEY"],               "https://generativelanguage.googleapis.com/v1beta"),
        "azure":     (["AZURE_API_KEY"],                ""),
        "minimax":   (["MINIMAX_API_KEY"],              "https://api.minimax.io/v1"),
        "mistral":   (["MISTRAL_API_KEY"],              "https://api.mistral.ai"),
        "together":  (["TOGETHER_API_KEY"],             "https://api.together.xyz"),
        "cloudflare":(["CLOUDFLARE_API_KEY"],           "https://api.cloudflare.com"),
        "groq":      (["GROQ_API_KEY"],                 "https://api.groq.com/openai"),
        "ollama":    (["OLLAMA_BASE_URL"],              "http://localhost:11434"),
        "lmstudio":  (["LMSTUDIO_BASE_URL"],            "http://localhost:1234"),
        "local":     (["LOCAL_API_KEY"],                "http://localhost:8000/v1"),
    }

    def _find_key(env_vars, env_dict):
        for k in env_vars:
            if k and env_dict.get(k, "").strip():
                return k
        return ""

    matched_env_var, matched_base = "", ""
    provider_lower = provider.lower().strip()

    if provider_lower == "auto":
        for env_vars, def_base in provider_defaults.values():
            key = _find_key(env_vars, env)
            if key:
                matched_env_var = key
                matched_base = def_base
                break
        if not matched_env_var:
            matched_env_var, matched_base = "OPENAI_API_KEY", "https://api.openai.com/v1"
    else:
        for prov_name, (env_vars, def_base) in provider_defaults.items():
            if prov_name in provider_lower:
                matched_env_var = _find_key(env_vars, env)
                matched_base = def_base
                break

    # base_url: config.yaml override > provider default
    configured_base = model_cfg.get("base_url", "").strip()
    if configured_base:
        base_url = configured_base.rstrip("/")
    else:
        base_url = matched_base

    # API key: config.yaml override > provider env var > OPENAI fallback
    configured_key = model_cfg.get("api_key", "")
    if configured_key:
        if configured_key.startswith("${") and configured_key.endswith("}"):
            var_name = configured_key[2:-1]
            api_key = env.get(var_name, "")
        else:
            api_key = configured_key
    elif matched_env_var:
        api_key = env.get(matched_env_var, "")
    else:
        api_key = env.get("OPENAI_API_KEY", "")

    # Default model based on provider
    provider_model_defaults = {
        "openai":    "gpt-4o-mini",
        "anthropic": "claude-3-haiku-20240307",
        "deepseek":  "deepseek-chat",
        "google":    "gemini-1.5-flash",
        "minimax":   "MiniMax-M2.7",
    }
    for prov, default_model in provider_model_defaults.items():
        if prov.lower() in provider_lower:
            model = default_model
            break

    if model_cfg.get("model"):
        model = model_cfg["model"]

    return api_key, base_url, model



def _call_llm(api_key: str, base_url: str, model: str, messages: list[dict]) -> str:
    """
    Make an API call. Supports both OpenAI-compatible (/chat/completions)
    and MiniMax Anthropic-compatible (/anthropic/v1/messages) endpoints.
    """
    import urllib.request

    url = base_url.rstrip("/")

    # MiniMax Anthropic endpoint uses /anthropic/v1/messages
    if "minimax.io/anthropic" in url:
        url += "/v1/messages"
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": TOKEN_CAP,
            "temperature": 0.3,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result["content"][0]["text"].strip()
    else:
        url += "/chat/completions"
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": TOKEN_CAP,
            "temperature": 0.3,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()


def summarize_entries(entries: list[dict], override: dict = None) -> str:
    """
    Build a compact summary paragraph of older entries via LLM.
    Uses RAW_CAP (5) as offset and SCAN_CAP (45) as limit from stm.db.
    """
    if not entries:
        return ""

    lines = []
    for e in entries:
        lines.append(
            f"[{e.get('session_id', '?')}][{e.get('status', '?')}] "
            f"{e.get('prompt', '?')} -> {e.get('result', '?')[:80]}"
        )
    body = "\n".join(lines)

    system_prompt = (
        "You are a context compressor. Given a list of older agent session entries, "
        "write a single short paragraph (2-4 sentences) summarizing what the agent did "
        "across those sessions. Be concise and casual. Output only the summary."
    )

    if override:
        api_key, base_url, model = override["api_key"], override["base_url"], override["model"]
    else:
        api_key, base_url, model = _get_model_config()

    if not api_key:
        print("[llm_summarize] No API key found in config.yaml/.env — using fallback",
              file=sys.stderr)
        return "; ".join(
            f"{e.get('session_id', '?')}: {e.get('prompt', '?')[:50]}"
            for e in entries[:3]
        )

    try:
        return _call_llm(api_key, base_url, model, [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Entries:\n{body}"},
        ])
    except Exception as e:
        print(f"[llm_summarize] LLM call failed: {e} — using fallback", file=sys.stderr)
        return "; ".join(
            f"{e.get('session_id', '?')}: {e.get('prompt', '?')[:50]}"
            for e in entries[:3]
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LLMsummarize older STM entries")
    parser.add_argument("--key", help="API key (overrides config)")
    parser.add_argument("--base-url", help="Base URL (overrides config)")
    parser.add_argument("--model", help="Model name (overrides config)")
    args = parser.parse_args()

    # CLI args take priority; otherwise fall back to config file / env probing
    if args.key or args.base_url or args.model:
        _override = {"api_key": args.key or "", "base_url": args.base_url or "", "model": args.model or ""}
    else:
        _override = None

    # Priority: stdin (JSON entries) > direct DB read (offset=RAW_CAP, limit=SCAN_CAP)
    stdin_data = sys.stdin.read().strip()

    if stdin_data:
        entries = json.loads(stdin_data)
    else:
        entries = _read_from_db()

    summary = summarize_entries(entries, override=_override)
    print(summary)
