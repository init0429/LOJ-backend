"""
레벨에 맞는 문제 추천 (GET /recommendations).

입력
- solved.ac를 연동했으면: 밤마다 받아 둔 티어와 태그별 solved/tried(solvedac_stats 표)
- LOJ 기록: 내 제출과 문제의 티어·태그(problems.solvedac_tier, problem_tags)

출력
1. LOJ 문제: 안 푼 문제 중 티어가 내 티어 -1 ~ +2 단계이고 약점 태그가 붙은 것부터
2. BOJ 문제: solved.ac 랜덤 문제(연동한 사용자만, 이미 푼 문제는 뺀다). LOJ에서 채점할 수
   없으므로 화면에서는 바깥 링크로 보인다.
기록이 하나도 없으면 가장 쉬운 LOJ 문제를 "처음이라면" 목록으로 준다.

통계는 요청할 때 받지 않는다. 한국 시각 새벽 4시에 한 번(run_nightly), 연동 직후에 한 번
(sync_user) 받는다. 연동했는데 아직 받은 적이 없으면 첫 추천 요청에서 한 번 받는다.
약점을 매기는 규칙은 weakness() 하나에 모아 두었다.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

import solvedac_client as sc
import solvedac_tags

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
SYNC_HOUR = 4
LOJ_LIMIT = 5
WEAK_TAG_LIMIT = 3
# 너무 덜 알려진 문제는 피한다. 맞는 문제가 없으면 이 조건 없이 다시 찾는다.
BOJ_MIN_SOLVERS = 100
BOJ_CACHE_TTL = 60 * 60
PAGE = 1000
# 시도로 치지 않는 판정. 채점이 끝나지 않았거나 채점 서버 탓인 것.
NOT_ATTEMPTS = {"Pending", "Running", "System Error", "Unknown"}

# 티어마다 꼭 다뤄 봐야 할 태그. 사용자 티어까지의 묶음을 모두 핵심으로 본다.
CORE_TAGS = (
    (0, ("impl", "math", "string", "sort", "brute", "greedy", "sim")),
    (6, ("dp", "graph_traversal", "bfs", "dfs", "binary_search", "ds", "prefix_sum",
         "backtrack", "two_pointer", "stack", "queue")),  # Silver V부터
    (11, ("shortest_path", "dijkstra", "tree", "pq", "dsu", "toposort", "set", "bitmask",
          "number_theory", "combinatorics", "knapsack", "dnc", "parametric_search")),  # Gold V부터
    (16, ("segtree", "mst", "lis")),  # Platinum V부터
)

HANDLE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
TAG_KEY = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass(frozen=True)
class TagCount:
    solved: int = 0
    # 시도했지만 아직 풀지 못한 문제 수(solved.ac의 tried와 같은 뜻)
    tried: int = 0


# ── 점수 매기기 (순수 함수) ────────────────────────────────────────────────

def core_tags(tier: Optional[int]) -> set[str]:
    level = tier or 0
    return {key for start, keys in CORE_TAGS if level >= start for key in keys}


def weakness(count: TagCount, core: bool) -> float:
    """
    태그 하나가 얼마나 약한지(0~1). 규칙을 바꾸려면 이 함수만 고치면 된다.
    - 못 푼 비율 tried / (solved + tried + 2): +2는 한두 번의 시도에 크게 흔들리지 않게 한다.
    - 적게 푼 정도 1 / (1 + solved): 핵심 태그는 그대로, 아니면 절반만 친다.
    """
    fail = count.tried / (count.solved + count.tried + 2)
    scarce = 1 / (1 + count.solved) * (1.0 if core else 0.5)
    return round(0.6 * fail + 0.4 * scarce, 4)


def weak_tags(stats: dict[str, TagCount], tier: Optional[int], limit: int = WEAK_TAG_LIMIT) -> list[str]:
    """약한 태그부터 limit개. 핵심이 아닌 태그는 한 번이라도 손댄 것만 본다."""
    core = core_tags(tier)
    scores = {}
    for key in core | set(stats):
        count = stats.get(key, TagCount())
        if key not in core and count.solved + count.tried == 0:
            continue
        scores[key] = weakness(count, key in core)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [key for key, score in ranked[:limit] if score > 0]


def estimate_tier(solved_tiers: Iterable[Optional[int]]) -> Optional[int]:
    """LOJ에서 푼 문제의 티어로 실력을 어림한다. 가장 어려운 세 문제의 평균."""
    tiers = sorted((tier for tier in solved_tiers if tier and tier > 0), reverse=True)[:3]
    return round(sum(tiers) / len(tiers)) if tiers else None


def tier_window(tier: Optional[int]) -> Optional[tuple[int, int]]:
    """추천할 문제의 티어 범위: 내 티어 -1 ~ +2. Unrated(0)는 Bronze V, Master(31)는 Ruby I로 본다."""
    if tier is None:
        return None
    base = min(max(tier, 1), 30)
    return max(base - 1, 1), min(base + 2, 30)


def loj_progress(submissions: list[dict], problem_tags: dict[int, list[str]]):
    """내 LOJ 제출에서 (푼 문제, 시도만 한 문제, 태그별 TagCount)."""
    attempted = {s["problem_id"] for s in submissions if s.get("status") not in NOT_ATTEMPTS}
    solved = {s["problem_id"] for s in submissions if s.get("status") == "Accepted"}
    tried = attempted - solved
    stats: dict[str, TagCount] = {}
    for problem_id in attempted:
        for key in problem_tags.get(problem_id, ()):
            count = stats.get(key, TagCount())
            stats[key] = TagCount(count.solved + (problem_id in solved), count.tried + (problem_id in tried))
    return solved, tried, stats


def merge_stats(*parts: dict[str, TagCount]) -> dict[str, TagCount]:
    merged: dict[str, TagCount] = {}
    for part in parts:
        for key, count in part.items():
            total = merged.get(key, TagCount())
            merged[key] = TagCount(total.solved + count.solved, total.tried + count.tried)
    return merged


def rank_loj(problems: list[dict], solved: set[int], weak: list[str],
             window: Optional[tuple[int, int]], limit: int = LOJ_LIMIT) -> list[dict]:
    """
    안 푼 LOJ 문제를 고른다. 티어가 범위 안인 문제가 먼저, 그 안에서는 약점 태그가 많이 붙은
    것부터, 그다음 내 티어에 가까운 것부터. 티어를 모르는 문제는 범위 안 문제 뒤에 둔다.
    """
    weight = {key: len(weak) - index for index, key in enumerate(weak)}
    target = window[0] + 1 if window else None
    ranked = []
    for problem in problems:
        if problem["id"] in solved:
            continue
        tier = problem.get("tier") or None  # 0(Unrated)은 모르는 것과 같다
        fits = window is not None and tier is not None and window[0] <= tier <= window[1]
        if window is not None and tier is not None and not fits:
            continue
        tag_score = sum(weight.get(key, 0) for key in problem.get("tags", ()))
        distance = abs(tier - target) if (tier is not None and target is not None) else 99
        ranked.append(((-int(fits), -tag_score, distance, problem.get("difficulty") or 9, problem["id"]), problem))
    ranked.sort(key=lambda item: item[0])
    return [problem for _, problem in ranked[:limit]]


def starter_problems(problems: list[dict], solved: set[int], limit: int = LOJ_LIMIT) -> list[dict]:
    """기록이 없을 때: 쉬운 문제부터. 티어가 있는 문제를 먼저, 그다음 난이도 순."""
    unsolved = [p for p in problems if p["id"] not in solved]
    unsolved.sort(key=lambda p: (p.get("tier") or 99, p.get("difficulty") or 9, p["id"]))
    return unsolved[:limit]


def boj_query(window: tuple[int, int], tag: str, handle: Optional[str], min_solvers: int = 0) -> str:
    """solved.ac 검색어. 유저 없는 검색 한도(100자, 8텀) 안에 들도록 조건은 넷까지만 쓴다."""
    terms = [f"*{window[0]}..{window[1]}", f"#{tag}"]
    if handle and HANDLE.match(handle):
        terms.append(f"-@{handle}")
    if min_solvers:
        terms.append(f"s#{min_solvers}..")
    return " ".join(terms)


def stats_row(user_id: str, handle: str, profile: dict, progress: dict) -> dict:
    """solved.ac 프로필과 태그별 진척도를 solvedac_stats 행으로."""
    tag_stats = [
        {"key": item["key"], "solved": int(item.get("solved") or 0), "tried": int(item.get("tried") or 0)}
        for item in (progress or {}).get("items") or []
        if item.get("key")
    ]
    return {
        "user_id": user_id,
        "handle": handle,
        "tier": profile.get("tier"),
        "rating": profile.get("rating"),
        "solved_count": profile.get("solvedCount"),
        "tag_stats": tag_stats,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


def seconds_until(hour: int, now: datetime) -> float:
    """now(시간대 포함) 다음에 오는 hour시 정각까지 남은 초."""
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


# ── 데이터와 동기화 ─────────────────────────────────────────────────────────

class Recommender:
    def __init__(self, *, client: sc.SolvedacClient, supabase, store, redis,
                 sleep: Callable = asyncio.sleep):
        self.client = client
        self.supabase = supabase
        self.store = store
        self.redis = redis
        self._sleep = sleep

    @property
    def can_sync(self) -> bool:
        return self.client.configured and self.store is not None

    async def sync_user(self, user_id: str) -> bool:
        """사용자의 solved.ac 티어와 태그별 통계를 받아 저장한다. 못 받으면 False(다음에 다시)."""
        if not self.can_sync:
            return False
        try:
            me = await self.client.me(user_id)
            handle = me["handle"]
            profile = await self.client.user_profile(handle, as_user=user_id)
            progress = await self.client.tag_progress(user_id)
        except (sc.SolvedacNotLinked, sc.SolvedacLinkExpired):
            return False
        except sc.SolvedacError as error:
            logger.warning("solved.ac 통계를 받지 못했습니다 (%s): %s", user_id, error)
            return False
        row = stats_row(user_id, handle, profile, progress)

        def save():
            self.supabase.table("solvedac_stats").upsert(row, on_conflict="user_id").execute()
            # solved.ac에서 handle을 바꿨으면 설정 화면에도 새 handle이 보이게 한다.
            self.supabase.table("solvedac_accounts").update({"handle": handle}).eq("user_id", user_id).neq("handle", handle).execute()

        try:
            await run_in_threadpool(save)
        except Exception:
            logger.exception("solved.ac 통계를 저장하지 못했습니다 (%s)", user_id)
            return False
        return True

    async def sync_all(self) -> int:
        """연동이 살아 있는 모든 사용자를 하나씩 동기화한다. 사용자 사이에 1초 쉰다(요청 한도)."""
        rows = await run_in_threadpool(
            lambda: self.supabase.table("solvedac_accounts").select("user_id").eq("status", "active").execute().data or []
        )
        synced = 0
        for index, row in enumerate(rows):
            if index:
                await self._sleep(1.0)
            synced += await self.sync_user(row["user_id"])
        return synced

    async def run_nightly(self) -> None:
        """매일 한국 시각 4시에 sync_all. 서버가 여럿이어도 Redis 잠금으로 하루 한 번만 돈다."""
        while True:
            await self._sleep(seconds_until(SYNC_HOUR, datetime.now(KST)))
            day = datetime.now(KST).date().isoformat()
            try:
                if await self.redis.set(f"solvedac:nightly:{day}", "1", nx=True, ex=20 * 60 * 60):
                    count = await self.sync_all()
                    logger.info("solved.ac 야간 동기화: %d명", count)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("solved.ac 야간 동기화에 실패했습니다")

    def _load_loj(self, user_id: str) -> dict:
        """추천에 쓰는 LOJ 데이터. secret 키로 읽으므로 공개 전 문제는 여기서 직접 뺀다."""
        db = self.supabase

        def all_rows(build):
            rows, start = [], 0
            while True:
                page = build().range(start, start + PAGE - 1).execute().data or []
                rows += page
                if len(page) < PAGE:
                    return rows
                start += PAGE

        submissions = all_rows(lambda: db.table("submissions").select("problem_id, status").eq("user_id", user_id).order("id"))
        try:
            raw = all_rows(lambda: db.table("problems").select("id, title, difficulty, solvedac_tier, problem_tags(tag_key)").order("id"))
        except Exception:
            # 태그 마이그레이션 전이면 티어와 태그 없이 추천한다.
            raw = all_rows(lambda: db.table("problems").select("id, title, difficulty").order("id"))
        now = datetime.now(timezone.utc)
        hidden = {
            row["problem_id"]
            for row in db.table("contest_problems").select("problem_id, contests(start_time)").execute().data or []
            if row.get("contests") and datetime.fromisoformat(row["contests"]["start_time"].replace("Z", "+00:00")) > now
        }
        problems = [
            {
                "id": row["id"],
                "title": row["title"],
                "difficulty": row.get("difficulty"),
                "tier": row.get("solvedac_tier"),
                "tags": [tag["tag_key"] for tag in row.get("problem_tags") or []],
            }
            for row in raw
            if row["id"] not in hidden
        ]
        try:
            names = {row["key"]: row["name_ko"] for row in db.table("tags").select("key, name_ko").execute().data or []}
        except Exception:
            names = {}
        return {"submissions": submissions, "problems": problems, "names": names}

    def _load_stats(self, user_id: str) -> Optional[dict]:
        rows = self.supabase.table("solvedac_stats").select("*").eq("user_id", user_id).limit(1).execute().data
        return rows[0] if rows else None

    async def _random_boj(self, window: tuple[int, int], tag: str, handle: str) -> Optional[dict]:
        for min_solvers in (BOJ_MIN_SOLVERS, 0):
            try:
                problem = await self.client.random_problem(boj_query(window, tag, handle, min_solvers))
            except (sc.SolvedacError, ValueError) as error:
                logger.warning("BOJ 추천 검색 실패 (%s): %s", tag, error)
                return None
            if problem is not None:
                return problem
        return None

    async def _boj_picks(self, user_id: str, handle: str, window: tuple[int, int], weak: list[str]) -> list[dict]:
        """약점 태그마다 BOJ 문제 하나. 한 시간 동안 같은 결과를 보여 준다(요청 한도 아끼기)."""
        tags = [tag for tag in weak if TAG_KEY.match(tag)]
        if not tags or not self.client.configured:
            return []
        cache_key = f"recommend:boj:{user_id}:{window[0]}-{window[1]}:{','.join(tags)}"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.warning("추천 캐시를 읽지 못했습니다", exc_info=True)
        found = await asyncio.gather(*(self._random_boj(window, tag, handle) for tag in tags))
        picks, seen = [], set()
        for payload in found:
            if not payload or payload.get("problemId") in seen:
                continue
            seen.add(payload["problemId"])
            picks.append(solvedac_tags.problem_summary(int(payload["problemId"]), payload))
        try:
            await self.redis.set(cache_key, json.dumps(picks), ex=BOJ_CACHE_TTL)
        except Exception:
            logger.warning("추천 캐시를 저장하지 못했습니다", exc_info=True)
        return picks

    async def recommend(self, user_id: str) -> dict:
        account = await self.store.account(user_id) if self.store is not None else None
        stats = await run_in_threadpool(self._load_stats, user_id) if account else None
        if account and account.get("status") == "active" and stats is None:
            if await self.sync_user(user_id):
                stats = await run_in_threadpool(self._load_stats, user_id)
        loj = await run_in_threadpool(self._load_loj, user_id)

        problem_tags = {p["id"]: p["tags"] for p in loj["problems"]}
        solved, tried, loj_stats = loj_progress(loj["submissions"], problem_tags)
        sac_stats = {
            item["key"]: TagCount(int(item.get("solved") or 0), int(item.get("tried") or 0))
            for item in (stats or {}).get("tag_stats") or []
        }
        if stats and stats.get("tier") is not None:
            source, tier = "solvedac", int(stats["tier"])
        else:
            tier = estimate_tier(p["tier"] for p in loj["problems"] if p["id"] in solved)
            source = "loj" if tier is not None else None

        has_history = bool(stats) or bool(solved or tried)
        weak = weak_tags(merge_stats(sac_stats, loj_stats), tier) if has_history else []
        window = tier_window(tier)
        if has_history:
            loj_picks = rank_loj(loj["problems"], solved, weak, window)
        else:
            loj_picks = starter_problems(loj["problems"], solved)
        boj_picks = []
        if stats and window and weak:
            boj_picks = await self._boj_picks(user_id, stats["handle"], window, weak)

        names = loj["names"]
        tag_list = lambda keys: [{"key": key, "name": names.get(key, key)} for key in keys]  # noqa: E731
        return {
            "basis": {
                "source": source,
                "tier": tier,
                "linked": account is not None,
                "handle": (stats or {}).get("handle") or (account or {}).get("handle"),
                "synced_at": (stats or {}).get("synced_at"),
            },
            "starter": not has_history,
            "weak_tags": tag_list(weak),
            "loj": [
                {"id": p["id"], "title": p["title"], "tier": p["tier"], "difficulty": p["difficulty"], "tags": tag_list(p["tags"])}
                for p in loj_picks
            ],
            "boj": [
                {
                    "problem_id": p["boj_problem_id"],
                    "title": p["title"],
                    "tier": p["tier"],
                    "tags": tag_list(p["tags"]),
                    "url": f"https://www.acmicpc.net/problem/{p['boj_problem_id']}",
                }
                for p in boj_picks
            ],
        }


def create_router(*, recommender: Recommender, current_user: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/recommendations")
    async def recommendations(user_id: str = Depends(current_user)) -> Any:
        try:
            return await recommender.recommend(user_id)
        except HTTPException:
            raise
        except Exception:
            logger.exception("추천을 만들지 못했습니다")
            raise HTTPException(503, "추천을 만들지 못했습니다. 잠시 후 다시 시도하세요.")

    return router
