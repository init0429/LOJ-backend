"""함수형 문제의 API(function_problems.py). 가짜 Supabase로 돌린다."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

import function_problems as fp
import signature

TWO_SUM = {
    "function": "twoSum",
    "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
    "returns": "int[]",
    "output_param": None,
}
AVERAGE = {"function": "average", "params": [{"name": "nums", "type": "int[]"}], "returns": "double"}


class FakeQuery:
    """select/eq/order를 이어 부르고 execute()로 미리 정한 행을 돌려주는 대역. 건 조건을 기록한다."""

    def __init__(self, rows, error=None):
        self.rows, self.error, self.filters = rows, error, []

    def select(self, *_):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *_):
        return self

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.rows)


def fake_supabase(*, problem=None, cases=(), admins=("admin",), error=None):
    tables = {
        "problems": FakeQuery([] if problem is None else [problem], error),
        "test_cases": FakeQuery(list(cases)),
    }

    def table(name):
        if name == "profiles":
            query = MagicMock()
            query.select.return_value.eq.side_effect = lambda _c, user_id: SimpleNamespace(
                execute=lambda: SimpleNamespace(data=[{"is_admin": user_id in admins}])
            )
            return query
        return tables[name]

    db = MagicMock()
    db.table.side_effect = table
    return db, tables


def make_client(db, released=lambda _id, _contest_only: True):
    def current_user(authorization: str = Header(None)) -> str:
        if not authorization:
            raise HTTPException(401, "로그인이 필요합니다.")
        return authorization.removeprefix("Bearer ")

    app = FastAPI()
    app.include_router(fp.create_router(supabase=db, current_user=current_user, released=released))
    return TestClient(app)


ADMIN = {"Authorization": "Bearer admin"}
USER = {"Authorization": "Bearer someone"}


class JudgedCodeTests(unittest.TestCase):
    def test_program_mode_sends_the_code_as_is(self):
        self.assertEqual(fp.judged_code("program", "python", "print(1)", TWO_SUM), (None, "print(1)"))
        self.assertEqual(fp.judged_code("program", "sql", "select 1", None), (None, "select 1"))

    def test_function_mode_wraps_the_code(self):
        sig, code = fp.judged_code("function", "python", "class Solution: pass", TWO_SUM)
        self.assertEqual(sig.function, "twoSum")
        self.assertIn("class Solution: pass", code)
        self.assertNotEqual(code, "class Solution: pass")

    def test_function_mode_needs_a_valid_signature_and_language(self):
        cases = [
            (None, "python", fp.NOT_SUPPORTED),
            ({"function": "class", "params": [], "returns": "int"}, "python", fp.BROKEN),
            (TWO_SUM, "sql", "함수 구현으로 낼 수 없습니다"),
            (TWO_SUM, "mysql", "함수 구현으로 낼 수 없습니다"),
        ]
        for raw, language, message in cases:
            with self.subTest(language=language, raw=raw), self.assertRaises(HTTPException) as caught:
                fp.judged_code("function", language, "code", raw)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertIn(message, caught.exception.detail)


class DisplayTests(unittest.TestCase):
    def test_answers_are_shown_as_arguments(self):
        sig = signature.parse_signature(TWO_SUM)
        result = {"status": "Wrong Answer", "test_cases": [
            {"id": 1, "status": "Wrong Answer", "input": "4\n2 7 11 15\n9\n", "expected": "0 1", "actual": "1 0"},
            {"id": 2, "status": "Runtime Error", "input": "1\n5\n5\n", "expected": "0", "actual": "Traceback ..."},
            {"id": 3, "status": "Accepted", "input": "4\n2 7 … (이하 생략)", "expected": "0 1", "actual": "0 1"},
            "not a case",
        ]}
        cases = fp.add_display(sig, result)["test_cases"]
        self.assertEqual(cases[0]["display"], {
            "input": "nums = [2,7,11,15], target = 9", "expected": "[0,1]", "actual": "[1,0]",
        })
        # 런타임 에러의 출력은 오류 메시지라 원문 그대로 보여 준다.
        self.assertEqual(cases[1]["display"], {"input": "nums = [5], target = 5", "expected": "[0]", "actual": None})
        # 잘린 입력은 읽을 수 없으니 원문을 보여 준다.
        self.assertIsNone(cases[2]["display"]["input"])
        self.assertEqual(cases[2]["input"], "4\n2 7 … (이하 생략)")

    def test_compile_errors_have_no_cases(self):
        sig = signature.parse_signature(TWO_SUM)
        result = {"status": "Compile Error", "message": "x", "test_cases": []}
        self.assertEqual(fp.add_display(sig, dict(result)), result)
        self.assertEqual(fp.add_display(sig, {"status": "System Error"}), {"status": "System Error"})


class CheckTestTests(unittest.TestCase):
    def test_matching_test_is_ok(self):
        sig = signature.parse_signature(TWO_SUM)
        self.assertEqual(fp.check_test(sig, "4\n2 7 11 15\n9\n", "0 1\n"), {
            "input": "nums = [2,7,11,15], target = 9", "output": "[0,1]", "error": None, "normalized_output": None,
        })

    def test_input_that_does_not_fit_the_signature(self):
        sig = signature.parse_signature(TWO_SUM)
        self.assertIn("입력:", fp.check_test(sig, "4\n2 7\n", "0 1")["error"])
        self.assertIn("입력이 남았습니다", fp.check_test(sig, "1\n2\n9\n10\n", "0")["error"])

    def test_answer_printed_differently_by_the_driver(self):
        sig = signature.parse_signature(AVERAGE)
        report = fp.check_test(sig, "2\n1 2\n", "1.5")
        self.assertIn("출력:", report["error"])
        self.assertEqual(report["normalized_output"], "1.50000")
        self.assertIsNone(fp.check_test(sig, "2\n1 2\n", "1.50000\n")["error"])
        self.assertIn("출력:", fp.check_test(sig, "2\n1 2\n", "abc")["error"])


class TemplatesEndpointTests(unittest.TestCase):
    def test_signature_starters_and_examples(self):
        db, tables = fake_supabase(problem={"signature": TWO_SUM}, cases=[
            {"id": 3, "input_data": "4\n2 7 11 15\n9\n", "expected_output": "0 1\n"},
            {"id": 5, "input_data": "garbage", "expected_output": "0 1"},
        ])
        response = make_client(db).get("/problems/7/templates")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["signature"], TWO_SUM)
        self.assertEqual(set(body["starters"]), set(signature.LANGUAGES))
        self.assertIn("twoSum", body["starters"]["python"])
        self.assertEqual(body["examples"], [
            {"id": 3, "input": "nums = [2,7,11,15], target = 9", "output": "[0,1]"},
            {"id": 5, "input": None, "output": "[0,1]"},
        ])
        # 숨겨진 테스트는 읽지 않는다.
        self.assertIn(("is_hidden", False), tables["test_cases"].filters)
        self.assertIn(("problem_id", 7), tables["test_cases"].filters)

    def test_problem_without_or_with_a_broken_signature(self):
        for raw in (None, {"function": "x y"}):
            with self.subTest(raw=raw):
                db, _ = fake_supabase(problem={"signature": raw})
                response = make_client(db).get("/problems/7/templates")
                self.assertEqual(response.json(), {"signature": None, "starters": {}, "examples": []})

    def test_missing_or_unreleased_problem_is_not_found(self):
        db, _ = fake_supabase(problem=None)
        self.assertEqual(make_client(db).get("/problems/7/templates").status_code, 404)
        db, tables = fake_supabase(problem={"signature": TWO_SUM})
        self.assertEqual(make_client(db, released=lambda _id, _only: False).get("/problems/7/templates").status_code, 404)
        self.assertEqual(tables["test_cases"].filters, [])

    def test_contest_only_flag_is_passed_to_the_release_check(self):
        seen = []
        db, _ = fake_supabase(problem={"signature": TWO_SUM, "contest_only": True})
        make_client(db, released=lambda problem_id, only: seen.append((problem_id, only)) or True).get("/problems/7/templates")
        db, _ = fake_supabase(problem={"signature": TWO_SUM})
        make_client(db, released=lambda problem_id, only: seen.append((problem_id, only)) or True).get("/problems/8/templates")
        self.assertEqual(seen, [(7, True), (8, False)])

    def test_database_failure_is_unavailable(self):
        db, _ = fake_supabase(problem={"signature": TWO_SUM}, error=RuntimeError("down"))
        self.assertEqual(make_client(db).get("/problems/7/templates").status_code, 503)
        self.assertEqual(make_client(db).get("/problems/0/templates").status_code, 422)


class AdminEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = make_client(fake_supabase()[0])

    def test_only_admins_may_use_the_tools(self):
        for path in ("/signature/preview", "/signature/convert"):
            body = {"signature": TWO_SUM, "input": "[1]"}
            self.assertEqual(self.client.post(path, json=body).status_code, 401)
            self.assertEqual(self.client.post(path, json=body, headers=USER).status_code, 403)

    def test_preview_normalizes_and_checks_tests(self):
        loose = {"function": "twoSum", "params": [{"name": "nums", "type": "int [ ]"}, {"name": "target", "type": "int"}],
                 "returns": "int[]", "output_param": "ignored"}
        response = self.client.post("/signature/preview", headers=ADMIN, json={
            "signature": loose, "tests": [{"input": "4\n2 7 11 15\n9", "output": "0 1"}, {"input": "x", "output": ""}],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["signature"], TWO_SUM)
        self.assertIn("vector<int>& nums", body["starters"]["cpp"])
        self.assertIsNone(body["tests"][0]["error"])
        self.assertIn("입력:", body["tests"][1]["error"])

    def test_invalid_signature_explains_why(self):
        response = self.client.post("/signature/preview", headers=ADMIN, json={
            "signature": {"function": "f", "params": [{"name": "a", "type": "map<int>"}], "returns": "int"},
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn("알 수 없는 자료형", response.json()["detail"])
        huge = dict(TWO_SUM, note="x" * fp.MAX_SIGNATURE_BYTES)
        self.assertEqual(self.client.post("/signature/preview", headers=ADMIN, json={"signature": huge}).status_code, 422)

    def test_convert_example_notation_to_test_text(self):
        response = self.client.post("/signature/convert", headers=ADMIN, json={
            "signature": TWO_SUM, "input": "nums = [2,7,11,15], target = 9", "output": "[0,1]",
        })
        self.assertEqual(response.json(), {"input": "4\n2 7 11 15\n9\n", "output": "0 1"})
        only_input = self.client.post("/signature/convert", headers=ADMIN, json={
            "signature": TWO_SUM, "input": "[3,3]\n6", "output": "  ",
        })
        self.assertEqual(only_input.json(), {"input": "2\n3 3\n6\n", "output": None})

    def test_convert_explains_bad_examples(self):
        response = self.client.post("/signature/convert", headers=ADMIN, json={
            "signature": TWO_SUM, "input": "nums = [2,7], k = 9",
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn("인자 이름이 맞지 않습니다", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
