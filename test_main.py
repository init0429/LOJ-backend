import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
        for method in ("select", "eq", "in_", "gte", "order", "limit", "insert", "update"):
            getattr(self.query, method).return_value = self.query
        # 따로 정하지 않으면 채점 중인 제출이 없는 것으로 본다.
        self.query.execute.return_value = SimpleNamespace(data=[])
        self.db_patch = patch.object(main, "supabase", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.admission_patch = patch.object(main, "check_admission")
        self.admission_patch.start()
        self.addCleanup(self.admission_patch.stop)
        self.redis_patch = patch.object(main, "r")
        self.redis = self.redis_patch.start()
        self.addCleanup(self.redis_patch.stop)

    def test_blank_code_and_invalid_ids_are_rejected(self):
        for values in ({"code": "  "}, {"problem_id": 0}, {"contest_id": -1}):
            with self.assertRaises(ValidationError):
                main.Submission(**(self.payload.model_dump() | values))

    def test_new_languages_are_accepted(self):
        for language in ("javascript", "typescript", "ruby", "php", "perl", "mysql"):
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
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[]), SimpleNamespace(data=[])]
        with self.assertRaises(HTTPException) as caught:
            main.validate_judging_context(self.payload)
        self.assertEqual(caught.exception.status_code, 422)

    def test_test_runs_only_require_a_public_case(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[]), SimpleNamespace(data=[{"id": 9}])]
        self.assertIsNone(main.validate_judging_context(self.payload, True))
        self.query.eq.assert_any_call("is_hidden", False)
        self.query.limit.assert_called_once_with(1)

    def test_problems_of_upcoming_contests_cannot_be_run_outside_them(self):
        upcoming = {"contests": {"start_time": "2999-01-01T00:00:00Z"}}
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[upcoming])]
        with self.assertRaises(HTTPException) as caught:
            main.validate_judging_context(self.payload, True)
        self.assertEqual(caught.exception.status_code, 404)

    def test_problems_of_started_contests_are_open_for_practice(self):
        started = {"contests": {"start_time": "2000-01-01T00:00:00Z"}}
        self.query.execute.side_effect = [SimpleNamespace(data=[{"id": 1}]), SimpleNamespace(data=[started, {"contests": None}]), SimpleNamespace(data=[{"id": 9}])]
        self.assertIsNone(main.validate_judging_context(self.payload, True))

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

    def test_pending_submission_blocks_the_next_one(self):
        self.query.execute.return_value = SimpleNamespace(data=[{"id": "earlier"}])
        with patch.object(main, "validate_judging_context") as validate, patch.object(main, "enqueue") as enqueue:
            with self.assertRaises(HTTPException) as caught:
                main.submit_code(self.payload, "user-1")
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("Retry-After", caught.exception.headers)
        validate.assert_not_called()
        self.query.insert.assert_not_called()
        enqueue.assert_not_called()
        # 잠금은 거절할 때도 풀어 둔다.
        self.redis.delete.assert_called_once()

    def test_pending_check_ignores_finished_and_stale_submissions(self):
        before = datetime.now(timezone.utc)
        main.check_no_pending_submission("user-1")
        self.query.eq.assert_any_call("user_id", "user-1")
        self.query.in_.assert_called_once_with("status", ["Pending", "Running"])
        column, since = self.query.gte.call_args.args
        self.assertEqual(column, "created_at")
        self.assertAlmostEqual(
            (before - datetime.fromisoformat(since)).total_seconds(),
            main.PENDING_SUBMISSION_WINDOW.total_seconds(), delta=5,
        )

    def test_pending_check_failure_is_not_a_free_pass(self):
        self.query.execute.side_effect = RuntimeError("db down")
        with self.assertRaises(HTTPException) as caught:
            main.check_no_pending_submission("user-1")
        self.assertEqual(caught.exception.status_code, 503)

    def test_concurrent_submission_is_rejected_while_one_is_being_recorded(self):
        self.redis.set.return_value = None  # 다른 요청이 잠금을 쥐고 있다.
        with patch.object(main, "enqueue") as enqueue, self.assertRaises(HTTPException) as caught:
            main.submit_code(self.payload, "user-1")
        self.assertEqual(caught.exception.status_code, 429)
        self.db.table.assert_not_called()
        enqueue.assert_not_called()
        # 남이 쥔 잠금은 건드리지 않는다.
        self.redis.delete.assert_not_called()

    def test_submission_lock_is_per_user_and_released(self):
        with patch.object(main, "validate_judging_context"), patch.object(main, "enqueue"):
            main.submit_code(self.payload, "user-1")
        key = self.redis.set.call_args.args[0]
        self.assertTrue(key.startswith("judge:submitting:"))
        self.assertNotIn("user-1", key)
        self.assertEqual(self.redis.set.call_args.kwargs, {"nx": True, "ex": main.SUBMIT_LOCK_TTL})
        self.redis.delete.assert_called_once_with(key)

    def test_queue_failure_marks_submission_failed(self):
        with patch.object(main, "validate_judging_context"), patch.object(main, "r") as queue:
            queue.eval.side_effect = main.redis.ConnectionError("unavailable")
            with self.assertRaises(HTTPException) as caught:
                main.submit_code(self.payload, "user-1")
        self.assertEqual(caught.exception.status_code, 503)
        self.query.update.assert_called_once_with({"status": "System Error"})



