from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
import redis
import json
import uuid
import time
import logging
from datetime import datetime, timezone
from supabase import create_client, Client
import os

# judge/worker.py의 LANGUAGES와 반드시 동기화해서 유지할 것.
# 여기서 막아두면 judge까지 안 가고 API 단에서 바로 422로 거절됨.
SupportedLanguage = Literal[
    "python", "c", "cpp", "java", "sql",
    "javascript", "typescript", "ruby", "php", "perl",
]

url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_KEY", "")
supabase: Client = create_client(url, key)

app = FastAPI(title="LOJ Backend API", version="1.0.0")

_extra_origins = os.getenv("ALLOWED_ORIGINS", "")
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
] + [o.strip() for o in _extra_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
logger = logging.getLogger(__name__)


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """
    프론트에서 보내는 `Authorization: Bearer <supabase access_token>`을 검증해서
    user_id를 뽑아낸다. /submit, /test_run 둘 다 이걸 통과해야 호출 가능함
    (로그인 안 한 사람이 채점 서버 자원을 그냥 쓰는 걸 막기 위함이기도 함).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    token = authorization.removeprefix("Bearer ").strip()

    try:
        user_response = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다.")

    user = getattr(user_response, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다.")

    return user.id



class Submission(BaseModel):
    problem_id: int = Field(gt=0)
    language: SupportedLanguage
    code: str = Field(min_length=1, max_length=100_000)
    contest_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("code")
    @classmethod
    def nonempty_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Code must not be blank")
        return value


def load_test_cases(submission: Submission, public_only: bool = False):
    try:
        problem = supabase.table("problems").select("id").eq("id", submission.problem_id).execute()
        if not problem.data:
            raise HTTPException(404, "문제를 찾을 수 없습니다.")
        if submission.contest_id is not None:
            contests = supabase.table("contests").select("start_time, end_time").eq("id", submission.contest_id).execute()
            if not contests.data:
                raise HTTPException(404, "콘테스트를 찾을 수 없습니다.")
            contest = contests.data[0]
            start = datetime.fromisoformat(contest["start_time"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(contest["end_time"].replace("Z", "+00:00"))
            if not start <= datetime.now(timezone.utc) < end:
                raise HTTPException(409, "콘테스트 진행 시간에만 실행하거나 제출할 수 있습니다.")
            links = supabase.table("contest_problems").select("problem_id").eq("contest_id", submission.contest_id).eq("problem_id", submission.problem_id).execute()
            if not links.data:
                raise HTTPException(400, "콘테스트에 포함되지 않은 문제입니다.")
        query = supabase.table("test_cases").select("input_data, expected_output").eq("problem_id", submission.problem_id)
        if public_only:
            query = query.eq("is_hidden", False)
        response = query.order("id").execute()
        if not response.data:
            raise HTTPException(422, "실행 가능한 테스트케이스가 없습니다.")
        return [{"input": tc["input_data"], "output": tc["expected_output"]} for tc in response.data]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to load judging context")
        raise HTTPException(503, "채점 정보를 불러오지 못했습니다. 잠시 후 다시 시도하세요.")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/submit")
def submit_code(submission: Submission, user_id: str = Depends(get_current_user_id)):
    submission_id = str(uuid.uuid4())

    test_cases = load_test_cases(submission)

    try:
        supabase.table("submissions").insert({
            "id": submission_id,
            "problem_id": submission.problem_id,
            "language": submission.language,
            "code": submission.code,
            "status": "Pending",
            "user_id": user_id,
            "contest_id": submission.contest_id,
        }).execute()
    except Exception:
        logger.exception("Failed to insert submission")
        raise HTTPException(503, "제출을 기록하지 못했습니다. 다시 시도하세요.")

    task_data = {
        "submission_id": submission_id,
        "problem_id": submission.problem_id,
        "language": submission.language,
        "code": submission.code,
        "test_cases": test_cases,
    }

    try:
        r.rpush("submission_queue", json.dumps(task_data))
    except redis.RedisError:
        logger.exception("Failed to enqueue submission %s", submission_id)
        try:
            supabase.table("submissions").update({"status": "System Error"}).eq("id", submission_id).execute()
        except Exception:
            logger.exception("Failed to mark submission as failed %s", submission_id)
        raise HTTPException(503, "채점 서버에 연결하지 못했습니다. 잠시 후 다시 제출하세요.")

    return {
        "submission_id": submission_id,
        "status": "pending",
        "message": "코드가 성공적으로 큐에 등록되었습니다.",
    }

@app.post("/test_run")
def test_code(submission: Submission, user_id: str = Depends(get_current_user_id)):
    test_id = str(uuid.uuid4())

    test_cases = load_test_cases(submission, public_only=True)

    task_data = {
        "submission_id": test_id,
        "is_test": True,
        "problem_id": submission.problem_id,
        "language": submission.language,
        "code": submission.code,
        "test_cases": test_cases,
    }

    channel_name = f"test_result:{test_id}"
    try:
        # Synchronous clients run in FastAPI's thread pool, not its event loop.
        with r.pubsub() as pubsub:
            pubsub.subscribe(channel_name)
            r.rpush("submission_queue", json.dumps(task_data))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if message:
                    result = json.loads(message["data"])
                    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
                        raise ValueError("Invalid judge response")
                    return {"status": "success", "result": result}
    except redis.RedisError:
        logger.exception("Test-run queue unavailable")
        raise HTTPException(503, "채점 서버에 연결하지 못했습니다.")
    except (ValueError, TypeError):
        logger.exception("Invalid test-run response")
        raise HTTPException(502, "채점 서버에서 올바르지 않은 결과를 받았습니다.")
    raise HTTPException(504, "채점 대기 시간이 초과되었습니다. 잠시 후 다시 실행하세요.")
