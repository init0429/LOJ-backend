import unittest

import signature as sg
from signature.codec import Reader
from signature.types import snake_case


def make(function="twoSum", params=(("nums", "int[]"), ("target", "int")), returns="int[]", output_param=None):
    return sg.parse_signature({
        "function": function,
        "params": [{"name": n, "type": t} for n, t in params],
        "returns": returns,
        "output_param": output_param,
    })


class TypeTests(unittest.TestCase):
    def test_types_round_trip_to_their_canonical_text(self):
        for text in ("int", "long", "double", "bool", "char", "string", "int[]", "string[][]",
                     "list<int>", "list<list<string>>", "ListNode", "TreeNode"):
            with self.subTest(text=text):
                self.assertEqual(sg.parse_type(text).text, text)
        self.assertEqual(sg.parse_type(" list< int > ").text, "list<int>")
        self.assertEqual(sg.parse_type("void", allow_void=True).kind, "void")

    def test_unknown_or_too_deep_types_are_rejected(self):
        for text in ("integer", "int[][][]", "list<list<list<int>>>", "ListNode[]", "map<int,int>", "", None, "void"):
            with self.subTest(text=text), self.assertRaises(sg.SignatureError):
                sg.parse_type(text)

    def test_ruby_names_follow_leetcode(self):
        self.assertEqual(snake_case("twoSum"), "two_sum")
        self.assertEqual(snake_case("isValidBST"), "is_valid_bst")
        self.assertEqual(snake_case("maxDepth2"), "max_depth2")


class SignatureTests(unittest.TestCase):
    def test_valid_signature_keeps_its_shape(self):
        sig = make()
        self.assertEqual(sig.to_json(), {
            "function": "twoSum",
            "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
            "returns": "int[]",
            "output_param": None,
        })

    def test_names_must_be_usable_in_every_language(self):
        bad = [
            {"function": "2sum"}, {"function": "class"}, {"function": "Solution"}, {"function": "main"},
            {"function": "lojHelper"}, {"function": "x" * 31},
            {"params": (("end", "int"),)}, {"params": (("my", "int"),)}, {"params": (("nums", "int"), ("Nums", "int"))},
            {"params": (("twoSum", "int"),)},
            {"params": (("aB", "int"), ("a_b", "int"))},          # Ruby에서 둘 다 a_b
            {"params": (("nums", "int[]"), ("numsSize", "int"))},  # C의 길이 인자와 겹친다
        ]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(sg.SignatureError):
                make(**change)

    def test_void_needs_an_array_or_node_to_print(self):
        self.assertEqual(make("rotate", (("matrix", "int[][]"),), "void", "matrix").output_type.text, "int[][]")
        self.assertEqual(make("reorderList", (("head", "ListNode"),), "void", "head").output_type.kind, "listnode")
        for params, output in (((("nums", "int[]"),), None), ((("s", "string"),), "s"), ((("n", "int"),), "n"),
                               ((("nums", "int[]"),), "other")):
            with self.subTest(output=output), self.assertRaises(sg.SignatureError):
                make("f", params, "void", output)

    def test_output_param_is_ignored_unless_void(self):
        self.assertIsNone(make(output_param="nums").output_param)

    def test_parameter_count_is_limited(self):
        make("f", [(f"a{i}", "int") for i in range(10)], "int")
        with self.assertRaises(sg.SignatureError):
            make("f", [(f"a{i}", "int") for i in range(11)], "int")


