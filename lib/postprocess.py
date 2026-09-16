"""Turn raw model output into a completion that can be inserted into a buffer.

Models ramble: they wrap code in markdown fences, repeat code already in the
buffer, and add closing brackets that duplicate the suffix. Each is fixed here.
"""

import re

_FENCE_OPEN = re.compile(r"^\s*```[^\n]*\n", re.S)
_FENCE_CLOSE = re.compile(r"\n?```\s*$", re.S)

# Opening words that betray a chatty model
_PROSE_LEAD = re.compile(
    r"^(the|this|that|here|sure|certainly|note|however|it|you|we|i|to)\b",
    re.I,
)
# Any of these means it is code rather than prose
_CODE_HINT = re.compile(r"[=(){}\[\];<>]|^\s*(#|//|/\*|\*|-{2,})")

_PROSE_FREE_LANGS = ("markdown", "text", "plaintext", "")


def looks_like_prose(text, ctx):
    """Detect a model that started explaining your code instead of extending it.

    In FIM mode the model should only emit code. Once the suffix is empty or
    the template does not match, an instruct model degrades into chat and
    returns a paragraph of prose -- which must never reach the buffer.

    False positives are the real risk here, so the conditions are tight: when
    the cursor already sits in a comment or a string, natural language is
    expected and the text is allowed through.
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
    # only a closing fence is present
    idx = text.find("```")
    return text[:idx] if idx > 0 else text


def strip_repeated_prefix(completion, prefix):
    """Models sometimes repeat the last chunk before the cursor; drop the overlap."""
    if not completion or not prefix:
        return completion
    tail = prefix[-400:]
    # Find the longest overlap between the head of the completion and the tail
    # of the prefix
    max_len = min(len(tail), len(completion))
    for size in range(max_len, 3, -1):
        if tail.endswith(completion[:size]):
            return completion[size:]
    return completion


def trim_overlap_with_suffix(completion, suffix):
    """Trim a completion whose tail duplicates the suffix, avoiding `))` / `}}`."""
    if not completion or not suffix:
        return completion
    # Only the first suffix line is compared; multi-line trimming over-trims
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
    """The full cleaning pipeline. '' means the suggestion is not worth showing.

    reject_prose is only enabled for FIM-style providers: there, natural
    language means the request went off the rails and is better dropped.
    """
    if not raw:
        return ""

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_code_fence(text)

    # Models occasionally end with a special token such as "<|...|>"
    for token in ("<|endoftext|>", "<|fim_middle|>", "<|file_separator|>",
                  "<|EOT|>", "<EOT>", "</s>"):
        pos = text.find(token)
        if pos != -1:
            text = text[:pos]

    if reject_prose and looks_like_prose(text, ctx):
        return ""

    text = strip_repeated_prefix(text, ctx.get("prefix", ""))

    # Leading whitespace: the cursor is already past the indent
    line_prefix = ctx.get("line_prefix", "")
    if line_prefix.strip() == "" and line_prefix:
        # Cursor sits after pure indentation; drop a duplicated indent
        if text.startswith(line_prefix):
            text = text[len(line_prefix):]
    elif line_prefix.endswith((" ", "\t")) and text[:1] in (" ", "\t"):
        # There is already a space before the cursor; one more would double it
        text = text.lstrip(" \t")

    text = limit_lines(text, max_lines)
    text = trim_overlap_with_suffix(text, ctx.get("suffix", ""))
    text = drop_trailing_blank_lines(text)

    if not text.strip():
        return ""
    return text


def consume_typed(completion, typed):
    """Check whether an existing suggestion can be reused after the user typed.

    typed is what the user just entered. Return the remaining part on a match,
    otherwise None.
    """
    if not typed:
        return completion
    if completion.startswith(typed):
        rest = completion[len(typed):]
        return rest if rest.strip() else None
    return None
