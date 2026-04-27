#!/usr/bin/env bash
# install.sh — Full installation of Hermes Short-Term Memory (STM)
#
# Does everything in one pass:
#   1. Symlink skills into ~/.hermes/skills/
#   2. Symlink scripts into ~/.hermes/scripts/
#   3. Run patch_stm_decorator.py (idempotent — safe to re-run)
#   4. Ensure hermes-post-update-recovery cron job exists
#   5. Verify stm.db is writable
#
# Idempotent: safe to run multiple times.
# Run once after clone, or after Hermes updates if you want to re-sync.

set -euo pipefail

# ── Detect paths ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PATCH_SCRIPT="$HERMES_HOME/scripts/patch_stm_decorator.py"
RUN_AGENT="$HERMES_HOME/hermes-agent/run_agent.py"

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

log()  { echo -e "${GREEN}[OK]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*" >&2; }
fail() { echo -e "${RED}[FAIL]${RESET} $*" >&2; exit 1; }

section() {
    echo; echo -e "${BOLD}${BLUE}── $* ──${RESET}"
}

# ── 1. Skills ───────────────────────────────────────────────────────────────────
section "1. Symlinking skills"

SKILLS=(
    "$SCRIPT_DIR/short-term-mem-sqlite"
    "$SCRIPT_DIR/short-term-mem-sqlite-recovery"
    "$SCRIPT_DIR/short-term-mem-search"
)

for src in "${SKILLS[@]}"; do
    name=$(basename "$src")
    dest="$HERMES_HOME/skills/$name"
    if [[ -L "$dest" && "$(readlink -f "$dest")" == "$src" ]]; then
        echo "  [SKIP] $name (already correctly symlinked)"
    elif [[ -L "$dest" ]]; then
        rm "$dest"
        ln -sf "$src" "$dest"
        log "$name (symlink updated)"
    elif [[ -d "$dest" ]]; then
        # Existing directory (e.g. bundled skill) — replace
        rm -rf "$dest"
        ln -sf "$src" "$dest"
        warn "$name (replaced existing directory with symlink)"
    else
        ln -sf "$src" "$dest"
        log "$name → $dest"
    fi
done

# ── 2. Scripts ─────────────────────────────────────────────────────────────────
section "2. Symlinking scripts"

# Core STM scripts
SCRIPTS=(
    "$SCRIPT_DIR/short-term-mem-sqlite/scripts/stm.py"
    "$SCRIPT_DIR/short-term-mem-sqlite/scripts/build_topic_index.py"
    "$SCRIPT_DIR/short-term-mem-sqlite/scripts/patch_stm_decorator.py"
    "$SCRIPT_DIR/short-term-mem-search/scripts/short_term_mem_search.py"
)

for src in "${SCRIPTS[@]}"; do
    name=$(basename "$src")
    dest="$HERMES_HOME/scripts/$name"

    # Skip deprecation-warned llm_summarize.py — no longer used
    [[ "$name" == "llm_summarize.py" ]] && continue

    if [[ -L "$dest" && "$(readlink -f "$dest")" == "$src" ]]; then
        echo "  [SKIP] $name (already correctly symlinked)"
    elif [[ -L "$dest" || -f "$dest" ]]; then
        rm -f "$dest"
        ln -sf "$src" "$dest"
        log "$name (file replaced with symlink)"
    else
        ln -sf "$src" "$dest"
        log "$name → $dest"
    fi
    chmod +x "$dest" 2>/dev/null || true
done

# ── 3. stm.db ─────────────────────────────────────────────────────────────────
section "3. Ensuring stm.db exists"

DB_DIR="$HERMES_HOME/sessions"
DB_PATH="$DB_DIR/stm.db"
mkdir -p "$DB_DIR"

if [[ -f "$DB_PATH" ]]; then
    log "stm.db already exists ($(wc -l < "$DB_PATH" 2>/dev/null || echo '?') lines)"
else
    # Create empty DB with schema
    python3 - "$DB_PATH" <<'EOF'
