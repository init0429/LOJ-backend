"""
C: LeetCode처럼 함수 하나. 배열 인자에는 길이 인자(numsSize, gridColSize)가 따라붙고,
배열을 돌려줄 때는 returnSize(2차원이면 returnColumnSizes도)로 길이를 알려 준다.
"""
from .common import comment_block, output_index, uses
from .types import Signature, Type

PRELUDE = """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <math.h>
#include <limits.h>
#include <ctype.h>
#include <stdint.h>
"""

LIST_NODE = [
    "struct ListNode {",
    "    int val;",
    "    struct ListNode *next;",
    "};",
]
TREE_NODE = [
    "struct TreeNode {",
    "    int val;",
    "    struct TreeNode *left;",
    "    struct TreeNode *right;",
    "};",
]

_SCALAR = {"int": "int", "long": "long long", "double": "double", "bool": "bool", "char": "char", "string": "char*"}
# 런타임 함수 이름에 쓰는 자료형 이름
_SUFFIX = {"int": "int", "long": "ll", "double": "double", "bool": "bool", "char": "char", "string": "str"}


def _element(t: Type) -> str:
    return "struct ListNode*" if t.kind == "listnode" else "struct TreeNode*" if t.kind == "treenode" else _SCALAR[t.base]


def type_name(t: Type) -> str:
    if t.kind == "array":
        return _SCALAR[t.base] + "*" * t.dims
    if t.kind == "void":
        return "void"
    return _element(t)


def _params(sig: Signature) -> list:
    out = []
    for p in sig.params:
        out.append(f"{type_name(p.type)} {p.name}")
        if p.type.kind == "array":
            out.append(f"int {p.name}Size")
            if p.type.dims == 2:
                out.append(f"int* {p.name}ColSize")
    if sig.returns.kind == "array":
        out.append("int* returnSize")
        if sig.returns.dims == 2:
            out.append("int** returnColumnSizes")
    return out


def starter(sig: Signature) -> str:
    parts = []
    if uses(sig, "listnode"):
        parts.append("/**\n" + comment_block(["Definition for singly-linked list.", *LIST_NODE], " * ") + "\n */")
    if uses(sig, "treenode"):
        parts.append("/**\n" + comment_block(["Definition for a binary tree node.", *TREE_NODE], " * ") + "\n */")
    if sig.returns.kind == "array" and sig.returns.dims == 2:
        parts.append("/**\n * Return an array of arrays of size *returnSize.\n"
                     " * The sizes of the arrays are returned as *returnColumnSizes array.\n"
                     " * Note: Both returned array and *columnSizes array must be malloced, assume caller calls free().\n */")
    elif sig.returns.kind == "array":
        parts.append("/**\n * Note: The returned array must be malloced, assume caller calls free().\n */")
    parts.append(f"{type_name(sig.returns)} {sig.function}({', '.join(_params(sig))}) {{\n    \n}}")
    return "\n".join(parts) + "\n"


