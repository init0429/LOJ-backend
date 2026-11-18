"""Java: LeetCode처럼 class Solution의 메서드. 합친 파일은 Main.java로 컴파일된다."""
import re

from .common import comment_block, output_index, spec, uses
from .types import Signature, Type

IMPORTS = "import java.util.*; import java.util.function.*; import java.util.stream.*; import java.math.*; import java.io.*; "

LIST_NODE = [
    "class ListNode {",
    "    int val;",
    "    ListNode next;",
    "    ListNode() {}",
    "    ListNode(int val) { this.val = val; }",
    "    ListNode(int val, ListNode next) { this.val = val; this.next = next; }",
    "}",
]
TREE_NODE = [
    "class TreeNode {",
    "    int val;",
    "    TreeNode left;",
    "    TreeNode right;",
    "    TreeNode() {}",
    "    TreeNode(int val) { this.val = val; }",
    "    TreeNode(int val, TreeNode left, TreeNode right) {",
    "        this.val = val;",
    "        this.left = left;",
    "        this.right = right;",
    "    }",
    "}",
]

_PRIMITIVE = {"int": "int", "long": "long", "double": "double", "bool": "boolean", "char": "char", "string": "String"}
_BOXED = {"int": "Integer", "long": "Long", "double": "Double", "bool": "Boolean", "char": "Character", "string": "String"}


def type_name(t: Type) -> str:
    if t.kind == "scalar":
        return _PRIMITIVE[t.base]
    if t.kind == "array":
        if t.is_list:
            return "List<" * t.dims + _BOXED[t.base] + ">" * t.dims
        return _PRIMITIVE[t.base] + "[]" * t.dims
    return {"listnode": "ListNode", "treenode": "TreeNode", "void": "void"}[t.kind]


def _cast(t: Type) -> str:
    return _BOXED[t.base] if t.kind == "scalar" else type_name(t)


def starter(sig: Signature) -> str:
    parts = []
    if uses(sig, "listnode"):
        lines = ["Definition for singly-linked list.", "public " + LIST_NODE[0], *LIST_NODE[1:]]
        parts.append("/**\n" + comment_block(lines, " * ") + "\n */")
    if uses(sig, "treenode"):
        lines = ["Definition for a binary tree node.", "public " + TREE_NODE[0], *TREE_NODE[1:]]
        parts.append("/**\n" + comment_block(lines, " * ") + "\n */")
    params = ", ".join(f"{type_name(p.type)} {p.name}" for p in sig.params)
    parts.append(
        "class Solution {\n"
        + f"    public {type_name(sig.returns)} {sig.function}({params}) {{\n        \n    }}\n}}"
    )
    return "\n".join(parts) + "\n"


LIST_RUNTIME = r'''
    static ListNode lojReadList() {
        int n = Integer.parseInt(lojToken());
        int[] values = new int[n];
        for (int i = 0; i < n; i++) values[i] = Integer.parseInt(lojToken());
        ListNode head = null;
        for (int i = n - 1; i >= 0; i--) head = new ListNode(values[i], head);
        return head;
    }

    static String lojWriteList(ListNode head) {
        StringJoiner values = new StringJoiner(" ");
        for (ListNode node = head; node != null; node = node.next) values.add(String.valueOf(node.val));
        return values.toString();
    }
'''

