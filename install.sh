#!/usr/bin/env bash
# 把 AhuAIComplete 链接进 Sublime Text 的 Packages 目录。
#
# 注意：Sublime 的文件监视不跟随 symlink，软链安装后再改源码不会热重载，
# 必须重启 Sublime。要频繁改代码就用 --copy 装到 Packages 里直接编辑。
#
#   ./install.sh            安装（软链）
#   ./install.sh --copy     安装（复制，适合装到别的机器）
#   ./install.sh --uninstall 卸载

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="AhuAIComplete"

case "$(uname -s)" in
    Darwin) PKGS="$HOME/Library/Application Support/Sublime Text/Packages" ;;
    Linux)  PKGS="$HOME/.config/sublime-text/Packages" ;;
    *)      echo "不认识的系统，请手动把目录放进 Packages/"; exit 1 ;;
esac

# 兼容 Sublime Text 3 的老路径
if [ ! -d "$PKGS" ]; then
    for alt in \
        "$HOME/Library/Application Support/Sublime Text 3/Packages" \
        "$HOME/.config/sublime-text-3/Packages"
    do
        [ -d "$alt" ] && PKGS="$alt" && break
    done
fi

if [ ! -d "$PKGS" ]; then
    echo "找不到 Sublime Text 的 Packages 目录：$PKGS"
    echo "先启动一次 Sublime Text 让它把目录建出来。"
    exit 1
fi

DEST="$PKGS/$NAME"

if [ "${1:-}" = "--uninstall" ]; then
    if [ -L "$DEST" ] || [ -d "$DEST" ]; then
        rm -rf "$DEST"
        echo "已卸载：$DEST"
    else
        echo "没装过，无需卸载。"
    fi
    exit 0
fi

if [ -L "$DEST" ] || [ -e "$DEST" ]; then
    echo "目标已存在，先移除：$DEST"
    rm -rf "$DEST"
fi

if [ "${1:-}" = "--copy" ]; then
    mkdir -p "$DEST"
    # 只拷运行需要的东西
    for item in ai_complete.py lib .python-version \
                AhuAIComplete.sublime-settings Main.sublime-menu \
                Default.sublime-commands; do
        cp -R "$SRC/$item" "$DEST/"
    done
    cp -R "$SRC"/*.sublime-keymap "$DEST/"
    find "$DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
    echo "已复制到：$DEST"
else
    ln -s "$SRC" "$DEST"
    echo "已软链：$DEST -> $SRC"
fi

echo
echo "下一步："
echo "  1. 重启 Sublime Text（已经开着的话必须重启，软链不会触发热重载）"
echo "  2. 命令面板搜 «AhuAIComplete: 测试与服务的连通性» 确认后端通了"
echo "  3. 默认后端是本地 Ollama，改成别的请编辑："
echo "     Preferences → Package Settings → AhuAIComplete → Settings"
