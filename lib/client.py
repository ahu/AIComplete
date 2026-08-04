"""与模型服务通信。只用标准库，Sublime 自带的 Python 就能跑。

支持三种后端：
  openai      /chat/completions      —— 通用，任何 OpenAI 兼容服务
  openai_fim  /completions + suffix  —— 真 FIM，代码补全效果最好
  ollama      /api/generate          —— 本地模型，支持 suffix 做 FIM
"""

import json
import re
import ssl
import urllib.error
import urllib.request
import concurrent.futures

from . import settings


class ClientError(Exception):
    """带用户可读信息的请求失败。"""

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
    """给 FIM 用的 suffix，保证非空。

    光标停在文件末尾时 suffix 是空串，而 Ollama 的模板判断长这样：
        {{- if .Suffix }}<|fim_prefix|>...<|fim_middle|>
        {{- else if .Messages }}  <- 走到这里就变成聊天了
    空 suffix 会让 instruct 模型开始用散文解释你的代码，而不是补全。
    塞一个换行就能把它按回 FIM 分支。
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
            hint = "，检查 api_key"
        elif exc.code == 404:
            hint = "，检查 base_url 和 model"
        elif exc.code == 429:
            hint = "，被限流了"
        raise ClientError("HTTP %s%s" % (exc.code, hint), detail)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ClientError("连不上服务：%s" % reason, str(url))
    except json.JSONDecodeError as exc:
        raise ClientError("返回的不是 JSON", str(exc))
    except Exception as exc:  # 超时等
        raise ClientError("请求失败：%s" % exc.__class__.__name__, str(exc))


def _join_url(base, path):
    return "%s/%s" % (base.rstrip("/"), path.lstrip("/"))


# ----------------------------------------------------------------------
# provider 实现
# ----------------------------------------------------------------------

def _openai_n(conf, num_suggestions, fim=False):
    """openai / openai_fim 请求里用的 n（候选条数）。

    - providers.X.n 显式写了就用它（DeepSeek /beta 这类只支持 n=1 的端点，
      在配置里设 n:1 即可，不用动全局 num_suggestions）。
    - 没写时：openai(chat) 默认沿用全局 num_suggestions；
      openai_fim 默认 1（多数 FIM 端点如 deepseek /beta 只支持单条）。
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
        # 有些推理模型把内容放在 reasoning_content 之外的 content，这里只取 content
        if text:
            out.append(text)
    if not out and data.get("error"):
        raise ClientError("服务返回错误", json.dumps(data["error"])[:400])
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
        raise ClientError("服务返回错误", json.dumps(data["error"])[:400])
    return out


def _ollama_one(ctx, conf, seed=None, temp=None):
    """向 Ollama 发一次 /api/generate。失败抛 ClientError，空响应返回 []。"""
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
    # seed 固定 RNG，用于复现单条结果；多候选场景不传 seed，靠升温出差异。
    if seed is not None:
        payload["options"]["seed"] = seed
    payload.update(conf.get("extra_body") or {})

    data = _post_json(_join_url(conf["base_url"], "api/generate"), payload, conf)
    if data.get("error"):
        raise ClientError("Ollama 报错", str(data["error"])[:400])
    text = data.get("response") or ""
    return [text] if text else []


# 多候选时给额外请求追加的「实现思路」提示。首条不追加，保持你设的低温
# 与最高质量；后面几条用不同思路引导出不同写法。Ollama（尤其小模型）在
# 低温下几乎是贪心解码，换 seed / 升温都没用，只有改 prompt 才真正出差异。
_HINTS = [
    "Use list comprehension.",
    "Use an explicit loop for clarity.",
    "Keep it short and idiomatic.",
]

# 不同语言的行注释前缀，用来把思路提示写成注释（留在 prefix，不会被插入）。
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
    """在 prefix 末尾追加一条实现思路注释（按当前缩进对齐）。"""
    line_prefix = ctx.get("line_prefix", "") or ""
    indent = line_prefix[: len(line_prefix) - len(line_prefix.lstrip())]
    cp = _comment_prefix(ctx.get("language", "python"))
    return "%s%s %s\n%s" % (ctx.get("prefix", ""), cp, hint, indent)


def _strip_echoed_hint(text, hint, language):
    """模型有时会把我们注入的思路提示当注释原样复述成补全首行。

    只对自己注入的提示做剥离（首行是以注释前缀开头、且包含提示文本），
    不会误伤用户真正想写的注释。
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

    # 多候选：N 个请求并行。首条不扰动（最忠实），其余各自带不同思路提示，
    # 让小模型也能产出几条不同的续写。提示写在 prefix 里不会被插入；若模型
    # 把它当注释复述成首行，_strip_echoed_hint 会剥掉。
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
    """请求补全，返回原始字符串列表（未清洗）。失败抛 ClientError。"""
    conf = settings.provider_config()
    name = conf.get("name")

    handler = _PROVIDERS.get(name)
    if handler is None:
        raise ClientError(
            "未知的 provider：%s" % name,
            "可选：%s" % ", ".join(sorted(_PROVIDERS)),
        )
    # 用户覆盖文件是「浅合并」——只写 providers.ollama.model 会把整个
    # providers.ollama 子树替换掉，连带吞掉包内默认的 base_url。
    # 对本地 ollama 给个兜底，避免这种情况直接报错。
    if not conf.get("base_url") and name == "ollama":
        conf["base_url"] = "http://127.0.0.1:11434"
    if not conf.get("base_url"):
        raise ClientError("provider «%s» 没配 base_url" % name)
    if not conf.get("model"):
        raise ClientError("provider «%s» 没配 model" % name)
    if name != "ollama" and not conf.get("api_key"):
        raise ClientError("provider «%s» 没配 api_key" % name)

    settings.debug("request ->", name, conf.get("model"),
                   "prefix=%d suffix=%d" % (len(ctx.get("prefix", "")),
                                            len(ctx.get("suffix", ""))))
    results = handler(ctx, conf, max(1, int(num_suggestions)))
    settings.debug("response <-", len(results), "candidate(s)")
    return results


# ----------------------------------------------------------------------
# 连通性自检，给 ai_complete_ping 命令用
# ----------------------------------------------------------------------

def ping():
    """返回 (ok: bool, message: str)。"""
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
        return False, "[%s / %s] 连上了，但模型没返回内容" % (name, conf.get("model"))
    sample = re.sub(r"\s+", " ", out[0]).strip()[:80]
    return True, "[%s / %s] 正常，返回示例：%s" % (name, conf.get("model"), sample)
