"""用 phantom 把建议画成灰色的 ghost text。

第一行走 LAYOUT_INLINE 直接插在光标处，剩下的行合成一个 LAYOUT_BLOCK
挂在当前行下方。这是 Sublime 里最接近 VSCode 内联建议的做法。

渲染要点（踩过坑）：
- minihtml 会整体丢弃带「引号字体名」的 inline style 属性，连 color 一起没，
  所以这里改用 <style> 类选择器，CSS 里不出现任何引号，最稳。
- 字体名可能带空格（如 "JetBrains Mono NL Thin"），一旦加引号就会触发上面的坑，
  所以这里干脆不指定 font-family，用 Sublime 自己的 phantom 默认字体即可。
"""

import html

import sublime

PHANTOM_KEY_INLINE = "ai_complete_inline"
PHANTOM_KEY_BLOCK = "ai_complete_block"

# view_id -> (PhantomSet, PhantomSet)
_sets = {}


def _phantom_sets(view):
    vid = view.id()
    if vid not in _sets:
        _sets[vid] = (
            sublime.PhantomSet(view, PHANTOM_KEY_INLINE),
            sublime.PhantomSet(view, PHANTOM_KEY_BLOCK),
        )
    return _sets[vid]


def _escape(text, tab_size):
    """转义成 minihtml，并且把空白保住（minihtml 会折叠连续空格）。"""
    text = text.replace("\t", " " * max(1, tab_size))
    escaped = html.escape(text, quote=False)
    return escaped.replace(" ", "&nbsp;")


# ---- 主题自适应灰阶（字面量 hex，minihtml 一定渲染）----

def _hex_to_rgb(h):
    h = (h or "").lstrip("#").strip()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _blend(fg, bg, t):
    """t=0 取前景，t=1 取背景；ghost 取偏背景的值，越偏越淡。"""
    a = _hex_to_rgb(fg)
    b = _hex_to_rgb(bg)
    if a is None or b is None:
        return None
    out = tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in (0, 1, 2))
    return "#%02x%02x%02x" % out


def _ghost_color(view, t):
    """从 color scheme 算一档灰。失败回退到中性灰。"""
    try:
        st = view.style()
        fg = st.get("foreground")
        bg = st.get("background")
        blended = _blend(fg, bg, t)
        if blended:
            return blended
    except Exception:
        pass
    return "#8a8a8a"


def _style_block(view):
    """返回一段 <style> 内容（CSS 里不带任何引号，避免 minihtml 丢属性）。"""
    font_size = int(view.settings().get("font_size") or 12)
    color = _ghost_color(view, 0.55)
    badge_color = _ghost_color(view, 0.7)
    return (
        "html,body{margin:0;padding:0;background-color:transparent;}"
        ".ai-ghost{color:%s;font-weight:normal;font-style:normal;"
        "font-size:%dpx;line-height:1;}"
        "div.ai-ghost{margin:0;padding:0;}"
        ".ai-badge{color:%s;font-weight:normal;font-size:0.8rem;}"
    ) % (color, font_size, badge_color)


def _badge(view, index, total):
    if total <= 1:
        return ""
    return (
        '<span class="ai-badge">&nbsp;&nbsp;[{i}/{n}]</span>'
    ).format(i=index + 1, n=total)


def show(view, text, point, index=0, total=1):
    """在 point 处画出 text。text 为空则等同于 clear。"""
    if not text:
        clear(view)
        return

    inline_set, block_set = _phantom_sets(view)
    tab_size = int(view.settings().get("tab_size") or 4)
    style_block = _style_block(view)

    lines = text.split("\n")
    first = lines[0]
    rest = lines[1:]

    inline_phantoms = []
    if first or not rest:
        body = _escape(first, tab_size) or "&nbsp;"
        content = (
            '<body id="ai-complete-inline">'
            "<style>{style}</style>"
            '<span class="ai-ghost">{body}</span>{badge}'
            "</body>"
        ).format(style=style_block, body=body,
                 badge=_badge(view, index, total) if not rest else "")
        inline_phantoms.append(
            sublime.Phantom(
                sublime.Region(point, point), content, sublime.LAYOUT_INLINE
            )
        )
    inline_set.update(inline_phantoms)

    block_phantoms = []
    if rest:
        rows = "".join(
            '<div class="ai-ghost">{body}</div>'.format(
                body=_escape(line, tab_size) or "&nbsp;"
            )
            for line in rest
        )
        content = (
            '<body id="ai-complete-block">'
            "<style>{style}</style>"
            "{rows}{badge}"
            "</body>"
        ).format(style=style_block, rows=rows,
                 badge=_badge(view, index, total))
        line_end = view.line(point).end()
        block_phantoms.append(
            sublime.Phantom(
                sublime.Region(line_end, line_end), content, sublime.LAYOUT_BLOCK
            )
        )
    block_set.update(block_phantoms)


def clear(view):
    if view is None:
        return
    vid = view.id()
    if vid in _sets:
        try:
            _sets[vid][0].update([])
            _sets[vid][1].update([])
        except Exception:
            pass
    try:
        view.erase_phantoms(PHANTOM_KEY_INLINE)
        view.erase_phantoms(PHANTOM_KEY_BLOCK)
    except Exception:
        pass


def forget(view_id):
    _sets.pop(view_id, None)


def clear_all():
    for window in sublime.windows():
        for view in window.views():
            clear(view)
    _sets.clear()
