"""Pure logic tests; no Sublime runtime required.

    python3 tests/test_logic.py

Most completion-quality problems come from text cleaning (models repeating the
prefix, adding fences, duplicating brackets), so these cases matter.
"""

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

# ---- Minimal fake sublime module, so ghost can be imported ----
_fake = types.ModuleType("sublime")
_fake.LAYOUT_INLINE = 0
_fake.LAYOUT_BELOW = 1
_fake.LAYOUT_BLOCK = 2
_fake.OP_EQUAL = 0
_fake.OP_NOT_EQUAL = 1


class _Region(object):
    def __init__(self, a, b=None):
        self.a = a
        self.b = a if b is None else b

    def begin(self):
        return self.a

    def end(self):
        return self.b


class _Phantom(object):
    def __init__(self, region, content, layout, on_navigate=None):
        self.region = region
        self.content = content
        self.layout = layout


class _PhantomSet(object):
    def __init__(self, view, key):
        self.phantoms = []

    def update(self, phantoms):
        self.phantoms = phantoms


_fake.Region = _Region
_fake.Phantom = _Phantom
_fake.PhantomSet = _PhantomSet
_fake.windows = lambda: []
_fake.load_settings = lambda name: types.SimpleNamespace(
    get=lambda k, d=None: d, set=lambda k, v: None,
    add_on_change=lambda *a: None, clear_on_change=lambda *a: None)
_fake.set_timeout = lambda fn, delay=0: None
_fake.status_message = lambda msg: None
sys.modules.setdefault("sublime", _fake)

PKG = os.path.basename(ROOT)
postprocess = __import__("%s.lib.postprocess" % PKG, fromlist=["postprocess"])
ghost = __import__("%s.lib.ghost" % PKG, fromlist=["ghost"])
engine_module = __import__("%s.lib.engine" % PKG, fromlist=["engine"])


def ctx(prefix="", suffix="", line_prefix="", language="python", scope="source.python"):
    return {"prefix": prefix, "suffix": suffix, "line_prefix": line_prefix,
            "language": language, "scope": scope}


PROSE = ("The provided function `fibonacci` is designed to compute the n-th "
         "Fibonacci number using recursion.")


class TestFence(unittest.TestCase):
    def test_full_fence(self):
        self.assertEqual(
            postprocess.strip_code_fence("```python\nreturn a + b\n```"),
            "return a + b",
        )

    def test_no_fence(self):
        self.assertEqual(postprocess.strip_code_fence("a + b"), "a + b")

    def test_bare_fence(self):
        self.assertEqual(
            postprocess.strip_code_fence("```\nx = 1\n```"), "x = 1"
        )


class TestRepeatedPrefix(unittest.TestCase):
    def test_model_repeats_current_line(self):
        out = postprocess.strip_repeated_prefix(
            "    return a + b", "def add(a, b):\n    return "
        )
        self.assertEqual(out, "a + b")

    def test_no_overlap_untouched(self):
        out = postprocess.strip_repeated_prefix("a + b", "def add(a, b):\n    ")
        self.assertEqual(out, "a + b")


class TestSuffixOverlap(unittest.TestCase):
    def test_closing_paren_not_duplicated(self):
        self.assertEqual(
            postprocess.trim_overlap_with_suffix("value)", ")"), "value"
        )

    def test_multi_char_overlap(self):
        self.assertEqual(
            postprocess.trim_overlap_with_suffix("foo(bar));", "));"), "foo(bar"
        )

    def test_blank_suffix_untouched(self):
        self.assertEqual(
            postprocess.trim_overlap_with_suffix("value)", "\n\n"), "value)"
        )


class TestClean(unittest.TestCase):
    def test_full_pipeline(self):
        raw = "```python\n    return a + b\n```"
        out = postprocess.clean(
            raw,
            ctx(prefix="def add(a, b):\n    return ", line_prefix="    return "),
            max_lines=12,
        )
        self.assertEqual(out, "a + b")

    def test_special_tokens_cut(self):
        out = postprocess.clean(
            "x = 1<|endoftext|>and more junk", ctx(), max_lines=12
        )
        self.assertEqual(out, "x = 1")

    def test_max_lines(self):
        raw = "\n".join("line%d" % i for i in range(30))
        out = postprocess.clean(raw, ctx(), max_lines=3)
        self.assertEqual(out.count("\n"), 2)

    def test_double_indent_removed(self):
        # cursor after pure indentation and the model adds the indent again
        out = postprocess.clean(
            "        total = 0", ctx(prefix="def f():\n        ",
                                     line_prefix="        "), max_lines=12
        )
        self.assertEqual(out, "total = 0")

    def test_whitespace_only_is_dropped(self):
        self.assertEqual(postprocess.clean("   \n  \n", ctx(), 12), "")

    def test_trailing_blank_lines_dropped(self):
        out = postprocess.clean("x = 1\n\n\n", ctx(), 12)
        self.assertEqual(out, "x = 1")