import sqlite3, sys
DB_PATH = sys.argv[1]
conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("""
    CREATE TABLE IF NOT EXISTS entries (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT    NOT NULL,
        prompt     TEXT    NOT NULL,
        actions    TEXT    DEFAULT "",
        result     TEXT    DEFAULT "",
        status     TEXT    DEFAULT "executing",
        timestamp  TEXT    NOT NULL
    )
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session_id)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_id ON entries(id)")
conn.commit()
conn.close()
print("created")
EOF
    log "stm.db created at $DB_PATH"
fi

# ── 4. patch_stm_decorator.py ───────────────────────────────────────────────────
section "4. Applying @stm_track decorator to run_agent.py"

if [[ ! -f "$RUN_AGENT" ]]; then
    warn "run_agent.py not found at $RUN_AGENT — skipping patch"
    warn "After Hermes install, re-run: $PATCH_SCRIPT --apply"
else
    # The patch script is now symlinked — resolve to real path
    PATCH_REAL=$(readlink -f "$PATCH_SCRIPT" 2>/dev/null || echo "$PATCH_SCRIPT")

    if [[ -f "$PATCH_REAL" ]]; then
        # Check current state
        if grep -q "def stm_track(" "$RUN_AGENT" 2>/dev/null; then
            log "Decorator already present — skipping"
        else
            log "Applying decorator (idempotent)..."
            python3 "$PATCH_REAL" --apply
        fi
    else
        warn "patch_stm_decorator.py not found — skipping patch"
        warn "After install, run: python3 $PATCH_SCRIPT --apply"
    fi
fi

# ── 5. Cron job ────────────────────────────────────────────────────────────────
section "5. Ensuring hermes-post-update-recovery cron job"

# Check if job already exists ( Hermes cron job IDs are stable)
if hermes cron list 2>/dev/null | grep -q "e0dde69b683a\|hermes-post-update-recovery"; then
    log "hermes-post-update-recovery cron job already exists (id: e0dde69b683a)"
else
    log "Creating hermes-post-update-recovery cron job (daily 05:00 UTC)..."
    hermes cron create \
        --name "hermes-post-update-recovery" \
        --schedule "0 5 * * *" \
        --repeat 100 \
        --deliver local \
        --prompt "Check if the stm_track decorator is present in run_agent.py and re-apply it if missing.

Run: python3 ~/.hermes/scripts/patch_stm_decorator.py --apply

Then verify: python3 ~/.hermes/scripts/patch_stm_decorator.py --verify

If verify fails, report the error. Otherwise confirm the decorator is in place." \
        2>/dev/null || warn "Could not create cron job — you may need to create it manually"
fi

# ── 6. Verify ──────────────────────────────────────────────────────────────────
section "6. Verification"

verify_ok=true

# stm.py
if "$HERMES_HOME/scripts/stm.py" count >/dev/null 2>&1; then
    count=$("$HERMES_HOME/scripts/stm.py" count 2>/dev/null | grep -oE '[0-9]+' | head -1)
    log "stm.py responds — $count entries in stm.db"
else
    warn "stm.py failed"
    verify_ok=false
fi

# build_topic_index.py
if python3 "$HERMES_HOME/scripts/build_topic_index.py" >/dev/null 2>&1; then
    log "build_topic_index.py runs without error"
else
    warn "build_topic_index.py returned non-zero (may be empty DB — OK for fresh install)"
fi

# patch script
if "$PATCH_SCRIPT" --verify 2>/dev/null; then
    log "patch_stm_decorator.py — VERIFY OK"
else
    warn "patch_stm_decorator.py — VERIFY MISSING (re-run install.sh to fix)"
    verify_ok=false
fi

# run_agent.py
if grep -q "def stm_track(" "$RUN_AGENT" 2>/dev/null; then
    log "@stm_track decorator found in run_agent.py"
else
    warn "@stm_track NOT found in run_agent.py"
    verify_ok=false
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo
if $verify_ok; then
    echo -e "${BOLD}${GREEN}✓ STM installed successfully.${RESET}"
else
    echo -e "${YELLOW}⚠ STM partially installed — review warnings above.${RESET}"
fi
echo
echo "Usage: starts automatically on every Hermes turn."
echo "Check entries:  python3 ~/.hermes/scripts/stm.py count"
echo "Manual patch:   python3 ~/.hermes/scripts/patch_stm_decorator.py --verify"
echo "Uninstall:     cd $SCRIPT_DIR && ./uninstall.sh"
