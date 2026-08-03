"""集中读取插件设置，避免每处都写一遍 load_settings。"""

import sublime

SETTINGS_FILE = "AhuAIComplete.sublime-settings"

_DEFAULTS = {
    "enabled": True,
    "debounce_ms": 300,
    "provider": "ollama",
    "providers": {},
    "timeout": 20,
    "verify_ssl": True,
    "prefix_chars": 3000,
    "suffix_chars": 1000,
    "use_open_files_context": True,
    "open_files_max": 3,
    "open_files_chars": 600,
    "max_lines": 12,
    "num_suggestions": 3,
    "hide_when_autocomplete_visible": True,
    "trigger_only_at_line_end": True,
    "disabled_scopes": [],
    "disabled_syntaxes": [],
    "show_status": True,
    "debug": False,
}

_PROVIDER_DEFAULTS = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "max_tokens": 256,
    "temperature": 0.2,
    "extra_headers": {},
    "extra_body": {},
}


def get(key, default=None):
    if default is None:
        default = _DEFAULTS.get(key)
    try:
        return sublime.load_settings(SETTINGS_FILE).get(key, default)
    except Exception:
        return default


def provider_name():
    return str(get("provider") or "ollama").strip()


def provider_config():
    """返回当前 provider 的配置，已合并默认值。"""
    name = provider_name()
    all_providers = get("providers") or {}
    conf = dict(_PROVIDER_DEFAULTS)
    conf.update(all_providers.get(name) or {})
    conf["name"] = name
    # 允许用环境变量兜底，方便不把 key 写进配置文件
    if not conf.get("api_key"):
        import os

        for env in ("AICOMPLETE_API_KEY", "OPENAI_API_KEY"):
            if os.environ.get(env):
                conf["api_key"] = os.environ[env]
                break
    conf["timeout"] = get("timeout")
    conf["verify_ssl"] = bool(get("verify_ssl"))
    return conf


def debug(*args):
    if get("debug"):
        print("[AhuAIComplete]", *args)


def add_on_change(tag, callback):
    try:
        sublime.load_settings(SETTINGS_FILE).add_on_change(tag, callback)
    except Exception:
        pass


def clear_on_change(tag):
    try:
        sublime.load_settings(SETTINGS_FILE).clear_on_change(tag)
    except Exception:
        pass
