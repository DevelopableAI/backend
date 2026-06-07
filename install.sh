#!/usr/bin/env bash
set -e

# ── Developable Installer ────────────────────────────────────────────────────
#
# Installs:
#   1. The Python CLI package (`developable` command)
#   2. The /developable skill for Claude Code (global, clean /developable UX)
#   3. The Codex skill into ./AGENTS.md (if run inside a git repo)
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/developableai/backend/main/install.sh | bash
# ────────────────────────────────────────────────────────────────────────────

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "  ${GREEN}✓${RESET} $*"; }
warn()    { echo -e "  ${YELLOW}!${RESET} $*"; }
error()   { echo -e "  ${RED}✗${RESET} $*" >&2; }

echo ""
echo -e "${BOLD}Developable — backend generator for Express + TypeScript${RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Python version check ──────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    error "Python 3.11+ is required but not found."
    echo "    Install Python from https://python.org and re-run this script."
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    error "Python 3.11+ is required (found $PY_VERSION)."
    exit 1
fi

# ── 2. Install the package ───────────────────────────────────────────────────
echo "Installing Python package..."
if pip install developable -q; then
    info "developable package installed"
else
    warn "PyPI install failed — trying git install..."
    pip install "git+https://github.com/developableai/backend.git" -q
    info "developable installed from git"
fi

# ── 3. Find installed package data location ──────────────────────────────────
SKILL_SRC=$(python3 -c "
import pathlib, developable
print(pathlib.Path(developable.__file__).parent)
" 2>/dev/null)

if [ -z "$SKILL_SRC" ]; then
    error "Could not locate installed package data."
    exit 1
fi

# ── 4. Install Claude Code skill (new format → /developable globally) ────────
SKILL_DIR="$HOME/.claude/skills/developable"
mkdir -p "$SKILL_DIR"

if [ -f "$SKILL_SRC/skills/developable/SKILL.md" ]; then
    cp "$SKILL_SRC/skills/developable/SKILL.md" "$SKILL_DIR/SKILL.md"
    info "/developable skill installed to ~/.claude/skills/developable/SKILL.md"
else
    warn "SKILL.md not found in package — skipping new-format install"
fi

# ── 5. Also install legacy commands format (for older Claude Code versions) ──
CMDS_DIR="$HOME/.claude/commands"
mkdir -p "$CMDS_DIR"

if [ -f "$SKILL_SRC/commands/developable.md" ]; then
    cp "$SKILL_SRC/commands/developable.md" "$CMDS_DIR/developable.md"
    info "/developable command installed to ~/.claude/commands/developable.md (legacy fallback)"
else
    warn "commands/developable.md not found in package — skipping legacy install"
fi

# ── 6. OpenAI Codex: append to AGENTS.md if inside a git repo ───────────────
if git rev-parse --is-inside-work-tree &>/dev/null 2>&1; then
    if [ -f "$SKILL_SRC/skills/developable/SKILL.md" ]; then
        if [ -f "./AGENTS.md" ]; then
            # Avoid duplicate appends
            if ! grep -q "# Developable" ./AGENTS.md 2>/dev/null; then
                echo "" >> ./AGENTS.md
                echo "---" >> ./AGENTS.md
                cat "$SKILL_SRC/skills/developable/SKILL.md" >> ./AGENTS.md
                info "Codex skill appended to ./AGENTS.md"
            else
                info "AGENTS.md already contains Developable skill — skipping"
            fi
        else
            cp "$SKILL_SRC/skills/developable/SKILL.md" ./AGENTS.md
            info "AGENTS.md created from Developable skill"
        fi
    fi
fi

# ── 7. ANTHROPIC_API_KEY reminder ────────────────────────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    warn "ANTHROPIC_API_KEY is not set."
    echo "    Add to your shell profile (~/.zshrc or ~/.bashrc):"
    echo "      export ANTHROPIC_API_KEY=sk-ant-..."
    echo "    Then reload: source ~/.zshrc"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BOLD}Done!${RESET}"
echo ""
echo "  CLI:       developable schema.prisma --out ./output"
echo "  Claude Code: restart Claude Code, then type /developable"
echo ""
