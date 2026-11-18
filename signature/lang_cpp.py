"""C++: LeetCode처럼 class Solution의 메서드. 배열은 vector, 인자는 참조로 받는다."""
from .common import comment_block, output_index, uses
from .types import Signature, Type

PRELUDE = "#include <bits/stdc++.h>\nusing namespace std;\n"

LIST_NODE = [
    "struct ListNode {",
    "    int val;",
    "    ListNode *next;",
    "    ListNode() : val(0), next(nullptr) {}",
    "    ListNode(int x) : val(x), next(nullptr) {}",
    "    ListNode(int x, ListNode *next) : val(x), next(next) {}",
    "};",
]
TREE_NODE = [
    "struct TreeNode {",
    "    int val;",
    "    TreeNode *left;",
    "    TreeNode *right;",
    "    TreeNode() : val(0), left(nullptr), right(nullptr) {}",
    "    TreeNode(int x) : val(x), left(nullptr), right(nullptr) {}",
    "    TreeNode(int x, TreeNode *left, TreeNode *right) : val(x), left(left), right(right) {}",
    "};",
]

_SCALAR = {"int": "int", "long": "long long", "double": "double", "bool": "bool", "char": "char", "string": "string"}


def type_name(t: Type) -> str:
    if t.kind == "scalar":
        return _SCALAR[t.base]
    if t.kind == "array":
        return "vector<" * t.dims + _SCALAR[t.base] + ">" * t.dims
    return {"listnode": "ListNode*", "treenode": "TreeNode*", "void": "void"}[t.kind]


def _param_type(t: Type) -> str:
    # LeetCode처럼 vector는 참조로, 나머지는 값으로 받는다.
    return type_name(t) + ("&" if t.kind == "array" else "")


def starter(sig: Signature) -> str:
    parts = []
    if uses(sig, "listnode"):
        parts.append("/**\n" + comment_block(["Definition for singly-linked list.", *LIST_NODE], " * ") + "\n */")
    if uses(sig, "treenode"):
        parts.append("/**\n" + comment_block(["Definition for a binary tree node.", *TREE_NODE], " * ") + "\n */")
    params = ", ".join(f"{_param_type(p.type)} {p.name}" for p in sig.params)
    parts.append(
        "class Solution {\npublic:\n"
        + f"    {type_name(sig.returns)} {sig.function}({params}) {{\n        \n    }}\n}};"
    )
    return "\n".join(parts) + "\n"


# 사용자 코드보다 앞에 둔다. 그래야 사용자가 #define int long long 같은 매크로를 써도 영향이 없다.
RUNTIME = r'''
namespace loj_judge {
static vector<string> lines;
static size_t li = 0, col = 0;

static void init() {
    std::ios::sync_with_stdio(false);
    string text((istreambuf_iterator<char>(cin)), istreambuf_iterator<char>());
    size_t start = 0;
    while (true) {
        size_t end = text.find('\n', start);
        string line = text.substr(start, end == string::npos ? string::npos : end - start);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        lines.push_back(line);
        if (end == string::npos) break;
        start = end + 1;
    }
}

static string token() {
    while (li < lines.size()) {
        const string& line = lines[li];
        size_t c = col;
        while (c < line.size() && (line[c] == ' ' || line[c] == '\t')) c++;
        if (c < line.size()) {
            size_t start = c;
            while (c < line.size() && line[c] != ' ' && line[c] != '\t') c++;
            col = c;
            return line.substr(start, c - start);
        }
        li++;
        col = 0;
    }
    fprintf(stderr, "input ended early\n");
    exit(1);
}

static string line() {
    if (col > 0) { li++; col = 0; }
    if (li >= lines.size()) return "";
    return lines[li++];
}

static int count() { return stoi(token()); }

template <class T> struct R;
template <> struct R<int> { static int get() { return stoi(token()); } };
template <> struct R<long long> { static long long get() { return stoll(token()); } };
template <> struct R<double> { static double get() { return stod(token()); } };
template <> struct R<bool> { static bool get() { string t = token(); return t == "true" || t == "1"; } };
template <> struct R<char> { static char get() { return token()[0]; } };
template <> struct R<string> { static string get() { return line(); } };
template <class T> struct R<vector<T>> {
    static vector<T> get() {
        int n = count();
        vector<T> v;
        v.reserve(n);
        for (int i = 0; i < n; i++) v.push_back(R<T>::get());
        return v;
    }
};

static string fmt(int v) { return to_string(v); }
static string fmt(long long v) { return to_string(v); }
static string fmt(bool v) { return v ? "true" : "false"; }
static string fmt(char v) { return string(1, v); }
static string fmt(const string& v) { return v; }
static string fmt(double v) {
    char buffer[64];
    snprintf(buffer, sizeof buffer, "%.5f", v);
    string text = buffer;
    return text == "-0.00000" ? "0.00000" : text;
}

template <class T> static string out(const T& v) { return fmt(v); }
static string out(const vector<string>& v) {
    string text;
    for (size_t i = 0; i < v.size(); i++) text += (i ? "\n" : "") + v[i];
    return text;
}
template <class T> static string out(const vector<T>& v) {
    string text;
    for (size_t i = 0; i < v.size(); i++) text += (i ? " " : "") + fmt(T(v[i]));
    return text;
}
template <class T> static string out(const vector<vector<T>>& v) {
    string text;
    for (size_t i = 0; i < v.size(); i++) {
        if (i) text += "\n";
        for (size_t j = 0; j < v[i].size(); j++) text += (j ? " " : "") + fmt(T(v[i][j]));
    }
    return text;
}
'''

