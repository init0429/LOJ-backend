"""문제 추천. 점수 규칙은 순수 함수로, 전체 흐름은 가짜 DB·solved.ac·Redis로 확인한다."""
import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import solvedac_client as sc
import recommend as rc

TC = rc.TagCount


class ScoringTests(unittest.TestCase):
    def test_newcomers_start_with_the_basic_core_tags(self):
        weak = rc.weak_tags({}, None)
        self.assertEqual(len(weak), 3)
        self.assertTrue(set(weak) <= rc.core_tags(0))
        self.assertNotIn("dp", rc.core_tags(5))
        self.assertIn("dp", rc.core_tags(6))
        self.assertIn("segtree", rc.core_tags(31))

    def test_struggling_tags_outrank_untouched_and_mastered_ones(self):
        stats = {
            "dp": TC(solved=1, tried=6),        # 자주 막힌다
            "impl": TC(solved=40, tried=1),     # 잘한다
            "geometry": TC(solved=0, tried=3),  # 핵심이 아니어도 계속 막히면 약점
        }
        weak = rc.weak_tags(stats, 8)
        self.assertEqual(weak[:2], ["dp", "geometry"])
        self.assertNotIn("impl", rc.weak_tags(stats, 8, limit=20)[:10])
        # 한 번도 건드리지 않은 비핵심 태그는 약점으로 치지 않는다.
        self.assertNotIn("fft", rc.weak_tags({"fft": TC()}, 8, limit=50))

    def test_one_bad_attempt_does_not_dominate(self):
        self.assertLess(rc.weakness(TC(0, 1), core=False), rc.weakness(TC(0, 6), core=False))
        self.assertGreater(rc.weakness(TC(0, 0), core=True), rc.weakness(TC(10, 0), core=True))

    def test_tier_estimate_and_window(self):
        self.assertEqual(rc.estimate_tier([3, None, 0, 12, 10, 5]), 9)  # (12 + 10 + 5) / 3
        self.assertIsNone(rc.estimate_tier([None, 0]))
        self.assertEqual(rc.tier_window(12), (11, 14))
        self.assertEqual(rc.tier_window(0), (1, 3))
        self.assertEqual(rc.tier_window(31), (29, 30))
        self.assertEqual(rc.tier_window(30), (29, 30))
        self.assertIsNone(rc.tier_window(None))

    def test_loj_progress_counts_problems_not_submissions(self):
        submissions = [
            {"problem_id": 1, "status": "Wrong Answer"}, {"problem_id": 1, "status": "Accepted"},
            {"problem_id": 2, "status": "Time Limit Exceeded"}, {"problem_id": 2, "status": "Wrong Answer"},
            {"problem_id": 3, "status": "System Error"}, {"problem_id": 4, "status": "Pending"},
        ]
        solved, tried, stats = rc.loj_progress(submissions, {1: ["dp"], 2: ["dp", "greedy"], 3: ["math"]})
        self.assertEqual((solved, tried), ({1}, {2}))
        self.assertEqual(stats, {"dp": TC(1, 1), "greedy": TC(0, 1)})

    def test_loj_candidates_fit_the_tier_window_then_weak_tags(self):
        problems = [
            {"id": 1, "tier": 12, "tags": ["impl"], "difficulty": 3},
            {"id": 2, "tier": 13, "tags": ["dp"], "difficulty": 3},
            {"id": 3, "tier": 20, "tags": ["dp"], "difficulty": 5},   # 범위 밖
            {"id": 4, "tier": None, "tags": ["dp"], "difficulty": 2},  # 티어 모름: 범위 안 문제 뒤
            {"id": 5, "tier": 12, "tags": ["dp", "greedy"], "difficulty": 3},
            {"id": 6, "tier": 12, "tags": ["dp"], "difficulty": 3},   # 이미 풂
        ]
        picks = rc.rank_loj(problems, {6}, ["dp", "greedy"], (11, 14))
        self.assertEqual([p["id"] for p in picks], [5, 2, 1, 4])

    def test_starters_are_the_easiest_unsolved_problems(self):
        problems = [
            {"id": 1, "tier": 5, "difficulty": 2}, {"id": 2, "tier": None, "difficulty": 1},
            {"id": 3, "tier": 1, "difficulty": 3}, {"id": 4, "tier": 1, "difficulty": 1},
        ]
        self.assertEqual([p["id"] for p in rc.starter_problems(problems, {4})], [3, 1, 2])

    def test_boj_queries_stay_within_the_anonymous_search_limits(self):
        query = rc.boj_query((11, 14), "shortest_path", "alice_99", rc.BOJ_MIN_SOLVERS)
        self.assertEqual(query, "*11..14 #shortest_path -@alice_99 s#100..")
        sc.check_search_query(query)
        self.assertEqual(rc.boj_query((1, 3), "impl", "bad handle"), "*1..3 #impl")

    def test_next_sync_is_at_four_in_the_morning_korean_time(self):
        before = datetime(2026, 11, 18, 3, 30, tzinfo=rc.KST)
        after = datetime(2026, 11, 18, 4, 0, tzinfo=rc.KST)
        self.assertEqual(rc.seconds_until(4, before), 30 * 60)
        self.assertEqual(rc.seconds_until(4, after), 24 * 60 * 60)

    def test_stats_rows_keep_tier_and_tag_counts(self):
        row = rc.stats_row("u", "alice", {"tier": 13, "rating": 1500, "solvedCount": 321},
                           {"items": [{"key": "dp", "solved": 5, "tried": 2}, {"solved": 1}]})
        self.assertEqual({k: row[k] for k in ("user_id", "handle", "tier", "rating", "solved_count", "tag_stats")}, {
            "user_id": "u", "handle": "alice", "tier": 13, "rating": 1500, "solved_count": 321,
            "tag_stats": [{"key": "dp", "solved": 5, "tried": 2}],
        })