# 사용자 코드보다 앞에 둔다. 입력은 한 번에 읽어 두고 토큰(숫자)과 줄(문자열) 단위로 꺼낸다.
RUNTIME = r'''
static char *loj_buf;
static size_t loj_len, loj_pos;
static int loj_fresh = 1; /* 아직 이 줄에서 아무것도 읽지 않았는지 */

static void loj_init(void) {
    size_t cap = 1 << 16;
    loj_buf = (char *) malloc(cap + 1);
    size_t got;
    while ((got = fread(loj_buf + loj_len, 1, cap - loj_len, stdin)) > 0) {
        loj_len += got;
        if (loj_len == cap) { cap *= 2; loj_buf = (char *) realloc(loj_buf, cap + 1); }
    }
    loj_buf[loj_len] = '\0';
}

static void loj_fail(void) { fprintf(stderr, "input ended early\n"); exit(1); }

/* 다음 토큰의 시작 위치와 길이 */
static size_t loj_token(size_t *start) {
    while (loj_pos < loj_len && (loj_buf[loj_pos] == ' ' || loj_buf[loj_pos] == '\t' || loj_buf[loj_pos] == '\r' || loj_buf[loj_pos] == '\n')) {
        if (loj_buf[loj_pos] == '\n') loj_fresh = 1;
        loj_pos++;
    }
    if (loj_pos >= loj_len) loj_fail();
    *start = loj_pos;
    while (loj_pos < loj_len && !(loj_buf[loj_pos] == ' ' || loj_buf[loj_pos] == '\t' || loj_buf[loj_pos] == '\r' || loj_buf[loj_pos] == '\n')) loj_pos++;
    loj_fresh = 0;
    return loj_pos - *start;
}

static char *loj_copy(size_t start, size_t length) {
    char *s = (char *) malloc(length + 1);
    memcpy(s, loj_buf + start, length);
    s[length] = '\0';
    return s;
}

static long long loj_read_ll(void) { size_t s, n = loj_token(&s); char *t = loj_copy(s, n); long long v = strtoll(t, NULL, 10); free(t); return v; }
static int loj_read_int(void) { return (int) loj_read_ll(); }
static double loj_read_double(void) { size_t s, n = loj_token(&s); char *t = loj_copy(s, n); double v = strtod(t, NULL); free(t); return v; }
static bool loj_read_bool(void) { size_t s, n = loj_token(&s); return (n == 4 && !strncmp(loj_buf + s, "true", 4)) || (n == 1 && loj_buf[s] == '1'); }
static char loj_read_char(void) { size_t s; loj_token(&s); return loj_buf[s]; }
static int loj_is_null(void) {
    size_t save_pos = loj_pos; int save_fresh = loj_fresh;
    size_t s, n = loj_token(&s);
    if (n == 4 && !strncmp(loj_buf + s, "null", 4)) return 1;
    loj_pos = save_pos; loj_fresh = save_fresh;
    return 0;
}
static char *loj_read_str(void) {
    if (!loj_fresh) {
        while (loj_pos < loj_len && loj_buf[loj_pos] != '\n') loj_pos++;
        if (loj_pos < loj_len) loj_pos++;
    }
    size_t start = loj_pos;
    while (loj_pos < loj_len && loj_buf[loj_pos] != '\n') loj_pos++;
    size_t end = loj_pos;
    if (end > start && loj_buf[end - 1] == '\r') end--;
    if (loj_pos < loj_len) loj_pos++;
    loj_fresh = 1;
    return loj_copy(start, end - start);
}

static void loj_put_int(int v) { printf("%d", v); }
static void loj_put_ll(long long v) { printf("%lld", v); }
static void loj_put_bool(bool v) { fputs(v ? "true" : "false", stdout); }
static void loj_put_char(char v) { putchar(v); }
static void loj_put_str(const char *v) { fputs(v ? v : "", stdout); }
static void loj_put_double(double v) {
    char text[64];
    snprintf(text, sizeof text, "%.5f", v);
    fputs(strcmp(text, "-0.00000") ? text : "0.00000", stdout);
}

#define LOJ_ARRAYS(NAME, TYPE, SEP) \
static TYPE *loj_read_arr_##NAME(int *size) { \
    *size = (int) loj_read_ll(); \
    TYPE *a = (TYPE *) malloc(sizeof(TYPE) * (*size > 0 ? *size : 1)); \
    for (int i = 0; i < *size; i++) a[i] = loj_read_##NAME(); \
    return a; \
} \
static TYPE **loj_read_arr2_##NAME(int *rows, int **cols) { \
    *rows = (int) loj_read_ll(); \
    TYPE **a = (TYPE **) malloc(sizeof(TYPE *) * (*rows > 0 ? *rows : 1)); \
    *cols = (int *) malloc(sizeof(int) * (*rows > 0 ? *rows : 1)); \
    for (int i = 0; i < *rows; i++) a[i] = loj_read_arr_##NAME(&(*cols)[i]); \
    return a; \
} \
static void loj_put_arr_##NAME(TYPE *a, int size) { \
    for (int i = 0; i < size; i++) { if (i) fputs(SEP, stdout); loj_put_##NAME(a[i]); } \
} \
static void loj_put_arr2_##NAME(TYPE **a, int rows, int *cols) { \
    for (int i = 0; i < rows; i++) { if (i) putchar('\n'); for (int j = 0; j < cols[i]; j++) { if (j) putchar(' '); loj_put_##NAME(a[i][j]); } } \
}
LOJ_ARRAYS(int, int, " ")
LOJ_ARRAYS(ll, long long, " ")
LOJ_ARRAYS(double, double, " ")
LOJ_ARRAYS(bool, bool, " ")
LOJ_ARRAYS(char, char, " ")
LOJ_ARRAYS(str, char *, "\n")
'''

LIST_RUNTIME = r'''
static struct ListNode *loj_read_list(void) {
    int n;
    int *values = loj_read_arr_int(&n);
    struct ListNode *head = NULL;
    for (int i = n - 1; i >= 0; i--) {
        struct ListNode *node = (struct ListNode *) malloc(sizeof(struct ListNode));
        node->val = values[i];
        node->next = head;
        head = node;
    }
    return head;
}
static void loj_put_list(struct ListNode *head) {
    for (struct ListNode *node = head; node; node = node->next) { if (node != head) putchar(' '); printf("%d", node->val); }
}
'''