class CodecTests(unittest.TestCase):
    def test_strings_always_start_on_a_new_line(self):
        reader = Reader("3 x\nhello world\n\n2\na\nb c\n")
        self.assertEqual(reader.token(), "3")
        # 같은 줄의 나머지("x")가 아니라 다음 줄을 읽는다.
        self.assertEqual(reader.line(), "hello world")
        self.assertEqual(reader.line(), "")
        self.assertEqual(reader.token(), "2")
        self.assertEqual([reader.line(), reader.line()], ["a", "b c"])
        self.assertEqual(reader.line(), "")

    def test_every_type_round_trips_through_stdin(self):
        sig = make("f", [("n", "int"), ("s", "string"), ("words", "string[]"), ("x", "double"), ("c", "char"),
                         ("flag", "bool"), ("big", "long"), ("grid", "int[][]"), ("names", "list<list<string>>"),
                         ("tail", "string")], "int")
        values = [3, "", ["a", "", "b c"], 1.5, "z", True, 4000000000, [[1], [], [2, 3]], [["x y"], []], "end"]
        text = sg.encode_input(sig, values)
        self.assertEqual(sg.decode_input(sig, text), values)
        empty = [0, "two words", [], -0.5, "q", False, -7, [], [], ""]
        self.assertEqual(sg.decode_input(sig, sg.encode_input(sig, empty)), empty)

    def test_nodes_use_leetcode_order(self):
        sig = make("f", [("head", "ListNode"), ("root", "TreeNode")], "TreeNode")
        text = sg.encode_input(sig, [[1, 2], [3, 9, 20, None, None, 15, 7]])
        self.assertEqual(text, "2\n1 2\n7\n3 9 20 null null 15 7\n")
        self.assertEqual(sg.encode_output(sig.returns, [1, None, 2, None, None]), "1 null 2")
        self.assertEqual(sg.encode_input(make("f", [("root", "TreeNode")], "int"), [[]]), "0\n")

    def test_bojs_style_inputs_read_as_arguments(self):
        # A+B의 "1 2"와 수열 입력은 그대로 인자가 된다.
        self.assertEqual(sg.decode_input(make("add", [("a", "int"), ("b", "int")], "int"), "1 2\n"), [1, 2])
        self.assertEqual(sg.decode_input(make("f", [("nums", "int[]")], "int[]"), "5\n20 10 35 30 7\n"),
                         [[20, 10, 35, 30, 7]])

    def test_broken_inputs_are_reported(self):
        sig = make()
        for text in ("", "2\n1\n", "2\n1 2\n3\nextra\n", "x\n", "-1\n"):
            with self.subTest(text=text), self.assertRaises(sg.SignatureError):
                sg.decode_input(sig, text)

    def test_outputs_are_written_without_counts(self):
        self.assertEqual(sg.encode_output(sg.parse_type("int[]"), [0, 1]), "0 1")
        self.assertEqual(sg.encode_output(sg.parse_type("string[]"), ["a b", "c"]), "a b\nc")
        self.assertEqual(sg.encode_output(sg.parse_type("int[][]"), [[1, 2], [3]]), "1 2\n3")
        self.assertEqual(sg.encode_output(sg.parse_type("double"), 2.5), "2.50000")
        self.assertEqual(sg.encode_output(sg.parse_type("double"), -0.0), "0.00000")
        self.assertEqual(sg.encode_output(sg.parse_type("bool"), False), "false")


class LeetCodeNotationTests(unittest.TestCase):
    def test_both_leetcode_input_forms_are_accepted(self):
        sig = make()
        self.assertEqual(sg.parse_leetcode_input(sig, "nums = [2,7,11,15], target = 9"), [[2, 7, 11, 15], 9])
        self.assertEqual(sg.parse_leetcode_input(sig, "target = 9, nums = [2, 7]"), [[2, 7], 9])
        self.assertEqual(sg.parse_leetcode_input(sig, "[2,7,11,15]\n9"), [[2, 7, 11, 15], 9])

    def test_examples_convert_to_stdin_and_back(self):
        sig = make()
        stdin, stdout = sg.leetcode_to_oj(sig, "nums = [2,7,11,15], target = 9", "[0,1]")
        self.assertEqual((stdin, stdout), ("4\n2 7 11 15\n9\n", "0 1"))
        self.assertEqual(sg.oj_to_leetcode(sig, stdin, stdout + "\n"), ("nums = [2,7,11,15], target = 9", "[0,1]"))

    def test_strings_chars_and_trees_keep_leetcode_spelling(self):
        sig = make("f", [("s", "string"), ("board", "char[][]"), ("root", "TreeNode")], "double")
        text = 's = "a \\"b\\"", board = [["5","."]], root = [1,null,2]'
        values = sg.parse_leetcode_input(sig, text)
        self.assertEqual(values, ['a "b"', [["5", "."]], [1, None, 2]])
        self.assertEqual(sg.format_args(sig, values), text)
        stdin, stdout = sg.leetcode_to_oj(sig, text, "2.5")
        self.assertEqual(stdout, "2.50000")
        self.assertEqual(sg.oj_to_leetcode(sig, stdin, stdout)[1], "2.50000")

    def test_values_that_do_not_fit_the_type_are_rejected(self):
        cases = [
            (make(), "nums = [1,2]"),                           # target 빠짐
            (make(), "nums = [1,2], target = 9, extra = 1"),
            (make(), "nums = [1,2], target = 2147483648"),      # int 범위 밖
            (make(), "nums = [1,\"2\"], target = 9"),
            (make(), "[1,2]"),
            (make("f", [("c", "char")], "int"), 'c = "ab"'),
            (make("f", [("s", "string")], "int"), 's = "a\\nb"'),
            (make("f", [("root", "TreeNode")], "int"), "root = [null,1]"),
            (make(), "nums = [1,2] target = 9"),
        ]
        for sig, text in cases:
            with self.subTest(text=text), self.assertRaises(sg.SignatureError):
                sg.parse_leetcode_input(sig, text)
        with self.assertRaises(sg.SignatureError):
            sg.parse_leetcode_output(make(), "[0,1] [2]")

    def test_unreadable_stored_examples_fall_back_to_raw(self):
        self.assertEqual(sg.oj_to_leetcode(make(), "not a count\n", "0 1"), (None, "[0,1]"))


