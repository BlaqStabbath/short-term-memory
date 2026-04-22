#!/usr/bin/env python3
"""
llm_summarize.py — Condense older STM entries via LLM for context compression.
Reads API credentials from the same config.yaml / .env that Hermes uses,
so no separate API key env var is needed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH    = HERMES_HOME / ".env"

# All known API key env vars — checked in both .env and os.environ
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
    # Overlay known API key env vars from shell
    for k in _KNOWN_KEY_VARS:
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def _get_model_config() -> tuple[str, str, str]:
    """
    Return (api_key, base_url, model) from config.yaml + .env.
    Reads the [model] section of config.yaml for provider/base_url,
    and the corresponding env var for the API key.
    """
    try:
        import yaml  # pip install pyyaml
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

    # Map provider name to the env var that holds its API key
    provider_key_map = {
        "openai":     "OPENAI_API_KEY",
        "anthropic":  "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "deepseek":   "DEEPSEEK_API_KEY",
        "google":     "GOOGLE_API_KEY",
        "azure":      "AZURE_API_KEY",
        "minimax":    "MINIMAX_API_KEY",
        "mistral":    "MISTRAL_API_KEY",
        "together":   "TOGETHER_API_KEY",
        "cloudflare": "CLOUDFLARE_API_KEY",
        "groq":       "GROQ_API_KEY",
        "ollama":     "OLLAMA_BASE_URL",
        "lmstudio":   "LMSTUDIO_BASE_URL",
        "local":      "LOCAL_API_KEY",
    }

    # Try config.yaml base_url first, then provider default
    configured_base = model_cfg.get("base_url", "")
    if configured_base:
        base_url = configured_base.rstrip("/")

    # Look up API key — config.yaml can reference an env var via ${VAR} or raw key
    configured_key = model_cfg.get("api_key", "")
    if configured_key:
        # Expand ${VAR} style reference
        if configured_key.startswith("${") and configured_key.endswith("}"):
            var_name = configured_key[2:-1]
            api_key = env.get(var_name, "")
        else:
            api_key = configured_key

    # Fallback: look up by provider name in env
    if not api_key:
        for prov_name, env_var in provider_key_map.items():
            if prov_name.lower() in provider.lower():
                api_key = env.get(env_var, "")
                break

    # If still no key, try openai as the default fallback
    if not api_key:
        api_key = env.get("OPENAI_API_KEY", "")

    # Default model based on provider
    provider_defaults = {
        "openai":    "gpt-4o-mini",
        "anthropic": "claude-3-haiku-20240307",
        "deepseek":  "deepseek-chat",
        "google":    "gemini-1.5-flash",
        "minimax":   "MiniMax-Text-01",
    }
    for prov, default_model in provider_defaults.items():
        if prov.lower() in provider.lower():
            model = default_model
            break

    # Let config.yaml override the model
    if model_cfg.get("model"):
        model = model_cfg["model"]

    return api_key, base_url, model


def _call_llm(api_key: str, base_url: str, model: str, messages: list[dict]) -> str:
    """Make an OpenAI-compatible chat completions call."""
    import urllib.request

    url = f"{base_url.rstrip('/')}/chat/completions"
    data = {
        "model": model,
        "messages": messages,
        "max_tokens": 200,
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


def summarize_entries(entries: list[dict]) -> str:
    """Build a compact summary paragraph of older entries via LLM."""
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

    api_key, base_url, model = _get_model_config()

    if not api_key:
        # No API key available — fall back to a compact non-LLM summary
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
    entries = json.loads(sys.stdin.read())
    summary = summarize_entries(entries)
    print(summary)