TREE_RUNTIME = r'''
    static TreeNode lojReadTree() {
        int n = Integer.parseInt(lojToken());
        String[] tokens = new String[n];
        for (int i = 0; i < n; i++) tokens[i] = lojToken();
        if (n == 0 || tokens[0].equals("null")) return null;
        TreeNode root = new TreeNode(Integer.parseInt(tokens[0]));
        List<TreeNode> queue = new ArrayList<>();
        queue.add(root);
        int i = 1;
        for (int q = 0; q < queue.size() && i < n; q++) {
            TreeNode node = queue.get(q);
            if (i < n && !tokens[i].equals("null")) { node.left = new TreeNode(Integer.parseInt(tokens[i])); queue.add(node.left); }
            i++;
            if (i < n && !tokens[i].equals("null")) { node.right = new TreeNode(Integer.parseInt(tokens[i])); queue.add(node.right); }
            i++;
        }
        return root;
    }

    static String lojWriteTree(TreeNode root) {
        List<String> tokens = new ArrayList<>();
        List<TreeNode> queue = new ArrayList<>();
        queue.add(root);
        for (int q = 0; q < queue.size(); q++) {
            TreeNode node = queue.get(q);
            if (node == null) { tokens.add("null"); continue; }
            tokens.add(String.valueOf(node.val));
            queue.add(node.left);
            queue.add(node.right);
        }
        while (!tokens.isEmpty() && tokens.get(tokens.size() - 1).equals("null")) tokens.remove(tokens.size() - 1);
        return String.join(" ", tokens);
    }
'''

# 연결 리스트와 트리를 다루는 부분은 시그니처가 쓸 때만 넣는다(그때만 ListNode, TreeNode 클래스가 있다).
RUNTIME = r'''
public class Main {
    static String[] lojLines;
    static int lojLi = 0, lojCol = 0;

    static String lojToken() {
        while (lojLi < lojLines.length) {
            String line = lojLines[lojLi];
            int c = lojCol, n = line.length();
            while (c < n && (line.charAt(c) == ' ' || line.charAt(c) == '\t')) c++;
            if (c < n) {
                int start = c;
                while (c < n && line.charAt(c) != ' ' && line.charAt(c) != '\t') c++;
                lojCol = c;
                return line.substring(start, c);
            }
            lojLi++;
            lojCol = 0;
        }
        throw new RuntimeException("input ended early");
    }

    static String lojLine() {
        if (lojCol > 0) { lojLi++; lojCol = 0; }
        if (lojLi >= lojLines.length) return "";
        return lojLines[lojLi++];
    }

    static Object lojScalar(String base) {
        switch (base) {
            case "string": return lojLine();
            case "int": return Integer.parseInt(lojToken());
            case "long": return Long.parseLong(lojToken());
            case "double": return Double.parseDouble(lojToken());
            case "bool": { String t = lojToken(); return t.equals("true") || t.equals("1"); }
            default: return lojToken().charAt(0);
        }
    }

    static Class<?> lojRowClass(String base) {
        switch (base) {
            case "int": return int[].class;
            case "long": return long[].class;
            case "double": return double[].class;
            case "bool": return boolean[].class;
            case "char": return char[].class;
            default: return String[].class;
        }
    }

    static Object lojRead(String kind, String base, int dims, boolean isList) {
        if (kind.equals("scalar")) return lojScalar(base);
        int n = Integer.parseInt(lojToken());
        if (kind.equals("array") && dims == 1) {
            List<Object> values = new ArrayList<>();
            for (int i = 0; i < n; i++) values.add(lojScalar(base));
            if (isList) return values;
            Object array = java.lang.reflect.Array.newInstance(lojRowClass(base).getComponentType(), n);
            for (int i = 0; i < n; i++) java.lang.reflect.Array.set(array, i, values.get(i));
            return array;
        }
        if (kind.equals("array")) {
            if (isList) {
                List<Object> rows = new ArrayList<>();
                for (int i = 0; i < n; i++) rows.add(lojRead("array", base, 1, true));
                return rows;
            }
            Object array = java.lang.reflect.Array.newInstance(lojRowClass(base), n);
            for (int i = 0; i < n; i++) java.lang.reflect.Array.set(array, i, lojRead("array", base, 1, false));
            return array;
        }
        throw new IllegalArgumentException(kind);
    }

    static String lojFormat(String base, Object v) {
        if (base.equals("bool")) return ((Boolean) v) ? "true" : "false";
        if (base.equals("double")) {
            String text = String.format(Locale.ROOT, "%.5f", ((Number) v).doubleValue());
            return text.equals("-0.00000") ? "0.00000" : text;
        }
        return String.valueOf(v);
    }

    static List<Object> lojItems(Object v) {
        if (v instanceof List) return new ArrayList<Object>((List<?>) v);
        List<Object> items = new ArrayList<>();
        for (int i = 0, n = java.lang.reflect.Array.getLength(v); i < n; i++) items.add(java.lang.reflect.Array.get(v, i));
        return items;
    }

    static String lojWrite(String kind, String base, int dims, Object v) {
        if (kind.equals("scalar")) return base.equals("string") ? String.valueOf(v) : lojFormat(base, v);
        if (kind.equals("array")) {
            StringJoiner rows = new StringJoiner(dims == 1 && base.equals("string") ? "\n" : dims == 1 ? " " : "\n");
            for (Object item : lojItems(v)) {
                if (dims == 1) { rows.add(base.equals("string") ? String.valueOf(item) : lojFormat(base, item)); continue; }
                StringJoiner row = new StringJoiner(" ");
                for (Object x : lojItems(item)) row.add(lojFormat(base, x));
                rows.add(row.toString());
            }
            return rows.toString();
        }
        throw new IllegalArgumentException(kind);
    }

'''


