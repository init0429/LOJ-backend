"""Ruby: LeetCode처럼 함수 하나. 함수와 인자 이름은 snake_case."""
from .common import comment_block, output_index, output_spec_json, specs_json, uses
from .types import Signature, Type, snake_case

LIST_NODE = [
    "class ListNode",
    "    attr_accessor :val, :next",
    "    def initialize(val = 0, _next = nil)",
    "        @val = val",
    "        @next = _next",
    "    end",
    "end",
]
TREE_NODE = [
    "class TreeNode",
    "    attr_accessor :val, :left, :right",
    "    def initialize(val = 0, left = nil, right = nil)",
    "        @val = val",
    "        @left = left",
    "        @right = right",
    "    end",
    "end",
]

_SCALAR = {"int": "Integer", "long": "Integer", "double": "Float", "bool": "Boolean", "char": "Character", "string": "String"}


def type_name(t: Type) -> str:
    if t.kind == "scalar":
        return _SCALAR[t.base]
    if t.kind == "array":
        return _SCALAR[t.base] + "[]" * t.dims
    return {"listnode": "ListNode", "treenode": "TreeNode", "void": "Void"}[t.kind]


def starter(sig: Signature) -> str:
    lines = []
    if uses(sig, "listnode"):
        lines += ["# Definition for singly-linked list.", comment_block(LIST_NODE, "# ")]
    if uses(sig, "treenode"):
        lines += ["# Definition for a binary tree node.", comment_block(TREE_NODE, "# ")]
    lines += [f"# @param {{{type_name(p.type)}}} {snake_case(p.name)}" for p in sig.params]
    if sig.returns.kind == "void":
        lines.append(f"# @return {{Void}} Do not return anything, modify {snake_case(sig.output_param)} in-place instead.")
    else:
        lines.append(f"# @return {{{type_name(sig.returns)}}}")
    params = ", ".join(snake_case(p.name) for p in sig.params)
    lines += [f"def {snake_case(sig.function)}({params})", "    ", "end"]
    return "\n".join(lines) + "\n"


RUNTIME = r'''
class LojJudgeReader
    def initialize(text)
        @lines = text.split("\n", -1).map { |l| l.chomp("\r") }
        @li = 0
        @col = 0
    end

    def token
        while @li < @lines.length
            line = @lines[@li]
            c = @col
            c += 1 while c < line.length && (line[c] == " " || line[c] == "\t")
            if c < line.length
                start = c
                c += 1 while c < line.length && line[c] != " " && line[c] != "\t"
                @col = c
                return line[start...c]
            end
            @li += 1
            @col = 0
        end
        raise "input ended early"
    end

    def line
        if @col > 0
            @li += 1
            @col = 0
        end
        return "" if @li >= @lines.length
        line = @lines[@li]
        @li += 1
        line
    end
end

def loj_judge_scalar(r, base)
    return r.line if base == "string"
    t = r.token
    case base
    when "int", "long" then Integer(t, 10)
    when "double" then Float(t)
    when "bool" then t == "true" || t == "1"
    else t
    end
end

def loj_judge_read(r, spec)
    kind, base, dims = spec
    case kind
    when "scalar"
        loj_judge_scalar(r, base)
    when "array"
        Array.new(Integer(r.token, 10)) { dims == 1 ? loj_judge_scalar(r, base) : loj_judge_read(r, ["array", base, 1]) }
    when "listnode"
        values = Array.new(Integer(r.token, 10)) { Integer(r.token, 10) }
        values.reverse.reduce(nil) { |head, v| ListNode.new(v, head) }
    else
        tokens = Array.new(Integer(r.token, 10)) { r.token }
        return nil if tokens.empty? || tokens[0] == "null"
        root = TreeNode.new(Integer(tokens[0], 10))
        queue = [root]
        i = 1
        q = 0
        while q < queue.length && i < tokens.length
            node = queue[q]
            q += 1
            [:left, :right].each do |side|
                if i < tokens.length && tokens[i] != "null"
                    child = TreeNode.new(Integer(tokens[i], 10))
                    node.send("#{side}=", child)
                    queue << child
                end
                i += 1
            end
        end
        root
    end
end

def loj_judge_token(base, value)
    case base
    when "bool" then value ? "true" : "false"
    when "double"
        text = format("%.5f", value)
        text == "-0.00000" ? "0.00000" : text
    else value.to_s
    end
end

def loj_judge_write(spec, value)
    kind, base, dims = spec
    case kind
    when "scalar"
        base == "string" ? value.to_s : loj_judge_token(base, value)
    when "array"
        if dims == 1
            base == "string" ? value.join("\n") : value.map { |v| loj_judge_token(base, v) }.join(" ")
        else
            value.map { |row| row.map { |v| loj_judge_token(base, v) }.join(" ") }.join("\n")
        end
    when "listnode"
        values = []
        node = value
        until node.nil?
            values << node.val.to_s
            node = node.next
        end
        values.join(" ")
    else
        tokens = []
        queue = [value]
        q = 0
        while q < queue.length
            node = queue[q]
            q += 1
            if node.nil?
                tokens << "null"
            else
                tokens << node.val.to_s
                queue << node.left << node.right
            end
        end
        tokens.pop while !tokens.empty? && tokens[-1] == "null"
        tokens.join(" ")
    end
end

def loj_judge_main(function, params, output, output_index)
    reader = LojJudgeReader.new($stdin.read)
    args = params.map { |spec| loj_judge_read(reader, spec) }
    result = send(function, *args)
    $stdout.write(loj_judge_write(output, output_index >= 0 ? args[output_index] : result) + "\n")
end
'''


def compose(sig: Signature, code: str) -> str:
    nodes = ""
    if uses(sig, "listnode"):
        nodes += "\n".join(LIST_NODE) + "\n"
    if uses(sig, "treenode"):
        nodes += "\n".join(TREE_NODE) + "\n"
    return (
        code + "\n" + nodes + RUNTIME
        + f"\nloj_judge_main(:{snake_case(sig.function)}, {specs_json(sig)}, {output_spec_json(sig)}, {output_index(sig)})\n"
    )
