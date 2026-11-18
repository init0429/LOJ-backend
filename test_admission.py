"""Dependency-free admission unit tests; Redis Lua needs integration testing."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class HTTPError(Exception):
    def __init__(self, status, detail, headers=None):
        self.status_code = status
        self.detail = detail
        self.headers = headers or {}


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path(__file__).with_name("main.py").read_text())
        selected = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {"check_admission", "enqueue", "user_key", "busy"}:
                selected.append(node)
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in {"RATE_SCRIPT", "ENQUEUE_SCRIPT", "RATE_LIMIT", "RATE_WINDOW",
                                                     "QUEUE_FULL", "JUDGE_UNAVAILABLE"}
                for t in node.targets
            ):
                selected.append(node)
        self.queue = Mock()
        self.ns = {"r": self.queue, "json": json, "hashlib": hashlib,
                   "HTTPException": HTTPError,
                   "redis": SimpleNamespace(RedisError=ConnectionError)}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "main.py", "exec"), self.ns)

    def test_rate_limit_rejects_before_queue_check(self):
        self.queue.eval.return_value = [11, 42]
        with self.assertRaises(HTTPError) as error:
            self.ns["check_admission"]("user")
        self.assertEqual(error.exception.status_code, 429)
        self.queue.llen.assert_not_called()

    def test_rate_limit_says_how_long_to_wait(self):
        self.queue.eval.return_value = [11, 42]
        with self.assertRaises(HTTPError) as error:
            self.ns["check_admission"]("user")
        self.assertIn("42초", error.exception.detail)
        self.assertEqual(error.exception.headers, {"Retry-After": "42"})
        # 사용자 id는 해시로만 키에 남는다.
        key = self.queue.eval.call_args.args[2]
        self.assertEqual(key, "judge:rate:" + hashlib.sha256(b"user").hexdigest())
        self.assertEqual(self.queue.eval.call_args.args[3], 60)

    def test_requests_within_the_limit_pass(self):
        self.queue.eval.return_value = [10, 5]
        self.queue.llen.return_value = 3
        self.assertIsNone(self.ns["check_admission"]("user"))

    def test_queue_full_rejects(self):
        self.queue.eval.return_value = [1, 60]
        self.queue.llen.return_value = 100
        with self.assertRaises(HTTPError) as error:
            self.ns["check_admission"]("user")
        self.assertEqual(error.exception.status_code, 503)

    def test_atomic_enqueue_rejection(self):
        self.queue.eval.return_value = 0
        with self.assertRaises(HTTPError):
            self.ns["enqueue"]({"code": "print(1)"})

    def test_oversized_payload_never_reaches_redis(self):
        with self.assertRaises(HTTPError) as error:
            self.ns["enqueue"]({"code": "x" * 1048576})
        self.assertEqual(error.exception.status_code, 422)
        self.queue.eval.assert_not_called()

    def test_redis_outage_fails_closed(self):
        self.queue.eval.side_effect = ConnectionError("offline")
        with self.assertRaises(HTTPError) as error:
            self.ns["check_admission"]("user")
        self.assertEqual(error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