class TestConsumeTyped(unittest.TestCase):
    def test_reuse_after_typing(self):
        self.assertEqual(postprocess.consume_typed("value = 1", "val"), "ue = 1")

    def test_mismatch_returns_none(self):
        self.assertIsNone(postprocess.consume_typed("value = 1", "x"))

    def test_typing_whole_suggestion_returns_none(self):
        self.assertIsNone(postprocess.consume_typed("abc", "abc"))


class TestProseRejection(unittest.TestCase):
    """Chatty degradation must be blocked without harming real code."""

    def test_chatty_explanation_rejected(self):
        self.assertTrue(postprocess.looks_like_prose(PROSE, ctx()))

    def test_rejected_by_clean_when_enabled(self):
        self.assertEqual(postprocess.clean(PROSE, ctx(), 12, reject_prose=True), "")

    def test_kept_when_flag_off(self):
        # chat providers leave the flag off, so behaviour is unchanged
        self.assertNotEqual(postprocess.clean(PROSE, ctx(), 12), "")

    # ---- these are legitimate completions that must survive ----

    def test_sql_kept(self):
        sql = "SELECT name, price FROM items WHERE qty > 0 ORDER BY price DESC"
        self.assertFalse(postprocess.looks_like_prose(sql, ctx(language="sql")))

    def test_code_with_symbols_kept(self):
        code = "this.currentUserProfileName = this.session.getActiveUser().name"
        self.assertFalse(postprocess.looks_like_prose(code, ctx()))

    def test_long_docstring_kept(self):
        # cursor inside a string: natural language is perfectly normal
        doc = ("This function computes the nth fibonacci number using "
               "recursion and returns it")
        self.assertFalse(postprocess.looks_like_prose(
            doc, ctx(scope="source.python string.quoted.docstring")))

    def test_comment_kept(self):
        comment = "# The result here is cached so repeated calls stay cheap"
        self.assertFalse(postprocess.looks_like_prose(
            comment, ctx(scope="source.python comment.line")))

    def test_markdown_never_rejected(self):
        self.assertFalse(postprocess.looks_like_prose(
            PROSE, ctx(language="markdown", scope="text.html.markdown")))

    def test_short_line_kept(self):
        self.assertFalse(postprocess.looks_like_prose("The x = 1", ctx()))


class TestFimSuffix(unittest.TestCase):
    """An empty suffix pushes Ollama into chat mode and must be handled."""

    def setUp(self):
        self.client = __import__("%s.lib.client" % PKG, fromlist=["client"])

    def test_empty_suffix_becomes_newline(self):
        self.assertEqual(self.client._fim_suffix({"suffix": ""}), "\n")

    def test_missing_suffix_becomes_newline(self):
        self.assertEqual(self.client._fim_suffix({}), "\n")

    def test_real_suffix_untouched(self):
        self.assertEqual(
            self.client._fim_suffix({"suffix": "\nprint(x)\n"}), "\nprint(x)\n"
        )


class _FakeSettings(object):
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


class _FakeView(object):
    def __init__(self, vid, settings):
        self._vid = vid
        self._settings = _FakeSettings(settings)

    def id(self):
        return self._vid

    def settings(self):
        return self._settings

    def line(self, point):
        # fake: a region for the line, with an end just beyond point
        return _Region(point, point + 1)

    def style(self):
        # mimics view.style(): foreground / background from the color scheme
        fg = self._settings.get("foreground")
        bg = self._settings.get("background")
        out = {}
        if fg:
            out["foreground"] = fg
        if bg:
            out["background"] = bg
        return out


