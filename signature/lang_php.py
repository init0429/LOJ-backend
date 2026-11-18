"""PHP: LeetCode처럼 class Solution의 메서드. 제자리에서 고치는 인자는 참조(&$nums)로 받는다."""
import re

from .common import comment_block, output_index, output_spec_json, spec, uses
from .types import Signature, Type

LIST_NODE = [
    "class ListNode {",
    "    public $val = 0;",
    "    public $next = null;",
    "    function __construct($val = 0, $next = null) {",
    "        $this->val = $val;",
    "        $this->next = $next;",
    "    }",
    "}",
]
TREE_NODE = [
    "class TreeNode {",
    "    public $val = null;",
    "    public $left = null;",
    "    public $right = null;",
    "    function __construct($val = 0, $left = null, $right = null) {",
    "        $this->val = $val;",
    "        $this->left = $left;",
    "        $this->right = $right;",
    "    }",
    "}",
]

_SCALAR = {"int": "Integer", "long": "Integer", "double": "Float", "bool": "Boolean", "char": "String", "string": "String"}


def type_name(t: Type) -> str:
    if t.kind == "scalar":
        return _SCALAR[t.base]
    if t.kind == "array":
        return _SCALAR[t.base] + "[]" * t.dims
    return {"listnode": "ListNode", "treenode": "TreeNode", "void": "NULL"}[t.kind]


def _param(sig: Signature, name: str) -> str:
    # 제자리에서 고치는 배열은 참조로 받아야 바뀐 내용이 밖에서 보인다(LeetCode의 PHP와 같다).
    by_ref = sig.returns.kind == "void" and name == sig.output_param
    return ("&$" if by_ref else "$") + name


def starter(sig: Signature) -> str:
    parts = []
    if uses(sig, "listnode"):
        parts.append("/**\n" + comment_block(["Definition for a singly-linked list.", *LIST_NODE], " * ") + "\n */")
    if uses(sig, "treenode"):
        parts.append("/**\n" + comment_block(["Definition for a binary tree node.", *TREE_NODE], " * ") + "\n */")
    doc = [f"@param {type_name(p.type)} ${p.name}" for p in sig.params]
    doc.append(f"@return {type_name(sig.returns)}")
    params = ", ".join(_param(sig, p.name) for p in sig.params)
    parts.append(
        "class Solution {\n\n"
        + "    /**\n" + comment_block(doc, "     * ") + "\n     */\n"
        + f"    function {sig.function}({params}) {{\n        \n    }}\n}}"
    )
    return "\n".join(parts) + "\n"


