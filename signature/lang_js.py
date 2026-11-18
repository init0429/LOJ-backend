"""JavaScript와 TypeScript: LeetCode처럼 함수 하나. 드라이버는 둘이 같다(평범한 JS는 TS로도 실행된다)."""
from .common import comment_block, output_index, output_spec_json, specs_json, uses
from .types import Signature, Type

JS_LIST_NODE = [
    "function ListNode(val, next) {",
    "    this.val = (val===undefined ? 0 : val)",
    "    this.next = (next===undefined ? null : next)",
    "}",
]
JS_TREE_NODE = [
    "function TreeNode(val, left, right) {",
    "    this.val = (val===undefined ? 0 : val)",
    "    this.left = (left===undefined ? null : left)",
    "    this.right = (right===undefined ? null : right)",
    "}",
]
TS_LIST_NODE = [
    "class ListNode {",
    "    val: number",
    "    next: ListNode | null",
    "    constructor(val?: number, next?: ListNode | null) {",
    "        this.val = (val===undefined ? 0 : val)",
    "        this.next = (next===undefined ? null : next)",
    "    }",
    "}",
]
TS_TREE_NODE = [
    "class TreeNode {",
    "    val: number",
    "    left: TreeNode | null",
    "    right: TreeNode | null",
    "    constructor(val?: number, left?: TreeNode | null, right?: TreeNode | null) {",
    "        this.val = (val===undefined ? 0 : val)",
    "        this.left = (left===undefined ? null : left)",
    "        this.right = (right===undefined ? null : right)",
    "    }",
    "}",
]

_JS = {"int": "number", "long": "number", "double": "number", "bool": "boolean", "char": "character", "string": "string"}
_TS = {**_JS, "char": "string"}


def _type(t: Type, names: dict, nullable: bool) -> str:
    if t.kind == "scalar":
        return names[t.base]
    if t.kind == "array":
        return names[t.base] + "[]" * t.dims
    if t.kind == "void":
        return "void"
    name = "ListNode" if t.kind == "listnode" else "TreeNode"
    return f"{name} | null" if nullable else name


def _definitions(sig: Signature, list_node, tree_node) -> list:
    blocks = []
    if uses(sig, "listnode"):
        blocks.append(["Definition for singly-linked list.", *list_node])
    if uses(sig, "treenode"):
        blocks.append(["Definition for a binary tree node.", *tree_node])
    return ["/**\n" + comment_block(block, " * ") + "\n */" for block in blocks]


def starter_js(sig: Signature) -> str:
    doc = [f"@param {{{_type(p.type, _JS, False)}}} {p.name}" for p in sig.params]
    if sig.returns.kind == "void":
        doc.append(f"@return {{void}} Do not return anything, modify {sig.output_param} in-place instead.")
    else:
        doc.append(f"@return {{{_type(sig.returns, _JS, False)}}}")
    names = ", ".join(p.name for p in sig.params)
    parts = _definitions(sig, JS_LIST_NODE, JS_TREE_NODE)
    parts.append("/**\n" + comment_block(doc, " * ") + "\n */")
    parts.append(f"var {sig.function} = function({names}) {{\n    \n}};")
    return "\n".join(parts) + "\n"


def starter_ts(sig: Signature) -> str:
    params = ", ".join(f"{p.name}: {_type(p.type, _TS, True)}" for p in sig.params)
    parts = _definitions(sig, TS_LIST_NODE, TS_TREE_NODE)
    body = "\n    \n"
    if sig.returns.kind == "void":
        body = f"\n    /**\n     Do not return anything, modify {sig.output_param} in-place instead.\n     */\n    \n"
    parts.append(f"function {sig.function}({params}): {_type(sig.returns, _TS, True)} {{{body}}};")
    return "\n".join(parts) + "\n"


