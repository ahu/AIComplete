"""Talk to the model service. Standard library only: Sublime's Python runs it.

Three backends are supported:
  openai      /chat/completions      -- generic, any OpenAI-compatible service
  openai_fim  /completions + suffix  -- real FIM, best completion quality
  ollama      /api/generate          -- local models, supports suffix for FIM
"""

import json
import re
import ssl
import urllib.error
import urllib.request
import concurrent.futures

from . import settings


class ClientError(Exception):
    """A request failure carrying a user-readable message."""

    def __init__(self, message, detail=""):
        super().__init__(message)
        self.message = message
        self.detail = detail


_SYSTEM_PROMPT = (
    "You are a code completion engine embedded in an editor. "
    "The user gives you the code before the cursor (<PREFIX>) and after the "
    "cursor (<SUFFIX>). Reply with ONLY the raw text that should be inserted "
    "at the cursor so the code becomes correct and idiomatic.\n"
    "Hard rules:\n"
    "1. No markdown, no code fences, no explanation, no comments about what "
    "you did.\n"
    "2. Do not repeat any part of <PREFIX> or <SUFFIX>.\n"
    "3. Continue exactly from the cursor, including mid-word or mid-line.\n"
    "4. Keep it short: finish the current statement or block, then stop.\n"
    "5. If nothing sensible can be added, reply with an empty string."
)


def _stop_tokens():
    return ["\n\n\n", "<|endoftext|>", "<|fim_prefix|>", "<|file_separator|>"]


def _fim_suffix(ctx):
    """Return the suffix used for FIM, guaranteed non-empty.

    At the end of a file the suffix is an empty string, and Ollama's template
    branches like this:
        {{- if .Suffix }}<|fim_prefix|>...<|fim_middle|>
        {{- else if .Messages }}  <- this branch means chat mode
    An empty suffix makes an instruct model explain your code in prose instead
    of completing it. A single newline pushes it back onto the FIM branch.
    """
    return ctx.get("suffix", "") or "\n"


def _related_block(ctx):
    if not ctx.get("related"):
        return ""
    parts = []
    for item in ctx["related"]:
        parts.append("--- %s ---\n%s" % (item["name"], item["body"]))
    return (
        "Here are excerpts from other open files in the same project, "
        "for style and API reference only:\n\n" + "\n\n".join(parts) + "\n\n"
    )


def _build_chat_messages(ctx):
    user = "%sLanguage: %s\nFile: %s\n\n<PREFIX>\n%s\n</PREFIX>\n<SUFFIX>\n%s\n</SUFFIX>" % (
        _related_block(ctx),
        ctx.get("language", "text"),
        ctx.get("filename", "untitled"),
        ctx.get("prefix", ""),
        ctx.get("suffix", ""),
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _ssl_context(conf):
    if conf.get("verify_ssl", True):
        return None
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    return unverified


def _post_json(url, payload, conf, headers=None):
    body = json.dumps(payload).encode("utf-8")
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "AhuAIComplete/1.0 (Sublime Text)",
    }
    if conf.get("api_key"):
        req_headers["Authorization"] = "Bearer %s" % conf["api_key"]
    req_headers.update(conf.get("extra_headers") or {})
    req_headers.update(headers or {})

    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    timeout = float(conf.get("timeout") or 20)

    try:
        kwargs = {"timeout": timeout}
        ctx = _ssl_context(conf)
        if ctx is not None:
            kwargs["context"] = ctx
        with urllib.request.urlopen(req, **kwargs) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        hint = ""
        if exc.code in (401, 403):
            hint = ", check api_key"
        elif exc.code == 404:
            hint = ", check base_url and model"
        elif exc.code == 429:
            hint = ", rate limited"
        raise ClientError("HTTP %s%s" % (exc.code, hint), detail)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ClientError("cannot reach the service: %s" % reason, str(url))
    except json.JSONDecodeError as exc:
        raise ClientError("response is not JSON", str(exc))
    except Exception as exc:  # timeouts and the like
        raise ClientError("request failed: %s" % exc.__class__.__name__, str(exc))


def _join_url(base, path):
    return "%s/%s" % (base.rstrip("/"), path.lstrip("/"))


# ----------------------------------------------------------------------
# provider implementations
# ----------------------------------------------------------------------

