#!/usr/bin/env bash
# uninstall.sh — Remove Hermes STM completely.
#
# Does:
#   1. Remove symlinked scripts (~/.hermes/scripts/)
#   2. Restore run_agent.py (remove @stm_track decorator via git)
#   3. Remove skill symlinks (~/.hermes/skills/)
#   4. Optionally remove stm.db
#   5. Optionally pause the hermes-post-update-recovery cron job
#
# Safe: uses git checkout to restore patched files; does not touch
# scripts that weren't installed by this project.

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RUN_AGENT="$HERMES_HOME/hermes-agent/run_agent.py"

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

log()  { echo -e "${GREEN}[OK]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*" >&2; }
fail() { echo -e "${RED}[FAIL]${RESET} $*" >&2; exit 1; }

section() {
    echo; echo -e "${BOLD}${BLUE}── $* ──${RESET}"
}

echo -e "${BOLD}Hermes STM Uninstall${RESET}"
echo "HERMES_HOME: $HERMES_HOME"
echo

# ── 1. Scripts ─────────────────────────────────────────────────────────────────
section "1. Removing scripts"

SCRIPTS=(
    stm.py
    build_topic_index.py
    patch_stm_decorator.py
    short_term_mem_search.py
    llm_summarize.py   # deprecated, may still be lingering
)

for name in "${SCRIPTS[@]}"; do
    dest="$HERMES_HOME/scripts/$name"
    if [[ -L "$dest" ]]; then
        rm "$dest"
        log "Removed symlink: $dest"
    elif [[ -f "$dest" ]]; then
        rm "$dest"
        log "Removed file: $dest"
    else
        echo "  [SKIP] $name (not found)"
    fi
done

# ── 2. Restore run_agent.py ───────────────────────────────────────────────────
section "2. Restoring run_agent.py (removing @stm_track decorator)"

if [[ ! -f "$RUN_AGENT" ]]; then
    warn "run_agent.py not found at $RUN_AGENT — skipping"
elif git -C "$(dirname "$RUN_AGENT")" rev-parse 2>/dev/null; then
    # Check if it actually has the decorator before trying to restore
    if grep -q "def stm_track(" "$RUN_AGENT" 2>/dev/null; then
        git -C "$(dirname "$RUN_AGENT")" checkout run_agent.py
        log "run_agent.py restored from git (decorator removed)"
    else
        echo "  [SKIP] @stm_track decorator not found in run_agent.py"
    fi
else
    warn "run_agent.py is not in a git repo — cannot auto-restore"
    warn "Manual fix: delete the stm_track decorator from $RUN_AGENT"
    warn "Or re-install Hermes to get a fresh run_agent.py"
fi

# ── 3. Skill symlinks ─────────────────────────────────────────────────────────
section "3. Removing skill symlinks"

SKILLS=(
    short-term-mem-sqlite
    short-term-mem-sqlite-recovery
    short-term-mem-search
)

for name in "${SKILLS[@]}"; do
    dest="$HERMES_HOME/skills/$name"
    if [[ -L "$dest" ]]; then
        rm "$dest"
        log "Removed symlink: $dest"
    elif [[ -d "$dest" ]]; then
        rm -rf "$dest"
        warn "Removed directory: $dest (was not a symlink)"
    else
        echo "  [SKIP] $name (not found)"
    fi
done

# ── 4. Cron job ───────────────────────────────────────────────────────────────
section "4. Pausing hermes-post-update-recovery cron job"

if hermes cron list 2>/dev/null | grep -q "e0dde69b683a"; then
    read -rp "Pause the hermes-post-update-recovery cron job? (Y/n): " confirm
    confirm="${confirm:-Y}"
    if [[ "$confirm" =~ ^[Yy] ]]; then
        hermes cron pause e0dde69b683a 2>/dev/null && \
            log "Paused hermes-post-update-recovery (id: e0dde69b683a)" || \
            warn "Could not pause cron job — you may need to do it manually"
    else
        echo "  [SKIP] Cron job left running"
    fi
else
    echo "  [SKIP] hermes-post-update-recovery cron job not found"
fi

# ── 5. stm.db ─────────────────────────────────────────────────────────────────
section "5. Optionally remove stm.db"

DB_PATH="$HERMES_HOME/sessions/stm.db"
if [[ -f "$DB_PATH" ]]; then
    echo "  stm.db found at $DB_PATH ($(wc -c < "$DB_PATH" 2>/dev/null || echo '?') bytes)"
    read -rp "  Remove stm.db? This deletes ALL short-term memory history. (y/N): " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm "$DB_PATH"
        log "Removed stm.db — all session history deleted"
    else
        echo "  [KEEP] stm.db"
    fi
else
    echo "  [SKIP] stm.db not found"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}✓ Uninstall complete.${RESET}"
echo
echo "To re-install:"
echo "  cd ~/DEV/SRC/short-term-memory && ./install.sh"
