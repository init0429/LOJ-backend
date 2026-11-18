"""
solved.ac 태그 체계를 LOJ 문제에 붙이는 일.

- seed_tags: solved.ac 태그 목록을 tags 표에 넣는다(스크립트와 관리자 버튼이 함께 쓴다).
- 관리자 API
    GET  /integrations/solvedac/problems/{boj_id}  BOJ 문제의 티어와 태그(관리자 화면의 자동 채우기)
    POST /integrations/solvedac/tags/sync          태그 목록 다시 받기

solved.ac 앱 키는 서버에만 있으므로 관리자 화면도 이 API를 거친다. problems와 problem_tags에
쓰는 일은 다른 관리 기능처럼 브라우저가 Supabase로 직접 한다(RLS가 관리자만 허용).
"""
import logging
from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.concurrency import run_in_threadpool

import solvedac_client as sc

logger = logging.getLogger(__name__)

UPSERT_BATCH = 500


def tag_rows(payload: dict) -> list[dict]:
    """GET /api/v4/tags 응답을 tags 표의 행으로 바꾼다."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for tag in payload.get("tags") or []:
        names = tag.get("names") or {}
        key = tag["key"]
        rows.append({
            "key": key,
            "solvedac_id": int(tag["id"]),
            "parent_keys": list(tag.get("parents") or []),
            "name_ko": names.get("ko") or names.get("en") or key,
            "name_en": names.get("en"),
            "aliases": list(tag.get("aliases") or []),
            "is_meta": bool(tag.get("isMeta")),
            "rated": bool(tag.get("rated", True)),
            "updated_at": now,
        })
    return rows


async def seed_tags(client: sc.SolvedacClient, supabase) -> int:
    """solved.ac 태그를 tags 표에 넣거나 고친다. solved.ac에서 사라진 태그는 지우지 않는다."""
    rows = tag_rows(await client.tags())
    for start in range(0, len(rows), UPSERT_BATCH):
        batch = rows[start:start + UPSERT_BATCH]
        await run_in_threadpool(
            lambda batch=batch: supabase.table("tags").upsert(batch, on_conflict="key").execute()
        )
    return len(rows)


def problem_summary(problem_id: int, payload: dict) -> dict:
    """GET /api/v4/problems/{id} 응답에서 관리자 화면이 쓰는 것만 추린다."""
    titles = payload.get("titles") or []
    title = next(
        (t["title"] for t in titles if t.get("language") == "ko"),
        next((t["title"] for t in titles if t.get("isOriginal")), titles[0]["title"] if titles else ""),
    )
    return {
        "boj_problem_id": problem_id,
        "title": title,
        # 0은 아직 난이도가 매겨지지 않은 문제(Unrated), 1~30은 Bronze V부터 Ruby I까지
        "tier": int(payload.get("level") or 0),
        "tags": [tag["key"] for tag in payload.get("tags") or [] if not tag.get("isMeta")],
    }


def create_router(*, client: sc.SolvedacClient, supabase, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/integrations/solvedac")

    def is_admin(user_id: str) -> bool:
        rows = supabase.table("profiles").select("is_admin").eq("id", user_id).execute().data
        return bool(rows and rows[0].get("is_admin"))

    async def require_admin(user_id: str = Depends(current_user)) -> str:
        try:
            allowed = await run_in_threadpool(is_admin, user_id)
        except Exception:
            logger.exception("관리자 여부를 확인하지 못했습니다")
            raise HTTPException(503, "관리자 여부를 확인하지 못했습니다. 잠시 후 다시 시도하세요.")
        if not allowed:
            raise HTTPException(403, "관리자만 쓸 수 있습니다.")
        if not client.configured:
            raise HTTPException(503, "solved.ac 연동이 아직 설정되지 않았습니다.")
        return user_id

    def unavailable(error: sc.SolvedacError) -> HTTPException:
        if isinstance(error, sc.SolvedacRateLimited):
            headers = {"Retry-After": str(int(error.retry_after))} if error.retry_after else None
            return HTTPException(429, "solved.ac 요청이 많습니다. 잠시 후 다시 시도하세요.", headers=headers)
        logger.warning("solved.ac 요청 실패: %s", error)
        return HTTPException(502, "solved.ac에서 정보를 받지 못했습니다. 잠시 후 다시 시도하세요.")

    @router.get("/problems/{boj_id}")
    async def boj_problem(boj_id: int = Path(gt=0, lt=10_000_000), _: str = Depends(require_admin)):
        try:
            payload = await client.problem(boj_id)
        except sc.SolvedacError as error:
            if error.status == 404:
                raise HTTPException(404, f"solved.ac에 {boj_id}번 문제가 없습니다.")
            raise unavailable(error)
        return problem_summary(boj_id, payload)

    @router.post("/tags/sync")
    async def sync_tags(_: str = Depends(require_admin)):
        try:
            count = await seed_tags(client, supabase)
        except sc.SolvedacError as error:
            raise unavailable(error)
        except Exception:
            logger.exception("태그 목록을 저장하지 못했습니다")
            raise HTTPException(503, "태그 목록을 저장하지 못했습니다. tags 표가 있는지 확인하세요.")
        return {"count": count}

    return router
