"""把模型吐出来的原始文本，清洗成可以直接插进 buffer 的补全。

模型很爱多嘴：套 markdown 围栏、把已经写过的代码再抄一遍、
补一堆和后文重复的闭合括号。这里逐条修掉。
"""

import re

_FENCE_OPEN = re.compile(r"^\s*```[^\n]*\n", re.S)
_FENCE_CLOSE = re.compile(r"\n?```\s*$", re.S)

# 聊天型模型跑偏时的开场白特征
_PROSE_LEAD = re.compile(
    r"^(the|this|that|here|sure|certainly|note|however|it|you|we|i|to)\b",
    re.I,
)
# 只要出现这些就基本可以认定是代码，不是解说
_CODE_HINT = re.compile(r"[=(){}\[\];<>]|^\s*(#|//|/\*|\*|-{2,})")

_PROSE_FREE_LANGS = ("markdown", "text", "plaintext", "")


def looks_like_prose(text, ctx):
    """判断模型是不是又开始「解释你的代码」了。

    FIM 模式下模型只该吐代码。一旦 suffix 为空或模板没命中，instruct
    模型就会退化成聊天，返回一整段英文说明 —— 这种绝不能插进 buffer。

    误伤是这里最大的风险，所以判定条件卡得很紧：
    光标本来就在注释或字符串里时直接放行，那里出现自然语言天经地义。
    """
    language = (ctx.get("language") or "").lower()
    if language in _PROSE_FREE_LANGS:
        return False

    scope = ctx.get("scope") or ""
    if "comment" in scope or "string" in scope:
        return False

    first = text.strip().split("\n", 1)[0].strip()
    if len(first.split()) < 8:
        return False
    if _CODE_HINT.search(first):
        return False
    return bool(_PROSE_LEAD.match(first))


def strip_code_fence(text):
    if "```" not in text:
        return text
    stripped = text.strip()
    if stripped.startswith("```"):
        text = _FENCE_OPEN.sub("", stripped, count=1)
        text = _FENCE_CLOSE.sub("", text)
        return text
    # 只有收尾围栏的情况
    idx = text.find("```")
    return text[:idx] if idx > 0 else text


def strip_repeated_prefix(completion, prefix):
    """模型有时会把光标前的最后一段重新抄一遍，去掉这种重复。"""
    if not completion or not prefix:
        return completion
    tail = prefix[-400:]
    # 找 completion 的开头和 prefix 的结尾最长的重叠
    max_len = min(len(tail), len(completion))
    for size in range(max_len, 3, -1):
        if tail.endswith(completion[:size]):
            return completion[size:]
    return completion


def trim_overlap_with_suffix(completion, suffix):
    """补全结尾和光标后的文本重复时裁掉，防止出现 `))` `}}`。"""
    if not completion or not suffix:
        return completion
    # 只跟后文的第一行比，跨行裁剪误伤太大
    suffix_head = suffix.split("\n", 1)[0]
    if not suffix_head.strip():
        return completion
    max_len = min(len(completion), len(suffix_head))
    for size in range(max_len, 0, -1):
        if completion.endswith(suffix_head[:size]):
            return completion[: len(completion) - size]
    return completion


def limit_lines(completion, max_lines):
    if max_lines and max_lines > 0:
        lines = completion.split("\n")
        if len(lines) > max_lines:
            completion = "\n".join(lines[:max_lines])
    return completion


def drop_trailing_blank_lines(completion):
    lines = completion.split("\n")
    while len(lines) > 1 and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def clean(raw, ctx, max_lines=12, reject_prose=False):
    """完整清洗流水线。返回 '' 表示这条建议不值得展示。

    reject_prose 建议只对 FIM 类 provider 打开：那种模式下模型返回
    自然语言就说明请求走岔了，宁可不显示。
    """
    if not raw:
        return ""

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_code_fence(text)

    # 模型偶尔用 "<|...|>" 之类的特殊 token 收尾
    for token in ("<|endoftext|>", "<|fim_middle|>", "<|file_separator|>",
                  "<|EOT|>", "<EOT>", "</s>"):
        pos = text.find(token)
        if pos != -1:
            text = text[:pos]

    if reject_prose and looks_like_prose(text, ctx):
        return ""

    text = strip_repeated_prefix(text, ctx.get("prefix", ""))

    # 首行前导空白：光标已经在缩进之后了，模型再补缩进就是重复
    line_prefix = ctx.get("line_prefix", "")
    if line_prefix.strip() == "" and line_prefix:
        # 光标处在纯缩进后面，模型如果又给了一份相同缩进就去掉
        if text.startswith(line_prefix):
            text = text[len(line_prefix):]
    elif line_prefix.endswith((" ", "\t")) and text[:1] in (" ", "\t"):
        # 光标前已经有空格了，模型再补一个就变成双空格
        text = text.lstrip(" \t")

    text = limit_lines(text, max_lines)
    text = trim_overlap_with_suffix(text, ctx.get("suffix", ""))
    text = drop_trailing_blank_lines(text)

    if not text.strip():
        return ""
    return text


def consume_typed(completion, typed):
    """用户又敲了几个字符时，看能不能接着用旧建议。

    typed 是用户新输入的内容。命中就返回剩下那截，否则返回 None。
    """
    if not typed:
        return completion
    if completion.startswith(typed):
        rest = completion[len(typed):]
        return rest if rest.strip() else None
    return None