MAIN = r'''
    @SuppressWarnings("unchecked")
    public static void main(String[] lojArgs) throws Exception {
        lojLines = new String(System.in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8).split("\n", -1);
        for (int i = 0; i < lojLines.length; i++) {
            if (lojLines[i].endsWith("\r")) lojLines[i] = lojLines[i].substring(0, lojLines[i].length() - 1);
        }
'''


def _java_string(text: str) -> str:
    return '"' + text + '"'


def _read_call(t: Type) -> str:
    if t.kind == "listnode":
        return "lojReadList()"
    if t.kind == "treenode":
        return "lojReadTree()"
    kind, base, dims = spec(t)
    return f"lojRead({_java_string(kind)}, {_java_string(base)}, {dims}, {'true' if t.is_list else 'false'})"


def _write_call(t: Type, value: str) -> str:
    if t.kind == "listnode":
        return f"lojWriteList((ListNode) {value})"
    if t.kind == "treenode":
        return f"lojWriteTree((TreeNode) {value})"
    kind, base, dims = spec(t)
    return f"lojWrite({_java_string(kind)}, {_java_string(base)}, {dims}, {value})"


def compose(sig: Signature, code: str) -> str:
    # import는 사용자 코드 첫 줄 앞에 같은 줄로 붙여 줄 번호를 그대로 둔다.
    # Main.java 안에서는 Main만 public일 수 있으므로 public class Solution의 public은 뗀다.
    code = re.sub(r"\bpublic(\s+(?:final\s+)?class\s+Solution\b)", r"\1", code)
    nodes, runtime = "", RUNTIME
    if uses(sig, "listnode"):
        nodes += "\n".join(LIST_NODE) + "\n"
        runtime += LIST_RUNTIME
    if uses(sig, "treenode"):
        nodes += "\n".join(TREE_NODE) + "\n"
        runtime += TREE_RUNTIME
    lines = [f"        Object a{i} = {_read_call(p.type)};" for i, p in enumerate(sig.params)]
    args = ", ".join(f"({_cast(p.type)}) a{i}" for i, p in enumerate(sig.params))
    call = f"new Solution().{sig.function}({args})"
    index = output_index(sig)
    if index >= 0:
        lines.append(f"        {call};")
        value = f"a{index}"
    else:
        lines.append(f"        Object lojResult = {call};")
        value = "lojResult"
    lines.append(f"        System.out.print({_write_call(sig.output_type, value)} + \"\\n\");")
    lines += ["        System.out.flush();", "    }", "}"]
    return IMPORTS + code + "\n" + nodes + runtime + MAIN + "\n".join(lines) + "\n"