# ── 전체 흐름 ─────────────────────────────────────────────────────────────────

class Query:
    """supabase-py 질의 흉내. eq/neq만 걸러 내고 나머지 조건은 무시한다."""

    def __init__(self, db, table):
        self.db, self.table, self.filters, self.window, self.op, self.payload = db, table, [], None, "select", None

    def select(self, *_):
        return self

    def eq(self, column, value):
        self.filters.append((column, value, True))
        return self

    def neq(self, column, value):
        self.filters.append((column, value, False))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _n):
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", (row, on_conflict)
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def _matches(self, row):
        return all((row.get(c) == v) == want for c, v, want in self.filters)

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            row, key = self.payload
            rows[:] = [r for r in rows if r[key] != row[key]] + [row]
            return SimpleNamespace(data=[row])
        if self.op == "update":
            for r in rows:
                if self._matches(r):
                    r.update(self.payload)
            return SimpleNamespace(data=[])
        found = [r for r in rows if self._matches(r)]
        if self.window:
            found = found[self.window[0]:self.window[1] + 1]
        return SimpleNamespace(data=found)


class FakeDB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return Query(self, name)


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True


class FakeStore:
    def __init__(self, accounts):
        self.accounts = accounts

    async def account(self, user_id):
        return self.accounts.get(user_id)


class FakeSolvedac:
    configured = True

    def __init__(self):
        self.queries = []
        self.calls = []

    async def me(self, user_id):
        self.calls.append(("me", user_id))
        return {"id": 7, "handle": "alice_new"}

    async def user_profile(self, handle, as_user=None):
        self.calls.append(("profile", handle))
        return {"tier": 12, "rating": 1500, "solvedCount": 300}

    async def tag_progress(self, user_id):
        self.calls.append(("progress", user_id))
        return {"items": [{"key": "dp", "solved": 1, "tried": 9}, {"key": "impl", "solved": 90, "tried": 0}]}

    async def random_problem(self, query):
        self.queries.append(query)
        tag = query.split()[1][1:]
        number = 1000 + len(self.queries)
        return {"problemId": number, "level": 12, "titles": [{"language": "ko", "title": f"{tag} 문제", "isOriginal": True}],
                "tags": [{"key": tag, "isMeta": False}]}


