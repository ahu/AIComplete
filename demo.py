"""AIComplete 试驾场地。

把光标放到下面任意一个 TODO 后面，停一下，灰色建议就会浮出来。
Tab 接受整条 / Esc 丢弃 / Cmd+Shift+Enter 手动再要一条。

多候选：本插件默认一次要 3 条候选。当 ghost 出现时，按
  Cmd+Shift+]  下一条候选
  Cmd+Shift+[  上一条候选
（Windows / Linux 上是 Alt+] / Alt+[）
角标会显示当前是第几条（如 [2/3]）。
"""


def fibonacci(n):
    """Return the n-th Fibonacci number."""
    if n < 2:
        return n
    # TODO: 光标放到下一行的缩进后面等一下，然后试试 Cmd+Shift+]
    return fibonacci(n - 1) + fibonacci(n - 2)


class ShoppingCart:
    def __init__(self):
        self.items = []

    def add(self, name, price, qty=1):
        self.items.append({"name": name, "price": price, "qty": qty})

    def total(self):
        # TODO: 这里也试试，多候选下能切到不同的累加写法
        return sum(item["price"] * item["qty"] for item in self.items)


def greet(name):
    # TODO: 同一个位置可能因为随机种子产出几种写法，正好拿来练候选切换
    return "Hello, %s!" % name


def dedupe(items):
    # TODO: 多候选的最佳练手点——集合法 / 循环法 / 字典保序法都可能被给出来，
    # 等 ghost 出现后按 Cmd+Shift+] 一条条看过去，挑顺眼的 Tab 接受。
    return list(dict.fromkeys(items))