def _openai_n(conf, num_suggestions, fim=False):
    """Return n (the number of candidates) for openai / openai_fim requests.

    - When providers.X.n is set explicitly, use it: endpoints that only accept
      n=1 (DeepSeek /beta and friends) just set n:1 instead of changing the
      global num_suggestions.
    - Otherwise openai (chat) falls back to the global num_suggestions, and
      openai_fim falls back to 1, since most FIM endpoints support one.
    """
    raw = conf.get("n")
    if raw is not None:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    if fim:
        return 1
    try:
        return max(1, int(num_suggestions))
    except (TypeError, ValueError):
        return 1


def _complete_openai_chat(ctx, conf, num_suggestions):
    n = _openai_n(conf, num_suggestions, fim=False)
    payload = {
        "model": conf["model"],
        "messages": _build_chat_messages(ctx),
        "max_tokens": int(conf.get("max_tokens") or 256),
        "temperature": float(conf.get("temperature") or 0),
        "stream": False,
    }
    if n > 1:
        payload["n"] = n
    payload.update(conf.get("extra_body") or {})

    data = _post_json(_join_url(conf["base_url"], "chat/completions"), payload, conf)
    choices = data.get("choices") or []
    out = []
    for choice in choices:
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        # Only content is taken; some reasoning models expose it elsewhere.
        if text:
            out.append(text)
    if not out and data.get("error"):
        raise ClientError("service returned an error", json.dumps(data["error"])[:400])
    return out


def _complete_openai_fim(ctx, conf, num_suggestions):
    n = _openai_n(conf, num_suggestions, fim=True)
    payload = {
        "model": conf["model"],
        "prompt": ctx.get("prefix", ""),
        "suffix": _fim_suffix(ctx),
        "max_tokens": int(conf.get("max_tokens") or 256),
        "temperature": float(conf.get("temperature") or 0),
        "stream": False,
        "stop": _stop_tokens(),
    }
    if n > 1:
        payload["n"] = n
    payload.update(conf.get("extra_body") or {})

    data = _post_json(_join_url(conf["base_url"], "completions"), payload, conf)
    choices = data.get("choices") or []
    out = [c.get("text") or "" for c in choices]
    out = [t for t in out if t]
    if not out and data.get("error"):
        raise ClientError("service returned an error", json.dumps(data["error"])[:400])
    return out


def _ollama_one(ctx, conf, seed=None, temp=None):
    """Send one /api/generate request to Ollama. Raises ClientError; [] if empty."""
    if temp is None:
        temp = float(conf.get("temperature") or 0)
    payload = {
        "model": conf["model"],
        "prompt": ctx.get("prefix", ""),
        "suffix": _fim_suffix(ctx),
        "stream": False,
        "options": {
            "num_predict": int(conf.get("max_tokens") or 256),
            "temperature": temp,
            "stop": _stop_tokens(),
        },
    }
    # A seed pins the RNG so one result can be reproduced; multi-candidate
    # requests omit it and rely on a higher temperature for variety.
    if seed is not None:
        payload["options"]["seed"] = seed
    payload.update(conf.get("extra_body") or {})

    data = _post_json(_join_url(conf["base_url"], "api/generate"), payload, conf)
    if data.get("error"):
        raise ClientError("Ollama returned an error", str(data["error"])[:400])
    text = data.get("response") or ""
    return [text] if text else []


# Extra "implementation idea" hints appended for multi-candidate requests.
# The first request gets none, keeping your low temperature and best quality;
# the others are steered toward different approaches. At low temperature
# Ollama (small models especially) decodes almost greedily, so changing the
# seed or the temperature does nothing -- only the prompt creates variety.
_HINTS = [
    "Use list comprehension.",
    "Use an explicit loop for clarity.",
    "Keep it short and idiomatic.",
]

# Line comment prefix per language, used to phrase a hint as a comment
# (it stays inside the prefix and is never inserted).
_COMMENT_PREFIX = {
    "python": "#", "ruby": "#", "shell": "#", "bash": "#", "yaml": "#",
    "r": "#", "perl": "#",
    "javascript": "//", "typescript": "//", "js": "//", "ts": "//",
    "java": "//", "c": "//", "cpp": "//", "csharp": "//", "go": "//",
    "rust": "//", "php": "//", "scala": "//", "kotlin": "//", "swift": "//",
    "sql": "--",
}


def _comment_prefix(language):
    return _COMMENT_PREFIX.get(str(language or "").lower(), "#")