SOON = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
LATER = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
EARLIER = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()


def loj_db(**extra):
    tables = {
        "problems": [
            {"id": 1, "title": "A+B", "difficulty": 1, "solvedac_tier": 1, "problem_tags": [{"tag_key": "math"}]},
            {"id": 2, "title": "최단 경로", "difficulty": 3, "solvedac_tier": 12, "problem_tags": [{"tag_key": "dp"}]},
            {"id": 3, "title": "대회 문제", "difficulty": 3, "solvedac_tier": 12, "problem_tags": [{"tag_key": "dp"}]},
            {"id": 4, "title": "지난 대회", "difficulty": 2, "solvedac_tier": 11, "problem_tags": []},
            {"id": 5, "title": "진행 중인 대회", "difficulty": 1, "solvedac_tier": 2, "problem_tags": [{"tag_key": "math"}]},
            # 대회 전용 문제는 대회가 끝나 공개(contest_only가 false)되기 전에는 추천하지 않는다.
            {"id": 1000000001, "title": "대회 전용", "difficulty": 1, "solvedac_tier": 1, "contest_only": True, "problem_tags": []},
        ],
        "contest_problems": [
            {"problem_id": 3, "contests": {"start_time": SOON, "end_time": LATER}},
            {"problem_id": 4, "contests": {"start_time": EARLIER, "end_time": PAST}},
            {"problem_id": 5, "contests": {"start_time": PAST, "end_time": SOON}},
        ],
        "tags": [{"key": "dp", "name_ko": "다이나믹 프로그래밍"}, {"key": "math", "name_ko": "수학"}],
        "submissions": [],
    }
    tables.update(extra)
    return FakeDB(**tables)


def run(coro):
    return asyncio.run(coro)