class LanguageTests(unittest.TestCase):
    def test_starters_follow_leetcode(self):
        sig = make()
        expected = {
            "c": "int* twoSum(int* nums, int numsSize, int target, int* returnSize) {",
            "cpp": "vector<int> twoSum(vector<int>& nums, int target) {",
            "java": "public int[] twoSum(int[] nums, int target) {",
            "python": "def twoSum(self, nums: List[int], target: int) -> List[int]:",
            "javascript": "var twoSum = function(nums, target) {",
            "typescript": "function twoSum(nums: number[], target: number): number[] {",
            "ruby": "def two_sum(nums, target)",
            "php": "function twoSum($nums, $target) {",
            "perl": "my ($nums, $target) = @_;",
        }
        for language, line in expected.items():
            with self.subTest(language=language):
                self.assertIn(line, sg.starter(sig, language))

    def test_node_definitions_appear_only_as_comments_in_starters(self):
        sig = make("reverseList", [("head", "ListNode")], "ListNode")
        python = sg.starter(sig, "python")
        self.assertIn("# class ListNode:", python)
        self.assertNotIn("\nclass ListNode", python)
        self.assertIn("struct ListNode *next;", sg.starter(sig, "c"))
        self.assertNotIn("TreeNode", sg.starter(sig, "java"))

    def test_in_place_php_takes_a_reference(self):
        sig = make("rotate", [("matrix", "int[][]")], "void", "matrix")
        self.assertIn("function rotate(&$matrix)", sg.starter(sig, "php"))
        self.assertIn("void rotate(int** matrix, int matrixSize, int* matrixColSize)", sg.starter(sig, "c"))

    def test_compose_keeps_user_code_and_line_numbers(self):
        sig = make()
        code = "class Solution {\n    public int[] twoSum(int[] nums, int target) { return nums; }\n}\n"
        java = sg.compose(sig, "java", "public " + code)
        # import는 같은 줄에 붙고, public class Solution의 public은 떨어진다.
        self.assertTrue(java.startswith("import java.util.*;"))
        self.assertEqual(java.split("\n")[1], "    public int[] twoSum(int[] nums, int target) { return nums; }")
        self.assertNotIn("public class Solution", java)
        php = sg.compose(sig, "php", "<?php\nclass Solution {}\n?>\n")
        self.assertTrue(php.startswith("<?php \nclass Solution {}"))
        self.assertIn('#line 1 "solution.c"\nint x;\n#line 1 "driver.c"', sg.compose(sig, "c", "int x;"))

    def test_sql_cannot_be_written_as_a_function(self):
        with self.assertRaises(sg.SignatureError):
            sg.compose(make(), "sql", "SELECT 1;")
        self.assertEqual(set(sg.starters(make())), set(sg.LANGUAGES))


if __name__ == "__main__":
    unittest.main()
