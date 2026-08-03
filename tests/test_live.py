"""对着真实模型服务跑一遍完整链路：请求 -> 清洗 -> 最终插入文本。

    python3 tests/test_live.py                  # 用设置文件里的默认 provider
    python3 tests/test_live.py ollama qwen2.5-coder:1.5b

需要后端真的可达，所以不放进 test_logic.py 里。
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
    """直接读包里的 .sublime-settings（去掉注释后就是 JSON）。"""
    path = os.path.join(ROOT, "AIComplete.sublime-settings")
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
        "name": "补完函数体",
        "prefix": "def fib(n):\n    \"\"\"Return the n-th Fibonacci number.\"\"\"\n    if n < 2:\n        return n\n    ",
        "suffix": "\n\n\nprint(fib(10))\n",
        "line_prefix": "    ",
    },
    {
        "name": "中间填充（光标在括号里）",
        "prefix": "import os\n\npaths = [p for p in os.listdir('.') if p.endswith(",
        "suffix": ")]\n",
        "line_prefix": "paths = [p for p in os.listdir('.') if p.endswith(",
    },
    {
        "name": "接着写一行赋值",
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
            print("  请求失败：%s\n  %s" % (exc.message, exc.detail[:200]))
            failures += 1
            continue

        if not raw_list:
            print("  模型没返回内容")
            failures += 1
            continue

        cleaned = postprocess.clean(raw_list[0], ctx, CONF.get("max_lines", 12))
        print("  原始输出 : %r" % raw_list[0][:160])
        print("  清洗之后 : %r" % cleaned[:160])
        print("\n  插入效果：")
        merged = case["prefix"] + cleaned + case["suffix"]
        for line in merged.rstrip().split("\n"):
            print("    | %s" % line)
        if not cleaned:
            print("  ! 清洗后为空，这条不会显示")
            failures += 1
        print()

    print("=" * 66)
    print("完成，%d / %d 条产出了可用建议" % (len(CASES) - failures, len(CASES)))

    # ---- 多候选路径：验证后端真的能一次给多条 ----
    print("=" * 66)
    print("· 多候选（num_suggestions=3）")
    print("-" * 66)
    mctx = {
        # 故意选一个有多种合理实现的函数，才能看出候选差异
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
        print("  请求失败：%s\n  %s" % (exc.message, exc.detail[:200]))
        return 1 if failures == len(CASES) else 0
    multi = [postprocess.clean(t, mctx, CONF.get("max_lines", 12)) for t in multi]
    multi = [t for t in multi if t]
    print("  拿到的候选数：%d（去重后）" % len(multi))
    for i, t in enumerate(multi):
        print("  [%d] %r" % (i + 1, t[:120]))
    if not multi:
        print("  ! 多候选全空")
        failures += 1
    print("=" * 66)
    print("完成，多候选用例 %s" % ("OK" if multi else "FAIL"))
    return 1 if failures == len(CASES) else 0


if __name__ == "__main__":
    sys.exit(main())