RUNTIME = r'''
class LojJudgeReader {
    private $lines;
    private $li = 0;
    private $col = 0;

    function __construct($text) {
        $this->lines = array_map(fn($l) => rtrim($l, "\r"), explode("\n", $text));
    }

    function token() {
        while ($this->li < count($this->lines)) {
            $line = $this->lines[$this->li];
            $n = strlen($line);
            $c = $this->col;
            while ($c < $n && ($line[$c] === " " || $line[$c] === "\t")) $c++;
            if ($c < $n) {
                $start = $c;
                while ($c < $n && $line[$c] !== " " && $line[$c] !== "\t") $c++;
                $this->col = $c;
                return substr($line, $start, $c - $start);
            }
            $this->li++;
            $this->col = 0;
        }
        throw new Exception("input ended early");
    }

    function line() {
        if ($this->col > 0) { $this->li++; $this->col = 0; }
        if ($this->li >= count($this->lines)) return "";
        return $this->lines[$this->li++];
    }
}

function loj_judge_scalar($r, $base) {
    if ($base === "string") return $r->line();
    $t = $r->token();
    if ($base === "int" || $base === "long") return intval($t);
    if ($base === "double") return floatval($t);
    if ($base === "bool") return $t === "true" || $t === "1";
    return $t;
}

function loj_judge_read($r, $spec) {
    [$kind, $base, $dims] = $spec;
    if ($kind === "scalar") return loj_judge_scalar($r, $base);
    if ($kind === "array") {
        $n = intval($r->token());
        $out = [];
        for ($i = 0; $i < $n; $i++) $out[] = $dims === 1 ? loj_judge_scalar($r, $base) : loj_judge_read($r, ["array", $base, 1]);
        return $out;
    }
    if ($kind === "listnode") {
        $n = intval($r->token());
        $values = [];
        for ($i = 0; $i < $n; $i++) $values[] = intval($r->token());
        $head = null;
        for ($i = $n - 1; $i >= 0; $i--) $head = new ListNode($values[$i], $head);
        return $head;
    }
    $n = intval($r->token());
    $tokens = [];
    for ($i = 0; $i < $n; $i++) $tokens[] = $r->token();
    if ($n === 0 || $tokens[0] === "null") return null;
    $root = new TreeNode(intval($tokens[0]));
    $queue = [$root];
    $i = 1;
    for ($q = 0; $q < count($queue) && $i < $n; $q++) {
        $node = $queue[$q];
        foreach (["left", "right"] as $side) {
            if ($i < $n && $tokens[$i] !== "null") {
                $node->$side = new TreeNode(intval($tokens[$i]));
                $queue[] = $node->$side;
            }
            $i++;
        }
    }
    return $root;
}

function loj_judge_token($base, $value) {
    if ($base === "bool") return $value ? "true" : "false";
    if ($base === "double") {
        $text = sprintf("%.5F", $value);
        return $text === "-0.00000" ? "0.00000" : $text;
    }
    return (string) $value;
}

function loj_judge_write($spec, $value) {
    [$kind, $base, $dims] = $spec;
    if ($kind === "scalar") return $base === "string" ? (string) $value : loj_judge_token($base, $value);
    if ($kind === "array" && $dims === 1) {
        if ($base === "string") return implode("\n", $value);
        return implode(" ", array_map(fn($v) => loj_judge_token($base, $v), $value));
    }
    if ($kind === "array") {
        return implode("\n", array_map(fn($row) => implode(" ", array_map(fn($v) => loj_judge_token($base, $v), $row)), $value));
    }
    if ($kind === "listnode") {
        $values = [];
        for ($node = $value; $node !== null; $node = $node->next) $values[] = (string) $node->val;
        return implode(" ", $values);
    }
    $tokens = [];
    $queue = [$value];
    for ($q = 0; $q < count($queue); $q++) {
        $node = $queue[$q];
        if ($node === null) { $tokens[] = "null"; continue; }
        $tokens[] = (string) $node->val;
        $queue[] = $node->left;
        $queue[] = $node->right;
    }
    while (count($tokens) && end($tokens) === "null") array_pop($tokens);
    return implode(" ", $tokens);
}
'''


def compose(sig: Signature, code: str) -> str:
    # 사용자가 <?php를 썼으면 떼고 같은 줄에 다시 붙여 줄 번호를 그대로 둔다. 끝의 ?>도 뗀다.
    code = re.sub(r"^\s*<\?php", "", code, count=1)
    code = re.sub(r"\?>\s*$", "", code)
    nodes = ""
    if uses(sig, "listnode"):
        nodes += "\n".join(LIST_NODE) + "\n"
    if uses(sig, "treenode"):
        nodes += "\n".join(TREE_NODE) + "\n"
    reads = "".join(
        f"$loj_a{i} = loj_judge_read($loj_reader, {spec_php(p.type)});\n" for i, p in enumerate(sig.params)
    )
    args = ", ".join(f"$loj_a{i}" for i in range(len(sig.params)))
    index = output_index(sig)
    value = f"$loj_a{index}" if index >= 0 else "$loj_result"
    return (
        "<?php " + code + "\n" + nodes + RUNTIME
        + "$loj_reader = new LojJudgeReader(stream_get_contents(STDIN));\n"
        + reads
        + f"$loj_result = (new Solution())->{sig.function}({args});\n"
        + f"echo loj_judge_write({output_spec_json(sig)}, {value}), \"\\n\";\n"
    )


def spec_php(t: Type) -> str:
    kind, base, dims = spec(t)
    return f'["{kind}", "{base}", {dims}]'
