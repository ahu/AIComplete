"""AhuAIComplete playground.

Put the cursor after any TODO below and pause: a grey suggestion appears.
Tab accepts the whole thing / Esc dismisses it / Cmd+Shift+Enter asks for more.

Multiple candidates: three are requested by default. With ghost text on:
  Cmd+Shift+]  next candidate
  Cmd+Shift+[  previous candidate
(Alt+] / Alt+[ on Windows / Linux)
A badge shows which one you are on, e.g. [2/3].
"""


def fibonacci(n):
    """Return the n-th Fibonacci number."""
    if n < 2:
        return n
    # TODO: put the cursor after the indent on the next line and try Cmd+Shift+]
    return fibonacci(n - 1) + fibonacci(n - 2)


class ShoppingCart:
    def __init__(self):
        self.items = []

    def add(self, name, price, qty=1):
        self.items.append({"name": name, "price": price, "qty": qty})

    def total(self):
        # TODO: try here too; multiple candidates give different ways to sum
        return sum(item["price"] * item["qty"] for item in self.items)


def greet(name):
    # TODO: the same spot may yield several variants; good cycling practice
    return "Hello, %s!" % name


def dedupe(items):
    # TODO: the best place to practice cycling -- set / loop / dict-based
    # versions may all appear; step through them with Cmd+Shift+] and Tab one.
    return list(dict.fromkeys(items))
