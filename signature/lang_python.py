"""Python 3: LeetCode처럼 class Solution의 메서드."""
from .common import comment_block, output_index, spec, specs_json, uses
from .types import Signature, Type

PRELUDE = """\
from typing import *
import collections, heapq, bisect, math, itertools, functools, string, re, random, operator, sys, copy
from collections import *
from heapq import *
from bisect import *
from itertools import *
from functools import *
"""

LIST_NODE = [
    "class ListNode:",
    "    def __init__(self, val=0, next=None):",
    "        self.val = val",
    "        self.next = next",
]
TREE_NODE = [
    "class TreeNode:",
    "    def __init__(self, val=0, left=None, right=None):",
    "        self.val = val",
    "        self.left = left",
    "        self.right = right",
]

_SCALAR = {"int": "int", "long": "int", "double": "float", "bool": "bool", "char": "str", "string": "str"}


def type_name(t: Type) -> str:
    if t.kind == "scalar":
        return _SCALAR[t.base]
    if t.kind == "array":
        return "List[" * t.dims + _SCALAR[t.base] + "]" * t.dims
    if t.kind == "listnode":
        return "Optional[ListNode]"
    if t.kind == "treenode":
        return "Optional[TreeNode]"
    return "None"


def starter(sig: Signature) -> str:
    lines = []
    if uses(sig, "listnode"):
        lines += ["# Definition for singly-linked list.", comment_block(LIST_NODE, "# ")]
    if uses(sig, "treenode"):
        lines += ["# Definition for a binary tree node.", comment_block(TREE_NODE, "# ")]
    params = "".join(f", {p.name}: {type_name(p.type)}" for p in sig.params)
    lines += ["class Solution:", f"    def {sig.function}(self{params}) -> {type_name(sig.returns)}:"]
    if sig.returns.kind == "void":
        lines += ['        """', f"        Do not return anything, modify {sig.output_param} in-place instead.", '        """']
    return "\n".join(lines) + "\n        \n"


RUNTIME = r'''
class _LojReader:
    def __init__(self, text):
        self.lines = [line.rstrip("\r") for line in text.split("\n")]
        self.li = 0
        self.col = 0

    def token(self):
        while self.li < len(self.lines):
            line, c = self.lines[self.li], self.col
            while c < len(line) and line[c] in " \t":
                c += 1
            if c < len(line):
                start = c
                while c < len(line) and line[c] not in " \t":
                    c += 1
                self.col = c
                return line[start:c]
            self.li, self.col = self.li + 1, 0
        raise EOFError("input ended early")

    def line(self):
        if self.col > 0:
            self.li, self.col = self.li + 1, 0
        if self.li >= len(self.lines):
            return ""
        line = self.lines[self.li]
        self.li += 1
        return line


def _loj_scalar(r, base):
    if base == "string":
        return r.line()
    t = r.token()
    if base in ("int", "long"):
        return int(t)
    if base == "double":
        return float(t)
    if base == "bool":
        return t in ("true", "1")
    return t


def _loj_read(r, spec):
    kind, base, dims = spec
    if kind == "scalar":
        return _loj_scalar(r, base)
    if kind == "array" and dims == 1:
        return [_loj_scalar(r, base) for _ in range(int(r.token()))]
    if kind == "array":
        return [[_loj_scalar(r, base) for _ in range(int(r.token()))] for _ in range(int(r.token()))]
    if kind == "listnode":
        head = None
        for value in reversed([int(r.token()) for _ in range(int(r.token()))]):
            head = ListNode(value, head)
        return head
    tokens = [r.token() for _ in range(int(r.token()))]
    if not tokens or tokens[0] == "null":
        return None
    root = TreeNode(int(tokens[0]))
    queue, i = [root], 1
    for node in queue:
        if i >= len(tokens):
            break
        for side in ("left", "right"):
            if i < len(tokens) and tokens[i] != "null":
                child = TreeNode(int(tokens[i]))
                setattr(node, side, child)
                queue.append(child)
            i += 1
    return root


def _loj_token(base, value):
    if base == "bool":
        return "true" if value else "false"
    if base == "double":
        text = "%.5f" % value
        return "0.00000" if text == "-0.00000" else text
    return str(value)


def _loj_write(spec, value):
    kind, base, dims = spec
    if kind == "scalar":
        return value if base == "string" else _loj_token(base, value)
    if kind == "array" and dims == 1:
        if base == "string":
            return "\n".join(value)
        return " ".join(_loj_token(base, v) for v in value)
    if kind == "array":
        return "\n".join(" ".join(_loj_token(base, v) for v in row) for row in value)
    if kind == "listnode":
        values = []
        while value is not None:
            values.append(str(value.val))
            value = value.next
        return " ".join(values)
    tokens, queue = [], [value]
    for node in queue:
        if node is None:
            tokens.append("null")
        else:
            tokens.append(str(node.val))
            queue += [node.left, node.right]
    while tokens and tokens[-1] == "null":
        tokens.pop()
    return " ".join(tokens)


def _loj_main(function, params, output, output_index):
    import sys as _sys
    reader = _LojReader(_sys.stdin.read())
    args = [_loj_read(reader, spec) for spec in params]
    result = getattr(Solution(), function)(*args)
    value = args[output_index] if output_index >= 0 else result
    _sys.stdout.write(_loj_write(output, value) + "\n")
'''


def compose(sig: Signature, code: str) -> str:
    nodes = ""
    if uses(sig, "listnode"):
        nodes += "\n".join(LIST_NODE) + "\n"
    if uses(sig, "treenode"):
        nodes += "\n".join(TREE_NODE) + "\n"
    # 사용자 코드는 따로 컴파일해서 오류의 파일 이름과 줄 번호가 사용자가 쓴 그대로 나오게 한다.
    return (
        PRELUDE + nodes
        + f"\nexec(compile({code!r}, 'solution.py', 'exec'), globals())\n"
        + RUNTIME
        + f"\n_loj_main({sig.function!r}, {specs_json(sig)}, {spec(sig.output_type)!r}, {output_index(sig)})\n"
    )