class TestGhostEscaping(unittest.TestCase):
    def test_html_is_escaped(self):
        out = ghost._escape("<div> & 'x'", 4)
        self.assertIn("&lt;div&gt;", out)
        self.assertIn("&amp;", out)

    def test_spaces_preserved(self):
        out = ghost._escape("a    b", 4)
        self.assertEqual(out.count("&nbsp;"), 4)

    def test_tab_expanded(self):
        out = ghost._escape("\tx", 4)
        self.assertEqual(out, "&nbsp;&nbsp;&nbsp;&nbsp;x")

    def test_ghost_color_blends_toward_background(self):
        """Ghost grey is blended from fg+bg into a literal hex that minihtml renders."""
        view = _FakeView(3, {"foreground": "#ffffff", "background": "#000000"})
        # t=0.55: white (255) leaning to black (0) -> about 115, grey
        self.assertEqual(ghost._ghost_color(view, 0.55), "#737373")

    def test_ghost_color_fallback_when_style_missing(self):
        view = _FakeView(4, {})  # no foreground/background
        self.assertEqual(ghost._ghost_color(view, 0.55), "#8a8a8a")


class TestGhostRendering(unittest.TestCase):
    """Rendering must use a <style> class selector, never a quoted inline style.

    Old bug: an inline style with font-family: 'JetBrains Mono NL Thin' made
    minihtml drop the whole style attribute, colour included -> white text.
    """

    def test_style_block_has_hex_color_no_quotes(self):
        view = _FakeView(1, {"foreground": "#ffffff", "background": "#1e1e1e",
                             "font_size": 15})
        block = ghost._style_block(view)
        self.assertIn("#", block)
        self.assertIn("font-weight:normal", block)
        # key: no quotes may appear in the CSS, or the old bug returns
        self.assertNotIn('"', block)
        self.assertNotIn("'", block)
        self.assertNotIn("font-family", block)
        self.assertNotIn("color(var(", block)
        self.assertNotIn("opacity", block)

    def test_show_uses_class_not_inline_style(self):
        view = _FakeView(2, {"foreground": "#d8dee9", "background": "#303841",
                             "font_size": 15, "tab_size": 4})
        ghost.show(view, "return fib(n)", 0)
        inline_set, _ = ghost._sets[view.id()]
        content = inline_set.phantoms[0].content
        self.assertIn('class="ai-ghost"', content)
        self.assertIn("#", content)
        # no quoted inline style may reappear (root cause of the old bug)
        self.assertNotIn('style="font-family', content)
        self.assertNotIn("'", content)
        ghost.clear(view)

    def test_show_multiline_renders_block(self):
        view = _FakeView(3, {"foreground": "#d8dee9", "background": "#303841",
                             "font_size": 15, "tab_size": 4})
        ghost.show(view, "a\nb\nc", 0)
        _, block_set = ghost._sets[view.id()]
        content = block_set.phantoms[0].content
        self.assertIn('class="ai-ghost"', content)
        self.assertIn("div", content)
        ghost.clear(view)


class TestCandidateCycling(unittest.TestCase):
    """Cycling only works with multiple candidates; a single one returns False."""

    def setUp(self):
        self.engine = engine_module.Engine()
        self.view = _FakeView(99, {"font_face": "Menlo", "font_size": 13,
                                   "tab_size": 4})
        self.S = engine_module.Suggestion

    def test_single_candidate_cannot_cycle(self):
        self.engine._suggestions[99] = self.S(99, ["only"], 0, "x", 0)
        self.assertFalse(self.engine.cycle(self.view, 1))
        self.assertFalse(self.engine.cycle(self.view, -1))

    def test_cycle_forward_wraps(self):
        self.engine._suggestions[99] = self.S(99, ["a", "b", "c"], 0, "x", 0)
        self.assertTrue(self.engine.cycle(self.view, 1))
        self.assertEqual(self.engine.current(self.view).index, 1)
        self.engine.cycle(self.view, 1)
        self.engine.cycle(self.view, 1)  # third call wraps back to 0
        self.assertEqual(self.engine.current(self.view).index, 0)

    def test_cycle_backward_wraps(self):
        self.engine._suggestions[99] = self.S(99, ["a", "b", "c"], 0, "x", 0)
        self.assertTrue(self.engine.cycle(self.view, -1))
        self.assertEqual(self.engine.current(self.view).index, 2)

    def test_cycle_text_advances(self):
        self.engine._suggestions[99] = self.S(99, ["aaa", "bbb"], 0, "x", 0)
        self.engine.cycle(self.view, 1)
        self.assertEqual(self.engine.current(self.view).text, "bbb")


if __name__ == "__main__":
    unittest.main(verbosity=2)
