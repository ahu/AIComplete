"""AhuAIComplete -- inline AI code completion for Sublime Text.

Grey ghost text appears at the cursor as you type: Tab accepts it, Esc
dismisses it. The backend can be a local Ollama instance or any
OpenAI-compatible service.
"""

import threading

import sublime
import sublime_plugin

from .lib import client, context, ghost, settings
from .lib.engine import STATUS_KEY, engine

SETTINGS_TAG = "ai_complete_settings"


# ======================================================================
# Event listeners
# ======================================================================

class AiCompleteListener(sublime_plugin.EventListener):
    def __init__(self):
        super().__init__()
        self._change_counts = {}

    # ---- Context key: only hijack Tab / Esc while a suggestion is visible ----
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

        # We just inserted a suggestion ourselves -- do not interrupt.
        if engine.is_self_inflicted(view):
            return

        # The user kept typing and it still matches -- reuse it, skip the request.
        if engine.try_extend(view):
            return

        engine.cancel(view, keep_status=True)
        engine.schedule(view)

    def on_selection_modified_async(self, view):
        vid = view.id()
        # Only pure cursor movement cancels; typing is handled by on_modified.
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
        # The two overlays look bad when the autocomplete popup opens.
        if command_name in ("auto_complete", "show_overlay", "undo", "redo"):
            if engine.is_visible(view):
                engine.cancel(view)
        return None


# ======================================================================
# Accepting suggestions
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
    """Accept the whole suggestion."""

    def run(self, edit):
        _accept(self.view, edit, "all")

    def is_enabled(self):
        return engine.is_visible(self.view)


class AiCompleteAcceptWordCommand(sublime_plugin.TextCommand):
    """Accept only the next word."""

    def run(self, edit):
        _accept(self.view, edit, "word")

    def is_enabled(self):
        return engine.is_visible(self.view)


class AiCompleteAcceptLineCommand(sublime_plugin.TextCommand):
    """Accept only the next line."""

    def run(self, edit):
        _accept(self.view, edit, "line")

    def is_enabled(self):
        return engine.is_visible(self.view)


# ======================================================================
# Other commands
# ======================================================================

class AiCompleteDismissCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        engine.cancel(self.view)

    def is_enabled(self):
        return engine.is_visible(self.view)


class AiCompleteRequestCommand(sublime_plugin.TextCommand):
    """Request a completion manually, ignoring the line-end trigger rule."""

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
            "AhuAIComplete: %s" % ("enabled" if new_value else "disabled")
        )

    def description(self):
        state = "Disable" if settings.get("enabled") else "Enable"
        return "AhuAIComplete: %s" % state


class AiCompleteToggleViewCommand(sublime_plugin.TextCommand):
    """Disable completion for this view only; other files are unaffected."""

    def run(self, edit):
        vs = self.view.settings()
        disabled = not vs.get("ai_complete_disabled", False)
        vs.set("ai_complete_disabled", disabled)
        if disabled:
            engine.cancel(self.view)
        sublime.status_message(
            "AhuAIComplete: this view is now %s" % ("disabled" if disabled else "enabled")
        )


class AiCompletePingCommand(sublime_plugin.WindowCommand):
    """Send one real request to verify base_url / api_key / model."""

    def run(self):
        sublime.status_message("AhuAIComplete: testing connection...")

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
# Settings / key binding menu (the package folder may not be named
# AhuAIComplete when installed via Add Repository)
# ======================================================================

def _own_package_path():
    """Find the Packages/xxx resource path this package lives in.

    The channel installs into Packages/<name>/, but "Add Repository" installs
    into Packages/<repo name>/ instead. Hardcoding AhuAIComplete would fail to
    open. The folder is derived from the location of ai_complete.py.
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
    """Return a base_file that edit_settings accepts: ${packages}/<pkg>/<file>.

    The literal "${packages}" prefix is required; the "Packages/xxx" string
    returned by find_resources cannot be used directly. Sublime's
    EditSettingsCommand does:
        base_path = base_file.replace("${packages}", "res://Packages")
    Only paths starting with res:// are treated as resources. Anything else
    degrades into a relative filesystem path, os.path.exists fails, and the
    editor reports "could not be opened".
    """
    pkg = _own_package_path()
    if not pkg:
        return None
    # pkg looks like "Packages/AIComplete" -- turn it into "${packages}/AIComplete"
    if pkg.startswith("Packages/"):
        pkg = "${packages}/" + pkg[len("Packages/"):]
    return "%s/%s" % (pkg, filename)


def _resource_exists(base_file):
    """base_file looks like ${packages}/X/Y -- check it exists, Sublime style."""
    path = base_file.replace("${packages}", "Packages")
    try:
        return path in sublime.find_resources(path.rsplit("/", 1)[-1])
    except Exception:
        return False


class AiCompleteEditSettingsCommand(sublime_plugin.ApplicationCommand):
    """Preferences → Package Settings → AhuAIComplete → Settings"""

    def run(self):
        base = _own_resource("AhuAIComplete.sublime-settings")
        if not base:
            sublime.status_message("AhuAIComplete: default settings file not found")
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
        # Prefer the platform-specific file; fall back to the generic keymap
        # if one is missing, so edit_settings does not report an error.
        base = None
        for name in ("Default (%s).sublime-keymap" % plat,
                     "Default.sublime-keymap"):
            candidate = _own_resource(name)
            if candidate and _resource_exists(candidate):
                base = candidate
                break
        if not base:
            sublime.status_message("AhuAIComplete: default key bindings file not found")
            return
        sublime.run_command("edit_settings", {
            "base_file": base,
            "default": "[\n\t$0\n]\n",
        })


# ======================================================================
# Plugin lifecycle
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