TREE_RUNTIME = r'''
static struct TreeNode *loj_new_tree(int val) {
    struct TreeNode *node = (struct TreeNode *) malloc(sizeof(struct TreeNode));
    node->val = val;
    node->left = node->right = NULL;
    return node;
}
static struct TreeNode *loj_read_tree(void) {
    int n = (int) loj_read_ll();
    if (n == 0) return NULL;
    struct TreeNode **queue = (struct TreeNode **) malloc(sizeof(struct TreeNode *) * n);
    int head = 0, tail = 0, used = 1;
    struct TreeNode *root = loj_is_null() ? NULL : loj_new_tree(loj_read_int());
    if (root) queue[tail++] = root;
    while (head < tail && used < n) {
        struct TreeNode *node = queue[head++];
        for (int side = 0; side < 2 && used < n; side++, used++) {
            if (loj_is_null()) continue;
            struct TreeNode *child = loj_new_tree(loj_read_int());
            if (side == 0) node->left = child; else node->right = child;
            queue[tail++] = child;
        }
    }
    while (used < n) { size_t s; loj_token(&s); used++; }
    free(queue);
    return root;
}
static void loj_put_tree(struct TreeNode *root) {
    int cap = 16, head = 0, tail = 0, last = -1;
    struct TreeNode **queue = (struct TreeNode **) malloc(sizeof(struct TreeNode *) * cap);
    queue[tail++] = root;
    while (head < tail) {
        struct TreeNode *node = queue[head++];
        if (node) {
            last = head - 1;
            if (tail + 2 > cap) { cap *= 2; queue = (struct TreeNode **) realloc(queue, sizeof(struct TreeNode *) * cap); }
            queue[tail++] = node->left;
            queue[tail++] = node->right;
        }
    }
    for (int i = 0; i <= last; i++) {
        if (i) putchar(' ');
        if (queue[i]) printf("%d", queue[i]->val); else fputs("null", stdout);
    }
    free(queue);
}
'''


def _reader(t: Type, var: str) -> list:
    """인자 하나를 읽는 문장들과 함수에 넘길 식"""
    if t.kind == "scalar":
        return [f"{type_name(t)} {var} = loj_read_{_SUFFIX[t.base]}();"], [var]
    if t.kind == "listnode":
        return [f"struct ListNode* {var} = loj_read_list();"], [var]
    if t.kind == "treenode":
        return [f"struct TreeNode* {var} = loj_read_tree();"], [var]
    suffix = _SUFFIX[t.base]
    if t.dims == 1:
        return [f"int {var}_size; {type_name(t)} {var} = loj_read_arr_{suffix}(&{var}_size);"], [var, f"{var}_size"]
    return ([f"int {var}_size; int* {var}_cols; {type_name(t)} {var} = loj_read_arr2_{suffix}(&{var}_size, &{var}_cols);"],
            [var, f"{var}_size", f"{var}_cols"])


def _writer(t: Type, var: str, size: str = "", cols: str = "") -> str:
    if t.kind == "scalar":
        return f"loj_put_{_SUFFIX[t.base]}({var});"
    if t.kind == "listnode":
        return f"loj_put_list({var});"
    if t.kind == "treenode":
        return f"loj_put_tree({var});"
    if t.dims == 1:
        return f"loj_put_arr_{_SUFFIX[t.base]}({var}, {size});"
    return f"loj_put_arr2_{_SUFFIX[t.base]}({var}, {size}, {cols});"


def compose(sig: Signature, code: str) -> str:
    head = PRELUDE
    runtime = RUNTIME
    if uses(sig, "listnode"):
        head += "\n".join(LIST_NODE) + "\n"
        runtime += LIST_RUNTIME
    if uses(sig, "treenode"):
        head += "\n".join(TREE_NODE) + "\n"
        runtime += TREE_RUNTIME

    lines = ["int main(void) {", "    loj_init();"]
    args = []
    for i, p in enumerate(sig.params):
        statements, passed = _reader(p.type, f"loj_a{i}")
        lines += ["    " + s for s in statements]
        args += passed

    returns = sig.returns
    if returns.kind == "array":
        lines.append("    int loj_rsize = 0;")
        args.append("&loj_rsize")
        if returns.dims == 2:
            lines.append("    int* loj_rcols = NULL;")
            args.append("&loj_rcols")
    call = f"{sig.function}({', '.join(args)})"
    index = output_index(sig)
    if index >= 0:
        lines.append(f"    {call};")
        var = f"loj_a{index}"
        lines.append("    " + _writer(sig.output_type, var, f"{var}_size", f"{var}_cols"))
    else:
        lines.append(f"    {type_name(returns)} loj_result = {call};")
        lines.append("    " + _writer(returns, "loj_result", "loj_rsize", "loj_rcols"))
    lines += ["    putchar('\\n');", "    return 0;", "}"]
    # #line으로 오류 메시지의 줄 번호를 사용자 코드 기준으로 맞춘다.
    return head + runtime + '#line 1 "solution.c"\n' + code + '\n#line 1 "driver.c"\n' + "\n".join(lines) + "\n"
