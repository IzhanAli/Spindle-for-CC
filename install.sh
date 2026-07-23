#!/usr/bin/env bash
#
# spindle installer — symlinks the launcher onto your PATH, seeds the news
# cache, and registers the Claude Code SessionStart hook.
#
# Two ways to run it:
#
#   1. From a checkout
#        ./install.sh                 full install (symlink + seed + hook)
#        ./install.sh --no-refresh    skip the initial network fetch
#
#   2. Straight from the web (curl | bash) — this script clones the repo into
#      $SPINDLE_INSTALL_DIR and re-runs itself from there:
#        curl -fsSL https://raw.githubusercontent.com/izhanali/spindle-claude-code/main/install.sh | bash
#        curl -fsSL https://raw.githubusercontent.com/izhanali/spindle-claude-code/main/install.sh | bash -s -- --no-refresh
#
# Environment overrides (all optional):
#   SPINDLE_BIN_DIR       where to symlink the launcher   (default ~/.local/bin)
#   SPINDLE_INSTALL_DIR   where to clone in curl mode      (default ~/.local/share/spindle-claude-code)
#   SPINDLE_REPO_URL      git URL to clone in curl mode    (default the GitHub repo below)
#   SPINDLE_REF           branch/tag to check out          (default main)
#
set -euo pipefail

REPO_URL="${SPINDLE_REPO_URL:-https://github.com/izhanali/spindle-claude-code.git}"
REF="${SPINDLE_REF:-main}"
BIN_DIR="${SPINDLE_BIN_DIR:-$HOME/.local/bin}"

# Resolve the directory this script lives in. Empty when piped via stdin
# (curl | bash), which is exactly how we detect "no checkout around us".
SELF_DIR=""
_source="${BASH_SOURCE[0]:-}"
if [ -n "$_source" ] && [ -f "$_source" ]; then
  SELF_DIR="$(cd "$(dirname "$_source")" && pwd)"
fi

# ── curl mode: clone the repo, then hand off to its install.sh ─────────────
if [ -z "$SELF_DIR" ] || [ ! -f "$SELF_DIR/src/spindle/__main__.py" ]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "error: git is required for the curl install (or clone the repo and run ./install.sh)" >&2
    exit 1
  fi
  INSTALL_DIR="${SPINDLE_INSTALL_DIR:-$HOME/.local/share/spindle-claude-code}"
  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "spindle: updating existing checkout at $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$REF"
    git -C "$INSTALL_DIR" checkout -q FETCH_HEAD
  else
    echo "spindle: cloning $REPO_URL ($REF) -> $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 --branch "$REF" "$REPO_URL" "$INSTALL_DIR"
  fi
  exec bash "$INSTALL_DIR/install.sh" "$@"
fi

REPO="$SELF_DIR"
LAUNCHER="$REPO/bin/spindle"

# --no-refresh may arrive as a positional arg or as SPINDLE_NO_REFRESH=1.
NO_REFRESH="${SPINDLE_NO_REFRESH:-}"
for arg in "$@"; do
  [ "$arg" = "--no-refresh" ] && NO_REFRESH=1
done

# 1. Python check ----------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found on PATH" >&2
  exit 1
fi
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "spindle: using python3 ($PYVER)"
python3 - <<'PY' || { echo "error: Python 3.8+ required" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)
PY

# 2. Symlink the launcher onto PATH ---------------------------------------
chmod +x "$LAUNCHER"
mkdir -p "$BIN_DIR"
ln -sf "$LAUNCHER" "$BIN_DIR/spindle"
echo "spindle: linked $BIN_DIR/spindle -> $LAUNCHER"

# 3. Seed the cache + register the hook -----------------------------------
if [ -n "$NO_REFRESH" ]; then
  "$LAUNCHER" install --no-refresh
else
  "$LAUNCHER" install
fi

# 4. PATH hint -------------------------------------------------------------
case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *)
    echo
    echo "note: $BIN_DIR is not on your PATH. Add this to your shell profile:"
    echo "    export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac
