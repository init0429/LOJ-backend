"""solved.ac 태그 시드와 관리자용 문제 정보 API. 가짜 solved.ac와 가짜 Supabase로 돌린다."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

import solvedac_client as sc
import solvedac_tags as st

TAGS = {"tags": [
    {"id": 96, "key": "2sat", "isMeta": False, "parents": ["graph", "scc"],
     "names": {"ko": "2-SAT", "en": "2-sat", "ja": "2-SAT"}, "aliases": ["투 셋"], "rated": False},
    {"id": 124, "key": "math", "isMeta": False, "parents": [],
     "names": {"en": "mathematics"}, "aliases": [], "rated": True},
]}

PROBLEM_1000 = {
    "level": 1,
    "titles": [
        {"language": "en", "title": "A+B", "isOriginal": True},
        {"language": "ko", "title": "에이 더하기 비", "isOriginal": False},
    ],
    "tags": [{"id": 124, "key": "math", "isMeta": False}, {"id": 1, "key": "meta", "isMeta": True}],
}


class FakeSolvedac:
    configured = True

    def __init__(self):
        self.problem_error = None

    async def tags(self):
        return TAGS

    async def problem(self, problem_id):
        if self.problem_error:
            raise self.problem_error
        return PROBLEM_1000


def fake_supabase(admin_ids=("admin",)):
    db = MagicMock()
    upserts = []

    def table(name):
        query = MagicMock()
        if name == "profiles":
            def eq(_column, user_id):
                return SimpleNamespace(execute=lambda: SimpleNamespace(data=[{"is_admin": user_id in admin_ids}]))
            query.select.return_value.eq.side_effect = eq
        if name == "tags":
            def upsert(rows, on_conflict=None):
                upserts.append((list(rows), on_conflict))
                return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))
            query.upsert.side_effect = upsert
        return query

    db.table.side_effect = table
    return db, upserts


def make_app(client, supabase):
    def current_user(authorization: str = Header(None)) -> str:
        if not authorization:
            raise HTTPException(401, "로그인이 필요합니다.")
        return authorization.removeprefix("Bearer ")

    app = FastAPI()
    app.include_router(st.create_router(client=client, supabase=supabase, current_user=current_user))
    return TestClient(app)


class TagRowTests(unittest.TestCase):
    def test_tags_keep_every_parent_and_a_korean_name(self):
        rows = st.tag_rows(TAGS)
        first = {k: v for k, v in rows[0].items() if k != "updated_at"}
        self.assertEqual(first, {
            "key": "2sat", "solvedac_id": 96, "parent_keys": ["graph", "scc"], "name_ko": "2-SAT",
            "name_en": "2-sat", "aliases": ["투 셋"], "is_meta": False, "rated": False,
        })
        # 한국어 이름이 없으면 영어 이름을 쓴다.
        self.assertEqual(rows[1]["name_ko"], "mathematics")

    def test_seed_upserts_by_key(self):
        db, upserts = fake_supabase()
        self.assertEqual(asyncio.run(st.seed_tags(FakeSolvedac(), db)), 2)
        self.assertEqual(len(upserts), 1)
        self.assertEqual([row["key"] for row in upserts[0][0]], ["2sat", "math"])
        self.assertEqual(upserts[0][1], "key")

    def test_problem_summary_prefers_the_korean_title_and_drops_meta_tags(self):
        self.assertEqual(st.problem_summary(1000, PROBLEM_1000), {
            "boj_problem_id": 1000, "title": "에이 더하기 비", "tier": 1, "tags": ["math"],
        })
        unrated = {"level": 0, "titles": [{"language": "en", "title": "X", "isOriginal": True}], "tags": []}
        self.assertEqual(st.problem_summary(5, unrated)["tier"], 0)
        self.assertEqual(st.problem_summary(5, unrated)["title"], "X")


class AdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.solvedac = FakeSolvedac()
        self.db, self.upserts = fake_supabase()
        self.http = make_app(self.solvedac, self.db)
        self.admin = {"Authorization": "Bearer admin"}

    def test_only_admins_may_look_up_problems(self):
        self.assertEqual(self.http.get("/integrations/solvedac/problems/1000").status_code, 401)
        response = self.http.get("/integrations/solvedac/problems/1000", headers={"Authorization": "Bearer student"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.http.post("/integrations/solvedac/tags/sync", headers={"Authorization": "Bearer student"}).status_code, 403)

    def test_admin_gets_tier_and_tags(self):
        response = self.http.get("/integrations/solvedac/problems/1000", headers=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"boj_problem_id": 1000, "title": "에이 더하기 비", "tier": 1, "tags": ["math"]})

    def test_bad_numbers_never_reach_solved_ac(self):
        for path in ("0", "-1", "abc", "10000000"):
            self.assertEqual(self.http.get(f"/integrations/solvedac/problems/{path}", headers=self.admin).status_code, 422, path)

    def test_solved_ac_failures_are_explained(self):
        self.solvedac.problem_error = sc.SolvedacError(404, "NOT_FOUND")
        response = self.http.get("/integrations/solvedac/problems/99999", headers=self.admin)
        self.assertEqual((response.status_code, response.json()["detail"]), (404, "solved.ac에 99999번 문제가 없습니다."))
        self.solvedac.problem_error = sc.SolvedacRateLimited(30)
        response = self.http.get("/integrations/solvedac/problems/1000", headers=self.admin)
        self.assertEqual((response.status_code, response.headers.get("retry-after")), (429, "30"))
        self.solvedac.problem_error = sc.SolvedacUnavailable(None, "NETWORK")
        self.assertEqual(self.http.get("/integrations/solvedac/problems/1000", headers=self.admin).status_code, 502)

    def test_unconfigured_app_is_reported(self):
        self.solvedac.configured = False
        self.assertEqual(self.http.get("/integrations/solvedac/problems/1000", headers=self.admin).status_code, 503)

    def test_sync_saves_every_tag(self):
        response = self.http.post("/integrations/solvedac/tags/sync", headers=self.admin)
        self.assertEqual((response.status_code, response.json()), (200, {"count": 2}))
        self.assertEqual(len(self.upserts), 1)


if __name__ == "__main__":
    unittest.main()
