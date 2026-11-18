"""
함수 구현 드라이버를 채점기와 같은 Docker 이미지로 실제로 컴파일·실행해 본다.

시간이 걸리고 이미지가 필요하므로 LOJ_DOCKER_TESTS=1일 때만 돈다.
    LOJ_DOCKER_TESTS=1 python -m unittest test_signature_run

- 자료형마다 "받은 인자를 그대로 돌려주는" 풀이를 9개 언어로 돌려 입력 → 값 → 출력이 맞는지 본다.
  풀이는 시작 코드(starter)의 빈 줄에 return 한 줄을 넣어 만들므로 시작 코드도 함께 검사된다.
- twoSum, 배열 제자리 뒤집기, 연결 리스트 뒤집기, 트리 깊이를 실제 풀이로 돌린다.
- 사용자 코드 3번째 줄의 오류가 오류 메시지에 3번째 줄로 나오는지 본다.
"""
import io
import os
import subprocess
import tarfile
import unittest
from concurrent.futures import ThreadPoolExecutor

import signature as sg
from signature.types import snake_case

RUN = {
    "c": ("gcc:13", "main.c", "gcc -O2 -std=c11 -o main main.c", "./main"),
    "cpp": ("gcc:13", "main.cpp", "g++ -O2 -std=c++17 -o main main.cpp", "./main"),
    "java": ("eclipse-temurin:17-jdk-alpine", "Main.java", "mkdir -p classes && javac -d classes Main.java", "java -cp classes Main"),
    "python": ("python:3.11-alpine", "main.py", "", "python3 main.py"),
    "javascript": ("node:24-alpine", "main.js", "", "node main.js"),
    "typescript": ("node:24-alpine", "main.ts", "", "node main.ts"),
    "ruby": ("ruby:3.4-alpine", "main.rb", "", "ruby main.rb"),
    "php": ("php:8.4-cli-alpine", "main.php", "", "php main.php"),
    "perl": ("perl:5.40-slim", "main.pl", "", "perl main.pl"),
}

SCRIPT = r"""
cd /w
for d in case_*; do
  (
    cd "$d"
    if [ -n "$COMPILE" ]; then sh -c "$COMPILE" > compile.out 2>&1; echo $? > compile.code; fi
    if [ ! -f compile.code ] || [ "$(cat compile.code)" = 0 ]; then
      for f in in_*; do
        n=${f#in_}
        timeout 20 sh -c "$RUN" < "$f" > "out_$n" 2> "err_$n"; echo $? > "code_$n"
      done
    fi
  ) 1>&2
done
tar -c -C /w .
"""


def sig(function, params, returns, output_param=None):
    return sg.parse_signature({
        "function": function,
        "params": [{"name": n, "type": t} for n, t in params],
        "returns": returns,
        "output_param": output_param,
    })


def fill(starter: str, body: list) -> str:
    """시작 코드의 빈 줄(공백만 있는 줄)에 풀이를 넣는다."""
    lines = starter.split("\n")
    index = max(i for i, line in enumerate(lines) if line and not line.strip())
    indent = lines[index]
    lines[index:index + 1] = [indent + line for line in body]
    return "\n".join(lines)


def return_param(language: str, signature, name: str) -> list:
    param = next(p for p in signature.params if p.name == name)
    if language == "python":
        return [f"return {name}"]
    if language == "ruby":
        return [snake_case(name)]
    if language in ("php", "perl"):
        return [f"return ${name};"]
    if language == "c":
        lines = []
        if param.type.kind == "array":
            lines.append(f"*returnSize = {name}Size;")
            if param.type.dims == 2:
                lines.append(f"*returnColumnSizes = {name}ColSize;")
        return lines + [f"return {name};"]
    return [f"return {name};"]


def normalize(text: str) -> list:
    """채점기의 outputs_match와 같은 비교"""
    return [line.rstrip() for line in text.strip().split("\n")]


