"""补全调度：防抖、并发、过期丢弃、缓存、候选管理。

关键约束：Sublime 的 async worker 是单线程队列，网络请求绝对不能放
在那上面跑，否则整个插件系统的异步事件都会被堵住。所以真正的 HTTP
在独立的 daemon 线程里，回主线程只用 set_timeout。
"""

import re
import threading
import time
from collections import OrderedDict

import sublime

from . import client, context, ghost, postprocess, settings

STATUS_KEY = "ai_complete"
_CACHE_SIZE = 64
_PREFIX_GUARD = 64  # 复用建议时用多少字符校验上下文没跑偏

_WORD_RE = re.compile(r"^[ \t]*(?:[A-Za-z0-9_]+|[^A-Za-z0-9_\s])")


class Suggestion(object):
    __slots__ = ("view_id", "candidates", "index", "point", "guard", "change_count")

    def __init__(self, view_id, candidates, point, guard, change_count):
        self.view_id = view_id
        self.candidates = candidates
        self.index = 0
        self.point = point
        self.guard = guard
        self.change_count = change_count

    @property
    def text(self):
        if not self.candidates:
            return ""
        return self.candidates[self.index % len(self.candidates)]

    @property
    def total(self):
        return len(self.candidates)


class Engine(object):
    def __init__(self):
        self._suggestions = {}      # view_id -> Suggestion
        self._gen = {}              # view_id -> 最新请求代号
        self._inflight = set()      # 正在请求中的 view_id
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._last_error = ("", 0.0)

    # ---------------- 状态 ----------------

    def _status(self, view, text):
        if not settings.get("show_status"):
            try:
                view.erase_status(STATUS_KEY)
            except Exception:
                pass
            return
        try:
            if text:
                view.set_status(STATUS_KEY, text)
            else:
                view.erase_status(STATUS_KEY)
        except Exception:
            pass

    def _report_error(self, view, message, detail=""):
        now = time.time()
        last_msg, last_at = self._last_error
        self._status(view, "AI err")
        settings.debug("error:", message, detail)
        if message == last_msg and now - last_at < 60:
            return
        self._last_error = (message, now)
        sublime.status_message("AhuAIComplete: %s" % message)

    # ---------------- 查询 ----------------

    def current(self, view):
        if view is None:
            return None
        return self._suggestions.get(view.id())

    def is_visible(self, view):
        sug = self.current(view)
        return bool(sug and sug.text)

    def is_self_inflicted(self, view):
        """这次 buffer 变化是不是我们自己插入 ghost 造成的。

        接受建议后 Sublime 照样会派发 on_modified，如果不认出来，
        剩余的 leftover 建议会被自己误杀。
        """
        sug = self.current(view)
        if sug is None:
            return False
        if sug.change_count != view.change_count():
            return False
        sel = view.sel()
        return len(sel) == 1 and sel[0].empty() and sel[0].b == sug.point

    # ---------------- 生命周期 ----------------

    def cancel(self, view, keep_status=False):
        """撤掉建议，并让在途请求作废。"""
        if view is None:
            return
        vid = view.id()
        self._gen[vid] = self._gen.get(vid, 0) + 1
        self._suggestions.pop(vid, None)
        ghost.clear(view)
        if not keep_status:
            self._status(view, "")

    def forget(self, view_id):
        self._suggestions.pop(view_id, None)
        self._gen.pop(view_id, None)
        self._inflight.discard(view_id)
        ghost.forget(view_id)

    def reset_all(self):
        self._suggestions.clear()
        self._gen.clear()
        self._inflight.clear()
        with self._lock:
            self._cache.clear()
        ghost.clear_all()

    # ---------------- 缓存 ----------------

    def _cache_get(self, key):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return list(self._cache[key])
        return None

    def _cache_put(self, key, value):
        with self._lock:
            self._cache[key] = list(value)
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)

    # ---------------- 复用 ----------------

    def try_extend(self, view):
        """用户又敲了几个字符，看能不能直接沿用现有建议，省一次请求。

        能沿用返回 True，此时 ghost 已经就地更新。
        """
        sug = self.current(view)
        if sug is None:
            return False

        sel = view.sel()
        if len(sel) != 1 or not sel[0].empty():
            return False
        point = sel[0].b

        if point <= sug.point:
            return False
        typed = view.substr(sublime.Region(sug.point, point))
        if not typed or "\n" in typed:
            return False

        # 校验光标之前那段没被别处改动带偏
        guard_start = max(0, sug.point - _PREFIX_GUARD)
        guard_now = view.substr(sublime.Region(guard_start, sug.point))
        if guard_now != sug.guard:
            return False

        survivors = []
        for cand in sug.candidates:
            rest = postprocess.consume_typed(cand, typed)
            if rest:
                survivors.append(rest)
        if not survivors:
            return False

        sug.candidates = survivors
        sug.index = 0
        sug.point = point
        sug.guard = view.substr(sublime.Region(max(0, point - _PREFIX_GUARD), point))
        sug.change_count = view.change_count()
        ghost.show(view, sug.text, point, sug.index, sug.total)
        self._status(view, "AI ok")
        return True

    # ---------------- 请求 ----------------

    def schedule(self, view, force=False):
        """防抖后发起一次补全。force=True 走手动触发路径。"""
        if view is None:
            return
        if not force and not settings.get("enabled"):
            return
        if not context.is_enabled_for(view):
            self.cancel(view)
            return
        if not force and not context.at_trigger_position(view):
            self.cancel(view)
            return
        if not force and settings.get("hide_when_autocomplete_visible") \
                and view.is_auto_complete_visible():
            self.cancel(view)
            return

        vid = view.id()
        token = self._gen.get(vid, 0) + 1
        self._gen[vid] = token

        delay = 0 if force else max(0, int(settings.get("debounce_ms") or 0))
        sublime.set_timeout(lambda: self._fire(view, token, force), delay)

    def _fire(self, view, token, force):
        vid = view.id()
        if self._gen.get(vid) != token:
            return          # 期间又有新输入
        if not view.is_valid():
            return
        if not force and not context.is_enabled_for(view):
            return

        ctx = context.build(view)
        if ctx is None:
            return
        if not ctx["prefix"].strip() and not ctx["suffix"].strip():
            return

        change_count = view.change_count()
        key = context.cache_key(ctx)

        cached = self._cache_get(key)
        if cached is not None:
            settings.debug("cache hit")
            self._deliver(view, token, ctx, cached, change_count, from_cache=True)
            return

        self._status(view, "AI ...")
        self._inflight.add(vid)
        num = max(1, int(settings.get("num_suggestions") or 1))

        thread = threading.Thread(
            target=self._worker, args=(view, token, ctx, change_count, key, num)
        )
        thread.daemon = True
        thread.start()

    def _worker(self, view, token, ctx, change_count, key, num):
        error = None
        raw = []
        try:
            raw = client.complete(ctx, num)
        except client.ClientError as exc:
            error = exc
        except Exception as exc:  # 防御：worker 里抛异常会静默丢线程
            error = client.ClientError("内部错误：%s" % exc.__class__.__name__, str(exc))

        def finish():
            self._inflight.discard(view.id())
            if error is not None:
                if self._gen.get(view.id()) == token:
                    self._report_error(view, error.message, error.detail)
                return
            self._cache_put(key, raw)
            self._deliver(view, token, ctx, raw, change_count)

        sublime.set_timeout(finish, 0)

    def _deliver(self, view, token, ctx, raw_list, change_count, from_cache=False):
        vid = view.id()
        if self._gen.get(vid) != token:
            settings.debug("drop stale response")
            return
        if not view.is_valid():
            return
        if view.change_count() != change_count:
            settings.debug("buffer moved on, drop")
            self._status(view, "")
            return

        sel = view.sel()
        if len(sel) != 1 or not sel[0].empty() or sel[0].b != ctx["point"]:
            self._status(view, "")
            return

        max_lines = int(settings.get("max_lines") or 12)
        # FIM 接口只该吐代码，返回散文说明请求走岔了，直接丢
        reject_prose = settings.provider_name() in ("ollama", "openai_fim")
        cleaned = []
        for raw in raw_list:
            text = postprocess.clean(raw, ctx, max_lines, reject_prose)
            if text and text not in cleaned:
                cleaned.append(text)

        if not cleaned:
            self._status(view, "")
            ghost.clear(view)
            self._suggestions.pop(vid, None)
            return

        point = ctx["point"]
        guard = view.substr(sublime.Region(max(0, point - _PREFIX_GUARD), point))
        sug = Suggestion(vid, cleaned, point, guard, view.change_count())
        self._suggestions[vid] = sug
        ghost.show(view, sug.text, point, sug.index, sug.total)
        self._status(view, "AI ok")

    # ---------------- 候选切换 ----------------

    def cycle(self, view, delta):
        sug = self.current(view)
        if sug is None or sug.total <= 1:
            return False
        sug.index = (sug.index + delta) % sug.total
        ghost.show(view, sug.text, sug.point, sug.index, sug.total)
        return True

    # ---------------- 接受 ----------------

    def take(self, view, portion="all"):
        """取出要插入的文本。返回 (point, text, leftover)。

        portion: all / word / line
        leftover 非空时说明只接受了一部分，插入后应该继续显示剩下的。
        """
        sug = self.current(view)
        if sug is None or not sug.text:
            return None

        sel = view.sel()
        if len(sel) != 1 or not sel[0].empty() or sel[0].b != sug.point:
            self.cancel(view)
            return None

        text = sug.text
        if portion == "word":
            match = _WORD_RE.match(text)
            if match and match.end() < len(text):
                head = text[: match.end()]
                return sug.point, head, text[match.end():]
            return sug.point, text, ""
        if portion == "line":
            nl = text.find("\n")
            if nl != -1:
                return sug.point, text[: nl + 1], text[nl + 1:]
            return sug.point, text, ""
        return sug.point, text, ""

    def after_insert(self, view, new_point, leftover):
        """插入完成后调用：要么收尾，要么把剩余部分接着显示。"""
        vid = view.id()
        self._gen[vid] = self._gen.get(vid, 0) + 1
        if leftover and leftover.strip():
            guard = view.substr(
                sublime.Region(max(0, new_point - _PREFIX_GUARD), new_point)
            )
            sug = Suggestion(vid, [leftover], new_point, guard, view.change_count())
            self._suggestions[vid] = sug
            ghost.show(view, leftover, new_point, 0, 1)
            self._status(view, "AI ok")
        else:
            self._suggestions.pop(vid, None)
            ghost.clear(view)
            self._status(view, "")


engine = Engine()
