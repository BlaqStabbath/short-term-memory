#!/usr/bin/env bash
# uninstall.sh — Remove STM skills, scripts, and optionally the DB.
# Safe: restores patched Hermes files first if they haven't been overwritten since install.

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

echo "=== STM Uninstall ==="
echo

# ── 1. Scripts ───────────────────────────────────────────────────────────────
echo "[1/4] Removing scripts..."
for script in stm.py short_term_mem_search.py; do
    if [[ -L "$HERMES_HOME/scripts/$script" ]]; then
        rm "$HERMES_HOME/scripts/$script"
        echo "  [RM]   $HERMES_HOME/scripts/$script"
    else
        echo "  [SKIP] $script (not symlinked)"
    fi
done

# ── 2. Patched Hermes files ─────────────────────────────────────────────────
echo
echo "[2/4] Restoring patched Hermes files..."
restore_if_patched() {
    local file="$1"
    if [[ -f "$HERMES_HOME/hermes-agent/$file" ]] && git -C "$HERMES_HOME/hermes-agent" diff --quiet "$file" 2>/dev/null; then
        git -C "$HERMES_HOME/hermes-agent" checkout "$file"
        echo "  [RESTORE] $file"
    else
        echo "  [SKIP]    $file (not patched or already overwritten by update)"
    fi
}

restore_if_patched "run_agent.py"
restore_if_patched "tools/session_search_tool.py"

# ── 3. Skill symlinks ───────────────────────────────────────────────────────
echo
echo "[3/4] Removing skill symlinks..."
for skill in short-term-mem-sqlite short-term-mem-sqlite-recovery short-term-mem-search; do
    if [[ -L "$HERMES_HOME/skills/$skill" ]]; then
        rm "$HERMES_HOME/skills/$skill"
        echo "  [RM]   $HERMES_HOME/skills/$skill"
    else
        echo "  [SKIP] $skill (not symlinked)"
    fi
done

# ── 4. Database (optional) ───────────────────────────────────────────────────
echo
echo "[4/4] Optionally remove stm.db..."
if [[ -f "$HERMES_HOME/sessions/stm.db" ]]; then
    echo "  stm.db exists at $HERMES_HOME/sessions/stm.db"
    read -rp "  Remove it? (y/N) " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm "$HERMES_HOME/sessions/stm.db"
        echo "  [RM]   stm.db (all session history deleted)"
    else
        echo "  [KEEP] stm.db"
    fi
else
    echo "  [SKIP] stm.db (not found)"
fi

echo
echo "=== Done ==="
