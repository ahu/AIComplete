"""Run the whole chain against a real service: request -> clean -> insert.

    python3 tests/test_live.py                  # provider from settings
    python3 tests/test_live.py ollama qwen2.5-coder:1.5b

The backend must be reachable, so this is not part of test_logic.py.
"""

import json
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ROOT))

_fake = types.ModuleType("sublime")
_fake.load_settings = lambda name: None
sys.modules.setdefault("sublime", _fake)

PKG = os.path.basename(ROOT)
settings = __import__("%s.lib.settings" % PKG, fromlist=["settings"])
client = __import__("%s.lib.client" % PKG, fromlist=["client"])
postprocess = __import__("%s.lib.postprocess" % PKG, fromlist=["postprocess"])


def load_defaults():
    """Read the packaged .sublime-settings (JSON once comments are stripped)."""
    path = os.path.join(ROOT, "AhuAIComplete.sublime-settings")
    raw = open(path, encoding="utf-8").read()
    raw = re.sub(r"^\s*//.*$", "", raw, flags=re.M)
    return json.loads(raw)


CONF = load_defaults()
if len(sys.argv) > 1:
    CONF["provider"] = sys.argv[1]
if len(sys.argv) > 2:
    CONF["providers"][CONF["provider"]]["model"] = sys.argv[2]

settings.get = lambda key, default=None: CONF.get(key, default)


CASES = [
    {
        "name": "complete the function body",
        "prefix": "def fib(n):\n    \"\"\"Return the n-th Fibonacci number.\"\"\"\n    if n < 2:\n        return n\n    ",
        "suffix": "\n\n\nprint(fib(10))\n",
        "line_prefix": "    ",
    },
    {
        "name": "fill in the middle (cursor inside brackets)",
        "prefix": "import os\n\npaths = [p for p in os.listdir('.') if p.endswith(",
        "suffix": ")]\n",
        "line_prefix": "paths = [p for p in os.listdir('.') if p.endswith(",
    },
    {
        "name": "continue with an assignment line",
        "prefix": "class Counter:\n    def __init__(self):\n        self.count = 0\n\n    def increment(self):\n        ",
        "suffix": "\n",
        "line_prefix": "        ",
    },
]


def main():
    conf = settings.provider_config()
    print("provider = %s   model = %s   base_url = %s\n"
          % (conf["name"], conf["model"], conf["base_url"]))

    failures = 0
    for case in CASES:
        ctx = {
            "prefix": case["prefix"],
            "suffix": case["suffix"],
            "language": "python",
            "filename": "demo.py",
            "line_prefix": case["line_prefix"],
            "related": [],
        }
        print("=" * 66)
        print("· %s" % case["name"])
        print("-" * 66)
        try:
            raw_list = client.complete(ctx, 1)
        except client.ClientError as exc:
            print("  request failed: %s\n  %s" % (exc.message, exc.detail[:200]))
            failures += 1
            continue

        if not raw_list:
            print("  the model returned nothing")
            failures += 1
            continue

        cleaned = postprocess.clean(raw_list[0], ctx, CONF.get("max_lines", 12))
        print("  raw     : %r" % raw_list[0][:160])
        print("  cleaned : %r" % cleaned[:160])
        print("\n  after insertion:")
        merged = case["prefix"] + cleaned + case["suffix"]
        for line in merged.rstrip().split("\n"):
            print("    | %s" % line)
        if not cleaned:
            print("  ! empty after cleaning; this one would not be shown")
            failures += 1
        print()

    print("=" * 66)
    print("done: %d / %d cases produced a usable suggestion"
          % (len(CASES) - failures, len(CASES)))

    # ---- multi-candidate path: verify the backend really returns several ----
    print("=" * 66)
    print("· multiple candidates (num_suggestions=3)")
    print("-" * 66)
    mctx = {
        # deliberately a function with several reasonable implementations
        "prefix": "def dedupe(items):\n    ",
        "suffix": "\n\n\nprint(dedupe([1, 2, 2, 3]))\n",
        "language": "python",
        "filename": "demo.py",
        "line_prefix": "    ",
        "related": [],
    }
    try:
        multi = client.complete(mctx, 3)
    except client.ClientError as exc:
        print("  request failed: %s\n  %s" % (exc.message, exc.detail[:200]))
        return 1 if failures == len(CASES) else 0
    multi = [postprocess.clean(t, mctx, CONF.get("max_lines", 12)) for t in multi]
    multi = [t for t in multi if t]
    print("  candidates received: %d (after dedup)" % len(multi))
    for i, t in enumerate(multi):
        print("  [%d] %r" % (i + 1, t[:120]))
    if not multi:
        print("  ! all candidates were empty")
        failures += 1
    print("=" * 66)
    print("done: multi-candidate case %s" % ("OK" if multi else "FAIL"))
    return 1 if failures == len(CASES) else 0


if __name__ == "__main__":
    sys.exit(main())