class RecommendFlowTests(unittest.TestCase):
    def make(self, db, accounts=None, store=True):
        self.solvedac = FakeSolvedac()
        self.redis = FakeRedis()
        return rc.Recommender(client=self.solvedac, supabase=db, redis=self.redis,
                              store=FakeStore(accounts or {}) if store else None)

    def test_newcomers_get_the_easiest_released_problems(self):
        result = run(self.make(loj_db()).recommend("new"))
        self.assertTrue(result["starter"])
        self.assertEqual(result["basis"], {"source": None, "tier": None, "linked": False, "handle": None, "synced_at": None})
        # 시작 전 대회의 3번과 진행 중인 대회의 5번은 빠지고, 끝난 대회의 4번은 들어간다.
        self.assertEqual([p["id"] for p in result["loj"]], [1, 4, 2])
        self.assertEqual((result["boj"], result["weak_tags"]), ([], []))

    def test_loj_history_alone_estimates_a_tier(self):
        db = loj_db(submissions=[
            {"user_id": "u", "problem_id": 4, "status": "Accepted"},
            {"user_id": "u", "problem_id": 2, "status": "Wrong Answer"},
        ])
        result = run(self.make(db).recommend("u"))
        self.assertEqual((result["basis"]["source"], result["basis"]["tier"]), ("loj", 11))
        self.assertFalse(result["starter"])
        self.assertEqual([p["id"] for p in result["loj"]], [2])
        self.assertEqual(result["loj"][0]["tags"], [{"key": "dp", "name": "다이나믹 프로그래밍"}])
        self.assertEqual(result["boj"], [])  # 연동하지 않았으면 BOJ 문제는 추천하지 않는다

    def test_linked_users_are_synced_once_then_get_boj_problems_without_solved_ones(self):
        db = loj_db(solvedac_accounts=[{"user_id": "u", "handle": "alice", "status": "active"}])
        recommender = self.make(db, {"u": {"handle": "alice", "status": "active"}})
        result = run(recommender.recommend("u"))
        # 통계가 없어서 처음 한 번 받았다. handle이 바뀐 것도 반영했다.
        self.assertEqual([c[0] for c in self.solvedac.calls], ["me", "profile", "progress"])
        self.assertEqual(db.tables["solvedac_accounts"][0]["handle"], "alice_new")
        self.assertEqual((result["basis"]["source"], result["basis"]["tier"]), ("solvedac", 12))
        self.assertEqual(result["weak_tags"][0], {"key": "dp", "name": "다이나믹 프로그래밍"})
        self.assertTrue(result["boj"])
        self.assertTrue(all("-@alice_new" in q and q.startswith("*11..14 #") for q in self.solvedac.queries))
        self.assertEqual(result["boj"][0]["url"], f"https://www.acmicpc.net/problem/{result['boj'][0]['problem_id']}")

        # 두 번째 요청은 저장한 통계와 캐시한 BOJ 문제를 쓴다.
        calls, queries = len(self.solvedac.calls), len(self.solvedac.queries)
        again = run(recommender.recommend("u"))
        self.assertEqual((len(self.solvedac.calls), len(self.solvedac.queries)), (calls, queries))
        self.assertEqual(again["boj"], result["boj"])

    def test_refresh_picks_new_boj_problems_but_not_too_often(self):
        db = loj_db(solvedac_stats=[{"user_id": "u", "handle": "alice", "tier": 12, "tag_stats": [], "synced_at": PAST}])
        recommender = self.make(db, {"u": {"handle": "alice", "status": "active"}})
        first = run(recommender.recommend("u"))
        asked = len(self.solvedac.queries)
        again = run(recommender.recommend("u", refresh=True))
        self.assertGreater(len(self.solvedac.queries), asked)
        self.assertNotEqual([p["problem_id"] for p in again["boj"]], [p["problem_id"] for p in first["boj"]])
        # 30초 안에 또 누르면 방금 고른 것을 그대로 보여 준다.
        asked = len(self.solvedac.queries)
        third = run(recommender.recommend("u", refresh=True))
        self.assertEqual(len(self.solvedac.queries), asked)
        self.assertEqual(third["boj"], again["boj"])

    def test_boj_search_retries_without_the_popularity_filter(self):
        recommender = self.make(loj_db())

        async def random_problem(query):
            self.solvedac.queries.append(query)
            return None if "s#" in query else {"problemId": 5, "level": 12, "titles": [], "tags": []}

        self.solvedac.random_problem = random_problem
        found = run(recommender._random_boj((11, 14), "dp", "alice"))
        self.assertEqual(found["problemId"], 5)
        self.assertEqual(self.solvedac.queries, ["*11..14 #dp -@alice s#100..", "*11..14 #dp -@alice"])

    def test_expired_links_are_not_synced_but_old_stats_still_count(self):
        stats = {"user_id": "u", "handle": "alice", "tier": 6, "tag_stats": [], "synced_at": PAST}
        recommender = self.make(loj_db(solvedac_stats=[stats]), {"u": {"handle": "alice", "status": "expired"}})
        result = run(recommender.recommend("u"))
        self.assertEqual(self.solvedac.calls, [])
        self.assertEqual((result["basis"]["source"], result["basis"]["tier"]), ("solvedac", 6))

    def test_nightly_sync_visits_active_accounts_with_a_pause(self):
        sleeps = []

        async def sleep(seconds):
            sleeps.append(seconds)

        db = loj_db(solvedac_accounts=[
            {"user_id": "a", "handle": "alice", "status": "active"},
            {"user_id": "b", "handle": "bob", "status": "expired"},
            {"user_id": "c", "handle": "carol", "status": "active"},
        ])
        recommender = self.make(db)
        recommender._sleep = sleep
        self.assertEqual(run(recommender.sync_all()), 2)
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(sorted(r["user_id"] for r in db.tables["solvedac_stats"]), ["a", "c"])

    def test_sync_gives_up_quietly_when_solved_ac_refuses(self):
        recommender = self.make(loj_db())

        async def expired(_user_id):
            raise sc.SolvedacLinkExpired(401, "EXPIRED")

        self.solvedac.me = expired
        self.assertFalse(run(recommender.sync_user("u")))
        self.assertFalse(run(self.make(loj_db(), store=False).sync_user("u")))


if __name__ == "__main__":
    unittest.main()