LIST_RUNTIME = r'''
template <> struct R<ListNode*> {
    static ListNode* get() {
        vector<int> values = R<vector<int>>::get();
        ListNode* head = nullptr;
        for (int i = (int) values.size() - 1; i >= 0; i--) head = new ListNode(values[i], head);
        return head;
    }
};
static string out(ListNode* head) {
    string text;
    for (ListNode* node = head; node; node = node->next) text += (text.empty() ? "" : " ") + to_string(node->val);
    return text;
}
'''

TREE_RUNTIME = r'''
template <> struct R<TreeNode*> {
    static TreeNode* get() {
        int n = count();
        vector<string> tokens;
        for (int i = 0; i < n; i++) tokens.push_back(token());
        if (n == 0 || tokens[0] == "null") return nullptr;
        TreeNode* root = new TreeNode(stoi(tokens[0]));
        vector<TreeNode*> queue = {root};
        size_t i = 1;
        for (size_t q = 0; q < queue.size() && i < tokens.size(); q++) {
            if (i < tokens.size() && tokens[i] != "null") { queue[q]->left = new TreeNode(stoi(tokens[i])); queue.push_back(queue[q]->left); }
            i++;
            if (i < tokens.size() && tokens[i] != "null") { queue[q]->right = new TreeNode(stoi(tokens[i])); queue.push_back(queue[q]->right); }
            i++;
        }
        return root;
    }
};
static string out(TreeNode* root) {
    vector<string> tokens;
    vector<TreeNode*> queue = {root};
    for (size_t q = 0; q < queue.size(); q++) {
        TreeNode* node = queue[q];
        if (!node) { tokens.push_back("null"); continue; }
        tokens.push_back(to_string(node->val));
        queue.push_back(node->left);
        queue.push_back(node->right);
    }
    while (!tokens.empty() && tokens.back() == "null") tokens.pop_back();
    string text;
    for (size_t i = 0; i < tokens.size(); i++) text += (i ? " " : "") + tokens[i];
    return text;
}
'''


def compose(sig: Signature, code: str) -> str:
    head = PRELUDE
    runtime = RUNTIME
    if uses(sig, "listnode"):
        head += "\n".join(LIST_NODE) + "\n"
        runtime += LIST_RUNTIME
    if uses(sig, "treenode"):
        head += "\n".join(TREE_NODE) + "\n"
        runtime += TREE_RUNTIME
    runtime += "}  // namespace loj_judge\n"

    lines = ["signed main() {", "    loj_judge::init();"]
    for i, p in enumerate(sig.params):
        lines.append(f"    auto loj_a{i} = loj_judge::R<{type_name(p.type)}>::get();")
    args = ", ".join(f"loj_a{i}" for i in range(len(sig.params)))
    call = f"Solution().{sig.function}({args})"
    index = output_index(sig)
    if index >= 0:
        lines.append(f"    {call};")
        value = f"loj_a{index}"
    else:
        lines.append(f"    auto loj_result = {call};")
        value = "loj_result"
    lines += [f'    string loj_text = loj_judge::out({value});',
              '    fwrite(loj_text.data(), 1, loj_text.size(), stdout);',
              '    putchar(\'\\n\');',
              "    return 0;", "}"]
    # #line으로 오류 메시지의 줄 번호를 사용자 코드 기준으로 맞춘다.
    return head + runtime + '#line 1 "solution.cpp"\n' + code + '\n#line 1 "driver.cpp"\n' + "\n".join(lines) + "\n"
