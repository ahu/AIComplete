#!/usr/bin/env bash
# Link AhuAIComplete into the Sublime Text Packages directory.
#
# Note: Sublime's file watcher does not follow symlinks, so after a symlink
# install the source must be changed and Sublime restarted; there is no hot
# reload. Use --copy when you intend to edit the code often.
#
#   ./install.sh              install (symlink)
#   ./install.sh --copy       install (copy, handy on other machines)
#   ./install.sh --uninstall  uninstall

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="AhuAIComplete"

case "$(uname -s)" in
    Darwin) PKGS="$HOME/Library/Application Support/Sublime Text/Packages" ;;
    Linux)  PKGS="$HOME/.config/sublime-text/Packages" ;;
    *)      echo "Unrecognized platform. Please copy the directory into Packages/ by hand."; exit 1 ;;
esac

# Also support the legacy Sublime Text 3 paths
if [ ! -d "$PKGS" ]; then
    for alt in \
        "$HOME/Library/Application Support/Sublime Text 3/Packages" \
        "$HOME/.config/sublime-text-3/Packages"
    do
        [ -d "$alt" ] && PKGS="$alt" && break
    done
fi

if [ ! -d "$PKGS" ]; then
    echo "Sublime Text Packages directory not found: $PKGS"
    echo "Start Sublime Text once so it creates the directory."
    exit 1
fi

DEST="$PKGS/$NAME"

if [ "${1:-}" = "--uninstall" ]; then
    if [ -L "$DEST" ] || [ -d "$DEST" ]; then
        rm -rf "$DEST"
        echo "Uninstalled: $DEST"
    else
        echo "Not installed; nothing to uninstall."
    fi
    exit 0
fi

if [ -L "$DEST" ] || [ -e "$DEST" ]; then
    echo "Destination already exists, removing it first: $DEST"
    rm -rf "$DEST"
fi

if [ "${1:-}" = "--copy" ]; then
    mkdir -p "$DEST"
    # Only copy what is needed at run time
    for item in ai_complete.py lib .python-version \
                AhuAIComplete.sublime-settings Main.sublime-menu \
                Default.sublime-commands; do
        cp -R "$SRC/$item" "$DEST/"
    done
    cp -R "$SRC"/*.sublime-keymap "$DEST/"
    find "$DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
    echo "Copied to: $DEST"
else
    ln -s "$SRC" "$DEST"
    echo "Symlinked: $DEST -> $SRC"
fi

echo
echo "Next steps:"
echo "  1. Restart Sublime Text (required if it is already running; symlinks do not hot reload)"
echo "  2. Run \"AhuAIComplete: Test Connection\" from the command palette to verify the backend"
echo "  3. The default backend is a local Ollama; to use something else, edit:"
echo "     Preferences -> Package Settings -> AhuAIComplete -> Settings"
