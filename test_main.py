import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

with patch("supabase.create_client"):
    import main

from fastapi import HTTPException
from pydantic import ValidationError


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.payload = main.Submission(problem_id=1, language="python", code="print(1)")
        self.db = MagicMock()
        self.query = self.db.table.return_value
        for method in ("select", "eq", "order", "insert", "update"):
            getattr(self.query, method).return_value = self.query
        self.db_patch = patch.object(main, "supabase", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

    def test_blank_code_and_invalid_ids_are_rejected(self):
        for values in ({"code": "  "}, {"problem_id": 0}, {"contest_id": -1}):
            with self.assertRaises(ValidationError):
                main.Submission(**(self.payload.model_dump() | values))

    def test_missing_problem_is_not_judged(self):
        self.query.execute.return_value = SimpleNamespace(data=[])
        with self.assertRaises(HTTPException) as caught:
            main.load_test_cases(self.payload)
        self.assertEqual(caught.exception.status_code, 404)

    def test_empty_cases_have_no_dummy_fallback(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[])]
        with self.assertRaises(HTTPException) as caught:
            main.load_test_cases(self.payload)
        self.assertEqual(caught.exception.status_code, 422)

    def test_public_cases_preserve_exact_input(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[{"input_data": "", "expected_output": "1"}])]
        self.assertEqual(main.load_test_cases(self.payload, True), [{"input": "", "output": "1"}])
        self.query.eq.assert_any_call("is_hidden", False)

    def test_ended_contest_rejects_submission(self):
        payload = self.payload.model_copy(update={"contest_id": 1})
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[{"start_time": "2000-01-01T00:00:00Z", "end_time": "2000-01-02T00:00:00Z"}])]
        with self.assertRaises(HTTPException) as caught:
            main.load_test_cases(payload)
        self.assertEqual(caught.exception.status_code, 409)

    def test_unrelated_problem_rejected_for_contest(self):
        payload = self.payload.model_copy(update={"contest_id": 1})
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[{"start_time": "2000-01-01T00:00:00Z", "end_time": "2999-01-01T00:00:00Z"}]), SimpleNamespace(data=[])]
        with self.assertRaises(HTTPException) as caught:
            main.load_test_cases(payload)
        self.assertEqual(caught.exception.status_code, 400)

    def test_queue_failure_marks_submission_failed(self):
        with patch.object(main, "load_test_cases", return_value=[]), patch.object(main, "r") as queue:
            queue.rpush.side_effect = main.redis.ConnectionError("unavailable")
            with self.assertRaises(HTTPException) as caught:
                main.submit_code(self.payload, "user-1")
        self.assertEqual(caught.exception.status_code, 503)
        self.query.update.assert_called_once_with({"status": "System Error"})

    def test_pubsub_is_closed_on_queue_failure(self):
        with patch.object(main, "load_test_cases", return_value=[]), patch.object(main, "r") as queue:
            queue.rpush.side_effect = main.redis.ConnectionError("unavailable")
            with self.assertRaises(HTTPException):
                main.test_code(self.payload, "user-1")
            queue.pubsub.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
