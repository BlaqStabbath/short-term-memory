#!/usr/bin/env bash
# install.sh — Symlink skills and scripts into ~/.hermes
# Idempotent: safe to run multiple times.

set -euo pipefail

SKILLS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

echo "=== STM Install ==="
echo "SKILLS_SRC: $SKILLS_SRC"
echo "HERMES_HOME: $HERMES_HOME"
echo

# ── 1. Skills ─────────────────────────────────────────────────────────────────
echo "[1/3] Symlinking skills..."
mkdir -p "$HERMES_HOME/skills"

link_skill() {
    local src="$1" name
    name=$(basename "$src")
    local dest="$HERMES_HOME/skills/$name"
    if [[ -L "$dest" ]]; then
        echo "  [SKIP] $name (already symlinked)"
    elif [[ -d "$dest" ]]; then
        # Existing copy — replace with symlink
        rm -rf "$dest"
        ln -sf "$src" "$dest"
        echo "  [REPLACE] $name (copy -> symlink)"
    else
        ln -sf "$src" "$dest"
        echo "  [LINK] $name -> $src"
    fi
}

link_skill "$SKILLS_SRC/short-term-mem-sqlite"
link_skill "$SKILLS_SRC/short-term-mem-sqlite-recovery"
link_skill "$SKILLS_SRC/short-term-mem-search"

# ── 2. Scripts ───────────────────────────────────────────────────────────────
echo
echo "[2/3] Symlinking scripts..."
mkdir -p "$HERMES_HOME/scripts"

link_script() {
    local src="$1" name
    name=$(basename "$src")
    local dest="$HERMES_HOME/scripts/$name"
    if [[ -L "$dest" ]]; then
        echo "  [SKIP] $name (already symlinked)"
    elif [[ -f "$dest" ]]; then
        # Existing file — replace with symlink
        rm "$dest"
        ln -sf "$src" "$dest"
        echo "  [REPLACE] $name (file -> symlink)"
    else
        ln -sf "$src" "$dest"
        echo "  [LINK] $name -> $src"
    fi
    chmod +x "$dest"
}

link_script "$SKILLS_SRC/short-term-mem-sqlite/scripts/stm.py"
link_script "$SKILLS_SRC/short-term-mem-sqlite/scripts/llm_summarize.py"
link_script "$SKILLS_SRC/short-term-mem-search/scripts/short_term_mem_search.py"

# ── 3. Verify ────────────────────────────────────────────────────────────────
echo
echo "[3/3] Verifying stm.py..."
if "$HERMES_HOME/scripts/stm.py" count > /dev/null 2>&1; then
    echo "  [OK]   stm.py responds"
    "$HERMES_HOME/scripts/stm.py" count | sed 's/^/         /'
else
    echo "  [ERR]  stm.py failed — check Python and HERMES_HOME"
fi

echo
echo "=== Done ==="
echo
echo "Next: Apply the run_agent.py patch (one-time):"
echo "  /skill short-term-mem-sqlite-recovery"
echo
echo "After Hermes updates, re-run recovery:"
echo "  /skill short-term-mem-sqlite-recovery"
echo "  /skill short-term-mem-search"
