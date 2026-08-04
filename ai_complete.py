"""AhuAIComplete —— Sublime Text 的内联 AI 代码补全。

灰色 ghost text 跟着光标出现，Tab 接受，Esc 丢弃。
后端可以是本地 Ollama，也可以是任何 OpenAI 兼容服务。
"""

import threading

import sublime
import sublime_plugin

from .lib import client, context, ghost, settings
from .lib.engine import STATUS_KEY, engine

SETTINGS_TAG = "ai_complete_settings"


# ======================================================================
# 事件监听
# ======================================================================

class AiCompleteListener(sublime_plugin.EventListener):
    def __init__(self):
        super().__init__()
        self._change_counts = {}

    # ---- 键位上下文：让 Tab / Esc 只在有建议时被劫持 ----
    def on_query_context(self, view, key, operator, operand, match_all):
        if key != "ai_complete_visible":
            return None
        value = engine.is_visible(view)
        if operator == sublime.OP_EQUAL:
            return value == bool(operand)
        if operator == sublime.OP_NOT_EQUAL:
            return value != bool(operand)
        return None

    def on_modified_async(self, view):
        if not settings.get("enabled"):
            return
        if not context.is_enabled_for(view):
            engine.cancel(view)
            return

        self._change_counts[view.id()] = view.change_count()

        # 刚刚接受建议造成的变化，不要打断自己
        if engine.is_self_inflicted(view):
            return

        # 用户继续往下打字且和建议吻合 —— 直接沿用，不重新请求
        if engine.try_extend(view):
            return

        engine.cancel(view, keep_status=True)
        engine.schedule(view)

    def on_selection_modified_async(self, view):
        vid = view.id()
        # 只有「纯移动光标」才需要撤建议；打字引起的位移交给 on_modified
        if self._change_counts.get(vid) == view.change_count():
            sug = engine.current(view)
            if sug is not None:
                sel = view.sel()
                if len(sel) != 1 or not sel[0].empty() or sel[0].b != sug.point:
                    engine.cancel(view)
        self._change_counts[vid] = view.change_count()

    def on_deactivated_async(self, view):
        engine.cancel(view)

    def on_pre_close(self, view):
        engine.forget(view.id())
        self._change_counts.pop(view.id(), None)

    def on_text_command(self, view, command_name, args):
        # 自动补全弹窗弹出来的时候，两层 UI 叠一起很难看
        if command_name in ("auto_complete", "show_overlay", "undo", "redo"):
            if engine.is_visible(view):
                engine.cancel(view)
        return None


# ======================================================================
# 接受建议
# ======================================================================

def _accept(view, edit, portion):
    taken = engine.take(view, portion)
    if taken is None:
        return
    point, text, leftover = taken
    if not text:
        return

    ghost.clear(view)
    inserted = view.insert(edit, point, text)
    new_point = point + inserted

    view.sel().clear()
    view.sel().add(sublime.Region(new_point, new_point))
    engine.after_insert(view, new_point, leftover)
    view.show(new_point)


class AiCompleteAcceptCommand(sublime_plugin.TextCommand):
    """接受整条建议。"""

    def run(self, edit):
        _accept(self.view, edit, "all")

    def is_enabled(self):
        return engine.is_visible(self.view)


class AiCompleteAcceptWordCommand(sublime_plugin.TextCommand):
    """只接受下一个词。"""

    def run(self, edit):
        _accept(self.view, edit, "word")

    def is_enabled(self):
        return engine.is_visible(self.view)


class AiCompleteAcceptLineCommand(sublime_plugin.TextCommand):
    """只接受下一行。"""

    def run(self, edit):
        _accept(self.view, edit, "line")

    def is_enabled(self):
        return engine.is_visible(self.view)


# ======================================================================
# 其它命令
# ======================================================================

class AiCompleteDismissCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        engine.cancel(self.view)

    def is_enabled(self):
        return engine.is_visible(self.view)


class AiCompleteRequestCommand(sublime_plugin.TextCommand):
    """手动触发一次，忽略「行尾才补全」之类的限制。"""

    def run(self, edit):
        engine.cancel(self.view, keep_status=True)
        engine.schedule(self.view, force=True)

    def is_enabled(self):
        return context.is_enabled_for(self.view)


class AiCompleteCycleCommand(sublime_plugin.TextCommand):
    def run(self, edit, delta=1):
        engine.cycle(self.view, int(delta))

    def is_enabled(self):
        sug = engine.current(self.view)
        return bool(sug and sug.total > 1)


class AiCompleteToggleCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        conf = sublime.load_settings(settings.SETTINGS_FILE)
        new_value = not conf.get("enabled", True)
        conf.set("enabled", new_value)
        sublime.save_settings(settings.SETTINGS_FILE)
        if not new_value:
            ghost.clear_all()
            engine.reset_all()
        sublime.status_message(
            "AhuAIComplete: %s" % ("已开启" if new_value else "已关闭")
        )

    def description(self):
        state = "关闭" if settings.get("enabled") else "开启"
        return "AhuAIComplete: %s" % state


class AiCompleteToggleViewCommand(sublime_plugin.TextCommand):
    """只在当前文件里停用，不影响其它文件。"""

    def run(self, edit):
        vs = self.view.settings()
        disabled = not vs.get("ai_complete_disabled", False)
        vs.set("ai_complete_disabled", disabled)
        if disabled:
            engine.cancel(self.view)
        sublime.status_message(
            "AhuAIComplete: 当前文件%s" % ("已停用" if disabled else "已启用")
        )


class AiCompletePingCommand(sublime_plugin.WindowCommand):
    """打一次真实请求，验证 base_url / api_key / model 配对不对。"""

    def run(self):
        sublime.status_message("AhuAIComplete: 正在测试…")

        def work():
            ok, message = client.ping()
            sublime.set_timeout(lambda: self._show(ok, message), 0)

        thread = threading.Thread(target=work)
        thread.daemon = True
        thread.start()

    def _show(self, ok, message):
        panel = self.window.create_output_panel("ai_complete")
        panel.set_read_only(False)
        panel.run_command(
            "append",
            {"characters": ("✓ " if ok else "✗ ") + message + "\n"},
        )
        panel.set_read_only(True)
        self.window.run_command("show_panel", {"panel": "output.ai_complete"})


# ======================================================================
# 设置 / 键位菜单（适配 Add Repository 时包文件夹名可能不是 AhuAIComplete）
# ======================================================================

def _own_package_path():
    """找到本插件包所在的 Packages/xxx 资源路径。

    Package Control 官方频道按 name 字段装到 Packages/AhuAIComplete/；
    但 Add Repository 直接按仓库名装到 Packages/AIComplete/。菜单里写死
    AhuAIComplete 会打不开。这里通过 ai_complete.py 的位置反推包名。
    """
    try:
        candidates = sublime.find_resources("ai_complete.py")
    except Exception:
        return None
    for res in candidates:
        if res.startswith("Packages/User/"):
            continue
        if res.endswith("/ai_complete.py"):
            return res[:-len("/ai_complete.py")]
    return None


def _own_resource(filename):
    pkg = _own_package_path()
    if pkg:
        return "%s/%s" % (pkg, filename)
    return None


class AiCompleteEditSettingsCommand(sublime_plugin.ApplicationCommand):
    """Preferences → Package Settings → AhuAIComplete → Settings"""

    def run(self):
        base = _own_resource("AhuAIComplete.sublime-settings")
        if not base:
            sublime.status_message("AhuAIComplete: 找不到默认设置文件")
            return
        sublime.run_command("edit_settings", {
            "base_file": base,
            "default": "{\n\t$0\n}\n",
        })


class AiCompleteEditKeyBindingsCommand(sublime_plugin.ApplicationCommand):
    """Preferences → Package Settings → AhuAIComplete → Key Bindings"""

    def run(self):
        plat = {"windows": "Windows", "linux": "Linux", "osx": "OSX"}.get(
            sublime.platform(), "Windows"
        )
        base = _own_resource("Default (%s).sublime-keymap" % plat)
        if not base:
            base = _own_resource("Default.sublime-keymap")
        if not base:
            sublime.status_message("AhuAIComplete: 找不到默认键位文件")
            return
        sublime.run_command("edit_settings", {
            "base_file": base,
            "default": "[\n\t$0\n]\n",
        })


# ======================================================================
# 插件生命周期
# ======================================================================

def _on_settings_changed():
    settings.debug("settings reloaded")
    engine.reset_all()


def plugin_loaded():
    settings.add_on_change(SETTINGS_TAG, _on_settings_changed)
    settings.debug("loaded, provider =", settings.provider_name())


def plugin_unloaded():
    settings.clear_on_change(SETTINGS_TAG)
    try:
        for window in sublime.windows():
            for view in window.views():
                view.erase_status(STATUS_KEY)
        engine.reset_all()
    except Exception:
        pass