class ConfigurationTests(unittest.TestCase):
    def reload_main(self, **env):
        import importlib
        with patch.dict(os.environ, env), patch("supabase.create_client"):
            return importlib.reload(main)

    def tearDown(self):
        self.reload_main()

    def test_missing_supabase_settings_stop_the_api_at_startup(self):
        for missing in ("SUPABASE_URL", "SUPABASE_KEY"):
            with self.subTest(missing=missing), self.assertRaisesRegex(RuntimeError, missing):
                self.reload_main(**{missing: ""})

    def test_configured_origins_replace_the_development_defaults(self):
        self.assertEqual(self.reload_main(ALLOWED_ORIGINS="").allowed_origins,
                         ["http://localhost:3000", "http://127.0.0.1:3000"])
        self.assertEqual(self.reload_main(ALLOWED_ORIGINS="https://a.example, https://b.example").allowed_origins,
                         ["https://a.example", "https://b.example"])


class AuthenticationTests(unittest.TestCase):
    def authenticate(self, email, domains=frozenset()):
        user = SimpleNamespace(id="user-1", email=email)
        with patch.object(main, "supabase") as db, patch.object(main, "ALLOWED_EMAIL_DOMAINS", domains):
            db.auth.get_user.return_value = SimpleNamespace(user=user)
            return main.get_current_user_id("Bearer token")

    def test_any_account_is_accepted_without_a_domain_list(self):
        self.assertEqual(self.authenticate("someone@gmail.com"), "user-1")

    def test_only_listed_domains_may_request_judging(self):
        domains = {"bssm.hs.kr"}
        self.assertEqual(self.authenticate("Student@BSSM.hs.kr", domains), "user-1")
        for email in ("someone@gmail.com", "x@evil-bssm.hs.kr", "bssm.hs.kr@gmail.com", None):
            with self.subTest(email=email), self.assertRaises(HTTPException) as caught:
                self.authenticate(email, domains)
            self.assertEqual(caught.exception.status_code, 403)

    def test_missing_or_rejected_tokens_are_unauthorized(self):
        with self.assertRaises(HTTPException) as caught:
            main.get_current_user_id(None)
        self.assertEqual(caught.exception.status_code, 401)
        with patch.object(main, "supabase") as db:
            db.auth.get_user.side_effect = RuntimeError("expired")
            with self.assertRaises(HTTPException) as caught:
                main.get_current_user_id("Bearer token")
        self.assertEqual(caught.exception.status_code, 401)


