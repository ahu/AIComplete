"""纯逻辑测试，不需要 Sublime 运行时。

    python3 tests/test_logic.py

补全质量的锅八成出在文本清洗上（模型抄前缀、套围栏、补重复括号），
所以这几条一定要盯住。
"""

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

# ---- 造一个够用的假 sublime，好让 ghost 模块能被 import ----
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
        # 光标停在纯缩进之后，模型又给了一遍缩进
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
    """模型退化成聊天时要挡住，但不能误伤正常代码。"""

    def test_chatty_explanation_rejected(self):
        self.assertTrue(postprocess.looks_like_prose(PROSE, ctx()))

    def test_rejected_by_clean_when_enabled(self):
        self.assertEqual(postprocess.clean(PROSE, ctx(), 12, reject_prose=True), "")

    def test_kept_when_flag_off(self):
        # chat provider 不开这个开关，行为保持原样
        self.assertNotEqual(postprocess.clean(PROSE, ctx(), 12), "")

    # ---- 以下都是不该被误杀的正常补全 ----

    def test_sql_kept(self):
        sql = "SELECT name, price FROM items WHERE qty > 0 ORDER BY price DESC"
        self.assertFalse(postprocess.looks_like_prose(sql, ctx(language="sql")))

    def test_code_with_symbols_kept(self):
        code = "this.currentUserProfileName = this.session.getActiveUser().name"
        self.assertFalse(postprocess.looks_like_prose(code, ctx()))

    def test_long_docstring_kept(self):
        # 光标在字符串里，出现自然语言完全正常
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
    """空 suffix 会让 Ollama 走进聊天分支，必须兜住。"""

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
        # 假实现：返回该点所在「行」的 region，行尾随便给个比 point 大的值
        return _Region(point, point + 1)

    def style(self):
        # 模拟 Sublime 的 view.style()：返回 color scheme 的前景/背景
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
        """ghost 灰阶由前景+背景混合得到，是字面量 hex（minihtml 一定渲染）。"""
        view = _FakeView(3, {"foreground": "#ffffff", "background": "#000000"})
        # t=0.55：白(255)偏向黑(0) -> 约 115，灰
        self.assertEqual(ghost._ghost_color(view, 0.55), "#737373")

    def test_ghost_color_fallback_when_style_missing(self):
        view = _FakeView(4, {})  # 没有 foreground/background
        self.assertEqual(ghost._ghost_color(view, 0.55), "#8a8a8a")


class TestGhostRendering(unittest.TestCase):
    """渲染必须走 <style> 类选择器，绝不能出现带引号的 inline style。

    旧 bug：inline style 里写 font-family: 'JetBrains Mono NL Thin'，
    单引号让 minihtml 把整个 style 属性丢掉，连 color 一起没了 -> 白字。
    """

    def test_style_block_has_hex_color_no_quotes(self):
        view = _FakeView(1, {"foreground": "#ffffff", "background": "#1e1e1e",
                             "font_size": 15})
        block = ghost._style_block(view)
        self.assertIn("#", block)
        self.assertIn("font-weight:normal", block)
        # 关键：CSS 里不能有任何引号，否则旧 bug 复现
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
        # 不应再出现带引号的 inline style（旧 bug 根源）
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
    """候选切换：多候选才切得动，单候选必须 return False。"""

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
        self.engine.cycle(self.view, 1)  # 第三次绕回 0
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