RUNTIME = r"""
function __lojReader(text) {
    const lines = text.split("\n").map((l) => (l.endsWith("\r") ? l.slice(0, -1) : l));
    let li = 0, col = 0;
    return {
        token() {
            while (li < lines.length) {
                const line = lines[li];
                let c = col;
                while (c < line.length && (line[c] === " " || line[c] === "\t")) c++;
                if (c < line.length) {
                    const start = c;
                    while (c < line.length && line[c] !== " " && line[c] !== "\t") c++;
                    col = c;
                    return line.slice(start, c);
                }
                li++;
                col = 0;
            }
            throw new Error("input ended early");
        },
        line() {
            if (col > 0) { li++; col = 0; }
            if (li >= lines.length) return "";
            return lines[li++];
        },
    };
}

function __lojScalar(r, base) {
    if (base === "string") return r.line();
    const t = r.token();
    if (base === "int" || base === "long" || base === "double") return Number(t);
    if (base === "bool") return t === "true" || t === "1";
    return t;
}

function __lojRead(r, spec) {
    const [kind, base, dims] = spec;
    const count = () => parseInt(r.token(), 10);
    if (kind === "scalar") return __lojScalar(r, base);
    if (kind === "array") {
        const n = count(), out = [];
        for (let i = 0; i < n; i++) out.push(dims === 1 ? __lojScalar(r, base) : __lojRead(r, ["array", base, 1]));
        return out;
    }
    if (kind === "listnode") {
        const n = count(), values = [];
        for (let i = 0; i < n; i++) values.push(Number(r.token()));
        let head = null;
        for (let i = n - 1; i >= 0; i--) head = new ListNode(values[i], head);
        return head;
    }
    const n = count(), tokens = [];
    for (let i = 0; i < n; i++) tokens.push(r.token());
    if (n === 0 || tokens[0] === "null") return null;
    const root = new TreeNode(Number(tokens[0]));
    const queue = [root];
    let i = 1;
    for (let q = 0; q < queue.length && i < tokens.length; q++) {
        const node = queue[q];
        for (const side of ["left", "right"]) {
            if (i < tokens.length && tokens[i] !== "null") {
                node[side] = new TreeNode(Number(tokens[i]));
                queue.push(node[side]);
            }
            i++;
        }
    }
    return root;
}

function __lojToken(base, value) {
    if (base === "bool") return value ? "true" : "false";
    if (base === "double") {
        const text = Number(value).toFixed(5);
        return text === "-0.00000" ? "0.00000" : text;
    }
    return String(value);
}

function __lojWrite(spec, value) {
    const [kind, base, dims] = spec;
    if (kind === "scalar") return base === "string" ? String(value) : __lojToken(base, value);
    if (kind === "array" && dims === 1) {
        return base === "string" ? value.join("\n") : value.map((v) => __lojToken(base, v)).join(" ");
    }
    if (kind === "array") return value.map((row) => row.map((v) => __lojToken(base, v)).join(" ")).join("\n");
    if (kind === "listnode") {
        const values = [];
        for (let node = value; node !== null && node !== undefined; node = node.next) values.push(String(node.val));
        return values.join(" ");
    }
    const tokens = [], queue = [value];
    for (let q = 0; q < queue.length; q++) {
        const node = queue[q];
        if (node === null || node === undefined) tokens.push("null");
        else { tokens.push(String(node.val)); queue.push(node.left, node.right); }
    }
    while (tokens.length && tokens[tokens.length - 1] === "null") tokens.pop();
    return tokens.join(" ");
}

function __lojMain(fn, params, output, outputIndex) {
    const reader = __lojReader(process.getBuiltinModule("fs").readFileSync(0, "utf8"));
    const args = params.map((spec) => __lojRead(reader, spec));
    const result = fn(...args);
    process.stdout.write(__lojWrite(output, outputIndex >= 0 ? args[outputIndex] : result) + "\n");
}
"""


def _compose(sig: Signature, code: str, list_node, tree_node) -> str:
    nodes = ""
    if uses(sig, "listnode"):
        nodes += "\n".join(list_node) + "\n"
    if uses(sig, "treenode"):
        nodes += "\n".join(tree_node) + "\n"
    # 사용자 코드를 맨 앞에 두어 오류의 줄 번호가 그대로 나오게 하고, 정의와 드라이버는 뒤에 붙인다.
    return (
        code + "\n;\n" + nodes + RUNTIME
        + f"\n__lojMain({sig.function}, {specs_json(sig)}, {output_spec_json(sig)}, {output_index(sig)});\n"
    )


def compose_js(sig: Signature, code: str) -> str:
    return _compose(sig, code, JS_LIST_NODE, JS_TREE_NODE)


def compose_ts(sig: Signature, code: str) -> str:
    return _compose(sig, code, TS_LIST_NODE, TS_TREE_NODE)