def _hinted_prefix(ctx, hint):
    """Append an implementation-idea comment to the prefix, indent-aligned."""
    line_prefix = ctx.get("line_prefix", "") or ""
    indent = line_prefix[: len(line_prefix) - len(line_prefix.lstrip())]
    cp = _comment_prefix(ctx.get("language", "python"))
    return "%s%s %s\n%s" % (ctx.get("prefix", ""), cp, hint, indent)


def _strip_echoed_hint(text, hint, language):
    """Models sometimes echo an injected hint back as the first completion line.

    Only hints we injected are stripped (first line starts with a comment
    prefix and contains the hint text), so genuine user comments are safe.
    """
    if not hint or not text:
        return text
    cp = _comment_prefix(language)
    nl = text.find("\n")
    first = text[:nl] if nl != -1 else text
    if first.strip().startswith(cp) and hint.lower() in first.lower():
        rest = text[nl + 1:] if nl != -1 else ""
        return rest.lstrip("\n")
    return text


def _complete_ollama(ctx, conf, n):
    n = max(1, int(n))
    if n == 1:
        return _ollama_one(ctx, conf)

    # Multi-candidate: N parallel requests. The first is left untouched (most
    # faithful), the rest carry different hints so even a small model produces
    # several distinct continuations. The hint lives in the prefix and is never
    # inserted; if the model echoes it as the first line, _strip_echoed_hint
    # strips it.
    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        jobs = []
        for i in range(n):
            if i == 0 or i - 1 >= len(_HINTS):
                jobs.append((ex.submit(_ollama_one, ctx, conf, None, None), ""))
            else:
                hint = _HINTS[i - 1]
                c = dict(ctx)
                c["prefix"] = _hinted_prefix(ctx, hint)
                jobs.append(
                    (ex.submit(_ollama_one, c, conf, None, None), hint)
                )
        for fut, hint in jobs:
            try:
                for text in fut.result():
                    out.append(_strip_echoed_hint(text, hint, ctx.get("language")))
            except ClientError:
                pass
    return out


_PROVIDERS = {
    "openai": _complete_openai_chat,
    "openai_fim": _complete_openai_fim,
    "ollama": _complete_ollama,
}


def complete(ctx, num_suggestions=1):
    """Request completions, returning raw strings (not cleaned). Raises ClientError."""
    conf = settings.provider_config()
    name = conf.get("name")

    handler = _PROVIDERS.get(name)
    if handler is None:
        raise ClientError(
            "unknown provider: %s" % name,
            "available: %s" % ", ".join(sorted(_PROVIDERS)),
        )
    # The user settings file is merged shallowly: writing only
    # providers.ollama.model replaces the whole providers.ollama subtree and
    # swallows the packaged default base_url. Local ollama gets a fallback so
    # that case does not become an error.
    if not conf.get("base_url") and name == "ollama":
        conf["base_url"] = "http://127.0.0.1:11434"
    if not conf.get("base_url"):
        raise ClientError('provider "%s" has no base_url' % name)
    if not conf.get("model"):
        raise ClientError('provider "%s" has no model' % name)
    if name != "ollama" and not conf.get("api_key"):
        raise ClientError('provider "%s" has no api_key' % name)

    settings.debug("request ->", name, conf.get("model"),
                   "prefix=%d suffix=%d" % (len(ctx.get("prefix", "")),
                                            len(ctx.get("suffix", ""))))
    results = handler(ctx, conf, max(1, int(num_suggestions)))
    settings.debug("response <-", len(results), "candidate(s)")
    return results


# ----------------------------------------------------------------------
# Connectivity self-check, used by the ai_complete_ping command
# ----------------------------------------------------------------------

def ping():
    """Return (ok: bool, message: str)."""
    conf = settings.provider_config()
    name = conf.get("name")
    probe_ctx = {
        "prefix": "def add(a, b):\n    return ",
        "suffix": "\n",
        "language": "python",
        "filename": "probe.py",
        "line_prefix": "    return ",
        "related": [],
    }
    try:
        out = complete(probe_ctx, 1)
    except ClientError as exc:
        detail = ("\n" + exc.detail) if exc.detail else ""
        return False, "[%s / %s] %s%s" % (name, conf.get("model"), exc.message, detail)
    if not out:
        return False, "[%s / %s] connected, but the model returned no content" % (name, conf.get("model"))
    sample = re.sub(r"\s+", " ", out[0]).strip()[:80]
    return True, "[%s / %s] OK, sample output: %s" % (name, conf.get("model"), sample)
