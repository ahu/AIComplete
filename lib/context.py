"""从 view 里抽取送给模型的上下文。"""

import os
import re

import sublime

from . import settings

# 光标后如果只剩这些字符，仍然认为「在行尾」，可以触发补全
_TRAILING_OK = re.compile(r"^[\s\)\]\}\;\,\.\:\'\"]*$")

_EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascriptreact", ".ts": "typescript", ".tsx": "typescriptreact",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".sh": "shell", ".zsh": "shell", ".bash": "shell",
    ".sql": "sql", ".html": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".lua": "lua", ".vue": "vue", ".dart": "dart",
    ".m": "objective-c", ".mm": "objective-cpp", ".scala": "scala",
    ".ex": "elixir", ".exs": "elixir", ".hs": "haskell", ".r": "r",
}


def language_of(view):
    """猜一个语言标识，优先看扩展名，其次看 syntax 名。"""
    fname = view.file_name()
    if fname:
        ext = os.path.splitext(fname)[1].lower()
        if ext in _EXT_LANG:
            return _EXT_LANG[ext]
    syntax = view.settings().get("syntax") or ""
    base = os.path.splitext(os.path.basename(syntax))[0]
    return base.lower().replace(" ", "") or "text"


def is_enabled_for(view):
    """判断这个 view 该不该跑补全。"""
    if view is None or view.is_read_only():
        return False
    if view.settings().get("is_widget"):
        return False
    if view.settings().get("ai_complete_disabled"):
        return False
    if view.element() is not None:
        # 控制台、输入框之类的内建 element
        return False

    syntax = (view.settings().get("syntax") or "").lower()
    for bad in settings.get("disabled_syntaxes") or []:
        if bad and str(bad).lower() in syntax:
            return False

    sel = view.sel()
    if not sel or len(sel) != 1 or not sel[0].empty():
        return False

    scope = view.scope_name(sel[0].b)
    for bad in settings.get("disabled_scopes") or []:
        if bad and scope.startswith(bad):
            return False
    return True


def at_trigger_position(view):
    """光标位置是否适合触发（默认要求行尾或后面只剩闭合符号/空白）。"""
    if not settings.get("trigger_only_at_line_end"):
        return True
    sel = view.sel()
    if not sel:
        return False
    point = sel[0].b
    line = view.line(point)
    rest = view.substr(sublime.Region(point, line.end()))
    return bool(_TRAILING_OK.match(rest))


def _other_files_snippets(view):
    """同窗口其它文件的开头片段，给模型一点项目风味。"""
    if not settings.get("use_open_files_context"):
        return []
    window = view.window()
    if window is None:
        return []
    limit = int(settings.get("open_files_max") or 0)
    chars = int(settings.get("open_files_chars") or 0)
    if limit <= 0 or chars <= 0:
        return []

    out = []
    my_lang = language_of(view)
    for other in window.views():
        if len(out) >= limit:
            break
        if other.id() == view.id() or other.size() == 0:
            continue
        if other.size() > 2 * 1024 * 1024:
            continue
        if language_of(other) != my_lang:
            continue
        name = other.file_name() or other.name() or "untitled"
        body = other.substr(sublime.Region(0, min(chars, other.size())))
        out.append({"name": os.path.basename(name), "body": body})
    return out


def build(view):
    """打包一份上下文字典。取不到就返回 None。"""
    sel = view.sel()
    if not sel:
        return None
    point = sel[0].b

    pre_chars = int(settings.get("prefix_chars") or 3000)
    suf_chars = int(settings.get("suffix_chars") or 1000)

    prefix = view.substr(sublime.Region(max(0, point - pre_chars), point))
    suffix = view.substr(sublime.Region(point, min(view.size(), point + suf_chars)))

    line_region = view.line(point)
    current_line_prefix = view.substr(sublime.Region(line_region.begin(), point))

    fname = view.file_name()
    return {
        "prefix": prefix,
        "suffix": suffix,
        "point": point,
        # 光标所在的 scope，用来判断这里是代码还是注释/字符串
        "scope": view.scope_name(point),
        "language": language_of(view),
        "filename": os.path.basename(fname) if fname else "untitled",
        "filepath": fname or "",
        "line_prefix": current_line_prefix,
        "indent": re.match(r"[\t ]*", current_line_prefix).group(0),
        "related": _other_files_snippets(view),
        "truncated_head": point > pre_chars,
    }


def cache_key(ctx):
    """同样的前后文不必重复请求。只看尾部，够区分了。"""
    return "\u0000".join([
        ctx.get("filepath", ""),
        ctx["prefix"][-800:],
        ctx["suffix"][:200],
    ])