ECHO_VALUES = {
    "int": [7, -2147483648, 0],
    "long": [4000000000, -5],
    "double": [2.5, -0.0, 0.001],
    "bool": [True, False],
    "char": ["a", "#"],
    "string": ["hello world", "", "  lead"],
    "int[]": [[2, 7, 11, 15], []],
    "long[]": [[4000000000, -1]],
    "double[]": [[1.5, -2.0]],
    "bool[]": [[True, False, True]],
    "char[]": [["h", "i"]],
    "string[]": [["a b", "", "c"], []],
    "int[][]": [[[1, 2], [], [3]], []],
    "char[][]": [[["a", "b"], ["c", "d"]]],
    "string[][]": [[["x y", "z"], ["w"]]],
    "list<int>": [[5, 6]],
    "list<string>": [["p q", "r"]],
    "list<list<int>>": [[[1], [2, 3]]],
    "ListNode": [[1, 2, 3], []],
    "TreeNode": [[3, 9, 20, None, None, 15, 7], [], [1, None, 2], [1, 2, 3, 4, None, None, 5]],
}

TWO_SUM = {
    "python": ["seen = {}", "for i, x in enumerate(nums):", "    if target - x in seen:",
               "        return [seen[target - x], i]", "    seen[x] = i", "return []"],
    "javascript": ["const seen = new Map();", "for (let i = 0; i < nums.length; i++) {",
                   "    if (seen.has(target - nums[i])) return [seen.get(target - nums[i]), i];",
                   "    seen.set(nums[i], i);", "}", "return [];"],
    "ruby": ["seen = {}", "nums.each_with_index do |x, i|", "  return [seen[target - x], i] if seen.key?(target - x)",
             "  seen[x] = i", "end", "[]"],
    "php": ["$seen = [];", "foreach ($nums as $i => $x) {",
            "    if (isset($seen[$target - $x])) return [$seen[$target - $x], $i];", "    $seen[$x] = $i;", "}", "return [];"],
    "perl": ["my %seen;", "for my $i (0 .. $#$nums) {", "    my $x = $nums->[$i];",
             "    return [$seen{$target - $x}, $i] if exists $seen{$target - $x};", "    $seen{$x} = $i;", "}", "return [];"],
    "java": ["Map<Integer, Integer> seen = new HashMap<>();", "for (int i = 0; i < nums.length; i++) {",
             "    if (seen.containsKey(target - nums[i])) return new int[]{seen.get(target - nums[i]), i};",
             "    seen.put(nums[i], i);", "}", "return new int[0];"],
    "cpp": ["unordered_map<int, int> seen;", "for (int i = 0; i < (int) nums.size(); i++) {",
            "    auto it = seen.find(target - nums[i]);", "    if (it != seen.end()) return {it->second, i};",
            "    seen[nums[i]] = i;", "}", "return {};"],
    "c": ["int* out = malloc(sizeof(int) * 2);", "*returnSize = 0;", "for (int i = 0; i < numsSize; i++)",
          "    for (int j = i + 1; j < numsSize; j++)",
          "        if (nums[i] + nums[j] == target) { out[0] = i; out[1] = j; *returnSize = 2; return out; }", "return out;"],
}
TWO_SUM["typescript"] = TWO_SUM["javascript"]

REVERSE_ARRAY = {
    "python": ["nums.reverse()"], "javascript": ["nums.reverse();"], "typescript": ["nums.reverse();"],
    "ruby": ["nums.reverse!"], "php": ["$nums = array_reverse($nums);"], "perl": ["@$nums = reverse @$nums;"],
    "java": ["for (int i = 0, j = nums.length - 1; i < j; i++, j--) { int t = nums[i]; nums[i] = nums[j]; nums[j] = t; }"],
    "cpp": ["std::reverse(nums.begin(), nums.end());"],
    "c": ["for (int i = 0, j = numsSize - 1; i < j; i++, j--) { int t = nums[i]; nums[i] = nums[j]; nums[j] = t; }"],
}

REVERSE_LIST = {
    "python": ["prev = None", "while head:", "    head.next, prev, head = prev, head, head.next", "return prev"],
    "javascript": ["let prev = null;", "while (head) { const next = head.next; head.next = prev; prev = head; head = next; }", "return prev;"],
    "ruby": ["prev = nil", "while head", "  nxt = head.next", "  head.next = prev", "  prev = head", "  head = nxt", "end", "prev"],
    "php": ["$prev = null;", "while ($head !== null) { $next = $head->next; $head->next = $prev; $prev = $head; $head = $next; }", "return $prev;"],
    "perl": ["my $prev;", "while ($head) { my $next = $head->{next}; $head->{next} = $prev; $prev = $head; $head = $next; }", "return $prev;"],
    "java": ["ListNode prev = null;", "while (head != null) { ListNode next = head.next; head.next = prev; prev = head; head = next; }", "return prev;"],
    "cpp": ["ListNode* prev = nullptr;", "while (head) { ListNode* next = head->next; head->next = prev; prev = head; head = next; }", "return prev;"],
    "c": ["struct ListNode* prev = NULL;", "while (head) { struct ListNode* next = head->next; head->next = prev; prev = head; head = next; }", "return prev;"],
}
REVERSE_LIST["typescript"] = REVERSE_LIST["javascript"]

