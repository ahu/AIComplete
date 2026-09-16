"""Render suggestions as grey ghost text using phantoms.

The first line is inserted at the cursor with LAYOUT_INLINE; the remaining lines
are merged into one LAYOUT_BLOCK hanging below the current line. This is the
closest Sublime equivalent of VSCode inline suggestions.

Rendering notes (learned the hard way):
- minihtml discards an entire inline style attribute when the font name is
  quoted, taking the color with it, so a <style> class selector is used
  instead and no quotes appear anywhere in the CSS.
- Font names may contain spaces ("JetBrains Mono NL Thin"); quoting one
  triggers the bug above, so font-family is left unset and Sublime's default
  phantom font is used.
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
    """Escape for minihtml and keep whitespace (it collapses runs of spaces)."""
    text = text.replace("\t", " " * max(1, tab_size))
    escaped = html.escape(text, quote=False)
    return escaped.replace(" ", "&nbsp;")


# ---- Theme-adaptive grey (literal hex, always rendered by minihtml) ----

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
    """t=0 is the foreground, t=1 the background; ghost text leans to the background."""
    a = _hex_to_rgb(fg)
    b = _hex_to_rgb(bg)
    if a is None or b is None:
        return None
    out = tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in (0, 1, 2))
    return "#%02x%02x%02x" % out


def _ghost_color(view, t):
    """Derive a shade of grey from the color scheme; fall back to neutral grey."""
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


def _ghost_font_size(view):
    """Convert Sublime's font_size (pt) into the px phantom CSS expects.

    Sublime's font_size is in points, and the default DPI differs per platform:
      - Windows / Linux are usually 96 DPI -> 1pt is about 4/3 px
      - macOS uses a logical 72 DPI -> 1pt is about 1px
    Treating the value as px makes ghost text noticeably smaller on Windows.
    """
    pt = view.settings().get("font_size") or 12
    try:
        pt = float(pt)
    except (TypeError, ValueError):
        pt = 12
    try:
        plat = sublime.platform()
    except Exception:
        plat = "osx"
    # 1:1 on macOS; the 96 DPI conversion on Windows/Linux
    mult = 1.0 if plat == "osx" else 4.0 / 3.0
    delta = view.settings().get("ghost_font_size_delta") or 0
    try:
        delta = float(delta)
    except (TypeError, ValueError):
        delta = 0
    return int(round(pt * mult + delta))


def _style_block(view):
    """Return <style> content (no quotes, so minihtml keeps the properties)."""
    font_size = _ghost_font_size(view)
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
    """Draw text at point. An empty text is equivalent to clear."""
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