def fake_pubsub(messages=()):
    """redis.asyncio의 PubSub처럼 async with로 쓰이고 get_message가 차례로 값을 돌려주는 대역."""
    pubsub = MagicMock()
    pubsub.__aenter__.return_value = pubsub
    pubsub.subscribe = AsyncMock()
    pubsub.get_message = AsyncMock(side_effect=list(messages) or None, return_value=None)
    return pubsub


class TestRunTests(unittest.TestCase):
    def setUp(self):
        self.payload = main.Submission(problem_id=1, language="python", code="print(1)")
        for name in ("check_admission", "validate_judging_context"):
            patcher = patch.object(main, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.async_r = MagicMock()
        self.async_r.set = AsyncMock(return_value=True)
        self.async_r.delete = AsyncMock()

    def run_test(self, pubsub, **enqueue):
        # pubsub()은 동기 메서드라 MagicMock을 직접 넘긴다 (patch가 고르는 AsyncMock이면 코루틴이 된다).
        async_r = self.async_r
        async_r.pubsub.return_value = pubsub
        with patch.object(main, "async_r", async_r), patch.object(main, "enqueue", **enqueue) as queued:
            return asyncio.run(main.test_code(self.payload, "user-1")), queued

    def test_result_is_returned_once_published(self):
        result = {"status": "Accepted", "test_cases": []}
        pubsub = fake_pubsub([None, {"data": json.dumps(result)}])
        response, queued = self.run_test(pubsub)
        self.assertEqual(response, {"status": "success", "result": result})
        task = queued.call_args.args[0]
        self.assertTrue(task["is_test"])
        self.assertNotIn("test_cases", task)
        # 구독이 먼저여야 빨리 끝난 채점 결과를 놓치지 않는다.
        pubsub.subscribe.assert_awaited_once_with(f"test_result:{task['submission_id']}")
        pubsub.__aexit__.assert_awaited_once()

    def test_pubsub_is_closed_on_queue_failure(self):
        pubsub = fake_pubsub()
        with self.assertRaises(HTTPException) as caught:
            self.run_test(pubsub, side_effect=main.redis.ConnectionError("unavailable"))
        self.assertEqual(caught.exception.status_code, 503)
        pubsub.__aexit__.assert_awaited_once()

    def test_malformed_result_is_a_bad_gateway(self):
        for data in ("not json", json.dumps(["Accepted"]), json.dumps({"status": 1})):
            with self.subTest(data=data), self.assertRaises(HTTPException) as caught:
                self.run_test(fake_pubsub([{"data": data}]))
            self.assertEqual(caught.exception.status_code, 502)

    def test_only_one_example_run_at_a_time(self):
        self.async_r.set.return_value = None  # 같은 사람의 예제 실행이 이미 진행 중이다.
        with self.assertRaises(HTTPException) as caught:
            self.run_test(fake_pubsub())
        self.assertEqual(caught.exception.status_code, 429)
        self.async_r.pubsub.assert_not_called()
        self.async_r.delete.assert_not_awaited()

    def test_example_run_slot_is_released_after_result_and_failure(self):
        self.run_test(fake_pubsub([{"data": json.dumps({"status": "Accepted"})}]))
        key = self.async_r.set.await_args.args[0]
        self.assertTrue(key.startswith("judge:testing:"))
        self.assertNotIn("user-1", key)
        self.assertEqual(self.async_r.set.await_args.kwargs, {"nx": True, "ex": main.TEST_RUN_TIMEOUT + 5})
        self.async_r.delete.assert_awaited_once_with(key)

        self.async_r.delete.reset_mock()
        with self.assertRaises(HTTPException):
            self.run_test(fake_pubsub(), side_effect=main.redis.ConnectionError("unavailable"))
        self.async_r.delete.assert_awaited_once_with(key)

    def test_silent_judge_times_out(self):
        with patch.object(main, "TEST_RUN_TIMEOUT", 0.05), self.assertRaises(HTTPException) as caught:
            self.run_test(fake_pubsub())
        self.assertEqual(caught.exception.status_code, 504)


if __name__ == "__main__":
    unittest.main()