MAX_DEPTH = {
    "python": ["if not root:", "    return 0", "return 1 + max(self.maxDepth(root.left), self.maxDepth(root.right))"],
    "javascript": ["if (!root) return 0;", "return 1 + Math.max(maxDepth(root.left), maxDepth(root.right));"],
    "ruby": ["return 0 if root.nil?", "1 + [max_depth(root.left), max_depth(root.right)].max"],
    "php": ["if ($root === null) return 0;", "return 1 + max($this->maxDepth($root->left), $this->maxDepth($root->right));"],
    "perl": ["return 0 unless $root;", "return 1 + max(maxDepth($root->{left}), maxDepth($root->{right}));"],
    "java": ["if (root == null) return 0;", "return 1 + Math.max(maxDepth(root.left), maxDepth(root.right));"],
    "cpp": ["if (!root) return 0;", "return 1 + max(maxDepth(root->left), maxDepth(root->right));"],
    "c": ["if (!root) return 0;", "int l = maxDepth(root->left), r = maxDepth(root->right);", "return 1 + (l > r ? l : r);"],
}
MAX_DEPTH["typescript"] = MAX_DEPTH["javascript"]

# 3번째 줄에서 없는 함수를 부른다. 컴파일 언어는 컴파일 메시지, 나머지는 실행 오류에 줄 번호가 나와야 한다.
BROKEN = {
    "python": ("class Solution:\n    def same(self, x: int) -> int:\n        return undefined_function_xyz(x)\n", 'solution.py", line 3'),
    "javascript": ("var same = function(x) {\n    // three\n    return undefined_function_xyz(x);\n};\n", "main.js:3"),
    "typescript": ("function same(x: number): number {\n    // three\n    return undefined_function_xyz(x);\n}\n", "main.ts:3"),
    "ruby": ("def same(x)\n  # three\n  undefined_function_xyz(x)\nend\n", "main.rb:3"),
    "php": ("class Solution {\n    function same($x) {\n        return undefined_function_xyz($x);\n    }\n}\n", "main.php:3"),
    "perl": ("sub same {\n    my ($x) = @_;\n    return undefined_function_xyz($x);\n}\n", "solution.pl line 3"),
    "java": ("class Solution {\n    public int same(int x) {\n        return undefined_function_xyz(x);\n    }\n}\n", "Main.java:3"),
    "cpp": ("class Solution {\npublic:\n    int same(int x) { return undefined_function_xyz(x); }\n};\n", "solution.cpp:3"),
    "c": ("int same(int x) {\n    int y = x;\n    return undefined_function_xyz(y);\n}\n", "solution.c:3"),
}


