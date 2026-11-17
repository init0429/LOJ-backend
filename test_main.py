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
        for method in ("select", "eq", "order", "limit", "insert", "update"):
            getattr(self.query, method).return_value = self.query
        self.db_patch = patch.object(main, "supabase", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.admission_patch = patch.object(main, "check_admission")
        self.admission_patch.start()
        self.addCleanup(self.admission_patch.stop)

    def test_blank_code_and_invalid_ids_are_rejected(self):
        for values in ({"code": "  "}, {"problem_id": 0}, {"contest_id": -1}):
            with self.assertRaises(ValidationError):
                main.Submission(**(self.payload.model_dump() | values))

    def test_new_languages_are_accepted(self):
        for language in ("javascript", "typescript", "ruby", "php", "perl"):
            with self.subTest(language=language):
                self.assertEqual(main.Submission(problem_id=1, language=language, code="code").language, language)
        with self.assertRaises(ValidationError):
            main.Submission(problem_id=1, language="unknown", code="code")

    def test_missing_problem_is_not_judged(self):
        self.query.execute.return_value = SimpleNamespace(data=[])
        with self.assertRaises(HTTPException) as caught:
            main.validate_judging_context(self.payload)
        self.assertEqual(caught.exception.status_code, 404)

    def test_empty_cases_have_no_dummy_fallback(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[])]
        with self.assertRaises(HTTPException) as caught:
            main.validate_judging_context(self.payload)
        self.assertEqual(caught.exception.status_code, 422)

    def test_test_runs_only_require_a_public_case(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[{"id": 9}])]
        self.assertIsNone(main.validate_judging_context(self.payload, True))
        self.query.eq.assert_any_call("is_hidden", False)
        self.query.limit.assert_called_once_with(1)

    def test_queued_job_carries_no_test_data(self):
        with patch.object(main, "validate_judging_context"), patch.object(main, "enqueue") as enqueue:
            main.submit_code(self.payload, "user-1")
        task = enqueue.call_args.args[0]
        self.assertEqual(set(task), {"submission_id", "problem_id", "language", "code"})

    def test_ended_contest_rejects_submission(self):
        payload = self.payload.model_copy(update={"contest_id": 1})
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[{"start_time": "2000-01-01T00:00:00Z", "end_time": "2000-01-02T00:00:00Z"}])]
        with self.assertRaises(HTTPException) as caught:
            main.validate_judging_context(payload)
        self.assertEqual(caught.exception.status_code, 409)

    def test_unrelated_problem_rejected_for_contest(self):
        payload = self.payload.model_copy(update={"contest_id": 1})
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[{"start_time": "2000-01-01T00:00:00Z", "end_time": "2999-01-01T00:00:00Z"}]), SimpleNamespace(data=[])]
        with self.assertRaises(HTTPException) as caught:
            main.validate_judging_context(payload)
        self.assertEqual(caught.exception.status_code, 400)

    def test_queue_failure_marks_submission_failed(self):
        with patch.object(main, "validate_judging_context"), patch.object(main, "r") as queue:
            queue.eval.side_effect = main.redis.ConnectionError("unavailable")
            with self.assertRaises(HTTPException) as caught:
                main.submit_code(self.payload, "user-1")
        self.assertEqual(caught.exception.status_code, 503)
        self.query.update.assert_called_once_with({"status": "System Error"})

    def test_pubsub_is_closed_on_queue_failure(self):
        with patch.object(main, "validate_judging_context"), patch.object(main, "r") as queue:
            queue.eval.side_effect = main.redis.ConnectionError("unavailable")
            with self.assertRaises(HTTPException):
                main.test_code(self.payload, "user-1")
            queue.pubsub.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
