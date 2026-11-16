"""Dependency-free admission unit tests; Redis Lua needs integration testing."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class HTTPError(Exception):
    def __init__(self, status, detail):
        self.status_code = status


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path(__file__).with_name("main.py").read_text())
        selected = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {"check_admission", "enqueue"}:
                selected.append(node)
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in {"RATE_SCRIPT", "ENQUEUE_SCRIPT"}
                for t in node.targets
            ):
                selected.append(node)
        self.queue = Mock()
        self.ns = {"r": self.queue, "json": json, "hashlib": hashlib,
                   "HTTPException": HTTPError,
                   "redis": SimpleNamespace(RedisError=ConnectionError)}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "main.py", "exec"), self.ns)

    def test_rate_limit_rejects_before_queue_check(self):
        self.queue.eval.return_value = 11
        with self.assertRaises(HTTPError) as error:
            self.ns["check_admission"]("user")
        self.assertEqual(error.exception.status_code, 429)
        self.queue.llen.assert_not_called()

    def test_queue_full_rejects(self):
        self.queue.eval.return_value = 1
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