def build_cases(language: str) -> list:
    """(이름, 합친 코드, [(입력, 기대 출력)], 오류에 나와야 할 글자)"""
    cases = []
    for type_text, values in ECHO_VALUES.items():
        s = sig("same", [("x", type_text)], type_text)
        code = fill(sg.starter(s, language), return_param(language, s, "x"))
        io_pairs = [(sg.encode_input(s, [v]), sg.encode_output(s.returns, v)) for v in values]
        cases.append((f"echo {type_text}", sg.compose(s, language, code), io_pairs, None))

    # 문자열(줄 단위)과 숫자(토큰 단위)가 섞인 입력에서 각 인자가 제자리에 읽히는지
    mixed = [("n", "int"), ("s", "string"), ("words", "string[]"), ("x", "double"), ("c", "char"),
             ("flag", "bool"), ("big", "long"), ("grid", "int[][]"), ("tail", "string")]
    inputs = [
        [3, "", ["a", "b c"], 1.5, "z", True, 10, [[1], [2, 3]], "end of input"],
        [0, "two words", [], -0.5, "q", False, -7, [], ""],
    ]
    for pick in ("s", "words", "grid", "tail", "big"):
        params = [(n, t) for n, t in mixed]
        s = sig("pick", params, dict(mixed)[pick])
        code = fill(sg.starter(s, language), return_param(language, s, pick))
        index = [n for n, _ in mixed].index(pick)
        io_pairs = [(sg.encode_input(s, v), sg.encode_output(s.returns, v[index])) for v in inputs]
        cases.append((f"pick {pick}", sg.compose(s, language, code), io_pairs, None))

    s = sig("twoSum", [("nums", "int[]"), ("target", "int")], "int[]")
    cases.append(("twoSum", sg.compose(s, language, fill(sg.starter(s, language), TWO_SUM[language])),
                  [(sg.encode_input(s, [n, t]), sg.encode_output(s.returns, a))
                   for n, t, a in (([2, 7, 11, 15], 9, [0, 1]), ([3, 2, 4], 6, [1, 2]), ([3, 3], 6, [0, 1]))], None))

    s = sig("reverseArray", [("nums", "int[]")], "void", "nums")
    cases.append(("reverseArray (in-place)", sg.compose(s, language, fill(sg.starter(s, language), REVERSE_ARRAY[language])),
                  [(sg.encode_input(s, [v]), sg.encode_output(s.output_type, v[::-1])) for v in ([1, 2, 3], [])], None))

    s = sig("reverseList", [("head", "ListNode")], "ListNode")
    cases.append(("reverseList", sg.compose(s, language, fill(sg.starter(s, language), REVERSE_LIST[language])),
                  [(sg.encode_input(s, [v]), sg.encode_output(s.returns, v[::-1])) for v in ([1, 2, 3, 4], [5], [])], None))

    s = sig("maxDepth", [("root", "TreeNode")], "int")
    cases.append(("maxDepth", sg.compose(s, language, fill(sg.starter(s, language), MAX_DEPTH[language])),
                  [(sg.encode_input(s, [v]), str(d)) for v, d in (([3, 9, 20, None, None, 15, 7], 3), ([], 0), ([1, None, 2], 2))], None))

    s = sig("same", [("x", "int")], "int")
    code, marker = BROKEN[language]
    cases.append(("error line", sg.compose(s, language, code), [("5\n", None)], marker))
    return cases


def run_language(language: str):
    image, filename, compile_command, run_command = RUN[language]
    cases = build_cases(language)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        def add(path, text):
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for i, (_, code, pairs, _) in enumerate(cases):
            add(f"case_{i:02d}/{filename}", code)
            for j, (stdin, _) in enumerate(pairs):
                add(f"case_{i:02d}/in_{j}", stdin)
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network=none", "-e", f"COMPILE={compile_command}", "-e", f"RUN={run_command}",
         image, "sh", "-c", "mkdir -p /w && tar -x -C /w && " + SCRIPT],
        input=archive.getvalue(), capture_output=True, timeout=1200,
    )
    files = {}
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name.lstrip("./")] = tar.extractfile(member).read().decode(errors="replace")
    return cases, files


@unittest.skipUnless(os.getenv("LOJ_DOCKER_TESTS") == "1", "LOJ_DOCKER_TESTS=1일 때만 Docker로 실행한다")
class DriverRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with ThreadPoolExecutor(max_workers=4) as pool:
            cls.results = dict(zip(RUN, pool.map(run_language, RUN)))

    def test_every_language(self):
        for language, (cases, files) in self.results.items():
            for i, (name, _, pairs, marker) in enumerate(cases):
                folder = f"case_{i:02d}"
                compile_log = files.get(f"{folder}/compile.out", "")
                for j, (stdin, expected) in enumerate(pairs):
                    with self.subTest(language=language, case=name, input=stdin[:60]):
                        if marker:
                            # PHP는 오류를 표준 출력에 쓰므로 출력도 함께 본다.
                            log = compile_log + files.get(f"{folder}/err_{j}", "") + files.get(f"{folder}/out_{j}", "")
                            self.assertIn(marker, log)
                            continue
                        self.assertEqual(files.get(f"{folder}/compile.code", "0").strip(), "0", compile_log[-3000:])
                        self.assertEqual(files.get(f"{folder}/code_{j}", "").strip(), "0", files.get(f"{folder}/err_{j}", "")[-3000:])
                        self.assertEqual(normalize(files.get(f"{folder}/out_{j}", "")), normalize(expected))


if __name__ == "__main__":
    unittest.main()
